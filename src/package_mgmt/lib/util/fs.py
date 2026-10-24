"""Shared filesystem and hashing helpers.

A single typed home for the file-hash, atomic-write and atomic-copy patterns
used across the service layer, so the divergent private copies can be removed.
Each helper here is behavior-compatible with the implementation it replaces.
"""

import hashlib
import os
import shutil
import tempfile

_CHUNK = 65536


def file_sha256(path: str, errors: str = "raise") -> str | None:
    """SHA-256 hex digest of a file's contents, read in bounded chunks.

    `errors="none"` returns `None` when the file cannot be read (the caller
    treats a missing/unreadable payload as absent); `errors="raise"` (the
    default) propagates the underlying `OSError`.
    """
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(_CHUNK), b""):
                digest.update(chunk)
    except OSError:
        if errors == "none":
            return None
        raise
    return digest.hexdigest()


def atomic_write_bytes(path: str, data: bytes, mode: int = 0o644) -> None:
    """Write bytes to `path` via a temp file in the same directory + rename.

    A kill mid-write can never leave a truncated artifact at `path`: the
    destination is only ever replaced by a fully written file, and the temp
    file is cleaned up on failure.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix=".tmp-", suffix=".part"
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_copy(
    src: str, dst: str, verify: tuple[str, int] | None = None, mode: int = 0o644
) -> None:
    """Copy `src` to `dst` via a temp file + rename, never partial at `dst`.

    When `verify=(sha256_hex, size)` is given, the copied bytes are checked
    against the expected digest and size before the destination becomes
    visible; a mismatch raises `OSError` and leaves `dst` untouched. The
    destination is chmod'd to `mode` (0644 by default — the payload-mode
    convention the downloader's `open(part, "wb")` path produces).
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(dst), prefix=".tmp-", suffix=".part"
    )
    try:
        with open(src, "rb") as fsrc, os.fdopen(fd, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst)
        if verify is not None:
            sha256_hex, size = verify
            if file_sha256(tmp_path) != sha256_hex or os.stat(tmp_path).st_size != size:
                raise OSError("checksum mismatch during copy")
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, dst)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
