# Data Model: Virtual Package Resolution

## Entity List

### Provides (DB table, new)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `package_name` | `TEXT` | NOT NULL | Providing binary's package name |
| `version` | `TEXT` | NOT NULL | Providing binary's version |
| `architecture` | `TEXT` | | Target architecture |
| `release` | `TEXT` | NOT NULL | Suite origin of the providing binary |
| `component` | `TEXT` | NOT NULL | Component origin of the providing binary |
| `virtual_name` | `TEXT` | NOT NULL | The virtual package name |
| `provides_version` | `TEXT` | | Optional version constraint (NULL for unversioned provides) |

Identity: `PRIMARY KEY (package_name, version, architecture, release, component, virtual_name, provides_version)`.

> **AMENDED by spec 007 (US9 validation)**: the original key omitted
> `provides_version`. Real archive data has binaries providing the same virtual at several
> versions (e.g. `debhelper` provides `debhelper-compat (= 9) … (= 13)`), which the original key
> collapsed into a single row. `provides_version` is now part of the identity.

This table is self-contained index metadata. It has no foreign key to `binaries(id)`. It is populated during `init` from all configured sources and is query-only during resolution.

Index: `idx_provides_virtual_name ON provides(virtual_name)` for performant lookup.

Relationships:
- A single binary (identified by its natural key) may provide multiple virtual packages
- A single virtual name may be provided by multiple binaries

### VirtualResolution (DB table, new)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `virtual_name` | `TEXT` | PRIMARY KEY | The virtual package name |
| `resolved_binary_id` | `INTEGER` | NOT NULL REFERENCES binaries(id) | The chosen provider's binaries row |

A virtual package name always resolves to exactly one binary. If the same virtual is encountered again (in another dependency or build-dep), the existing resolution is reused.

This table IS a child of `binaries(id)` — the FK ensures the referenced provider exists. The row is created during `add` when a provider is selected.

### Binary (DB table, referenced, unchanged from spec 005)

The existing `binaries` table with `(package_name, version, architecture, release, component)` UNIQUE constraint. No changes needed.

## Schema DDL

```sql
-- Additions to existing schema (SCHEMA_VERSION stays 1)

CREATE TABLE IF NOT EXISTS provides (
    package_name TEXT NOT NULL,
    version TEXT NOT NULL,
    architecture TEXT,
    release TEXT NOT NULL,
    component TEXT NOT NULL,
    virtual_name TEXT NOT NULL,
    provides_version TEXT,
    PRIMARY KEY (package_name, version, architecture, release, component, virtual_name, provides_version)
);

CREATE INDEX IF NOT EXISTS idx_provides_virtual_name ON provides(virtual_name);

CREATE TABLE IF NOT EXISTS virtual_resolutions (
    virtual_name TEXT PRIMARY KEY,
    resolved_binary_id INTEGER NOT NULL REFERENCES binaries(id)
);
```

## State Transitions

### Init Flow (US1)
1. Load all configured sources and parse Packages.gz stanzas
2. For each binary package with a `Provides` field:
   a. Parse the field into `(virtual_name, version)` tuples
   b. Insert a row into `provides` with the natural key
3. No `binaries` rows are created during init

### Resolution Flow (US2)
1. Encounter dependency name `X`
2. Check if `X` is a real package in the in-memory index → if found, resolve normally
3. If not found, query `virtual_resolutions`:
   ```sql
   SELECT resolved_binary_id FROM virtual_resolutions WHERE virtual_name = ?
   ```
   - If row exists, use the `resolved_binary_id` directly (skip steps 4-6)
4. Query `provides` for candidates:
   ```sql
   SELECT package_name, version, architecture, release, component
   FROM provides WHERE virtual_name = ?
   ```
5. Select provider deterministically (alphabetical, prefer already-resolved)
6. Look up provider's full metadata from the loaded in-memory index
7. Call `upsert_binary()` to create/resolve the binaries row (with real dependencies)
8. Insert into `virtual_resolutions`:
   ```sql
   INSERT INTO virtual_resolutions (virtual_name, resolved_binary_id) VALUES (?, ?)
   ```
9. Continue resolving the provider's dependency tree recursively
