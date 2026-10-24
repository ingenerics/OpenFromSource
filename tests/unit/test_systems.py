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
from db import insert_binary  # noqa: E402

from package_mgmt.cli.commands.systems import (
    handle_systems_add,
    handle_systems_list,
    handle_systems_remove,
    handle_systems_rename,
)
from package_mgmt.lib.service.db_init import initialize_database
from package_mgmt.lib.util.errors import EXIT_SUCCESS, EXIT_ERROR, EXIT_SCHEMA_MISMATCH


class TestSystemsRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        initialize_database(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, handler, *args, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handler(self.db_path, *args, **kwargs)
        return rc, out.getvalue(), err.getvalue()

    def _add(self, name, note=None):
        rc, out, err = self._run(handle_systems_add, name, note=note)
        return rc, out, err

    def _list(self):
        return self._run(handle_systems_list)

    def _rows(self):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT name, bit, note, retired FROM systems ORDER BY id"
        ).fetchall()
        conn.close()
        return rows

    def _bit(self, name):
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT bit FROM systems WHERE name = ? AND retired = 0", (name,)
        ).fetchone()
        conn.close()
        return row[0] if row else None

    def _insert_binary(self, package_name, version, status, sync_mask):
        conn = sqlite3.connect(self.db_path)
        insert_binary(
            conn, package_name, version, component="main", status=status, verified=0
        )
        conn.execute(
            "UPDATE binaries SET sync_mask = ? WHERE package_name = ? AND version = ?",
            (sync_mask, package_name, version),
        )
        conn.commit()
        conn.close()

    def test_add_without_note(self):
        rc, out, _ = self._add("bravo-main")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "Added system 'bravo-main'\n")
        self.assertEqual(self._rows(), [("bravo-main", 2, None, 0)])

    def test_add_with_note(self):
        rc, out, _ = self._add("lab-box", note="main box")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(self._rows(), [("lab-box", 2, "main box", 0)])

    def test_duplicate_moniker_rejected(self):
        self._add("bravo-main")
        rc, _, err = self._add("bravo-main")
        self.assertEqual(rc, EXIT_ERROR)
        payload = json.loads(err)
        self.assertEqual(payload["error"], "System 'bravo-main' already exists")
        self.assertEqual(len(self._rows()), 1)

    def test_empty_moniker_rejected_no_row(self):
        rc, _, err = self._add("")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(
            json.loads(err)["error"],
            "System name must be a non-empty word (no spaces, newlines)",
        )
        self.assertEqual(self._rows(), [])

    def test_whitespace_moniker_rejected_no_row(self):
        rc, _, err = self._add("a b")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(
            json.loads(err)["error"],
            "System name must be a non-empty word (no spaces, newlines)",
        )
        self.assertEqual(self._rows(), [])

    def test_newline_moniker_rejected_no_row(self):
        rc, _, err = self._add("a\nb")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(
            json.loads(err)["error"],
            "System name must be a non-empty word (no spaces, newlines)",
        )
        self.assertEqual(self._rows(), [])

    def test_duplicate_moniker_error_closes_connection(self):
        self._add("bravo-main")
        opened, closed = [], []
        real_connect = sqlite3.connect

        class _TrackingConn:
            def __init__(self, real):
                self._real = real

            def execute(self, *args, **kwargs):
                return self._real.execute(*args, **kwargs)

            def commit(self):
                return self._real.commit()

            def close(self):
                closed.append(True)
                return self._real.close()

        def tracking_connect(*args, **kwargs):
            opened.append(True)
            return _TrackingConn(real_connect(*args, **kwargs))

        with mock.patch(
            "package_mgmt.cli.commands.systems.sqlite3.connect",
            side_effect=tracking_connect,
        ):
            rc, _, err = self._add("bravo-main")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(
            json.loads(err)["error"], "System 'bravo-main' already exists"
        )
        self.assertEqual(
            len(opened), len(closed),
            "every connection opened on the duplicate error path must be closed",
        )

    def test_bits_are_monotonic_powers_of_two(self):
        self._add("a")
        self._add("b")
        self._add("c")
        self.assertEqual([self._bit("a"), self._bit("b"), self._bit("c")], [2, 4, 8])

    def test_rename_preserves_bit_and_deliveries(self):
        self._add("bravo-main")
        self._insert_binary("pkg1", "1.0", "current", 2)
        self._insert_binary("pkg2", "1.0", "current", 4)
        self._insert_binary("pkg3", "1.0", "superseded", 2)
        rc, out, _ = self._run(handle_systems_rename, "bravo-main", "bravo-laptop")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "Renamed system 'bravo-main' to 'bravo-laptop'\n")
        self.assertEqual(self._bit("bravo-laptop"), 2)
        rc, out, _ = self._list()
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("bravo-laptop", out)
        self.assertIn("1", out.splitlines()[2])

    def test_rename_to_existing_rejected(self):
        self._add("bravo-main")
        self._add("lab-box")
        rc, _, err = self._run(handle_systems_rename, "bravo-main", "lab-box")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(json.loads(err)["error"], "System 'lab-box' already exists")

    def test_rename_unknown_rejected(self):
        rc, _, err = self._run(handle_systems_rename, "ghost", "new-name")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(json.loads(err)["error"], "Unknown system 'ghost'")

    def test_rename_invalid_moniker_rejected(self):
        self._add("bravo-main")
        for bad in ("", "two words", "line\nbreak"):
            rc, _, err = self._run(handle_systems_rename, "bravo-main", bad)
            self.assertEqual(
                rc, EXIT_ERROR,
                "rename to %r must be rejected" % bad,
            )
            self.assertEqual(
                json.loads(err)["error"],
                "System name must be a non-empty word (no spaces, newlines)",
            )
        self.assertEqual(self._bit("bravo-main"), 2)

    def test_rename_duplicate_error_closes_connection(self):
        self._add("bravo-main")
        self._add("lab-box")
        opened, closed = [], []
        real_connect = sqlite3.connect

        class _TrackingConn:
            def __init__(self, real):
                self._real = real

            def execute(self, *args, **kwargs):
                return self._real.execute(*args, **kwargs)

            def commit(self):
                return self._real.commit()

            def close(self):
                closed.append(True)
                return self._real.close()

        def tracking_connect(*args, **kwargs):
            opened.append(True)
            return _TrackingConn(real_connect(*args, **kwargs))

        with mock.patch(
            "package_mgmt.cli.commands.systems.sqlite3.connect",
            side_effect=tracking_connect,
        ):
            rc, _, err = self._run(handle_systems_rename, "bravo-main", "lab-box")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(
            json.loads(err)["error"], "System 'lab-box' already exists"
        )
        self.assertEqual(
            len(opened), len(closed),
            "every connection opened on the rename error path must be closed",
        )

    def test_remove_soft_deletes_and_keeps_deliveries(self):
        self._add("bravo-main")
        self._insert_binary("pkg1", "1.0", "current", 2)
        rc, out, _ = self._run(handle_systems_remove, "bravo-main")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "Removed system 'bravo-main'\n")
        self.assertEqual(self._rows(), [("bravo-main", 2, None, 1)])
        rc, out, _ = self._list()
        self.assertNotIn("bravo-main", out)
        conn = sqlite3.connect(self.db_path)
        mask = conn.execute(
            "SELECT sync_mask FROM binaries WHERE package_name = 'pkg1'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(mask, 2)

    def test_remove_unknown_rejected(self):
        rc, _, err = self._run(handle_systems_remove, "ghost")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(json.loads(err)["error"], "Unknown system 'ghost'")

    def test_readd_gets_fresh_bit_and_never_synced(self):
        self._add("bravo-main")
        self._add("lab-box")
        self._insert_binary("pkg1", "1.0", "current", 2)
        self._run(handle_systems_remove, "bravo-main")
        rc, _, _ = self._add("bravo-main")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(self._bit("bravo-main"), 8)
        rc, out, _ = self._list()
        self.assertEqual(rc, EXIT_SUCCESS)
        bravo_line = next(
            line for line in out.splitlines() if line.startswith("bravo-main")
        )
        self.assertIn("0", bravo_line)

    def test_retired_bit_never_reused(self):
        self._add("a")
        self._add("b")
        self._add("c")
        self._run(handle_systems_remove, "b")
        self._add("d")
        self.assertEqual(self._bit("d"), 16)

    def test_registry_full_error(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO systems (name, bit) VALUES ('max', ?)", (2 ** 62,)
        )
        conn.commit()
        conn.close()
        rc, _, err = self._add("overflow")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(
            json.loads(err)["error"], "System registry is full (max 62 systems)"
        )

    def test_list_empty(self):
        rc, out, _ = self._list()
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "No systems registered.\n")

    def test_list_delivered_count(self):
        self._add("bravo-main")
        self._add("lab-box")
        self._insert_binary("pkg1", "1.0", "current", 2)
        self._insert_binary("pkg2", "1.0", "current", 4)
        self._insert_binary("pkg3", "1.0", "current", 2)
        rc, out, _ = self._list()
        self.assertEqual(rc, EXIT_SUCCESS)
        lines = out.splitlines()
        self.assertIn("NAME", lines[0])
        self.assertIn("NOTE", lines[0])
        self.assertIn("DELIVERED", lines[0])
        bravo_line = next(line for line in lines if line.startswith("bravo-main"))
        lab_line = next(line for line in lines if line.startswith("lab-box"))
        self.assertIn("2", bravo_line)
        self.assertIn("1", lab_line)

    def test_stale_database_fails_with_rebuild_message(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("DROP TABLE systems")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        rc, _, err = self._add("bravo-main")
        self.assertEqual(rc, EXIT_SCHEMA_MISMATCH)
        payload = json.loads(err)
        self.assertIn("rebuild", payload["error"].lower())

    def test_bit_never_appears_in_output(self):
        self._add("bravo-main")
        rc, out, _ = self._list()
        self.assertEqual(rc, EXIT_SUCCESS)
        for token in ("2", "4", "8"):
            self.assertNotIn(f" {token}", out)


if __name__ == "__main__":
    unittest.main()
