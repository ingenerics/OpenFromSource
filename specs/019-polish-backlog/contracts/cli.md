# CLI Contracts: Polish Backlog (Merged Low Findings)

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md) · **Data model**: [data-model.md](../data-model.md)

**Contract policy (spec Assumption).** The exit-code/JSON contract table in
`specs/014-base-profiles/contracts/cli.md` is **extended, not diverged from**; `tests/contract/test_cli.py` stays
green throughout. `verify --graph` is **removed** (spec Clarifications) — doc/contract references to it (014
contract table, 018 quickstart, CLI `--help`) are updated in this feature (Constitution VIII). Exit codes are
unchanged: 0 success, 1 general error, 100 permission, 101 stale/missing database, 102 config error.

---

## Phase 1 — `add` → 101 on stale schema; `init` exit-code split + offline summary; `ensure-base` no DB on failure (FR-001/FR-002/FR-003)

**Contract.**
- `add` on a stale-schema database exits **101** (matching its siblings), not 1.
- `init` maps real permission problems to **100**; other initialization failures (e.g. ENOSPC) to **1**; a
  mirror-unreachable `init` exits **0** but its summary **states the offline mode**.
- A failed `ensure-base` on a fresh cache exits non-zero and **creates no database file** (spec 008 US1 AS2).

```text
$ package_mgmt add vifm        # database schema is stale
{"error": "Schema mismatch: database schema version …", "exit_code": 101}

$ package_mgmt init            # cache dir not writable (permission)
{"error": "Permission denied: /var/cache/package_mgmt", "exit_code": 100}

$ package_mgmt init            # disk full (ENOSPC)
{"error": "Failed to create /var/cache/package_mgmt/binary/pool: [Errno 28] No space left on device", "exit_code": 1}

$ package_mgmt init            # no mirror reachable — exits 0 with an honest summary
Status:            initialized (no mirror reachable — offline mode, 0 binaries cached)

$ package_mgmt ensure-base     # fresh cache, no image in iso_dir
{"error": "No installer image found in …", "exit_code": 1}        # and no package_mgmt.db file is created
```

**Assertions**:
- Stale-schema `add` → exit 101 with the stale-schema message; valid DB → unchanged.
- `init` permission failure → 100; non-permission `InitError` (ENOSPC-style) → 1.
- `init` with 0 binaries (unreachable mirrors) → exit 0 and the summary line carries the offline note.
- `ensure-base` failing discovery (no/multiple/corrupt image, populated cache) on a fresh cache → non-zero and no
  database file exists afterward.

---

## Phase 2 — `verify` loses `--graph`; `--package` obeys report depth; `systems add` validates; empty pin and mutually exclusive `list` flags (FR-004/FR-005/FR-006)

**Contract.**
- The `--graph` flag is **removed** from the CLI; bare `verify` is the single dependency-graph verification
  command (the depth gate is default `verify` behavior). `verify --graph` is a usage error (unknown argument).
- `verify --package X` applies `max_build_dep_report_depth` to its output exactly like the full-cache path.
- `systems add` rejects an empty moniker and any moniker containing whitespace/newlines with a clean error and no
  record; a duplicate moniker still errors but never leaks the database connection.
- `add` rejects an empty pin (`name=`) in the positional path exactly like the `--file` path.
- `list --deps X --status all` together is a **usage error** (the flags are mutually exclusive, exit 1); each flag
  alone works as before (per the Clarifications session — supersedes the previous silent status-wins behavior).

```text
$ package_mgmt verify --graph
usage: package_mgmt verify [-h] [--package PACKAGE]      # --graph is gone (usage error if passed)

$ package_mgmt verify --package vifm    # finite report depth
  … (build-dep lists respect max_build_dep_report_depth)

$ package_mgmt systems add "" 
{"error": "System name must be a non-empty word (no spaces, newlines)", "exit_code": 1}

$ package_mgmt systems add "a b" / $'a\nb'
{"error": "System name must be a non-empty word (no spaces, newlines)", "exit_code": 1}

$ package_mgmt add "vim="
{"error": "Malformed package argument 'vim=': empty version pin", "exit_code": 1}

$ package_mgmt add --file list.txt     # list.txt: "vim="
{"error": "Malformed line 1 in list.txt: 'vim='", "exit_code": 1}

$ package_mgmt list --deps vifm --status all
{"error": "Use either --deps or --status, not both", "exit_code": 1}
```

**Assertions**:
- `verify --graph` is a usage error; bare `verify` runs the graph verification (regression: output/exit unchanged).
- `verify --package X` under a finite depth filters build-dep output (both "Unresolved build deps" and "Missing
  build tools").
- `systems add ""` / `systems add $'a b'` / `systems add $'a\nb'` → clean error, exit 1, no row; a duplicate
  moniker error path leaves no leaked connection (covered by a fixture-level connection-count assertion).
- `add "vim="` positional == `--file` line `vim=` → both rejected.
- `list --deps X --status all` → clean usage error (exit 1); `list --deps X` alone → the tree; `list --status all`
  alone → the status table (unchanged).

---

## Phase 3 — zero-pending `sync` leaves an empty diff; build-dep report depth (FR-007/FR-008)

**Contract.**
- A `sync` with zero new packages still **regenerates the diff as empty** — the `diff/` directory never retains
  stale content (spec 013 AS2/FR-006). Delivery/snapshot state is unchanged (no marking).
- `verify`'s "Unresolved build deps" and "Missing build tools" lists honor `max_build_dep_report_depth`
  (`0` = none, `-1` = all, `N` = up to `N`) in both the full-cache and `--package` paths (014 FR-017).

```text
$ package_mgmt sync --system prod-box     # nothing pending, after a previous non-empty diff
Sync ready:      {binary_cache}/diff
New packages:    0 — nothing new since the last sync for 'prod-box'
Snapshot:        no change
# {binary_cache}/diff contains an empty Packages.gz/Release + apply.sh (New files: 0) — not stale content
```

**Assertions**:
- After a successful non-empty `sync`, re-running with nothing pending leaves an *empty* diff (previous files
  removed/regenerated as empty), exit 0, snapshot unchanged.
- `verify` at depth `0` reports no build-tool levels; at `-1`/`N` reports all/up-to-`N`; per-package path matches
  the full-cache path at every depth.

---

## Phase 4 — deterministic, spec-compliant resolution (FR-009/FR-010/FR-011/FR-012)

**Contract.** Behavior-only (no CLI surface change). The resolver's selection and linking become deterministic and
constraint-aware:

- On an exact-version tie across components, `main` (highest priority) wins and the pool path reflects it (006
  FR-005); `_find_current_binary` selection is deterministic on multi-row matches.
- A build-dep fallback links only a constraint-satisfying entry and leaves no residual "unresolved" error once a
  valid link is made.
- Runtime-alternative "already satisfied" probes consider only `current` rows.
- A cached virtual resolution picks up a same-release provider upgrade after an index refresh.

**Assertions** (fixture-level; no CLI output change):
- Same version current in `main`/`contrib`/`non-free` → `main` entry + `main` pool path.
- Two current rows for the same package+arch → the same (deterministic) id on repeated calls.
- Build-dep fallback with a mismatched-version candidate → not linked; with a valid candidate → linked with no
  residual error.
- An alternative group satisfied only by superseded rows → does **not** suppress resolution; a `current` provider
  resolves it.
- After an index refresh upgrades a provider in the primary source → a cached virtual re-resolution picks up the
  upgrade (re-recorded `virtual_resolutions`).

---

## Phase 5 — accurate database lifecycle (FR-013/FR-014/FR-015/FR-016)

**Contract.** Behavior-only. The database state becomes truthful:

- Provides population performs **no nested commit** and counts only real inserts; `init`/`ensure-base` own their
  transactions. Stale `provides` rows (releases/indexes no longer configured) are **swept** on refresh.
- Circular reports count only `current` rows, and the `circular` flag resets on re-registration.
- `packages.status` transitions `pending → current`; `requested_version` holds only the actually-requested version.

**Assertions** (fixture-level):
- `ensure-base`'s Phase A is a single transaction (a forced failure mid-Phase-A rolls back provides too).
- `populate_provides_table` reports N inserts when N rows are new and 0 when all already exist (no phantom count).
- After a release is removed from config and the index refreshed, its `provides` rows are gone; a stale provider is
  never selected.
- A re-upserted circular binary loses its flag; `verify` and `list --deps` agree on the circular set (current-only).
- After resolution, `show`/`list` reflect `packages.status = current` and a transitive dep shows no fabricated
  requested version.

---

## Phase 6 — path and download robustness (FR-017/FR-018/FR-019)

**Contract.**
- A relative `base_dir` fails at load: exit **102**, error names `[paths] base_dir`.
- A server `Retry-After` is capped at the internal backoff bound (30 s) — a run never stalls longer on one
  response.
- A cache path containing spaces produces a **clear, actionable diagnostic** from the handoff (never a
  silently-broken `deb` entry).

```text
# project.defaults.toml:  [paths]  base_dir = "relative/path"
$ package_mgmt config
Config error: [paths] base_dir Path is not absolute: relative/path        # exit 102, JSON payload

# handoff from a cache under /media/My USB/cache
$ package_mgmt handoff --system prod-box
Handoff error: the cache path contains a space ('/media/My USB/cache'); apt sources cannot carry it.
Place the cache at a space-free path (e.g. mount point or base_dir) and re-run.    # exit 1
```

**Assertions**:
- Relative `base_dir` in defaults or local overrides → exit 102, names the key; valid absolute paths unchanged.
- `_backoff_delay` with `Retry-After: 86400` ≤ 30 s; with `Retry-After: 2` → 2 s (preserves sane server intent).
- Handoff from a space-containing path → exit 1 with the diagnostic, no `deb` line written; space-free paths →
  unchanged behavior (regression).

---

## Phase 7 — doc/test hygiene (FR-020)

**Contract.** No CLI behavior change. The downloader's `_stat_size_matches` docstring and the size-match test name
describe the actual conservative behavior (a size match does **not** skip the re-hash); the mirror-normalization
tests are declared under a dedicated `TestMirrorNormalization` class.

**Assertions**:
- The docstring states the checksum is always re-verified on a size match; the renamed test asserts re-hash but
  no re-fetch.
- `TestMirrorNormalization` groups the mirror tests; the suite's total test count is unchanged (coverage preserved).

---

## Exit codes (unchanged, consolidated)

| Code | Meaning |
|------|---------|
| 0 | Success (including `init` offline mode, zero-pending sync, valid `verify`/`list`) |
| 1 (`EXIT_ERROR`) | General failure — now also: non-permission `init` failure, `systems add` bad moniker / duplicate, `add` empty pin, handoff space-path diagnostic |
| 100 (`EXIT_PERMISSION`) | Permission problems only — now reserved for actual permission failures in `init` |
| 101 (`EXIT_SCHEMA_MISMATCH`) | Stale/missing database — now also for stale-schema `add` |
| 102 (`EXIT_CONFIG_ERROR`) | Any configuration failure — now also for a relative `base_dir` |

**Removed surface**: `verify --graph` (bare `verify` is the single graph-verification command).
