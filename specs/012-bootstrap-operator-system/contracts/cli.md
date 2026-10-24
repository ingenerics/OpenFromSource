# CLI Contracts: Bootstrap Operator System

## `package_mgmt handoff` (admin-only)

Prepares the cache for operator handoff: validates the system moniker,
generates the apt-consumable source (one filtered `Release` + per-component
indexes **per release** — a multi-release cache such as trixie +
trixie-backports is fully served), the signing key, and the operator's
`bootstrap.sh` under `{binary_cache}/handoff/`, then takes the spec 010
delivery snapshot marking **exactly the binaries the handoff actually
delivers** (binaries excluded because their pool file is missing, their index
entry is unconfirmable, or their `(release, component)` is no longer served by
the configured sources are never marked — the delivery record equals the
payload, FR-003). Idempotent — re-running regenerates the handoff, refreshes
the signature validity window, and re-marks (bitwise OR — no double-counting).

```text
package_mgmt handoff --system <moniker>
```

The moniker must name an existing, non-retired system (spec 010 `systems`
registry; the moniker is the only handle); it is validated **before** anything
is generated — an unknown/retired moniker leaves the cache untouched. Uses the
configured cache (`base_dir`/paths from `project.defaults.toml`).

### Output (stdout)

Single-release cache:

```text
Handoff ready: {binary_cache}/handoff
Release:        trixie
Components:     main, contrib
Packages:       342 (main: 330, contrib: 12)
Signed:         yes (key offline-cache.gpg)
Bootstrap:      {binary_cache}/handoff/bootstrap.sh
Snapshot:       342 binaries marked for 'prod-box'
```

Multi-release cache (one `Release` + index set per release; the script
configures one source line per release):

```text
Handoff ready: {binary_cache}/handoff
Release:        trixie, trixie-backports
Components:     trixie: main, contrib; trixie-backports: main
Packages:       347 (trixie/main: 330, trixie/contrib: 12, trixie-backports/main: 5)
Signed:         yes (key offline-cache.gpg)
Bootstrap:      {binary_cache}/handoff/bootstrap.sh
Snapshot:       347 binaries marked for 'prod-box'
```

With zero verified records, a warning line is printed in the summary:

```text
Warning:        0 packages — nothing verified yet; run download first?
```

When a verified record's `(release, component)` is no longer in the currently
configured sources (e.g. a component was dropped from `project.defaults.toml`
after the record was verified), a warning line reports the exclusion loudly —
the record is omitted from every generated index, its delivery id is **not**
advanced, and the warning is non-permanent (restoring the component to the
config re-includes the record on the next run):

```text
Warning:        12 verified records excluded (release/component no longer configured)
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Handoff generated (including when nothing is verified yet — empty index, still successful) |
| 1 (`EXIT_ERROR`) | Failure (below) |
| 101 (`EXIT_SCHEMA_MISMATCH`) | Stale/missing database (existing `validate_schema` convention) |
| 102 (`EXIT_CONFIG_ERROR`) | Invalid configuration (existing behavior) |

### Error cases (JSON to stderr via the shared `_print_error` convention)

- Missing `--system` →
  `{"error": "handoff requires --system <moniker>", "exit_code": 1}`
- Unknown or retired moniker → spec 010 convention:
  `{"error": "Unknown system '<name>'", "exit_code": 1}` (nothing generated, no snapshot taken)
- Database schema mismatch → standard spec 010 message, `exit_code: 101`
- No verified records at all → still succeeds (an empty apt source is valid;
  the operator's script reports zero packages) — *not* an error
- A verified record whose index entry or pool file is missing → the record is
  skipped with a warning to the log and counted in the summary as excluded
  (the offered set must equal what is actually deliverable, FR-003), and it is
  **not** marked in the delivery snapshot:
  `{"error": "handoff: 2 verified records excluded (no index entry or pool file); run download or verify", "exit_code": 1}` —
  excluded records that are NOT fixable by `verify` are a command error
- A verified record whose `(release, component)` is no longer configured → the
  record is omitted from every index and reported loudly as a summary Warning
  line (`release/component no longer configured`), its delivery id is **not**
  advanced, and this is **not** an error (exit `0`) — the in-config portion of
  the handoff is still generated; restoring the component re-includes the
  record (non-permanent, derived from the live config each run)
- Unreadable cache directories / pool → clear error naming the path,
  `exit_code: 1`

## `bootstrap.sh` (operator-only, shipped in the handoff)

Takes the cache directory as an **optional positional argument**, defaulting
to the cache the script ships in (the handoff's parent directory); a given
path is used as-is (relative paths resolve against the current working
directory) and is authoritative — the script never parses project settings.
`--remove` unconfigures. Requires root (or sudo).

```text
sudo {cache}/handoff/bootstrap.sh
sudo {cache}/handoff/bootstrap.sh {cache}
sudo {cache}/handoff/bootstrap.sh {cache} --remove
```

### Behavior

1. **Preflight** (as root): at most one argument (the cache dir defaults to
   the handoff's parent directory); handoff structure
   present at the resolved path for **every release the handoff serves**
   (`Release.gpg` present, per-component `Packages.gz`, `pool` target
   exists); release match — the system's `VERSION_CODENAME` in
   `/etc/os-release` must be **one of the handoff releases**; otherwise refuse.
2. **Configure**: ensure the `pool` symlink exists; write
   `/etc/apt/sources.list.d/openfromsource-cache.list` atomically
   (overwriting only its own prior content) with **one `deb` line per
   release** (`deb [signed-by={handoff}/offline-cache.gpg] file:{handoff} {release} {components}`);
   run `apt update`.
3. **Remove** (`--remove`): delete the entry; nothing else is touched.

### Output (stdout)

Single-release handoff:

```text
Bootstrap: configuring apt from /media/usb/cache/handoff (release trixie)
Source added: /etc/apt/sources.list.d/openfromsource-cache.list
apt update: OK (N packages available from the offline cache)
```

Multi-release handoff (one `deb` line per release is written to the entry):

```text
Bootstrap: configuring apt from /media/usb/cache/handoff (releases: trixie, trixie-backports)
Source added: /etc/apt/sources.list.d/openfromsource-cache.list
apt update: OK (N packages available from the offline cache)
```

`--remove`:

```text
Bootstrap: removing offline cache source
Removed: /etc/apt/sources.list.d/openfromsource-cache.list
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Configured / removed / already configured (idempotent success) |
| 1 | Any failure (below), with a clear message on stderr; no configuration changes are made on failure |

### Error messages (stderr, plain text)

- `Not running as root — run with sudo.`
- `Handoff not found at {resolved}/handoff — is the cache complete?` (structure/Release.gpg missing)
- `Release mismatch: cache is for {releases}, this system is {codename}. Refusing to configure.`
- `apt update failed — see output above; the source entry was left in place for inspection.` (entry already written; system apt otherwise untouched)
- `No such option: {arg}. Usage: bootstrap.sh [<cache-dir>] [--remove]`

### Testability

The script carries no test-only hooks. Its operator flow is implemented as
plain bash functions taking explicit paths (`handoff_complete`, `release_match`,
`write_sources_entry`, `run_apt_update`, `bootstrap_configure`, ...), so unit
tests source the shipped script and call the functions with scratch paths —
no root, no host modification. `bootstrap_configure` accepts an optional
scratch apt state root (tests only) so the FR-006 `apt update` failure path is
exercised against isolated apt dirs: an unreadable handoff index makes
`apt update` fail, the entry remains in place, the error is reported, and the
function returns non-zero (the CLI collapses any failure to exit 1).
