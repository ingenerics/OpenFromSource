import contextlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_iso  # noqa: E402

from package_mgmt.cli.commands.add import handle_add  # noqa: E402
from package_mgmt.cli.commands.init import handle_init  # noqa: E402
from package_mgmt.cli.commands.verify import handle_verify_graph  # noqa: E402
from package_mgmt.lib.service.config_loader import load_config  # noqa: E402
from package_mgmt.lib.util.errors import EXIT_SUCCESS, EXIT_ERROR  # noqa: E402

NAMES = ["vifm", "lib1", "lib2", "app", "base-pkg", "firmware-x"]
FILE_CONTENT = "vifm\nlib1=2.0-1\nlib2\napp\nbase-pkg\nfirmware-x\n"


class AddFileFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "flow_test")
        self._write_defaults(f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = true\n[resolution]\nmax_source_dep_depth = -1\n\n[[archive.source]]\nmirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n')
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _provision(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        make_test_iso.write_archive(self.base)

    def _write_list(self, content: str, name="pkgs.txt") -> str:
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def _db(self, base=None):
        return sqlite3.connect(os.path.join(base or self.base, "package_mgmt.db"))

    def _table_state(self, conn):
        return {
            "binaries": conn.execute(
                "SELECT package_name, version, architecture, release, component,"
                " dependencies, status, verified, circular FROM binaries ORDER BY 1, 2"
            ).fetchall(),
            "packages": conn.execute(
                "SELECT name, requested_version, status FROM packages ORDER BY 1"
            ).fetchall(),
            "sources": conn.execute(
                "SELECT package_name, source_version, release, component, verified"
                " FROM sources ORDER BY 1, 2"
            ).fetchall(),
            "links": conn.execute(
                """SELECT b.package_name, s.package_name FROM binary_to_source bts
                   JOIN binaries b ON b.id = bts.binary_id
                   JOIN sources s ON s.id = bts.source_id ORDER BY 1, 2"""
            ).fetchall(),
        }

    def test_file_add_matches_positional_add(self):
        """add --file produces the same DB state as positional add."""
        dir_a = os.path.join(self.tmp.name, "cache_a")
        dir_b = os.path.join(self.tmp.name, "cache_b")
        defaults = (
            '[paths]\nbase_dir = "{base}" \n[fetch]\nsource = true\n[resolution]\nmax_source_dep_depth = -1\n'
            "[[archive.source]]\nmirror = \"http://127.0.0.1:1\"\n"
            'release = "trixie"\ncomponents = ["main", "contrib"]\n'
        )
        for d in (dir_a, dir_b):
            os.makedirs(d)
            with open(os.path.join(d, "project.defaults.toml"), "w") as f:
                f.write(defaults.format(base=os.path.join(d, "cache")))

        # Cache A: file-driven add
        os.chdir(dir_a)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        make_test_iso.write_archive(os.path.join(dir_a, "cache"))
        list_path = os.path.join(dir_a, "pkgs.txt")
        with open(list_path, "w") as f:
            f.write(FILE_CONTENT)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_add([], files=[list_path])
        self.assertEqual(rc, EXIT_SUCCESS)
        output = buf.getvalue()
        # Non-TTY output: clean per-update lines, no \r escape garbage.
        self.assertIn("Resolved 6/6", output)
        self.assertNotIn("\r", output)
        self.assertIn("Packages requested:  6", output)
        self.assertIn("Packages resolved:   6", output)
        self.assertIn("Errors:              0", output)
        conn = self._db(os.path.join(dir_a, "cache"))
        try:
            state_a = self._table_state(conn)
            self.assertEqual(
                conn.execute("SELECT count(*) FROM binaries WHERE verified = 1").fetchone()[0],
                0,
                "file-driven add must never set verified flags",
            )
        finally:
            conn.close()

        # Cache B: positional add of the same names
        os.chdir(dir_b)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        make_test_iso.write_archive(os.path.join(dir_b, "cache"))
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add(NAMES)
        self.assertEqual(rc, EXIT_SUCCESS)
        conn = self._db(os.path.join(dir_b, "cache"))
        try:
            state_b = self._table_state(conn)
        finally:
            conn.close()

        os.chdir(self.tmp.name)
        self.assertEqual(
            state_a, state_b,
            "add --file must hydrate the same tables as positional add",
        )

    def test_resolution_errors_reported_in_summary(self):
        """Unresolvable names fail best-effort; exit non-zero."""
        self._provision()
        path = self._write_list("vifm\nghost-pkg\nlib1\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_add([], files=[path])
        self.assertEqual(rc, EXIT_ERROR)
        output = buf.getvalue()
        self.assertIn("Packages requested:  3", output)
        self.assertIn("Packages resolved:   2", output)
        self.assertIn("Errors:              1", output)
        self.assertIn("- ghost-pkg: ", output)
        conn = self._db()
        try:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM binaries WHERE package_name = 'vifm'").fetchone()[0],
                1,
                "valid names must still be resolved",
            )
        finally:
            conn.close()

    def test_mixed_input_rejected(self):
        """--file and positional names are mutually exclusive."""
        self._provision()
        path = self._write_list("vim\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_add(["vim"], files=[path])
        self.assertEqual(rc, EXIT_ERROR)
        conn = self._db()
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0)
        finally:
            conn.close()

    def test_missing_file_errors_and_leaves_db_untouched(self):
        """Unreadable file -> error, exit 1, nothing processed."""
        self._provision()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_add([], files=[os.path.join(self.tmp.name, "nope.txt")])
        self.assertEqual(rc, EXIT_ERROR)
        conn = self._db()
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0)
        finally:
            conn.close()

    def test_malformed_line_prevents_processing_of_valid_lines(self):
        """All-or-nothing — valid lines in a partially bad file are NOT
        processed."""
        self._provision()
        path = self._write_list("vim\nvim= \nlib1\n")
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf), contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add([], files=[path])
        self.assertEqual(rc, EXIT_ERROR)
        self.assertIn("Malformed line 2", err_buf.getvalue())
        conn = self._db()
        try:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0,
                "nothing may be processed when any line is malformed",
            )
        finally:
            conn.close()

    def test_empty_file_errors(self):
        """An empty file yields no names -> error, nothing processed."""
        self._provision()
        path = self._write_list("")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_add([], files=[path])
        self.assertEqual(rc, EXIT_ERROR)
        conn = self._db()
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0)
        finally:
            conn.close()

    def test_rerun_is_idempotent(self):
        """Re-running the same file converges — no duplicate rows."""
        self._provision()
        path = self._write_list(FILE_CONTENT)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_add([], files=[path]), EXIT_SUCCESS)
        conn = self._db()
        try:
            first = {
                "binaries": conn.execute("SELECT count(*) FROM binaries").fetchone()[0],
                "packages": conn.execute("SELECT count(*) FROM packages").fetchone()[0],
                "sources": conn.execute("SELECT count(*) FROM sources").fetchone()[0],
                "links": conn.execute("SELECT count(*) FROM binary_to_source").fetchone()[0],
            }
        finally:
            conn.close()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_add([], files=[path]), EXIT_SUCCESS)
        conn = self._db()
        try:
            second = {
                "binaries": conn.execute("SELECT count(*) FROM binaries").fetchone()[0],
                "packages": conn.execute("SELECT count(*) FROM packages").fetchone()[0],
                "sources": conn.execute("SELECT count(*) FROM sources").fetchone()[0],
                "links": conn.execute("SELECT count(*) FROM binary_to_source").fetchone()[0],
            }
        finally:
            conn.close()
        self.assertEqual(second, first)

    def test_finite_depth_bounds_sources_and_keeps_runtime_closure(self):
        """Add the same package under different source depths — the
        resolved source set matches the depth (0 = none, 1 = requested set +
        direct runtime deps) and the runtime `binaries` closure is identical
        to the unlimited run."""

        def _shape(depth):
            base = os.path.join(self.tmp.name, f"depth_{depth}")
            os.makedirs(base)
            defaults = (
                f'[paths]\nbase_dir = "{base}" \n[fetch]\nsource = true\n'
                f"[resolution]\nmax_source_dep_depth = {depth}\n"
                "[[archive.source]]\nmirror = \"http://127.0.0.1:1\"\n"
                'release = "trixie"\ncomponents = ["main", "contrib"]\n'
            )
            with open(os.path.join(base, "project.defaults.toml"), "w") as f:
                f.write(defaults)
            os.chdir(base)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(handle_init(), EXIT_SUCCESS)
            make_test_iso.write_archive(base)
            with contextlib.redirect_stdout(io.StringIO()):
                rc = handle_add(["vifm"])
            self.assertEqual(rc, EXIT_SUCCESS)
            conn = sqlite3.connect(os.path.join(base, "package_mgmt.db"))
            try:
                binaries = sorted(r[0] for r in conn.execute(
                    "SELECT package_name FROM binaries WHERE status = 'current'"))
                sources = sorted(r[0] for r in conn.execute("SELECT package_name FROM sources"))
            finally:
                conn.close()
            return binaries, sources

        zero = _shape(0)
        one = _shape(1)
        unlimited = _shape(-1)
        os.chdir(self.tmp.name)

        self.assertEqual(zero[0], one[0], "runtime closure must be identical across depths")
        self.assertEqual(one[0], unlimited[0], "runtime closure must be identical across depths")
        self.assertEqual(zero[1], [], "depth 0 fetches no sources")
        self.assertEqual(
            one[1], ["lib1", "lib2", "vifm"],
            "depth 1 fetches sources for the requested set and its direct runtime deps",
        )
        self.assertEqual(
            one[1], unlimited[1],
            "the fixture has no runtime deps beyond level 1, so the in-scope set equals the unlimited set",
        )

    def test_depth_zero_with_source_enabled_warns_once_and_exits_zero(self):
        """At the CLI: `source = true` + `max_source_dep_depth = 0`
        warns exactly once and the command still exits 0 with no sources
        fetched; `source = false` shows no warning."""
        base = os.path.join(self.tmp.name, "warn_case")
        os.makedirs(base)
        defaults = (
            f'[paths]\nbase_dir = "{base}" \n[fetch]\nsource = true\n'
            "[resolution]\nmax_source_dep_depth = 0\n"
            "[[archive.source]]\nmirror = \"http://127.0.0.1:1\"\n"
            'release = "trixie"\ncomponents = ["main", "contrib"]\n'
        )
        with open(os.path.join(base, "project.defaults.toml"), "w") as f:
            f.write(defaults)
        os.chdir(base)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        make_test_iso.write_archive(base)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_add(["vifm"])
        self.assertEqual(rc, EXIT_SUCCESS, "depth-0-with-source must still succeed")
        output = buf.getvalue()
        self.assertEqual(output.count("Warning:"), 1, "warning must fire exactly once")
        self.assertIn("max_source_dep_depth = 0 fetches no sources", output)
        conn = sqlite3.connect(os.path.join(base, "package_mgmt.db"))
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM sources").fetchone()[0], 0)
        finally:
            conn.close()
        os.chdir(self.tmp.name)

        base = os.path.join(self.tmp.name, "no_warn_case")
        os.makedirs(base)
        defaults = (
            f'[paths]\nbase_dir = "{base}" \n[fetch]\nsource = false\n'
            "[resolution]\nmax_source_dep_depth = 0\n"
            "[[archive.source]]\nmirror = \"http://127.0.0.1:1\"\n"
            'release = "trixie"\ncomponents = ["main", "contrib"]\n'
        )
        with open(os.path.join(base, "project.defaults.toml"), "w") as f:
            f.write(defaults)
        os.chdir(base)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        make_test_iso.write_archive(base)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_add(["vifm"])
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertNotIn("Warning:", buf.getvalue(),
                         "source = false disables the functionality; no warning (regular_user)")
        os.chdir(self.tmp.name)

    def test_profile_presets_shape_the_cache(self):
        """Each profile file used as `project.defaults.toml` → add
        the same package → the resolved binary/source set matches the profile
        and the runtime `binaries` closure is identical."""
        profiles = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "profiles")
        expected = {
            "regular_user": {
                "sources": [],
                "binaries": ["lib1", "lib2", "vifm"],
                "links": 0,
            },
            "developer": {
                # depth 1 → sources for the requested set and its direct runtime deps.
                "sources": ["lib1", "lib2", "vifm"],
                "binaries": ["lib1", "lib2", "vifm"],
                "links": 3,
            },
            "graybeard": {
                # -1 (unlimited) → every in-scope binary has its source.
                "sources": ["lib1", "lib2", "vifm"],
                "binaries": ["lib1", "lib2", "vifm"],
                "links": 3,
            },
        }
        closures = {}
        for profile, exp in expected.items():
            base = os.path.join(self.tmp.name, f"profile_{profile}")
            os.makedirs(base)
            shutil.copy(
                os.path.join(profiles, f"{profile}.toml"),
                os.path.join(base, "project.defaults.toml"),
            )
            # The local overlay points the profile at the scratch cache and a
            # dead loopback mirror (profiles are drop-in defaults; overlays
            # still apply on top, including the archive sources). init's
            # index fetch fails fast (connection refused) and the test's own
            # local archive is written before add, so the run is hermetic.
            with open(os.path.join(base, "project.local.toml"), "w") as f:
                f.write(
                    f'[paths]\nbase_dir = "{base}"\n\n'
                    "[[archive.source]]\n"
                    'mirror = "http://127.0.0.1:1"\n'
                    'release = "trixie"\ncomponents = ["main", "contrib"]\n'
                )
            os.chdir(base)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(handle_init(), EXIT_SUCCESS)
            make_test_iso.write_archive(base)
            with contextlib.redirect_stdout(io.StringIO()):
                rc = handle_add(["vifm"])
            self.assertEqual(rc, EXIT_SUCCESS, f"{profile} add must succeed")
            conn = sqlite3.connect(os.path.join(base, "package_mgmt.db"))
            try:
                sources = sorted(r[0] for r in conn.execute("SELECT package_name FROM sources"))
                binaries = sorted(r[0] for r in conn.execute(
                    "SELECT package_name FROM binaries WHERE status = 'current'"))
                links = conn.execute(
                    "SELECT count(*) FROM binary_to_source"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(sources, exp["sources"], f"{profile} source set")
            self.assertEqual(binaries, exp["binaries"], f"{profile} runtime closure")
            self.assertEqual(links, exp["links"], f"{profile} binary_to_source links")
            closures[profile] = binaries
        os.chdir(self.tmp.name)
        self.assertEqual(
            len({tuple(c) for c in closures.values()}), 1,
            "the runtime closure must be identical across all profiles",
        )

    def _write_custom_archive(self, base: str):
        """A two-hop runtime chain (vifm -> lib1 -> lib3) so a finite depth 1
        (developer) resolves fewer sources than the unlimited depth (graybeard)
        while the runtime binary closure stays identical."""
        main_packages = [
            ("vifm", "1.0-2", "lib1", None),
            ("lib1", "2.0-1", "lib3", None),
            ("lib3", "4.0-1", None, None),
        ]
        for component in ("main", "contrib"):
            binary_dir = os.path.join(
                base, "binary", "dists", "trixie", component, "binary-amd64"
            )
            source_dir = os.path.join(base, "source", "dists", "trixie", component, "source")
            os.makedirs(binary_dir, exist_ok=True)
            os.makedirs(source_dir, exist_ok=True)
            pkgs = main_packages if component == "main" else [("firmware-x", "1.0-1", None, None)]
            with open(os.path.join(binary_dir, "Packages.gz"), "wb") as f:
                f.write(make_test_iso._gzip_bytes(make_test_iso._index_text(pkgs, component)))
            with open(os.path.join(source_dir, "Sources.gz"), "wb") as f:
                f.write(make_test_iso._gzip_bytes(make_test_iso._sources_text(pkgs, component)))

    def test_developer_vs_graybeard_verifiable_and_smaller(self):
        """The same package under developer (finite
        depth 1) vs graybeard (-1) on a two-hop chain — developer sources are a
        strict subset of graybeard's, the runtime closure is identical, and
        both caches verify COMPLETE (the developer out-of-scope binary lib3 is
        informational-only, never a defect)."""
        profiles = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "profiles")
        results = {}
        for profile in ("developer", "graybeard"):
            base = os.path.join(self.tmp.name, f"verifiable_{profile}")
            os.makedirs(base)
            shutil.copy(
                os.path.join(profiles, f"{profile}.toml"),
                os.path.join(base, "project.defaults.toml"),
            )
            # Dead loopback mirror: init's index fetch fails fast and the
            # custom archive written below is what add resolves against.
            with open(os.path.join(base, "project.local.toml"), "w") as f:
                f.write(
                    f'[paths]\nbase_dir = "{base}"\n\n'
                    "[[archive.source]]\n"
                    'mirror = "http://127.0.0.1:1"\n'
                    'release = "trixie"\ncomponents = ["main", "contrib"]\n'
                )
            os.chdir(base)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(handle_init(), EXIT_SUCCESS)
            self._write_custom_archive(base)
            with contextlib.redirect_stdout(io.StringIO()):
                rc = handle_add(["vifm"])
            self.assertEqual(rc, EXIT_SUCCESS, f"{profile} add must succeed")
            conn = sqlite3.connect(os.path.join(base, "package_mgmt.db"))
            try:
                binaries = sorted(r[0] for r in conn.execute(
                    "SELECT package_name FROM binaries WHERE status = 'current'"))
                sources = sorted(r[0] for r in conn.execute("SELECT package_name FROM sources"))
            finally:
                conn.close()
            config = load_config(base)[0]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = handle_verify_graph(
                    config.db_path,
                    None,
                    config.max_build_dep_report_depth,
                    config.max_source_dep_depth,
                )
            self.assertEqual(rc, EXIT_SUCCESS, f"{profile} must verify COMPLETE")
            results[profile] = {"binaries": binaries, "sources": sources, "verify": buf.getvalue()}
            os.chdir(self.tmp.name)

        dev, gray = results["developer"], results["graybeard"]
        self.assertEqual(dev["binaries"], ["lib1", "lib3", "vifm"], "developer runtime closure")
        self.assertEqual(gray["binaries"], ["lib1", "lib3", "vifm"], "graybeard runtime closure")
        self.assertEqual(dev["binaries"], gray["binaries"], "runtime closure identical")
        self.assertEqual(dev["sources"], ["lib1", "vifm"], "developer depth-1 sources")
        self.assertEqual(gray["sources"], ["lib1", "lib3", "vifm"], "graybeard full sources")
        self.assertTrue(
            set(dev["sources"]).issubset(set(gray["sources"])),
            "developer source set must be a subset of graybeard's",
        )
        self.assertLess(
            len(dev["sources"]), len(gray["sources"]),
            "developer must resolve strictly fewer sources",
        )
        self.assertIn("Status:                 COMPLETE", dev["verify"])
        self.assertIn("Status:                 COMPLETE", gray["verify"])
        self.assertIn("Beyond source depth:", dev["verify"])
        self.assertIn("lib3 (= 4.0-1)", dev["verify"])
        self.assertNotIn("Beyond source depth:", gray["verify"])


if __name__ == "__main__":
    unittest.main()
