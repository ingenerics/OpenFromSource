import os


def expand_path(template: str, base_dir: str) -> str:
    return template.replace("{base_dir}", base_dir)


def validate_absolute(path: str) -> str:
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        raise ValueError(f"Path is not absolute: {path}")
    return os.path.abspath(expanded)


def binary_index_path(binary_cache: str, release: str, component: str, arch: str = "amd64") -> str:
    return os.path.join(binary_cache, "dists", release, component, f"binary-{arch}", "Packages.gz")


def source_index_path(source_cache: str, release: str, component: str) -> str:
    return os.path.join(source_cache, "dists", release, component, "source", "Sources.gz")


def index_path_xz(gz_path: str) -> str:
    return gz_path.removesuffix(".gz") + ".xz"


def resolve_index_path(gz_path: str) -> str:
    if os.path.exists(gz_path):
        return gz_path
    xz_path = index_path_xz(gz_path)
    if os.path.exists(xz_path):
        return xz_path
    return gz_path


def handoff_dir(binary_cache: str) -> str:
    """The derived apt source the operator consumes (spec 012)."""
    return os.path.join(binary_cache, "handoff")


def handoff_release_dir(binary_cache: str, release: str) -> str:
    return os.path.join(handoff_dir(binary_cache), "dists", release)


def handoff_index_path(binary_cache: str, release: str, component: str, arch: str = "amd64") -> str:
    return os.path.join(
        handoff_release_dir(binary_cache, release), component, f"binary-{arch}", "Packages.gz"
    )


def handoff_key_path(binary_cache: str) -> str:
    return os.path.join(handoff_dir(binary_cache), "offline-cache.gpg")


def handoff_gnupg_dir(binary_cache: str) -> str:
    """Persistent GNUPGHOME holding the cache-local signing key, derived from
    the cache's base directory and kept out of the handoff payload.

    The key is generated once and reused across regenerations so
    `offline-cache.gpg` is a stable trust anchor. Living at `{base_dir}/.
    gnupg-handoff/` — the handoff ships only the public key. The handoff 
    references the cache above it, so the transported unit is the cache root; 
    a plain copy of `{base_dir}` still carries the keyring and the admin should 
    exclude `.gnupg-handoff/` unless operators are trusted relays that must re-sign.
    """
    base_dir = os.path.dirname(binary_cache.rstrip(os.sep))
    return os.path.join(base_dir, ".gnupg-handoff")


def handoff_signature_path(binary_cache: str, release: str) -> str:
    return os.path.join(handoff_release_dir(binary_cache, release), "Release.gpg")


def handoff_script_path(binary_cache: str) -> str:
    return os.path.join(handoff_dir(binary_cache), "bootstrap.sh")


def handoff_pool_link(binary_cache: str) -> str:
    return os.path.join(handoff_dir(binary_cache), "pool")


def handoff_source_link(binary_cache: str) -> str:
    """The handoff's `source` symlink -> the source cache (spec 024 Q3).

    Serves the `source/pool/...` `Directory` paths of the local deb-src index
    through the handoff's `file:` source, mirroring the `pool` link.
    """
    return os.path.join(handoff_dir(binary_cache), "source")


def diff_dir(binary_cache: str) -> str:
    """The minimal diff cache the operator applies (spec 013)."""
    return os.path.join(binary_cache, "diff")


def diff_release_dir(binary_cache: str, release: str) -> str:
    return os.path.join(diff_dir(binary_cache), "dists", release)


def diff_index_path(binary_cache: str, release: str, component: str, arch: str = "amd64") -> str:
    return os.path.join(
        diff_release_dir(binary_cache, release), component, f"binary-{arch}", "Packages.gz"
    )


def diff_key_path(binary_cache: str) -> str:
    return os.path.join(diff_dir(binary_cache), "offline-cache.gpg")


def diff_signature_path(binary_cache: str, release: str) -> str:
    return os.path.join(diff_release_dir(binary_cache, release), "Release.gpg")


def diff_manifest_path(binary_cache: str) -> str:
    return os.path.join(diff_dir(binary_cache), "manifest.txt")


def diff_script_path(binary_cache: str) -> str:
    return os.path.join(diff_dir(binary_cache), "apply.sh")


def diff_pool_path(binary_cache: str) -> str:
    """The diff's own pool holding only the pending binaries' files."""
    return os.path.join(diff_dir(binary_cache), "pool")


def safe_pool_path(cache_root: str, rel_path: str) -> str | None:
    """Join a Debian index path under cache_root, guarding against traversal
    (spec 012 security hardening).

    Index `Filename`/`Directory` values are attacker-influenced (a crafted or
    compromised archive index), so they must never escape the cache. Returns
    the normalized destination path when `rel_path` is a safe relative path
    inside `cache_root/pool/` (no `..` segments, not absolute), else None —
    callers must never join an invalid path.
    """
    if not rel_path:
        return None
    if os.path.isabs(rel_path):
        return None
    norm = os.path.normpath(rel_path)
    if norm == ".." or norm.startswith(".." + os.sep):
        return None
    if os.path.isabs(norm):
        return None
    if not (norm == "pool" or norm.startswith("pool" + os.sep)):
        return None
    return os.path.join(cache_root, norm)
