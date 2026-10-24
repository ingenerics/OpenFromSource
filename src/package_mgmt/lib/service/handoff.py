"""Handoff generator (spec 012): derive the operator's apt source from the
cache's verified state.

The admin-only `handoff` command takes the spec 010 delivery snapshot of the
binaries the handoff actually delivers, then runs the generator. Output lives
under `{binary_cache}/handoff/` (data-model.md): per-release filtered
`Packages.gz` (exactly the verified set whose pool files exist), one
`Release` with checksums and its armored detached signature `Release.gpg` per
release (Constitution gate; see research Decision 2 for the `file://` trust
model), the public key as a binary keyring `offline-cache.gpg` (readable by
both gpgv and sqv apt backends), the operator's `bootstrap.sh` (one source
line per release), and the `pool -> ../pool` symlink. The cache's own `dists/`
archive indexes are never touched (spec 010/011 index-read design).

Multi-release caches (e.g. trixie + trixie-backports) are served fully: each
release gets its own `dists/{release}/Release` + signature + component
indexes, and the script configures one apt source line per release. A record's
delivery set — the binary ids actually included — is returned so the caller
marks exactly those as delivered (never the whole verified superset).

Everything is derived — no schema changes — and regeneration is idempotent
(the key, signatures, and indexes are rebuilt from the current verified
state).
"""

import gzip
import hashlib
import logging
import os
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field

from debian import debfile

from package_mgmt.lib.model.dependency import (
    deserialize_depends,
    version_hash_fields_from_depends_json,
)
from package_mgmt.lib.model.package import BinaryPackage
from package_mgmt.lib.service.import_local import local_pool_rel_path
from package_mgmt.lib.service.index_loader import _format_dep_spec, load_binary_index
from package_mgmt.lib.service.downloader import lookup_entry
from package_mgmt.lib.util.paths import (
    binary_index_path,
    diff_dir,
    diff_index_path,
    diff_key_path,
    diff_manifest_path,
    diff_pool_path,
    diff_release_dir,
    diff_script_path,
    diff_signature_path,
    handoff_dir,
    handoff_gnupg_dir,
    handoff_index_path,
    handoff_key_path,
    handoff_pool_link,
    handoff_release_dir,
    handoff_script_path,
    handoff_signature_path,
    handoff_source_link,
    resolve_index_path,
    safe_pool_path,
)
from package_mgmt.lib.util.fs import atomic_copy, atomic_write_bytes, file_sha256

logger = logging.getLogger(__name__)

GPG_KEY_UID = "OpenFromSource Offline Cache <offline@openfromsource.local>"
RELEASE_DATE_FORMAT = "%a, %d %b %Y %H:%M:%S UTC"

# Operator script template (spec 012, contracts/cli.md). `__RELEASE_LABEL__`,
# `__RELEASES__` (space-separated) and `__SOURCES__` (one "release comps..."
# line per release) are filled by the generator.
#
# The operator flow is implemented as plain functions taking explicit paths, so
# the shipped script carries no test-only hooks: unit tests source this file
# and call the functions with scratch paths.
BOOTSTRAP_SCRIPT = r"""#!/usr/bin/env bash
# OpenFromSource offline cache bootstrap (spec 012).
# Usage: bootstrap.sh [<cache-dir>] [--remove]
set -u

releases="__RELEASES__"
sources="__SOURCES__"
release_label="__RELEASE_LABEL__"
sources_file="/etc/apt/sources.list.d/openfromsource-cache.list"
os_release="/etc/os-release"

# handoff_complete <handoff_dir> <cache_dir> <sources>
#   0 when every release has Release + Release.gpg and per-component
#   Packages.gz, the cache pool directory exists, and the handoff/pool link
#   resolves to a directory (the pool is reachable through the handoff);
#   non-zero otherwise. A served `local` component also requires its
#   `local/source/Sources.gz` (deb-src) index — the operator's source channel
#   must never point at a missing index.
handoff_complete() {
  local handoff_dir="$1" cache_dir="$2" sources="$3" rel comps comp
  while read -r rel comps; do
    [ -f "$handoff_dir/dists/$rel/Release" ] || return 1
    [ -f "$handoff_dir/dists/$rel/Release.gpg" ] || return 1
    for comp in $comps; do
      [ -f "$handoff_dir/dists/$rel/$comp/binary-amd64/Packages.gz" ] || return 1
      if [ "$comp" = "local" ]; then
        [ -f "$handoff_dir/dists/$rel/local/source/Sources.gz" ] || return 1
      fi
    done
  done <<< "$sources"
  [ -d "$cache_dir/pool" ] || return 1
  [ -d "$handoff_dir/pool" ]
}

# system_codename <os_release>  -> prints the VERSION_CODENAME
system_codename() {
  sed -n 's/^VERSION_CODENAME="\?\([^"]*\)"\?$/\1/p' "$1" | head -n 1
}

# release_match <os_release> <releases>
#   0 when the system codename is one of the handoff releases (or os-release is
#   absent); 1 when the codename cannot be read; 2 on mismatch.
release_match() {
  local os_release="$1" releases="$2" codename rel
  [ -f "$os_release" ] || return 0
  codename="$(system_codename "$os_release")"
  [ -n "$codename" ] || return 1
  for rel in $releases; do
    [ "$codename" = "$rel" ] && return 0
  done
  return 2
}

# ensure_pool_link <handoff_dir>
#   0 when the pool is reachable through the handoff dir; non-zero when it is
#   not. On media that cannot carry a symlink (e.g. FAT) the pool is
#   unreachable through a `deb file:` line — fail hard, never leave a broken
#   apt source pointing at an unreachable pool.
ensure_pool_link() {
  local handoff_dir="$1"
  if [ ! -d "$handoff_dir" ]; then
    echo "Handoff directory $handoff_dir does not exist." >&2
    return 1
  fi
  if [ ! -e "$handoff_dir/pool" ] || [ ! -d "$handoff_dir/pool" ]; then
    rm -f "$handoff_dir/pool"
    if ! ln -s ../pool "$handoff_dir/pool" 2>/dev/null; then
      echo "Cannot link $handoff_dir/pool -> ../pool: this media cannot carry a symlink, so the pool is unreachable through the handoff. Refusing to configure a broken apt source." >&2
      return 1
    fi
  fi
  return 0
}

# write_sources_entry <sources_file> <handoff_dir> <sources>
#   Atomic (temp file + rename); overwrites only its own prior content. A
#   handoff dir containing a space is refused with a clear diagnostic — apt
#   sources lines are whitespace-delimited, so an unquoted path with a space
#   would silently break the `deb` entry. A served `local` component also
#   gains a `deb-src` line for `local` (spec 024 Q3): the operator's source
#   channel is configured from the same handoff, with no operator-side
#   tooling.
write_sources_entry() {
  local sources_file="$1" handoff_dir="$2" sources="$3"
  local dir tmp_file rel comps comp
  case "$handoff_dir" in
    *" "*)
      echo "The cache path contains a space ('$handoff_dir'); apt sources cannot carry it." >&2
      echo "Place the cache at a space-free path (e.g. mount point or base_dir) and re-run." >&2
      return 1
      ;;
  esac
  dir="$(dirname "$sources_file")"
  mkdir -p "$dir"
  tmp_file="$(mktemp "$dir/.openfromsource.XXXXXX")"
  {
    while read -r rel comps; do
      printf 'deb [signed-by=%s/offline-cache.gpg] file:%s %s %s\n' "$handoff_dir" "$handoff_dir" "$rel" "$comps"
      for comp in $comps; do
        if [ "$comp" = "local" ]; then
          printf 'deb-src [signed-by=%s/offline-cache.gpg] file:%s %s local\n' "$handoff_dir" "$handoff_dir" "$rel"
        fi
      done
    done <<< "$sources"
  } > "$tmp_file"
  chmod 644 "$tmp_file"
  mv "$tmp_file" "$sources_file"
}

# remove_sources_entry <sources_file>  (deletes the entry only)
remove_sources_entry() {
  rm -f "$1"
}

# run_apt_update <sources_file> [<state_root>]
#   Runs the operator `apt update`. A state_root (tests only) runs it against
#   scratch apt dirs under that root so no system state is touched.
run_apt_update() {
  local sources_file="$1" state_root="${2:-}" apt_root
  if [ -n "$state_root" ]; then
    apt_root="$state_root/var"
    mkdir -p "$apt_root/lib/apt/lists/partial" \
             "$apt_root/lib/apt/parts" \
             "$apt_root/cache/apt/archives/partial" \
             "$apt_root/lib/dpkg"
    : > "$apt_root/lib/dpkg/status"
    apt-get \
        -o "Dir::Etc::sourcelist=$sources_file" \
        -o "Dir::Etc::sourceparts=-" \
        -o "Dir::Etc::apt.conf=/dev/null" \
        -o "Dir::Etc::parts=$apt_root/lib/apt/parts" \
        -o "Dir::State::lists=$apt_root/lib/apt/lists" \
        -o "Dir::State::status=$apt_root/lib/dpkg/status" \
        -o "Dir::Cache=$apt_root/cache/apt" \
        -o "Dir::Cache::archives=$apt_root/cache/apt/archives" \
        -o "Debug::NoLocking=1" \
        update
  else
    apt update
  fi
}

# bootstrap_configure <cache_dir> <sources_file> <os_release> <sources> <releases> [<state_root>]
#   Pool link -> preflight (structure + release match) -> atomic entry write ->
#   apt update. Nothing is written until the pool is reachable and validation
#   passes. Returns:
#     0 ok; 1 pool unreachable / handoff structure incomplete; 2 release
#   mismatch; 3 apt update failed after the entry was written (entry left in
#   place for inspection).
bootstrap_configure() {
  local cache_dir="$1" sources_file="$2" os_release="$3" sources="$4" releases="$5" state_root="${6:-}"
  local handoff_dir="$cache_dir/handoff" rc codename
  if ! ensure_pool_link "$handoff_dir"; then
    return 1
  fi
  if ! handoff_complete "$handoff_dir" "$cache_dir" "$sources"; then
    echo "Handoff not found at $cache_dir/handoff — is the cache complete?" >&2
    return 1
  fi
  release_match "$os_release" "$releases"
  rc=$?
  if [ "$rc" -eq 1 ]; then
    echo "Cannot determine the system release from $os_release" >&2
    return 2
  fi
  if [ "$rc" -eq 2 ]; then
    codename="$(system_codename "$os_release")"
    echo "Release mismatch: cache is for $releases, this system is $codename. Refusing to configure." >&2
    return 2
  fi
  write_sources_entry "$sources_file" "$handoff_dir" "$sources"
  if ! run_apt_update "$sources_file" "$state_root"; then
    echo "apt update failed — see output above; the source entry was left in place for inspection." >&2
    return 3
  fi
  return 0
}

bootstrap_main() {
  local cache_dir="" remove=0 positional=0 arg script_dir handoff_dir rc
  for arg in "$@"; do
    case "$arg" in
      --remove)
        remove=1
        ;;
      -*)
        echo "No such option: ${arg}. Usage: bootstrap.sh [<cache-dir>] [--remove]" >&2
        exit 1
        ;;
      *)
        positional=$((positional + 1))
        if [ "$positional" -gt 1 ]; then
          echo "No such option: ${arg}. Usage: bootstrap.sh [<cache-dir>] [--remove]" >&2
          exit 1
        fi
        cache_dir="$arg"
        ;;
    esac
  done

  if [ -z "$cache_dir" ]; then
    script_dir="$(cd "$(dirname "$0")" && pwd)"
    cache_dir="$(dirname "$script_dir")"
  else
    # A given path is used as-is, resolved against the current working
    # directory (contracts/cli.md) — errors report the resolved path.
    cache_dir="$(cd "$cache_dir" 2>/dev/null && pwd || printf '%s' "$cache_dir")"
  fi

  if [ "$(id -u)" -ne 0 ]; then
    echo "Not running as root — run with sudo." >&2
    exit 1
  fi

  if [ "$remove" -eq 1 ]; then
    remove_sources_entry "$sources_file"
    echo "Bootstrap: removing offline cache source"
    echo "Removed: $sources_file"
    exit 0
  fi

  handoff_dir="$cache_dir/handoff"
  bootstrap_configure "$cache_dir" "$sources_file" "$os_release" "$sources" "$releases"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "Bootstrap: configuring apt from $handoff_dir ($release_label)"
    echo "Source added: $sources_file"
    echo "apt update: OK"
    exit 0
  fi
  exit 1
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  bootstrap_main "$@"
fi
"""


# Operator script shipped in the diff (spec 013, contracts/cli.md).
# `__RELEASE_LABEL__`, `__RELEASES__` (space-separated) and `__SOURCES__`
# (one "release comps..." line per release) are filled by the generator.
# The operator flow is implemented as plain functions taking explicit paths,
# so the shipped script carries no test-only hooks: unit tests source this
# file and call the functions with scratch paths (no root, no host
# modification). `run_apt_update` accepts an optional scratch state root
# (tests only) so the apt-update failure path runs against isolated
# apt dirs.
APPLY_SCRIPT = r"""#!/usr/bin/env bash
# OpenFromSource offline cache diff apply (spec 013).
# Usage: apply.sh [<cache-dir>]   (default = the diff's parent)
set -u

releases="__RELEASES__"
sources="__SOURCES__"
release_label="__RELEASE_LABEL__"
os_release="/etc/os-release"

# system_codename <os_release>  -> prints the VERSION_CODENAME
system_codename() {
  sed -n 's/^VERSION_CODENAME="\?\([^"]*\)"\?$/\1/p' "$1" | head -n 1
}

# release_match <os_release> <releases>
#   0 when the system codename is one of the diff releases (or os-release is
#   absent); 1 when the codename cannot be read; 2 on mismatch.
release_match() {
  local os_release="$1" releases="$2" codename rel
  [ -f "$os_release" ] || return 0
  codename="$(system_codename "$os_release")"
  [ -n "$codename" ] || return 1
  for rel in $releases; do
    [ "$codename" = "$rel" ] && return 0
  done
  return 2
}

# diff_complete <diff_dir> <sources>
#   0 when the diff has manifest.txt, per-release Release.gpg + per-component
#   Packages.gz and a pool/ directory; non-zero otherwise. A served `local`
#   component also requires its `local/source/Sources.gz` (deb-src) index — the
#   operator's source channel must never point at a missing index.
diff_complete() {
  local diff_dir="$1" sources="$2" rel comps comp
  [ -f "$diff_dir/manifest.txt" ] || return 1
  while read -r rel comps; do
    [ -f "$diff_dir/dists/$rel/Release.gpg" ] || return 1
    for comp in $comps; do
      [ -f "$diff_dir/dists/$rel/$comp/binary-amd64/Packages.gz" ] || return 1
      if [ "$comp" = "local" ]; then
        [ -f "$diff_dir/dists/$rel/local/source/Sources.gz" ] || return 1
      fi
    done
  done <<< "$sources"
  [ -d "$diff_dir/pool" ]
}

# check_base_set <diff_dir> <cache_dir>
#   Every `delivered` line of the manifest must already exist under the
#   operator's pool; a missing one means an intermediate diff was missed and
#   is refused before anything changes.
check_base_set() {
  local diff_dir="$1" cache_dir="$2" kind sha size path
  while read -r kind sha size path; do
    [ "$kind" = "delivered" ] || continue
    if [ ! -f "$cache_dir/$path" ]; then
      echo "Missing intermediate sync: $path is not in the cache pool — an earlier diff must be applied first. Refusing to apply." >&2
      return 1
    fi
  done < "$diff_dir/manifest.txt"
  return 0
}

# cleanup_temps <root>
#   Discard stale .apply.* temp files left by an interrupted apply, so a
#   re-run converges to the same state as a single clean run: every
#   destination is written via a fresh temp + atomic rename, so any leftover
#   partial temp is dead weight that must not confuse a subsequent apply.
#   Best-effort — unreadable leftovers never block the merge.
cleanup_temps() {
  local root="$1"
  [ -d "$root" ] || return 0
  find "$root" -name '.apply.*' -type f -delete 2>/dev/null
  return 0
}

# copy_new_files <diff_dir> <cache_dir>
#   For each `new` line, verify the diff's file against the manifest SHA256
#   (and size), then copy it into the pool atomically (temp + verify +
#   rename). A file already present with a matching checksum is skipped
#   (idempotent); present with a different checksum -> refuse. Prints the
#   number of files copied; returns 0 on success, 1 on any refusal.
copy_new_files() {
  local diff_dir="$1" cache_dir="$2" kind sha size path
  local copied=0 dest src tmp actual actual_size
  cleanup_temps "$cache_dir/pool"
  while read -r kind sha size path; do
    [ "$kind" = "new" ] || continue
    dest="$cache_dir/$path"
    src="$diff_dir/$path"
    if [ -f "$dest" ]; then
      if [ "$(sha256sum "$dest" | cut -d' ' -f1)" = "$sha" ]; then
        continue
      fi
      echo "Existing file conflict: $path is already in the pool with a different checksum. Refusing to apply." >&2
      return 1
    fi
    if [ ! -f "$src" ]; then
      echo "Checksum mismatch for $path: expected $sha, got (missing). Refusing to apply." >&2
      return 1
    fi
    actual="$(sha256sum "$src" | cut -d' ' -f1)"
    actual_size="$(stat -c %s "$src" 2>/dev/null || echo 0)"
    if [ "$actual" != "$sha" ] || [ "$actual_size" != "$size" ]; then
      echo "Checksum mismatch for $path: expected $sha, got $actual. Refusing to apply." >&2
      return 1
    fi
    mkdir -p "$(dirname "$dest")"
    tmp="$(mktemp "$(dirname "$dest")/.apply.XXXXXX")"
    cp "$src" "$tmp"
    actual="$(sha256sum "$tmp" | cut -d' ' -f1)"
    if [ "$actual" != "$sha" ]; then
      rm -f "$tmp"
      echo "Checksum mismatch for $path: expected $sha, got $actual. Refusing to apply." >&2
      return 1
    fi
    chmod 644 "$tmp"
    mv "$tmp" "$dest"
    copied=$((copied + 1))
  done < "$diff_dir/manifest.txt"
  echo "$copied"
  return 0
}

# atomic_copy <src> <dst>  (temp + rename, per-file atomic)
atomic_copy() {
  local src="$1" dst="$2" tmp
  mkdir -p "$(dirname "$dst")"
  tmp="$(mktemp "$(dirname "$dst")/.apply.XXXXXX")"
  cp "$src" "$tmp"
  chmod 644 "$tmp"
  mv "$tmp" "$dst"
}

# replace_handoff_dists <diff_dir> <cache_dir> <sources>
#   Replace the handoff's per-release Packages.gz, Release and Release.gpg
#   with the diff's complete ones (per-file atomic) so apt presents prior +
#   new. A served `local` component also replaces `local/source/Sources.gz`
#   (the diff ships the source index, and its Release hash-lists it — the
#   applied handoff must carry the same file). The shipped Release carries no
#   Valid-Until (apt imposes no expiry) and its Date reflects the diff's
#   generation time. offline-cache.gpg is copied over if the handoff lacks
#   one. The apt sources entry is never touched.
replace_handoff_dists() {
  local diff_dir="$1" cache_dir="$2" sources="$3" rel comps comp
  local handoff="$cache_dir/handoff"
  cleanup_temps "$handoff/dists"
  while read -r rel comps; do
    for comp in $comps; do
      atomic_copy "$diff_dir/dists/$rel/$comp/binary-amd64/Packages.gz" \
                  "$handoff/dists/$rel/$comp/binary-amd64/Packages.gz"
      if [ "$comp" = "local" ]; then
        atomic_copy "$diff_dir/dists/$rel/local/source/Sources.gz" \
                    "$handoff/dists/$rel/local/source/Sources.gz"
      fi
    done
    atomic_copy "$diff_dir/dists/$rel/Release" "$handoff/dists/$rel/Release"
    atomic_copy "$diff_dir/dists/$rel/Release.gpg" "$handoff/dists/$rel/Release.gpg"
  done <<< "$sources"
  if [ ! -f "$handoff/offline-cache.gpg" ]; then
    atomic_copy "$diff_dir/offline-cache.gpg" "$handoff/offline-cache.gpg"
  fi
}

# run_apt_update <sources_file> <cache_dir> [<state_root>]
#   Runs the operator `apt update` (the existing sources entry keeps pointing
#   at the handoff), routing apt's output to a transient log under the diff so
#   `apply_configure`'s stdout carries only the copied-file count. Returns
#   apt's exit code. A state_root (tests only) runs it against scratch apt dirs
#   under that root so no system state is touched.
run_apt_update() {
  local sources_file="$1" cache_dir="$2" state_root="${3:-}" apt_root log
  log="$cache_dir/diff/.apply-apt.log"
  if [ -n "$state_root" ]; then
    apt_root="$state_root/var"
    mkdir -p "$apt_root/lib/apt/lists/partial" \
             "$apt_root/lib/apt/parts" \
             "$apt_root/cache/apt/archives/partial" \
             "$apt_root/lib/dpkg"
    : > "$apt_root/lib/dpkg/status"
    apt-get \
        -o "Dir::Etc::sourcelist=$sources_file" \
        -o "Dir::Etc::sourceparts=-" \
        -o "Dir::Etc::apt.conf=/dev/null" \
        -o "Dir::Etc::parts=$apt_root/lib/apt/parts" \
        -o "Dir::State::lists=$apt_root/lib/apt/lists" \
        -o "Dir::State::status=$apt_root/lib/dpkg/status" \
        -o "Dir::Cache=$apt_root/cache/apt" \
        -o "Dir::Cache::archives=$apt_root/cache/apt/archives" \
        -o "Debug::NoLocking=1" \
        update > "$log" 2>&1
  else
    apt update > "$log" 2>&1
  fi
}

# apply_configure <cache_dir> <sources_file> <os_release> <sources> <releases> [<state_root>]
#   Preflight (structure + release match) -> base-set check -> copy new files
#   -> replace handoff dists -> apt update. Nothing is written until
#   validation passes. Prints the number of new files copied; returns 0 ok,
#   1 on any failure (the merged state is left in place for inspection).
apply_configure() {
  local cache_dir="$1" sources_file="$2" os_release="$3" sources="$4" releases="$5" state_root="${6:-}"
  local diff_dir="$cache_dir/diff" rc codename new_files
  if ! diff_complete "$diff_dir" "$sources"; then
    echo "Diff not found at $cache_dir/diff — is the diff complete?" >&2
    return 1
  fi
  release_match "$os_release" "$releases"
  rc=$?
  if [ "$rc" -eq 1 ]; then
    echo "Cannot determine the system release from $os_release" >&2
    return 1
  fi
  if [ "$rc" -eq 2 ]; then
    codename="$(system_codename "$os_release")"
    echo "Release mismatch: diff is for $releases, this system is $codename. Refusing to apply." >&2
    return 1
  fi
  if ! check_base_set "$diff_dir" "$cache_dir"; then
    return 1
  fi
  new_files="$(copy_new_files "$diff_dir" "$cache_dir")"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    return 1
  fi
  replace_handoff_dists "$diff_dir" "$cache_dir" "$sources"
  if ! run_apt_update "$sources_file" "$cache_dir" "$state_root"; then
    echo "apt update failed — see $cache_dir/diff/.apply-apt.log (excerpt below); the merged files and indexes were left in place for inspection." >&2
    tail -n 20 "$cache_dir/diff/.apply-apt.log" >&2 2>/dev/null || true
    return 1
  fi
  echo "$new_files"
  return 0
}

# apply_summary <new_files> <prior> <new> <releases>
#   Prints the apply summary. The count line is exactly `New files: <number>`
#   (no apt noise, no extra suffix); when nothing was copied the already-applied
#   state is reported on a separate line. Exit code stays 0 for both merged and
#   already-applied.
apply_summary() {
  local new_files="$1" prior="$2" new="$3" releases="$4"
  echo "New files:       $new_files"
  if [ "$new_files" -eq 0 ]; then
    echo "Already applied: all new files already present in the pool"
  fi
  echo "Offered after:   $((prior + new)) packages ($new new)"
  echo "Handoff indexes: replaced for $releases"
  echo "apt update: OK"
}

apply_main() {
  local cache_dir="" positional=0 arg script_dir diff_dir rc new_files
  local prior=0 new=0 line_kind manifest
  for arg in "$@"; do
    case "$arg" in
      -*)
        echo "No such option: ${arg}. Usage: apply.sh [<cache-dir>]" >&2
        exit 1
        ;;
      *)
        positional=$((positional + 1))
        if [ "$positional" -gt 1 ]; then
          echo "No such option: ${arg}. Usage: apply.sh [<cache-dir>]" >&2
          exit 1
        fi
        cache_dir="$arg"
        ;;
    esac
  done

  if [ -z "$cache_dir" ]; then
    script_dir="$(cd "$(dirname "$0")" && pwd)"
    cache_dir="$(dirname "$script_dir")"
  else
    cache_dir="$(cd "$cache_dir" 2>/dev/null && pwd || printf '%s' "$cache_dir")"
  fi

  if [ "$(id -u)" -ne 0 ]; then
    echo "Not running as root — run with sudo." >&2
    exit 1
  fi

  diff_dir="$cache_dir/diff"
  echo "Apply: merging diff into $cache_dir ($release_label)"
  new_files="$(apply_configure "$cache_dir" "" "$os_release" "$sources" "$releases")"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    exit 1
  fi

  manifest="$diff_dir/manifest.txt"
  if [ -f "$manifest" ]; then
    while read -r line_kind _ _ _; do
      [ -z "$line_kind" ] && continue
      if [ "$line_kind" = "delivered" ]; then
        prior=$((prior + 1))
      elif [ "$line_kind" = "new" ]; then
        new=$((new + 1))
      fi
    done < "$manifest"
  fi

  apply_summary "$new_files" "$prior" "$new" "$releases"
  exit 0
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  apply_main "$@"
fi
"""


class HandoffError(Exception):
    """Command-level handoff failure with a user-facing message."""


@dataclass
class HandoffStats:
    release: str
    releases: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    release_components: dict[str, tuple[str, ...]] = field(default_factory=dict)
    packages: int = 0
    per_component: dict[str, int] = field(default_factory=dict)
    excluded_pool: list[str] = field(default_factory=list)
    excluded_config: list[str] = field(default_factory=list)
    unconfirmable: list[str] = field(default_factory=list)
    delivered_ids: list[int] = field(default_factory=list)
    degraded_local: list[str] = field(default_factory=list)


@dataclass
class DiffStats:
    """The minimal diff the `sync` generator produced (spec 013).

    `prior_count` is the delivered binaries included in the complete indexes
    (the manifest base set), `new_count` the pending binaries shipped in
    `diff/pool/` (the manifest `new` lines), and `packages` their sum — the
    set apt offers after the operator applies. `shipped_ids` is the delivery
    set: the caller marks exactly those delivered (never the whole pending
    set, and never excluded records). Excluded pending records are reported
    and remain pending; an excluded delivered record is a defect state (the
    delivery record must keep matching the operator's payload).
    """

    release: str
    releases: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    release_components: dict[str, tuple[str, ...]] = field(default_factory=dict)
    packages: int = 0
    prior_count: int = 0
    new_count: int = 0
    per_component_new: dict[str, int] = field(default_factory=dict)
    shipped_ids: list[int] = field(default_factory=list)
    excluded_pending: list[str] = field(default_factory=list)
    excluded_delivered: list[str] = field(default_factory=list)
    degraded_local: list[str] = field(default_factory=list)


def _check_gpg() -> None:
    if shutil.which("gpg") is None:
        raise HandoffError(
            "gpg not found — the handoff requires gnupg on the admin system"
        )


def _assert_space_free(cache_path: str) -> None:
    """Refuse to generate a handoff whose apt `deb file:` source line would
    carry a path containing a space.

    apt sources lines are whitespace-delimited, so an unquoted path with a
    space would silently break the entry (a fragment of the path becomes the
    release/components). Fail fast with an actionable diagnostic instead of
    shipping a broken source.
    """
    if " " in cache_path:
        raise HandoffError(
            f"The cache path contains a space ({cache_path!r}); apt sources "
            "cannot carry it. Place the cache at a space-free path (e.g. a "
            "mount point or base_dir) and re-run."
        )


def _release_epoch() -> int:
    """`Date` anchor for the Release files.

    `SOURCE_DATE_EPOCH` overrides (explicit opt-in for reproducible,
    byte-identical regeneration); otherwise the generation time is used — the
    repository's `Date` reflects when it was generated, never a frozen cache
    mtime. An invalid `SOURCE_DATE_EPOCH` logs a warning and falls back to
    generation time.
    """
    source_date = os.environ.get("SOURCE_DATE_EPOCH")
    if source_date:
        try:
            return int(source_date)
        except ValueError:
            logger.warning("handoff: ignoring invalid SOURCE_DATE_EPOCH %r", source_date)
    return int(time.time())


def _verified_records(conn: sqlite3.Connection) -> list[tuple[int, str, str, str, str, str]]:
    rows = conn.execute(
        "SELECT id, package_name, version, architecture, release, component"
        " FROM binaries WHERE status = 'current' AND verified = 1"
        " ORDER BY package_name, version"
    ).fetchall()
    return [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]


def _component_bases(config) -> list[tuple[str, str]]:
    """(release, component) pairs from the config whose cached archive index
    exists — the handoff mirrors these, even when empty."""
    bases: list[tuple[str, str]] = []
    for source in config.sources:
        for component in source.components:
            index_path = resolve_index_path(
                binary_index_path(config.binary_cache, source.release, component)
            )
            if os.path.exists(index_path):
                bases.append((source.release, component))
    return bases


def _local_component_bases(conn: sqlite3.Connection, config) -> list[tuple[str, str]]:
    """The `local` component base when the cache holds local records.

    `local` is derived (its payload is the imported `.deb`, no archive index
    to confirm against), so it is served whenever at least one current +
    verified `local` binary exists — and only then, so an empty cache never
    gains a bogus `local` component.
    """
    row = conn.execute(
        "SELECT 1 FROM binaries"
        " WHERE status = 'current' AND verified = 1 AND component = 'local'"
        " LIMIT 1"
    ).fetchone()
    if row is None or not config.sources:
        return []
    return [(config.sources[0].release, "local")]


def _read_deb_control(pool_path: str) -> dict:
    """Control fields of a `local` `.deb` payload needed to emit its stanza.

    The payload is exactly the supplied `.deb`, so its control file is the
    original package the operator installs. The dependency/`Installed-Size`
    fields are apt's VersionHash inputs; `Section`/`Maintainer`/`Homepage`/
    `Description` are carried so the emitted stanza is a complete package
    description (they are not VersionHash inputs). Returns a plain dict
    (missing keys absent); an unreadable `.deb` returns `{}` so the handoff
    falls back to the DB-only reconstruction instead of failing the whole
    generation.
    """
    try:
        with debfile.DebFile(pool_path) as deb:
            control = deb.control.debcontrol()
    except Exception:
        return {}
    result: dict = {}
    for control_field in (
        "Depends",
        "Pre-Depends",
        "Installed-Size",
        "Conflicts",
        "Breaks",
        "Replaces",
        "Provides",
        "Multi-Arch",
        "Section",
        "Maintainer",
        "Homepage",
        "Description",
    ):
        value = control.get(control_field)
        if value:
            result[control_field] = value
    return result


def local_binary_entry(
    conn: sqlite3.Connection,
    config,
    rid: int,
    name: str,
    version: str,
    arch: str,
    release: str,
    component: str,
):
    """A paragraph entry for a `local` record built from the DB record and its
    pool payload (there is no archive index to confirm against).

    The dependency fields (`Depends`/`Pre-Depends`/`Conflicts`/`Breaks`/
    `Replaces`) and `Installed-Size` are read from the payload's own control
    file — the original package the operator installs — so apt's VersionHash
    matches the operator's dpkg-status record; the `Filename` follows the local
    pool layout convention and `Size`/`SHA256` come from the copied payload.
    When the payload is not a readable `.deb`, the VersionHash fields are
    reproduced from the values `import-deb` captured at import time (stored in
    `binaries.dependencies`), so the degraded fallback stays parity-correct and
    never falls back to the merged `Depends`+`Pre-Depends` view.
    Returns `(entry, rel, size, sha256_hex, used_fallback)` or `None` when the
    pool file is missing — the same integrity posture as every other component.
    `used_fallback` is True only when the payload was unreadable and the stored
    values were used.
    """
    rel = local_pool_rel_path(name, version, arch)
    pool_path = safe_pool_path(config.binary_cache, rel)
    if pool_path is None or not os.path.isfile(pool_path):
        return None
    size = os.stat(pool_path).st_size
    sha256_hex = file_sha256(pool_path)
    depends_raw = None
    stored_fields: dict[str, str] = {}
    row = conn.execute(
        "SELECT dependencies FROM binaries WHERE id = ?", (rid,)
    ).fetchone()
    if row and row[0]:
        depends_raw = ", ".join(
            _format_dep_spec(spec) for spec in deserialize_depends(row[0])
        )
        stored_fields = version_hash_fields_from_depends_json(row[0])
    # The local payload is the supplied .deb — its control file is the
    # original package (there is no archive index to confirm against). Read the
    # apt-VersionHash-relevant fields verbatim so the operator's dpkg-status
    # record hashes identically and the installed version is not reported as
    # upgradable. A readable control is authoritative (a missing `Depends`
    # stays missing — emitting the merged view would change the VersionHash);
    # only an unreadable .deb falls back to the stored field values captured at
    # import time (parity-preserving degraded fallback).
    control = _read_deb_control(pool_path)
    if control:
        depends_field = control.get("Depends")
        pre_depends_raw = control.get("Pre-Depends")
        installed_size = control.get("Installed-Size")
        conflicts_raw = control.get("Conflicts")
        breaks_raw = control.get("Breaks")
        replaces_raw = control.get("Replaces")
        section = control.get("Section")
        maintainer = control.get("Maintainer")
        homepage = control.get("Homepage")
        description = control.get("Description")
        used_fallback = False
    else:
        depends_field = stored_fields.get("Depends")
        pre_depends_raw = stored_fields.get("Pre-Depends")
        installed_size = stored_fields.get("Installed-Size")
        conflicts_raw = stored_fields.get("Conflicts")
        breaks_raw = stored_fields.get("Breaks")
        replaces_raw = stored_fields.get("Replaces")
        section = None
        maintainer = None
        homepage = None
        description = None
        used_fallback = True
    entry = BinaryPackage(
        package=name,
        version=version,
        architecture=arch,
        release=release,
        component=component,
        depends_raw=depends_raw,
        depends_field=depends_field,
        pre_depends_raw=pre_depends_raw,
        installed_size=installed_size,
        conflicts_raw=conflicts_raw,
        breaks_raw=breaks_raw,
        replaces_raw=replaces_raw,
        provides_raw=control.get("Provides") if control else None,
        multi_arch=control.get("Multi-Arch") if control else None,
        source_field=None,
        filename=rel,
        sha256=sha256_hex,
        size=str(size),
        section=section,
        maintainer=maintainer,
        homepage=homepage,
        description=description,
    )
    return entry, rel, size, sha256_hex, used_fallback


def paragraph(entry) -> str:
    lines = [
        f"Package: {entry.package}",
        f"Version: {entry.version}",
        f"Architecture: {entry.architecture}",
    ]
    section = getattr(entry, "section", None)
    if section:
        lines.append(f"Section: {section}")
    # Multi-Arch is preserved so apt can resolve `name:any` dependencies (e.g.
    # `perl:any`) against the offline index — without `Multi-Arch: foreign` on
    # the provider the operator's offline install of such packages fails.
    if getattr(entry, "multi_arch", None):
        lines.append(f"Multi-Arch: {entry.multi_arch}")
    # Installed-Size and the dependency fields are apt's VersionHash inputs: the
    # operator's dpkg-status record (produced by installing the shipped .deb)
    # must hash identically, or apt keeps the repo version separate and reports
    # the already-installed version as upgradable.
    installed_size = getattr(entry, "installed_size", None)
    if installed_size:
        lines.append(f"Installed-Size: {installed_size}")
    maintainer = getattr(entry, "maintainer", None)
    if maintainer:
        lines.append(f"Maintainer: {maintainer}")
    # `Depends` is emitted only from the original control/index field text —
    # never from the resolver's merged view (`depends_raw` folds Pre-Depends
    # targets in and would alter apt's VersionHash). A package whose control
    # carries no `Depends` field must not gain one: dpkg records exactly the
    # .deb's fields, so an invented `Depends` splits the repo version from the
    # installed dpkg-status record and the identical version is reported as
    # upgradable.
    depends = getattr(entry, "depends_field", None)
    if depends:
        lines.append(f"Depends: {depends}")
    pre_depends = getattr(entry, "pre_depends_raw", None)
    if pre_depends:
        lines.append(f"Pre-Depends: {pre_depends}")
    conflicts = getattr(entry, "conflicts_raw", None)
    if conflicts:
        lines.append(f"Conflicts: {conflicts}")
    breaks = getattr(entry, "breaks_raw", None)
    if breaks:
        lines.append(f"Breaks: {breaks}")
    replaces = getattr(entry, "replaces_raw", None)
    if replaces:
        lines.append(f"Replaces: {replaces}")
    if entry.provides_raw:
        lines.append(f"Provides: {entry.provides_raw}")
    homepage = getattr(entry, "homepage", None)
    if homepage:
        lines.append(f"Homepage: {homepage}")
    lines += [
        f"Filename: {entry.filename}",
        f"Size: {entry.size}",
        f"SHA256: {entry.sha256.lower()}",
    ]
    description = getattr(entry, "description", None)
    if description:
        lines.append(f"Description: {description}")
    return "\n".join(lines)


def local_entries(
    conn: sqlite3.Connection, config, release: str | None = None
):
    """Yield `(rid, name, version, built_or_none, used_fallback)` per verified
    current `local` row, in stable (package_name, version) order.

    `release` scopes the rows to one release (the handoff/diff per-group call
    sites); `None` — the package-stack call site — serves every current
    verified `local` row, matching that index's historical all-release
    behavior. `built_or_none` is the full `local_binary_entry` result tuple
    `(entry, rel, size, sha256_hex, used_fallback)` or `None` when the pool
    payload is missing — the caller decides its exclusion accounting. One
    shared loop for the handoff, the diff, and package-stack, so the three
    paragraph loops cannot diverge again.
    """
    query = (
        "SELECT id, package_name, version, architecture, release, component"
        " FROM binaries WHERE component = 'local' AND status = 'current'"
        " AND verified = 1"
    )
    params: tuple = ()
    if release is not None:
        query += " AND release = ?"
        params = (release,)
    query += " ORDER BY package_name, version"
    rows = conn.execute(query, params).fetchall()
    for rid, name, version, arch, rel, component in rows:
        built = local_binary_entry(
            conn, config, rid, name, version, arch, rel, component
        )
        if built is None:
            yield rid, name, version, None, False
            continue
        _entry, _rel, _size, _sha256, used_fallback = built
        yield rid, name, version, built, used_fallback


def write_gzip(path: str, text: str) -> None:
    import io

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(text.encode("utf-8"))
    atomic_write_bytes(path, buf.getvalue())


def hash_entries_for_pair(
    component: str,
    plain_rel: str,
    plain_data: bytes | None,
    gz_rel: str,
    gz_data: bytes | None,
) -> dict[str, dict]:
    """Hash entries for one component index pair: the plain file (the form
    apt fetches and verifies for `file://` sources) and the shipped `.gz`.
    `plain_rel`/`gz_rel` are relative to the component directory.

    Both forms must be listed in the `Release` or apt, once it parses the
    Release hashes, drops the index. When only the `.gz` is given, the missing
    plain hash is synthesized by decompressing it — the stored files are never
    touched.
    """
    entries: dict[str, dict] = {}

    def add(rel_path: str, data: bytes) -> None:
        entries[f"{component}/{rel_path}"] = {
            "MD5Sum": hashlib.md5(data).hexdigest(),
            "SHA1": hashlib.sha1(data).hexdigest(),
            "SHA256": hashlib.sha256(data).hexdigest(),
            "SHA512": hashlib.sha512(data).hexdigest(),
            "size": len(data),
        }

    if plain_data is not None:
        add(plain_rel, plain_data)
    if gz_data is not None:
        add(gz_rel, gz_data)
        if plain_data is None:
            add(plain_rel, gzip.decompress(gz_data))
    return entries


def _release_text(
    release: str, components: list[str], index_hashes: dict[str, dict], release_epoch: int
) -> str:
    date = time.strftime(RELEASE_DATE_FORMAT, time.gmtime(release_epoch))
    lines = [
        "Origin: OpenFromSource offline cache",
        "Label: OpenFromSource",
        f"Suite: {release}",
        f"Codename: {release}",
        "Architectures: amd64",
        f"Components: {' '.join(components)}",
        f"Date: {date}",
    ]
    for header in ("MD5Sum", "SHA1", "SHA256", "SHA512"):
        lines.append(header + ":")
        for rel_path, digests in sorted(index_hashes.items()):
            lines.append(f" {digests[header]} {digests['size']:16d} {rel_path}")
    return "\n".join(lines) + "\n"


def _run_gpg(gnupg_home: str, *args: str) -> None:
    env = dict(os.environ, GNUPGHOME=gnupg_home)
    result = subprocess.run(
        ["gpg", "--batch", "--yes", *args],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise HandoffError(
            f"gpg failed: {' '.join(args[:3])}...: {result.stderr.strip()[:200]}"
        )


def _run_gpg_to(gnupg_home: str, output_path: str, *args: str) -> None:
    """Run gpg producing `output_path` atomically (temp file + rename)."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp_path = os.path.join(
        os.path.dirname(output_path), ".tmp-" + os.path.basename(output_path)
    )
    try:
        _run_gpg(gnupg_home, "--output", tmp_path, *args)
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, output_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _secret_key_exists(gnupg_home: str) -> bool:
    env = dict(os.environ, GNUPGHOME=gnupg_home)
    result = subprocess.run(
        ["gpg", "--batch", "--yes", "--list-secret-keys", GPG_KEY_UID],
        env=env,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _ensure_key(config) -> str:
    """Ensure the cache-local GPG key exists under `{base_dir}/.gnupg-handoff/`
    (generated once, reused across handoff and diff
    regenerations) and return the GNUPGHOME path."""
    gnupg_home = handoff_gnupg_dir(config.binary_cache)
    os.makedirs(gnupg_home, exist_ok=True)
    os.chmod(gnupg_home, 0o700)
    if not _secret_key_exists(gnupg_home):
        _run_gpg(gnupg_home, "--passphrase", "", "--quick-gen-key", GPG_KEY_UID, "rsa2048", "sign", "never")
    return gnupg_home


def _write_key_and_signatures(config, releases: list[str]) -> None:
    """Generate (once) the cache-local GPG key and export/sign atomically.

    The keyring is persisted at `{base_dir}/.gnupg-handoff/`, so the
    key is generated only on the first run and reused afterwards — the shipped
    `offline-cache.gpg` public key is a stable trust anchor across
    regenerations. Every release's `Release` is signed with it. The keyring is
    deliberately outside the handoff directory, so the payload itself carries
    only the public key..
    """
    gnupg_home = _ensure_key(config)
    _run_gpg_to(
        gnupg_home,
        handoff_key_path(config.binary_cache),
        "--export", GPG_KEY_UID,
    )
    for release in releases:
        release_path = os.path.join(handoff_release_dir(config.binary_cache, release), "Release")
        signature_path = handoff_signature_path(config.binary_cache, release)
        # Detached armored signature: verified end-to-end (release without a
        # trailing blank line before the hash blocks, `Packages` + `Packages.gz`
        # hash entries, `[signed-by=.../offline-cache.gpg]`) — apt update runs
        # clean (no warnings) and the index is fetched (research.md).
        _run_gpg_to(
            gnupg_home,
            signature_path,
            "--armor", "--detach-sign", release_path,
        )


def _ensure_pool_link(config) -> None:
    link = handoff_pool_link(config.binary_cache)
    if not os.path.isdir(link):
        if os.path.lexists(link):
            os.remove(link)
        os.symlink("../pool", link)


def _ensure_source_link(config) -> None:
    """The handoff's `source` symlink -> the source cache (spec 024 Q3).

    The local deb-src index's `Directory:` paths are `source/pool/...`; the
    link makes them reachable through the handoff's `file:` source, mirroring
    the `pool` link. The target is computed relative to the link's own parent
    so the layout of the cache is not assumed.
    """
    link = handoff_source_link(config.binary_cache)
    if not os.path.isdir(link):
        if os.path.lexists(link):
            os.remove(link)
        target = os.path.relpath(config.source_cache, os.path.dirname(link))
        os.symlink(target, link)


def _write_bootstrap_script(config, releases: list[str], bases: list[tuple[str, str]]) -> None:
    script = handoff_script_path(config.binary_cache)
    release_label = (
        "release " + releases[0]
        if len(releases) == 1
        else "releases: " + ", ".join(releases)
    )
    sources_lines = [
        f"{release} {' '.join(component for rel, component in bases if rel == release)}"
        for release in releases
    ]
    content = (
        BOOTSTRAP_SCRIPT
        .replace("__RELEASE_LABEL__", release_label)
        .replace("__RELEASES__", " ".join(releases))
        .replace("__SOURCES__", "\n".join(sources_lines))
    )
    atomic_write_bytes(script, content.encode("utf-8"), mode=0o755)


def _diff_records(conn: sqlite3.Connection, bit: int):
    """Split the current+verified set by the system's sync bit.

    Returns (pending, delivered): pending = bit unset (the delta to ship),
    delivered = bit set (already on the operator — the manifest base set).
    """
    pending: list[tuple[int, str, str, str, str, str]] = []
    delivered: list[tuple[int, str, str, str, str, str]] = []
    rows = conn.execute(
        "SELECT id, package_name, version, architecture, release, component, sync_mask"
        " FROM binaries WHERE status = 'current' AND verified = 1"
        " ORDER BY package_name, version"
    ).fetchall()
    for rid, name, version, arch, release, component, mask in rows:
        if mask & bit:
            delivered.append((rid, name, version, arch, release, component))
        else:
            pending.append((rid, name, version, arch, release, component))
    return pending, delivered


def _write_apply_script(config, releases: list[str], bases: list[tuple[str, str]]) -> None:
    script = diff_script_path(config.binary_cache)
    release_label = (
        "release " + releases[0]
        if len(releases) == 1
        else "releases: " + ", ".join(releases)
    )
    sources_lines = [
        f"{release} {' '.join(component for rel, component in bases if rel == release)}"
        for release in releases
    ]
    content = (
        APPLY_SCRIPT
        .replace("__RELEASE_LABEL__", release_label)
        .replace("__RELEASES__", " ".join(releases))
        .replace("__SOURCES__", "\n".join(sources_lines))
    )
    atomic_write_bytes(script, content.encode("utf-8"), mode=0o755)


def generate_diff(config, conn: sqlite3.Connection, system_bit: int) -> DiffStats:
    """Build the minimal diff cache from the cache's verified state (spec 013).

    The pending set (current + verified, the system's `sync_mask` bit unset —
    spec 010) is the delta: only its binaries' files are copied into
    `diff/pool/` (real copies — they must physically ship), while the
    per-(release, component) `Packages.gz` cover the COMPLETE current verified
    set (delivered + shipped pending) so apt presents prior + new after the
    operator applies. `manifest.txt` carries one `new|delivered <sha256> <size>
    <pool-rel-path>` line per indexed file — the `delivered` lines are the
    base set ordering check. `Release` + `Release.gpg` are signed with
    the same cache-local key as the baseline handoff and `offline-cache.gpg` is
    that same key. The ids of the binaries actually shipped — the delivery set
    — are returned in `DiffStats.shipped_ids`, so the caller marks exactly
    those as delivered. Excluded pending records (missing index entry or pool
    file) are reported and never shipped; an excluded delivered record is a
    defect state for the caller.
    """
    _check_gpg()
    diff = diff_dir(config.binary_cache)
    os.makedirs(diff, exist_ok=True)
    # The pool is the only additive artifact: rebuild it from scratch so it
    # always contains exactly the current new files (regeneration is otherwise
    # fully overwrite-based and atomic; a stale pool would drift from the
    # manifest).
    pool_dir = diff_pool_path(config.binary_cache)
    if os.path.isdir(pool_dir):
        shutil.rmtree(pool_dir)
    os.makedirs(pool_dir, exist_ok=True)
    release_epoch = _release_epoch()

    pending, delivered = _diff_records(conn, system_bit)

    bases = _component_bases(config)
    bases.extend(_local_component_bases(conn, config))
    if not bases:
        raise HandoffError(
            "No cached archive indexes found for the configured sources; "
            "the index is part of the cache — restore or re-fetch it"
        )
    releases = list(dict.fromkeys(release for release, _ in bases))
    base_set = set(bases)

    grouped: dict[tuple[str, str], list[tuple[int, str, str, str, bool]]] = {}
    for rid, name, version, arch, release, component in pending:
        grouped.setdefault((release, component), []).append((rid, name, version, arch, True))
    for rid, name, version, arch, release, component in delivered:
        grouped.setdefault((release, component), []).append((rid, name, version, arch, False))
    for records in grouped.values():
        records.sort(key=lambda r: (r[1], r[2]))

    index_hashes: dict[str, dict[str, dict]] = {}
    paragraphs: dict[tuple[str, str], list[str]] = {}
    manifest: list[str] = []
    shipped_ids: list[int] = []
    excluded_pending: list[str] = []
    excluded_delivered: list[str] = []
    degraded_local: list[str] = []
    per_component_new: dict[str, int] = {}
    prior_count = 0
    new_count = 0

    def exclude(record, is_new: bool, reason: str) -> None:
        name, version = record[1], record[2]
        if is_new:
            excluded_pending.append(f"{name} {version}")
            logger.warning("sync excluded pending %s %s: %s", name, version, reason)
        else:
            excluded_delivered.append(f"{name} {version}")
            logger.warning("sync defect (delivered record unconfirmable) %s %s: %s", name, version, reason)

    for (release, component), records in grouped.items():
        if component == "local":
            if (release, component) not in base_set:
                for record in records:
                    exclude(record, record[4], "release/component not served by the configured sources")
                continue
            lookup = {rid: (arch, is_new) for rid, name, version, arch, is_new in records}
            for rid, name, version, built, used_fallback in local_entries(conn, config, release):
                arch, is_new = lookup[rid]
                if built is None:
                    exclude(
                        (rid, name, version, arch, is_new), is_new,
                        f"missing pool file {local_pool_rel_path(name, version, arch)}",
                    )
                    continue
                entry, rel, size, sha256_hex, _used_fallback = built
                if used_fallback:
                    degraded_local.append(f"{name} {version}")
                    logger.warning(
                        "sync degraded local fallback %s %s (%s): unreadable pool payload %s",
                        name, version, rid, local_pool_rel_path(name, version, arch),
                    )
                if is_new:
                    pool_rel = rel.removeprefix("pool/")
                    diff_dest = os.path.join(diff_pool_path(config.binary_cache), pool_rel)
                    try:
                        atomic_copy(
                            os.path.join(config.binary_cache, "pool", pool_rel),
                            diff_dest,
                            verify=(sha256_hex, size),
                        )
                    except OSError:
                        exclude((rid, name, version, arch, is_new), True, f"failed copying {rel}")
                        continue
                    shipped_ids.append(rid)
                    new_count += 1
                    per_component_new[f"{release}/{component}"] = (
                        per_component_new.get(f"{release}/{component}", 0) + 1
                    )
                else:
                    prior_count += 1
                paragraphs.setdefault((release, component), []).append(paragraph(entry))
                manifest.append(
                    f"{'new' if is_new else 'delivered'} {sha256_hex} {size} {rel}"
                )
            continue
        if (release, component) not in base_set:
            for record in records:
                exclude(record, record[4], "release/component not served by the configured sources")
            continue
        index_path = resolve_index_path(
            binary_index_path(config.binary_cache, release, component)
        )
        if not os.path.exists(index_path):
            for record in records:
                exclude(record, record[4], f"cached index missing for {release}/{component}")
            continue
        binary_index = load_binary_index(index_path, release, component)
        for rid, name, version, arch, is_new in records:
            entry = lookup_entry(binary_index, name, version, arch)
            if entry is None or not entry.filename or not entry.sha256:
                exclude((rid, name, version, arch, is_new), is_new, "no index entry/checksum")
                continue
            pool_path = safe_pool_path(config.binary_cache, entry.filename)
            if pool_path is None:
                exclude((rid, name, version, arch, is_new), is_new, f"unsafe Filename {entry.filename!r}")
                continue
            if not os.path.isfile(pool_path):
                exclude((rid, name, version, arch, is_new), is_new, f"missing pool file {entry.filename}")
                continue
            size = os.stat(pool_path).st_size
            if is_new:
                pool_root = os.path.join(config.binary_cache, "pool")
                rel = os.path.relpath(pool_path, pool_root)
                diff_dest = os.path.join(diff_pool_path(config.binary_cache), rel)
                try:
                    atomic_copy(pool_path, diff_dest, verify=(entry.sha256.lower(), size))
                except OSError:
                    exclude((rid, name, version, arch, is_new), True, f"failed copying {entry.filename}")
                    continue
                shipped_ids.append(rid)
                new_count += 1
                per_component_new[f"{release}/{component}"] = (
                    per_component_new.get(f"{release}/{component}", 0) + 1
                )
            else:
                prior_count += 1
            # Append the stanza and manifest line ONLY after the payload is
            # confirmed present: a failed copy must not leave a diff
            # that references a payload it does not contain.
            paragraphs.setdefault((release, component), []).append(paragraph(entry))
            manifest.append(
                f"{'new' if is_new else 'delivered'} {entry.sha256.lower()} {size} {entry.filename}"
            )

    for (release, component) in bases:
        text = "\n\n".join(paragraphs.get((release, component), []))
        if text:
            text += "\n"
        index_path = diff_index_path(config.binary_cache, release, component)
        write_gzip(index_path, text)
        with open(index_path, "rb") as f:
            gz_data = f.read()
        index_hashes.setdefault(release, {}).update(
            hash_entries_for_pair(
                component,
                "binary-amd64/Packages",
                text.encode("utf-8"),
                "binary-amd64/Packages.gz",
                gz_data,
            )
        )
        if component == "local":
            # The operator's source channel: the `local` `source/Sources`
            # deb-src index mirrors the handoff's, so a diff sync never drops
            # the source channel the baseline handoff serves (the source rows
            # are the same verified DB state).
            from package_mgmt.lib.service import srcindex

            component_root = os.path.join(
                diff_release_dir(config.binary_cache, release), component
            )
            index_hashes.setdefault(release, {}).update(
                srcindex.write_source_index(
                    conn, config, release, component, component_root
                )
            )

    for release in releases:
        components = [component for rel, component in bases if rel == release]
        release_path = os.path.join(
            diff_release_dir(config.binary_cache, release), "Release"
        )
        os.makedirs(os.path.dirname(release_path), exist_ok=True)
        atomic_write_bytes(
            release_path,
            _release_text(release, components, index_hashes.get(release, {}), release_epoch).encode("utf-8"),
        )

    gnupg_home = _ensure_key(config)
    _run_gpg_to(
        gnupg_home,
        diff_key_path(config.binary_cache),
        "--export", GPG_KEY_UID,
    )
    for release in releases:
        release_path = os.path.join(diff_release_dir(config.binary_cache, release), "Release")
        _run_gpg_to(
            gnupg_home,
            diff_signature_path(config.binary_cache, release),
            "--armor", "--detach-sign", release_path,
        )

    atomic_write_bytes(
        diff_manifest_path(config.binary_cache),
        ("\n".join(manifest) + "\n" if manifest else "").encode("utf-8"),
    )
    _write_apply_script(config, releases, bases)

    primary = releases[0]
    stats = DiffStats(
        release=primary,
        releases=tuple(releases),
        components=tuple(component for rel, component in bases if rel == primary),
        release_components={
            rel: tuple(component for r, component in bases if r == rel)
            for rel in releases
        },
        packages=prior_count + new_count,
        prior_count=prior_count,
        new_count=new_count,
        per_component_new=per_component_new,
        shipped_ids=shipped_ids,
        excluded_pending=excluded_pending,
        excluded_delivered=excluded_delivered,
        degraded_local=degraded_local,
    )
    logger.info(
        "diff generated: %d new across %d release(s) (%s), %d delivered in the base set",
        stats.new_count, len(releases), ", ".join(releases), stats.prior_count,
    )
    return stats


def generate_handoff(config, conn: sqlite3.Connection) -> HandoffStats:
    """Build the handoff directory from the cache's verified state.

    One apt suite is emitted for every release the configured sources cover:
    `dists/{release}/Release` + `Release.gpg` + a per-component `Packages.gz`,
    so a multi-release cache (e.g. trixie + trixie-backports) is fully served
    and `bootstrap.sh` configures one source line per release.

    Records with a missing pool file are excluded with a warning (fixable by
    re-running download); records whose index entry or checksum cannot be
    confirmed are excluded and reported as unconfirmable (a command error for
    the caller). Components from the config with a cached archive index always
    get an index (empty when nothing is verified) so the operator's source is
    valid even for an empty cache. The ids of the binaries actually included —
    the delivery set — are returned in `HandoffStats.delivered_ids`, so the
    caller marks exactly those as delivered, never the verified superset.
    """
    _assert_space_free(config.binary_cache)
    _check_gpg()
    handoff = handoff_dir(config.binary_cache)
    os.makedirs(handoff, exist_ok=True)
    release_epoch = _release_epoch()

    records = _verified_records(conn)
    grouped: dict[tuple[str, str], list[tuple[int, str, str, str]]] = {}
    for rid, name, version, arch, release, component in records:
        grouped.setdefault((release, component), []).append((rid, name, version, arch))

    bases = _component_bases(config)
    bases.extend(_local_component_bases(conn, config))
    if not bases:
        raise HandoffError(
            "No cached archive indexes found for the configured sources; "
            "the index is part of the cache — restore or re-fetch it"
        )
    releases = list(dict.fromkeys(release for release, _ in bases))
    base_set = set(bases)

    index_hashes: dict[str, dict[str, dict]] = {}
    paragraphs: dict[tuple[str, str], list[str]] = {}
    delivered_ids: list[int] = []
    excluded_pool: list[str] = []
    excluded_config: list[str] = []
    unconfirmable: list[str] = []
    degraded_local: list[str] = []
    per_component: dict[str, int] = {}

    for (release, component), recs in grouped.items():
        if component == "local":
            if (release, component) not in base_set:
                for rid, name, version, arch in recs:
                    excluded_config.append(f"{name} {version}")
                    logger.warning(
                        "handoff excluded %s %s (%s): release/component not served by the configured sources",
                        name, version, rid,
                    )
                continue
            arch_by_rid = {rid: arch for rid, name, version, arch in recs}
            for rid, name, version, built, used_fallback in local_entries(conn, config, release):
                if built is None:
                    excluded_pool.append(f"{name} {version}")
                    logger.warning(
                        "handoff excluded %s %s (%s): missing pool file %s",
                        name, version, rid, local_pool_rel_path(name, version, arch_by_rid[rid]),
                    )
                    continue
                entry, _rel, _size, _sha256, _used_fallback = built
                if used_fallback:
                    degraded_local.append(f"{name} {version}")
                    logger.warning(
                        "handoff degraded local fallback %s %s (%s): unreadable pool payload %s",
                        name, version, rid, local_pool_rel_path(name, version, arch_by_rid[rid]),
                    )
                paragraphs.setdefault((release, component), []).append(paragraph(entry))
                delivered_ids.append(rid)
            continue
        if (release, component) not in base_set:
            for rid, name, version, arch in recs:
                excluded_config.append(f"{name} {version}")
                logger.warning(
                    "handoff excluded %s %s (%s): release/component not served by the configured sources",
                    name, version, rid,
                )
            continue
        index_path = resolve_index_path(
            binary_index_path(config.binary_cache, release, component)
        )
        if not os.path.exists(index_path):
            unconfirmable.extend(f"{name} {version}" for _, name, version, _ in recs)
            logger.warning(
                "handoff excluded %d record(s) for %s/%s: cached index missing",
                len(recs), release, component,
            )
            continue
        binary_index = load_binary_index(index_path, release, component)
        for rid, name, version, arch in recs:
            entry = lookup_entry(binary_index, name, version, arch)
            if entry is None or not entry.filename or not entry.sha256:
                unconfirmable.append(f"{name} {version}")
                logger.warning(
                    "handoff excluded %s %s (%s): no index entry/checksum in %s",
                    name, version, rid, index_path,
                )
                continue
            pool_path = safe_pool_path(config.binary_cache, entry.filename)
            if pool_path is None:
                unconfirmable.append(f"{name} {version}")
                logger.warning(
                    "handoff excluded %s %s (%s): unsafe Filename %r in %s",
                    name, version, rid, entry.filename, index_path,
                )
                continue
            if not os.path.isfile(pool_path):
                excluded_pool.append(f"{name} {version}")
                logger.warning(
                    "handoff excluded %s %s (%s): missing pool file %s",
                    name, version, rid, entry.filename,
                )
                continue
            paragraphs.setdefault((release, component), []).append(paragraph(entry))
            delivered_ids.append(rid)

    for (release, component) in bases:
        text = "\n\n".join(paragraphs.get((release, component), []))
        if text:
            text += "\n"
        index_path = handoff_index_path(config.binary_cache, release, component)
        write_gzip(index_path, text)
        with open(index_path, "rb") as f:
            gz_data = f.read()
        index_hashes.setdefault(release, {}).update(
            hash_entries_for_pair(
                component,
                "binary-amd64/Packages",
                text.encode("utf-8"),
                "binary-amd64/Packages.gz",
                gz_data,
            )
        )
        if component == "local":
            # The operator's source channel (Q3/FR-008): the `local`
            # `source/Sources` deb-src index is generated from the verified DB
            # `sources` rows and hash-listed + signed in the same Release as
            # the binary index (schema-freeze: a file artifact, no new rows).
            from package_mgmt.lib.service import srcindex

            component_root = os.path.join(
                handoff_release_dir(config.binary_cache, release), component
            )
            index_hashes.setdefault(release, {}).update(
                srcindex.write_source_index(
                    conn, config, release, component, component_root
                )
            )
        per_component[f"{release}/{component}"] = len(
            paragraphs.get((release, component), [])
        )

    for release in releases:
        components = [component for rel, component in bases if rel == release]
        release_path = os.path.join(
            handoff_release_dir(config.binary_cache, release), "Release"
        )
        os.makedirs(os.path.dirname(release_path), exist_ok=True)
        atomic_write_bytes(
            release_path,
            _release_text(release, components, index_hashes.get(release, {}), release_epoch).encode("utf-8"),
        )

    _write_key_and_signatures(config, releases)
    _ensure_pool_link(config)
    _ensure_source_link(config)
    _write_bootstrap_script(config, releases, bases)

    primary = releases[0]
    stats = HandoffStats(
        release=primary,
        releases=tuple(releases),
        components=tuple(component for rel, component in bases if rel == primary),
        release_components={
            rel: tuple(component for r, component in bases if r == rel)
            for rel in releases
        },
        packages=sum(per_component.values()),
        per_component=per_component,
        excluded_pool=excluded_pool,
        excluded_config=excluded_config,
        unconfirmable=unconfirmable,
        delivered_ids=delivered_ids,
        degraded_local=degraded_local,
    )
    logger.info(
        "handoff generated: %d packages across %d release(s) (%s)",
        stats.packages, len(releases), ", ".join(releases),
    )
    return stats
