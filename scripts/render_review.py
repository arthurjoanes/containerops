"""Render presentation fixtures offline; never write to the operational evidence directory."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from report import generate


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    destination = project / "artifacts/interface-review"
    source = project / "docs/evidence"
    completed = {
        "id": "12345678-1234-1234-1234-123456789abc",
        "state": "succeeded",
        "attempts": 1,
        "version": "2.0.0",
        "result": {"word_count": 7, "checksum": "c" * 64},
    }
    timestamp = "2026-09-21T04:00:00Z"
    cases: dict[str, dict[str, object]] = {
        "empty": {},
        "backup-only": {"backup": (source / "backup.json").read_bytes()},
        "release-invalid": {
            "release": (source / "release.json").read_bytes(),
            "release-failed": b"invalid-json-for-presentation-review",
        },
        "release-failed": {
            "release": (source / "release.json").read_bytes(),
            "release-failed": {
                "recorded_at": "2026-09-22T05:00:00Z",
                "rolled_back": True,
                "project": "presentation-review-failed-release",
                "version": "2.0.0",
            },
        },
    }
    for state in ("queued", "running", "succeeded", "failed", "unexpected"):
        result = (
            completed if state == "succeeded" else {**completed, "state": state, "result": None}
        )
        cases["job-" + state] = {"demo": {"recorded_at": timestamp, "job": result}}
    cases["job-incomplete"] = {
        "demo": {"recorded_at": timestamp, "job": {**completed, "result": None}}
    }
    case_manifest: dict[str, object] = {}
    manifest: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "Presentation fixtures only. No Docker operation or service was executed.",
        "sources": {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in ("backup.json", "release.json")
        },
        "cases": case_manifest,
    }
    for name, records in cases.items():
        root = destination / name
        evidence = root / "docs/evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        for record_name, record in records.items():
            data = record if isinstance(record, bytes) else json.dumps(record).encode("utf-8")
            (evidence / f"{record_name}.json").write_bytes(data)
        output = generate(root, root / "unused-runtime")
        html = output.read_text(encoding="utf-8").replace(
            '<div class="report-heading">',
            '<p class="record-warning">Cenário de apresentação — dados de teste; '
            'nenhuma operação executada.</p><div class="report-heading">',
            1,
        )
        output.write_text(html, encoding="utf-8")
        case_manifest[name] = {
            "report": output.relative_to(project).as_posix(),
            "records": sorted(records),
        }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
