import sqlite3
import tempfile
import types
import unittest

from package_mgmt.lib.model.config import ArchiveSource
from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL
from package_mgmt.lib.model.package import BinaryPackage, SourcePackage
from package_mgmt.lib.model.repository import RepositoryIndex
from package_mgmt.lib.service.resolver import PackageResolver


def _binary(name, version="1.0", release="trixie", component="main", source_field=None):
    return BinaryPackage(
        package=name, version=version, architecture="amd64",
        release=release, component=component,
        depends_raw=None, provides_raw=None,
        source_field=source_field if source_field is not None else name,
        filename=None, sha256=None,
    )


def _source(name, version, release="trixie", component="main"):
    return SourcePackage(
        package=name, version=version, release=release, component=component,
        directory=None, files=None, build_depends=None,
    )


def _config(fetch_source: bool = True, depth: int = -1) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        sources=(
            ArchiveSource(mirror="http://example.invalid", release="trixie", components=("main",)),
        ),
        fetch_source=fetch_source,
        max_build_dep_report_depth=2,
        max_source_dep_depth=depth,
    )


class SourceLinkageTestBase(unittest.TestCase):
    def setUp(self):
        self.config = _config()
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

    def _linked_sources(self):
        return self.conn.execute(
            "SELECT package_name, source_version, release, component FROM sources"
            " ORDER BY package_name, source_version"
        ).fetchall()


class TestExactSourceVersionLinkage(SourceLinkageTestBase):
    """A binary links to its exact source version — the version carried in
    the `Source:` field wins, otherwise the version derived from the binary
    version (binNMU `+bN` stripped) — never the newest available."""

    def test_declared_source_version_wins_over_newest(self):
        index = RepositoryIndex(
            binary_packages={
                "foo-bin": [_binary("foo-bin", version="1.0-1+b1", source_field="foo (1.0-1)")],
            },
            source_packages={
                "foo": [_source("foo", "1.0-1"), _source("foo", "2.0-1")],
            },
        )
        resolver = self._resolver(index)
        stats = resolver.resolve("foo-bin")
        self.assertEqual(stats.errors, [])
        self.assertEqual(
            self._linked_sources(),
            [("foo", "1.0-1", "trixie", "main")],
            "the exact declared source version must win, not the newest",
        )

    def test_bin_nmu_derived_version_when_no_declared_source_version(self):
        index = RepositoryIndex(
            binary_packages={
                "foo-bin": [_binary("foo-bin", version="1.0-1+b1", source_field="foo")],
            },
            source_packages={
                "foo": [_source("foo", "1.0-1"), _source("foo", "2.0-1")],
            },
        )
        resolver = self._resolver(index)
        stats = resolver.resolve("foo-bin")
        self.assertEqual(stats.errors, [])
        self.assertEqual(
            self._linked_sources(),
            [("foo", "1.0-1", "trixie", "main")],
            "+bN must be stripped so the base source version is linked",
        )


class TestBackportsSourceLinkage(SourceLinkageTestBase):
    """A fallback-release binary links its source from its own
    release/component first; an absent exact version falls back to the newest
    in the release with a logged divergence (never silent)."""

    def test_backports_binary_links_source_from_its_own_release(self):
        index = RepositoryIndex(
            binary_packages={
                "foo-bin": [_binary(
                    "foo-bin", version="1.0-1+b1", source_field="foo",
                    release="trixie-backports",
                )],
            },
            source_packages={
                "foo": [
                    _source("foo", "1.0-1", release="trixie-backports"),
                    _source("foo", "2.0-1", release="trixie"),
                ],
            },
        )
        resolver = self._resolver(index)
        stats = resolver.resolve("foo-bin")
        self.assertEqual(stats.errors, [])
        self.assertEqual(
            self._linked_sources(),
            [("foo", "1.0-1", "trixie-backports", "main")],
            "a backports binary must link its source from the backports release, "
            "not the primary release's newer source",
        )

    def test_exact_version_absent_falls_back_with_divergence_warning(self):
        index = RepositoryIndex(
            binary_packages={
                "foo-bin": [_binary(
                    "foo-bin", version="3.0-1", source_field="foo",
                    release="trixie-backports",
                )],
            },
            source_packages={
                "foo": [
                    _source("foo", "2.0-1", release="trixie-backports"),
                    _source("foo", "2.0-1", release="trixie"),
                ],
            },
        )
        resolver = self._resolver(index)
        with self.assertLogs("package_mgmt.lib.service.resolver", level="WARNING") as cm:
            stats = resolver.resolve("foo-bin")
        self.assertEqual(stats.errors, [])
        self.assertEqual(
            self._linked_sources(),
            [("foo", "2.0-1", "trixie-backports", "main")],
            "the fallback prefers the newest source in the binary's own release",
        )
        self.assertTrue(
            any("3.0-1" in m for m in cm.output),
            f"a divergence warning must name the expected version, got: {cm.output}",
        )

    def test_no_source_exists_keeps_derived_version_fallback(self):
        index = RepositoryIndex(
            binary_packages={
                "foo-bin": [_binary("foo-bin", version="1.0-1+b1", source_field="foo")],
            },
            source_packages={},
        )
        resolver = self._resolver(index)
        stats = resolver.resolve("foo-bin")
        self.assertEqual(stats.errors, [])
        self.assertEqual(
            self._linked_sources(),
            [("foo", "1.0-1", "trixie", "main")],
            "no source at all keeps the derived version + the binary's release/component",
        )


if __name__ == "__main__":
    unittest.main()
