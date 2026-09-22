import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import capture_operations
import ops
import proof
import report_evidence


class OperationsProofTests(unittest.TestCase):
    def test_evidence_json_uses_lf_bytes_on_every_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "record.json"
            ops.write_json(path, {"status": "passed", "detail": "ação"})
            self.assertNotIn(b"\r", path.read_bytes())
            self.assertTrue(path.read_bytes().endswith(b"\n"))
            self.assertEqual(ops.read_json(path)["detail"], "ação")

    def backup(self, root):
        directory = root / "backups" / "known"
        directory.mkdir(parents=True)
        dump = b"synthetic-pg-custom-dump"
        (directory / "containerops.dump").write_bytes(dump)
        ops.write_json(
            directory / "metadata.json",
            {
                "created_at": "2026-01-01T00:00:00+00:00",
                "cutoff_interval": {
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "completed_at": "2026-01-01T00:00:01+00:00",
                },
                "sha256": hashlib.sha256(dump).hexdigest(),
                "image": "sha256:source",
                "schema_version": 2,
                "snapshot": {"schema_version": 2, "admission_paused": True, "jobs": []},
            },
        )
        return directory

    def test_corrupted_dump_is_rejected_before_target_or_docker(self):
        with tempfile.TemporaryDirectory(prefix="containerops-restore-") as temporary:
            root = Path(temporary)
            directory = self.backup(root)
            (directory / "containerops.dump").write_bytes(b"changed")
            with (
                patch.object(ops, "RUNTIME", root),
                patch.object(ops, "EVIDENCE", root / "evidence"),
                patch.object(ops, "Stack") as stack,
                patch.object(ops, "run") as run,
            ):
                with self.assertRaisesRegex(RuntimeError, "Checksum"):
                    ops.restore_test(directory)
            stack.assert_not_called()
            run.assert_not_called()
            record = ops.read_json(root / "evidence/restore.json")
            self.assertEqual(record["status"], "failed")
            self.assertEqual(record["phases"][0]["name"], "checksum")
            self.assertEqual(record["phases"][0]["status"], "failed")
            self.assertNotIn("new_job", record)
            self.assertNotIn("project", record)

    def test_restore_outside_reserved_root_is_refused_before_reading_dump(self):
        with tempfile.TemporaryDirectory(prefix="containerops-restore-") as temporary:
            root = Path(temporary)
            with (
                patch.object(ops, "RUNTIME", root / "runtime"),
                patch.object(ops, "EVIDENCE", root / "evidence"),
                patch.object(ops, "Stack") as stack,
            ):
                with self.assertRaisesRegex(RuntimeError, "fora do runtime"):
                    ops.restore_test(root / "outside")
            stack.assert_not_called()

    def test_protected_copy_has_same_bytes_and_independent_files(self):
        with tempfile.TemporaryDirectory(prefix="containerops-copy-") as temporary:
            root = Path(temporary)
            directory = self.backup(root)
            with patch.object(ops, "RUNTIME", root):
                copied, record = proof.protected_backup_copy(directory)
            self.assertNotEqual(copied, directory)
            for name, digest in record["sha256"].items():
                self.assertEqual(hashlib.sha256((copied / name).read_bytes()).hexdigest(), digest)
                self.assertEqual((copied / name).read_bytes(), (directory / name).read_bytes())
            original = (directory / "containerops.dump").read_bytes()
            (copied / "containerops.dump").write_bytes(b"damaged only copy")
            self.assertEqual((directory / "containerops.dump").read_bytes(), original)
            self.assertTrue(record["same_host"])

    def test_copy_rejects_corrupted_origin_without_creating_destination(self):
        with tempfile.TemporaryDirectory(prefix="containerops-copy-") as temporary:
            root = Path(temporary)
            directory = self.backup(root)
            (directory / "containerops.dump").write_bytes(b"changed")
            with patch.object(ops, "RUNTIME", root):
                with self.assertRaisesRegex(RuntimeError, "Checksum"):
                    proof.protected_backup_copy(directory)
            self.assertEqual(list((root / "backups").iterdir()), [directory])

    def test_cleanup_failure_preserves_observations_but_cannot_publish_success(self):
        self.exercise_restore(cleanup_failure=True)

    def test_restore_success_is_published_after_cleanup_and_has_phase_timings(self):
        self.exercise_restore(cleanup_failure=False)

    def test_legacy_backup_without_cutoff_remains_restorable_and_readable(self):
        self.exercise_restore(cleanup_failure=False, legacy=True)

    def exercise_restore(self, *, cleanup_failure, legacy=False):
        with tempfile.TemporaryDirectory(prefix="containerops-restore-") as temporary:
            root = Path(temporary)
            directory = self.backup(root)
            if legacy:
                metadata = ops.read_json(directory / "metadata.json")
                del metadata["cutoff_interval"]
                ops.write_json(directory / "metadata.json", metadata)
            stack_directory = root / "environments/restore"
            stack_directory.mkdir(parents=True)
            stack = Mock(
                project="pf-containerops-restore-12345678",
                directory=stack_directory,
                assert_fresh=Mock(),
            )
            stack.snapshot.return_value = ops.read_json(directory / "metadata.json")["snapshot"]
            stack.cid.return_value = "restore-helper"
            stack.job.return_value = {"id": "new-job"}
            stack.completed.return_value = {
                "id": "new-job",
                "state": "succeeded",
                "attempts": 1,
                "result": {
                    "word_count": 4,
                    "checksum": hashlib.sha256(b"Backup restaurado com sucesso").hexdigest(),
                },
            }

            def destroy():
                record = ops.read_json(root / "evidence/restore.json")
                self.assertEqual(record["status"], "in_progress")
                self.assertNotIn("new_job", record)
                if cleanup_failure:
                    raise RuntimeError("cleanup failed")

            stack.destroy_test.side_effect = destroy
            with (
                patch.object(ops, "RUNTIME", root),
                patch.object(ops, "EVIDENCE", root / "evidence"),
                patch.object(ops, "Stack", return_value=stack),
                patch.object(ops, "setup_secrets"),
                patch.object(ops, "run", return_value=Mock(stdout='[{"State":{"ExitCode":0}}]')),
            ):
                if cleanup_failure:
                    with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                        ops.restore_test(directory)
                else:
                    ops.restore_test(directory)
            record = ops.read_json(root / "evidence/restore.json")
            self.assertEqual(record["status"], "failed" if cleanup_failure else "passed")
            self.assertEqual(record["cleanup_succeeded"], not cleanup_failure)
            if cleanup_failure:
                self.assertNotIn("snapshot_equal", record)
                self.assertNotIn("new_job", record)
                self.assertEqual(record["observations"]["new_job"]["id"], "new-job")
            else:
                self.assertEqual(record["new_job"]["id"], "new-job")
                self.assertGreaterEqual(record["total_seconds"], record["recovery_seconds"])
                self.assertEqual(record["phases"][-1]["name"], "cleanup-check")
                self.assertTrue(all(step["status"] == "passed" for step in record["phases"]))
                # The unchanged report parser must accept actual operational records.
                parsed, errors = report_evidence.read_records(root / "evidence")
                self.assertFalse(errors)
                self.assertIn("restore", parsed)

    def capture_fixture(self, root):
        directory = root / "problem-proof" / "capture-proof"
        directory.mkdir(parents=True)
        records = {
            "restore": {"status": "passed", "cleanup_succeeded": True},
            "source-preservation": {"equal": True},
            "rollback": {"injected_smoke_failure": True},
        }
        references = []
        for name, record in records.items():
            path = directory / (name + ".json")
            ops.write_json(path, record)
            references.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        manifest = {
            "status": "passed",
            "scenario": "operations",
            "run_id": directory.name,
            "evidence": references,
            "steps": [{"name": "cleanup-source", "status": "passed"}],
        }
        path = directory / "manifest.json"
        ops.write_json(path, manifest)
        return path

    def test_capture_rejects_tampered_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.capture_fixture(root)
            with patch.object(ops, "EVIDENCE", root):
                capture_operations.validated_records(manifest)
                (manifest.parent / "restore.json").write_text("{}", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "alterada"):
                    capture_operations.validated_records(manifest)

    def test_capture_rejects_unfinished_or_failed_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.capture_fixture(root)
            record = ops.read_json(path)
            record["steps"] = [{"name": "cleanup-source", "status": "failed"}]
            ops.write_json(path, record)
            with patch.object(ops, "EVIDENCE", root):
                with self.assertRaisesRegex(RuntimeError, "limpeza"):
                    capture_operations.validated_records(path)
                record["status"] = "in_progress"
                ops.write_json(path, record)
                with self.assertRaisesRegex(RuntimeError, "aprovada"):
                    capture_operations.validated_records(path)

    def test_new_proof_fails_before_start_when_sources_are_unavailable(self):
        with tempfile.TemporaryDirectory(prefix="containerops-proof-") as temporary:
            root = Path(temporary)
            with (
                patch.object(ops, "RUNTIME", root / "runtime"),
                patch.object(ops, "EVIDENCE", root / "evidence"),
                patch.object(proof, "source_manifest", side_effect=OSError("missing")),
                patch.object(ops, "Stack") as stack,
                patch.object(ops, "build") as build,
            ):
                with self.assertRaises(OSError):
                    proof.prove_operations()
            record = ops.read_json(root / "evidence/operations-proof-latest.json")
            self.assertEqual(record["status"], "failed")
            self.assertEqual(record["scenario"], "operations")
            stack.assert_not_called()
            build.assert_not_called()

    def test_operations_never_replaces_full_proof_alias(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(ops, "EVIDENCE", root):
                full = proof.ProofRun()
                historical = (root / "problem-proof-latest.json").read_bytes()
                operations = proof.ProofRun(scenario="operations")
                operations.data["status"] = "failed"
                operations.save()
                self.assertEqual((root / "problem-proof-latest.json").read_bytes(), historical)
                self.assertEqual(
                    ops.read_json(root / "operations-proof-latest.json")["run_id"],
                    operations.run_id,
                )
                self.assertNotEqual(operations.run_id, full.run_id)

    def test_operations_scenario_is_explicit_and_preserves_full_default(self):
        self.assertEqual(ops.parse_arguments(["prove"]).scenario, "full")
        self.assertEqual(
            ops.parse_arguments(["prove", "--scenario", "operations"]).scenario, "operations"
        )

    def test_public_proof_redacts_private_backup_path_without_modifying_original(self):
        record = {"backup": str(ops.RUNTIME / "backups/known"), "nested": [{"count": 3}]}
        result = proof.public_record(record)
        self.assertIn("<runtime>", result["backup"])
        self.assertNotEqual(record["backup"], result["backup"])
        self.assertEqual(json.loads(json.dumps(result))["nested"], [{"count": 3}])


if __name__ == "__main__":
    unittest.main()
