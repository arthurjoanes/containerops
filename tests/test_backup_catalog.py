import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backup_catalog as catalog


class BackupCatalogTests(unittest.TestCase):
    def setUp(self):
        # Testes destrutivos usam só fixtures novas dentro de uma sessão reservada.
        parent = (
            Path(os.environ["USERPROFILE"])
            / "AppData/Local/ContainerOps-runtime/sessions/catalog-fixtures/test-temp"
            if os.name == "nt"
            else Path(tempfile.gettempdir()) / "containerops-catalog-fixtures"
        )
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="catalog-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.runtime = Path(self.temporary.name)
        (self.runtime / "backups").mkdir()

    def backup(self, identifier, day, *, project="pf-containerops", context="origin-a"):
        directory = self.runtime / "backups" / identifier
        directory.mkdir()
        dump = (identifier + ":synthetic-custom-dump").encode()
        (directory / "containerops.dump").write_bytes(dump)
        created = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=day)
        metadata = {
            "created_at": created.isoformat(),
            "cutoff_interval": {
                "started_at": (created - timedelta(seconds=2)).isoformat(),
                "completed_at": (created - timedelta(seconds=1)).isoformat(),
                "method": "admission paused; jobs drained",
            },
            "sha256": hashlib.sha256(dump).hexdigest(),
            "server": "postgres (PostgreSQL) 17.11",
            "schema_version": 2,
            "snapshot": {"schema_version": 2, "jobs": [], "admission_paused": True},
            "source_project": project,
            "source_context": context,
            "image": "sha256:" + "a" * 64,
        }
        (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return directory

    def bytes_and_mtimes(self):
        return {
            path.relative_to(self.runtime).as_posix(): (
                path.read_bytes() if path.is_file() else None,
                path.stat().st_mtime_ns,
            )
            for path in [self.runtime, *sorted(self.runtime.rglob("*"))]
        }

    def verified(self, identifier):
        dump = self.runtime / "backups" / identifier / "containerops.dump"
        return {
            "status": "passed",
            "checksum_verified": True,
            "snapshot_equal": True,
            "cleanup_succeeded": True,
            "backup_sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
            "completed_at": "2026-02-01T12:00:00+00:00",
            "phases": [
                {"name": "cleanup", "status": "passed"},
                {"name": "cleanup-check", "status": "passed"},
            ],
            "new_job": {
                "id": "restored-job",
                "state": "succeeded",
                "text": "must never appear in receipt",
                "result": {
                    "word_count": 4,
                    "checksum": hashlib.sha256(b"Backup restaurado com sucesso").hexdigest(),
                },
            },
        }

    def test_discovery_and_dry_run_preserve_legacy_bytes_mtimes_and_pointers(self):
        self.backup("20260101T120000-0123abcd", 1)
        latest = self.backup("20260102T120000-0123abcd", 2)
        (self.runtime / "latest-backup.json").write_text(
            json.dumps({"directory": str(latest)}), encoding="utf-8"
        )
        before = self.bytes_and_mtimes()
        discovered = catalog.discover(self.runtime)
        first = catalog.plan_retention(self.runtime, 1)
        second = catalog.plan_retention(self.runtime, 1)
        self.assertEqual(first, second)
        self.assertEqual(first["candidates"], ["20260101T120000-0123abcd"])
        self.assertEqual(discovered["format_version"], 1)
        self.assertTrue(all(item["validation"] == "valid" for item in discovered["entries"]))
        self.assertEqual(self.bytes_and_mtimes(), before)
        self.assertFalse((self.runtime / catalog.CATALOG_FILE).exists())

    def test_legacy_metadata_without_context_or_cutoff_is_not_rewritten(self):
        directory = self.backup("old-backup", 1)
        path = directory / "metadata.json"
        metadata = json.loads(path.read_text())
        del metadata["source_context"]
        del metadata["cutoff_interval"]
        path.write_text(json.dumps(metadata), encoding="utf-8")
        before = path.read_bytes()
        entry = catalog.discover(self.runtime)["entries"][0]
        self.assertEqual(entry["validation"], "valid")
        self.assertEqual(entry["source_context"], "legacy")
        self.assertEqual(path.read_bytes(), before)

    def test_incomplete_metadata_unknown_files_and_destinations_are_never_candidates(self):
        self.backup("latest", 3)
        incomplete = self.runtime / "backups" / "incomplete"
        incomplete.mkdir()
        (incomplete / "metadata.json").write_text("{}", encoding="utf-8")
        extra = self.backup("extra-file", 1)
        (extra / "unknown.txt").write_text("preserve me", encoding="utf-8")
        external = self.backup("external-path", 2)
        path = external / "metadata.json"
        metadata = json.loads(path.read_text())
        metadata["destination"] = str(self.runtime.parent)
        path.write_text(json.dumps(metadata), encoding="utf-8")
        plan = catalog.plan_retention(self.runtime, 0)
        self.assertEqual(plan["candidates"], [])
        retained = {item["id"]: item["reasons"] for item in plan["retained"]}
        for identifier in ("incomplete", "extra-file", "external-path"):
            self.assertEqual(retained[identifier], ["unknown"])

    def test_select_uses_only_exact_internal_ids(self):
        directory = self.backup("safe-id", 1)
        self.assertEqual(catalog.select_backup(self.runtime, "safe-id"), directory)
        for identifier in ("../safe-id", str(directory), "safe-id/..", "missing", "."):
            with self.subTest(identifier=identifier), self.assertRaises(catalog.CatalogError):
                catalog.select_backup(self.runtime, identifier)
        (directory / "containerops.dump").write_bytes(b"corrupt")
        with self.assertRaisesRegex(catalog.CatalogError, "^Checksum de backup inválido$"):
            catalog.select_backup(self.runtime, "safe-id")

    def test_keep_last_groups_by_original_project_and_context_after_import(self):
        for context in ("origin-a", "origin-b"):
            for day in (1, 2):
                identifier = f"{context}-{day}"
                self.backup(identifier, day, context=context)
                catalog.register_backup(
                    self.runtime, identifier, imported=True, context="destination"
                )
        self.backup("proof-1", 1, project="pf-containerops-test-0123abcd")
        plan = catalog.plan_retention(self.runtime, 1, context="destination")
        self.assertEqual(plan["candidates"], ["origin-a-1", "origin-b-1"])
        entries = catalog.discover(self.runtime, context="destination")["entries"]
        self.assertTrue(
            all(item["imported"] for item in entries if item["id"].startswith("origin"))
        )

    def test_last_valid_marked_latest_and_last_restore_verified_are_distinct_protections(self):
        for day in range(6):
            self.backup(f"copy-{day}", day)
        catalog.mark_protected(self.runtime, "copy-1")
        catalog.record_restore_verified(self.runtime, "copy-2", self.verified("copy-2"))
        (self.runtime / "latest-backup.json").write_text(
            json.dumps({"backup_id": "copy-3"}), encoding="utf-8"
        )
        plan = catalog.plan_retention(self.runtime, 0)
        self.assertEqual(plan["candidates"], ["copy-0", "copy-4"])
        reasons = {item["id"]: item["reasons"] for item in plan["retained"]}
        self.assertIn("marked-protected", reasons["copy-1"])
        self.assertIn("last-restore-verified", reasons["copy-2"])
        self.assertIn("latest-backup", reasons["copy-3"])
        self.assertIn("last-valid", reasons["copy-5"])
        before = {
            identifier: self.bytes_and_mtimes()[f"backups/{identifier}/containerops.dump"]
            for identifier in reasons
        }
        result = catalog.apply_retention(self.runtime, 0, plan["plan_id"])
        self.assertEqual(result["deleted"], ["copy-0", "copy-4"])
        self.assertEqual({path.name for path in (self.runtime / "backups").iterdir()}, set(reasons))
        for identifier, artifact in before.items():
            self.assertEqual(
                self.bytes_and_mtimes()[f"backups/{identifier}/containerops.dump"], artifact
            )

    def test_every_environment_latest_pointer_is_protected(self):
        self.backup("old", 0)
        self.backup("environment-latest", 1)
        self.backup("newest", 2)
        environment = self.runtime / "environments" / "pf-containerops-test-0123abcd"
        environment.mkdir(parents=True)
        (environment / "latest-backup.json").write_text(
            json.dumps({"id": "environment-latest"}), encoding="utf-8"
        )
        plan = catalog.plan_retention(self.runtime, 0)
        self.assertEqual(plan["candidates"], ["old"])

    def test_checksum_snapshot_job_and_cleanup_are_required_for_restore_receipt(self):
        self.backup("copy", 1)
        for changed in (
            {"status": "failed"},
            {"checksum_verified": False},
            {"snapshot_equal": False},
            {"cleanup_succeeded": False},
            {"phases": [{"name": "cleanup", "status": "passed"}]},
            {"new_job": {"state": "succeeded", "result": {"word_count": 0}}},
            {"backup_sha256": "b" * 64},
            {"backup_metadata_sha256": "b" * 64},
        ):
            with self.subTest(changed=changed), self.assertRaises(catalog.CatalogError):
                catalog.record_restore_verified(
                    self.runtime, "copy", {**self.verified("copy"), **changed}
                )
        self.assertFalse((self.runtime / "backup-receipts").exists())
        before = (self.runtime / "backups/copy/metadata.json").read_bytes()
        link = catalog.record_restore_verified(self.runtime, "copy", self.verified("copy"))
        receipt = self.runtime / "backup-receipts" / link["receipt"]
        self.assertEqual(hashlib.sha256(receipt.read_bytes()).hexdigest(), link["sha256"])
        self.assertNotIn("must never", receipt.read_text())
        self.assertNotIn(str(self.runtime), receipt.read_text())
        self.assertEqual((self.runtime / "backups/copy/metadata.json").read_bytes(), before)
        entry = catalog.discover(self.runtime)["entries"][0]
        self.assertEqual(entry["restore_verified"], link)
        receipt.write_text("{}", encoding="utf-8")
        self.assertEqual(catalog.discover(self.runtime)["entries"][0]["validation"], "unknown")

    def test_any_new_backup_changed_bytes_or_mtime_invalidates_preview(self):
        for mutation in ("new-backup", "corrupt", "touch", "new-file"):
            with self.subTest(mutation=mutation):
                # Cada mutação usa seu próprio diretório de fixture.
                nested = self.runtime / mutation
                nested.mkdir()
                original_runtime = self.runtime
                self.runtime = nested
                (self.runtime / "backups").mkdir()
                old = self.backup("old", 1)
                self.backup("new", 2)
                plan = catalog.plan_retention(self.runtime, 1)
                if mutation == "new-backup":
                    self.backup("concurrent", 3)
                elif mutation == "corrupt":
                    (old / "containerops.dump").write_bytes(b"changed")
                elif mutation == "touch":
                    path = old / "metadata.json"
                    os.utime(
                        path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000)
                    )
                else:
                    (self.runtime / "backups/unrecognized.txt").write_text(
                        "unknown", encoding="utf-8"
                    )
                before = self.bytes_and_mtimes()
                with self.assertRaisesRegex(catalog.CatalogError, "obsoleto"):
                    catalog.apply_retention(self.runtime, 1, plan["plan_id"])
                self.assertEqual(self.bytes_and_mtimes(), before)
                self.runtime = original_runtime

    def test_unrecognized_root_file_refuses_apply_even_if_present_in_preview(self):
        self.backup("old", 1)
        self.backup("new", 2)
        (self.runtime / "backups/unknown.txt").write_text("preserve", encoding="utf-8")
        plan = catalog.plan_retention(self.runtime, 1)
        with self.assertRaisesRegex(catalog.CatalogError, "Arquivo desconhecido"):
            catalog.apply_retention(self.runtime, 1, plan["plan_id"])
        self.assertTrue((self.runtime / "backups/old").exists())

    def test_duplicate_json_and_future_schema_stay_unknown(self):
        directory = self.backup("duplicate", 1)
        path = directory / "metadata.json"
        path.write_text('{"sha256":"a","sha256":"b"}', encoding="utf-8")
        future = self.backup("future-schema", 2)
        metadata_path = future / "metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["schema_version"] = metadata["snapshot"]["schema_version"] = 99
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        self.assertTrue(
            all(
                item["validation"] == "unknown"
                for item in catalog.discover(self.runtime)["entries"]
            )
        )

    def test_hard_links_and_windows_reparse_attribute_are_rejected(self):
        directory = self.backup("linked", 1)
        dump = directory / "containerops.dump"
        os.link(dump, self.runtime / "hardlinked-copy")
        with self.assertRaisesRegex(catalog.CatalogError, "Hard links"):
            catalog.discover(self.runtime)
        fake = Mock()
        fake.lstat.return_value = SimpleNamespace(
            st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
        )
        with self.assertRaisesRegex(catalog.CatalogError, "reparse"):
            catalog._check_node(fake, directory=True)

    def test_symlink_directory_is_rejected_without_touching_external_bytes(self):
        external = self.runtime / "external"
        external.mkdir()
        secret = external / "preserve.txt"
        secret.write_bytes(b"external untouched")
        try:
            os.symlink(external, self.runtime / "backups/linked", target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Symlink privilege unavailable: {error}")
        with self.assertRaisesRegex(catalog.CatalogError, "reparse"):
            catalog.plan_retention(self.runtime, 0)
        self.assertEqual(secret.read_bytes(), b"external untouched")

    def test_interruption_during_unlink_is_explicit_and_same_plan_can_recover(self):
        self.backup("old", 1)
        self.backup("latest", 2)
        plan = catalog.plan_retention(self.runtime, 1)
        original_unlink = Path.unlink
        interrupted = False

        def fail_after_unlink(path, *args, **kwargs):
            nonlocal interrupted
            result = original_unlink(path, *args, **kwargs)
            if path.name == "containerops.dump" and ".prune-" in str(path) and not interrupted:
                interrupted = True
                raise KeyboardInterrupt("simulated process interruption")
            return result

        with patch.object(Path, "unlink", fail_after_unlink), self.assertRaises(KeyboardInterrupt):
            catalog.apply_retention(self.runtime, 1, plan["plan_id"])
        pending = catalog.discover(self.runtime)["pending_prune"]
        self.assertEqual(pending["status"], "interrupted")
        with self.assertRaisesRegex(catalog.CatalogError, "interrompida"):
            catalog.plan_retention(self.runtime, 1)
        result = catalog.recover_retention(self.runtime, plan["plan_id"])
        self.assertEqual(result["deleted"], ["old"])
        self.assertIsNone(catalog.discover(self.runtime)["pending_prune"])
        self.assertEqual({path.name for path in (self.runtime / "backups").iterdir()}, {"latest"})

    def test_interruption_after_directory_removal_recovers_without_touching_retained_backup(self):
        self.backup("old", 1)
        retained = self.backup("latest", 2)
        retained_dump = (retained / "containerops.dump").read_bytes()
        plan = catalog.plan_retention(self.runtime, 1)
        original_rmdir = Path.rmdir

        def interrupted_rmdir(path):
            result = original_rmdir(path)
            if path.name == "old" and ".prune-" in str(path):
                raise KeyboardInterrupt("interruption after candidate directory removal")
            return result

        with patch.object(Path, "rmdir", interrupted_rmdir), self.assertRaises(KeyboardInterrupt):
            catalog.apply_retention(self.runtime, 1, plan["plan_id"])

        pending = catalog.discover(self.runtime)["pending_prune"]
        self.assertEqual(pending["status"], "interrupted")
        result = catalog.recover_retention(self.runtime, plan["plan_id"])

        self.assertEqual(result["deleted"], ["old"])
        self.assertIsNone(catalog.discover(self.runtime)["pending_prune"])
        self.assertEqual((retained / "containerops.dump").read_bytes(), retained_dump)
        self.assertEqual({path.name for path in (self.runtime / "backups").iterdir()}, {"latest"})

    def test_interruption_during_move_recovers_exact_candidates(self):
        for day in range(3):
            self.backup(f"copy-{day}", day)
        plan = catalog.plan_retention(self.runtime, 1)
        original_rename = os.rename
        calls = 0

        def interrupted_rename(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt("simulated interruption during move")
            return original_rename(source, target)

        with patch.object(os, "rename", interrupted_rename), self.assertRaises(KeyboardInterrupt):
            catalog.apply_retention(self.runtime, 1, plan["plan_id"])
        result = catalog.recover_retention(self.runtime, plan["plan_id"])
        self.assertEqual(result["deleted"], ["copy-0", "copy-1"])
        self.assertTrue((self.runtime / "backups/copy-2/containerops.dump").exists())

    def test_changed_journal_ids_or_unknown_quarantine_file_refuse_recovery(self):
        self.backup("old", 1)
        self.backup("latest", 2)
        plan = catalog.plan_retention(self.runtime, 1)
        with (
            patch.object(catalog, "_finish_retention", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            catalog.apply_retention(self.runtime, 1, plan["plan_id"])
        path = self.runtime / catalog.JOURNAL_FILE
        original = path.read_bytes()
        journal = json.loads(original)
        journal["candidates"].append("latest")
        path.write_text(json.dumps(journal), encoding="utf-8")
        with self.assertRaisesRegex(catalog.CatalogError, "plano aprovado"):
            catalog.recover_retention(self.runtime, plan["plan_id"])
        path.write_bytes(original)
        extra = self.runtime / "backups" / journal["quarantine"] / "old/unknown.txt"
        extra.write_bytes(b"must survive")
        with self.assertRaisesRegex(catalog.CatalogError, "Arquivo desconhecido"):
            catalog.recover_retention(self.runtime, plan["plan_id"])
        self.assertEqual(extra.read_bytes(), b"must survive")
        self.assertTrue((self.runtime / "backups/latest/containerops.dump").exists())

    def test_changed_pointer_or_concurrent_backup_after_interruption_refuses_recovery(self):
        self.backup("old", 1)
        self.backup("latest", 2)
        plan = catalog.plan_retention(self.runtime, 1)
        with (
            patch.object(catalog, "_finish_retention", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            catalog.apply_retention(self.runtime, 1, plan["plan_id"])
        pointer = self.runtime / "latest-backup.json"
        pointer.write_text(json.dumps({"id": "old"}), encoding="utf-8")
        with self.assertRaisesRegex(catalog.CatalogError, "ponteiros mudaram"):
            catalog.recover_retention(self.runtime, plan["plan_id"])
        pointer.unlink()
        self.backup("concurrent", 3)
        with self.assertRaisesRegex(catalog.CatalogError, "Catálogo mudou"):
            catalog.recover_retention(self.runtime, plan["plan_id"])
        self.assertTrue((self.runtime / "backups/latest/containerops.dump").exists())

    def test_invalid_source_references_image_release_and_tool_stay_unknown(self):
        mutations = (
            {"source_ref": {"revision": "current", "sha256": "a" * 64}},
            {"image_release": "9.0.0"},
            {"tool": "pg_dump (PostgreSQL) 99.0"},
            {"config_digest": "mutable-tag"},
        )
        for index, mutation in enumerate(mutations):
            directory = self.backup(f"invalid-{index}", index)
            path = directory / "metadata.json"
            metadata = json.loads(path.read_text())
            path.write_text(json.dumps({**metadata, **mutation}), encoding="utf-8")
        self.assertTrue(
            all(
                item["validation"] == "unknown"
                for item in catalog.discover(self.runtime)["entries"]
            )
        )

    def test_manifest_corruption_and_archive_without_manifest_stay_unknown(self):
        directory = self.backup("bad-manifest", 1)
        (directory / "manifest.json").write_text("{}", encoding="utf-8")
        directory = self.backup("unmanifested-image", 2)
        (directory / "application-image.tar").write_bytes(b"not-an-approved-image")
        self.assertTrue(
            all(
                item["validation"] == "unknown"
                for item in catalog.discover(self.runtime)["entries"]
            )
        )

    def test_registered_manifest_hash_and_linked_receipt_mtime_are_checked(self):
        import backup_package

        source = self.backup("source", 1)
        metadata_path = source / "metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata.update(
            tool="pg_dump (PostgreSQL) 17.11",
            source_ref={"revision": "a" * 40, "sha256": "b" * 64},
            image_release="2.0.0",
            config_digest="sha256:" + "a" * 64,
        )
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        transport = self.runtime / "transport"
        backup_package.export_backup(
            source, transport, source_context="origin-a", protect=lambda _: {}
        )
        imported, _ = backup_package.import_backup(
            transport, self.runtime / "backups", "imported", protect=lambda _: {}
        )
        catalog.register_backup(self.runtime, "imported", imported=True)
        manifest = imported / "manifest.json"
        manifest.write_bytes(manifest.read_bytes() + b" ")
        entry = next(
            item for item in catalog.discover(self.runtime)["entries"] if item["id"] == "imported"
        )
        self.assertEqual(entry["validation"], "unknown")
        self.backup("newest", 2)
        link = catalog.record_restore_verified(self.runtime, "source", self.verified("source"))
        plan = catalog.plan_retention(self.runtime, 1)
        receipt = self.runtime / "backup-receipts" / link["receipt"]
        os.utime(receipt, ns=(receipt.stat().st_atime_ns, receipt.stat().st_mtime_ns + 1_000_000))
        with self.assertRaisesRegex(catalog.CatalogError, "obsoleto"):
            catalog.apply_retention(self.runtime, 1, plan["plan_id"])

    def test_receipt_catalog_write_failure_does_not_mark_restore_verified(self):
        self.backup("copy", 1)
        original_write = catalog._write

        def failed_catalog_write(path, value):
            if path.name == catalog.CATALOG_FILE:
                raise OSError("simulated catalogue write failure")
            return original_write(path, value)

        with patch.object(catalog, "_write", failed_catalog_write), self.assertRaises(OSError):
            catalog.record_restore_verified(self.runtime, "copy", self.verified("copy"))
        self.assertIsNone(catalog.discover(self.runtime)["entries"][0]["restore_verified"])
        self.assertEqual(list((self.runtime / "backup-receipts").iterdir()), [])

    def test_imported_legacy_bytes_group_by_manifest_origin_when_metadata_context_missing(self):
        import backup_package

        source = self.backup("source", 1)
        path = source / "metadata.json"
        metadata = json.loads(path.read_text())
        del metadata["source_context"]
        metadata.update(
            tool="pg_dump (PostgreSQL) 17.11",
            source_ref={"revision": "a" * 40, "sha256": "b" * 64},
            config_digest="sha256:" + "a" * 64,
        )
        path.write_text(json.dumps(metadata), encoding="utf-8")
        original = path.read_bytes()
        transport = self.runtime / "transport"
        backup_package.export_backup(
            source,
            transport,
            source_context="origin-a",
            image_release="2.0.0",
            protect=lambda _: {},
        )
        imported, _ = backup_package.import_backup(
            transport, self.runtime / "backups", "imported", protect=lambda _: {}
        )
        catalog.register_backup(self.runtime, "imported", imported=True, context="destination")
        self.backup("newer-same-origin", 2)
        entry = next(
            item for item in catalog.discover(self.runtime)["entries"] if item["id"] == "imported"
        )
        self.assertEqual(entry["source_context"], "origin-a")
        self.assertEqual((imported / "metadata.json").read_bytes(), original)
        self.assertTrue(entry["imported"] and entry["protected"])
        self.assertNotIn("imported", catalog.plan_retention(self.runtime, 1)["candidates"])
        catalog.mark_protected(self.runtime, "imported", protected=False, context="destination")
        self.assertIn("imported", catalog.plan_retention(self.runtime, 1)["candidates"])


if __name__ == "__main__":
    unittest.main()
