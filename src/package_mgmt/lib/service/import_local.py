"""Import a locally built `.deb` into the `local` component (spec 021).

`import-deb` registers a single locally built/patched package: the control is
parsed with python-debian (`debian.debfile.DebFile`), every declared
`Depends`/`Pre-Depends` is re-resolved against the cache (an unsatisfiable
requirement aborts atomically with zero DB and pool changes), the binary row
is registered (`component='local'`, `verified=0`), the payload is copied to
`pool/local/<prefix>/<pkg>/<pkg>_<ver>_<arch>.deb` (identical layout to every
other component), its SHA256 computed, and the record marked `verified=1`.
Re-import of the identical binary is idempotent via the `ON CONFLICT` path.

`local` uses no archive index and is never fetched: the payload is exactly the
supplied `.deb` and the record is verified from that payload. The optional
`--source <dir>` (provenance) is wired through and handled by the source
import path.
"""

import gzip
import logging
import os
import sqlite3
import tarfile
from dataclasses import dataclass

from debian import deb822
from debian import debfile
from debian.debian_support import Version

from package_mgmt.lib.model.dependency import (
    DependencyGraph,
    deps_satisfied,
    serialize_depends_with_version_hash,
)
from package_mgmt.lib.service.index_loader import parse_provides_field
from package_mgmt.lib.service.packaging_util import read_dsc_files
from package_mgmt.lib.service.resolver import (
    link_binary_to_source,
    supersede_current_binary,
    upsert_package,
    upsert_source,
)
from package_mgmt.lib.util.fs import atomic_copy, file_sha256
from package_mgmt.lib.util.paths import safe_pool_path

logger = logging.getLogger(__name__)

LOCAL_COMPONENT = "local"

_DEB_READ_ERRORS = (debfile.DebError, debfile.ArError, OSError, ValueError, EOFError, gzip.BadGzipFile, tarfile.TarError)


class ImportDebError(Exception):
    """An import failure with a user-facing message (exit 1, zero changes)."""


@dataclass(frozen=True)
class ImportResult:
    package: str
    version: str
    architecture: str
    source_summary: str | None = None


def pool_prefix(package_name: str) -> str:
    """Debian pool prefix: `lib*` -> `lib<first char of the rest>`."""
    if package_name.startswith("lib") and len(package_name) > 3:
        return "lib" + package_name[3]
    return package_name[0]


def local_pool_rel_path(package: str, version: str, architecture: str) -> str:
    return f"pool/local/{pool_prefix(package)}/{package}/{package}_{version}_{architecture}.deb"


def _format_constraint(vc: dict | None) -> str:
    if vc:
        return f" ({vc.get('operator', '>=')} {vc.get('version', '')})"
    return ""


def _verify_dependencies(conn: sqlite3.Connection, dep_specs) -> None:
    """Re-resolve every declared requirement against the cache; an
    unsatisfiable requirement aborts naming it (FR-003, zero changes)."""
    for spec in dep_specs:
        name = spec.name.split(":")[0]
        if deps_satisfied(conn, name, spec):
            continue
        if spec.alternatives:
            alts = " | ".join(
                a.name.split(":")[0] + _format_constraint(a.version_constraint)
                for a in spec.alternatives
            )
            raise ImportDebError(
                f"unsatisfiable dependency: {name}{_format_constraint(spec.version_constraint)}"
                f" | {alts} — not in cache"
            )
        raise ImportDebError(
            f"unsatisfiable dependency: {name}{_format_constraint(spec.version_constraint)} — not in cache"
        )


def _register_provides(
    conn: sqlite3.Connection,
    package: str,
    version: str,
    architecture: str,
    release: str,
    provides_raw: str | None,
) -> None:
    for virtual_name, pversion in parse_provides_field(provides_raw):
        conn.execute(
            """INSERT OR IGNORE INTO provides
               (package_name, version, architecture, release, component, virtual_name, provides_version)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (package, version, architecture, release, LOCAL_COMPONENT, virtual_name, pversion or ""),
        )


def _default_release(config) -> str:
    if not config.sources:
        raise ImportDebError("No archive sources configured; cannot determine the default release")
    return config.sources[0].release


def _validate_source_set(
    source_dir: str,
) -> tuple[deb822.Dsc, dict[str, dict], str]:
    """Locate and validate the complete rebuilt source set in `source_dir`.

    The directory must hold exactly one `.dsc` plus every file it references,
    each present and matching the `.dsc`'s declared checksums — a bare `.dsc`
    cannot be `dpkg-source -x`'d, so anything short of the complete,
    checksum-valid set is rejected with a clear error naming the problem and
    zero changes (FR-005). Returns `(dsc, files, dsc_name)`.
    """
    if not os.path.isdir(source_dir):
        raise ImportDebError(
            f"--source must be a directory holding the .dsc and its files: {source_dir}"
        )
    dsc_names = sorted(
        name for name in os.listdir(source_dir)
        if name.endswith(".dsc") and os.path.isfile(os.path.join(source_dir, name))
    )
    if len(dsc_names) != 1:
        if not dsc_names:
            raise ImportDebError(f"--source must contain exactly one .dsc file: {source_dir}")
        raise ImportDebError(
            "--source must contain exactly one .dsc file, found: " + ", ".join(dsc_names)
        )
    dsc_name = dsc_names[0]
    try:
        with open(os.path.join(source_dir, dsc_name), "r", encoding="utf-8", errors="replace") as f:
            dsc = deb822.Dsc(f.read())
    except Exception as e:
        raise ImportDebError(f"Cannot read source control file: {dsc_name}") from e
    files = read_dsc_files(dsc)
    if not files:
        raise ImportDebError(f"Cannot read source control file: {dsc_name} (no files listed)")
    for name, meta in files.items():
        if not meta.get("sha256"):
            raise ImportDebError(f"Source control file {dsc_name} lacks a SHA256 for {name}")
        src = os.path.join(source_dir, name)
        if not os.path.isfile(src):
            raise ImportDebError(f"Source file missing: {name} (required by {dsc_name})")
        actual = file_sha256(src)
        if actual != meta["sha256"].lower():
            raise ImportDebError(
                f"Source file checksum mismatch: {name} "
                f"(expected {meta['sha256']}, got {actual})"
            )
    return dsc, files, dsc_name


def _import_source(
    conn: sqlite3.Connection,
    config,
    source_dir: str,
    release: str,
    dsc: deb822.Dsc,
    files: dict[str, dict],
    dsc_name: str,
) -> tuple[int, str, str]:
    """Copy the verified source set into the source pool and register it.

    All referenced files were already validated against the `.dsc`; this
    copies them — plus the `.dsc` itself (apt's `apt-get source` needs it to
    unpack) — to `source/pool/local/<prefix>/<pkg>/…` (the same layout as
    archive source components), registers the `local` source row and marks it
    verified. Returns `(source_id, source_name, source_version)`. A copy
    failure cleans up the files it copied — nothing is registered or left
    behind.
    """
    source_name = (dsc.get("Source") or "").split()[0].strip() or None
    source_version = dsc.get("Version")
    if not source_name or not source_version:
        raise ImportDebError("Source control file is missing Source/Version fields")
    rel_dir = f"pool/local/{pool_prefix(source_name)}/{source_name}"
    copied: list[tuple[str, bool]] = []
    try:
        for name in sorted(files) + [dsc_name]:
            rel = f"{rel_dir}/{name}"
            dest = safe_pool_path(config.source_cache, rel)
            if dest is None:
                raise ImportDebError(f"Unsafe source pool path for {name}")
            existed = os.path.exists(dest)
            atomic_copy(os.path.join(source_dir, name), dest)
            copied.append((dest, existed))
        src_id, _ = upsert_source(conn, source_name, source_version, release, LOCAL_COMPONENT, None)
        conn.execute("UPDATE sources SET verified = 1 WHERE id = ?", (src_id,))
        return src_id, source_name, source_version
    except BaseException:
        for dest, existed in copied:
            if not existed:
                try:
                    os.remove(dest)
                except OSError:
                    pass
        raise


def import_local_deb(
    conn: sqlite3.Connection,
    config,
    deb_path: str,
    source_dir: str | None = None,
) -> ImportResult:
    """Import exactly one locally built `.deb` into `local`.

    Atomic: any failure leaves zero DB rows and zero pool changes. The binary
    row is registered before the payload copy (database-first), and
    `verified=1` is set only after the payload is copied and its SHA256
    computed. With `--source`, the complete rebuilt source set is validated up
    front (zero changes on any gap) and registered + linked in the same
    transaction. Re-import is idempotent via the `ON CONFLICT` update path.
    """
    if not os.path.isfile(deb_path):
        raise ImportDebError(f"Cannot read package file: {deb_path}")
    try:
        with debfile.DebFile(deb_path) as deb:
            control = deb.control.debcontrol()
    except _DEB_READ_ERRORS as e:
        raise ImportDebError(f"Cannot read package file: {deb_path}") from e

    package = control.get("Package")
    version = control.get("Version")
    architecture = control.get("Architecture") or "amd64"
    if not package or not version:
        raise ImportDebError(
            f"Cannot read package file: {deb_path} (missing Package/Version in control)"
        )
    try:
        Version(version)
    except ValueError:
        raise ImportDebError(f"Invalid version '{version}' declared by {package}") from None

    depends_raw = control.get("Depends")
    pre_depends_raw = control.get("Pre-Depends")
    provides_raw = control.get("Provides")

    dep_specs: list = []
    for raw in (depends_raw, pre_depends_raw):
        if raw:
            dep_specs.extend(DependencyGraph.parse_depends_line(raw))

    _verify_dependencies(conn, dep_specs)
    # The original VersionHash-relevant field text is stored alongside the
    # parsed deps so a later generation-time fallback (unreadable payload) can
    # still reproduce the installed record's VersionHash — the DB-only
    # reconstruction must never fall back to the merged `Depends`+`Pre-Depends` view.
    version_hash_fields = {
        "Depends": depends_raw,
        "Pre-Depends": pre_depends_raw,
        "Installed-Size": control.get("Installed-Size"),
        "Conflicts": control.get("Conflicts"),
        "Breaks": control.get("Breaks"),
        "Replaces": control.get("Replaces"),
    }
    version_hash_fields = {k: v for k, v in version_hash_fields.items() if v}
    deps_json = serialize_depends_with_version_hash(dep_specs, version_hash_fields)

    source_set = _validate_source_set(source_dir) if source_dir is not None else None

    release = _default_release(config)
    dest = safe_pool_path(config.binary_cache, local_pool_rel_path(package, version, architecture))
    if dest is None:
        raise ImportDebError(f"Unsafe pool path for {package} {version}")
    dest_existed = os.path.exists(dest)

    try:
        binary_id = supersede_current_binary(
            conn, package, architecture, version, release, LOCAL_COMPONENT, deps_json
        )
        upsert_package(conn, package, None, status="current")
        if provides_raw:
            _register_provides(conn, package, version, architecture, release, provides_raw)

        atomic_copy(deb_path, dest)
        source_summary = None
        if source_set is not None:
            dsc, files, dsc_name = source_set
            src_id, source_name, source_version = _import_source(
                conn, config, source_dir, release, dsc, files, dsc_name
            )
            link_binary_to_source(conn, binary_id, src_id)
            source_summary = (
                f"Source linked ({source_name} {source_version}, local, "
                f"{len(files)} files verified against dsc)"
            )
        conn.execute(
            "UPDATE binaries SET verified = 1, dependencies = ? WHERE id = ?",
            (deps_json, binary_id),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        if not dest_existed:
            try:
                os.remove(dest)
            except OSError:
                pass
        raise

    logger.info("imported %s %s (%s) into local component (verified)", package, version, architecture)
    return ImportResult(
        package=package, version=version, architecture=architecture,
        source_summary=source_summary,
    )
