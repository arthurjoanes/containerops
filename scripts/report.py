from __future__ import annotations

import math
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from string import Template
from urllib.parse import quote

from report_evidence import (
    read_records,
    record_time,
    select_artifacts,
    successful_job,
    verification_checks,
    verification_result,
)


def shown(value: object, suffix: str = "") -> str:
    if value is None or value == "":
        return "Não informado"
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return "Dado inválido"
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value < 0:
            return "Dado inválido"
        precision = 1 if isinstance(value, float) else 0
        formatted = f"{value:,.{precision}f}".replace(",", "_").replace(".", ",").replace("_", ".")
        return formatted + suffix
    return escape(str(value)) + suffix


def timestamp(record: dict) -> str:
    value = record_time(record)
    if value is None:
        return "Sem data"
    return f'<time datetime="{value.isoformat()}">{value:%d/%m/%Y · %H:%M:%S} UTC</time>'


def badge(passed: bool | None) -> str:
    state, text = (
        ("pass", "Passou")
        if passed is True
        else ("fail", "Falhou")
        if passed is False
        else ("pending", "Sem resultado")
    )
    return f'<span class="badge {state}">{text}</span>'


def proof(name: str, records: dict[str, dict]) -> str:
    if name not in records:
        return "Sem arquivo"
    return (
        f'<a href="evidence/{quote(records[name].get("_source", name + ".json"))}">{escape(name)}.json</a>'
        f'<span class="proof-date">{timestamp(records[name])}</span>'
    )


def observed(record: dict, predicate: bool) -> bool | None:
    return bool(predicate) if record else None


def control_row(title: str, passed: bool | None, body: str, evidence: str) -> str:
    return (
        '<li class="control-row">'
        f'<div class="control-title"><h3>{escape(title)}</h3>{badge(passed)}</div>'
        f'<p>{body}</p><div class="proof">{evidence}</div></li>'
    )


def record_context(record: dict, *, label: str = "Registro") -> str:
    """Keep the identity and clock of each operation beside its own result."""
    return (
        '<dl class="record-context">'
        f"<div><dt>{escape(label)}</dt><dd>{timestamp(record)}</dd></div>"
        f"<div><dt>Projeto</dt><dd><code>{shown(record.get('project', record.get('source_project')))}</code></dd></div>"
        f"<div><dt>Execução</dt><dd><code>{shown(record.get('run_id'))}</code></dd></div>"
        "</dl>"
    )


def operation_link(identifier: str, title: str, state: str, detail: str) -> str:
    labels = {
        "pass": "Aprovado",
        "fail": "Falhou",
        "pending": "Sem resultado",
        "missing": "Ausente",
        "invalid": "Inválido",
        "partial": "Parcial",
        "running": "Em andamento",
        "stale": "Registro antigo",
        "info": "Consulta",
    }
    return (
        f'<a class="operation-link" href="#{identifier}" data-operation="{identifier}">'
        f'<span class="operation-title"><span class="state-dot {state}" aria-hidden="true"></span>'
        f'{escape(title)}</span><span class="operation-meta">{escape(labels.get(state, state))}'
        f" · {detail}</span></a>"
    )


def state_of(value: bool | None) -> str:
    return "pass" if value is True else "fail" if value is False else "missing"


def metrics(items: list[tuple[str, object, str]]) -> str:
    return (
        '<div class="metrics">'
        + "".join(
            f'<div class="metric"><span>{escape(label)}</span><strong>{shown(value, suffix)}</strong></div>'
            for label, value, suffix in items
        )
        + "</div>"
    )


def resource_table(hardening: dict) -> str:
    checks = hardening.get("checks", {})
    rows = []
    for service in ("proxy", "api", "worker", "db"):
        values = checks.get(service, {})
        memory = values.get("memory_bytes")
        cpu = values.get("nano_cpus")
        actual = values.get("runtime", {})
        uid = actual.get("uid")
        group = str(actual.get("status", {}).get("Gid", "")).split()
        user = f"{uid}:{group[0]}" if uid is not None and group else None
        filesystem = "Sem dados"
        if values.get("read_only") is True:
            filesystem = '<span class="badge pass">Somente leitura</span>'
        elif values.get("read_only") is False:
            filesystem = (
                "Gravável (banco)"
                if service == "db"
                else '<span class="badge fail">Gravável</span>'
            )
        rows.append(
            f'<tr><th scope="row">{service}</th><td>{shown(user)}</td>'
            f"<td>{shown(round(memory / 1024**2, 1) if isinstance(memory, (int, float)) else None, ' MiB')}</td>"
            f"<td>{shown(cpu / 1_000_000_000 if isinstance(cpu, (int, float)) else None)}</td>"
            f"<td>{shown(values.get('pids_limit'))}</td>"
            f"<td>{filesystem}</td></tr>"
        )
    return (
        '<div class="table-wrap" role="region" aria-label="Limites dos containers, tabela com rolagem horizontal" tabindex="0"><table><caption>Limites por serviço</caption><thead><tr><th scope="col">Serviço</th><th scope="col">UID:GID</th><th scope="col">Memória máxima</th><th scope="col">Limite de CPU</th><th scope="col">PIDs máx.</th><th scope="col">Filesystem</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div>"
    )


def job_panel(job: dict, evidence: str) -> str:
    if not job:
        return '<div class="empty-state"><h3>Nenhum resultado registrado</h3><p>Não há job vinculado a este registro. Consulte o arquivo da operação; uma nova demonstração não completa uma evidência anterior.</p></div>'
    result = job.get("result") or {}
    state = job.get("state")
    label = {
        "queued": "Na fila",
        "running": "Processando",
        "succeeded": "Concluído",
        "failed": "Falhou",
    }.get(str(state), "Estado não reconhecido")
    if state == "succeeded" and not successful_job(job):
        label = "Resultado incompleto"
    return (
        '<div class="job"><div>'
        f"<h3>{shown(label)}</h3>"
        "</div>"
        + metrics(
            [
                ("Palavras", result.get("word_count"), ""),
                ("Tentativas", job.get("attempts"), ""),
                ("Versão", job.get("version"), ""),
            ]
        )
        + '<details class="checksum"><summary>Detalhes do job</summary>'
        f"<p>Estado na API</p><code>{shown(state)}</code>"
        f"<p>ID do job</p><code>{shown(job.get('id'))}</code>"
        f"<p>SHA-256 do texto UTF-8</p><code>{shown(result.get('checksum'))}</code></details>"
        f'<div class="proof">{evidence}</div></div>'
    )


def generate(root: Path, runtime: Path, *, now: datetime | None = None) -> Path:
    root, runtime = Path(root), Path(runtime)
    generated_at = now or datetime.now(UTC)
    records, errors = read_records(root / "docs" / "evidence")
    for name, record in list(records.items()):
        stamped = record_time(record)
        if stamped is not None and stamped > generated_at:
            errors[name + ".json"] = "data posterior à geração; confira o relógio"
            del records[name]
    verification = records.get("verification-run", {})
    attempt = verification_result(root / "docs" / "evidence", verification, errors, generated_at)
    current = attempt.records
    journey = current.get("journey", {})
    hardening = current.get("hardening", {})
    recovery = current.get("recovery", {})
    backup = records.get("backup", {})
    restore = records.get("restore", {})
    release_source = "release"
    if "release-failed" in records and (
        not records.get("release")
        or record_time(records["release-failed"]) is None
        or record_time(records["release"]) is None
        or record_time(records["release-failed"]) >= record_time(records["release"])
    ):
        release_source = "release-failed"
    release = {} if "release-failed.json" in errors else records.get(release_source, {})
    rollback = records.get("rollback", {})
    tls = records.get("tls", {})
    available_jobs = {
        name: value
        for name, value in {"demo": records.get("demo", {}), "journey": journey}.items()
        if value
    }
    job_source = max(
        available_jobs,
        key=lambda name: record_time(available_jobs[name]) or datetime.min.replace(tzinfo=UTC),
        default="demo",
    )
    job_record = available_jobs.get(job_source, {}) if "demo.json" not in errors else {}
    job = job_record.get("job") or {}
    recovery_kill = recovery.get("sigkill", {})
    recreation = recovery.get("recreation", {})
    stage_checks = verification_checks(current)

    build_name, audit_name, scan_name = select_artifacts(records)
    if any(name.startswith("build-") for name in errors):
        build_name, audit_name, scan_name = "", "", ""
    build, audit, scan = (records.get(name, {}) for name in (build_name, audit_name, scan_name))
    blockers = scan.get("blocking_findings")
    blocker_count = len(blockers) if isinstance(blockers, list) else blockers
    severities = scan.get("findings_by_severity", {})
    audit_passed = observed(
        audit,
        all(
            audit.get(field) is True
            for field in (
                "attestation_subjects_verified",
                "daemon_config_and_layers_verified",
                "sentinel_absent_layers_config_history_attestations",
                "sentinel_absent_daemon_history_exported_filesystem",
            )
        ),
    )
    scan_passed = observed(
        scan,
        scan.get("status") == "passed"
        and isinstance(blockers, list)
        and not blockers
        and type(scan.get("database_age_hours")) in (int, float)
        and type(scan.get("max_db_age_hours")) in (int, float)
        and scan["database_age_hours"] <= scan["max_db_age_hours"],
    )
    controls = [
        (
            "HTTP pelo proxy",
            observed(
                journey,
                stage_checks["journey"],
            ),
            f"{shown(journey.get('concurrent_requests'))} requisições simultâneas; {shown(journey.get('unique_jobs'))} job salvo. Testa autorização e limites HTTP.",
            "journey",
        ),
        (
            "Usuário e filesystem",
            observed(
                hardening,
                stage_checks["runtime"],
            ),
            "Testa UID, capabilities, no-new-privileges e escrita fora dos mounts.",
            "hardening",
        ),
        (
            "Rede isolada",
            observed(
                hardening,
                stage_checks["network"],
            ),
            "Testa acesso ao banco pelas redes data e front e verifica se o banco publica portas.",
            "hardening",
        ),
        (
            "Recuperação do worker",
            observed(
                recovery,
                stage_checks["worker"],
            ),
            f"Testa conclusão após SIGKILL ({shown(recovery_kill.get('attempts'))} tentativas) e após SIGTERM.",
            "recovery",
        ),
        (
            "Banco indisponível",
            observed(
                recovery,
                stage_checks["database"],
            ),
            "Banco parado: espera liveness 200, readiness e criação de jobs 503. Testa um novo job após o restart.",
            "recovery",
        ),
        (
            "Persistência",
            observed(recovery, stage_checks["persistence"]),
            f"Compara snapshots antes e depois de recriar os containers: {shown(recreation.get('preserved_jobs'))} jobs mantidos.",
            "recovery",
        ),
        (
            "Backup restaurado",
            observed(
                restore,
                restore.get("checksum_verified") is True
                and restore.get("snapshot_equal") is True
                and successful_job(restore.get("new_job", {})),
            ),
            "Restaura o dump em outro projeto e volume. Confere checksum, snapshot e um novo job.",
            "restore",
        ),
        (
            "Troca de release",
            observed(
                release,
                release.get("rolled_back") is False
                and successful_job(release.get("candidate_job", {}))
                and release_source == "release",
            ),
            f"Confere a imagem candidata, aplica a migração e verifica {shown(release.get('preserved_jobs'))} jobs.",
            release_source,
        ),
        (
            "Rollback",
            observed(
                rollback,
                rollback.get("rolled_back") is True
                and successful_job(rollback.get("candidate_job", {}))
                and successful_job(rollback.get("rollback_job", {})),
            ),
            "Injeta falha no smoke test e verifica o retorno à imagem anterior, sem downgrade do banco.",
            "rollback",
        ),
        (
            "TLS local",
            observed(
                tls,
                tls.get("verified_with_explicit_ca") is True
                and tls.get("default_trust_rejected") is True,
            ),
            "Testa TLS com a CA local passada ao cliente.",
            "tls",
        ),
        (
            "Imagem e OCI",
            audit_passed,
            "Confere config digest, camadas e subjects das attestations. Busca a sentinela nas camadas, histórico, filesystem e metadados.",
            audit_name,
        ),
        (
            "Scan",
            scan_passed,
            (
                f"Status: {shown(scan.get('status'))}. Bloqueantes: {shown(blocker_count)}. Sem correção: {shown(scan.get('unfixed_findings'))}. HIGH: {shown(severities.get('HIGH'))}. CRITICAL: {shown(severities.get('CRITICAL'))}."
            )
            if scan
            else "Sem scan compatível com esta imagem.",
            scan_name,
        ),
    ]
    controls_html = (
        "".join(
            control_row(title, status, body, proof(name, current))
            for title, status, body, name in controls[:6]
        )
        if current
        else '<li class="empty-state">Sem etapas vinculadas a esta execução.</li>'
    )
    operation_records = dict(records)
    if not release:
        operation_records.pop(release_source, None)
    operation_rows = {
        name: control_row(title, status, body, proof(name, operation_records))
        for title, status, body, name in controls[6:]
    }
    restore_steps = "".join(
        control_row(title, passed, body, "")
        for title, passed, body in (
            (
                "Checksum do backup",
                restore.get("checksum_verified"),
                "Compara o dump recebido com o checksum registrado.",
            ),
            (
                "Dados no volume restaurado",
                restore.get("snapshot_equal"),
                "Compara o snapshot restaurado com os dados esperados.",
            ),
            (
                "Novo job após restauração",
                observed(restore.get("new_job", {}), successful_job(restore.get("new_job", {}))),
                "Confere conclusão, contagem de palavras e checksum do novo resultado.",
            ),
        )
    )
    evidence_rows = (
        "".join(
            f'<tr><th scope="row"><a href="evidence/{quote(name)}.json">{escape("Scan inicial" if name == "scan-initial-failed" else name)}</a></th>'
            f"<td>{timestamp(record)}</td>"
            f"<td>{shown(record.get('project', record.get('source_project')))}</td></tr>"
            for name, record in sorted(records.items())
        )
        or '<tr><td colspan="3">Nenhum JSON encontrado. Execute verify e report.</td></tr>'
    )
    errors_html = "".join(
        f'<li><a href="evidence/{quote(name)}">{escape(name)}</a>: {escape(message)}</li>'
        for name, message in errors.items()
    ) + "".join(f"<li>{escape(message)}</li>" for message in attempt.problems)
    errors_html = (
        f'<section class="record-errors" aria-label="Erros nos JSONs"><h2>JSONs inválidos</h2><ul>{errors_html}</ul></section>'
        if errors_html
        else ""
    )
    supply_names = [
        name
        for name in records
        if any(
            key in name
            for key in (
                "build",
                "audit",
                "supply",
                "scan",
                "sbom",
                "oci",
                "cache",
                "provenance",
                "sentinel",
            )
        )
    ]
    supply_html = (
        "".join(
            f'<li><strong>{escape("Scan inicial" if name == "scan-initial-failed" else name)}</strong><div class="proof">{proof(name, records)}</div></li>'
            for name in supply_names
        )
        or "<li>Sem arquivos de build, SBOM, provenance ou scan.</li>"
    )
    supply_metrics = metrics(
        [
            ("Build OCI + carregamento", build.get("duration_seconds"), " s"),
            ("Pacotes no SBOM", audit.get("packages_total"), ""),
            ("Idade da base de scan", scan.get("database_age_hours"), " h"),
        ]
    )
    scan_findings = (
        '<div class="scan-findings"><h3>Vulnerabilidades</h3>'
        + metrics(
            [
                ("HIGH", severities.get("HIGH"), ""),
                ("CRITICAL", severities.get("CRITICAL"), ""),
                ("Sem correção", scan.get("unfixed_findings"), ""),
            ]
        )
        + f"<p>Bloqueantes: <strong>{shown(blocker_count)}</strong>. O relatório Trivy fica no runtime após <code>scan</code>.</p>"
        + f'<div class="proof">{proof(scan_name, records)}</div></div>'
    )
    if not scan:
        scan_findings = '<p class="empty-state">Sem scan para esta imagem. Execute <code>python scripts/ops.py scan --version &lt;versão&gt;</code>.</p>'
    cache = records.get("cache-experiment", {})
    cache_rows = "".join(
        f'<tr><th scope="row">{shown(item.get("case"))}</th><td>{shown(item.get("duration_seconds"), " s")}</td>'
        f"<td>{shown(item.get('cached_steps'))}</td></tr>"
        for item in cache.get("runs", [])
        if isinstance(item, dict)
    )
    cache_html = (
        '<div class="table-wrap" role="region" aria-label="Experimentos de cache, tabela com rolagem horizontal" tabindex="0"><table><caption>Cache do build</caption><thead><tr><th scope="col">Teste</th><th scope="col">Duração</th><th scope="col">Etapas reutilizadas</th></tr></thead><tbody>'
        + cache_rows
        + "</tbody></table></div>"
        if cache_rows
        else '<p class="metadata">Cache sem medições.</p>'
    )
    verified_at = record_time(verification)
    if verified_at is None:
        verification_age = "Sem execução"
    elif verified_at > generated_at:
        verification_age = "Data posterior à geração: confira o relógio"
    else:
        hours = (generated_at - verified_at).total_seconds() / 3600
        verification_age = f"{hours:.1f}".replace(".", ",") + " h antes da geração"
    final_state = records.get("final-state", {})
    final_health = final_state.get("health", {})
    supply_identity = "Build, auditoria ou scan ausentes para esta imagem."
    if build and audit and scan:
        supply_identity = "Build, auditoria e scan da mesma versão e image_id."
    if build and not audit:
        supply_identity = "Sem auditoria compatível com este build."
    if build and not scan:
        supply_identity += " Sem scan compatível."
    scan_age = scan.get("database_age_hours")
    scan_limit = scan.get("max_db_age_hours")
    if (
        type(scan_age) in (int, float)
        and type(scan_limit) in (int, float)
        and scan_age > scan_limit
    ):
        supply_identity += " Base do scanner vencida no momento do scan."
    release_warning = (
        '<p class="record-warning"><strong>A última tentativa de release falhou.</strong> '
        '<a href="#operations">Conferir esta tentativa →</a></p>'
        if release_source == "release-failed"
        else ""
    )
    elapsed = verification.get("elapsed_seconds")
    precise_duration = (
        f"{elapsed:.3f}".replace(".", ",") + " s"
        if type(elapsed) in (int, float)
        else "Não informado"
    )
    assets = Path(__file__).resolve().parent
    operation_states = {name: state_of(status) for _, status, _, name in controls[6:]}
    for name in ("restore", release_source, "rollback", "tls"):
        if name + ".json" in errors:
            operation_states[name] = "invalid"
    if "release-failed.json" in errors:
        operation_states[release_source] = "invalid"
    artifact_state = (
        "fail"
        if False in (audit_passed, scan_passed)
        else "pass"
        if audit_passed is True and scan_passed is True
        else "partial"
        if build
        else "missing"
    )
    if any(name.startswith(("build-", "audit-", "scan-")) for name in errors):
        artifact_state = "invalid"
    operation_groups = [
        (
            "Verificação",
            [
                (
                    "overview",
                    "Aplicação e isolamento",
                    attempt.state,
                    shown(verification.get("elapsed_seconds"), " s"),
                ),
                (
                    "result",
                    "Resultado do job",
                    state_of(observed(job, successful_job(job))),
                    "v" + shown(job.get("version")),
                ),
                ("tls", "Conexão TLS", operation_states["tls"], "CA local"),
            ],
        ),
        (
            "Recuperação",
            [
                (
                    "recovery",
                    "Backup e restauração",
                    operation_states["restore"],
                    shown(restore.get("recovery_seconds"), " s"),
                ),
            ],
        ),
        (
            "Release",
            [
                (
                    "operations",
                    "Última troca de imagem",
                    operation_states[release_source],
                    shown(release.get("maintenance_seconds"), " s"),
                ),
                (
                    "rollback",
                    "Retorno após falha",
                    operation_states["rollback"],
                    shown(rollback.get("maintenance_seconds"), " s"),
                ),
            ],
        ),
        (
            "Artefatos",
            [
                (
                    "artifacts",
                    "Imagem, auditoria e scan",
                    artifact_state,
                    "v" + shown(build.get("version")),
                ),
                ("evidence", "Arquivos de evidência", "info", f"{len(records)} JSONs"),
            ],
        ),
    ]
    navigation = "".join(
        f'<div class="operation-group"><h2>{title}</h2>'
        + "".join(operation_link(*item) for item in items)
        + "</div>"
        for title, items in operation_groups
    )
    template = Template((assets / "report.html").read_text(encoding="utf-8"))
    page = template.substitute(
        styles=(assets / "report.css").read_text(encoding="utf-8"),
        scripts=(assets / "report.js").read_text(encoding="utf-8"),
        navigation=navigation,
        created=timestamp({"recorded_at": generated_at.isoformat()}),
        record_count=len(records),
        verification_class=attempt.state,
        verification_title=escape(attempt.title),
        verification_explanation=(
            f"<p>{escape(attempt.explanation)}</p>" if attempt.explanation else ""
        ),
        verification_time=timestamp(verification),
        verification_age=escape(verification_age),
        verification_duration=shown(verification.get("elapsed_seconds"), " s"),
        verification_duration_exact=precise_duration,
        verification_run=shown(verification.get("run_id")),
        verification_project=shown(verification.get("project")),
        verification_started=timestamp({"started_at": verification.get("started_at")}),
        verification_error=shown(verification.get("error_category")),
        verification_proof=proof("verification-run", records),
        artifact_version=shown(build.get("version")),
        artifact_image=shown(build.get("image_id")),
        artifact_proof=proof(build_name, records),
        scan_status=badge(scan_passed),
        snapshot_version=shown(final_health.get("version")),
        snapshot_health=shown(final_health.get("status")),
        snapshot_proof=proof("final-state", records),
        supply_identity=escape(supply_identity),
        job_panel=job_panel(job, proof(job_source, {job_source: job_record} if job_record else {})),
        controls=controls_html,
        restore_steps=restore_steps,
        release_steps=operation_rows[release_source],
        rollback_steps=operation_rows["rollback"],
        tls_steps=operation_rows["tls"],
        artifact_steps="".join(
            control_row(title, status, body, proof(name, records))
            for title, status, body, name in controls[10:]
        ),
        verification_context=record_context(verification, label="Conclusão registrada"),
        job_context=record_context(job_record),
        backup_context=record_context(backup),
        restore_context=record_context(restore),
        release_context=record_context(release),
        rollback_context=record_context(rollback),
        tls_context=record_context(tls),
        artifact_context=record_context(build, label="Build registrado"),
        snapshot_context=record_context(final_state),
        restore_duration=shown(restore.get("recovery_seconds"), " s"),
        restore_status=badge(controls[6][1]),
        backup_duration=shown(backup.get("elapsed_seconds"), " s"),
        backup_checksum=shown(backup.get("sha256")),
        backup_schema=shown(backup.get("schema_version")),
        restored_jobs=shown(restore.get("restored_jobs")),
        restored_job=job_panel(restore.get("new_job") or {}, proof("restore", records)),
        release_status=badge(controls[7][1]),
        release_duration=shown(release.get("maintenance_seconds"), " s"),
        release_version=shown(release.get("requested_version")),
        release_image=shown(release.get("candidate_image")),
        release_previous=shown(release.get("previous_image")),
        release_job=job_panel(release.get("candidate_job") or {}, proof(release_source, records)),
        rollback_status=badge(controls[8][1]),
        rollback_duration=shown(rollback.get("maintenance_seconds"), " s"),
        rollback_image=shown(rollback.get("previous_image")),
        rollback_candidate=shown(rollback.get("candidate_image")),
        rollback_job=job_panel(rollback.get("rollback_job") or {}, proof("rollback", records)),
        release_warning=release_warning,
        resources=resource_table(hardening),
        hardening_proof=proof("hardening", current),
        backup_proof=proof("backup", records),
        restore_proof=proof("restore", records),
        release_proof=proof(release_source, operation_records),
        rollback_proof=proof("rollback", records),
        supply_metrics=supply_metrics,
        scan_findings=scan_findings,
        supply_files=supply_html,
        cache_table=cache_html,
        cache_proof=proof("cache-experiment", records),
        audit_image=shown(audit.get("image_id")),
        audit_config=shown(audit.get("config_digest")),
        evidence_errors=errors_html,
        evidence_rows=evidence_rows,
    )
    page = "\n".join(line.rstrip() for line in page.splitlines()) + "\n"
    destination = root / "docs" / "report.html"
    destination.write_text(page, encoding="utf-8")
    print(f"Relatório: {destination}")
    return destination


if __name__ == "__main__":
    project = Path(__file__).resolve().parents[1]
    generate(project, project / ".runtime")
