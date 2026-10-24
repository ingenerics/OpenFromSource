# Data Model: Package Dependency Resolution

## Core Entities

### DependencySpec

A single parsed entry from a binary package's `Depends` line, stored as a JSON object within `binaries.dependencies`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Package name (e.g., `perl`, `perl:amd64`) |
| `version_constraint` | object or null | no | `{operator: ">=", version: "5.0"}` or null |
| `architecture` | string or null | no | Architecture qualifier (`amd64`) or null for any |
| `type` | string | yes | Dependency type: `depends`, `pre-depends`, `recommends`, `suggests`, `enhances`, `breaks`, `conflicts`, `provides` |

**Validation rules**:
- `operator` in `version_constraint` must be one of: `>=`, `<<`, `<=`, `=`, `>>`, `!=`
- `version` in `version_constraint` must be a valid Debian version string (matching `Version.re_valid_version`)
- Alternative dependencies (pipe-separated in the Depends field) are stored as separate entries with the same `type`
- Architecture-qualified names retain the qualifier as part of `name`

**Example**: The Depends line `perl (>= 5.0), libc6 (>= 2.0) | libc7` produces:
```json
[
  {"name": "perl", "version_constraint": {"operator": ">=", "version": "5.0"}, "architecture": null, "type": "depends"},
  {"name": "libc6", "version_constraint": {"operator": ">=", "version": "2.0"}, "architecture": null, "type": "depends"},
  {"name": "libc7", "version_constraint": null, "architecture": null, "type": "depends"}
]
```

### Package (DB `packages` table)

Base record per unique package name in the dependency tree.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `name` | TEXT | NOT NULL, PRIMARY KEY | Package name (e.g., `vifm`) |
| `requested_version` | TEXT | | Version specified by the admin, or NULL if "latest" |
| `status` | TEXT | NOT NULL | `pending` / `verified` / `failed` |
| `verified` | INTEGER | NOT NULL DEFAULT 0 | Whether the package is confirmed available in the archive |

**State transitions**:
- `pending` → `verified` (when all its binary and source records are confirmed)
- `pending` → `failed` (when resolution determines the package cannot be satisfied)

### Binary (DB `binaries` table)

A specific binary build of a package at a given version for a given architecture.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| `package_name` | TEXT | NOT NULL | Package name (FK → packages.name) |
| `version` | TEXT | NOT NULL | Full version string from Packages.gz (may include `+bN`) |
| `architecture` | TEXT | | Debian architecture (e.g., `amd64`, `all`) |
| `verified` | INTEGER | NOT NULL DEFAULT 0 | Whether the binary .deb is confirmed available |
| `dependencies` | TEXT | | JSON array of DependencySpec objects |

**Identity**: (package_name, version, architecture) — used for idempotent upsert.

**Validation rules**:
- `version` must match the Debian version format (including `+bN` if present)
- `dependencies` must be valid JSON matching the DependencySpec schema
- A binary may have zero dependencies (base system packages)

**State transitions**:
- Created with `verified = 0` during resolution
- Set to `verified = 1` when the `.deb` file is confirmed present in the archive (in a download-focused future spec)

### Source (DB `sources` table)

A source package that produces one or more binary packages.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| `package_name` | TEXT | NOT NULL | Source package name |
| `source_version` | TEXT | | Full version string from Sources.gz (never has `+bN`) |
| `verified` | INTEGER | NOT NULL DEFAULT 0 | Whether the source files are confirmed available |

**Identity**: (package_name, source_version) — used for idempotent upsert.

**Validation rules**:
- `source_version` MUST NOT contain a `+bN` suffix (source packages never carry binary NMU revisions)
- If a binary has no source (e.g., arch-independent metapackage), `source_version` is NULL

### BinaryToSource (DB `binary_to_source` table)

Links binary packages to their corresponding source packages for deterministic rebuild tracking.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `binary_id` | INTEGER | NOT NULL, FK → binaries.id | |
| `source_id` | INTEGER | NOT NULL, FK → sources.id | |
| `verified` | INTEGER | NOT NULL DEFAULT 0 | |

**State transitions**:
- A binary may link to multiple sources if it can be built from multiple source versions
- Multiple binaries may link to the same source (a source package produces many binaries)

### RepositoryIndex (in-memory, not a DB table)

A parsed representation of the repository metadata, loaded from Packages.gz and/or Sources.gz.

| Attribute | Type | Description |
|-----------|------|-------------|
| `binary_packages` | dict[str, list[BinaryPackageEntry]] | Package name → list of available binary versions |
| `source_packages` | dict[str, list[SourcePackageEntry]] | Package name → list of available source versions |
| `target_arch` | str | Configured target architecture (default: `amd64`) |

**BinaryPackageEntry** (parsed from Packages.gz stanza):

| Field | Source field | Type |
|-------|-------------|------|
| `package` | Package | str |
| `version` | Version | debian.debian_support.Version |
| `architecture` | Architecture | str |
| `depends` | Depends | str (raw) |
| `source` | Source | str or None |
| `filename` | Filename | str |
| `sha256` | SHA256 | str |

**SourcePackageEntry** (parsed from Sources.gz stanza):

| Field | Source field | Type |
|-------|-------------|------|
| `package` | Package | str |
| `version` | Version | debian.debian_support.Version |
| `directory` | Directory | str |
| `files` | Files | list[dict] |
| `build_depends` | Build-Depends | str or None |

### DependencyGraph (computed at query time, not stored)

The resolved dependency tree. Not a separate table — computed from `binaries.dependencies` via transitive closure queries.

| Component | Source |
|-----------|--------|
| Root nodes | `packages` records where `requested_version IS NOT NULL` |
| Edges | `binaries.dependencies` JSON → look up each dependency name in `packages` → find all `binaries` for that package |
| Leaf nodes | Binaries whose `dependencies` is empty or reference only base system packages |

**Validation rules**:
- Every dependency name referenced in `binaries.dependencies` must have a matching `packages.name` record (or be a known base system package)
- Cycles are detected during resolution and annotated in a `circular:` prefix on the dependency name

## Relationships

```
packages (1) ──< (N) binaries (1) ──< (N) binary_to_source (N) >── (1) sources
```

- A Package (row in `packages`) may have multiple Binary rows (one per version/architecture)
- A Binary may link to multiple Source rows (multiple source versions can produce the same binary)
- A Source may be linked from multiple Binary rows (a source produces many binaries)
- A Binary may have zero Source links (metapackages without source)

## Key Design Decision: Version String Fidelity

The version strings stored in `binaries.version` and `sources.source_version` are stored **exactly as they appear in the repository index** (Packages.gz / Sources.gz). This is critical because:

1. **Download URL construction**: The `.deb` filename uses the exact version string from Packages.gz including any `+bN` suffix. The `.dsc` filename uses the exact version from Sources.gz.
2. **apt-get compatibility**: If future specs use `apt-get download`/`apt-get source`, those commands require the exact version string from the respective index.
3. **Apt-offline signature generation**: The apt-offline bundle format identifies packages by their full version string as it appears in the archive.

Binary and source versions for the same logical package MAY differ (e.g., binary has `1.0-1+b1` while source has `1.0-1`). The `binary_to_source` link table tracks this mapping without requiring version string normalisation.
