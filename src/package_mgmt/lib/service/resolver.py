import json
import logging
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from package_mgmt.lib.model.dependency import (
    DependencyGraph,
    DependencySpec,
    BuildDependencySpec,
    deserialize_depends,
    parse_build_depends_field,
    provider_satisfies_constraint,
    serialize_build_depends,
    serialize_depends,
)
from package_mgmt.lib.model.package import BinaryPackage, SourcePackage
from package_mgmt.lib.model.repository import RepositoryIndex
from package_mgmt.lib.model.resolution import PackageRequest, ProviderCandidate, ResolutionStats
from package_mgmt.lib.service.index_loader import (
    INDEX_READ_ERRORS,
    _format_dep_spec,
    fetch_index,
    load_binary_index,
    load_source_index,
    release_base_for,
)
from package_mgmt.lib.util.paths import binary_index_path, source_index_path, index_path_xz, resolve_index_path
from package_mgmt.lib.util.versions import (
    satisfies_constraint,
    source_version_from_binary,
    version_compare,
)

logger = logging.getLogger(__name__)

# Safety cap on the transitive build-tool expansion (spec 007): resolution
# runs to completion in practice; this only guards against pathological
# dependency loops.
MAX_BUILD_DEP_ITERATIONS = 100

# Level used when re-seeding batch state after a segment rollback: committed
# names are treated as seen at the deepest possible level, so any later
# in-scope re-encounter triggers the (idempotent) source-repair path.
_MIN_LEVEL_SENTINEL = 10**9


@dataclass
class _SegmentStats:
    """Mutable per-segment accumulator (replaces the untyped `dict`).

    `errors` stays a mutable list so the retraction sites (index marks) can
    `del stats.errors[start:end]` when a later constraint supersedes a choice.
    """

    packages: int = 0
    binaries: int = 0
    sources: int = 0
    build_tools: int = 0
    build_dep_links: int = 0
    virtual_count: int = 0
    fallback_packages: int = 0
    errors: list[str] = field(default_factory=list)


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _load_or_fetch_index(
    cache_path: str,
    url_base: str,
    release_base: str,
    loader,
    release: str,
    component: str,
    mirror: str,
) -> dict:
    """Return the parsed index for cache_path, fetching it when the cached
    copy is absent or cannot be read.

    A cached index that fails to load (e.g. truncated) is a cache miss: it is
    removed and re-fetched, so a poisoned cache self-heals instead of
    crash-looping. A failed fetch deletes its partial destination so a
    half-written index is never preferred. Raises RuntimeError when the mirror
    cannot provide a readable index."""
    path = resolve_index_path(cache_path)
    if not os.path.exists(path):
        path = None
    for _ in range(3):
        if path is None:
            for ext in ("gz", "xz"):
                url = f"{url_base}.{ext}"
                dest = cache_path if ext == "gz" else index_path_xz(cache_path)
                try:
                    fetch_index(url, dest, release_base=release_base)
                    path = dest
                    break
                except Exception:
                    _remove_file(dest)
                    continue
            if path is None:
                break
        try:
            return loader(path, release, component)
        except INDEX_READ_ERRORS:
            _remove_file(path)
            path = None
    raise RuntimeError(
        f"Index fetch failed for {release}/{component} from {mirror}; "
        "the index is part of the cache — restore or re-fetch it"
    )


def upsert_package(conn: sqlite3.Connection, name: str, requested_version: str | None, status: str = "pending") -> None:
    conn.execute(
        """INSERT INTO packages (name, requested_version, status)
           VALUES (?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET
               requested_version = COALESCE(NULLIF(?, ''), requested_version),
               status = ?""",
        (name, requested_version, status, requested_version, status),
    )


def _find_current_binary(conn: sqlite3.Connection, package_name: str, architecture: str) -> tuple[int, str] | None:
    cursor = conn.execute(
        "SELECT id, version FROM binaries WHERE package_name = ? AND architecture = ? AND status = 'current'"
        " ORDER BY id",
        (package_name, architecture),
    )
    return cursor.fetchone()


def supersede_current_binary(
    conn: sqlite3.Connection,
    package_name: str,
    architecture: str,
    version: str,
    release: str,
    component: str,
    dependencies_json: str,
) -> int:
    """Register `version` as the current binary for `(package_name,
    architecture)`, superseding any prior current row of a different version.

    Exactly one `current` row can exist per package/architecture: an existing
    current row of the same version and component is reused (idempotent
    re-registration), a same-version row of a different component is
    superseded (a `local` import overrides an upstream row at the same
    version), and a different version marks the old row `superseded` — its
    payload is retained in the content-addressed pool (still checksummed on
    sync, so a lagging snapshot referencing it keeps installing) — before the
    new current row is upserted. Returns the id of the current row.
    """
    existing = _find_current_binary(conn, package_name, architecture)
    if existing:
        existing_id, existing_version = existing
        if existing_version == version:
            row = conn.execute(
                "SELECT component FROM binaries WHERE id = ?", (existing_id,)
            ).fetchone()
            existing_component = row[0] if row else None
            if existing_component == component:
                return existing_id
        conn.execute(
            "UPDATE binaries SET status = 'superseded' WHERE id = ?",
            (existing_id,),
        )
    return upsert_binary(
        conn, package_name, version, architecture, release, component, dependencies_json,
    )


def upsert_binary(
    conn: sqlite3.Connection,
    package_name: str,
    version: str,
    architecture: str,
    release: str,
    component: str,
    dependencies_json: str,
) -> int:
    cursor = conn.execute(
        """INSERT INTO binaries (package_name, version, architecture, release, component, dependencies, verified)
           VALUES (?, ?, ?, ?, ?, ?, 0)
           ON CONFLICT(package_name, version, architecture, release, component) DO UPDATE SET
               dependencies = excluded.dependencies,
               verified = 0,
               status = 'current',
               circular = 0
           RETURNING id""",
        (package_name, version, architecture, release, component, dependencies_json),
    )
    return cursor.fetchone()[0]


def upsert_source(
    conn: sqlite3.Connection,
    package_name: str,
    source_version: str | None,
    release: str,
    component: str,
    build_depends_json: str | None = None,
) -> tuple[int, bool]:
    """Upsert a source row, returning `(source_id, inserted)`.

    `inserted` is True when the row was newly created and False when an
    existing row was `ON CONFLICT`-updated (a retained source). The caller
    uses it to keep the current batch's source set."""
    existing = conn.execute(
        "SELECT 1 FROM sources"
        " WHERE package_name = ? AND source_version = ? AND release = ? AND component = ?",
        (package_name, source_version, release, component),
    ).fetchone()
    inserted = existing is None
    cursor = conn.execute(
        """INSERT INTO sources (package_name, source_version, release, component, verified, build_depends)
           VALUES (?, ?, ?, ?, 0, ?)
           ON CONFLICT(package_name, source_version, release, component) DO UPDATE SET
               verified = 0,
               build_depends = COALESCE(excluded.build_depends, build_depends)
           RETURNING id""",
        (package_name, source_version, release, component, build_depends_json),
    )
    return cursor.fetchone()[0], inserted


def link_binary_to_source(conn: sqlite3.Connection, binary_id: int, source_id: int) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO binary_to_source (binary_id, source_id)
           VALUES (?, ?)""",
        (binary_id, source_id),
    )


def link_source_build_dep(conn: sqlite3.Connection, source_id: int, binary_id: int, build_dep_depth: int = 0) -> None:
    conn.execute(
        """INSERT INTO source_build_deps (source_id, binary_id, build_dep_depth)
           VALUES (?, ?, ?)
           ON CONFLICT(source_id, binary_id) DO UPDATE SET
               build_dep_depth = MIN(build_dep_depth, excluded.build_dep_depth)""",
        (source_id, binary_id, build_dep_depth),
    )


def parse_source_field(source_field: str | None, default_name: str) -> tuple[str, str | None]:
    if not source_field:
        return default_name, None
    m = re.match(r"^([^(]+)(?:\s*\(([^)]+)\))?\s*$", source_field)
    if m:
        src_name = m.group(1).strip()
        src_version = m.group(2)
        return src_name, src_version
    return source_field.strip(), None


def check_virtual_resolution(conn: sqlite3.Connection, virtual_name: str) -> int | None:
    cursor = conn.execute(
        "SELECT resolved_binary_id FROM virtual_resolutions WHERE virtual_name = ?",
        (virtual_name,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def query_provides(conn: sqlite3.Connection, virtual_name: str) -> list[ProviderCandidate]:
    rows = conn.execute(
        "SELECT package_name, version, architecture, release, component, provides_version"
        " FROM provides WHERE virtual_name = ?",
        (virtual_name,),
    ).fetchall()
    return [
        ProviderCandidate(
            package_name=r[0], version=r[1], architecture=r[2],
            release=r[3], component=r[4], provides_version=r[5],
        )
        for r in rows
    ]


def record_virtual_resolution(conn: sqlite3.Connection, virtual_name: str, resolved_binary_id: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO virtual_resolutions (virtual_name, resolved_binary_id) VALUES (?, ?)",
        (virtual_name, resolved_binary_id),
    )


def select_provider(
    candidates: list[ProviderCandidate],
    visited: set[str],
    primary_source: str | None,
    version_constraint: dict | None,
    index: RepositoryIndex,
) -> ProviderCandidate | None:
    filtered: list[tuple[ProviderCandidate, int]] = []

    for cand in candidates:
        if not provider_satisfies_constraint(
            version_constraint, cand.provides_version, cand.version
        ):
            continue

        priority = 0
        if primary_source and cand.release == primary_source:
            priority += 10
        if cand.package_name in visited:
            priority += 5
        filtered.append((cand, priority))

    if not filtered:
        return None

    filtered.sort(key=lambda x: (-x[1], x[0].package_name))
    return filtered[0][0]


class PackageResolver:
    def __init__(self, config, conn: sqlite3.Connection):
        self.config = config
        self.conn = conn
        self.index: RepositoryIndex | None = None
        self._visited: set[str] = set()
        self._ancestry: set[str] = set()
        self._batch_registered: dict[str, str] = {}
        self._batch_constraints: dict[str, list] = {}
        self._batch_levels: dict[str, int] = {}
        self._segment_failed: set[str] = set()
        self._top_level_source: tuple[str, str] | None = None
        self._batch_inserted_sources: set[int] = set()

    def ensure_indexes(self) -> RepositoryIndex:
        if self.index is not None:
            return self.index

        merged_binary: dict[str, list] = {}
        merged_source: dict[str, list] = {}

        for src in self.config.sources:
            mirror = src.mirror.rstrip("/")
            release_base = release_base_for(src.mirror, src.release)
            for comp in src.components:
                bin_gz_path = binary_index_path(self.config.binary_cache, src.release, comp)
                src_gz_path = source_index_path(self.config.source_cache, src.release, comp)

                bin_url_base = f"{mirror}/dists/{src.release}/{comp}/binary-amd64/Packages"
                binary_pkgs = _load_or_fetch_index(
                    bin_gz_path, bin_url_base, release_base,
                    load_binary_index, src.release, comp, mirror,
                )
                for name, pkgs in binary_pkgs.items():
                    merged_binary.setdefault(name, []).extend(pkgs)

                if self.config.fetch_source:
                    src_url_base = f"{mirror}/dists/{src.release}/{comp}/source/Sources"
                    source_pkgs = _load_or_fetch_index(
                        src_gz_path, src_url_base, release_base,
                        load_source_index, src.release, comp, mirror,
                    )
                    for name, pkgs in source_pkgs.items():
                        merged_source.setdefault(name, []).extend(pkgs)

        self._inject_local_records(merged_binary, merged_source)

        self.index = RepositoryIndex(
            binary_packages=merged_binary,
            source_packages=merged_source,
        )

        total_binaries = sum(len(v) for v in merged_binary.values())
        total_sources = sum(len(v) for v in merged_source.values())
        logger.info("Indexes loaded: %d binary packages, %d source packages (%d sources configured)",
                     total_binaries, total_sources, len(self.config.sources))
        return self.index

    def _inject_local_records(
        self,
        merged_binary: dict[str, list],
        merged_source: dict[str, list],
    ) -> None:
        """Append current+verified `local` binaries/sources from the DB to the
        merged index (DB-read only — no index file, no fetch).

        `local` has no archive index, so without this the resolver's
        `lookup_binary*`/`lookup_source` could never see a locally patched
        package. Injection makes the same-version component tie-break
        effective (`local` is the highest-priority component); a strictly
        newer upstream version still wins. Half-imported rows (unverified or
        non-current) are never injected.
        """
        if not self.config.sources:
            return
        default_release = self.config.sources[0].release
        rows = self.conn.execute(
            "SELECT package_name, version, architecture, dependencies"
            " FROM binaries WHERE status = 'current' AND component = 'local'"
            " AND verified = 1"
        ).fetchall()
        for name, version, arch, deps_json in rows:
            depends_raw = None
            if deps_json:
                depends_raw = ", ".join(
                    _format_dep_spec(spec) for spec in deserialize_depends(deps_json)
                )
            entry = BinaryPackage(
                package=name,
                version=version,
                architecture=arch,
                release=default_release,
                component="local",
                depends_raw=depends_raw,
                provides_raw=self._local_provides_raw(name, version, arch, default_release),
                source_field=None,
                filename=None,
                sha256=None,
            )
            merged_binary.setdefault(name, []).append(entry)

        srows = self.conn.execute(
            "SELECT package_name, source_version"
            " FROM sources WHERE component = 'local' AND verified = 1"
        ).fetchall()
        for name, version in srows:
            entry = SourcePackage(
                package=name,
                version=version,
                release=default_release,
                component="local",
                directory=None,
                files=None,
                build_depends=None,
            )
            merged_source.setdefault(name, []).append(entry)

    def _local_provides_raw(
        self, name: str, version: str, arch: str, release: str
    ) -> str | None:
        """Reconstruct the `Provides:` field for a `local` binary from the
        `provides` table (the record's own metadata — no index file)."""
        rows = self.conn.execute(
            "SELECT virtual_name, provides_version FROM provides"
            " WHERE package_name = ? AND version = ? AND architecture = ?"
            " AND release = ? AND component = 'local'",
            (name, version, arch, release),
        ).fetchall()
        if not rows:
            return None
        parts = []
        for virtual_name, provides_version in rows:
            if provides_version:
                parts.append(f"{virtual_name} (= {provides_version})")
            else:
                parts.append(virtual_name)
        return ", ".join(parts)

    def resolve(
        self, name: str, requested_version: str | None = None, source: str | None = None,
    ) -> ResolutionStats:
        """Resolve a single request — a batch of one (Phase 9)."""
        return self.resolve_batch([PackageRequest(name, requested_version)], source)[0]

    def resolve_batch(
        self,
        requests: list[PackageRequest],
        source: str | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[ResolutionStats]:
        """Resolve a batch of requests in a single shared walk (Phase 9).

        The union dependency closure of all requests is walked ONCE (shared
        _visited): each package is registered once per batch, with per-package
        incoming constraint collection (a later constraint that invalidates an
        earlier selection triggers a supersede + subtree re-walk; when no
        version satisfies every accumulated constraint — mutually exclusive
        minimums like debhelper — the later constraint wins, sequential
        last-writer semantics). Per-name commits checkpoint progress
        (interrupted runs converge). The build-dep pass runs once for the whole
        batch; its counters feed every segment's stats while its errors attach
        to the first segment only (single-name batches keep exact `add`
        output; multi-request batches report each build error once). Returns
        one ResolutionStats per request, in order.
        """
        if self.index is None:
            self.ensure_indexes()

        primary_source = source or self.config.sources[0].release
        self._visited.clear()
        self._ancestry.clear()
        self._batch_registered.clear()
        self._batch_constraints.clear()
        self._batch_levels.clear()
        self._segment_failed = set()
        self._batch_inserted_sources.clear()

        segments: list[dict] = []
        total = len(requests)
        for i, req in enumerate(requests, 1):
            stats = _SegmentStats()
            self._top_level_source = None
            self._segment_failed = set()
            try:
                self._resolve_one(req.name, req.requested_version, stats, primary_source)
                self.conn.commit()
            except Exception as e:
                self.conn.rollback()
                self._re_seed_batch_state_from_db()
                stats.errors.append(str(e))
            resolved_source: str | None = None
            if self._top_level_source:
                resolved_source = (
                    f"{self._top_level_source[0]}/{self._top_level_source[1]}"
                )
            else:
                resolved_source = self._lookup_resolved_source(req.name)
            segments.append({"stats": stats, "resolved_source": resolved_source})
            if progress:
                progress(i, total)

        if self.config.fetch_source:
            build_stats = _SegmentStats()
            self._segment_failed = set()
            self._resolve_build_deps(build_stats, primary_source)
            self.conn.commit()
            # Counters: the batch's single build-dep pass feeds every segment,
            # mirroring sequential per-name behavior (each name's line carried
            # the full global build-dep totals).
            for segment in segments:
                for key in (
                    "packages", "binaries", "sources", "build_tools",
                    "build_dep_links", "virtual_count", "fallback_packages",
                ):
                    setattr(
                        segment["stats"], key,
                        getattr(segment["stats"], key) + getattr(build_stats, key),
                    )
            # Errors: attributed to the FIRST segment only. For single-name
            # batches this preserves add's exact output (build-tool errors
            # appear in the name's error list, per the 007 contract); for
            # multi-request batches each build error is reported once instead
            # of once per name, so per-name "resolved" counts reflect the
            # name's own closure (per the 008 contract summary shape).
            if segments and build_stats.errors:
                segments[0]["stats"].errors.extend(build_stats.errors)

        return [
            ResolutionStats(
                packages=s["stats"].packages,
                binaries=s["stats"].binaries,
                sources=s["stats"].sources,
                build_tools=s["stats"].build_tools,
                build_dep_links=s["stats"].build_dep_links,
                virtual_count=s["stats"].virtual_count,
                errors=s["stats"].errors,
                resolved_source=s["resolved_source"],
                fallback_count=s["stats"].fallback_packages,
            )
            for s in segments
        ]

    def _lookup_resolved_source(self, name: str) -> str | None:
        """Source of a request's current binary (or virtual provider)."""
        row = self.conn.execute(
            "SELECT release, component FROM binaries"
            " WHERE package_name = ? AND status = 'current' ORDER BY id LIMIT 1",
            (name,),
        ).fetchone()
        if row:
            return f"{row[0]}/{row[1]}"
        vr = self.conn.execute(
            "SELECT b.release, b.component FROM virtual_resolutions vr"
            " JOIN binaries b ON b.id = vr.resolved_binary_id"
            " WHERE vr.virtual_name = ?",
            (name,),
        ).fetchone()
        if vr:
            return f"{vr[0]}/{vr[1]}"
        return None

    def _source_in_scope(self, level: int) -> bool:
        """Whether a binary at runtime level `level` receives
        source/build-tool support.

        `max_source_dep_depth`: `-1` = unlimited (every binary gets a source),
        `0` = none (no sources at all), positive `N` = binaries within `N`
        levels of the requested set (level 0 = requested, level 1 = direct
        runtime deps, ...). The runtime binary closure is never bounded."""
        depth = getattr(self.config, "max_source_dep_depth", 0)
        if depth == -1:
            return True
        if depth == 0:
            return False
        return level <= depth

    def _min_level_repair_enabled(self) -> bool:
        """Whether the minimum-level source repair machinery is active.

        Applies whenever source fetching is on — any non-zero
        `max_source_dep_depth`. At `0` (regular_user) no sources are fetched,
        so there is nothing to repair. At `-1` (graybeard) and positive `N`
        the repair paths must run: `_ensure_source` is itself scope- and
        idempotence-gated, so the outer gate only has to allow it — including
        at `-1`, where a provider first cached under a source-less profile
        must still gain its source when the same virtual is later reached."""
        depth = getattr(self.config, "max_source_dep_depth", 0)
        return isinstance(depth, int) and depth != 0

    def _register_source_for(
        self, binary_entry: BinaryPackage, binary_id: int,
        stats: _SegmentStats, primary_source: str | None, level: int,
    ) -> None:
        """Source/build-tool registration for an in-scope binary
        (extracted from `_resolve_one_inner`). All upserts are idempotent and
        the `binary_to_source` link uses INSERT OR IGNORE, so re-running on a
        min-level upgrade or cached-virtual re-encounter is safe."""
        if not (self.config.fetch_source and self._source_in_scope(level)):
            return

        src_name, declared_version = parse_source_field(binary_entry.source_field, binary_entry.package)
        expected_version = declared_version or source_version_from_binary(binary_entry.version)

        src_entry = None
        if self.index is not None:
            # Exact (name, version) in the binary's own release+component,
            # then the primary release (when different), then anywhere.
            src_entry = self.index.lookup_source_exact(
                src_name, expected_version, binary_entry.release, binary_entry.component
            )
            if src_entry is None and primary_source and primary_source != binary_entry.release:
                src_entry = self.index.lookup_source_exact(src_name, expected_version, primary_source)
            if src_entry is None:
                src_entry = self.index.lookup_source_exact(src_name, expected_version)
            # Newest-in-release fallback — divergence is never silent.
            if src_entry is None:
                src_entry = self.index.lookup_source_by_source(src_name, binary_entry.release)
                if src_entry is None:
                    src_entry = self.index.lookup_source(src_name)
                if src_entry is not None:
                    logger.warning(
                        "Source %s %s not found exactly; linking newest available %s "
                        "(%s/%s) for %s %s",
                        src_name, expected_version, src_entry.version,
                        src_entry.release, src_entry.component,
                        binary_entry.package, binary_entry.version,
                    )

        if src_entry:
            src_version = src_entry.version
        else:
            src_version = expected_version

        build_deps_json: str | None = None
        if src_entry:
            all_specs: list[BuildDependencySpec] = []
            if src_entry.build_depends:
                all_specs.extend(parse_build_depends_field(src_entry.build_depends, "build"))
            if src_entry.build_depends_arch:
                all_specs.extend(parse_build_depends_field(src_entry.build_depends_arch, "build-arch"))
            if src_entry.build_depends_indep:
                all_specs.extend(parse_build_depends_field(src_entry.build_depends_indep, "build-indep"))
            if all_specs:
                build_deps_json = serialize_build_depends(all_specs)

        src_release = src_entry.release if src_entry else binary_entry.release
        src_component = src_entry.component if src_entry else binary_entry.component
        src_id, inserted = upsert_source(self.conn, src_name, src_version, src_release, src_component, build_deps_json)
        if inserted:
            # The build-dep pass processes only sources created by
            # the current batch; retained sources are never re-walked.
            self._batch_inserted_sources.add(src_id)
        stats.sources += 1
        link_binary_to_source(self.conn, binary_id, src_id)

    def _lookup_binary_entry(
        self, package_name: str, version: str, release: str, primary_source: str | None,
    ) -> BinaryPackage | None:
        """Find the index entry for a registered binary (used by the source
        repair path). Exact version wins; the binary's stored release is
        preferred, then the primary source, then any entry."""
        if self.index is None:
            return None
        entries = self.index.binary_packages.get(package_name, [])
        for e in entries:
            if e.version == version and e.release == release:
                return e
        for e in entries:
            if e.version == version:
                return e
        if primary_source:
            e = self.index.lookup_binary_by_source(package_name, primary_source)
            if e is not None and e.version == version:
                return e
        return self.index.lookup_binary(package_name)

    def _ensure_source(
        self, binary_id: int, level: int, stats: _SegmentStats, primary_source: str | None,
    ) -> None:
        """Register the source for an in-scope binary that has none yet.

        Used on min-level upgrades and cached-virtual
        re-encounters. Idempotent via the `binary_to_source` link guard."""
        if not (self.config.fetch_source and self._source_in_scope(level)):
            return
        if self.conn.execute(
            "SELECT 1 FROM binary_to_source WHERE binary_id = ?", (binary_id,)
        ).fetchone():
            return
        row = self.conn.execute(
            "SELECT id, package_name, version, release, component FROM binaries WHERE id = ?",
            (binary_id,),
        ).fetchone()
        if not row:
            return
        _bid, pkg, ver, rel, comp = row
        entry = self._lookup_binary_entry(pkg, ver, rel, primary_source)
        if entry is None:
            return
        self._register_source_for(entry, binary_id, stats, primary_source, level)

    def _maybe_repair_min_level(
        self, name: str, level: int, stats: _SegmentStats, primary_source: str | None,
    ) -> None:
        """A name re-encountered at a strictly smaller in-scope level
        re-runs source registration — a binary first visited out of scope must
        gain its source when later reached in scope. Developer profile only."""
        if not self._min_level_repair_enabled():
            return
        min_level = self._batch_levels.get(name)
        if min_level is None or not (level < min_level):
            return
        self._batch_levels[name] = level
        row = self.conn.execute(
            "SELECT id FROM binaries WHERE package_name = ? AND status = 'current' LIMIT 1",
            (name,),
        ).fetchone()
        if row:
            self._ensure_source(row[0], level, stats, primary_source)

    def _re_seed_batch_state_from_db(self) -> None:
        """After a segment rollback, rebuild the batch's
        in-memory state from the DB so later segments never treat
        never-committed names as resolved (a rollback discards the segment's
        writes but not `_visited`/`_batch_registered`)."""
        self._visited.clear()
        self._batch_registered.clear()
        self._batch_constraints.clear()
        self._batch_levels.clear()
        rows = self.conn.execute(
            "SELECT package_name, version FROM binaries WHERE status = 'current'"
        ).fetchall()
        for pkg, ver in rows:
            self._visited.add(pkg)
            self._batch_registered[pkg] = ver
            if self._min_level_repair_enabled():
                self._batch_levels[pkg] = _MIN_LEVEL_SENTINEL

    @staticmethod
    def _alt_names(entry: dict) -> list[str]:
        """Alternative names from a build-dep JSON entry — dict-form (new)
        and string-form (legacy) alternatives both handled."""
        return [name for name, _ in PackageResolver._alt_specs(entry)]

    @staticmethod
    def _alt_specs(entry: dict) -> list[tuple[str, dict | None]]:
        """(name, version_constraint) pairs for a build-dep JSON entry's
        alternatives, handling dict-form and string-form elements."""
        result = []
        for a in entry.get("alternatives", ()):
            if isinstance(a, dict):
                result.append((a.get("name", "").split(":")[0], a.get("version_constraint")))
            else:
                result.append((a.split(":")[0], None))
        return result

    def _group_satisfying_member(
        self,
        all_names: list[str],
        name_constraints: dict[str, dict | None],
        *,
        virtual_leg: str,
    ) -> tuple[int, str] | None:
        """Best constraint-wise-satisfying alternative-group member as
        `(binary_id, version)`, or None.

        Direct binaries are matched by name and their current version must
        satisfy that member's own constraint. The virtual leg is selected by
        `virtual_leg`:
        - "provides": the runtime probe's provides-join — any current provider
          of the virtual satisfies the group, over-inclusive, NOT constraint-
          checked (deliberately preserved).
        - "resolutions": the build-dep link-block leg — the recorded
          virtual_resolutions provider's version IS constraint-checked.

        Shared by the runtime "already satisfied" probe, the build-dep twin
        probe, and the build-dep link block so the three cannot drift.
        """
        for cand_name in all_names:
            candidate = self.conn.execute(
                "SELECT id, version FROM binaries"
                " WHERE package_name = ? AND status = 'current' LIMIT 1",
                (cand_name,),
            ).fetchone()
            if candidate and satisfies_constraint(candidate[1], name_constraints.get(cand_name)):
                return (candidate[0], candidate[1])
            if virtual_leg == "provides":
                virtual = self.conn.execute(
                    "SELECT b.id, b.version FROM provides p JOIN binaries b"
                    " ON b.package_name = p.package_name"
                    " WHERE p.virtual_name = ? AND b.status = 'current' LIMIT 1",
                    (cand_name,),
                ).fetchone()
                if virtual:
                    return (virtual[0], virtual[1])
            elif virtual_leg == "resolutions":
                virtual = self.conn.execute(
                    "SELECT vr.resolved_binary_id, b.version FROM virtual_resolutions vr"
                    " JOIN binaries b ON b.id = vr.resolved_binary_id"
                    " WHERE vr.virtual_name = ? LIMIT 1",
                    (cand_name,),
                ).fetchone()
                if virtual and satisfies_constraint(virtual[1], name_constraints.get(cand_name)):
                    return (virtual[0], virtual[1])
        return None

    def _resolve_build_deps(self, stats: _SegmentStats, primary_source: str | None = None) -> None:
        # Phase 9: the batch keeps the shared _visited set — the runtime pass's
        # registrations are reused (walk once), so this runs once per batch.
        build_tools_seen: set[str] = set()
        max_iterations = MAX_BUILD_DEP_ITERATIONS
        processed_ids: set[int] = set()
        # Build tools are placed beyond any finite source depth, so at a finite
        # depth they resolve as installable binaries (with their full runtime
        # closures) but get no source record of their own (Constitution III
        # scoped exception); at `-1` (unlimited) they are in scope like
        # everything else.
        depth = getattr(self.config, "max_source_dep_depth", 0)
        build_tool_level = depth + 1 if depth != -1 else 0

        iteration = 0
        while True:
            # Process only sources created by the current batch —
            # retained (pre-existing) sources are never re-walked. Sources
            # registered during the pass (a build tool's own source) join
            # `_batch_inserted_sources`, so the `while True` expansion keeps
            # resolving the full transitive build-tool chain.
            batch_ids = self._batch_inserted_sources - processed_ids
            if not batch_ids:
                break
            placeholders = ",".join("?" * len(batch_ids))
            cursor = self.conn.execute(
                f"SELECT id, build_depends FROM sources"
                f" WHERE id IN ({placeholders}) AND build_depends IS NOT NULL",
                tuple(batch_ids),
            )
            pending = cursor.fetchall()
            if not pending:
                break

            logger.debug("_resolve_build_deps iteration %d: %d pending sources", iteration, len(pending))

            for src_id, build_depends_json in pending:
                processed_ids.add(src_id)
                try:
                    bd_entries = json.loads(build_depends_json)
                except (json.JSONDecodeError, TypeError):
                    continue
                for entry in bd_entries:
                    bd_primary = entry.get("name", "").split(":")[0]
                    bd_alts = self._alt_names(entry)
                    all_names = [bd_primary] + bd_alts
                    name_constraints = {bd_primary: entry.get("version_constraint")}
                    for alt_name, alt_vc in self._alt_specs(entry):
                        name_constraints[alt_name] = alt_vc
                    entry_error_mark = len(stats.errors)
                    bd_binary_id = self._resolve_one(
                        bd_primary, None, stats, primary_source, entry.get("version_constraint"),
                        level=build_tool_level,
                    )
                    if bd_binary_id is None and bd_alts:
                        any_satisfied = (
                            self._group_satisfying_member(
                                all_names, name_constraints, virtual_leg="resolutions"
                            )
                            is not None
                        )
                        if not any_satisfied:
                            for alt_name, alt_vc in self._alt_specs(entry):
                                alt_mark = len(stats.errors)
                                bd_binary_id = self._resolve_one(
                                    alt_name, None, stats, primary_source, alt_vc, level=build_tool_level
                                )
                                if bd_binary_id is not None:
                                    # This alternative resolved: drop the failed
                                    # primary's and earlier alternatives' errors,
                                    # keep this member's own subtree errors.
                                    del stats.errors[entry_error_mark:alt_mark]
                                    break
                    link_depth = iteration + 1
                    if bd_binary_id is not None:
                        link_source_build_dep(self.conn, src_id, bd_binary_id, link_depth)
                        stats.build_dep_links += 1
                    else:
                        satisfied = self._group_satisfying_member(
                            all_names, name_constraints, virtual_leg="resolutions"
                        )
                        if satisfied:
                            link_source_build_dep(self.conn, src_id, satisfied[0], link_depth)
                            stats.build_dep_links += 1
                            del stats.errors[entry_error_mark:]
                        else:
                            label = " | ".join(all_names)
                            logger.warning("Build tool not resolved: %s (required by source %d)", label, src_id)
                            stats.errors.append(f"Build tool not resolved: {label}")
                    if bd_primary not in build_tools_seen:
                        build_tools_seen.add(bd_primary)
                        stats.build_tools += 1

            iteration += 1
            if iteration >= max_iterations:
                logger.warning(
                    "Build-tool resolution safety cap reached (%d iterations); some build deps may be unresolved",
                    max_iterations,
                )
                break

    def _resolve_one(
        self, name: str, requested_version: str | None, stats: _SegmentStats,
        primary_source: str | None = None, version_constraint: dict | None = None,
        level: int = 0,
    ) -> int | None:
        if version_constraint is not None:
            constraints = self._batch_constraints.setdefault(name, [])
            if version_constraint not in constraints:
                constraints.append(version_constraint)

        registered = self._batch_registered.get(name)
        if name in self._visited and registered is not None:
            if requested_version:
                if registered == requested_version:
                    self._maybe_repair_min_level(name, level, stats, primary_source)
                    return None
            elif version_constraint is None or satisfies_constraint(registered, version_constraint):
                self._maybe_repair_min_level(name, level, stats, primary_source)
                return None
            else:
                # A later constraint invalidates the earlier selection: pick a
                # version satisfying EVERY constraint collected for the
                # package, superseding + re-walking its subtree.
                if self.index is not None and name in self.index.binary_packages:
                    chosen = self._select_satisfying_all(
                        name, self._batch_constraints.get(name, []), primary_source
                    )
                    if chosen is None:
                        # No version satisfies every accumulated constraint
                        # (real archives carry mutually exclusive minimums,
                        # e.g. debhelper >> 13.x vs << 10.x): fall back to the
                        # new constraint alone — sequential last-writer
                        # semantics — instead of failing the batch.
                        chosen = self.index.lookup_binary_satisfying(
                            name, version_constraint, primary_source
                        )
                    if chosen is None or chosen.version == registered:
                        if chosen is None and name not in self._segment_failed:
                            self._segment_failed.add(name)
                            op = version_constraint.get("operator", ">=") if version_constraint else ">="
                            target = version_constraint.get("version", "") if version_constraint else ""
                            stats.errors.append(
                                f"No version of {name} satisfies constraint ({op} {target})"
                            )
                        return None
                    version_constraint = {"operator": "=", "version": chosen.version}
                # virtual packages re-select through the inner path with the
                # new constraint (sequential last-writer semantics)
            self._visited.discard(name)
            self._ancestry.discard(name)
        elif name in self._visited:
            # Failed in an earlier segment: retry once per segment (per-name
            # error attribution like sequential). Repeat edges within one
            # segment are skipped silently — the failed attempt is recorded
            # in _segment_failed right after inner returns (below).
            if name in self._segment_failed:
                return None
            self._visited.discard(name)

        self._visited.add(name)
        self._ancestry.add(name)
        try:
            result = self._resolve_one_inner(
                name, requested_version, stats, primary_source, version_constraint, level
            )
        finally:
            self._ancestry.discard(name)
        if result is None and self._batch_registered.get(name) is None:
            # Failed: report the error once per segment (repeat edges skip).
            self._segment_failed.add(name)
        return result

    def _select_satisfying_all(
        self, name: str, constraints: list, primary_source: str | None,
    ):
        """Best index entry for name whose version satisfies every constraint."""
        if self.index is None:
            return None
        candidates = []
        for c in constraints:
            entry = self.index.lookup_binary_satisfying(name, c, primary_source)
            if entry is not None and not any(entry.version == x.version for x in candidates):
                candidates.append(entry)
        for entry in candidates:
            if all(c is None or satisfies_constraint(entry.version, c) for c in constraints):
                return entry
        return None

    def _resolve_one_inner(
        self, name: str, requested_version: str | None, stats: _SegmentStats,
        primary_source: str | None, version_constraint: dict | None,
        level: int = 0,
    ) -> int | None:
        if self.index is None:
            return None

        if requested_version:
            entries = self.index.binary_packages.get(name, [])
            binary_entry = None
            for e in entries:
                if e.version == requested_version:
                    if primary_source:
                        if e.release == primary_source:
                            binary_entry = e
                            break
                    else:
                        binary_entry = e
                        break
            if binary_entry is None:
                stats.errors.append(f"Version {requested_version} not found for {name}")
                return None
        else:
            if primary_source:
                binary_entry = self.index.lookup_binary_by_source(name, primary_source)
                if not binary_entry:
                    binary_entry = self.index.lookup_binary(name)
            else:
                binary_entry = self.index.lookup_binary(name)

        if binary_entry and not satisfies_constraint(binary_entry.version, version_constraint):
            op = version_constraint.get("operator", ">=") if version_constraint else ">="
            target = version_constraint.get("version", "") if version_constraint else ""
            binary_entry = self.index.lookup_binary_satisfying(name, version_constraint, primary_source)
            if binary_entry is None:
                constraint_candidates = query_provides(self.conn, name)
                if constraint_candidates:
                    binary_entry = None
                else:
                    stats.errors.append(
                        f"No version of {name} satisfies constraint ({op} {target})"
                    )
                    return None

        _is_virtual_resolution = False
        if not binary_entry:
            candidates = query_provides(self.conn, name)
            if not candidates:
                stats.errors.append(f"Virtual package '{name}' has no providers in the index")
                return None

            resolved_id = check_virtual_resolution(self.conn, name)
            if resolved_id is not None:
                cached_row = self.conn.execute(
                    "SELECT package_name, version, release, status FROM binaries WHERE id = ?",
                    (resolved_id,),
                ).fetchone()
                if cached_row:
                    cached_pkg, cached_ver, cached_rel, cached_status = cached_row
                    self._batch_registered[name] = cached_ver
                    cached_usable = cached_status == "current"
                    if cached_usable and version_constraint:
                        cached_usable = satisfies_constraint(cached_ver, version_constraint)
                    if cached_usable and primary_source and cached_rel != primary_source:
                        fresh_candidates = query_provides(self.conn, name)
                        fresh_selected = select_provider(
                            fresh_candidates, self._visited, primary_source, version_constraint, self.index,
                        )
                        if fresh_selected:
                            if (
                                fresh_selected.package_name != cached_pkg
                                or fresh_selected.version != cached_ver
                                or fresh_selected.release != cached_rel
                            ):
                                candidates = fresh_candidates
                                resolved_id = None
                            else:
                                if self._min_level_repair_enabled():
                                    self._ensure_source(resolved_id, level, stats, primary_source)
                                return resolved_id
                        else:
                            if self._min_level_repair_enabled():
                                self._ensure_source(resolved_id, level, stats, primary_source)
                            return resolved_id
                    elif cached_usable:
                        fresh_candidates = query_provides(self.conn, name)
                        fresh_selected = select_provider(
                            fresh_candidates, self._visited, primary_source, version_constraint, self.index,
                        )
                        re_resolve = False
                        if fresh_selected:
                            if fresh_selected.package_name != cached_pkg:
                                re_resolve = True
                            elif version_compare(fresh_selected.version, cached_ver) > 0:
                                re_resolve = True
                        if re_resolve:
                            candidates = fresh_candidates
                            resolved_id = None
                        else:
                            if self._min_level_repair_enabled():
                                self._ensure_source(resolved_id, level, stats, primary_source)
                            return resolved_id

            selected = select_provider(candidates, self._visited, primary_source, version_constraint, self.index)
            if not selected:
                if version_constraint:
                    op = version_constraint.get("operator", ">=")
                    target = version_constraint.get("version", "")
                    stats.errors.append(
                        f"Virtual package '{name}' has no satisfiable providers ({op} {target})"
                    )
                else:
                    stats.errors.append(
                        f"Virtual package '{name}' has no satisfiable providers"
                    )
                return None

            if primary_source:
                pkg_entry = self.index.lookup_binary_by_source(selected.package_name, primary_source)
                if not pkg_entry or pkg_entry.version != selected.version:
                    pkg_entry = self.index.lookup_binary(selected.package_name)
            else:
                pkg_entry = self.index.lookup_binary(selected.package_name)

            if not pkg_entry:
                stats.errors.append(f"Provider '{selected.package_name}' selected but not found in index")
                return None

            binary_entry = pkg_entry
            _is_virtual_resolution = True

        self._batch_registered[name] = binary_entry.version
        if self._min_level_repair_enabled():
            prev_level = self._batch_levels.get(name)
            if prev_level is None or level < prev_level:
                self._batch_levels[name] = level
        stats.fallback_packages += (
            1 if binary_entry.release != primary_source else 0
        )
        if self._top_level_source is None:
            self._top_level_source = (binary_entry.release, binary_entry.component)

        upsert_package(self.conn, name, requested_version, status="current")

        dep_specs = []
        deps_json = "[]"
        if binary_entry.depends_raw:
            dep_specs = DependencyGraph.parse_depends_line(binary_entry.depends_raw)
            deps_json = serialize_depends(dep_specs)

        binary_id = supersede_current_binary(
            self.conn, binary_entry.package, binary_entry.architecture,
            binary_entry.version, binary_entry.release, binary_entry.component,
            deps_json,
        )
        stats.binaries += 1

        if _is_virtual_resolution:
            record_virtual_resolution(self.conn, name, binary_id)
            stats.virtual_count += 1

        self._register_source_for(binary_entry, binary_id, stats, primary_source, level)

        stats.packages += 1
        for i, spec in enumerate(dep_specs):
            dep_name = spec.name.split(":")[0]
            if dep_name in self._ancestry:
                circular_spec = DependencySpec(
                    name=spec.name,
                    version_constraint=spec.version_constraint,
                    architecture=spec.architecture,
                    dep_type=spec.dep_type,
                    alternatives=spec.alternatives,
                    circular=True,
                )
                dep_specs[i] = circular_spec
                self.conn.execute(
                    "UPDATE binaries SET circular = 1, dependencies = ? WHERE id = ?",
                    (serialize_depends(dep_specs), binary_id),
                )
                logger.warning("Circular dependency detected: %s -> %s", name, dep_name)
                continue
            # Resolve the alternative group as a unit. A failed primary
            # attempt leaves an error behind; it is retracted when the group
            # resolves (a later alternative already present/satisfied or a
            # later alternative resolves). Errors pushed by a
            # successfully-resolved member's own subtree are genuine failures
            # and are NOT retracted, so the truncation snapshot is taken per
            # alternative attempt.
            error_mark = len(stats.errors)
            resolved = self._resolve_one(dep_name, None, stats, primary_source, spec.version_constraint, level=level + 1)
            if resolved is None and spec.alternatives:
                all_names = [dep_name] + [a.name for a in spec.alternatives]
                name_constraints = {dep_name: spec.version_constraint}
                for alt in spec.alternatives:
                    name_constraints[alt.name] = alt.version_constraint
                any_satisfied = (
                    self._group_satisfying_member(
                        all_names, name_constraints, virtual_leg="provides"
                    )
                    is not None
                )
                if not any_satisfied:
                    for alt in spec.alternatives:
                        alt_mark = len(stats.errors)
                        resolved = self._resolve_one(
                            alt.name, None, stats, primary_source, alt.version_constraint, level=level + 1
                        )
                        if resolved is not None:
                            # This member resolved: drop the failed primary's
                            # and earlier alternatives' errors, keep this
                            # member's own subtree errors.
                            del stats.errors[error_mark:alt_mark]
                            break
                if any_satisfied:
                    del stats.errors[error_mark:]
                elif resolved is None:
                    # The whole group failed: exactly one error for the group.
                    del stats.errors[error_mark:]
                    stats.errors.append(
                        f"Alternative group '{dep_name} | {' | '.join(a.name for a in spec.alternatives)}' could not be resolved"
                    )

        return binary_id
