import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
from signed_archive import INDEX_REL_PATH, RELEASE_NAME, build_signed_archive  # noqa: E402

from package_mgmt.lib.service.index_loader import (  # noqa: E402
    IndexVerificationError,
    fetch_index,
    is_loopback_mirror,
    release_base_for,
)

MIRROR = "https://mirror.invalid"
RELEASE_BASE = f"{MIRROR}/dists/{RELEASE_NAME}"
INDEX_URL = f"{MIRROR}/dists/{RELEASE_NAME}/{INDEX_REL_PATH}"


class _FakeFetch:
    """Serves bytes keyed by URL path (default 404) — no network."""

    def __init__(self, files: dict):
        self.files = files
        self.calls: list[str] = []

    def __call__(self, url: str):
        self.calls.append(url)
        path = urllib.parse.urlsplit(url).path
        data = self.files.get(path)
        if data is None:
            raise urllib.error.HTTPError(url, 404, "not found", {}, __import__("io").BytesIO(b""))
        return data


def _gzip_index(paragraph: str) -> bytes:
    import gzip
    import io

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(paragraph.encode("utf-8"))
    return buf.getvalue()


@unittest.skipUnless(shutil.which("gpg") and shutil.which("gpgv"), "gpg/gpgv required")
class TestIndexSignatureVerification(unittest.TestCase):
    """Spec 012 security hardening: `fetch_index` only loads an index whose
    release file signature verifies against the pinned keyring and whose bytes
    match the signed Release checksum."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.files, self.keyring = build_signed_archive(self.tmp.name)
        self.fetch = _FakeFetch(self.files)

    def tearDown(self):
        self.tmp.cleanup()

    def _fetch(self, dest=None, url=INDEX_URL, **kwargs):
        if dest is None:
            dest = os.path.join(self.tmp.name, "Packages.gz")
        fetch_index(url, dest, release_base=RELEASE_BASE, keyring_path=self.keyring, fetch_fn=self.fetch, **kwargs)
        return dest

    def test_valid_signature_writes_index(self):
        dest = self._fetch()
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), self.files[f"/dists/{RELEASE_NAME}/{INDEX_REL_PATH}"])
        self.assertIn(f"{RELEASE_BASE}/InRelease", self.fetch.calls)
        self.assertIn(INDEX_URL, self.fetch.calls)

    def test_refuses_tampered_index(self):
        self.files[f"/dists/{RELEASE_NAME}/{INDEX_REL_PATH}"] = b"attacker-controlled-bytes"
        dest = os.path.join(self.tmp.name, "Packages.gz")
        with self.assertRaises(IndexVerificationError) as ctx:
            fetch_index(INDEX_URL, dest, release_base=RELEASE_BASE, keyring_path=self.keyring, fetch_fn=self.fetch)
        self.assertIn("checksum mismatch", str(ctx.exception))
        self.assertFalse(os.path.exists(dest))

    def test_refuses_invalid_signature(self):
        self.files[f"/dists/{RELEASE_NAME}/InRelease"] = b"this is not an openpgp signature"
        dest = os.path.join(self.tmp.name, "Packages.gz")
        with self.assertRaises(IndexVerificationError) as ctx:
            fetch_index(INDEX_URL, dest, release_base=RELEASE_BASE, keyring_path=self.keyring, fetch_fn=self.fetch)
        self.assertIn("InRelease", str(ctx.exception))
        self.assertFalse(os.path.exists(dest))

    def test_refuses_signature_by_untrusted_key(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        other_files, other_keyring = build_signed_archive(other.name)
        dest = os.path.join(self.tmp.name, "Packages.gz")
        # Serve our archive but verify with a different keyring.
        with self.assertRaises(IndexVerificationError):
            fetch_index(INDEX_URL, dest, release_base=RELEASE_BASE, keyring_path=other_keyring, fetch_fn=self.fetch)
        self.assertFalse(os.path.exists(dest))

    def test_falls_back_to_release_and_release_gpg(self):
        del self.files[f"/dists/{RELEASE_NAME}/InRelease"]
        dest = self._fetch()
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), self.files[f"/dists/{RELEASE_NAME}/{INDEX_REL_PATH}"])
        self.assertIn(f"{RELEASE_BASE}/Release", self.fetch.calls)
        self.assertIn(f"{RELEASE_BASE}/Release.gpg", self.fetch.calls)

    def test_refuses_when_no_signature_files(self):
        del self.files[f"/dists/{RELEASE_NAME}/InRelease"]
        del self.files[f"/dists/{RELEASE_NAME}/Release"]
        del self.files[f"/dists/{RELEASE_NAME}/Release.gpg"]
        dest = os.path.join(self.tmp.name, "Packages.gz")
        with self.assertRaises(IndexVerificationError) as ctx:
            fetch_index(INDEX_URL, dest, release_base=RELEASE_BASE, keyring_path=self.keyring, fetch_fn=self.fetch)
        self.assertIn("no signed release file", str(ctx.exception))
        self.assertFalse(os.path.exists(dest))

    def test_refuses_index_not_listed_in_release(self):
        other = "contrib/binary-amd64/Packages.gz"
        url = f"{MIRROR}/dists/{RELEASE_NAME}/{other}"
        dest = os.path.join(self.tmp.name, "Packages.gz")
        with self.assertRaises(IndexVerificationError) as ctx:
            fetch_index(url, dest, release_base=RELEASE_BASE, keyring_path=self.keyring, fetch_fn=self.fetch)
        self.assertIn("not listed in the signed release", str(ctx.exception))
        self.assertFalse(os.path.exists(dest))

    def test_refuses_url_outside_release_dir(self):
        dest = os.path.join(self.tmp.name, "Packages.gz")
        with self.assertRaises(IndexVerificationError) as ctx:
            fetch_index("https://mirror.invalid/debian/pool/x.deb", dest,
                        release_base=RELEASE_BASE, keyring_path=self.keyring, fetch_fn=self.fetch)
        self.assertIn("not under release directory", str(ctx.exception))
        self.assertFalse(os.path.exists(dest))


class TestReleaseBaseHelpers(unittest.TestCase):
    def test_remote_mirror_gets_release_base(self):
        self.assertEqual(
            release_base_for("https://deb.debian.org/debian", "trixie"),
            "https://deb.debian.org/debian/dists/trixie",
        )

    def test_loopback_mirror_skips_verification(self):
        self.assertIsNone(release_base_for("http://127.0.0.1:8000", "trixie"))
        self.assertIsNone(release_base_for("http://localhost:8000", "trixie"))

    def test_is_loopback_mirror(self):
        self.assertTrue(is_loopback_mirror("http://127.0.0.1:8000"))
        self.assertTrue(is_loopback_mirror("http://localhost:8000"))
        self.assertFalse(is_loopback_mirror("https://deb.debian.org/debian"))
        self.assertFalse(is_loopback_mirror("http://mirror.example.com"))


if __name__ == "__main__":
    unittest.main()
