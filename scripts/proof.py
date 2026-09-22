"""Ensaio operacional completo em projetos descartáveis; nunca altera a stack principal."""

import hashlib
import json
import os
import shutil
import stat
import time
import uuid
from pathlib import Path

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
            and path.suffix
            in (
                ".py",
                ".ps1",
                ".sql",
                ".sh",
                ".lock",
                ".toml",
                ".conf",
                ".json",
                ".html",
                ".css",
                ".js",
                ".cjs",
            )
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
    def __init__(self, scenario="full"):
        if scenario not in ("full", "operations"):
            raise ValueError("Cenário de prova desconhecido")
        self.started = time.monotonic()
        self.run_id = uuid.uuid4().hex
        self.directory = ops.EVIDENCE / "problem-proof" / self.run_id
        self.directory.mkdir(parents=True)
        self.data = {
            "scenario": scenario,
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
        alias = (
            "operations-proof-latest.json"
            if self.data["scenario"] == "operations"
            else "problem-proof-latest.json"
        )
        ops.write_json(ops.EVIDENCE / alias, self.data)

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


def public_record(data):
    """A public proof references private dumps; it never publishes their bytes or host path."""
    if isinstance(data, dict):
        return {key: public_record(value) for key, value in data.items()}
    if isinstance(data, list):
        return [public_record(value) for value in data]
    if isinstance(data, str):
        return data.replace(str(ops.RUNTIME), "<runtime>")
    return data


def protect_copy_directory(directory):
    """Limit a newly created private copy to its operator (and SYSTEM on Windows)."""
    if os.name != "nt":
        directory.chmod(0o700)
        checks.require(stat.S_IMODE(directory.stat().st_mode) == 0o700, "Cópia não protegida")
        files = list(directory.iterdir())
        checks.require(
            all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in files),
            "Arquivos da cópia não protegidos",
        )
        return {
            "method": "POSIX",
            "directory_mode": "0700",
            "files_mode": "0600",
            "verified_files": len(files),
        }
    script = r"""
$ErrorActionPreference = 'Stop'
$path = $env:CONTAINEROPS_COPY_PATH
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$system = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$acl = [System.Security.AccessControl.DirectorySecurity]::new()
$acl.SetOwner($user)
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in @($user, $system)) {
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
[System.IO.Directory]::SetAccessControl($path, $acl)
$actual = [System.IO.Directory]::GetAccessControl($path)
$rules = @($actual.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
if (!$actual.AreAccessRulesProtected -or $rules.Count -ne 2) { throw 'Invalid copy ACL' }
foreach ($rule in $rules) {
    if ($rule.IdentityReference.Value -notin @($user.Value, $system.Value) -or
        $rule.AccessControlType -ne 'Allow' -or
        $rule.FileSystemRights -ne 'FullControl') { throw 'Unexpected copy ACL principal' }
}
$files = [System.IO.Directory]::GetFiles($path)
foreach ($file in $files) {
    $fileAcl = [System.IO.File]::GetAccessControl($file)
    $fileRules = @($fileAcl.GetAccessRules($true, $true,
        [System.Security.Principal.SecurityIdentifier]))
    if ($fileRules.Count -ne 2) { throw 'Invalid file ACL' }
    foreach ($rule in $fileRules) {
        if ($rule.IdentityReference.Value -notin @($user.Value, $system.Value) -or
            $rule.AccessControlType -ne 'Allow' -or
            $rule.FileSystemRights -ne 'FullControl') { throw 'Unexpected file ACL principal' }
    }
}
@{method='Windows DACL'; inheritance_disabled=$true; principals='current user and SYSTEM';
  verified_files=$files.Count} |
    ConvertTo-Json -Compress
"""
    response = ops.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "CONTAINEROPS_COPY_PATH": str(directory)},
        capture=True,
        timeout=30,
    )
    return json.loads(response.stdout)


def protected_backup_copy(directory):
    directory = Path(directory).resolve()
    root = (ops.RUNTIME / "backups").resolve()
    checks.require(directory.is_relative_to(root), "Backup fora do runtime reservado")
    for name in ("metadata.json", "containerops.dump"):
        path = directory / name
        checks.require(
            path.is_file() and not path.is_symlink(), "Backup precisa de arquivos regulares"
        )
    metadata = ops.read_json(directory / "metadata.json")
    checks.require(
        hashlib.sha256((directory / "containerops.dump").read_bytes()).hexdigest()
        == metadata["sha256"],
        "Checksum da origem do backup inválido",
    )
    destination = root / ("proof-copy-" + uuid.uuid4().hex)
    destination.mkdir(mode=0o700)
    protection = protect_copy_directory(destination)
    hashes = {}
    for name in ("metadata.json", "containerops.dump"):
        source, target = directory / name, destination / name
        with source.open("rb") as original, target.open("xb") as copied:
            shutil.copyfileobj(original, copied)
        if os.name != "nt":
            target.chmod(0o600)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        after = hashlib.sha256(target.read_bytes()).hexdigest()
        checks.require(before == after, "Cópia privada diverge do backup")
        hashes[name] = after
    protection = protect_copy_directory(destination)
    return destination, {
        "recorded_at": ops.now(),
        "original": str(directory),
        "copy": str(destination),
        "sha256": hashes,
        "protection": protection,
        "same_host": True,
        "limit": "Independent files, not an independent host/disaster recovery or encrypted backup",
    }


def reject_corrupted_copy(directory):
    corrupted, copied = protected_backup_copy(directory)
    dump = corrupted / "containerops.dump"
    with dump.open("r+b") as stream:
        byte = stream.read(1)
        checks.require(bool(byte), "Dump vazio não é controle de corrupção")
        stream.seek(0)
        stream.write(bytes([byte[0] ^ 0x01]))
    try:
        ops.restore_test(corrupted)
    except RuntimeError as error:
        checks.require(str(error) == "Checksum de backup inválido", "Recusa não foi por checksum")
        rejected = ops.read_json(ops.EVIDENCE / "restore.json")
        checks.require(rejected["status"] == "failed", "Corrupção foi registrada como aprovação")
        checks.require("project" not in rejected, "Restore começou antes da validação do hash")
        return {"copy": copied, "rejection": rejected, "rejected_before_target_created": True}
    raise RuntimeError("Cópia adulterada não foi recusada")


def source_state(stack):
    return {
        "snapshot": stack.snapshot(),
        "services": {
            service: {"id": item["Id"], "image": item["Image"]}
            for service in ("db", "api", "worker", "proxy")
            for item in (stack.inspect(service),)
        },
    }


def prove_operations():
    """Short CO-03/04 proof. Reuse only existing images with exact app-source matches."""
    started = time.monotonic()
    attempt = ProofRun(scenario="operations")
    attempt.data.update(
        scenario="operations",
        scope="CO-03/04: real rollback, protected same-host backup copy and disposable restore",
        limits=[
            "Synthetic local data and one Docker host; no off-host disaster recovery.",
            "Existing images with source verification; no new build or vulnerability scan.",
            "Cutoff is the bounded UTC interval in which admission paused and jobs drained.",
            "Recovery time and total including preflight/cleanup have separate fields.",
        ],
    )
    attempt.save()
    stack = None
    cleanup_required = False
    try:
        attempt.data["source"] = source_manifest()
        attempt.write(
            "environment",
            {
                "docker": json.loads(
                    ops.run(
                        [
                            "docker",
                            "info",
                            "--format",
                            '{"server_version":"{{.ServerVersion}}","cpus":{{.NCPU}},'
                            '"memory_bytes":{{.MemTotal}},"os":"{{.OSType}}","arch":"{{.Architecture}}"}',
                        ],
                        capture=True,
                    ).stdout
                ),
                "host_python": os.sys.version,
                "concurrent_containers": inventory(),
            },
        )
        runtime_before = main_runtime_state()
        attempt.write("main-runtime-before", runtime_before)
        images = {}
        for version in ops.RELEASES:
            image = ops.image_id("containerops-app:" + version)
            checks.require(ops.release_version(image) == version, "Release da imagem incompatível")
            images[version] = image
            attempt.write(
                "sources-" + version,
                attempt.step("source-check-" + version, lambda i=image: image_sources(i)),
            )
        attempt.data["images"] = images
        attempt.save()
        stack = ops.Stack(attempt.data["project"], image=images["1.0.0"])
        stack.assert_fresh()
        cleanup_required = True
        attempt.step("start-isolated", stack.start)
        job = attempt.step(
            "initial-job",
            lambda: stack.completed(stack.job("Trabalho identificado e preservado")["id"]),
        )
        checks.require(
            job["result"]
            == {
                "word_count": 4,
                "checksum": hashlib.sha256(b"Trabalho identificado e preservado").hexdigest(),
            },
            "Job inicial incorreto",
        )
        attempt.write("demo", {"recorded_at": ops.now(), "project": stack.project, "job": job})
        before = stack.snapshot()
        require_completed_count(before, 1)
        attempt.write("snapshot-before-rollback", before)
        rollback = attempt.step(
            "real-controlled-rollback",
            lambda: ops.release(
                stack, "2.0.0", inject_failure=True, candidate_image=images["2.0.0"]
            ),
        )
        checks.require(rollback["rolled_back"], "Não ocorreu rollback real")
        checks.require(rollback["candidate_image"] == images["2.0.0"], "Candidata divergente")
        checks.require(rollback["previous_image"] == images["1.0.0"], "Imagem anterior divergente")
        checks.require("preserved_candidate_job" in rollback, "Job da candidata não conferido")
        attempt.write("rollback", public_record(ops.read_json(ops.EVIDENCE / "rollback.json")))
        after = stack.snapshot()
        require_completed_count(after, 3)
        require_jobs(before, after)
        checks.require(after["schema_version"] == 2, "Rollback rebaixou schema")
        attempt.write("snapshot-after-rollback", after)
        source_before = source_state(stack)
        directory = attempt.step("backup", lambda: ops.backup(stack))
        attempt.write("backup", public_record(ops.read_json(ops.EVIDENCE / "backup.json")))
        copied, copy_record = attempt.step(
            "protected-copy", lambda: protected_backup_copy(directory)
        )
        attempt.write("protected-copy", public_record(copy_record))
        rejected = attempt.step("corruption-control", lambda: reject_corrupted_copy(copied))
        attempt.write("corrupted-copy-rejected", public_record(rejected))
        restored = attempt.step("restore-and-cleanup", lambda: ops.restore_test(copied))
        checks.require(restored["status"] == "passed", "Restore não concluiu a limpeza")
        checks.require(restored["restored_jobs"] == 3, "Restore perdeu jobs")
        attempt.write("restore", public_record(ops.read_json(ops.EVIDENCE / "restore.json")))
        source_after = source_state(stack)
        checks.require(source_after == source_before, "Origem mudou durante backup/restore")
        original_hashes_after = {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in copy_record["sha256"]
        }
        checks.require(original_hashes_after == copy_record["sha256"], "Backup original mudou")
        attempt.write(
            "source-preservation",
            {
                "before": source_before,
                "after": source_after,
                "equal": True,
                "original_backup_sha256_after": original_hashes_after,
                "original_backup_unchanged": True,
            },
        )
        runtime_after = main_runtime_state()
        checks.require(runtime_after == runtime_before, "Estado do runtime principal mudou")
        attempt.write("main-runtime-after", runtime_after)
        source_after = source_manifest()
        attempt.data["source_after"] = source_after
        attempt.data["source_unchanged"] = source_after["files"] == attempt.data["source"]["files"]
        attempt.data["git_revision_unchanged"] = (
            source_after["revision"] == attempt.data["source"]["revision"]
        )
        checks.require(attempt.data["source_unchanged"], "Fontes mudaram durante prova")
    except BaseException as error:
        attempt.data.update(status="failed", error_category=type(error).__name__)
        raise
    finally:
        try:
            if cleanup_required:
                attempt.step("cleanup-source", stack.destroy_test)
                stack.assert_fresh()
        except BaseException as error:
            attempt.data.update(status="failed", cleanup_error_category=type(error).__name__)
            raise
        finally:
            if attempt.data["status"] == "in_progress":
                attempt.data["status"] = "passed"
            attempt.data["completed_at"] = ops.now()
            attempt.data["total_seconds"] = round(time.monotonic() - started, 3)
            attempt.save()
            print(f"Prova: {attempt.directory / 'manifest.json'}", flush=True)
    return attempt.data


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
