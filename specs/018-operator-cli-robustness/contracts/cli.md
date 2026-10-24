# CLI Contracts: Operator Track & CLI/Config Robustness

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md) · **Data model**: [data-model.md](../data-model.md)

**Contract policy (spec Assumption).** The exit-code/JSON contract table in
`specs/014-base-profiles/contracts/cli.md` is **extended, not diverged from**, for F-20/F-21/F-22; the
operator-script contracts in `specs/012-bootstrap-operator-system/contracts/cli.md` (handoff) and
`specs/013-sync-new-packages/contracts/cli.md` (apply) are **updated** for F-18/F-19 (docs inheritance).
`tests/contract/test_cli.py` stays green throughout. Exit codes are unchanged: 0 success, 1 general error,
101 stale/missing database, 102 config error.

---

## Phase 1 — `handoff` excludes out-of-config records loudly (F-18, FR-001/FR-002)

**Contract.** A verified record whose `(release, component)` is no longer in the currently configured sources is
excluded from the generated handoff, **reported loudly** (a summary Warning line, mirroring the existing
missing-pool-file Warning), and its delivery id is **not** advanced. Restoring the component to the config
re-includes the record on the next run (exclusion is derived from the live config, never persisted).

```text
$ package_mgmt handoff --system prod-box
Handoff ready: {binary_cache}/handoff
Release:        trixie
Components:     main
Packages:       330 (trixie/main: 330)
Signed:         yes (key offline-cache.gpg)
Bootstrap:      {binary_cache}/handoff/bootstrap.sh
Snapshot:       330 binaries marked for 'prod-box'
Warning:        12 verified records excluded (release/component no longer configured)
```

**Assertions**:
- A record under a removed component (e.g. `trixie/contrib` dropped from the config) is omitted from every
  `Packages.gz`, appears in the Warning line, and its `sync_mask` bit for the system is unchanged.
- A mix of in- and out-of-config verified records → only the in-config records appear and are marked delivered.
- Config restored → the record is included again (non-permanent).

---

## Phase 2 — `apply.sh` prints a clean count (F-19, FR-003/FR-004)

**Contract.** The operator `apply.sh` keeps apt/progress output out of the count capture: `apply_configure`'s
stdout is exactly the copied-file count, and apt output is routed to a transient log
(`{cache}/diff/.apply-apt.log`) tailed to stderr **only on failure**. The summary count line is exactly
`New files: <number>`; the already-applied branch is reachable and reported on its own line; a zero-file diff
reports `0`; exit code stays `0` for merged and already-applied.

```text
$ sudo {cache}/diff/apply.sh            # fresh apply
Apply: merging diff into /media/usb/cache (release trixie)
New files:       7
Offered after:   349 packages (7 new)
Handoff indexes: replaced for trixie
apt update: OK

$ sudo {cache}/diff/apply.sh            # already applied
Apply: merging diff into /media/usb/cache (release trixie)
New files:       0
Already applied: all new files already present in the pool
Offered after:   349 packages (0 new)
Handoff indexes: replaced for trixie
apt update: OK
```

**Assertions**:
- Summary is exactly `New files: <number>` with no apt noise; stderr is empty on a successful apply.
- No shell integer-comparison error on any run; already-applied branch reached without error.
- Zero-file diff → `New files:       0` and exit 0.
- Apt failure → exit 1, stderr names the log and tails its excerpt; merged files/indexes left in place for
  inspection (`apt update failed — see {cache}/diff/.apply-apt.log (excerpt below); the merged files and indexes
  were left in place for inspection.`).

---

## Phase 3 — every config value is type-validated (F-20, FR-005/FR-006/FR-007)

**Contract.** Every known configuration key is validated against its expected type at load, in the shared loader
(before any command runs). A mistyped value fails with `ConfigError` naming the key and the expected type,
routed through the established exit-102 path with the JSON payload — never a raw traceback. Booleans accept only
real TOML booleans. Unknown/mistyped key names are **rejected** as structured config errors (exit 102, JSON,
naming the key) — unknown keys are no longer silently ignored (per the Clarifications session; the only
intentional behavioral supersession in this feature). Unknown keys in the local overlay remain ignored (the
override merge adopts only recognized keys, unchanged).

```text
$ package_mgmt add vifm        # [paths] base_dir = 123
Config error: [paths] base_dir must be a string, got: 123

$ package_mgmt add vifm        # archive = 5  (non-table)
Config error: [archive] must be a table, got: 5

$ package_mgmt add vifm        # [fetch] source = "yes"
Config error: [fetch] source must be a boolean, got: 'yes'

$ package_mgmt add vifm        # [paths] binry_cache = "/tmp/x/b"  (unknown key)
Config error: Unknown configuration key: [paths] binry_cache
```

stderr JSON (with `--format json`): `{"error": "[fetch] source must be a boolean, got: 'yes'", "exit_code": 102}`

**Assertions**:
- Parametrized mistyped cases (wrong-type scalar, non-table table, wrong-type path, string-as-boolean) → exit
  102, error names the key, `config --format json` emits the JSON payload.
- `"yes"`/`"true"` strings for `fetch.source` rejected; TOML `true`/`false` accepted.
- Unknown/mistyped key names (e.g. `binry_cache`, `max_source_dep_th`) → exit 102, JSON naming the key.
- Correctly-typed values load unchanged (regression).

---

## Phase 4 — local overrides apply for known keys in absent sections (F-21, FR-008/FR-009)

**Contract.** A local override for a **known** key applies even when `project.defaults.toml` lacks the section,
and is flagged as an override. The merged result is validated after overrides are applied, so a bad depth in a
local override fails at load naming the key. `[[archive.source]]` replacement semantics are preserved (a local
`[[archive.source]]` still replaces the defaults' sources).

```text
# project.defaults.toml has no [resolution]; project.local.toml has:
#   [resolution]
#   max_source_dep_depth = 2
$ package_mgmt config --format json
{ ... "resolution": { "max_source_dep_depth": 2, "max_source_dep_depth_override": true, ... } }

# project.local.toml instead: max_source_dep_depth = -2
$ package_mgmt add vifm
Config error: [resolution] max_source_dep_depth must be an integer >= -1, got: -2   # exit 102, JSON payload
```

**Assertions**:
- Defaults without `[resolution]` + local valid depth → override applied and flagged.
- Defaults without `[resolution]` + local bad depth → exit 102, error names the key.
- `[[archive.source]]` replacement behaves exactly as before (regression).
- Unknown local keys still ignored (regression).

---

## Phase 5 — `show`/`list`/`verify` gate on cache initialization (F-22, FR-010/FR-011)

**Contract.** `show`, `list`, and `verify` route through the same existence+schema gate as their siblings
(`systems`, `snapshot`, `handoff`, `sync`): on an uninitialized cache they print the clean error and exit `101`
with **no database file created**; on a schema-mismatched database they take the identical rebuild path.

```text
$ package_mgmt show vifm        # no database yet
{"error": "Database not found at {db_path}; run 'init' first", "exit_code": 101}

$ package_mgmt list --status all        # no database yet
{"error": "Database not found at {db_path}; run 'init' first", "exit_code": 101}

$ package_mgmt verify               # no database yet
{"error": "Database not found at {db_path}; run 'init' first", "exit_code": 101}
```

**Assertions**:
- No database → exit 101, clean JSON/text error, no `package_mgmt.db` file created (directory listing
  unchanged).
- Schema-mismatched database → same rebuild message/exit 101 as sibling commands.
- Valid initialized cache → output/exit unchanged (regression).

---

## Exit codes (unchanged, consolidated)

| Code | Meaning |
|------|---------|
| 0 | Success (including handoff with excluded records, apply merged/already-applied) |
| 1 (`EXIT_ERROR`) | General failure (incl. handoff unconfirmable records, apply failure) |
| 101 (`EXIT_SCHEMA_MISMATCH`) | Stale/missing database — now also for `show`/`list`/`verify` |
| 102 (`EXIT_CONFIG_ERROR`) | Any configuration failure — now for every mistyped value and invalid local override |
