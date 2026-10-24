import sqlite3
import tempfile
import types
import unittest

from package_mgmt.lib.model.config import ArchiveSource
from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL
from package_mgmt.lib.model.package import BinaryPackage
from package_mgmt.lib.model.repository import RepositoryIndex
from package_mgmt.lib.service.resolver import PackageResolver


def _binary(name, version, release="trixie", component="main", depends=None):
    return BinaryPackage(
        package=name, version=version, architecture="amd64",
        release=release, component=component,
        depends_raw=depends, provides_raw=None,
        source_field=name, filename=None, sha256=None,
    )


class ConstraintTestBase(unittest.TestCase):
    def setUp(self):
        self.config = types.SimpleNamespace(
            sources=(
                ArchiveSource(mirror="http://example.invalid", release="trixie", components=("main",)),
                ArchiveSource(mirror="http://example.invalid", release="trixie-backports", components=("main",)),
            ),
            fetch_source=False,
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(f"{self.tmp.name}/test.db")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _resolver(self, index):
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = index
        return resolver


class TestRealPackageConstraint(ConstraintTestBase):
    """Constrained real dependencies fall back across sources."""

    def test_constraint_falls_back_to_satisfying_source(self):
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", "1.0", depends="libdrm (>= 2.4.120)")],
                "libdrm": [
                    _binary("libdrm", "2.4.110", release="trixie"),
                    _binary("libdrm", "2.4.120", release="trixie-backports"),
                ],
            },
        )
        stats = self._resolver(index).resolve("app")

        self.assertEqual(stats.errors, [])
        row = self.conn.execute(
            "SELECT version, release FROM binaries WHERE package_name = 'libdrm' AND status = 'current'"
        ).fetchone()
        self.assertEqual(row, ("2.4.120", "trixie-backports"),
                         "constraint >= 2.4.120 must fall back from trixie 2.4.110 to backports 2.4.120")

    def test_no_satisfying_version_reported_best_effort(self):
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", "1.0", depends="libdrm (>= 2.4.120)")],
                "libdrm": [_binary("libdrm", "2.4.110", release="trixie")],
            },
        )
        stats = self._resolver(index).resolve("app")

        self.assertTrue(any("libdrm" in e and "2.4.120" in e for e in stats.errors),
                        f"expected unsatisfiable-constraint error, got {stats.errors}")
        row = self.conn.execute(
            "SELECT 1 FROM binaries WHERE package_name = 'libdrm' AND status = 'current'"
        ).fetchone()
        self.assertIsNone(row, "no record may be created for a constraint-violating version")

    def test_unconstrained_behavior_unchanged(self):
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", "1.0", depends="libdrm")],
                "libdrm": [
                    _binary("libdrm", "2.4.110", release="trixie"),
                    _binary("libdrm", "2.4.120", release="trixie-backports"),
                ],
            },
        )
        stats = self._resolver(index).resolve("app")
        self.assertEqual(stats.errors, [])
        row = self.conn.execute(
            "SELECT version, release FROM binaries WHERE package_name = 'libdrm' AND status = 'current'"
        ).fetchone()
        self.assertEqual(row[0], "2.4.110", "without a constraint the primary source wins")


class TestVirtualPackageConstraint(ConstraintTestBase):
    """Constrained virtual dependencies filter providers."""

    def setUp(self):
        super().setUp()
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS provides (
                package_name TEXT NOT NULL, version TEXT NOT NULL, architecture TEXT,
                release TEXT NOT NULL, component TEXT NOT NULL,
                virtual_name TEXT NOT NULL, provides_version TEXT,
                PRIMARY KEY (package_name, version, architecture, release, component, virtual_name))
            """
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS virtual_resolutions (
                virtual_name TEXT PRIMARY KEY, resolved_binary_id INTEGER NOT NULL REFERENCES binaries(id))
            """
        )
        self.conn.commit()

    def _seed_provides(self):
        for name, ver, rel, virtual in [
            ("gawk", "5.2.1", "trixie", "awk"),
            ("mawk", "1.3.4", "trixie", "awk"),
        ]:
            self.conn.execute(
                """INSERT INTO provides (package_name, version, architecture, release, component, virtual_name)
                   VALUES (?, ?, 'amd64', ?, 'main', ?)""",
                (name, ver, rel, virtual),
            )
        self.conn.commit()

    def test_constrained_virtual_filters_out_smaller_provider(self):
        self._seed_provides()
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", "1.0", depends="awk (>= 5.0)")],
                "gawk": [_binary("gawk", "5.2.1")],
                "mawk": [_binary("mawk", "1.3.4")],
            },
        )
        stats = self._resolver(index).resolve("app")

        self.assertEqual(stats.errors, [])
        row = self.conn.execute(
            "SELECT package_name, version FROM binaries WHERE status = 'current' AND package_name = 'gawk'"
        ).fetchone()
        self.assertEqual(row, ("gawk", "5.2.1"), "mawk 1.3.4 must be filtered out by the >= 5.0 constraint")
        vr = self.conn.execute(
            "SELECT b.package_name FROM virtual_resolutions vr JOIN binaries b ON b.id = vr.resolved_binary_id WHERE vr.virtual_name = 'awk'"
        ).fetchone()
        self.assertEqual(vr, ("gawk",))

    def test_constrained_virtual_no_satisfier_reported_best_effort(self):
        self._seed_provides()
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", "1.0", depends="awk (>= 9.0)")],
                "gawk": [_binary("gawk", "5.2.1")],
                "mawk": [_binary("mawk", "1.3.4")],
            },
        )
        stats = self._resolver(index).resolve("app")

        self.assertTrue(any("awk" in e and "satisfiable" in e for e in stats.errors),
                        f"expected virtual unsatisfiable error, got {stats.errors}")
        self.assertIsNone(
            self.conn.execute("SELECT 1 FROM virtual_resolutions WHERE virtual_name = 'awk'").fetchone(),
            "no virtual resolution may be recorded for an unsatisfiable constraint",
        )

    def test_cached_virtual_resolution_reused_when_satisfying(self):
        self._seed_provides()
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", "1.0", depends="awk")],
                "gawk": [_binary("gawk", "5.2.1")],
                "mawk": [_binary("mawk", "1.3.4")],
            },
        )
        resolver = self._resolver(index)
        resolver.resolve("app")
        cached_id = self.conn.execute(
            "SELECT resolved_binary_id FROM virtual_resolutions WHERE virtual_name = 'awk'"
        ).fetchone()[0]

        index2 = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", "1.0", depends="awk (>= 5.0)")],
                "gawk": [_binary("gawk", "5.2.1")],
                "mawk": [_binary("mawk", "1.3.4")],
            },
        )
        self._resolver(index2).resolve("app")
        after_id = self.conn.execute(
            "SELECT resolved_binary_id FROM virtual_resolutions WHERE virtual_name = 'awk'"
        ).fetchone()[0]
        self.assertEqual(after_id, cached_id, "satisfying cached virtual resolution must be reused")


if __name__ == "__main__":
    unittest.main()


class TestCompareVersionsOperators(unittest.TestCase):
    """e2e fix: Debian legacy >> / << operators in compare_versions."""

    def test_legacy_strict_greater(self):
        from package_mgmt.lib.util.versions import compare_versions
        self.assertTrue(compare_versions("5.40.0-7", ">>", "5.005"))
        self.assertFalse(compare_versions("2.41", ">>", "2.41"))
        self.assertFalse(compare_versions("4.9", ">>", "5.005"))

    def test_modern_operators_unchanged(self):
        from package_mgmt.lib.util.versions import compare_versions
        self.assertTrue(compare_versions("5.40.0-7", ">", "5.005"))
        self.assertTrue(compare_versions("5.40.0-7", ">=", "5.40.0-7"))
        self.assertTrue(compare_versions("5.0", "<", "5.1"))
        self.assertTrue(compare_versions("5.0", "<<", "5.1"))
        self.assertTrue(compare_versions("5.0", "=", "5.0"))
        self.assertTrue(compare_versions("5.0", "!=", "5.1"))

    def test_unknown_operator_returns_false(self):
        from package_mgmt.lib.util.versions import compare_versions
        self.assertFalse(compare_versions("1.0", "~>", "0.9"))


class TestRealAndVirtualConstraintFallback(unittest.TestCase):
    """e2e fix: a name that is both a real package and a provides falls back to
    the virtual provider when no real version satisfies the constraint."""

    def setUp(self):
        self.config = types.SimpleNamespace(
            sources=(ArchiveSource(mirror="http://example.invalid", release="trixie", components=("main",)),),
            fetch_source=False,
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(f"{self.tmp.name}/test.db")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.execute(
            """INSERT INTO provides (package_name, version, architecture, release, component, virtual_name, provides_version)
               VALUES ('python3-python-multipart', '0.0.20', 'amd64', 'trixie', 'main', 'python3-multipart', '0.0.20')"""
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_constraint_falls_back_to_versioned_provider(self):
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", "1.0", depends="python3-multipart (<< 0.1)")],
                "python3-multipart": [_binary("python3-multipart", "1.2.1")],
                "python3-python-multipart": [_binary("python3-python-multipart", "0.0.20")],
            },
        )
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = index
        stats = resolver.resolve("app")

        self.assertEqual(stats.errors, [])
        row = self.conn.execute(
            "SELECT package_name, version FROM binaries WHERE status='current'"
            " AND package_name='python3-python-multipart'"
        ).fetchone()
        self.assertEqual(row, ("python3-python-multipart", "0.0.20"),
                         "the versioned provider must satisfy (<< 0.1)")

    def test_no_provider_no_real_version_reports_constraint_error(self):
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", "1.0", depends="onlyreal (<< 0.1)")],
                "onlyreal": [_binary("onlyreal", "1.2.1")],
            },
        )
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = index
        stats = resolver.resolve("app")

        self.assertTrue(any("onlyreal" in e and "0.1" in e for e in stats.errors))
