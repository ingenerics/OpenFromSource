import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_iso  # noqa: E402

from package_mgmt.cli.commands.add import handle_add  # noqa: E402
from package_mgmt.cli.commands.download import handle_download  # noqa: E402
from package_mgmt.cli.commands.init import handle_init  # noqa: E402
from package_mgmt.lib.service.config_loader import load_config  # noqa: E402
from package_mgmt.lib.util.errors import EXIT_SUCCESS, EXIT_ERROR  # noqa: E402


def _write_defaults(base, content: str):
    with open(os.path.join(base, "project.defaults.toml"), "w") as f:
        f.write(content)


class DownloadFlowTest(unittest.TestCase):
    """End-to-end download flows against a local HTTP mirror."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_base = os.path.join(self.tmp.name, "cache")
        self.fixture_base = os.path.join(self.tmp.name, "fixture")
        # The resolver reads the indexes from the cache trees; the mirror
        # serves only the shared pool (a separate copy, so pool edits and
        # cache edits are independent).
        make_test_iso.write_archive(self.cache_base)
        make_test_iso.write_pool_files(self.fixture_base)
        self.server = make_test_iso.serve_pool(self.fixture_base, port=0)
        self.port = self.server.server_address[1]
        _write_defaults(
            self.tmp.name,
            f'[paths]\nbase_dir = "{self.cache_base}"\n[fetch]\nsource = false\n\n'
            "[[archive.source]]\n"
            f'mirror = "http://127.0.0.1:{self.port}"\n'
            'release = "trixie"\ncomponents = ["main", "contrib"]\n',
        )
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.server.shutdown()
        self.server.thread.join()
        self.tmp.cleanup()

    def _provision(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add(["vifm", "lib1"])
        self.assertEqual(rc, EXIT_SUCCESS)
        return load_config()[0]

    def _run(self, config):
        with contextlib.redirect_stdout(io.StringIO()):
            return handle_download(config.db_path, config)

    def _pool_deb(self, name, version):
        return os.path.join(
            self.cache_base, "binary", "pool", "main", name[0], name, f"{name}_{version}_amd64.deb"
        )

    def test_happy_path_downloads_every_deb_and_verifies(self):
        config = self._provision()
        rc = self._run(config)
        self.assertEqual(rc, EXIT_SUCCESS)
        for name, version in (("vifm", "1.0-2"), ("lib1", "2.0-1"), ("lib2", "3.0-1")):
            self.assertTrue(
                os.path.exists(self._pool_deb(name, version)),
                f"missing {name} in the pool",
            )
        conn = sqlite3.connect(config.db_path)
        try:
            pending = conn.execute(
                "SELECT COUNT(*) FROM binaries WHERE status = 'current' AND verified = 0"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(pending, 0)
        parts = []
        for root, _, files in os.walk(os.path.join(self.cache_base, "binary")):
            parts.extend(f for f in files if f.endswith(".part"))
        self.assertEqual(parts, [])

    def test_idempotent_rerun_downloads_nothing(self):
        config = self._provision()
        self.assertEqual(self._run(config), EXIT_SUCCESS)
        conn = sqlite3.connect(config.db_path)
        try:
            before = conn.execute(
                "SELECT package_name, version, verified FROM binaries ORDER BY 1, 2"
            ).fetchall()
        finally:
            conn.close()
        rc = self._run(config)
        self.assertEqual(rc, EXIT_SUCCESS)
        conn = sqlite3.connect(config.db_path)
        try:
            after = conn.execute(
                "SELECT package_name, version, verified FROM binaries ORDER BY 1, 2"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(before, after)

    def test_reset_unverified_records_do_not_trigger_full_redownload(self):
        config = self._provision()
        self.assertEqual(self._run(config), EXIT_SUCCESS)
        conn = sqlite3.connect(config.db_path)
        conn.execute("UPDATE binaries SET verified = 0")
        conn.commit()
        conn.close()

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_download(config.db_path, config)
        self.assertEqual(rc, EXIT_SUCCESS)
        out = buf.getvalue()
        self.assertIn("Binary phase: nothing to download.", out)
        self.assertIn("Source phase: nothing to download.", out)
        conn = sqlite3.connect(config.db_path)
        try:
            current = conn.execute(
                "SELECT COUNT(*) FROM binaries WHERE status = 'current'"
            ).fetchone()[0]
            verified = conn.execute(
                "SELECT COUNT(*) FROM binaries WHERE status = 'current' AND verified = 1"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertGreater(current, 0)
        self.assertEqual(current, verified, "present-and-valid files are re-verified without a re-download")

    def test_delete_one_file_rerun_redownloads_exactly_it(self):
        config = self._provision()
        self.assertEqual(self._run(config), EXIT_SUCCESS)
        deb = self._pool_deb("vifm", "1.0-2")
        os.remove(deb)
        rc = self._run(config)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertTrue(os.path.exists(deb), "deleted file must be re-downloaded")
        conn = sqlite3.connect(config.db_path)
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT verified FROM binaries WHERE package_name = 'vifm'"
                ).fetchone()[0],
                1,
            )
        finally:
            conn.close()

    def test_corrupt_file_mismatch_removes_and_reports(self):
        config = self._provision()
        self.assertEqual(self._run(config), EXIT_SUCCESS)
        # Corrupt the MIRROR copy: the downloaded content must then fail the
        # index checksum on re-download.
        mirror_deb = os.path.join(
            self.fixture_base, "binary", "pool", "main", "v", "vifm", "vifm_1.0-2_amd64.deb"
        )
        with open(mirror_deb, "wb") as f:
            f.write(b"corrupt-bytes")
        conn = sqlite3.connect(config.db_path)
        conn.execute("UPDATE binaries SET verified = 0 WHERE package_name = 'vifm'")
        conn.commit()
        conn.close()
        deb = self._pool_deb("vifm", "1.0-2")
        os.remove(deb)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_download(config.db_path, config)
        self.assertEqual(rc, EXIT_ERROR)
        out = buf.getvalue()
        self.assertIn("checksum mismatch", out)
        self.assertIn("pool/main/v/vifm/vifm_1.0-2_amd64.deb", out)
        self.assertFalse(os.path.exists(deb), "corrupt file must be removed")
        self.assertFalse(os.path.exists(deb + ".part"), "no .part may remain")
        conn = sqlite3.connect(config.db_path)
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT verified FROM binaries WHERE package_name = 'vifm'"
                ).fetchone()[0],
                0,
            )
        finally:
            conn.close()
        # A re-run against the restored mirror converges.
        with open(mirror_deb, "wb") as f:
            f.write(make_test_iso._pool_content("vifm_1.0-2_amd64.deb"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_download(config.db_path, config)
        self.assertEqual(rc, EXIT_SUCCESS)

    def test_missing_file_fails_that_file_others_continue(self):
        config = self._provision()
        self.assertEqual(self._run(config), EXIT_SUCCESS)
        # Reset all binaries, delete vifm's cache file, and remove it from the
        # mirror pool (permanent 404): only vifm must be re-fetched; the
        # others are present-and-valid and are re-verified without re-download.
        conn = sqlite3.connect(config.db_path)
        conn.execute("UPDATE binaries SET verified = 0")
        conn.commit()
        conn.close()
        os.remove(self._pool_deb("vifm", "1.0-2"))
        os.remove(os.path.join(
            self.fixture_base, "binary", "pool", "main", "v", "vifm", "vifm_1.0-2_amd64.deb"
        ))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_download(config.db_path, config)
        self.assertEqual(rc, EXIT_ERROR)
        out = buf.getvalue()
        self.assertIn("pool/main/v/vifm/vifm_1.0-2_amd64.deb: HTTP 404", out)
        self.assertIn("Total: 0 files downloaded, 0 verified, 1 failed (", out)
        conn = sqlite3.connect(config.db_path)
        try:
            rows = conn.execute(
                "SELECT package_name, verified FROM binaries ORDER BY package_name"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(rows, [("lib1", 1), ("lib2", 1), ("vifm", 0)])


class SafeRunsFlowTest(unittest.TestCase):
    """Mirror-polite retries, interrupted-run convergence,
    and missing-index errors."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_base = os.path.join(self.tmp.name, "cache")
        self.fixture_base = os.path.join(self.tmp.name, "fixture")
        make_test_iso.write_archive(self.cache_base)
        make_test_iso.write_pool_files(self.fixture_base)
        self._old_cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, port: int, fetch_source: bool = True):
        _write_defaults(
            self.tmp.name,
            f'[paths]\nbase_dir = "{self.cache_base}"\n[fetch]\nsource = {"true" if fetch_source else "false"}\n\n'
            "[[archive.source]]\n"
            f'mirror = "http://127.0.0.1:{port}"\n'
            'release = "trixie"\ncomponents = ["main", "contrib"]\n',
        )

    def _provision(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add(["vifm", "lib1"])
        self.assertEqual(rc, EXIT_SUCCESS)
        return load_config()[0]

    def test_rate_limited_server_completes_with_zero_refusals(self):
        self.server = make_test_iso.serve_rate_limited(self.fixture_base, port=0, limit=3)
        try:
            self._write_defaults(self.server.server_address[1], fetch_source=False)
            os.chdir(self.tmp.name)
            config = self._provision()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = handle_download(config.db_path, config)
            self.assertEqual(rc, EXIT_SUCCESS)
            out = buf.getvalue()
            self.assertNotIn("retries exhausted", out)
            self.assertIn("Total: 3 files downloaded, 3 verified, 0 failed (", out)
            conn = sqlite3.connect(config.db_path)
            try:
                pending = conn.execute(
                    "SELECT COUNT(*) FROM binaries WHERE verified = 0"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(pending, 0)
            refusals = sum(
                v for k, v in self.server.request_counts.items()
                if v > 3
            )
            self.assertGreater(refusals, 0, "the retry path must actually be exercised")
        finally:
            self.server.shutdown()
            self.server.thread.join()

    def test_interrupted_run_converges_on_rerun(self):
        # A crashed run leaves stale *.part files in the pool; the re-run must
        # clean them and converge to the same state as a clean run.
        self.server = make_test_iso.serve_pool(self.fixture_base, port=0)
        try:
            self._write_defaults(self.server.server_address[1], fetch_source=False)
            os.chdir(self.tmp.name)
            config = self._provision()
            for name, version in (("vifm", "1.0-2"), ("lib1", "2.0-1"), ("lib2", "3.0-1")):
                dest = os.path.join(
                    self.cache_base, "binary", "pool", "main", name[0], name,
                    f"{name}_{version}_amd64.deb",
                )
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest + ".part", "wb") as f:
                    f.write(b"partial-bytes-from-a-crashed-run")

            with contextlib.redirect_stdout(io.StringIO()):
                rc = handle_download(config.db_path, config)
            self.assertEqual(rc, EXIT_SUCCESS)

            parts = []
            for root, _, files in os.walk(os.path.join(self.cache_base, "binary")):
                parts.extend(f for f in files if f.endswith(".part"))
            self.assertEqual(parts, [], "no *.part files may remain after the run")
            for name, version in (("vifm", "1.0-2"), ("lib1", "2.0-1"), ("lib2", "3.0-1")):
                dest = os.path.join(
                    self.cache_base, "binary", "pool", "main", name[0], name,
                    f"{name}_{version}_amd64.deb",
                )
                with open(dest, "rb") as f:
                    self.assertEqual(
                        f.read(), make_test_iso._pool_content(f"{name}_{version}_amd64.deb"),
                        f"{name} must hold the verified content",
                    )
            conn = sqlite3.connect(config.db_path)
            try:
                pending = conn.execute(
                    "SELECT COUNT(*) FROM binaries WHERE verified = 0"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(pending, 0)
        finally:
            self.server.shutdown()
            self.server.thread.join()

    def test_missing_cached_index_errors_and_downloads_nothing(self):
        self.server = make_test_iso.serve_pool(self.fixture_base, port=0)
        try:
            self._write_defaults(self.server.server_address[1], fetch_source=False)
            os.chdir(self.tmp.name)
            config = self._provision()
            os.remove(os.path.join(
                self.cache_base, "binary", "dists", "trixie", "main", "binary-amd64", "Packages.gz"
            ))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = handle_download(config.db_path, config)
            self.assertEqual(rc, EXIT_ERROR)
            payload = json.loads(err.getvalue())
            self.assertIn("Cached index missing for trixie/main", payload["error"])
            self.assertEqual(payload["exit_code"], 1)
            debs = []
            for root, _, files in os.walk(os.path.join(self.cache_base, "binary", "pool")):
                debs.extend(f for f in files if f.endswith(".deb"))
            self.assertEqual(debs, [], "nothing may be downloaded")
        finally:
            self.server.shutdown()
            self.server.thread.join()


class SourceDownloadFlowTest(unittest.TestCase):
    """Source artifacts download when fetch_source is enabled."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_base = os.path.join(self.tmp.name, "cache")
        self.fixture_base = os.path.join(self.tmp.name, "fixture")
        make_test_iso.write_archive(self.cache_base)
        make_test_iso.write_pool_files(self.fixture_base)
        self.server = make_test_iso.serve_pool(self.fixture_base, port=0)
        port = self.server.server_address[1]
        _write_defaults(
            self.tmp.name,
            f'[paths]\nbase_dir = "{self.cache_base}"\n[fetch]\nsource = true\n[resolution]\nmax_source_dep_depth = -1\n\n'
            "[[archive.source]]\n"
            f'mirror = "http://127.0.0.1:{port}"\n'
            'release = "trixie"\ncomponents = ["main", "contrib"]\n',
        )
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.server.shutdown()
        self.server.thread.join()
        self.tmp.cleanup()

    def _provision(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add(["vifm", "lib1"])
        self.assertEqual(rc, EXIT_SUCCESS)
        return load_config()[0]

    def _run(self, config):
        with contextlib.redirect_stdout(io.StringIO()):
            return handle_download(config.db_path, config)

    def _source_file(self, name, version, fname):
        directory = make_test_iso.source_pool_directory("main", name)
        return os.path.join(self.cache_base, "source", *directory.split("/"), fname)

    def test_source_phase_downloads_linked_sources_and_marks_verified(self):
        config = self._provision()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_download(config.db_path, config)
        self.assertEqual(rc, EXIT_SUCCESS)
        out = buf.getvalue()
        self.assertIn("Source phase summary", out)
        self.assertIn("Files downloaded:  9", out)
        self.assertIn("Files verified:    9", out)
        self.assertIn("Total: 12 files downloaded, 12 verified, 0 failed (", out)

        for name, version in (("vifm", "1.0-2"), ("lib1", "2.0-1"), ("lib2", "3.0-1")):
            for fname in make_test_iso.source_files(name, version):
                self.assertTrue(
                    os.path.exists(self._source_file(name, version, fname)),
                    f"missing source file {fname}",
                )
        conn = sqlite3.connect(config.db_path)
        try:
            unverified = conn.execute(
                "SELECT COUNT(*) FROM sources WHERE verified = 0"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(unverified, 0)

    def test_corrupt_source_file_keeps_source_unverified(self):
        config = self._provision()
        self.assertEqual(self._run(config), EXIT_SUCCESS)
        # Corrupt the mirror's copy of vifm's .dsc and reset the source.
        mirror_dsc = os.path.join(
            self.fixture_base, "binary", "pool", "main", "v", "vifm", "vifm_1.0-2.dsc"
        )
        with open(mirror_dsc, "wb") as f:
            f.write(b"corrupt-dsc")
        conn = sqlite3.connect(config.db_path)
        conn.execute(
            "UPDATE sources SET verified = 0"
            " WHERE package_name = 'vifm' AND source_version = '1.0-2'"
        )
        conn.commit()
        conn.close()
        dsc_path = self._source_file("vifm", "1.0-2", "vifm_1.0-2.dsc")
        os.remove(dsc_path)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_download(config.db_path, config)
        self.assertEqual(rc, EXIT_ERROR)
        out = buf.getvalue()
        self.assertIn("checksum mismatch", out)
        self.assertIn("vifm_1.0-2.dsc", out)
        self.assertFalse(os.path.exists(dsc_path), "bad dsc must be removed")
        conn = sqlite3.connect(config.db_path)
        try:
            vifm = conn.execute(
                "SELECT verified FROM sources WHERE package_name = 'vifm'"
            ).fetchone()[0]
            others = conn.execute(
                "SELECT COUNT(*) FROM sources WHERE package_name != 'vifm' AND verified = 0"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(vifm, 0, "source with a bad file must stay unverified")
        self.assertEqual(others, 0, "other sources must still verify")
        # A re-run against the restored mirror converges.
        with open(mirror_dsc, "wb") as f:
            f.write(make_test_iso._pool_content("vifm_1.0-2.dsc"))
        self.assertEqual(self._run(config), EXIT_SUCCESS)

    def test_deleted_verified_source_file_is_reefetched_on_rerun(self):
        config = self._provision()
        self.assertEqual(self._run(config), EXIT_SUCCESS)
        conn = sqlite3.connect(config.db_path)
        try:
            self.assertEqual(
                conn.execute("SELECT verified FROM sources WHERE package_name = 'vifm'").fetchone()[0],
                1,
            )
        finally:
            conn.close()

        deleted = "vifm_1.0-2.orig.tar.xz"
        deleted_path = self._source_file("vifm", "1.0-2", deleted)
        os.remove(deleted_path)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_download(config.db_path, config)
        self.assertEqual(rc, EXIT_SUCCESS)
        out = buf.getvalue()
        self.assertIn("Source phase summary", out)
        self.assertIn("Files downloaded:  1", out)
        self.assertIn("Files verified:    1", out)
        self.assertTrue(os.path.exists(deleted_path), "deleted source file must be restored")
        conn = sqlite3.connect(config.db_path)
        try:
            vifm = conn.execute(
                "SELECT verified FROM sources WHERE package_name = 'vifm'"
            ).fetchone()[0]
            others = conn.execute(
                "SELECT COUNT(*) FROM sources WHERE package_name != 'vifm' AND verified = 0"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(vifm, 1, "the re-checked source must be re-verified")
        self.assertEqual(others, 0, "other sources must remain verified and untouched")

    def test_all_intact_verified_sources_rerun_downloads_nothing(self):
        config = self._provision()
        self.assertEqual(self._run(config), EXIT_SUCCESS)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_download(config.db_path, config)
        self.assertEqual(rc, EXIT_SUCCESS)
        out = buf.getvalue()
        self.assertIn("Binary phase: nothing to download.", out)
        self.assertIn("Source phase: nothing to download.", out)

    def test_fetch_source_false_downloads_binaries_only(self):
        _write_defaults(
            self.tmp.name,
            f'[paths]\nbase_dir = "{self.cache_base}"\n[fetch]\nsource = false\n\n'
            "[[archive.source]]\n"
            f'mirror = "http://127.0.0.1:{self.server.server_address[1]}"\n'
            'release = "trixie"\ncomponents = ["main", "contrib"]\n',
        )
        config = self._provision()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_download(config.db_path, config)
        self.assertEqual(rc, EXIT_SUCCESS)
        out = buf.getvalue()
        self.assertIn("Source phase: nothing to download.", out)
        conn = sqlite3.connect(config.db_path)
        try:
            sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(sources, 0, "no source records exist without fetch_source")
        source_pool = os.path.join(self.cache_base, "source", "pool")
        found = []
        for root, _, files in os.walk(source_pool):
            found.extend(files)
        self.assertEqual(found, [], "no source files may be fetched")

    def test_regular_user_profile_skips_source_phase(self):
        """`download` under the regular_user profile (source=false,
        depth 0) skips the source phase (Sources: 0) and exits 0."""
        profiles = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "profiles")
        shutil.copy(
            os.path.join(profiles, "regular_user.toml"),
            os.path.join(self.tmp.name, "project.defaults.toml"),
        )
        # The local overlay points the profile at the scratch cache and the
        # local fixture mirror (profiles are drop-in defaults; overlays still
        # apply on top, including the archive sources).
        with open(os.path.join(self.tmp.name, "project.local.toml"), "w") as f:
            f.write(
                f'[paths]\nbase_dir = "{self.cache_base}"\n\n'
                "[[archive.source]]\n"
                f'mirror = "http://127.0.0.1:{self.server.server_address[1]}"\n'
                'release = "trixie"\ncomponents = ["main", "contrib"]\n'
            )
        config = self._provision()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_download(config.db_path, config)
        self.assertEqual(rc, EXIT_SUCCESS)
        out = buf.getvalue()
        self.assertIn("Source phase: nothing to download.", out,
                      "regular_user must skip the source phase")
        conn = sqlite3.connect(config.db_path)
        try:
            sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(sources, 0, "regular_user fetches no source records")


class PreDependsDownloadFlowTest(unittest.TestCase):
    """The Pre-Depends-only fixture archive downloads its full
    runtime closure (pre-dep-app + lib1 + pre-dep-lib) and verifies COMPLETE,
    so the shipped cache is genuinely installable offline."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_base = os.path.join(self.tmp.name, "cache")
        self.fixture_base = os.path.join(self.tmp.name, "fixture")
        make_test_iso.write_pre_depends_archive(self.cache_base)
        make_test_iso.write_pre_depends_archive(self.fixture_base)
        self.server = make_test_iso.serve_pool(self.fixture_base, port=0)
        self.port = self.server.server_address[1]
        _write_defaults(
            self.tmp.name,
            f'[paths]\nbase_dir = "{self.cache_base}"\n[fetch]\nsource = false\n\n'
            "[[archive.source]]\n"
            f'mirror = "http://127.0.0.1:{self.port}"\n'
            'release = "trixie"\ncomponents = ["main"]\n',
        )
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.server.shutdown()
        self.server.thread.join()
        self.tmp.cleanup()

    def test_pre_depends_only_target_downloaded_and_graph_complete(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
            self.assertEqual(handle_add(["pre-dep-app"]), EXIT_SUCCESS)
        config = load_config()[0]
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_download(config.db_path, config)
        self.assertEqual(rc, EXIT_SUCCESS)
        for name, version in (
            ("pre-dep-app", "1.0-1"),
            ("lib1", "2.0-1"),
            ("pre-dep-lib", "1.0-1"),
        ):
            deb = os.path.join(
                self.cache_base, "binary", "pool", "main", name[0], name,
                f"{name}_{version}_amd64.deb",
            )
            self.assertTrue(os.path.exists(deb), f"missing {name} in the pool")
        conn = sqlite3.connect(config.db_path)
        try:
            rows = conn.execute(
                "SELECT package_name FROM binaries WHERE status = 'current'"
                " ORDER BY package_name"
            ).fetchall()
            pending = conn.execute(
                "SELECT COUNT(*) FROM binaries WHERE status = 'current' AND verified = 0"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual([r[0] for r in rows], ["lib1", "pre-dep-app", "pre-dep-lib"])
        self.assertEqual(pending, 0, "every record must verify before the cache is complete")


if __name__ == "__main__":
    unittest.main()
