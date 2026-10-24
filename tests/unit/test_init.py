import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from package_mgmt.cli.commands.init import handle_init
from package_mgmt.lib.util.errors import (
    EXIT_SUCCESS,
    EXIT_ERROR,
    EXIT_PERMISSION,
)


class TestInitExitCodeSplit(unittest.TestCase):
    """init maps a permission failure to 100 and any other failure to 1."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "init_test")
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

    def test_permission_error_exits_100(self):
        with mock.patch(
            "package_mgmt.lib.service.initializer.os.makedirs",
            side_effect=PermissionError(13, "Permission denied"),
        ):
            err_buf = io.StringIO()
            with contextlib.redirect_stderr(err_buf):
                rc = handle_init()
        self.assertEqual(rc, EXIT_PERMISSION)

    def test_non_permission_error_exits_1(self):
        with mock.patch(
            "package_mgmt.lib.service.initializer.os.makedirs",
            side_effect=OSError(28, "No space left on device"),
        ):
            err_buf = io.StringIO()
            with contextlib.redirect_stderr(err_buf):
                rc = handle_init()
        self.assertEqual(rc, EXIT_ERROR)


class TestInitOfflineSummary(unittest.TestCase):
    """An init that caches zero binaries (unreachable mirrors) must state the
    offline mode in the summary instead of a bare 'initialized'."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "init_offline_test")
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

    def test_offline_mode_stated_in_summary(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_init()
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn(
            "initialized (no mirror reachable \u2014 offline mode, 0 binaries cached)",
            buf.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
