from typing import TypedDict, cast
from uuid import UUID, uuid4

from psycopg.rows import class_row

from containerops.config import Settings
from containerops.database import connect
from containerops.domain import (
    LEASE_SECONDS,
    MAX_ATTEMPTS,
    MAX_PENDING_JOBS,
    AdmissionPaused,
    Job,
    JobConflict,
    QueueFull,
    SchemaIncompatible,
    TextResult,
    fingerprint,
)

JOB_COLUMNS = (
    "id, owner, payload, payload_hash, state, attempts, duration_seconds, "
    "lease_token, word_count, checksum, error_category"
)


class SnapshotJob(TypedDict):
    id: str
    owner: str
    state: str
    attempts: int
    word_count: int | None
    checksum: str | None
    error_category: str | None


class Snapshot(TypedDict):
    schema_version: int
    admission_paused: bool
    counts: dict[str, int]
    jobs: list[SnapshotJob]


def readiness(settings: Settings) -> tuple[int, bool]:
    with connect(settings) as connection:
        row = connection.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            raise SchemaIncompatible("Schema ausente")
        version = cast(int, row[0])
        required = 2 if settings.release_two else 1
        if not required <= version <= 2:
            raise SchemaIncompatible("Schema incompatível com a release")
        paused = connection.execute("SELECT admission_paused FROM operations").fetchone()
        if paused is None:
            raise SchemaIncompatible("Estado operacional ausente")
        return version, cast(bool, paused[0])


def submit_job(
    settings: Settings, owner: str, key: str, text: str, duration: float
) -> tuple[Job, bool]:
    payload_hash = fingerprint(text, duration)
    with connect(settings) as connection:
        # O lock em operations serializa admissão, pausa e limite global; UNIQUE impede duplicatas.
        operation = connection.execute(
            "SELECT admission_paused FROM operations WHERE singleton FOR UPDATE"
        ).fetchone()
        if operation is None:
            raise SchemaIncompatible("Estado operacional ausente")
        with connection.cursor(row_factory=class_row(Job)) as cursor:
            cursor.execute(
                f"SELECT {JOB_COLUMNS} FROM jobs WHERE owner = %s AND idempotency_key = %s",
                (owner, key),
            )
            existing = cursor.fetchone()
            if existing is not None:
                # Hashes anteriores distinguiam -0.0 de 0.0; preserve replays já persistidos.
                if (
                    existing.payload_hash != payload_hash
                    and fingerprint(existing.payload, existing.duration_seconds) != payload_hash
                ):
                    raise JobConflict
                return existing, False
            if operation[0]:
                raise AdmissionPaused
            count = connection.execute(
                "SELECT count(*) FROM jobs WHERE state IN ('queued', 'running')"
            ).fetchone()
            assert count is not None
            if cast(int, count[0]) >= MAX_PENDING_JOBS:
                raise QueueFull
            cursor.execute(
                "INSERT INTO jobs (id, owner, idempotency_key, payload, payload_hash, state, "
                "duration_seconds) VALUES (%s,%s,%s,%s,%s,'queued',%s) "
                f"RETURNING {JOB_COLUMNS}",
                (uuid4(), owner, key, text, payload_hash, duration),
            )
            created = cursor.fetchone()
            assert created is not None
            return created, True


def get_job(settings: Settings, owner: str, job_id: UUID) -> Job | None:
    with connect(settings) as connection:
        with connection.cursor(row_factory=class_row(Job)) as cursor:
            cursor.execute(
                f"SELECT {JOB_COLUMNS} FROM jobs WHERE owner=%s AND id=%s", (owner, job_id)
            )
            return cursor.fetchone()


def claim_job(settings: Settings) -> Job | None:
    with connect(settings) as connection:
        connection.execute(
            "UPDATE jobs SET state='failed', error_category='attempts_exhausted', "
            "lease_token=NULL, lease_until=NULL, completed_at=clock_timestamp(), "
            "updated_at=clock_timestamp() WHERE state='running' "
            "AND lease_until < clock_timestamp() AND attempts >= %s",
            (MAX_ATTEMPTS,),
        )
        with connection.cursor(row_factory=class_row(Job)) as cursor:
            cursor.execute(
                "WITH candidate AS (SELECT id FROM jobs WHERE attempts < %s AND "
                "(state='queued' OR (state='running' AND lease_until < clock_timestamp())) "
                "ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1) "
                "UPDATE jobs SET state='running', attempts=attempts+1, lease_token=%s, "
                "lease_until=clock_timestamp() + %s * interval '1 second', "
                "updated_at=clock_timestamp() WHERE id IN (SELECT id FROM candidate) "
                f"RETURNING {JOB_COLUMNS}",
                (MAX_ATTEMPTS, uuid4(), LEASE_SECONDS),
            )
            return cursor.fetchone()


def renew_lease(settings: Settings, job: Job) -> bool:
    with connect(settings) as connection:
        result = connection.execute(
            "UPDATE jobs SET lease_until=clock_timestamp()+%s*interval '1 second', "
            "updated_at=clock_timestamp() WHERE id=%s AND state='running' AND lease_token=%s "
            "AND lease_until > clock_timestamp()",
            (LEASE_SECONDS, job.id, job.lease_token),
        )
        return result.rowcount == 1


def complete_job(settings: Settings, job: Job, result: TextResult) -> bool:
    with connect(settings) as connection:
        completed = connection.execute(
            "UPDATE jobs SET state='succeeded', word_count=%s, checksum=%s, "
            "lease_token=NULL, lease_until=NULL, completed_at=clock_timestamp(), "
            "updated_at=clock_timestamp() WHERE id=%s AND state='running' AND lease_token=%s "
            "AND lease_until > clock_timestamp()",
            (result.word_count, result.checksum, job.id, job.lease_token),
        )
        if completed.rowcount == 1 and settings.release_two:
            connection.execute(
                "UPDATE jobs SET algorithm='unicode-alnum-marks-v1' WHERE id=%s", (job.id,)
            )
        return completed.rowcount == 1


def set_admission(settings: Settings, paused: bool) -> None:
    with connect(settings) as connection:
        result = connection.execute(
            "UPDATE operations SET admission_paused=%s WHERE singleton", (paused,)
        )
        if result.rowcount != 1:
            raise SchemaIncompatible("Estado operacional ausente")


def cleanup(settings: Settings) -> int:
    with connect(settings) as connection:
        operation = connection.execute(
            "SELECT admission_paused FROM operations WHERE singleton FOR UPDATE"
        ).fetchone()
        if operation is None:
            raise SchemaIncompatible("Estado operacional ausente")
        if operation[0]:
            return 0
        result = connection.execute(
            "DELETE FROM jobs WHERE completed_at < clock_timestamp() - interval '24 hours'"
        )
        return result.rowcount


def snapshot(settings: Settings) -> Snapshot:
    with connect(settings) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        version = connection.execute("SELECT version FROM schema_version").fetchone()
        operation = connection.execute("SELECT admission_paused FROM operations").fetchone()
        if version is None or operation is None:
            raise SchemaIncompatible("Schema incompleto")
        with connection.cursor(row_factory=class_row(Job)) as cursor:
            cursor.execute(f"SELECT {JOB_COLUMNS} FROM jobs ORDER BY id")
            jobs = cursor.fetchall()
        counts = dict.fromkeys(("queued", "running", "succeeded", "failed"), 0)
        rows: list[SnapshotJob] = []
        for job in jobs:
            counts[job.state] += 1
            rows.append(
                {
                    "id": str(job.id),
                    "owner": job.owner,
                    "state": job.state,
                    "attempts": job.attempts,
                    "word_count": job.word_count,
                    "checksum": job.checksum,
                    "error_category": job.error_category,
                }
            )
        return {
            "schema_version": cast(int, version[0]),
            "admission_paused": cast(bool, operation[0]),
            "counts": counts,
            "jobs": rows,
        }


def metrics(settings: Settings) -> dict[str, object]:
    with connect(settings) as connection:
        counts = connection.execute("SELECT state, count(*) FROM jobs GROUP BY state").fetchall()
        stats = connection.execute(
            "SELECT coalesce(max(extract(epoch FROM (clock_timestamp()-created_at))) "
            "FILTER (WHERE state IN ('queued','running')),0), "
            "coalesce(avg(extract(epoch FROM (completed_at-created_at))) "
            "FILTER (WHERE state='succeeded'),0), "
            "coalesce(sum(greatest(attempts-1,0)),0) FROM jobs"
        ).fetchone()
        assert stats is not None
        return {
            "counts": {cast(str, state): cast(int, count) for state, count in counts},
            "oldest_pending_seconds": float(str(stats[0])),
            "mean_completion_seconds": float(str(stats[1])),
            "recoveries": int(str(stats[2])),
            "database_healthy": True,
        }
