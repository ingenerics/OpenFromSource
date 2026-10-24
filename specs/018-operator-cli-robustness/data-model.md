# Data Model: Operator Track & CLI/Config Robustness

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

The spec 010/011 schema is **frozen** — this feature changes no tables, columns, or migrations. This document
records the entities this feature reasons about: the handoff exclusion set, the delivery record, the operator
apply summary, the configuration type contract, the local-override merge, and the cache initialization gate.
Most are **derived state** (computed per run), not stored.

## Schema (frozen, unchanged)

Tables: `packages`, `binaries`, `sources`, `binary_to_source`, `source_build_deps`, `provides`,
`virtual_resolutions`, `systems`. Defined in `src/package_mgmt/lib/model/database.py` (`EXPECTED_SCHEMA_SQL`);
NOT modified by this feature (F-22 is behavioral gating only).

## Entities

### Handoff exclusion set (Phase 1 — F-18) — derived, not stored

The set of `(release, component)` pairs the **currently configured** sources serve whose cached archive index
exists. Computed each run by `_component_bases(config)` (iterates `config.sources`, resolves each component's
`Packages.gz`/`.xz`):

```text
configured (release, component) pairs ── archive index exists ──► base_set
```

Invariant: a record whose `(release, component)` is **not** in `base_set` is excluded from the handoff output,
reported loudly, and its delivery id is **not** advanced. Because the set is re-derived from the live config
every run, exclusion is non-permanent: restoring the component to the config re-includes the record.

### Delivery record (Phase 1 — F-18) — stored, unchanged schema

`binaries.sync_mask` bit per system (spec 010) advanced **only** for records actually included in a handoff.
Phase 1 adds no schema change — `cli/commands/handoff.py` already marks exactly `HandoffStats.delivered_ids`;
the fix simply ensures out-of-config records never enter that list. New `HandoffStats.excluded_config:
list[str]` (in-memory report only) carries `"<name> <version>"` of excluded records for the loud summary.

State transition for a record under a removed component:

```text
current + verified ── (release, component) ∉ base_set ──► excluded_config + log warning + NOT delivered
                  ── (release, component) ∈ base_set ──► processed as today (stanza + delivered id)
```

### Operator apply summary (Phase 2 — F-19) — runtime output, not stored

The count of files copied by the operator's `apply.sh`, produced by `copy_new_files` and echoed through
`apply_configure`. The new invariant: `apply_configure`'s stdout carries **only** the count; apt/progress output
goes to a transient log file (`$cache_dir/diff/.apply-apt.log`). The summary count line is exactly
`New files: <number>`; the already-applied state (count 0) is reported on a separate line.

```text
copy_new_files ── stdout = "<n>" only ──► apply_configure ── stdout = "<n>" only ──► apply_main summary
apt update    ── stdout+stderr ──► log file (tailed to stderr only on failure)
```

### Configuration type contract (Phase 3 — F-20) — declarative, in code

`config_loader._TYPE_MAP`: `(section, key) → expected type` for every key in `_KEY_MAP`.

| `(section, key)` | Field | Expected type |
|------------------|-------|---------------|
| `(paths, base_dir)` | `base_dir` | `str` (absolute path) |
| `(paths, binary_cache)` / `(paths, source_cache)` / `(paths, iso_dir)` | path fields | `str` |
| `(database, path)` | `db_path` | `str` |
| `(logging, level)` | `log_level` | `str` |
| `(logging, directory)` | `log_dir` | `str` |
| `(fetch, source)` | `fetch_source` | `bool` (real TOML boolean only; `"yes"` rejected) |
| `(resolution, max_build_dep_report_depth)` / `(resolution, max_source_dep_depth)` | depth fields | `int` (not bool) with `>= -1` range |

Validation order in the merged loop: type-map check (names the key and expected type) → depth range rule →
path expansion/absolute check. Every violation raises `ConfigError` → exit 102 + JSON payload (never a raw
traceback). Unknown keys remain silently ignored (unchanged).

### Local-override merge (Phase 4 — F-21) — in-memory

`load_config` merges defaults + local overrides; the override gate changes from `tup in defaults_flat` to
`tup in _KEY_MAP`:

```text
local (section, key) ── known key (_KEY_MAP) ──► adopted (overrides_raw + overridden_keys) ──► merged ──► validated
                   ── unknown key ──► ignored (unchanged)
                   ── [archive] section ──► skipped by _flatten; handled by the dedicated [[archive.source]]
                                            replacement logic (semantics preserved)
```

The merged result is validated after overrides are applied, so a bad depth in a local override fails at load
naming the key even when defaults lack `[resolution]`; a valid override is applied **and** flagged in
`overridden_keys` (visible in `config --format json` as the override flag).

### Cache initialization gate (Phase 5 — F-22) — derived

The shared existence+schema gate (`db_init.validate_schema_or_error`) that `show`/`list`/`verify` adopt:

```text
no database file ──► DatabaseError "Database not found … run 'init' first" ──► exit 101, NO file created
database exists, schema objects missing ──► _rebuild_message ──► exit 101
database valid ──► proceed (unchanged behavior)
```

Invariant: a read command never creates a database file. `validate_schema` checks `os.path.exists` before
opening a connection, so the stray 0-byte file is impossible.

## Validation rules (from requirements)

- **FR-001/FR-002**: a verified record outside the configured set is excluded, reported loudly, and never marked
  delivered (Phase 1).
- **FR-003/FR-004**: the apply summary contains only the count; apt output never enters the count capture or the
  summary; no shell error; already-applied branch reachable (Phase 2).
- **FR-005/FR-006/FR-007**: every config value type-validated; booleans accept only real booleans; all config
  failures flow through the structured exit-102 + JSON path (Phase 3).
- **FR-008/FR-009**: known-key local overrides apply regardless of defaults section presence, flagged as
  overrides, and the merged result is validated (Phase 4).
- **FR-010/FR-011**: `show`/`list`/`verify` gate on cache existence+schema like their siblings; missing → clean
  error + 101, no file; stale → shared rebuild path (Phase 5).

## Relationship to existing tables

Unchanged: `packages`, `binaries` (`sync_mask`, `verified`, `status` semantics untouched), `sources`,
`binary_to_source`, `source_build_deps`, `provides`, `virtual_resolutions`, `systems`. This feature adds no rows
or columns; the only schema-adjacent change is the in-memory `HandoffStats.excluded_config` report field.
