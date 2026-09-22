"""Scan the database and proxy archives without exposing the Docker socket."""

import argparse
import hashlib
import json
import tarfile
from datetime import UTC, datetime

import ops
import supply


def scan_services(*, offline=False):
    lock = supply.image_lock(ops.ROOT)
    cache = ops.RUNTIME / "trivy-cache"
    cache.mkdir(parents=True, exist_ok=True)
    summaries = []
    ops.evidence("scan-services", {"status": "incomplete", "images": []})
    for component in ("database", "proxy"):
        image = f"containerops-{component}:local"
        artifact = ops.RUNTIME / "service-scans" / component
        (artifact / "scanner").mkdir(parents=True, exist_ok=True)
        archive = artifact / "image.docker.tar"
        supply.run(["docker", "image", "save", "--output", str(archive), image])
        with tarfile.open(archive, "r:*") as exported:
            manifests = json.load(exported.extractfile("manifest.json"))
            if len(manifests) != 1:
                raise RuntimeError("Scan de serviço exige uma única imagem")
            config = exported.extractfile(manifests[0]["Config"]).read()
            config_digest = "sha256:" + hashlib.sha256(config).hexdigest()
        command = supply.scanner_command(lock, cache, artifact, offline)
        version = supply.run(command + ["--version"]).stdout
        if f"Version: {supply.TRIVY_VERSION}" not in version:
            raise RuntimeError("Versão Trivy diverge do lock")
        if not offline:
            supply.run(command + ["image", "--download-db-only", "--no-progress"])
        metadata = ops.read_json(cache / "db" / "metadata.json")
        updated = datetime.fromisoformat(metadata["UpdatedAt"].replace("Z", "+00:00"))
        age = (datetime.now(UTC) - updated).total_seconds() / 3600
        if metadata.get("Version") != 2 or not -1 <= age <= 72:
            raise RuntimeError("Base Trivy ausente, inválida ou fora do limite de 72 horas")
        database_hash = supply.file_digest(cache / "db" / "trivy.db")
        supply.run(
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
        report = ops.read_json(report_path)
        if report.get("Metadata", {}).get("ImageID") != config_digest:
            raise RuntimeError("Config do scan não corresponde à imagem exportada")
        if not any(item.get("Class") == "os-pkgs" for item in report.get("Results", [])):
            raise RuntimeError("Scan sem inventário do sistema operacional")
        if supply.file_digest(cache / "db" / "trivy.db") != database_hash:
            raise RuntimeError("Base Trivy mudou durante o scan")
        findings = [v for result in report["Results"] for v in result.get("Vulnerabilities", [])]
        blockers = supply.blocking_vulnerabilities(findings)
        summaries.append(
            {
                "component": component,
                "image": image,
                "image_id": ops.image_id(image),
                "config_digest": config_digest,
                "findings_by_severity": {
                    severity: sum(v.get("Severity") == severity for v in findings)
                    for severity in ("UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL")
                },
                "blocking_count": len(blockers),
                "report_sha256": supply.file_digest(report_path),
                "database_sha256": database_hash,
                "database_updated_at": metadata["UpdatedAt"],
                "database_age_hours": round(age, 3),
            }
        )
    failed = any(summary["blocking_count"] for summary in summaries)
    result = {
        "status": "failed_policy" if failed else "passed",
        "scanner_image": lock["trivy"],
        "images": summaries,
        "policy": "Bloqueia HIGH/CRITICAL, inclusive sem correção; todas as severidades registradas.",
    }
    ops.evidence("scan-services", result)
    if failed:
        raise RuntimeError("Scan de banco/proxy encontrou HIGH/CRITICAL")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    arguments = parser.parse_args()
    with ops.operation_lock():
        scan_services(offline=arguments.offline)
