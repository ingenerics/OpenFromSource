# Data Model: Consolidation Fixes

## Entity List

### Binary (DB table, extended)

| Column | Change | Constraints | Description |
|--------|--------|-------------|-------------|
| `circular` | **added** | `INTEGER NOT NULL DEFAULT 0` | Set to `1` when a dependency of this binary closes a resolution cycle (dependency references a package on the current resolution path) |

Additive DDL only. `SCHEMA_VERSION` remains `1` (per spec 005 FR-011 development-stage rule). Existing databases are recreated by developers; no migration runs.

```sql
ALTER TABLE binaries ADD COLUMN circular INTEGER NOT NULL DEFAULT 0;
-- (equivalent form used in EXPECTED_SCHEMA_SQL DDL)
```

### DependencySpec (model, extended)

| Field | Change | Type | Description |
|-------|--------|------|-------------|
| `circular` | **added** | `bool = False` | Serialized into the `binaries.dependencies` JSON entry only when `True`; `from_json` defaults to `False`, so existing rows parse unchanged |

JSON shape:

```json
{"name": "c", "type": "depends", "circular": true}
```

### ResolutionStats (new type)

Frozen dataclass replacing the bare `stats` dict returned by `PackageResolver.resolve()`:

| Field | Type | Description |
|-------|------|-------------|
| `packages` | `int` | packages records created/updated |
| `binaries` | `int` | binaries records created/updated |
| `sources` | `int` | source records created/updated |
| `build_tools` | `int` | unique build tools resolved |
| `build_dep_links` | `int` | source_build_deps links created |
| `virtual_count` | `int` | unique virtual packages resolved (new) |
| `errors` | `list[str]` | best-effort error messages |
| `resolved_source` | `str \| None` | `release/component` the top-level package resolved from |
| `fallback_count` | `int` | dependency records resolved from non-primary sources |

### ProviderCandidate (new type)

Frozen dataclass replacing bare tuples in `query_provides()` / `select_provider()`:

| Field | Type |
|-------|------|
| `package_name` | `str` |
| `version` | `str` |
| `architecture` | `str \| None` |
| `release` | `str` |
| `component` | `str` |
| `provides_version` | `str \| None` (from the provides row, where applicable) |

## State Transitions

### Binaries.status

Unchanged (`current` → `superseded` on version change). The `circular` flag is orthogonal — a binary may be both `current` and `circular`.

### VirtualResolution lifecycle (hygiene)

- Re-init: rows whose `resolved_binary_id` no longer exists in `binaries` are deleted.
- Re-resolution: a cached row whose binary is `superseded` is treated as absent and re-resolved (existing `INSERT OR REPLACE` then updates the row).

### Provides lifecycle (repopulation)

Re-init repopulates `provides` from the loaded index inside one transaction: `DELETE FROM provides` followed by the natural-key inserts. A failed index load leaves the previous snapshot untouched. If the load succeeds but inserts fail, the transaction rolls back, preserving the previous snapshot.

## DDL Delta (EXPECTED_SCHEMA_SQL)

```sql
CREATE TABLE IF NOT EXISTS binaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_name TEXT NOT NULL,
    version TEXT NOT NULL,
    architecture TEXT,
    release TEXT NOT NULL,
    component TEXT NOT NULL,
    dependencies TEXT,
    status TEXT NOT NULL DEFAULT 'current',
    verified INTEGER NOT NULL DEFAULT 0,
    circular INTEGER NOT NULL DEFAULT 0,
    UNIQUE(package_name, version, architecture, release, component)
);
```

All other tables unchanged.
