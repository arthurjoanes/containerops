import gzip
import hashlib
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from oci_audit import add_bytes, inspect_docker_archive


class DockerExportTests(unittest.TestCase):
    def archive(self, path, *, corrupt=False):
        contents = [b"base layer tar bytes", b"application layer tar bytes"]
        diff_ids = ["sha256:" + hashlib.sha256(layer).hexdigest() for layer in contents]
        config = json.dumps({"rootfs": {"diff_ids": diff_ids}}).encode()
        config_id = "sha256:" + hashlib.sha256(config).hexdigest()
        with tarfile.open(path, "w") as archive:
            add_bytes(archive, "config.json", config)
            add_bytes(
                archive,
                "manifest.json",
                json.dumps(
                    [{"Config": "config.json", "Layers": ["base.tar.gz", "app.tar"]}]
                ).encode(),
            )
            add_bytes(
                archive,
                "base.tar.gz",
                gzip.compress(b"changed content" if corrupt else contents[0]),
            )
            add_bytes(archive, "app.tar", contents[1])
        return {"config_digest": config_id, "diff_ids": diff_ids}

    def test_mixed_gzip_and_tar_layers_match_uncompressed_diff_ids(self):
        with tempfile.TemporaryDirectory(prefix="containerops-oci-") as directory:
            path = Path(directory) / "export.tar"
            expected = self.archive(path)
            actual = inspect_docker_archive(path, expected, expected["config_digest"])
            self.assertEqual(actual["diff_ids"], expected["diff_ids"])

    def test_changed_gzip_content_is_rejected_even_with_expected_config(self):
        with tempfile.TemporaryDirectory(prefix="containerops-oci-") as directory:
            path = Path(directory) / "export.tar"
            expected = self.archive(path, corrupt=True)
            with self.assertRaisesRegex(RuntimeError, "Conteúdo das camadas"):
                inspect_docker_archive(path, expected, expected["config_digest"])
