from __future__ import annotations

import base64
import hashlib
import math
import posixpath
import re
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from pathlib import Path
from string import Template
from urllib.parse import quote, unquote, urlsplit

from report_evidence import (
    read_records,
    record_time,
    select_artifacts,
    successful_job,
    verification_checks,
    verification_result,
)
from report_recovery import restore_result
from runtime_context import (
    ROOT,
    RuntimeContext,
    active_context,
    ensure_file_path,
    ensure_no_links,
    valid_session_slug,
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
    """Mostra a identificação e a data junto do resultado da operação."""
    return (
        '<dl class="record-context">'
        f"<div><dt>{escape(label)}</dt><dd>{timestamp(record)}</dd></div>"
        f"<div><dt>Projeto</dt><dd><code>{shown(record.get('project', record.get('source_project')))}</code></dd></div>"
        f"<div><dt>Execução</dt><dd><code>{shown(record.get('run_id'))}</code></dd></div>"
        "</dl>"
    )


def operation_link(identifier: str, title: str, state: str, detail: str) -> str:
    labels = {
        "fail": "Falhou",
        "pending": "Revisar",
        "missing": "Ausente",
        "invalid": "Inválido",
        "partial": "Revisar",
        "running": "Em andamento",
        "queued": "Na fila",
        "stale": "Registro antigo",
    }
    attention = (
        f'<span class="operation-meta">{escape(labels[state])}</span>' if state in labels else ""
    )
    return (
        f'<a class="operation-link" href="#{identifier}" data-state="{state}" '
        f'data-operation="{identifier}" '
        f'title="{escape(detail, quote=True)}">'
        f'<span class="operation-title">{escape(title)}</span>{attention}</a>'
    )


def state_of(value: bool | None) -> str:
    return "pass" if value is True else "fail" if value is False else "missing"


def job_state(job: dict) -> str:
    if not job:
        return "missing"
    state = job.get("state")
    if state in ("queued", "running"):
        return state
    if state == "succeeded":
        return "pass" if successful_job(job) else "partial"
    return "fail" if state == "failed" else "invalid"


def metrics(items: list[tuple[str, object, str]]) -> str:
    return (
        '<div class="metrics">'
        + "".join(
            f'<div class="metric"><span>{escape(label)}</span><strong{" class=unavailable" if value is None or value == "" else ""}>{shown(value, suffix)}</strong></div>'
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
            f'<td class="numeric">{shown(round(memory / 1024**2, 1) if isinstance(memory, (int, float)) else None, " MiB")}</td>'
            f'<td class="numeric">{cpu_quota(cpu)}</td>'
            f'<td class="numeric">{shown(values.get("pids_limit"))}</td>'
            f"<td>{filesystem}</td></tr>"
        )
    return (
        '<div class="table-wrap" role="region" aria-label="Limites dos containers, tabela com rolagem horizontal" tabindex="0"><table><caption>Limites por serviço</caption><thead><tr><th scope="col">Serviço</th><th scope="col">UID:GID</th><th scope="col" class="numeric">Memória máxima</th><th scope="col" class="numeric">Limite de CPU (CPUs)</th><th scope="col" class="numeric">PIDs máx.</th><th scope="col">Filesystem</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div>"
    )


def cpu_quota(nano_cpus: object) -> str:
    if nano_cpus is None:
        return "Não informado"
    if type(nano_cpus) is not int or not 0 <= nano_cpus <= 2**63 - 1:
        return "Dado inválido"
    value = Decimal(nano_cpus) / Decimal(1_000_000_000)
    return format(value.normalize(), "f").replace(".", ",")


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
        + '<dl class="job-values">'
        + "".join(
            f"<div><dt>{label}</dt><dd>{shown(value)}</dd></div>"
            for label, value in (
                ("Palavras", result.get("word_count")),
                ("Tentativas", job.get("attempts")),
                ("Versão", job.get("version")),
            )
        )
        + "</dl>"
        + '<details class="checksum"><summary>Detalhes do job</summary>'
        f"<p>Estado na API</p><code>{shown(state)}</code>"
        f"<p>ID do job</p><code>{shown(job.get('id'))}</code>"
        f"<p>SHA-256 do texto UTF-8</p><code>{shown(result.get('checksum'))}</code></details>"
        f'<div class="proof">{evidence}</div></div>'
    )


def command_block(command: str, identifier: str, label: str) -> str:
    return (
        '<div class="code-block local-command"><div class="command-heading">'
        f'<span class="code-language">{escape(label)}</span>'
        f'<button type="button" data-copy-command data-command-target="{identifier}" '
        "hidden>Copiar comando</button></div>"
        f'<pre id="{identifier}" role="region" tabindex="0" aria-label="{escape(label, quote=True)}">'
        f"<code>{escape(command)}</code></pre>"
        '<p class="copy-feedback" role="status" aria-live="polite"></p></div>'
    )


def catalog_namespace(record: dict, context: RuntimeContext) -> tuple[str | None, str]:
    value = record.get("context")
    if value == "default":
        return "default", "padrão"
    if valid_session_slug(value):
        return value, value
    if "context" in record or record:
        return None, "Não registrado ou inválido"
    return context.session or "default", context.session or "padrão"


def catalog_commands(namespace: str, arguments: str, identifier: str, label: str) -> str:
    # Namespace and backup IDs have already passed their strict slug validation.
    powershell = (
        (
            f"$env:CONTAINEROPS_SESSION='{namespace}'\n"
            if namespace != "default"
            else "Remove-Item Env:CONTAINEROPS_SESSION -ErrorAction SilentlyContinue\n"
        )
        + "python scripts/ops.py "
        + arguments
    )
    posix = (
        f"CONTAINEROPS_SESSION='{namespace}' python3 scripts/ops.py {arguments}"
        if namespace != "default"
        else f"(unset CONTAINEROPS_SESSION; python3 scripts/ops.py {arguments})"
    )
    return command_block(powershell, identifier, label + " · PowerShell") + command_block(
        posix, identifier + "-posix", label + " · Shell POSIX"
    )


def backup_catalog_panel(
    record: dict, context: RuntimeContext, *, records: dict | None = None, invalid: bool = False
) -> str:
    """Apresenta integridade, recibo de restore e proteção em campos separados."""
    namespace, namespace_label = catalog_namespace(record, context)
    entries = record.get("entries")
    malformed = invalid or bool(record and not isinstance(entries, list))
    accepted, rejected, identifiers = [], 0, set()
    for entry in entries if isinstance(entries, list) else []:
        identifier = entry.get("id") if isinstance(entry, dict) else None
        if (
            not isinstance(identifier, str)
            or identifier in identifiers
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", identifier)
        ):
            rejected += 1
            continue
        identifiers.add(identifier)
        accepted.append(entry)
    malformed = malformed or rejected > 0
    accepted.sort(
        key=lambda entry: (
            record_time({"created_at": entry.get("created_at")})
            or datetime.min.replace(tzinfo=UTC),
            entry["id"],
        ),
        reverse=True,
    )
    reason_labels = {
        "unknown": "Integridade desconhecida",
        "latest-backup": "Último ponteiro",
        "marked-protected": "Marca de proteção",
        "last-valid": "Última cópia válida",
        "keep-last": "Manter últimas",
        "last-restore-verified": "Último restore verificado",
    }
    rows = []
    for entry in accepted:
        identifier = entry["id"]
        origin = entry.get("source_project")
        source_context = entry.get("source_context")
        origin = (
            origin if isinstance(origin, str) and not any(c in origin for c in "/\\:") else None
        )
        source_context = (
            source_context
            if isinstance(source_context, str) and not any(c in source_context for c in "/\\:")
            else None
        )
        valid = entry.get("validation") == "valid"
        verified = entry.get("restore_verified") is True and valid
        validation = (
            "Íntegro"
            if valid
            else "Desconhecido"
            if entry.get("validation") == "unknown"
            else "Dado inválido"
        )
        restore_label = "Verificado no catálogo" if verified else "Sem recibo no catálogo"
        restore_state = "verified" if verified else "unverified" if valid else "unknown"
        protection = (
            "Protegido"
            if entry.get("protected") is True
            else "Sem marca de proteção"
            if entry.get("protected") is False
            else "Proteção não informada"
        )
        imported = entry.get("imported") is True
        reasons = entry.get("retention_reasons", entry.get("reasons", []))
        reason = (
            "; ".join(
                reason_labels[value]
                for value in reasons
                if isinstance(value, str) and value in reason_labels
            )
            if isinstance(reasons, list)
            else ""
        )
        command = (
            catalog_commands(
                namespace,
                f"restore-test --backup {identifier}",
                f"restore-command-{identifier}",
                "Comando local para este ID",
            )
            + (
                "<p class=notice>Esta cópia foi importada. Confira a origem antes de acrescentar <code>--trusted-source</code>; o hash não autentica o conteúdo.</p>"
                if imported
                else ""
            )
            if valid and namespace
            else '<p class="notice">Confira integridade e contexto com <code>backups list</code> antes de preparar um restore deste ID.</p>'
        )
        search = " ".join(str(v) for v in (identifier, origin or "", source_context or ""))
        rows.append(
            f'<li id="copy-{identifier}" data-backup-row data-backup-search="{escape(search, quote=True)}" '
            f'data-backup-restore="{restore_state}" tabindex="-1"><article aria-labelledby="copy-title-{identifier}">'
            '<div class="backup-row-heading"><div class="backup-identity">'
            f'<h3 id="copy-title-{identifier}"><code>{escape(identifier)}</code></h3>'
            f'<p class="backup-date">{("Criada em " + timestamp({"created_at": entry.get("created_at")})) if record_time({"created_at": entry.get("created_at")}) else "Data de criação não registrada"}</p>'
            f'<p class="backup-origin">Origem: <code>{shown(origin)}</code> · contexto <code>{shown(source_context)}</code></p></div>'
            '<dl class="backup-checks"><div><dt>Integridade</dt>'
            f'<dd><span class="badge {"pass" if valid else "pending" if entry.get("validation") == "unknown" else "fail"}">{validation}</span></dd></div>'
            f'<div><dt>Restore</dt><dd><span class="badge {"pass" if verified else "pending"}">{restore_label}</span></dd></div></dl></div>'
            '<details class="record-details"><summary>Detalhes e comando local</summary>'
            f'<dl class="copy-facts"><div><dt>Schema</dt><dd>{shown(entry.get("schema_version"))}</dd></div>'
            f"<div><dt>Tamanho</dt><dd>{shown(entry.get('size_bytes'), ' bytes')}</dd></div>"
            f"<div><dt>Proteção</dt><dd>{protection}</dd></div><div><dt>Transporte</dt><dd>{'Importado' if imported else 'Não marcado como importado'}</dd></div>"
            f"<div><dt>Retenção</dt><dd>{escape(reason) if reason else 'Consulte a prévia na CLI'}</dd></div></dl>"
            + command
            + "</details></article></li>"
        )
    warning = (
        '<p class="record-warning">Há entradas inválidas ou um catálogo ilegível. Nenhuma delas é tratada como ausência de cópias. Confira o JSON e a CLI.</p>'
        if malformed
        else ""
    )
    missing = (
        '<div class="empty-state"><h3>Catálogo ainda não registrado</h3><p>Gere o catálogo na CLI e atualize este relatório. A ausência do registro não demonstra que não há backups.</p></div>'
        if not record and not invalid
        else '<div class="empty-state"><h3>Nenhuma entrada válida para apresentar</h3><p>Confira as entradas inválidas no arquivo.</p></div>'
        if malformed and not rows
        else '<div class="empty-state"><h3>Nenhuma cópia catalogada</h3><p>Este catálogo foi registrado sem cópias. Gere um backup pelo procedimento dos runbooks.</p></div>'
        if not rows
        else ""
    )
    filters = (
        '<form class="catalog-filters" data-catalog-controls hidden role="search" aria-label="Buscar cópia no catálogo">'
        '<div><label for="catalog-search">Buscar ID ou origem</label><input id="catalog-search" type="search" data-catalog-search maxlength="128" autocomplete="off" placeholder="ID, projeto ou contexto"/></div>'
        '<div><label for="catalog-restore">Recibo de restore</label><select id="catalog-restore" data-catalog-restore>'
        '<option value="all">Todas as cópias</option><option value="verified">Com restore verificado</option><option value="unverified">Íntegras sem recibo</option><option value="unknown">Desconhecida ou inválida</option></select></div>'
        '<button type="button" data-catalog-reset>Limpar filtros</button></form>'
        f'<p class="catalog-count" role="status" aria-live="polite" data-catalog-status>{len(rows)} cópias no registro. Mais recentes primeiro.</p>'
        '<p class="empty-state" data-catalog-no-results hidden>Nenhuma cópia corresponde aos filtros. Limpe a busca ou escolha outro estado.</p>'
        if rows
        else ""
    )
    commands = (
        catalog_commands(
            namespace,
            "backups list",
            "catalog-list-command",
            "Conferir o catálogo atual",
        )
        if namespace
        else '<p class="notice">Contexto não registrado. Confira a sessão no terminal antes de selecionar uma cópia.</p>'
    )
    retention = (
        catalog_commands(
            namespace,
            "backups prune --keep-last 3",
            "catalog-plan-command",
            "Gerar uma prévia sem remoção",
        )
        if namespace
        else ""
    )
    return (
        '<div class="surface-panel backup-catalog">'
        f'<div class="catalog-intro"><p>Contexto do catálogo: <code>{escape(namespace_label)}</code></p>'
        f'<p class="metadata">{("Registrado em " + timestamp(record)) if record_time(record) else "Data do catálogo não registrada"}. A CLI confere o estado atual.</p></div>'
        '<p class="catalog-reading">Integridade confere os bytes. Restore verificado registra snapshot, novo job e limpeza. Sem recibo no catálogo não significa que a cópia nunca foi ensaiada.</p>'
        + warning
        + filters
        + missing
        + ('<ol class="backup-ledger">' + "".join(rows) + "</ol>" if rows else "")
        + '<div class="catalog-source"><h3>Fonte e próximos passos</h3><div class="proof">'
        + proof("backup-catalog", records or ({"backup-catalog": record} if record else {}))
        + "</div>"
        + commands
        + '<details class="record-details"><summary>Prévia de retenção</summary>'
        + retention
        + "<p>A prévia não remove cópias. Aplicar exige <code>--apply --plan-id &lt;id-do-plano&gt;</code> e uma nova conferência sob lock na CLI.</p></details>"
        + '<p class="notice">O HTML permite consultar registros e copiar texto. Execute operações somente no terminal, na sessão indicada.</p></div></div>'
    )


def bundle_guides(sources: list[tuple[str, Path]]) -> list[tuple[str, bytes]]:
    bundled = {name for name, _ in sources}
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
        tracked = set(
            subprocess.check_output(
                ["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", revision], text=True
            ).splitlines()
        )
        dirty = bool(
            subprocess.check_output(
                ["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"], text=True
            ).strip()
        )
    except (OSError, subprocess.CalledProcessError):
        revision, tracked, dirty = None, set(), True
    output = []
    for name, source in sources:
        linked = False

        def destination(value: str, *, image: bool = False) -> str:
            nonlocal linked
            parsed = urlsplit(value.strip("<>"))
            if parsed.scheme or parsed.netloc or not parsed.path:
                return value
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(name), unquote(parsed.path))
            )
            if resolved in bundled:
                return value
            if resolved.startswith("../") or resolved.startswith("/") or not revision:
                raise RuntimeError(
                    f"Referência de guia sem destino público versionado: {name}: {value}"
                )
            directory = any(item.startswith(resolved.rstrip("/") + "/") for item in tracked)
            if resolved not in tracked and not directory:
                raise RuntimeError(
                    f"Referência fora dos arquivos públicos do commit: {name}: {value}"
                )
            linked = True
            path = quote(resolved, safe="/")
            url = (
                f"https://raw.githubusercontent.com/arthurjoanes/containerops/{revision}/{path}"
                if image and not directory
                else f"https://github.com/arthurjoanes/containerops/{'tree' if directory else 'blob'}/{revision}/{path}"
            )
            return (
                url
                + ("?" + parsed.query if parsed.query else "")
                + ("#" + parsed.fragment if parsed.fragment else "")
            )

        document = source.read_text(encoding="utf-8")
        document = re.sub(
            r"(!?\[[^\]\n]*\]\()(<[^>\n]+>|[^\s)]+)",
            lambda match: match[1] + destination(match[2], image=match[1].startswith("!")),
            document,
        )
        document = re.sub(
            r"(?m)^(\s*\[[^\]\n]+\]:\s*)(<[^>\n]+>|\S+)",
            lambda match: match[1] + destination(match[2]),
            document,
        )
        document = re.sub(
            r"((?:href|src)=[\"'])([^\"']+)([\"'])",
            lambda match: (
                match[1] + destination(match[2], image=match[1].startswith("src")) + match[3]
            ),
            document,
        )
        if linked:
            notice = (
                f"> Referências externas: código e imagens usam o commit `{revision}` e exigem rede. "
                "Os guias deste pacote são cópias dos arquivos locais."
            )
            if dirty:
                notice += " Há alterações locais: os links do commit não são permalinks dessas alterações."
            document = notice + "\n\n" + document
        output.append((name, document.encode("utf-8")))
    return output


def guide_urls(root: Path, destination: Path, session_output: bool) -> dict[str, str]:
    if not session_output:
        return {
            "readme_url": "../README.md",
            "runbooks_url": "runbooks.md",
            "verification_url": "verification.md",
        }
    names = (
        "README.md",
        "docs/runbooks.md",
        "docs/verification.md",
        "docs/report-guide.md",
        "docs/architecture.md",
        "docs/security.md",
        "docs/supply-chain.md",
        "docs/operational-recovery.md",
        "docs/demo.md",
        "docs/frontend-quality.md",
        "docs/screenshots.md",
        "docs/data-contract.md",
        "docs/brand.md",
    )
    sources = [(name, ROOT / name) for name in names if (ROOT / name).is_file()]
    contents = bundle_guides(sources)
    signature = hashlib.sha256(
        b"".join(name.encode() + content for name, content in contents)
    ).hexdigest()[:16]
    directory = ensure_no_links(destination.parent / "documentation" / signature)
    for name, content in contents:
        target = ensure_file_path(directory / name)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.exists():
            if target.read_bytes() != content:
                raise RuntimeError(
                    "Documentação da sessão diverge da fonte; nenhum arquivo foi sobrescrito"
                )
        else:
            with target.open("xb") as stream:
                stream.write(content)
    return {
        "readme_url": f"documentation/{signature}/README.md",
        "runbooks_url": f"documentation/{signature}/docs/runbooks.md",
        "verification_url": f"documentation/{signature}/docs/verification.md",
    }


def generate(root: Path, runtime: Path, *, now: datetime | None = None) -> Path:
    root, runtime = Path(root), Path(runtime)
    context = active_context(root)
    session_output = bool(context.session and runtime.resolve() == context.runtime)
    if context.session and root.resolve() == ROOT and not session_output:
        raise RuntimeError("Relatório deve usar o runtime do contexto ativo")
    directory = context.evidence if session_output else root / "docs" / "evidence"
    destination = context.report if session_output else root / "docs" / "report.html"
    ensure_file_path(destination)
    if session_output:
        ensure_no_links(directory)
        ensure_no_links(destination.parent)
    generated_at = now or datetime.now(UTC)
    records, errors = read_records(directory)
    for name, record in list(records.items()):
        stamped = record_time(record)
        if stamped is not None and stamped > generated_at:
            errors[name + ".json"] = "data posterior à geração; confira o relógio"
            del records[name]
    verification = records.get("verification-run", {})
    attempt = verification_result(directory, verification, errors, generated_at)
    current = attempt.records
    journey = current.get("journey", {})
    hardening = current.get("hardening", {})
    recovery = current.get("recovery", {})
    backup = records.get("backup", {})
    restore = records.get("restore", {})
    restore_outcome = restore_result({"status": "invalid"} if "restore.json" in errors else restore)
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
            (
                f"{shown(journey.get('concurrent_requests'))} requisições simultâneas; {shown(journey.get('unique_jobs'))} job salvo. Testa autorização e limites HTTP."
                if journey.get("concurrent_requests") is not None
                and journey.get("unique_jobs") is not None
                else "Testa chamadas simultâneas, autorização, idempotência e limites HTTP."
            ),
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
            (
                f"Testa conclusão após SIGKILL ({shown(recovery_kill.get('attempts'))} {'tentativa' if recovery_kill.get('attempts') == 1 else 'tentativas'}) e após SIGTERM."
                if recovery_kill.get("attempts") is not None
                else "Testa conclusão após SIGKILL e após SIGTERM."
            ),
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
            (
                f"Compara snapshots antes e depois de recriar os containers: {shown(recreation.get('preserved_jobs'))} jobs mantidos."
                if recreation.get("preserved_jobs") is not None
                else "Compara os jobs antes e depois de recriar os containers."
            ),
            "recovery",
        ),
        (
            "Backup restaurado",
            True
            if restore_outcome.state == "pass"
            else False
            if restore_outcome.state in ("fail", "invalid")
            else None,
            "Restaura em volume novo e confere checksum, snapshot, novo job e limpeza dos recursos de teste.",
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
            (
                f"Confere a imagem candidata, aplica a migração e verifica {shown(release.get('preserved_jobs'))} jobs."
                if release.get("preserved_jobs") is not None
                else "Confere a imagem candidata, aplica a migração e verifica os jobs."
            ),
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
            (
                "Limpeza dos recursos de teste",
                restore.get("cleanup_succeeded"),
                "Remove os recursos da tentativa e confere sua ausência."
                if "cleanup_succeeded" in restore
                else "Limpeza não registrada neste recibo. Confira o JSON antes de concluir que a recuperação terminou.",
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
        f'<div class="scan-findings {"clear" if scan_passed is True else "attention"}"><h3>Vulnerabilidades</h3>'
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
        scan_findings = (
            '<div class="empty-state"><p>Gerar scan pelo terminal:</p>'
            '<div class="code-block"><span class="code-language">Shell</span>'
            '<pre tabindex="0" aria-label="Comando para gerar o scan">'
            '<code class="language-sh"><span class="syntax-command">python</span> '
            '<span class="syntax-path">scripts/ops.py</span> scan '
            '<span class="syntax-option">--version</span> '
            '<span class="syntax-placeholder">&lt;versão&gt;</span></code></pre></div></div>'
        )
    cache = records.get("cache-experiment", {})
    cache_rows = "".join(
        f'<tr><th scope="row">{shown(item.get("case"))}</th><td class="numeric">{shown(item.get("duration_seconds"), " s")}</td>'
        f'<td class="numeric">{shown(item.get("cached_steps"))}</td></tr>'
        for item in cache.get("runs", [])
        if isinstance(item, dict)
    )
    cache_html = (
        '<div class="table-wrap" role="region" aria-label="Experimentos de cache, tabela com rolagem horizontal" tabindex="0"><table><caption>Cache do build</caption><thead><tr><th scope="col">Teste</th><th scope="col" class="numeric">Duração</th><th scope="col" class="numeric">Etapas reutilizadas</th></tr></thead><tbody>'
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
    if artifact_state == "invalid":
        artifact_diagnosis = "Registros de artefatos inválidos"
        artifact_reason = "Confira os erros de JSON. A integridade dos registros precisa ser resolvida antes de aprovar a imagem."
    elif not build:
        artifact_diagnosis = "Nenhum build selecionado"
        artifact_reason = "Não há imagem identificada para vincular auditoria e scan. Gere o build pelo terminal e atualize este relatório."
    elif artifact_state == "fail":
        artifact_diagnosis = "A imagem não passou por todas as verificações"
        artifact_reason = "Confira os achados e os critérios abaixo; uma falha registrada não é substituída por outra execução."
    elif not audit and not scan:
        artifact_diagnosis = "Faltam auditoria e scan desta imagem"
        artifact_reason = "O build está identificado. Sem scan compatível nem auditoria vinculada, os outros arquivos não aprovam esta imagem."
    elif not audit or not scan:
        artifact_diagnosis = (
            "Falta auditoria desta imagem" if not audit else "Falta scan desta imagem"
        )
        artifact_reason = "A verificação disponível cobre apenas uma parte da imagem selecionada. Confira o vínculo por identidade e tempo."
    else:
        artifact_diagnosis = "Auditoria e scan vinculados à imagem"
        artifact_reason = "Os registros selecionados passaram pelos critérios de identidade, integridade e política do scanner."
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
                    job_state(job),
                    "v" + shown(job["version"]) if job.get("version") else "Versão não informada",
                ),
                ("tls", "Conexão TLS", operation_states["tls"], "CA local"),
            ],
        ),
        (
            "Recuperação",
            [
                (
                    "backups",
                    "Cópias de backup",
                    "invalid"
                    if "backup-catalog.json" in errors
                    else "pass"
                    if records.get("backup-catalog")
                    else "missing",
                    "Localizar ID, integridade e recibo de restore",
                ),
                (
                    "recovery",
                    "Último ensaio de restore",
                    "invalid" if "restore.json" in errors else restore_outcome.state,
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
                    "v" + shown(build["version"])
                    if build.get("version")
                    else "Versão não informada",
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
        styles=(
            "/* "
            + (assets / "assets/source-sans-LICENSE.md").read_text(encoding="utf-8")
            + " */\n@font-face { font-family:'Source Sans 3'; font-weight:200 900; "
            + "font-display:swap; src:url(data:font/woff2;base64,"
            + base64.b64encode((assets / "assets/source-sans-3.woff2").read_bytes()).decode()
            + ") format('woff2'); }\n"
            + (assets / "report.css").read_text(encoding="utf-8")
        ),
        brand_mark=(assets / "assets/mark-light.svg").read_text(encoding="utf-8"),
        favicon=base64.b64encode((assets / "assets/favicon.svg").read_bytes()).decode(),
        artifact_diagnosis=escape(artifact_diagnosis),
        artifact_reason=escape(artifact_reason),
        artifact_state=artifact_state,
        artifact_state_label={
            "pass": "Verificado",
            "fail": "Falha na verificação",
            "partial": "Verificação parcial",
            "missing": "Sem evidência",
            "invalid": "Registro inválido",
        }[artifact_state],
        scripts=(assets / "report.js").read_text(encoding="utf-8"),
        navigation=navigation,
        created=timestamp({"recorded_at": generated_at.isoformat()}),
        **guide_urls(root, destination, session_output),
        record_count=len(records),
        verification_class=attempt.state,
        verification_title=escape(attempt.title),
        verification_explanation=(
            f"<p>{escape(attempt.explanation)}</p>" if attempt.explanation else ""
        ),
        verification_age=escape(verification_age),
        verification_duration=shown(verification.get("elapsed_seconds"), " s"),
        verification_duration_exact=precise_duration,
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
        backup_catalog=backup_catalog_panel(
            records.get("backup-catalog", {}),
            context,
            records=records,
            invalid="backup-catalog.json" in errors,
        ),
        restore_context=record_context(restore),
        release_context=record_context(release),
        rollback_context=record_context(rollback),
        tls_context=record_context(tls),
        artifact_context=record_context(build, label="Build registrado"),
        snapshot_context=record_context(final_state),
        restore_duration=shown(restore.get("recovery_seconds"), " s"),
        restore_status=f'<span class="badge {"pass" if restore_outcome.state == "pass" else "fail" if restore_outcome.state in ("fail", "invalid") else "pending"}">{escape(restore_outcome.label)}</span>',
        restore_explanation=escape(restore_outcome.explanation),
        restore_backup_id=shown(restore.get("backup_id")),
        restore_backup_checksum=shown(restore.get("backup_sha256")),
        restore_metadata_checksum=shown(restore.get("backup_metadata_sha256")),
        backup_duration=shown(backup.get("elapsed_seconds"), " s"),
        backup_checksum=shown(backup.get("sha256")),
        backup_schema=shown(backup.get("schema_version")),
        restored_jobs=shown(restore.get("restored_jobs")),
        restored_jobs_style="unavailable" if restore.get("restored_jobs") is None else "",
        restore_duration_style="unavailable" if restore.get("recovery_seconds") is None else "",
        release_version_style="unavailable" if release.get("requested_version") is None else "",
        release_duration_style="unavailable" if release.get("maintenance_seconds") is None else "",
        rollback_duration_style="unavailable"
        if rollback.get("maintenance_seconds") is None
        else "",
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
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8", newline="\n")
    print(f"Relatório: {destination}")
    return destination


if __name__ == "__main__":
    project = Path(__file__).resolve().parents[1]
    generate(project, active_context(project).runtime)
