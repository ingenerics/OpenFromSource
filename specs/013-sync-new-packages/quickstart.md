# Quickstart: Sync New Packages

**Feature**: [spec.md](spec.md) | **Validation guide** — prove the incremental sync works end to end. Contracts: [contracts/cli.md](contracts/cli.md); state model: [data-model.md](data-model.md).

## Prerequisites

- A cache already handed off to a system via spec 012: `handoff --system <moniker>` ran, `{cache}/handoff/` exists, the operator bootstrapped, and the spec 010 snapshot marks the baseline delivered.
- A hydrated, growing admin cache (`add`/`ensure-base` + `download` + `verify`) — new packages registered and verified since the baseline.
- No network access anywhere in the operator flow.

## Scenario 1 — Admin produces the minimal diff (US1/FR-001/FR-003/FR-005)

After growing the cache:

```bash
package_mgmt sync --system prod-box
```

**Expected**: `Sync ready: {binary_cache}/diff` summary with `New packages: N`,
`Total offered: prior+N`, and `Snapshot: N binaries marked for 'prod-box'`. The
directory contains `apply.sh`, `offline-cache.gpg`, `manifest.txt`, `pool/`
(only the N new files — verify `du -sh diff/pool` ≈ the sum of the new `.deb`
sizes), and `dists/{release}/{component}/binary-amd64/Packages.gz` + `Release` +
`Release.gpg`; the `Packages.gz` paragraphs cover all current verified binaries
(prior + new); the `systems`/`sync_mask` record (spec 010) now marks the N new
binaries delivered.

**Re-run**: a second `package_mgmt sync --system prod-box` reports `New packages: 0`
and produces no new diff (the snapshot already advanced).

## Scenario 2 — Operator pulls the diff into the system cache (US2/FR-007/FR-008/FR-009)

Copy the diff directory into the existing cache (or mount it beside it), then apply:

```bash
cp -r /media/usb2/diff /media/usb/cache/diff
sudo /media/usb/cache/diff/apply.sh          # arg optional: defaults to the cache the script ships in
apt update          # no network; succeeds cleanly (signature verified via signed-by)
apt list | grep <a-new-package>              # new package is now offered
apt install <a-new-package>                  # installs from the cache, no network
```

**Expected**: `apt update` lists prior + new packages; the new package installs
from the cache with zero network and zero manual apt edits; previously installed
packages keep working and every previously offered package remains available.

## Scenario 3 — Trust and ordering (US3/FR-010/FR-011/FR-012)

- **Release mismatch**: run `apply.sh` on a system whose `VERSION_CODENAME`
  differs from the diff release → `Release mismatch … Refusing to apply`, exit 1,
  nothing changed.
- **Missed intermediate diff**: delete one of the diff's `delivered` files from
  the operator's pool (simulating a missed earlier diff) → the apply refuses with
  `Missing intermediate sync: …`, exit 1, and the handoff/pool are unchanged.
- **Corrupt diff**: tamper with a file in `diff/pool/` (or its manifest line) →
  `Checksum mismatch … Refusing to apply`, exit 1, nothing copied.
- **Exact set**: after a successful apply, `apt list` shows exactly the prior +
  new verified set; a package NOT in the pool fails with apt's normal "unable to
  locate" (no phantom offers).

## Scenario 4 — Re-runs and failures (US3/FR-012/FR-013)

- Re-run `apply.sh` → `New files: 0 (already applied)`, success, no duplicates,
  no breakage.
- Interrupt an apply mid-copy → no partial file remains in the pool (temp files
  cleaned up); a re-run converges to the same state as a single clean run.
- `apt update` failure after the merge (e.g. an unreadable index) → the merged
  files and indexes are left in place for inspection, the error is reported, and
  a re-run after fixing the cause converges.

## Contract verification

- `sync` stdout shape, exit codes, and JSON errors: [contracts/cli.md](contracts/cli.md) — `package_mgmt sync`
- Apply script output/exit/error text and its testability (unit-testable functions, no test hooks): [contracts/cli.md](contracts/cli.md) — `apply.sh`

## Automated validation

The integration suite (`tests/integration/test_diff_flow.py`) replicates the full
cycle against the fixture cache: baseline `handoff` + snapshot → `add`/`download`
new packages → `sync` → apply into a scratch cache → real `apt-get`
with scratch apt directories (no root required) confirming prior + new are
offered and the new package installs. The `apply.sh` logic is unit-tested
(`tests/unit/test_apply_script.py`) by sourcing its functions with scratch paths —
no root, no host modification, no test hooks.
