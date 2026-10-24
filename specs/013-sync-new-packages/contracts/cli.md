# CLI Contracts: Sync New Packages

## `package_mgmt sync` (admin-only)

Prepares a minimal diff cache for a system that already received a full handoff: validates the system moniker, computes the spec 010 pending set (current + verified + not yet delivered to that system), generates the diff at `{binary_cache}/diff/` (only the pending binaries' files + the **complete** per-release indexes so apt presents prior + new, `Release` + `Release.gpg` signed with the same cache-local key, `offline-cache.gpg`, `manifest.txt`, and the operator's `apply.sh`), then takes the spec 010 delivery snapshot marking **exactly the binaries the diff ships** (excluded records — missing pool file / unconfirmable index entry — are never marked). Idempotent: a second run after a successful one sees zero pending and produces no new diff.

```text
package_mgmt sync --system <moniker>
```

The moniker must name an existing, non-retired system (spec 010 `systems` registry; the moniker is the only handle); it is validated **before** anything is generated. The baseline `{binary_cache}/handoff/` (and its `.gnupg` keyring) is read, never rewritten. The spec 012 `handoff` command (full) is unchanged — `sync` produces only the minimal diff, `handoff` the full source.

### Output (stdout)

Diff with pending packages:

```text
Sync ready:      {binary_cache}/diff
Release:         trixie
Components:      main, contrib
New packages:    7 (main: 6, contrib: 1)
Total offered:   349 (prior 342 + new 7)
Signed:          yes (key offline-cache.gpg, same as handoff)
Apply:           {binary_cache}/diff/apply.sh
Snapshot:        7 binaries marked for 'prod-box'
```

Nothing pending (idempotent second run):

```text
Sync ready:      {binary_cache}/diff
New packages:    0 — nothing new since the last sync for 'prod-box'
Snapshot:        no change
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Diff generated, or nothing pending (reported as 0, still successful) |
| 1 (`EXIT_ERROR`) | Failure (below) |
| 101 (`EXIT_SCHEMA_MISMATCH`) | Stale/missing database (existing `validate_schema` convention) |
| 102 (`EXIT_CONFIG_ERROR`) | Invalid configuration (existing behavior) |

### Error cases (JSON to stderr via the shared `_print_error` convention)

- Missing `--system` → `{"error": "sync requires --system <moniker>", "exit_code": 1}`
- Unknown or retired moniker → spec 010 convention: `{"error": "Unknown system '<name>'", "exit_code": 1}` (nothing generated, no snapshot)
- A pending binary whose index entry or pool file is missing → skipped with a warning to the log, counted as excluded, and **not** marked: `{"error": "sync: 2 pending records excluded (no index entry or pool file); run download or verify", "exit_code": 1}`
- A *delivered* binary that can no longer be confirmed (missing index/pool on the admin side) → reported as a defect state, command error (exit 1), nothing marked — the delivery record must keep matching the operator's payload.
- Database schema mismatch → standard spec 010 message, `exit_code: 101`
- Unreadable cache directories / pool → clear error naming the path, `exit_code: 1`

## `apply.sh` (operator-only, shipped in the diff)

Merges the minimal diff into the operator's existing bootstrapped cache. Takes the cache directory as an **optional positional argument**, defaulting to the cache the script ships in (the diff's parent directory); a given path is used as-is (relative paths resolve against the current working directory) and is authoritative — the script never parses project settings. Requires root (or sudo).

```text
sudo {cache}/diff/apply.sh
sudo {cache}/diff/apply.sh {cache}
```

### Behavior

1. **Preflight** (as root): at most one argument (the cache dir defaults to the diff's parent); diff structure present at the resolved path for **every release the diff serves** (`manifest.txt`, `base set`, per-release `Release.gpg` + per-component `Packages.gz`, `pool/` target exists); release match — the system's `VERSION_CODENAME` in `/etc/os-release` must be **one of the diff releases**; otherwise refuse.
2. **Base-set check**: every `delivered` path in `manifest.txt` must already exist under the operator's pool; a missing one means an intermediate diff was missed → refuse with clear guidance (FR-011). Nothing is changed yet.
3. **Merge new files**: for each `new` line, verify the diff's file against the manifest SHA256 (and size), then copy it into the pool atomically (temp name in the pool + verify + rename). A file already present with a matching checksum is skipped (idempotent); present with a different checksum → refuse.
4. **Replace handoff `dists/`**: copy the diff's `Packages.gz`, `Release`, and `Release.gpg` over the handoff's per-release (per-file atomic), so apt presents prior + new and `Valid-Until` is refreshed. `offline-cache.gpg` is copied over if the handoff lacks one.
5. **Apply**: run `apt update`; report success or the failure (the handoff is left consistent with the copied files for inspection, FR-013).

### Output (stdout)

`apply_configure`'s stdout carries **only** the copied-file count; apt/progress
output is routed to a transient log (`{cache}/diff/.apply-apt.log`) tailed to
stderr only on failure. The summary count line is exactly `New files: <number>`
(no apt noise, no extra suffix):

```text
Apply: merging diff into /media/usb/cache (release trixie)
New files:       7
Offered after:   349 packages (7 new)
Handoff indexes: replaced for trixie
apt update: OK
```

Nothing to do (already applied) — the already-applied state is reported on a
separate line, a zero-file diff reports `0`, and exit stays 0:

```text
Apply: merging diff into /media/usb/cache (release trixie)
New files:       0
Already applied: all new files already present in the pool
Offered after:   349 packages (0 new)
Handoff indexes: replaced for trixie
apt update: OK
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Merged / already applied (idempotent success) |
| 1 | Any failure (below), with a clear message on stderr; no changes are made on a preflight/base-set failure |

### Error messages (stderr, plain text)

- `Not running as root — run with sudo.`
- `Diff not found at {resolved}/diff — is the diff complete?` (structure missing)
- `Release mismatch: diff is for {releases}, this system is {codename}. Refusing to apply.`
- `Missing intermediate sync: {rel-path} is not in the cache pool — an earlier diff must be applied first. Refusing to apply.` (base-set failure; nothing changed)
- `Checksum mismatch for {rel-path}: expected {sha256}, got {actual}. Refusing to apply.`
- `Existing file conflict: {rel-path} is already in the pool with a different checksum. Refusing to apply.`
- `apt update failed — see {cache}/diff/.apply-apt.log (excerpt below); the merged files and indexes were left in place for inspection.` (followed by a bounded `tail` of the log)
- `No such option: {arg}. Usage: apply.sh [<cache-dir>]`

### Testability

The script carries no test-only hooks. Its operator flow is implemented as plain bash functions taking explicit paths (`diff_complete`, `release_match`, `check_base_set`, `copy_new_files`, `replace_handoff_dists`, `run_apt_update`, `apply_configure`, `apply_summary`, ...), so unit tests source the shipped script and call the functions with scratch paths — no root, no host modification. `run_apt_update` accepts an optional scratch apt state root (tests only) so the FR-013 `apt update` failure path is exercised against isolated apt dirs, mirroring the spec 012 `bootstrap.sh` convention.
