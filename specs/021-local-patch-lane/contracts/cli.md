# Contracts: Local Patch Lane

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md) · **Data model**: [data-model.md](../data-model.md)

**Contract policy.** The exit-code/JSON contract table in
`specs/014-base-profiles/contracts/cli.md` is **extended, not diverged from**;
`tests/contract/test_cli.py` stays green throughout. Exit codes are unchanged: 0
success, 1 general error, 100 permission, 101 stale/missing database, 102 config error.

---

## Phase 1 — `import-deb` registers a local package (FR-001…FR-004)

**Contract.** `import-deb <file> [--source <dir>]` imports exactly one locally built
`.deb` file into the `local` component; passing more than one `.deb` is an error. There
is **no** component selector — a request to name a component (e.g. `--component foo`)
is an unrecognized-
option CLI error. Every declared dependency of the imported package must be satisfiable
from the cache, otherwise the import fails atomically with zero DB and pool changes.
The payload lands in the pool under the identical component layout, the record is
marked `verified`, and the package is listable/showable/verifiable like any other.

```text
# a locally built patched package whose deps are all in the cache
$ package_mgmt import-deb libxkbcommon-x11-0_1.7.0-2_amd64.deb
Imported libxkbcommon-x11-0 1.7.0-2 (amd64) into local component (verified)
# exit 0

$ package_mgmt import-deb foo.deb --component main
usage: package_mgmt import-deb [-h] [--source PATH] deb
package_mgmt import-deb: error: unrecognized arguments: --component main
# exit 2 (argparse), nothing imported

$ package_mgmt import-deb missing.deb
Error: Cannot read package file: missing.deb
# exit 1, zero changes
```

**Assertions**:
- A valid `.deb` with satisfiable deps imports into `local` (`component='local'`,
  `verified=1`), payload at `pool/local/<prefix>/<pkg>/<pkg>_<ver>_<arch>.deb`, `list`/
  `show` display it, `verify` reports it complete, exit 0.
- A `.deb` declaring an unsatisfiable dependency fails with an error naming the
  dependency, exit 1, zero DB/pool changes.
- A missing/unreadable/corrupt/non-`.deb` file fails with a clear error, exit 1, zero
  changes.
- Re-importing the identical binary is idempotent (no duplicate rows), exit 0.
- No component selector is accepted (argparse error, nothing imported).
- Passing more than one `.deb` (`import-deb a.deb b.deb`) is an error, exit 1 (or the
  argparse contract), nothing imported.

---

## Phase 2 — `--source` imports provenance (FR-005)

**Contract.** `import-deb <file> --source <dir>` also registers the rebuilt source and
links it to the imported binary via `binary_to_source`, so the pair is deterministically
rebuildable. `--source` is a **directory** containing the complete source set — the
`.dsc` plus every file it references (a bare `.dsc` cannot be `dpkg-source -x`'d, so a
single-file `--source` is rejected). Every referenced file must be present and match the
`.dsc`'s declared checksums. Without `--source`, no link is created and `verify` does
not fail on the missing source. An incomplete or checksum-mismatched source set fails
the import with zero changes.

```text
$ package_mgmt import-deb libxkbcommon-x11-0_1.7.0-2_amd64.deb --source ~/src
Imported libxkbcommon-x11-0 1.7.0-2 (amd64) into local component (verified)
Source linked (libxkbcommon 1.7.0-2, local, 3 files verified against dsc)
# exit 0 — ~/src holds libxkbcommon_1.7.0-2.dsc + .orig.tar.xz + .debian.tar.xz
```

**Assertions**:
- With `--source <dir>` (complete, checksum-valid set): source row `component='local'`,
  `verified=1`, every referenced file copied to the source pool, `binary_to_source` link
  present, exit 0.
- With `--source <dir>` missing any referenced file (or a file whose checksum does not
  match the `.dsc`): clear error naming the missing/mismatched file, exit 1, binary NOT
  registered.
- With `--source` pointing at a file or a non-directory: clear error, exit 1, nothing
  registered.
- Without `--source`: no source row, no link, `verify` passes, exit 0.

---

## Phase 3 — `local` wins in implicit resolution (FR-006)

**Contract.** Without an explicit component request, a `local` package is preferred over
the same-named package in any upstream component at equal version (the existing
version-then-component tie-break, with `local` on top). Explicit component requests and
upstream-only packages behave unchanged.

**Assertions**:
- With the same `(name, version, arch)` in `main` and `local`, an unqualified lookup
  resolves to `local`.
- With only the `main` copy present, resolution is unchanged (existing component order).
- A strictly newer upstream version still wins over an older `local` version (upgrade
  semantics preserved).

---

## Phase 4 — distribution through the same channel (FR-007)

**Contract.** `handoff` and `sync` serve the `local` component like any other: the
`deb` line for the default release lists `local` alongside the archive components, the
per-component `Packages.gz` for `local` is generated from the DB, and the exclusion
filters carry it. The operator installs and upgrades the patched package with ordinary
apt.

```text
# bootstrap.sh on the operator
deb [signed-by=…/offline-cache.gpg] file:… trixie main local

$ sudo apt install libxkbcommon-x11-0
# installs the patched local package from the cache, no network
```

**Assertions**:
- `bootstrap.sh`'s `deb` line for the default release contains `local` when local
  records exist; the `Release` `Components:` line lists it; the handoff/diff contains
  `pool/local/…` payloads and the `local/binary-amd64/Packages.gz` index.
- A local record with a missing pool file is excluded with a warning (same integrity
  posture as other components).
- An empty cache (no local records) does not gain a bogus `local` component.
- Applying a diff/handoff and running `apt install` / `apt upgrade` on the operator
  installs and updates the patched package with zero network access.
