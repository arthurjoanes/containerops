import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ops
import report
import supply
from runtime_context import (
    ROOT,
    RuntimeContext,
    ensure_file_path,
    ensure_no_links,
    ensure_tree,
    resolve_context,
)


class RuntimeContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="containerops-context-")
        self.addCleanup(self.temporary.cleanup)
        self.profile = Path(self.temporary.name)
        self.environment = {"USERPROFILE": str(self.profile)}

    def context(self, slug=None, **extra):
        environment = {**self.environment, **extra}
        if slug is not None:
            environment["CONTAINEROPS_SESSION"] = slug
        return resolve_context(ROOT, environment=environment, windows=True)

    def test_default_contract_keeps_original_runtime_names_and_evidence(self):
        context = self.context()
        self.assertEqual(context.runtime, self.profile / "AppData/Local/ContainerOps-runtime")
        self.assertEqual(context.main, "pf-containerops")
        self.assertEqual(context.builder, "pf-containerops-builder")
        self.assertEqual(context.evidence, ROOT / "docs/evidence")
        self.assertEqual(context.report, ROOT / "docs/report.html")
        self.assertEqual(context.image("app", "2.0.0"), "containerops-app:2.0.0")
        self.assertEqual(context.image("test"), "containerops-test:local")

    def test_session_isolates_runtime_images_builder_and_evidence(self):
        alpha, beta = self.context("alpha"), self.context("beta")
        self.assertEqual(alpha.runtime, alpha.base / "sessions/alpha")
        self.assertEqual(alpha.evidence, alpha.runtime / "docs/evidence")
        self.assertEqual(alpha.report, alpha.runtime / "docs/report.html")
        self.assertEqual(alpha.main, "pf-containerops-session-alpha")
        self.assertEqual(alpha.builder, "pf-containerops-builder-alpha")
        for component in ("app", "test", "database", "proxy", "cache-experiment"):
            self.assertNotEqual(alpha.image(component), beta.image(component))
        self.assertEqual(alpha.image("app", "2.0.0"), "containerops-alpha-app:2.0.0")

    def test_session_accepts_only_base_or_its_exact_reserved_subdirectory(self):
        context = self.context("alpha")
        for runtime in (context.base, context.runtime):
            self.assertEqual(self.context("alpha", CONTAINEROPS_RUNTIME=str(runtime)), context)
        for runtime in (self.profile / "external", context.base / "sessions/beta"):
            with self.subTest(runtime=runtime), self.assertRaisesRegex(RuntimeError, "reservado"):
                self.context("alpha", CONTAINEROPS_RUNTIME=str(runtime))
        with self.assertRaisesRegex(RuntimeError, "reservado"):
            self.context(CONTAINEROPS_RUNTIME=str(context.runtime))

    def test_invalid_slugs_and_traversal_fail_before_creating_anything(self):
        for slug in (
            "",
            "../other",
            "a/b",
            "a\\b",
            ".",
            "..",
            "UPPER",
            "a.",
            "con",
            "lpt1",
            "a" * 33,
        ):
            with self.subTest(slug=slug), self.assertRaisesRegex(RuntimeError, "basename"):
                self.context(slug)
        with self.assertRaisesRegex(RuntimeError, "traversal"):
            self.context("alpha", CONTAINEROPS_RUNTIME=str(self.profile / ".." / "other"))
        self.assertEqual(list(self.profile.iterdir()), [])

    def test_existing_reparse_ancestor_and_symlink_are_rejected(self):
        context = self.context("alpha")
        real_lstat = Path.lstat

        def reparse(path):
            if path == self.profile:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return real_lstat(path)

        with patch.object(Path, "lstat", reparse), self.assertRaisesRegex(RuntimeError, "junções"):
            ensure_no_links(context.runtime)
        target, link = self.profile / "target", self.profile / "linked"
        target.mkdir()
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("O host não permite criar symlink sem privilégios")
        with self.assertRaisesRegex(RuntimeError, "links"):
            ensure_no_links(link / "new")

    @unittest.skipUnless(os.name == "nt", "Windows junction")
    def test_windows_junction_in_session_root_is_rejected(self):
        context = self.context("alpha")
        context.base.mkdir(parents=True)
        target = self.profile / "external"
        target.mkdir()
        junction = context.base / "sessions"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        try:
            with self.assertRaisesRegex(RuntimeError, "junções"):
                self.context("alpha")
        finally:
            junction.rmdir()

    def test_supply_rejects_a_runtime_from_another_session(self):
        context = self.context("alpha")
        environment = {
            **os.environ,
            **self.environment,
            "CONTAINEROPS_SESSION": "alpha",
            "CONTAINEROPS_RUNTIME": str(context.base),
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(RuntimeError, "contexto ativo"):
                supply.paths(ROOT, context.base / "sessions/beta")
            _, runtime, artifact = supply.paths(ROOT, context.runtime)
        self.assertEqual(runtime, context.runtime)
        self.assertEqual(artifact, context.runtime / "artifacts/1.0.0")
        self.assertTrue(context.evidence.is_dir())

    def test_writable_tree_and_report_reject_existing_hardlinks(self):
        directory = self.profile / "artifact"
        directory.mkdir()
        original = directory / "original"
        original.write_text("preserve", encoding="utf-8")
        linked = directory / "linked"
        os.link(original, linked)
        with self.assertRaisesRegex(RuntimeError, "hardlinks"):
            ensure_file_path(linked)
        with self.assertRaisesRegex(RuntimeError, "hardlinks"):
            ensure_tree(directory)
        root = self.profile / "repo"
        (root / "docs").mkdir(parents=True)
        os.link(original, root / "docs/report.html")
        with self.assertRaisesRegex(RuntimeError, "hardlinks"):
            report.generate(root, root / "runtime")
        self.assertEqual(original.read_text(encoding="utf-8"), "preserve")

    @unittest.skipUnless(os.name == "nt", "Windows junction")
    def test_nested_supply_artifact_junction_is_rejected_before_any_write(self):
        context = self.context("alpha")
        (context.runtime / "artifacts").mkdir(parents=True)
        target = self.profile / "external-artifact"
        target.mkdir()
        junction = context.runtime / "artifacts/1.0.0"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        environment = {
            **os.environ,
            **self.environment,
            "CONTAINEROPS_SESSION": "alpha",
            "CONTAINEROPS_RUNTIME": str(context.base),
        }
        try:
            with (
                patch.dict(os.environ, environment, clear=True),
                self.assertRaisesRegex(RuntimeError, "junções"),
            ):
                supply.paths(ROOT, context.runtime)
            self.assertEqual(list(target.iterdir()), [])
        finally:
            junction.rmdir()

    def test_locks_are_exclusive_per_context_and_release_on_process_exit(self):
        command = [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "import ops; lock=ops.operation_lock(); lock.__enter__()",
            str(ROOT / "scripts"),
        ]
        first = self.context("alpha")
        environment = {
            **os.environ,
            **self.environment,
            "CONTAINEROPS_SESSION": "alpha",
            "CONTAINEROPS_RUNTIME": str(first.base),
        }
        with ops.operation_lock(first.runtime):
            blocked = subprocess.run(
                command, env=environment, capture_output=True, text=True, timeout=10
            )
            other = subprocess.run(
                command,
                env={**environment, "CONTAINEROPS_SESSION": "beta"},
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("operation.lock", blocked.stderr)
        self.assertEqual(other.returncode, 0, other.stderr)
        released = subprocess.run(
            command, env=environment, capture_output=True, text=True, timeout=10
        )
        self.assertEqual(released.returncode, 0, released.stderr)

    def test_session_report_reads_its_evidence_and_preserves_global_report(self):
        root = self.profile / "repo"
        root.mkdir()
        context = RuntimeContext(root, self.profile, self.profile / "session", "alpha")
        context.evidence.mkdir(parents=True)
        global_report = root / "docs/report.html"
        global_report.parent.mkdir(parents=True)
        global_report.write_text("preserve-global", encoding="utf-8")
        catalog = {
            "context": "alpha",
            "entries": [
                {
                    "id": "sample-1",
                    "source_project": "<source>",
                    "schema_version": 2,
                    "size_bytes": 42,
                    "validation": "valid",
                    "protected": True,
                    "imported": True,
                    "restore_verified": True,
                    "directory": "C:\\private\\do-not-publish",
                    "source_context": "origin-alpha",
                    "retention_reasons": ["latest-backup", "C:\\private\\do-not-publish"],
                }
            ],
        }
        (context.evidence / "backup-catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
        with patch.object(report, "active_context", return_value=context):
            path = report.generate(root, context.runtime, now=datetime(2026, 10, 12, tzinfo=UTC))
        page = path.read_text(encoding="utf-8")
        self.assertEqual(path, context.report)
        self.assertIn("sample-1", page)
        self.assertIn("origin-alpha", page)
        self.assertIn("Último ponteiro", page)
        self.assertIn("&lt;source&gt;", page)
        self.assertIn("CONTAINEROPS_SESSION", page)
        self.assertIn("restore-test --backup sample-1", page)
        self.assertNotIn("do-not-publish", page)
        self.assertEqual(global_report.read_text(encoding="utf-8"), "preserve-global")


if __name__ == "__main__":
    unittest.main()
