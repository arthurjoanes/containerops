import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ops
import proof


class ProofEvidenceTests(unittest.TestCase):
    def test_source_read_failure_invalidates_old_success_before_any_docker_command(self):
        with tempfile.TemporaryDirectory(prefix="containerops-proof-") as temporary:
            directory = Path(temporary)
            with (
                patch.object(ops, "EVIDENCE", directory / "evidence"),
                patch.object(ops, "RUNTIME", directory / "runtime"),
                patch.object(proof, "source_manifest", side_effect=OSError("source unavailable")),
                patch.object(proof, "inventory") as inventory,
                patch.dict(proof.os.environ),
            ):
                path = ops.EVIDENCE / "problem-proof-latest.json"
                ops.write_json(path, {"status": "passed", "run_id": "old"})
                with self.assertRaises(OSError):
                    proof.prove()
                manifest = ops.read_json(path)
                self.assertNotEqual(manifest["run_id"], "old")
                self.assertEqual(manifest["status"], "failed")
                inventory.assert_not_called()

    def test_interruption_is_failed_not_false_success_and_does_not_start_stack(self):
        with tempfile.TemporaryDirectory(prefix="containerops-proof-") as temporary:
            directory = Path(temporary)
            with (
                patch.object(ops, "EVIDENCE", directory / "evidence"),
                patch.object(ops, "RUNTIME", directory / "runtime"),
                patch.object(proof, "source_manifest", return_value={"sha256": "fixture"}),
                patch.object(proof, "inventory", side_effect=KeyboardInterrupt),
                patch.object(ops, "Stack") as stack,
                patch.dict(proof.os.environ),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    proof.prove()
                manifest = ops.read_json(directory / "evidence" / "problem-proof-latest.json")
                self.assertEqual(manifest["status"], "failed")
                self.assertEqual(manifest["error_category"], "KeyboardInterrupt")
                self.assertIsNotNone(manifest["completed_at"])
                stack.assert_not_called()

    def test_archive_remains_bound_to_attempt_when_alias_changes(self):
        with tempfile.TemporaryDirectory(prefix="containerops-proof-") as temporary:
            directory = Path(temporary)
            with (
                patch.object(ops, "EVIDENCE", directory),
                patch.object(proof, "source_manifest", return_value={"sha256": "fixture"}),
            ):
                attempt = proof.ProofRun()
                ops.write_json(
                    directory / "backup.json", {"source_project": attempt.data["project"]}
                )
                attempt.archive("backup")
                ops.write_json(directory / "backup.json", {"source_project": "later"})
                reference = attempt.data["evidence"][0]
                archived = directory / reference["path"]
                self.assertEqual(
                    reference["sha256"], hashlib.sha256(archived.read_bytes()).hexdigest()
                )
                self.assertEqual(ops.read_json(archived)["source_project"], attempt.data["project"])

    def test_missing_or_changed_job_is_not_preservation(self):
        before = {"jobs": [{"id": "known", "checksum": "expected"}]}
        for after in ({"jobs": []}, {"jobs": [{"id": "known", "checksum": "changed"}]}):
            with self.subTest(after=after), self.assertRaisesRegex(RuntimeError, "desapareceu"):
                proof.require_jobs(before, after)
        proof.require_jobs(before, {"jobs": [*before["jobs"], {"id": "new"}]})

    def test_sources_mismatch_rejects_image_even_when_tag_exists(self):
        with patch.object(ops, "run", return_value=Mock(stdout="{}")):
            with self.assertRaisesRegex(RuntimeError, "código atual"):
                proof.image_sources("sha256:stale")


if __name__ == "__main__":
    unittest.main()
