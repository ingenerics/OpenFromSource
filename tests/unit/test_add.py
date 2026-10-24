import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest

from package_mgmt.cli.commands.add import handle_add, parse_package_list
from package_mgmt.lib.service.db_init import initialize_database
from package_mgmt.lib.util.errors import EXIT_ERROR, EXIT_SCHEMA_MISMATCH


class TestAddStaleSchemaExitCode(unittest.TestCase):
    """A stale-schema add must exit 101, matching the shared schema gate used
    by the read commands, instead of the generic failure code."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "add_test")
        os.makedirs(self.base, exist_ok=True)
        self._write_defaults(
            f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = false\n\n'
            "[[archive.source]]\n"
            'mirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main"]\n'
        )
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def test_stale_schema_add_exits_101(self):
        db_path = os.path.join(self.base, "package_mgmt.db")
        initialize_database(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
        conn.close()

        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf):
            rc = handle_add(["vim"])
        self.assertEqual(rc, EXIT_SCHEMA_MISMATCH)
        payload = json.loads(err_buf.getvalue())
        self.assertIn("Schema mismatch", payload["error"])


class TestAddEmptyPin(unittest.TestCase):
    """The positional path rejects an empty version pin (name=) exactly like
    the --file path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "add_pin_test")
        os.makedirs(self.base, exist_ok=True)
        self._write_defaults(
            f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = false\n\n'
            "[[archive.source]]\n"
            'mirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main"]\n'
        )
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def test_empty_pin_positional_rejected(self):
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf):
            rc = handle_add(["vim="])
        self.assertEqual(rc, EXIT_ERROR)
        self.assertIn("vim=", err_buf.getvalue())

    def test_empty_pin_file_line_rejected_by_parser(self):
        path = os.path.join(self.tmp.name, "list.txt")
        with open(path, "w") as f:
            f.write("vim=\n")
        result = parse_package_list(path)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("vim=", result.errors[0])


if __name__ == "__main__":
    unittest.main()
