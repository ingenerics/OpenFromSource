# Data Model: Polish Backlog (Merged Low Findings)

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

The spec 010/011 schema is **frozen** — this feature changes no tables, columns, or migrations. This document
records the entities/state this feature reasons about: exit-code mapping, the database-file lifecycle,
current-binary selection, the link/probe constraint state, the provides lifecycle, the circular state, the
status/requested state, config path validation, the download backoff, and the handoff deb entry.

## Schema (frozen, unchanged)

Tables: `packages`, `binaries`, `sources`, `binary_to_source`, `source_build_deps`, `provides`,
`virtual_resolutions`, `systems`. Defined in `src/package_mgmt/lib/model/database.py` (`EXPECTED_SCHEMA_SQL`);
**NOT modified** by this feature. All fixes operate on existing columns and in-memory values.

## Entities

### Exit-code mapping (Phase 1 — P-1, P-4) — in code

The contract table (`contracts/cli.md`) maps failures to exit codes: 0 success, 1 general, 100 permission,
101 stale/missing database, 102 config error. Phase 1 aligns two commands:

```text
add:      DatabaseError (stale schema) ──► 101        (was: generic 1)
init:     InitError.permission = True  ──► 100
          InitError.permission = False ──► 1          (e.g. ENOSPC — was: 100)
```

`InitError` gains an optional `permission: bool = False` attribute, set by `initializer._ensure_dir` only in the
`except PermissionError` branch; all other `OSError` paths set it `False`.

### Database-file lifecycle (Phase 1 — P-4, P-5) — filesystem state

Whether a command creates the database at all. New invariants:

```text
ensure-base: find image ─► load/validate indexes ─► (exists? assert fresh) ─► initialize_database ─► BEGIN ─► insert
             (any discovery failure ⇒ NO database file created — spec 008 US1 AS2)
init:        initialize_caches ─► initialize_database ─► fetch indexes ─► populate provides
```

The `populate_provides_table` fix (P-13) moves commit ownership to the caller: `init` commits explicitly; the
`ensure-base` single transaction (its own `BEGIN`…`commit`) is now genuinely atomic for Phase A.

### Current-binary selection (Phase 4 — P-9, P-16) — deterministic, spec-compliant

The rule for choosing among multiple candidates:

```text
_best_entry / lookup_source*:  sort by (version, -component_order)   ⇒  main(0) wins exact-version ties
_find_current_binary:          SELECT … WHERE status='current' ORDER BY id   ⇒  deterministic on multi-row matches
```

`_COMPONENT_PRIORITY` is unchanged (`main=0, contrib=1, non-free=2, non-free-firmware=3`); only the sort key
negates the component term so the *highest priority* (lowest order) wins on a version tie. `_find_current_binary`
adds `ORDER BY id` (insertion order) — a determinism fix, not a semantics change.

### Link/probe constraint state (Phase 4 — P-10, P-11, P-12)

The version/status constraints honored at three points:

```text
build-dep fallback link (P-10):  candidate must satisfy entry.version_constraint, else NOT linked;
                                 residual "unresolved" error retracted when a valid link is made
runtime-alternative probe (P-11): binaries probe + provides join both add status='current'
cached virtual resolution (P-12): same-release cached row is re-compared against a fresh select_provider
                                  (refreshed index) — provider upgrade in primary source re-resolves (006 FR-005)
```

### Provides lifecycle (Phase 5 — P-13, P-14) — stored

`provides` rows (package_name, version, architecture, release, component, virtual_name, provides_version).
New lifecycle rules:

```text
populate (refresh):  DELETE FROM provides WHERE (release, component) NOT IN (configured set)   [P-14 sweep]
                     INSERT OR IGNORE ... for configured (release, component)                   [unchanged]
                     count += rowcount (0 for skipped duplicates)                               [P-13 real count]
                     no internal commit — caller owns the transaction                            [P-13]
```

Invariant: every `provides` row corresponds to a currently configured `(release, component)`; a stale provider is
never selectable. `init.py` commits explicitly after the call (it previously inherited the internal commit).

### Circular state (Phase 5 — P-15) — stored flag

`binaries.circular` (0/1). New invariants:

```text
upsert_binary ON CONFLICT:  circular = 0        (re-registration clears a stale flag; fresh insert defaults 0)
circular report (verify):   WHERE circular = 1 AND status = 'current'   (superseded rows never counted)
```

`verify` and `list --deps` therefore agree on the circular set.

### Status/requested state (Phase 5 — P-17) — stored

`packages.status` + `packages.requested_version`. The defined lifecycle (minimal, schema-freeze-compatible):

```text
pending  ── resolver registers a current binary ──►  current
(no other transitions in this feature; richer lifecycle deferred)
```

`requested_version` holds only the version **actually requested** (set for a pinned top-level `name=version`
request); a transitive dependency with no explicit request gets `None` (not the resolved version), and the
existing `COALESCE(NULLIF(?, ''), requested_version)` keeps an already-recorded explicit request.

### Config path validation (Phase 6 — P-19) — validated value

`base_dir` must be absolute. `validate_absolute`:

```text
expanded = os.path.expanduser(path)
if not os.path.isabs(expanded): raise ValueError("Path is not absolute: …")   [NEW: fires — was dead code]
return os.path.abspath(expanded)
```

`config_loader.py` wraps the `ValueError` into `ConfigError` (exit 102, names `[paths] base_dir`).

### Download backoff (Phase 6 — P-20) — runtime bound

The retry delay between download attempts. New invariant:

```text
delay = min(BACKOFF_CAP, max(0.0, retry_after))    when a server Retry-After is present   (BACKOFF_CAP = 30.0)
delay = min(BACKOFF_CAP, 1.0 * 2^(attempt-1)) + jitter   otherwise (unchanged)
```

A run can never sleep longer than `BACKOFF_CAP` on a single response.

### Handoff deb/sources entry (Phase 6 — P-21) — operator artifact

The operator apt `sources.list` entry generated by `write_sources_entry`. New invariant: a `handoff_dir`
containing whitespace is **refused with a clear, actionable diagnostic** (name the path and the remedy: place the
cache at a space-free path / mount point) instead of emitting a silently-broken `deb` line. The entry remains a
single whitespace-delimited line for space-free paths (unchanged).

## Validation rules (from requirements)

- **FR-001**: stale-schema `add` → 101 (Phase 1).
- **FR-002**: `init` distinguishes permission (100) from other failures (1) and flags offline mode (Phase 1).
- **FR-003**: failed `ensure-base` creates no database file (Phase 1).
- **FR-004**: `--graph` removed; `--package X` obeys the report-depth filter (Phase 2).
- **FR-005**: moniker validation; no connection leak on failure (Phase 2).
- **FR-006**: empty-pin parity; `--deps`/`--status` mutually exclusive (Phase 2).
- **FR-007**: zero-pending sync leaves an empty diff (Phase 3).
- **FR-008**: build-dep report honors `max_build_dep_report_depth` in every path (Phase 3).
- **FR-009**: `main` wins exact-version ties; deterministic current-binary selection (Phase 4).
- **FR-010**: build-dep fallback links only constraint-satisfying entries, no residual error (Phase 4).
- **FR-011**: runtime-alternative probes consider only current rows (Phase 4).
- **FR-012**: same-release provider upgrades are picked up for cached virtual resolutions (Phase 4).
- **FR-013**: no nested commit; real insert count (Phase 5).
- **FR-014**: stale providers swept (Phase 5).
- **FR-015**: circular current-only + reset on upsert (Phase 5).
- **FR-016**: status transitions; truthful `requested_version` (Phase 5).
- **FR-017**: relative `base_dir` rejected at load (Phase 6).
- **FR-018**: `Retry-After` capped (Phase 6).
- **FR-019**: space-containing cache path → working entry or clear diagnostic (Phase 6).
- **FR-020**: doc/test labels corrected (Phase 7).

## Relationship to existing tables

All existing tables are unchanged in shape. Data-affecting fixes are updates **within existing rows**:

| Table | Column(s) touched | Fix |
|-------|-------------------|-----|
| `packages` | `status`, `requested_version` | P-17 (status lifecycle + truthful requested version) |
| `binaries` | `circular` | P-15 (reset on upsert; report filters current) |
| `binaries` | (selection) | P-16 (`ORDER BY id` — no schema change) |
| `provides` | rows | P-13 (insert counting/commit), P-14 (stale sweep `DELETE`) |
| `virtual_resolutions` | rows | P-12 (re-resolution on refresh) |
| `systems` | (none) | P-3 (validation + connection hygiene only) |
| `source_build_deps` | (read) | P-18 (report depth filter) |
