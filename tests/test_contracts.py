import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import checks
import ops
import supply


class CommandContractTests(unittest.TestCase):
    def test_every_operation_rejects_flags_that_do_not_apply(self):
        commands = "setup build test start status demo logs inspect verify backup restore-test release rollback stop report scan sbom cache-builds tls prove".split()
        allowed = {
            "--version": {"build", "start", "scan", "sbom", "release"},
            "--offline": {"scan"},
            "--inject-smoke-failure": {"release"},
        }
        for command in commands:
            self.assertEqual(ops.parse_arguments([command]).command, command)
            for flag, supported in allowed.items():
                argv = [command, flag] + (["2.0.0"] if flag == "--version" else [])
                with self.subTest(command=command, flag=flag):
                    if command in supported:
                        self.assertEqual(ops.parse_arguments(argv).command, command)
                    else:
                        with (
                            contextlib.redirect_stderr(io.StringIO()),
                            self.assertRaises(SystemExit) as error,
                        ):
                            ops.parse_arguments(argv)
                        self.assertEqual(error.exception.code, 2)

    def test_unknown_commands_versions_and_incomplete_flags_are_rejected(self):
        cases = [
            [],
            ["unknown"],
            ["start", "--version"],
            ["start", "--version", "latest"],
            ["release", "--version", "1.0.0"],
            ["start", "--unknown"],
        ]
        for argv in cases:
            with (
                self.subTest(argv=argv),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as error,
            ):
                ops.parse_arguments(argv)
            self.assertEqual(error.exception.code, 2)

    def test_supply_flags_are_command_specific(self):
        self.assertTrue(supply.parse_arguments(["scan", "--offline"]).offline)
        for argv in (
            ["build", "--offline"],
            ["audit", "--offline"],
            ["cache", "--version", "1.0.0"],
        ):
            with (
                self.subTest(argv=argv),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as error,
            ):
                supply.parse_arguments(argv)
            self.assertEqual(error.exception.code, 2)

    def test_invalid_ports_and_project_names_fail_before_docker(self):
        with tempfile.TemporaryDirectory(prefix="containerops-contract-") as directory:
            with patch.object(ops, "RUNTIME", Path(directory)), patch.object(ops, "run") as run:
                for value in ("0", "65536", "8104", "invalid", "8445"):
                    with (
                        self.subTest(value=value),
                        patch.dict(os.environ, HTTP_PORT=value, TLS_PORT="8445"),
                        self.assertRaises(ValueError),
                    ):
                        ops.Stack()
                for name in (
                    "production",
                    "pf-containerops-test-../../x",
                    "pf-containerops-restore-xyz",
                    "pf-containerops-test-ABCDEF01",
                ):
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        ops.Stack(name)
                run.assert_not_called()

    def test_terminal_job_failure_does_not_wait_or_retry(self):
        stack = Mock()
        stack.request.return_value = (
            200,
            {"id": "job", "state": "failed", "error": {"code": "attempts_exhausted"}},
        )
        with patch.object(ops.time, "sleep") as sleep:
            with self.assertRaisesRegex(ops.JobFailed, "attempts_exhausted"):
                ops.Stack.completed(stack, "job")
        stack.request.assert_called_once()
        sleep.assert_not_called()

    def test_transient_transport_failure_recovers_without_post_replay(self):
        stack = Mock()
        stack.request.side_effect = [
            OSError("connection reset"),
            (503, {}),
            (200, {"state": "running"}),
            (200, {"state": "succeeded"}),
        ]
        with patch.object(ops.time, "sleep"):
            self.assertEqual(ops.Stack.completed(stack, "job")["state"], "succeeded")
        self.assertEqual(stack.request.call_count, 4)
        self.assertTrue(all(call.args[0] == "GET" for call in stack.request.call_args_list))


class VerificationEvidenceTests(unittest.TestCase):
    def test_archived_evidence_survives_alias_replacement_and_is_hashed(self):
        with tempfile.TemporaryDirectory(prefix="containerops-evidence-") as directory:
            evidence = Path(directory)
            with (
                patch.object(checks, "EVIDENCE", evidence),
                patch.object(ops, "EVIDENCE", evidence),
            ):
                attempt = checks.VerificationRun(full=True)
                attempt.record("in_progress")
                ops.evidence("journey", {"project": attempt.data["project"], "passed": True})
                attempt.archive("journey")
                ops.evidence("journey", {"project": "another-project", "passed": False})
                attempt.record("passed")
                manifest = ops.read_json(evidence / "verification-run.json")
                archived = evidence / manifest["evidence"][0]["path"]
                stored = ops.read_json(archived)
                self.assertEqual(stored["run_id"], manifest["run_id"])
                self.assertEqual(stored["project"], manifest["project"])
                self.assertEqual(
                    manifest["evidence"][0]["sha256"],
                    "sha256:" + hashlib.sha256(archived.read_bytes()).hexdigest(),
                )
                self.assertLessEqual(manifest["started_at"], manifest["completed_at"])
                self.assertTrue(manifest["success"])
                with self.assertRaisesRegex(RuntimeError, "outra execução"):
                    attempt.archive("journey")

    def test_failed_quality_check_records_partial_evidence_without_starting_docker(self):
        with tempfile.TemporaryDirectory(prefix="containerops-evidence-") as directory:
            evidence = Path(directory)
            with (
                patch.object(checks, "EVIDENCE", evidence),
                patch.object(ops, "EVIDENCE", evidence),
                patch.object(checks, "Stack") as stack,
                patch.object(
                    checks,
                    "run",
                    return_value=Mock(returncode=1, stdout="", stderr="assertion failed"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "host_operations"):
                    checks.verify()
                manifest = ops.read_json(evidence / "verification-run.json")
                self.assertEqual(manifest["status"], "failed")
                self.assertFalse(manifest["success"])
                self.assertEqual(len(manifest["evidence"]), 1)
                result = ops.read_json(evidence / manifest["evidence"][0]["path"])
                self.assertEqual(result["checks"]["host_operations"]["exit_code"], 1)
                stack.assert_not_called()

    def test_failed_atomic_replace_preserves_previous_json_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory(prefix="containerops-evidence-") as directory:
            path = Path(directory) / "state.json"
            ops.write_json(path, {"status": "old"})
            with patch.object(Path, "replace", side_effect=OSError("locked")):
                with self.assertRaises(OSError):
                    ops.write_json(path, {"status": "new"})
            self.assertEqual(json.loads(path.read_text()), {"status": "old"})
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
