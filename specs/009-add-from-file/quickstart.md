# Quickstart: Add from File

Validation scenarios for spec 009. Reuses the standard test bootstrap and the
scratch archive fixture from spec 008:

```bash
export PYTHONPATH=src
python3 -m unittest discover -s tests         # full suite green
```

All scenarios run against a scratch cache with a synthetic archive index
(`tests/fixtures/make_test_iso.py`):

```bash
mkdir -p /tmp/opencode/addfile
cd /tmp/opencode/addfile
# project.defaults.toml pointing base_dir at a scratch cache with a
# [[archive.source]] (unreachable mirror is fine — the scratch archive is
# written locally), then:
PYTHONPATH=src python3 -m package_mgmt init
python3 -c "
import sys; sys.path.insert(0, 'tests/fixtures')
import make_test_iso
make_test_iso.write_archive('/tmp/opencode/addfile/cache')
"
```

## 1. One file, one run

```bash
printf 'vifm\nlib1=2.0-1\napp\n' > pkgs.txt
PYTHONPATH=src python3 -m package_mgmt add --file pkgs.txt
# Expect: "Resolved 3/3" progress, summary block:
#   Packages requested:  3
#   Packages resolved:   3
#   Errors:              0   (or "Errors:" with the list when failures exist)
```

Database parity with positional `add` (contract §1):

```bash
sqlite3 .../package_mgmt.db "SELECT count(*) FROM binaries WHERE verified = 1"   # 0
```

## 2. File equals positional add

Run `add vifm lib1=2.0-1 app` on a fresh cache — identical table contents to
scenario 1 (same records, same `verified = 0`).

## 3. Batch reporting shape

A file with more than 50 failing names shows the console cap:

```bash
seq 1 70 | sed 's/.*/ghost-pkg-&/' > broken.txt
PYTHONPATH=src python3 -m package_mgmt add --file broken.txt
# Expect: "Errors:              70 (showing first 50; 20 more in the log)"
# and 50 "  - ..." lines; the full 70-item list in logs/package_mgmt.log.
```

## 4. Mixed input is an error

```bash
PYTHONPATH=src python3 -m package_mgmt add --file pkgs.txt vim
# Expect: error JSON on stderr (both input forms given), exit 1,
# database untouched.
```

## 5. Malformed line — all-or-nothing

```bash
printf 'vim\nvim (>= 2.0)\nlib1\n' > bad.txt
PYTHONPATH=src python3 -m package_mgmt add --file bad.txt
# Expect: error JSON naming the file and line 2, exit 1, NOTHING processed
# (vim and lib1 must not be registered).
```

## 6. Missing and empty files

```bash
PYTHONPATH=src python3 -m package_mgmt add --file /nonexistent.txt   # error, exit 1
: > empty.txt
PYTHONPATH=src python3 -m package_mgmt add --file empty.txt           # error, exit 1
```

## 7. Comments, blanks, duplicates, pins

```bash
printf '# rebuild manifest\n\nvim = 2.0-1\nvim\n' > messy.txt
# Note: "vim = 2.0-1" (spaces around =) — the pin is trimmed per line but
# the pin syntax itself is name=version; behavior per contract.
PYTHONPATH=src python3 -m package_mgmt add --file messy.txt
# Duplicate names resolve once per batch semantics; no duplicate DB rows.
```

## 8. Idempotent re-run

```bash
PYTHONPATH=src python3 -m package_mgmt add --file pkgs.txt
# snapshot table counts, run again — identical counts/contents.
```

## 9. Positional add unchanged

```bash
PYTHONPATH=src python3 -m package_mgmt add vim lib1 app
# Expect the existing per-name "Package X resolved from ..." lines — the
# file input does not alter positional output.
```
