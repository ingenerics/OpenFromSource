# Feature Specification: Package Dependency Resolution

**Feature Branch**: `002-dependency-resolution`
**Status**: Draft

**Input**: User description: "Establish dependency tree for requested packages, populate all database tables before any download, with reusable version parsing logic across binary and source package flows."

## Clarifications

### Session

- Q: When the repository index contains multiple versions of the same package, which version is selected for the `binaries` record when the admin does not provide a version pin? → A: Select the latest version (highest version number per Debian version comparison rules) from the index.
- Q: When multiple packages are specified in a single `add` invocation and one fails to resolve, what happens to the records already created for the successful packages? → A: Best-effort — each package is resolved independently. Successful packages are committed; failures are reported with an error. No rollback of successful results.
- Q: Should dependency resolution stop at base system packages (Priority: required / Essential: yes) or resolve all transitive dependencies universally? → A: Resolve all transitive dependencies universally. Base system packages will be populated by a future `ensure-base` command, so there is no need for special-casing during resolution.
- Q: How does `add` obtain the repository indexes (Packages.gz, Sources.gz) needed to resolve dependencies? → A: Index acquisition is a required step of the `add` workflow — the command fetches them from the configured Debian mirror if not already available locally. Index metadata downloads are distinct from package payload downloads and are permitted at this phase.
- Q: When adding multiple packages, how does the admin specify a version for individual packages? → A: Per-package version via `pkg=version` syntax (e.g., `add vifm=2.1-1 nano`). The `=version` part is optional — when omitted, the latest version is selected per Q1. The `--version` flag is not used.

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Resolves and registers packages into the cache database |
| **Operator** | Offline system (no network) | Consumes the pre-populated cache (downstream — no dependency resolution) |

**Phase 1 — Database-First Registration**: The administrator specifies one or more packages to add to the cache. The system resolves their full dependency tree from repository metadata (Packages.gz / Sources.gz), creates records in every database table, and marks them as unverified. No package payloads are downloaded — this phase produces only a populated database and a resolved dependency graph.

**Phase 2 — Dependency Inspection**: The administrator inspects the resolved graph to verify completeness before any download workflow begins.

---

### User Story 1 — Binary & Source Dependency Resolution (Priority: P1)

An administrator wants to add a package (e.g. `vifm`) to the cache. The system must determine everything required: the package itself, its runtime dependencies (binary), their corresponding source packages, and the transitive closure of all dependencies — all before any `.deb` or source payload is fetched.

**Why this priority**: Without dependency resolution, no package can be added to the cache. This is the foundation for every future cache operation (add, sync, download). It is the core of the populate-tables-up-front design.

**Independent Test**: Run `package_mgmt add vifm` against a repository index that includes vifm, perl, and perl's transitive dependencies. The `packages` table contains vifm + all resolved packages. The `binaries` table contains vifm's binary entry plus at minimum perl and its immediate dependencies. The `sources` table contains source entries for every binary. The `binary_to_source` table links each binary to its source. Every record has `verified = 0`. Re-running `add vifm` is idempotent — records are updated, never duplicated.

**Acceptance Scenarios**:

1. **Given** repository metadata (Packages.gz, Sources.gz) for the Debian Trixie archive,
   **When** the administrator runs `package_mgmt add vifm`,
   **Then** the `packages` table contains exactly one row for vifm with `status = 'pending'` and `verified = 0`.

2. **Given** vifm's Depends field declares `perl (>= 5.0)` and perl declares further dependencies,
   **When** the dependency resolution completes,
   **Then** the `binaries` table contains rows for vifm, perl, and every package in perl's transitive dependency closure, each with its version, architecture, and a `dependencies` field listing its direct dependency specs.

3. **Given** binary records have been created,
   **When** source resolution completes,
   **Then** the `sources` table contains one row per unique source package across all binary records, and the `binary_to_source` table links every binary row to its corresponding source row.

4. **Given** the administrator runs `package_mgmt add vifm` a second time,
   **When** the records already exist,
   **Then** no duplicate rows are created — existing `packages` records have their `status` and `verified` fields updated if the index data has changed.

5. **Given** a package has no source package (e.g. an architecture-independent metapackage),
   **When** source resolution finds no matching Sources.gz entry,
   **Then** the source record is created with `source_version = NULL` and the binary_to_source link is still created.

---

### User Story 2 — Dependency Graph Inspection (Priority: P2)

An administrator wants to review the resolved dependency graph — which packages are registered, which binaries have associated sources, and whether any dependencies are missing — before proceeding to fetch payloads.

**Why this priority**: Inspection provides the feedback loop that makes the populate-tables-up-front pattern useful. It allows the administrator to verify correctness, catch resolution gaps, and confirm the graph before any network activity.

**Independent Test**: After resolving a package, run a command to display the graph. The output shows package names, their binary dependencies, linked sources, and version annotations. A package with a missing source dependency is flagged.

**Acceptance Scenarios**:

1. **Given** resolved dependency records in the database,
   **When** the administrator runs `package_mgmt list --deps vifm`,
   **Then** the output lists vifm and every transitive dependency, each annotated with whether its binary and source records exist and whether they are verified.

2. **Given** a binary exists in the database but its source record is missing (source_version is NULL),
   **When** the administrator inspects the graph,
   **Then** that binary is flagged as "source missing" in the output.

3. **Given** all records are populated,
   **When** the administrator runs `package_mgmt verify --graph`,
   **Then** the output reports the total count of packages, binaries, sources, the number of unresolved dependencies (packages in `binaries.dependencies` not present as rows), and the count of unlinked binaries.

---

### Edge Cases

- What happens when a requested package is not found in the repository index? — The system reports the package as unresolvable and does not create a record.
- How does the system handle circular dependencies (e.g., a → b → a)? — The resolver detects cycles, records the dependency but marks it as circular, and does not infinitely recurse.
- What about virtual packages (e.g., a dependency provided by multiple real packages)? — The system records the virtual dependency with all real providers listed; no single provider is chosen without further disambiguation.
- How are architecture-specific dependencies handled (e.g., `:amd64` qualifiers)? — The dependency specifier is stored as-is in `binaries.dependencies`; the resolver filters by the target architecture defined in configuration.
- What if the repository index is absent or unparseable? — The `add` command reports a clear error and no records are modified.
- How does the system handle version constraints like `>=`, `<<`, `=`? — Each constraint is parsed and stored as part of the dependency spec in `binaries.dependencies`; the resolver does not evaluate version satisfaction — it only records what is required.
- What if a binary package has multiple source packages across different versions? — Each unique (package_name, version) pair in `binaries` creates its own `binary_to_source` link; a single binary may link to multiple source versions.
- How does the administrator resolve a dependency that has no matching package in any configured archive? — The dependency is recorded but its `verified` field remains 0; the administrator is informed during `verify --graph`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept one or more package names as input, optionally with a version pin per package using `package=version` syntax, and resolve their full transitive dependency tree from the configured repository index (Packages.gz). If the index is not available locally, the system MUST fetch it from the configured Debian mirror before resolution begins. Index metadata downloads are distinct from package payload downloads and are permitted at this phase. When no version is specified for a package, the latest available version in the index is selected.
- **FR-002**: System MUST create exactly one `packages` record per unique requested or transitively-resolved package name, with `status` set to `pending` and `verified` set to `0`.
- **FR-003**: System MUST create one `binaries` record per unique (package_name, version, architecture) tuple in the dependency tree, with the `dependencies` field containing the parsed Depends line as a JSON array of dependency specs.
- **FR-004**: System MUST resolve the corresponding source package for each binary record from Sources.gz and create one `sources` record per unique source (package_name, source_version).
- **FR-005**: System MUST create `binary_to_source` links mapping every binary record to its corresponding source record(s).
- **FR-006**: System MUST be idempotent — re-running `add` for the same package name updates existing records rather than creating duplicates. Identity is determined by the (package_name, version, architecture) tuple for binaries and (package_name, source_version) for sources.
- **FR-007**: System MUST detect circular dependencies and terminate resolution for the affected branch, recording the circular dependency in the record's metadata without infinite recursion.
- **FR-008**: System MUST report unresolvable packages (not found in index) with a clear error and no database modification for that specific package. When multiple packages are specified in a single invocation, each package is resolved independently — success of one does not depend on success of another, and failures do not roll back successful resolutions.
- **FR-009**: System MUST store dependency specifiers with their original version constraints (``>=``, `<<`, `=`, etc.) as-is in the JSON `binaries.dependencies` field without evaluating them.
- **FR-010**: System MUST support an inspect command (`list --deps <pkg>`) that displays the resolved dependency graph with verification status for each node.
- **FR-011**: System MUST support a verification command (`verify --graph`) that reports completeness statistics: total packages, binaries, sources, unresolved dependencies, and unlinked binaries.
- **FR-012**: System MUST ignore virtual package resolution at this stage — the dependency specifier is recorded but no automatic provider selection occurs without further disambiguation.
- **FR-013**: System MUST handle architecture-qualified dependencies (e.g. `package:amd64`) by storing the qualifier and filtering against the configured target architecture.
- **FR-014**: When no explicit version is provided by the administrator, the resolver MUST select the highest available version of each package from the repository index, using Debian version comparison rules (epoch → upstream_version → debian_revision).

### Key Entities *(include if feature involves data)*

- **Package (DB `packages` table)**: Base record per unique package name with `requested_version` (if specified by admin) and `status` (pending/verified/failed). Identified by `name`.
- **Binary (DB `binaries` table)**: A specific binary build of a package at a given version for a given architecture. The `dependencies` field stores the parsed Depends as a JSON array. Identified by (package_name, version, architecture).
- **Source (DB `sources` table)**: A source package that produces one or more binary packages. The `source_version` may differ from any binary version it produces. Identified by (package_name, source_version).
- **BinaryToSource (DB `binary_to_source` table)**: Link table mapping binary rows to their source rows, enabling deterministic rebuild tracking.
- **RepositoryIndex**: Parsed metadata from Packages.gz (binary) and Sources.gz (source). Provides the name → metadata mapping needed for dependency lookup.
- **DependencyGraph**: Resolved tree structure mapping each package to its transitive dependencies. Not a separate table — computed from `binaries.dependencies` at query time.
- **DependencySpec**: A single entry in a Depends line — package name, optional version constraint (operator + version), and optional architecture qualifier. Stored as JSON in `binaries.dependencies`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A package with no dependencies (or only base system dependencies) resolves and populates all four tables (packages, binaries, sources, binary_to_source) with correct cross-references in under 2 seconds on a standard Debian Trixie workstation.
- **SC-002**: A package with moderate dependencies (e.g., `vifm` → `perl` → 5-10 transitive deps) resolves fully with no missing binary or source records, verifiable via `list --deps`.
- **SC-003**: The resolver handles at least 1000 transitive dependencies without measurable degradation in correctness (all links valid, no duplicates, no infinite loops).
- **SC-004**: Running `add` for the same package twice produces identical database state (idempotency verified by row count and checksum of `binary_to_source` links).
- **SC-005**: An unresolvable package (e.g. `nonexistent-pkg-12345`) produces an error message and leaves the database in its prior state (no phantom records).
- **SC-006**: Circular dependency scenarios (e.g. constructed test case a→b→c→a) resolve without error — all non-circular nodes are recorded, circular dependency is annotated.

## Assumptions

- **Repository Index Acquisition**: The repository index metadata (Packages.gz, Sources.gz) is fetched by `add` from the configured Debian mirror if not already available locally. Index downloads are distinct from package payload downloads (`.deb`, `.dsc`, source tarballs) and are required for dependency resolution to function.
- **Target Architecture**: The configured target architecture is `amd64` (Debian Trixie default). Architecture-filtered resolution filters to this value.
- **Version Constraint Semantics**: Version constraints are parsed and stored but not evaluated during resolution. The `add` command records what is needed; satisfaction checking is deferred to a later phase when actual package versions are downloaded.
- **Single Archive**: Resolution operates against a single configured Debian archive (e.g. `http://deb.debian.org/debian`). Multi-archive or PPA resolution is out of scope.
- **No Payload Download**: No `.deb`, `.dsc`, or source tarball is downloaded during dependency resolution. This spec covers database population only.
- **apt-offline Compatibility**: The resolved dependency data recorded in the database must be sufficient to generate an apt-offline signature bundle in a future spec. This means all package names, versions, and dependency relationships must be present and queryable.
- **vifm Viability**: The `vifm` package and its `perl` dependency serve as the canonical development and testing example. Any package that differs significantly in its dependency pattern (e.g. metapackages, virtual provides) should be verified separately.
- **Universal Transitive Resolution**: The resolver does not special-case base system packages (Priority: required, Essential: yes). All transitive dependencies are resolved universally. Base system packages will be populated separately by a future `ensure-base` command.
