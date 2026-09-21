from unittest.mock import patch

import psycopg
import pytest
from fastapi.testclient import TestClient

from containerops.domain import SchemaIncompatible


def test_liveness_does_not_need_database(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive", "version": "1.0.0"}
    assert response.headers["X-Request-ID"]


def test_authentication_required(client: TestClient) -> None:
    assert client.post("/v1/jobs", json={"text": "sintético"}).status_code == 401
    response = client.get("/v1/jobs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 401
    assert client.get("/internal/metrics").status_code == 401


def test_invalid_input_does_not_echo_text(client: TestClient, auth: dict[str, str]) -> None:
    marker = "synthetic-private-marker"
    response = client.post("/v1/jobs", headers=auth, json={"text": marker, "extra": marker})
    assert response.status_code == 422
    assert marker not in response.text


def test_text_and_body_limits(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post("/v1/jobs", headers=auth, json={"text": "é" * 8193})
    assert response.status_code == 422
    response = client.post("/v1/jobs", headers=auth, content=b"x" * 32769)
    assert response.status_code == 413


def test_idempotency_header_required(client: TestClient, auth: dict[str, str]) -> None:
    auth.pop("Idempotency-Key")
    response = client.post("/v1/jobs", headers=auth, json={"text": "ok"})
    assert response.status_code == 422


def test_chunked_body_limit(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post("/v1/jobs", headers=auth, content=iter([b"x" * 16000] * 3))
    assert response.status_code == 413


@pytest.mark.parametrize(
    ("error", "status", "retry"),
    [
        (psycopg.OperationalError("secret-database-url"), 503, "2"),
        (psycopg.errors.LockNotAvailable("internal-query"), 503, "2"),
        (psycopg.errors.UndefinedTable("private-table-name"), 500, None),
        (psycopg.errors.CheckViolation("private-payload"), 500, None),
        (SchemaIncompatible("private-schema-description"), 503, None),
    ],
)
def test_database_errors_have_honest_retry_semantics(
    client: TestClient,
    auth: dict[str, str],
    error: Exception,
    status: int,
    retry: str | None,
) -> None:
    with patch("containerops.repository.submit_job", side_effect=error):
        response = client.post("/v1/jobs", headers=auth, json={"text": "synthetic"})
    assert response.status_code == status
    assert response.headers.get("Retry-After") == retry
    assert str(error) not in response.text
    assert response.headers["X-Request-ID"]
