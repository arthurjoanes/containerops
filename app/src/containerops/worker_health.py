"""Worker heartbeat probe using only the standard library."""

import time
from pathlib import Path

HEARTBEAT = Path("/tmp/worker-health")


def heartbeat() -> None:
    HEARTBEAT.write_text(str(time.time()), encoding="ascii")


def healthy() -> bool:
    try:
        return 0 <= time.time() - float(HEARTBEAT.read_text(encoding="ascii")) <= 20
    except (OSError, ValueError):
        return False


if __name__ == "__main__":
    raise SystemExit(0 if healthy() else 1)
