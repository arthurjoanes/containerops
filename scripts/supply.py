from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

try:
    from .oci_audit import (
        convert_to_docker,
        file_digest,
        inspect_docker_archive,
        inspect_oci,
        locked_packages,
    )
except ImportError:
    from oci_audit import (
        convert_to_docker,
        file_digest,
        inspect_docker_archive,
        inspect_oci,
        locked_packages,
    )


BUILDER = "pf-containerops-builder"
ROOT = Path(__file__).resolve().parents[1]
TRIVY_VERSION = "0.74.0"


def blocking_vulnerabilities(findings: list[dict]) -> list[dict]:
    return [item for item in findings if item.get("Severity") in {"HIGH", "CRITICAL"}]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def paths(root: Path, runtime: Path, version: str = "1.0.0") -> tuple[Path, Path, Path]:
    root, runtime = Path(root).resolve(), Path(runtime).resolve()
    if root != ROOT:
        raise RuntimeError("Execute a partir da raiz do ContainerOps")
    if os.name == "nt":
        allowed = (Path(os.environ["USERPROFILE"]) / "AppData/Local/ContainerOps-runtime").resolve()
        if runtime != allowed:
            raise RuntimeError(f"Runtime Windows deve ser {allowed}")
    elif "containerops" not in runtime.name.lower():
        raise RuntimeError("Runtime CI precisa de nome exclusivo com containerops")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?", version):
        raise RuntimeError("Versão inválida; use major.minor.patch")
    artifact = runtime / "artifacts" / version
    artifact.mkdir(parents=True, exist_ok=True)
    (root / "docs" / "evidence").mkdir(parents=True, exist_ok=True)
    return root, runtime, artifact


def run(
    args: list[str], *, cwd: Path = ROOT, log: Path | None = None, timeout: int = 1800
) -> subprocess.CompletedProcess:
    started = time.monotonic()
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    output = result.stdout + result.stderr
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(output, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(
            f"Comando {args[:3]} falhou ({result.returncode}); log: {log}\n{output[-6000:]}"
        )
    if log is not None:
        print(f"{log.name}: {time.monotonic() - started:.1f}s", flush=True)
    return result


def evidence(root: Path, name: str, data: dict) -> dict:
    (root / "docs" / "evidence" / f"{name}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return data


def image_lock(root: Path) -> dict[str, str]:
    values = json.loads((root / "docker" / "images.lock.json").read_text(encoding="utf-8-sig"))
    for key in ("python", "trivy", "buildkit", "sbom_scanner"):
        if not re.search(r"@sha256:[a-f0-9]{64}$", values.get(key, "")):
            raise RuntimeError(f"Imagem {key} sem digest fixado")
    return values


def sentinel_file(runtime: Path) -> Path:
    path = runtime / "secrets" / "sentinel"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("CONTAINEROPS_SYNTHETIC_" + secrets.token_hex(32), encoding="ascii")
        if os.name != "nt":
            path.chmod(0o600)
    if len(path.read_bytes()) < 24:
        raise RuntimeError("Sentinela inválida")
    return path


def ensure_builder(root: Path, runtime: Path) -> dict:
    lock = image_lock(root)
    found = subprocess.run(
        ["docker", "buildx", "inspect", BUILDER],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if found.returncode:
        run(
            [
                "docker",
                "buildx",
                "create",
                "--name",
                BUILDER,
                "--driver",
                "docker-container",
                "--driver-opt",
                "image=" + lock["buildkit"],
                "--driver-opt",
                "memory=1536m",
                "--driver-opt",
                "memory-swap=1536m",
                "--driver-opt",
                "cpu-period=100000",
                "--driver-opt",
                "cpu-quota=150000",
            ],
            log=runtime / "logs" / "builder-create.log",
        )
    info = run(
        ["docker", "buildx", "inspect", BUILDER, "--bootstrap"],
        log=runtime / "logs" / "builder-inspect.log",
    ).stdout
    if not re.search(r"Driver:\s+docker-container", info):
        raise RuntimeError("Builder existente usa driver incompatível")
    driver = json.loads(run(["docker", "info", "--format", "{{json .DriverStatus}}"]).stdout)
    buildx = run(["docker", "buildx", "version"]).stdout.strip()
    # Inspect only this builder's container; do not touch the machine's default builder.
    containers = run(
        ["docker", "ps", "-aq", "--filter", "name=buildx_buildkit_" + BUILDER]
    ).stdout.split()
    instances = [
        json.loads(run(["docker", "inspect", container]).stdout)[0] for container in containers
    ]
    if not instances or any(item["Config"]["Image"] != lock["buildkit"] for item in instances):
        raise RuntimeError("Builder não usa a imagem BuildKit do lock")
    return {
        "name": BUILDER,
        "driver": "docker-container",
        "buildx": buildx,
        "docker_driver_status": driver,
        "buildkit": lock["buildkit"],
        "instances": [
            {
                "id": item["Id"],
                "image_id": item["Image"],
                "memory": item["HostConfig"]["Memory"],
                "cpu_quota": item["HostConfig"]["CpuQuota"],
            }
            for item in instances
        ],
    }


def revision(root: Path) -> str:
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if top.returncode or Path(top.stdout.strip()).resolve() != root:
        return "uncommitted"
    head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if head.returncode:
        return "uncommitted"
    dirty = run(["git", "status", "--porcelain"], cwd=root).stdout
    return head.stdout.strip() + ("-dirty" if dirty else "")


def build_args(root: Path, runtime: Path, version: str, target: str = "runtime") -> list[str]:
    lock = image_lock(root)
    return [
        "docker",
        "buildx",
        "build",
        "--builder",
        BUILDER,
        "--platform",
        "linux/amd64",
        "--progress=plain",
        "--target",
        target,
        "--file",
        str(root / "docker" / "app.Dockerfile"),
        "--build-arg",
        "PYTHON_IMAGE=" + lock["python"],
        "--build-arg",
        "APP_VERSION=" + version,
        "--build-arg",
        "VCS_REF=" + revision(ROOT),
        "--secret",
        "id=sentinel,src=" + str(sentinel_file(runtime)),
    ]


def cache_steps(log: str) -> list[dict]:
    steps: dict[str, dict] = {}
    for line in log.splitlines():
        header = re.match(r"^(#\d+) (\[[^]]+\] .+)", line)
        if header:
            steps.setdefault(header[1], {"step": header[2], "cached": False, "completed": False})
        state = re.match(r"^(#\d+) (CACHED|DONE)(?: (.+))?", line)
        if state and state[1] in steps:
            steps[state[1]]["cached"] = state[2] == "CACHED"
            steps[state[1]]["completed"] = True
            steps[state[1]]["duration"] = state[3]
    return list(steps.values())


@contextmanager
def build_context(root: Path, runtime: Path):
    "Copia os inputs para um caminho ASCII exigido pelo Buildx no Windows."
    root, runtime, _ = paths(root, runtime)
    context = Path(tempfile.mkdtemp(prefix="containerops-build-context-", dir=runtime)).resolve()
    context.relative_to(runtime)
    try:
        ignored = {"__pycache__", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
        for directory in ("app", "docker"):
            for source in (root / directory).rglob("*"):
                relative = source.relative_to(root)
                if source.is_symlink() or getattr(source.lstat(), "st_reparse_tag", 0) == getattr(
                    stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003
                ):
                    raise RuntimeError(f"Contexto não aceita links: {relative}")
                if any(part in ignored for part in relative.parts) or source.suffix == ".pyc":
                    continue
                if not source.is_file():
                    continue
                allowed = (
                    directory == "docker"
                    and source.suffix in {".json", ".sh", ".conf", ".template", ".Dockerfile"}
                ) or source.name == "Dockerfile"
                allowed |= directory == "app" and (
                    source.suffix in {".py", ".sql", ".lock", ".toml"}
                )
                if not allowed:
                    continue
                target = context / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        shutil.copy2(root / ".dockerignore", context / ".dockerignore")
        yield context
    finally:
        context.relative_to(runtime)
        if not context.name.startswith("containerops-build-context-"):
            raise RuntimeError("Contexto fora do diretório permitido para limpeza")
        shutil.rmtree(context)


def build(root: Path, runtime: Path, version: str = "1.0.0") -> dict:
    root, runtime, artifact = paths(root, runtime, version)
    builder = ensure_builder(root, runtime)
    started = time.monotonic()
    tag = "containerops-app:" + version
    lock = image_lock(root)
    with build_context(root, runtime) as context:
        command = build_args(context, runtime, version) + [
            "--tag",
            tag,
            "--attest",
            "type=provenance,mode=max",
            "--attest",
            "type=sbom,generator=" + lock["sbom_scanner"],
            "--metadata-file",
            str(artifact / "build-metadata.json"),
            "--output",
            "type=oci,compression=gzip,dest=" + str(artifact / "image.oci.tar"),
            str(context),
        ]
        result = run(command, log=artifact / "build.log", cwd=context)
    linked = convert_to_docker(
        artifact / "image.oci.tar",
        artifact / "image.docker.tar",
        tag,
        sentinel_file(runtime).read_bytes(),
    )
    run(
        ["docker", "load", "--input", str(artifact / "image.docker.tar")], log=artifact / "load.log"
    )
    actual = json.loads(run(["docker", "image", "inspect", tag]).stdout)[0]
    daemon_link = verify_loaded_image(actual, linked, artifact)
    return evidence(
        root,
        "build-" + version,
        {
            "executed_at": utc_now(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "version": version,
            "tag": tag,
            "image_id": actual["Id"],
            "image_bytes": actual["Size"],
            "builder": builder,
            "images": lock,
            "artifact_directory": str(artifact),
            "oci_sha256": file_digest(artifact / "image.oci.tar"),
            "docker_archive_sha256": file_digest(artifact / "image.docker.tar"),
            "runtime_link": linked,
            "daemon_link": daemon_link,
            "steps": cache_steps(result.stdout + result.stderr),
            "context_workaround": "Cópia temporária dos inputs em caminho ASCII no runtime.",
            "reproducibility": "funcional",
        },
    )


def verify_loaded_image(actual: dict, expected: dict, artifact: Path) -> dict:
    if actual["RootFS"]["Layers"] != expected["diff_ids"]:
        raise RuntimeError("Camadas da imagem carregada não correspondem ao OCI.")
    exported = artifact / "loaded-image.docker.tar"
    run(
        ["docker", "image", "save", "--output", str(exported), actual["Id"]],
        log=artifact / "daemon-export.log",
    )
    return inspect_docker_archive(exported, expected, actual["Id"])


def audit(root: Path, runtime: Path, version: str = "1.0.0") -> dict:
    root, runtime, artifact = paths(root, runtime, version)
    sentinel = sentinel_file(runtime).read_bytes()
    report = inspect_oci(
        artifact / "image.oci.tar", sentinel, root / "app" / "requirements-runtime.lock", artifact
    )
    python_digest = image_lock(root)["python"].split("@sha256:", 1)[1]
    if not any(
        "python" in item.get("uri", "") and item.get("digest", {}).get("sha256") == python_digest
        for item in report["materials"]
    ):
        raise RuntimeError("Base Python da provenance difere do lock")
    report["python_base_material_matches_lock"] = True
    actual = json.loads(run(["docker", "image", "inspect", "containerops-app:" + version]).stdout)[
        0
    ]
    daemon_link = verify_loaded_image(actual, report, artifact)
    history = run(
        ["docker", "history", "--no-trunc", "--format", "{{json .}}", actual["Id"]]
    ).stdout
    if sentinel.decode("ascii") in history:
        raise RuntimeError("Sentinela encontrada no histórico do daemon.")
    (artifact / "history.jsonl").write_text(history, encoding="utf-8")
    container = run(
        [
            "docker",
            "create",
            "--label",
            "com.containerops.purpose=artifact-audit",
            "--network",
            "none",
            actual["Id"],
        ]
    ).stdout.strip()
    try:
        run(["docker", "export", "--output", str(artifact / "filesystem.tar"), container])
        filesystem_hash = file_digest(artifact / "filesystem.tar", sentinel)
    finally:
        run(["docker", "rm", container])
    report.update(
        {
            "executed_at": utc_now(),
            "version": version,
            "image_id": actual["Id"],
            "daemon_config_and_layers_verified": True,
            "daemon_link": daemon_link,
            "sentinel_absent_daemon_history_exported_filesystem": True,
            "sentinel_sha256": file_digest(sentinel_file(runtime)),
            "exported_filesystem_sha256": filesystem_hash,
            "oci_sha256": file_digest(artifact / "image.oci.tar"),
            "artifact_directory": str(artifact),
        }
    )
    return evidence(root, "audit-" + version, report)


def scanner_command(lock: dict, cache: Path, artifact: Path, offline: bool) -> list[str]:
    user = "10001:10001" if os.name == "nt" else f"{os.getuid()}:{os.getgid()}"
    return [
        "docker",
        "run",
        "--rm",
        "--label",
        "com.containerops.purpose=scanner",
        "--user",
        user,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--memory",
        "1g",
        "--cpus",
        "1",
        "--pids-limit",
        "128",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=268435456",
        "--network",
        "none" if offline else "bridge",
        "--mount",
        f"type=bind,src={cache},dst=/cache",
        "--mount",
        f"type=bind,src={artifact},dst=/artifacts,readonly",
        "--mount",
        f"type=bind,src={artifact / 'scanner'},dst=/reports",
        "--env",
        "GOMAXPROCS=1",
        "--env",
        "TRIVY_DISABLE_VEX_NOTICE=true",
        lock["trivy"],
        "--cache-dir",
        "/cache",
    ]


def scan(
    root: Path,
    runtime: Path,
    version: str = "1.0.0",
    *,
    offline: bool = False,
    max_db_age_hours: float = 72,
) -> dict:
    root, runtime, artifact = paths(root, runtime, version)
    evidence(
        root,
        "scan-" + version,
        {"status": "incomplete", "started_at": utc_now(), "version": version},
    )
    lock = image_lock(root)
    if not (artifact / "image.docker.tar").is_file():
        raise RuntimeError("Execute build antes do scan")
    cache = runtime / "trivy-cache"
    cache.mkdir(parents=True, exist_ok=True)
    (artifact / "scanner").mkdir(exist_ok=True)
    command = scanner_command(lock, cache, artifact, offline)
    version_result = run(command + ["--version"], log=artifact / "scanner-version.log").stdout
    if f"Version: {TRIVY_VERSION}" not in version_result:
        raise RuntimeError("Trivy executado difere da versão fixada")
    if not offline:
        run(
            command + ["image", "--download-db-only", "--no-progress"],
            log=artifact / "scanner-db-update.log",
        )
    metadata_path = cache / "db" / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    updated_at = datetime.fromisoformat(metadata["UpdatedAt"].replace("Z", "+00:00"))
    age = (datetime.now(UTC) - updated_at).total_seconds() / 3600
    if metadata.get("Version") != 2 or age < -1 or age > max_db_age_hours:
        raise RuntimeError(
            f"Base Trivy inválida ou vencida: {age:.2f}h; limite {max_db_age_hours}h"
        )
    database_hash = file_digest(cache / "db" / "trivy.db")
    started = time.monotonic()
    # Keep every severity and unfixed finding in the full report; policy is evaluated below.
    run(
        command
        + [
            "image",
            "--input",
            "/artifacts/image.docker.tar",
            "--skip-db-update",
            "--skip-java-db-update",
            "--offline-scan",
            "--scanners",
            "vuln",
            "--ignorefile",
            "/dev/null",
            "--list-all-pkgs",
            "--parallel",
            "1",
            "--timeout",
            "15m",
            "--no-progress",
            "--format",
            "json",
            "--output",
            "/reports/trivy-report.json",
        ],
        log=artifact / "scan.log",
    )
    report_path = artifact / "scanner" / "trivy-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    results = report.get("Results", [])
    if not report.get("Metadata", {}).get("OS", {}).get("Family") or not results:
        raise RuntimeError("Scan sem sistema operacional ou resultados")
    classes = {item.get("Class") for item in results}
    if not {"os-pkgs", "lang-pkgs"}.issubset(classes):
        raise RuntimeError("Scan precisa incluir SO e dependências Python")
    build_evidence = json.loads(
        (root / "docs/evidence" / f"build-{version}.json").read_text(encoding="utf-8")
    )
    expected_id = build_evidence["runtime_link"]["config_digest"]
    if report.get("Metadata", {}).get("ImageID") != expected_id:
        raise RuntimeError("Config digest do scan difere do build")
    if file_digest(cache / "db" / "trivy.db") != database_hash:
        raise RuntimeError("Base alterada durante o scan; execute novamente")
    findings = [finding for item in results for finding in item.get("Vulnerabilities", [])]
    blocking = blocking_vulnerabilities(findings)
    summary = evidence(
        root,
        "scan-" + version,
        {
            "executed_at": utc_now(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "status": "failed_policy" if blocking else "passed",
            "version": version,
            "image_id": build_evidence["image_id"],
            "config_digest": expected_id,
            "scanner_image": lock["trivy"],
            "scanner_version": version_result.strip(),
            "offline": offline,
            "database": metadata,
            "database_age_hours": round(age, 3),
            "database_sha256": database_hash,
            "max_db_age_hours": max_db_age_hours,
            "report": str(report_path),
            "report_sha256": file_digest(report_path),
            "docker_archive_sha256": file_digest(artifact / "image.docker.tar"),
            "findings_by_severity": {
                severity: sum(item.get("Severity") == severity for item in findings)
                for severity in ("UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL")
            },
            "unfixed_findings": sum(not item.get("FixedVersion") for item in findings),
            "blocking_findings": [
                {
                    key: item.get(key)
                    for key in (
                        "VulnerabilityID",
                        "PkgName",
                        "InstalledVersion",
                        "FixedVersion",
                        "Severity",
                    )
                }
                for item in blocking
            ],
            "policy": "Bloqueia todo HIGH/CRITICAL reportado, inclusive sem correção disponível.",
        },
    )
    if blocking:
        raise RuntimeError(
            f"Scan falhou: {len(blocking)} HIGH/CRITICAL reportados; veja {report_path}"
        )
    return summary


def cache_experiment(root: Path, runtime: Path) -> dict:
    root, runtime, _ = paths(root, runtime)
    ensure_builder(root, runtime)
    experiment = runtime / "cache-experiment"
    experiment.mkdir(exist_ok=True)
    original_lock = root / "app" / "requirements-runtime.lock"
    original_hash = file_digest(original_lock)
    with build_context(root, runtime) as context:
        original_inputs = {
            str(path.relative_to(context)): file_digest(path)
            for path in context.rglob("*")
            if path.is_file()
        }
        experiment_id = secrets.token_hex(12)
        # The same namespace in all four builds prevents a previous experiment's
        # alternate lock/source from disguising this series' invalidation behavior.
        with (context / "app/requirements-runtime.lock").open("a", encoding="utf-8") as lock:
            lock.write(f"\n# controlled-cache-experiment: {experiment_id}\n")
        runs = []
        for name in ("initial", "unchanged", "source", "dependency"):
            if name == "source":
                with (context / "app/src/containerops/__init__.py").open(
                    "a", encoding="utf-8"
                ) as source:
                    source.write(f"\nCACHE_EXPERIMENT_MARKER = 'source-{experiment_id}'\n")
            if name == "dependency":
                lock = context / "app/requirements-runtime.lock"
                content = lock.read_text(encoding="utf-8")
                packages = locked_packages(lock)
                before = packages["idna"]
                after = "3.19" if before != "3.19" else "3.20"
                content, count = re.subn(r"(?m)^(idna==)[^\s;]+", r"\g<1>" + after, content)
                if count != 1 or "--hash=" in content:
                    raise RuntimeError(
                        "Formato de lock diferente; confira a versão e os hashes da substituição"
                    )
                lock.write_text(content, encoding="utf-8")
                change = {"package": "idna", "from": before, "to": after}
            command = build_args(context, runtime, "1.0.0")
            if name == "initial":
                command += ["--no-cache"]
            if name == "dependency":
                command += [
                    "--tag",
                    "containerops-cache-experiment:" + experiment_id,
                    "--attest",
                    "type=provenance,mode=max",
                    "--attest",
                    "type=sbom,generator=" + image_lock(root)["sbom_scanner"],
                    "--output",
                    "type=oci,compression=gzip,dest=" + str(experiment / "dependency.oci.tar"),
                ]
            else:
                command += ["--provenance=false", "--output", "type=cacheonly"]
            started = time.monotonic()
            result = run(command + [str(context)], log=experiment / f"{name}.log", cwd=context)
            steps = cache_steps(result.stdout + result.stderr)
            runs.append(
                {
                    "case": name,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "cached_steps": sum(item["cached"] for item in steps),
                    "steps": steps,
                    "log": str(experiment / f"{name}.log"),
                }
            )

        def dependency_cached(run_info: dict) -> bool:
            steps = [
                item
                for item in run_info["steps"]
                if "[deps " in item["step"] and "pip install" in item["step"]
            ]
            if len(steps) != 1:
                raise RuntimeError("Log sem etapa única de instalação de dependências")
            return steps[0]["cached"]

        if (
            dependency_cached(runs[0])
            or not dependency_cached(runs[1])
            or not dependency_cached(runs[2])
            or dependency_cached(runs[3])
        ):
            raise RuntimeError("Cache das dependências não corresponde ao esperado")
        source_steps = [
            item
            for item in runs[2]["steps"]
            if "COPY" in item["step"] and "app/src" in item["step"]
        ]
        if not source_steps or any(item["cached"] for item in source_steps):
            raise RuntimeError("Mudança na fonte não invalidou COPY")
        artifact = inspect_oci(
            experiment / "dependency.oci.tar",
            sentinel_file(runtime).read_bytes(),
            context / "app/requirements-runtime.lock",
            experiment,
        )
        if file_digest(original_lock) != original_hash:
            raise RuntimeError(
                "Lock original alterado durante o teste; confira execuções simultâneas"
            )
        if any(
            not (root / name).is_file() or file_digest(root / name) != digest
            for name, digest in original_inputs.items()
        ):
            raise RuntimeError(
                "Inputs originais alterados durante o teste; confira execuções simultâneas"
            )
        return evidence(
            root,
            "cache-experiment",
            {
                "executed_at": utc_now(),
                "runs": runs,
                "dependency_change": change,
                "experiment_id": experiment_id,
                "original_lock_sha256": original_hash,
                "original_sources_unmodified": True,
                "original_input_hashes": original_inputs,
                "changed_package_verified_in_sbom": artifact["locked_packages_verified"]["idna"],
                "note": "Os quatro builds usam o mesmo comentário de namespace no lock copiado. Initial usa --no-cache; o cache de download do pip pode estar aquecido. Só dependency exporta OCI e SBOM. Os tempos incluem etapas diferentes. As imagens de teste não são carregadas.",
            },
        )


def parse_arguments(argv=None):
    from ops import RELEASES, RUNTIME

    parser = argparse.ArgumentParser(description="Build e auditoria local do ContainerOps")
    parser.set_defaults(version="1.0.0", offline=False)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "audit", "scan", "cache"):
        command = commands.add_parser(name)
        command.add_argument("--runtime", type=Path, default=RUNTIME)
        if name != "cache":
            command.add_argument("--version", choices=RELEASES, default="1.0.0")
        if name == "scan":
            command.add_argument("--offline", action="store_true")
    return parser.parse_args(argv)


def main() -> int:
    from ops import operation_lock

    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = parse_arguments()
    try:
        _, runtime, _ = paths(ROOT, args.runtime, args.version)
        with operation_lock(runtime):
            if args.command == "cache":
                result = cache_experiment(ROOT, runtime)
            elif args.command == "scan":
                result = scan(ROOT, runtime, args.version, offline=args.offline)
            else:
                result = {"build": build, "audit": audit}[args.command](ROOT, runtime, args.version)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"erro: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
