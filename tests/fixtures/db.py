"""Shared test scaffolding: config, binary-row, and pool-prefix helpers.

The near-identical `_config`, `_insert_binary`, and `_pool_prefix` definitions
duplicated across the unit-test modules live here as one update point for the
schema fixtures. No behavior change from the private copies they replace.
"""

import os
import sqlite3

from package_mgmt.lib.model.config import ArchiveSource, ProjectConfig


def config(
    base_dir: str,
    mirror: str = "http://mirror.invalid",
    release: str = "trixie",
    fetch_source: bool = False,
) -> ProjectConfig:
    """A standard test cache config rooted at `base_dir`."""
    return ProjectConfig(
        base_dir=base_dir,
        binary_cache=f"{base_dir}/binary",
        source_cache=f"{base_dir}/source",
        iso_dir=f"{base_dir}/iso",
        db_path=f"{base_dir}/package_mgmt.db",
        log_dir=f"{base_dir}/logs",
        fetch_source=fetch_source,
        sources=(
            ArchiveSource(
                mirror=mirror,
                release=release,
                components=("main", "contrib"),
            ),
        ),
    )


def insert_binary(
    conn: sqlite3.Connection,
    name: str,
    version: str,
    release: str = "trixie",
    component: str = "main",
    architecture: str = "amd64",
    status: str = "current",
    verified: int = 1,
) -> int:
    """Insert a binary row and return its id."""
    cur = conn.execute(
        "INSERT INTO binaries (package_name, version, architecture, release, component, status, verified)"
        " VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (name, version, architecture, release, component, status, verified),
    )
    return cur.fetchone()[0]


def pool_prefix(package_name: str) -> str:
    """Debian pool prefix convention: `lib*` -> `lib<first char of the rest>`."""
    if package_name.startswith("lib") and len(package_name) > 3:
        return "lib" + package_name[3]
    return package_name[0]


def pool_rel_path(package: str, version: str, architecture: str) -> str:
    """Pool-relative path for a payload under `pool/<component>/...`."""
    return os.path.join(
        "pool", "local", pool_prefix(package), package,
        f"{package}_{version}_{architecture}.deb",
    )
