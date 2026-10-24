# Quickstart: Ensure Base Packages

Validation scenarios for spec 008. Prerequisite: system package `python3-pycdlib` (Rock Ridge ISO reading).

```bash
sudo apt install python3-pycdlib
```

All scenarios reuse the standard test bootstrap:

```bash
export PYTHONPATH=src
python3 -m unittest discover -s tests         # full suite green
```

## 0. Synthetic installer image (test fixture)

No root and no real DVD are required for validation. The fixture builder
`tests/fixtures/make_test_iso.py` uses pycdlib's write support to produce a
small Rock Ridge ISO with a mini release tree (a few `dists/trixie/main/...`
indexes and pool `.deb` placeholders, plus a tiny `contrib` component):

```bash
python3 tests/fixtures/make_test_iso.py /tmp/opencode/test-dvd.iso
```

For production use, copy the real image instead:

```bash
sudo mkdir -p /var/cache/package_mgmt/iso
cp debian-13.0.0-amd64-DVD-1.iso /var/cache/package_mgmt/iso/
```

## 1. One-command ingestion

```bash
package_mgmt init
package_mgmt ensure-base
# Expect: the ISO image line (release + components), Binary packages/Provides/
# Index/image mismatches counts, Base packages resolved X/Y, no warnings on a
# full-size image. The fixture image (~5 packages) resolves fully against the
# configured archive.
```

Verify in the database:

```bash
sqlite3 /var/cache/package_mgmt/package_mgmt.db \
  "SELECT count(*) FROM binaries WHERE release = 'trixie' AND status = 'current'"
# Expect >= fixture package count; verified flags follow the add rule — nothing
# sets verified=1, same as after `add`:
sqlite3 /var/cache/package_mgmt/package_mgmt.db \
  "SELECT count(*) FROM binaries WHERE verified = 1"
# Expect 0 (registration never sets the flag; a later download/verification stage will)
```

## 2. Same-as-`add` verified flags and mismatch diagnostics (US2)

Rebuild the fixture ISO with one pool file omitted (the fixture builder's
`--omit <file>` option):

```bash
python3 tests/fixtures/make_test_iso.py /tmp/opencode/test-dvd.iso --omit pool/main/v/vifm/vifm_1.0-2_amd64.deb
package_mgmt ensure-base
# Expect: "Index/image mismatches: 1" naming the omitted path (warning only);
# the record is registered normally, verified=0 like every other record:
sqlite3 /var/cache/package_mgmt/package_mgmt.db \
  "SELECT verified FROM binaries WHERE package_name = 'vifm'"
# Expect 0 — the flag semantics never change due to image presence.
```

## 3. Netinst / minimal warning (FR-011)

A fixture built with `--minimal` (few packages, below the 1,000 threshold):

```bash
python3 tests/fixtures/make_test_iso.py /tmp/opencode/mini.iso --minimal
package_mgmt ensure-base
# Expect: the warning "looks like a netinst or minimal image..." while the run completes.
```

## 4. Idempotency (US4)

```bash
package_mgmt ensure-base
sqlite3 /var/cache/package_mgmt/package_mgmt.db "SELECT count(*) FROM binaries"   # snapshot A
package_mgmt ensure-base
sqlite3 /var/cache/package_mgmt/package_mgmt.db "SELECT count(*) FROM binaries"   # snapshot B == A
package_mgmt ensure-base 2>&1  # no duplicate records, no new superseding
```

## 5. Behaves like `add` (US3)

After `ensure-base`, resolution results match a plain `add` on the same names:

```bash
package_mgmt show vifm --deps       # tree present, same formats as add-fetched records
package_mgmt verify --graph         # image records counted with the same rules
sqlite3 /var/cache/package_mgmt/package_mgmt.db \
  "SELECT count(*) FROM binary_to_source"   # links created by the existing flow
package_mgmt add <another-package-that-depends-on-an-image-record>
# Expect: dependency satisfied by the image record; no duplicate rows.
```

## 6. Error handling (FR-010, FR-009)

```bash
rm -rf /var/cache/package_mgmt/iso/*      # no image
package_mgmt ensure-base
# Expect: error JSON on stderr, exit 1, database untouched.

echo "not an iso" > /var/cache/package_mgmt/iso/fake.iso
package_mgmt ensure-base
# Expect: "Failed to read ISO image ..." error, exit 1.
```

## 7. Config surface (FR-002)

```bash
package_mgmt config          # text output now includes iso_dir
package_mgmt config --format json | python3 -c \
  "import json,sys; print(json.load(sys.stdin)['paths']['iso_dir'])"
```

Override in `project.local.toml`:

```toml
[paths]
iso_dir = "{base_dir}/iso-alt"
```

```bash
package_mgmt init            # creates iso-alt
package_mgmt config | grep iso_dir   # shows the override
```

## 8. Real-image smoke test (optional, admin machine)

```bash
sudo mkdir -p /var/cache/package_mgmt/iso
sudo cp debian-13.0.0-amd64-DVD-1.iso /var/cache/package_mgmt/iso/
package_mgmt ensure-base
# Expect: ~4,200+ Binary packages, Index/image mismatches 0, verified flags all 0
# (same as add), progress "Resolved X/Y" during the batch, errors listed at the
# end (capped at 50 on console, full list in the log), then the summary block.
```
