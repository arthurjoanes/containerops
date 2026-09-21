import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import checks
import ops


class OperationSafetyTests(unittest.TestCase):
    def test_verification_invalidates_previous_success_before_running_checks(self):
        with (
            patch.object(checks, "evidence") as write_evidence,
            patch.object(checks, "run", side_effect=RuntimeError("host tests failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "host tests failed"):
                checks.verify()
        name, status = write_evidence.call_args.args
        self.assertEqual(name, "verification-run")
        self.assertEqual(write_evidence.call_args_list[0].args[1]["status"], "in_progress")
        self.assertEqual(status["status"], "failed")
        self.assertFalse(status["success"])
        self.assertIsNotNone(status["completed_at"])

    def test_operation_lock_rejects_another_process_and_releases_after_exit(self):
        with tempfile.TemporaryDirectory(prefix="containerops-lock-test-") as temporary:
            directory = Path(temporary)
            command = [
                sys.executable,
                "-c",
                "import sys; from pathlib import Path; "
                "sys.path.insert(0, sys.argv[1]); import ops; "
                "lock=ops.operation_lock(Path(sys.argv[2])); lock.__enter__()",
                str(ops.ROOT / "scripts"),
                str(directory),
            ]
            with ops.operation_lock(directory):
                rejected = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("operation.lock", rejected.stderr)
            accepted = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            with ops.operation_lock(directory):
                pass

    def test_start_selects_requested_image_and_preserves_saved_image_without_version(self):
        for version in (None, "2.0.0"):
            with self.subTest(version=version):
                stack = Mock(image="sha256:saved")
                args = argparse.Namespace(command="start", version=version)
                with (
                    patch.object(ops, "Stack", return_value=stack),
                    patch.object(ops, "image_id", return_value="sha256:requested") as inspect_image,
                ):
                    ops.execute(args)
                stack.start.assert_called_once_with()
                self.assertEqual(stack.image, "sha256:requested" if version else "sha256:saved")
                self.assertEqual(inspect_image.call_count, 1 if version else 0)

    def test_start_uses_schema_required_by_selected_image(self):
        with tempfile.TemporaryDirectory(prefix="containerops-start-test-") as temporary:
            with patch.object(ops, "RUNTIME", Path(temporary)):
                stack = ops.Stack(image="sha256:release-two")
            stack.compose = Mock(return_value=Mock(stdout="0"))
            stack.ready = Mock()
            stack.save = Mock()
            with (
                patch.object(ops, "release_version", return_value="2.0.0"),
                patch.object(ops, "image_id", return_value="sha256:release-two"),
                patch.object(ops, "setup_secrets"),
            ):
                stack.start()
            migration = [
                call.args for call in stack.compose.call_args_list if "--target" in call.args
            ]
            self.assertEqual(len(migration), 1)
            self.assertEqual(migration[0][-1], "2")
            stack.save.assert_called_once_with()

    def test_failed_drain_restores_admission_before_any_release_change(self):
        stack = Mock()
        stack.snapshot.return_value = {"admission_paused": False}
        stack.inspect.return_value = {"Image": "sha256:current"}
        stack.request.return_value = (200, {"version": "1.0.0"})
        with (
            patch.object(ops, "image_id", return_value="sha256:candidate"),
            patch.object(ops, "backup"),
            patch.object(ops, "drain", side_effect=RuntimeError("queue did not drain")),
            patch.object(ops, "evidence") as write_evidence,
        ):
            with self.assertRaisesRegex(RuntimeError, "queue did not drain"):
                ops.release(stack, "2.0.0")
        stack.compose.assert_not_called()
        stack.manage.assert_called_once_with("resume")
        self.assertEqual(write_evidence.call_args.args[0], "release-failed")
        self.assertEqual(write_evidence.call_args.args[1]["phase"], "drain")
        self.assertNotIn("rolled_back", write_evidence.call_args.args[1])

    def test_manual_rollback_restores_current_image_when_previous_cannot_start(self):
        stack = Mock(image="mutable-tag")
        stack.snapshot.return_value = {"admission_paused": False}
        stack.inspect.return_value = {"Image": "sha256:current"}
        stack.compose.side_effect = [RuntimeError("candidate unhealthy"), None]
        with (
            patch.object(ops, "read_json", return_value={"previous_image": "sha256:previous"}),
            patch.object(ops, "image_id", return_value="sha256:previous"),
            patch.object(ops, "release_version", return_value="1.0.0"),
            patch.object(ops, "drain", return_value={"jobs": []}),
        ):
            with self.assertRaisesRegex(RuntimeError, "candidate unhealthy"):
                ops.rollback(stack)
        self.assertEqual(stack.image, "sha256:current")
        self.assertEqual(stack.compose.call_count, 2)
        stack.save.assert_called_once_with()
        stack.manage.assert_called_once_with("resume")

    def test_failed_backup_is_recorded_without_changing_the_running_image(self):
        stack = Mock(project="pf-containerops-test-12345678")
        stack.inspect.return_value = {"Image": "sha256:current"}
        stack.snapshot.return_value = {"admission_paused": False}
        with (
            patch.object(ops, "image_id", return_value="sha256:candidate"),
            patch.object(ops, "backup", side_effect=RuntimeError("dump failed")),
            patch.object(ops, "evidence") as write_evidence,
        ):
            with self.assertRaisesRegex(RuntimeError, "dump failed"):
                ops.release(stack, "2.0.0")
        stack.compose.assert_not_called()
        name, result = write_evidence.call_args.args
        self.assertEqual(name, "release-failed")
        self.assertEqual(result["phase"], "backup")
        self.assertEqual(result["previous_image"], "sha256:current")
        self.assertNotIn("rolled_back", result)

    def test_failed_automatic_rollback_never_records_recovery_as_successful(self):
        stack = Mock(project="pf-containerops-test-12345678")
        stack.inspect.return_value = {"Image": "sha256:current"}
        stack.snapshot.return_value = {"admission_paused": False}
        stack.request.return_value = (200, {"version": "1.0.0"})
        stack.compose.side_effect = [
            None,
            RuntimeError("candidate unhealthy"),
            RuntimeError("previous unhealthy"),
        ]
        with (
            patch.object(ops, "image_id", return_value="sha256:candidate"),
            patch.object(ops, "backup"),
            patch.object(ops, "drain", return_value={"jobs": []}),
            patch.object(ops, "evidence") as write_evidence,
        ):
            with self.assertRaisesRegex(RuntimeError, "previous unhealthy"):
                ops.release(stack, "2.0.0")
        name, result = write_evidence.call_args.args
        self.assertEqual(name, "release-failed")
        self.assertEqual(result["phase"], "rollback")
        self.assertNotIn("rolled_back", result)
        stack.save.assert_not_called()

    def test_release_one_is_rejected_before_docker_or_backup(self):
        with patch.object(ops, "image_id") as inspect_image:
            with self.assertRaisesRegex(ValueError, "rollback"):
                ops.release(Mock(), "1.0.0")
        inspect_image.assert_not_called()

    def test_image_without_valid_release_label_is_rejected(self):
        response = Mock(stdout=json.dumps([{"Config": {"Labels": {}}}]))
        with patch.object(ops, "run", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "release ContainerOps"):
                ops.release_version("unrelated-image")

    def test_running_api_does_not_hide_worker_with_another_image(self):
        stack = Mock()
        stack.inspect.side_effect = [{"Image": "sha256:expected"}, {"Image": "sha256:stale"}]
        with self.assertRaisesRegex(RuntimeError, "API/worker"):
            ops.running_images(stack, "sha256:expected")

    def test_isolated_backup_does_not_replace_main_restore_pointer(self):
        with tempfile.TemporaryDirectory(prefix="containerops-backup-") as temporary:
            runtime = Path(temporary)
            main_pointer = runtime / "latest-backup.json"
            ops.write_json(main_pointer, {"directory": "main-backup"})
            stack = Mock(project="pf-containerops-test-12345678", directory=runtime / "isolated")
            snapshot = {"admission_paused": False, "schema_version": 2, "jobs": []}
            stack.snapshot.return_value = snapshot
            stack.compose.return_value = Mock(stdout="postgres 17")
            stack.inspect.return_value = {"Image": "sha256:actual"}

            def command(args, **kwargs):
                if args[:2] == ["docker", "cp"]:
                    Path(args[-1]).write_bytes(b"synthetic-custom-dump")
                return Mock(returncode=0)

            with (
                patch.object(ops, "RUNTIME", runtime),
                patch.object(ops, "drain", return_value=snapshot),
                patch.object(ops, "run", side_effect=command),
                patch.object(ops, "evidence"),
            ):
                directory = ops.backup(stack)
            self.assertEqual(ops.read_json(main_pointer), {"directory": "main-backup"})
            self.assertEqual(
                ops.read_json(stack.directory / "latest-backup.json"),
                {"directory": str(directory)},
            )

    def test_candidate_job_must_survive_rollback_with_result_and_attempts(self):
        candidate = {
            "id": "post-migration",
            "state": "succeeded",
            "attempts": 1,
            "result": {"word_count": 4, "checksum": "known"},
            "error": None,
            "version": "2.0.0",
            "algorithm": "unicode-alnum-marks-v1",
        }
        stack = Mock()
        # A response from version 1 is expected; a result change or missing row is not.
        restored = {key: value for key, value in candidate.items() if key != "algorithm"}
        restored["version"] = "1.0.0"
        stack.request.return_value = (200, restored)
        self.assertEqual(ops.preserved_candidate(stack, candidate), restored)
        for status, actual in (
            (404, {}),
            (200, {**restored, "attempts": 2}),
            (200, {**restored, "result": {"word_count": 0, "checksum": "wrong"}}),
        ):
            with self.subTest(status=status, actual=actual):
                stack.request.return_value = (status, actual)
                with self.assertRaisesRegex(RuntimeError, "após migração"):
                    ops.preserved_candidate(stack, candidate)


if __name__ == "__main__":
    unittest.main()
