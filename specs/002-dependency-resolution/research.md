# Research: Package Dependency Resolution

## Resolved Decisions

### Version Format Handling

- **Decision**: Use `debian.debian_support.Version` (from `python3-debian`) as the canonical version type for both binary and source packages.
- **Rationale**: `Version` is a unified type that handles both binary versions (with `+bN` suffix) and source versions (without). It provides epoch/upstream/debian_revision decomposition, comparison operators, and validation. No need for separate version classes.
- **Alternatives considered**: `apt_pkg.version_compare` (fast but returns only comparison result, no decomposition), custom parsing (redundant - python3-debian already implements the Debian Policy §5.6.12 grammar).

### Package Fetch Method (for downstream spec)

- **Decision**: Use direct HTTP download against the Debian archive URL pattern, constructing URLs from the `Filename` field in Packages.gz (binary) and the `Directory` + `Files` fields in Sources.gz (source).
- **Rationale**: The `Filename` field in Packages.gz gives the exact repository-relative path for each `.deb`. The `Directory` and `Files` fields in Sources.gz give exact paths for `.dsc`, `.orig.tar.*`, and `.debian.tar.*`. This approach:
  - Uses the version string exactly as it appears in the index (no transformation needed)
  - Does not depend on apt's installed state or configuration
  - Works identically on the networked admin system and when reconstructing URLs from cached metadata
  - Gives full control over download logic for offline/apt-offline workflows
- **Alternatives considered**: `apt-get download` / `apt-get source` (requires installed apt, system state, and root-like permissions; version string must match apt's resolved version format exactly). `apt-offline` (good for bulk transport but adds an extra tool dependency and indirect version handling).

### Repository Index Parsing

- **Decision**: Use `deb822.Packages` and `deb822.Sources` from `python3-debian` to parse `Packages.gz` and `Sources.gz` respectively, with `gzip` for decompression.
- **Rationale**: `deb822` provides structured access to all fields as dicts, supports iterating multi-paragraph stanzas, and its `_VersionAccessorMixin.get_version()` returns a `debian.debian_support.Version` object. Pure Python, works offline.
- **Alternatives considered**: `apt_pkg.TagFile` (fast C-backed but returns raw bytes/strings, no version object, more cumbersome for field access). `python3-apt` (requires system APT cache, not suitable for offline metadata from arbitrary archives).

### Version Storage Strategy

- **Decision**: Store the full version string exactly as it appears in the repository index (Packages.gz / Sources.gz) in the database tables. Binary versions include any `+bN` suffix; source versions do not.
- **Rationale**: The stored version must match the version used in the download URL. For binary packages, the `.deb` filename includes the `+bN` suffix. For source packages, the `.dsc` and tarball filenames use the source version without `+bN`. Storing the index-verbatim string guarantees the downstream download step can construct the correct URL or pass the exact version to `apt-get download`.
- **Alternatives considered**: Normalizing versions (stripping epoch, normalizing `+bN`) would require a reverse transformation when constructing download URLs, introducing a source of bugs.

### Binary-to-Source Version Linking

- **Decision**: For each binary record, look up the `Source` field in the binary's Packages.gz stanza. If present, use that value (which may be just a package name or `name (version)`). Look up the source in Sources.gz by package name and match versions. If the `Source` field is absent, the binary package name IS the source package name. The `source_version` in the sources table stores the full version from Sources.gz.
- **Rationale**: The Debian archive's own metadata defines the source→binary mapping. Using the `Source` field is the authoritative way to determine which source package produced a given binary. The optional version pin in the `Source` field handles the case where a binary at a given binary version was built from a specific source version.
- **Alternatives considered**: Heuristic (stripping `+bN` from binary version to get source version) would fail when a source-only upload changes the debian_revision without a corresponding binary NMU, or when epochs differ between binary and source versions.

### Dependency Spec Storage Format

- **Decision**: Store each dependency line as a JSON array of objects, each with keys `name`, `version_constraint` (null or `{"operator": ">=", "version": "5.0"}`), `architecture` (null or "amd64"), and `type` ("depends" | "pre-depends" | "recommends" | etc.).
- **Rationale**: Structured JSON allows querying individual dependencies without additional parsing. The `binaries.dependencies` field in the existing schema is TEXT, which is sufficient for JSON storage. The structure preserves all information from the Depends line including alternative dependencies (pipe-separated) which are stored as parallel entries.
- **Alternatives considered**: Flat text storage (re-parsing needed for every operation), separate dependencies table (over-engineered for v1 — can be normalised later if query performance demands it).

### Architecture Filtering

- **Decision**: Resolve dependencies only for the configured target architecture (default: `amd64`). Architecture-qualified dependencies (e.g., `package:amd64`) are stored with their qualifier; non-qualified dependencies are assumed to match the target architecture. Dependencies for non-target architectures are recorded but marked as architecture-filtered in the JSON.
- **Rationale**: The target architecture is fixed per cache (Debian Trixie amd64). Multi-arch support is not needed for v1.
- **Alternatives considered**: Multi-arch resolution (adds significant complexity, not needed until cross-architecture support is required).

### Dependency Graph Querying

- **Decision**: The dependency graph is not stored as a separate structure. It is computed at query time from the `binaries.dependencies` JSON field by resolving package names against the `packages` and `binaries` tables. The `list --deps` command performs transitive closure walks through `dependencies → packages.name → binaries.package_name` joins.
- **Rationale**: No duplication of graph topology. The database tables already contain all relationships. Query-time computation avoids sync issues between a separate graph table and the actual records.
- **Alternatives considered**: Separate `dependency_edges` table (maintenance burden, sync complexity with upsert semantics).
