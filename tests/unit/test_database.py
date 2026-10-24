import os
import sqlite3
import tempfile
import unittest

from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL
from package_mgmt.lib.service.db_init import initialize_database, validate_schema
from package_mgmt.lib.util.errors import DatabaseError


class TestInitializeDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _get_tables(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        return tables

    def _get_user_version(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version")
        ver = cursor.fetchone()[0]
        conn.close()
        return ver

    def _get_columns(self, table):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table})")
        cols = [col[1] for col in cursor.fetchall()]
        conn.close()
        return cols

    def test_creates_all_tables(self):
        initialize_database(self.db_path)
        tables = self._get_tables()
        self.assertEqual(tables, ["binaries", "binary_to_source", "packages", "provides", "source_build_deps", "sources", "systems", "virtual_resolutions"])

    def test_sets_schema_version_to_one(self):
        initialize_database(self.db_path)
        self.assertEqual(self._get_user_version(), 1)

    def test_reinit_with_matching_version_succeeds(self):
        initialize_database(self.db_path)
        ver = initialize_database(self.db_path)
        self.assertEqual(ver, 1)

    def test_schema_mismatch_raises_database_error(self):
        initialize_database(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
        conn.close()
        with self.assertRaises(DatabaseError):
            initialize_database(self.db_path)

    def test_verified_flag_policy(self):
        """Only binaries and sources carry the record-level verified flag
        (spec 010 schema decision: packages and link tables dropped theirs)."""
        initialize_database(self.db_path)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        for row in cursor.fetchall():
            table = row[0]
            cursor.execute("PRAGMA table_info({})".format(table))
            cols = [col[1] for col in cursor.fetchall()]
            if table in ("binaries", "sources"):
                self.assertIn("verified", cols, f"Table '{table}' missing verified column")
            else:
                self.assertNotIn("verified", cols, f"Table '{table}' must not have verified column")
        conn.close()

    def test_schema_sql_creates_tables_with_correct_structure(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(EXPECTED_SCHEMA_SQL)
        conn.commit()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [r[0] for r in cursor.fetchall()]
        self.assertEqual(len(tables), 8)
        conn.close()

    def test_binaries_has_provenance_columns(self):
        initialize_database(self.db_path)
        cols = self._get_columns("binaries")
        self.assertIn("release", cols)
        self.assertIn("component", cols)
        self.assertIn("status", cols)
        self.assertIn("circular", cols)
        self.assertIn("sync_mask", cols)
        self.assertEqual(cols, [
            "id", "package_name", "version", "architecture", "release",
            "component", "dependencies", "status", "verified", "circular",
            "sync_mask",
        ])

    def test_sources_has_provenance_columns(self):
        initialize_database(self.db_path)
        cols = self._get_columns("sources")
        self.assertIn("release", cols)
        self.assertIn("component", cols)

    def test_systems_table_has_expected_columns(self):
        initialize_database(self.db_path)
        cols = self._get_columns("systems")
        self.assertEqual(cols, ["id", "name", "bit", "note", "retired"])

    def test_binaries_sync_mask_defaults_to_zero(self):
        initialize_database(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO binaries (package_name, version, release, component)"
            " VALUES ('pkg', '1.0', 'trixie', 'main')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT sync_mask FROM binaries WHERE package_name = 'pkg'"
        ).fetchone()
        self.assertEqual(row[0], 0)
        conn.close()

    def test_systems_active_name_unique_index(self):
        initialize_database(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO systems (name, bit) VALUES ('bravo', 2)")
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO systems (name, bit) VALUES ('bravo', 4)")
        # A retired row does not block re-adding the same moniker
        conn.execute("UPDATE systems SET retired = 1 WHERE name = 'bravo'")
        conn.commit()
        conn.execute("INSERT INTO systems (name, bit) VALUES ('bravo', 4)")
        conn.commit()
        rows = conn.execute(
            "SELECT name, retired, bit FROM systems ORDER BY id"
        ).fetchall()
        self.assertEqual(rows, [("bravo", 1, 2), ("bravo", 0, 4)])
        conn.close()

    def test_systems_bit_unique_even_when_retired(self):
        initialize_database(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO systems (name, bit) VALUES ('bravo', 2)")
        conn.execute("INSERT INTO systems (name, bit, retired) VALUES ('old', 4, 1)")
        conn.commit()
        # Bits are never reused: the retired bit must still collide
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO systems (name, bit) VALUES ('charlie', 4)")
        conn.close()

    def _make_stale_database(self):
        """A pre-feature v1 database: current schema minus the feature's objects."""
        conn = sqlite3.connect(self.db_path)
        conn.executescript(EXPECTED_SCHEMA_SQL)
        conn.execute("DROP TABLE systems")
        conn.execute("ALTER TABLE binaries DROP COLUMN sync_mask")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()

    def test_stale_database_raises_rebuild_error(self):
        self._make_stale_database()
        with self.assertRaises(DatabaseError) as ctx:
            initialize_database(self.db_path)
        self.assertIn("rebuild", str(ctx.exception).lower())
        self.assertIn("systems", str(ctx.exception))

    def test_validate_schema_passes_on_current_database(self):
        initialize_database(self.db_path)
        validate_schema(self.db_path)  # must not raise

    def test_validate_schema_raises_on_stale_database(self):
        self._make_stale_database()
        with self.assertRaises(DatabaseError) as ctx:
            validate_schema(self.db_path)
        self.assertIn("rebuild", str(ctx.exception).lower())

    def test_validate_schema_raises_on_missing_database(self):
        with self.assertRaises(DatabaseError) as ctx:
            validate_schema(self.db_path)
        self.assertIn("run 'init' first", str(ctx.exception))

