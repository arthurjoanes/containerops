import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ops
import transfer_proof as transfer
from runtime_context import RuntimeContext


class TransferProofTests(unittest.TestCase):
    def setUp(self):
        parent = (
            Path(os.environ["USERPROFILE"]) / "AppData/Local/ContainerOps-runtime/sessions/tests"
            if os.name == "nt"
            else Path(tempfile.gettempdir()) / "containerops-transfer-fixtures"
        )
        parent.mkdir(parents=True, exist_ok=True)
        # Leave room for nested manifest names after Windows app path redirection.
        self.temporary = tempfile.TemporaryDirectory(prefix="tx-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = RuntimeContext(ops.ROOT, self.root, self.root / "sessions/source", "source")
        self.destination = RuntimeContext(
            ops.ROOT, self.root, self.root / "sessions/destination", "destination"
        )
        self.empty = {"containers": [], "volumes": [], "networks": []}

    def test_no_default_runtime_execution_before_docker_or_files(self):
        context = RuntimeContext(ops.ROOT, self.root, self.root)
        with patch.object(ops, "CONTEXT", context), patch.object(ops, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "CONTAINEROPS_SESSION"):
                transfer.destination_context("destination")
        run.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_destination_environment_is_set_before_child_context_imports(self):
        with (
            patch.object(ops, "CONTEXT", self.source),
            patch.object(transfer, "resolve_context", return_value=self.destination) as resolve,
        ):
            destination, environment = transfer.destination_context("destination")
        self.assertEqual(destination, self.destination)
        self.assertEqual(environment["CONTAINEROPS_SESSION"], "destination")
        self.assertEqual(environment["CONTAINEROPS_RUNTIME"], str(self.root))
        self.assertEqual(resolve.call_args.kwargs["environment"], environment)
        self.assertFalse(self.destination.runtime.exists())

    def test_existing_destination_and_invalid_slug_rejected(self):
        self.destination.runtime.mkdir(parents=True)
        with (
            patch.object(ops, "CONTEXT", self.source),
            patch.object(transfer, "resolve_context", return_value=self.destination),
        ):
            with self.assertRaisesRegex(RuntimeError, "já existe"):
                transfer.destination_context("destination")
        # O resolver real valida o basename antes de criar qualquer diretório.
        real = RuntimeContext(
            ops.ROOT, ops.DEFAULT_RUNTIME, ops.DEFAULT_RUNTIME / "sessions/source", "source"
        )
        with patch.object(ops, "CONTEXT", real), patch.object(ops, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "basename"):
                transfer.destination_context("../elsewhere")
        run.assert_not_called()

    def test_auxiliary_collision_checks_all_targets_before_tagging(self):
        found = Mock(returncode=0, stdout=json.dumps([{"Id": "sha256:other"}]))
        with (
            patch.object(ops, "CONTEXT", self.source),
            patch.object(ops, "image_id", return_value="sha256:source"),
            patch.object(ops, "run", return_value=found) as run,
        ):
            with self.assertRaisesRegex(RuntimeError, "outra imagem"):
                transfer.transfer_auxiliaries(self.destination)
        self.assertFalse(any(call.args[0][:2] == ["docker", "tag"] for call in run.call_args_list))

    def test_corruption_failure_cannot_dispatch_load_or_restore(self):
        context = self.destination
        with (
            patch.object(ops, "setup"),
            patch.object(ops, "CONTEXT", context),
            patch.object(ops, "RUNTIME", context.runtime),
            patch.object(transfer, "inventory", return_value=self.empty),
            patch.object(
                transfer.backup_package,
                "import_backup",
                side_effect=RuntimeError("unexpected manifest problem"),
            ),
            patch.object(ops, "execute_backups") as dispatch,
            patch.object(ops, "restore_test") as restore,
        ):
            with self.assertRaisesRegex(RuntimeError, "outra causa"):
                transfer.receive_package(
                    self.root / "package", self.root / "corrupted", uuid.uuid4().hex
                )
        dispatch.assert_not_called()
        restore.assert_not_called()
        self.assertFalse(list((context.runtime / "backups").iterdir()))

    def test_destination_worker_orders_import_explicit_load_restore_and_receipt(self):
        context = self.destination
        evidence = self.root / "evidence"
        evidence.mkdir()
        ops.write_json(evidence / "backup-import.json", {"backup_id": "import-fixture"})
        restored = {
            "status": "passed",
            "cleanup_succeeded": True,
            "snapshot_equal": True,
            "checksum_verified": True,
            "project": "pf-containerops-restore-12345678",
        }
        checked = {
            "manifest": {"backup": {"source": {"revision": "uncommitted", "sha256": "a" * 64}}},
            "files": {"containerops.dump": {"sha256": "b" * 64, "size": 1}},
        }
        fingerprints = {"db_admin": "private-fingerprint"}
        with (
            patch.object(ops, "setup") as setup,
            patch.object(ops, "CONTEXT", context),
            patch.object(ops, "RUNTIME", context.runtime),
            patch.object(ops, "EVIDENCE", evidence),
            patch.object(transfer, "inventory", return_value=self.empty),
            patch.object(
                transfer.backup_package,
                "import_backup",
                side_effect=RuntimeError("Checksum de backup inválido"),
            ),
            patch.object(ops, "execute_backups") as dispatch,
            patch.object(ops, "restore_test", return_value=restored) as restore,
            patch.object(transfer.backup_package, "validate_backup", return_value=checked),
            patch.object(transfer, "credential_fingerprints", return_value=fingerprints),
            patch.object(transfer, "_private_worker_result") as saved,
            patch("report.generate") as report,
        ):
            result = transfer.receive_package(
                self.root / "package", self.root / "corrupted", uuid.uuid4().hex
            )
        self.assertEqual(
            [call.args[0].command for call in dispatch.call_args_list],
            ["backup-import", "backup-image-load"],
        )
        setup.assert_called_once_with()
        self.assertTrue(dispatch.call_args_list[1].args[0].trusted_image_source)
        restore.assert_called_once_with(backup_id="import-fixture", trusted_source=True)
        self.assertTrue(result["main_pointer_unchanged"])
        self.assertTrue(result["corruption"]["rejected_before_resources"])
        self.assertEqual(saved.call_args.args[1]["credential_fingerprints"], fingerprints)
        report.assert_called_once_with(ops.ROOT, context.runtime)

    def test_cleanup_failure_is_failed_manifest_with_no_success_alias(self):
        stack = Mock(spec=ops.Stack)
        stack.destroy_test.side_effect = RuntimeError("cleanup failed")
        evidence = self.root / "evidence"
        with (
            patch.object(ops, "CONTEXT", self.source),
            patch.object(ops, "EVIDENCE", evidence),
            patch.object(transfer, "destination_context", return_value=(self.destination, {})),
            patch.object(ops, "Stack", return_value=stack),
            patch.object(transfer, "_source_work"),
            patch.object(transfer, "inventory", return_value=self.empty),
        ):
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                transfer.run_transfer("destination")
        final = ops.read_json(evidence / "transfer-proof-latest.json")
        self.assertEqual(final["status"], "failed")
        self.assertFalse(final["cleanup_succeeded"])
        self.assertIsNotNone(final["completed_at"])
        self.assertEqual(final["phases"][-1]["status"], "failed")

    def test_source_failure_still_cleans_owned_project_and_records_failure(self):
        stack = Mock(spec=ops.Stack)
        evidence = self.root / "evidence"
        with (
            patch.object(ops, "CONTEXT", self.source),
            patch.object(ops, "EVIDENCE", evidence),
            patch.object(transfer, "destination_context", return_value=(self.destination, {})),
            patch.object(ops, "Stack", return_value=stack),
            patch.object(transfer, "_source_work", side_effect=RuntimeError("source changed")),
            patch.object(transfer, "inventory", return_value=self.empty),
        ):
            with self.assertRaisesRegex(RuntimeError, "source changed"):
                transfer.run_transfer("destination")
        stack.destroy_test.assert_called_once_with()
        self.assertEqual(stack.assert_fresh.call_count, 2)
        self.assertEqual(ops.read_json(evidence / "transfer-proof-latest.json")["status"], "failed")

    def test_public_records_redact_both_session_paths(self):
        private = {
            "source": str(self.source.runtime / "backups/a"),
            "destination": str(self.destination.runtime / "backups/b"),
        }
        with patch.object(ops, "CONTEXT", self.source):
            public = transfer.public_record(private)
        self.assertNotIn(str(self.root), json.dumps(public))
        self.assertIn(str(self.root), private["destination"])

    def test_cli_keeps_worker_flags_internal_and_requires_complete_modes(self):
        self.assertEqual(
            transfer.parse_arguments(["--destination-session", "destination"]).destination_session,
            "destination",
        )
        for args in (
            ["--receive", "package"],
            ["--destination-session", "destination", "--run-id", "abc"],
            [],
        ):
            with patch("sys.stderr"), self.assertRaises(SystemExit):
                transfer.parse_arguments(args)


if __name__ == "__main__":
    unittest.main()
