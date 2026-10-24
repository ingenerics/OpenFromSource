"""ensure-base workflow: register offline DVD installer image metadata.

Three phases (per data-model.md):
- Phase A (local): discover release/components, load each component's
  Packages.gz, register binaries/packages/provides (binary records start with `verified = 0`)
  (same semantics as `add`), cross-check index Filenames against the image.
- Phase B (behaves like `add`): per-name resolution.
- Phase C: finalize — summary, temp cleanup.
"""

import glob
import logging
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field

from package_mgmt.lib.model.dependency import DependencyGraph, serialize_depends
from package_mgmt.lib.model.repository import RepositoryIndex
from package_mgmt.lib.model.resolution import PackageRequest
from package_mgmt.lib.service.config_loader import (
    graybeard_missing_components_warning,
    source_depth_zero_warning,
)
from package_mgmt.lib.service.db_init import initialize_database
from package_mgmt.lib.service.index_loader import load_binary_index
from package_mgmt.lib.service.initializer import populate_provides_table
from package_mgmt.lib.service.iso_reader import IsoImage
from package_mgmt.lib.service.resolver import PackageResolver, upsert_binary, upsert_package
from package_mgmt.lib.util.errors import CacheNotEmptyError, IsoError

logger = logging.getLogger(__name__)

# Measured: netinst 494-520 binary packages, DVD-1 4,233-4,270 (trixie/bookworm).
ISO_MIN_FULL_DVD_BINARIES = 1000

NETINST_WARNING = (
    "Image contains only {count} binary packages — this looks like a netinst or minimal "
    "image, which cannot support fully offline installation. The full offline DVD "
    "installer image (DVD-1) is the supported artifact."
)


@dataclass(frozen=True)
class EnsureBaseStats:
    image_path: str
    release: str
    components: tuple[str, ...]
    binaries: int = 0
    provides: int = 0
    mismatches: int = 0
    mismatch_paths: tuple[str, ...] = ()
    resolved: int | None = None
    total: int | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def find_installer_image(iso_dir: str) -> str:
    """Return the single .iso file in iso_dir; raise IsoError otherwise."""
    if not os.path.isdir(iso_dir):
        raise IsoError(f"No installer image found in {iso_dir}")
    images = sorted(
        name for name in os.listdir(iso_dir) if name.lower().endswith(".iso")
    )
    if not images:
        raise IsoError(f"No installer image found in {iso_dir}")
    if len(images) > 1:
        listed = ", ".join(images)
        raise IsoError(
            f"Multiple installer images found in {iso_dir}: {listed}. "
            "Keep exactly one image in the ISO directory and re-run."
        )
    return os.path.join(iso_dir, images[0])


def _load_image_indexes(image: IsoImage, temp_dir: str) -> tuple[dict, int]:
    """Load every component's Packages.gz into a merged binary index.

    Returns (merged_binary_packages, total_entries). A corrupt index raises
    IsoError — the caller's transaction is not yet open.
    """
    merged: dict[str, list] = {}
    total = 0
    for component in image.components:
        rr_path = f"/dists/{image.suite}/{component}/binary-amd64/Packages.gz"
        data = image.read_file(rr_path)
        tmp_path = os.path.join(temp_dir, f"{component}.Packages.gz")
        with open(tmp_path, "wb") as f:
            f.write(data)
        try:
            binary_pkgs = load_binary_index(tmp_path, image.release, component)
        except Exception as e:
            raise IsoError(
                f"Corrupt index {rr_path} in {image.path}: {e}"
            ) from e
        for name, entries in binary_pkgs.items():
            merged.setdefault(name, []).extend(entries)
        total += sum(len(v) for v in binary_pkgs.values())
    return merged, total


def _register_binaries(
    conn: sqlite3.Connection, merged: dict, release: str,
) -> int:
    count = 0
    for entries in merged.values():
        for entry in entries:
            dep_specs = DependencyGraph.parse_depends_line(entry.depends_raw) if entry.depends_raw else []
            deps_json = serialize_depends(dep_specs)
            upsert_binary(
                conn, entry.package, entry.version, entry.architecture,
                release, entry.component, deps_json,
            )
            upsert_package(conn, entry.package, None)
            count += 1
    return count


def _find_mismatches(merged: dict, image_files: set[str]) -> tuple[str, ...]:
    """Index Filenames absent from the image (damaged-image diagnostic).

    Never affects verified flags and never fails the run.
    """
    indexed = {
        entry.filename
        for entries in merged.values()
        for entry in entries
        if entry.filename
    }
    normalized = {path.lstrip("/") for path in image_files}
    return tuple(sorted(indexed - normalized))


def _cleanup_stale_temp_dirs(base_dir: str) -> None:
    """Remove ensure-base-* temp dirs left behind by crashed runs (SIGKILL).

    Single-user CLI: concurrent runs are not supported, so stale dirs are
    always leftovers, never another active run's workspace.
    """
    for path in glob.glob(os.path.join(base_dir, "ensure-base-*")):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)


def _resolve_base_packages(
    config,
    conn: sqlite3.Connection,
    names: list[str],
    progress: Callable[[int, int], None] | None = None,
) -> tuple[int, list[str]]:
    """Phase B: resolve the base set in ONE batch walk, like `add`.

    Uses the batch resolver (`resolve_batch`); dependencies are walked once
    per invocation; per-name commits checkpoint progress. Verified flags are
    never mutated. Returns (resolved_count, errors) where resolved counts
    names whose resolution produced no errors.
    """
    resolver = PackageResolver(config, conn)
    stats_list = resolver.resolve_batch(
        [PackageRequest(n) for n in names], progress=progress
    )
    resolved = 0
    errors: list[str] = []
    for name, stats in zip(names, stats_list):
        if stats.errors:
            errors.extend(f"{name}: {e}" for e in stats.errors)
        else:
            resolved += 1
    return resolved, errors


def _assert_fresh_cache(conn: sqlite3.Connection) -> None:
    """ensure-base is for a fresh cache only — refuse when packages exist.

    The graybeard flow runs ensure-base once on an empty cache
    (init -> ensure-base -> verify -> ...). Refusing a populated cache
    removes the two legacy failure modes of a re-run: resetting every record's
    `verified` state (forcing a full re-download) and creating duplicate
    `current` rows when the image version differs from a cached one.
    """
    if conn.execute("SELECT 1 FROM binaries LIMIT 1").fetchone() is not None:
        raise CacheNotEmptyError(
            "ensure-base is only for a fresh cache: this cache already contains "
            "registered packages. Start from an empty cache (delete the database "
            "and re-run 'init') to ingest an installer image."
        )


def ensure_base(config, progress: Callable[[int, int], None] | None = None) -> EnsureBaseStats:
    """Full ensure-base workflow: Phase A registration, Phase B resolution."""
    image_path = find_installer_image(config.iso_dir)
    _cleanup_stale_temp_dirs(config.base_dir)
    temp_dir = tempfile.mkdtemp(prefix="ensure-base-", dir=config.base_dir)
    try:
        with IsoImage(image_path) as image:
            merged, total = _load_image_indexes(image, temp_dir)
            mismatch_paths = _find_mismatches(merged, image.file_paths())
            # Database creation is deferred until the failure-prone discovery
            # above (image lookup, index loading) has succeeded, so a failed
            # run leaves no database behind.
            if os.path.exists(config.db_path):
                probe = sqlite3.connect(config.db_path)
                try:
                    _assert_fresh_cache(probe)
                finally:
                    probe.close()
            initialize_database(config.db_path)
            conn = sqlite3.connect(config.db_path)
            try:
                try:
                    # Single transaction: committed only after full Phase A success.
                    conn.execute("BEGIN")
                    _register_binaries(conn, merged, image.release)
                    index = RepositoryIndex(binary_packages=merged)
                    configured_sources = {
                        (image.release, comp) for comp in image.components
                    }
                    provides_count = populate_provides_table(conn, index, configured_sources)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                names = sorted({entry.package for entries in merged.values() for entry in entries})
                resolved, errors = _resolve_base_packages(config, conn, names, progress)
                # Phase C: re-runs are idempotent by construction — the
                # binary_to_source UNIQUE constraint makes re-linking a no-op.
                conn.commit()
            finally:
                conn.close()
        warnings: list[str] = []
        if total < ISO_MIN_FULL_DVD_BINARIES:
            warnings.append(NETINST_WARNING.format(count=total))
        depth_warning = source_depth_zero_warning(config)
        if depth_warning:
            warnings.append(depth_warning)
        component_warning = graybeard_missing_components_warning(config)
        if component_warning:
            warnings.insert(0, component_warning)
        logger.info(
            "ensure-base registered %d binaries, %d provides from %s (%s); "
            "%d index/image mismatches; %d/%d packages resolved, %d errors",
            total, provides_count, image_path, image.release, len(mismatch_paths),
            resolved, len(names), len(errors),
        )
        return EnsureBaseStats(
            image_path=image_path,
            release=image.release,
            components=image.components,
            binaries=total,
            provides=provides_count,
            mismatches=len(mismatch_paths),
            mismatch_paths=mismatch_paths,
            resolved=resolved,
            total=len(names),
            warnings=warnings,
            errors=errors,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
