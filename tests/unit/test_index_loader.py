import gzip
import hashlib
import http.server
import io
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_iso  # noqa: E402

from package_mgmt.lib.service import index_loader  # noqa: E402
from package_mgmt.lib.service.index_loader import load_binary_index, load_source_index  # noqa: E402
from package_mgmt.lib.util.paths import binary_index_path, source_index_path  # noqa: E402


def _gz_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(text.encode("utf-8"))
    return buf.getvalue()


class TestBinaryIndexParsing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "archive")
        make_test_iso.write_archive(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_size_lands_on_binary_package(self):
        path = binary_index_path(f"{self.base}/binary", "trixie", "main")
        packages = load_binary_index(path, "trixie", "main")
        vifm = packages["vifm"][0]
        expected = make_test_iso._pool_content("vifm_1.0-2_amd64.deb")
        self.assertEqual(vifm.size, str(len(expected)))
        self.assertEqual(vifm.sha256, hashlib.sha256(expected).hexdigest())

    def test_size_matches_index_checksum(self):
        path = binary_index_path(f"{self.base}/binary", "trixie", "contrib")
        packages = load_binary_index(path, "trixie", "contrib")
        firmware = packages["firmware-x"][0]
        expected = make_test_iso._pool_content("firmware-x_1.0-1_amd64.deb")
        self.assertEqual(firmware.size, str(len(expected)))
        self.assertEqual(firmware.sha256, hashlib.sha256(expected).hexdigest())

    def test_missing_size_yields_none(self):
        text = "Package: pkg\nVersion: 1.0\nArchitecture: amd64\nFilename: pool/main/p/pkg/pkg_1.0_amd64.deb\n"
        path = os.path.join(self.tmp.name, "no-size.Packages.gz")
        with open(path, "wb") as f:
            f.write(_gz_bytes(text))
        packages = load_binary_index(path, "trixie", "main")
        self.assertIsNone(packages["pkg"][0].size)

    def test_pre_depends_captured_and_merged(self):
        """`load_binary_index` reads Pre-Depends and merges it (deduplicated)
        into the Depends field the closure walk resolves."""
        text = (
            "Package: pre-app\nVersion: 1.0\nArchitecture: amd64\n"
            "Depends: liba\nPre-Depends: liba, libb\n"
            "Filename: pool/main/p/pre-app/pre-app_1.0_amd64.deb\n"
            "Size: 10\nSHA256: deadbeef\n"
        )
        path = os.path.join(self.tmp.name, "pre-dep.Packages.gz")
        with open(path, "wb") as f:
            f.write(_gz_bytes(text))
        packages = load_binary_index(path, "trixie", "main")
        entry = packages["pre-app"][0]
        self.assertEqual(entry.pre_depends_raw, "liba, libb")
        self.assertEqual(
            entry.depends_raw, "liba, libb",
            "Depends+Pre-Depends must merge, with the shared target appearing once",
        )

    def test_no_pre_depends_yields_none_and_depends_unchanged(self):
        """Without a Pre-Depends field, `pre_depends_raw` is None and
        `depends_raw` is the original text (regression-preserving)."""
        text = (
            "Package: plain\nVersion: 1.0\nArchitecture: amd64\n"
            "Depends: liba (>= 2.0), libb\n"
            "Filename: pool/main/p/plain/plain_1.0_amd64.deb\n"
            "Size: 10\nSHA256: deadbeef\n"
        )
        path = os.path.join(self.tmp.name, "plain.Packages.gz")
        with open(path, "wb") as f:
            f.write(_gz_bytes(text))
        packages = load_binary_index(path, "trixie", "main")
        entry = packages["plain"][0]
        self.assertIsNone(entry.pre_depends_raw)
        self.assertEqual(entry.depends_raw, "liba (>= 2.0), libb")

    def test_version_hash_fields_captured(self):
        """`load_binary_index` reads the remaining apt VersionHash inputs
        (`Installed-Size`, `Conflicts`, `Breaks`, `Replaces`) plus the original
        `Depends` text, so the handoff can re-emit them verbatim."""
        text = (
            "Package: vh\nVersion: 1.0\nArchitecture: amd64\n"
            "Installed-Size: 77\n"
            "Depends: liba\n"
            "Conflicts: old\n"
            "Breaks: broken\n"
            "Replaces: replaced\n"
            "Filename: pool/main/v/vh/vh_1.0_amd64.deb\n"
            "Size: 10\nSHA256: deadbeef\n"
        )
        path = os.path.join(self.tmp.name, "version-hash.Packages.gz")
        with open(path, "wb") as f:
            f.write(_gz_bytes(text))
        packages = load_binary_index(path, "trixie", "main")
        entry = packages["vh"][0]
        self.assertEqual(entry.depends_field, "liba")
        self.assertEqual(entry.installed_size, "77")
        self.assertEqual(entry.conflicts_raw, "old")
        self.assertEqual(entry.breaks_raw, "broken")
        self.assertEqual(entry.replaces_raw, "replaced")


class TestSourceIndexParsing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "archive")
        make_test_iso.write_archive(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_directory_and_checksums_sha256_parse(self):
        path = source_index_path(f"{self.base}/source", "trixie", "main")
        sources = load_source_index(path, "trixie", "main")
        vifm = sources["vifm"][0]
        self.assertEqual(vifm.directory, "pool/main/v/vifm")
        self.assertIsNotNone(vifm.parsed_files)
        names = [f.name for f in vifm.parsed_files]
        self.assertEqual(
            names,
            [
                "vifm_1.0-2.dsc",
                "vifm_1.0-2.orig.tar.xz",
                "vifm_1.0-2.debian.tar.xz",
            ],
        )
        for f in vifm.parsed_files:
            content = make_test_iso._pool_content(f.name)
            self.assertEqual(f.size, len(content))
            self.assertEqual(f.sha256, hashlib.sha256(content).hexdigest())

    def test_raw_files_field_kept_for_compatibility(self):
        path = source_index_path(f"{self.base}/source", "trixie", "main")
        sources = load_source_index(path, "trixie", "main")
        vifm = sources["vifm"][0]
        self.assertIsNotNone(vifm.files)
        self.assertEqual(len(vifm.files), 3)

    def test_missing_checksums_yields_none(self):
        text = "Package: pkg\nVersion: 1.0\nDirectory: pool/main/p/pkg\n"
        path = os.path.join(self.tmp.name, "no-checksums.Sources.gz")
        with open(path, "wb") as f:
            f.write(_gz_bytes(text))
        sources = load_source_index(path, "trixie", "main")
        self.assertIsNone(sources["pkg"][0].parsed_files)

    def test_partial_checksums_line(self):
        text = (
            "Package: pkg\nVersion: 1.0\nDirectory: pool/main/p/pkg\n"
            "Checksums-Sha256:\n 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef NOPE file.deb\n"
        )
        path = os.path.join(self.tmp.name, "partial-checksums.Sources.gz")
        with open(path, "wb") as f:
            f.write(_gz_bytes(text))
        sources = load_source_index(path, "trixie", "main")
        entry = sources["pkg"][0]
        self.assertEqual(len(entry.parsed_files), 1)
        self.assertEqual(entry.parsed_files[0].name, "file.deb")
        self.assertIsNone(entry.parsed_files[0].size)
        self.assertEqual(
            entry.parsed_files[0].sha256,
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        )


class _StallHandler(http.server.BaseHTTPRequestHandler):
    """Accepts a request, sends headers with a large Content-Length, then
    never delivers the body — a connection that idles forever."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "1000000")
        self.end_headers()
        try:
            while True:
                time.sleep(3600)
        except Exception:
            pass

    def log_message(self, *args):
        pass


class _StallingServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


class TestIndexFetchTimeout(unittest.TestCase):
    """A stalled index fetch must abort within the socket timeout instead of
    blocking the run forever."""

    def setUp(self):
        self.server = _StallingServer(("127.0.0.1", 0), _StallHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_stalled_download_aborts_within_timeout(self):
        url = f"http://127.0.0.1:{self.server.server_address[1]}/stall"
        with mock.patch.object(index_loader, "INDEX_FETCH_TIMEOUT", 1.0):
            start = time.monotonic()
            with self.assertRaises(TimeoutError):
                index_loader._download(url)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 5.0, "the stalled fetch must abort in bounded time")

    def test_stalled_unverified_fetch_aborts_within_timeout(self):
        url = f"http://127.0.0.1:{self.server.server_address[1]}/stall"
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "Packages")
            with mock.patch.object(index_loader, "INDEX_FETCH_TIMEOUT", 1.0):
                start = time.monotonic()
                with self.assertRaises(TimeoutError):
                    index_loader.fetch_index(url, dest, release_base=None)
                elapsed = time.monotonic() - start
                self.assertLess(elapsed, 5.0, "the stalled fetch must abort in bounded time")


class _StaticBytesHandler(http.server.BaseHTTPRequestHandler):
    """Serves a fixed byte payload on every request."""

    payload = b"index-body-bytes"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args):
        pass


class _NotFoundHandler(http.server.BaseHTTPRequestHandler):
    """Answers every request with 404 — a failed fetch."""

    def do_GET(self):
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


class TestIndexAtomicWrite(unittest.TestCase):
    """A successful index download is persisted atomically (write to
    `*.part`, then replace): the cached index is never observed partially
    written, and a failed fetch leaves neither a final file nor a part."""

    def _serve(self, handler_class):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def test_successful_fetch_is_persisted_atomically(self):
        server, thread = self._serve(_StaticBytesHandler)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dest = os.path.join(tmp, "Packages.gz")
                url = f"http://127.0.0.1:{server.server_address[1]}/Packages.gz"
                index_loader.fetch_index(url, dest, release_base=None)
                self.assertTrue(os.path.exists(dest))
                self.assertFalse(
                    os.path.exists(dest + ".part"),
                    "the part file must be replaced, never left behind",
                )
                with open(dest, "rb") as f:
                    self.assertEqual(f.read(), _StaticBytesHandler.payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_failed_fetch_leaves_no_partial_index(self):
        server, thread = self._serve(_NotFoundHandler)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dest = os.path.join(tmp, "Packages.gz")
                url = f"http://127.0.0.1:{server.server_address[1]}/Packages.gz"
                with self.assertRaises(urllib.error.HTTPError):
                    index_loader.fetch_index(url, dest, release_base=None)
                self.assertFalse(os.path.exists(dest))
                self.assertFalse(
                    os.path.exists(dest + ".part"),
                    "a failed fetch must not leave a partial index behind",
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class TestFetchIndexForwardsFetchFn(unittest.TestCase):
    """`fetch_index` forwards `fetch_fn` on the unverified (loopback) path —
    the verified path already forwards it, so the injected fetcher is honored
    everywhere."""

    def test_unverified_path_uses_fetch_fn(self):
        payload = b"injected-index-bytes"
        calls: list[str] = []

        def fake_fetch(url: str) -> bytes:
            calls.append(url)
            return payload

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "Packages.gz")
            index_loader.fetch_index(
                "http://mirror.invalid/dists/trixie/main/binary-amd64/Packages.gz",
                dest,
                release_base=None,
                fetch_fn=fake_fetch,
            )
            self.assertEqual(calls, [
                "http://mirror.invalid/dists/trixie/main/binary-amd64/Packages.gz"
            ])
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), payload)


if __name__ == "__main__":
    unittest.main()
