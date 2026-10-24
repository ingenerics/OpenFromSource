import logging
import os
import sqlite3

from package_mgmt.lib.model.cache import BinaryCache, SourceCache
from package_mgmt.lib.model.config import ArchiveSource
from package_mgmt.lib.model.repository import RepositoryIndex
from package_mgmt.lib.service.index_loader import parse_provides_field
from package_mgmt.lib.util.errors import InitError

logger = logging.getLogger(__name__)


def _ensure_dir(path: str, mode: int = 0o755) -> None:
    try:
        os.makedirs(path, mode=mode, exist_ok=True)
        os.chmod(path, mode)
    except PermissionError:
        raise InitError(f"Permission denied: {path}", permission=True)
    except OSError as e:
        raise InitError(f"Failed to create {path}: {e}")


def initialize_caches(
    binary_cache_path: str,
    source_cache_path: str,
    sources: tuple[ArchiveSource, ...],
    iso_dir_path: str | None = None,
    arch: str = "amd64",
) -> tuple[BinaryCache, SourceCache]:
    binary_exists = os.path.isdir(binary_cache_path) and os.path.isdir(os.path.join(binary_cache_path, "pool"))
    source_exists = os.path.isdir(source_cache_path) and os.path.isdir(os.path.join(source_cache_path, "pool"))

    _ensure_dir(binary_cache_path)
    _ensure_dir(source_cache_path)

    _ensure_dir(os.path.join(binary_cache_path, "pool"))
    _ensure_dir(os.path.join(source_cache_path, "pool"))

    if iso_dir_path:
        _ensure_dir(iso_dir_path)

    components_seen: set[str] = set()
    for src in sources:
        for comp in src.components:
            if comp not in components_seen:
                components_seen.add(comp)
                _ensure_dir(os.path.join(binary_cache_path, "pool", comp))
                _ensure_dir(os.path.join(source_cache_path, "pool", comp))

    for src in sources:
        for comp in src.components:
            _ensure_dir(os.path.join(binary_cache_path, "dists", src.release, comp, f"binary-{arch}"))
            _ensure_dir(os.path.join(source_cache_path, "dists", src.release, comp, "source"))

    binary = BinaryCache(path=binary_cache_path, exists=binary_exists)
    source = SourceCache(path=source_cache_path, exists=source_exists)

    logger.info("Binary cache %s (was %s)", binary_cache_path, "existing" if binary_exists else "created")
    logger.info("Source cache %s (was %s)", source_cache_path, "existing" if source_exists else "created")

    return binary, source


def populate_provides_table(
    conn: sqlite3.Connection,
    index: RepositoryIndex,
    configured_sources: set[tuple[str, str]] | None = None,
) -> int:
    count = 0
    if configured_sources is not None:
        # Sweep stale rows first: a (release, component) no longer configured
        # must never leave a selectable provider behind. The caller owns the
        # transaction.
        if configured_sources:
            placeholders = ",".join("(?, ?)" for _ in configured_sources)
            params = [item for pair in configured_sources for item in pair]
            conn.execute(
                "DELETE FROM provides WHERE (release, component) NOT IN (VALUES "
                + placeholders
                + ")",
                params,
            )
        else:
            conn.execute("DELETE FROM provides")
    for packages in index.binary_packages.values():
        for binary in packages:
            if not binary.provides_raw:
                continue
            provides = parse_provides_field(binary.provides_raw)
            if not provides:
                continue
            for virtual_name, version in provides:
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO provides
                       (package_name, version, architecture, release, component, virtual_name, provides_version)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (binary.package, binary.version, binary.architecture,
                     binary.release, binary.component, virtual_name, version or ""),
                )
                count += cursor.rowcount
    logger.info("Populated %d provides entries into database", count)
    return count
