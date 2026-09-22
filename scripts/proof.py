"""Ensaio operacional completo em projetos descartáveis; nunca altera a stack principal."""

import hashlib
import json
import os
import time
import uuid

import checks
import ops
import scan_services
import supply


def source_manifest():
    paths = [
        ops.ROOT / name
        for name in ("compose.yaml", "compose.tls.yaml", ".dockerignore", "pyproject.toml")
    ]
    for folder in ("app", "docker", "scripts", "tests"):
        paths.extend(
            path
            for path in (ops.ROOT / folder).rglob("*")
            if path.is_file()
            and path.suffix in (".py", ".ps1", ".sql", ".sh", ".lock", ".toml", ".conf", ".json")
            and not {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"} & set(path.parts)
        )
    paths.extend((ops.ROOT / "docker").rglob("*Dockerfile"))
    files = {
        path.relative_to(ops.ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(set(paths))
    }
    return {
        "files": files,
        "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        "revision": supply.revision(ops.ROOT),
    }


def image_sources(image):
    expected = {
        path.relative_to(ops.ROOT / "app" / "src").as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted((ops.ROOT / "app" / "src").rglob("*"))
        if path.is_file() and path.suffix in (".py", ".sql")
    }
    script = (
        "import hashlib,json; from pathlib import Path; root=Path('/app/src'); "
        "print(json.dumps({p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() "
        "for p in sorted(root.rglob('*')) if p.is_file() and p.suffix in ('.py','.sql')}))"
    )
    actual = json.loads(
        ops.run(
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
                "0.5",
                "--pids-limit",
                "32",
                "--label",
                "com.containerops.purpose=source-proof",
                image,
                "python",
                "-c",
                script,
            ],
            capture=True,
        ).stdout
    )
    checks.require(actual == expected, "Fontes da imagem diferem do código atual")
    return {"image_id": image, "files": actual, "equal": True}


def inventory():
    result = ops.run(["docker", "ps", "--format", "{{json .}}"], capture=True).stdout
    return [json.loads(line) for line in result.splitlines() if line.strip()]


def main_runtime_state():
    return {
        name: hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        for name in ("state.json", "latest-backup.json")
        for path in (ops.RUNTIME / name,)
    }


class ProofRun:
    def __init__(self):
        self.started = time.monotonic()
        self.run_id = uuid.uuid4().hex
        self.directory = ops.EVIDENCE / "problem-proof" / self.run_id
        self.directory.mkdir(parents=True)
        self.data = {
            "run_id": self.run_id,
            "started_at": ops.now(),
            "completed_at": None,
            "status": "in_progress",
            "project": "pf-containerops-test-" + self.run_id[:8],
            "steps": [],
            "evidence": [],
            "source": None,
            "scope": "projetos descartáveis; nenhuma operação na stack principal",
        }
        self.save()

    def save(self):
        self.data["elapsed_seconds"] = round(time.monotonic() - self.started, 3)
        ops.write_json(self.directory / "manifest.json", self.data)
        ops.write_json(ops.EVIDENCE / "problem-proof-latest.json", self.data)

    def write(self, name, data):
        path = self.directory / f"{name}.json"
        checks.require(not path.exists(), "Evidência da tentativa já existe")
        ops.write_json(path, data)
        self.data["evidence"].append(
            {
                "path": path.relative_to(ops.EVIDENCE).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
        self.save()

    def archive(self, name):
        self.write(name, ops.read_json(ops.EVIDENCE / f"{name}.json"))

    def step(self, name, callback):
        step = {"name": name, "started_at": ops.now(), "status": "in_progress"}
        self.data["steps"].append(step)
        self.save()
        started = time.monotonic()
        try:
            result = callback()
            step["status"] = "passed"
            return result
        except BaseException as error:
            step.update(status="failed", error_category=type(error).__name__)
            raise
        finally:
            step.update(
                completed_at=ops.now(), elapsed_seconds=round(time.monotonic() - started, 3)
            )
            self.save()


def require_jobs(before, after):
    actual = {job["id"]: job for job in after["jobs"]}
    checks.require(
        all(actual.get(job["id"]) == job for job in before["jobs"]),
        "Trabalho persistido mudou ou desapareceu durante a operação",
    )


def require_completed_count(snapshot, expected):
    checks.require(
        snapshot["counts"] == {"queued": 0, "running": 0, "succeeded": expected, "failed": 0}
        and len(snapshot["jobs"]) == expected,
        "Quantidade de jobs difere do oráculo da jornada isolada",
    )


def prove():
    attempt = ProofRun()
    stack = None
    cleanup_required = False
    # unittest/tempfile também ficam no runtime reservado no Windows.
    temporary = ops.RUNTIME / "test-temp"
    temporary.mkdir(parents=True, exist_ok=True)
    os.environ.update(TMP=str(temporary), TEMP=str(temporary), TMPDIR=str(temporary))
    try:
        attempt.data["source"] = source_manifest()
        attempt.save()
        runtime_before = main_runtime_state()
        attempt.write("main-runtime-before", runtime_before)
        attempt.write(
            "concurrent-containers", {"recorded_at": ops.now(), "containers": inventory()}
        )
        attempt.write(
            "environment",
            {
                "docker": ops.run(
                    ["docker", "version", "--format", "{{json .}}"], capture=True
                ).stdout,
                "compose": ops.run(["docker", "compose", "version"], capture=True).stdout.strip(),
                "platform": "linux/amd64",
                "host_python": os.sys.version,
            },
        )
        for version in ops.RELEASES:
            attempt.step("build-" + version, lambda v=version: ops.build(v))
            attempt.archive("build-" + version)
            image = ops.image_id("containerops-app:" + version)
            attempt.write(
                "sources-" + version,
                attempt.step("sources-" + version, lambda i=image: image_sources(i)),
            )
            attempt.step(
                "audit-" + version, lambda v=version: supply.audit(ops.ROOT, ops.RUNTIME, v)
            )
            attempt.archive("audit-" + version)
            attempt.step(
                "scan-" + version,
                lambda v=version: supply.scan(ops.ROOT, ops.RUNTIME, v, offline=True),
            )
            attempt.archive("scan-" + version)
        attempt.step("scan-services", lambda: scan_services.scan_services(offline=True))
        attempt.archive("scan-services")
        attempt.step("verify", checks.verify)
        attempt.archive("verification-run")
        verified = ops.read_json(ops.EVIDENCE / "verification-run.json")
        for reference in verified["evidence"]:
            path = ops.EVIDENCE / reference["path"]
            checks.require(
                "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() == reference["sha256"],
                "Artefato de verify mudou antes de arquivar",
            )
            attempt.write("verify-" + path.stem, ops.read_json(path))

        stack = ops.Stack(attempt.data["project"], image=ops.image_id("containerops-app:1.0.0"))
        stack.assert_fresh()
        cleanup_required = True
        attempt.step("start-isolated", stack.start)
        attempt.write("images-before", ops.running_images(stack, stack.image))
        attempt.step("journey", lambda: checks.journey(stack))
        attempt.archive("journey")
        before = stack.snapshot()
        # Jornada: texto concorrente + corpo no limite + replay de zero com sinal.
        require_completed_count(before, 3)
        attempt.write("snapshot-before", before)
        failed_release = attempt.step(
            "release-controlled-failure", lambda: ops.release(stack, "2.0.0", inject_failure=True)
        )
        attempt.archive("rollback")
        checks.require(failed_release["rolled_back"], "Retorno automático não ocorreu")
        checks.require("preserved_candidate_job" in failed_release, "Job candidato não conferido")
        after_failure = stack.snapshot()
        require_completed_count(after_failure, 5)  # candidata e smoke do retorno
        checks.require(after_failure["schema_version"] == 2, "Rollback rebaixou o schema")
        require_jobs(before, after_failure)
        attempt.write("snapshot-after-controlled-failure", after_failure)
        attempt.step("release-success", lambda: ops.release(stack, "2.0.0"))
        attempt.archive("release")
        promoted = stack.snapshot()
        require_completed_count(promoted, 6)
        require_jobs(after_failure, promoted)
        attempt.write("snapshot-promoted", promoted)
        attempt.step("manual-rollback", lambda: ops.rollback(stack))
        attempt.archive("manual-rollback")
        rolled_back = stack.snapshot()
        require_completed_count(rolled_back, 7)
        require_jobs(promoted, rolled_back)
        checks.require(rolled_back["schema_version"] == 2, "Retorno manual rebaixou schema")
        attempt.write("snapshot-rolled-back", rolled_back)
        attempt.step("tls", lambda: ops.tls_setup(stack))
        attempt.archive("tls")
        require_completed_count(stack.snapshot(), 8)
        directory = attempt.step("backup", lambda: ops.backup(stack))
        attempt.archive("backup")
        restored = attempt.step("restore", lambda: ops.restore_test(directory))
        checks.require(restored["restored_jobs"] == 8, "Restore perdeu quantidade de jobs")
        attempt.archive("restore")
        runtime_after = main_runtime_state()
        checks.require(runtime_after == runtime_before, "Estado local da stack principal mudou")
        attempt.write("main-runtime-after", runtime_after)
        checks.require(source_manifest() == attempt.data["source"], "Fontes mudaram durante prova")
    except BaseException as error:
        attempt.data.update(status="failed", error_category=type(error).__name__)
        raise
    finally:
        try:
            if cleanup_required:
                attempt.step("cleanup", stack.destroy_test)
                stack.assert_fresh()
        except BaseException as error:
            attempt.data.update(status="failed", cleanup_error_category=type(error).__name__)
            raise
        finally:
            if attempt.data["status"] == "in_progress":
                attempt.data["status"] = "passed"
            attempt.data["completed_at"] = ops.now()
            attempt.save()
            print(f"Prova: {attempt.directory / 'manifest.json'}", flush=True)
    return attempt.data
