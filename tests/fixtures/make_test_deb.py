#!/usr/bin/env python3
"""Build minimal, real `.deb` files for the local patch lane import tests.

The binary `import-deb` parses must be actual Debian packages (`debian.debfile.
DebFile` needs the ar layout: `debian-binary` + `control.tar.gz` + `data.tar.gz`),
so this helper assembles them without external tooling — no `dpkg-deb` needed.

`build_deb` returns the path; the caller owns the temporary directory.
"""

import gzip
import hashlib
import io
import os
import tarfile

from db import pool_prefix


def _tar_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _gzip_bytes(data: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(data)
    return buf.getvalue()


def _ar_member(name: str, data: bytes) -> bytes:
    header = (
        name.encode("ascii").ljust(16)
        + b"0".ljust(12)
        + b"0".ljust(6)
        + b"0".ljust(6)
        + b"100644".ljust(8)
        + str(len(data)).encode("ascii").ljust(10)
        + b"`\n"
    )
    if len(data) % 2 == 1:
        data += b"\n"
    return header + data


def build_deb(
    path: str,
    *,
    package: str,
    version: str,
    architecture: str = "amd64",
    depends: str | None = None,
    pre_depends: str | None = None,
    provides: str | None = None,
    multi_arch: str | None = None,
    installed_size: str | None = None,
    conflicts: str | None = None,
    breaks: str | None = None,
    replaces: str | None = None,
    maintainer: str = "Local Patch Test <test@example.invalid>",
    data_files: dict[str, bytes] | None = None,
) -> str:
    """Write a valid `.deb` to `path` and return it.

    `depends`/`pre_depends` are raw Debian relation strings (e.g.
    `"lib1 (>= 2.0)"`). `installed_size`/`conflicts`/`breaks`/`replaces` are
    the remaining apt VersionHash control fields the handoff carries verbatim.
    Optional `data_files` maps member paths to bytes.
    """
    control_lines = [
        f"Package: {package}",
        f"Version: {version}",
        "Section: misc",
        "Priority: optional",
        f"Architecture: {architecture}",
        f"Maintainer: {maintainer}",
        "Description: Test package for the local patch lane",
    ]
    if depends:
        control_lines.append(f"Depends: {depends}")
    if pre_depends:
        control_lines.append(f"Pre-Depends: {pre_depends}")
    if installed_size:
        control_lines.append(f"Installed-Size: {installed_size}")
    if conflicts:
        control_lines.append(f"Conflicts: {conflicts}")
    if breaks:
        control_lines.append(f"Breaks: {breaks}")
    if replaces:
        control_lines.append(f"Replaces: {replaces}")
    if provides:
        control_lines.append(f"Provides: {provides}")
    if multi_arch:
        control_lines.append(f"Multi-Arch: {multi_arch}")
    control_text = "\n".join(control_lines) + "\n"

    ar = b"!<arch>\n"
    ar += _ar_member("debian-binary", b"2.0\n")
    ar += _ar_member(
        "control.tar.gz", _gzip_bytes(_tar_bytes({"./control": control_text.encode("utf-8")}))
    )
    ar += _ar_member("data.tar.gz", _gzip_bytes(_tar_bytes(data_files or {})))

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(ar)
    return path


def pool_rel_path(package: str, version: str, architecture: str = "amd64") -> str:
    """The pool-relative payload path used by `import-deb` (layout parity)."""
    return f"pool/local/{pool_prefix(package)}/{package}/{package}_{version}_{architecture}.deb"


def pool_abs_path(base_dir: str, package: str, version: str, architecture: str = "amd64") -> str:
    """Absolute payload path under `base_dir/binary` (the `binary_cache`)."""
    return os.path.join(base_dir, "binary", pool_rel_path(package, version, architecture))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_dsc(
    directory: str,
    *,
    source: str,
    version: str,
    files: dict[str, bytes],
    build_depends: str | None = None,
) -> str:
    """Write a complete source package directory: the `.dsc` plus every file
    it references, with matching `Files:` (md5/size) and `Checksums-Sha256:`
    (sha256/size) paragraphs. Returns the `.dsc` path.

    The `.dsc` names the files exactly like a real Debian source package, so
    `import-deb --source` parses a realistic, complete set.
    """
    os.makedirs(directory, exist_ok=True)
    for name, content in files.items():
        with open(os.path.join(directory, name), "wb") as f:
            f.write(content)

    lines = [
        "Format: 3.0 (quilt)",
        f"Source: {source}",
        f"Binary: {source}",
        "Architecture: any",
        f"Version: {version}",
        "Maintainer: Local Patch Test <test@example.invalid>",
        "Standards-Version: 4.6.2",
    ]
    if build_depends:
        lines.append(f"Build-Depends: {build_depends}")
    lines.append("Files:")
    for name, content in sorted(files.items()):
        lines.append(
            f" {hashlib.md5(content).hexdigest()} {len(content)} {name}"
        )
    lines.append("Checksums-Sha256:")
    for name, content in sorted(files.items()):
        lines.append(
            f" {hashlib.sha256(content).hexdigest()} {len(content)} {name}"
        )
    lines.append("")
    dsc_path = os.path.join(directory, f"{source}_{version}.dsc")
    with open(dsc_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return dsc_path
