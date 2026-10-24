# Feature Specification: Virtual Package Resolution

**Feature Branch**: `006-virtual-package-resolution`
**Status**: Draft

**Input**: User description: "On top of what is already discussed, remember the database state is the primary source of truth, so merely updating the in-memory objects is not sufficient, nor would it be deterministic. After the init phase the Provides data from the index should be stored in the Provides Db table. The first time a virtual package is resolved, the package choice should be stored in the VirtualResolution table. This way future requests can be ensured to use the same package."

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Fetches packages + source into cache from Debian repos; runs init to populate provides table; runs add which resolves virtual packages |
| **Operator** | Offline system (no network) | Installs packages from the pre-built cache; benefits from deterministic virtual resolution already recorded |

**Phase 1 — Provides Metadata**: Capture `Provides` fields from Packages.gz into the database during init, creating a persistent record of which binary packages satisfy which virtual package names. The `provides` table uses a natural key `(package_name, version, architecture, release, component, virtual_name)` — it is self-contained index metadata with no foreign key to `binaries(id)`.

**Phase 2 — Virtual Resolution at Add Time**: Extend the resolver to detect dependency entries that reference virtual packages. On first encounter, query the `provides` table for candidates, select one deterministically, look up the provider's metadata from the in-memory index, create a `binaries` record for the provider, and record the choice in `virtual_resolutions`. Subsequent encounters of the same virtual name reuse the existing resolution via the `virtual_resolutions` table.

---

### User Story 1 - Record Provides Relationships (Priority: P1)

The administrator runs `package_mgmt init`. The system loads every `Packages.gz` from all configured sources. For each binary package that has a `Provides` field, the system inserts a row in the `provides` table using the natural key `(package_name, version, architecture, release, component, virtual_name)` — no `binaries` row is created during init. This populates the database with all provides relationships from the entire index before any `add` command runs.

**Why this priority**: Without persisted provides data, the resolver cannot resolve virtual package names. The database must be the source of truth so that all downstream operations can query the same mapping that was used during resolution.

**Independent Test**: After running `package_mgmt init` with a Debian index, query `SELECT COUNT(*) FROM provides` — returns the total number of provides relationships across all packages. Query `SELECT package_name FROM provides WHERE virtual_name = 'awk'` — returns gawk, mawk, original-awk.

**Acceptance Scenarios**:

1. **Given** a binary package with a `Provides` field in Packages.gz (e.g., `Package: gawk, Provides: awk`), **When** the index is loaded during init, **Then** a `provides` row exists with `package_name='gawk'`, `virtual_name='awk'`, and the correct `release` and `component` values
2. **Given** a binary package with no `Provides` field, **When** the index is loaded, **Then** no provides records are created for that binary
3. **Given** multiple binary packages providing the same virtual name (e.g., `gawk`, `mawk`, `original-awk` all provide `awk`), **When** the index is loaded, **Then** three separate rows exist in `provides` with distinct `package_name` values

---

### User Story 2 - Resolve Virtual Dependencies (Priority: P2)

The administrator adds a package whose dependency references a virtual package name (e.g., `Depends: awk` or `Build-Depends: c-compiler`). The resolver detects that no binary package with that exact name exists as a real package. It first checks the `virtual_resolutions` table — if the virtual name was already resolved in a previous `add`, it reuses that resolution. Otherwise, it queries the `provides` table for candidate providers, selects one deterministically, looks up the provider's metadata from the loaded index, creates a `binaries` record for the provider (with its real dependencies), records the resolution in `virtual_resolutions`, and continues resolution using the provider's dependency tree.

**Why this priority**: This is the core value — enabling resolution of real-world dependency trees that reference virtual packages. Without it, many packages fail to resolve with "package not found" errors.

**Independent Test**: After adding a package whose dependency tree includes a virtual package (e.g., `add vifm` — many of its dependencies reference virtual package names), confirm that the database contains resolution records in `virtual_resolutions` for those virtual names and that no "package not found" errors appear for virtual packages.

**Acceptance Scenarios**:

1. **Given** a dependency on a virtual package name (e.g., `awk`), **When** the resolver encounters this during `add`, **Then** it looks up `virtual_resolutions` first, and if not found, queries the `provides` table, selects a provider (e.g., `gawk`), creates a `binaries` record for the provider, records the resolution choice in `virtual_resolutions`, and continues dependency resolution using the provider's dependency tree
2. **Given** multiple binary packages providing the same virtual name, **When** the resolver selects one, **Then** the selection is deterministic (same inputs always produce the same choice), and the choice is persisted in `virtual_resolutions`
3. **Given** a virtual package that was already resolved in a previous `add` invocation (e.g., `add vifm` resolved `awk` to `gawk`), **When** a later `add` encounters the same virtual name, **Then** it reuses the existing `virtual_resolutions` record and the same providing binary without re-querying the provides table
4. **Given** a cached virtual resolution pointing to `gawk 5.2.1` from `trixie/main`, **When** a later `add hyprland --source trixie-backports` encounters the same virtual and `trixie-backports` offers `gawk 5.3.0`, **Then** the resolver re-resolves and updates `virtual_resolutions` to point to the newer provider from the primary source

---

### Edge Cases

- What happens when a binary package provides multiple virtual packages (e.g., `Provides: awk, awk-api-2.0`)? → Multiple provides rows with the same package_name but different virtual_name values.
- How does the resolver handle a virtual package that is both a runtime dependency and a build-dependency? → The virtual resolution is shared — the same provider is used for both.
- What happens when a providing package is later removed from the cache? → The provides row remains (index metadata is immutable after init); the `verified` flag on the binaries row indicates whether the `.deb` is present.
- How does the resolver handle a virtual package where none of the providing packages are installable due to unsatisfiable transitive dependencies? → The resolver reports an error for the virtual name, same as for unresolvable real packages.
- What happens when two different dependencies reference the same virtual package but with different version constraints? → The provider must satisfy the most restrictive constraint; if no provider satisfies all, resolution fails.
- How does the system handle alternative dependencies (pipe-separated) where one alternative is a virtual package? → The virtual is resolved if selected as the best alternative; standard alternative handling applies.
- What happens when a cached virtual resolution exists but the current `add --source` specifies a primary source that offers a better (higher version or same version from higher-priority component) provider? → The resolver re-resolves and updates `virtual_resolutions` to the better provider. The old binaries row remains as `status='superseded'` if the version changed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST parse the `Provides` field from each binary Packages.gz stanza during `init` and persist the provides-to-binary mappings in the `provides` database table for all packages in the index — not just in memory.
- **FR-002**: The `provides` table MUST have columns: `package_name TEXT NOT NULL`, `version TEXT NOT NULL`, `architecture TEXT`, `release TEXT NOT NULL`, `component TEXT NOT NULL`, `virtual_name TEXT NOT NULL`, `provides_version TEXT`. The PRIMARY KEY MUST be `(package_name, version, architecture, release, component, virtual_name, provides_version)` — AMENDED by spec 007 (real-mirror validation): the original key without `provides_version` collapsed multi-versioned provides such as `debhelper-compat (= 9) … (= 13)` into one row. An index on `virtual_name` MUST exist for performant lookup during resolution. The table MUST NOT have a foreign key to `binaries(id)`.
- **FR-003**: The `virtual_resolutions` table MUST have columns: `virtual_name TEXT PRIMARY KEY`, `resolved_binary_id INTEGER NOT NULL REFERENCES binaries(id)`. A virtual package resolves to exactly one binary. This row is created when the virtual is first resolved and reused on subsequent encounters.
- **FR-004**: During `init`, the system MUST insert into the `provides` table directly from the parsed Packages.gz stanzas without creating corresponding `binaries` rows. The `binaries` table is populated only by resolution (`add`), not by init.
- **FR-005**: During resolution, when a dependency name is not found as a real package name in the binary index, the resolver MUST first check the `virtual_resolutions` table by `virtual_name`. If a row exists, the resolver MUST compare the cached provider against candidates from the current primary source. If the primary source offers a higher version of the same provider (or a higher-priority component offers the same version), the resolver MUST re-resolve and update `virtual_resolutions`. Otherwise, the cached resolution is reused.
- **FR-006**: If no `virtual_resolutions` row exists for the virtual name, the resolver MUST query the `provides` table for candidate providers, select one deterministically, look up the provider's full metadata from the loaded index, create a `binaries` record for the provider (with its real dependencies), and record the resolution in `virtual_resolutions`.
- **FR-007**: Provider selection MUST be deterministic — given the same index, the same set of providing packages, and the same dependency constraints, the same provider is always selected.
- **FR-008**: When a providing package is already resolved in the dependency tree (e.g., because another dependency already pulled it in), the resolver MUST reuse the existing `binaries` record rather than creating a duplicate.
- **FR-009**: If a virtual package name cannot be resolved (no provider found in the index), the resolver MUST report it as an error but continue resolving remaining dependencies (same best-effort approach as unresolvable real packages).

### Key Entities *(include if feature involves data)*

- **Provides (DB table, new)**: Self-contained index metadata mapping binary package identities to virtual package names. Uses a natural key `(package_name, version, architecture, release, component, virtual_name)` — no foreign key to `binaries(id)`. Populated during `init` from all configured sources. A single binary may provide multiple virtual packages; a single virtual name may be provided by multiple binaries.
- **VirtualResolution (DB table, new)**: Records which real binary was selected for each virtual package name during dependency resolution. Columns: `virtual_name` (TEXT, PRIMARY KEY), `resolved_binary_id` (INTEGER NOT NULL REFERENCES binaries(id)). A virtual package always resolves to exactly one binary. This table is populated lazily during `add` and persists across invocations. The referenced `binaries` row must exist — it is created during resolution when the provider is selected.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After `package_mgmt init`, the `provides` table contains entries for every virtual package name in Packages.gz across all configured sources — verifiable by querying the database.
- **SC-002**: After `package_mgmt add vifm`, zero "Package not found" errors for known virtual packages (`awk`, `c-compiler`, `debhelper-compat`, `perlapi-*`, etc.).
- **SC-003**: The `virtual_resolutions` table contains entries for every virtual dependency resolved during `add`, matching the actual choices made during resolution.
- **SC-004**: Re-running `package_mgmt add vifm` produces identical `virtual_resolutions` entries (deterministic re-resolution).

## Assumptions

- **DB as Source of Truth**: The `provides` and `virtual_resolutions` tables are the authoritative record. The `provides` table is bulk-populated during `init` for all packages in the index — it is self-contained index metadata with no dependency on the `binaries` table. The `virtual_resolutions` table records which provider was selected for each virtual name and persists across `add` invocations; its rows DO reference `binaries(id)` via FK.
- **Natural Key for Provides**: The provides table's natural key extends the binary identity with virtual_name: `(package_name, version, architecture, release, component, virtual_name)`. The first five columns match the `binaries` table's UNIQUE constraint, allowing the resolver to identify the providing binary from a provides row and look up or create the corresponding `binaries` entry.
- **Deterministic Selection**: When multiple packages provide the same virtual name, the selection follows a deterministic rule: the first provider in alphabetical order by package name, preferring packages that are already present in the dependency tree. Version constraints on the dependency are matched against the provider's version.
- **Re-resolution on Source Mismatch**: A cached `virtual_resolutions` entry is reused unless the current `add --source` specifies a primary source that offers a better provider (higher version or same version from higher-priority component). Re-resolution updates `virtual_resolutions` and marks the old binaries row as `status='superseded'` if the version changed.
- **Scope**: Covers both runtime dependencies (`Depends`) and build-dependencies (`Build-Depends`, `Build-Depends-Arch`, `Build-Depends-Indep`).
- **Versioned Provides**: The `Provides` field may include a version (e.g., `Provides: awk-api-2.0 (= 2.0)`). Versioned provides are stored in the `provides` table and used for version-constrained dependency matching.
- **Best-Effort on Failure**: Unresolvable virtual packages produce errors but do not halt resolution of other packages.
- **Re-resolution**: If the index is re-loaded (e.g., after `init` is re-run), the `provides` table is re-populated and `virtual_resolutions` entries remain valid as long as the referenced binaries still exist.

## Clarifications

### Session

- Q: When is the `provides` table populated — during `init` or during `add`? → A: During `init`. The system parses Packages.gz at init time and bulk-populates the `provides` table for all packages in the index, ensuring DB-first resolution has full provider data before any `add` command runs.
- Q: Should `virtual_resolutions` persist across separate `add` invocations or be ephemeral per run? → A: Persist across adds. Once a virtual package is resolved, the record stays in the DB and is reused by all subsequent `add` invocations.
- Q: Does the `provides` table use a FK to `binaries(id)` or a natural key? → A: Natural key. The provides table is self-contained index metadata, not resolution state. No FK means no placeholder binaries rows need to exist during init. During resolution, the provides table is queried for candidates, and a binaries row is created only after a provider is selected.
