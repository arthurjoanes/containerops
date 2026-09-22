"""Render both interfaces from the same records and generation instant; no operations."""

import importlib.util
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from report import generate

root = Path(__file__).resolve().parents[1]
baseline_ref = "243b1a44769b324faf28f1795f510a49cda5fbdf"
destination = root / ".runtime/art-direction"
sources = destination / "baseline-sources"
sources.mkdir(parents=True, exist_ok=True)
for name in ("report.py", "report.html", "report.css", "report.js"):
    content = subprocess.check_output(["git", "show", f"{baseline_ref}:scripts/{name}"], cwd=root)
    (sources / name).write_bytes(content)
spec = importlib.util.spec_from_file_location("baseline_report", sources / "report.py")
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)
now = datetime.now(UTC)
baseline_page = baseline.generate(root, root / ".runtime", now=now)
shutil.copyfile(baseline_page, destination / "baseline.html")
generate(root, root / ".runtime", now=now)
