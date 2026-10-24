# Feature Specification: Build-Tool Dependencies

**Feature Branch**: `004-build-tool-dependencies`
**Status**: Draft

**Input**: User description: "We now have cache dirs for binaries and sources. However, in order to build the sources each package typically has its own set of build tools, which is also made available as a package. It seems this component is still missing from the (offline) design."

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Fetches packages + source into cache from Debian repos |
| **Operator** | Offline system (no network) | Installs packages from the pre-built cache; rebuilds packages from source |

**Phase 1 — Build-Tool Metadata**: Parse and persist build-dependency information from Sources.gz into the database, extending the schema to track which source packages require which build tools.
**Phase 2 — Build-Tool Resolution**: Resolve build-tool binary packages and record them in the database alongside their `source_build_deps` links. No payload download — DB population only, consistent with the Database-First approach.
**Phase 3 — Build-Tool Verification**: Extend the verify command to report build-tool cache completeness and cross-reference each source package against its required build dependencies.

---

### User Story 1 - Record Build-Dependency Metadata (Priority: P1)

The administrator adds a source package. The system extracts its Build-Depends field from Sources.gz, persists the structured list in the database, and records the source-to-build-tool relationship for later resolution.

**Why this priority**: Without parsing and persisting the build-dependency data, no downstream resolution or caching is possible. This is the foundational phase.

**Independent Test**: After `package_mgmt add <pkg>` completes, query the database directly to confirm that each source record has its build-dependency list stored and that the source_build_deps link table contains entries.

**Acceptance Scenarios**:

1. **Given** a source package whose Sources.gz stanza has a Build-Depends field, **When** the package is resolved and added, **Then** the build dependencies are parsed into structured JSON and stored in the database alongside the source record
2. **Given** a source package whose Sources.gz stanza has no Build-Depends field, **When** the package is resolved and added, **Then** an empty build-dependency list is stored and no error is raised
3. **Given** a source package with Build-Depends that include architecture qualifications (e.g., `libfoo:amd64`), **When** the metadata is parsed, **Then** architecture-qualified entries are preserved in the structured JSON

---

### User Story 2 - Resolve and Cache Build Tools (Priority: P2)

When the TOML config has `[fetch] source = true`, the administrator adds a package with source dependencies. The system resolves the build-tool dependency tree, records the build-tool binaries in the database, and links them to the source via the new source_build_deps table — all before any payload download. When `source = false`, build-tool resolution is skipped entirely.

**Why this priority**: Populating the build-tool cache is the core value of this feature — it ensures the offline operator can rebuild packages from source without network access.

**Independent Test**: After resolving a source package with known build dependencies (e.g., `gcc`, `make`), confirm that the `source_build_deps` link table has entries mapping the source to those binaries and that the build-tool binaries have corresponding records in the `binaries` table.

**Acceptance Scenarios**:

1. **Given** a source package with Build-Depends (e.g., `Build-Depends: debhelper-compat (= 13), dh-sequence-python3`) and `[fetch] source = true` in the TOML config, **When** the admin runs `package_mgmt add <pkg>`, **Then** the build-tool binaries are resolved, their dependency trees are computed, binary records are created in the database, and each source-to-build-tool link is recorded in `source_build_deps`
2. **Given** a build tool that is also a runtime dependency of another package (e.g., `libc6`), **When** the build-tool tree is resolved, **Then** a single `binaries` record serves both roles with entries in both `binary_to_source` (for the runtime relationship) and `source_build_deps` (for the build-tool relationship)
3. **Given** a build tool whose own runtime dependencies are not yet in the database, **When** the build-tool tree is resolved, **Then** transitive dependencies of the build tool are also recorded in the `binaries` table and linked via `source_build_deps`

---

### User Story 3 - Verify Build-Tool Cache Completeness (Priority: P3)

The administrator runs the verify command and sees a separate section reporting build-tool statistics, identifying unresolved build dependencies and missing packages.

**Why this priority**: Verification is essential before cache handoff to the operator. Without it, an operator attempting an offline rebuild would discover missing tools only at build time.

**Independent Test**: After running `package_mgmt verify --graph`, confirm the output includes build-tool-specific statistics (total build-tool packages, unresolved build deps) and that the exit code reflects any build-tool issues.

**Acceptance Scenarios**:

1. **Given** a fully resolved cache with all build-tool packages present, **When** the admin runs `package_mgmt verify --graph`, **Then** the output shows zero unresolved build dependencies and all build-tool packages are accounted for
2. **Given** a cache where a build tool is missing (e.g., `debhelper` was not cached), **When** the admin runs `package_mgmt verify --graph`, **Then** the output reports the missing build tool as an unresolved build dependency and the exit code indicates an incomplete cache
3. **Given** a cache where a source package references a build tool that does not exist in the configured archive, **When** the admin runs `package_mgmt verify --graph`, **Then** the output reports the unresolvable build dependency and the exit code indicates an error

---

### Edge Cases

- What happens when a build tool package exists in the archive but is not installable due to unsatisfiable transitive dependencies?
- How does the system handle the same package being both a runtime dependency and a build tool — should separate `binaries` records exist per context, or does one record with entries in both `binary_to_source` and `source_build_deps` suffice?
- What happens when Build-Depends includes build profiles (e.g., `<!stage1>`) that conditionally enable or disable certain dependencies?
- How does the admin resolve version conflicts between the Debian LTS base's build tools and a newer upstream source package's requirements?
- What happens when a source package lists a virtual package as a build dependency (e.g., `Build-Depends: default-jdk`)?
- How does the system handle circular build dependencies (e.g., gcc requires g++ to build, but g++ requires gcc to build)?
- What happens when the build-tools cache is not populated and the operator attempts to rebuild from source?
- How does a Build-Depends that includes alternative dependencies (pipe-separated) get resolved?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST parse and persist build-dependency metadata from Sources.gz `Build-Depends`, `Build-Depends-Arch`, and `Build-Depends-Indep` fields into structured JSON in the database
- **FR-002**: Build-tool packages MUST be recorded as `binaries` table entries in the same database as runtime packages — no separate table, cache directory, or role column is needed. A single `binaries` record serves both runtime and build-tool purposes
- **FR-003**: The `source_build_deps` link table is the sole mechanism for tracking that a particular binary is required as a build tool by a particular source package
- **FR-004**: The system MUST resolve the full transitive dependency tree for build tools before any download, following the same Database-First approach established for runtime packages; build-tool binaries reuse the same `binaries` table and `binaries.dependencies` field for their own runtime dependency tree
- **FR-005**: A `[fetch] source` boolean setting (default `true`) in the TOML config MUST control whether source packages and their build-tool dependencies are resolved and cached during `add`. When `false`, only binary packages are fetched
- **FR-006**: The `verify --graph` command MUST report build-tool-specific statistics, including total build-tool packages, unresolved build dependencies, and missing build-tool records
- **FR-007**: When a package is needed as both a runtime dependency and a build tool, the system MUST store it once in the binary cache with a single `binaries` record; the dual relationship is captured naturally through entries in both `binary_to_source` and `source_build_deps`
- **FR-008**: Version constraints in Build-Depends (e.g., `debhelper (>= 13)`) MUST be parsed and stored in the same structured format used for binary package dependencies
- **FR-009**: The system MUST NOT fail if a source package has no Build-Depends field — an empty build-dependency list is valid

### Key Entities *(include if feature involves data)*

- **Binary (DB table, unchanged)**: Existing table (id, package_name, version, architecture, verified, dependencies) — no new columns needed. A binary is a binary; its role in the dependency graph is determined by which link table references it.
- **Source (DB table)**: Gains a `build_depends` TEXT column to store the structured JSON representation of the package's build dependencies parsed from Sources.gz.
- **SourceBuildDeps (DB link table)**: New table mapping source package records to their required build-tool binary records. Columns: `source_id` (FK → sources.id), `binary_id` (FK → binaries.id), `verified` (INTEGER). A binary appears here if it is a build tool for the linked source — entries in this table constitute the build-tool relationship.
- **BinaryCache (shared)**: The existing binary cache directory stores all `.deb` files. No role-based distinction — a single `.deb` serves both runtime and build-tool purposes.
- **BuildDependencyGraph**: The transitive closure of build-tool dependencies stemming from a source package's Build-Depends, computed at query time from `source_build_deps` joined through `binaries.dependencies`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A source package with 20+ build dependencies (e.g., `linux`) resolves its full build-tool dependency tree, creates binary records for each build tool in the `binaries` table, and records each source-to-build-tool link in `source_build_deps`
- **SC-002**: Build-tool dependency resolution for a standard source package completes within the same 5-second threshold established for runtime dependency resolution
- **SC-003**: The `verify --graph` command reports build-tool cache status as a separate section in the output, using the same INCOMPLETE/COMPLETE status convention
- **SC-004**: After build-tool resolution, the database contains complete build-tool records (binaries + source_build_deps links) for every transitive build dependency of each cached source package
- **SC-005**: Build-tool metadata (build_depends column + source_build_deps table) adds no more than 200 KB of additional database size for a typical medium-complexity source package stack

## Assumptions

- **Archive Coverage**: All required build tools are available in the same Debian archive (same `archive_mirror`, `release`, `component`) as the runtime packages. No external toolchains are needed.
- **No Source Rebuild Yet**: This spec covers caching build tools for offline source rebuilds, not the rebuild process itself. The actual `dpkg-buildpackage` invocation is a future concern.
- **Build Profiles**: Build profiles (e.g., `<!stage1>`, `<!nocheck>`) are initially stored as raw annotations in the structured JSON but not actively filtered during resolution. Profile-aware resolution is a future enhancement.
- **Virtual Packages**: Build dependencies that reference virtual packages (e.g., `default-jdk`) are resolved to concrete binary packages using the same Provides-resolution logic established for runtime packages.
- **Cycle Handling**: Circular build dependencies (e.g., gcc → g++ → gcc) are annotated in the dependency graph and reported to the admin, mirroring the circular-dependency handling for runtime packages.
- **Single Physical Copy**: A package needed as both a runtime dependency and a build tool is stored once in the binary cache with one `binaries` record. The `binary_to_source` and `source_build_deps` link tables capture each relationship independently — no on-disk duplication or role annotation needed.
- **Single Fetch Setting**: A single `[fetch] source` boolean (default `true`) controls whether source packages AND their build-tool dependencies are resolved. When `false`, only binary packages are fetched. Source rebuild capability is an all-or-nothing choice — there is no meaningful scenario where source is cached without its build tools.
