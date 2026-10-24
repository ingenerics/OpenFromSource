import contextlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

from package_mgmt.cli.commands.list import _build_tree, handle_list_deps
from package_mgmt.cli.main import main
from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL
from package_mgmt.lib.util.errors import EXIT_SUCCESS, EXIT_SCHEMA_MISMATCH


class TestBuildTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript("""
            CREATE TABLE packages (name TEXT PRIMARY KEY, requested_version TEXT, status TEXT);
            CREATE TABLE binaries (id INTEGER PRIMARY KEY AUTOINCREMENT, package_name TEXT, version TEXT, architecture TEXT, release TEXT, component TEXT, status TEXT NOT NULL DEFAULT 'current', verified INTEGER DEFAULT 0, dependencies TEXT);
            CREATE TABLE sources (id INTEGER PRIMARY KEY AUTOINCREMENT, package_name TEXT, source_version TEXT, verified INTEGER DEFAULT 0);
            CREATE TABLE binary_to_source (binary_id INTEGER, source_id INTEGER);
        """)
        self._seed_data()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed_data(self):
        c = self.conn
        c.execute("INSERT INTO packages VALUES ('root', NULL, 'pending')")
        c.execute("INSERT INTO packages VALUES ('child1', NULL, 'pending')")
        c.execute("INSERT INTO packages VALUES ('child2', NULL, 'pending')")
        c.execute("INSERT INTO packages VALUES ('shared', NULL, 'pending')")

        bids = {}
        for name in ["root", "child1", "child2", "shared"]:
            deps = {"root": '[{"name":"child1","type":"depends"},{"name":"child2","type":"depends"}]',
                    "child1": '[{"name":"shared","type":"depends"}]',
                    "child2": '[{"name":"shared","type":"depends"}]',
                    "shared": "[]"}
            row = c.execute("INSERT INTO binaries (package_name, version, architecture, dependencies, verified) VALUES (?, '1.0', 'amd64', ?, 0) RETURNING id", (name, deps[name])).fetchone()
            bids[name] = row[0]
            src = c.execute("INSERT INTO sources (package_name, source_version, verified) VALUES (?, '1.0', 0) RETURNING id", (name,)).fetchone()
            c.execute("INSERT INTO binary_to_source VALUES (?, ?)", (bids[name], src[0]))
        c.commit()

    def test_compact_suppresses_shared_dep(self):
        tree = _build_tree(self.conn, "root", full=False)
        child_names = [c["name"] for c in tree["dependencies"]]
        self.assertIn("child1", child_names)
        self.assertIn("child2", child_names)
        expanded = 0
        suppressed = 0

        def walk(node):
            nonlocal expanded, suppressed
            if node["name"] == "shared":
                if node.get("suppressed"):
                    suppressed += 1
                else:
                    expanded += 1
            for dep in node.get("dependencies", []):
                walk(dep)

        walk(tree)
        self.assertEqual(expanded, 1, "shared should expand only once in compact mode")
        self.assertGreaterEqual(suppressed, 1, "second occurrence should be suppressed")

    def test_full_shows_all_copies_of_shared_dep(self):
        tree = _build_tree(self.conn, "root", full=True)
        shared_count = 0

        def count_shared(node):
            nonlocal shared_count
            if node["name"] == "shared":
                shared_count += 1
            for dep in node.get("dependencies", []):
                count_shared(dep)

        count_shared(tree)
        self.assertGreaterEqual(shared_count, 2, "shared should appear under both child1 and child2 in full mode")

    def test_circular_dependency_detection(self):
        self.conn.execute("INSERT INTO packages VALUES ('a', NULL, 'pending')")
        self.conn.execute("INSERT INTO packages VALUES ('b', NULL, 'pending')")
        bid_a = self.conn.execute("INSERT INTO binaries (package_name, version, architecture, dependencies, verified) VALUES ('a', '1.0', 'amd64', '[{\"name\":\"b\",\"type\":\"depends\"}]', 0) RETURNING id").fetchone()[0]
        bid_b = self.conn.execute("INSERT INTO binaries (package_name, version, architecture, dependencies, verified) VALUES ('b', '1.0', 'amd64', '[{\"name\":\"a\",\"type\":\"depends\"}]', 0) RETURNING id").fetchone()[0]
        for bid in (bid_a, bid_b):
            src = self.conn.execute("INSERT INTO sources (package_name, source_version, verified) VALUES ('src', '1.0', 0) RETURNING id").fetchone()
            self.conn.execute("INSERT INTO binary_to_source VALUES (?, ?)", (bid, src[0]))
        self.conn.commit()

        tree = _build_tree(self.conn, "a", full=True)
        b_node = tree["dependencies"][0]
        self.assertEqual(b_node["name"], "b")
        a_under_b = b_node["dependencies"][0]
        self.assertTrue(a_under_b.get("circular"), "circular dep should be flagged in full mode")

    def test_missing_package_graceful(self):
        tree = _build_tree(self.conn, "nonexistent")
        self.assertFalse(tree["binary_exists"])
        self.assertEqual(tree["dependencies"], [])


class TestBuildTreeBuildDeps(unittest.TestCase):
    """list --deps --include-build-deps follows the binary -> source link, so a
    binary whose source has a different name (libxkbcommon-x11-0 ->
    libxkbcommon) still surfaces its build tools; build tools attach only at
    the root node."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript("""
            CREATE TABLE packages (name TEXT PRIMARY KEY, requested_version TEXT, status TEXT);
            CREATE TABLE binaries (id INTEGER PRIMARY KEY AUTOINCREMENT, package_name TEXT, version TEXT, architecture TEXT, release TEXT, component TEXT, status TEXT NOT NULL DEFAULT 'current', verified INTEGER DEFAULT 0, dependencies TEXT);
            CREATE TABLE sources (id INTEGER PRIMARY KEY AUTOINCREMENT, package_name TEXT, source_version TEXT, verified INTEGER DEFAULT 0);
            CREATE TABLE binary_to_source (binary_id INTEGER, source_id INTEGER);
            CREATE TABLE source_build_deps (source_id INTEGER, binary_id INTEGER, build_dep_depth INTEGER);
        """)
        self._seed()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed(self):
        c = self.conn
        # libxkbcommon-x11-0: binary name differs from its source name
        wrapper = c.execute(
            "INSERT INTO binaries (package_name, version, architecture, dependencies, verified) "
            "VALUES ('libxkbcommon-x11-0', '1.7.0-2', 'amd64', "
            "'[{\"name\":\"libxcb-xkb1\",\"type\":\"depends\"}]', 1) RETURNING id"
        ).fetchone()[0]
        src = c.execute(
            "INSERT INTO sources (package_name, source_version, verified) "
            "VALUES ('libxkbcommon', '1.7.0-2', 0) RETURNING id"
        ).fetchone()[0]
        c.execute("INSERT INTO binary_to_source VALUES (?, ?)", (wrapper, src))

        # nested binary libxcb-xkb1: its own source's build tools must NOT
        # surface at the root (root-only attachment)
        xcb = c.execute(
            "INSERT INTO binaries (package_name, version, architecture, dependencies, verified) "
            "VALUES ('libxcb-xkb1', '1.17.0-2+b1', 'amd64', '[]', 1) RETURNING id"
        ).fetchone()[0]
        xcb_src = c.execute(
            "INSERT INTO sources (package_name, source_version, verified) "
            "VALUES ('libxcb-xkb', '1.17.0-2', 0) RETURNING id"
        ).fetchone()[0]
        c.execute("INSERT INTO binary_to_source VALUES (?, ?)", (xcb, xcb_src))

        tools = {}
        for name in ["meson", "pkgconf", "xutils-dev"]:
            tools[name] = c.execute(
                "INSERT INTO binaries (package_name, version, architecture, dependencies, verified) "
                "VALUES (?, '1.0', 'amd64', '[]', 1) RETURNING id", (name,)
            ).fetchone()[0]
        c.execute("INSERT INTO source_build_deps VALUES (?, ?, 1)", (src, tools["meson"]))
        c.execute("INSERT INTO source_build_deps VALUES (?, ?, 1)", (src, tools["pkgconf"]))
        c.execute("INSERT INTO source_build_deps VALUES (?, ?, 1)", (xcb_src, tools["xutils-dev"]))
        c.commit()

    def test_build_deps_follow_binary_to_source(self):
        tree = _build_tree(self.conn, "libxkbcommon-x11-0", include_build_deps=True)
        build_names = [c["name"] for c in tree["dependencies"] if c.get("build_dep")]
        self.assertEqual(sorted(build_names), ["meson", "pkgconf"])

    def test_build_deps_attach_only_at_root(self):
        tree = _build_tree(self.conn, "libxkbcommon-x11-0", include_build_deps=True)
        names = []

        def walk(node):
            if node.get("build_dep"):
                names.append(node["name"])
            for dep in node.get("dependencies", []):
                walk(dep)

        walk(tree)
        self.assertIn("meson", names)
        self.assertNotIn("xutils-dev", names)


class TestListDepsGate(unittest.TestCase):
    """list gates on cache initialization like its siblings: with no
    database it returns 101, prints a clean error, and creates no file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "package_mgmt.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, package=None, status=None, fmt="text"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_list_deps(self.db_path, package, fmt, status=status)
        return rc, out.getvalue(), err.getvalue()

    def test_status_branch_no_database_exits_101_no_file(self):
        rc, out, err = self._run(status="all")
        self.assertEqual(rc, EXIT_SCHEMA_MISMATCH)
        self.assertIn("Database not found", err)
        self.assertFalse(os.path.exists(self.db_path), "no database file may be created")

    def test_deps_branch_no_database_exits_101_no_file(self):
        rc, out, err = self._run(package="vifm")
        self.assertEqual(rc, EXIT_SCHEMA_MISMATCH)
        self.assertIn("Database not found", err)
        self.assertFalse(os.path.exists(self.db_path), "no database file may be created")

    def test_valid_database_unchanged(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(EXPECTED_SCHEMA_SQL)
        conn.execute("INSERT INTO packages VALUES ('vifm', NULL, 'pending')")
        conn.execute(
            "INSERT INTO binaries (package_name, version, architecture, release, component, status)"
            " VALUES ('vifm', '1.0', 'amd64', 'trixie', 'main', 'pending')"
        )
        conn.commit()
        conn.close()
        rc, out, err = self._run(package="vifm")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("vifm (pending)", out)
        self.assertEqual(err, "")


class TestListFlagMutualExclusion(unittest.TestCase):
    """list --deps X and --status all are mutually exclusive: passing both is a
    clean usage error (exit 1); each flag alone dispatches as before."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "list_flags")
        os.makedirs(self.base, exist_ok=True)
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(
                f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = false\n\n'
                "[[archive.source]]\n"
                'mirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main"]\n'
            )
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _run_main(self, argv):
        err_buf = io.StringIO()
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(err_buf):
            with self.assertRaises(SystemExit) as ctx:
                main()
        return ctx.exception.code, err_buf.getvalue()

    def test_deps_and_status_together_usage_error(self):
        code, err = self._run_main(
            ["package_mgmt", "list", "--deps", "vifm", "--status", "all"]
        )
        self.assertEqual(code, 1)
        self.assertIn("Use either --deps or --status, not both", err)

    def test_deps_alone_dispatches(self):
        code, err = self._run_main(["package_mgmt", "list", "--deps", "vifm"])
        self.assertNotEqual(code, 1)
        self.assertNotIn("Use either --deps or --status, not both", err)

    def test_status_alone_dispatches(self):
        code, err = self._run_main(["package_mgmt", "list", "--status", "all"])
        self.assertNotEqual(code, 1)
        self.assertNotIn("Use either --deps or --status, not both", err)
