import gzip
import hashlib
import io
import lzma
import os
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping

from debian import deb822

from package_mgmt.lib.model.dependency import DependencyGraph
from package_mgmt.lib.model.package import BinaryPackage, SourceFile, SourcePackage
from package_mgmt.lib.util.fs import atomic_write_bytes

# Pinned Debian archive signing keys (spec 012 security hardening): index
# fetches are only trusted after the archive's signed Release verifies against
# this keyring (a snapshot of the `debian-archive-keyring` package, committed
# under src/package_mgmt/data/ so the trust anchor is deterministic).
DEBIAN_ARCHIVE_KEYRING = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "debian-archive-keyring.gpg"
)

_INDEX_USER_AGENT = "package_mgmt index fetcher (OpenFromSource offline cache builder)"

# Socket timeout (seconds) for every index fetch. An idle connection — a
# server that accepts a request but never sends data — must abort instead of
# blocking a run forever. This is an idle timeout: a slow but actively
# streaming download (a multi-MB Packages.xz) is unaffected.
INDEX_FETCH_TIMEOUT = 10.0

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class IndexVerificationError(Exception):
    """The archive's release file signature or the index checksum could not be
    verified — the index MUST NOT be loaded (spec 012)."""


def _open_compressed(path: str):
    if path.endswith(".xz"):
        with open(path, "rb") as f:
            raw = f.read()
        text = lzma.decompress(raw).decode("utf-8")
        return io.StringIO(text)
    # Read the whole gzip stream eagerly: a truncated index then raises here
    # (EOFError) instead of silently parsing a partial Packages file.
    with gzip.open(path, "rb") as f:
        text = f.read().decode("utf-8")
    return io.StringIO(text)


# Read/decompression failures a cached index may raise (a truncated .gz/.xz);
# the resolver treats any of these as a cache miss and re-fetches the index.
INDEX_READ_ERRORS = (OSError, EOFError, lzma.LZMAError)


def _format_dep_spec(spec) -> str:
    """Render a parsed dependency spec back to Debian control-field text."""
    parts = [spec.name]
    if spec.version_constraint:
        op = spec.version_constraint.get("operator")
        ver = spec.version_constraint.get("version")
        if op and ver:
            parts.append(f"({op} {ver})")
    if spec.arch_restriction:
        parts.append("[" + " ".join(spec.arch_restriction) + "]")
    for alt in spec.alternatives:
        if isinstance(alt, str):
            parts.append(f"| {alt}")
            continue
        text = alt.name
        if alt.version_constraint:
            op = alt.version_constraint.get("operator")
            ver = alt.version_constraint.get("version")
            if op and ver:
                text += f" ({op} {ver})"
        parts.append(f"| {text}")
    return " ".join(parts)


def merge_depends_text(depends_raw: str | None, pre_depends_raw: str | None) -> str | None:
    """Merge `Depends` + `Pre-Depends` into one deduplicated `Depends` value.

    Every hard runtime requirement must land in the dependency set the
    closure walk resolves, so a Pre-Depends target that is NOT duplicated in
    `Depends` is still included. A target listed in both fields appears exactly
    once. When there is no Pre-Depends the original `Depends` text is returned
    unchanged (regression-preserving).
    """
    if not pre_depends_raw:
        return depends_raw
    specs = []
    for raw in (depends_raw, pre_depends_raw):
        if raw:
            specs.extend(DependencyGraph.parse_depends_line(raw))
    merged: list = []
    seen: set[str] = set()
    for spec in specs:
        if spec.name in seen:
            continue
        seen.add(spec.name)
        merged.append(spec)
    return ", ".join(_format_dep_spec(spec) for spec in merged)


def load_binary_index(path: str, release: str, component: str) -> dict[str, list[BinaryPackage]]:
    packages: dict[str, list[BinaryPackage]] = {}
    # use_apt_pkg=False: same rationale as the Sources loader below — the
    # internal parser avoids python3-apt's mangling of continuation-line
    # fields and never emits the "apt_pkg not importable" UserWarning that
    # would pollute a JSON error stream (contracts/cli.md Phase 6).
    with _open_compressed(path) as f:
        for para in deb822.Packages.iter_paragraphs(f, use_apt_pkg=False):
            name = para.get("Package")
            if not name:
                continue
            pre_depends_raw = para.get("Pre-Depends")
            entry = BinaryPackage(
                package=name,
                version=para.get("Version", ""),
                architecture=para.get("Architecture", ""),
                release=release,
                component=component,
                depends_raw=merge_depends_text(para.get("Depends"), pre_depends_raw),
                pre_depends_raw=pre_depends_raw,
                provides_raw=para.get("Provides"),
                source_field=para.get("Source"),
                filename=para.get("Filename"),
                sha256=para.get("SHA256"),
                size=para.get("Size"),
                multi_arch=para.get("Multi-Arch"),
                depends_field=para.get("Depends"),
                installed_size=para.get("Installed-Size"),
                conflicts_raw=para.get("Conflicts"),
                breaks_raw=para.get("Breaks"),
                replaces_raw=para.get("Replaces"),
            )
            packages.setdefault(name, []).append(entry)
    return packages


def parse_provides_field(provides_raw: str | None) -> list[tuple[str, str | None]]:
    if not provides_raw:
        return []
    results: list[tuple[str, str | None]] = []
    for spec in DependencyGraph.parse_depends_line(provides_raw):
        version = None
        if spec.version_constraint:
            version = spec.version_constraint.get("version")
        results.append((spec.name, version))
    return results


def _parse_source_files(checksums_sha256) -> list[SourceFile] | None:
    if not checksums_sha256:
        return None
    parsed: list[SourceFile] = []
    for line in checksums_sha256:
        if isinstance(line, str):
            parts = line.split()
            if len(parts) < 3:
                continue
            sha256, size_raw, name = parts[0], parts[1], parts[2]
        elif isinstance(line, Mapping):
            name = line.get("name")
            size_raw = line.get("size")
            sha256 = line.get("sha256")
        else:
            continue
        if not name:
            continue
        try:
            size = int(size_raw) if size_raw is not None else None
        except (TypeError, ValueError):
            size = None
        parsed.append(SourceFile(name=name, size=size, sha256=sha256))
    return parsed


def load_source_index(path: str, release: str, component: str) -> dict[str, list[SourcePackage]]:
    packages: dict[str, list[SourcePackage]] = {}
    # use_apt_pkg=False: python3-debian's apt_pkg-backed parsing mangles
    # continuation-line fields (Files, Checksums-Sha256); the internal parser
    # returns them as structured list-of-dicts.
    with _open_compressed(path) as f:
        for para in deb822.Sources.iter_paragraphs(f, use_apt_pkg=False):
            name = para.get("Package")
            if not name:
                continue
            entry = SourcePackage(
                package=name,
                version=para.get("Version", ""),
                release=release,
                component=component,
                directory=para.get("Directory"),
                files=para.get("Files"),
                build_depends=para.get("Build-Depends"),
                build_depends_arch=para.get("Build-Depends-Arch"),
                build_depends_indep=para.get("Build-Depends-Indep"),
                parsed_files=_parse_source_files(para.get("Checksums-Sha256")),
            )
            packages.setdefault(name, []).append(entry)
    return packages


def is_loopback_mirror(mirror: str) -> bool:
    """True for 127.0.0.1/localhost/::1 mirrors (the admin's own machine or a
    local test archive) — signature verification is skipped for them because
    the Debian archive keyring cannot apply and there is no on-path attacker."""
    try:
        host = (urllib.parse.urlsplit(mirror).hostname or "").lower()
    except ValueError:
        return False
    return host in _LOOPBACK_HOSTS


def release_base_for(mirror: str, release: str) -> str | None:
    """`dists/{release}/` URL used to verify index signatures, or None for
    loopback mirrors where verification is skipped (spec 012 security
    hardening)."""
    if is_loopback_mirror(mirror):
        return None
    return f"{mirror.rstrip('/')}/dists/{release}"


def _download(url: str, fetch_fn=None) -> bytes:
    if fetch_fn is not None:
        return fetch_fn(url)
    request = urllib.request.Request(url, headers={"User-Agent": _INDEX_USER_AGENT})
    with urllib.request.urlopen(request, timeout=INDEX_FETCH_TIMEOUT) as resp:
        return resp.read()


def _write_bytes(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


def _run_gpgv(keyring_path: str, homedir: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gpgv", "--homedir", homedir, "--keyring", keyring_path, *args],
        capture_output=True,
        text=True,
    )


def _gpgv_err(result: subprocess.CompletedProcess) -> str:
    err = (result.stderr or "").strip()
    return err[:300] if err else f"gpgv exited {result.returncode}"


def _verified_release_text(release_base: str, keyring_path: str, fetch_fn=None) -> str:
    """Fetch and verify the archive's release file against the keyring.

    Tries the clearsigned `InRelease` first, falling back to `Release` +
    detached `Release.gpg`. Returns the signed Release text (the index
    checksums it carries are then trusted). Raises IndexVerificationError when
    no valid signature can be established — indexes MUST NOT be loaded then.
    """
    with tempfile.TemporaryDirectory(prefix="index-verify-") as scratch:
        os.makedirs(scratch, 0o700, exist_ok=True)
        inrelease = os.path.join(scratch, "InRelease")
        try:
            _write_bytes(inrelease, _download(f"{release_base}/InRelease", fetch_fn))
        except urllib.error.HTTPError:
            inrelease = None
        if inrelease is not None:
            out = os.path.join(scratch, "signed-release")
            result = _run_gpgv(keyring_path, scratch, "--output", out, inrelease)
            if result.returncode != 0:
                raise IndexVerificationError(
                    f"cannot verify InRelease signature for {release_base}: "
                    f"{_gpgv_err(result)}"
                )
            with open(out) as f:
                return f.read()

        release_path = os.path.join(scratch, "Release")
        release_gpg = os.path.join(scratch, "Release.gpg")
        try:
            _write_bytes(release_path, _download(f"{release_base}/Release", fetch_fn))
            _write_bytes(release_gpg, _download(f"{release_base}/Release.gpg", fetch_fn))
        except urllib.error.HTTPError as e:
            raise IndexVerificationError(
                f"no signed release file at {release_base} "
                f"(InRelease and Release.gpg unavailable; HTTP {e.code})"
            ) from e
        result = _run_gpgv(keyring_path, scratch, release_gpg, release_path)
        if result.returncode != 0:
            raise IndexVerificationError(
                f"cannot verify Release.gpg signature for {release_base}: "
                f"{_gpgv_err(result)}"
            )
        with open(release_path) as f:
            return f.read()


def _release_sha256(release_text: str) -> dict[str, tuple[str, int]]:
    """Map relative index path -> (sha256_hex_lower, size) from the signed
    Release's SHA256 block."""
    rel = deb822.Release(io.StringIO(release_text))
    result: dict[str, tuple[str, int]] = {}
    for entry in rel.get("SHA256") or ():
        name = entry.get("name")
        sha = entry.get("sha256")
        if not name or not sha:
            continue
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        result[name] = (sha.lower(), size)
    return result


def fetch_index(
    url: str,
    dest_path: str,
    release_base: str | None = None,
    keyring_path: str | None = None,
    fetch_fn=None,
) -> None:
    """Fetch an archive index into dest_path, verifying it against the
    archive's signed Release when `release_base` (the `dists/{release}/` URL)
    is given (spec 012 security hardening).

    Verification: the release file's signature must verify against the pinned
    Debian archive keyring AND the index bytes must match the checksum the
    signed Release lists for the index's relative path. On any mismatch an
    IndexVerificationError is raised and nothing is written — the index is
    refused. Without `release_base` (loopback mirrors) the fetch is unverified
    (historical behavior).
    """
    if release_base is None:
        data = _download(url, fetch_fn)
        atomic_write_bytes(dest_path, data)
        return

    keyring = keyring_path or DEBIAN_ARCHIVE_KEYRING
    release_text = _verified_release_text(release_base, keyring, fetch_fn)
    checksums = _release_sha256(release_text)

    prefix = release_base.rstrip("/") + "/"
    if not url.startswith(prefix):
        raise IndexVerificationError(
            f"index URL {url} is not under release directory {release_base}"
        )
    relative = url[len(prefix):]
    expected = checksums.get(relative)
    if expected is None:
        raise IndexVerificationError(
            f"index {relative} is not listed in the signed release at {release_base}"
        )
    expected_sha, expected_size = expected

    data = _download(url, fetch_fn)
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_sha or (expected_size and len(data) != expected_size):
        raise IndexVerificationError(
            f"index {relative} checksum mismatch (signed release expects "
            f"{expected_sha[:12]}…, fetched {digest[:12]}…)"
        )

    atomic_write_bytes(dest_path, data)
