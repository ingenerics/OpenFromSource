import os
import sqlite3
import tempfile
import unittest

from package_mgmt.lib.model.cache import BinaryCache, SourceCache
from package_mgmt.lib.model.config import ArchiveSource
from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL
from package_mgmt.lib.model.package import BinaryPackage
from package_mgmt.lib.model.repository import RepositoryIndex
from package_mgmt.lib.service.initializer import initialize_caches, populate_provides_table

_DEFAULT_SOURCES = (
    ArchiveSource(mirror="http://deb.debian.org/debian", release="trixie", components=("main",)),
)


class TestInitializeCaches(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bin_path = os.path.join(self.tmp.name, "binary")
        self.src_path = os.path.join(self.tmp.name, "source")

    def tearDown(self):
        self.tmp.cleanup()

    def test_creates_both_cache_directories(self):
        initialize_caches(self.bin_path, self.src_path, _DEFAULT_SOURCES)
        self.assertTrue(os.path.isdir(os.path.join(self.bin_path, "pool", "main")))
        self.assertTrue(os.path.isdir(os.path.join(self.bin_path, "dists", "trixie", "main", "binary-amd64")))
        self.assertTrue(os.path.isdir(os.path.join(self.src_path, "pool", "main")))
        self.assertTrue(os.path.isdir(os.path.join(self.src_path, "dists", "trixie", "main", "source")))

    def test_sets_755_permissions(self):
        initialize_caches(self.bin_path, self.src_path, _DEFAULT_SOURCES)
        pool_mode = os.stat(os.path.join(self.bin_path, "pool")).st_mode & 0o777
        self.assertEqual(pool_mode, 0o755)

    def test_idempotent_reinit(self):
        binary1, source1 = initialize_caches(self.bin_path, self.src_path, _DEFAULT_SOURCES)
        self.assertFalse(binary1.exists)
        self.assertFalse(source1.exists)

        binary2, source2 = initialize_caches(self.bin_path, self.src_path, _DEFAULT_SOURCES)
        self.assertTrue(binary2.exists)
        self.assertTrue(source2.exists)

    def test_returns_cache_objects(self):
        binary, source = initialize_caches(self.bin_path, self.src_path, _DEFAULT_SOURCES)
        self.assertIsInstance(binary, BinaryCache)
        self.assertIsInstance(source, SourceCache)
        self.assertEqual(binary.path, self.bin_path)
        self.assertEqual(source.path, self.src_path)


def _binary(name, version="1.0", release="trixie", component="main", provides=None):
    return BinaryPackage(
        package=name, version=version, architecture="amd64",
        release=release, component=component,
        depends_raw=None, provides_raw=provides,
        source_field=None, filename=None, sha256=None,
    )


class TestPopulateProvidesTable(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)

    def tearDown(self):
        self.conn.close()

    def test_reports_zero_inserts_when_all_rows_exist(self):
        index = RepositoryIndex(
            binary_packages={
                "gawk": [_binary("gawk", provides="awk, awk-api-2.0 (= 2.0)")],
            },
        )
        first = populate_provides_table(self.conn, index)
        self.assertEqual(first, 2)
        second = populate_provides_table(self.conn, index)
        self.assertEqual(second, 0, "re-populating identical rows must count 0 real inserts")

    def test_no_commit_inside_caller_transaction(self):
        index = RepositoryIndex(
            binary_packages={"gawk": [_binary("gawk", provides="awk")]},
        )
        self.conn.execute("BEGIN")
        populate_provides_table(self.conn, index)
        self.assertTrue(
            self.conn.in_transaction,
            "the caller owns the transaction — no nested commit may happen inside",
        )
        self.conn.rollback()
        count = self.conn.execute("SELECT COUNT(*) FROM provides").fetchone()[0]
        self.assertEqual(count, 0, "a rollback must discard the inserts")

    def test_stale_release_provides_swept_on_refresh(self):
        index_v1 = RepositoryIndex(
            binary_packages={
                "gawk": [_binary("gawk", release="trixie", provides="awk")],
                "oldpkg": [_binary("oldpkg", release="bookworm", provides="oldvirtual")],
            },
        )
        configured_v1 = {("trixie", "main"), ("bookworm", "main")}
        populate_provides_table(self.conn, index_v1, configured_sources=configured_v1)
        before = self.conn.execute(
            "SELECT DISTINCT release FROM provides ORDER BY 1"
        ).fetchall()
        self.assertEqual(before, [("bookworm",), ("trixie",)])

        index_v2 = RepositoryIndex(
            binary_packages={"gawk": [_binary("gawk", release="trixie", provides="awk")]},
        )
        configured_v2 = {("trixie", "main")}
        populate_provides_table(self.conn, index_v2, configured_sources=configured_v2)
        after = self.conn.execute(
            "SELECT DISTINCT release FROM provides ORDER BY 1"
        ).fetchall()
        self.assertEqual(
            after, [("trixie",)],
            "provides rows for no-longer-configured releases must be swept",
        )
