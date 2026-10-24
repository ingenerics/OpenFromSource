import gzip
import hashlib
import http.client
import http.server
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_iso  # noqa: E402
import db  # noqa: E402

from package_mgmt.lib.model.config import ArchiveSource, ProjectConfig  # noqa: E402
from package_mgmt.lib.service.db_init import initialize_database  # noqa: E402
from package_mgmt.lib.service.downloader import (  # noqa: E402
    BACKOFF_CAP,
    _backoff_delay,
    _default_opener,
    _file_sha256,
    _PooledOpener,
    pending_binaries,
    pending_sources,
    download_pending_binaries,
    download_pending_sources,
)
from package_mgmt.lib.util.paths import binary_index_path, source_index_path  # noqa: E402


def _config(base_dir: str, mirror: str = "http://mirror.invalid", release: str = "trixie",
            fetch_source: bool = True) -> ProjectConfig:
    return db.config(base_dir, mirror=mirror, release=release, fetch_source=fetch_source)


class _StubResponse:
    def __init__(self, data: bytes):
        self.data = data

    def read(self, chunk_size: int = -1):
        if chunk_size == -1:
            return self.data
        if not self.data:
            return b""
        chunk, self.data = self.data[:chunk_size], self.data[chunk_size:]
        return chunk

    def close(self):
        pass


class _StubOpener:
    """Returns scripted responses keyed by URL path (default 404)."""

    def __init__(self):
        self.responses: dict[str, object] = {}
        self.calls: list[str] = []

    def add(self, url: str, data: bytes):
        self.responses[url] = data

    def fail(self, url: str, code: int):
        self.responses[url] = urllib.error.HTTPError(
            url, code, "error", {}, io.BytesIO(b"")
        )

    def __call__(self, url: str):
        self.calls.append(url)
        resp = self.responses.get(url, urllib.error.HTTPError(url, 404, "not found", {}, io.BytesIO(b"")))
        if isinstance(resp, urllib.error.HTTPError):
            raise resp
        return _StubResponse(resp)


class _StubProgress:
    def __init__(self):
        self.totals = None
        self.updates = []

    def set_totals(self, total_files: int, total_bytes: int) -> None:
        self.totals = (total_files, total_bytes)

    def update(self, done_files: int, done_bytes: int) -> None:
        self.updates.append((done_files, done_bytes))

    def done(self) -> None:
        pass


class _LiveProgress(_StubProgress):
    """A progress object that advertises a TTY, so the downloader streams
    live in-flight byte progress into it (see `_live_progress`)."""

    def __init__(self, is_tty=True):
        super().__init__()
        self.is_tty = is_tty


def _insert_binary(conn, name, version, release="trixie", component="main",
                   status="current", verified=0, architecture="amd64") -> int:
    return db.insert_binary(
        conn, name, version,
        release=release, component=component, architecture=architecture,
        status=status, verified=verified,
    )


def _insert_source(conn, name, version, release="trixie", component="main",
                   verified=0) -> int:
    cur = conn.execute(
        "INSERT INTO sources (package_name, source_version, release, component, verified)"
        " VALUES (?, ?, ?, ?, ?) RETURNING id",
        (name, version, release, component, verified),
    )
    return cur.fetchone()[0]


def _link(conn, binary_id: int, source_id: int) -> None:
    conn.execute(
        "INSERT INTO binary_to_source (binary_id, source_id) VALUES (?, ?)",
        (binary_id, source_id),
    )


class TestPendingBinaries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        initialize_database(self.db_path)
        self.conn = sqlite3.connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_enumerates_only_current_unverified(self):
        _insert_binary(self.conn, "vifm", "1.0-2", verified=0)
        _insert_binary(self.conn, "lib1", "2.0-1", verified=1)
        _insert_binary(self.conn, "old", "1.0-1", status="superseded", verified=0)
        self.conn.commit()
        rows = pending_binaries(self.conn)
        self.assertEqual(len(rows), 1)
        rid, name, version, release, component = rows[0]
        self.assertEqual((name, version, release, component), ("vifm", "1.0-2", "trixie", "main"))
        self.assertIsInstance(rid, int)


class TestPendingSources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        initialize_database(self.db_path)
        self.conn = sqlite3.connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_only_sources_linked_to_current_binaries(self):
        cur_bid = _insert_binary(self.conn, "vifm", "1.0-2", status="current")
        old_bid = _insert_binary(self.conn, "lib1", "2.0-1", status="superseded")
        s_cur = _insert_source(self.conn, "vifm", "1.0-2", verified=0)
        s_old = _insert_source(self.conn, "lib1", "2.0-1", verified=0)
        _insert_source(self.conn, "orphan", "1.0-1", verified=0)
        s_done = _insert_source(self.conn, "lib2", "3.0-1", verified=1)
        _link(self.conn, cur_bid, s_cur)
        _link(self.conn, old_bid, s_old)
        _link(self.conn, cur_bid, s_done)
        self.conn.commit()

        rows = pending_sources(self.conn)
        self.assertEqual(len(rows), 1)
        sid, name, version, release, component = rows[0]
        self.assertEqual((name, version, release, component), ("vifm", "1.0-2", "trixie", "main"))


class TestSourceDownloadEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        self.config = _config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.opener = _StubOpener()
        self.progress = _StubProgress()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _url(self, rel: str) -> str:
        return f"{self.config.sources[0].mirror.rstrip('/')}/{rel}"

    def _add_linked_source(self, name="vifm", version="1.0-2"):
        bid = _insert_binary(self.conn, name, version)
        sid = _insert_source(self.conn, name, version)
        _link(self.conn, bid, sid)
        self.conn.commit()
        return bid, sid

    def _source_urls(self, name, version):
        directory = make_test_iso.source_pool_directory("main", name)
        return {
            fname: self._url(f"{directory}/{fname}")
            for fname in make_test_iso.source_files(name, version)
        }

    def test_happy_path_downloads_all_files_and_marks_source(self):
        _, sid = self._add_linked_source()
        urls = self._source_urls("vifm", "1.0-2")
        for fname, url in urls.items():
            self.opener.add(url, make_test_iso._pool_content(fname))

        stats = download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(stats.files_downloaded, 3)
        self.assertEqual(stats.files_verified, 3)
        self.assertEqual(stats.failures, [])
        self.assertEqual(stats.command_errors, [])
        self.assertEqual(self.progress.totals, (3, sum(
            len(make_test_iso._pool_content(f)) for f in urls
        )))
        for fname in urls:
            dest = os.path.join(
                self.base, "source", *make_test_iso.source_pool_directory("main", "vifm").split("/"), fname
            )
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), make_test_iso._pool_content(fname))
        conn2 = sqlite3.connect(self.config.db_path)
        try:
            self.assertEqual(conn2.execute("SELECT verified FROM sources WHERE id = ?", (sid,)).fetchone()[0], 1)
        finally:
            conn2.close()
        parts = []
        for root, _, files in os.walk(os.path.join(self.base, "source")):
            parts.extend(f for f in files if f.endswith(".part"))
        self.assertEqual(parts, [])

    def test_one_bad_file_keeps_source_unverified_but_other_files_download(self):
        _, sid = self._add_linked_source()
        urls = self._source_urls("vifm", "1.0-2")
        dsc_url = [u for u in urls.values() if u.endswith(".dsc")][0]
        self.opener.fail(dsc_url, 404)
        for fname, url in urls.items():
            if not url.endswith(".dsc"):
                self.opener.add(url, make_test_iso._pool_content(fname))

        stats = download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(len(stats.failures), 1)
        self.assertTrue(stats.failures[0].endswith("vifm_1.0-2.dsc: HTTP 404"), stats.failures[0])
        self.assertEqual(stats.files_downloaded, 2)
        self.assertEqual(stats.files_verified, 0)
        conn2 = sqlite3.connect(self.config.db_path)
        try:
            self.assertEqual(conn2.execute("SELECT verified FROM sources WHERE id = ?", (sid,)).fetchone()[0], 0)
        finally:
            conn2.close()
        directory = make_test_iso.source_pool_directory("main", "vifm")
        source_root = os.path.join(self.base, "source", *directory.split("/"))
        self.assertTrue(os.path.exists(os.path.join(source_root, "vifm_1.0-2.orig.tar.xz")))
        self.assertFalse(os.path.exists(os.path.join(source_root, "vifm_1.0-2.dsc")))

    def test_checksum_mismatch_keeps_source_unverified(self):
        _, sid = self._add_linked_source()
        urls = self._source_urls("vifm", "1.0-2")
        for fname, url in urls.items():
            content = b"corrupt" if fname.endswith(".dsc") else make_test_iso._pool_content(fname)
            self.opener.add(url, content)

        stats = download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(len(stats.failures), 1)
        self.assertIn("checksum mismatch", stats.failures[0])
        self.assertEqual(stats.files_verified, 0)
        conn2 = sqlite3.connect(self.config.db_path)
        try:
            self.assertEqual(conn2.execute("SELECT verified FROM sources WHERE id = ?", (sid,)).fetchone()[0], 0)
        finally:
            conn2.close()
        # The bad file must not remain in the source pool.
        directory = make_test_iso.source_pool_directory("main", "vifm")
        self.assertFalse(os.path.exists(os.path.join(self.base, "source", *directory.split("/"), "vifm_1.0-2.dsc")))

    def test_missing_index_entry_is_unconfirmable(self):
        self._add_linked_source(name="ghost", version="9.9-1")
        stats = download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)
        self.assertEqual(len(stats.command_errors), 1)
        self.assertIn("No index entry for trixie/main ghost 9.9-1", stats.command_errors[0])
        self.assertEqual(self.opener.calls, [])

    def test_fetch_source_false_does_no_work(self):
        self.config = _config(self.base, fetch_source=False)
        self._add_linked_source()
        stats = download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)
        self.assertEqual(stats.planned_files, 0)
        self.assertEqual(stats.files_downloaded, 0)
        self.assertEqual(stats.failures, [])
        self.assertEqual(stats.command_errors, [])
        self.assertEqual(self.opener.calls, [])

    def test_no_pending_sources_is_a_noop(self):
        stats = download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)
        self.assertEqual(stats.planned_files, 0)
        self.assertEqual(stats.files_downloaded, 0)
        self.assertEqual(self.opener.calls, [])

    def test_missing_cached_source_index_errors(self):
        self._add_linked_source()
        os.remove(source_index_path(f"{self.base}/source", "trixie", "main"))
        stats = download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)
        self.assertEqual(len(stats.command_errors), 1)
        self.assertIn("Cached index missing for trixie/main", stats.command_errors[0])
        self.assertEqual(self.opener.calls, [])

    def test_crafted_source_directory_traversal_is_refused(self):
        """A `Directory` that escapes the cache is refused: no source file is
        written outside the source cache."""
        self._add_linked_source()
        path = source_index_path(f"{self.base}/source", "trixie", "main")
        with open(path, "rb") as f:
            raw = gzip.decompress(f.read()).decode("utf-8")
        paragraphs = raw.split("\n\n")
        for i, para in enumerate(paragraphs):
            if para.startswith("Package: vifm"):
                lines = [ln for ln in para.splitlines() if not ln.startswith("Directory: ")]
                paragraphs[i] = "\n".join(lines) + "\nDirectory: ../../etc\n"
                break
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            gz.write(("\n\n".join(paragraphs)).encode("utf-8"))
        with open(path, "wb") as f:
            f.write(buf.getvalue())

        stats = download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(stats.files_downloaded, 0)
        self.assertEqual(len(stats.failures), 3)
        self.assertTrue(all("unsafe path" in f for f in stats.failures))
        self.assertEqual(self.opener.calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.base, "source", "etc")))

    def test_deleted_file_of_verified_source_is_reefetched_and_stays_verified(self):
        _, sid = self._add_linked_source()
        urls = self._source_urls("vifm", "1.0-2")
        for fname, url in urls.items():
            self.opener.add(url, make_test_iso._pool_content(fname))
        download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)
        self.assertEqual(
            self.conn.execute("SELECT verified FROM sources WHERE id = ?", (sid,)).fetchone()[0], 1
        )

        deleted = "vifm_1.0-2.orig.tar.xz"
        dest = os.path.join(
            self.base, "source", *make_test_iso.source_pool_directory("main", "vifm").split("/"), deleted
        )
        os.remove(dest)
        self.opener.calls.clear()

        stats = download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(self.opener.calls, [urls[deleted]])
        self.assertEqual(stats.files_downloaded, 1)
        self.assertEqual(stats.failures, [])
        self.assertTrue(os.path.exists(dest))
        self.assertEqual(
            self.conn.execute("SELECT verified FROM sources WHERE id = ?", (sid,)).fetchone()[0], 1
        )

    def test_corrupt_size_matching_file_is_unmarked_and_reefetched(self):
        _, sid = self._add_linked_source()
        urls = self._source_urls("vifm", "1.0-2")
        for fname, url in urls.items():
            self.opener.add(url, make_test_iso._pool_content(fname))
        download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)
        self.assertEqual(
            self.conn.execute("SELECT verified FROM sources WHERE id = ?", (sid,)).fetchone()[0], 1
        )

        bad = "vifm_1.0-2.dsc"
        dest = os.path.join(
            self.base, "source", *make_test_iso.source_pool_directory("main", "vifm").split("/"), bad
        )
        good = make_test_iso._pool_content(bad)
        with open(dest, "wb") as f:
            f.write(b"X" * len(good))  # same size, wrong bytes: size check alone cannot catch it
        self.opener.calls.clear()

        stats = download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(self.opener.calls, [urls[bad]])
        self.assertEqual(stats.files_downloaded, 1)
        self.assertEqual(stats.failures, [])
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), good)
        self.assertEqual(
            self.conn.execute("SELECT verified FROM sources WHERE id = ?", (sid,)).fetchone()[0], 1
        )

    def test_all_intact_verified_source_downloads_nothing(self):
        _, sid = self._add_linked_source()
        urls = self._source_urls("vifm", "1.0-2")
        for fname, url in urls.items():
            self.opener.add(url, make_test_iso._pool_content(fname))
        download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)
        self.opener.calls.clear()

        stats = download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(self.opener.calls, [])
        self.assertEqual(stats.planned_files, 0)
        self.assertEqual(stats.files_downloaded, 0)
        self.assertEqual(stats.failures, [])
        self.assertEqual(
            self.conn.execute("SELECT verified FROM sources WHERE id = ?", (sid,)).fetchone()[0], 1
        )

    def test_only_missing_files_of_verified_source_are_reefetched(self):
        _, sid = self._add_linked_source()
        urls = self._source_urls("vifm", "1.0-2")
        for fname, url in urls.items():
            self.opener.add(url, make_test_iso._pool_content(fname))
        download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)

        missing = ("vifm_1.0-2.dsc", "vifm_1.0-2.debian.tar.xz")
        source_root = os.path.join(
            self.base, "source", *make_test_iso.source_pool_directory("main", "vifm").split("/")
        )
        for fname in missing:
            os.remove(os.path.join(source_root, fname))
        self.opener.calls.clear()

        stats = download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(sorted(self.opener.calls), sorted(urls[f] for f in missing))
        self.assertEqual(stats.files_downloaded, 2)
        self.assertEqual(stats.failures, [])
        self.assertEqual(
            self.conn.execute("SELECT verified FROM sources WHERE id = ?", (sid,)).fetchone()[0], 1
        )


def _http_error(url: str, code: int, retry_after: str | None = None):
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return urllib.error.HTTPError(url, code, "error", headers, io.BytesIO(b""))


class TestBackoffDelay(unittest.TestCase):
    def test_doubling_with_cap(self):
        self.assertEqual(_backoff_delay(1, jitter_fn=lambda: 0.0), 1.0)
        self.assertEqual(_backoff_delay(2, jitter_fn=lambda: 0.0), 2.0)
        self.assertEqual(_backoff_delay(3, jitter_fn=lambda: 0.0), 4.0)
        self.assertEqual(_backoff_delay(10, jitter_fn=lambda: 0.0), 30.0)

    def test_jitter_applied(self):
        self.assertEqual(_backoff_delay(1, jitter_fn=lambda: 0.25), 1.25)

    def test_retry_after_overrides_backoff(self):
        self.assertEqual(_backoff_delay(3, retry_after=1.5, jitter_fn=lambda: 0.0), 1.5)
        self.assertEqual(_backoff_delay(1, retry_after=0.0, jitter_fn=lambda: 0.0), 0.0)

    def test_negative_retry_after_clamped(self):
        self.assertEqual(_backoff_delay(2, retry_after=-2.0, jitter_fn=lambda: 0.0), 0.0)

    def test_retry_after_capped_at_internal_bound(self):
        self.assertLessEqual(
            _backoff_delay(1, retry_after=86400.0, jitter_fn=lambda: 0.0),
            BACKOFF_CAP,
        )

    def test_retry_after_within_cap_preserved(self):
        self.assertEqual(_backoff_delay(1, retry_after=2.0, jitter_fn=lambda: 0.0), 2.0)


class TestRetryLayer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        self.config = _config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.progress = _StubProgress()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _noop_sleep(self, seconds):
        pass

    def test_transient_429_retried_then_succeeds(self):
        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        content = make_test_iso._pool_content("vifm_1.0-2_amd64.deb")
        calls = {"n": 0}

        def flaky(url):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise _http_error(url, 429, retry_after="0")
            return _StubResponse(content)

        stats = download_pending_binaries(
            self.config, self.conn, progress=self.progress, opener=flaky,
            sleep=self._noop_sleep,
        )
        self.assertEqual(calls["n"], 3)
        self.assertEqual(stats.failures, [])
        self.assertEqual(stats.files_downloaded, 1)
        conn2 = sqlite3.connect(self.config.db_path)
        try:
            self.assertEqual(conn2.execute("SELECT verified FROM binaries").fetchone()[0], 1)
        finally:
            conn2.close()

    def test_transient_503_exhausted_reports_retries(self):
        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        filename = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"
        calls = {"n": 0}

        def always_503(url):
            calls["n"] += 1
            raise _http_error(url, 503, retry_after="0")

        stats = download_pending_binaries(
            self.config, self.conn, progress=self.progress, opener=always_503,
            sleep=self._noop_sleep,
        )
        self.assertEqual(calls["n"], 5)
        self.assertEqual(len(stats.failures), 1)
        self.assertEqual(stats.failures[0], f"{filename}: 5 retries exhausted (HTTP 503)")
        conn2 = sqlite3.connect(self.config.db_path)
        try:
            self.assertEqual(conn2.execute("SELECT verified FROM binaries").fetchone()[0], 0)
        finally:
            conn2.close()

    def test_permanent_404_not_retried(self):
        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        filename = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"
        calls = {"n": 0}

        def always_404(url):
            calls["n"] += 1
            raise _http_error(url, 404)

        stats = download_pending_binaries(
            self.config, self.conn, progress=self.progress, opener=always_404,
            sleep=self._noop_sleep,
        )
        self.assertEqual(calls["n"], 1)
        self.assertEqual(stats.failures[0], f"{filename}: HTTP 404")

    def test_connection_error_retried_then_fails(self):
        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        filename = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"
        calls = {"n": 0}

        def connection_error(url):
            calls["n"] += 1
            raise urllib.error.URLError("connection reset by peer")

        stats = download_pending_binaries(
            self.config, self.conn, progress=self.progress, opener=connection_error,
            sleep=self._noop_sleep,
        )
        self.assertEqual(calls["n"], 5)
        self.assertEqual(
            stats.failures[0], f"{filename}: 5 retries exhausted (connection reset by peer)"
        )

    def test_default_opener_sends_descriptive_user_agent(self):
        captured = {}

        class _UAHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                captured["ua"] = self.headers.get("User-Agent")
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _UAHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            opener = _default_opener()
            response = opener(f"http://127.0.0.1:{server.server_address[1]}/x")
            self.assertEqual(response.read(), b"")
            response.close()
        finally:
            server.shutdown()
            thread.join()
        self.assertIn("package_mgmt/", captured["ua"])
        self.assertIn("OpenFromSource offline cache builder", captured["ua"])


class TestRedirects(unittest.TestCase):
    """Redirects are followed inside _PooledOpener (bounded, loop-rejected)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        make_test_iso.write_pool_files(self.base)
        self.server = None

    def tearDown(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.thread.join()
        self.tmp.cleanup()

    def _serve(self, redirects):
        self.server = make_test_iso.serve_redirecting(self.base, port=0, redirects=redirects)
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def test_follows_redirect_and_final_payload_passes_checksum(self):
        fname = "vifm_1.0-2_amd64.deb"
        mirror = self._serve({
            f"/pool/main/v/vifm/{fname}": (301, f"/alt/pool/main/v/vifm/{fname}"),
        })
        opener = _default_opener()
        try:
            response = opener(f"{mirror}/pool/main/v/vifm/{fname}")
            body = response.read()
            response.close()
        finally:
            opener.close()
        expected = make_test_iso._pool_content(fname)
        self.assertEqual(body, expected)
        self.assertEqual(
            hashlib.sha256(body).hexdigest(), hashlib.sha256(expected).hexdigest()
        )

    def test_chain_over_hop_limit_fails_permanent_error(self):
        chain = {f"/chain/{i}": (301, f"/chain/{i + 1}") for i in range(6)}
        mirror = self._serve(chain)
        opener = _default_opener()
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                opener(f"{mirror}/chain/0")
        finally:
            opener.close()
        self.assertIn("redirect limit exceeded", str(ctx.exception))
        self.assertIn("5", str(ctx.exception))

    def test_redirect_loop_is_rejected(self):
        mirror = self._serve({
            "/loop/a": (302, "/loop/b"),
            "/loop/b": (302, "/loop/a"),
        })
        opener = _default_opener()
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                opener(f"{mirror}/loop/a")
        finally:
            opener.close()
        self.assertIn("redirect loop detected", str(ctx.exception))

    def test_redirect_without_location_fails_clear_error(self):
        mirror = self._serve({"/noloc": (302, None)})
        opener = _default_opener()
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                opener(f"{mirror}/noloc")
        finally:
            opener.close()
        self.assertIn("redirect without Location", str(ctx.exception))

    def test_direct_download_unchanged(self):
        fname = "vifm_1.0-2_amd64.deb"
        mirror = self._serve({})
        opener = _default_opener()
        try:
            response = opener(f"{mirror}/pool/main/v/vifm/{fname}")
            self.assertEqual(response.read(), make_test_iso._pool_content(fname))
            response.close()
        finally:
            opener.close()


class TestRedirectDownloadFlow(unittest.TestCase):
    """End-to-end: a redirecting mirror downloads through the integrity gate,
    and bad redirects fail permanently with no leftover part file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        self.fixture_base = os.path.join(self.tmp.name, "fixture")
        make_test_iso.write_archive(self.base)
        make_test_iso.write_pool_files(self.fixture_base)
        self.server = None

    def tearDown(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.thread.join()
        self.tmp.cleanup()

    def _serve(self, redirects):
        self.server = make_test_iso.serve_redirecting(self.fixture_base, port=0, redirects=redirects)
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def _config_with_mirror(self, mirror):
        config = _config(self.base, mirror=mirror)
        initialize_database(config.db_path)
        return config

    def test_redirected_download_downloads_verifies_and_leaves_no_part(self):
        fname = "vifm_1.0-2_amd64.deb"
        filename = f"pool/main/v/vifm/{fname}"
        mirror = self._serve({f"/{filename}": (301, f"/alt/{filename}")})
        config = self._config_with_mirror(mirror)
        conn = sqlite3.connect(config.db_path)
        try:
            _insert_binary(conn, "vifm", "1.0-2")
            conn.commit()
            stats = download_pending_binaries(config, conn, opener=_default_opener())
            self.assertEqual(stats.failures, [])
            self.assertEqual(stats.files_downloaded, 1)
            self.assertEqual(conn.execute("SELECT verified FROM binaries").fetchone()[0], 1)
        finally:
            conn.close()
        self.assertTrue(os.path.exists(os.path.join(config.binary_cache, filename)))
        self.assertFalse(os.path.exists(os.path.join(config.binary_cache, filename + ".part")))

    def test_redirect_loop_fails_permanently_with_no_part(self):
        fname = "vifm_1.0-2_amd64.deb"
        filename = f"pool/main/v/vifm/{fname}"
        mirror = self._serve({
            f"/{filename}": (302, f"/loop/{filename}"),
            f"/loop/{filename}": (302, f"/{filename}"),
        })
        config = self._config_with_mirror(mirror)
        conn = sqlite3.connect(config.db_path)
        try:
            _insert_binary(conn, "vifm", "1.0-2")
            conn.commit()
            stats = download_pending_binaries(config, conn, opener=_default_opener())
            self.assertEqual(len(stats.failures), 1)
            self.assertIn("redirect loop detected", stats.failures[0])
            self.assertEqual(conn.execute("SELECT verified FROM binaries").fetchone()[0], 0)
        finally:
            conn.close()
        self.assertFalse(os.path.exists(os.path.join(config.binary_cache, filename + ".part")))

    def test_redirect_without_location_fails_permanently_with_no_part(self):
        fname = "vifm_1.0-2_amd64.deb"
        filename = f"pool/main/v/vifm/{fname}"
        mirror = self._serve({f"/{filename}": (302, None)})
        config = self._config_with_mirror(mirror)
        conn = sqlite3.connect(config.db_path)
        try:
            _insert_binary(conn, "vifm", "1.0-2")
            conn.commit()
            stats = download_pending_binaries(config, conn, opener=_default_opener())
            self.assertEqual(len(stats.failures), 1)
            self.assertIn("redirect without Location", stats.failures[0])
        finally:
            conn.close()
        self.assertFalse(os.path.exists(os.path.join(config.binary_cache, filename + ".part")))


class _ResetResponse:
    """Simulates a connection dropping mid-read (http.client behavior)."""

    def __init__(self, exc, fail_reads=1, good_data=b""):
        self.exc = exc
        self.fail_reads = fail_reads
        self.good_data = good_data
        self._reads = 0

    def read(self, chunk_size=-1):
        self._reads += 1
        if self._reads <= self.fail_reads:
            raise self.exc
        if self._reads == self.fail_reads + 1:
            return self.good_data
        return b""

    def close(self):
        pass


class TestMidReadFailures(unittest.TestCase):
    """Connection failures mid-read are transient (retried), and
    they never abort the whole run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        self.config = _config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _noop_sleep(self, seconds):
        pass

    def test_connection_reset_mid_read_is_retried_then_succeeds(self):

        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        content = make_test_iso._pool_content("vifm_1.0-2_amd64.deb")
        resets = {"n": 0}

        def flaky(url):
            resets["n"] += 1
            if resets["n"] <= 2:
                return _ResetResponse(ConnectionResetError("connection reset by peer"))
            return _StubResponse(content)

        stats = download_pending_binaries(self.config, self.conn, opener=flaky, sleep=self._noop_sleep)
        self.assertEqual(resets["n"], 3)
        self.assertEqual(stats.failures, [])
        self.assertEqual(stats.files_downloaded, 1)

    def test_connection_reset_exhausted_reports_retries_not_write_error(self):
        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        resets = {"n": 0}

        def flaky(url):
            resets["n"] += 1
            return _ResetResponse(ConnectionResetError("connection reset by peer"))

        stats = download_pending_binaries(self.config, self.conn, opener=flaky, sleep=self._noop_sleep)
        self.assertEqual(resets["n"], 5)
        self.assertEqual(len(stats.failures), 1)
        self.assertIn("5 retries exhausted", stats.failures[0])
        self.assertIn("connection reset by peer", stats.failures[0])
        self.assertNotIn("write error", stats.failures[0])

    def test_incomplete_read_does_not_abort_other_files(self):
        import http.client

        _insert_binary(self.conn, "vifm", "1.0-2")
        _insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        f_lib1 = "pool/main/l/lib1/lib1_2.0-1_amd64.deb"

        def flaky(url):
            if url.endswith(f_lib1):
                return _StubResponse(make_test_iso._pool_content("lib1_2.0-1_amd64.deb"))
            return _ResetResponse(http.client.IncompleteRead(b"partial"))

        stats = download_pending_binaries(self.config, self.conn, opener=flaky, sleep=self._noop_sleep)
        self.assertEqual(len(stats.failures), 1)
        self.assertIn("retries exhausted", stats.failures[0])
        self.assertEqual(stats.files_downloaded, 1)
        self.assertEqual(stats.files_verified, 1)
        conn2 = sqlite3.connect(self.config.db_path)
        try:
            rows = conn2.execute(
                "SELECT package_name, verified FROM binaries ORDER BY package_name"
            ).fetchall()
        finally:
            conn2.close()
        self.assertEqual(rows, [("lib1", 1), ("vifm", 0)])

    def test_disk_write_error_is_permanent_write_error(self):
        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        calls = {"n": 0}

        def write_error(url):
            calls["n"] += 1
            return _StubResponse(make_test_iso._pool_content("vifm_1.0-2_amd64.deb"))

        # Make the destination directory unwritable: the part write fails.
        dest_dir = os.path.join(self.base, "binary", "pool", "main", "v", "vifm")
        os.makedirs(dest_dir)
        os.chmod(dest_dir, 0o500)
        try:
            stats = download_pending_binaries(self.config, self.conn, opener=write_error, sleep=self._noop_sleep)
        finally:
            os.chmod(dest_dir, 0o755)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(len(stats.failures), 1)
        self.assertIn("write error", stats.failures[0])


class TestFetchToPartLiveProgress(unittest.TestCase):
    """`_fetch_to_part`'s `on_progress` hook reports the running byte count
    of each attempt, throttled to ~PROGRESS_LIVE_BYTES, so a large payload
    moves the bar while it streams in."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = os.path.join(self.tmp.name, "big.deb")

    def tearDown(self):
        self.tmp.cleanup()

    def test_on_progress_reports_throttled_monotonic_bytes(self):
        from package_mgmt.lib.service import downloader as dl

        content = b"x" * (2 * 1024 * 1024)
        opener = _StubOpener()
        opener.add("http://m/big.deb", content)
        calls = []
        digest, total = dl._fetch_to_part(
            opener, "http://m/big.deb", self.dest, on_progress=calls.append
        )
        self.assertEqual(total, len(content))
        self.assertEqual(digest, hashlib.sha256(content).hexdigest())
        # Throttled to ~1 MiB: exactly two reports, monotonic, capped at the
        # final size (the committed per-file update reports the rest).
        self.assertEqual(calls, [1024 * 1024, 2 * 1024 * 1024])
        # `_fetch_to_part` writes the part file; the engine renames it.
        with open(self.dest + ".part", "rb") as f:
            self.assertEqual(f.read(), content)

    def test_on_progress_is_per_attempt_after_a_retry(self):
        from package_mgmt.lib.service import downloader as dl

        # The first attempt streams ~1 MiB then drops the connection; the
        # retry delivers everything. Live reports must restart from zero on
        # the new attempt (no double counting of the failed attempt).
        class _StreamThenDropResponse:
            def __init__(self, data):
                self.data = data
                self._delivered = 0
                self._drop = False

            def read(self, chunk_size=-1):
                if chunk_size == -1:
                    return self.data
                if self._drop:
                    raise ConnectionError("connection reset")
                if not self.data:
                    return b""
                chunk, self.data = self.data[:chunk_size], self.data[chunk_size:]
                self._delivered += len(chunk)
                if self._delivered >= 1024 * 1024:
                    self._drop = True
                return chunk

            def close(self):
                pass

        content = b"y" * (2 * 1024 * 1024)
        attempts = {"n": 0}

        def flaky(url):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return _StreamThenDropResponse(content)
            return _StubResponse(content)

        calls = []
        digest, total, error = dl._fetch_with_retries(
            flaky, "http://m/big.deb", self.dest, sleep=lambda _: None,
            on_progress=calls.append,
        )
        self.assertEqual(attempts["n"], 2)
        self.assertIsNone(error)
        self.assertEqual(total, len(content))
        self.assertEqual(digest, hashlib.sha256(content).hexdigest())
        # Failed attempt reported 1 MiB; the retry restarts from zero and
        # reports 1 MiB again then the final 2 MiB — never cumulative.
        self.assertEqual(calls, [1024 * 1024, 1024 * 1024, 2 * 1024 * 1024])


class TestLiveProgressWiring(unittest.TestCase):
    """The downloader streams live in-flight bytes only into a TTY progress
    object; a piped/logged (non-TTY) run keeps its one-line-per-file output
    and receives no live hook. Live bytes are reported on top of the
    committed baseline."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        self.config = _config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.opener = _StubOpener()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _insert(self):
        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()

    def test_tty_progress_receives_live_bytes_over_committed_baseline(self):
        from package_mgmt.lib.service import downloader as dl

        self._insert()
        progress = _LiveProgress(is_tty=True)
        with mock.patch.object(dl, "_fetch_with_retries") as fetch:
            fetch.return_value = (None, 0, "HTTP 500")
            dl.download_pending_binaries(
                self.config, self.conn, progress=progress, opener=self.opener
            )
        on_progress = fetch.call_args.kwargs["on_progress"]
        self.assertTrue(callable(on_progress))
        # In-flight bytes land on top of the committed baseline (0 files,
        # 0 bytes before the first file completes).
        on_progress(4096)
        self.assertIn((0, 4096), progress.updates)
        # The committed per-file update still lands after the fetch.
        self.assertIn((0, 0), progress.updates)

    def test_non_tty_progress_receives_no_live_hook(self):
        from package_mgmt.lib.service import downloader as dl

        self._insert()
        progress = _StubProgress()
        with mock.patch.object(dl, "_fetch_with_retries") as fetch:
            fetch.return_value = (None, 0, "HTTP 500")
            dl.download_pending_binaries(
                self.config, self.conn, progress=progress, opener=self.opener
            )
        self.assertIsNone(fetch.call_args.kwargs.get("on_progress"))
        self.assertEqual(progress.updates, [(0, 0)])


class TestDownloadEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        self.config = _config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.opener = _StubOpener()
        self.progress = _StubProgress()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _url(self, filename: str) -> str:
        return f"{self.config.sources[0].mirror.rstrip('/')}/{filename}"

    def test_happy_path_downloads_verifies_and_marks(self):
        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        filename = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"
        content = make_test_iso._pool_content("vifm_1.0-2_amd64.deb")
        self.opener.add(self._url(filename), content)

        stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(stats.files_downloaded, 1)
        self.assertEqual(stats.bytes_downloaded, len(content))
        self.assertEqual(stats.files_verified, 1)
        self.assertEqual(stats.failures, [])
        self.assertEqual(stats.command_errors, [])
        dest = os.path.join(self.base, "binary", *filename.split("/"))
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), content)
        self.assertEqual(self.progress.totals, (1, len(content)))
        self.assertEqual(self.progress.updates, [(1, len(content))])
        # Per-file commit visible from a fresh connection.
        conn2 = sqlite3.connect(self.config.db_path)
        try:
            self.assertEqual(conn2.execute("SELECT verified FROM binaries").fetchone()[0], 1)
        finally:
            conn2.close()
        parts = [p for p in os.listdir(os.path.dirname(dest)) if p.endswith(".part")]
        self.assertEqual(parts, [])

    def test_missing_index_entry_is_unconfirmable(self):
        _insert_binary(self.conn, "ghost", "9.9-1")
        self.conn.commit()
        stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)
        self.assertEqual(len(stats.command_errors), 1)
        self.assertIn("No index entry for trixie/main ghost 9.9-1", stats.command_errors[0])
        self.assertEqual(stats.files_downloaded, 0)
        self.assertEqual(self.opener.calls, [])
        ghost_dir = os.path.join(self.base, "binary", "pool", "main", "g", "ghost")
        self.assertFalse(os.path.exists(ghost_dir))

    def test_crafted_filename_traversal_is_refused(self):
        """A `Filename` that escapes the cache (`../../…` or absolute) must be
        refused: the record is excluded with a command error and nothing is
        written outside the cache pool."""
        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        path = binary_index_path(f"{self.base}/binary", "trixie", "main")
        for evil in ("../../etc/passwd", "/tmp/escape.deb"):
            with open(path, "rb") as f:
                raw = gzip.decompress(f.read()).decode("utf-8")
            paragraphs = raw.split("\n\n")
            for i, para in enumerate(paragraphs):
                if para.startswith("Package: vifm"):
                    lines = [ln for ln in para.splitlines() if not ln.startswith("Filename: ")]
                    paragraphs[i] = "\n".join(lines) + f"\nFilename: {evil}\n"
                    break
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
                gz.write(("\n\n".join(paragraphs)).encode("utf-8"))
            with open(path, "wb") as f:
                f.write(buf.getvalue())

            stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)

            self.assertEqual(stats.files_downloaded, 0)
            self.assertEqual(stats.failures, [])
            self.assertEqual(len(stats.command_errors), 1)
            self.assertIn("Unsafe Filename", stats.command_errors[0])
            self.assertEqual(self.opener.calls, [])
            conn2 = sqlite3.connect(self.config.db_path)
            try:
                self.assertEqual(conn2.execute("SELECT verified FROM binaries").fetchone()[0], 0)
            finally:
                conn2.close()
            self.assertFalse(os.path.exists(os.path.join(self.base, "binary", "etc")))
            self.assertFalse(os.path.exists(os.path.join(self.base, "binary", "tmp")))
            self.assertFalse(os.path.exists("/tmp/escape.deb"))

    def test_missing_checksum_is_unconfirmable(self):
        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        path = binary_index_path(f"{self.base}/binary", "trixie", "main")
        with open(path, "rb") as f:
            raw = f.read()
        lines = [
            line for line in gzip.decompress(raw).decode("utf-8").splitlines()
            if not line.startswith("SHA256: ")
        ]
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            gz.write(("\n".join(lines) + "\n").encode("utf-8"))
        with open(path, "wb") as f:
            f.write(buf.getvalue())
        stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)
        self.assertEqual(len(stats.command_errors), 1)
        self.assertIn("No index entry for trixie/main vifm 1.0-2", stats.command_errors[0])
        self.assertEqual(self.opener.calls, [])

    def test_missing_cached_index_errors(self):
        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        os.remove(binary_index_path(f"{self.base}/binary", "trixie", "main"))
        stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)
        self.assertEqual(len(stats.command_errors), 1)
        self.assertIn("Cached index missing for trixie/main", stats.command_errors[0])
        self.assertIn("part of the cache", stats.command_errors[0])
        self.assertEqual(self.opener.calls, [])

    def test_checksum_mismatch_removes_part_and_stays_unverified(self):
        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        filename = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"
        expected = make_test_iso._pool_content("vifm_1.0-2_amd64.deb")
        import hashlib
        self.opener.add(self._url(filename), b"corrupt-bytes")

        stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(len(stats.failures), 1)
        self.assertIn(filename, stats.failures[0])
        self.assertIn("checksum mismatch", stats.failures[0])
        self.assertIn(hashlib.sha256(expected).hexdigest()[:4], stats.failures[0])
        dest = os.path.join(self.base, "binary", *filename.split("/"))
        self.assertFalse(os.path.exists(dest))
        self.assertFalse(os.path.exists(dest + ".part"))
        conn2 = sqlite3.connect(self.config.db_path)
        try:
            self.assertEqual(conn2.execute("SELECT verified FROM binaries").fetchone()[0], 0)
        finally:
            conn2.close()
        self.assertEqual(stats.files_verified, 0)

    def test_fetch_failure_reports_and_other_files_continue(self):
        _insert_binary(self.conn, "vifm", "1.0-2")
        _insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        f_vifm = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"
        f_lib1 = "pool/main/l/lib1/lib1_2.0-1_amd64.deb"
        self.opener.fail(self._url(f_vifm), 404)
        self.opener.add(self._url(f_lib1), make_test_iso._pool_content("lib1_2.0-1_amd64.deb"))

        stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(len(stats.failures), 1)
        self.assertEqual(stats.failures[0], f"{f_vifm}: HTTP 404")
        self.assertEqual(stats.files_downloaded, 1)
        self.assertEqual(stats.files_verified, 1)
        conn2 = sqlite3.connect(self.config.db_path)
        try:
            rows = conn2.execute(
                "SELECT package_name, verified FROM binaries ORDER BY package_name"
            ).fetchall()
        finally:
            conn2.close()
        self.assertEqual(rows, [("lib1", 1), ("vifm", 0)])
        dest = os.path.join(self.base, "binary", *f_lib1.split("/"))
        self.assertTrue(os.path.exists(dest))
        self.assertFalse(os.path.exists(os.path.join(self.base, "binary", *f_vifm.split("/"))))

    def test_shared_file_downloaded_once_across_releases(self):
        # Two releases share the same pool path on the mirror (Debian layout):
        # the file must be fetched exactly once and both records verified.
        bookworm_dir = f"{self.base}/binary/dists/bookworm/main/binary-amd64"
        os.makedirs(bookworm_dir, exist_ok=True)
        trixie_gz = binary_index_path(f"{self.base}/binary", "trixie", "main")
        with open(trixie_gz, "rb") as f, open(os.path.join(bookworm_dir, "Packages.gz"), "wb") as out:
            shutil.copyfileobj(f, out)
        config = _config(self.base)
        config = ProjectConfig(
            **{
                **config.__dict__,
                "sources": (
                    ArchiveSource(mirror="http://mirror.invalid", release="trixie", components=("main",)),
                    ArchiveSource(mirror="http://mirror.invalid", release="bookworm", components=("main",)),
                ),
            }
        )
        _insert_binary(self.conn, "vifm", "1.0-2", release="trixie")
        _insert_binary(self.conn, "vifm", "1.0-2", release="bookworm")
        self.conn.commit()
        filename = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"
        content = make_test_iso._pool_content("vifm_1.0-2_amd64.deb")
        self.opener.add(f"http://mirror.invalid/{filename}", content)

        stats = download_pending_binaries(config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(len(self.opener.calls), 1)
        self.assertEqual(stats.files_downloaded, 1)
        self.assertEqual(stats.files_verified, 2)
        conn2 = sqlite3.connect(self.config.db_path)
        try:
            verified = conn2.execute("SELECT COUNT(*) FROM binaries WHERE verified = 1").fetchone()[0]
        finally:
            conn2.close()
        self.assertEqual(verified, 2)

    def test_zero_pending_is_a_noop(self):
        stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)
        self.assertEqual(stats.files_downloaded, 0)
        self.assertEqual(stats.files_verified, 0)
        self.assertEqual(stats.failures, [])
        self.assertEqual(self.opener.calls, [])

    def test_local_component_binary_is_not_rechecked_against_archive_index(self):
        """A `local` record is verified at import time from the supplied
        payload and has no archive index to confirm against, so the binary
        phase must not group it under `(<release>, local)`: `download` reports
        zero command errors (no missing-`local`-index error), fetches nothing,
        and the record stays verified."""
        rid = _insert_binary(self.conn, "patch-tool", "1.2-3", component="local", verified=1)
        self.conn.commit()
        stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)
        self.assertEqual(stats.command_errors, [])
        self.assertEqual(stats.failures, [])
        self.assertEqual(stats.files_downloaded, 0)
        self.assertEqual(self.opener.calls, [])
        self.assertEqual(
            self.conn.execute("SELECT verified FROM binaries WHERE id = ?", (rid,)).fetchone()[0],
            1,
        )

    def test_local_and_archive_records_mix_cleanly(self):
        """A cache holding both a `local` record and an archive record stays
        clean: the archive record is processed normally and the `local` one
        contributes no command errors."""
        _insert_binary(self.conn, "patch-tool", "1.2-3", component="local", verified=1)
        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        filename = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"
        content = make_test_iso._pool_content("vifm_1.0-2_amd64.deb")
        self.opener.add(self._url(filename), content)

        stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(stats.command_errors, [])
        self.assertEqual(stats.failures, [])
        self.assertEqual(stats.files_downloaded, 1)
        self.assertEqual(stats.files_verified, 1)
        self.assertEqual(len(self.opener.calls), 1)

    def test_verified_file_with_matching_size_is_rehashed_but_not_refetched(self):
        """A verified record whose pool file matches the index Size skips the
        re-fetch only — the checksum is always re-hashed (a size match never
        skips the O(file) verification)."""
        _insert_binary(self.conn, "vifm", "1.0-2", verified=1)
        self.conn.commit()
        filename = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"
        content = make_test_iso._pool_content("vifm_1.0-2_amd64.deb")
        dest = os.path.join(self.base, "binary", *filename.split("/"))
        os.makedirs(os.path.dirname(dest))
        with open(dest, "wb") as f:
            f.write(content)

        with mock.patch(
            "package_mgmt.lib.service.downloader._file_sha256",
            wraps=_file_sha256,
        ) as sha:
            stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertTrue(sha.called, "a matching size must still re-hash the file")
        self.assertEqual(stats.files_downloaded, 0)
        self.assertEqual(stats.failures, [])
        self.assertEqual(self.opener.calls, [])
        conn2 = sqlite3.connect(self.config.db_path)
        try:
            self.assertEqual(conn2.execute("SELECT verified FROM binaries").fetchone()[0], 1)
        finally:
            conn2.close()

    def test_verified_file_with_wrong_size_is_rechecked_and_refetched(self):
        """A verified record whose pool file has a different Size than the
        index is re-hashed, found bad, and re-downloaded."""
        _insert_binary(self.conn, "vifm", "1.0-2", verified=1)
        self.conn.commit()
        filename = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"
        content = make_test_iso._pool_content("vifm_1.0-2_amd64.deb")
        dest = os.path.join(self.base, "binary", *filename.split("/"))
        os.makedirs(os.path.dirname(dest))
        with open(dest, "wb") as f:
            f.write(b"stale-bytes-with-different-length")
        self.opener.add(self._url(filename), content)

        stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(stats.files_downloaded, 1)
        self.assertEqual(stats.failures, [])
        self.assertEqual(len(self.opener.calls), 1)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), content)

    def test_lookup_prefers_record_architecture(self):
        """A multi-arch index entry (same name+version under two archs)
        resolves to the record's own architecture; an unknown arch falls
        back to the plain version match."""
        import gzip as _gzip

        content_all = make_test_iso._pool_content("vifm_1.0-2_all.deb")
        para = "\n".join([
            "Package: vifm",
            "Version: 1.0-2",
            "Architecture: all",
            "Filename: pool/main/v/vifm/vifm_1.0-2_all.deb",
            f"Size: {len(content_all)}",
            f"SHA256: {hashlib.sha256(content_all).hexdigest()}",
        ])
        path = binary_index_path(f"{self.base}/binary", "trixie", "main")
        with open(path, "rb") as f:
            raw = _gzip.decompress(f.read()).decode("utf-8")
        buf = io.BytesIO()
        with _gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            gz.write((raw + "\n\n" + para + "\n").encode("utf-8"))
        with open(path, "wb") as f:
            f.write(buf.getvalue())

        _insert_binary(self.conn, "vifm", "1.0-2", architecture="amd64")
        _insert_binary(self.conn, "vifm", "1.0-2", architecture="all")
        _insert_binary(self.conn, "vifm", "1.0-2", architecture="arm64")
        self.conn.commit()
        f_amd64 = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"
        f_all = "pool/main/v/vifm/vifm_1.0-2_all.deb"
        self.opener.add(self._url(f_amd64), make_test_iso._pool_content("vifm_1.0-2_amd64.deb"))
        self.opener.add(self._url(f_all), content_all)

        stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(stats.files_downloaded, 2)
        self.assertEqual(stats.failures, [])
        self.assertEqual(sorted(self.opener.calls), [self._url(f_all), self._url(f_amd64)])
        for f in (f_all, f_amd64):
            dest = os.path.join(self.base, "binary", *f.split("/"))
            self.assertTrue(os.path.exists(dest), f)
        conn2 = sqlite3.connect(self.config.db_path)
        try:
            verified = conn2.execute("SELECT COUNT(*) FROM binaries WHERE verified = 1").fetchone()[0]
        finally:
            conn2.close()
        self.assertEqual(verified, 3)


class TestUnverifiedShortCircuit(unittest.TestCase):
    """Present-and-valid files are not re-downloaded for unverified records;
    the re-check confirms integrity and (re)verifies them without a fetch.
    Mismatched or absent files are still fetched."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        make_test_iso.write_pool_files(self.base)
        self.config = _config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.opener = _StubOpener()
        self.progress = _StubProgress()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _url(self, filename: str) -> str:
        return f"{self.config.sources[0].mirror.rstrip('/')}/{filename}"

    def _pool_dest(self, filename: str) -> str:
        return os.path.join(self.base, "binary", *filename.split("/"))

    def test_unverified_present_and_matching_file_is_not_reefetched_but_reverified(self):
        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        filename = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"
        dest = self._pool_dest(filename)
        self.assertTrue(os.path.exists(dest), "fixture pool file must be on disk")

        stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(self.opener.calls, [])
        self.assertEqual(stats.planned_files, 0)
        self.assertEqual(stats.files_downloaded, 0)
        self.assertEqual(stats.failures, [])
        self.assertEqual(
            self.conn.execute("SELECT verified FROM binaries").fetchone()[0], 1,
            "the re-check confirms integrity and re-verifies without a download",
        )

    def test_unverified_mismatched_file_is_reefetched(self):
        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        filename = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"
        dest = self._pool_dest(filename)
        with open(dest, "wb") as f:
            f.write(b"corrupt-bytes")
        content = make_test_iso._pool_content("vifm_1.0-2_amd64.deb")
        self.opener.add(self._url(filename), content)

        stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(self.opener.calls, [self._url(filename)])
        self.assertEqual(stats.files_downloaded, 1)
        self.assertEqual(stats.failures, [])
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), content)
        self.assertEqual(
            self.conn.execute("SELECT verified FROM binaries").fetchone()[0], 1,
            "a successful re-fetch verifies the record",
        )

    def test_unverified_absent_file_is_fetched(self):
        _insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        filename = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"
        dest = self._pool_dest(filename)
        os.remove(dest)
        content = make_test_iso._pool_content("vifm_1.0-2_amd64.deb")
        self.opener.add(self._url(filename), content)

        stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(self.opener.calls, [self._url(filename)])
        self.assertEqual(stats.files_downloaded, 1)
        self.assertEqual(stats.failures, [])
        self.assertTrue(os.path.exists(dest))

    def test_mix_shortcircuits_valid_and_fetches_missing(self):
        _insert_binary(self.conn, "vifm", "1.0-2")
        _insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        filename_missing = "pool/main/l/lib1/lib1_2.0-1_amd64.deb"
        os.remove(self._pool_dest(filename_missing))
        content = make_test_iso._pool_content("lib1_2.0-1_amd64.deb")
        self.opener.add(self._url(filename_missing), content)

        stats = download_pending_binaries(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(self.opener.calls, [self._url(filename_missing)])
        self.assertEqual(stats.files_downloaded, 1)
        self.assertEqual(stats.failures, [])
        rows = dict(self.conn.execute("SELECT package_name, verified FROM binaries").fetchall())
        self.assertEqual(rows["vifm"], 1, "the valid-but-unverified record is re-verified without a fetch")
        self.assertEqual(rows["lib1"], 1, "the re-fetched record is verified")

    def test_unverified_source_with_all_files_present_is_reverified_without_fetch(self):
        bid = _insert_binary(self.conn, "vifm", "1.0-2")
        sid = _insert_source(self.conn, "vifm", "1.0-2", verified=0)
        _link(self.conn, bid, sid)
        self.conn.commit()
        urls = {}
        directory = make_test_iso.source_pool_directory("main", "vifm")
        for fname in make_test_iso.source_files("vifm", "1.0-2"):
            dest = os.path.join(
                self.base, "source", *directory.split("/"), fname
            )
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(make_test_iso._pool_content(fname))
            urls[fname] = self._url(f"{directory}/{fname}")

        stats = download_pending_sources(self.config, self.conn, progress=self.progress, opener=self.opener)

        self.assertEqual(self.opener.calls, [])
        self.assertEqual(stats.files_downloaded, 0)
        self.assertEqual(stats.failures, [])
        self.assertEqual(
            self.conn.execute("SELECT verified FROM sources WHERE id = ?", (sid,)).fetchone()[0], 1,
            "all-present source is re-verified without re-downloading",
        )


class _CountingPoolHandler(http.server.BaseHTTPRequestHandler):
    """HTTP/1.1 keep-alive handler that counts accepted connections."""

    protocol_version = "HTTP/1.1"
    connections = 0

    def setup(self):
        super().setup()
        type(self).connections += 1

    def do_GET(self):
        body = b"pool-bytes"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class _ResetOnceHandler(_CountingPoolHandler):
    """First connection resets mid-body (Content-Length lies, then the socket
    is RST-closed via SO_LINGER — a real "connection reset by peer"); later
    connections serve `server.body` fully."""

    def do_GET(self):
        body = getattr(self.server, "body", b"")
        if type(self).connections == 1:
            self.send_response(200)
            self.send_header("Content-Length", str(len(body) + 100))
            self.end_headers()
            self.wfile.write(body[:10])
            self.wfile.flush()
            import socket
            import struct
            linger = struct.pack("ii", 1, 0)
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
            self.connection.close()
            self.close_connection = True
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TestPooledOpener(unittest.TestCase):
    """Connection reuse: the production opener keeps one keep-alive
    connection per mirror and drops it when a read fails mid-body."""

    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CountingPoolHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        _CountingPoolHandler.connections = 0
        _ResetOnceHandler.connections = 0
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()

    def test_reuses_one_connection_across_files(self):
        opener = _PooledOpener()
        for i in range(3):
            response = opener(f"{self.base_url}/file{i}")
            self.assertEqual(response.read(), b"pool-bytes")
            response.close()
        self.assertEqual(_CountingPoolHandler.connections, 1)
        opener.close()

    def test_read_reset_evicts_connection_and_next_request_uses_fresh(self):
        self.server.RequestHandlerClass = _ResetOnceHandler
        self.server.body = b"full-bytes"
        opener = _PooledOpener()
        response = opener(f"{self.base_url}/reset")
        with self.assertRaises(ConnectionError):
            while response.read(65536):
                pass
        # The broken connection must not be offered again: pool is empty.
        self.assertEqual(opener._conns, {})
        response = opener(f"{self.base_url}/retry")
        self.assertEqual(response.read(), b"full-bytes")
        response.close()
        self.assertEqual(_ResetOnceHandler.connections, 2)
        opener.close()

    def test_engine_recovers_after_mid_read_reset_over_real_http(self):
        # End to end: a connection reset mid-read is retried by the engine
        # (backoff) and succeeds on a fresh pooled connection.
        self.server.RequestHandlerClass = _ResetOnceHandler
        self.server.body = make_test_iso._pool_content("vifm_1.0-2_amd64.deb")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = os.path.join(tmp.name, "cache")
        make_test_iso.write_archive(base)
        config = _config(base, mirror=self.base_url)
        initialize_database(config.db_path)
        conn = sqlite3.connect(config.db_path)
        self.addCleanup(conn.close)
        _insert_binary(conn, "vifm", "1.0-2")
        conn.commit()

        stats = download_pending_binaries(
            config, conn, opener=_PooledOpener(), sleep=lambda _: None
        )

        self.assertEqual(stats.files_downloaded, 1)
        self.assertEqual(stats.failures, [])
        self.assertEqual(_ResetOnceHandler.connections, 2)
        dest = os.path.join(base, "binary", "pool", "main", "v", "vifm", "vifm_1.0-2_amd64.deb")
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), self.server.body)


class _HistogramHandler(http.server.BaseHTTPRequestHandler):
    """Serves deterministic pool content; special paths exercise throttling,
    errors and redirects so the opener's HTTP histogram can be observed."""

    protocol_version = "HTTP/1.1"
    throttled = 0

    def do_GET(self):
        if self.path == "/throttle" and type(self).throttled == 0:
            type(self).throttled = 1
            self.send_response(429)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/notfound":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/pool/main/v/vifm/vifm_1.0-2_amd64.deb")
            self.end_headers()
            return
        body = make_test_iso._pool_content(os.path.basename(self.path))
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class TestStatusHistogram(unittest.TestCase):
    """The per-run HTTP histogram: every response status the opener observes
    (success, followed redirects, 4xx/5xx incl. each retried attempt) and
    network errors that never produced an HTTP status."""

    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HistogramHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        _HistogramHandler.throttled = 0
        _ResetOnceHandler.connections = 0
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.pool_url = self.base_url + "/pool/main/v/vifm/vifm_1.0-2_amd64.deb"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()

    def test_success_and_error_statuses_are_counted(self):
        opener = _PooledOpener()
        response = opener(self.pool_url)
        self.assertEqual(response.read(), make_test_iso._pool_content("vifm_1.0-2_amd64.deb"))
        response.close()
        with self.assertRaises(urllib.error.HTTPError):
            opener(self.base_url + "/notfound")
        self.assertEqual(opener.status_counts, {200: 1, 404: 1})
        self.assertEqual(opener.network_errors, 0)
        opener.close()

    def test_followed_redirect_is_counted(self):
        opener = _PooledOpener()
        response = opener(self.base_url + "/redirect")
        self.assertEqual(response.read(), make_test_iso._pool_content("vifm_1.0-2_amd64.deb"))
        response.close()
        self.assertEqual(opener.status_counts, {302: 1, 200: 1})
        opener.close()

    def test_throttled_retry_counts_each_attempt(self):
        from package_mgmt.lib.service.downloader import _fetch_with_retries

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        dest = os.path.join(tmp.name, "out")
        opener = _PooledOpener()
        digest, total, error = _fetch_with_retries(
            opener, self.base_url + "/throttle", dest, sleep=lambda _: None
        )
        self.assertIsNone(error)
        self.assertEqual(opener.status_counts, {429: 1, 200: 1})
        opener.close()

    def test_connection_refused_is_a_network_error_not_a_status(self):
        # A dead port: no HTTP status was ever exchanged.
        dead = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HistogramHandler)
        dead_thread = threading.Thread(target=dead.serve_forever, daemon=True)
        dead_thread.start()
        port = dead.server_address[1]
        dead.shutdown()
        dead_thread.join()
        dead.server_close()
        opener = _PooledOpener()
        with self.assertRaises(urllib.error.URLError):
            opener(f"http://127.0.0.1:{port}/x")
        self.assertEqual(opener.status_counts, {})
        self.assertEqual(opener.network_errors, 1)
        opener.close()

    def test_mid_read_reset_is_a_network_error(self):
        self.server.RequestHandlerClass = _ResetOnceHandler
        self.server.body = b"full-bytes"
        opener = _PooledOpener()
        response = opener(self.base_url + "/reset")
        with self.assertRaises(ConnectionError):
            while response.read(65536):
                pass
        self.assertEqual(opener.status_counts, {200: 1})
        self.assertEqual(opener.network_errors, 1)
        opener.close()

    def test_engine_copies_histogram_into_stats(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = os.path.join(tmp.name, "cache")
        make_test_iso.write_archive(base)
        config = _config(base, mirror=self.base_url)
        initialize_database(config.db_path)
        conn = sqlite3.connect(config.db_path)
        self.addCleanup(conn.close)
        _insert_binary(conn, "vifm", "1.0-2")
        conn.commit()

        stats = download_pending_binaries(
            config, conn, opener=_PooledOpener(), sleep=lambda _: None
        )
        self.assertEqual(stats.files_downloaded, 1)
        self.assertEqual(stats.failures, [])
        self.assertEqual(stats.status_counts, {200: 1})
        self.assertEqual(stats.network_errors, 0)


if __name__ == "__main__":
    unittest.main()
