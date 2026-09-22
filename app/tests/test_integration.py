import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import psycopg
import pytest
from conftest import TOKEN_BOB
from fastapi.testclient import TestClient

from containerops import repository
from containerops.api import create_app
from containerops.config import Settings
from containerops.database import connect
from containerops.domain import (
    MAX_PENDING_JOBS_PER_OWNER,
    AdmissionPaused,
    JobConflict,
    OwnerQueueFull,
    QueueFull,
    analyze_text,
)

pytestmark = pytest.mark.integration


def expire_lease(settings: Settings) -> None:
    with connect(settings) as connection:
        connection.execute(
            "UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE state='running'"
        )


def test_concurrent_idempotency_is_one_row(database: Settings) -> None:
    def submit(_: int) -> tuple[str, bool]:
        job, created = repository.submit_job(database, "alice", "same", "Olá mundo", 0)
        return str(job.id), created

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))
    assert len({job_id for job_id, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    assert repository.snapshot(database)["counts"]["queued"] == 1


def test_concurrent_conflicting_payloads(database: Settings) -> None:
    def submit(text: str) -> str:
        try:
            repository.submit_job(database, "alice", "same", text, 0)
            return "created"
        except JobConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ["texto A", "texto B"]))
    assert sorted(results) == ["conflict", "created"]


def test_duration_is_part_of_idempotent_request(database: Settings) -> None:
    repository.submit_job(database, "alice", "same", "abc", 0)
    with pytest.raises(JobConflict):
        repository.submit_job(database, "alice", "same", "abc", 1)


def test_workers_do_not_claim_same_live_lease(database: Settings) -> None:
    repository.submit_job(database, "alice", "single", "abc", 0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda _: repository.claim_job(database), range(4)))
    assert sum(job is not None for job in jobs) == 1


def test_stale_worker_cannot_renew_or_overwrite_result(database: Settings) -> None:
    repository.submit_job(database, "alice", "lease", "one two three", 0)
    old = repository.claim_job(database)
    assert old is not None
    expire_lease(database)
    assert not repository.complete_job(database, old, analyze_text(old.payload))
    new = repository.claim_job(database)
    assert new is not None and old.id == new.id and new.lease_token != old.lease_token
    assert new.attempts == 2
    assert not repository.renew_lease(database, old)
    assert not repository.complete_job(database, old, analyze_text("different"))
    assert repository.complete_job(database, new, analyze_text(new.payload))
    assert not repository.complete_job(database, new, analyze_text(new.payload))
    stored = repository.get_job(database, "alice", old.id)
    assert stored is not None and stored.word_count == 3 and stored.state == "succeeded"


def test_expired_jobs_fail_after_three_attempts(database: Settings) -> None:
    repository.submit_job(database, "alice", "limit", "abc", 0)
    for attempt in range(1, 4):
        claimed = repository.claim_job(database)
        assert claimed is not None and claimed.attempts == attempt
        expire_lease(database)
    assert repository.claim_job(database) is None
    result = repository.snapshot(database)
    assert result["counts"]["failed"] == 1
    assert result["jobs"][0]["error_category"] == "attempts_exhausted"


def test_queue_limit_is_atomic_under_concurrency(database: Settings) -> None:
    for number in range(99):
        repository.submit_job(database, f"owner-{number // 20}", f"fill-{number}", "abc", 0)

    def submit(key: str) -> str:
        try:
            repository.submit_job(database, key, key, "abc", 0)
            return "created"
        except QueueFull:
            return "full"

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(submit, ["last-a", "last-b"]))
    assert sorted(result) == ["created", "full"]
    assert repository.snapshot(database)["counts"]["queued"] == 100
    _, created = repository.submit_job(database, "owner-0", "fill-0", "abc", 0)
    assert created is False


def test_pause_preserves_idempotent_reads_and_blocks_cleanup(database: Settings) -> None:
    job, _ = repository.submit_job(database, "alice", "retention", "abc", 0)
    claimed = repository.claim_job(database)
    assert claimed is not None
    assert repository.complete_job(database, claimed, analyze_text("abc"))
    with connect(database) as connection:
        connection.execute("UPDATE jobs SET completed_at=clock_timestamp()-interval '25 hours'")
    repository.set_admission(database, True)
    assert repository.cleanup(database) == 0
    reused, created = repository.submit_job(database, "alice", "retention", "abc", 0)
    assert reused.id == job.id and created is False
    with pytest.raises(AdmissionPaused):
        repository.submit_job(database, "alice", "new", "abc", 0)
    before = repository.snapshot(database)
    assert before == repository.snapshot(database)
    repository.set_admission(database, False)
    assert repository.cleanup(database) == 1


def test_application_role_cannot_do_ddl_or_change_schema_version(database: Settings) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), connect(database) as connection:
        connection.execute("CREATE TABLE public.forbidden (id integer)")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), connect(database) as connection:
        connection.execute("UPDATE schema_version SET version=99")
    with connect(database) as connection:
        role = connection.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
    assert role == (False, False, False)


def test_http_owner_boundary_and_result(database: Settings, auth: dict[str, str]) -> None:
    with TestClient(create_app(database)) as client:
        response = client.post("/v1/jobs", headers=auth, json={"text": "Olá, mundo!"})
        assert response.status_code == 201
        job_id = response.json()["id"]
        repeated = client.post("/v1/jobs", headers=auth, json={"text": "Olá, mundo!"})
        assert repeated.status_code == 200 and repeated.json()["id"] == job_id
        assert client.post("/v1/jobs", headers=auth, json={"text": "outro"}).status_code == 409
        bob = {"Authorization": f"Bearer {TOKEN_BOB}"}
        assert client.get(f"/v1/jobs/{job_id}", headers=bob).status_code == 404
        claimed = repository.claim_job(database)
        assert claimed is not None
        assert repository.complete_job(database, claimed, analyze_text(claimed.payload))
        result = client.get(f"/v1/jobs/{job_id}", headers=auth)
        assert result.json()["state"] == "succeeded"
        assert result.json()["result"]["word_count"] == 2
        assert result.json()["result"]["checksum"] == analyze_text("Olá, mundo!").checksum
        assert "payload" not in result.text and "text" not in result.json()
        assert client.get("/internal/metrics", headers=auth).json()["counts"]["succeeded"] == 1


def test_release_two_schema_keeps_release_one_compatible(database: Settings) -> None:
    assert repository.readiness(database)[0] == 2
    second = replace(database, version="2.0.0")
    assert repository.readiness(second)[0] == 2
    job, _ = repository.submit_job(database, "alice", "release", "a b", 0)
    claimed = repository.claim_job(second)
    assert claimed is not None
    assert repository.complete_job(second, claimed, analyze_text("a b"))
    assert repository.get_job(database, "alice", job.id) is not None
    with connect(database) as connection:
        row = connection.execute("SELECT algorithm FROM jobs WHERE id=%s", (job.id,)).fetchone()
    assert row == ("unicode-alnum-marks-v1",)


def test_zero_duration_replays_include_previously_stored_negative_zero(
    database: Settings, auth: dict[str, str]
) -> None:
    with TestClient(create_app(database)) as client:
        first = client.post(
            "/v1/jobs", headers=auth, json={"text": "abc", "demo_duration_seconds": -0.0}
        )
        assert first.status_code == 201
        # Simula um hash antigo que distingue -0.0 de 0.0.
        legacy_hash = hashlib.sha256(b'["abc",-0.0]').hexdigest()
        with connect(database) as connection:
            connection.execute(
                "UPDATE jobs SET payload_hash=%s WHERE id=%s", (legacy_hash, first.json()["id"])
            )
        replay = client.post(
            "/v1/jobs", headers=auth, json={"text": "abc", "demo_duration_seconds": 0}
        )
        assert replay.status_code == 200
        assert replay.json()["id"] == first.json()["id"]
        assert client.post("/v1/jobs", headers=auth, json={"text": "different"}).status_code == 409


def test_terminal_failure_is_explained_and_replay_does_not_requeue(
    database: Settings, auth: dict[str, str]
) -> None:
    with TestClient(create_app(database)) as client:
        first = client.post("/v1/jobs", headers=auth, json={"text": "exhausted"})
        for _ in range(3):
            assert repository.claim_job(database) is not None
            expire_lease(database)
        assert repository.claim_job(database) is None
        result = client.get(f"/v1/jobs/{first.json()['id']}", headers=auth)
        assert result.status_code == 200
        assert result.json()["state"] == "failed"
        assert result.json()["error"] == {"code": "attempts_exhausted"}
        assert result.json()["result"] is None and result.json()["attempts"] == 3
        replay = client.post("/v1/jobs", headers=auth, json={"text": "exhausted"})
        assert replay.status_code == 200 and replay.json() == result.json()
        assert repository.snapshot(database)["counts"] == {
            "queued": 0,
            "running": 0,
            "succeeded": 0,
            "failed": 1,
        }


def test_http_pause_capacity_and_owner_scoped_keys(
    database: Settings, auth: dict[str, str]
) -> None:
    with TestClient(create_app(database)) as client:
        first = client.post("/v1/jobs", headers=auth, json={"text": "alice"})
        bob = {**auth, "Authorization": f"Bearer {TOKEN_BOB}"}
        other = client.post("/v1/jobs", headers=bob, json={"text": "bob"})
        assert first.status_code == other.status_code == 201
        assert first.json()["id"] != other.json()["id"]
        repository.set_admission(database, True)
        assert client.get("/health/ready").json()["admission_paused"] is True
        assert client.post("/v1/jobs", headers=auth, json={"text": "alice"}).status_code == 200
        paused = client.post(
            "/v1/jobs", headers={**auth, "Idempotency-Key": "new"}, json={"text": "new"}
        )
        assert paused.status_code == 503 and paused.headers["Retry-After"] == "2"
        repository.set_admission(database, False)
        for number in range(98):
            repository.submit_job(database, f"owner-{number // 20}", f"fill-{number}", "x", 0)
        full = client.post(
            "/v1/jobs", headers={**auth, "Idempotency-Key": "new"}, json={"text": "new"}
        )
        assert full.status_code == 429 and full.headers["Retry-After"] == "2"
        assert client.post("/v1/jobs", headers=auth, json={"text": "alice"}).status_code == 200
        conflict = client.post("/v1/jobs", headers=auth, json={"text": "changed"})
        assert conflict.status_code == 409 and "Retry-After" not in conflict.headers


def test_owner_quota_is_atomic_and_includes_running_jobs(database: Settings) -> None:
    for number in range(MAX_PENDING_JOBS_PER_OWNER - 1):
        repository.submit_job(database, "alice", f"fill-{number}", "abc", 15)
    running = repository.claim_job(database)
    assert running is not None

    def submit(number: int) -> str:
        try:
            repository.submit_job(database, "alice", f"race-{number}", "abc", 15)
            return "created"
        except OwnerQueueFull:
            return "full"

    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(submit, range(8)))
    assert result.count("created") == 1 and result.count("full") == 7
    counts = repository.snapshot(database)["counts"]
    assert counts["running"] == 1 and counts["queued"] == MAX_PENDING_JOBS_PER_OWNER - 1
    assert repository.complete_job(database, running, analyze_text(running.payload))
    assert repository.submit_job(database, "alice", "released-slot", "abc", 15)[1]


def test_one_owner_cannot_block_another_in_demo_mode(
    database: Settings, auth: dict[str, str]
) -> None:
    with TestClient(create_app(database)) as client:
        body = {"text": "synthetic demo", "demo_duration_seconds": 15}
        for number in range(MAX_PENDING_JOBS_PER_OWNER):
            headers = {**auth, "Idempotency-Key": f"alice-{number}"}
            assert client.post("/v1/jobs", headers=headers, json=body).status_code == 201
        full = client.post("/v1/jobs", headers=auth, json=body)
        assert full.status_code == 429 and full.headers["Retry-After"] == "2"
        assert "proprietário" in full.json()["detail"]
        replay = {**auth, "Idempotency-Key": "alice-0"}
        assert client.post("/v1/jobs", headers=replay, json=body).status_code == 200
        assert client.post("/v1/jobs", headers=replay, json={"text": "changed"}).status_code == 409
        bob = {**auth, "Authorization": f"Bearer {TOKEN_BOB}"}
        response = client.post("/v1/jobs", headers=bob, json=body)
        assert response.status_code == 201
        first = repository.claim_job(database)
        second = repository.claim_job(database)
        assert first is not None and first.owner == "alice"
        assert second is not None and second.owner == "bob"
        assert str(second.id) == response.json()["id"]


def test_dispatch_shares_workers_between_owners(database: Settings) -> None:
    # Todo o backlog de Alice é anterior ao de Bob. FIFO global monopolizaria os workers.
    for owner in ("alice", "bob"):
        for number in range(6):
            repository.submit_job(database, owner, f"{owner}-{number}", "abc", 15)
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda _: repository.claim_job(database), range(4)))
    assert all(job is not None for job in jobs)
    assert len({job.id for job in jobs if job is not None}) == 4
    assert sum(job.owner == "alice" for job in jobs if job is not None) == 2
    assert sum(job.owner == "bob" for job in jobs if job is not None) == 2


def test_continuous_owner_cannot_delay_another_behind_its_backlog(database: Settings) -> None:
    for owner in ("alice", "bob"):
        for number in range(4):
            repository.submit_job(database, owner, f"{owner}-{number}", "abc", 15)
    owners = []
    for _ in range(8):
        job = repository.claim_job(database)
        assert job is not None
        owners.append(job.owner)
        assert repository.complete_job(database, job, analyze_text(job.payload))
    assert owners == ["alice", "bob"] * 4
