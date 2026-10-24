"""Tests for the shared filesystem/hash helpers (`lib/util/fs.py`, F1).

The three helpers are the single typed home for the file-hash, atomic-write
and atomic-copy patterns used across the service layer, and each is
behavior-compatible with the private implementation it replaces. These tests
pin the shared contract directly (the F1 call-site tests cover the re-points).
"""

import hashlib
import os
import tempfile
import unittest

from package_mgmt.lib.util.fs import atomic_copy, atomic_write_bytes, file_sha256


class TestFileSha256(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "payload.bin")

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, data: bytes):
        with open(self.path, "wb") as f:
            f.write(data)

    def test_matches_stdlib_digest(self):
        self._write(b"hello world\n")
        expected = hashlib.sha256(b"hello world\n").hexdigest()
        self.assertEqual(file_sha256(self.path), expected)

    def test_empty_file(self):
        self._write(b"")
        expected = hashlib.sha256(b"").hexdigest()
        self.assertEqual(file_sha256(self.path), expected)

    def test_missing_file_raises_by_default(self):
        with self.assertRaises(OSError):
            file_sha256(os.path.join(self.tmp.name, "missing.bin"))

    def test_missing_file_errors_none_returns_none(self):
        self.assertIsNone(
            file_sha256(os.path.join(self.tmp.name, "missing.bin"), errors="none")
        )

    def test_unreadable_file_errors_none_returns_none(self):
        self._write(b"data")
        os.chmod(self.path, 0o000)
        try:
            self.assertIsNone(file_sha256(self.path, errors="none"))
        finally:
            os.chmod(self.path, 0o644)


class TestAtomicWriteBytes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sub", "out.txt")

    def tearDown(self):
        self.tmp.cleanup()

    def test_creates_parent_directories(self):
        atomic_write_bytes(self.path, b"payload")
        self.assertTrue(os.path.isfile(self.path))
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), b"payload")

    def test_replaces_existing_content(self):
        atomic_write_bytes(self.path, b"old")
        atomic_write_bytes(self.path, b"new")
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), b"new")

    def test_respects_mode(self):
        atomic_write_bytes(self.path, b"data", mode=0o600)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_no_partial_files_left_after_success(self):
        atomic_write_bytes(self.path, b"data")
        leftovers = [
            name for name in os.listdir(os.path.dirname(self.path))
            if name.endswith(".part")
        ]
        self.assertEqual(leftovers, [])


class TestAtomicCopy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "src.bin")
        self.dst = os.path.join(self.tmp.name, "sub", "dst.bin")
        with open(self.src, "wb") as f:
            f.write(b"copy me")

    def tearDown(self):
        self.tmp.cleanup()

    def test_copies_content_and_creates_parents(self):
        atomic_copy(self.src, self.dst)
        with open(self.dst, "rb") as f:
            self.assertEqual(f.read(), b"copy me")

    def test_verify_match_succeeds(self):
        expected = (file_sha256(self.src), os.path.getsize(self.src))
        atomic_copy(self.src, self.dst, verify=expected)
        with open(self.dst, "rb") as f:
            self.assertEqual(f.read(), b"copy me")

    def test_verify_mismatch_raises_and_leaves_no_destination(self):
        bad = ("0" * 64, os.path.getsize(self.src))
        with self.assertRaises(OSError):
            atomic_copy(self.src, self.dst, verify=bad)
        self.assertFalse(os.path.exists(self.dst))

    def test_verify_size_mismatch_raises(self):
        with self.assertRaises(OSError):
            atomic_copy(self.src, self.dst, verify=(file_sha256(self.src), 9999))
        self.assertFalse(os.path.exists(self.dst))

    def test_no_partial_files_left_after_success(self):
        atomic_copy(self.src, self.dst)
        leftovers = [
            name for name in os.listdir(os.path.dirname(self.dst))
            if name.endswith(".part")
        ]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
