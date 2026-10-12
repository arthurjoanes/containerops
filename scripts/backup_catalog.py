"""Catálogo de backups e planos de retenção identificados pelo conteúdo.

Consultas não gravam arquivos. A CLI mantém ops.operation_lock durante alterações,
inclusive aplicação e retomada de planos, para evitar disputa com um backup
no mesmo runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path

FORMAT_VERSION = 1
MAX_METADATA_BYTES = 16 * 1024**2
MAX_DUMP_BYTES = 8 * 1024**3
MAX_IMAGE_BYTES = 8 * 1024**3
FILES = {"containerops.dump", "metadata.json", "manifest.json", "application-image.tar"}
CATALOG_FILE = "backup-catalog.json"
JOURNAL_FILE = "backup-prune-journal.json"
_ID = re.compile(r"(?:[a-z0-9][a-z0-9_-]{0,127}|\d{8}T\d{6}-[a-f0-9]{8})\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_IMAGE = re.compile(r"sha256:[a-f0-9]{64}\Z")
_METADATA_KEYS = {
    "created_at",
    "cutoff_interval",
    "sha256",
    "server",
    "schema_version",
    "snapshot",
    "source_project",
    "image",
    "elapsed_seconds",
    "source_context",
    "source_ref",
    "image_release",
    "config_digest",
    "oci_manifest_digest",
    "tool",
}


class CatalogError(RuntimeError):
    pass


class UnsafePath(CatalogError):
    pass


def _require(condition, message):
    if not condition:
        raise CatalogError(message)


def _id(value):
    _require(isinstance(value, str) and bool(_ID.fullmatch(value)), "ID de backup inválido")
    return value


def _stamp(value):
    _require(isinstance(value, str), "Data de backup inválida")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CatalogError("Data de backup inválida") from error
    _require(parsed.tzinfo is not None, "Data de backup precisa de timezone")
    return parsed


def _signature(item):
    return {
        "device": item.st_dev,
        "inode": item.st_ino,
        "size": item.st_size,
        "mtime_ns": item.st_mtime_ns,
        "ctime_ns": item.st_ctime_ns,
        "mode": item.st_mode,
        "links": item.st_nlink,
    }


def _check_node(path, *, directory=False):
    item = path.lstat()
    if (
        stat.S_ISLNK(item.st_mode)
        or getattr(item, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(item, "st_reparse_tag", 0)
    ):
        raise UnsafePath("Links/junções/reparse points não são aceitos no catálogo")
    _require(
        stat.S_ISDIR(item.st_mode) if directory else stat.S_ISREG(item.st_mode),
        "Catálogo exige diretórios e arquivos regulares",
    )
    if not directory:
        if item.st_nlink != 1:
            raise UnsafePath("Hard links não são aceitos no catálogo")
    return item


def _directory(path, *, allow_missing=False):
    path = Path(path).absolute()
    for parent in reversed((path, *path.parents)):
        if parent.exists() or parent.is_symlink():
            _check_node(parent, directory=True)
    if not allow_missing:
        _check_node(path, directory=True)
    return path


def _file(path, limit):
    before = _check_node(path)
    _require(before.st_size <= limit, "Arquivo de backup excede o limite documentado")
    digest = hashlib.sha256()
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    with os.fdopen(descriptor, "rb") as stream:
        _require(
            _signature(os.fstat(stream.fileno())) == _signature(before),
            "Arquivo mudou antes da leitura",
        )
        while block := stream.read(1024 * 1024):
            digest.update(block)
        _require(
            _signature(os.fstat(stream.fileno())) == _signature(before),
            "Arquivo mudou durante leitura",
        )
    _require(_signature(_check_node(path)) == _signature(before), "Arquivo mudou durante leitura")
    return {"sha256": digest.hexdigest(), **_signature(before)}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "JSON com chave duplicada")
        result[key] = value
    return result


def _json(path):
    before = _file(path, MAX_METADATA_BYTES)
    try:
        value = json.loads(
            path.read_text(encoding="utf-8-sig"),
            object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(CatalogError("JSON não finito")),
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise CatalogError("JSON inválido no catálogo") from error
    _require(_file(path, MAX_METADATA_BYTES) == before, "JSON mudou durante leitura")
    _require(isinstance(value, dict), "JSON do catálogo precisa ser objeto")
    return value, before


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write(path, value):
    _directory(path.parent)
    if path.exists() or path.is_symlink():
        _check_node(path)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            if os.name != "nt":
                temporary.chmod(0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _state(runtime):
    path = runtime / CATALOG_FILE
    if not path.exists() and not path.is_symlink():
        return {"format_version": FORMAT_VERSION, "entries": {}}, None
    data, signature = _json(path)
    _require(set(data) == {"format_version", "entries"}, "Campos inválidos no catálogo")
    _require(
        data["format_version"] == FORMAT_VERSION and isinstance(data["entries"], dict),
        "Versão de catálogo inválida",
    )
    for identifier, record in data["entries"].items():
        _id(identifier)
        _require(
            isinstance(record, dict) and record.get("id") == identifier,
            "Registro de catálogo inválido",
        )
        _require(record.get("internal_path") == identifier, "Caminho interno do catálogo inválido")
        _require(
            type(record.get("protected")) is bool and type(record.get("imported")) is bool,
            "Flags do catálogo inválidas",
        )
    return data, signature


def _metadata(data):
    from backup_package import validate_metadata

    _require(set(data) <= _METADATA_KEYS, "Metadados desconhecidos; backup fora da remoção")
    created = _stamp(data.get("created_at"))
    _require(created <= datetime.now(UTC), "Data de backup está no futuro")
    _require(bool(_HASH.fullmatch(str(data.get("sha256", "")))), "Hash do dump inválido")
    _require(
        type(data.get("schema_version")) is int and data["schema_version"] in (1, 2),
        "Schema de backup desconhecido",
    )
    _id(data.get("source_project"))
    _id(data.get("source_context", "legacy"))
    _require(bool(_IMAGE.fullmatch(str(data.get("image", "")))), "Identidade da imagem inválida")
    _require(
        isinstance(data.get("server"), str)
        and bool(re.search(r"(?:PostgreSQL|postgres).*\b17(?:\.|\b)", data["server"], re.I)),
        "Ferramenta PostgreSQL desconhecida",
    )
    snapshot = data.get("snapshot")
    _require(
        isinstance(snapshot, dict)
        and snapshot.get("schema_version") == data["schema_version"]
        and isinstance(snapshot.get("jobs"), list),
        "Snapshot de backup incompleto",
    )
    interval = data.get("cutoff_interval")
    if interval is not None:
        _require(isinstance(interval, dict), "Janela de corte inválida")
        _require(
            set(interval) == {"started_at", "completed_at", "method"},
            "Campos da janela de corte inválidos",
        )
        _require(
            _stamp(interval.get("started_at")) <= _stamp(interval.get("completed_at")) <= created,
            "Janela de corte inválida",
        )
        _require(
            isinstance(interval.get("method"), str) and bool(interval["method"]),
            "Método de corte inválido",
        )
    try:
        return validate_metadata(data)
    except RuntimeError as error:
        raise CatalogError(str(error)) from error


def _latest(runtime, root):
    pointers = [runtime / "latest-backup.json"]
    environments = runtime / "environments"
    if environments.exists():
        _directory(environments)
        for child in sorted(environments.iterdir()):
            _check_node(child, directory=True)
            pointers.append(child / "latest-backup.json")
    result, files = set(), {}
    for path in pointers:
        if not path.exists() and not path.is_symlink():
            continue
        value, signature = _json(path)
        _require(set(value) <= {"directory", "backup_id", "id"}, "Ponteiro latest-backup inválido")
        identifier = value.get("backup_id", value.get("id"))
        if "directory" in value:
            candidate = Path(value["directory"])
            _require(
                candidate.is_absolute() and candidate.parent == root,
                "Ponteiro latest-backup fora dos IDs internos",
            )
            _require(
                identifier is None or identifier == candidate.name,
                "Ponteiro latest-backup inconsistente",
            )
            identifier = candidate.name
        result.add(_id(identifier))
        files[path.relative_to(runtime).as_posix()] = signature
    return result, files


def _receipt(runtime, entry, saved):
    link = saved.get("restore_verified")
    if link is None:
        return None
    _require(
        isinstance(link, dict) and set(link) == {"receipt", "sha256", "verified_at"},
        "Recibo do catálogo inválido",
    )
    name = link["receipt"]
    _require(
        isinstance(name, str)
        and bool(re.fullmatch(re.escape(entry["id"]) + r"-[a-f0-9]{32}\.json", name)),
        "ID de recibo inválido",
    )
    directory = _directory(runtime / "backup-receipts")
    receipt, signature = _json(directory / name)
    _require(signature["sha256"] == link["sha256"], "Hash do recibo de restore divergente")
    _require(
        receipt.get("backup_id") == entry["id"]
        and receipt.get("dump_sha256") == entry["dump_sha256"]
        and receipt.get("metadata_sha256") == entry["metadata_sha256"],
        "Recibo de restore não corresponde aos artefatos atuais",
    )
    _require(
        receipt.get("source_project") == entry["source_project"]
        and receipt.get("source_context") == entry["source_context"],
        "Origem do recibo divergente",
    )
    _require(
        receipt.get("status") == "passed"
        and all(
            receipt.get(key) is True
            for key in (
                "checksum_verified",
                "snapshot_equal",
                "cleanup_succeeded",
                "new_job_verified",
            )
        ),
        "Restore não foi verificado com limpeza",
    )
    _stamp(link["verified_at"])
    _require(receipt.get("verified_at") == link["verified_at"], "Data do recibo divergente")
    return link, signature


def discover(runtime, *, context="default"):
    """Inclui backups legados sem regravá-los; cópias incompletas ficam desconhecidas."""
    runtime = _directory(runtime, allow_missing=True)
    root = _directory(runtime / "backups", allow_missing=True)
    state, state_signature = _state(runtime)
    latest, pointers = _latest(runtime, root)
    journal = None
    if (runtime / JOURNAL_FILE).exists():
        journal, _ = _json(runtime / JOURNAL_FILE)
    entries, unexpected = [], []
    names = []
    if root.exists():
        names = sorted(path.name for path in root.iterdir())
        for name in names:
            path = root / name
            if name.startswith(".prune-") and journal and name == journal.get("quarantine"):
                _directory(path)
                continue
            _check_node(path, directory=path.is_dir())
            entry = {
                "id": name,
                "internal_path": name,
                "validation": "unknown",
                "protected": False,
                "imported": False,
                "restore_verified": None,
                "receipt_signature": None,
                "latest": name in latest,
            }
            try:
                _id(name)
                _directory(path)
                files = {}
                for child in sorted(path.iterdir()):
                    _check_node(child)
                    limit = (
                        MAX_DUMP_BYTES
                        if child.name == "containerops.dump"
                        else MAX_IMAGE_BYTES
                        if child.name == "application-image.tar"
                        else MAX_METADATA_BYTES
                    )
                    files[child.name] = _file(child, limit)
                entry["files"] = files
                _require(
                    {"metadata.json", "containerops.dump"} <= set(files) <= FILES,
                    "Backup incompleto ou com arquivo desconhecido",
                )
                _require(
                    "application-image.tar" not in files or "manifest.json" in files,
                    "Imagem de backup sem manifesto",
                )
                metadata, _ = _json(path / "metadata.json")
                _metadata(metadata)
                _require(files["containerops.dump"]["size"] > 0, "Dump vazio")
                _require(
                    files["containerops.dump"]["sha256"] == metadata["sha256"],
                    "Checksum de backup inválido",
                )
                source_context = metadata.get("source_context", "legacy")
                if "manifest.json" in files:
                    from backup_package import validate_manifest, validate_metadata

                    manifest, _ = _json(path / "manifest.json")
                    try:
                        validate_metadata(metadata, portable=True)
                        validate_manifest(
                            manifest,
                            metadata,
                            {
                                name: {"size": record["size"], "sha256": record["sha256"]}
                                for name, record in files.items()
                            },
                        )
                    except RuntimeError as error:
                        raise CatalogError(str(error)) from error
                    source_context = manifest["backup"]["origin"]["context"]
                entry.update(
                    created_at=metadata["created_at"],
                    source_project=metadata["source_project"],
                    source_context=source_context,
                    schema_version=metadata["schema_version"],
                    size_bytes=sum(item["size"] for item in files.values()),
                    dump_sha256=metadata["sha256"],
                    metadata_sha256=files["metadata.json"]["sha256"],
                    validation="valid",
                )
                saved = state["entries"].get(name, {})
                if saved:
                    _require(
                        saved.get("dump_sha256") == entry["dump_sha256"]
                        and saved.get("metadata_sha256") == entry["metadata_sha256"],
                        "Artefatos mudaram desde o registro do catálogo",
                    )
                    entry.update(protected=saved["protected"], imported=saved["imported"])
                    if "files" in saved:
                        _require(
                            saved["files"]
                            == {
                                name: {"size": record["size"], "sha256": record["sha256"]}
                                for name, record in files.items()
                            },
                            "Conjunto/hashes de arquivos mudaram desde o catálogo",
                        )
                    receipt = _receipt(runtime, entry, saved)
                    if receipt:
                        entry["restore_verified"], entry["receipt_signature"] = receipt
            except (CatalogError, OSError, ValueError) as error:
                if isinstance(error, UnsafePath):
                    raise
                entry.update(validation="unknown", reason=str(error))
                if not path.is_dir():
                    unexpected.append(name)
            entries.append(entry)
    snapshot = {
        "format_version": FORMAT_VERSION,
        "context": context,
        "entries": entries,
        "latest_ids": sorted(latest),
        "unexpected": unexpected,
        "pending_prune": journal if journal and journal.get("status") != "completed" else None,
        "controls": {"catalog": state_signature, "pointers": pointers},
    }
    snapshot["fingerprint"] = _digest(
        {**snapshot, "root_names": names, "state": state_signature, "pointers": pointers}
    )
    return snapshot


def select_backup(runtime, identifier, *, context="default"):
    identifier = _id(identifier)
    entry = next(
        (
            item
            for item in discover(runtime, context=context)["entries"]
            if item["id"] == identifier
        ),
        None,
    )
    _require(entry is not None, "ID não catalogado neste runtime")
    if entry.get("reason") == "Checksum de backup inválido":
        raise CatalogError("Checksum de backup inválido")
    _require(
        entry["validation"] == "valid",
        "Backup desconhecido/inválido: " + entry.get("reason", "integridade não conferida"),
    )
    return _directory(Path(runtime) / "backups" / identifier)


def _entry(runtime, identifier, context):
    select_backup(runtime, identifier, context=context)
    return next(
        item for item in discover(runtime, context=context)["entries"] if item["id"] == identifier
    )


def register_backup(runtime, identifier, *, imported=False, protected=False, context="default"):
    entry = _entry(runtime, identifier, context)
    runtime = _directory(runtime)
    state, _ = _state(runtime)
    previous = state["entries"].get(identifier, {})
    record = {
        key: entry[key]
        for key in (
            "id",
            "internal_path",
            "source_project",
            "source_context",
            "created_at",
            "schema_version",
            "dump_sha256",
            "metadata_sha256",
            "size_bytes",
        )
    }
    record.update(
        imported=bool(imported or previous.get("imported")),
        protected=bool(protected or previous.get("protected")),
        files={
            name: {"size": value["size"], "sha256": value["sha256"]}
            for name, value in entry["files"].items()
        },
    )
    if previous.get("restore_verified"):
        record["restore_verified"] = previous["restore_verified"]
    state["entries"][identifier] = record
    _write(runtime / CATALOG_FILE, state)
    return record


def mark_protected(runtime, identifier, protected=True, *, context="default"):
    _require(type(protected) is bool, "Proteção precisa de flag booleana")
    register_backup(runtime, identifier, context=context)
    state, _ = _state(Path(runtime))
    state["entries"][identifier]["protected"] = protected
    _write(Path(runtime) / CATALOG_FILE, state)
    return state["entries"][identifier]


def record_restore_verified(runtime, identifier, result, *, context="default"):
    """O recibo exige que o chamador confira o novo job e a ausência dos recursos."""
    _require(
        result.get("status") == "passed"
        and all(
            result.get(key) is True
            for key in ("checksum_verified", "snapshot_equal", "cleanup_succeeded")
        ),
        "Restore exige snapshot/job/limpeza aprovados",
    )
    phases = {item.get("name"): item.get("status") for item in result.get("phases", [])}
    _require(
        phases.get("cleanup") == phases.get("cleanup-check") == "passed",
        "Limpeza e ausência de recursos precisam ser conferidas",
    )
    job = result.get("new_job", {})
    _require(
        job.get("state") == "succeeded"
        and job.get("result")
        == {
            "word_count": 4,
            "checksum": hashlib.sha256(b"Backup restaurado com sucesso").hexdigest(),
        },
        "Novo job pós-restore não foi aprovado",
    )
    entry = _entry(runtime, identifier, context)
    _require(
        result.get("backup_sha256") == entry["dump_sha256"], "Restore verificou outro hash de dump"
    )
    if "backup_metadata_sha256" in result:
        _require(
            result["backup_metadata_sha256"] == entry["metadata_sha256"],
            "Metadados mudaram desde o restore",
        )
    verified_at = result.get("completed_at")
    _stamp(verified_at)
    receipt = {
        "format_version": FORMAT_VERSION,
        "backup_id": identifier,
        "source_project": entry["source_project"],
        "source_context": entry["source_context"],
        "dump_sha256": entry["dump_sha256"],
        "metadata_sha256": entry["metadata_sha256"],
        "verified_at": verified_at,
        "status": "passed",
        "checksum_verified": True,
        "snapshot_equal": True,
        "new_job_verified": True,
        "cleanup_succeeded": True,
        "new_job": {"id": job.get("id"), "state": job["state"], "result": job["result"]},
    }
    directory = _directory(Path(runtime) / "backup-receipts", allow_missing=True)
    directory.mkdir(mode=0o700, exist_ok=True)
    name = identifier + "-" + uuid.uuid4().hex + ".json"
    _write(directory / name, receipt)
    link = {
        "receipt": name,
        "sha256": _file(directory / name, MAX_METADATA_BYTES)["sha256"],
        "verified_at": verified_at,
    }
    try:
        register_backup(runtime, identifier, context=context)
        state, _ = _state(Path(runtime))
        state["entries"][identifier]["restore_verified"] = link
        _write(Path(runtime) / CATALOG_FILE, state)
    except BaseException:
        (directory / name).unlink(missing_ok=True)
        raise
    return link


def plan_retention(runtime, keep_last, *, context="default"):
    _require(
        type(keep_last) is int and keep_last >= 0, "keep-last precisa ser inteiro não negativo"
    )
    catalog = discover(runtime, context=context)
    _require(
        catalog["pending_prune"] is None,
        "Retenção interrompida: recupere o plano do journal antes de continuar",
    )
    reasons = {item["id"]: [] for item in catalog["entries"]}
    groups = {}
    for item in catalog["entries"]:
        if item["validation"] != "valid":
            reasons[item["id"]].append("unknown")
            continue
        groups.setdefault((item["source_project"], item["source_context"]), []).append(item)
        if item["latest"]:
            reasons[item["id"]].append("latest-backup")
        if item["protected"]:
            reasons[item["id"]].append("marked-protected")
    for entries in groups.values():
        ordered = sorted(
            entries, key=lambda item: (_stamp(item["created_at"]), item["id"]), reverse=True
        )
        reasons[ordered[0]["id"]].append("last-valid")
        for item in ordered[:keep_last]:
            reasons[item["id"]].append("keep-last")
        verified = [item for item in ordered if item["restore_verified"]]
        if verified:
            latest = max(
                verified,
                key=lambda item: (_stamp(item["restore_verified"]["verified_at"]), item["id"]),
            )
            reasons[latest["id"]].append("last-restore-verified")
    candidates = [item["id"] for item in catalog["entries"] if not reasons[item["id"]]]
    plan = {
        "format_version": FORMAT_VERSION,
        "context": context,
        "keep_last": keep_last,
        "catalog_fingerprint": catalog["fingerprint"],
        "candidates": candidates,
        "candidate_files": {
            item["id"]: item["files"] for item in catalog["entries"] if item["id"] in candidates
        },
        "control_files": catalog["controls"],
        "retained_entries": [item for item in catalog["entries"] if item["id"] not in candidates],
        "retained": [
            {"id": identifier, "reasons": value} for identifier, value in reasons.items() if value
        ],
    }
    plan["plan_id"] = _digest(plan)
    return plan


def _guard_retention(runtime, journal):
    approved = journal["plan"]
    current = discover(runtime, context=approved["context"])
    retained = [item for item in current["entries"] if item["id"] not in journal["candidates"]]
    _require(
        retained == approved["retained_entries"],
        "Catálogo mudou durante a retenção; journal preservado",
    )
    if set(journal["deleted"]) != set(journal["candidates"]):
        _require(
            current["controls"] == approved["control_files"],
            "Proteção/ponteiros mudaram durante a retenção; journal preservado",
        )


def _finish_retention(runtime, journal):
    """Retoma as remoções do plano aprovado; conteúdo inesperado interrompe a retomada."""
    _guard_retention(runtime, journal)
    root = _directory(runtime / "backups")
    quarantine = _directory(root / journal["quarantine"], allow_missing=True)
    if quarantine.exists():
        _require(
            {child.name for child in quarantine.iterdir()} <= set(journal["candidates"]),
            "Arquivo desconhecido no journal de retenção",
        )
    for identifier in journal["candidates"]:
        original = root / _id(identifier)
        target = quarantine / identifier
        _require(
            not original.exists() and not original.is_symlink(),
            "ID concorrente reapareceu durante retenção",
        )
        if not target.exists():
            _require(
                identifier in journal["deleted"] or journal.get("deleting_id") == identifier,
                "Diretório do journal desapareceu",
            )
            if identifier not in journal["deleted"]:
                journal["deleted"].append(identifier)
                _write(runtime / JOURNAL_FILE, journal)
            continue
        _directory(target)
        expected = journal["files"][identifier]
        actual = {child.name for child in target.iterdir()}
        _require(actual <= set(expected), "Arquivo desconhecido durante retenção")
        removed = journal["removed_files"].get(identifier, [])
        removing = journal.get("removing_file")
        allowed_missing = set(removed)
        if removing and removing["id"] == identifier:
            allowed_missing.add(removing["name"])
        _require(
            set(expected) - actual <= allowed_missing, "Arquivo do journal desapareceu sem registro"
        )
        # Confere todos os arquivos antes da remoção, para não apagar parte
        # de uma pasta cujos bytes mudaram após o plano.
        for name in sorted(actual):
            _require(
                _file(
                    target / name,
                    MAX_IMAGE_BYTES
                    if name in ("containerops.dump", "application-image.tar")
                    else MAX_METADATA_BYTES,
                )
                == expected[name],
                "Artefato mudou após o plano de retenção",
            )
        for name in sorted(actual):
            _require(
                _file(
                    target / name,
                    MAX_IMAGE_BYTES
                    if name in ("containerops.dump", "application-image.tar")
                    else MAX_METADATA_BYTES,
                )
                == expected[name],
                "Artefato mudou imediatamente antes da remoção",
            )
            journal.update(deleting_id=identifier, removing_file={"id": identifier, "name": name})
            _write(runtime / JOURNAL_FILE, journal)
            (target / name).unlink()
            journal["removed_files"].setdefault(identifier, []).append(name)
            journal["removing_file"] = None
            _write(runtime / JOURNAL_FILE, journal)
        target.rmdir()
        journal["deleted"].append(identifier)
        _write(runtime / JOURNAL_FILE, journal)
    if quarantine.exists():
        quarantine.rmdir()
    state, _ = _state(runtime)
    for identifier in journal["candidates"]:
        state["entries"].pop(identifier, None)
    if (runtime / CATALOG_FILE).exists():
        _write(runtime / CATALOG_FILE, state)
    journal["status"] = "completed"
    _write(runtime / JOURNAL_FILE, journal)
    return {"plan_id": journal["plan_id"], "deleted": journal["deleted"], "status": "completed"}


def apply_retention(runtime, keep_last, plan_id, *, context="default"):
    """Exige operation_lock do chamador; refaz os hashes para recusar prévia desatualizada."""
    _require(
        isinstance(plan_id, str) and bool(_HASH.fullmatch(plan_id)),
        "Aplicação exige plan-id SHA-256 explícito",
    )
    runtime = _directory(runtime)
    plan = plan_retention(runtime, keep_last, context=context)
    _require(plan["plan_id"] == plan_id, "Plano de retenção obsoleto; gere uma nova prévia")
    catalog = discover(runtime, context=context)
    _require(
        catalog["fingerprint"] == plan["catalog_fingerprint"],
        "Catálogo mudou durante recomputação do plano",
    )
    _require(not catalog["unexpected"], "Arquivo desconhecido na raiz de backups")
    if not plan["candidates"]:
        return {"plan_id": plan_id, "deleted": [], "status": "completed"}
    quarantine = ".prune-" + plan_id[:24]
    root = _directory(runtime / "backups")
    _require(not (root / quarantine).exists(), "Quarentena do plano já existe")
    journal = {
        "format_version": FORMAT_VERSION,
        "status": "moving",
        "plan_id": plan_id,
        "plan": plan,
        "quarantine": quarantine,
        "candidates": plan["candidates"],
        "files": plan["candidate_files"],
        "moved": [],
        "deleted": [],
        "removed_files": {},
        "removing_file": None,
        "deleting_id": None,
    }
    _write(runtime / JOURNAL_FILE, journal)
    (root / quarantine).mkdir(mode=0o700)
    try:
        for identifier in journal["candidates"]:
            original = _directory(root / identifier)
            _require(
                {child.name for child in original.iterdir()} == set(journal["files"][identifier]),
                "Arquivo desconhecido antes da retenção",
            )
            for name, expected in journal["files"][identifier].items():
                _require(
                    _file(
                        original / name,
                        MAX_IMAGE_BYTES
                        if name in ("containerops.dump", "application-image.tar")
                        else MAX_METADATA_BYTES,
                    )
                    == expected,
                    "Artefato mudou após o plano de retenção",
                )
            os.rename(original, root / quarantine / identifier)
            journal["moved"].append(identifier)
            _write(runtime / JOURNAL_FILE, journal)
        journal["status"] = "deleting"
        _write(runtime / JOURNAL_FILE, journal)
        return _finish_retention(runtime, journal)
    except BaseException:
        journal["status"] = "interrupted"
        _write(runtime / JOURNAL_FILE, journal)
        raise


def recover_retention(runtime, plan_id, *, context="default"):
    """Retoma o plano interrompido com operation_lock, sem escolher novos IDs."""
    runtime = _directory(runtime)
    journal, _ = _json(runtime / JOURNAL_FILE)
    _require(
        journal.get("format_version") == FORMAT_VERSION
        and journal.get("status") in ("moving", "deleting", "interrupted"),
        "Nenhuma retenção interrompida recuperável",
    )
    _require(
        journal.get("plan_id") == plan_id and bool(_HASH.fullmatch(str(plan_id))),
        "plan-id não corresponde ao journal",
    )
    approved = journal.get("plan", {})
    _require(
        approved.get("plan_id") == plan_id
        and _digest({key: value for key, value in approved.items() if key != "plan_id"}) == plan_id,
        "Journal não corresponde ao plano aprovado",
    )
    _require(
        approved.get("context") == context
        and approved.get("candidates") == journal["candidates"]
        and approved.get("candidate_files") == journal["files"],
        "IDs/arquivos do journal não correspondem ao plano aprovado",
    )
    _require(
        journal.get("quarantine") == ".prune-" + plan_id[:24], "Quarentena do journal inválida"
    )
    _require(
        isinstance(journal.get("candidates"), list)
        and len(set(journal["candidates"])) == len(journal["candidates"]),
        "Candidatos do journal inválidos",
    )
    _require(set(journal["files"]) == set(journal["candidates"]), "Arquivos do journal inválidos")
    root = _directory(runtime / "backups")
    _guard_retention(runtime, journal)
    quarantine = _directory(root / journal["quarantine"], allow_missing=True)
    quarantine.mkdir(mode=0o700, exist_ok=True)
    for identifier in journal["candidates"]:
        _id(identifier)
        _require(set(journal["files"][identifier]) <= FILES, "Arquivos não permitidos no journal")
        original, target = root / identifier, quarantine / identifier
        if original.exists():
            _require(not target.exists(), "ID duplicado entre backup e quarentena")
            _directory(original)
            _require(
                {child.name for child in original.iterdir()} == set(journal["files"][identifier]),
                "Arquivo desconhecido na recuperação",
            )
            for name, expected in journal["files"][identifier].items():
                _require(
                    _file(
                        original / name,
                        MAX_IMAGE_BYTES
                        if name in ("containerops.dump", "application-image.tar")
                        else MAX_METADATA_BYTES,
                    )
                    == expected,
                    "Artefato mudou antes da recuperação",
                )
            os.rename(original, target)
    journal["status"] = "deleting"
    _write(runtime / JOURNAL_FILE, journal)
    try:
        return _finish_retention(runtime, journal)
    except BaseException:
        journal["status"] = "interrupted"
        _write(runtime / JOURNAL_FILE, journal)
        raise
