import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_iso  # noqa: E402

from package_mgmt.cli.commands.add import handle_add  # noqa: E402
from package_mgmt.cli.commands.ensure_base import handle_ensure_base  # noqa: E402
from package_mgmt.cli.commands.init import handle_init  # noqa: E402
from package_mgmt.cli.commands.list import handle_list_deps  # noqa: E402
from package_mgmt.cli.commands.show import handle_show  # noqa: E402
from package_mgmt.cli.commands.verify import handle_verify_graph  # noqa: E402
from package_mgmt.lib.model.config import ArchiveSource, ProjectConfig  # noqa: E402
from package_mgmt.lib.service.db_init import initialize_database  # noqa: E402
from package_mgmt.lib.service.ensure_base import ensure_base  # noqa: E402
from package_mgmt.lib.util.errors import (
    CacheNotEmptyError,
    EXIT_SUCCESS,
    EXIT_ERROR,
)  # noqa: E402


def _snapshot_db(db_path: str) -> dict:
    """Content snapshot normalized over auto-increment ids."""
    conn = sqlite3.connect(db_path)
    try:
        binaries = conn.execute(
            "SELECT package_name, version, architecture, release, component,"
            " dependencies, status, verified, circular FROM binaries ORDER BY 1, 2"
        ).fetchall()
        packages = conn.execute(
            "SELECT name, requested_version, status FROM packages ORDER BY 1"
        ).fetchall()
        provides = conn.execute("SELECT * FROM provides ORDER BY 1, 2").fetchall()
        sources = conn.execute(
            "SELECT package_name, source_version, release, component, verified,"
            " build_depends FROM sources ORDER BY 1, 2"
        ).fetchall()
        links = conn.execute(
            """SELECT b.package_name, s.package_name
               FROM binary_to_source bts
               JOIN binaries b ON b.id = bts.binary_id
               JOIN sources s ON s.id = bts.source_id
               ORDER BY 1, 2"""
        ).fetchall()
        build_deps = conn.execute(
            """SELECT s.package_name, b.package_name, sbd.build_dep_depth
               FROM source_build_deps sbd
               JOIN sources s ON s.id = sbd.source_id
               JOIN binaries b ON b.id = sbd.binary_id
               ORDER BY 1, 2"""
        ).fetchall()
        return {
            "binaries": binaries,
            "packages": packages,
            "provides": provides,
            "sources": sources,
            "binary_to_source": links,
            "source_build_deps": build_deps,
        }
    finally:
        conn.close()


class EnsureBaseFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "flow_test")
        self._write_defaults(f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = false\n\n[[archive.source]]\nmirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n')
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _place_image(self, name="fx.iso", **opts):
        os.makedirs(os.path.join(self.base, "iso"), exist_ok=True)
        path = os.path.join(self.base, "iso", name)
        make_test_iso.build_iso(path, **opts)
        return path

    def _provision_archive(self, count=5):
        make_test_iso.write_archive(self.base, count=count)

    def test_init_then_ensure_base_end_to_end(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_init()
        self.assertEqual(rc, EXIT_SUCCESS)
        self._provision_archive()
        self._place_image()

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_SUCCESS)
        output = buf.getvalue()
        self.assertIn("ISO image:        fx.iso (trixie: contrib, main)", output)
        self.assertIn("Binary packages:  6", output)
        self.assertIn("Provides entries: 1", output)
        self.assertIn("Index/image mismatches: 0", output)
        self.assertIn("Warnings:", output)

        conn = sqlite3.connect(os.path.join(self.base, "package_mgmt.db"))
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 6)
            self.assertEqual(
                conn.execute("SELECT count(*) FROM binaries WHERE verified = 1").fetchone()[0],
                0,
            )
            self.assertEqual(conn.execute("SELECT count(*) FROM packages").fetchone()[0], 6)
        finally:
            conn.close()

    def test_corrupt_image_fails_without_touching_db(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_init()
        self.assertEqual(rc, EXIT_SUCCESS)
        self._provision_archive()
        self._place_image("bad.iso", corrupt_index=True)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_ERROR)

        conn = sqlite3.connect(os.path.join(self.base, "package_mgmt.db"))
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0)
        finally:
            conn.close()

    def test_omit_fixture_reports_mismatch_and_keeps_flags(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_init()
        self.assertEqual(rc, EXIT_SUCCESS)
        self._provision_archive()
        self._place_image("omit.iso", omit=["pool/main/v/vifm/vifm_1.0-2_amd64.deb"])

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_SUCCESS)
        output = buf.getvalue()
        self.assertIn("Index/image mismatches: 1", output)
        self.assertIn("pool/main/v/vifm/vifm_1.0-2_amd64.deb (in index, not found in image)", output)

        conn = sqlite3.connect(os.path.join(self.base, "package_mgmt.db"))
        try:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM binaries WHERE verified = 1").fetchone()[0],
                0,
            )
            self.assertEqual(conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 6)
        finally:
            conn.close()

    def test_results_equal_to_add_on_same_names(self):
        """ensure-base resolution == add on the same names."""
        names = ["vifm", "lib1", "lib2", "app", "base-pkg", "firmware-x"]
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

        def _counts(base_dir):
            conn = sqlite3.connect(os.path.join(base_dir, "cache", "package_mgmt.db"))
            try:
                binaries = conn.execute(
                    "SELECT package_name, version, release, component, status"
                    " FROM binaries WHERE status = 'current' ORDER BY 1, 2"
                ).fetchall()
                sources = conn.execute("SELECT count(*) FROM sources").fetchone()[0]
                links = conn.execute(
                    "SELECT count(DISTINCT binary_id || '-' || source_id) FROM binary_to_source"
                ).fetchone()[0]
                return binaries, sources, links
            finally:
                conn.close()

        # Cache A: ensure-base
        os.chdir(dir_a)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        make_test_iso.write_archive(os.path.join(dir_a, "cache"))
        os.makedirs(os.path.join(dir_a, "cache", "iso"), exist_ok=True)
        make_test_iso.build_iso(os.path.join(dir_a, "cache", "iso", "fx.iso"))
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_SUCCESS)
        counts_a = _counts(dir_a)

        # Cache B: plain adds of the same names
        os.chdir(dir_b)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        make_test_iso.write_archive(os.path.join(dir_b, "cache"))
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add(names)
        self.assertEqual(rc, EXIT_SUCCESS)
        counts_b = _counts(dir_b)

        os.chdir(self.tmp.name)
        self.assertEqual(counts_a, counts_b,
                         "ensure-base must produce the same current-tree, source, and "
                         "link records as add on the same names")

    def test_interrupted_run_leaves_partial_state_and_refuses_rerun(self):
        """An interrupt mid-Phase B leaves a partial cache; ensure-base is
        fresh-cache only, so the re-run is refused with a clear error."""
        base = os.path.join(self.tmp.name, "interrupt_test")
        os.makedirs(os.path.join(base, "iso"))
        config = ProjectConfig(
            base_dir=base,
            binary_cache=f"{base}/binary",
            source_cache=f"{base}/source",
            iso_dir=f"{base}/iso",
            db_path=f"{base}/package_mgmt.db",
            log_dir=f"{base}/logs",
            sources=(
                ArchiveSource(
                    mirror="http://127.0.0.1:1",
                    release="trixie",
                    components=("main", "contrib"),
                ),
            ),
        )
        initialize_database(config.db_path)
        make_test_iso.write_archive(base, count=10)
        make_test_iso.build_iso(os.path.join(base, "iso", "fx.iso"), count=10)

        def _interrupt(done, total):
            if done == 4:
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            ensure_base(config, progress=_interrupt)

        conn = sqlite3.connect(config.db_path)
        try:
            self.assertGreater(
                conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0,
                "Phase A committed binaries before the interrupt",
            )
        finally:
            conn.close()

        with self.assertRaises(CacheNotEmptyError) as ctx:
            ensure_base(config)
        self.assertIn("fresh cache", str(ctx.exception))

    def test_image_removed_between_runs_errors_and_keeps_db(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_init()
        self.assertEqual(rc, EXIT_SUCCESS)
        self._provision_archive()
        image = self._place_image()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_ensure_base(), EXIT_SUCCESS)

        os.remove(image)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_ERROR)
        conn = sqlite3.connect(os.path.join(self.base, "package_mgmt.db"))
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 6)
        finally:
            conn.close()

    def test_no_image_returns_error(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_init()
        self.assertEqual(rc, EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_ERROR)

    def test_failed_ensure_base_on_fresh_cache_creates_no_db(self):
        """A failed ensure-base on a cache with no database must not create
        one: the database is created only after the failure-prone discovery
        (image lookup, index loading) has succeeded."""
        db_path = os.path.join(self.base, "package_mgmt.db")
        self.assertFalse(os.path.exists(db_path))
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf), contextlib.redirect_stdout(io.StringIO()):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_ERROR)
        self.assertFalse(
            os.path.exists(db_path),
            "no database file may be created when the run fails",
        )

    def test_rerun_refused_via_cli(self):
        with contextlib.redirect_stdout(io.StringIO()):
            handle_init()
        self._provision_archive()
        self._place_image()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_ensure_base(), EXIT_SUCCESS)
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf), contextlib.redirect_stdout(io.StringIO()):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_ERROR)
        self.assertIn("fresh cache", err_buf.getvalue())
        conn = sqlite3.connect(os.path.join(self.base, "package_mgmt.db"))
        try:
            rows = conn.execute(
                "SELECT count(*) FROM binaries UNION ALL SELECT count(*) FROM packages"
                " UNION ALL SELECT count(*) FROM provides"
            ).fetchall()
            self.assertEqual(rows, [(6,), (6,), (1,)])
        finally:
            conn.close()

    def test_phase_a_failure_rolls_back_registrations(self):
        """A forced failure mid-Phase-A (during registration) leaves no
        committed binaries, packages, or provides — Phase A is a single
        transaction."""
        with contextlib.redirect_stdout(io.StringIO()):
            handle_init()
        self._provision_archive()
        self._place_image()

        import package_mgmt.lib.service.ensure_base as ensure_base_module

        real_upsert_binary = ensure_base_module.upsert_binary
        calls = {"n": 0}

        def flaky_upsert(conn, package_name, version, architecture, release, component, dependencies_json):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated Phase A failure")
            return real_upsert_binary(
                conn, package_name, version, architecture, release, component, dependencies_json
            )

        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            with mock.patch.object(ensure_base_module, "upsert_binary", flaky_upsert):
                rc = handle_ensure_base()
        self.assertNotEqual(rc, EXIT_SUCCESS)
        conn = sqlite3.connect(os.path.join(self.base, "package_mgmt.db"))
        try:
            total = conn.execute(
                "SELECT (SELECT COUNT(*) FROM binaries)"
                " + (SELECT COUNT(*) FROM packages)"
                " + (SELECT COUNT(*) FROM provides)"
            ).fetchone()[0]
            self.assertEqual(
                total, 0,
                "a mid-Phase-A failure must roll back all registrations",
            )
        finally:
            conn.close()

    def test_list_show_image_records_match_add_records(self):
        """list/show on an image record produce the same field/format
        output as on an add-fetched record."""
        names = ["vifm", "lib1", "lib2", "app", "base-pkg", "firmware-x"]
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

        # Cache A: records registered from the installer image
        os.chdir(dir_a)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        make_test_iso.write_archive(os.path.join(dir_a, "cache"))
        os.makedirs(os.path.join(dir_a, "cache", "iso"), exist_ok=True)
        make_test_iso.build_iso(os.path.join(dir_a, "cache", "iso", "fx.iso"))
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_SUCCESS)
        db_a = os.path.join(dir_a, "cache", "package_mgmt.db")

        # Cache B: the same names added from the archive (add-fetched records)
        os.chdir(dir_b)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        make_test_iso.write_archive(os.path.join(dir_b, "cache"))
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add(names)
        self.assertEqual(rc, EXIT_SUCCESS)
        db_b = os.path.join(dir_b, "cache", "package_mgmt.db")

        os.chdir(self.tmp.name)

        def _list_deps(db_path):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = handle_list_deps(db_path, "vifm", fmt="text")
            self.assertEqual(rc, EXIT_SUCCESS)
            return buf.getvalue()

        # list --deps: identical dependency-tree text (007 contract format)
        self.assertEqual(_list_deps(db_a), _list_deps(db_b))

        def _list_status(db_path):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = handle_list_deps(db_path, None, fmt="json", status="current")
            self.assertEqual(rc, EXIT_SUCCESS)
            return json.loads(buf.getvalue())

        # list --status current: identical listings (same fields and values)
        self.assertEqual(_list_status(db_a), _list_status(db_b))

        def _show_json(db_path):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = handle_show(db_path, "vifm", fmt="json")
            self.assertEqual(rc, EXIT_SUCCESS)
            return json.loads(buf.getvalue())

        def _strip_ids(data):
            # Row ids are DB-local, not record content; strip them for equality.
            for rows in data.values():
                for row in rows:
                    for key in ("id", "binary_id", "source_id"):
                        row.pop(key, None)
            return data

        # show: identical table fields/values in json format
        self.assertEqual(_strip_ids(_show_json(db_a)), _strip_ids(_show_json(db_b)))

        def _show_text(db_path):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = handle_show(
                    db_path, "vifm", tables=["packages", "provides"], fmt="text"
                )
            self.assertEqual(rc, EXIT_SUCCESS)
            return buf.getvalue()

        # show text on id-free tables: identical layout and values
        self.assertEqual(_show_text(db_a), _show_text(db_b))

    def test_verify_graph_and_add_treat_image_records_like_add_records(self):
        """verify --graph counts/checks image records with the same rules
        (current-tree only); add whose dependency resolves to an image record
        satisfies it without duplication (quickstart §5)."""
        # Sources/links are created only with fetch.source = true (same as add).
        self._write_defaults(
            f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = true\n[resolution]\nmax_source_dep_depth = -1\n\n'
            "[[archive.source]]\nmirror = \"http://127.0.0.1:1\"\n"
            'release = "trixie"\ncomponents = ["main", "contrib"]\n'
        )
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_init()
        self.assertEqual(rc, EXIT_SUCCESS)
        self._provision_archive()
        self._place_image()
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_SUCCESS)
        db_path = os.path.join(self.base, "package_mgmt.db")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_verify_graph(db_path)
        self.assertEqual(rc, EXIT_SUCCESS)
        output = buf.getvalue()
        self.assertIn("Total binaries:         6", output)
        self.assertIn("Unresolved deps:        0", output)
        self.assertIn("Unlinked binaries:      0", output)
        self.assertIn("Runtime Status:         COMPLETE", output)
        self.assertIn("Status:                 COMPLETE", output)

        conn = sqlite3.connect(db_path)
        before_links = conn.execute(
            "SELECT DISTINCT binary_id, source_id FROM binary_to_source"
        ).fetchall()
        conn.close()
        before = _snapshot_db(db_path)

        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add(["app"])
        self.assertEqual(rc, EXIT_SUCCESS)
        after = _snapshot_db(db_path)
        self.assertEqual(
            after["binaries"], before["binaries"],
            "the dependency must be satisfied by the image record without "
            "creating new binary rows",
        )
        self.assertEqual(after["packages"], before["packages"])
        self.assertEqual(after["sources"], before["sources"])
        self.assertEqual(after["source_build_deps"], before["source_build_deps"])
        self.assertEqual(after["provides"], before["provides"])
        conn = sqlite3.connect(db_path)
        after_links = conn.execute(
            "SELECT DISTINCT binary_id, source_id FROM binary_to_source"
        ).fetchall()
        conn.close()
        self.assertEqual(
            after_links, before_links,
            "no new binary<->source links may be created by the add run",
        )

    def test_finite_depth_bounds_base_source_set(self):
        """ensure-base enforces the same depth via the shared
        `resolve_batch` path — a finite depth caps the base set's sources
        while the runtime `binaries` closure stays complete, and the depth-0
        warning is surfaced in the summary when source fetching is enabled."""

        def _shape(depth):
            base = os.path.join(self.tmp.name, f"base_depth_{depth}")
            os.makedirs(os.path.join(base, "iso"), exist_ok=True)
            config = ProjectConfig(
                base_dir=base,
                binary_cache=f"{base}/binary",
                source_cache=f"{base}/source",
                iso_dir=f"{base}/iso",
                db_path=f"{base}/package_mgmt.db",
                log_dir=f"{base}/logs",
                fetch_source=True,
                max_source_dep_depth=depth,
                sources=(
                    ArchiveSource(
                        mirror="http://127.0.0.1:1",
                        release="trixie",
                        components=("main", "contrib"),
                    ),
                ),
            )
            initialize_database(config.db_path)
            make_test_iso.write_archive(base)
            make_test_iso.build_iso(os.path.join(base, "iso", "fx.iso"))
            stats = ensure_base(config)
            self.assertEqual(stats.errors, [])
            conn = sqlite3.connect(config.db_path)
            try:
                binaries = sorted(r[0] for r in conn.execute(
                    "SELECT package_name FROM binaries WHERE status = 'current'"))
                sources = sorted(r[0] for r in conn.execute("SELECT package_name FROM sources"))
            finally:
                conn.close()
            return binaries, sources, stats

        zero = _shape(0)
        one = _shape(1)
        unlimited = _shape(-1)

        self.assertEqual(zero[0], unlimited[0],
                         "the base runtime closure must be complete at any depth")
        self.assertEqual(one[0], unlimited[0])
        self.assertEqual(zero[1], [], "depth 0 fetches no sources")
        self.assertEqual(
            one[1], unlimited[1],
            "every base package is a level-0 request, so the in-scope set equals the "
            "unlimited set for this fixture (the depth still bounds it: 0 < 1 == -1)",
        )
        self.assertTrue(
            any("max_source_dep_depth = 0 fetches no sources" in w for w in zero[2].warnings),
            "ensure-base must surface the depth-0 warning",
        )


if __name__ == "__main__":
    unittest.main()
