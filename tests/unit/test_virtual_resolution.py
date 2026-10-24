import sqlite3
import tempfile
import types
import unittest

from package_mgmt.lib.model.config import ArchiveSource
from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL
from package_mgmt.lib.model.resolution import ProviderCandidate
from package_mgmt.lib.model.package import BinaryPackage, SourcePackage
from package_mgmt.lib.model.repository import RepositoryIndex
from package_mgmt.lib.service.index_loader import parse_provides_field
from package_mgmt.lib.service.initializer import populate_provides_table
from package_mgmt.lib.service.resolver import PackageResolver
from package_mgmt.lib.service.resolver import (
    check_virtual_resolution,
    query_provides,
    record_virtual_resolution,
    select_provider,
)


_BINARY_ENTRIES = [
    BinaryPackage(
        package="gawk", version="5.2.1", architecture="amd64",
        release="trixie", component="main",
        depends_raw=None, provides_raw="awk, awk-api-2.0 (= 2.0)",
        source_field=None, filename=None, sha256=None,
    ),
    BinaryPackage(
        package="mawk", version="1.3.4", architecture="amd64",
        release="trixie", component="main",
        depends_raw=None, provides_raw="awk",
        source_field=None, filename=None, sha256=None,
    ),
    BinaryPackage(
        package="original-awk", version="2023-12", architecture="amd64",
        release="trixie", component="contrib",
        depends_raw=None, provides_raw="awk",
        source_field=None, filename=None, sha256=None,
    ),
    BinaryPackage(
        package="gawk", version="5.3.0", architecture="amd64",
        release="trixie-backports", component="main",
        depends_raw=None, provides_raw="awk, awk-api-2.0 (= 2.0)",
        source_field=None, filename=None, sha256=None,
    ),
    BinaryPackage(
        package="no-provides-pkg", version="1.0", architecture="amd64",
        release="trixie", component="main",
        depends_raw=None, provides_raw=None,
        source_field=None, filename=None, sha256=None,
    ),
]


class TestParseProvidesField(unittest.TestCase):
    def test_parses_unversioned(self):
        result = parse_provides_field("awk")
        self.assertEqual(result, [("awk", None)])

    def test_parses_versioned(self):
        result = parse_provides_field("awk-api-2.0 (= 2.0)")
        self.assertEqual(result, [("awk-api-2.0", "2.0")])

    def test_parses_multiple(self):
        result = parse_provides_field("awk, awk-api-2.0 (= 2.0)")
        self.assertEqual(result, [("awk", None), ("awk-api-2.0", "2.0")])

    def test_returns_empty_for_none(self):
        self.assertEqual(parse_provides_field(None), [])

    def test_returns_empty_for_empty(self):
        self.assertEqual(parse_provides_field(""), [])


class TestVirtualResolutionHelpers(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE provides ("
            "  package_name TEXT, version TEXT, architecture TEXT,"
            "  release TEXT, component TEXT, virtual_name TEXT, provides_version TEXT,"
            "  PRIMARY KEY (package_name, version, architecture, release, component, virtual_name)"
            ")"
        )
        self.conn.execute(
            "CREATE TABLE virtual_resolutions ("
            "  virtual_name TEXT PRIMARY KEY, resolved_binary_id INTEGER"
            ")"
        )
        self.conn.execute(
            "CREATE TABLE binaries ("
            "  id INTEGER PRIMARY KEY, package_name TEXT, version TEXT,"
            "  architecture TEXT, release TEXT, component TEXT, status TEXT"
            ")"
        )
        self.conn.execute(
            "INSERT INTO binaries VALUES (1, 'gawk', '5.2.1', 'amd64', 'trixie', 'main', 'current')"
        )
        self.conn.execute(
            "INSERT INTO binaries VALUES (2, 'gawk', '5.3.0', 'amd64', 'trixie-backports', 'main', 'current')"
        )
        self.conn.execute(
            "INSERT INTO provides VALUES ('gawk', '5.2.1', 'amd64', 'trixie', 'main', 'awk', NULL)"
        )
        self.conn.execute(
            "INSERT INTO provides VALUES ('mawk', '1.3.4', 'amd64', 'trixie', 'main', 'awk', NULL)"
        )
        self.conn.execute(
            "INSERT INTO provides VALUES ('original-awk', '2023-12', 'amd64', 'trixie', 'contrib', 'awk', NULL)"
        )
        self.conn.execute(
            "INSERT INTO provides VALUES ('gawk', '5.3.0', 'amd64', 'trixie-backports', 'main', 'awk', NULL)"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_check_virtual_resolution_found(self):
        self.conn.execute("INSERT INTO virtual_resolutions VALUES ('awk', 1)")
        result = check_virtual_resolution(self.conn, "awk")
        self.assertEqual(result, 1)

    def test_check_virtual_resolution_not_found(self):
        result = check_virtual_resolution(self.conn, "nonexistent")
        self.assertIsNone(result)

    def test_query_provides_returns_candidates(self):
        results = query_provides(self.conn, "awk")
        self.assertEqual(len(results), 4)

    def test_query_provides_empty(self):
        results = query_provides(self.conn, "nonexistent")
        self.assertEqual(results, [])

    def test_record_virtual_resolution_inserts(self):
        record_virtual_resolution(self.conn, "awk", 1)
        result = check_virtual_resolution(self.conn, "awk")
        self.assertEqual(result, 1)

    def test_record_virtual_resolution_replaces(self):
        record_virtual_resolution(self.conn, "awk", 1)
        record_virtual_resolution(self.conn, "awk", 2)
        result = check_virtual_resolution(self.conn, "awk")
        self.assertEqual(result, 2)

    def test_re_resolve_when_primary_source_offers_higher_version(self):
        record_virtual_resolution(self.conn, "awk", 1)
        cached_id = check_virtual_resolution(self.conn, "awk")
        self.assertEqual(cached_id, 1)

        cached_row = self.conn.execute(
            "SELECT package_name, version, release FROM binaries WHERE id = ?",
            (cached_id,),
        ).fetchone()
        self.assertEqual(cached_row, ("gawk", "5.2.1", "trixie"))

        candidates = query_provides(self.conn, "awk")
        self.assertEqual(len(candidates), 4)

        primary_source = "trixie-backports"
        fresh_selected = select_provider(candidates, set(), primary_source, None, None)
        self.assertIsNotNone(fresh_selected)
        f_pkg = fresh_selected.package_name
        f_ver = fresh_selected.version
        f_rel = fresh_selected.release
        self.assertEqual(f_rel, "trixie-backports")

        cached_pkg, cached_ver, cached_rel = cached_row
        should_re_resolve = (
            f_pkg != cached_pkg or f_ver != cached_ver or f_rel != cached_rel
        )
        self.assertTrue(should_re_resolve, "Expected re-resolution when primary source offers higher version")

        record_virtual_resolution(self.conn, "awk", 2)
        updated_id = check_virtual_resolution(self.conn, "awk")
        self.assertEqual(updated_id, 2)

        updated_row = self.conn.execute(
            "SELECT version, release FROM binaries WHERE id = ?",
            (updated_id,),
        ).fetchone()
        self.assertEqual(updated_row, ("5.3.0", "trixie-backports"))


class TestSelectProvider(unittest.TestCase):
    def setUp(self):
        self.index = RepositoryIndex()
        for entry in _BINARY_ENTRIES:
            self.index.binary_packages.setdefault(entry.package, []).append(entry)

    @staticmethod
    def _cand(package_name, version, release="trixie", component="main", provides_version=None):
        return ProviderCandidate(
            package_name=package_name, version=version, architecture="amd64",
            release=release, component=component, provides_version=provides_version,
        )

    def _select(self, candidates, visited=None, primary_source=None, version_constraint=None):
        return select_provider(candidates, visited or set(), primary_source, version_constraint, self.index)

    def test_selects_alphabetical_first(self):
        candidates = [
            self._cand("original-awk", "2023-12", component="contrib"),
            self._cand("mawk", "1.3.4"),
            self._cand("gawk", "5.2.1"),
        ]
        result = self._select(candidates)
        self.assertEqual(result.package_name, "gawk")

    def test_prefers_visited(self):
        candidates = [
            self._cand("original-awk", "2023-12", component="contrib"),
            self._cand("mawk", "1.3.4"),
            self._cand("gawk", "5.2.1"),
        ]
        result = self._select(candidates, visited={"mawk"})
        self.assertEqual(result.package_name, "mawk")

    def test_prefers_primary_source(self):
        candidates = [
            self._cand("original-awk", "2023-12", component="contrib"),
            self._cand("gawk", "5.2.1", release="trixie-backports"),
            self._cand("gawk", "5.2.1"),
        ]
        result = self._select(candidates, primary_source="trixie-backports")
        self.assertEqual(result.release, "trixie-backports")

    def test_primary_source_beats_visited(self):
        candidates = [
            self._cand("mawk", "1.3.4"),
            self._cand("gawk", "5.2.1", release="trixie-backports"),
        ]
        result = self._select(candidates, visited={"mawk"}, primary_source="trixie-backports")
        self.assertEqual(result.package_name, "gawk")

    def test_returns_none_for_empty(self):
        result = self._select([])
        self.assertIsNone(result)

    def test_filters_by_version_constraint(self):
        candidates = [
            self._cand("original-awk", "2023-12", component="contrib"),
            self._cand("mawk", "1.3.4"),
            self._cand("gawk", "5.2.1"),
        ]
        result = self._select(candidates, version_constraint={"operator": ">=", "version": "5.0"})
        self.assertIsNotNone(result)
        self.assertEqual(result.package_name, "gawk", "only providers satisfying >= 5.0 may be selected")

    def test_returns_none_when_constraint_filters_all(self):
        candidates = [
            self._cand("original-awk", "2023-12", component="contrib"),
            self._cand("mawk", "1.3.4"),
        ]
        result = self._select(candidates, version_constraint={"operator": ">=", "version": "9999"})
        self.assertIsNone(result)


class TestVersionedProvidesMatching(unittest.TestCase):
    """Fix B: versioned provides match constraints against provides_version."""

    def setUp(self):
        self.index = RepositoryIndex()
        for entry in _BINARY_ENTRIES:
            self.index.binary_packages.setdefault(entry.package, []).append(entry)

    def _select(self, candidates, constraint):
        return select_provider(candidates, set(), None, constraint, self.index)

    @staticmethod
    def _cand(package_name, version, provides_version):
        return ProviderCandidate(
            package_name=package_name, version=version, architecture="amd64",
            release="trixie", component="main", provides_version=provides_version,
        )

    def test_matches_constraint_against_provides_version(self):
        candidates = [self._cand("debhelper", "13.24.2", "9")]
        result = self._select(candidates, {"operator": "=", "version": "9"})
        self.assertIsNotNone(result)
        self.assertEqual(result.package_name, "debhelper")

    def test_rejects_provider_when_provides_version_mismatches(self):
        candidates = [self._cand("debhelper", "13.24.2", "9")]
        result = self._select(candidates, {"operator": "=", "version": "13"})
        self.assertIsNone(result, "provider version 13.24.2 must not satisfy (= 13) via versioned provides")

    def test_unversioned_provides_matches_against_provider_version(self):
        candidates = [self._cand("gawk", "5.2.1", None)]
        result = self._select(candidates, {"operator": ">=", "version": "5.0"})
        self.assertIsNotNone(result)
        self.assertEqual(result.package_name, "gawk")


class TestMultiVersionedProvides(unittest.TestCase):
    """e2e fix: a binary providing the same virtual at several versions keeps all rows."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_all_versioned_provides_rows_stored(self):
        index = RepositoryIndex()
        index.binary_packages.setdefault("debhelper", []).append(
            BinaryPackage(
                package="debhelper", version="13.24.2", architecture="amd64",
                release="trixie", component="main",
                depends_raw=None,
                provides_raw="debhelper-compat (= 9), debhelper-compat (= 13), dh-buildinfo",
                source_field=None, filename=None, sha256=None,
            )
        )
        count = populate_provides_table(self.conn, index)
        rows = self.conn.execute(
            "SELECT provides_version FROM provides WHERE virtual_name='debhelper-compat' ORDER BY provides_version"
        ).fetchall()
        self.assertEqual({r[0] for r in rows}, {"9", "13"},
                         "each versioned provides value needs its own row")
        self.assertEqual(count, 3)


class TestVirtualSourceDepthRepair(unittest.TestCase):
    """A virtually-provided binary first resolved out of
    scope (deep) gains its source when later reached in scope (shallow) —
    including across separate runs. The cached-virtual fast path must not
    skip the source gate."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(f"{self.tmp.name}/test.db")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()
        self.config = types.SimpleNamespace(
            sources=(
                ArchiveSource(mirror="http://example.invalid", release="trixie", components=("main",)),
            ),
            fetch_source=True,
            max_source_dep_depth=1,
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    @staticmethod
    def _index():
        def _b(name, depends=None, provides=None):
            return BinaryPackage(
                package=name, version="1.0", architecture="amd64",
                release="trixie", component="main",
                depends_raw=depends, provides_raw=provides,
                source_field=name, filename=None, sha256=None,
            )

        def _s(name):
            return SourcePackage(
                package=name, version="1.0", release="trixie", component="main",
                directory=None, files=None, build_depends=None,
            )

        return RepositoryIndex(
            binary_packages={
                "deepapp": [_b("deepapp", depends="mid")],
                "mid": [_b("mid", depends="awk")],
                "app": [_b("app", depends="awk")],
                "gawk": [_b("gawk", provides="awk")],
            },
            source_packages={
                "deepapp": [_s("deepapp")],
                "mid": [_s("mid")],
                "app": [_s("app")],
                "gawk": [_s("gawk")],
            },
        )

    def test_virtual_provider_source_repaired_across_runs(self):
        index = self._index()
        populate_provides_table(self.conn, index)

        resolver = PackageResolver(self.config, self.conn)
        resolver.index = index
        stats = resolver.resolve("deepapp")
        self.assertEqual(stats.errors, [])
        sources = [r[0] for r in self.conn.execute("SELECT package_name FROM sources")]
        self.assertEqual(
            sorted(sources), ["deepapp", "mid"],
            "awk reached at level 2 is out of scope at depth 1 — no source yet",
        )

        resolver2 = PackageResolver(self.config, self.conn)
        resolver2.index = index
        stats = resolver2.resolve("app")
        self.assertEqual(stats.errors, [])
        sources = [r[0] for r in self.conn.execute("SELECT package_name FROM sources")]
        self.assertEqual(
            sorted(sources), ["app", "deepapp", "gawk", "mid"],
            "the virtually-provided provider (gawk) must gain its source when "
            "awk is later reached at in-scope level 1 (cross-run)",
        )

    def test_virtual_provider_source_repaired_at_unlimited_depth(self):
        """Graybeard (-1): a provider first cached under a source-less
        profile (regular_user) must gain its source when the same virtual is
        later reached at unlimited depth — the cached-virtual fast path must
        not skip the source repair at `-1` (N-2)."""
        index = self._index()
        populate_provides_table(self.conn, index)

        source_less = types.SimpleNamespace(
            sources=self.config.sources,
            fetch_source=False,
            max_source_dep_depth=0,
        )
        resolver = PackageResolver(source_less, self.conn)
        resolver.index = index
        stats = resolver.resolve("deepapp")
        self.assertEqual(stats.errors, [])
        self.assertEqual(
            [r[0] for r in self.conn.execute("SELECT package_name FROM sources")],
            [],
            "regular_user run: no sources anywhere, provider cached via virtual",
        )

        unlimited = types.SimpleNamespace(
            sources=self.config.sources,
            fetch_source=True,
            max_source_dep_depth=-1,
        )
        resolver2 = PackageResolver(unlimited, self.conn)
        resolver2.index = index
        stats = resolver2.resolve("app")
        self.assertEqual(stats.errors, [])
        sources = [r[0] for r in self.conn.execute("SELECT package_name FROM sources")]
        self.assertEqual(
            sorted(sources), ["app", "gawk"],
            "the virtually-provided provider (gawk) must gain its source at "
            "-1 (cached-virtual repair must not be gated off at unlimited depth)",
        )
