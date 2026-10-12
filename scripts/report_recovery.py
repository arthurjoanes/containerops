"""Classifica o resultado do restore pelas etapas registradas."""

from __future__ import annotations

from dataclasses import dataclass

from report_evidence import successful_job


@dataclass(frozen=True)
class RecoveryResult:
    state: str
    label: str
    explanation: str


def restore_result(record: dict) -> RecoveryResult:
    if not record:
        return RecoveryResult(
            "missing",
            "Sem restauração registrada",
            "Uma cópia íntegra precisa de um ensaio de restauração para verificar a recuperação.",
        )
    if not isinstance(record, dict):
        return RecoveryResult(
            "invalid", "Registro de restauração inválido", "Objeto JSON esperado."
        )

    status = record.get("status")
    cleanup = record.get("cleanup_succeeded")
    if "status" in record and (
        type(status) is not str or status not in {"passed", "failed", "in_progress"}
    ):
        return RecoveryResult(
            "invalid", "Status de restauração inválido", "Confira o arquivo da tentativa."
        )
    if "cleanup_succeeded" in record and type(cleanup) is not bool:
        return RecoveryResult(
            "invalid", "Resultado de limpeza inválido", "cleanup_succeeded precisa ser booleano."
        )
    for field in ("checksum_verified", "snapshot_equal"):
        if field in record and record[field] is not None and type(record[field]) is not bool:
            return RecoveryResult(
                "invalid", "Verificação de restauração inválida", f"{field} precisa ser booleano."
            )

    if cleanup is False:
        return RecoveryResult(
            "fail",
            "Limpeza falhou",
            "A tentativa não concluiu a limpeza dos recursos temporários. Confira a fase registrada.",
        )
    if status == "failed":
        return RecoveryResult(
            "fail",
            "Restauração falhou",
            "A tentativa registra falha. Confira as fases e a categoria do erro antes de repetir.",
        )
    if status == "in_progress":
        return RecoveryResult(
            "running",
            "Restauração em andamento",
            "A tentativa ainda não registra conclusão; os resultados das etapas permanecem separados.",
        )

    checksum = record.get("checksum_verified")
    snapshot = record.get("snapshot_equal")
    job = record.get("new_job")
    if checksum is False or snapshot is False:
        failed = "checksum do backup" if checksum is False else "snapshot restaurado"
        return RecoveryResult(
            "fail", "Verificação de restauração falhou", f"A conferência do {failed} falhou."
        )
    if isinstance(job, dict) and job.get("state") == "failed":
        return RecoveryResult(
            "fail", "Novo job falhou", "O job de verificação pós-restore registra falha."
        )

    if (
        status == "passed"
        and cleanup is True
        and checksum is True
        and snapshot is True
        and successful_job(job)
    ):
        return RecoveryResult(
            "pass",
            "Restauração verificada",
            "Checksum, snapshot, novo job e limpeza foram aprovados na tentativa registrada.",
        )

    missing = []
    if status is None:
        missing.append("status final")
    if cleanup is None:
        missing.append("limpeza")
    if missing:
        return RecoveryResult(
            "partial",
            "Registro de restauração incompleto",
            "O registro não informa " + " nem ".join(missing) + ". Confira as etapas disponíveis.",
        )
    if isinstance(job, dict) and job.get("state") in ("queued", "running"):
        state = "na fila" if job["state"] == "queued" else "em execução"
        return RecoveryResult(
            "partial",
            "Novo job ainda não concluído",
            f"O job pós-restore permanece {state} no registro.",
        )

    missing = []
    if checksum is not True:
        missing.append("checksum do backup")
    if snapshot is not True:
        missing.append("snapshot restaurado")
    if not successful_job(job):
        missing.append("resultado completo do novo job")
    return RecoveryResult(
        "partial",
        "Restauração parcialmente verificada",
        "Falta conferir " + ", ".join(missing) + ".",
    )
