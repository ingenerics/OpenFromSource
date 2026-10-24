# Data Model: Build-Tool Dependencies

## Core Entities

### Source (DB table, extended)

The existing `sources` table gains a column for build-dependency metadata.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| *(existing columns)* | | | id, package_name, source_version, verified |
| `build_depends` | TEXT | | Structured JSON array of BuildDependencySpec objects, parsed from `Build-Depends` field in Sources.gz. Each entry carries a `build_context` field (`"build"`, `"build-arch"`, or `"build-indep"`) tracking which Sources.gz field it originated from |

**Validation rules**:
- `build_depends` MUST be a valid JSON array (or NULL/empty for packages without build dependencies)
- Each element MUST conform to the BuildDependencySpec schema below
- `build_depends` is populated at resolution time, same phase as the source record itself

### BuildDependencySpec

A single parsed entry from any of the Build-Depends fields in Sources.gz. Stored as an element in the `sources.build_depends` JSON array.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Package name (e.g., `debhelper`, `gcc`) |
| `version_constraint` | object or null | no | `{operator: ">=", version: "13"}` or null |
| `architecture` | string or null | no | Architecture restriction (e.g., `linux-any`, `amd64`) or null if none |
| `build_context` | string | yes | Origin field: `"build"` (from Build-Depends), `"build-arch"` (from Build-Depends-Arch), or `"build-indep"` (from Build-Depends-Indep) |
| `profiles` | list[str] or null | no | Build profile restrictions (e.g., `["stage1"]`) from `<!...>` annotations, or null |

**Validation rules**:
- `operator` in `version_constraint` must be one of: `>=`, `<<`, `<=`, `=`, `>>`, `!=`
- `version` in `version_constraint` must be a valid Debian version string
- Alternative dependencies (pipe-separated in the Build-Depends field) are stored as separate entries with the same `build_context`
- Build profiles are stored as parsed list items without the `<!` `>` delimiters

### SourceBuildDeps (DB link table, new)

Maps source packages to their required build-tool binary packages. A binary appears here if it is needed to build the linked source.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `source_id` | INTEGER | NOT NULL, FK → sources.id | The source package that requires this build tool |
| `binary_id` | INTEGER | NOT NULL, FK → binaries.id | The binary package serving as a build tool |
| `verified` | INTEGER | NOT NULL DEFAULT 0 | Whether the link is confirmed (binary exists in cache) |

**Identity**: (source_id, binary_id) — used for idempotent upsert.

**Validation rules**:
- Both `source_id` and `binary_id` MUST reference existing records in their respective tables
- A binary may serve as a build tool for multiple sources (many-to-many)
- A source may require multiple build tools (many-to-many)
- A binary that is already linked via `binary_to_source` (i.e., produced by the source) MAY also be linked via `source_build_deps` (i.e., required to build the source) — this captures circular build dependencies

### Binary (DB table, unchanged)

No new columns. The `binaries` table remains:

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| `package_name` | TEXT | NOT NULL | Package name |
| `version` | TEXT | NOT NULL | Full version string from Packages.gz |
| `architecture` | TEXT | | Debian architecture (e.g., `amd64`, `all`) |
| `verified` | INTEGER | NOT NULL DEFAULT 0 | Whether the binary .deb is confirmed available |
| `dependencies` | TEXT | | JSON array of DependencySpec objects |

A binary is a binary — its role (runtime vs build-tool) is determined by which link tables reference it, not by a column on the record itself.

### BuildDependencyGraph (computed at query time, not stored)

The transitive closure of build-tool dependencies stemming from a source package's Build-Depends.

| Component | Source |
|-----------|--------|
| Root nodes | Entries in `sources.build_depends` JSON for each source package |
| Edges | `source_build_deps` → `binaries.id` → `binaries.dependencies` (transitive closure through the binaries' own runtime dependencies) |
| Leaf nodes | Build-tool binaries whose own `dependencies` are empty or reference only base system packages |

**Validation rules**:
- Every dependency name referenced in a build tool's own `binaries.dependencies` must have a matching record in `binaries`
- Cycles are detected during resolution and annotated with a `circular:` prefix on the dependency name, matching the runtime dependency behaviour

## Relationships

```
sources (1) ──< (N) source_build_deps (N) >── (1) binaries
```

- A Source may require multiple Build-Tool binaries (via `source_build_deps`)
- A Binary may be required by multiple Sources (via `source_build_deps`)
- A Binary's relationship to a Source via `source_build_deps` is independent of its relationship via `binary_to_source` — the same pair may appear in both tables
- Build-tool binaries resolve their own runtime dependencies through the standard `binaries.dependencies` → `binaries.package_name` resolution path — no special handling needed

## State Transitions

### Source.`build_depends`
- Populated during resolution from Sources.gz (same phase as source record creation)
- Updated if the source is re-resolved (e.g., after index update)

### SourceBuildDeps.`verified`
- Created with `verified = 0` during resolution (when the build-dependency graph is computed)
- The `verified` flag transitions to `1` in a future download-focused spec when the linked binary `.deb` is confirmed present on disk. In the current spec scope (metadata resolution only), `verified` remains `0` for all entries
- The `verify --graph` command checks for unresolved build deps by comparing `sources.build_depends` JSON entries against `source_build_deps` links, not by checking the `verified` flag
