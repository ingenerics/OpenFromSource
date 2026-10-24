import gzip
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_deb  # noqa: E402
import make_test_iso  # noqa: E402
from db import config, insert_binary  # noqa: E402

from package_mgmt.lib.model.config import ArchiveSource, ProjectConfig  # noqa: E402
from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL
from package_mgmt.lib.model.package import BinaryPackage, SourcePackage
from package_mgmt.lib.model.repository import RepositoryIndex
from package_mgmt.lib.service.db_init import initialize_database  # noqa: E402
from package_mgmt.lib.service.handoff import generate_handoff  # noqa: E402
from package_mgmt.lib.service.import_local import import_local_deb  # noqa: E402
from package_mgmt.lib.service.initializer import populate_provides_table
from package_mgmt.lib.service.resolver import (
    PackageResolver,
    _find_current_binary,
    supersede_current_binary,
    upsert_binary,
)
from package_mgmt.lib.util.paths import handoff_index_path  # noqa: E402


def _binary(name, version="1.0", depends=None, provides=None, release="trixie", component="main"):
    return BinaryPackage(
        package=name, version=version, architecture="amd64",
        release=release, component=component,
        depends_raw=depends, provides_raw=provides,
        source_field=None, filename=None, sha256=None,
    )


def _source(name, build_depends, version="1.0"):
    return SourcePackage(
        package=name, version=version, release="trixie", component="main",
        directory=None, files=None, build_depends=build_depends,
    )


def _config(fetch_source):
    return types.SimpleNamespace(
        sources=(ArchiveSource(mirror="http://example.invalid", release="trixie", components=("main",)),),
        fetch_source=fetch_source,
        max_source_dep_depth=-1,
    )


class TestFindCurrentBinaryDeterministic(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)

    def tearDown(self):
        self.conn.close()

    @staticmethod
    def _insert(conn, name, version, release, component, status):
        conn.execute(
            "INSERT INTO binaries"
            " (package_name, version, architecture, release, component, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (name, version, "amd64", release, component, status),
        )

    def test_first_inserted_row_wins_on_multiple_current_rows(self):
        self._insert(self.conn, "gawk", "9.9", "trixie-backports", "main", "current")
        self._insert(self.conn, "gawk", "1.0", "trixie", "main", "current")
        row = _find_current_binary(self.conn, "gawk", "amd64")
        self.assertIsNotNone(row)
        self.assertEqual(
            row, (1, "9.9"),
            "the lowest-id (first-inserted) current row must win — a defined, deterministic ordering",
        )

    def test_superseded_rows_are_ignored(self):
        self._insert(self.conn, "gawk", "5.2.1", "trixie", "main", "superseded")
        self._insert(self.conn, "gawk", "5.3.0", "trixie-backports", "main", "current")
        row = _find_current_binary(self.conn, "gawk", "amd64")
        self.assertIsNotNone(row)
        self.assertEqual(row[1], "5.3.0")


class TestSupersedeCurrentBinary(unittest.TestCase):
    """The shared supersede primitive used by both the archive resolve path
    and the local import lane: exactly one `current` row per
    package/architecture, a different-version registration marks the prior row
    `superseded` (its payload is retained), and re-registering the same version
    is idempotent (the existing row is reused)."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)

    def tearDown(self):
        self.conn.close()

    def _register(self, name, version, component="main"):
        return supersede_current_binary(
            self.conn, name, "amd64", version, "trixie", component, "[]"
        )

    def test_no_current_row_registers_current(self):
        bid = self._register("patch-tool", "1.2-3")
        row = self.conn.execute(
            "SELECT id, version, component, status FROM binaries WHERE id = ?", (bid,)
        ).fetchone()
        self.assertEqual(row, (bid, "1.2-3", "main", "current"))

    def test_different_version_supersedes_prior_current_row(self):
        old_id = self._register("patch-tool", "1.2-3", component="local")
        new_id = self._register("patch-tool", "1.3-0", component="local")
        self.assertNotEqual(new_id, old_id)
        rows = self.conn.execute(
            "SELECT version, status FROM binaries WHERE package_name = 'patch-tool'"
            " ORDER BY version"
        ).fetchall()
        self.assertEqual(rows, [("1.2-3", "superseded"), ("1.3-0", "current")])

    def test_same_version_is_idempotent_and_reuses_row(self):
        first = self._register("patch-tool", "1.2-3", component="local")
        second = self._register("patch-tool", "1.2-3", component="local")
        self.assertEqual(first, second)
        rows = self.conn.execute(
            "SELECT version, status FROM binaries WHERE package_name = 'patch-tool'"
        ).fetchall()
        self.assertEqual(rows, [("1.2-3", "current")])


class TestSameVersionArchiveCollision(unittest.TestCase):
    """Importing a local `.deb` at the same version as a current archive row
    must create a distinct current `local` row (the archive row is
    superseded), never mark the archive row verified, and the handoff must
    emit the local stanza rather than the upstream one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        make_test_iso.write_pool_files(self.base)
        self.config = config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.conn.execute("INSERT INTO systems (name, bit) VALUES ('prod-box', 2)")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_same_version_local_import_supersedes_archive_row(self):
        # An upstream `app 1.5-1` is current (its stanza exists in the cached
        # main index) but its payload is not yet downloaded (unverified).
        archive_id = insert_binary(self.conn, "app", "1.5-1", verified=0)
        self.conn.commit()

        deb = os.path.join(self.tmp.name, "app_1.5-1_amd64.deb")
        make_test_deb.build_deb(deb, package="app", version="1.5-1")
        import_local_deb(self.conn, self.config, deb)

        rows = self.conn.execute(
            "SELECT id, component, status, verified FROM binaries"
            " WHERE package_name = 'app' ORDER BY id"
        ).fetchall()
        self.assertEqual(len(rows), 2, "the import must create a distinct local row")
        self.assertEqual(
            rows,
            [
                (archive_id, "main", "superseded", 0),
                (rows[1][0], "local", "current", 1),
            ],
        )

        generate_handoff(self.config, self.conn)
        with open(handoff_index_path(self.config.binary_cache, "trixie", "local"), "rb") as f:
            local_index = gzip.decompress(f.read()).decode("utf-8")
        self.assertIn("Package: app", local_index,
                      "the handoff must emit the local stanza")
        with open(handoff_index_path(self.config.binary_cache, "trixie", "main"), "rb") as f:
            main_index = gzip.decompress(f.read()).decode("utf-8")
        self.assertNotIn("Package: app", main_index,
                         "the superseded archive row must not be emitted upstream")


class TestBuildDepFallbackConstraint(unittest.TestCase):
    def setUp(self):
        self.config = _config(fetch_source=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(f"{self.tmp.name}/test.db")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _register_current(self, name, version):
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = RepositoryIndex(binary_packages={name: [_binary(name, version=version)]})
        resolver.resolve(name)

    def _app_links(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM source_build_deps sbd"
            " JOIN sources s ON s.id = sbd.source_id"
            " WHERE s.package_name = 'app'"
        ).fetchone()[0]

    def test_fallback_does_not_link_constraint_violating_candidate(self):
        self._register_current("tool", "1.0")
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends=None)],
                "tool": [_binary("tool", version="1.0")],
            },
            source_packages={
                "app": [_source("app", "tool (>= 2.0)")],
            },
        )
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = index
        resolver.resolve("app")
        self.assertEqual(
            self._app_links(), 0,
            "a current binary that violates the build-dep version constraint must not be linked",
        )

    def test_fallback_links_satisfying_candidate_without_residual_error(self):
        self._register_current("tool", "3.0")
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends=None)],
            },
            source_packages={
                "app": [_source("app", "tool (>= 2.0)")],
            },
        )
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = index
        stats = resolver.resolve("app")
        self.assertEqual(stats.errors, [], "a linked build tool must not also be reported as unresolved")
        link = self.conn.execute(
            "SELECT b.package_name FROM source_build_deps sbd"
            " JOIN sources s ON s.id = sbd.source_id"
            " JOIN binaries b ON b.id = sbd.binary_id"
            " WHERE s.package_name = 'app'"
        ).fetchone()
        self.assertEqual(link, ("tool",))

    def test_build_dep_twin_tries_resolvable_alternative_when_primary_too_old(self):
        # a 1.0 is current; app's build-deps are "a (>= 2.0) | b": the twin
        # probe must not suppress the resolvable alternative, and b is
        # linked as the build tool with no residual error.
        self._register_current("a", "1.0")
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends=None)],
                "a": [_binary("a", version="1.0")],
                "b": [_binary("b")],
            },
            source_packages={
                "app": [_source("app", "a (>= 2.0) | b")],
            },
        )
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = index
        stats = resolver.resolve("app")
        self.assertEqual(
            stats.errors, [],
            "the resolvable build-dep alternative must link with no residual error",
        )
        link = self.conn.execute(
            "SELECT b.package_name FROM source_build_deps sbd"
            " JOIN sources s ON s.id = sbd.source_id"
            " JOIN binaries b ON b.id = sbd.binary_id"
            " WHERE s.package_name = 'app'"
        ).fetchone()
        self.assertEqual(link, ("b",), "the resolvable alternative is linked as the build tool")


class TestRuntimeAlternativeStatusFilter(unittest.TestCase):
    def setUp(self):
        self.config = _config(fetch_source=False)
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(f"{self.tmp.name}/test.db")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_alternative_group_not_suppressed_by_superseded_rows(self):
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="foo | bar")],
                "bar": [_binary("bar")],
            },
        )
        # The virtual "foo" is provided only by a superseded binary — it must
        # not count as satisfying the group, so the alternative resolves.
        self.conn.execute(
            "INSERT INTO binaries (package_name, version, architecture, release, component, status, dependencies, verified)"
            " VALUES ('oldfoo', '1.0', 'amd64', 'trixie', 'main', 'superseded', '[]', 0)"
        )
        self.conn.execute(
            "INSERT INTO provides (package_name, version, architecture, release, component, virtual_name, provides_version)"
            " VALUES ('oldfoo', '1.0', 'amd64', 'trixie', 'main', 'foo', NULL)"
        )
        self.conn.commit()

        resolver = PackageResolver(self.config, self.conn)
        resolver.index = index
        stats = resolver.resolve("app")
        self.assertEqual(stats.errors, [])
        row = self.conn.execute(
            "SELECT id FROM binaries WHERE package_name = 'bar' AND status = 'current'"
        ).fetchone()
        self.assertIsNotNone(row, "the alternative must be resolved rather than suppressed")


class TestRuntimeAlternativeConstraintProbe(unittest.TestCase):
    """The runtime "already satisfied" alternative-group probe is
    constraint-aware — a member whose current row fails its own version
    constraint must not satisfy the group, and a resolvable alternative is
    brought into the closure."""

    def setUp(self):
        self.config = _config(fetch_source=False)
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(f"{self.tmp.name}/test.db")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _register_current(self, name, version):
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = RepositoryIndex(binary_packages={name: [_binary(name, version=version)]})
        resolver.resolve(name)

    def test_too_old_primary_does_not_suppress_resolvable_alternative(self):
        # a 1.0 is already current; app depends "a (>= 2.0) | b".
        self._register_current("a", "1.0")
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="a (>= 2.0) | b")],
                "a": [_binary("a", version="1.0")],
                "b": [_binary("b")],
            },
        )
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = index
        stats = resolver.resolve("app")
        self.assertEqual(stats.errors, [], "no residual error after the alternative resolves")
        row = self.conn.execute(
            "SELECT version FROM binaries WHERE package_name = 'b' AND status = 'current'"
        ).fetchone()
        self.assertEqual(
            row, ("1.0",),
            "the resolvable alternative must be brought into the closure",
        )

    def test_satisfying_primary_still_wins(self):
        # a 2.0 satisfies ">= 2.0": the group is satisfied by a, b NOT resolved
        # (behavior unchanged — guards against over-correction).
        self._register_current("a", "2.0")
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="a (>= 2.0) | b")],
                "a": [_binary("a", version="2.0")],
                "b": [_binary("b")],
            },
        )
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = index
        stats = resolver.resolve("app")
        self.assertEqual(stats.errors, [])
        row = self.conn.execute(
            "SELECT id FROM binaries WHERE package_name = 'b' AND status = 'current'"
        ).fetchone()
        self.assertIsNone(row, "a constraint-satisfying primary must win; b is not resolved")

    def test_virtual_member_current_provider_still_satisfies(self):
        # A virtual member with any current provider satisfies the group — the
        # provides-join (virtual-member) leg stays over-inclusive.
        self.conn.execute(
            "INSERT INTO binaries (package_name, version, architecture, release, component, status, dependencies, verified)"
            " VALUES ('prov', '1.0', 'amd64', 'trixie', 'main', 'current', '[]', 0)"
        )
        self.conn.execute(
            "INSERT INTO provides (package_name, version, architecture, release, component, virtual_name, provides_version)"
            " VALUES ('prov', '1.0', 'amd64', 'trixie', 'main', 'foo', NULL)"
        )
        self.conn.commit()
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="foo | b")],
                "b": [_binary("b")],
            },
        )
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = index
        stats = resolver.resolve("app")
        self.assertEqual(stats.errors, [], "the over-inclusive virtual-provider leg retracts the probe error")
        row = self.conn.execute(
            "SELECT id FROM binaries WHERE package_name = 'b' AND status = 'current'"
        ).fetchone()
        self.assertIsNone(row, "the over-inclusive virtual member must satisfy the group")


class TestCachedVirtualSameReleaseRefresh(unittest.TestCase):
    def setUp(self):
        self.config = _config(fetch_source=False)
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(f"{self.tmp.name}/test.db")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_same_release_provider_upgrade_picked_up(self):
        index_v1 = RepositoryIndex(
            binary_packages={"gawk": [_binary("gawk", version="5.2.1", provides="awk")]},
        )
        populate_provides_table(self.conn, index_v1)
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = index_v1
        stats = resolver.resolve("awk")
        self.assertEqual(stats.errors, [])
        old = self.conn.execute(
            "SELECT version FROM binaries WHERE package_name = 'gawk' AND status = 'current'"
        ).fetchone()
        self.assertEqual(old, ("5.2.1",))

        # The index refresh upgrades the provider in the primary source and
        # the provides table is re-populated.
        index_v2 = RepositoryIndex(
            binary_packages={"gawk": [_binary("gawk", version="5.3.0", provides="awk")]},
        )
        self.conn.execute("DELETE FROM provides")
        populate_provides_table(self.conn, index_v2)

        resolver2 = PackageResolver(self.config, self.conn)
        resolver2.index = index_v2
        stats2 = resolver2.resolve("awk")
        self.assertEqual(stats2.errors, [])
        current = self.conn.execute(
            "SELECT version FROM binaries WHERE package_name = 'gawk' AND status = 'current'"
        ).fetchone()
        self.assertEqual(
            current, ("5.3.0",),
            "a cached virtual resolution must pick up a same-release provider upgrade",
        )


class TestCircularFlagLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(f"{self.tmp.name}/test.db")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_upsert_resets_circular_flag(self):
        self.conn.execute(
            "INSERT INTO binaries (package_name, version, architecture, release, component, status, dependencies, circular)"
            " VALUES ('c', '1.0', 'amd64', 'trixie', 'main', 'current', '[]', 1)"
        )
        upsert_binary(self.conn, "c", "1.0", "amd64", "trixie", "main", "[]")
        row = self.conn.execute(
            "SELECT status, circular FROM binaries WHERE package_name = 'c'"
        ).fetchone()
        self.assertEqual(
            row, ("current", 0),
            "a re-upserted binary must lose its stale circular flag",
        )


class TestPackageLifecycle(unittest.TestCase):
    def setUp(self):
        self.config = _config(fetch_source=False)
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(f"{self.tmp.name}/test.db")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_status_transitions_to_current_and_requested_version_truthful(self):
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="lib1")],
                "lib1": [_binary("lib1")],
            },
        )
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = index
        stats = resolver.resolve("app")
        self.assertEqual(stats.errors, [])
        rows = {
            name: (status, requested_version)
            for name, status, requested_version in self.conn.execute(
                "SELECT name, status, requested_version FROM packages"
            ).fetchall()
        }
        self.assertEqual(rows.get("app"), ("current", None))
        self.assertEqual(
            rows.get("lib1"), ("current", None),
            "a transitive dep must not store the resolved version as its requested version",
        )


class TestLocalIndexInjection(unittest.TestCase):
    """`ensure_indexes()` injects current+verified `local` binaries/sources
    from the DB into the merged index (DB-read only — no index file, no
    fetch), so implicit resolution can see them and `local` shadows the stock
    package at equal version (Clarification Q1)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        make_test_iso.write_pool_files(self.base)
        self.config = ProjectConfig(
            base_dir=self.base,
            binary_cache=f"{self.base}/binary",
            source_cache=f"{self.base}/source",
            iso_dir=f"{self.base}/iso",
            db_path=f"{self.base}/package_mgmt.db",
            log_dir=f"{self.base}/logs",
            fetch_source=True,
            sources=(
                ArchiveSource(
                    mirror="http://example.invalid",
                    release="trixie",
                    components=("main",),
                ),
            ),
        )
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _insert_local_binary(self, name, version, deps=None, verified=1, status="current"):
        self.conn.execute(
            "INSERT INTO binaries (package_name, version, architecture, release, component,"
            " status, verified, dependencies)"
            " VALUES (?, ?, 'amd64', 'trixie', 'local', ?, ?, ?)",
            (name, version, status, verified, deps or "[]"),
        )
        self.conn.commit()

    def _insert_local_source(self, name, version, verified=1):
        self.conn.execute(
            "INSERT INTO sources (package_name, source_version, release, component, verified)"
            " VALUES (?, ?, 'trixie', 'local', ?)",
            (name, version, verified),
        )
        self.conn.commit()

    def _resolver(self, fetch_source=True):
        resolver = PackageResolver(replace(self.config, fetch_source=fetch_source), self.conn)
        resolver.ensure_indexes()
        return resolver

    def test_local_binary_injected_into_merged_index(self):
        self._insert_local_binary(
            "lib1", "2.0-1",
            deps=json.dumps([{"name": "lib2", "type": "depends"}]),
        )
        resolver = self._resolver()
        entries = [e for e in resolver.index.binary_packages.get("lib1", [])]
        local = [e for e in entries if e.component == "local"]
        self.assertEqual(len(local), 1)
        self.assertEqual(local[0].release, "trixie")
        self.assertEqual(local[0].version, "2.0-1")
        self.assertEqual(local[0].depends_raw, "lib2")

    def test_local_source_injected_into_merged_index(self):
        self._insert_local_source("lib1", "2.0-1")
        resolver = self._resolver()
        self.assertIsNotNone(resolver.index.lookup_source("lib1"))
        self.assertEqual(resolver.index.lookup_source("lib1").component, "local")

    def test_unverified_local_row_not_injected(self):
        self._insert_local_binary("lib1", "2.0-1", verified=0)
        resolver = self._resolver()
        local = [e for e in resolver.index.binary_packages.get("lib1", []) if e.component == "local"]
        self.assertEqual(local, [],
                         "a half-imported (unverified) local row must not be injected")

    def test_non_current_local_row_not_injected(self):
        self._insert_local_binary("lib1", "2.0-1", status="superseded")
        resolver = self._resolver()
        local = [e for e in resolver.index.binary_packages.get("lib1", []) if e.component == "local"]
        self.assertEqual(local, [],
                         "a non-current local row must not be injected")

    def test_dependency_resolves_to_same_version_local(self):
        self._insert_local_binary("lib1", "2.0-1")
        resolver = self._resolver()
        stats = resolver.resolve("app")
        self.assertEqual(stats.errors, [])
        row = self.conn.execute(
            "SELECT component FROM binaries WHERE package_name = 'lib1' AND status = 'current'"
        ).fetchone()
        self.assertEqual(row[0], "local",
                         "the same-version local lib1 must shadow the stock main copy")

    def test_newer_upstream_wins_over_older_local(self):
        self._insert_local_binary("lib1", "1.0-0")
        resolver = self._resolver()
        stats = resolver.resolve("lib1")
        self.assertEqual(stats.errors, [])
        row = self.conn.execute(
            "SELECT component FROM binaries WHERE package_name = 'lib1' AND status = 'current'"
        ).fetchone()
        self.assertEqual(row[0], "main",
                         "a strictly newer upstream version must win over an older local one")


if __name__ == "__main__":
    unittest.main()
