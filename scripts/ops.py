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
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME = (
    Path(os.environ["USERPROFILE"]) / "AppData" / "Local" / "ContainerOps-runtime"
    if os.name == "nt"
    else ROOT / ".runtime-containerops"
)
RUNTIME = Path(os.environ.get("CONTAINEROPS_RUNTIME", str(DEFAULT_RUNTIME))).resolve()
if os.name == "nt" and RUNTIME != DEFAULT_RUNTIME.resolve():
    raise RuntimeError(f"Runtime Windows deve ser o caminho reservado: {DEFAULT_RUNTIME}")
EVIDENCE = ROOT / "docs" / "evidence"
MAIN = "pf-containerops"
RELEASES = ("1.0.0", "2.0.0")


@contextmanager
def operation_lock(directory=RUNTIME):
    """The OS releases this lock when the process exits, including after a crash."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "operation.lock").open("a+b") as lock:
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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
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
    secret_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
        secret_dir.chmod(0o700)
    for name in ("db_admin", "db_migrator", "db_app", "db_backup", "sentinel"):
        path = secret_dir / name
        if not path.exists():
            path.write_text("containerops-demo-" + secrets.token_hex(24), encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)
    token_file = secret_dir / "api_tokens"
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


class Stack:
    def __init__(self, project=MAIN, *, image=None, port=None, tls=False):
        if not re.fullmatch(r"pf-containerops(?:-(?:test|restore)-[a-f0-9]{8})?", project):
            raise ValueError("Nome Compose fora do escopo ContainerOps")
        self.project = project
        self.directory = RUNTIME if project == MAIN else RUNTIME / "environments" / project
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_path = self.directory / "state.json"
        state = read_json(self.state_path) if self.state_path.exists() else {}
        self.image = image or state.get("image", "containerops-app:1.0.0")
        default_port = (
            int(os.environ.get("HTTP_PORT", state.get("port", 8105)))
            if project == MAIN
            else free_port()
        )
        self.port = port if port is not None else default_port
        self.tls_port = (
            int(os.environ.get("TLS_PORT", state.get("tls_port", 8445)))
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
            "PROXY_IMAGE": "containerops-proxy:local",
            "TEST_IMAGE": "containerops-test:local",
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
    setup_secrets(RUNTIME)
    run(["docker", "version"], capture=True)
    run(["docker", "compose", "version"])
    print(f"Runtime demo: {RUNTIME}\nCredenciais: {RUNTIME / 'secrets' / 'api_tokens'}")


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
                "pf-containerops-builder",
                "--load",
                "--provenance=false",
                "--tag",
                "containerops-proxy:local",
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
    started = time.monotonic()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    destination = RUNTIME / "backups" / stamp
    destination.mkdir(parents=True)
    helper = stack.project + "-backup-" + uuid.uuid4().hex[:8]
    previous_pause = stack.snapshot()["admission_paused"]
    try:
        snapshot = drain(stack)
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
        metadata = {
            "created_at": now(),
            "sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
            "server": server,
            "schema_version": snapshot["schema_version"],
            "snapshot": snapshot,
            "source_project": stack.project,
            "image": stack.inspect("api")["Image"],
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        write_json(destination / "metadata.json", metadata)
        evidence("backup", {**metadata, "directory": str(destination)})
        pointer_directory = RUNTIME if stack.project == MAIN else stack.directory
        write_json(pointer_directory / "latest-backup.json", {"directory": str(destination)})
        print(f"Backup: {destination}")
        return destination
    finally:
        # Remove only the uniquely named helper started above, retaining artifacts volume.
        run(["docker", "rm", "-f", helper], capture=True, check=False)
        if not previous_pause:
            stack.manage("resume")


def restore_test(directory=None):
    directory = Path(directory or read_json(RUNTIME / "latest-backup.json")["directory"]).resolve()
    if not directory.is_relative_to(RUNTIME / "backups"):
        raise RuntimeError("Origem de backup fora do runtime reservado")
    metadata = read_json(directory / "metadata.json")
    dump = directory / "containerops.dump"
    if hashlib.sha256(dump.read_bytes()).hexdigest() != metadata["sha256"]:
        raise RuntimeError("Checksum de backup inválido")
    stack = Stack("pf-containerops-restore-" + uuid.uuid4().hex[:8], image=metadata["image"])
    stack.assert_fresh()
    setup_secrets(stack.directory)
    started = time.monotonic()
    try:
        stack.compose("up", "-d", "--wait", "--wait-timeout", "100", "db")
        stack.compose("create", "restore")
        helper = stack.cid("restore")
        staged = stack.directory / "restore.dump"
        shutil.copyfile(dump, staged)
        staged.chmod(0o644)  # docker cp otherwise preserves pg_dump's 0600 for root, not UID999.
        try:
            run(["docker", "cp", staged, f"{helper}:/var/lib/postgresql/data/containerops.dump"])
        finally:
            staged.unlink()
        run(["docker", "start", "-a", helper], timeout=120)
        restored = json.loads(run(["docker", "inspect", helper], capture=True).stdout)[0]
        if restored["State"]["ExitCode"] != 0:
            raise RuntimeError("pg_restore falhou")
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
            str(metadata["schema_version"]),
        )
        snapshot = stack.snapshot()
        if snapshot != metadata["snapshot"]:
            raise RuntimeError("Snapshot restaurado difere do backup")
        stack.manage("resume")
        stack.compose("up", "-d", "--wait", "--wait-timeout", "120", "api", "worker", "proxy")
        stack.ready()
        job = stack.completed(stack.job("Backup restaurado com sucesso")["id"])
        if job["result"] != {
            "word_count": 4,
            "checksum": hashlib.sha256(b"Backup restaurado com sucesso").hexdigest(),
        }:
            raise RuntimeError("Novo job pós-restore incorreto")
        result = {
            "project": stack.project,
            "backup": str(directory),
            "checksum_verified": True,
            "snapshot_equal": True,
            "restored_jobs": len(snapshot["jobs"]),
            "new_job": job,
            "recovery_seconds": round(time.monotonic() - started, 3),
        }
        evidence("restore", result)
        return result
    finally:
        stack.destroy_test()


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


def release(stack, version, inject_failure=False):
    if version != "2.0.0":
        raise ValueError("Use --version 2.0.0 para atualizar ou rollback para voltar")
    result = {
        "project": stack.project,
        "requested_version": version,
        "injected_smoke_failure": inject_failure,
        "phase": "preflight",
    }
    try:
        candidate = image_id("containerops-app:" + version)
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
    parser.set_defaults(version=None, inject_smoke_failure=False, offline=False)
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
    return parser.parse_args(argv)


def main():
    args = parse_arguments()
    readonly = args.command in ("status", "logs", "report")
    with nullcontext() if readonly else operation_lock():
        execute(args)


def execute(args):
    stack = Stack()
    version = args.version or "1.0.0"
    if args.command == "setup":
        setup()
    elif args.command == "build":
        build(version)
    elif args.command == "start":
        if args.version:
            stack.image = image_id("containerops-app:" + args.version)
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
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "backup":
        backup(stack)
    elif args.command == "restore-test":
        restore_test()
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

        proof.prove()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"erro: {error}", file=sys.stderr)
        sys.exit(1)
