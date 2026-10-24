import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
from db import insert_binary  # noqa: E402

from package_mgmt.cli.commands.snapshot import handle_snapshot
from package_mgmt.cli.commands.systems import handle_systems_add
from package_mgmt.lib.service.db_init import initialize_database
from package_mgmt.lib.service.snapshot import mark_delivered
from package_mgmt.lib.util.errors import EXIT_SUCCESS, EXIT_ERROR


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        initialize_database(self.db_path)
        self._add_system("bravo-main")
        self._add_system("lab-box")

    def tearDown(self):
        self.tmp.cleanup()

    def _add_system(self, name):
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_systems_add(self.db_path, name)
        self.assertEqual(rc, EXIT_SUCCESS)

    def _insert_binary(self, package_name, version, status, verified, sync_mask):
        conn = sqlite3.connect(self.db_path)
        insert_binary(
            conn, package_name, version, component="main", status=status, verified=verified
        )
        conn.execute(
            "UPDATE binaries SET sync_mask = ? WHERE package_name = ? AND version = ?",
            (sync_mask, package_name, version),
        )
        conn.commit()
        conn.close()

    def _masks(self):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT package_name, sync_mask FROM binaries ORDER BY package_name"
        ).fetchall()
        conn.close()
        return rows

    def _run(self, system, dry_run=False):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_snapshot(self.db_path, system, dry_run)
        return rc, out.getvalue(), err.getvalue()

    def test_dry_run_marks_nothing(self):
        self._insert_binary("pkg1", "1.0", "current", 1, 0)
        rc, out, _ = self._run("bravo-main", dry_run=True)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "1 pending for 'bravo-main'\n")
        self.assertEqual(self._masks(), [("pkg1", 0)])

    def test_marks_only_current_and_verified(self):
        self._insert_binary("pkg-cur", "1.0", "current", 1, 0)
        self._insert_binary("pkg-unverified", "1.0", "current", 0, 0)
        self._insert_binary("pkg-superseded", "1.0", "superseded", 1, 0)
        rc, out, _ = self._run("bravo-main")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "Marked 1 binaries for 'bravo-main'\n")
        masks = dict(self._masks())
        self.assertEqual(masks["pkg-cur"], 2)
        self.assertEqual(masks["pkg-unverified"], 0)
        self.assertEqual(masks["pkg-superseded"], 0)

    def test_or_accumulation_across_snapshots(self):
        self._insert_binary("pkg1", "1.0", "current", 1, 0)
        self._run("bravo-main")
        self._insert_binary("pkg2", "1.0", "current", 1, 0)
        rc, out, _ = self._run("lab-box")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "Marked 2 binaries for 'lab-box'\n")
        masks = dict(self._masks())
        self.assertEqual(masks["pkg1"], 2 | 4)
        self.assertEqual(masks["pkg2"], 4)

    def test_idempotent_rerun_reports_zero(self):
        self._insert_binary("pkg1", "1.0", "current", 1, 0)
        self._run("bravo-main")
        rc, out, _ = self._run("bravo-main")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "Marked 0 binaries for 'bravo-main'\n")

    def test_unknown_system_errors_without_changes(self):
        self._insert_binary("pkg1", "1.0", "current", 1, 0)
        rc, _, err = self._run("ghost")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(json.loads(err)["error"], "Unknown system 'ghost'")
        self.assertEqual(self._masks(), [("pkg1", 0)])

    def test_empty_cache_marks_zero(self):
        rc, out, _ = self._run("bravo-main")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "Marked 0 binaries for 'bravo-main'\n")

    def test_verified_reset_does_not_clear_mask(self):
        self._insert_binary("pkg1", "1.0", "current", 1, 0)
        self._run("bravo-main")
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE binaries SET verified = 0")
        conn.commit()
        conn.close()
        rc, out, _ = self._run("bravo-main", dry_run=True)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "0 pending for 'bravo-main'\n")
        self.assertEqual(dict(self._masks())["pkg1"], 2)

    def test_missing_system_flag_errors(self):
        rc, _, err = self._run(None)
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(
            json.loads(err)["error"], "snapshot requires --system <name>"
        )

    def test_new_binary_after_snapshot_is_pending(self):
        self._insert_binary("pkg1", "1.0", "current", 1, 0)
        self._run("bravo-main")
        self._insert_binary("pkg2", "1.0", "current", 1, 0)
        rc, out, _ = self._run("bravo-main", dry_run=True)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "1 pending for 'bravo-main'\n")


class TestMarkDeliveredService(unittest.TestCase):
    """Spec 012 delivery-record semantics: `mark_delivered` marks exactly the
    supplied ids — the handoff's actual delivery set — never the verified
    superset (contracts/cli.md; handoff.py handler)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        initialize_database(self.db_path)
        with contextlib.redirect_stdout(io.StringIO()):
            handle_systems_add(self.db_path, "bravo-main")

    def tearDown(self):
        self.tmp.cleanup()

    def _insert(self, package_name, status="current", verified=1):
        conn = sqlite3.connect(self.db_path)
        bid = conn.execute(
            "INSERT INTO binaries (package_name, version, release, component, status, verified)"
            " VALUES (?, '1.0', 'trixie', 'main', ?, ?) RETURNING id",
            (package_name, status, verified),
        ).fetchone()[0]
        conn.commit()
        conn.close()
        return bid

    def _mask(self, package_name):
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT sync_mask FROM binaries WHERE package_name = ?", (package_name,)
        ).fetchone()
        conn.close()
        return row[0]

    def test_marks_only_supplied_ids(self):
        a = self._insert("pkg-a")
        self._insert("pkg-b")
        conn = sqlite3.connect(self.db_path)
        try:
            newly = mark_delivered(conn, "bravo-main", [a])
        finally:
            conn.close()
        self.assertEqual(newly, 1)
        self.assertEqual(self._mask("pkg-a"), 2)
        self.assertEqual(self._mask("pkg-b"), 0)

    def test_empty_delivery_set_marks_nothing(self):
        self._insert("pkg-a")
        conn = sqlite3.connect(self.db_path)
        try:
            newly = mark_delivered(conn, "bravo-main", [])
        finally:
            conn.close()
        self.assertEqual(newly, 0)
        self.assertEqual(self._mask("pkg-a"), 0)

    def test_ignores_non_current_or_unverified_ids(self):
        self._insert("pkg-a", status="current", verified=1)
        self._insert("pkg-superseded", status="superseded", verified=1)
        self._insert("pkg-unverified", status="current", verified=0)
        conn = sqlite3.connect(self.db_path)
        try:
            all_ids = [r[0] for r in conn.execute(
                "SELECT id FROM binaries"
            ).fetchall()]
            newly = mark_delivered(conn, "bravo-main", all_ids)
        finally:
            conn.close()
        self.assertEqual(newly, 1)
        self.assertEqual(self._mask("pkg-a"), 2)
        self.assertEqual(self._mask("pkg-superseded"), 0)
        self.assertEqual(self._mask("pkg-unverified"), 0)

    def test_idempotent_rerun_reports_zero(self):
        a = self._insert("pkg-a")
        conn = sqlite3.connect(self.db_path)
        try:
            first = mark_delivered(conn, "bravo-main", [a])
            second = mark_delivered(conn, "bravo-main", [a])
        finally:
            conn.close()
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(self._mask("pkg-a"), 2)

    def test_unknown_system_raises_without_changes(self):
        a = self._insert("pkg-a")
        conn = sqlite3.connect(self.db_path)
        try:
            with self.assertRaises(ValueError):
                mark_delivered(conn, "ghost", [a])
        finally:
            conn.close()
        self.assertEqual(self._mask("pkg-a"), 0)


if __name__ == "__main__":
    unittest.main()
