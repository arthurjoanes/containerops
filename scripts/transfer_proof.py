"""Exercise a portable backup in a fresh local session, with disposable PostgreSQL volumes."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import backup_package
import checks
import ops
import proof
from runtime_context import resolve_context


def destination_context(session):
    checks.require(
        ops.CONTEXT.session is not None, "Transferência exige CONTAINEROPS_SESSION ativo"
    )
    environment = {
        **os.environ,
        "CONTAINEROPS_RUNTIME": str(ops.CONTEXT.base),
        "CONTAINEROPS_SESSION": session,
    }
    destination = resolve_context(ops.ROOT, environment=environment)
    checks.require(destination.session != ops.CONTEXT.session, "Destino precisa de outra sessão")
    backup_package.safe_path(destination.runtime)
    checks.require(not destination.runtime.exists(), "Sessão de destino já existe; use slug novo")
    return destination, environment


def inventory(project=None):
    """Só consulta IDs; uptime e health ficam fora por variarem entre as chamadas."""
    label = ["--filter", "label=com.docker.compose.project=" + project] if project else []
    return {
        "containers": sorted(ops.run(["docker", "ps", "-aq", *label], capture=True).stdout.split()),
        "volumes": sorted(
            ops.run(["docker", "volume", "ls", "-q", *label], capture=True).stdout.split()
        ),
        "networks": sorted(
            ops.run(["docker", "network", "ls", "-q", *label], capture=True).stdout.split()
        ),
    }


def default_demo_state():
    state = {}
    for name in ("state.json", "latest-backup.json"):
        path = ops.CONTEXT.base / name
        state[name] = backup_package.file_hash(path) if path.exists() else None
    resources = inventory("pf-containerops")
    services = []
    if resources["containers"]:
        raw = ops.run(["docker", "inspect", *resources["containers"]], capture=True).stdout
        for item in backup_package.decode_json(raw.encode()):
            services.append(
                {
                    "id": item["Id"],
                    "image": item["Image"],
                    "running": item["State"]["Running"],
                    "paused": item["State"]["Paused"],
                    "started_at": item["State"]["StartedAt"],
                    "restart_count": item["RestartCount"],
                }
            )
    return {"state_sha256": state, "resources": resources, "services": services}


def credential_fingerprints(directory):
    return {
        name: backup_package.file_hash(Path(directory) / "secrets" / name)
        for name in ("db_admin", "db_migrator", "db_app", "db_backup", "api_tokens")
    }


def public_record(value):
    if isinstance(value, dict):
        return {key: public_record(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_record(item) for item in value]
    if isinstance(value, str):
        return value.replace(str(ops.CONTEXT.base), "<runtime-base>")
    return value


def transfer_auxiliaries(destination):
    records = []
    # O pacote leva só a aplicação; banco e proxy já precisam existir no host.
    # Conferir todas as tags antes da alteração evita mudanças parciais por conflito.
    for component in ("database", "proxy"):
        source_id = ops.image_id(ops.CONTEXT.image(component))
        tag = destination.image(component)
        found = ops.run(["docker", "image", "inspect", tag], capture=True, check=False)
        if found.returncode == 0:
            value = backup_package.decode_json(found.stdout.encode())
            checks.require(
                type(value) is list and len(value) == 1 and value[0].get("Id") == source_id,
                "Tag auxiliar de destino já aponta para outra imagem",
            )
        records.append({"component": component, "image_id": source_id, "destination_tag": tag})
    for record in records:
        ops.run(["docker", "tag", record["image_id"], record["destination_tag"]])
    return records


class TransferRun:
    def __init__(self, destination):
        self.started = time.monotonic()
        run_id = uuid.uuid4().hex
        self.directory = ops.EVIDENCE / "transfer-proof" / run_id
        backup_package.safe_path(self.directory)
        self.directory.mkdir(parents=True)
        self.data = {
            "run_id": run_id,
            "status": "in_progress",
            "started_at": ops.now(),
            "completed_at": None,
            "source_context": ops.CONTEXT.session,
            "destination_context": destination.session,
            "project": "pf-containerops-test-" + run_id[:8],
            "phases": [],
            "commands": [],
            "limits": [
                "Two reserved contexts on one Docker host; no independent physical backup or second-computer recovery was demonstrated.",
                "The logical dump and application image travel; compatible database/proxy images are reused by ImageID on this host and require independent provisioning for a fully offline destination.",
                "Measured phases share daemon/CPU/RAM with other sessions; durations are observations, not an RTO guarantee.",
                "Restore executes trusted source content in a disposable database; this proof does not adopt it as a main service.",
            ],
        }
        self.save()

    def save(self):
        self.data["elapsed_seconds"] = round(time.monotonic() - self.started, 3)
        ops.write_json(self.directory / "manifest.json", self.data)
        ops.write_json(ops.EVIDENCE / "transfer-proof-latest.json", self.data)

    def phase(self, name, callback):
        phase = {"name": name, "started_at": ops.now(), "status": "in_progress"}
        self.data["phases"].append(phase)
        self.save()
        started = time.monotonic()
        try:
            value = callback()
            phase["status"] = "passed"
            return value
        except BaseException as error:
            phase.update(status="failed", error_category=type(error).__name__)
            raise
        finally:
            phase.update(
                completed_at=ops.now(), elapsed_seconds=round(time.monotonic() - started, 3)
            )
            self.save()

    def archive(self, name):
        source = ops.read_json(ops.EVIDENCE / (name + ".json"))
        target = self.directory / (name + ".json")
        checks.require(not target.exists(), "Evidência da transferência já existe")
        ops.write_json(target, proof.public_record(source))
        self.data.setdefault("evidence", []).append(
            {
                "path": "transfer-proof/" + self.data["run_id"] + "/" + target.name,
                "sha256": backup_package.file_hash(target),
            }
        )
        self.save()


def _private_worker_result(run_id, result):
    directory = ops.RUNTIME / "transfer-private" / run_id
    backup_package.safe_path(directory)
    directory.mkdir(parents=True, mode=0o700)
    proof.protect_copy_directory(directory)
    path = directory / "receipt.json"
    checks.require(not path.exists(), "Recibo da transferência já existe")
    ops.write_json(path, result)
    if os.name != "nt":
        path.chmod(0o600)
    proof.protect_copy_directory(directory)


def receive_package(source, corrupted, run_id):
    """O ambiente do destino precisa estar definido antes dos imports.

    ops fixa o contexto ao ser carregado pelo processo filho.
    """
    checks.require(ops.CONTEXT.session is not None, "Destino exige sessão reservada")
    checks.require(bool(re.fullmatch(r"[a-f0-9]{32}", run_id)), "ID da transferência inválido")
    backup_package.safe_path(ops.RUNTIME)
    ops.setup()
    backups = ops.RUNTIME / "backups"
    backup_package.safe_path(backups)
    backups.mkdir(parents=True, exist_ok=True)
    before = inventory()
    checked_project = "pf-containerops-restore-" + run_id[:8]
    own_before = inventory(checked_project)
    checks.require(
        own_before == {"containers": [], "volumes": [], "networks": []},
        "Projeto do controle de corrupção já existe",
    )
    try:
        backup_package.import_backup(corrupted, backups, "corruption-" + run_id)
    except RuntimeError as error:
        checks.require(
            str(error) == "Checksum de backup inválido",
            "Controle de corrupção foi recusado por outra causa",
        )
    else:
        raise RuntimeError("Pacote corrompido foi aceito")
    after = inventory()
    own_after = inventory(checked_project)
    checks.require(
        own_before == own_after and not (backups / ("corruption-" + run_id)).exists(),
        "Corrupção criou recursos ou cópia catalogável",
    )
    pointer = ops.RUNTIME / "latest-backup.json"
    pointer_before = backup_package.file_hash(pointer) if pointer.exists() else None
    ops.execute_backups(ops.parse_arguments(["backup-import", "--source", str(source)]))
    imported = ops.read_json(ops.EVIDENCE / "backup-import.json")["backup_id"]
    ops.execute_backups(
        ops.parse_arguments(["backup-image-load", "--backup", imported, "--trusted-image-source"])
    )
    restored = ops.restore_test(backup_id=imported, trusted_source=True)
    checks.require(
        restored["status"] == "passed"
        and restored["cleanup_succeeded"]
        and restored["snapshot_equal"]
        and restored["checksum_verified"],
        "Destino não concluiu restauração e limpeza",
    )
    directory = ops.RUNTIME / "environments" / restored["project"]
    fingerprints = credential_fingerprints(directory)
    pointer_after = backup_package.file_hash(pointer) if pointer.exists() else None
    checks.require(
        pointer_before == pointer_after, "Importação substituiu o ponteiro principal do destino"
    )
    target = backups / imported
    checked = backup_package.validate_backup(target, portable=True)
    checks.require(
        inventory(restored["project"]) == {"containers": [], "volumes": [], "networks": []},
        "Recursos temporários de restore ainda existem",
    )
    import report

    report.generate(ops.ROOT, ops.RUNTIME)
    result = {
        "run_id": run_id,
        "backup_id": imported,
        "source": checked["manifest"]["backup"]["source"],
        "files": checked["files"],
        "restore": restored,
        "credential_fingerprints": fingerprints,
        "main_pointer_unchanged": True,
        "corruption": {
            "rejected_before_resources": True,
            "inventory_before": before,
            "inventory_after": after,
            "checked_project": checked_project,
            "own_resources_before": own_before,
            "own_resources_after": own_after,
        },
        "cleanup_succeeded": True,
    }
    _private_worker_result(run_id, result)
    return result


def _source_work(attempt, destination, environment, stack):
    source = attempt.phase("source-snapshot", proof.source_manifest)
    attempt.data["source"] = source
    attempt.data["default_demo_before"] = default_demo_state()
    attempt.data["inventory_before"] = inventory()
    image1 = ops.image_id(ops.CONTEXT.image("app", "1.0.0"))
    image2 = ops.image_id(ops.CONTEXT.image("app", "2.0.0"))
    attempt.phase("image-sources-release1", lambda: proof.image_sources(image1))
    attempt.phase("image-sources-release2", lambda: proof.image_sources(image2))
    stack.image = image1
    attempt.phase("start-source", stack.start)
    text = "Olá mundo! Café e ação. 東京 42"
    job = attempt.phase("unicode-job", lambda: stack.completed(stack.job(text)["id"]))
    checks.require(
        job["result"] == {"word_count": 7, "checksum": hashlib.sha256(text.encode()).hexdigest()},
        "Novo job Unicode incorreto",
    )
    attempt.phase("successful-release", lambda: ops.release(stack, "2.0.0", candidate_image=image2))
    attempt.archive("release")
    attempt.phase("tls", lambda: ops.tls_setup(stack))
    attempt.archive("tls")
    attempt.phase("hardening", lambda: checks.hardening(stack))
    attempt.archive("hardening")
    source_before = proof.source_state(stack)
    backup = attempt.phase("logical-backup", lambda: ops.backup(stack))
    attempt.archive("backup")
    original = backup_package.validate_backup(backup)
    metadata = original["metadata"]
    transport = ops.RUNTIME / "transport"
    backup_package.safe_path(transport)
    transport.mkdir(parents=True, exist_ok=True)
    package = transport / ("package-" + attempt.data["run_id"])
    exported = attempt.phase(
        "portable-export",
        lambda: backup_package.export_backup(
            backup, package, source_context=ops.CONTEXT.session, include_image=True, run=ops.run
        ),
    )
    copied, _record = attempt.phase(
        "corruption-copy",
        lambda: backup_package.import_backup(
            package, transport, "corrupt-" + attempt.data["run_id"]
        ),
    )
    with (copied / "containerops.dump").open("r+b") as stream:
        byte = stream.read(1)
        stream.seek(0)
        stream.write(bytes([byte[0] ^ 1]))
    attempt.data["images"] = {
        "application": exported["manifest"]["application_image"],
        "auxiliaries": attempt.phase(
            "destination-auxiliary-images", lambda: transfer_auxiliaries(destination)
        ),
    }
    command = [
        sys.executable,
        str(ops.ROOT / "scripts" / "transfer_proof.py"),
        "--receive",
        str(package),
        "--corrupt",
        str(copied),
        "--run-id",
        attempt.data["run_id"],
    ]
    attempt.data["commands"] = [
        "python scripts/transfer_proof.py --destination-session " + destination.session,
        "backup-export --backup <source-id> --destination <private-package> --include-image",
        "backup-import --source <private-package>",
        "backup-image-load --backup <import-id> --trusted-image-source",
        "restore-test --backup <import-id> --trusted-source",
        "report",
    ]
    attempt.phase(
        "destination-child", lambda: ops.run(command, env=environment, capture=True, timeout=1200)
    )
    received = backup_package.read_json(
        destination.runtime / "transfer-private" / attempt.data["run_id"] / "receipt.json"
    )
    own = credential_fingerprints(stack.directory)
    other = received.pop("credential_fingerprints")
    checks.require(
        set(own) == set(other) and all(own[name] != other[name] for name in own),
        "Destino reutilizou credenciais da origem",
    )
    checks.require(
        received["files"] == exported["files"]
        and received["restore"]["restored_snapshot"] == metadata["snapshot"],
        "Dados/arquivos do destino divergem do pacote",
    )
    source_after = proof.source_state(stack)
    checks.require(source_after == source_before, "Origem mudou durante a transferência")
    checks.require(
        backup_package.validate_backup(backup)["files"] == original["files"],
        "Backup original mudou durante a transferência",
    )
    source_after_manifest = proof.source_manifest()
    checks.require(
        source_after_manifest == source, "Fontes/HEAD/diff mudaram durante a transferência"
    )
    checks.require(
        default_demo_state() == attempt.data["default_demo_before"],
        "Demonstração principal mudou durante a transferência",
    )
    attempt.data.update(
        destination=public_record(received),
        source_after=source_after_manifest,
        preservation={
            "source_state_equal": True,
            "original_backup_unchanged": True,
            "original_files": original["files"],
            "credentials_independent": True,
            "default_demo_unchanged": True,
            "source_and_revision_unchanged": True,
        },
    )


def run_transfer(session):
    destination, environment = destination_context(session)
    attempt = TransferRun(destination)
    stack = None
    cleanup_required = False
    try:
        stack = ops.Stack(attempt.data["project"])
        stack.assert_fresh()
        cleanup_required = True
        _source_work(attempt, destination, environment, stack)
    except BaseException as error:
        attempt.data.update(status="failed", error_category=type(error).__name__)
        raise
    finally:
        try:
            if cleanup_required:
                attempt.phase("cleanup-source", stack.destroy_test)
                attempt.phase("cleanup-source-check", stack.assert_fresh)
            attempt.data["cleanup_succeeded"] = True
        except BaseException as error:
            attempt.data.update(
                status="failed",
                cleanup_succeeded=False,
                cleanup_error_category=type(error).__name__,
            )
            raise
        finally:
            try:
                attempt.data["inventory_after"] = inventory()
                if "default_demo_before" in attempt.data:
                    attempt.data["default_demo_after"] = default_demo_state()
                    checks.require(
                        attempt.data["default_demo_after"] == attempt.data["default_demo_before"],
                        "Demonstração principal mudou durante a limpeza",
                    )
                if "source" in attempt.data:
                    attempt.data["source_final"] = proof.source_manifest()
                    checks.require(
                        attempt.data["source_final"] == attempt.data["source"],
                        "Fontes mudaram durante a limpeza",
                    )
            except BaseException as error:
                attempt.data.update(status="failed", inventory_error_category=type(error).__name__)
                raise
            finally:
                if attempt.data["status"] == "in_progress":
                    attempt.data["status"] = "passed"
                attempt.data.update(completed_at=ops.now())
                attempt.save()
                print(f"Transferência: {attempt.directory / 'manifest.json'}", flush=True)
    return attempt.data


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--destination-session")
    mode.add_argument("--receive", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--corrupt", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.receive is not None and (args.corrupt is None or args.run_id is None):
        parser.error("Destino interno exige controle de corrupção e run-id")
    if args.destination_session and (args.corrupt is not None or args.run_id is not None):
        parser.error("Opções internas não se aplicam à origem")
    return args


def main():
    args = parse_arguments()
    checks.require(
        ops.CONTEXT.session is not None, "Transferência exige CONTAINEROPS_SESSION ativo"
    )
    with ops.operation_lock():
        if args.receive is not None:
            receive_package(args.receive, args.corrupt, args.run_id)
        else:
            run_transfer(args.destination_session)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"erro: {error}", file=sys.stderr)
        raise SystemExit(1) from error
