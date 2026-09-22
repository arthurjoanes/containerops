"""Render both interfaces from the same records and generation instant; no operations."""

import importlib.util
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from report import generate

root = Path(__file__).resolve().parents[1]
baseline_ref = "243b1a44769b324faf28f1795f510a49cda5fbdf"
now = datetime.now(UTC)
destination = root / ".runtime/art-direction" / now.strftime("replay-%Y%m%dT%H%M%S%fZ")
sources = destination / "baseline-sources"
sources.mkdir(parents=True, exist_ok=True)
for name in ("report.py", "report.html", "report.css", "report.js"):
    content = subprocess.check_output(["git", "show", f"{baseline_ref}:scripts/{name}"], cwd=root)
    (sources / name).write_bytes(content)
spec = importlib.util.spec_from_file_location("baseline_report", sources / "report.py")
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)
# Copy only versioned inputs, including destinations of the report's relative links.
# Both renderers read the same records; no published HTML or evidence is replaced.
tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode("utf-8")
for relative in filter(None, tracked.split("\0")):
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / relative, target)
baseline_page = baseline.generate(destination, root / ".runtime", now=now)
shutil.copyfile(baseline_page, destination / "docs/baseline.html")
generate(destination, root / ".runtime", now=now)
print(f'Compare: node scripts/audit_art_direction.cjs "{destination}"')
