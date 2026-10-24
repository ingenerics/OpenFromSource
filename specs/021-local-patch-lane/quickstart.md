# Quickstart / Validation: Local Patch Lane

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md) · **Data model**: [data-model.md](data-model.md)
· **Contracts**: [contracts/cli.md](contracts/cli.md)

This is the runnable validation guide for the local patch lane. It proves the feature
end-to-end: import a locally built `.deb` into the `local` component, ship it through
`handoff`/`sync`, and install it on an offline operator with ordinary apt.

Prerequisites: the project installed (`pip install -e .`), both test runners (`pytest`
and `python -m unittest discover -s tests`), and a scratch cache dir configured via a
`project.local.toml` (or the real `{base}/package_mgmt.db`). For the handoff/diff
scenarios a second scratch dir acts as the operator's cache after transfer.

## Phase 1 — Import a local `.deb` (FR-001…FR-004)

**Scenario 1a — happy path.**

```text
$ package_mgmt import-deb libxkbcommon-x11-0_1.7.0-2_amd64.deb
Imported libxkbcommon-x11-0 1.7.0-2 (amd64) into local component (verified)
```

**Expected**: exit 0; the DB has a `binaries` row `component='local'`, `verified=1`;
the payload exists at `{base}/binary/pool/local/libx/libxkbcommon-x11-0/…_1.7.0-2_amd64.deb`;
`package_mgmt list` and `package_mgmt show libxkbcommon-x11-0` display it; `package_mgmt
verify` reports COMPLETE.

**Scenario 1b — unsatisfiable dependency (atomic failure).**

```text
# a .deb whose Depends names a package not in the cache
$ package_mgmt import-deb some-dep-missing.deb
Error: unsatisfiable dependency: somepkg (>= 2.0) — not in cache
```

**Expected**: exit 1; zero DB rows and zero pool files for the import (FR-003/008).

**Scenario 1c — bad input.**

```text
$ package_mgmt import-deb /nonexistent.deb
```

**Expected**: clear error naming the file, exit 1, zero changes. Same for a non-`.deb`
file and for `import-deb foo.deb --component main` (argparse error, nothing imported,
FR-001).

**Scenario 1d — idempotent re-import.**

```text
$ package_mgmt import-deb libxkbcommon-x11-0_1.7.0-2_amd64.deb   # again
```

**Expected**: exit 0, no duplicate `binaries` rows (FR-009).

## Phase 2 — `--source` provenance (FR-005)

**Scenario 2a.** With the complete source set in a directory (the `.dsc` plus the
`.orig.tar.*`/`.debian.tar.*` it references — exactly what `dpkg-source -x` needs):

```text
# ~/src holds libxkbcommon_1.7.0-2.dsc + libxkbcommon_1.7.0.orig.tar.xz + libxkbcommon_1.7.0-2.debian.tar.xz
$ package_mgmt import-deb libxkbcommon-x11-0_1.7.0-2_amd64.deb --source ~/src
```

**Expected**: exit 0; a `sources` row `component='local'`, `verified=1`; a
`binary_to_source` link; the linkage is visible via `list --include-build-deps` /
`show`; every referenced source file is copied to `source/pool/local/…` and verified
against the `.dsc`'s checksums. Without `--source`, no link exists and `verify` still
passes (US3 AS2).

**Scenario 2b — incomplete source set (a bare `.dsc` delivers nothing).**

```text
$ package_mgmt import-deb libxkbcommon-x11-0_1.7.0-2_amd64.deb --source ~/only-dsc
# ~/only-dsc contains libxkbcommon_1.7.0-2.dsc but NOT the .orig/.debian tarballs
```

**Expected**: clear error naming the missing referenced file(s), exit 1, zero changes.
Same for a `--source` that is a file rather than a directory, an unreadable directory,
or a set whose checksums do not match the `.dsc`.

## Phase 3 — `local` wins in implicit resolution (FR-006)

**Scenario 3a.** With the same `(name, version, arch)` present in `main` and `local`,
resolve the name with no component request:

```text
$ package_mgmt list <name> --full        # or a resolver-driven add that depends on it
```

**Expected**: the `local` version is selected (equal-version tie-break, `local` on top).
With only the `main` copy present, resolution is unchanged; a strictly newer upstream
version still wins.

## Phase 4 — Distribution through the same channel (FR-007)

**Scenario 4a — handoff.**

```text
$ package_mgmt handoff
$ grep '^deb ' {base}/binary/handoff/bootstrap.sh
```

**Expected**: the default release's `deb` line lists `local` with the archive
components (e.g. `deb [signed-by=…] file:… trixie main local`); the handoff contains
`dists/<release>/local/binary-amd64/Packages.gz` (with a `Packages` hash entry) and the
`pool/local/…` payloads; the `Release` `Components:` line lists `local`.

**Scenario 4b — sync/diff.**

```text
$ package_mgmt systems add mini-pc
$ package_mgmt sync --system mini-pc
```

**Expected**: the diff carries the local payload + index; `apply.sh` on the operator
merges it (no bogus `local` component when there are no local records).

**Scenario 4c — operator install/upgrade (offline).**

```text
# on the operator, after apply/bootstrap
$ sudo apt update
$ sudo apt install libxkbcommon-x11-0     # resolves to the patched local package
$ sudo apt upgrade                         # a newer local revision upgrades cleanly
```

**Expected**: installs/upgrades from the cache with zero network access; no `dpkg -i`.

## Regression

After all scenarios: `pytest` and `python -m unittest discover -s tests` are green
(baseline 730), and `tests/contract/test_cli.py` passes with the new `import-deb`
assertions.

## Validation Log

Run end-to-end against the installed CLI with a scratch cache + fixture archive
mirror (`.venv/bin/package_mgmt`, cwd = scratch `project.defaults.toml`). All
scenarios passed:

- **Phase 1** — `import-deb libxkbcommon-x11-0_1.7.0-2_amd64.deb` → `Imported
  libxkbcommon-x11-0 1.7.0-2 (amd64) into local component (verified)`; DB row
  `('local', 1, 'current')`; payload at
  `binary/pool/local/libx/libxkbcommon-x11-0/…_1.7.0-2_amd64.deb`; `verify`
  reports COMPLETE. Unsatisfiable dep (`ghost (>= 2.0)`) → exit 1, zero rows.
  `/nonexistent.deb` → exit 1 with `Cannot read package file: …`. Re-import →
  still exactly 1 binary row (idempotent).
- **Phase 2** — `--source` complete set → `Source linked (libxkbcommon 1.7.0-2,
  local, 2 files verified against dsc)`; `sources` row `('local', 1)`,
  `binary_to_source` link present, both referenced files copied to
  `source/pool/local/libx/libxkbcommon/…` and verified. Incomplete set (missing
  `…debian.tar.xz`) → exit 1, `Source file missing: … (required by
  libxkbcommon_1.7.0-2.dsc)`.
- **Phase 3** — same `(name, version, arch)` in `main` + `local`: `add app`
  (depends on lib1) leaves the current lib1 row `component='local'` (equal-version
  tie-break; `show` displays both rows).
- **Phase 4** — `sync --system mini-pc` → `New packages: 3 (local: 2, main: 1)`,
  manifest carries the two `pool/local/…` payload lines. `handoff --system
  mini-pc` → `sources="trixie main contrib local"`, `Components: main contrib
  local`, local index `dists/trixie/local/binary-amd64/Packages.gz` lists lib1 and
  libxkbcommon-x11-0. Operator (scratch apt, `file:` handoff, `--simulate`):
  `apt update` rc=0, `apt install lib1` selects the local copy, a newer local
  revision (`2.1-0`) is offered by `apt upgrade --simulate`.
- No story/task codes appear in any code file or comment.
