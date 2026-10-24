import gzip
import io
import os
import sqlite3
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_iso  # noqa: E402

from package_mgmt.lib.model.config import ArchiveSource
from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL
from package_mgmt.lib.model.package import BinaryPackage, SourcePackage
from package_mgmt.lib.model.repository import RepositoryIndex
from package_mgmt.lib.model.resolution import PackageRequest
from package_mgmt.lib.service.index_loader import load_binary_index
from package_mgmt.lib.service.resolver import PackageResolver
from package_mgmt.lib.util.paths import binary_index_path, source_index_path


def _binary(name, version="1.0", depends=None, release="trixie", component="main"):
    return BinaryPackage(
        package=name, version=version, architecture="amd64",
        release=release, component=component,
        depends_raw=depends, provides_raw=None,
        source_field=name, filename=None, sha256=None,
    )


def _gzip_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(text.encode("utf-8"))
    return buf.getvalue()


def _source(name, version="1.0"):
    return SourcePackage(
        package=name, version=version, release="trixie", component="main",
        directory=None, files=None, build_depends=None,
    )


def _source_config(fetch_source: bool = True, depth: int | None = 0) -> types.SimpleNamespace:
    """Fresh resolver config with the given source depth; `None` leaves the
    key absent (exercises the dataclass-default path in the resolver)."""
    config = types.SimpleNamespace(
        sources=(
            ArchiveSource(mirror="http://example.invalid", release="trixie", components=("main",)),
        ),
        fetch_source=fetch_source,
        max_build_dep_report_depth=2,
    )
    if depth is not None:
        config.max_source_dep_depth = depth
    return config


class BatchResolverTestBase(unittest.TestCase):
    def setUp(self):
        self.config = types.SimpleNamespace(
            sources=(
                ArchiveSource(mirror="http://example.invalid", release="trixie", components=("main",)),
            ),
            fetch_source=False,
            max_build_dep_report_depth=2,
            max_source_dep_depth=-1,
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(f"{self.tmp.name}/test.db")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _resolver(self, index=None):
        resolver = PackageResolver(self.config, self.conn)
        if index is not None:
            resolver.index = index
        return resolver

    def _binary_rows(self, status: str = "current"):
        return self.conn.execute(
            "SELECT package_name, version, status FROM binaries"
            " WHERE status = ? ORDER BY 1, 2",
            (status,),
        ).fetchall()

    def _table_state(self):
        return {
            "binaries": self.conn.execute(
                "SELECT package_name, version, architecture, release, component,"
                " dependencies, status, verified, circular FROM binaries ORDER BY 1, 2"
            ).fetchall(),
            "packages": self.conn.execute(
                "SELECT name, requested_version, status FROM packages ORDER BY 1"
            ).fetchall(),
        }


class CountingResolver(PackageResolver):
    """Records every _resolve_one_inner call to prove the walk-once guarantee."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.inner_calls: list[str] = []

    def _resolve_one_inner(self, name, requested_version, stats, primary_source, version_constraint, level=0):
        self.inner_calls.append(name)
        return super()._resolve_one_inner(name, requested_version, stats, primary_source, version_constraint, level)


class TestWalkOnce(BatchResolverTestBase):
    """The union closure is walked once per batch invocation."""

    def test_shared_dependency_visited_once(self):
        index = RepositoryIndex(
            binary_packages={
                "app1": [_binary("app1", depends="lib1")],
                "app2": [_binary("app2", depends="lib1, lib2")],
                "lib1": [_binary("lib1")],
                "lib2": [_binary("lib2")],
            },
        )
        resolver = CountingResolver(self.config, self.conn)
        resolver.index = index
        stats = resolver.resolve_batch(
            [PackageRequest("app1"), PackageRequest("app2")]
        )
        self.assertEqual([s.errors for s in stats], [[], []])
        calls = resolver.inner_calls
        self.assertEqual(
            len(calls), len(set(calls)),
            f"each package must be visited once, got: {calls}",
        )
        self.assertEqual(set(calls), {"app1", "app2", "lib1", "lib2"})

    def test_compatible_constraints_keep_single_selection(self):
        index = RepositoryIndex(
            binary_packages={
                "app1": [_binary("app1", depends="lib (>= 2.0)")],
                "app2": [_binary("app2", depends="lib (<= 2.5)")],
                "lib": [_binary("lib", "1.0"), _binary("lib", "2.0"), _binary("lib", "2.5")],
            },
        )
        resolver = CountingResolver(self.config, self.conn)
        resolver.index = index
        stats = resolver.resolve_batch(
            [PackageRequest("app1"), PackageRequest("app2")]
        )
        self.assertEqual([s.errors for s in stats], [[], []])
        self.assertEqual(
            self._binary_rows(),
            [
                ("app1", "1.0", "current"),
                ("app2", "1.0", "current"),
                ("lib", "2.5", "current"),
            ],
        )
        self.assertEqual(
            resolver.inner_calls.count("lib"), 1,
            "compatible constraints must not re-walk the shared dep",
        )

    def test_later_constraint_supersedes_and_rewalks_subtree(self):
        index = RepositoryIndex(
            binary_packages={
                "app1": [_binary("app1", depends="lib (>= 2.0)")],
                "app2": [_binary("app2", depends="lib (= 2.0)")],
                "lib": [
                    _binary("lib", "2.0", depends="leaf"),
                    _binary("lib", "2.5", depends="leaf"),
                ],
                "leaf": [_binary("leaf")],
            },
        )
        resolver = CountingResolver(self.config, self.conn)
        resolver.index = index
        stats = resolver.resolve_batch(
            [PackageRequest("app1"), PackageRequest("app2")]
        )
        self.assertEqual([s.errors for s in stats], [[], []])
        rows = {r[0]: r[1] for r in self._binary_rows()}
        self.assertEqual(rows["lib"], "2.0",
                         "a later constraint must supersede to a version satisfying all")
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM binaries WHERE status = 'superseded'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            resolver.inner_calls.count("leaf"), 1,
            "the subtree must be walked once even across the supersede",
        )

    def test_unsatisfiable_intersection_falls_back_to_later_constraint(self):
        """Mutually exclusive minimums (e.g. debhelper >> 13.x vs << 10.x):
        no version satisfies all, so the new constraint wins (last-writer),
        matching sequential resolution — and no error is reported."""
        index = RepositoryIndex(
            binary_packages={
                "app1": [_binary("app1", depends="lib (>= 2.0)")],
                "app3": [_binary("app3", depends="lib (= 1.0)")],
                "lib": [_binary("lib", "1.0"), _binary("lib", "2.5")],
            },
        )
        resolver = self._resolver(index)
        stats = resolver.resolve_batch(
            [PackageRequest("app1"), PackageRequest("app3")]
        )
        self.assertEqual([s.errors for s in stats], [[], []])
        rows = {r[0]: r[1] for r in self._binary_rows()}
        self.assertEqual(rows["lib"], "1.0",
                         "the later constraint must win when no version satisfies all")
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM binaries WHERE status = 'superseded'"
            ).fetchone()[0],
            1,
        )

    def test_unsatisfiable_single_constraint_errors(self):
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="lib (= 9.9)")],
                "lib": [_binary("lib", "1.0"), _binary("lib", "2.5")],
            },
        )
        resolver = self._resolver(index)
        stats = resolver.resolve_batch([PackageRequest("app")])[0]
        self.assertTrue(
            any("lib" in e and "(= 9.9)" in e for e in stats.errors),
            f"expected unsatisfiable-constraint error, got {stats.errors}",
        )

    def test_pin_requests_supersede_in_order(self):
        index = RepositoryIndex(
            binary_packages={
                "lib": [_binary("lib", "1.0"), _binary("lib", "2.5")],
            },
        )
        resolver = self._resolver(index)
        stats = resolver.resolve_batch(
            [PackageRequest("lib", "1.0"), PackageRequest("lib", "2.5")]
        )
        self.assertEqual([s.errors for s in stats], [[], []])
        rows = {r[0]: r[1] for r in self._binary_rows()}
        self.assertEqual(rows["lib"], "2.5", "the later pin must supersede")
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM binaries WHERE status = 'superseded'"
            ).fetchone()[0],
            1,
        )


class TestSupersededVersionReRequest(BatchResolverTestBase):
    """Re-requesting a previously superseded version
    restores it as the single current binary; history preserved; sync_mask
    untouched (data-model.md invariant)."""

    def _lib_index(self):
        return RepositoryIndex(
            binary_packages={
                "lib": [_binary("lib", "1.0"), _binary("lib", "2.5")],
            },
        )

    def _resolve_seq(self, versions):
        resolver = self._resolver(self._lib_index())
        for v in versions:
            stats = resolver.resolve_batch([PackageRequest("lib", v)])
            self.assertEqual(stats[0].errors, [], f"resolve {v} failed")

    def test_rerequest_superseded_version_restores_single_current(self):
        self._resolve_seq(["1.0", "2.5", "1.0"])
        current = self._binary_rows("current")
        superseded = self._binary_rows("superseded")
        self.assertEqual(current, [("lib", "1.0", "current")])
        self.assertEqual(superseded, [("lib", "2.5", "superseded")])
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM binaries WHERE package_name = 'lib'"
            ).fetchone()[0],
            2,
            "both versions keep their history",
        )

    def test_sync_mask_survives_rerequest(self):
        self._resolve_seq(["1.0", "2.5"])
        self.conn.execute(
            "UPDATE binaries SET sync_mask = 2 WHERE package_name = 'lib' AND version = '1.0'"
        )
        self.conn.commit()
        self._resolve_seq(["1.0"])
        row = self.conn.execute(
            "SELECT status, sync_mask FROM binaries"
            " WHERE package_name = 'lib' AND version = '1.0'"
        ).fetchone()
        self.assertEqual(row, ("current", 2))

    def test_plain_upgrade_path_unchanged(self):
        self._resolve_seq(["1.0", "2.5"])
        current = self._binary_rows("current")
        superseded = self._binary_rows("superseded")
        self.assertEqual(current, [("lib", "2.5", "current")])
        self.assertEqual(superseded, [("lib", "1.0", "superseded")])


class TestBuildErrorAttribution(BatchResolverTestBase):
    """Build-dep errors attach once per batch (first segment)."""

    def _resolver_with_sources(self):
        from package_mgmt.lib.model.package import SourcePackage

        def _source(name, build_depends, version="1.0"):
            return SourcePackage(
                package=name, version=version, release="trixie", component="main",
                directory=None, files=None, build_depends=build_depends,
            )

        self.config.fetch_source = True
        index = RepositoryIndex(
            binary_packages={
                "app1": [_binary("app1", depends=None, )],
                "app2": [_binary("app2", depends=None)],
                "tool": [_binary("tool")],
            },
            source_packages={
                "app1": [_source("app1", "ghost-tool")],
                "app2": [_source("app2", "tool")],
            },
        )
        return self._resolver(index)

    def test_single_request_keeps_build_errors_in_stats(self):
        resolver = self._resolver_with_sources()
        stats = resolver.resolve_batch([PackageRequest("app1")])[0]
        self.assertTrue(any("ghost-tool" in e for e in stats.errors))

    def test_multi_request_reports_build_errors_once(self):
        resolver = self._resolver_with_sources()
        stats = resolver.resolve_batch(
            [PackageRequest("app1"), PackageRequest("app2")]
        )
        self.assertEqual(stats[0].errors.count("Build tool not resolved: ghost-tool"), 1)
        self.assertEqual(
            [e for e in stats[1].errors if "ghost-tool" in e], [],
            "build errors must not poison later segments",
        )
        links = self.conn.execute(
            "SELECT count(*) FROM source_build_deps"
        ).fetchone()[0]
        self.assertEqual(links, 1, "only app2's tool must be linked")


class TestBatchParity(BatchResolverTestBase):
    """resolve_batch produces the same final state as sequential resolve."""

    def _build_index(self):
        return RepositoryIndex(
            binary_packages={
                "app1": [_binary("app1", depends="lib1")],
                "app2": [_binary("app2", depends="lib1, lib2")],
                "lib1": [_binary("lib1")],
                "lib2": [_binary("lib2")],
            },
        )

    def test_batch_matches_sequential_final_state(self):
        index = self._build_index()
        resolver = self._resolver(index)
        stats = resolver.resolve_batch(
            [PackageRequest("app1"), PackageRequest("app2")]
        )
        self.assertEqual([s.errors for s in stats], [[], []])
        batch_state = self._table_state()

        self.conn.execute("DELETE FROM binaries")
        self.conn.execute("DELETE FROM packages")
        self.conn.commit()

        resolver = self._resolver(index)
        resolver.resolve("app1")
        resolver.resolve("app2")
        sequential_state = self._table_state()

        self.assertEqual(batch_state, sequential_state)

    def test_resolve_delegates_to_resolve_batch(self):
        index = self._build_index()
        resolver = self._resolver(index)
        single = resolver.resolve("app1")
        batch = self._resolver(index).resolve_batch([PackageRequest("app1")])[0]
        self.assertEqual(single.packages, batch.packages)
        self.assertEqual(single.binaries, batch.binaries)
        self.assertEqual(single.sources, batch.sources)
        self.assertEqual(single.errors, batch.errors)
        self.assertEqual(single.resolved_source, batch.resolved_source)

    def test_per_name_error_attribution(self):
        index = RepositoryIndex(
            binary_packages={
                "good": [_binary("good")],
                "bad": [_binary("bad", depends="ghost")],
            },
        )
        resolver = self._resolver(index)
        stats = resolver.resolve_batch(
            [PackageRequest("good"), PackageRequest("bad")]
        )
        self.assertEqual(stats[0].errors, [])
        self.assertTrue(any("ghost" in e for e in stats[1].errors))

    def test_failed_dep_reported_once_per_segment(self):
        """Two edges to the same unresolvable dep within one segment must
        produce one error, not one per edge (sequential parity)."""
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="p1, p2")],
                "p1": [_binary("p1", depends="ghost")],
                "p2": [_binary("p2", depends="ghost")],
            },
        )
        resolver = self._resolver(index)
        stats = resolver.resolve_batch([PackageRequest("app")])[0]
        self.assertEqual(
            stats.errors.count("Virtual package 'ghost' has no providers in the index"),
            1,
            f"one error per failed dep per segment, got: {stats.errors}",
        )
        # A later segment still reports its own attempt (per-name attribution).
        index2 = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="p1, p2")],
                "p1": [_binary("p1", depends="ghost")],
                "p2": [_binary("p2", depends="ghost")],
                "other": [_binary("other", depends="ghost")],
            },
        )
        resolver = self._resolver(index2)
        stats = resolver.resolve_batch(
            [PackageRequest("app"), PackageRequest("other")]
        )
        total = sum(1 for s in stats for e in s.errors if "ghost" in e)
        self.assertEqual(total, 2, f"one error per segment, got: {total}")

    def test_interrupt_mid_batch_converges_on_rerun(self):
        index = RepositoryIndex(
            binary_packages={
                "app1": [_binary("app1", depends="lib1")],
                "app2": [_binary("app2", depends="lib1, lib2")],
                "lib1": [_binary("lib1")],
                "lib2": [_binary("lib2")],
            },
        )

        def _interrupt(done, total):
            if done == 1:
                raise KeyboardInterrupt

        resolver = self._resolver(index)
        with self.assertRaises(KeyboardInterrupt):
            resolver.resolve_batch(
                [PackageRequest("app1"), PackageRequest("app2")], progress=_interrupt
            )
        resolver.resolve_batch([PackageRequest("app1"), PackageRequest("app2")])
        converged = self._table_state()

        self.conn.execute("DELETE FROM binaries")
        self.conn.execute("DELETE FROM packages")
        self.conn.commit()
        self._resolver(index).resolve_batch(
            [PackageRequest("app1"), PackageRequest("app2")]
        )
        reference = self._table_state()
        self.assertEqual(
            converged, reference,
            "an interrupted batch must converge to the same state as a clean run",
        )

    def test_rerun_adds_no_link_rows(self):
        index = self._build_index()
        resolver = self._resolver(index)
        resolver.resolve_batch([PackageRequest("app1"), PackageRequest("app2")])
        first = self.conn.execute("SELECT count(*) FROM binary_to_source").fetchone()[0]
        resolver.resolve_batch([PackageRequest("app1"), PackageRequest("app2")])
        second = self.conn.execute("SELECT count(*) FROM binary_to_source").fetchone()[0]
        self.assertEqual(
            second, first,
            "re-runs must not add binary_to_source rows (uq_binary_to_source)",
        )


class TestSourceDepth(BatchResolverTestBase):
    """Per-binary level accounting — source records are
    created only for binaries within `max_source_dep_depth`, while the runtime
    `binaries` closure is byte-identical across `-1`/`0`/`1`/`N`."""

    def _chain_index(self):
        return RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="lib1")],
                "lib1": [_binary("lib1", depends="lib2")],
                "lib2": [_binary("lib2", depends="lib3")],
                "lib3": [_binary("lib3")],
            },
            source_packages={
                "app": [_source("app")],
                "lib1": [_source("lib1")],
                "lib2": [_source("lib2")],
                "lib3": [_source("lib3")],
            },
        )

    def _resolve_chain(self, depth: int | None = None, fetch_source: bool = True):
        config = _source_config(fetch_source=fetch_source, depth=depth)
        resolver = PackageResolver(config, self.conn)
        resolver.index = self._chain_index()
        stats = resolver.resolve("app")
        self.assertEqual(stats.errors, [])
        sources = sorted(r[0] for r in self.conn.execute("SELECT package_name FROM sources"))
        binaries = sorted(r[0] for r in self.conn.execute(
            "SELECT package_name FROM binaries WHERE status = 'current'"))
        return sources, binaries

    def test_level_accounting_limits_sources_to_depth(self):
        sources, binaries = self._resolve_chain(depth=1)
        self.assertEqual(sources, ["app", "lib1"],
                         "levels 0 and 1 are in scope; deeper levels get no source record")
        self.assertEqual(binaries, ["app", "lib1", "lib2", "lib3"],
                         "the runtime closure is never truncated")

    def test_runtime_closure_identical_across_depths(self):
        closures = {}
        for depth in (-1, 0, 1, 2):
            closures[depth] = self._resolve_chain(depth=depth)[1]
        self.assertEqual(
            len({tuple(c) for c in closures.values()}), 1,
            f"runtime closure must be byte-identical across depths, got: {closures}",
        )

    def test_depth_zero_fetches_no_sources_even_when_enabled(self):
        sources, binaries = self._resolve_chain(depth=0, fetch_source=True)
        self.assertEqual(sources, [])
        self.assertEqual(binaries, ["app", "lib1", "lib2", "lib3"])

    def test_depth_unlimited_equals_pre_feature_full_source_behavior(self):
        sources, binaries = self._resolve_chain(depth=-1, fetch_source=True)
        self.assertEqual(sources, ["app", "lib1", "lib2", "lib3"],
                         "every binary in the closure has its source")
        self.assertEqual(binaries, ["app", "lib1", "lib2", "lib3"])

    def test_regression_matrix_setting_a(self):
        """Setting A: `max_source_dep_depth` x `[fetch] source` rows.

        The source/build-tool set at a finite depth is a strict subset of the
        `-1` set, and `0` fetches nothing. The depth-0 warning
        is a CLI-level concern, tested at the command layer."""
        cases = [
            # (depth, fetch_source, expected in-scope source names)
            (None, False, []),                         # unset key -> default 0 + source=false: binaries only
            (-1, True, ["app", "lib1", "lib2", "lib3"]),  # -1+true: unlimited — full source coverage
            (-1, False, []),                           # -1+false: binaries only
            (0, True, []),                             # 0+true: nothing fetched (warning is CLI-level)
            (0, False, []),                            # 0+false: regular_user presets
            (1, True, ["app", "lib1"]),                # 1+true: requested set + direct runtime deps
            (2, True, ["app", "lib1", "lib2"]),        # N>1 finite: within N levels
        ]
        for depth, fetch_source, expected in cases:
            with self.subTest(depth=depth, fetch_source=fetch_source):
                sources, binaries = self._resolve_chain(depth=depth, fetch_source=fetch_source)
                self.assertEqual(sources, expected)
                self.assertEqual(binaries, ["app", "lib1", "lib2", "lib3"],
                                 "the runtime closure must never be truncated")
                self.conn.execute("DELETE FROM sources")
                self.conn.execute("DELETE FROM binary_to_source")
                self.conn.commit()

    def test_rerun_shallower_retains_fetched_sources(self):
        """The cap bounds new resolution only — sources fetched under
        an unlimited cap are retained (never pruned) on a shallower re-run."""
        self._resolve_chain(depth=-1, fetch_source=True)
        first = sorted(r[0] for r in self.conn.execute("SELECT package_name FROM sources"))
        self._resolve_chain(depth=0, fetch_source=True)
        second = sorted(r[0] for r in self.conn.execute("SELECT package_name FROM sources"))
        self.assertEqual(second, first,
                         "re-running with a shallower depth must not prune retained sources")


class TestIndexFetchFailure(BatchResolverTestBase):
    """Index-fetch failures surface a clear message, not a raw
    FileNotFoundError from load_binary_index/load_source_index."""

    def setUp(self):
        super().setUp()
        self.config.binary_cache = os.path.join(self.tmp.name, "binary")
        self.config.source_cache = os.path.join(self.tmp.name, "source")
        self.config.sources = (
            ArchiveSource(mirror="http://127.0.0.1:1", release="trixie", components=("main",)),
        )

    def test_missing_unfetchable_index_raises_clear_message(self):
        resolver = PackageResolver(self.config, self.conn)
        with self.assertRaises(Exception) as ctx:
            resolver.ensure_indexes()
        self.assertNotIsInstance(ctx.exception, FileNotFoundError)
        self.assertIn("trixie/main", str(ctx.exception))
        self.assertIn("index", str(ctx.exception).lower())


class TestSourceIndexGating(BatchResolverTestBase):
    """Source indexes are fetched/loaded only when source fetching is
    enabled; switching from a source-disabled to a source-enabled profile
    re-fetches the never-downloaded indexes."""

    def setUp(self):
        super().setUp()
        self.config.binary_cache = os.path.join(self.tmp.name, "binary")
        self.config.source_cache = os.path.join(self.tmp.name, "source")

    def _cached_src_index(self) -> str:
        return source_index_path(self.config.source_cache, "trixie", "main")

    def test_fetch_source_false_skips_source_fetch_and_load_with_unreachable_sources(self):
        # Binary index cached; source index absent; mirror unreachable. A
        # source-index fetch would raise, so success proves no source fetch.
        make_test_iso.write_archive(self.tmp.name)
        os.remove(self._cached_src_index())
        self.config.fetch_source = False
        self.config.sources = (
            ArchiveSource(mirror="http://127.0.0.1:1", release="trixie", components=("main",)),
        )

        resolver = PackageResolver(self.config, self.conn)
        index = resolver.ensure_indexes()

        self.assertGreater(sum(len(v) for v in index.binary_packages.values()), 0)
        self.assertEqual(index.source_packages, {})

    def test_fetch_source_true_fetches_and_loads_source_indexes(self):
        fixture = os.path.join(self.tmp.name, "fixture")
        make_test_iso.write_archive(fixture)
        make_test_iso.write_source_index_for_mirror(fixture)
        server = make_test_iso.serve_pool(fixture, port=0)
        try:
            self.config.fetch_source = True
            self.config.sources = (
                ArchiveSource(
                    mirror=f"http://127.0.0.1:{server.server_address[1]}",
                    release="trixie", components=("main",),
                ),
            )
            resolver = PackageResolver(self.config, self.conn)
            index = resolver.ensure_indexes()
        finally:
            server.shutdown()
            server.thread.join()
        self.assertGreater(sum(len(v) for v in index.binary_packages.values()), 0)
        self.assertGreater(sum(len(v) for v in index.source_packages.values()), 0)

    def test_profile_switch_from_source_disabled_refetches_source_indexes(self):
        fixture = os.path.join(self.tmp.name, "fixture")
        make_test_iso.write_archive(fixture)
        make_test_iso.write_source_index_for_mirror(fixture)
        server = make_test_iso.serve_pool(fixture, port=0)
        try:
            mirror = f"http://127.0.0.1:{server.server_address[1]}"
            self.config.fetch_source = False
            self.config.sources = (
                ArchiveSource(mirror=mirror, release="trixie", components=("main",)),
            )
            first = PackageResolver(self.config, self.conn)
            index = first.ensure_indexes()
            self.assertGreater(sum(len(v) for v in index.binary_packages.values()), 0)
            self.assertEqual(index.source_packages, {})
            self.assertFalse(
                os.path.exists(self._cached_src_index()),
                "no source index may be fetched under source=false",
            )

            self.config.fetch_source = True
            second = PackageResolver(self.config, self.conn)
            index2 = second.ensure_indexes()
            self.assertGreater(sum(len(v) for v in index2.source_packages.values()), 0)
            self.assertTrue(
                os.path.exists(self._cached_src_index()),
                "the never-fetched source index must be fetched after the switch",
            )
        finally:
            server.shutdown()
            server.thread.join()


class TestIndexSelfHeal(BatchResolverTestBase):
    """A corrupted or truncated cached index is a cache miss: it is removed
    and re-fetched, so the next run succeeds instead of crash-looping. Index
    downloads are persisted atomically — a failed or dropped fetch never
    leaves a half-written index that blocks a retry."""

    def setUp(self):
        super().setUp()
        self.config.binary_cache = os.path.join(self.tmp.name, "binary")
        self.config.source_cache = os.path.join(self.tmp.name, "source")
        self.config.fetch_source = False

    def _cached_bin_index(self) -> str:
        return binary_index_path(self.config.binary_cache, "trixie", "main")

    def _configure(self, mirror: str):
        self.config.sources = (
            ArchiveSource(mirror=mirror, release="trixie", components=("main",)),
        )

    def _full_package_names(self) -> set[str]:
        return set(make_test_iso.BASE_PACKAGES[i][0] for i in range(5))

    def test_connection_dropped_mid_index_self_heals_on_retry(self):
        fixture = os.path.join(self.tmp.name, "fixture")
        make_test_iso.write_archive(fixture)
        drop_path = "/dists/trixie/main/binary-amd64/Packages.gz"
        server = make_test_iso.serve_index_drop(fixture, port=0, drop_paths=[drop_path])
        try:
            self._configure(f"http://127.0.0.1:{server.server_address[1]}")
            index = PackageResolver(self.config, self.conn).ensure_indexes()
        finally:
            server.shutdown()
            server.thread.join()
        self.assertEqual(
            set(index.binary_packages), self._full_package_names(),
            "the truncated fetch must self-heal to the complete index "
            "within the same run",
        )
        self.assertTrue(os.path.exists(self._cached_bin_index()))
        self.assertFalse(
            os.path.exists(self._cached_bin_index() + ".part"),
            "no half-written index may remain in the cache",
        )
        self.assertEqual(
            set(load_binary_index(self._cached_bin_index(), "trixie", "main")),
            self._full_package_names(),
            "the cached index must be the complete, readable re-fetch",
        )

    def test_truncated_cached_index_is_cache_miss_and_refetched(self):
        fixture = os.path.join(self.tmp.name, "fixture")
        make_test_iso.write_archive(fixture)
        server = make_test_iso.serve_pool(fixture, port=0)
        try:
            self._configure(f"http://127.0.0.1:{server.server_address[1]}")
            cached = self._cached_bin_index()
            full = make_test_iso.archive_index_bytes(5)["main"][0]
            os.makedirs(os.path.dirname(cached), exist_ok=True)
            with open(cached, "wb") as f:
                f.write(full[: len(full) // 2])
            for _ in range(2):  # no crash loop across repeated runs
                index = PackageResolver(self.config, self.conn).ensure_indexes()
                self.assertEqual(
                    set(index.binary_packages), self._full_package_names()
                )
            self.assertFalse(os.path.exists(cached + ".part"))
            self.assertEqual(
                set(load_binary_index(cached, "trixie", "main")),
                self._full_package_names(),
                "the truncated file must be replaced by the fetched index",
            )
        finally:
            server.shutdown()
            server.thread.join()


class TestPreDependsClosure(BatchResolverTestBase):
    """A Pre-Depends target not duplicated in Depends lands in the
    resolved runtime closure exactly once; a target in both fields is not
    duplicated. The index is loaded through `load_binary_index` so the merge
    is exercised end-to-end."""

    def _index(self, packages_gz_text: str):
        path = os.path.join(self.tmp.name, "pre-dep.Packages.gz")
        with open(path, "wb") as f:
            f.write(_gzip_bytes(packages_gz_text))
        return load_binary_index(path, "trixie", "main")

    def test_pre_depends_only_target_in_closure(self):
        text = (
            "Package: app\nVersion: 1.0\nArchitecture: amd64\n"
            "Depends: a\nPre-Depends: a, b\n"
            "Filename: pool/main/a/app/app_1.0_amd64.deb\nSize: 1\nSHA256: 00\n\n"
            "Package: a\nVersion: 1.0\nArchitecture: amd64\n"
            "Filename: pool/main/a/a/a_1.0_amd64.deb\nSize: 1\nSHA256: 00\n\n"
            "Package: b\nVersion: 1.0\nArchitecture: amd64\n"
            "Filename: pool/main/b/b/b_1.0_amd64.deb\nSize: 1\nSHA256: 00\n"
        )
        index = RepositoryIndex(binary_packages=self._index(text))
        resolver = self._resolver(index)
        stats = resolver.resolve_batch([PackageRequest("app")])
        self.assertEqual([s.errors for s in stats], [[]])
        rows = self._binary_rows()
        self.assertEqual(
            [r[0] for r in rows],
            ["a", "app", "b"],
            "the duplicated target appears once and the Pre-Depends-only target is included",
        )

    def test_pre_depends_only_target_across_batch(self):
        text = (
            "Package: app1\nVersion: 1.0\nArchitecture: amd64\n"
            "Pre-Depends: shared, prelib\n"
            "Filename: pool/main/a/app1/app1_1.0_amd64.deb\nSize: 1\nSHA256: 00\n\n"
            "Package: app2\nVersion: 1.0\nArchitecture: amd64\n"
            "Depends: shared\n"
            "Filename: pool/main/a/app2/app2_1.0_amd64.deb\nSize: 1\nSHA256: 00\n\n"
            "Package: shared\nVersion: 1.0\nArchitecture: amd64\n"
            "Filename: pool/main/s/shared/shared_1.0_amd64.deb\nSize: 1\nSHA256: 00\n\n"
            "Package: prelib\nVersion: 1.0\nArchitecture: amd64\n"
            "Filename: pool/main/p/prelib/prelib_1.0_amd64.deb\nSize: 1\nSHA256: 00\n"
        )
        index = RepositoryIndex(binary_packages=self._index(text))
        resolver = self._resolver(index)
        stats = resolver.resolve_batch([PackageRequest("app1"), PackageRequest("app2")])
        self.assertEqual([s.errors for s in stats], [[], []])
        self.assertEqual(
            [r[0] for r in self._binary_rows()],
            ["app1", "app2", "prelib", "shared"],
        )


class TestDiamondMinLevelSourceSet(BatchResolverTestBase):
    """A binary's in-scope source support is decided by
    its MINIMUM reachable level, not its first-visit level. The diamond
    `app` Depends `c1, x`; `c1` Depends `x` first visits `x` at level 2
    (via c1) and later at level 1 (via app) — `x` must still gain its
    source at depth 1. The source set must be identical regardless of the
    `Depends`-line ordering."""

    def _diamond_index(self, app_depends="c1, x"):
        return RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends=app_depends)],
                "c1": [_binary("c1", depends="x, deep")],
                "x": [_binary("x")],
                "deep": [_binary("deep", depends="deeper")],
                "deeper": [_binary("deeper")],
            },
            source_packages={
                "app": [_source("app")],
                "c1": [_source("c1")],
                "x": [_source("x")],
                "deep": [_source("deep")],
                "deeper": [_source("deeper")],
            },
        )

    def _resolve_diamond(self, depth: int, app_depends="c1, x"):
        config = _source_config(fetch_source=True, depth=depth)
        resolver = PackageResolver(config, self.conn)
        resolver.index = self._diamond_index(app_depends)
        stats = resolver.resolve("app")
        self.assertEqual(stats.errors, [])
        return sorted(r[0] for r in self.conn.execute("SELECT package_name FROM sources"))

    def test_depth_one_includes_min_level_binary(self):
        sources = self._resolve_diamond(depth=1)
        self.assertEqual(
            sources, ["app", "c1", "x"],
            "x, first visited at level 2 via c1, must gain its source when its "
            "minimum level 1 is in scope",
        )

    def test_depth_zero_fetches_nothing(self):
        self.assertEqual(self._resolve_diamond(depth=0), [])

    def test_depth_n_keeps_binaries_within_n_levels(self):
        self.assertEqual(
            self._resolve_diamond(depth=2), ["app", "c1", "deep", "x"],
            "level 2 (deep) is in scope at depth 2; level 3 (deeper) is not",
        )

    def test_depth_unlimited_keeps_every_source(self):
        self.assertEqual(
            self._resolve_diamond(depth=-1), ["app", "c1", "deep", "deeper", "x"],
        )

    def test_min_level_set_is_order_independent(self):
        forward = self._resolve_diamond(depth=1, app_depends="c1, x")
        self.conn.execute("DELETE FROM sources")
        self.conn.execute("DELETE FROM binary_to_source")
        self.conn.commit()
        reversed_ = self._resolve_diamond(depth=1, app_depends="x, c1")
        self.assertEqual(
            forward, reversed_,
            "the in-scope source set must be independent of the Depends-line "
            "ordering",
        )


class TestAlternativeGroupResolution(BatchResolverTestBase):
    """A request whose alternative dependency group resolves succeeds with no
    error attributed to the group — even when the first alternative is
    absent. An error is recorded only when every member of the group fails,
    and per-segment error attribution is preserved."""

    def test_group_satisfied_by_later_alternative_is_clean(self):
        """`a | b` with only `b` present -> empty error list, closure
        contains `b` (the absent first alternative leaves no error behind)."""
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="ghost | realpkg")],
                "realpkg": [_binary("realpkg")],
            },
        )
        resolver = self._resolver(index)
        stats = resolver.resolve_batch([PackageRequest("app")])[0]
        self.assertEqual(stats.errors, [])
        names = [r[0] for r in self._binary_rows()]
        self.assertIn("realpkg", names, "the resolving alternative is in the closure")
        self.assertNotIn("ghost", names)

    def test_group_satisfied_by_third_alternative_is_clean(self):
        """`a | b | c` satisfied by a later alternative -> clean."""
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="a | b | c")],
                "c": [_binary("c")],
            },
        )
        resolver = self._resolver(index)
        stats = resolver.resolve_batch([PackageRequest("app")])[0]
        self.assertEqual(stats.errors, [])
        names = [r[0] for r in self._binary_rows()]
        self.assertIn("c", names)

    def test_whole_group_failure_reports_exactly_one_error(self):
        """`a | b` with **both** absent -> exactly one group error, the
        request is genuinely failed."""
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="a | b")],
            },
        )
        resolver = self._resolver(index)
        stats = resolver.resolve_batch([PackageRequest("app")])[0]
        self.assertEqual(
            len(stats.errors), 1,
            f"exactly one error for a fully-failed group, got: {stats.errors}",
        )
        self.assertIn("Alternative group", stats.errors[0])

    def test_mixed_batch_attributes_errors_only_to_failing_segment(self):
        """A satisfied group next to a genuinely failing segment
        attributes errors only to the failing segment."""
        index = RepositoryIndex(
            binary_packages={
                "good": [_binary("good", depends="ghost | realpkg")],
                "bad": [_binary("bad", depends="ghost")],
                "realpkg": [_binary("realpkg")],
            },
        )
        resolver = self._resolver(index)
        stats = resolver.resolve_batch(
            [PackageRequest("good"), PackageRequest("bad")]
        )
        self.assertEqual(stats[0].errors, [], "the satisfied group segment is clean")
        self.assertEqual(len(stats[1].errors), 1, "only the failing segment errors")

    def test_alternative_subtree_errors_are_not_hidden(self):
        """When an alternative resolves but has a genuinely failing
        hard dep of its own, that error is kept (it is not attributed to the
        group). Only the failed members' own errors are retracted."""
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="ghost | realpkg")],
                "realpkg": [_binary("realpkg", depends="missing-lib")],
            },
        )
        resolver = self._resolver(index)
        stats = resolver.resolve_batch([PackageRequest("app")])[0]
        self.assertEqual(len(stats.errors), 1, f"got: {stats.errors}")
        self.assertIn("missing-lib", stats.errors[0])

    def test_alternative_unsatisfied_constraint_errors_and_never_links(self):
        """`foo (= 9.9) | bar (= 2.0)` with only `bar 1.0` in the index
        fails the group — `bar 1.0` does not satisfy its own `(= 2.0)` — and
        no silent `bar 1.0` link is registered."""
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="foo (= 9.9) | bar (= 2.0)")],
                "bar": [_binary("bar", "1.0")],
            },
        )
        resolver = self._resolver(index)
        stats = resolver.resolve_batch([PackageRequest("app")])[0]
        self.assertEqual(len(stats.errors), 1, f"got: {stats.errors}")
        self.assertIn("Alternative group", stats.errors[0])
        names = [r[0] for r in self._binary_rows()]
        self.assertNotIn("bar", names, "an unsatisfying version must not be linked silently")

    def test_alternative_satisfied_under_its_own_constraint_links(self):
        """`foo | bar (= 2.0)` with `bar 2.0` in the index is satisfied
        via `bar` resolved under its own constraint."""
        index = RepositoryIndex(
            binary_packages={
                "app": [_binary("app", depends="foo | bar (= 2.0)")],
                "bar": [_binary("bar", "2.0")],
            },
        )
        resolver = self._resolver(index)
        stats = resolver.resolve_batch([PackageRequest("app")])[0]
        self.assertEqual(stats.errors, [])
        names = [r[0] for r in self._binary_rows()]
        self.assertIn("bar", names, "the satisfying alternative is in the closure")
        self.assertNotIn("foo", names)


if __name__ == "__main__":
    unittest.main()
