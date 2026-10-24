import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
from signed_archive import (  # noqa: E402
    INDEX_REL_PATH,
    RELEASE_NAME,
    build_signed_archive,
    serve_signed_archive,
)

from package_mgmt.lib.service.index_loader import (  # noqa: E402
    IndexVerificationError,
    fetch_index,
)


@unittest.skipUnless(shutil.which("gpg") and shutil.which("gpgv"), "gpg/gpgv required")
class IndexVerifyFlowTest(unittest.TestCase):
    """Spec 012 security hardening: real HTTP + real gpgv — `fetch_index` only
    writes an index whose release-file signature verifies against the pinned
    keyring and whose bytes match the signed Release checksum."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.files, self.keyring = build_signed_archive(self.tmp.name)
        self.server = serve_signed_archive(self.files)
        port = self.server.server_address[1]
        self.mirror = f"http://127.0.0.1:{port}"
        self.release_base = f"{self.mirror}/dists/{RELEASE_NAME}"
        self.index_url = f"{self.mirror}/dists/{RELEASE_NAME}/{INDEX_REL_PATH}"

    def tearDown(self):
        self.server.shutdown()
        self.server.thread.join()
        self.tmp.cleanup()

    def test_index_loaded_over_http_after_signature_verification(self):
        dest = os.path.join(self.tmp.name, "binary", "dists", RELEASE_NAME, "main", "binary-amd64", "Packages.gz")
        fetch_index(self.index_url, dest, release_base=self.release_base, keyring_path=self.keyring)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), self.files[f"/dists/{RELEASE_NAME}/{INDEX_REL_PATH}"])

    def test_tampered_index_refused_over_http(self):
        self.files[f"/dists/{RELEASE_NAME}/{INDEX_REL_PATH}"] = b"evil-bytes"
        dest = os.path.join(self.tmp.name, "Packages.gz")
        with self.assertRaises(IndexVerificationError) as ctx:
            fetch_index(self.index_url, dest, release_base=self.release_base, keyring_path=self.keyring)
        self.assertIn("checksum mismatch", str(ctx.exception))
        self.assertFalse(os.path.exists(dest))


if __name__ == "__main__":
    unittest.main()
