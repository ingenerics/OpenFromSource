# Feature Specification: Multi-Release and Multi-Component Support

**Feature Branch**: `005-multi-release-and-components`
**Status**: Draft

**Input**: User description: "We want to mirror the actual cache structure used by Debian to remove any future friction during bootstrap with local cache. No schema version bump is needed at this point (development stage). The entire dir hierarchy should mimic a real Debian cache however. In addition, we want to be able to keep multiple versions of binaries, mapping to different releases and/or components. Keeping the history fully transparent in the Db tables, ref status flag from discussion."

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Fetches packages + source into cache from Debian repos across multiple suites and components |
| **Operator** | Offline system (no network) | Installs packages from the pre-built cache; rebuilds packages from source; may need to handle multiple versions of the same package in the pool |

**Phase 1 — Cache Layout & Config**: Define the Debian-mirroring cache directory hierarchy, extend the config format to support multiple archive sources, and update the schema to track release/component provenance and resolution history via a status flag.

**Phase 2 — Multi-Source Index Loading**: Extend the init command to load Packages.gz and Sources.gz from all configured sources, tag each entry with its (release, component) origin, and persist the data.

**Phase 3 — Resolution with Source Targeting**: Implement append-only binaries with status transitions. The `add` command targets a specific source by default (first configured source, typically `trixie/main`), with a `--source` flag to override. The resolver uses the targeted source as the primary resolution anchor, falling back to other configured sources for transitive dependencies. This produces one coherent tree while preserving full history for operator fallback.

---

### User Story 1 - Debian-Compatible Cache Layout (Priority: P1)

An administrator initialises the project. The system creates a cache directory tree that exactly mirrors a standard Debian apt repository layout. The binary cache has a `dists/` tree structured as `dists/{release}/{component}/binary-{arch}/Packages.gz` and a shared `pool/` tree structured as `pool/{component}/{prefix}/{package}_{version}_{arch}.deb` (where `{prefix}` follows Debian's pool naming convention). The source cache mirrors the same `dists/` layout for `Sources.gz` and uses the `pool/` tree for source files following the `Directory` and `Files` fields from the Sources index. The `pool/` is shared across all releases — the same component's packages reside in the same pool directory regardless of which release references them. This layout allows the operator to use the cache directly as an apt source during bootstrap without any transformation.

**Why this priority**: Eliminating friction during operator bootstrap is the primary driver of this feature. If the cache does not look like a standard Debian repository, the operator cannot use standard Debian tooling against it.

**Independent Test**: After running `package_mgmt init`, inspect the cache directory tree. The binary cache has `dists/trixie/main/binary-amd64/` (and any other configured sources), the source cache has `dists/trixie/main/source/`, and both share a common `pool/` tree with component subdirectories (e.g., `pool/main/`). The layout matches a standard Debian apt repository.

**Acceptance Scenarios**:

1. **Given** a configured archive source with release `trixie` and component `main`, **When** init runs, **Then** the binary cache contains `dists/trixie/main/binary-amd64/Packages.gz` and the source cache contains `dists/trixie/main/source/Sources.gz`
2. **Given** two configured sources sharing the same component (`trixie/main` and `trixie-backports/main`), **When** init runs, **Then** both releases' metadata lives under separate `dists/` paths but their `.deb` files share the same `pool/main/` directory
3. **Given** a complete cache with a binary at `pool/main/libd/libdrm2/libdrm2_2.4.110_amd64.deb`, **When** the operator adds the cache as an apt source (`deb [trusted=yes] file:/path/to/cache trixie main`), **Then** apt can resolve and fetch the package using the `Filename` field from the local Packages.gz
4. **Given** multiple versions of the same package from different releases (e.g., `libdrm2_2.4.110_amd64.deb` from trixie and `libdrm2_2.4.120_amd64.deb` from trixie-backports, both under `pool/main/`), **When** apt scans the pool, **Then** both files coexist without conflict — standard Debian pool naming handles multiple versions by distinct filenames

---

### User Story 2 - Multi-Source Configuration (Priority: P1)

An administrator configures the project to consume packages from multiple Debian archive sources — for example, the main release (`trixie/main`) plus backports (`trixie-backports/main`). The configuration lists each source with its mirror, release name, and list of component names. The system validates that no two sources produce identical `(release, component)` pairs.

**Why this priority**: The config defines the scope of the cache. Without multi-source config, the system is limited to a single suite/component and cannot satisfy real-world use cases like pulling Hyprland from backports.

**Independent Test**: Write a configuration with two archive sources. Run `package_mgmt config` and verify both are listed with their mirror, release, and components. A configuration with duplicate `(release, component)` entries is rejected.

**Acceptance Scenarios**:

1. **Given** a configuration file listing `trixie/main` and `trixie-backports/main` as sources, **When** the admin runs `package_mgmt config`, **Then** the output lists both sources with all details
2. **Given** a configuration with two identical `(release, component)` pairs, **When** the admin runs any command, **Then** the system reports a duplicate source error and exits
3. **Given** a mixture of scalar and multi-source config (migration period), **When** the config is loaded, **Then** the system handles both formats and normalises to the multi-source representation

---

### User Story 3 - Provenance-Tracked Database Schema (Priority: P1)

The database schema captures the origin of every binary and source package. The `binaries` table gains `release` and `component` columns to track which suite and area each binary came from. The `sources` table does the same. A `status` column on `binaries` tracks resolution currency (`'current'` or `'superseded'`), enabling transparent version history while maintaining a coherent resolved tree. The schema version remains 1 during development.

**Why this priority**: The schema is the foundation for all query, resolution, and operator bootstrap workflows. Getting it right now avoids painful migrations later. No version bump is needed because the feature is being developed before any stable release.

**Independent Test**: After init with multiple sources, query the database. The `binaries` table contains rows with distinct `release` values. Querying `SELECT DISTINCT release FROM binaries` returns all configured releases.

**Acceptance Scenarios**:

1. **Given** a binary package loaded from `trixie/main`, **When** the database is queried, **Then** the corresponding `binaries` row has `release='trixie'` and `component='main'`
2. **Given** the same package name and version existing in both `trixie/main` and `trixie-backports/main`, **When** both sources are loaded, **Then** two separate rows exist in `binaries` — one per release — each with its own dependencies
3. **Given** a newly resolved package, **When** committed to the database, **Then** its `binaries` row has `status='current'`

---

### User Story 4 - Source-Targeted Resolution (Priority: P2)

An administrator adds a package with `package_mgmt add vifm`. By default, the resolver resolves from the first configured source (typically `trixie/main`). The administrator can target a different source with the `--source` flag: `package_mgmt add hyprland --source trixie-backports`. The resolver uses the targeted source as the primary resolution anchor. Transitive dependencies are resolved from the targeted source first, falling back to other configured sources if a package is not found. This ensures that a package from backports pulls in its required (newer) dependencies from backports when available, while still finding common dependencies in the base suite.

**Why this priority**: The operator needs to know what changed and why. If rebuilding against the current library fails, the superseded version is still in the pool and the database still references it. This is critical for the offline rebuild use case.

**Independent Test**: Run `package_mgmt add vifm` (no flag) and verify it resolves from the default source. Run `package_mgmt add hyprland --source trixie-backports` and verify the `binaries` table shows `release='trixie-backports'` for hyprland and any dependencies that came from backports.

**Acceptance Scenarios**:

1. **Given** a default config with `trixie/main` as the first source, **When** the admin runs `package_mgmt add vifm` without flags, **Then** vifm and all its transitive dependencies are resolved from `trixie/main`
2. **Given** a configured `trixie-backports/main` source, **When** the admin runs `package_mgmt add hyprland --source trixie-backports`, **Then** hyprland is resolved from `trixie-backports/main`, and its transitive dependencies first check `trixie-backports/main` before falling back to `trixie/main`
3. **Given** a dependency exists in multiple sources at different versions (e.g., `libdrm` 2.4.110 in trixie/main and 2.4.120 in trixie-backports/main), **When** resolving hyprland's transitive dependency on `libdrm`, **Then** the resolver picks the backports version if it satisfies the constraint, falling back to the base version otherwise
4. **Given** the same version already exists as `status='current'` (re-resolution from the same source), **When** the resolver processes it, **Then** no new row is inserted and no status change occurs

### User Story 5 - Append-Only Resolution History (Priority: P2)

An administrator adds packages over time. Each resolution creates new `binaries` rows. When a package is resolved at a different version than a previously-current entry (e.g., `libdrm` goes from 2.4.110 to 2.4.120 because a backports dependency required the newer version), the old row is marked `status='superseded'` and the new row is inserted as `status='current'`. At any point, exactly one row per `(package_name, architecture)` is `'current'`, representing the coherent resolved tree. The full history remains queryable.

**Why this priority**: This is the mechanism that makes the operator workflow resilient. If a rebuild against the current library fails, the superseded version is still in the pool and the database still references it.

**Independent Test**: After the User Story 4 test (hyprland from backports upgraded libdrm), query the `binaries` table for `libdrm2`. Two rows exist: one with version 2.4.110 and status `'superseded'` (from the earlier vifm resolution), one with version 2.4.120 and status `'current'` (from the hyprland resolution). The old row is not deleted.

**Acceptance Scenarios**:

1. **Given** a `status='current'` row for `libdrm2` at version 2.4.110 from `trixie/main`, **When** a new resolution selects `libdrm2` 2.4.120 from `trixie-backports`, **Then** the old row is updated to `status='superseded'` and a new row is inserted with `status='current'`, retaining the old row's `release='trixie'` and version
2. **Given** a superseded row in `binaries`, **When** the operator queries by `WHERE status='current'`, **Then** the superseded row is excluded from results
3. **Given** a superseded row, **When** the operator queries by `WHERE package_name = 'libdrm2' ORDER BY id`, **Then** both rows are returned with their respective versions, releases, and status values intact

---

### Edge Cases

- What happens when the same `.deb` file (same package, version, arch, release, component) is resolved twice? → Existing row is reused; no duplicate.
- What happens when two different configured sources point to the same mirror but different releases? → They are distinct entries; no conflict.
- What happens when a configured source's `Packages.gz` is unreachable during init? → Per-source error, continue with other sources, partial cache is usable.
- How does the system handle a superseded package whose `.deb` is later removed from the pool? → The binaries row stays as `'superseded'` for history; the `verified` flag indicates whether the `.deb` is present.
- Can the operator manually change a `status` from `'superseded'` to `'current'`? → Out of scope for this spec; future operator workflow.
- What happens when a release is updated in the Debian archive (e.g., new Packages.gz)? → Out of scope for this spec; index refresh is a future feature.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The binary cache `dists/` directory MUST mirror the Debian apt repository layout: `dists/{release}/{component}/binary-{arch}/Packages.gz`. The source cache `dists/` MUST mirror the same layout: `dists/{release}/{component}/source/Sources.gz`.
- **FR-002**: The pool directory MUST follow the existing project convention: `pool/<component>/<letter>/<package>_<version>_<arch>.deb` for binaries, where `<letter>` follows Debian's pool naming convention (first letter of the package name, or `lib` + first letter for library packages). The exact path for downloaded files is determined by the `Filename` field from each `Packages.gz` stanza. The pool is shared across all releases — the same component subdirectory serves all suites. Multiple versions of the same package coexist naturally by distinct filenames.
- **FR-003**: The configuration MUST support multiple archive sources. Each source MUST define a `mirror` (URL), a `release` (suite name), and a `components` list.
- **FR-004**: Duplicate `(release, component)` pairs across configured sources MUST be rejected at config load time with a clear error message.
- **FR-005**: The `binaries` table MUST include `release TEXT NOT NULL`, `component TEXT NOT NULL`, and `status TEXT NOT NULL DEFAULT 'current'` columns. The UNIQUE constraint MUST be `(package_name, version, architecture, release, component)`.
- **FR-006**: Valid `status` values are `'current'` and `'superseded'`. At most one row per `(package_name, architecture)` may have `status = 'current'` at any time.
- **FR-007**: The `sources` table MUST include `release TEXT NOT NULL` and `component TEXT NOT NULL` columns. The UNIQUE constraint MUST be `(package_name, source_version, release, component)`.
- **FR-008**: The `add` command MUST resolve packages from the first configured source by default, unless the `--source <release>` flag is specified.
- **FR-009**: When `--source <release>` is provided to `add`, the resolver MUST use that release as the primary resolution anchor. Transitive dependencies are resolved from the specified release first, falling back to other configured sources if a package is not found in the primary source.
- **FR-010**: When the resolver selects a version of a package that differs from an existing `status='current'` row for the same `(package_name, architecture)`, it MUST update the old row to `status='superseded'` and insert a new row with `status='current'`. If the version is identical, no new row is created.
- **FR-011**: The database schema version MUST remain at 1 during development. Schema changes (new columns, new tables) are additive modifications to the existing DDL. No formal migration logic is required — development databases can be recreated.
- **FR-012**: The `init` command MUST iterate over all configured sources, load each `Packages.gz` and `Sources.gz`, tag each package with its `(release, component)` origin, and populate the `sources` table accordingly. The `binaries` table is populated during resolution (`add`), not during init.

### Key Entities *(include if feature involves data)*

- **ArchiveSource (config entity)**: A single repository source defined by `mirror` (URL), `release` (suite name like `trixie` or `trixie-backports`), and `components` (list of component names like `["main", "contrib"]`). The project config holds a list of these.
- **Binaries (DB table, extended)**: Gains `release`, `component`, and `status` columns. Identity is `(package_name, version, architecture, release, component)`. The `status` column (`'current'` / `'superseded'`) tracks resolution currency and enables transparent version history.
- **Sources (DB table, extended)**: Gains `release` and `component` columns. Identity is `(package_name, source_version, release, component)`.
- **Status Flag**: The `status` column on `binaries` indicating whether a row is the current resolution choice (`'current'`) or has been superseded by a later resolution (`'superseded'`). Rows are never deleted; history is append-only.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The cache directory tree passes a structural audit against the Debian apt repository specification — `dists/{release}/{component}/binary-amd64/Packages.gz` exists for each configured source, and `pool/` follows Debian naming conventions.
- **SC-002**: An operator can add the binary cache as an apt source (`deb [trusted=yes] file:/cache trixie main`) and run `apt update` successfully, confirming the cache is a valid Debian repository layout.
- **SC-003**: After init with two configured sources (e.g., `trixie/main` and `trixie-backports/main`), the `binaries` table contains rows with at least two distinct `release` values — verifiable by query.
- **SC-004**: After a version-upgrading resolution (adding a backports package that upgrades a shared library), the `binaries` table contains two rows for the shared library: one with `status='superseded'` (old version, base release) and one with `status='current'` (new version, backports release). The old row's data remains intact.
## Assumptions

- **No Schema Version Bump**: During development, `SCHEMA_VERSION` remains 1. Schema changes (new columns, new tables) are made in-place. Developers recreate their databases as needed. A formal migration mechanism will be introduced at release time if needed.
- **Debian Layout Compatibility**: The cache `dists/` and `pool/` directories follow the same structure as a real Debian apt repository. The `pool/` tree includes the component prefix: `pool/{component}/{prefix}/{package}_{version}_{arch}.deb`. The `pool/` is shared across all releases — `pool/main/` is the same directory whether referenced from `trixie/main` or `trixie-backports/main`. This means the cache can be used as a drop-in apt source without transformation.
- **Suite Identity**: A suite is identified by its release name (e.g., `trixie`, `trixie-backports`, `trixie-security`). Different suites are independent trees under `dists/`. The same package name+version can appear in multiple suites.
- **Component Identity**: Components (`main`, `contrib`, `non-free`, `non-free-firmware`) are areas within a suite. They share the same release name but have different `dists/` subdirectories and different `pool/{component}/` subdirectories.
- **Source-Targeted Resolution**: The `add` command resolves from the first configured source by default. The `--source <release>` flag overrides the primary resolution anchor. The specified source is searched first for every package in the dependency tree; if a package is not found there, the resolver falls back to other configured sources in order. This matches the mental model of "install from this suite, but use the base suite for anything it doesn't have."
- **Append-Only Semantics**: Binaries rows are never deleted. The `status` column tracks which row is the current resolution choice. This preserves full history for operator debugging and fallback.
- **Index Refresh**: Out of scope. The cache is built once (snapshot in time) and used offline. Re-fetching indexes or incremental updates are future features.
- **Scope Boundary**: Multi-architecture support is out of scope (single `amd64`). GPG/release file verification is out of scope. Operator tooling for status manipulation (`set superseded → current`) is out of scope.

## Clarifications

### Session

- Q: Is the `release` concept separate from `component`? → A: Yes. Release is the suite (e.g., `trixie`, `trixie-backports`) and component is the area (`main`, `contrib`, etc.). They map to `dists/{release}/{component}/` in the cache layout. Backports is a different release, not a component.
- Q: At most one `status='current'` per `(package_name, architecture)` — what about different architectures? → A: Architecture is part of the constraint. A package at different architectures can each have their own current status. However, multi-arch support is explicitly out of scope for now (single amd64 assumed).
