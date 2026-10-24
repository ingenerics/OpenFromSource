# Feature Specification: Bootstrap Operator System

**Feature Branch**: `012-bootstrap-operator-system`
**Status**: Draft

**Input**: User description: "spec bootstrap operator system - Starting from a hydrated cache ready for hand off, we should now be able to set up an offline system. After base install using the offline installer DVD ISO, the operator's system should be configured to use to the offline cache for package installs. This bootstrap process should be as friction free as possible, however, don't over-engineer either. If a simple bash script can do it better then at least propose to do it like that during planning phases."

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Builds and verifies the cache, ships it (with the Debian LTS ISO) to the operator |
| **Operator** | Offline system (no network) | Installs the base system from the offline DVD, then bootstraps it to install packages from the received cache |

**Phase 1 — Configure**: The operator runs the bootstrap once on a freshly installed offline system; the system's package installer is configured to consume the cache, with no manual configuration editing. Run by the Operator.

**Phase 2 — Trust & Recovery**: The bootstrap refuses to configure an invalid cache, re-runs safely, and can be cleanly removed — failures never leave the system broken. Run by the Operator.

---

### User Story 1 - One Command, Offline-Ready (Priority: P1)

The administrator has shipped a verified cache and a Debian LTS ISO. The operator installs the base system from the ISO (with no network), connects the media containing the cache, and runs the bootstrap. After it finishes, the system's package tooling installs software from the cache — `apt update` succeeds with no network, and `apt install` of any package the cache contains works. The operator never edits package-source configuration by hand.

**Why this priority**: This is the essence of the feature — turning the handoff media into a working offline package system with the least possible friction. Nothing before it delivers value to the operator.

**Independent Test**: Install a base system from the offline DVD into a scratch machine/VM with no network, hand it the fixture-backed cache, run the bootstrap, then confirm `apt update` and `apt install` of a cached package succeed end to end with no network and no manual configuration edits.

**Acceptance Scenarios**:

1. **Given** a freshly DVD-installed offline system and a verified cache on removable media, **When** the operator runs the bootstrap, **Then** the system's package tooling is configured to use the cache and `apt update` succeeds without any network access.
2. **Given** the same system after a successful bootstrap, **When** the operator runs `apt install` for a package the cache contains, **Then** the package installs from the cache with no network access and no manual configuration steps.
3. **Given** a freshly DVD-installed offline system, **When** the operator runs the bootstrap, **Then** it completes in a single action with no interactive prompts beyond the system's standard permission prompt.

---

### User Story 2 - Exactly What the Cache Holds (Priority: P2)

After the bootstrap, the package tooling offers precisely the set of packages the cache can actually deliver — nothing is listed that cannot be installed, and nothing in the cache is unreachable. The offered set is exact by construction: the `handoff` generator emits indexes containing only records whose pool file exists (FR-003), and the bootstrap validates the handoff structure and release match before configuring anything. The configured source is accepted by the package tooling without the operator working around signature or trust warnings by hand. Deep per-file checksum verification of the pool is deferred — the bootstrap preflight is structural (handoff structure + release match) only, because the operator cannot run `package_mgmt verify` on a bare install (FR-007).

**Why this priority**: An offline system that lists phantom packages fails confusingly mid-install; an untrusted source undermines the project's integrity guarantees. Both must be solved for the configuration to be trustworthy.

**Independent Test**: Point the bootstrap at (a) a complete verified cache, (b) a cache whose handoff structure is incomplete (missing a per-release `Release.gpg`, a component `Packages.gz`, or the `pool` target), and (c) a cache from a different release than the installed system — confirm (a) lists exactly the verified content and installs cleanly, and (b)/(c) are refused with a clear error and no system changes. (Preflight is structural + release match; deep pool checksum verification is deferred — see the Edge Cases and FR-004.)

**Acceptance Scenarios**:

1. **Given** a complete verified cache, **When** the bootstrap configures the system, **Then** every package listed by the configured source installs successfully, and every package the cache verifiably contains is listed.
2. **Given** a cache whose handoff structure is incomplete (missing `Release.gpg`, a component `Packages.gz`, or the `pool` target) or whose release does not match the installed system, **When** the operator runs the bootstrap, **Then** it refuses with a clear error naming the problem, and the system's package configuration is unchanged. (The preflight is structural + release match; deep pool checksum verification is deferred.)
3. **Given** a cache whose release or architecture does not match the installed system, **When** the operator runs the bootstrap, **Then** it refuses with a clear error, and no configuration is applied.
4. **Given** a valid cache, **When** the bootstrap configures the system, **Then** the package tooling accepts the source without the operator manually handling signature or trust warnings.

---

### User Story 3 - Safe Re-runs and a Clean Exit (Priority: P2)

The bootstrap is safe to run again after a successful run (nothing breaks, nothing duplicates), failures or interruptions before the source entry is written leave the system exactly as it was (an `apt update` failure after the entry is written leaves the entry in place for inspection), and the operator can remove the configuration with one documented action to return to the pre-bootstrap state.

**Why this priority**: Real operators misplace media, lose power mid-run, and change their minds; the offline guarantee extends to not leaving a broken package configuration behind.

**Independent Test**: Run the bootstrap twice on the same system (second run reports success and changes nothing), interrupt a run mid-way (system package tooling still works), then remove the configuration with the documented single action and confirm the system returns to its prior behavior.

**Acceptance Scenarios**:

1. **Given** a successfully bootstrapped system, **When** the operator runs the bootstrap again, **Then** it reports success, makes no duplicate or conflicting configuration, and the system keeps working.
2. **Given** a bootstrap interrupted or failed before the source entry is written, **When** the system is inspected, **Then** the package tooling remains in its pre-bootstrap working state and the error is reported clearly; a failure after the entry was written (e.g. `apt update` failing) leaves the entry in place for inspection with the error reported.
3. **Given** a bootstrapped system, **When** the operator performs the documented removal action, **Then** the cache source is gone and the system's package tooling behaves as it did before the bootstrap.

---

### Edge Cases

- Removable media with the cache not connected, or the given cache path wrong/not readable → clear error naming the path; nothing configured.
- Cache with zero verified records → `handoff` still succeeds with a warning; the configured source simply offers no packages.
- Unknown or retired moniker passed to `handoff --system` → refused with the spec 010 error convention; nothing is generated and no snapshot is taken.
- Cache whose release/architecture differs from the installed system → refused with a clear error (FR-004).
- Cache with an incomplete handoff (missing per-release `Release.gpg`, a component `Packages.gz`, or the `pool` target) or a release/architecture mismatch → refused with a clear error (FR-004). The offered package set never includes files that cannot be delivered: the generator excludes records whose pool file is missing at generation time (FR-003). Deep pool checksum verification is deferred — the bootstrap does not re-check the pool per file.
- Base installer's own package sources (DVD) present on the system → left untouched; the cache source complements them.
- Bootstrap run as a non-root user → clear guidance and no partial configuration.
- Bootstrap run twice, or after the operator manually edited package sources → idempotent merge, no duplicates, no destructive edits.
- Interrupted bootstrap (power loss, media removed mid-run) → system package tooling still works; a re-run completes cleanly.
- A package in the cache whose dependencies are satisfied only by other cached packages → installs with no network (dependencies resolve from the cache or the base DVD source).
- Disk space insufficient while the bootstrap prepares the source → clear error, no partial configuration.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The operator MUST be able to configure the offline system to use the received cache as the package source with a single bootstrap action, with no manual editing of package-source configuration.
- **FR-002**: After the bootstrap, package installation MUST work with no network access: refreshing package metadata and installing a cached package MUST succeed using the cache alone (plus the base installer's own offline sources).
- **FR-003**: The set of packages offered by the configured source MUST be exactly the cache's verified content — every offered package MUST be installable from the cache, and every verified package in the cache MUST be offered.
- **FR-004**: The bootstrap MUST validate the handoff before applying any configuration: structure complete (per-release `Release.gpg` and per-component `Packages.gz` present, `pool` target exists) and the handoff release matching the installed system. An incomplete handoff, or one whose release/architecture does not match the installed system, MUST be refused with a clear error and MUST NOT change the system's package configuration. The offered set is exact by construction (the generator only includes records whose pool file exists, FR-003); deep per-file checksum verification of the pool is deferred (the operator cannot run `package_mgmt verify` on a bare install, FR-007).
- **FR-005**: Re-running the bootstrap over an already-configured system MUST be safe and idempotent: no duplicate or conflicting configuration, no disruption to working installs, and a successful report.
- **FR-006**: The bootstrap MUST apply configuration only after validation succeeds: a failure or interruption BEFORE the source entry is written MUST leave the system's package tooling in its pre-bootstrap working state. If `apt update` fails after the entry was written, the entry MUST be left in place for inspection, the failure MUST be reported clearly and completely, and a re-run after the cause is fixed MUST converge cleanly (FR-005).
- **FR-007**: The bootstrap MUST run using only the software that ships with the standard base installation plus the contents of the cache handoff — no network access and no additional software installation on the operator system.
- **FR-008**: Removing the cache source configuration MUST be achievable with one documented action, restoring the package tooling to its pre-bootstrap behavior.
- **FR-009**: The configured source's metadata MUST carry the project's standard integrity guarantees (signed repository metadata; per-package checksums consistent with the verified pool), and the package tooling MUST accept it without manual trust or signature workarounds by the operator.
- **FR-010**: After the bootstrap, the operator MUST be able to see that the active package source is the offline cache and which packages it offers, using ordinary package-tooling commands.
- **FR-011**: The `handoff` command MUST take a snapshot against the provided system moniker — marking every current verified binary as delivered to that system, using the spec 010 snapshot semantics — and MUST refuse an unknown or retired moniker with a clear error.

### Key Entities *(include if feature involves data)*

- **Apt source configuration (derived, on the operator system)**: Points the system's package tooling at the cache; created, merged idempotently, and removed by the bootstrap.
- **Delivery record (existing, spec 010)**: `systems` monikers + `binaries.sync_mask` bits; `handoff` writes the snapshot (marking the verified set delivered to the moniker) at generation time — the snapshot IS the delivery record.
- **Generated package indexes (derived, no new tables)**: Per-release/component package metadata reflecting exactly the verified pool — derived from the database records, the cached archive indexes, and the pool contents (schema decision from spec 010/011: the database stores state, the indexes store file metadata; the schema stays frozen).
- **The cache as shipped (unchanged by this feature)**: `dists/` indexes, `pool/` files, and the database, as produced by `init`/`add`/`ensure-base`/`download` and verified by the administrator pre-handoff.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a freshly DVD-installed offline system, the operator completes the bootstrap in one action; refreshing package metadata and installing a cached package succeed with zero network access and zero manual configuration edits.
- **SC-002**: 100% of packages listed by the configured source install successfully from the cache.
- **SC-003**: An invalid or mismatched cache is refused with a clear error and the system's package configuration is provably unchanged.
- **SC-004**: A second bootstrap run on a configured system reports success and makes no changes to the configuration.
- **SC-005**: The removal action returns the package tooling to its pre-bootstrap behavior with no leftover configuration.
- **SC-006**: No manual trust or signature workarounds are needed anywhere in the flow; integrity is enforced by the same checksum standard the download flow applies.

## Assumptions

- **Persona & timing**: The bootstrap runs on the operator's system after a base install from the offline installer DVD ISO, using the cache exactly as the administrator shipped it (verified pre-handoff).
- **Bootstrap delivery**: Everything the bootstrap needs ships inside the cache handoff; no separate media or downloads. The bootstrap is a self-contained bash script (`bootstrap.sh`) shipped in the handoff — chosen over a `package_mgmt` command because the tool cannot be assumed to exist on a bare base install (the feature request's "prefer a simple bash script" guidance).
- **Invocation**: The operator runs `bootstrap.sh`; the cache directory is an **optional positional argument** (a given path is used as-is, relative to the current working directory), defaulting to the cache the script ships in (the handoff's parent directory). The script never parses project settings — the settings merge (defaults + local overrides) governs only where the cache is placed (admin side).
- **Base coverage**: The base system's packages come from the installer DVD's own source; the cache source complements (never replaces or removes) existing sources on the operator system.
- **Read-only cache**: The bootstrap only reads the cache (database, indexes, pool); no schema changes (the spec 010/011 freeze holds) and no writes into the handoff contents.
- **Derived metadata**: The package indexes the configured source presents are generated from the cache's own state at bootstrap time, so the offered set always matches the verified pool — nothing phantom, nothing unreachable.
- **Integrity**: Repository metadata is signed per the Constitution's quality gate; key handling is fully automatic inside the bootstrap — the operator never manages trust manually (FR-009).
- **Scope boundary**: This feature delivers system configuration for offline package installs. The later "Rebuild" capability (reinstalling the entire cached package stack on a fresh system, roles.md operator command `bootstrap`) is a separate future feature per Constitution IX (Incremental Spec Scope).
- **Privileges**: The operator runs the bootstrap with standard administrator privileges on the target system (e.g. `sudo`); the single-action requirement includes the standard privilege prompt.

## Clarifications

### Session

- Q: How does the bootstrap locate the cache on the operator system (settings-resolved vs. self-locating vs. argument)? → A: `bootstrap.sh [<cache-dir>]` — the cache directory is an optional positional argument (a given path is used as-is), defaulting to the cache the script ships in (the handoff's parent directory); no settings parsing on the operator system — the settings merge (defaults + local) governs only where the cache is placed, admin side.
- Q: What happens when `apt update` fails after the source entry was written? → A: The entry is left in place for inspection (Option B); FR-006 amended — the pre-bootstrap-state guarantee applies to failures before the entry is written, while post-write `apt update` failures keep the entry and are reported clearly.
- Q: What should `handoff` do when the cache has zero verified records? → A: Succeed with a warning in the summary (an empty apt source is valid; the operator's script still works and configures a source offering zero packages).
- Q: When should the delivery snapshot (spec 010) be taken? → A: At `handoff` time: `handoff --system <moniker>` (moniker required) takes the snapshot — marking every current verified binary as delivered to that system — before generating the artifacts; the snapshot is the delivery record, so the admin never runs `snapshot` separately for a handoff.
