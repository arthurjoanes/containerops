import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backup_catalog
import backup_package


class FileIdentityTests(unittest.TestCase):
    def test_regular_file_modified_before_read_is_not_reported_as_concurrent_change(self):
        with tempfile.TemporaryDirectory(prefix="containerops-file-identity-") as directory:
            path = Path(directory) / "containerops.dump"
            path.write_bytes(b"original")
            path.write_bytes(b"modified")
            stamp = path.stat()
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))

            for reader in (backup_catalog._file, backup_package.file_record):
                with self.subTest(reader=reader.__name__):
                    result = reader(path, 100) if reader is backup_catalog._file else reader(path)

                    self.assertEqual(result["sha256"], hashlib.sha256(b"modified").hexdigest())
                    self.assertEqual(result["size"], 8)
