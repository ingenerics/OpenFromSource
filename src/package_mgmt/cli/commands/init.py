import logging
import os
import sqlite3

from package_mgmt.lib.service.config_loader import load_config
from package_mgmt.lib.service.db_init import initialize_database
from package_mgmt.lib.model.repository import RepositoryIndex
from package_mgmt.lib.service.index_loader import (
    load_binary_index,
    load_source_index,
    fetch_index,
    release_base_for,
)
from package_mgmt.lib.service.initializer import initialize_caches, populate_provides_table
from package_mgmt.lib.util.paths import binary_index_path, source_index_path
from package_mgmt.lib.util.errors import print_error, InitError, DatabaseError, EXIT_SUCCESS, EXIT_PERMISSION, EXIT_CONFIG_ERROR, EXIT_SCHEMA_MISMATCH, EXIT_ERROR, ConfigError

logger = logging.getLogger(__name__)


def _gz_path_for_xz(gz_path: str) -> str:
    return gz_path.removesuffix(".gz") + ".xz"


def _is_expected_404(e: Exception) -> bool:
    import urllib.error
    return isinstance(e, urllib.error.HTTPError) and e.code == 404


def _fetch_with_fallback(url_base: str, dest_gz: str, release_base: str | None) -> bool:
    for ext, dest in [("gz", dest_gz), ("xz", _gz_path_for_xz(dest_gz))]:
        url = f"{url_base}.{ext}"
        logger.info("Fetching: %s", url)
        try:
            fetch_index(url, dest, release_base=release_base)
            return True
        except Exception as e:
            if _is_expected_404(e) and ext == "gz":
                logger.debug("No .gz available for %s, trying .xz", url_base)
            else:
                logger.warning("Failed to fetch %s: %s", url, e)
            if os.path.exists(dest):
                os.remove(dest)
    return False


def _index_exists(gz_path: str) -> str | None:
    for p in (gz_path, _gz_path_for_xz(gz_path)):
        if os.path.exists(p):
            return p
    return None


def _fetch_per_source(config) -> tuple[list[tuple[str, str, int, int]], RepositoryIndex]:
    source_stats: list[tuple[str, str, int, int]] = []
    merged_index = RepositoryIndex()

    for src in config.sources:
        mirror = src.mirror.rstrip("/")
        release_base = release_base_for(src.mirror, src.release)
        for comp in src.components:
            bin_prefix = binary_index_path(config.binary_cache, src.release, comp)
            src_prefix = source_index_path(config.source_cache, src.release, comp)

            bin_exists = _index_exists(bin_prefix)
            if not bin_exists:
                bin_url_base = f"{mirror}/dists/{src.release}/{comp}/binary-amd64/Packages"
                _fetch_with_fallback(bin_url_base, bin_prefix, release_base)

            src_exists = _index_exists(src_prefix) if config.fetch_source else True
            if config.fetch_source and not src_exists:
                src_url_base = f"{mirror}/dists/{src.release}/{comp}/source/Sources"
                _fetch_with_fallback(src_url_base, src_prefix, release_base)

            binary_count = 0
            source_count = 0

            bin_path = _index_exists(bin_prefix)
            if bin_path:
                binary_pkgs = load_binary_index(bin_path, src.release, comp)
                for name, entries in binary_pkgs.items():
                    merged_index.binary_packages.setdefault(name, []).extend(entries)
                binary_count = sum(len(v) for v in binary_pkgs.values())

            src_path = _index_exists(src_prefix) if config.fetch_source else None
            if src_path:
                source_pkgs = load_source_index(src_path, src.release, comp)
                for name, entries in source_pkgs.items():
                    merged_index.source_packages.setdefault(name, []).extend(entries)
                source_count = sum(len(v) for v in source_pkgs.values())

            source_stats.append((src.release, comp, binary_count, source_count))

    return source_stats, merged_index


def handle_init() -> int:
    try:
        config, _ = load_config()
    except ConfigError as e:
        print_error(str(e), EXIT_CONFIG_ERROR)
        return EXIT_CONFIG_ERROR

    binary_path = config.binary_cache
    source_path = config.source_cache

    try:
        initialize_caches(binary_path, source_path, config.sources, iso_dir_path=config.iso_dir)
    except InitError as e:
        logger.error("Init failed: %s", e)
        # Only an actual permission failure is the permission exit code; any
        # other initialization failure is a generic failure (e.g. ENOSPC).
        exit_code = EXIT_PERMISSION if e.permission else EXIT_ERROR
        print_error(str(e), exit_code)
        return exit_code

    try:
        schema_version = initialize_database(config.db_path)
    except DatabaseError as e:
        logger.error("Database init failed: %s", e)
        print_error(str(e), EXIT_SCHEMA_MISMATCH)
        return EXIT_SCHEMA_MISMATCH

    source_stats, index = _fetch_per_source(config)

    provides_count = 0
    try:
        conn = sqlite3.connect(config.db_path)
        try:
            configured_sources = {
                (src.release, comp) for src in config.sources for comp in src.components
            }
            provides_count = populate_provides_table(conn, index, configured_sources)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.error("Failed to populate provides table: %s", e)
        print_error(f"Failed to populate provides table: {e}", EXIT_ERROR)
        return EXIT_ERROR

    status = "initialized"
    if sum(binary_count for _, _, binary_count, _ in source_stats) == 0:
        # No index was reachable, so nothing was cached: state the offline
        # mode honestly instead of a bare "initialized".
        status = (
            "initialized (no mirror reachable \u2014 offline mode, "
            "0 binaries cached)"
        )
    default_src = config.sources[0]
    print(f"Base directory:    {config.base_dir}")
    print(f"Binary cache:      {binary_path}")
    print(f"Source cache:      {source_path}")
    print(f"Database:          {config.db_path}")
    print(f"Schema version:    {schema_version}")
    print(f"Default source:    {default_src.release}/{default_src.components[0]}")
    print("Archive sources:")
    for i, (release, comp, bcnt, scnt) in enumerate(source_stats, 1):
        suffix = f", {scnt} sources" if scnt else ""
        print(f"  [{i}] {release}/{comp} \u2014 {bcnt} binaries{suffix}")
    print(f"Provides entries:  {provides_count}")
    print(f"Status:            {status}")

    logger.info("Init completed (base_dir=%s, schema=v%d, provides=%d, status=%s)", config.base_dir, schema_version, provides_count, status)
    return EXIT_SUCCESS
