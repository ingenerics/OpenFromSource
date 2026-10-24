# Quickstart: Package Download

Validation scenarios for spec 011. Runs fully offline: a local HTTP server
serves the scratch archive's pool; no real downloads.

```bash
export PYTHONPATH=src
python3 -m unittest discover -s tests         # full suite green
```

## Prerequisites: scratch cache + local mirror server

The cache and the mirror are **separate trees**: `write_archive` places the
indexes into the cache (where `add` reads them), while the mirror holds only
the shared pool it serves. Keeping them apart means deleting or corrupting a
cache pool file never affects what the mirror serves.

```bash
mkdir -p /tmp/opencode/download && cd /tmp/opencode/download
# project.defaults.toml with base_dir = "/tmp/opencode/download/cache",
# [fetch] source = true, and an [[archive.source]] whose mirror points at
# the local server port (e.g. http://127.0.0.1:8765), release trixie,
# components ["main", "contrib"]. Then:
PYTHONPATH=src python3 -m package_mgmt init
python3 -c "
import sys; sys.path.insert(0, 'tests/fixtures')
import make_test_iso
make_test_iso.write_archive('cache')            # indexes into the cache trees
make_test_iso.write_pool_files('mirror')        # real bytes at index paths (mirror pool)
make_test_iso.serve_pool('mirror', port=8765)   # threaded HTTP server (blocking helper for tests; in a shell, run it in the background)
"
PYTHONPATH=src python3 -m package_mgmt add vifm lib1
```

## 1. Happy path — binary phase

```bash
PYTHONPATH=src python3 -m package_mgmt download
# Expect: elapsed line + progress bar updating in place (binary phase),
# then the binary phase summary; every file present:
find cache/binary/pool -name '*.deb' | wc -l      # matches the vifm tree
sqlite3 cache/package_mgmt.db \
  "SELECT count(*) FROM binaries WHERE status='current' AND verified=0"   # 0
```

## 2. Idempotent re-run

```bash
PYTHONPATH=src python3 -m package_mgmt download
# Expect: "Binary phase: nothing to download." (or zero-progress summary),
# exit 0, no changes.
```

## 3. Source phase

With `fetch_source = true` (above), the same run also downloaded sources:

```bash
find cache/source/pool -name '*.dsc' -o -name '*.tar*' | wc -l   # > 0
sqlite3 cache/package_mgmt.db \
  "SELECT count(*) FROM sources WHERE verified=0"                  # 0
```

## 4. Delete one file, re-run

Deleting a cache pool file does not affect the mirror's copy:

```bash
rm cache/binary/pool/main/v/vifm/vifm_1.0-2_amd64.deb
PYTHONPATH=src python3 -m package_mgmt download
# Expect: exactly that file re-downloaded and re-verified; exit 0.
```

## 5. Checksum mismatch

A checksum failure means the *downloaded* content does not match the index
entry, so the mirror copy is corrupted (the cache copy alone is simply
re-fetched):

```bash
python3 -c "open('mirror/binary/pool/main/v/vifm/vifm_1.0-2_amd64.deb','wb').write(b'corrupt')"
rm cache/binary/pool/main/v/vifm/vifm_1.0-2_amd64.deb
PYTHONPATH=src python3 -m package_mgmt download
# Expect: failure line naming the file with expected/actual checksums,
# the downloaded copy removed, exit 1; a re-run after restoring the server
# copy converges.
python3 -c "
import sys; sys.path.insert(0, 'tests/fixtures')
import make_test_iso
open('mirror/binary/pool/main/v/vifm/vifm_1.0-2_amd64.deb','wb').write(
    make_test_iso._pool_content('vifm_1.0-2_amd64.deb'))
"
PYTHONPATH=src python3 -m package_mgmt download   # converges, exit 0
```

## 6. Rate-limited server (mirror politeness)

Point a fresh scratch cache at a test server that answers the first 3
requests per path with `429 Retry-After: 1` and then succeeds:

```bash
mkdir -p /tmp/opencode/download-rl && cd /tmp/opencode/download-rl
# project.defaults.toml as above, mirror = http://127.0.0.1:8766
PYTHONPATH=src python3 -m package_mgmt init
python3 -c "
import sys; sys.path.insert(0, '/tmp/opencode/download/tests/fixtures')
import make_test_iso
make_test_iso.write_archive('cache')
make_test_iso.write_pool_files('mirror')
make_test_iso.serve_rate_limited('mirror', port=8766, limit=3, retry_after='1')
"
PYTHONPATH=src python3 -m package_mgmt add vifm lib1
PYTHONPATH=src python3 -m package_mgmt download
# Expect: downloads still complete; backoff pauses visible in timing;
# no refusal in the server log; exit 0.
```

## 7. fetch_source = false

With `[fetch] source = false`, a fresh hydration + `download`:

```bash
PYTHONPATH=src python3 -m package_mgmt download
# Expect: binary phase runs, source phase prints "nothing to download"
# (or is skipped with a note), exit 0 — never an error.
```

## 8. Missing cached index

```bash
rm cache/binary/dists/trixie/main/binary-amd64/Packages.gz
PYTHONPATH=src python3 -m package_mgmt download
# Expect: clear JSON error (stderr) telling the admin the index is part of
# the cache — restore or re-fetch — exit 1; nothing downloaded.
```
