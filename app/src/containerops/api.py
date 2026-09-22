import hmac
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

import psycopg
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator
from starlette.middleware.base import RequestResponseEndpoint
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from containerops import repository
from containerops.config import Settings
from containerops.domain import (
    ALGORITHM,
    MAX_BODY_BYTES,
    MAX_TEXT_BYTES,
    AdmissionPaused,
    Job,
    JobConflict,
    OwnerQueueFull,
    QueueFull,
    SchemaIncompatible,
)
from containerops.log import configure, event


class JobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: StrictStr
    demo_duration_seconds: float = Field(default=0, ge=0, le=15, strict=True)

    @field_validator("text")
    @classmethod
    def validate_text(cls, text: str) -> str:
        try:
            size = len(text.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise ValueError("Texto deve conter Unicode válido") from error
        if size > MAX_TEXT_BYTES:
            raise ValueError("Texto excede 16 KiB em UTF-8")
        if "\x00" in text:
            raise ValueError("Caractere nulo não é aceito")
        return text


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > MAX_BODY_BYTES:
                response = JSONResponse({"detail": "Corpo excede 32 KiB"}, status_code=413)
                await response(scope, receive, send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, replay, send)


def job_response(job: Job, settings: Settings) -> dict[str, object]:
    result = None
    if job.state == "succeeded":
        result = {"word_count": job.word_count, "checksum": job.checksum}
    response: dict[str, object] = {
        "id": str(job.id),
        "state": job.state,
        "attempts": job.attempts,
        "result": result,
        "error": {"code": "attempts_exhausted"} if job.state == "failed" else None,
        "version": settings.version,
    }
    if settings.release_two:
        response["algorithm"] = ALGORITHM
    return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    tokens: dict[str, str] = {}

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        tokens.update(settings.tokens())
        application.state.accepting = True
        event("api", settings.version, "started")
        yield
        application.state.accepting = False
        tokens.clear()
        event("api", settings.version, "stopped")

    application = FastAPI(
        title="ContainerOps",
        version=settings.version,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.add_middleware(BodyLimitMiddleware)

    @application.middleware("http")
    async def request_log(request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.monotonic()
        request_id = str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        event(
            "api",
            settings.version,
            "request",
            request_id=request_id,
            method=request.method,
            status=response.status_code,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
        )
        return response

    def authenticated_owner(request: Request) -> str:
        authorization = request.headers.getlist("authorization")
        if len(authorization) != 1:
            raise HTTPException(
                401, "Credencial Bearer necessária", headers={"WWW-Authenticate": "Bearer"}
            )
        scheme, separator, supplied = authorization[0].partition(" ")
        supplied = supplied.lstrip(" ")
        if scheme.lower() != "bearer" or not separator or not supplied or not supplied.isascii():
            raise HTTPException(401, "Credencial inválida", headers={"WWW-Authenticate": "Bearer"})
        for owner, token in tokens.items():
            if hmac.compare_digest(supplied, token):
                return owner
        raise HTTPException(401, "Credencial inválida", headers={"WWW-Authenticate": "Bearer"})

    @application.exception_handler(psycopg.Error)
    async def database_error(request: Request, error: psycopg.Error) -> JSONResponse:
        retryable = isinstance(
            error,
            (
                psycopg.OperationalError,
                psycopg.InterfaceError,
                psycopg.errors.QueryCanceled,
                psycopg.errors.LockNotAvailable,
            ),
        )
        event(
            "api",
            settings.version,
            "database_unavailable" if retryable else "database_operation_failed",
            request_id=getattr(request.state, "request_id", None),
            error_category=type(error).__name__,
        )
        if not retryable:
            return JSONResponse({"detail": "Falha ao concluir a operação"}, 500)
        return JSONResponse(
            {"detail": "Banco temporariamente indisponível"}, 503, headers={"Retry-After": "2"}
        )

    @application.exception_handler(SchemaIncompatible)
    async def incompatible_schema(request: Request, error: SchemaIncompatible) -> JSONResponse:
        event(
            "api",
            settings.version,
            "schema_incompatible",
            request_id=getattr(request.state, "request_id", None),
            error_category=type(error).__name__,
        )
        return JSONResponse(
            {"detail": "Serviço indisponível: configuração de dados incompatível"}, 503
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {
                "detail": "Entrada inválida",
                "errors": [
                    {"location": list(item["loc"]), "type": item["type"]} for item in error.errors()
                ],
            },
            422,
        )

    @application.get("/health/live")
    def live() -> dict[str, object]:
        return {"status": "alive", "version": settings.version}

    @application.get("/health/ready")
    def ready() -> JSONResponse:
        try:
            schema, paused = repository.readiness(settings)
        except (psycopg.Error, SchemaIncompatible):
            return JSONResponse({"status": "not_ready", "version": settings.version}, 503)
        return JSONResponse(
            {
                "status": "ready",
                "version": settings.version,
                "schema_version": schema,
                "admission_paused": paused,
            }
        )

    @application.post("/v1/jobs")
    def submit(
        payload: JobInput,
        request: Request,
        response: Response,
        owner: Annotated[str, Depends(authenticated_owner)],
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    ) -> dict[str, object]:
        if len(request.headers.getlist("idempotency-key")) != 1:
            raise HTTPException(422, "Envie apenas um Idempotency-Key")
        if (
            not idempotency_key.isascii()
            or not idempotency_key.isprintable()
            or not idempotency_key.strip()
        ):
            raise HTTPException(422, "Idempotency-Key deve usar ASCII imprimível")
        if payload.demo_duration_seconds and not settings.demo_mode:
            raise HTTPException(422, "Duração controlada exige DEMO_MODE=true")
        if not application.state.accepting:
            raise HTTPException(503, "API encerrando")
        try:
            job, created = repository.submit_job(
                settings, owner, idempotency_key, payload.text, payload.demo_duration_seconds
            )
        except JobConflict as error:
            raise HTTPException(409, "Chave já utilizada com outro conteúdo") from error
        except AdmissionPaused as error:
            raise HTTPException(
                503, "Admissão pausada para manutenção", headers={"Retry-After": "2"}
            ) from error
        except OwnerQueueFull as error:
            raise HTTPException(
                429, "Limite de trabalhos pendentes do proprietário", headers={"Retry-After": "2"}
            ) from error
        except QueueFull as error:
            raise HTTPException(429, "Fila cheia", headers={"Retry-After": "2"}) from error
        response.status_code = 201 if created else 200
        event(
            "api",
            settings.version,
            "job_submitted",
            request_id=request.state.request_id,
            job_id=job.id,
            reused=not created,
        )
        return job_response(job, settings)

    @application.get("/v1/jobs/{job_id}")
    def get(job_id: UUID, owner: Annotated[str, Depends(authenticated_owner)]) -> dict[str, object]:
        job = repository.get_job(settings, owner, job_id)
        if job is None:
            raise HTTPException(404, "Job não encontrado")
        return job_response(job, settings)

    @application.get("/internal/metrics")
    def metrics(owner: Annotated[str, Depends(authenticated_owner)]) -> dict[str, object]:
        return repository.metrics(settings)

    return application


app = create_app()


if __name__ == "__main__":
    configure()
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False, timeout_graceful_shutdown=15)
