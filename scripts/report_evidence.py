from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def at(record: dict, path: str) -> object:
    value = record
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _unique_keys(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"campo repetido: {key}")
        result[key] = value
    return result


def restricted_runtime(service: dict) -> bool:
    runtime = service.get("runtime", {})
    status = runtime.get("status", {})
    return (
        service.get("read_only") is True
        and isinstance(runtime.get("uid"), int)
        and runtime["uid"] > 0
        and str(status.get("NoNewPrivs")) == "1"
        and status.get("CapEff") == "0000000000000000"
        and isinstance(runtime.get("blocked"), list)
        and bool(runtime["blocked"])
        and all(isinstance(path, str) and path.startswith("/") for path in runtime["blocked"])
    )


def successful_job(job: object) -> bool:
    if not isinstance(job, dict):
        return False
    return (
        job.get("state") == "succeeded"
        and type(job.get("attempts")) is int
        and job["attempts"] >= 1
        and type(at(job, "result.word_count")) is int
        and at(job, "result.word_count") >= 0
        and isinstance(at(job, "result.checksum"), str)
        and re.fullmatch(r"[a-f0-9]{64}", at(job, "result.checksum")) is not None
    )


def parse_record(content: str, name: str) -> dict:
    def invalid_constant(value: str):
        raise ValueError(f"número JSON inválido: {value}")

    record = json.loads(content, object_pairs_hook=_unique_keys, parse_constant=invalid_constant)
    if not isinstance(record, dict):
        raise ValueError("a raiz precisa ser um objeto JSON")
    for key in ("project", "source_project", "run_id", "version", "image_id"):
        if record.get(key) is not None and (not isinstance(record[key], str) or not record[key]):
            raise ValueError(f"{key}: texto não vazio esperado")
    if record.get("image_id") is not None and not re.fullmatch(
        r"sha256:[a-f0-9]{64}", record["image_id"]
    ):
        raise ValueError("image_id: digest SHA-256 esperado")
    kind = name.split("-", 1)[0]
    objects = {
        "demo": ["job", "job.result"],
        "journey": ["job", "job.result"],
        "hardening": ["checks", "checks.network"],
        "recovery": [
            "sigkill",
            "sigterm",
            "after_database_restart",
            "database_outage",
            "recreation",
        ],
        "restore": ["new_job", "new_job.result"],
        "release": ["candidate_job", "candidate_job.result"],
        "rollback": [
            "candidate_job",
            "candidate_job.result",
            "rollback_job",
            "rollback_job.result",
        ],
        "scan": ["findings_by_severity"],
        "final": ["health"],
        "test": ["checks"],
    }.get(kind, [])
    if kind == "hardening":
        for service in ("api", "worker", "proxy", "db"):
            objects += [
                f"checks.{service}",
                f"checks.{service}.runtime",
                f"checks.{service}.runtime.status",
            ]
    for path in objects:
        value = at(record, path)
        parent_path, _, key = path.rpartition(".")
        parent = at(record, parent_path) if parent_path else record
        present = isinstance(parent, dict) and key in parent
        nullable_result = path.endswith("result") and value is None
        if present and not isinstance(value, dict) and not nullable_result:
            raise ValueError(f"{path}: objeto esperado")
    for key in ("recorded_at", "executed_at", "created_at", "started_at", "completed_at"):
        if record.get(key) is not None and record_time({key: record[key]}) is None:
            raise ValueError(f"{key}: data ISO 8601 com fuso esperada")

    def inspect(value: object, path: str = ""):
        if isinstance(value, dict):
            for key, item in value.items():
                child = f"{path}.{key}" if path else key
                if key in ("runs", "blocking_findings") and not isinstance(item, list):
                    raise ValueError(f"{child}: lista esperada")
                if item is None:
                    continue
                integer = key in {
                    "word_count",
                    "attempts",
                    "unique_jobs",
                    "concurrent_requests",
                    "preserved_jobs",
                    "nano_cpus",
                    "pids_limit",
                    "packages_total",
                    "unfixed_findings",
                    "cached_steps",
                    "uid",
                } or path.endswith("findings_by_severity")
                numeric = key.endswith(("_seconds", "_hours", "_bytes")) or integer
                boolean = key in {
                    "success",
                    "full",
                    "read_only",
                    "rolled_back",
                    "checksum_verified",
                    "snapshot_equal",
                    "verified_with_explicit_ca",
                    "no_published_port",
                    "limits_enforced",
                    "attestation_subjects_verified",
                    "daemon_config_and_layers_verified",
                    "sentinel_absent_layers_config_history_attestations",
                    "sentinel_absent_daemon_history_exported_filesystem",
                }
                if numeric and (
                    type(item) not in (int, float) or not math.isfinite(item) or item < 0
                ):
                    raise ValueError(f"{child}: número finito não negativo esperado")
                if integer and type(item) is not int:
                    raise ValueError(f"{child}: inteiro esperado")
                if boolean and type(item) is not bool:
                    raise ValueError(f"{child}: booleano esperado")
                if key in ("runs", "blocking_findings") and not isinstance(item, list):
                    raise ValueError(f"{child}: lista esperada")
                if key in ("runs", "blocking_findings") and any(
                    not isinstance(member, dict) for member in item
                ):
                    raise ValueError(f"{child}: lista de objetos esperada")
                inspect(item, child)
        elif isinstance(value, list):
            for item in value:
                inspect(item, path)

    inspect(record)
    return record


def record_time(record: dict) -> datetime | None:
    for key in ("completed_at", "recorded_at", "executed_at", "created_at", "started_at"):
        value = record.get(key)
        if not isinstance(value, str):
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC)
    return None


def select_artifacts(records: dict[str, dict]) -> tuple[str, str, str]:
    """A recent scan of another image cannot approve the selected build."""
    builds = [name for name in records if name.startswith("build-")]
    if not builds or any(record_time(records[name]) is None for name in builds):
        return "", "", ""
    oldest = datetime.min.replace(tzinfo=UTC)
    build_name = max(builds, key=lambda name: (record_time(records[name]) or oldest, name))
    build = records[build_name]

    def matching(prefix: str) -> str:
        candidates = [
            name
            for name, record in records.items()
            if name.startswith(prefix + "-")
            and name != "scan-initial-failed"
            and build.get("image_id")
            and build.get("version")
            and record.get("image_id") == build["image_id"]
            and record.get("version") == build.get("version")
            and record_time(record) is not None
            and record_time(record) >= record_time(build)
        ]
        return max(
            candidates,
            key=lambda name: (record_time(records[name]) or oldest, name),
            default="",
        )

    return build_name, matching("audit"), matching("scan")


def verification_checks(records: dict[str, dict]) -> dict[str, bool]:
    journey = records.get("journey", {})
    hardening = records.get("hardening", {})
    recovery = records.get("recovery", {})
    checks = hardening.get("checks", {})
    network = checks.get("network", {})
    killed = recovery.get("sigkill", {})
    return {
        "journey": journey.get("unique_jobs") == 1
        and successful_job(journey.get("job"))
        and (journey.get("concurrent_requests") or 0) >= 2
        and journey.get("conflict") == 409
        and journey.get("limits_enforced") is True,
        "runtime": all(
            restricted_runtime(checks.get(service, {})) for service in ("api", "worker", "proxy")
        ),
        "network": network.get("front_to_database") == "denied"
        and network.get("data_to_database") == "allowed"
        and checks.get("db", {}).get("no_published_port") is True,
        "worker": successful_job(killed)
        and killed.get("attempts", 0) >= 2
        and successful_job(recovery.get("sigterm")),
        "database": all(
            at(recovery, f"database_outage.{field}") == expected
            for field, expected in {"liveness": 200, "readiness": 503, "admission": 503}.items()
        )
        and successful_job(recovery.get("after_database_restart")),
        "persistence": at(recovery, "recreation.snapshot_equal") is True,
    }


@dataclass(frozen=True)
class Verification:
    state: str
    title: str
    explanation: str
    records: dict[str, dict]
    problems: tuple[str, ...] = ()


def verification_result(directory: Path, record: dict, errors: dict, now: datetime) -> Verification:
    """Read the latest attempt independently of successful files left by older runs."""
    if "verification-run.json" in errors:
        return Verification(
            "invalid",
            "JSON da execução inválido",
            "Corrija verification-run.json.",
            {},
        )
    if not record:
        return Verification(
            "missing",
            "Nenhuma verificação registrada",
            "Execute python scripts/ops.py verify e python scripts/ops.py report.",
            {},
        )
    status, success = record.get("status"), record.get("success")
    if status not in ("in_progress", "passed", "failed") or type(success) is not bool:
        return Verification(
            "invalid",
            "Estado da tentativa inválido",
            "status deve ser in_progress, passed ou failed; success deve ser booleano.",
            {},
        )
    if (status == "passed") != success:
        return Verification(
            "invalid",
            "Estado da tentativa contraditório",
            "status e success não correspondem.",
            {},
        )
    started = record_time({"started_at": record.get("started_at")})
    completed = record_time({"completed_at": record.get("completed_at")})
    if (started and started > now) or (
        completed and (completed > now or (started and completed < started))
    ):
        return Verification(
            "invalid",
            "Datas da tentativa incoerentes",
            "Confira o relógio e a sequência início/conclusão.",
            {},
        )
    run_id, project, entries = record.get("run_id"), record.get("project"), record.get("evidence")
    if (
        not isinstance(run_id, str)
        or not re.fullmatch(r"[0-9a-f]{32}", run_id)
        or not isinstance(entries, list)
        or not started
    ):
        label = "Última tentativa falhou" if status == "failed" else "Sem manifesto dos testes"
        return Verification(
            "fail" if status == "failed" else "partial",
            label,
            "Sem manifesto válido para vincular os testes à execução.",
            {},
        )
    archived: dict[str, dict] = {}
    problems = []
    base = directory.resolve()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            problems.append("Entrada inválida no manifesto.")
            continue
        relative = Path(entry["path"])
        resolved = (base / relative).resolve()
        expected_prefix = ("runs", run_id)
        if (
            relative.is_absolute()
            or relative.parts[:2] != expected_prefix
            or len(relative.parts) != 3
            or not resolved.is_relative_to(base)
            or resolved.suffix != ".json"
        ):
            problems.append("Arquivo fora do diretório da execução.")
            continue
        name = relative.stem
        try:
            if name in archived:
                raise ValueError("arquivo repetido no manifesto")
            content = resolved.read_bytes()
            digest = "sha256:" + hashlib.sha256(content).hexdigest()
            if entry.get("sha256") != digest:
                raise ValueError("SHA-256 diverge do manifesto")
            value = parse_record(content.decode("utf-8-sig"), name)
            if value.get("run_id") != run_id or not project or value.get("project") != project:
                raise ValueError("run_id ou projeto diferente da tentativa")
            stamped = record_time(value)
            if stamped is None or stamped < started or stamped > (completed or now):
                raise ValueError("data fora da janela da tentativa")
            value["_source"] = relative.as_posix()
            archived[name] = value
        except (OSError, UnicodeError, ValueError) as error:
            problems.append(f"{name}.json: {error}")
    if problems:
        return Verification(
            "invalid",
            "Arquivos da execução inválidos",
            "",
            {},
            tuple(problems),
        )
    if status == "in_progress":
        return Verification(
            "running",
            "Verificação em andamento",
            "",
            archived,
        )
    if status == "failed":
        return Verification(
            "fail",
            "Última tentativa falhou",
            "Consulte o erro antes de repetir verify.",
            archived,
        )
    if (
        completed is None
        or type(record.get("full")) is not bool
        or type(record.get("elapsed_seconds")) not in (int, float)
    ):
        return Verification(
            "partial",
            "Execução incompleta",
            "Faltam duração, término ou escopo.",
            archived,
        )
    required = {"test-results", "journey"} | (
        {"hardening", "recovery"} if record["full"] else set()
    )
    missing = required - archived.keys()
    checks = at(archived.get("test-results", {}), "checks")
    required_checks = {"host_operations", "report", "lint", "format", "types", "tests"}
    if missing or not isinstance(checks, dict) or not required_checks <= checks.keys():
        return Verification(
            "partial",
            "Faltam etapas da verificação",
            "Faltam arquivos ou resultados exigidos pelo verify.",
            archived,
        )
    if any(
        type(at(checks, name + ".exit_code")) is not int or at(checks, name + ".exit_code") != 0
        for name in required_checks
    ):
        return Verification(
            "invalid",
            "Conclusão contradiz os testes",
            "Há comandos com erro ou resultado inválido.",
            archived,
        )
    stages = verification_checks(archived)
    required_stages = set(stages) if record["full"] else {"journey"}
    if any(not stages[name] for name in required_stages):
        return Verification(
            "invalid",
            "Resultado não corresponde aos testes",
            "Uma etapa não passou no critério do teste.",
            archived,
        )
    if not record["full"]:
        return Verification(
            "partial",
            "Verificação parcial concluída",
            "Inclui qualidade e HTTP; não inclui isolamento e recuperação.",
            archived,
        )
    if (now - completed).total_seconds() > 24 * 3600:
        return Verification(
            "stale",
            "Verificação aprovada há mais de 24 h",
            "Execute verify para atualizar.",
            archived,
        )
    return Verification(
        "pass",
        "Última verificação aprovada",
        "",
        archived,
    )


def read_records(directory: Path) -> tuple[dict[str, dict], dict[str, str]]:
    records: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            value = parse_record(path.read_text(encoding="utf-8-sig"), path.stem)
            value["_source"] = path.name
            records[path.stem] = value
        except (OSError, UnicodeError, ValueError) as exc:
            errors[path.name] = str(exc)
    return records, errors
