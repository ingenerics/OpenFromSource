# Quickstart: Sync Mask & Snapshot

Validation scenarios for spec 010. Reuses the standard test bootstrap and the
scratch archive fixture from spec 008:

```bash
export PYTHONPATH=src
python3 -m unittest discover -s tests         # full suite green
```

All scenarios run against a scratch cache with a synthetic archive index
(`tests/fixtures/make_test_iso.py`). Scenarios 2 and 4 need versions the stock
fixture lacks (`extra-pkg`, `lib1=1.0-1`); extend the main index after
`write_archive` (same technique as `tests/integration/test_sync_snapshot_flow.py`):

```bash
python3 -c "
import sys; sys.path.insert(0, 'tests/fixtures')
import make_test_iso, gzip
pkg = make_test_iso.BASE_PACKAGES + [('lib1','1.0-1',None,None), ('extra-pkg','1.0-1',None,None)]
gz = make_test_iso._gzip_bytes(make_test_iso._index_text(pkg, 'main'))
open('/tmp/opencode/snapshot/cache/binary/dists/trixie/main/binary-amd64/Packages.gz','wb').write(gz)
src = make_test_iso._gzip_bytes('\n\n'.join(f'Package: {n}\nVersion: {v}' for n,v,_,_ in pkg)+'\n')
open('/tmp/opencode/snapshot/cache/source/dists/trixie/main/source/Sources.gz','wb').write(src)
"
```

```bash
mkdir -p /tmp/opencode/snapshot
cd /tmp/opencode/snapshot
# project.defaults.toml pointing base_dir at a scratch cache with a
# [[archive.source]] (unreachable mirror is fine — the scratch archive is
# written locally), then:
PYTHONPATH=src python3 -m package_mgmt init
python3 -c "
import sys; sys.path.insert(0, 'tests/fixtures')
import make_test_iso
make_test_iso.write_archive('/tmp/opencode/snapshot/cache')
"
```

## 1. Registry lifecycle

```bash
PYTHONPATH=src python3 -m package_mgmt systems add bravo-main --note "main box"
PYTHONPATH=src python3 -m package_mgmt systems add lab-box
PYTHONPATH=src python3 -m package_mgmt systems list
# Expect: two rows (NAME/NOTE/DELIVERED), both DELIVERED = 0; no numeric
# identifier anywhere in the output.
PYTHONPATH=src python3 -m package_mgmt systems add bravo-main
# Expect: {"error": "System 'bravo-main' already exists", ...} exit 1
PYTHONPATH=src python3 -m package_mgmt systems rename bravo-main bravo-laptop
PYTHONPATH=src python3 -m package_mgmt systems remove lab-box
# "lab-box" gone from list; re-adding it later behaves as never-synced.
```

## 2. Two-points-in-time delta (the essence)

No command sets `verified = 1` yet (download/verification is a future spec),
so downloaded files are simulated by setting the flag directly. Only current
**and verified** binaries qualify — nothing else is ever marked:

```bash
PYTHONPATH=src python3 -m package_mgmt add vifm lib1 app
PYTHONPATH=src python3 -m package_mgmt snapshot --system bravo-main --dry-run
# Expect: "0 pending for 'bravo-main'" — registered but not downloaded

sqlite3 .../package_mgmt.db "UPDATE binaries SET verified = 1"   # simulate download
PYTHONPATH=src python3 -m package_mgmt snapshot --system bravo-main --dry-run
# Expect: "<N> pending for 'bravo-main'"
PYTHONPATH=src python3 -m package_mgmt snapshot --system bravo-main
# Expect: "Marked <N> binaries for 'bravo-main'"
PYTHONPATH=src python3 -m package_mgmt snapshot --system bravo-main
# Expect: "Marked 0 binaries for 'bravo-main'" (idempotent)

PYTHONPATH=src python3 -m package_mgmt add extra-pkg
sqlite3 .../package_mgmt.db "UPDATE binaries SET verified = 1"   # simulate download
PYTHONPATH=src python3 -m package_mgmt snapshot --system bravo-main --dry-run
# Expect: the count of exactly the binaries that became current AND verified
# after the first snapshot (the delta between the two points in time).
```

## 3. Mark-after-copy discipline

The snapshot is a pure assertion — the copy is the administrator's flow:

```bash
# after a successful handover only:
PYTHONPATH=src python3 -m package_mgmt snapshot --system bravo-main
```

## 4. Supersede fix

```bash
PYTHONPATH=src python3 -m package_mgmt add lib1=1.0-1
PYTHONPATH=src python3 -m package_mgmt add lib1=2.0-1
PYTHONPATH=src python3 -m package_mgmt add lib1=1.0-1
# lib1 must have EXACTLY ONE current binary (1.0-1); 2.0-1 superseded:
sqlite3 .../package_mgmt.db \
  "SELECT count(*) FROM binaries WHERE package_name='lib1' AND status='current'"
# Expect: 1
```

## 5. Stale database diagnosis

Take the pre-feature schema (drop the new objects in a scratch DB, or use a
DB created by an older checkout):

```bash
sqlite3 .../package_mgmt.db "DROP TABLE systems"
PYTHONPATH=src python3 -m package_mgmt systems list
# Expect: a clear error describing the rebuild procedure, exit 101 —
# NOT an obscure "no such table" traceback.
```

## 6. Idempotency and unknown systems

```bash
PYTHONPATH=src python3 -m package_mgmt snapshot --system ghost
# Expect: {"error": "Unknown system 'ghost'", ...} exit 1, nothing marked
PYTHONPATH=src python3 -m package_mgmt snapshot
# Expect: {"error": "snapshot requires --system <name>", ...} exit 1
```
