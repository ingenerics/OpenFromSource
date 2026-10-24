"""Source index generation (`srcindex`): DB `sources` rows -> the handoff's
`source/Sources` (deb-src) index for `local` (spec 024 Q3/FR-008).

The operator retrieves imported sources offline with `apt-get source` over the
same signed `Release` channel as the binary index. This module generates one
`deb-src` paragraph per verified `component='local'` source row: `Package`,
`Version`, `Directory` (the `source/pool/...` path apt reaches through the
handoff's `source` link) and `Files:`/`Checksums-Sha256:`/`Size` computed from
the pool payloads (the imported `.dsc` + referenced tarballs). Paragraphs are
serialized in stable (version, name) order with fixed formatting, so two
generations over identical DB state produce byte-identical `Sources`/
`Sources.gz`.
"""

import hashlib
import os
import sqlite3

from package_mgmt.lib.service.handoff import hash_entries_for_pair, write_gzip
from package_mgmt.lib.service.import_local import pool_prefix
from package_mgmt.lib.util.fs import atomic_write_bytes

_CHUNK = 65536


def local_source_pool_dir(config, package_name: str) -> str:
    """The pool directory holding an imported `local` source set.

    `import_local_deb` copies the complete source set (the `.dsc` + every
    referenced file) flat into `source/pool/local/<prefix>/<name>/`.
    """
    return os.path.join(
        config.source_cache, "pool", "local", pool_prefix(package_name), package_name
    )


def verified_local_sources(
    conn: sqlite3.Connection, release: str, component: str = "local"
) -> list[tuple[str, str]]:
    """Verified `(package_name, source_version)` rows for the component, in
    stable (version, name) order so the index is byte-deterministic."""
    rows = conn.execute(
        "SELECT package_name, source_version FROM sources"
        " WHERE component = ? AND release = ? AND verified = 1"
        " ORDER BY source_version, package_name",
        (component, release),
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def _file_meta(path: str) -> tuple[str, str, int]:
    """(md5, sha256, size) for a pool file, read once for all hashes.

    Both digests are computed in the same bounded-chunk pass — the caller
    needs md5 *and* sha256 per file, so a single read avoids re-reading every
    payload (the shared `file_sha256` is the one-digest helper for callers
    that want only sha256)."""
    digest_md5 = hashlib.md5()
    digest_sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            digest_md5.update(chunk)
            digest_sha.update(chunk)
    return digest_md5.hexdigest(), digest_sha.hexdigest(), os.path.getsize(path)


def source_paragraph(
    conn: sqlite3.Connection, config, package_name: str, source_version: str
) -> str | None:
    """One `deb-src` paragraph for a verified local source row.

    `Files:`/`Checksums-Sha256:`/`Size` are computed from the pool payloads
    (the imported `.dsc` + referenced tarballs), so apt fetches exactly the
    files that exist and their checksums match the pool. Returns `None` when
    the pool set is missing — the row is skipped (nothing to serve).
    """
    directory = local_source_pool_dir(config, package_name)
    names: list[str] = []
    if os.path.isdir(directory):
        names = sorted(
            name
            for name in os.listdir(directory)
            if os.path.isfile(os.path.join(directory, name))
        )
    if not names:
        return None
    meta = [
        (name, *(_file_meta(os.path.join(directory, name))[:3]))
        for name in names
    ]
    lines = [
        f"Package: {package_name}",
        f"Version: {source_version}",
        f"Directory: source/{os.path.relpath(directory, config.source_cache)}",
        "Files:",
    ]
    for name, md5, _sha, size in meta:
        lines.append(f" {md5} {size} {name}")
    lines.append("Checksums-Sha256:")
    for name, _md5, sha256, size in meta:
        lines.append(f" {sha256} {size} {name}")
    return "\n".join(lines)


def source_index_text(
    conn: sqlite3.Connection, config, release: str, component: str = "local"
) -> str:
    """The full `Sources` text for the component (empty when nothing serves)."""
    paragraphs: list[str] = []
    for package_name, source_version in verified_local_sources(conn, release, component):
        paragraph = source_paragraph(conn, config, package_name, source_version)
        if paragraph is not None:
            paragraphs.append(paragraph)
    text = "\n\n".join(paragraphs)
    return text + "\n" if text else ""


def source_index_hash_entries(text: str, gz_path: str, component: str) -> dict[str, dict]:
    """Hash entries for a component's source index: the plain `Sources` (the
    file apt verifies for `file://` sources) and the shipped `Sources.gz`.

    Mirrors the binary `Packages` handling (`hash_entries_for_pair`): both
    forms must be listed in the `Release` or apt, once it parses the Release
    hashes, drops the index.
    """
    with open(gz_path, "rb") as f:
        gz_data = f.read()
    return hash_entries_for_pair(
        component,
        "source/Sources",
        text.encode("utf-8"),
        "source/Sources.gz",
        gz_data,
    )


def write_source_index(
    conn: sqlite3.Connection, config, release: str, component: str, component_root: str
) -> dict[str, dict]:
    """Generate the deb-src index for `local` under `component_root/source/`.

    `component_root` is the handoff's `dists/<release>/<component>` directory.
    Writes `Sources` + `Sources.gz` atomically and returns the Release hash
    entries keyed by `{component}/source/Sources[.gz]`, so the source channel
    is covered by the existing `Release`/`Release.gpg` signing.
    """
    text = source_index_text(conn, config, release, component)
    src_dir = os.path.join(component_root, "source")
    os.makedirs(src_dir, exist_ok=True)
    plain_path = os.path.join(src_dir, "Sources")
    gz_path = os.path.join(src_dir, "Sources.gz")
    atomic_write_bytes(plain_path, text.encode("utf-8"))
    write_gzip(gz_path, text)
    return source_index_hash_entries(text, gz_path, component)
