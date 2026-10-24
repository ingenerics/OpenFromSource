import io
import contextlib
import os
import sqlite3
import tempfile
import unittest

from package_mgmt.cli.commands.init import handle_init
from package_mgmt.lib.util.errors import EXIT_SCHEMA_MISMATCH, EXIT_SUCCESS


class TestInitFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "flow_test")
        self._write_defaults(f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = false\n\n[[archive.source]]\nmirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main"]\n')
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def test_full_init_creates_dirs_and_db(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_init()
        self.assertEqual(rc, EXIT_SUCCESS)

        self.assertTrue(os.path.isdir(os.path.join(self.base, "binary", "pool")))
        self.assertTrue(os.path.isdir(os.path.join(self.base, "binary", "dists")))
        self.assertTrue(os.path.isdir(os.path.join(self.base, "source", "pool")))
        self.assertTrue(os.path.isdir(os.path.join(self.base, "source", "dists")))
        self.assertTrue(os.path.isfile(os.path.join(self.base, "package_mgmt.db")))

    def test_full_init_creates_source_dirs(self):
        self._write_defaults(f"""[paths]
base_dir = "{self.base}"
[fetch]
source = false

[[archive.source]]
mirror = "http://127.0.0.1:1"
release = "trixie"
components = ["main", "contrib"]

[[archive.source]]
mirror = "http://127.0.0.1:1"
release = "trixie-backports"
components = ["main"]
""")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_init()
        self.assertEqual(rc, EXIT_SUCCESS)

        self.assertTrue(os.path.isdir(os.path.join(self.base, "binary", "dists", "trixie", "main", "binary-amd64")))
        self.assertTrue(os.path.isdir(os.path.join(self.base, "binary", "dists", "trixie", "contrib", "binary-amd64")))
        self.assertTrue(os.path.isdir(os.path.join(self.base, "binary", "dists", "trixie-backports", "main", "binary-amd64")))
        self.assertTrue(os.path.isdir(os.path.join(self.base, "source", "dists", "trixie", "main", "source")))
        self.assertTrue(os.path.isdir(os.path.join(self.base, "binary", "pool", "main")))
        self.assertTrue(os.path.isdir(os.path.join(self.base, "binary", "pool", "contrib")))

    def test_reinit_preserves_schema_and_caches(self):
        handle_init()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_init()
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertTrue(os.path.isdir(os.path.join(self.base, "binary", "pool")))
        conn = sqlite3.connect(os.path.join(self.base, "package_mgmt.db"))
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version")
        self.assertEqual(cursor.fetchone()[0], 1)
        conn.close()

    def test_schema_mismatch_exits_with_101(self):
        handle_init()
        db_path = os.path.join(self.base, "package_mgmt.db")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
        conn.close()

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = handle_init()
        self.assertEqual(rc, EXIT_SCHEMA_MISMATCH)
