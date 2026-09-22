import argparse
import signal
import threading
import time
from types import FrameType

import psycopg

from containerops import repository
from containerops.config import Settings
from containerops.domain import Job, SchemaIncompatible, analyze_text
from containerops.log import configure, event
from containerops.worker_health import healthy, heartbeat

MAX_DATABASE_FAILURES = 6


def process_job(settings: Settings, job: Job) -> bool:
    start = time.monotonic()
    deadline = start + job.duration_seconds
    next_renewal = start + 1
    event(
        "worker",
        settings.version,
        "job_started",
        job_id=job.id,
        attempt=job.attempts,
        recovered=job.attempts > 1,
    )
    while time.monotonic() < deadline:
        time.sleep(min(0.2, max(0, deadline - time.monotonic())))
        heartbeat()
        if time.monotonic() >= next_renewal:
            if not repository.renew_lease(settings, job):
                event(
                    "worker",
                    settings.version,
                    "lease_lost",
                    job_id=job.id,
                    attempt=job.attempts,
                    error_category="stale_lease",
                )
                return False
            next_renewal = time.monotonic() + 1
    success = repository.complete_job(settings, job, analyze_text(job.payload))
    event(
        "worker",
        settings.version,
        "job_completed" if success else "lease_lost",
        job_id=job.id,
        attempt=job.attempts,
        duration_ms=round((time.monotonic() - start) * 1000, 2),
    )
    return success


def run(settings: Settings, stopping: threading.Event) -> int:
    failures = 0
    next_cleanup = time.monotonic()
    while not stopping.is_set():
        heartbeat()
        try:
            repository.readiness(settings)
            if time.monotonic() >= next_cleanup:
                deleted = repository.cleanup(settings)
                if deleted:
                    event("worker", settings.version, "retention_cleanup", deleted_jobs=deleted)
                next_cleanup = time.monotonic() + 60
            if stopping.is_set():
                break
            job = repository.claim_job(settings)
            if job is not None:
                process_job(settings, job)
            failures = 0
            if job is None:
                stopping.wait(0.3)
        except (psycopg.Error, SchemaIncompatible) as error:
            failures += 1
            event(
                "worker",
                settings.version,
                "database_unavailable",
                error_category=type(error).__name__,
                attempt=failures,
            )
            if failures >= MAX_DATABASE_FAILURES:
                event("worker", settings.version, "retry_budget_exhausted")
                return 1
            stopping.wait(min(0.5 * 2 ** (failures - 1), 8))
    event("worker", settings.version, "stopped")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Worker ContainerOps")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()
    if args.healthcheck:
        return 0 if healthy() else 1
    configure()
    settings = Settings.from_env()
    stopping = threading.Event()

    def stop(signum: int, frame: FrameType | None) -> None:
        event("worker", settings.version, "shutdown_requested", signal=signum)
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    return run(settings, stopping)


if __name__ == "__main__":
    raise SystemExit(main())
