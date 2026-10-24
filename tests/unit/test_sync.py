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
from db import config, insert_binary  # noqa: E402

from package_mgmt.cli.commands.sync import handle_sync  # noqa: E402
from package_mgmt.lib.service.db_init import initialize_database  # noqa: E402
from package_mgmt.lib.service.snapshot import marked_total  # noqa: E402
from package_mgmt.lib.util.errors import EXIT_SUCCESS  # noqa: E402
from package_mgmt.lib.util.paths import (  # noqa: E402
    diff_manifest_path,
    diff_pool_path,
    diff_script_path,
)


def _pool_files(diff_pool: str) -> list[str]:
    files = []
    for root, _, names in os.walk(diff_pool):
        for name in names:
            files.append(os.path.relpath(os.path.join(root, name), diff_pool))
    return sorted(files)


@unittest.skipUnless(shutil.which("gpg") and shutil.which("gpgv"), "gpg/gpgv required")
class TestSyncZeroPendingRegeneratesEmptyDiff(unittest.TestCase):
    """A sync with zero pending packages regenerates the diff as empty —
    a previous non-empty diff is never left stale."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        make_test_iso.write_pool_files(self.base)
        self.config = config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.conn.execute("INSERT INTO systems (name, bit) VALUES ('prod-box', 2)")
        # vifm is already delivered; app is pending (the delta to ship).
        self._insert_delivered("vifm", "1.0-2")
        self._insert_pending("app", "1.5-1")
        self.conn.commit()
        self.cache = self.config.binary_cache

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _insert_delivered(self, name, version):
        binary_id = insert_binary(self.conn, name, version)
        self.conn.execute("UPDATE binaries SET sync_mask = sync_mask | 2 WHERE id = ?", (binary_id,))

    def _insert_pending(self, name, version):
        insert_binary(self.conn, name, version)

    def _sync(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_sync(self.config.db_path, self.config, "prod-box")
        return rc, out.getvalue(), err.getvalue()

    def test_zero_pending_rerun_regenerates_empty_diff(self):
        # First sync ships the single pending binary: a non-empty diff.
        rc, out, err = self._sync()
        self.assertEqual(rc, EXIT_SUCCESS, err)
        self.assertIn("New packages:    1", out)
        with open(diff_manifest_path(self.cache)) as f:
            first_manifest = f.read()
        self.assertTrue(any(line.startswith("new ") for line in first_manifest.splitlines()))
        self.assertEqual(
            _pool_files(diff_pool_path(self.cache)),
            ["main/a/app/app_1.5-1_amd64.deb"],
        )

        # Re-run with nothing pending: the diff is regenerated as empty —
        # stale new files are removed and the manifest loses its new lines.
        rc, out, err = self._sync()
        self.assertEqual(rc, EXIT_SUCCESS, err)
        self.assertEqual(err, "")
        self.assertIn(f"Sync ready:      {self.cache}/diff", out)
        self.assertIn("New packages:    0 \u2014 nothing new since the last sync for 'prod-box'", out)
        self.assertIn("Snapshot:        no change", out)

        with open(diff_manifest_path(self.cache)) as f:
            rerun_manifest = f.read()
        non_empty = [line for line in rerun_manifest.splitlines() if line.strip()]
        self.assertTrue(non_empty, "the regenerated manifest must keep the delivered base set")
        self.assertNotIn(
            "new ", [line for line in non_empty],
            "no new lines may remain after an empty regeneration",
        )
        self.assertTrue(all(line.startswith("delivered ") for line in non_empty))
        self.assertEqual(
            _pool_files(diff_pool_path(self.cache)),
            [],
            "stale pool files from the previous run must be removed",
        )
        self.assertTrue(os.path.isfile(diff_script_path(self.cache)))

        # Snapshot unchanged: no new marking happened on the empty re-run.
        self.assertEqual(marked_total(self.conn, "prod-box"), 2)


if __name__ == "__main__":
    unittest.main()
