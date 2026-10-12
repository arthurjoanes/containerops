from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path

from runtime_context import active_context, ensure_file_path, ensure_no_links

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = active_context(ROOT)
DEFAULT_RUNTIME = CONTEXT.base
RUNTIME = CONTEXT.runtime
EVIDENCE = CONTEXT.evidence
MAIN = CONTEXT.main
RELEASES = ("1.0.0", "2.0.0")


@contextmanager
def operation_lock(directory=RUNTIME):
    """The OS releases this lock when the process exits, including after a crash."""
    directory = ensure_no_links(Path(directory))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = directory / "operation.lock"
    if lock_path.exists() or lock_path.is_symlink():
        info = lock_path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise RuntimeError("operation.lock deve ser arquivo regular privado")
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    if os.name != "nt":
        os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "r+b") as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError(
                "Outra operação está usando este runtime. Aguarde; não apague operation.lock."
            ) from error
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def release_version(image):
    inspected = json.loads(run(["docker", "image", "inspect", image], capture=True).stdout)[0]
    version = inspected["Config"].get("Labels", {}).get("org.opencontainers.image.version")
    if version not in RELEASES:
        raise RuntimeError("Imagem não pertence a uma release ContainerOps suportada")
    return version


def now():
    return datetime.now(UTC).isoformat()


def write_json(path, data):
    ensure_file_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        if os.name != "nt" and path.absolute().is_relative_to(RUNTIME):
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def run(args, *, env=None, capture=False, timeout=300, check=True, cwd=ROOT):
    process_env = {**(os.environ if env is None else env), "PYTHONUTF8": "1"}
    result = subprocess.run(
        [str(a) for a in args],
        cwd=cwd,
        env=process_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if not capture or result.returncode:
        if result.stdout:
            print(result.stdout.rstrip(), flush=True)
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr, flush=True)
    if check and result.returncode:
        raise RuntimeError(f"Comando falhou ({result.returncode}): {' '.join(map(str, args[:6]))}")
    return result


class JobFailed(RuntimeError):
    """A terminal job cannot succeed by polling it again."""


def wait_for(predicate, description, timeout=90):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except JobFailed:
            raise
        except (RuntimeError, OSError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(0.35)
    raise RuntimeError(f"Timeout: {description}: {last_error}")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def setup_secrets(directory):
    secret_dir = directory / "secrets"
    ensure_no_links(secret_dir)
    secret_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
        secret_dir.chmod(0o700)
    for name in ("db_admin", "db_migrator", "db_app", "db_backup", "sentinel"):
        path = secret_dir / name
        assert_regular_private(path)
        if not path.exists():
            path.write_text("containerops-demo-" + secrets.token_hex(24), encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)
    token_file = secret_dir / "api_tokens"
    assert_regular_private(token_file)
    if not token_file.exists():
        write_json(
            token_file, {owner: "demo-" + secrets.token_urlsafe(32) for owner in ("alice", "bob")}
        )
    # Docker Desktop file mounts do not honor all POSIX mode fields. Effective
    # permissions are checked in the running containers, not assumed here.
    if os.name != "nt":
        token_file.chmod(0o644)
        for name in ("db_admin", "db_migrator", "db_app", "db_backup"):
            (secret_dir / name).chmod(0o644)


def assert_regular_private(path):
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise RuntimeError("Arquivo privado precisa ser regular, sem links/junções")


class Stack:
    def __init__(self, project=MAIN, *, image=None, port=None, tls=False):
        if project != MAIN and not re.fullmatch(
            r"pf-containerops-(?:test|restore)-[a-f0-9]{8}", project
        ):
            raise ValueError("Nome Compose fora do escopo ContainerOps")
        self.project = project
        self._owns_resources = False
        self.directory = RUNTIME if project == MAIN else RUNTIME / "environments" / project
        ensure_no_links(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_path = self.directory / "state.json"
        assert_regular_private(self.state_path)
        state = read_json(self.state_path) if self.state_path.exists() else {}
        self.image = image or state.get("image", CONTEXT.image("app", "1.0.0"))
        default_port = (
            int(
                os.environ.get(
                    "HTTP_PORT", state.get("port", free_port() if CONTEXT.session else 8105)
                )
            )
            if project == MAIN
            else free_port()
        )
        self.port = port if port is not None else default_port
        self.tls_port = (
            int(
                os.environ.get(
                    "TLS_PORT", state.get("tls_port", free_port() if CONTEXT.session else 8445)
                )
            )
            if project == MAIN
            else free_port()
        )
        while project != MAIN and self.tls_port == self.port:
            self.tls_port = free_port()
        reserved = {
            3101,
            8101,
            5541,
            3102,
            8102,
            5542,
            3103,
            8103,
            4043,
            3104,
            8104,
            8444,
            5544,
            6384,
            9104,
            9184,
            9194,
            16684,
        }
        if any(
            type(value) is not int or value in reserved or not 1 <= value <= 65535
            for value in (self.port, self.tls_port)
        ):
            raise ValueError("Porta inválida ou reservada a outro projeto")
        if self.port == self.tls_port:
            raise ValueError("HTTP_PORT e TLS_PORT precisam usar portas diferentes")
        self.tls = tls or state.get("tls", False)

    def env(self):
        return {
            **os.environ,
            "CONTAINEROPS_RUNTIME": self.directory.as_posix(),
            "APP_IMAGE": self.image,
            "PROXY_IMAGE": CONTEXT.image("proxy"),
            "DATABASE_IMAGE": CONTEXT.image("database"),
            "TEST_IMAGE": CONTEXT.image("test"),
            "HTTP_PORT": str(self.port),
            "TLS_PORT": str(self.tls_port),
            "COMPOSE_ANSI": "never",
        }

    def compose(self, *args, capture=False, check=True, timeout=300):
        command = [
            "docker",
            "compose",
            "--project-directory",
            ROOT,
            "-p",
            self.project,
            "-f",
            ROOT / "compose.yaml",
        ]
        if self.tls:
            command += ["-f", ROOT / "compose.tls.yaml"]
        return run([*command, *args], env=self.env(), capture=capture, check=check, timeout=timeout)

    def save(self, **extra):
        state = read_json(self.state_path) if self.state_path.exists() else {}
        # Consulte o banco: o schema pode ter mudado desde a última migração.
        state.pop("schema_version", None)
        write_json(
            self.state_path,
            {
                **state,
                "image": self.image,
                "port": self.port,
                "tls": self.tls,
                "tls_port": self.tls_port,
                **extra,
            },
        )

    def assert_fresh(self):
        if self.project == MAIN:
            raise RuntimeError("O projeto principal não pode receber testes de falha")
        label = "label=com.docker.compose.project=" + self.project
        for resource, args in (
            ("container", ["ps", "-a", "-q"]),
            ("volume", ["volume", "ls", "-q"]),
            ("network", ["network", "ls", "-q"]),
        ):
            if run(["docker", *args, "--filter", label], capture=True).stdout.strip():
                raise RuntimeError(f"Projeto de teste já contém {resource}: {self.project}")
        self._owns_resources = True

    def cid(self, service):
        value = self.compose("ps", "-a", "-q", service, capture=True).stdout.strip()
        if not value or "\n" in value:
            raise RuntimeError(f"Container único não encontrado: {service}")
        return value

    def inspect(self, service):
        return json.loads(run(["docker", "inspect", self.cid(service)], capture=True).stdout)[0]

    def manage(self, *args):
        result = self.compose(
            "run",
            "--rm",
            "--no-deps",
            "manage",
            "python",
            "-m",
            "containerops.manage",
            *args,
            capture=True,
        )
        return json.loads(result.stdout) if result.stdout.strip() else None

    def snapshot(self):
        return self.manage("snapshot")

    def request(self, method, path, data=None, *, owner="alice", key=None, tls=False):
        headers = {}
        if owner:
            headers["Authorization"] = (
                "Bearer " + read_json(self.directory / "secrets" / "api_tokens")[owner]
            )
        if key:
            headers["Idempotency-Key"] = key
        raw = None
        if data is not None:
            raw = json.dumps(data, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        port = self.tls_port if tls else self.port
        req = urllib.request.Request(
            f"{'https' if tls else 'http'}://127.0.0.1:{port}{path}", raw, headers, method=method
        )
        context = (
            ssl.create_default_context(cafile=str(self.directory / "tls" / "ca.crt"))
            if tls
            else None
        )
        try:
            with urllib.request.urlopen(req, timeout=8, context=context) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                pass
            return exc.code, body

    def ready(self):
        return wait_for(
            lambda: self.request("GET", "/health/ready", owner=None)[0] == 200,
            "readiness pelo proxy",
            timeout=120,
        )

    def start(self, target=None):
        self.image = image_id(self.image)
        version = release_version(self.image)
        required = 2 if version == "2.0.0" else 1
        if target is not None and target != required:
            raise RuntimeError("Migração não corresponde à imagem selecionada")
        target = required
        setup_secrets(self.directory)
        self.compose("config", "--quiet")
        self.compose("up", "-d", "--wait", "--wait-timeout", "100", "db")
        probe = (
            "from containerops.config import Settings\nfrom containerops.database import connect\n"
            "with connect(Settings.from_env()) as c:\n"
            " exists=c.execute(\"SELECT to_regclass('public.schema_version')\").fetchone()[0]\n"
            " print(c.execute('SELECT version FROM schema_version').fetchone()[0] if exists else 0)\n"
        )
        current = int(
            self.compose(
                "run", "--rm", "--no-deps", "migrate", "python", "-c", probe, capture=True
            ).stdout.strip()
        )
        target = max(
            target, current
        )  # Starting release 1 after rollback never downgrades schema 2.
        self.compose(
            "run",
            "--rm",
            "--no-deps",
            "migrate",
            "python",
            "-m",
            "containerops.manage",
            "migrate",
            "--target",
            str(target),
        )
        self.compose("up", "-d", "--wait", "--wait-timeout", "120", "api", "worker", "proxy")
        self.ready()
        self.save()

    def stop(self):
        self.compose("down", "--remove-orphans")

    def destroy_test(self):
        if self.project == MAIN or not self.project.startswith(
            ("pf-containerops-test-", "pf-containerops-restore-")
        ):
            raise RuntimeError("Exclusão permitida apenas em projeto de teste gerado")
        if not self._owns_resources:
            raise RuntimeError("Limpeza exige projeto conferido vazio por esta tentativa")
        self.compose("--profile", "tools", "down", "--volumes", "--remove-orphans")

    def job(self, text="Olá mundo! Café e ação. 東京 42", *, duration=0, key=None):
        status, result = self.request(
            "POST",
            "/v1/jobs",
            {"text": text, "demo_duration_seconds": duration},
            key=key or uuid.uuid4().hex,
        )
        if status not in (200, 201, 202):
            raise RuntimeError(f"Admissão falhou: {status}: {result}")
        return result

    def completed(self, job_id, timeout=70):
        def poll():
            status, job = self.request("GET", f"/v1/jobs/{job_id}")
            if status == 200 and job["state"] == "failed":
                raise JobFailed(f"Job falhou: {job}")
            return job if status == 200 and job["state"] == "succeeded" else None

        return wait_for(poll, f"job {job_id} concluído", timeout)


def evidence(name, data):
    write_json(EVIDENCE / f"{name}.json", {"recorded_at": now(), **data})


def setup():
    import proof

    ensure_no_links(RUNTIME).mkdir(parents=True, exist_ok=True, mode=0o700)
    if CONTEXT.session:
        if os.name == "nt":
            proof.protect_copy_directory(RUNTIME)
        else:
            # A runtime contains directories; the flat backup-copy check does not apply.
            RUNTIME.chmod(0o700)
            if stat.S_IMODE(RUNTIME.stat().st_mode) != 0o700:
                raise RuntimeError("Runtime da sessão não protegido")
    setup_secrets(RUNTIME)
    run(["docker", "version"], capture=True)
    run(["docker", "compose", "version"])
    print(f"Runtime demo: {RUNTIME}\nCredenciais: {RUNTIME / 'secrets' / 'api_tokens'}")


def catalog_evidence():
    import backup_catalog

    catalog = backup_catalog.discover(RUNTIME, context=CONTEXT.session or "default")
    fields = (
        "id",
        "source_project",
        "source_context",
        "created_at",
        "schema_version",
        "size_bytes",
        "validation",
        "protected",
        "imported",
        "restore_verified",
    )
    evidence(
        "backup-catalog",
        {
            "context": CONTEXT.session or "default",
            "entries": [
                {
                    key: bool(entry.get(key)) if key == "restore_verified" else entry.get(key)
                    for key in fields
                }
                for entry in catalog["entries"]
            ],
        },
    )
    return catalog


def build(version):
    import supply

    setup()
    supply.build(ROOT, RUNTIME, version)
    with supply.build_context(ROOT, RUNTIME) as context:
        run(
            [
                "docker",
                "buildx",
                "build",
                "--builder",
                CONTEXT.builder,
                "--load",
                "--provenance=false",
                "--tag",
                CONTEXT.image("database"),
                "--build-arg",
                "POSTGRES_IMAGE=" + supply.image_lock(ROOT)["postgres"],
                "-f",
                context / "docker" / "db" / "Dockerfile",
                context,
            ],
            timeout=600,
            cwd=context,
        )
        run(
            [
                "docker",
                "buildx",
                "build",
                "--builder",
                CONTEXT.builder,
                "--load",
                "--provenance=false",
                "--tag",
                CONTEXT.image("proxy"),
                "-f",
                context / "docker" / "proxy" / "Dockerfile",
                context,
            ],
            timeout=600,
            cwd=context,
        )


def drain(stack):
    stack.manage("pause")

    def empty():
        snapshot = stack.snapshot()
        return (
            snapshot if snapshot["counts"]["queued"] == snapshot["counts"]["running"] == 0 else None
        )

    return wait_for(empty, "drenagem da fila", timeout=120)


def backup(stack):
    import backup_catalog
    import backup_package
    import proof

    started = time.monotonic()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    destination = RUNTIME / "backups" / stamp
    ensure_no_links(destination)
    destination.mkdir(parents=True, mode=0o700)
    proof.protect_copy_directory(destination)
    helper = stack.project + "-backup-" + uuid.uuid4().hex[:8]
    active_image = stack.inspect("api")["Image"]
    running_images(stack, active_image)
    previous_pause = stack.snapshot()["admission_paused"]
    try:
        cutoff_started_at = now()
        snapshot = drain(stack)
        cutoff_completed_at = now()
        stack.compose(
            "run",
            "--name",
            helper,
            "--no-deps",
            "backup",
            "dump",
            "/var/lib/postgresql/data/containerops.dump",
        )
        dump = destination / "containerops.dump"
        run(["docker", "cp", f"{helper}:/var/lib/postgresql/data/containerops.dump", dump])
        server = stack.compose(
            "exec", "-T", "db", "postgres", "--version", capture=True
        ).stdout.strip()
        tool = stack.compose(
            "exec", "-T", "db", "pg_dump", "--version", capture=True
        ).stdout.strip()
        image_release = release_version(active_image)
        build_record = EVIDENCE / f"build-{image_release}.json"
        image_identity = {}
        if build_record.is_file():
            built = read_json(build_record)
            if built.get("image_id") == active_image:
                image_identity["config_digest"] = built["runtime_link"]["config_digest"]
                image_identity["oci_manifest_digest"] = built["runtime_link"][
                    "platform_manifest_digest"
                ]
        metadata = {
            "created_at": now(),
            "cutoff_interval": {
                "started_at": cutoff_started_at,
                "completed_at": cutoff_completed_at,
                "method": "admission paused; queued/running drained; snapshot observed in interval",
            },
            "sha256": backup_package.file_hash(dump),
            "server": server,
            "tool": tool,
            "schema_version": snapshot["schema_version"],
            "snapshot": snapshot,
            "source_project": stack.project,
            "source_context": CONTEXT.session or "default",
            "source_ref": {
                key: value
                for key, value in proof.source_manifest().items()
                if key in ("revision", "sha256", "files")
            },
            "image": active_image,
            "image_release": image_release,
            **image_identity,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        write_json(destination / "metadata.json", metadata)
        proof.protect_copy_directory(destination)
        evidence("backup", {**metadata, "directory": str(destination)})
        pointer_directory = RUNTIME if stack.project == MAIN else stack.directory
        write_json(pointer_directory / "latest-backup.json", {"directory": str(destination)})
        backup_catalog.register_backup(RUNTIME, stamp, context=CONTEXT.session or "default")
        catalog_evidence()
        print(f"Backup: {destination}")
        return destination
    finally:
        # Remove only the uniquely named helper started above, retaining artifacts volume.
        run(["docker", "rm", "-f", helper], capture=True, check=False)
        if not previous_pause:
            stack.manage("resume")


def restore_preflight(metadata, identity=None):
    import backup_package

    selected = backup_package.preflight_image(metadata, run, identity=identity)
    for component in ("database", "proxy"):
        try:
            image_id(CONTEXT.image(component))
        except RuntimeError as error:
            raise RuntimeError(
                f"Imagem auxiliar {component} ausente; execute build neste contexto ou obtenha a imagem compatível."
            ) from error
    tool = run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            "64m",
            "--cpus",
            "0.25",
            "--pids-limit",
            "32",
            "--entrypoint",
            "pg_restore",
            CONTEXT.image("database"),
            "--version",
        ],
        capture=True,
    ).stdout.strip()
    expected_major = re.search(
        r"(?:PostgreSQL\)?\s+)(\d+)", metadata.get("tool", metadata["server"])
    )
    actual_major = re.search(r"PostgreSQL\)?\s+(\d+)", tool)
    if not expected_major or not actual_major or expected_major[1] != actual_major[1]:
        raise RuntimeError("Ferramenta PostgreSQL incompatível com o backup")
    return {"image_id": selected, "pg_restore": tool, "schema_version": metadata["schema_version"]}


def restore_test(directory=None, *, backup_id=None, trusted_source=False):
    import backup_catalog
    import backup_package

    started = time.monotonic()
    result = {"started_at": now(), "status": "in_progress", "phases": []}
    stack = None
    cleanup_required = False

    def phase(name, callback):
        step = {"name": name, "started_at": now(), "status": "in_progress"}
        result["phases"].append(step)
        phase_start = time.monotonic()
        try:
            value = callback()
            step["status"] = "passed"
            return value
        except BaseException as error:
            step.update(status="failed", error_category=type(error).__name__)
            raise
        finally:
            step.update(
                completed_at=now(), elapsed_seconds=round(time.monotonic() - phase_start, 3)
            )

    evidence("restore", result)
    try:
        if backup_id is not None:
            if directory is not None:
                raise ValueError("Selecione apenas um ID de backup")
            directory = backup_catalog.select_backup(
                RUNTIME, backup_id, context=CONTEXT.session or "default"
            )
        directory = Path(
            directory or read_json(RUNTIME / "latest-backup.json")["directory"]
        ).resolve()
        if not directory.is_relative_to(RUNTIME / "backups"):
            raise RuntimeError("Origem de backup fora do runtime reservado")
        if directory.parent != (RUNTIME / "backups").resolve():
            raise RuntimeError("Origem de backup fora do runtime reservado")
        backup_package.safe_path(directory, directory=True)
        metadata = backup_package.read_json(directory / "metadata.json")
        dump = directory / "containerops.dump"

        def verify_checksum():
            backup_catalog.select_backup(
                RUNTIME, directory.name, context=CONTEXT.session or "default"
            )
            actual_hash = backup_package.file_hash(dump)
            if actual_hash != metadata["sha256"]:
                raise RuntimeError("Checksum de backup inválido")
            return actual_hash

        actual_hash = phase("checksum", verify_checksum)
        validated = phase("package-validation", lambda: backup_package.validate_backup(directory))
        metadata = validated["metadata"]
        catalog = backup_catalog.discover(RUNTIME, context=CONTEXT.session or "default")
        entry = next(item for item in catalog["entries"] if item["id"] == directory.name)
        if entry.get("imported") and not trusted_source:
            raise RuntimeError(
                "Backup importado exige --trusted-source: restore executa código da origem confiável."
            )
        identity = validated["manifest"]["application_image"] if validated["manifest"] else None
        preflight = phase("preflight", lambda: restore_preflight(metadata, identity))
        result.update(
            backup=str(directory),
            backup_sha256=actual_hash,
            backup_metadata_sha256=validated["files"]["metadata.json"]["sha256"],
            backup_created_at=metadata["created_at"],
            cutoff_interval=metadata.get("cutoff_interval"),
            backup_id=directory.name,
            source_project=metadata["source_project"],
            source_context=metadata.get("source_context", "default"),
            preflight=preflight,
        )
        stack = Stack(
            "pf-containerops-restore-" + uuid.uuid4().hex[:8], image=preflight["image_id"]
        )
        stack.assert_fresh()
        result["project"] = stack.project
        setup_secrets(stack.directory)
        cleanup_required = True
        recovery_started = time.monotonic()
        phase(
            "database-ready",
            lambda: stack.compose("up", "-d", "--wait", "--wait-timeout", "100", "db"),
        )
        stack.compose("create", "restore")
        helper = stack.cid("restore")
        staged = stack.directory / "restore.dump"
        with staged.open("xb") as output:
            copied = backup_package.file_record(dump, output=output)
        if copied["sha256"] != actual_hash or backup_package.file_hash(staged) != actual_hash:
            staged.unlink(missing_ok=True)
            raise RuntimeError("Backup mudou durante staging do restore")
        staged.chmod(0o644)  # Docker cp preserves the mode; pg_restore runs as UID 999.
        try:
            phase(
                "stage-dump",
                lambda: run(
                    ["docker", "cp", staged, f"{helper}:/var/lib/postgresql/data/containerops.dump"]
                ),
            )
        finally:
            staged.unlink()
        phase("pg-restore", lambda: run(["docker", "start", "-a", helper], timeout=120))
        restored = json.loads(run(["docker", "inspect", helper], capture=True).stdout)[0]
        if restored["State"]["ExitCode"] != 0:
            raise RuntimeError("pg_restore falhou")
        phase(
            "grants-and-schema",
            lambda: stack.compose(
                "run",
                "--rm",
                "--no-deps",
                "migrate",
                "python",
                "-m",
                "containerops.manage",
                "migrate",
                "--target",
                str(metadata["schema_version"]),
            ),
        )
        snapshot = phase("snapshot", stack.snapshot)
        if snapshot != metadata["snapshot"]:
            raise RuntimeError("Snapshot restaurado difere do backup")
        result["restored_snapshot"] = snapshot
        stack.manage("resume")
        phase(
            "application-ready",
            lambda: stack.compose(
                "up", "-d", "--wait", "--wait-timeout", "120", "api", "worker", "proxy"
            ),
        )
        stack.ready()
        job = phase(
            "new-job",
            lambda: stack.completed(stack.job("Backup restaurado com sucesso")["id"]),
        )
        if job["result"] != {
            "word_count": 4,
            "checksum": hashlib.sha256(b"Backup restaurado com sucesso").hexdigest(),
        }:
            raise RuntimeError("Novo job pós-restore incorreto")
        validated_at = now()
        result.update(
            checksum_verified=True,
            snapshot_equal=True,
            restored_jobs=len(snapshot["jobs"]),
            new_job=job,
            validated_at=validated_at,
            recovery_seconds=round(time.monotonic() - recovery_started, 3),
            timing_definition={
                "recovery": "recovery_seconds: after preflight/setup through validated new job; excludes cleanup (legacy scope)",
                "total": "total_seconds: entry through cleanup and resource absence check",
            },
            backup_age_seconds=(
                datetime.fromisoformat(validated_at)
                - datetime.fromisoformat(metadata["created_at"])
            ).total_seconds(),
        )
        if metadata.get("cutoff_interval"):
            result["cutoff_age"] = {
                "minimum_seconds": (
                    datetime.fromisoformat(validated_at)
                    - datetime.fromisoformat(metadata["cutoff_interval"]["completed_at"])
                ).total_seconds(),
                "maximum_seconds": (
                    datetime.fromisoformat(validated_at)
                    - datetime.fromisoformat(metadata["cutoff_interval"]["started_at"])
                ).total_seconds(),
            }
        if result["backup_age_seconds"] < 0:
            raise RuntimeError("Data do backup está no futuro; idade não é confiável")
    except BaseException as error:
        result.update(status="failed", error_category=type(error).__name__)
        raise
    finally:
        try:
            if cleanup_required:
                phase("cleanup", stack.destroy_test)
                phase("cleanup-check", stack.assert_fresh)
            result["cleanup_succeeded"] = True
        except BaseException as error:
            result.update(
                status="failed",
                cleanup_succeeded=False,
                cleanup_error_category=type(error).__name__,
            )
            raise
        finally:
            if result["status"] == "in_progress":
                result["status"] = "passed"
            if result["status"] != "passed":
                # Preserve facts, but do not expose the legacy success shape to the report.
                result["observations"] = {
                    key: result.pop(key)
                    for key in ("checksum_verified", "snapshot_equal", "new_job")
                    if key in result
                }
            result.update(completed_at=now(), total_seconds=round(time.monotonic() - started, 3))
            if result["status"] == "passed":
                try:
                    final_backup = backup_package.validate_backup(directory)
                    if final_backup["files"] != validated["files"]:
                        raise RuntimeError("Backup mudou durante restore; recibo recusado")
                    backup_catalog.record_restore_verified(
                        RUNTIME, directory.name, result, context=CONTEXT.session or "default"
                    )
                    catalog_evidence()
                except BaseException as error:
                    result.update(status="failed", error_category=type(error).__name__)
                    result["observations"] = {
                        key: result.pop(key)
                        for key in ("checksum_verified", "snapshot_equal", "new_job")
                        if key in result
                    }
                    evidence("restore", result)
                    raise
            evidence("restore", result)
    return result


def image_id(image):
    return json.loads(run(["docker", "image", "inspect", image], capture=True).stdout)[0]["Id"]


def running_images(stack, expected):
    actual = {service: stack.inspect(service)["Image"] for service in ("api", "worker")}
    if any(image != expected for image in actual.values()):
        raise RuntimeError("API/worker não usam a mesma imagem esperada")
    return actual


def preserved_candidate(stack, candidate):
    status, actual = stack.request("GET", f"/v1/jobs/{candidate['id']}")
    fields = ("id", "state", "attempts", "result", "error")
    if status != 200 or any(actual.get(key) != candidate.get(key) for key in fields):
        raise RuntimeError("Job criado após migração diverge depois do rollback")
    return actual


class InjectedSmokeFailure(RuntimeError):
    pass


def release(stack, version, inject_failure=False, *, candidate_image=None):
    if version != "2.0.0":
        raise ValueError("Use --version 2.0.0 para atualizar ou rollback para voltar")
    result = {
        "project": stack.project,
        "requested_version": version,
        "injected_smoke_failure": inject_failure,
        "phase": "preflight",
    }
    try:
        candidate = image_id(candidate_image or CONTEXT.image("app", version))
        old = stack.inspect("api")["Image"]
        result.update(previous_image=old, candidate_image=candidate)
        previous_pause = stack.snapshot()["admission_paused"]
        result["phase"] = "backup"
        backup(stack)
        started = time.monotonic()
        old_version = stack.request("GET", "/health/live", owner=None)[1]
        before = None
        try:
            result["phase"] = "drain"
            before = drain(stack)
            result["phase"] = "candidate"
            stack.image = candidate
            stack.compose(
                "run",
                "--rm",
                "--no-deps",
                "migrate",
                "python",
                "-m",
                "containerops.manage",
                "migrate",
                "--target",
                "2",
            )
            stack.compose(
                "up", "-d", "--no-deps", "--wait", "--wait-timeout", "90", "api", "worker"
            )
            stack.ready()
            live = stack.request("GET", "/health/live", owner=None)[1]
            result["candidate_images"] = running_images(stack, candidate)
            if live.get("version") != version:
                raise RuntimeError("Release candidata não corresponde à versão solicitada")
            if stack.snapshot()["jobs"] != before["jobs"]:
                raise RuntimeError("Dados alterados durante release")
            result["candidate_health"] = live
            stack.manage("resume")
            smoke = stack.completed(stack.job("Release candidata preserva dados")["id"])
            require_result = smoke["result"]
            if (
                require_result["word_count"] != 4
                or require_result["checksum"]
                != hashlib.sha256(b"Release candidata preserva dados").hexdigest()
                or smoke.get("algorithm") != "unicode-alnum-marks-v1"
            ):
                raise RuntimeError("Smoke funcional da candidata falhou")
            result["candidate_job"] = smoke
            drain(stack)
            if inject_failure:
                raise InjectedSmokeFailure("Falha de smoke injetada no teste de rollback")
            stack.save(previous_image=old)
            result["rolled_back"] = False
        except Exception as exc:
            if before is None:
                raise
            result["phase"] = "rollback"
            stack.image = old
            stack.compose(
                "up", "-d", "--no-deps", "--wait", "--wait-timeout", "90", "api", "worker"
            )
            stack.ready()
            result["rollback_images"] = running_images(stack, old)
            actual = {job["id"]: job for job in stack.snapshot()["jobs"]}
            if any(actual.get(job["id"]) != job for job in before["jobs"]):
                raise RuntimeError("Dados divergem depois do rollback") from exc
            if "candidate_job" in result:
                result["preserved_candidate_job"] = preserved_candidate(
                    stack, result["candidate_job"]
                )
            stack.manage("resume")
            result["rollback_job"] = stack.completed(stack.job("Rollback preserva dados")["id"])
            if result["rollback_job"]["result"] != {
                "word_count": 3,
                "checksum": hashlib.sha256(b"Rollback preserva dados").hexdigest(),
            }:
                raise RuntimeError("Resultado do job incorreto após rollback") from exc
            drain(stack)
            stack.save()
            result.update(
                rolled_back=True,
                reason=str(exc),
                rollback_health=stack.request("GET", "/health/live", owner=None)[1],
            )
            if not isinstance(exc, InjectedSmokeFailure):
                raise
        finally:
            stack.manage("pause" if previous_pause else "resume")
        result.update(
            maintenance_seconds=round(time.monotonic() - started, 3),
            previous_health=old_version,
            preserved_jobs=len(before["jobs"]),
        )
        result["phase"] = "completed"
        evidence("rollback" if inject_failure else "release", result)
        return result

    except Exception as error:
        evidence(
            "release-failed",
            {**result, "error_category": type(error).__name__, "reason": str(error)},
        )
        raise


def rollback(stack):
    state = read_json(stack.state_path)
    previous = state.get("previous_image")
    if not previous:
        raise RuntimeError("Nenhuma imagem anterior registrada")
    previous_pause = stack.snapshot()["admission_paused"]
    current = stack.inspect("api")["Image"]
    previous = image_id(previous)
    release_version(previous)
    started = time.monotonic()
    before = None
    try:
        before = drain(stack)
        stack.image = previous
        stack.compose("up", "-d", "--no-deps", "--wait", "api", "worker")
        stack.ready()
        active_images = running_images(stack, previous)
        if stack.snapshot()["jobs"] != before["jobs"]:
            raise RuntimeError("Dados divergiram no rollback")
        stack.manage("resume")
        text = "Retorno manual preserva dados"
        job = stack.completed(stack.job(text)["id"])
        if job["result"] != {
            "word_count": 4,
            "checksum": hashlib.sha256(text.encode()).hexdigest(),
        }:
            raise RuntimeError("Smoke funcional do retorno manual falhou")
        drain(stack)
        stack.save(previous_image=current)
        evidence(
            "manual-rollback",
            {
                "project": stack.project,
                "previous_image": current,
                "active_image": previous,
                "active_images": active_images,
                "preserved_jobs": len(before["jobs"]),
                "job": job,
                "maintenance_seconds": round(time.monotonic() - started, 3),
            },
        )
    except Exception:
        if before is not None:
            stack.image = current
            stack.compose("up", "-d", "--no-deps", "--wait", "api", "worker")
            stack.ready()
            running_images(stack, current)
            stack.save()
        raise
    finally:
        stack.manage("pause" if previous_pause else "resume")


def tls_setup(stack):
    directory = stack.directory / "tls"
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
    openssl = shutil.which("openssl")
    if not openssl and os.name == "nt":
        candidate = Path("C:/Program Files/Git/usr/bin/openssl.exe")
        if candidate.exists():
            openssl = str(candidate)
    if not openssl:
        raise RuntimeError("OpenSSL não encontrado; necessário para gerar os certificados locais")
    if not (directory / "server.crt").exists():
        run(
            [
                openssl,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                directory / "ca.key",
                "-out",
                directory / "ca.crt",
                "-days",
                "30",
                "-subj",
                "/CN=ContainerOps Demo CA",
            ],
            capture=True,
        )
        run(
            [
                openssl,
                "req",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                directory / "server.key",
                "-out",
                directory / "server.csr",
                "-subj",
                "/CN=localhost",
            ],
            capture=True,
        )
        (directory / "extensions.cnf").write_text(
            "subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n",
            encoding="ascii",
        )
        run(
            [
                openssl,
                "x509",
                "-req",
                "-in",
                directory / "server.csr",
                "-CA",
                directory / "ca.crt",
                "-CAkey",
                directory / "ca.key",
                "-CAcreateserial",
                "-out",
                directory / "server.crt",
                "-days",
                "30",
                "-extfile",
                directory / "extensions.cnf",
            ],
            capture=True,
        )
    if os.name != "nt":
        (directory / "server.key").chmod(0o644)  # Demo bind mount readable by container UID101.
        (directory / "ca.key").chmod(0o600)
    stack.tls = True
    stack.compose("config", "--quiet")
    stack.compose("up", "-d", "--no-deps", "--wait", "proxy")
    status, body = stack.request("GET", "/health/ready", owner=None, tls=True)
    if status != 200:
        raise RuntimeError("TLS com CA explícita falhou")
    status, created = stack.request(
        "POST", "/v1/jobs", {"text": "TLS validado com CA local"}, key=uuid.uuid4().hex, tls=True
    )
    if status != 201:
        raise RuntimeError("Criação autorizada via TLS falhou")

    def finished():
        response_status, job = stack.request("GET", f"/v1/jobs/{created['id']}", tls=True)
        return job if response_status == 200 and job["state"] == "succeeded" else None

    completed = wait_for(finished, "jornada TLS autorizada")
    if completed["result"] != {
        "word_count": 5,
        "checksum": hashlib.sha256(b"TLS validado com CA local").hexdigest(),
    }:
        raise RuntimeError("Resultado da jornada TLS incorreto")
    try:
        with urllib.request.urlopen(f"https://localhost:{stack.tls_port}/health/live", timeout=5):
            raise RuntimeError("CA local foi aceita sem fornecimento explícito ao cliente")
    except urllib.error.URLError as exc:
        if not isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise
    stack.save()
    evidence(
        "tls",
        {
            "url": f"https://localhost:{stack.tls_port}",
            "verified_with_explicit_ca": True,
            "default_trust_rejected": True,
            "health": body,
            "job": completed,
        },
    )


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description="Operação local do ContainerOps")
    parser.set_defaults(version=None, inject_smoke_failure=False, offline=False, scenario="full")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in [
        "setup",
        "build",
        "test",
        "start",
        "status",
        "demo",
        "logs",
        "inspect",
        "verify",
        "backup",
        "backup-export",
        "backup-import",
        "backup-image-load",
        "backups",
        "restore-test",
        "release",
        "rollback",
        "stop",
        "report",
        "scan",
        "sbom",
        "cache-builds",
        "tls",
        "prove",
    ]:
        command = commands.add_parser(name)
        if name in ("build", "start", "scan", "sbom", "release"):
            command.add_argument("--version", choices=("2.0.0",) if name == "release" else RELEASES)
        if name == "release":
            command.add_argument("--inject-smoke-failure", action="store_true")
        if name == "scan":
            command.add_argument("--offline", action="store_true")
        if name == "prove":
            command.add_argument("--scenario", choices=("full", "operations"), default="full")
        if name in ("restore-test", "backup-export", "backup-image-load"):
            command.add_argument("--backup", required=name != "restore-test")
        if name == "restore-test":
            command.add_argument("--trusted-source", action="store_true")
        if name == "backup-export":
            command.add_argument("--destination", type=Path, required=True)
            command.add_argument("--include-image", action="store_true")
        if name == "backup-import":
            command.add_argument("--source", type=Path, required=True)
        if name == "backup-image-load":
            command.add_argument("--trusted-image-source", action="store_true", required=True)
        if name == "backups":
            actions = command.add_subparsers(dest="backup_action", required=True)
            actions.add_parser("list")
            protect = actions.add_parser("protect")
            protect.add_argument("--backup", required=True)
            protect.add_argument("--unprotect", action="store_true")
            prune = actions.add_parser("prune")
            prune.add_argument("--keep-last", type=int, required=True)
            prune.add_argument("--apply", action="store_true")
            prune.add_argument("--plan-id")
            prune.add_argument("--recover", action="store_true")
    return parser.parse_args(argv)


def main():
    args = parse_arguments()
    readonly = args.command in ("status", "logs", "report") or (
        args.command == "backups"
        and (
            args.backup_action == "list"
            or args.backup_action == "prune"
            and not args.apply
            and not args.recover
        )
    )
    print(f"Contexto ativo: {CONTEXT.session or 'default'}; projeto: {MAIN}", flush=True)
    with nullcontext() if readonly else operation_lock():
        execute(args)


def execute(args):
    if args.command in ("backup-export", "backup-import", "backup-image-load", "backups"):
        execute_backups(args)
        return
    stack = (
        Stack()
        if args.command
        in (
            "start",
            "stop",
            "status",
            "logs",
            "demo",
            "backup",
            "release",
            "rollback",
            "tls",
            "inspect",
        )
        else None
    )
    version = args.version or "1.0.0"
    if args.command == "setup":
        setup()
    elif args.command == "build":
        build(version)
    elif args.command == "start":
        if args.version:
            stack.image = image_id(CONTEXT.image("app", args.version))
        stack.start()
    elif args.command == "stop":
        stack.stop()
    elif args.command == "status":
        stack.compose("ps")
    elif args.command == "logs":
        stack.compose("logs", "--tail", "80")
    elif args.command == "demo":
        result = stack.completed(stack.job()["id"])
        evidence("demo", {"job": result, "url": f"http://localhost:{stack.port}"})
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif args.command == "backup":
        backup(stack)
    elif args.command == "restore-test":
        restore_test(backup_id=args.backup, trusted_source=args.trusted_source)
    elif args.command == "release":
        release(stack, args.version or "2.0.0", args.inject_smoke_failure)
    elif args.command == "rollback":
        rollback(stack)
    elif args.command == "tls":
        tls_setup(stack)
    elif args.command in ("sbom", "scan", "cache-builds"):
        import supply

        if args.command == "sbom":
            supply.audit(ROOT, RUNTIME, version)
        elif args.command == "scan":
            supply.scan(ROOT, RUNTIME, version, offline=args.offline)
        else:
            supply.cache_experiment(ROOT, RUNTIME)
    elif args.command in ("test", "verify", "inspect"):
        import checks

        if args.command == "inspect":
            checks.hardening(stack)
        else:
            checks.verify(full=args.command == "verify")
    elif args.command == "report":
        import report

        report.generate(ROOT, RUNTIME)
    elif args.command == "prove":
        import proof

        if args.scenario == "operations":
            proof.prove_operations()
        else:
            proof.prove()


def execute_backups(args):
    import backup_catalog
    import backup_package

    context = CONTEXT.session or "default"
    if args.command == "backup-export":
        directory = backup_catalog.select_backup(RUNTIME, args.backup, context=context)
        checked = backup_package.validate_backup(directory)
        metadata = checked["metadata"]
        manifest = checked["manifest"]
        result = backup_package.export_backup(
            directory,
            args.destination,
            source_ref=metadata.get("source_ref"),
            source_context=metadata.get(
                "source_context", manifest["backup"]["origin"]["context"] if manifest else "default"
            ),
            image_release=metadata.get(
                "image_release", manifest["application_image"]["release"] if manifest else None
            ),
            include_image=args.include_image,
            run=run,
            config_digest=metadata.get("config_digest"),
            oci_manifest_digest=metadata.get("oci_manifest_digest"),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif args.command == "backup-import":
        backup_id = "import-" + uuid.uuid4().hex
        ensure_no_links(RUNTIME / "backups")
        (RUNTIME / "backups").mkdir(parents=True, exist_ok=True)
        directory, _record = backup_package.import_backup(
            args.source, RUNTIME / "backups", backup_id
        )
        backup_catalog.register_backup(
            RUNTIME, backup_id, imported=True, protected=True, context=context
        )
        print(f"Importado: {directory.name}. Integridade conferida; nenhuma restauração executada.")
        evidence("backup-import", {"backup_id": backup_id, "integrity_verified": True})
        catalog_evidence()
    elif args.command == "backup-image-load":
        if not CONTEXT.session:
            raise RuntimeError("Carregamento exige CONTAINEROPS_SESSION para tag exclusiva")
        directory = backup_catalog.select_backup(RUNTIME, args.backup, context=context)
        checked = backup_package.validate_backup(directory, portable=True)
        image_release = checked["manifest"]["application_image"]["release"]
        result = backup_package.load_application_image(
            directory,
            run=run,
            tag=CONTEXT.image("app", image_release),
            trusted=args.trusted_image_source,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif args.backup_action == "list":
        print(
            json.dumps(
                backup_catalog.discover(RUNTIME, context=context), ensure_ascii=False, indent=2
            )
        )
    elif args.backup_action == "protect":
        backup_catalog.mark_protected(
            RUNTIME, args.backup, protected=not args.unprotect, context=context
        )
        catalog_evidence()
    else:
        if args.keep_last < 0:
            raise ValueError("--keep-last deve ser >= 0")
        if args.recover:
            if not args.apply or not args.plan_id:
                raise ValueError("Recuperação exige --apply e --plan-id explícitos")
            result = backup_catalog.recover_retention(RUNTIME, args.plan_id, context=context)
        elif args.apply:
            if not args.plan_id:
                raise ValueError("Aplicação exige --plan-id da prévia")
            result = backup_catalog.apply_retention(
                RUNTIME, args.keep_last, args.plan_id, context=context
            )
        else:
            if args.plan_id:
                raise ValueError("--plan-id exige --apply")
            result = backup_catalog.plan_retention(RUNTIME, args.keep_last, context=context)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.apply:
            catalog_evidence()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"erro: {error}", file=sys.stderr)
        sys.exit(1)
