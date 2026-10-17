import gzip
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backup_package as package


def digest(content):
    return hashlib.sha256(content).hexdigest()


def protected(directory):
    return {"method": "fixture", "verified_files": len(list(directory.iterdir()))}


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


class BackupPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="containerops-package-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.backup = self.root / "original"
        self.backup.mkdir()
        content = b"PGDMP synthetic logical dump"
        (self.backup / "containerops.dump").write_bytes(content)
        self.source = {"revision": "uncommitted", "sha256": digest(b"{}"), "files": {}}
        self.metadata = {
            "created_at": "2026-01-02T03:04:06+00:00",
            "cutoff_interval": {
                "started_at": "2026-01-02T03:04:04+00:00",
                "completed_at": "2026-01-02T03:04:05+00:00",
                "method": "paused and drained",
            },
            "sha256": digest(content),
            "server": "postgres (PostgreSQL) 17.11",
            "tool": "pg_dump (PostgreSQL) 17.11",
            "schema_version": 2,
            "snapshot": {"schema_version": 2, "jobs": []},
            "source_project": "pf-containerops-test-12345678",
            "source_context": "source-fixture",
            "source_ref": self.source,
            "image": "sha256:" + "a" * 64,
            "config_digest": "sha256:" + "a" * 64,
            "image_release": "1.0.0",
        }
        self.save_metadata()
        self.transport = self.root / "transport"
        self.destination = self.root / "backups"
        self.destination.mkdir()

    def save_metadata(self):
        write_json(self.backup / "metadata.json", self.metadata)

    def export(self, **kwargs):
        return package.export_backup(
            self.backup,
            self.transport,
            source_context="source-fixture",
            protect=protected,
            **kwargs,
        )

    def import_package(self):
        return package.import_backup(
            self.transport, self.destination, "imported-fixture", protect=protected
        )

    def test_roundtrip_preserves_bytes_identity_and_latest_pointer(self):
        original = {path.name: path.read_bytes() for path in self.backup.iterdir()}
        pointer = self.root / "latest-backup.json"
        pointer.write_bytes(b"unchanged pointer")
        before = pointer.stat()
        exported = self.export()
        with patch.object(package, "preflight_image") as image:
            copied, record = self.import_package()
        image.assert_not_called()
        self.assertEqual(record["origin"]["context"], "source-fixture")
        self.assertTrue(record["imported"] and record["protected"])
        self.assertEqual(package.validate_backup(copied)["metadata"], self.metadata)
        for name, content in original.items():
            self.assertEqual((copied / name).read_bytes(), content)
            self.assertEqual((self.backup / name).read_bytes(), content)
        self.assertEqual(pointer.read_bytes(), b"unchanged pointer")
        self.assertEqual(pointer.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertNotIn(str(self.root), json.dumps(exported["manifest"]))
        self.assertEqual(
            set(path.name for path in copied.iterdir()),
            {"containerops.dump", "metadata.json", "manifest.json"},
        )
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(copied.stat().st_mode), 0o700)
            self.assertTrue(
                all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in copied.iterdir())
            )

    def test_published_import_keeps_trust_and_protection_after_catalog_failure(self):
        import backup_catalog as catalog
        import ops

        self.export()
        directory, _ = self.import_package()
        runtime = self.destination.parent
        with patch.object(catalog, "_write", side_effect=OSError("interrupted catalog write")):
            with self.assertRaises(OSError):
                catalog.register_backup(runtime, directory.name, imported=True, protected=True)
        # Open the runtime in a fresh interpreter: no in-memory import state survives.
        reopened = subprocess.run(
            [
                sys.executable,
                "-c",
                "import backup_catalog as c,json,sys; print(json.dumps(c.discover(sys.argv[1])))",
                str(runtime),
            ],
            cwd=Path(__file__).resolve().parents[1] / "scripts",
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        entry = json.loads(reopened.stdout)["entries"][0]
        self.assertEqual(entry["validation"], "valid")
        self.assertTrue(entry["imported"] and entry["protected"])
        # A later copy removes the independent last-valid exemption from this ID.
        package.import_backup(self.transport, self.destination, "zz-later-copy", protect=protected)
        catalog.register_backup(runtime, "zz-later-copy", imported=True, protected=True)
        self.assertEqual(catalog.plan_retention(runtime, 0)["candidates"], [])
        with (
            patch.object(ops, "RUNTIME", runtime),
            patch.object(ops, "CONTEXT", SimpleNamespace(session=None)),
            patch.object(ops, "evidence"),
            patch.object(ops, "restore_preflight") as preflight,
        ):
            with self.assertRaisesRegex(RuntimeError, "trusted-source"):
                ops.restore_test(backup_id=directory.name, trusted_source=False)
            preflight.assert_not_called()
        # Recovery through the existing protect command cannot erase the import flag.
        catalog.mark_protected(runtime, directory.name)
        catalog.register_backup(runtime, directory.name)
        recovered = catalog.discover(runtime)["entries"][0]
        self.assertTrue(recovered["imported"] and recovered["protected"])
        self.assertEqual(
            package.validate_backup(directory)["files"],
            package.validate_backup(self.transport)["files"],
        )

    def test_interruption_immediately_after_rename_is_conservatively_discovered(self):
        import backup_catalog as catalog

        self.export()
        finalize = package._finalize

        def interrupted(stage, destination):
            finalize(stage, destination)
            raise OSError("interrupted after publication")

        with patch.object(package, "_finalize", interrupted), self.assertRaises(OSError):
            self.import_package()
        entry = catalog.discover(self.destination.parent)["entries"][0]
        self.assertEqual(entry["validation"], "valid")
        self.assertTrue(entry["imported"] and entry["protected"])

    def test_corrupt_dump_rejected_before_any_destination_created(self):
        self.export()
        with (self.transport / "containerops.dump").open("r+b") as dump:
            dump.write(b"X")
        with self.assertRaisesRegex(RuntimeError, "Checksum de backup inválido"):
            self.import_package()
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_incomplete_extra_and_duplicate_json_rejected(self):
        self.export()
        manifest = (self.transport / "manifest.json").read_bytes()
        (self.transport / "manifest.json").unlink()
        with self.assertRaisesRegex(RuntimeError, "manifesto ausente"):
            self.import_package()
        (self.transport / "manifest.json").write_bytes(manifest)
        (self.transport / "state.json").write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "itens extras"):
            self.import_package()
        (self.transport / "state.json").unlink()
        (self.transport / "manifest.json").write_text('{"format":1,"format":2}')
        with self.assertRaisesRegex(RuntimeError, "duplicada"):
            self.import_package()
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_manifest_future_format_and_strict_sizes(self):
        self.export()
        path = self.transport / "manifest.json"
        manifest = package.read_json(path)
        for mutation, message in (
            (lambda value: value.update(format_version=99), "Formato"),
            (lambda value: value.update(format_version=True), "Formato"),
            (lambda value: value["files"]["containerops.dump"].update(size=True), "Tamanho"),
            (lambda value: value.update(destination="../../elsewhere"), "Campo"),
        ):
            with self.subTest(message=message):
                changed = json.loads(json.dumps(manifest))
                mutation(changed)
                write_json(path, changed)
                with self.assertRaisesRegex(RuntimeError, message):
                    self.import_package()

    def test_schema_cutoff_dates_types_and_origin_rejected(self):
        for change, message in (
            ({"schema_version": 99}, "Schema"),
            ({"schema_version": True}, "Schema"),
            ({"created_at": "2099-01-01T00:00:00Z"}, "futuro"),
            ({"created_at": "2026-01-02T03:04:06"}, "timezone"),
            ({"source_project": "other-project"}, "Origem"),
            ({"source_context": "../../other"}, "Contexto"),
            ({"image": "containerops-app:1.0.0"}, "ImageID"),
            ({"server": "postgres (PostgreSQL) 18.0"}, "PostgreSQL"),
            ({"tool": "pg_dump (PostgreSQL) 16.0"}, "pg_dump"),
        ):
            with self.subTest(change=change):
                metadata = {**self.metadata, **change}
                with self.assertRaisesRegex(RuntimeError, message):
                    package.validate_metadata(metadata, portable=True)
        cutoff = {**self.metadata["cutoff_interval"], "started_at": "2026-01-02T03:04:06Z"}
        with self.assertRaisesRegex(RuntimeError, "Janela"):
            package.validate_metadata({**self.metadata, "cutoff_interval": cutoff})
        with self.assertRaisesRegex(RuntimeError, "janela"):
            package.validate_metadata(
                {key: value for key, value in self.metadata.items() if key != "cutoff_interval"},
                portable=True,
            )

    def test_legacy_without_cutoff_validates_but_cannot_export(self):
        self.metadata.pop("cutoff_interval")
        self.save_metadata()
        package.validate_backup(self.backup)
        with self.assertRaisesRegex(RuntimeError, "janela"):
            self.export()

    def test_links_and_reparse_points_are_rejected(self):
        self.export()
        dump = self.transport / "containerops.dump"
        original = self.root / "linked-original"
        dump.rename(original)
        os.link(original, dump)
        with self.assertRaisesRegex(RuntimeError, "links"):
            self.import_package()
        dump.unlink()
        original.rename(dump)
        native_lstat = Path.lstat

        def reparse(path):
            item = native_lstat(path)
            return (
                SimpleNamespace(st_mode=item.st_mode, st_file_attributes=0x400)
                if path == self.transport
                else item
            )

        with patch.object(Path, "lstat", reparse):
            with self.assertRaisesRegex(RuntimeError, "reparse"):
                self.import_package()

    def test_copy_failure_in_each_phase_keeps_catalog_destination_empty(self):
        self.export()
        native_copy = package._copy
        for failed_phase in range(1, 4):
            counter = 0

            def interrupted(*args):
                nonlocal counter
                counter += 1
                native_copy(*args)
                if counter == failed_phase:
                    raise OSError("interrupted copy")

            with self.subTest(phase=failed_phase), patch.object(package, "_copy", interrupted):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    self.import_package()
            self.assertEqual(list(self.destination.iterdir()), [])

    def test_export_protection_metadata_and_finalization_failure_cleanup(self):
        for target in ("_protect", "_write_json", "_finalize"):
            with (
                self.subTest(target=target),
                patch.object(package, target, side_effect=OSError("interrupted")),
            ):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    self.export()
            self.assertFalse(self.transport.exists())
            self.assertFalse(list(self.root.glob(".containerops-export-*")))

    def test_existing_destination_and_concurrent_reservation_preserved(self):
        self.transport.mkdir()
        sentinel = self.transport / "foreign"
        sentinel.write_bytes(b"keep")
        with self.assertRaisesRegex(RuntimeError, "Destino já existe"):
            self.export()
        self.assertEqual(sentinel.read_bytes(), b"keep")
        sentinel.unlink()
        self.transport.rmdir()
        self.export()
        native_finalize = package._finalize

        def reserve(stage, destination):
            destination.mkdir()
            (destination / "foreign").write_bytes(b"keep")
            native_finalize(stage, destination)

        with patch.object(package, "_finalize", reserve):
            with self.assertRaisesRegex(RuntimeError, "Destino já existe"):
                self.import_package()
        self.assertEqual((self.destination / "imported-fixture" / "foreign").read_bytes(), b"keep")
        self.assertFalse(list(self.destination.glob(".containerops-import-*")))

    def test_source_change_during_copy_rejected(self):
        self.export()
        native_record = package.file_record

        def altered(path, **kwargs):
            result = native_record(path, **kwargs)
            if path == self.transport / "containerops.dump" and kwargs.get("output") is not None:
                path.write_bytes(b"X" * result["size"])
            return result

        with patch.object(package, "file_record", altered):
            with self.assertRaisesRegex(RuntimeError, "Cópia privada diverge"):
                self.import_package()
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_limits_checked_before_copy(self):
        with patch.dict(package.LIMITS, {"containerops.dump": 4}):
            with self.assertRaisesRegex(RuntimeError, "limite"):
                self.export()
        self.assertFalse(self.transport.exists())

    def image_inspect(self, *, image_id=None, release="1.0.0"):
        return Mock(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "Id": image_id or self.metadata["image"],
                        "Os": "linux",
                        "Architecture": "amd64",
                        "Config": {
                            "Labels": {
                                "org.opencontainers.image.title": "ContainerOps",
                                "org.opencontainers.image.source": "https://github.com/arthurjoanes/containerops",
                                "org.opencontainers.image.version": release,
                            }
                        },
                    }
                ]
            ),
        )

    def test_image_preflight_missing_incompatible_and_release_one_schema_two(self):
        with self.assertRaisesRegex(RuntimeError, "backup-image-load"):
            package.preflight_image(self.metadata, Mock(return_value=Mock(returncode=1)))
        with self.assertRaisesRegex(RuntimeError, "incompatível"):
            package.preflight_image(
                self.metadata, Mock(return_value=self.image_inspect(release="2.0.0"))
            )
        self.assertEqual(
            package.preflight_image(self.metadata, Mock(return_value=self.image_inspect())),
            self.metadata["image"],
        )
        config = "sha256:" + "b" * 64
        metadata = {**self.metadata, "config_digest": config}
        inspect = Mock(side_effect=[Mock(returncode=1), self.image_inspect(image_id=config)])
        self.assertEqual(package.preflight_image(metadata, inspect), config)

    def create_image(
        self, *, tags=None, oci=False, classic_oci=False, compressed=False, link=False
    ):
        layer = b"synthetic layer bytes"
        raw_layer = gzip.compress(layer, mtime=0) if compressed else layer
        config = {
            "os": "linux",
            "architecture": "amd64",
            "rootfs": {"diff_ids": ["sha256:" + digest(layer)]},
            "config": {
                "Labels": {
                    "org.opencontainers.image.title": "ContainerOps",
                    "org.opencontainers.image.source": "https://github.com/arthurjoanes/containerops",
                    "org.opencontainers.image.version": "1.0.0",
                }
            },
        }
        config_bytes = json.dumps(config).encode()
        config_digest = "sha256:" + digest(config_bytes)
        image_id = config_digest
        config_name = "config.json"
        layer_name = "layer.tar"
        if oci or classic_oci:
            config_name = "blobs/sha256/" + config_digest.split(":")[1]
            layer_name = "blobs/sha256/" + digest(raw_layer)
        files = {
            config_name: config_bytes,
            layer_name: raw_layer,
            "manifest.json": json.dumps(
                [{"Config": config_name, "RepoTags": tags, "Layers": [layer_name]}]
            ).encode(),
        }
        archive_manifest_digest = None
        if oci or classic_oci:
            payload = json.dumps(
                {
                    "schemaVersion": 2,
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "config": {
                        "mediaType": "application/vnd.oci.image.config.v1+json",
                        "digest": config_digest,
                        "size": len(config_bytes),
                    },
                    "layers": [
                        {
                            "mediaType": "application/vnd.oci.image.layer.v1.tar"
                            + ("+gzip" if compressed else ""),
                            "digest": "sha256:" + digest(raw_layer),
                            "size": len(raw_layer),
                        }
                    ],
                }
            ).encode()
            archive_manifest_digest = "sha256:" + digest(payload)
            if not classic_oci:
                image_id = archive_manifest_digest
            files["index.json"] = json.dumps(
                {
                    "schemaVersion": 2,
                    "mediaType": "application/vnd.oci.image.index.v1+json",
                    "manifests": [
                        {
                            "mediaType": "application/vnd.oci.image.manifest.v1+json",
                            "digest": archive_manifest_digest,
                            "size": len(payload),
                        }
                    ],
                }
            ).encode()
            files["blobs/sha256/" + archive_manifest_digest.split(":")[1]] = payload
        path = self.root / "synthetic-image.tar"
        with tarfile.open(path, "w") as archive:
            for name, content in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            if link:
                member = tarfile.TarInfo("linked")
                member.type = tarfile.SYMTYPE
                member.linkname = "config.json"
                archive.addfile(member)
        self.metadata.update(image=image_id, config_digest=config_digest)
        self.save_metadata()
        return path, {
            "image_id": image_id,
            "config_digest": config_digest,
            "oci_manifest_digest": "sha256:" + "d" * 64 if classic_oci else archive_manifest_digest,
            "release": "1.0.0",
            "archive": "application-image.tar",
        }

    def test_archive_distinguishes_oci_identity_and_rejects_shared_tags_links(self):
        path, identity = self.create_image(oci=True)
        self.assertNotEqual(identity["image_id"], identity["config_digest"])
        self.assertEqual(
            package.inspect_application_archive(path, identity)["config_digest"],
            identity["config_digest"],
        )
        path, identity = self.create_image(tags=["containerops-app:1.0.0"])
        with self.assertRaisesRegex(RuntimeError, "tags compartilhadas"):
            package.inspect_application_archive(path, identity)
        path, identity = self.create_image(link=True)
        with self.assertRaisesRegex(RuntimeError, "link"):
            package.inspect_application_archive(path, identity)

    def test_classic_config_image_id_accepts_separately_hashed_export_manifest(self):
        path, identity = self.create_image(classic_oci=True)
        with tarfile.open(path) as archive:
            descriptor = json.load(archive.extractfile("index.json"))["manifests"][0]
        self.assertEqual(identity["image_id"], identity["config_digest"])
        self.assertNotEqual(descriptor["digest"], identity["image_id"])
        self.assertNotEqual(descriptor["digest"], identity["oci_manifest_digest"])
        inspected = package.inspect_application_archive(path, identity)
        self.assertEqual(inspected["archive_manifest_digest"], descriptor["digest"])
        self.assertEqual(inspected["image_id"], identity["image_id"])
        self.assertFalse(inspected["shared_tags"])
        with self.assertRaisesRegex(RuntimeError, "ImageID OCI ausente"):
            package.inspect_application_archive(
                path, {**identity, "image_id": "sha256:" + "e" * 64}
            )

    def test_classic_oci_gzip_descriptor_hashes_compressed_bytes_and_diff_id(self):
        path, identity = self.create_image(classic_oci=True, compressed=True)
        with tarfile.open(path) as archive:
            descriptor = json.load(archive.extractfile("index.json"))["manifests"][0]
            manifest = json.load(
                archive.extractfile("blobs/sha256/" + descriptor["digest"].split(":")[1])
            )
            config = json.load(
                archive.extractfile("blobs/sha256/" + identity["config_digest"].split(":")[1])
            )
        self.assertNotEqual(manifest["layers"][0]["digest"], config["rootfs"]["diff_ids"][0])
        self.assertEqual(
            package.inspect_application_archive(path, identity)["config_digest"],
            identity["config_digest"],
        )

    def test_classic_oci_linkage_tampering_rejected_even_with_new_manifest_hash(self):
        for mutation, message in (
            (lambda value: value["config"].update(digest="sha256:" + "f" * 64), "outro config"),
            (lambda value: value["config"].update(size=True), "Config OCI"),
            (
                lambda value: value["layers"][0].update(digest="sha256:" + "f" * 64),
                "camada OCI diverge",
            ),
            (lambda value: value["layers"][0].update(size=True), "camada OCI diverge"),
            (
                lambda value: value["layers"][0].update(
                    mediaType="application/vnd.oci.image.layer.v1.tar+gzip"
                ),
                "Compressão",
            ),
        ):
            with self.subTest(message=message):
                path, identity = self.create_image(classic_oci=True)
                with tarfile.open(path) as archive:
                    values = {
                        member.name: archive.extractfile(member).read()
                        for member in archive
                        if member.isfile()
                    }
                index = json.loads(values["index.json"])
                descriptor = index["manifests"][0]
                manifest = json.loads(
                    values.pop("blobs/sha256/" + descriptor["digest"].split(":")[1])
                )
                mutation(manifest)
                content = json.dumps(manifest).encode()
                descriptor.update(digest="sha256:" + digest(content), size=len(content))
                values["index.json"] = json.dumps(index).encode()
                values["blobs/sha256/" + digest(content)] = content
                with tarfile.open(path, "w") as archive:
                    for name, value in values.items():
                        member = tarfile.TarInfo(name)
                        member.size = len(value)
                        archive.addfile(member, io.BytesIO(value))
                with self.assertRaisesRegex(RuntimeError, message):
                    package.inspect_application_archive(path, identity)

    def test_oci_containerd_naming_annotation_cannot_replace_shared_tag(self):
        path, identity = self.create_image(oci=True)
        with tarfile.open(path) as archive:
            values = {
                member.name: archive.extractfile(member).read()
                for member in archive
                if member.isfile()
            }
        index = json.loads(values["index.json"])
        index["manifests"][0]["annotations"] = {
            "io.containerd.image.name": "containerops-app:1.0.0"
        }
        values["index.json"] = json.dumps(index).encode()
        with tarfile.open(path, "w") as archive:
            for name, value in values.items():
                member = tarfile.TarInfo(name)
                member.size = len(value)
                archive.addfile(member, io.BytesIO(value))
        with self.assertRaisesRegex(RuntimeError, "tag compartilhada"):
            package.inspect_application_archive(path, identity)

    def export_image(self):
        path, _ = self.create_image()

        def docker(args, **kwargs):
            if args[:3] == ["docker", "image", "save"]:
                Path(args[4]).write_bytes(path.read_bytes())
                self.assertEqual(args[-1], self.metadata["image"])
                return Mock(returncode=0)
            return self.image_inspect()

        run = Mock(side_effect=docker)
        self.export(include_image=True, run=run)
        return run

    def test_image_export_is_explicit_by_id_and_import_never_loads_it(self):
        run = self.export_image()
        self.assertEqual(
            sum(call.args[0][:3] == ["docker", "image", "save"] for call in run.call_args_list), 1
        )
        with patch.object(package, "preflight_image") as preflight:
            copied, _ = self.import_package()
        preflight.assert_not_called()
        self.assertTrue((copied / "application-image.tar").is_file())

    def test_optional_image_copy_interruption_cleans_staging(self):
        self.export_image()
        native_copy = package._copy

        def interrupted(source, target, expected):
            native_copy(source, target, expected)
            if source.name == "application-image.tar":
                raise OSError("image copy interrupted")

        with patch.object(package, "_copy", interrupted):
            with self.assertRaisesRegex(OSError, "image copy interrupted"):
                self.import_package()
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_import_protection_and_finalization_interruptions_clean_staging(self):
        self.export()
        original = {path.name: path.read_bytes() for path in self.transport.iterdir()}
        for target in ("_protect", "_finalize"):
            with (
                self.subTest(target=target),
                patch.object(package, target, side_effect=OSError("interrupted")),
            ):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    self.import_package()
            self.assertEqual(list(self.destination.iterdir()), [])
            self.assertEqual(
                {path.name: path.read_bytes() for path in self.transport.iterdir()}, original
            )

    def test_image_load_requires_trust_exclusive_tag_and_checks_collision(self):
        self.export_image()
        run = Mock()
        with self.assertRaisesRegex(RuntimeError, "trusted-image-source"):
            package.load_application_image(
                self.transport, run=run, tag="containerops-fixture-app:1.0.0"
            )
        with self.assertRaisesRegex(RuntimeError, "exclusiva"):
            package.load_application_image(
                self.transport, run=run, tag="containerops-app:1.0.0", trusted=True
            )
        run.assert_not_called()
        run.return_value = self.image_inspect(image_id="sha256:" + "f" * 64)
        with self.assertRaisesRegex(RuntimeError, "outro conteúdo"):
            package.load_application_image(
                self.transport, run=run, tag="containerops-fixture-app:1.0.0", trusted=True
            )
        self.assertEqual(len(run.call_args_list), 1)

    def test_explicit_load_checks_exact_image_before_tag(self):
        self.export_image()

        def docker(args, **kwargs):
            if args[:3] == ["docker", "image", "inspect"]:
                return (
                    Mock(returncode=1)
                    if args[-1].startswith("containerops-")
                    else self.image_inspect()
                )
            return Mock(returncode=0)

        run = Mock(side_effect=docker)
        result = package.load_application_image(
            self.transport,
            run=run,
            tag="containerops-fixture-app:1.0.0",
            trusted=True,
            protect=protected,
        )
        self.assertEqual(result["image_id"], self.metadata["image"])
        loaded = [
            call.args[0] for call in run.call_args_list if call.args[0][:2] == ["docker", "load"]
        ]
        self.assertEqual(len(loaded), 1)
        self.assertTrue(Path(loaded[0][-1]).parent.name.startswith(".containerops-load-"))
        self.assertFalse(Path(loaded[0][-1]).exists())
        self.assertEqual(
            run.call_args.args[0],
            ["docker", "tag", self.metadata["image"], "containerops-fixture-app:1.0.0"],
        )

    def test_loaded_archive_changes_are_rejected_before_tag(self):
        self.export_image()

        def docker(args, **kwargs):
            if args[:3] == ["docker", "image", "inspect"]:
                return Mock(returncode=1)
            if args[:2] == ["docker", "load"]:
                Path(args[-1]).write_bytes(b"changed image")
            return Mock(returncode=0)

        run = Mock(side_effect=docker)
        with self.assertRaisesRegex(RuntimeError, "mudou durante a carga"):
            package.load_application_image(
                self.transport,
                run=run,
                tag="containerops-fixture-app:1.0.0",
                trusted=True,
                protect=protected,
            )
        self.assertFalse(any(call.args[0][:2] == ["docker", "tag"] for call in run.call_args_list))
        self.assertFalse(list(self.root.glob(".containerops-load-*")))


if __name__ == "__main__":
    unittest.main()
