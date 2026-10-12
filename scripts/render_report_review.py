"""Freeze a renderer and public evidence in a new review directory; no operations run."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "report.py",
    "report_evidence.py",
    "runtime_context.py",
    "report.html",
    "report.css",
    "report.js",
)
SCENARIOS = (
    "real",
    "catalog-empty",
    "catalog-malformed",
    "integrity-without-receipt",
    "cleanup-failed",
    "legacy-no-cleanup",
    "long-ids-imported",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe(path):
    path = Path(path).absolute()
    for node in (*reversed(path.parents), path):
        try:
            item = node.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(item.st_mode) or getattr(item, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Review paths must not contain links or reparse points")
    return path


def write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def synthetic(directory, name, generated_at):
    catalog = directory / "backup-catalog.json"
    if name == "catalog-malformed":
        catalog.write_text('{"entries": [ }', encoding="utf-8")
        return
    record = (
        json.loads(catalog.read_text(encoding="utf-8")) if catalog.exists() else {"entries": []}
    )
    if name == "catalog-empty":
        record["entries"] = []
    elif name == "integrity-without-receipt":
        (directory / "restore.json").unlink(missing_ok=True)
        for entry in record.get("entries", []):
            entry["restore_verified"] = False
    elif name in ("cleanup-failed", "legacy-no-cleanup"):
        path = directory / "restore.json"
        restore = json.loads(path.read_text(encoding="utf-8"))
        if name == "cleanup-failed":
            restore.update(
                status="failed",
                cleanup_succeeded=False,
                cleanup_error_category="FixtureCleanupError",
            )
        else:
            restore.pop("cleanup_succeeded", None)
        for entry in record.get("entries", []):
            entry["restore_verified"] = False
        write_json(path, restore)
    elif name == "long-ids-imported":
        row = {
            "source_project": "pf-containerops-test-12345678",
            "source_context": "fixture-origin",
            "created_at": generated_at,
            "schema_version": 2,
            "size_bytes": 17550,
            "validation": "valid",
            "protected": True,
            "imported": True,
            "restore_verified": False,
            "retention_reasons": ["marked-protected", "last-valid"],
        }
        record["entries"] = [
            {**row, "id": "import-" + "long-identifier-" * 7},
            {
                **row,
                "id": "restore-verified-fixture",
                "restore_verified": True,
                "retention_reasons": ["last-restore-verified"],
            },
            {
                **row,
                "id": "unknown-fixture",
                "validation": "unknown",
                "size_bytes": None,
                "schema_version": None,
                "restore_verified": False,
                "retention_reasons": ["unknown"],
            },
        ]
    record["_presentation_fixture"] = name
    write_json(catalog, record)


def render(output, source, generated_at):
    # MSIX can expose a logical AppData path whose physical cache path differs.
    # Use the resolved new output path; the frozen production guards stay intact.
    output, source = safe(output).resolve(), safe(source)
    if output.exists():
        raise RuntimeError("Output exists; choose a new directory to preserve prior reviews")
    if (source / "evidence").is_dir():
        source = source / "evidence"
    if not source.is_dir():
        raise RuntimeError("Public curated evidence directory is missing")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    diff = subprocess.check_output(
        [
            "git",
            "diff",
            "--binary",
            "HEAD",
            "--",
            "scripts/report.py",
            "scripts/report_evidence.py",
            "scripts/report.html",
            "scripts/report.css",
            "scripts/report.js",
        ],
        cwd=ROOT,
    )
    output.mkdir(parents=True)
    frozen = output / "frozen" / "scripts"
    frozen.mkdir(parents=True)
    source_hashes = {}
    source_names = [*SOURCES]
    if (ROOT / "scripts" / "report_recovery.py").exists():
        source_names.append("report_recovery.py")
    for name in source_names:
        path = safe(ROOT / "scripts" / name)
        shutil.copyfile(path, frozen / name)
        source_hashes["scripts/" + name] = digest(frozen / name)
        if digest(path) != source_hashes["scripts/" + name]:
            raise RuntimeError("Renderer source changed while freezing")
    shutil.copytree(ROOT / "scripts" / "assets", frozen / "assets")
    for path in (frozen / "assets").rglob("*"):
        safe(path)
        if path.is_file():
            source_hashes["scripts/assets/" + path.relative_to(frozen / "assets").as_posix()] = (
                digest(path)
            )
    inputs = {}
    files = []
    for path in sorted(source.rglob("*.json")):
        safe(path)
        if not path.is_file():
            raise RuntimeError("Review input must be a regular public JSON file")
        content = path.read_bytes()
        if re.search(rb"[A-Za-z]:[\\/]+(?:Users|Documents)|/home/|/Users/", content):
            raise RuntimeError("Public review input contains a private host path")
        relative = path.relative_to(source)
        files.append((relative, content))
        inputs[relative.as_posix()] = hashlib.sha256(content).hexdigest()
    # Import every renderer dependency from the frozen snapshot, never from a
    # mutable previously imported report module. Rendering uses no session state.
    previous = {
        key: os.environ.get(key) for key in ("CONTAINEROPS_SESSION", "CONTAINEROPS_RUNTIME")
    }
    os.environ.pop("CONTAINEROPS_SESSION", None)
    os.environ.pop("CONTAINEROPS_RUNTIME", None)
    sys.path.insert(0, str(frozen))
    for name in ("report_evidence", "runtime_context", "report_recovery"):
        sys.modules.pop(name, None)
    try:
        spec = importlib.util.spec_from_file_location("frozen_review_report", frozen / "report.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cases = {}
        for name in SCENARIOS:
            view = output / name
            evidence = view / "docs" / "evidence"
            evidence.mkdir(parents=True)
            for relative, content in files:
                target = evidence / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            for guide in (ROOT / "docs").glob("*.md"):
                shutil.copyfile(safe(guide), view / "docs" / guide.name)
            shutil.copyfile(ROOT / "README.md", view / "README.md")
            if name != "real":
                synthetic(evidence, name, generated_at.isoformat())
            report = module.generate(view, view / "unused-runtime", now=generated_at)
            if name != "real":
                content = report.read_text(encoding="utf-8")
                notice = '<p class="fixture-notice" role="note">Cenário de apresentação — dados de teste; nenhuma operação executada.</p>'
                content = re.sub(
                    r"(<main\b[^>]*>)", lambda match: match[1] + notice, content, count=1
                )
                report.write_text(content, encoding="utf-8", newline="\n")
            cases[name] = {
                "report": report.relative_to(output).as_posix(),
                "sha256": digest(report),
                "presentation_fixture": name != "real",
            }
    finally:
        sys.path.pop(0)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    if any(digest(ROOT / relative) != value for relative, value in source_hashes.items()):
        raise RuntimeError(
            "Renderer source changed during preparation; choose a new output and freeze edits"
        )
    manifest = {
        "generated_at": generated_at.isoformat(),
        "source_revision": revision,
        "source_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "source_files_sha256": source_hashes,
        "input_files_sha256": inputs,
        "scenarios": cases,
        "scope": "Public saved 12 October evidence replayed offline. Synthetic scenarios are visibly marked; no Docker or domain operation ran.",
    }
    write_json(output / "review-manifest.json", manifest)
    print(output / "review-manifest.json")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--source", type=Path, default=ROOT / "docs/evidence/containerops-evolution-20261012"
    )
    parser.add_argument("--generated-at", default=datetime.now(UTC).isoformat())
    arguments = parser.parse_args()
    generated_at = datetime.fromisoformat(arguments.generated_at.replace("Z", "+00:00"))
    if generated_at.tzinfo is None or generated_at.utcoffset().total_seconds() != 0:
        parser.error("--generated-at must include a UTC timezone")
    render(arguments.output, arguments.source, generated_at)


if __name__ == "__main__":
    main()
