# Feature Specification: Base Project Infrastructure

**Feature Branch**: `001-base-project-infrastructure`
**Status**: Draft

**Input**: User description: "base project infrastructure - Create one or more Specs to build the base infrastructure that will allow us to build the project."

## User Scenarios & Testing *(mandatory)*

<!--
  This project has two distinct personas — an Administrator who builds a package
  cache on a networked system, and an Operator who consumes that cache on an
  offline Debian LTS system. Reference .specify/memory/roles.md for details.
-->

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Fetches packages + source into cache from Debian repos |
| **Operator** | Offline system (no network) | Installs packages from the pre-built cache |

**Phase 1 — Core Infrastructure**: Project configuration, cache directory setup, database schema initialization (both roles)
**Phase 2 — Feature Workflows**: Admin fetch and operator install commands layered on the infrastructure

---
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.

  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - Project Configuration and Constants (Priority: P1)

A developer or admin initialises a new workstation to work with the project.
They clone the repository and find a single project configuration file that
defines all variable paths and defaults. They inspect the defaults, optionally
override settings (e.g. a custom cache location for testing), and confirm
the system reads the expected values without error.

**Why this priority**: Every subsequent command and workflow depends on knowing
where the cache lives, where the database resides, and which paths to use.
Without configuration, nothing else can function.

**Independent Test**: A fresh clone of the repository has a project config file
with documented defaults. Running a config-inspection command prints the
effective values and clearly indicates which came from defaults and which from
overrides. No other infrastructure needs to exist.

**Acceptance Scenarios**:

1. **Given** a fresh clone of the project, **When** the user runs `package_mgmt
   config`, **Then** it prints the full set of default variables (base
   directory, binary cache path, source cache path, database path, log level)
   without errors.
2. **Given** a local override file (`project.local.toml`) setting a custom
   base directory, **When** the user runs `package_mgmt config`, **Then**
   the output shows the custom value and marks it as overridden.
3. **Given** an invalid or missing value in the override, **When** the user
   runs any command, **Then** the system reports a clear validation error
   naming the problematic setting and exits with a non-zero code.

---

### User Story 2 - Cache Directory Initialisation (Priority: P2)

An administrator on a networked machine runs `package_mgmt init` to prepare
the local cache directories at the configured base path. The system creates
two separate cache trees (binary and source), each following the Debian apt
repository layout (`pool/` + `dists/`), sets appropriate permissions, and
reports success. The operator repeats the same flow on the offline machine.

**Why this priority**: The cache directories are the physical stores for all
downloaded and installed packages. Both roles must have working caches
before any package operation can proceed.

**Independent Test**: Run `package_mgmt init` on a system where the cache
directories do not yet exist. Both `binary/` and `source/` trees are created
under the base path with correct Debian apt layout. A second run confirms
they already exist and skips creation gracefully.

**Acceptance Scenarios**:

1. **Given** a system without the cache directories, **When** `package_mgmt
   init` is run, **Then** both `binary/` and `source/` directory trees are
   created with `pool/` and `dists/` subdirectories and permissions that
   allow both admin and operator access.
2. **Given** already existing cache directories, **When** `package_mgmt init`
   is run again, **Then** it detects the existing state, verifies the Debian
   apt layout is intact, and exits successfully without errors.
3. **Given** a parent path that does not exist and cannot be created (e.g.
   missing /var), **When** `package_mgmt init` is run, **Then** it reports a
   meaningful error and suggests corrective action.

---

### User Story 3 - Database Schema Initialisation (Priority: P3)

A role (admin or operator) runs the init command, which also creates the
SQLite database at the configured path. The database is populated with the
schema defined in the project constitution: tables for packages, binaries,
sources, and the binary_to_source link table. The verification confirms the
schema matches the expected definition.

**Why this priority**: The database is the central metadata store for all
package operations. Its schema must exist before any add, remove, verify,
or bootstrap command can operate. However, the config and cache directory
must be in place first.

**Independent Test**: On a system with a valid config and cache directory,
run the database initialisation step. Verify the database file exists at
the expected path and contains all required tables with the correct column
definitions. Running it again detects the existing database and skips
re-creation.

**Acceptance Scenarios**:

1. **Given** a configured project with an empty cache directory, **When**
   the init command creates the database, **Then** the file exists at
    `{base_dir}/package_mgmt.db` and contains the packages, binaries,
   sources, and binary_to_source tables with a `verified` column on each.
2. **Given** an existing database file at the target path, **When** the
   init command is run again, **Then** it validates the existing schema
   matches the expected definition and reports success without data loss.
3. **Given** a database with an outdated or mismatched schema, **When**
   the init command runs, **Then** it reports a schema mismatch error and
   does not overwrite or destroy existing data.

---

[Add more user stories as needed, each with an assigned priority]

### Edge Cases

- What happens when the config file has syntax errors or is not parseable?
- What happens when the configured cache path is on a read-only filesystem?
- How does the system handle concurrent initialisation from two processes?
- What happens when the database file exists but is corrupted or from a different project?
- How does the system behave when run without sufficient privileges to create /var/cache/package_mgmt?
- What happens when the override file references paths that contain spaces or special characters?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a project configuration file with documented
  default values for cache directory, database path, log directory, and any
  other runtime variables.
- **FR-002**: Cache base directory MUST default to `/var/cache/package_mgmt`
  for both administrator and operator roles, containing separate `binary/`
  and `source/` subdirectories each following the Debian apt repository
  layout (`pool/` + `dists/`).
- **FR-003**: Configuration MUST be overridable through a mechanism that does
  not require editing the default file (e.g. a local override file or
  environment variables).
- **FR-004**: System MUST validate configuration on startup and report a
  clear error for any missing required setting or invalid value.
- **FR-005**: Both administrator and operator MUST have read and write access
  to the cache directory on their respective machines.
- **FR-006**: System MUST initialise the cache directory tree on demand,
  creating parent directories if they do not exist.
- **FR-007**: Repeated initialisation MUST be idempotent — running it on an
  already-initialised system detects the existing state and exits
  successfully without data loss.
- **FR-008**: System MUST create the SQLite database schema (packages,
  binaries, sources, binary_to_source) on first initialisation.
- **FR-009**: Every database table MUST include a `verified` column with
  a default value of `false`.
- **FR-010**: System MUST detect a pre-existing database and validate its
  schema against the expected definition before using it.

- **FR-011**: Configuration file MUST use the TOML format (Python standard
  library `tomllib` since 3.11).
- **FR-012**: Configuration overrides MUST use a local override file
  (`project.local.toml`) that extends or overrides specific keys from the
  default file; no environment variable override mechanism is required.

### Key Entities *(include if feature involves data)*

- **ProjectConfig**: The runtime configuration object that aggregates
  default values, override values, and resolved effective values for all
  project paths and settings.
- **BinaryCache**: Physical directory at `{base_dir}/binary` (default
  `/var/cache/package_mgmt/binary`) with Debian apt layout; stores `.deb`
  files and binary `Packages.gz` indices.
- **SourceCache**: Physical directory at `{base_dir}/source` (default
  `/var/cache/package_mgmt/source`) with Debian apt layout; stores `.dsc`,
  `.orig.tar.*`, `.debian.tar.*` files and `Sources.gz` indices.
- **PackageDatabase (file)**: The SQLite database file containing the
  packages, binaries, sources, and binary_to_source tables as defined by
  Constitution Principle VII.
- **SchemaDefinition**: The expected table and column definitions against
  which an existing database is validated on startup.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A new system can initialise the full project infrastructure
  (config, cache directory, database) with a single command and no manual
  intervention.
- **SC-002**: Configuration inspection prints the effective runtime values
  and clearly distinguishes defaults from overrides.
- **SC-003**: Both roles can create and write to the cache directory on
  their respective systems without permission errors.
- **SC-004**: Running init twice on the same system reports success both
  times with no data loss or error.
- **SC-005**: Database schema validation completes in under 500ms on a
  pre-existing database file.

## Assumptions

- **Target Base**: Debian Trixie LTS amd64; Python 3.x runtime.
- **Cache Path**: Default `/var/cache/package_mgmt` requires `sudo` or
  equivalent privilege for creation; both roles have such access. Two
  subdirectories are created: `binary/` and `source/`, each with a Debian
  apt repository layout (`pool/` + `dists/`).
- **Config Format**: TOML, parsed via Python stdlib `tomllib`.
- **Override Mechanism**: Local override file `project.local.toml` in the
  project root; keys in the override shadow those in the default file.
- **Database**: SQLite via Python stdlib (`sqlite3`); no external database
  server required.
- **Single-User at a Time**: The tool is not designed for concurrent
  multi-process access to the database (SQLite WAL mode may be considered
  later but is not required for v1).
- **Scope Boundary**: Permission management (ACLs, group creation) is
  manual or handled by the system administrator outside this tool; the
  tool only creates directories and sets basic POSIX permissions.

## Clarifications

### Session

- Q: Configuration format? → A: TOML (Python stdlib tomllib).
- Q: Override mechanism? → A: Local override file only (`project.local.toml`).
