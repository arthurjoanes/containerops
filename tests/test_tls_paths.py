import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ops


class TLSPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="containerops-tls-paths-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.target = self.root / "preserved.txt"
        self.target.write_text("preserve", encoding="utf-8")
        self.stack = SimpleNamespace(directory=self.runtime, tls=False, compose=Mock())

    def assert_rejected(self):
        with (
            patch.object(ops.shutil, "which", return_value="openssl"),
            patch.object(ops, "run", side_effect=AssertionError("OpenSSL reached")) as run,
            patch.object(
                self.stack, "compose", side_effect=AssertionError("Compose reached")
            ) as compose,
        ):
            with self.assertRaisesRegex(RuntimeError, "links|junções|hardlinks"):
                ops.tls_setup(self.stack)
            run.assert_not_called()
            compose.assert_not_called()
        self.assertFalse(self.stack.tls)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "preserve")

    def test_generation_rejects_linked_extensions_before_running_openssl(self):
        directory = self.runtime / "tls"
        directory.mkdir()
        os.link(self.target, directory / "extensions.cnf")
        self.assert_rejected()

    def test_reused_certificates_reject_every_linked_output(self):
        directory = self.runtime / "tls"
        directory.mkdir()
        names = (
            "ca.key",
            "ca.crt",
            "server.key",
            "server.csr",
            "extensions.cnf",
            "server.crt",
            "ca.srl",
        )
        for name in names:
            (directory / name).write_text("certificate fixture", encoding="utf-8")
        for name in names:
            with self.subTest(name=name):
                path = directory / name
                path.unlink()
                os.link(self.target, path)
                try:
                    self.assert_rejected()
                finally:
                    path.unlink()
                    path.write_text("certificate fixture", encoding="utf-8")
                    self.stack.tls = False

    def test_linked_tls_directory_is_rejected(self):
        target = self.root / "outside"
        target.mkdir()
        directory = self.runtime / "tls"
        try:
            directory.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Host does not permit directory symlinks")
        self.assert_rejected()
        self.assertEqual(list(target.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows junction")
    def test_junction_tls_directory_is_rejected(self):
        target = self.root / "outside"
        target.mkdir()
        junction = self.runtime / "tls"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            self.assert_rejected()
            self.assertEqual(list(target.iterdir()), [])
        finally:
            junction.rmdir()

    def test_regular_new_directory_reaches_certificate_generation(self):
        with (
            patch.object(ops.shutil, "which", return_value="openssl"),
            patch.object(ops, "run", side_effect=LookupError("generation reached")) as run,
        ):
            with self.assertRaisesRegex(LookupError, "generation reached"):
                ops.tls_setup(self.stack)
        self.assertEqual(run.call_count, 1)
        self.assertTrue((self.runtime / "tls").is_dir())
        self.stack.compose.assert_not_called()

    def test_regular_existing_certificates_reach_compose_without_regeneration(self):
        directory = self.runtime / "tls"
        directory.mkdir()
        for name in ("server.crt", "server.key", "ca.key"):
            (directory / name).write_text("certificate fixture", encoding="utf-8")
        with (
            patch.object(ops.shutil, "which", return_value="openssl"),
            patch.object(ops, "run") as run,
            patch.object(self.stack, "compose", side_effect=LookupError("compose reached")),
        ):
            with self.assertRaisesRegex(LookupError, "compose reached"):
                ops.tls_setup(self.stack)
        run.assert_not_called()
        self.assertTrue(self.stack.tls)


if __name__ == "__main__":
    unittest.main()
