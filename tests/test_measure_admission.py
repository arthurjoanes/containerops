"""Measurement integrity checks without a Docker daemon or application mutations."""

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import measure_admission as measurement
import ops


class MeasurementIntegrityTests(unittest.TestCase):
    def test_nearest_rank_uses_observations_not_interpolation_or_zero_for_empty(self):
        self.assertIsNone(measurement.nearest_rank([]))
        self.assertEqual(measurement.nearest_rank([999, *range(1, 20)]), 19)
        self.assertEqual(measurement.nearest_rank([4, 2, 8, 6], 50), 4)
        with self.assertRaises(ValueError):
            measurement.nearest_rank([1], 0)

    def test_rejections_and_transport_failures_remain_in_offered_latency_denominator(self):
        requests = [
            {"owner": "alice", "status": status, "admission_latency_ms": latency}
            for status, latency in [(201, 1), (429, 50), (None, 8000)]
        ]
        result = measurement.summarize(requests, [])
        self.assertEqual(result["alice"]["offered"], 3)
        self.assertEqual(result["alice"]["transport_errors"], 1)
        self.assertEqual(result["alice"]["admission_all"]["p95"], 8000)
        self.assertEqual(result["alice"]["admission_rejected"]["n"], 1)
        self.assertIsNone(result["bob"]["admission_all"]["p95"])

    def inputs(self):
        requests = [
            {
                "owner": "alice",
                "sequence": 1,
                "status": 201,
                "response": {"id": "job1"},
                "admission_latency_ms": 2,
            }
        ]
        snapshot = {
            "observed_at": "2026-09-22T00:00:07+00:00",
            "jobs": [
                {
                    "id": "job1",
                    "owner": "alice",
                    "state": "succeeded",
                    "attempts": 1,
                    "created_at": "2026-09-22T00:00:00+00:00",
                    "completed_at": "2026-09-22T00:00:06+00:00",
                }
            ],
        }
        logs = [
            {
                "event": "job_started",
                "job_id": "job1",
                "attempt": 1,
                "timestamp": "2026-09-22T00:00:05+00:00",
            }
        ]
        release = {
            "before_db_utc": "2026-09-22T00:00:03+00:00",
            "after_db_utc": "2026-09-22T00:00:04+00:00",
        }
        return requests, snapshot, logs, release

    def test_release_bracket_separates_artificial_wait_from_queue_wait(self):
        rows = measurement.correlate(*self.inputs())
        self.assertEqual(rows[0]["induced_wait_lower_ms"], 3000)
        self.assertEqual(rows[0]["total_queue_wait_ms"], 5000)
        self.assertEqual(rows[0]["after_release_wait_lower_ms"], 1000)
        self.assertEqual(rows[0]["after_release_wait_upper_ms"], 2000)
        self.assertEqual(rows[0]["start_to_db_completion_ms"], 1000)

    def test_missing_start_and_completion_are_censored_not_assigned_zero(self):
        requests, snapshot, _, release = self.inputs()
        snapshot["jobs"][0].update(state="queued", attempts=0, completed_at=None)
        rows = measurement.correlate(requests, snapshot, [], release)
        stats = measurement.summarize(requests, rows)["alice"]
        self.assertEqual(stats["start_censored"], 1)
        self.assertEqual(stats["completion_censored"], 1)
        self.assertEqual(stats["accepted"], 1)
        self.assertIsNone(stats["total_queue_wait_ms"]["p95"])

    def test_start_after_snapshot_is_preserved_as_raw_but_censored_in_summary(self):
        requests, snapshot, logs, release = self.inputs()
        snapshot["jobs"][0].update(state="queued", attempts=0, completed_at=None)
        logs[0]["timestamp"] = "2026-09-22T00:00:08+00:00"
        rows = measurement.correlate(requests, snapshot, logs, release)
        self.assertTrue(rows[0]["start_censored"])
        self.assertEqual(len(logs), 1)

    def test_expired_global_deadline_never_dispatches_queued_http(self):
        stack = Mock()
        with patch.object(measurement.time, "monotonic", return_value=20):
            result = measurement.request_observation(stack, "alice", 1, 0, [10])
        stack.request.assert_not_called()
        self.assertTrue(result["dispatch_censored"])
        self.assertIsNone(result["admission_latency_ms"])

    def test_wrong_owner_or_unoffered_job_cannot_be_correlated(self):
        requests, snapshot, logs, release = self.inputs()
        snapshot["jobs"][0]["owner"] = "bob"
        with self.assertRaisesRegex(RuntimeError, "owner"):
            measurement.correlate(requests, snapshot, logs, release)
        snapshot["jobs"][0]["id"] = "unoffered"
        with self.assertRaisesRegex(RuntimeError, "differ"):
            measurement.correlate(requests, snapshot, logs, release)

    def test_job_started_while_worker_should_be_paused_invalidates_measurement(self):
        requests, snapshot, logs, release = self.inputs()
        logs[0]["timestamp"] = "2026-09-22T00:00:02+00:00"
        with self.assertRaisesRegex(RuntimeError, "pause"):
            measurement.correlate(requests, snapshot, logs, release)

    def test_duplicate_starts_do_not_look_like_independent_fast_jobs(self):
        requests, snapshot, logs, release = self.inputs()
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            measurement.correlate(requests, snapshot, logs * 2, release)

    def test_every_delegated_command_is_bounded_and_function_restored(self):
        original = Mock(return_value="result")
        with (
            patch.object(ops, "run", original),
            patch.object(measurement.time, "monotonic", return_value=100),
        ):
            with measurement.bounded_commands([110]):
                self.assertEqual(ops.run(["safe"], timeout=300), "result")
                self.assertEqual(original.call_args.kwargs["timeout"], 10)
            self.assertIs(ops.run, original)
            with measurement.bounded_commands([99]):
                with self.assertRaises(TimeoutError):
                    ops.run(["must-not-start"])
            self.assertEqual(original.call_count, 1)

    def test_cross_project_service_never_passes_ownership_check(self):
        stack = Mock(project="pf-containerops-test-12345678")
        stack.inspect.return_value = {
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "pf-containerops",
                    "com.docker.compose.service": "worker",
                }
            }
        }
        with self.assertRaisesRegex(RuntimeError, "ownership"):
            measurement.owned_service(stack, "worker")

    def test_http_timeout_is_capped_by_remaining_global_budget(self):
        opener = Mock(return_value="response")
        with (
            patch.object(ops.urllib.request, "urlopen", opener),
            patch.object(measurement.time, "monotonic", return_value=100),
        ):
            with measurement.bounded_commands([101]):
                self.assertEqual(
                    ops.urllib.request.urlopen("http://127.0.0.1", timeout=8), "response"
                )
                self.assertEqual(opener.call_args.kwargs["timeout"], 1)
            self.assertIs(ops.urllib.request.urlopen, opener)

    def test_late_completed_snapshot_is_failure_not_drain_within_budget(self):
        record = {"paused_database": {"jobs": [{"state": "queued"}]}}
        deadline = [600]

        def query(_stack):
            self.assertEqual(deadline[0], 90)
            return {"jobs": [{"state": "succeeded"}]}

        with (
            patch.object(measurement, "database", side_effect=query),
            patch.object(measurement.time, "monotonic", side_effect=[89, 91, 91]),
        ):
            measurement.observe_drain(Mock(), deadline, 0, record)
        self.assertTrue(record["drain_deadline_reached"])
        self.assertEqual(record["final_database"]["jobs"][0]["state"], "succeeded")
        self.assertEqual(deadline[0], 600)

    def test_timeout_preserves_last_snapshot_for_censorship_and_restores_global_budget(self):
        record = {"paused_database": {"jobs": [{"state": "queued"}]}}
        deadline = [600]
        with (
            patch.object(measurement, "database", side_effect=TimeoutError),
            patch.object(measurement.time, "monotonic", side_effect=[89, 90]),
        ):
            measurement.observe_drain(Mock(), deadline, 0, record)
        self.assertTrue(record["drain_deadline_reached"])
        self.assertEqual(record["final_database"], record["paused_database"])
        self.assertEqual(deadline[0], 600)


if __name__ == "__main__":
    unittest.main()
