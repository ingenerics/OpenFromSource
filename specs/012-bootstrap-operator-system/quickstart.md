# Quickstart: Bootstrap Operator System

**Feature**: [spec.md](spec.md) | **Validation guide** — prove the feature end to end. Contracts: [contracts/cli.md](contracts/cli.md); state model: [data-model.md](data-model.md).

## Prerequisites

- A hydrated cache (`init` + `add`/`ensure-base` + `download`) — see specs 001-011 quickstarts.
- A scratch Debian-like environment for the operator side (the integration tests use the fixture archive + a scratch apt root; a chroot/VM is the manual equivalent).
- No network access anywhere in the operator flow.

## Scenario 1 — Admin prepares the handoff (US1/FR-003/FR-009)

```bash
package_mgmt handoff --system prod-box
```

**Expected**: `Handoff ready: {binary_cache}/handoff` summary including
`Snapshot: N binaries marked for 'prod-box'`; the directory contains
`bootstrap.sh`, `offline-cache.gpg`, `pool -> ../pool`, and
`dists/{release}/{component}/binary-amd64/Packages.gz` + `Release` +
`Release.gpg`; every paragraph in each filtered `Packages.gz` corresponds to a
verified binary whose pool file exists (spot-check counts against
`SELECT count(*) FROM binaries WHERE verified = 1`); the `systems`/`sync_mask`
delivery record (spec 010) marks those binaries for `prod-box`.

**Re-run**: a second `handoff --system prod-box` succeeds identically
(idempotent — the snapshot re-marks without double-counting).

## Scenario 2 — Operator bootstraps the offline system (US1/FR-001/FR-002)

With the cache on media at, say, `/media/usb/cache`:

```bash
sudo /media/usb/cache/handoff/bootstrap.sh           # arg optional: defaults to the cache the script ships in
sudo /media/usb/cache/handoff/bootstrap.sh /media/usb/cache   # explicit path for relocated caches
apt update          # no network; succeeds cleanly (signature verified via signed-by)
apt install <a-cached-package>
```

**Expected**: `apt update` shows the offline cache source; the package
installs from the cache with zero network access and zero manual apt edits.
`/etc/os-release` `VERSION_CODENAME` matches the cache release.

## Scenario 3 — Trust and exactness (US2/FR-003/FR-004)

- **Invalid cache**: delete `handoff/dists/{release}/Release.gpg` (or the `pool`
  target) → the script refuses with `Handoff not found …`, exit 1, and no
  sources entry is written.
- **Release mismatch**: run the script on a system whose `VERSION_CODENAME`
  differs from the cache release → `Release mismatch … Refusing to configure`,
  exit 1, nothing written.
- **Exact set**: `apt list` shows exactly the verified pool; installing a
  package NOT in the pool fails with apt's normal "unable to locate" (no
  phantom offers).

## Scenario 4 — Re-runs and removal (US3/FR-005/FR-006/FR-008)

- Re-run `bootstrap.sh` (or with an explicit `{cache}` arg) → success, single
  source entry (no duplicates).
- Interrupt a run before completion → no sources entry exists.
- `sudo bootstrap.sh {cache} --remove` (arg optional, same default) → entry
  gone; `apt` behaves as before (verified via `ls /etc/apt/sources.list.d/`
  and `apt update`).
- **Component adoption**: when a *new* component is added to the handoff after
  the system was bootstrapped (e.g. a source stack served through the `local`
  component, or `non-free-firmware`), the existing sources entry does not
  carry it — `apt update` fetches only the configured components and the new
  packages are invisible. Re-run `bootstrap.sh` to regenerate the entry with
  the current component set (`bootstrap.sh` is idempotent, FR-005), then
  verify the fetched `deb` line lists the component and
  `apt search`/`apt-cache policy` resolves the expected packages.
- `sync`/`apply.sh` is **not** the adoption path: it delivers incremental
  packages within already configured components and never rewrites the apt
  sources entry. A component missing from the sources file is always fixed by
  re-running `bootstrap.sh`.

## Contract verification

- `handoff` stdout shape, exit codes, and JSON errors: [contracts/cli.md](contracts/cli.md) — `package_mgmt handoff`
- Script output/exit/error text and its testability (unit-testable functions, no test hooks): [contracts/cli.md](contracts/cli.md) — `bootstrap.sh`

## Automated validation

The integration suites (`tests/integration/test_handoff_flow.py`) replicate the
admin handoff and the operator's real `apt-get` consumption against the fixture
cache with scratch apt directories (no root required); the operator `bootstrap.sh`
logic is unit-tested (`tests/unit/test_bootstrap_script.py`) by sourcing its
functions with scratch paths — no root, no host modification, no test hooks.
