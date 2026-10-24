#!/usr/bin/env python3
"""Build a synthetic Rock Ridge installer image for tests and quickstart validation.

Usage:
    python3 tests/fixtures/make_test_iso.py <out.iso> [options]

Options:
    --count N          number of main packages (default 5)
    --minimal          tiny image: 3 main packages (below the netinst threshold)
    --omit <rr_path>   keep the index entry but do not add the file to the image
    --no-pool          omit the whole pool/ tree (index still lists the files)
    --corrupt-index    write garbage bytes as the main Packages.gz

The image contains a mini Debian release tree:
    dists/trixie/Release, dists/trixie/main/binary-amd64/Packages.gz,
    dists/trixie/contrib/binary-amd64/Packages.gz, a dists/stable symlink,
    and pool/ .deb placeholders referenced by the index Filename fields.
"""

import argparse
import functools
import gzip
import hashlib
import http.server
import io
import os
import sys
import threading

from pycdlib import PyCdlib
from pycdlib.facade import PyCdlibRockRidge

BASE_PACKAGES = [
    ("vifm", "1.0-2", "lib1, lib2", None),
    ("lib1", "2.0-1", None, None),
    ("lib2", "3.0-1", None, "virtual-lib2"),
    ("app", "1.5-1", "lib1", None),
    ("base-pkg", "1.0-1", None, None),
]

CONTRIB_PACKAGES = [
    ("firmware-x", "1.0-1", None, None),
]

# Pre-Depends fixture: `pre-dep-app` declares
# `pre-dep-lib` as a Pre-Depends that is NOT duplicated in Depends — the
# resolved runtime closure must include it for the cache to be installable.
# Tuples are `(name, version, deps, provides, pre_depends)`.
PRE_DEPENDS_PACKAGES = [
    ("pre-dep-app", "1.0-1", "lib1", None, "pre-dep-lib"),
    ("pre-dep-lib", "1.0-1", None, None, None),
    ("lib1", "2.0-1", None, None, None),
]

RELEASE_FILE = (
    "Origin: Debian\n"
    "Label: Debian\n"
    "Suite: trixie\n"
    "Codename: trixie\n"
    "Architectures: amd64\n"
    "Components: main contrib\n"
)


def binary_pool_filename(component: str, name: str, version: str) -> str:
    return f"pool/{component}/{name[0]}/{name}/{name}_{version}_amd64.deb"


def source_pool_directory(component: str, name: str) -> str:
    return f"pool/{component}/{name[0]}/{name}"


def source_files(name: str, version: str) -> list[str]:
    return [
        f"{name}_{version}.dsc",
        f"{name}_{version}.orig.tar.xz",
        f"{name}_{version}.debian.tar.xz",
    ]


def _pool_content(file_name: str) -> bytes:
    """Deterministic file bytes keyed by the pool file name.

    The scratch archive's index Size/SHA256 fields are derived from this same
    content, so served pool bytes always match the cached index entries.
    """
    return file_name.encode("utf-8") + b"\n" * 3


def _index_text(packages, component: str) -> str:
    paragraphs = []
    for entry in packages:
        name, version, deps, provides = entry[0], entry[1], entry[2], entry[3]
        pre_depends = entry[4] if len(entry) > 4 else None
        filename = binary_pool_filename(component, name, version)
        content = _pool_content(os.path.basename(filename))
        lines = [
            f"Package: {name}",
            f"Version: {version}",
            "Architecture: amd64",
        ]
        if deps:
            lines.append(f"Depends: {deps}")
        if pre_depends:
            lines.append(f"Pre-Depends: {pre_depends}")
        if provides:
            lines.append(f"Provides: {provides}")
        lines.append(f"Filename: {filename}")
        lines.append(f"Size: {len(content)}")
        lines.append(f"SHA256: {hashlib.sha256(content).hexdigest()}")
        paragraphs.append("\n".join(lines))
    return "\n\n".join(paragraphs) + "\n"


def _sources_text(packages, component: str) -> str:
    paragraphs = []
    for entry in packages:
        name, version = entry[0], entry[1]
        directory = source_pool_directory(component, name)
        files = source_files(name, version)
        files_block = []
        checksums_block = []
        for fname in files:
            content = _pool_content(fname)
            files_block.append(
                f" {hashlib.md5(content).hexdigest()} {len(content)} {fname}"
            )
            checksums_block.append(
                f" {hashlib.sha256(content).hexdigest()} {len(content)} {fname}"
            )
        paragraphs.append(
            "\n".join(
                [
                    f"Package: {name}",
                    f"Version: {version}",
                    f"Directory: {directory}",
                    "Files:",
                    "\n".join(files_block),
                    "Checksums-Sha256:",
                    "\n".join(checksums_block),
                ]
            )
        )
    return "\n\n".join(paragraphs) + "\n"


def _gzip_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(text.encode("utf-8"))
    return buf.getvalue()


def _make_packages(count: int = 5) -> list:
    if count != len(BASE_PACKAGES):
        return [(f"pkg{i:04d}", f"1.0-{i}", None, None) for i in range(1, count + 1)]
    return BASE_PACKAGES


def archive_index_bytes(count: int = 5) -> dict[str, tuple[bytes, bytes]]:
    """Packages.gz + Sources.gz bytes per component for a scratch archive.

    Mirrors the fixture image (same packages/versions) so resolution results
    are comparable to `add` on the same names.
    """
    packages = _make_packages(count)
    result = {}
    for component, pkgs in (("main", packages), ("contrib", CONTRIB_PACKAGES)):
        packages_gz = _gzip_bytes(_index_text(pkgs, component))
        sources_gz = _gzip_bytes(_sources_text(pkgs, component))
        result[component] = (packages_gz, sources_gz)
    return result


def write_archive(base_dir: str, count: int = 5, components=("main", "contrib")) -> None:
    """Write a scratch archive tree into base_dir for the resolver.

    Creates base_dir/binary/dists/trixie/<comp>/binary-amd64/Packages.gz and
    base_dir/source/dists/trixie/<comp>/source/Sources.gz per component.
    """
    indexes = archive_index_bytes(count)
    for component in components:
        if component not in indexes:
            continue
        packages_gz, sources_gz = indexes[component]
        binary_dir = os.path.join(
            base_dir, "binary", "dists", "trixie", component, "binary-amd64"
        )
        source_dir = os.path.join(base_dir, "source", "dists", "trixie", component, "source")
        os.makedirs(binary_dir, exist_ok=True)
        os.makedirs(source_dir, exist_ok=True)
        with open(os.path.join(binary_dir, "Packages.gz"), "wb") as f:
            f.write(packages_gz)
        with open(os.path.join(source_dir, "Sources.gz"), "wb") as f:
            f.write(sources_gz)


def write_source_index_for_mirror(base_dir: str, count: int = 5, components=("main", "contrib")) -> None:
    """Write Sources.gz into the served mirror tree (base_dir/binary/dists/...)
    so serve_pool can serve source indexes over HTTP alongside the binary
    index and the shared pool."""
    indexes = archive_index_bytes(count)
    for component in components:
        if component not in indexes:
            continue
        _, sources_gz = indexes[component]
        src_dir = os.path.join(base_dir, "binary", "dists", "trixie", component, "source")
        os.makedirs(src_dir, exist_ok=True)
        with open(os.path.join(src_dir, "Sources.gz"), "wb") as f:
            f.write(sources_gz)


def write_pool_files(base_dir: str, count: int = 5, components=("main", "contrib")) -> None:
    """Write deterministic bytes at every index pool path under base_dir/binary.

    The mirror serves one shared pool (Debian layout): binary .debs at their
    index `Filename` paths and source artifacts at their `Directory`/name
    paths, both under `base_dir/binary/pool/`. The bytes match the Size and
    SHA256 the indexes list (see `_pool_content`), so a download against this
    pool verifies cleanly.
    """
    packages = _make_packages(count)
    for component in components:
        pkgs = packages if component == "main" else CONTRIB_PACKAGES
        for entry in pkgs:
            name, version = entry[0], entry[1]
            deb = binary_pool_filename(component, name, version)
            _write_pool_path(base_dir, deb, _pool_content(os.path.basename(deb)))
            for fname in source_files(name, version):
                src = f"{source_pool_directory(component, name)}/{fname}"
                _write_pool_path(base_dir, src, _pool_content(fname))


def write_pre_depends_archive(base_dir: str) -> None:
    """Scratch archive (index + pool) carrying a Pre-Depends-only requirement.

    `pre-dep-app` Depends `lib1` and Pre-Depends `pre-dep-lib`, which is NOT
    listed in Depends — a correctly-resolved runtime closure must include
    `pre-dep-lib`. Writing both the index and the pool
    makes this usable both as the resolver's cache archive and as the mirror.
    """
    pkgs = PRE_DEPENDS_PACKAGES
    component = "main"
    binary_dir = os.path.join(
        base_dir, "binary", "dists", "trixie", component, "binary-amd64"
    )
    source_dir = os.path.join(base_dir, "source", "dists", "trixie", component, "source")
    os.makedirs(binary_dir, exist_ok=True)
    os.makedirs(source_dir, exist_ok=True)
    with open(os.path.join(binary_dir, "Packages.gz"), "wb") as f:
        f.write(_gzip_bytes(_index_text(pkgs, component)))
    with open(os.path.join(source_dir, "Sources.gz"), "wb") as f:
        f.write(_gzip_bytes(_sources_text(pkgs, component)))
    for entry in pkgs:
        name, version = entry[0], entry[1]
        deb = binary_pool_filename(component, name, version)
        _write_pool_path(base_dir, deb, _pool_content(os.path.basename(deb)))


BACKPORTS_PACKAGES = [
    ("backport-tool", "2.0-1", None, None),
]


def write_backports_archive(base_dir: str, release: str = "trixie-backports") -> None:
    """Scratch index + pool for a second release (multi-release handoff).

    Adds a `main` archive index for `release` under base_dir/binary/dists (and
    base_dir/source/dists) plus a pool file for its single package in the
    shared pool. Harmless to call on both the cache base (index is read by the
    resolver/generator) and the mirror base (pool is served over HTTP).
    """
    pkgs = BACKPORTS_PACKAGES
    component = "main"
    binary_dir = os.path.join(
        base_dir, "binary", "dists", release, component, "binary-amd64"
    )
    source_dir = os.path.join(base_dir, "source", "dists", release, component, "source")
    os.makedirs(binary_dir, exist_ok=True)
    os.makedirs(source_dir, exist_ok=True)
    with open(os.path.join(binary_dir, "Packages.gz"), "wb") as f:
        f.write(_gzip_bytes(_index_text(pkgs, component)))
    with open(os.path.join(source_dir, "Sources.gz"), "wb") as f:
        f.write(_gzip_bytes(_sources_text(pkgs, component)))
    for name, version, _, _ in pkgs:
        deb = binary_pool_filename(component, name, version)
        _write_pool_path(base_dir, deb, _pool_content(os.path.basename(deb)))


def _write_pool_path(base_dir: str, pool_rel: str, content: bytes) -> None:
    """Write a pool file under the mirror root (base_dir/binary + pool_rel)."""
    dest = os.path.join(base_dir, "binary", *pool_rel.split("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(content)


class _QuietPoolHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass


class _IndexDropHandler(_QuietPoolHandler):
    """Sends a partial index body then closes the connection on the first
    request per path — the client reads a truncated response (connection-close
    delimited, so no read error is raised). Later requests for the same path
    are served normally, so a retry re-fetches cleanly."""

    def do_GET(self):
        if self.path in getattr(self.server, "drop_paths", []):
            counts = getattr(self.server, "drop_counts", {})
            n = counts.get(self.path, 0) + 1
            counts[self.path] = n
            if n == 1:
                translated = self.translate_path(self.path)
                try:
                    with open(translated, "rb") as f:
                        data = f.read()
                except OSError:
                    super().do_GET()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                self.wfile.write(data[: max(1, len(data) // 2)])
                self.wfile.flush()
                self.connection.close()
                return
        super().do_GET()


class _RateLimitHandler(_QuietPoolHandler):
    """Answers 429 + Retry-After for the first `limit` requests per path."""

    def do_GET(self):
        counts = getattr(self.server, "request_counts", {})
        n = counts.get(self.path, 0) + 1
        counts[self.path] = n
        if n <= getattr(self.server, "rate_limit", 0):
            self.send_response(429)
            self.send_header("Retry-After", str(getattr(self.server, "retry_after", "0")))
            self.end_headers()
            return
        super().do_GET()


def serve_pool(base_dir: str, port: int = 0):
    """Start a threaded HTTP server rooted at base_dir/binary (the mirror).

    The mirror root serves the shared pool, so `{mirror}/{Filename}` and
    `{mirror}/{Directory}/{name}` resolve to real file bytes. Returns the
    started server; tests should call `shutdown()` (and join its thread) when
    done.
    """
    root = os.path.join(base_dir, "binary")
    if not os.path.isdir(root):
        raise OSError(f"Pool root not found: {root} (run write_pool_files first)")
    handler = functools.partial(_QuietPoolHandler, directory=root)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.thread = thread
    return server


class _RedirectHandler(_QuietPoolHandler):
    """Answers mapped paths with a redirect, serves /alt/... from the pool
    (a redirect target resolving to real file bytes), and serves the pool
    normally otherwise."""

    def do_GET(self):
        entry = getattr(self.server, "redirects", {}).get(self.path)
        if entry is not None:
            code, location = entry
            self.send_response(code)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/alt/"):
            self.path = "/" + self.path[len("/alt/"):]
        super().do_GET()


def serve_index_drop(base_dir: str, port: int = 0, drop_paths=()):
    """Like serve_pool, but drops the connection partway through an index
    response once per listed path — the client reads a truncated body.
    Subsequent requests for those paths are served normally, so a retry
    re-fetches cleanly. Returns the started server; tests should call
    `shutdown()` (and join its thread) when done.
    """
    root = os.path.join(base_dir, "binary")
    if not os.path.isdir(root):
        raise OSError(f"Pool root not found: {root} (run write_pool_files first)")
    handler = functools.partial(_IndexDropHandler, directory=root)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.drop_paths = list(drop_paths)
    server.drop_counts = {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.thread = thread
    return server


def serve_redirecting(base_dir: str, port: int = 0, redirects: dict | None = None):
    """Like serve_pool, but answers mapped paths with a redirect.

    `redirects` maps a request path to `(status, location)`; a `None`
    location sends the status with no Location header (no-target case).
    Paths under `/alt/...` are served from the pool, giving redirect targets
    real file bytes to land on. Returns the started server; tests should call
    `shutdown()` (and join its thread) when done.
    """
    root = os.path.join(base_dir, "binary")
    if not os.path.isdir(root):
        raise OSError(f"Pool root not found: {root} (run write_pool_files first)")
    handler = functools.partial(_RedirectHandler, directory=root)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.redirects = redirects or {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.thread = thread
    return server


def serve_rate_limited(base_dir: str, port: int = 0, limit: int = 3, retry_after: str = "0"):
    """Like serve_pool, but 429s (with the given Retry-After) the first
    `limit` requests per path before serving normally — exercises the
    retry/backoff path."""
    root = os.path.join(base_dir, "binary")
    if not os.path.isdir(root):
        raise OSError(f"Pool root not found: {root} (run write_pool_files first)")
    handler = functools.partial(_RateLimitHandler, directory=root)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.request_counts = {}
    server.rate_limit = limit
    server.retry_after = retry_after
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.thread = thread
    return server


def build_iso(out_path: str, count: int = 5, omit=None, no_pool: bool = False,
              corrupt_index: bool = False) -> None:
    """Write a synthetic installer image to out_path."""
    packages = _make_packages(count)
    omit = {p.lstrip("/") for p in (omit or [])}

    iso = PyCdlib()
    iso.new(rock_ridge="1.12", interchange_level=3)
    rr = PyCdlibRockRidge(iso)

    dirs = [
        "/dists",
        "/dists/trixie",
        "/dists/trixie/main",
        "/dists/trixie/main/binary-amd64",
        "/dists/trixie/contrib",
        "/dists/trixie/contrib/binary-amd64",
    ]
    if not no_pool:
        # NOTE: pycdlib 1.12.0's _find_rr_record crashes with IndexError when a
        # lookup name sorts after all existing siblings — parents must exist
        # before children (no implicit scaffolding).
        dirs.append("/pool")
        for component in ("main", "contrib"):
            pool_component = f"/pool/{component}"
            dirs.append(pool_component)
            for name, version, _, _ in (packages if component == "main" else CONTRIB_PACKAGES):
                dirs.append(f"{pool_component}/{name[0]}")
                dirs.append(f"{pool_component}/{name[0]}/{name}")
    for d in dict.fromkeys(dirs):
        rr.add_directory(d, file_mode=0o755)

    rr.add_fp(io.BytesIO(RELEASE_FILE.encode()), len(RELEASE_FILE),
              "/dists/trixie/Release", file_mode=0o644)
    main_gz = _gzip_bytes(_index_text(packages, "main"))
    if corrupt_index:
        main_gz = b"NOT A GZIP FILE"
    rr.add_fp(io.BytesIO(main_gz), len(main_gz),
              "/dists/trixie/main/binary-amd64/Packages.gz", file_mode=0o644)
    contrib_gz = _gzip_bytes(_index_text(CONTRIB_PACKAGES, "contrib"))
    rr.add_fp(io.BytesIO(contrib_gz), len(contrib_gz),
              "/dists/trixie/contrib/binary-amd64/Packages.gz", file_mode=0o644)

    if not no_pool:
        for component, pkgs in (("main", packages), ("contrib", CONTRIB_PACKAGES)):
            for name, version, _, _ in pkgs:
                letter = name[0]
                rr_path = f"/pool/{component}/{letter}/{name}/{name}_{version}_amd64.deb"
                if rr_path.lstrip("/") in omit:
                    continue
                rr.add_fp(io.BytesIO(b"placeholder-deb"), len(b"placeholder-deb"),
                          rr_path, file_mode=0o644)

    rr.add_symlink("/dists/stable", "trixie")

    iso.write(out_path)
    iso.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", help="path of the .iso to write")
    parser.add_argument("--count", type=int, default=5, help="number of main packages")
    parser.add_argument("--minimal", action="store_true", help="3 main packages (below netinst threshold)")
    parser.add_argument("--omit", action="append", default=[], help="rr_path to omit from the image")
    parser.add_argument("--no-pool", action="store_true", help="omit the pool/ tree")
    parser.add_argument("--corrupt-index", action="store_true", help="garbage Packages.gz")
    args = parser.parse_args()

    count = 3 if args.minimal else args.count
    build_iso(args.out, count=count, omit=args.omit,
              no_pool=args.no_pool, corrupt_index=args.corrupt_index)
    print(f"Wrote {os.path.getsize(args.out)} bytes to {args.out} "
          f"(main packages: {count})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
