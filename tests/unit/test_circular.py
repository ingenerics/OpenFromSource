import json
import sqlite3
import tempfile
import types
import unittest

from package_mgmt.lib.model.config import ArchiveSource
from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL
from package_mgmt.lib.model.package import BinaryPackage
from package_mgmt.lib.model.repository import RepositoryIndex
from package_mgmt.lib.service.resolver import PackageResolver
from package_mgmt.cli.commands.verify import _find_circular


def _binary(name, depends=None):
    return BinaryPackage(
        package=name, version="1.0", architecture="amd64",
        release="trixie", component="main",
        depends_raw=depends, provides_raw=None,
        source_field=name, filename=None, sha256=None,
    )


def _make_resolver(conn, packages: dict[str, BinaryPackage]):
    config = types.SimpleNamespace(
        sources=(ArchiveSource(mirror="http://example.invalid", release="trixie", components=("main",)),),
        fetch_source=False,
    )
    index = RepositoryIndex(binary_packages=packages, source_packages={})
    resolver = PackageResolver(config, conn)
    resolver.index = index
    return resolver


def _new_db():
    tmp = tempfile.TemporaryDirectory()
    conn = sqlite3.connect(f"{tmp.name}/test.db")
    conn.executescript(EXPECTED_SCHEMA_SQL)
    conn.commit()
    return tmp, conn


class TestCircularDetection(unittest.TestCase):
    """Cycles are annotated; diamonds are not."""

    def test_cycle_a_to_b_to_c_to_a_is_marked(self):
        tmp, conn = _new_db()
        try:
            index = {
                "a": [_binary("a", depends="b")],
                "b": [_binary("b", depends="c")],
                "c": [_binary("c", depends="a")],
            }
            _make_resolver(conn, index).resolve("a")

            row = conn.execute(
                "SELECT dependencies, circular FROM binaries WHERE package_name = 'c'"
            ).fetchone()
            self.assertEqual(row[1], 1, "cycle-closing binary must be marked circular")
            specs = json.loads(row[0])
            self.assertEqual(specs[0]["name"], "a")
            self.assertTrue(specs[0].get("circular"), "dependency spec closing the cycle must be flagged")

            row_a = conn.execute("SELECT circular FROM binaries WHERE package_name = 'a'").fetchone()
            self.assertEqual(row_a[0], 0, "a's own row must not be marked")
        finally:
            conn.close()
            tmp.cleanup()

    def test_verify_reports_circular(self):
        tmp, conn = _new_db()
        try:
            index = {
                "a": [_binary("a", depends="b")],
                "b": [_binary("b", depends="c")],
                "c": [_binary("c", depends="a")],
            }
            _make_resolver(conn, index).resolve("a")

            circular = _find_circular(conn, None)
            self.assertEqual(len(circular), 1)
            self.assertIn("c -> a (circular)", circular[0])
        finally:
            conn.close()
            tmp.cleanup()

    def test_diamond_is_not_false_circular(self):
        tmp, conn = _new_db()
        try:
            index = {
                "d": [_binary("d", depends="e, f")],
                "e": [_binary("e", depends="g")],
                "f": [_binary("f", depends="g")],
                "g": [_binary("g", depends=None)],
            }
            _make_resolver(conn, index).resolve("d")

            rows = conn.execute("SELECT package_name, circular FROM binaries").fetchall()
            self.assertTrue(all(circ == 0 for _, circ in rows), f"no false circular flags: {rows}")
            self.assertEqual(_find_circular(conn, None), [])
        finally:
            conn.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
