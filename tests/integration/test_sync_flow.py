import contextlib
import gzip
import io
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_iso  # noqa: E402

from package_mgmt.cli.commands.add import handle_add  # noqa: E402
from package_mgmt.cli.commands.init import handle_init  # noqa: E402
from package_mgmt.cli.commands.sync import handle_sync  # noqa: E402
from package_mgmt.cli.commands.systems import handle_systems_add  # noqa: E402
from package_mgmt.lib.service.config_loader import load_config  # noqa: E402
from package_mgmt.lib.util.errors import EXIT_SUCCESS  # noqa: E402


class SyncEmptyDiffFlowTest(unittest.TestCase):
    """A zero-pending sync for a system with a prior non-empty
    diff regenerates the diff as empty — stale content is never retained."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "sync_flow")
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
        make_test_iso.write_pool_files(self.base)

    def _db_path(self):
        return os.path.join(self.base, "package_mgmt.db")

    def _db(self):
        return sqlite3.connect(self._db_path())

    def _simulate_verify_all(self):
        conn = self._db()
        conn.execute("UPDATE binaries SET verified = 1")
        conn.commit()
        conn.close()

    def _sync(self, config, system="bravo-main"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_sync(config.db_path, config, system)
        return rc, out.getvalue(), err.getvalue()

    def _diff_manifest(self):
        with open(os.path.join(self.base, "binary", "diff", "manifest.txt")) as f:
            return f.read()

    def _diff_pool_files(self):
        pool = os.path.join(self.base, "binary", "diff", "pool")
        files = []
        for root, _, names in os.walk(pool):
            for name in names:
                files.append(os.path.relpath(os.path.join(root, name), pool))
        return sorted(files)

    def _read_packages(self, config, component="main"):
        path = os.path.join(
            config.binary_cache, "diff", "dists", "trixie", component,
            "binary-amd64", "Packages.gz",
        )
        with open(path, "rb") as f:
            return gzip.decompress(f.read()).decode("utf-8")

    def test_zero_pending_sync_regenerates_empty_diff(self):
        self._provision()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_add(["vifm", "lib1"], None, None), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            handle_systems_add(self._db_path(), "bravo-main")
        self._simulate_verify_all()

        # First sync ships the pending set: a non-empty diff with new files.
        config = load_config()[0]
        rc, out, err = self._sync(config)
        self.assertEqual(rc, EXIT_SUCCESS, err)
        self.assertIn("New packages:    ", out)
        self.assertTrue(
            any(line.startswith("new ") for line in self._diff_manifest().splitlines()),
            "the first sync must produce new manifest lines",
        )
        self.assertTrue(self._diff_pool_files(), "the first sync must populate the diff pool")

        # Re-run with nothing pending: the diff is regenerated as empty.
        rc, out, err = self._sync(config)
        self.assertEqual(rc, EXIT_SUCCESS, err)
        self.assertIn("New packages:    0 \u2014 nothing new since the last sync for 'bravo-main'", out)
        self.assertIn("Snapshot:        no change", out)

        non_empty = [line for line in self._diff_manifest().splitlines() if line.strip()]
        self.assertTrue(non_empty, "the regenerated manifest keeps the delivered base set")
        self.assertTrue(all(line.startswith("delivered ") for line in non_empty))
        self.assertEqual(
            self._diff_pool_files(), [],
            "no stale pool files may remain after the empty regeneration",
        )
        self.assertTrue(os.path.isfile(os.path.join(self.base, "binary", "diff", "apply.sh")))


if __name__ == "__main__":
    unittest.main()
