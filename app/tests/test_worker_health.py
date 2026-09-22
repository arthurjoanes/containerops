import subprocess
import sys
from pathlib import Path

import pytest

from containerops import worker_health


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (None, False),
        ("", False),
        ("invalid", False),
        ("nan", False),
        ("inf", False),
        ("100.01", False),
        ("79.99", False),
        ("80", True),
        ("100", True),
    ],
    ids=[
        "missing",
        "empty",
        "invalid",
        "nan",
        "infinite",
        "future",
        "expired",
        "boundary",
        "fresh",
    ],
)
def test_heartbeat_age_contract(tmp_path: Path, monkeypatch, content, expected) -> None:
    path = tmp_path / "heartbeat"
    monkeypatch.setattr(worker_health, "HEARTBEAT", path)
    monkeypatch.setattr(worker_health.time, "time", lambda: 100.0)
    if content is not None:
        path.write_text(content, encoding="ascii")
    assert worker_health.healthy() is expected


def test_probe_does_not_import_database_or_worker() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import containerops.worker_health; "
            "assert 'psycopg' not in sys.modules; assert 'containerops.worker' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        cwd=Path(worker_health.__file__).resolve().parents[1],
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
