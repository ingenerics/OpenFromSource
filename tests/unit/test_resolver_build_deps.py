import sqlite3
import tempfile
import types
import unittest
from unittest import mock

from package_mgmt.lib.model.config import ArchiveSource
from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL
from package_mgmt.lib.model.package import BinaryPackage, SourcePackage
from package_mgmt.lib.model.repository import RepositoryIndex
from package_mgmt.lib.model.resolution import PackageRequest
from package_mgmt.lib.service.resolver import PackageResolver


def _binary(name, version="1.0", depends=None, source_field=None):
    return BinaryPackage(
        package=name, version=version, architecture="amd64",
        release="trixie", component="main",
        depends_raw=depends, provides_raw=None,
        source_field=source_field or name, filename=None, sha256=None,
    )


def _source(name, build_depends, version="1.0"):
    return SourcePackage(
        package=name, version=version, release="trixie", component="main",
        directory=None, files=None, build_depends=build_depends,
    )


class TestBuildDepResolutionDepth(unittest.TestCase):
    """Build-tool resolution is complete regardless of reporting depth."""

    def setUp(self):
        config = types.SimpleNamespace(
            sources=(ArchiveSource(mirror="http://example.invalid", release="trixie", components=("main",)),),
            fetch_source=True,
            max_build_dep_report_depth=2,
            max_source_dep_depth=-1,
        )
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends=None, source_field="app")],
                "bt1": [_binary("bt1", source_field="bt1")],
                "bt2": [_binary("bt2", source_field="bt2")],
                "bt3": [_binary("bt3", source_field="bt3")],
            },
            source_packages={
                "app": [_source("app", "bt1")],
                "bt1": [_source("bt1", "bt2")],
                "bt2": [_source("bt2", "bt3")],
                "bt3": [_source("bt3", None)],
            },
        )
        self.config = config
        self.index = index
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(f"{self.tmp.name}/test.db")
        from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_deep_build_tool_chain_resolves_beyond_report_depth(self):
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = self.index
        resolver.resolve("app")

        links = self.conn.execute(
            """SELECT s.package_name, b.package_name, sbd.build_dep_depth
               FROM source_build_deps sbd
               JOIN sources s ON s.id = sbd.source_id
               JOIN binaries b ON b.id = sbd.binary_id
               ORDER BY sbd.build_dep_depth""",
        ).fetchall()
        by_tool = {b: depth for _, b, depth in links}

        self.assertEqual(by_tool.get("bt1"), 1, "app -> bt1 must be depth 1")
        self.assertEqual(by_tool.get("bt2"), 2, "bt1 -> bt2 must be depth 2")
        self.assertEqual(by_tool.get("bt3"), 3, "bt2 -> bt3 must be depth 3 (previously never resolved)")

    def test_missing_build_tool_creates_no_link(self):
        index = RepositoryIndex(
            binary_packages={"app": [_binary("app", depends=None, source_field="app")]},
            source_packages={"app": [_source("app", "nonexistent-tool-xyz")]},
        )
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = index
        stats = resolver.resolve("app")

        self.assertIn("Build tool not resolved: nonexistent-tool-xyz", stats.errors)
        links = self.conn.execute("SELECT COUNT(*) FROM source_build_deps").fetchone()[0]
        self.assertEqual(links, 0, "no link may be created for an unresolved build tool")


class TestBatchScopedBuildDeps(unittest.TestCase):
    """The build-dep pass processes only sources created in
    the current batch — retained (pre-existing) sources are never re-walked on
    a later `add` run, so the second run's counters count only its own sources."""

    def setUp(self):
        config = types.SimpleNamespace(
            sources=(ArchiveSource(mirror="http://example.invalid", release="trixie", components=("main",)),),
            fetch_source=True,
            max_build_dep_report_depth=2,
            max_source_dep_depth=-1,
        )
        index = RepositoryIndex(
            binary_packages={
                "app1": [_binary("app1", depends=None, source_field="app1")],
                "app2": [_binary("app2", depends=None, source_field="app2")],
                "tool": [_binary("tool", source_field="tool")],
            },
            source_packages={
                "app1": [_source("app1", "tool")],
                "app2": [_source("app2", "tool")],
                "tool": [_source("tool", None)],
            },
        )
        self.config = config
        self.index = index
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(f"{self.tmp.name}/test.db")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_second_run_counters_count_only_its_own_sources(self):
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = self.index
        stats1 = resolver.resolve("app1")
        self.assertEqual(stats1.errors, [])
        self.assertEqual(stats1.build_dep_links, 1, "run 1 links app1's own source to the shared tool")

        resolver2 = PackageResolver(self.config, self.conn)
        resolver2.index = self.index
        stats2 = resolver2.resolve("app2")
        self.assertEqual(stats2.errors, [])
        self.assertEqual(
            stats2.build_dep_links, 1,
            "run 2 must count only app2's own source link — app1's retained source must not be "
            "re-walked (pre-fix this was 2: app1 + app2)",
        )
        self.assertEqual(stats2.build_tools, 1, "the shared tool is the only build tool in run 2's batch")


class TestBatchScopedShallowAfterDeep(unittest.TestCase):
    """A shallower re-run over retained sources resolves ZERO
    new build tools (retention = keep, not keep extending); a fresh batch still
    resolves its full transitive build-tool chain (bounded-query regression)."""

    def _config(self, depth: int):
        return types.SimpleNamespace(
            sources=(ArchiveSource(mirror="http://example.invalid", release="trixie", components=("main",)),),
            fetch_source=True,
            max_source_dep_depth=depth,
        )

    def _chain_index(self) -> RepositoryIndex:
        return RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends=None, source_field="app")],
                "bt1": [_binary("bt1", source_field="bt1")],
                "bt2": [_binary("bt2", source_field="bt2")],
                "bt3": [_binary("bt3", source_field="bt3")],
            },
            source_packages={
                "app": [_source("app", "bt1")],
                "bt1": [_source("bt1", "bt2")],
                "bt2": [_source("bt2", "bt3")],
                "bt3": [_source("bt3", None)],
            },
        )

    def test_shallow_after_deep_resolves_zero_new_build_tools(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(EXPECTED_SCHEMA_SQL)
        index = self._chain_index()

        deep = PackageResolver(self._config(-1), conn)
        deep.index = index
        stats_deep = deep.resolve("app")
        self.assertEqual(stats_deep.errors, [])
        self.assertGreater(stats_deep.build_dep_links, 0, "the deep run resolves the full tool chain")

        shallow = PackageResolver(self._config(1), conn)
        shallow.index = index
        stats_shallow = shallow.resolve("app")
        self.assertEqual(stats_shallow.errors, [])
        self.assertEqual(
            stats_shallow.build_dep_links, 0,
            "a shallow re-run must not re-walk the retained sources' build tools "
            "(pre-fix it re-resolved the whole chain)",
        )
        self.assertEqual(stats_shallow.build_tools, 0)
        conn.close()

    def test_fresh_batch_resolves_full_transitive_chain(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(EXPECTED_SCHEMA_SQL)
        index = self._chain_index()

        resolver = PackageResolver(self._config(-1), conn)
        resolver.index = index
        stats = resolver.resolve("app")
        self.assertEqual(stats.errors, [])

        links = {
            b: depth
            for _, b, depth in conn.execute(
                """SELECT s.package_name, b.package_name, sbd.build_dep_depth
                   FROM source_build_deps sbd
                   JOIN sources s ON s.id = sbd.source_id
                   JOIN binaries b ON b.id = sbd.binary_id"""
            ).fetchall()
        }
        self.assertEqual(
            links, {"bt1": 1, "bt2": 2, "bt3": 3},
            "the bounded query must still expand the transitive build-tool chain",
        )
        conn.close()


class TestBuildToolReuse(unittest.TestCase):
    """A build tool already satisfied must link, not error."""

    def setUp(self):
        config = types.SimpleNamespace(
            sources=(ArchiveSource(mirror="http://example.invalid", release="trixie", components=("main",)),),
            fetch_source=True,
            max_source_dep_depth=-1,
        )
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends=None, source_field="app")],
                "tool": [_binary("tool", source_field="tool")],
            },
            source_packages={
                "app": [_source("app", "tool")],
                "tool": [_source("tool", None)],
            },
        )
        self.config = config
        self.index = index
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(f"{self.tmp.name}/test.db")
        from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _resolved_ids(self):
        return set(
            bid for (bid,) in self.conn.execute("SELECT binary_id FROM source_build_deps").fetchall()
        )

    def test_shared_build_tool_linked_once_for_each_source(self):
        index = RepositoryIndex(
            binary_packages={
                "app1": [_binary("app1", depends=None, source_field="app1")],
                "app2": [_binary("app2", depends=None, source_field="app2")],
                "tool": [_binary("tool", depends=None, source_field="tool")],
            },
            source_packages={
                "app1": [_source("app1", "tool")],
                "app2": [_source("app2", "tool")],
                "tool": [_source("tool", None)],
            },
        )
        resolver = PackageResolver(self.config, self.conn)
        resolver.index = index
        stats = resolver.resolve("app1")
        stats2 = resolver.resolve("app2")

        self.assertEqual(stats.errors, [])
        self.assertEqual(stats2.errors, [],
                         "a build tool already current must not be reported as unresolved")
        links = self.conn.execute(
            "SELECT COUNT(*) FROM source_build_deps sbd JOIN sources s ON s.id = sbd.source_id"
            " WHERE s.package_name IN ('app1', 'app2') AND sbd.binary_id = ("
            "   SELECT id FROM binaries WHERE package_name = 'tool' AND status = 'current'"
            " )"
        ).fetchone()[0]
        self.assertEqual(links, 2, "each source needs its own link to the shared build tool")


class TestSourceDepthBuildTools(unittest.TestCase):
    """At a finite source depth the source/build-tool set is a
    strict subset of the `-1` set, and a boundary build tool (one
    level beyond the in-scope source it serves) resolves as an installable
    binary with its full runtime closure but no source record of its own
    (scoped Constitution III exception)."""

    def _config(self, depth: int):
        return types.SimpleNamespace(
            sources=(ArchiveSource(mirror="http://example.invalid", release="trixie", components=("main",)),),
            fetch_source=True,
            max_source_dep_depth=depth,
        )

    def _resolve(self, index: RepositoryIndex, depth: int):
        conn = sqlite3.connect(":memory:")
        conn.executescript(EXPECTED_SCHEMA_SQL)
        resolver = PackageResolver(self._config(depth), conn)
        resolver.index = index
        stats = resolver.resolve("app")
        sources = sorted(r[0] for r in conn.execute("SELECT package_name FROM sources"))
        tools = sorted(
            r[0] for r in conn.execute(
                """SELECT DISTINCT b.package_name FROM source_build_deps sbd
                   JOIN binaries b ON b.id = sbd.binary_id"""
            )
        )
        binaries = sorted(r[0] for r in conn.execute(
            "SELECT package_name FROM binaries WHERE status = 'current'"))
        conn.close()
        return sources, tools, binaries, stats

    def test_finite_depth_source_set_is_strict_subset_of_unlimited(self):
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="lib1")],
                "lib1": [_binary("lib1", depends="lib2")],
                "lib2": [_binary("lib2")],
                "bt1": [_binary("bt1")],
                "bt2": [_binary("bt2")],
            },
            source_packages={
                "app": [_source("app", "bt1")],
                "lib1": [_source("lib1", "bt2")],
                "lib2": [_source("lib2", None)],
                "bt1": [_source("bt1", None)],
                "bt2": [_source("bt2", None)],
            },
        )
        finite = self._resolve(index, 1)
        unlimited = self._resolve(index, -1)
        finite_sources, finite_tools, finite_binaries, finite_stats = finite
        unlim_sources, unlim_tools, unlim_binaries, unlim_stats = unlimited

        self.assertEqual(finite_stats.errors, [])
        self.assertEqual(unlim_stats.errors, [])
        self.assertTrue(
            set(finite_sources) < set(unlim_sources),
            f"finite source set {finite_sources} must be a strict subset of {unlim_sources}",
        )
        self.assertTrue(
            set(finite_tools) <= set(unlim_tools),
            f"finite build-tool set {finite_tools} must be within {unlim_tools}",
        )
        self.assertEqual(
            set(finite_binaries), set(unlim_binaries),
            "the runtime closure must be identical across depths",
        )
        self.assertEqual(
            set(unlim_sources), {"app", "lib1", "lib2", "bt1", "bt2"},
            "`-1` (unlimited) gives every binary its source",
        )

    def test_boundary_build_tool_resolves_as_binary_without_own_source(self):
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="lib1")],
                "lib1": [_binary("lib1")],
                "bt2": [_binary("bt2", depends="bt2lib")],
                "bt2lib": [_binary("bt2lib")],
            },
            source_packages={
                "app": [_source("app", None)],
                "lib1": [_source("lib1", "bt2")],
            },
        )
        sources, tools, binaries, stats = self._resolve(index, 1)
        self.assertEqual(stats.errors, [])
        # lib1 is at the boundary of the in-scope depth (level 1); its build
        # tool bt2 is one level beyond and gets no source record of its own.
        self.assertEqual(sources, ["app", "lib1"])
        self.assertEqual(tools, ["bt2"], "the in-scope source's build tool must be resolved")
        self.assertEqual(
            set(binaries), {"app", "lib1", "bt2", "bt2lib"},
            "the boundary build tool resolves with its full runtime closure",
        )


class TestBatchRollbackReseedsState(unittest.TestCase):
    """After a mid-segment failure rolls the segment back,
    later segments must re-resolve never-committed packages — never treat
    them as resolved (which would silently skip them)."""

    def setUp(self):
        self.config = types.SimpleNamespace(
            sources=(
                ArchiveSource(mirror="http://example.invalid", release="trixie", components=("main",)),
            ),
            fetch_source=False,
            max_source_dep_depth=-1,
        )
        self.index = RepositoryIndex(
            binary_packages={
                "app1": [_binary("app1", depends="shared")],
                "app2": [_binary("app2", depends="shared")],
                "shared": [_binary("shared")],
            },
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(f"{self.tmp.name}/test.db")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_failed_segment_does_not_poison_later_segments(self):
        import package_mgmt.lib.service.resolver as resolver_module

        real_upsert_binary = resolver_module.upsert_binary
        failed = {"called": False}

        def flaky_upsert(conn, package_name, version, architecture, release, component, dependencies_json):
            if package_name == "shared" and not failed["called"]:
                failed["called"] = True
                raise RuntimeError("simulated upsert failure")
            return real_upsert_binary(
                conn, package_name, version, architecture, release, component, dependencies_json
            )

        resolver = PackageResolver(self.config, self.conn)
        resolver.index = self.index
        with mock.patch.object(resolver_module, "upsert_binary", flaky_upsert):
            stats = resolver.resolve_batch(
                [PackageRequest("app1"), PackageRequest("app2")]
            )

        self.assertTrue(failed["called"], "the flaky upsert must have been triggered")
        self.assertTrue(
            any("simulated upsert failure" in e for e in stats[0].errors),
            f"app1's segment must report the failure, got: {stats[0].errors}",
        )
        self.assertEqual(stats[1].errors, [], "app2's segment must be clean")
        rows = {
            r[0]: r[1] for r in self.conn.execute(
                "SELECT package_name, version FROM binaries WHERE status = 'current'"
            )
        }
        self.assertEqual(
            rows.get("shared"), "1.0",
            "the rolled-back shared dep must be re-resolved by the later segment "
            "(never silently skipped) — got rows: %r" % rows,
        )


if __name__ == "__main__":
    unittest.main()



