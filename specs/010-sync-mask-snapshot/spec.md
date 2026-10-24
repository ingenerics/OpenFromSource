# Feature Specification: Sync Mask & Snapshot

**Feature Branch**: `010-sync-mask-snapshot`
**Status**: Draft

**Input**: User description: "spec sync mask snapshot - Let's add the required plumbing to be able to snapshot the admin's cache, so we can easily sync an operator's system between two points in time. We did a thorough analysis, everything is still in context. Ensure nothing is missed, think hard."

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Builds and grows the cache; records which cache state has been delivered to which operator system; prepares deltas between two points in time |
| **Operator** | Offline system (no network) | Unchanged — consumes the finished cache; never runs `systems` or `snapshot` |

**Phase 1 — Sync State Plumbing**: The database records which binaries (files) have been delivered to which sync target ("system"). A registry of systems (each with a unique human moniker, e.g. `bravo-main`, `lab-box`) is added and managed via the CLI; every binary in the cache carries an internal delivery record for all systems at once. The internal encoding is opaque — the administrator never sees or manipulates it. Run by the Administrator.

**Phase 2 — Snapshot Operation**: The `snapshot` command marks exactly the set of binaries that are *current and verified* at the moment of the call as delivered for a chosen system — a pure assertion ("this state has been handed over"), with a read-only dry-run preview first. Combined with cache growth between two calls, the pending set between two snapshots is exactly the delta the administrator needs to move to that system. A correctness fix to version superseding ships in this phase because snapshot marking depends on exactly one current binary per package. Run by the Administrator.

---

### User Story 1 - Know Your Targets: A Registry of Sync Systems (Priority: P1)

The administrator keeps a list of the systems they deliver caches to — an operator may have several (main machine, lab box, spare). Each system gets a moniker, any word the administrator finds meaningful. The internal per-system bookkeeping (a number assigned at creation) is invisible; the moniker is the only handle ever shown or used. The administrator can add, list, rename, and remove systems; listing shows the moniker, optional note, and how many current binaries have been delivered to it.

**Why this priority**: The registry is the foundation — the snapshot operation cannot exist without a named sync target, and the opaque assignment keeps the "hash" private by design (never exposed, never reused).

**Independent Test**: Manage a registry without touching the database directly: `systems add` several names, `systems list` them, rename one, remove one, re-add the removed name — each command's effect is verifiable through `systems list` alone; the internal numbers never appear in any output.

**Acceptance Scenarios**:

1. **Given** an empty registry, **When** the administrator runs `systems add <moniker>` with a note, **Then** the system appears in `systems list` with the given moniker and note, and its internal identifier is never shown in any output.
2. **Given** an existing system, **When** the administrator adds a second system with the same moniker, **Then** a clear error is shown, the command exits non-zero, and the registry is unchanged.
3. **Given** a system that has received a snapshot, **When** the administrator renames it, **Then** the rename succeeds and the system keeps all previously recorded deliveries.
4. **Given** a system that has received a snapshot, **When** the administrator removes it and later re-adds the same moniker, **Then** the re-created system is treated as having received nothing — no previously recorded deliveries are inherited.
5. **Given** a registry with systems, **When** the administrator runs `systems list`, **Then** each row shows the moniker, note, and the count of current binaries delivered to that system.

---

### User Story 2 - Snapshot: This State Has Been Delivered (Priority: P1)

After handing a cache state to a system (e.g. copying the cache onto USB media), the administrator tells the tool "this state is now with `bravo-main`" — a snapshot. Every binary that is *current and verified* at that moment is recorded as delivered for that system; a binary whose file has not been downloaded and verified is never marked, because it could not have been delivered. The command is a pure assertion: it moves, copies, or verifies nothing — the actual delivery is the administrator's own flow, and the marking happens *after* a successful delivery, never before. A dry-run preview reports how many binaries would be marked without changing anything. Running a snapshot again marks nothing new; binaries that become current and verified after a snapshot are pending until the next one — so the pending set between two snapshots is exactly the delta for the next handover.

**Why this priority**: This is the essence of the feature — it turns "what the cache looked like at time T" into a queryable state, making every future sync flow (delta export, bootstrap, staged syncs) a composition of *copy the pending set, then snapshot*.

**Independent Test**: Set up a fresh cache, take a snapshot for a system (dry-run first, then real), resolve more packages, take a second snapshot — the second run's dry-run reports exactly the binaries that became current after the first snapshot; re-running it reports zero.

**Acceptance Scenarios**:

1. **Given** a cache with N current, verified binaries, **When** the administrator runs `snapshot --system <moniker> --dry-run`, **Then** it reports N binaries pending without modifying the database.
2. **Given** the same cache, **When** the administrator runs `snapshot --system <moniker>`, **Then** exactly the N current, verified binaries are recorded as delivered, superseded or unverified binaries are not, and the summary reports N marked.
3. **Given** a freshly snapshotted cache, **When** the administrator runs `snapshot --system <moniker>` again, **Then** it reports 0 pending and marks nothing (idempotent).
4. **Given** new binaries that are current and verified after a snapshot (registered via `add`, `ensure-base`, or resolution, then downloaded and verified), **When** a second snapshot is taken for the same system, **Then** exactly those binaries are marked and reported — the delta between the two points in time.
5. **Given** a system moniker that does not exist, **When** a snapshot is attempted for it, **Then** a clear error is shown, the command exits non-zero, and no binary is marked.

---

### User Story 3 - Version History Stays Consistent (Priority: P2)

A previously-registered version that has been superseded can currently be re-requested in a way that leaves the package with *no* current binary at all. Since a snapshot records exactly the *current, verified* binaries, a package in that broken state would silently never be marked — it would be missing from every delta. This story fixes the superseding behavior so re-requesting a superseded version leaves exactly one current binary, the same way every other resolution path does.

**Why this priority**: Snapshot correctness depends on "current" being a well-defined, always-exactly-one state. The fix is small but must land with the mask, because the two share the same code path.

**Independent Test**: `add pkg=1.0`, then `add pkg=2.0`, then `add pkg=1.0` again — afterwards, querying current binaries for `pkg` returns exactly one, the re-requested version.

**Acceptance Scenarios**:

1. **Given** a package whose current binary is v2.0 and whose v1.0 is registered as superseded, **When** the administrator re-adds `pkg=1.0`, **Then** exactly one current binary remains (v1.0), the previous one becomes superseded, and the resolver state is consistent.
2. **Given** the re-request scenario above and the re-requested version is verified, **When** a snapshot is taken, **Then** it is included in the marked set like any other current, verified binary.

---

### Edge Cases

- Snapshot for a removed or never-created moniker → clear error, non-zero exit, no changes (US2 AS5).
- Registry identifier reuse → identifiers are assigned once and never reused; a removed system's identifier is retired forever, so a re-added moniker can never inherit another system's deliveries (US1 AS4).
- Re-adding the same moniker after removal → behaves as a brand-new, never-synced system — conservative, never inherits marks (US1 AS4).
- Snapshot on an empty cache (no binaries at all) → marks 0, exits successfully.
- Snapshot with only superseded binaries and no current ones → marks 0; superseded binaries are never marked.
- Current-but-unverified binaries (metadata-only registrations whose files were never downloaded and verified) → never marked; they are a pending-*download* concern, not a pending-delivery one.
- Renaming a system to a moniker that already exists → clear error, non-zero exit, registry unchanged.
- A stale development database (created before this feature) missing the new schema objects → a clear message instructing the rebuild path, not an obscure query-time failure.
- Many systems (approaching the internal identifier ceiling of 62) → the registry rejects new systems with a clear error before exhaustion; nothing else changes.
- Snapshot while another command is resolving → a snapshot marks exactly the committed current state at its moment of execution; resolution commits per name, so the marked set is always a consistent committed state.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The tool MUST provide a registry of sync systems; each system MUST have a unique human-readable moniker (any word), and an internal per-system identifier that is assigned automatically, never shown to the user, and never chosen by the user.
- **FR-002**: System identifiers MUST be assigned once and never reused — a removed system's identifier MUST NOT be issued to any later system, so no later system can ever inherit another system's deliveries.
- **FR-003**: The registry MUST support adding, listing, renaming, and removing systems; listing MUST show the moniker, the optional note, and the number of current binaries delivered to that system; renaming MUST preserve all recorded deliveries.
- **FR-004**: Adding a moniker that already exists MUST fail with a clear error, a non-zero exit, and no changes.
- **FR-005**: Removing a system MUST retire it from the registry; its recorded deliveries MUST remain untouched in the data and MUST never be reported or queried again.
- **FR-006**: The tool MUST record for every binary whether it has been delivered to each system; the record MUST default to "not delivered" for every newly registered binary and MUST be changed by exactly one operation — the snapshot (FR-007).
- **FR-007**: The `snapshot` command MUST mark exactly the set of binaries that are both `current` and verified at the moment of execution as delivered for the named system, and MUST be a pure state assertion — it MUST NOT copy, move, or verify any files.
- **FR-008**: The `snapshot` command MUST support a read-only dry-run mode that reports how many current, verified binaries would be marked, without modifying the database.
- **FR-009**: The `snapshot` command MUST be idempotent: re-running it for the same system marks no additional binaries and reports 0 pending.
- **FR-010**: A snapshot for a moniker that does not exist in the registry MUST fail with a clear error, a non-zero exit, and no modifications.
- **FR-011**: Re-requesting a version whose binary was previously superseded MUST leave exactly one current binary for that package (the re-requested version); the superseded version's history MUST be preserved.
- **FR-012**: A database that is missing the feature's schema objects (a stale development database) MUST fail with a clear message describing the documented rebuild procedure, instead of failing obscurely at query time.
- **FR-013**: The existing schema versioning convention MUST remain in effect (no version bump for this feature); schema changes land in place, and the delete-and-rebuild procedure for stale databases MUST be documented as the sanctioned migration path during development.

### Key Entities *(include if feature involves data)*

- **Systems (new DB table)**: The registry of sync targets. One row per system: moniker (unique), opaque identifier (unique, never reused), optional note. An operator may own several systems; the table is persona-agnostic.
- **Binaries (existing DB table, extended)**: Gains a delivery record covering every system at once. Written by exactly one operation (snapshot); defaults to "not delivered" on registration; never auto-reset.
- **SyncState (derived, no new table)**: The set of "current, verified binaries not yet delivered to system X" — the pending set between two points in time. Not stored; derived by a single query, which is the contract every future sync flow (delta export, bootstrap, staged syncs) composes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The two-points-in-time flow works end to end: snapshot at T1, register new packages, snapshot at T2 — the T2 run reports exactly the binaries that became current and verified between T1 and T2, and a re-run reports 0.
- **SC-002**: A snapshot on a cache with N current, verified binaries marks exactly N — 100% of current-and-verified, 0% of superseded or unverified — in a single atomic step.
- **SC-003**: The full registry lifecycle (add several, list, rename, remove, re-add the same moniker) completes with no identifier collisions and no mark inheritance; a re-added moniker always appears as never-synced.
- **SC-004**: The superseded-version re-request scenario leaves exactly one current binary, verifiable through existing listing commands.
- **SC-005**: A stale development database is diagnosed with a clear rebuild message on the first command run, with no obscure failures.
- **SC-006**: No user-visible output ever exposes the internal system identifiers; the moniker is the only handle.

## Assumptions

- **Persona**: The registry and snapshot are administrator-only concerns, like every other cache-building command; the operator's flow is unchanged (roles.md).
- **Moniker**: Any word the administrator chooses, unique within the registry; notes are optional free text.
- **Opaque identifiers**: Assignment is automatic (monotonic, never reused); the ceiling is 62 systems; reaching it is a clear error, and any migration beyond it is a future decision — nothing in the diff contract changes shape.
- **Mark-after-copy**: The snapshot is a pure assertion and the administrator marks only after a successful delivery; the future sync/export command will enforce copy → verify → snapshot ordering. Snapshot-before-copy is possible via the CLI but documented as unsafe (a failed delivery would lose its delta).
- **No history table, no timestamps**: The delivery record itself is the source of truth; the snapshot is an operation, not a stored object. Delivery manifests (the list of files actually shipped) are a future export-command artifact written into the destination folder — not database state.
- **Metadata only**: This feature touches no files and never writes the `verified` flag. It reads `verified` as the delivery gate: only binaries that are `current` and verified qualify for snapshot marking. For this feature, "verified" means the file is present locally and its integrity has been confirmed — setting the flag is the future download/verification work's job (spec 011+), so today the flag is only ever 0; validation simulates downloaded files by setting it directly.
- **Schema versioning**: During development, schema changes land in place at version 1 and stale databases are rebuilt (delete the database and re-run `ensure-base` + `add` manifests — the whole cache is derivable from indexes); this policy is documented and revisitable before implementation (FR-012, FR-013).
- **Supersede fix scope**: FR-011 changes only the re-request-of-superseded-version path; all other superseding semantics are unchanged.
- **Pending listing**: A full "pending list" command is deliberately out of scope — the dry-run count (FR-008) is the preview, and a detailed listing ships with the future sync/export command that actually consumes the pending set.
- **Existing behavior**: All existing commands' output and semantics are unchanged by this feature.

## Clarifications

### Session

Resolved during design brainstorming; no open items.

- Registry naming: the sync target is a **system** (an operator may have several), so the table and commands use `systems`.
- Granularity: the delivery record lives on **binaries** (the file unit actually shipped), not on packages — new binaries naturally start undelivered and superseded history is preserved; no reset hook is needed anywhere.
- Snapshot history: no snapshots table — the mask is the record, `created_at` would be decorative and the set would not be reconstructible from it anyway; delivery manifests are a future export-command artifact.
- Schema version: no bump; delete-and-rebuild is the documented development-time migration path.
- Verified gate: only current **and verified** binaries qualify for snapshot marking (resolved post-planning review) — an unverified binary's file does not exist locally, so it cannot have been delivered; the feature reads `verified` as a gate and never writes it.
