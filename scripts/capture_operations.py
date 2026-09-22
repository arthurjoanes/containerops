"""Render/capture only the immutable real CO-03/04 attempt, never presentation fixtures."""

import argparse
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

import checks
import ops
import report


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validated_records(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    root = ops.EVIDENCE.resolve()
    checks.require(manifest_path.is_relative_to(root / "problem-proof"), "Manifesto fora da prova")
    manifest = ops.read_json(manifest_path)
    checks.require(manifest.get("status") == "passed", "Prova ainda não aprovada")
    checks.require(manifest.get("scenario") == "operations", "Cenário incompatível")
    checks.require(manifest_path.parent.name == manifest["run_id"], "Identidade divergente")
    records = {}
    for item in manifest["evidence"]:
        path = (root / item["path"]).resolve()
        checks.require(path.parent == manifest_path.parent, "Referência fora da tentativa")
        checks.require(digest(path) == item["sha256"], "Evidência alterada após prova")
        checks.require(path.stem not in records, "Referência duplicada")
        records[path.stem] = ops.read_json(path)
    checks.require(records["restore"].get("status") == "passed", "Restore não aprovado")
    checks.require(records["restore"].get("cleanup_succeeded") is True, "Destino não limpo")
    checks.require(records["source-preservation"].get("equal") is True, "Origem não preservada")
    checks.require(
        records["rollback"].get("injected_smoke_failure") is True, "Falta rollback real controlado"
    )
    checks.require(
        any(s["name"] == "cleanup-source" and s["status"] == "passed" for s in manifest["steps"]),
        "Falta limpeza da origem descartável",
    )
    return manifest, records


def capture(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest, records = validated_records(manifest_path)
    destination = (
        ops.EVIDENCE / "operations-captures" / (manifest["run_id"] + "-" + uuid.uuid4().hex[:8])
    )
    checks.require(not destination.exists(), "Captura já existe; preserve a tentativa anterior")
    # The renderer expects docs/evidence; this separate root never touches docs/report.html.
    isolated = destination / "view"
    evidence = isolated / "docs" / "evidence"
    evidence.mkdir(parents=True)
    for name in ("demo", "rollback", "backup", "restore"):
        # Copy bytes, preserving the exact records that passed their hashes above.
        shutil.copyfile(manifest_path.parent / (name + ".json"), evidence / (name + ".json"))
    html = report.generate(isolated, destination / "unused-runtime")
    # Keep the unchanged renderer's documentation links usable from this nested report.
    text = html.read_text(encoding="utf-8")
    documentation_links = {}
    for original, target in {
        "../README.md": ops.ROOT / "README.md",
        "runbooks.md": ops.ROOT / "docs" / "runbooks.md",
        "verification.md": ops.ROOT / "docs" / "verification.md",
    }.items():
        checks.require(target.is_file(), "Documento do relatório ausente")
        relative = Path(os.path.relpath(target, html.parent)).as_posix()
        text = text.replace(f'href="{original}"', f'href="{relative}"')
        documentation_links[original] = relative
    html.write_text(text, encoding="utf-8", newline="\n")
    record = {
        "run_id": manifest["run_id"],
        "status": "in_progress",
        "recorded_at": ops.now(),
        "scope": "Real recorded operations rendered locally; not a live application UI or user study",
        "proof": manifest_path.relative_to(ops.EVIDENCE).as_posix(),
        "proof_sha256": digest(manifest_path),
        "renderer_sources": {
            name: digest(ops.ROOT / "scripts" / name)
            for name in (
                "report.py",
                "report_evidence.py",
                "report.html",
                "report.css",
                "report.js",
                "capture_operations.py",
                "capture_operations.cjs",
            )
        },
        "report_sha256": digest(html),
        "documentation_links": documentation_links,
        "changes_since_operational_proof": [
            {
                "path": name,
                "at_proof": expected,
                "at_capture": digest(ops.ROOT / name) if (ops.ROOT / name).is_file() else None,
            }
            for name, expected in manifest["source"]["files"].items()
            if not (ops.ROOT / name).is_file() or digest(ops.ROOT / name) != expected
        ],
    }
    ops.write_json(destination / "manifest.json", record)
    try:
        ops.run(
            ["node", ops.ROOT / "scripts" / "capture_operations.cjs", html, destination],
            timeout=120,
        )
        browser = ops.read_json(destination / "browser.json")
        checks.require(browser["status"] == "passed", "Captura não passou")
        checks.require(digest(manifest_path) == record["proof_sha256"], "Prova mudou na captura")
        record.update(
            status="passed",
            completed_at=ops.now(),
            browser=browser,
            screenshots={p.name: digest(p) for p in sorted(destination.glob("*.png"))},
        )
    except BaseException as error:
        record.update(status="failed", error_category=type(error).__name__, completed_at=ops.now())
        raise
    finally:
        ops.write_json(destination / "manifest.json", record)
    print(
        json.dumps(
            {"capture_manifest": str(destination / "manifest.json"), "status": record["status"]}
        )
    )
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Immutable passed operations proof manifest")
    capture(parser.parse_args().manifest)
