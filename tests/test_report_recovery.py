import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from report_evidence import parse_record
from report_recovery import restore_result


def completed():
    return {
        "status": "passed",
        "cleanup_succeeded": True,
        "checksum_verified": True,
        "snapshot_equal": True,
        "new_job": {
            "state": "succeeded",
            "attempts": 1,
            "result": {"word_count": 4, "checksum": "a" * 64},
        },
    }


class ReportRecoveryTests(unittest.TestCase):
    def test_no_attempt_cannot_claim_restore_from_a_backup(self):
        result = restore_result({})
        self.assertEqual(result.state, "missing")
        self.assertIn("Sem restauração", result.label)

    def test_pass_requires_every_fact_and_final_status(self):
        self.assertEqual(restore_result(completed()).state, "pass")
        for field in (
            "status",
            "cleanup_succeeded",
            "checksum_verified",
            "snapshot_equal",
            "new_job",
        ):
            record = completed()
            del record[field]
            with self.subTest(field=field):
                self.assertEqual(restore_result(record).state, "partial")

    def test_cleanup_failure_overrides_successful_snapshot_and_new_job(self):
        for status in ("passed", "failed", "in_progress"):
            record = {**completed(), "status": status, "cleanup_succeeded": False}
            with self.subTest(status=status):
                result = restore_result(record)
                self.assertEqual(result.state, "fail")
                self.assertIn("limpeza", result.explanation)

    def test_failed_status_overrides_other_success_facts(self):
        record = {**completed(), "status": "failed"}
        self.assertEqual(restore_result(record).state, "fail")

    def test_in_progress_is_not_a_completed_restore_even_with_all_observations(self):
        record = {**completed(), "status": "in_progress"}
        result = restore_result(record)
        self.assertEqual(result.state, "running")
        self.assertIn("em andamento", result.label)

    def test_legacy_success_keeps_facts_and_names_missing_status_and_cleanup(self):
        record = completed()
        del record["status"]
        del record["cleanup_succeeded"]
        parsed = parse_record(json.dumps(record), "restore")
        result = restore_result(parsed)
        self.assertEqual(result.state, "partial")
        self.assertIn("status final nem limpeza", result.explanation)
        self.assertTrue(parsed["checksum_verified"])
        self.assertTrue(parsed["snapshot_equal"])

    def test_single_missing_legacy_fact_has_specific_explanation(self):
        for field, explanation in (("status", "status final"), ("cleanup_succeeded", "limpeza")):
            record = completed()
            del record[field]
            with self.subTest(field=field):
                result = restore_result(record)
                self.assertEqual(result.state, "partial")
                self.assertIn(explanation, result.explanation)

    def test_queued_or_running_new_job_is_partial(self):
        for state in ("queued", "running"):
            record = completed()
            record["new_job"] = {"state": state, "attempts": 0, "result": None}
            with self.subTest(state=state):
                self.assertEqual(restore_result(record).state, "partial")

    def test_incomplete_successful_job_is_partial(self):
        for job in (
            {"state": "succeeded", "attempts": 1, "result": None},
            {
                "state": "succeeded",
                "attempts": 0,
                "result": {"word_count": 4, "checksum": "a" * 64},
            },
            {"state": "succeeded", "attempts": 1, "result": {"word_count": 4, "checksum": "bad"}},
        ):
            record = {**completed(), "new_job": job}
            with self.subTest(job=job):
                self.assertEqual(restore_result(record).state, "partial")

    def test_failed_checkpoint_or_job_cannot_be_approved(self):
        for change in (
            {"checksum_verified": False},
            {"snapshot_equal": False},
            {"new_job": {"state": "failed", "attempts": 3, "result": None}},
        ):
            with self.subTest(change=change):
                self.assertEqual(restore_result({**completed(), **change}).state, "fail")

    def test_numeric_one_is_not_boolean_success(self):
        for field in ("cleanup_succeeded", "checksum_verified", "snapshot_equal"):
            record = {**completed(), field: 1}
            with self.subTest(field=field):
                self.assertEqual(restore_result(record).state, "invalid")
                with self.assertRaisesRegex(ValueError, "booleano"):
                    parse_record(json.dumps(record), "restore")

    def test_parser_rejects_invalid_cleanup_and_explicit_status(self):
        for value in (None, 0, 1, "true", [], {}):
            with (
                self.subTest(cleanup=value),
                self.assertRaisesRegex(ValueError, "cleanup_succeeded"),
            ):
                parse_record(json.dumps({**completed(), "cleanup_succeeded": value}), "restore")
        for value in (None, True, 1, "", "success", "queued", [], {}):
            with self.subTest(status=value), self.assertRaisesRegex(ValueError, "status"):
                parse_record(json.dumps({**completed(), "status": value}), "restore")


if __name__ == "__main__":
    unittest.main()
