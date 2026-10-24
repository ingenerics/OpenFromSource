# Data Model: Base Project Infrastructure

## Entities

### ProjectConfig

Represents the resolved runtime configuration — defaults merged with overrides.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `base_dir` | `str` | `/var/cache/package_mgmt` | Base path for all cache data |
| `binary_cache` | `str` | `{base_dir}/binary` | Binary package cache (Debian apt layout) |
| `source_cache` | `str` | `{base_dir}/source` | Source package cache (Debian apt layout) |
| `db_path` | `str` | `{base_dir}/package_mgmt.db` | Path to the SQLite database |
| `log_level` | `str` | `INFO` | Logging verbosity |
| `log_dir` | `str` | `{base_dir}/logs` | Log output directory |

**Validation rules**:
- `base_dir` MUST be an absolute path (validated at load time)
- All path fields MUST resolve to writable locations at time of use
- Unknown keys in config files MUST be ignored (forward compatibility)

**Loading order** (last wins):
1. `project.defaults.toml` (shipped, version-controlled)
2. `project.local.toml` (optional, git-ignored, overrides individual keys)

### BinaryCache

Represents the binary package cache directory following Debian apt layout.

| Attribute | Type | Description |
|-----------|------|-------------|
| `path` | `str` | Resolved absolute path (from `binary_cache` config) |
| `exists` | `bool` | Whether the directory already exists |

**Layout**:
```
{binary_cache}/
├── pool/<component>/<letter>/<package>_<version>_<arch>.deb
└── dists/<release>/<component>/binary-<arch>/{Packages.gz,Packages.xz}
```

**State transitions**:
- `absent` → `present` (on init)
- `present` → `present` (idempotent re-init, permissions check)

**Permissions**: `755` (rwxr-xr-x), owner = running user.

### SourceCache

Represents the source package cache directory following Debian apt layout.

| Attribute | Type | Description |
|-----------|------|-------------|
| `path` | `str` | Resolved absolute path (from `source_cache` config) |
| `exists` | `bool` | Whether the directory already exists |

**Layout**:
```
{source_cache}/
├── pool/<component>/<letter>/<package>_<version>.dsc
├── pool/<component>/<letter>/<package>_<version>.orig.tar.*
├── pool/<component>/<letter>/<package>_<version>.debian.tar.*
└── dists/<release>/<component>/source/{Sources.gz,Sources.xz}
```

**State transitions**:
- `absent` → `present` (on init)
- `present` → `present` (idempotent re-init, permissions check)

**Permissions**: `755` (rwxr-xr-x), owner = running user.

### PackageDatabase

Represents the SQLite database at `{base_dir}/package_mgmt.db`.

| Attribute | Type | Description |
|-----------|------|-------------|
| `path` | `str` | Resolved absolute path (from config) |
| `schema_version` | `int` | Expected schema version (stored in `PRAGMA user_version`) |

**Schema version**: `1` (initial).

**Tables** (per Constitution Principle VII):

#### `packages`

| Column | Type | Constraints |
|--------|------|-------------|
| `name` | TEXT | NOT NULL, PRIMARY KEY |
| `requested_version` | TEXT | |
| `status` | TEXT | NOT NULL (pending/verified/failed) |
| `verified` | INTEGER | NOT NULL DEFAULT 0 |

#### `binaries`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT |
| `package_name` | TEXT | NOT NULL |
| `version` | TEXT | NOT NULL |
| `architecture` | TEXT | |
| `verified` | INTEGER | NOT NULL DEFAULT 0 |
| `dependencies` | TEXT | JSON array of dep specs |

#### `sources`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT |
| `package_name` | TEXT | NOT NULL |
| `source_version` | TEXT | |
| `verified` | INTEGER | NOT NULL DEFAULT 0 |

#### `binary_to_source`

| Column | Type | Constraints |
|--------|------|-------------|
| `binary_id` | INTEGER | NOT NULL, FK → binaries.id |
| `source_id` | INTEGER | NOT NULL, FK → sources.id |
| `verified` | INTEGER | NOT NULL DEFAULT 0 |

**State transitions**:
- `absent` → `present` (created on first init)
- `present` → `present` (validated on re-init, no data loss)
- `present` with mismatched schema → error (manual migration required)
