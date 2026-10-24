# Data Model: Multi-Release and Multi-Component Support

## Entity List

### ArchiveSource (config entity, new)

| Field | Type | Description |
|-------|------|-------------|
| `mirror` | `str` | Base URL of the Debian archive (e.g., `http://deb.debian.org/debian`) |
| `release` | `str` | Suite name (e.g., `trixie`, `trixie-backports`, `trixie-security`) |
| `components` | `list[str]` | Component names (e.g., `["main", "contrib", "non-free"]`) |

Validation: No two configured sources may share the same `(release, component)` pair. The mirror is expected to be reachable during init but is not validated at config load time.

### ProjectConfig (modified)

| Field | Change |
|-------|--------|
| `archive_mirror` | Moved into each `ArchiveSource` |
| `release` | Moved into each `ArchiveSource` |
| `component` | Replaced by `components` list on each `ArchiveSource` |
| `sources: list[ArchiveSource]` | **New** — the list of configured archive sources |

No backward compatibility: If `archive_mirror`, `release`, and `component` are present at the top level in the old format, exit with error.

### Binary (DB table, extended)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `INTEGER` | PRIMARY KEY AUTOINCREMENT | Auto-generated unique row ID |
| `package_name` | `TEXT` | NOT NULL | Name of the binary package |
| `version` | `TEXT` | NOT NULL | Package version string |
| `architecture` | `TEXT` | | Target architecture |
| `release` | `TEXT` | NOT NULL | Suite origin (e.g., `trixie`, `trixie-backports`) |
| `component` | `TEXT` | NOT NULL | Component origin (e.g., `main`, `contrib`) |
| `dependencies` | `TEXT` | | JSON array of dependency specs |
| `status` | `TEXT` | NOT NULL DEFAULT `'current'` | Resolution status: `'current'` or `'superseded'` |
| `verified` | `INTEGER` | NOT NULL DEFAULT 0 | Whether the `.deb` has been downloaded and verified |

Identity: `UNIQUE(package_name, version, architecture, release, component)`.

At most one row per `(package_name, architecture)` may have `status = 'current'`. When a new resolution produces a different version for the same `(package_name, architecture)`, the existing `status='current'` row is marked `'superseded'` and a new `status='current'` row is inserted.

### Source (DB table, extended)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `INTEGER` | PRIMARY KEY AUTOINCREMENT | |
| `package_name` | `TEXT` | NOT NULL | |
| `source_version` | `TEXT` | | |
| `release` | `TEXT` | NOT NULL | Suite origin |
| `component` | `TEXT` | NOT NULL | Component origin |
| `verified` | `INTEGER` | NOT NULL DEFAULT 0 | |
| `build_depends` | `TEXT` | | |

Identity: `UNIQUE(package_name, source_version, release, component)`.

### BinaryToSource (DB table, unchanged)

Links `binary_id` → `source_id`. No release/component needed — derivable from the referenced tables.

### SourceBuildDeps (DB table, unchanged)

Links `source_id` → `binary_id`. No release/component needed.

### Packages (DB table, unchanged)

User-level logical request tracking. No release/component needed.

### RepositoryIndex (in-memory, extended)

| Field | Type | Description |
|-------|------|-------------|
| `binary_packages` | `dict[str, list[BinaryPackage]]` | All binary packages from all sources, keyed by name |
| `source_packages` | `dict[str, list[SourcePackage]]` | All source packages from all sources, keyed by name |

The `BinaryPackage` and `SourcePackage` dataclasses gain `release` and `component` fields.

## Schema DDL

```sql
-- SCHEMA_VERSION = 1 (no bump during development)

CREATE TABLE IF NOT EXISTS packages (
    name TEXT NOT NULL PRIMARY KEY,
    requested_version TEXT,
    status TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0
);

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
    UNIQUE(package_name, version, architecture, release, component)
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_name TEXT NOT NULL,
    source_version TEXT,
    release TEXT NOT NULL,
    component TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    build_depends TEXT,
    UNIQUE(package_name, source_version, release, component)
);

CREATE TABLE IF NOT EXISTS binary_to_source (
    binary_id INTEGER NOT NULL REFERENCES binaries(id),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS source_build_deps (
    source_id INTEGER NOT NULL REFERENCES sources(id),
    binary_id INTEGER NOT NULL REFERENCES binaries(id),
    verified INTEGER NOT NULL DEFAULT 0
);
```

## State Transitions

### Init Flow
1. Load config → list of `ArchiveSource` objects
2. Create cache directories (dists/ and pool/ for binary and source)
3. Initialize database with extended schema (v1)
4. For each source `(mirror, release, components)`:
   a. For each component in `components`:
      - Fetch or load `dists/{release}/{component}/binary-amd64/Packages.gz`
      - Parse stanzas, tag each `BinaryPackage` with `(release, component)`
      - Add to merged `RepositoryIndex.binary_packages`
      - Same for `Sources.gz` → `SourcePackage` with `(release, component)`
   b. Merge into single `RepositoryIndex`

### Resolution Flow
1. `add <name>` (no flag) → `PackageResolver.resolve(name, source=None)` — uses first configured source as primary
2. `add <name> --source <release>` → `PackageResolver.resolve(name, source=release)` — uses specified release as primary
3. `ensure_indexes()` loads all sources from config if not already loaded
4. `_resolve_one(name, primary_source)`:
   - Look up in `RepositoryIndex.binary_packages` filtered by primary source first
   - If not found in primary source, search remaining sources in config order
   - If multiple versions found, select highest version satisfying the constraint
   - Check `binaries` for existing `(package_name, architecture)` with `status='current'`:
     - If same version: reuse (no new row)
     - If different version: update old to `status='superseded'`, insert new with `status='current'`
     - If no existing: insert new with `status='current'`
   - Process dependencies recursively, each passing the same `primary_source`

### Operator Query Flow
- `SELECT * FROM binaries WHERE package_name = ? AND status = 'current'` → current resolution
- `SELECT * FROM binaries WHERE package_name = ? ORDER BY id` → full history
- `SELECT * FROM binaries WHERE status = 'current'` → the entire coherent dependency forest
- Direct apt access to `dists/` and `pool/` for standard Debian tooling
