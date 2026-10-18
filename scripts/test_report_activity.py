from __future__ import annotations

import unittest
from datetime import UTC, datetime

from report_activity import activity_data, activity_panel


class ActivityTests(unittest.TestCase):
    def test_latest_observation_deduplicates_job_within_project(self):
        records = {
            "demo": {
                "recorded_at": "2026-10-11T23:59:59Z",
                "project": "one",
                "job": {"id": "same", "state": "queued"},
            },
            "journey": {
                "recorded_at": "2026-10-12T00:30:00+02:00",
                "project": "one",
                "job": {"id": "same", "state": "succeeded"},
            },
            "restore": {
                "recorded_at": "2026-10-12T01:00:00Z",
                "project": "two",
                "new_job": {"id": "same", "state": "failed"},
            },
        }
        data = activity_data(records)
        self.assertEqual(len(data["jobs"]), 2)
        self.assertEqual(data["jobs"][0]["state"], "queued")
        self.assertEqual(data["jobs"][0]["day"], "2026-10-11")
        self.assertEqual(data["jobs"][1]["state"], "failed")

    def test_unknown_states_missing_ids_and_undated_records_are_not_counted(self):
        records = {
            "demo": {"job": {"id": "one", "state": "succeeded"}},
            "journey": {
                "recorded_at": "2026-10-12T00:00:00Z",
                "job": {"id": "two", "state": "invented"},
            },
            "restore": {"recorded_at": "2026-10-12T00:00:00Z", "new_job": {"state": "succeeded"}},
        }
        self.assertEqual(activity_data(records)["jobs"], [])
        self.assertIn("Nenhum job com estado e data válidos", activity_panel(records))

    def test_malformed_state_cannot_break_the_report(self):
        for state in ([], {}, True, None):
            with self.subTest(state=state):
                self.assertEqual(
                    activity_data(
                        {
                            "demo": {
                                "recorded_at": "2026-10-12T00:00:00Z",
                                "job": {"id": "a", "state": state},
                            }
                        }
                    )["jobs"],
                    [],
                )

    def test_durations_preserve_zero_and_reject_boolean_nonfinite_or_negative(self):
        records = {
            "backup": {"recorded_at": "2026-10-12T00:00:00Z", "elapsed_seconds": 0},
            "restore": {"recorded_at": "2026-10-12T00:00:00Z", "recovery_seconds": True},
            "release": {"recorded_at": "2026-10-12T00:00:00Z", "maintenance_seconds": -1},
            "rollback": {
                "recorded_at": "2026-10-12T00:00:00Z",
                "maintenance_seconds": float("inf"),
            },
        }
        self.assertEqual([row["seconds"] for row in activity_data(records)["operations"]], [0])

    def test_only_known_job_slots_are_used_and_html_is_escaped(self):
        document = activity_panel(
            {
                "demo": {
                    "recorded_at": "2026-10-12T00:00:00Z",
                    "project": '<script>"',
                    "job": {"id": "<img>", "state": "running"},
                    "unrelated": {"id": "not-a-job", "state": "failed"},
                },
            }
        )
        self.assertNotIn("<script>", document)
        self.assertNotIn("not-a-job", document)
        self.assertIn("&lt;img&gt;", document)
        self.assertIn('data-job-day="2026-10-12"', document)
        self.assertIn('min="2026-10-12"', document)
        self.assertIn('max="2026-10-12"', document)

    def test_generation_does_not_use_wall_clock_for_period(self):
        records = {
            "demo": {
                "recorded_at": "2020-01-02T23:30:00-02:00",
                "job": {"id": "a", "state": "succeeded"},
            }
        }
        data = activity_data(records)
        self.assertEqual(data["last_day"], "2020-01-03")
        self.assertNotEqual(data["last_day"], datetime.now(UTC).date().isoformat())


if __name__ == "__main__":
    unittest.main()
