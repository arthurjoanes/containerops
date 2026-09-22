"""Bounded local admission experiment; synthetic inputs, fresh Stack per repetition.

Run: python scripts/measure_admission.py --image containerops-app:1.0.0
No build, retries, image pulls, benchmark claim, or operation on the main project.
The public JSON contains every request, selected DB columns and worker lifecycle logs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime

import ops
import proof

SCENARIO = {
    "repetitions": 3,
    "owners": ["alice", "bob"],
    "requests_per_owner": 24,
    "concurrency": 8,
    "initial_history_jobs": 0,
    "owner_pending_limit": 20,
    "global_pending_limit": 100,
    "demo_duration_seconds": 0.5,
    "drain_deadline_seconds": 90,
    "global_deadline_seconds": 600,
    "cleanup_reserve_seconds": 60,
    "text": "alpha beta gamma",
    "expected_word_count": 3,
    "request_retries": 0,
    "worker_control": "graceful stop of empty worker, then start; admission stays enabled",
    "submission_order": "interleaved alice,bob; executor starts up to 8 requests concurrently",
}

DB_QUERY = """
import json
from psycopg.rows import dict_row
from containerops.config import Settings
from containerops.database import connect
from containerops.domain import MAX_PENDING_JOBS, MAX_PENDING_JOBS_PER_OWNER
with connect(Settings.from_env()) as c:
    c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    with c.cursor(row_factory=dict_row) as q:
        q.execute('SELECT clock_timestamp() AS observed_at')
        stamp=q.fetchone()['observed_at']
        q.execute('SELECT id, owner, state, attempts, created_at, updated_at, completed_at, '
                  'duration_seconds, word_count, checksum, error_category FROM jobs ORDER BY id')
        jobs=q.fetchall()
        q.execute('SELECT admission_paused FROM operations')
        paused=q.fetchone()['admission_paused']
        q.execute('SELECT version FROM schema_version')
        version=q.fetchone()['version']
print(json.dumps(dict(observed_at=stamp, jobs=jobs, admission_paused=paused,
    schema_version=version, global_limit=MAX_PENDING_JOBS,
    owner_limit=MAX_PENDING_JOBS_PER_OWNER), default=str))
"""


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def nearest_rank(values, percentile=95):
    """No interpolation; empty observations stay null, never zero."""
    if not 0 < percentile <= 100:
        raise ValueError("Percentile must be in (0, 100]")
    if not values:
        return None
    return sorted(values)[math.ceil(percentile / 100 * len(values)) - 1]


def distribution(values):
    return {
        "n": len(values),
        "min": min(values) if values else None,
        "p50": nearest_rank(values, 50),
        "p95": nearest_rank(values, 95),
        "max": max(values) if values else None,
        "unit": "milliseconds",
        "method": "nearest-rank: sorted values[ceil(p*n)-1], no interpolation",
    }


def utc_seconds(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def source_identity():
    paths = [ops.ROOT / "compose.yaml", ops.ROOT / "compose.tls.yaml"]
    paths.append(ops.ROOT / "tests" / "test_measure_admission.py")
    paths += [
        ops.ROOT / "scripts" / name
        for name in (
            "measure_admission.py",
            "ops.py",
            "proof.py",
            "checks.py",
            "supply.py",
            "scan_services.py",
            "oci_audit.py",
        )
    ]
    for folder in ("app", "docker"):
        paths += [
            p
            for p in (ops.ROOT / folder).rglob("*")
            if p.is_file()
            and not {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"} & set(p.parts)
        ]
    exact = {
        p.relative_to(ops.ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(set(paths))
    }
    lf = {
        p.relative_to(ops.ROOT).as_posix(): hashlib.sha256(
            p.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for p in sorted(set(paths))
    }
    return {
        "revision": ops.run(["git", "rev-parse", "HEAD"], capture=True).stdout.strip(),
        "files": exact,
        "text_lf_files": lf,
        "sha256": hashlib.sha256(json.dumps(exact, sort_keys=True).encode()).hexdigest(),
        "scope": "operational app, Docker recipes/config, Compose and imported measurement helpers; report UI excluded",
    }


@contextmanager
def bounded_commands(deadline):
    """Preserve ops guards; cap subprocesses and Stack HTTP by the shared deadline."""
    original = ops.run
    original_urlopen = ops.urllib.request.urlopen

    def bounded(*args, **kwargs):
        remaining = deadline[0] - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Global experiment deadline exhausted")
        kwargs["timeout"] = min(kwargs.get("timeout", 300), remaining)
        return original(*args, **kwargs)

    def bounded_urlopen(*args, **kwargs):
        remaining = deadline[0] - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Global experiment HTTP deadline exhausted")
        kwargs["timeout"] = min(kwargs.get("timeout", 8), remaining)
        return original_urlopen(*args, **kwargs)

    ops.run = bounded
    ops.urllib.request.urlopen = bounded_urlopen
    try:
        yield
    finally:
        ops.run = original
        ops.urllib.request.urlopen = original_urlopen


class MeasuredStack(ops.Stack):
    def __init__(self, project, images, deadline):
        super().__init__(project, image=images["app"])
        self.images = images
        self.deadline = deadline

    def env(self):
        return {
            **super().env(),
            "PROXY_IMAGE": self.images["proxy"],
            "DATABASE_IMAGE": self.images["db"],
            "DEMO_MODE": "true",
        }

    def ready(self):
        while time.monotonic() < self.deadline[0]:
            try:
                if self.request("GET", "/health/ready", owner=None)[0] == 200:
                    return True
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        raise TimeoutError("Readiness exceeded global deadline")


def containers():
    return [
        json.loads(line)
        for line in ops.run(
            ["docker", "ps", "--format", "{{json .}}"], capture=True
        ).stdout.splitlines()
        if line.strip()
    ]


def database(stack):
    result = stack.compose("exec", "-T", "api", "python", "-c", DB_QUERY, capture=True)
    return json.loads(result.stdout)


def owned_service(stack, service):
    item = stack.inspect(service)
    labels = item["Config"].get("Labels", {})
    require(
        labels.get("com.docker.compose.project") == stack.project,
        "Service project ownership mismatch",
    )
    require(labels.get("com.docker.compose.service") == service, "Service label mismatch")
    return item


def resource_identity(stack):
    result = {}
    for service in ("api", "worker", "db", "proxy"):
        item = owned_service(stack, service)
        expected = stack.images["app" if service in ("api", "worker") else service]
        require(item["Image"] == expected, "Running image differs from pinned image")
        host = item["HostConfig"]
        require(0 < host["Memory"] <= 256 * 1024**2, "Memory limit missing or raised")
        require(0 < host["NanoCpus"] <= 750_000_000, "CPU limit missing or raised")
        result[service] = {
            "id": item["Id"],
            "image_id": item["Image"],
            "memory_bytes": host["Memory"],
            "nano_cpus": host["NanoCpus"],
            "pids_limit": host["PidsLimit"],
        }
    require(
        "DEMO_MODE=true" in owned_service(stack, "worker")["Config"]["Env"],
        "Synthetic duration requires demo mode",
    )
    return result


def request_observation(stack, owner, sequence, started, deadline):
    before = time.monotonic()
    row = {
        "owner": owner,
        "sequence": sequence,
        "started_at": ops.now(),
        "started_offset_seconds": before - started,
        "status": None,
        "response": None,
    }
    if before >= deadline[0]:
        row.update(
            dispatch_censored=True,
            censor_reason="global deadline before HTTP dispatch",
            completed_at=ops.now(),
            admission_latency_ms=None,
        )
        return row
    row["dispatch_censored"] = False
    try:
        status, response = stack.request(
            "POST",
            "/v1/jobs",
            {"text": SCENARIO["text"], "demo_duration_seconds": 0.5},
            owner=owner,
            key=f"measurement-{owner}-{sequence:02d}",
        )
        row.update(status=status, response=response)
    except (OSError, ValueError) as error:
        row["transport_error_category"] = type(error).__name__
    row.update(completed_at=ops.now(), admission_latency_ms=(time.monotonic() - before) * 1000)
    return row


def lifecycle_logs(stack):
    result = stack.compose("logs", "--no-color", "--no-log-prefix", "worker", capture=True)
    selected = []
    allowed = {
        "timestamp",
        "service",
        "version",
        "event",
        "job_id",
        "attempt",
        "recovered",
        "duration_ms",
        "error_category",
    }
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") in (
            "job_started",
            "job_completed",
            "lease_lost",
            "database_unavailable",
            "retry_budget_exhausted",
        ):
            selected.append({key: value for key, value in event.items() if key in allowed})
    return selected


def correlate(requests, snapshot, logs, release):
    db = {job["id"]: job for job in snapshot["jobs"]}
    starts = {}
    for event in logs:
        # The DB transaction is a consistent snapshot. Later logs remain archived but
        # cannot turn a queued/censored job in that snapshot into an observed start.
        if event["event"] == "job_started" and utc_seconds(event["timestamp"]) <= utc_seconds(
            snapshot["observed_at"]
        ):
            starts.setdefault(event["job_id"], []).append(event)
    accepted = [r for r in requests if r["status"] == 201]
    ids = [r["response"]["id"] for r in accepted]
    require(len(ids) == len(set(ids)), "Accepted requests share a job ID")
    require(set(db) == set(ids), "DB jobs differ from accepted request IDs")
    require(set(starts) <= set(ids), "Worker started an unoffered job")
    before = utc_seconds(release["before_db_utc"])
    after = utc_seconds(release["after_db_utc"])
    require(after >= before, "DB clock moved backwards around release")
    rows = []
    for request in accepted:
        job = db[request["response"]["id"]]
        require(job["owner"] == request["owner"], "Job owner correlation failed")
        row = {
            **job,
            "request_sequence": request["sequence"],
            "start_events": starts.get(job["id"], []),
            "start_censored": job["id"] not in starts,
            "completion_censored": job["state"] not in ("succeeded", "failed"),
        }
        created = utc_seconds(job["created_at"])
        row["induced_wait_lower_ms"] = (before - created) * 1000
        require(row["induced_wait_lower_ms"] >= 0, "Job created after worker release")
        if row["start_events"]:
            require(
                len(row["start_events"]) == 1 and job["attempts"] == 1,
                "Recovery/duplicate starts invalidate this no-failure queue measurement",
            )
            start = utc_seconds(row["start_events"][0]["timestamp"])
            require(start >= before, "Worker started during deliberate pause")
            row.update(
                total_queue_wait_ms=(start - created) * 1000,
                after_release_wait_lower_ms=max(0, start - after) * 1000,
                after_release_wait_upper_ms=(start - before) * 1000,
            )
            if job["completed_at"]:
                elapsed = (utc_seconds(job["completed_at"]) - start) * 1000
                require(elapsed >= 0, "Completion precedes worker start")
                row["start_to_db_completion_ms"] = elapsed
        rows.append(row)
    return rows


def summarize(requests, jobs):
    summary = {}
    for owner in SCENARIO["owners"]:
        offered = [r for r in requests if r["owner"] == owner]
        accepted = [r for r in offered if r["status"] == 201]
        rejected = [r for r in offered if r["status"] == 429]
        own = [job for job in jobs if job["owner"] == owner]
        data = {
            "offered": len(offered),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "status_counts": dict(Counter(str(r["status"]) for r in offered)),
            "transport_errors": sum(
                r["status"] is None and not r.get("dispatch_censored", False) for r in offered
            ),
            "http_attempted": sum(not r.get("dispatch_censored", False) for r in offered),
            "dispatch_censored": sum(r.get("dispatch_censored", False) for r in offered),
            "admission_all": distribution(
                [
                    r["admission_latency_ms"]
                    for r in offered
                    if r["admission_latency_ms"] is not None
                ]
            ),
            "admission_accepted": distribution([r["admission_latency_ms"] for r in accepted]),
            "admission_rejected": distribution([r["admission_latency_ms"] for r in rejected]),
            "started": sum(not j["start_censored"] for j in own),
            "start_censored": sum(j["start_censored"] for j in own),
            "succeeded": sum(j["state"] == "succeeded" for j in own),
            "failed": sum(j["state"] == "failed" for j in own),
            "completion_censored": sum(j["completion_censored"] for j in own),
        }
        for key in (
            "induced_wait_lower_ms",
            "total_queue_wait_ms",
            "after_release_wait_lower_ms",
            "after_release_wait_upper_ms",
            "start_to_db_completion_ms",
        ):
            data[key] = distribution([j[key] for j in own if key in j])
        data["wait_percentiles_population"] = (
            "observed jobs only; censored jobs explicitly counted above, not imputed or called zero"
        )
        summary[owner] = data
    return summary


def write(path, data):
    # Public evidence hashes refer to canonical LF bytes on Windows and Git clones.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())
    temporary.replace(path)


def observe_drain(stack, deadline, release_started, record):
    """Preserve the last DB snapshot on timeout; late terminal rows do not pass."""
    drain_deadline = min(release_started + SCENARIO["drain_deadline_seconds"], deadline[0])
    global_phase_deadline = deadline[0]
    record["final_database"] = record["paused_database"]
    deadline[0] = drain_deadline
    try:
        while True:
            if time.monotonic() >= drain_deadline:
                record["drain_deadline_reached"] = True
                break
            record["final_database"] = database(stack)
            if time.monotonic() >= drain_deadline:
                record["drain_deadline_reached"] = True
                break
            if all(j["state"] in ("succeeded", "failed") for j in record["final_database"]["jobs"]):
                break
            time.sleep(min(0.8, max(0, drain_deadline - time.monotonic())))
    except (TimeoutError, subprocess.TimeoutExpired):
        record["drain_deadline_reached"] = True
    finally:
        deadline[0] = global_phase_deadline
    record["drain_observed_seconds"] = time.monotonic() - release_started


def repetition(number, images, deadline, hard_deadline, started, directory):
    project = "pf-containerops-test-" + uuid.uuid4().hex[:8]
    stack = MeasuredStack(project, images, deadline)
    record = {
        "number": number,
        "project": project,
        "started_at": ops.now(),
        "status": "in_progress",
        "phase": "prepare",
        "requests": [],
        "jobs": [],
    }
    path = directory / f"repetition-{number}.json"
    cleanup_needed = False

    def save(phase=None):
        if phase:
            record["phase"] = phase
            print(f"repetition={number} project={project} phase={phase}", flush=True)
        write(path, record)

    try:
        stack.assert_fresh()
        require(not stack.state_path.exists(), "Runtime directory already used")
        cleanup_needed = True
        save("starting")
        stack.start()
        record["resources"] = resource_identity(stack)
        record["initial_database"] = database(stack)
        require(not record["initial_database"]["jobs"], "Historical jobs must be zero")
        require(
            record["initial_database"]["owner_limit"] == 20
            and record["initial_database"]["global_limit"] == 100,
            "Quota constants changed",
        )
        owned_service(stack, "worker")
        stack.compose("stop", "--timeout", "25", "worker")
        require(not owned_service(stack, "worker")["State"]["Running"], "Worker not stopped")
        save("admission_worker_stopped")
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(request_observation, stack, owner, i, started, deadline)
                for i in range(1, 25)
                for owner in SCENARIO["owners"]
            ]
            for future in as_completed(futures):
                record["requests"].append(future.result())
                save()
        record["paused_database"] = database(stack)
        require(not record["paused_database"]["admission_paused"], "Admission was paused")
        require(
            all(
                j["state"] == "queued" and j["attempts"] == 0
                for j in record["paused_database"]["jobs"]
            ),
            "Worker consumed paused queue",
        )
        # Validate after archiving every request, including any unexpected response.
        for owner in SCENARIO["owners"]:
            own = [r for r in record["requests"] if r["owner"] == owner]
            require(
                Counter(r["status"] for r in own) == {201: 20, 429: 4},
                "Admission outcomes differ from fixed owner quota oracle",
            )
            require(
                all(
                    r["response"].get("detail") == "Limite de trabalhos pendentes do proprietário"
                    for r in own
                    if r["status"] == 429
                ),
                "Rejection reason differs",
            )
        require(len(record["paused_database"]["jobs"]) == 40, "Expected 40 queued jobs")
        record["release"] = {
            "before_db_utc": database(stack)["observed_at"],
            "command_started_at": ops.now(),
        }
        release_started = time.monotonic()
        stack.compose("start", "worker")
        record["release"]["command_finished_at"] = ops.now()
        record["release"]["after_db_utc"] = database(stack)["observed_at"]
        record["release"]["bracket_width_ms"] = 1000 * (
            utc_seconds(record["release"]["after_db_utc"])
            - utc_seconds(record["release"]["before_db_utc"])
        )
        save("draining")
        observe_drain(stack, deadline, release_started, record)
        record["worker_lifecycle"] = lifecycle_logs(stack)
        record["censoring_boundary_db_utc"] = record["final_database"]["observed_at"]
        record["jobs"] = correlate(
            record["requests"],
            record["final_database"],
            record["worker_lifecycle"],
            record["release"],
        )
        record["summary_by_owner"] = summarize(record["requests"], record["jobs"])
        save("validated_observations")
        expected_hash = hashlib.sha256(SCENARIO["text"].encode()).hexdigest()
        require(
            not record.get("drain_deadline_reached"), "Drain deadline reached; censored rows kept"
        )
        require(
            all(
                j["state"] == "succeeded"
                and j["word_count"] == 3
                and j["checksum"] == expected_hash
                and not j["start_censored"]
                for j in record["jobs"]
            ),
            "Completion/result/log oracle failed",
        )
        record["status"] = "passed"
    except BaseException as error:
        record.update(status="failed", error_category=type(error).__name__, error=str(error))
        raise
    finally:
        record["measurement_finished_at"] = ops.now()
        previous_deadline = deadline[0]
        deadline[0] = hard_deadline
        try:
            if cleanup_needed:
                save("cleanup")
                item = (
                    owned_service(stack, "worker")
                    if stack.compose("ps", "-a", "-q", "worker", capture=True).stdout.strip()
                    else None
                )
                require(
                    not item or not item["State"].get("Paused"),
                    "Unexpected frozen worker state; do not hide external mutation",
                )
                # Assert ownership of all extant project resources before destructive cleanup.
                ids = ops.run(
                    [
                        "docker",
                        "ps",
                        "-a",
                        "-q",
                        "--filter",
                        "label=com.docker.compose.project=" + project,
                    ],
                    capture=True,
                )
                for cid in ids.stdout.split():
                    labels = json.loads(ops.run(["docker", "inspect", cid], capture=True).stdout)[
                        0
                    ]["Config"]["Labels"]
                    require(
                        labels.get("com.docker.compose.project") == project,
                        "Cleanup resource ownership mismatch",
                    )
                stack.destroy_test()
                stack.assert_fresh()
                record["cleanup"] = {
                    "status": "passed",
                    "remaining_owned_resources": 0,
                    "completed_at": ops.now(),
                }
        except BaseException as error:
            record.update(
                status="failed",
                cleanup={"status": "failed", "error_category": type(error).__name__},
            )
            raise
        finally:
            deadline[0] = previous_deadline
            record["completed_at"] = ops.now()
            save("complete")
    return record


def measure(image):
    started = time.monotonic()
    hard_deadline = started + 600
    deadline = [hard_deadline - 60]
    run_id = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z") + "-" + uuid.uuid4().hex[:8]
    directory = ops.EVIDENCE / "admission-measurement" / run_id
    directory.mkdir(parents=True, exist_ok=False)
    record = {
        "run_id": run_id,
        "started_at": ops.now(),
        "status": "in_progress",
        "scenario": SCENARIO,
        "repetitions": [],
        "command": ["python", "scripts/measure_admission.py", "--image", image],
        "limits": [
            "Synthetic local demo; 0.5 seconds is deliberate delay, not CPU service capacity.",
            "Two owners can hold at most 40 pending jobs; global quota 100 is not saturated or tested.",
            "No bound on waiting time, production throughput, growing-history fairness or cross-host reliability is claimed.",
            "HTTP latency uses host monotonic clock. DB and worker timestamps use the same Docker host wall clock.",
            "Worker release is bracketed by two DB clock observations; post-release wait is a lower/upper interval.",
            "Post-release wait includes worker process startup; graceful stop avoids freezing an admission lock.",
            "Queue distributions describe accepted jobs with observed starts; HTTP distributions include all offered requests and failures.",
            "The 600s budget caps subprocess/socket operations and is checked for success; OS stalls and socket reads are not a real-time guarantee.",
        ],
    }
    try:
        with ops.operation_lock(), bounded_commands(deadline):
            write(directory / "manifest.json", record)
            record["containers_before"] = containers()
            require(not record["containers_before"], "Other containers active; defer measurement")
            record["source_before"] = source_identity()
            images = {
                "app": ops.image_id(image),
                "db": ops.image_id("containerops-database:local"),
                "proxy": ops.image_id("containerops-proxy:local"),
            }
            record["images"] = images
            record["image_source_check"] = proof.image_sources(images["app"])
            record["environment"] = {
                "python": platform.python_version(),
                "host": platform.platform(),
                "compose": ops.run(
                    ["docker", "compose", "version", "--short"], capture=True
                ).stdout.strip(),
                "docker": json.loads(
                    ops.run(["docker", "version", "--format", "{{json .}}"], capture=True).stdout
                ),
            }
            write(directory / "manifest.json", record)
            for number in range(1, 4):
                require(not containers(), "Another stack became active; defer next repetition")
                result = repetition(number, images, deadline, hard_deadline, started, directory)
                record["repetitions"].append(
                    {
                        "number": number,
                        "status": result["status"],
                        "path": f"repetition-{number}.json",
                        "sha256": hashlib.sha256(
                            (directory / f"repetition-{number}.json").read_bytes()
                        ).hexdigest(),
                    }
                )
                write(directory / "manifest.json", record)
            record["source_after"] = source_identity()
            record["source_unchanged"] = (
                record["source_before"]["files"] == record["source_after"]["files"]
            )
            record["git_revision_unchanged"] = (
                record["source_before"]["revision"] == record["source_after"]["revision"]
            )
            require(record["source_unchanged"], "Operational sources changed during measurement")
            record["containers_after"] = containers()
            require(not record["containers_after"], "Concurrent container activity at end")
            require(time.monotonic() < hard_deadline, "Global elapsed budget exceeded")
            record["status"] = "passed"
    except BaseException as error:
        record.update(status="failed", error_category=type(error).__name__, error=str(error))
        raise
    finally:
        record.update(completed_at=ops.now(), elapsed_seconds=time.monotonic() - started)
        write(directory / "manifest.json", record)
        print(f"evidence={directory} status={record['status']}", flush=True)
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="containerops-app:1.0.0")
    args = parser.parse_args()
    measure(args.image)
