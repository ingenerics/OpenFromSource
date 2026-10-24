# Feature Specification: Sync New Packages

**Feature Branch**: `013-sync-new-packages`
**Status**: Draft

**Input**: User description: "spec sync new packages - This spec leans on the earlier snapshot plumbing and involves both personas:
- The admin should be able to hyrdate a partial cache for handoff, with only the new, verified packages since last snapshot.
- The operator should be able to pull in this minimal diff cache into the system cache and use the standard apt commands without breaking the system.
During which a new snapshot should be set, to enable the same process again in the future."

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Grows and verifies the cache, then produces a minimal diff cache of the packages that are new and verified since a chosen system's last snapshot, and sets a new snapshot during the handoff |
| **Operator** | Offline system (no network) | Pulls the minimal diff cache into the already-configured system cache and keeps using the standard apt commands, without breaking anything already installed |

**Phase 1 — Delta Export**: The administrator grows the cache as usual (`add`, `ensure-base`, `download`, `verify`), then produces, for a chosen sync system, a minimal diff cache that carries exactly the packages that are *current, verified, and not yet delivered to that system* — the delta since that system's last snapshot (spec 010 pending set). The diff cache is apt-consumable on its own and ships with an apply script. The operation sets a new snapshot at the end, marking the shipped set delivered. Run by the Administrator.

**Phase 2 — Pull the Diff In**: The operator connects the media containing the diff cache, runs the apply script against the existing bootstrapped system cache, and the new packages merge in — refreshing package metadata lists the new packages alongside the prior ones, and installing one works offline. Nothing already installed or previously offered is disturbed. Run by the Operator.

**Phase 3 — Trust & Convergence**: The diff is validated before anything changes, out-of-order or repeated diffs are detected and refused, and failures or interruptions leave the existing cache working and a re-run converges. The cycle repeats indefinitely: every diff-and-apply advances the snapshot, so the next diff is again only what's new. Run by the Operator (incoming-diff validation) on top of the Administrator's pre-ship verification.

---

### User Story 1 - Ship Only What's New (Priority: P1)

After handing a full cache to a system (spec 012 handoff set the baseline snapshot), the administrator keeps growing the cache. When new packages are current and verified, the administrator produces a minimal diff cache for that system. It contains only the new, verified packages since that system's last snapshot — never anything already delivered — plus the apt metadata needed for the operator's package tooling to see the prior packages *and* the new ones, and an apply script. At the end, the operation sets a new snapshot for the system, marking exactly what it shipped as delivered, so the next run ships only what's new again. A system with nothing new produces an empty diff and reports zero. An unknown moniker is refused before anything is generated.

**Why this priority**: This is the essence of the feature — turning the spec 010 pending set into a shippable, minimal, apt-consumable handoff that keeps the process repeatable. Without it the operator would have to re-receive a full cache every time.

**Independent Test**: Bootstrap a scratch cache to a system (first handoff + snapshot), `add`/`download` new small packages, run the delta export — the produced diff contains exactly the new packages' files and none of the already-delivered ones; running it again produces an empty diff (the snapshot already advanced).

**Acceptance Scenarios**:

1. **Given** a system with a prior snapshot and new current, verified binaries registered since, **When** the administrator runs the delta export for that system, **Then** the diff cache contains exactly those binaries' files — every pending binary is included and no already-delivered binary is.
2. **Given** a cache with no new current, verified binaries since the system's last snapshot, **When** the delta export runs, **Then** it reports zero new packages, produces an empty diff, and exits successfully.
3. **Given** a system moniker that does not exist, **When** the delta export runs for it, **Then** a clear error is shown, the command exits non-zero, and no diff is produced and no snapshot is taken.
4. **Given** a pending binary whose pool file is missing or cannot be confirmed, **When** the delta export runs, **Then** that binary is reported and excluded from the diff, and it is never marked delivered.
5. **Given** a successful delta export, **When** the same export runs again for the same system, **Then** it produces an empty diff — the snapshot taken during the first run already marked the set (idempotent).

---

### User Story 2 - Pull the Diff Into the System Cache (Priority: P1)

The operator's system is already bootstrapped to the cache (spec 012). The administrator ships a minimal diff cache on media. The operator connects it and runs the apply script with a single action — no manual configuration editing. The new packages merge into the existing system cache; refreshing package metadata with no network lists the new packages alongside everything already offered; installing any of them works from the cache. Packages installed previously keep working, and every package previously offered remains offered. A diff whose release or architecture does not match the existing cache is refused before anything changes.

**Why this priority**: This is the operator's half of the value — turning a shipped delta into usable, offline-installable packages on an already-working system, with standard apt commands and zero breakage.

**Independent Test**: Bootstrap a scratch system/cache, receive a diff from the admin side, run the apply script, then confirm `apt update` (offline) lists the new packages alongside the old, `apt install` of a new package succeeds with no network, and everything previously installed still works.

**Acceptance Scenarios**:

1. **Given** a bootstrapped offline system with an existing cache and a valid diff cache, **When** the operator runs the apply script, **Then** the new packages' files are merged into the system cache, refreshing package metadata (no network) lists them alongside the prior packages, and installing a new package succeeds from the cache with no network and no manual steps.
2. **Given** the system after a successful apply, **When** the operator uses the standard package commands (`apt list`, `apt upgrade`, `apt install`), **Then** every package previously offered remains offered and nothing already installed is broken.
3. **Given** a diff cache whose release or architecture does not match the installed system or existing cache, **When** the apply script runs, **Then** it refuses with a clear error and the existing system cache is unchanged.
4. **Given** the same diff cache applied a second time, **When** the apply script runs again, **Then** it reports success and makes no changes — no duplicates, no breakage.

---

### User Story 3 - Safe Re-runs, Ordering, and Clean Failures (Priority: P2)

The diff flow is safe under real-world conditions. The apply script validates the diff before changing anything: complete structure, release/architecture match, and payload integrity. A corrupt or incomplete diff is refused with the existing cache untouched. Applying a diff out of order — one whose base state does not match the cache's applied state, meaning an intermediate diff was missed — is detected and refused with clear guidance, so apt never sees a package whose file is missing. Interruptions and failures leave the cache working: no partial files, no inconsistent metadata, and a re-run converges cleanly.

**Why this priority**: Real operators miss diffs, lose media, and lose power mid-run; the offline guarantee must extend to a diff flow that never leaves the cache offering packages it cannot deliver or corrupting a working system.

**Independent Test**: Point the apply script at (a) a valid diff, (b) a diff with a missing pool file / tampered metadata, (c) a diff whose base state does not match the cache (an intermediate diff skipped), and (d) a re-run of an already-applied diff — (a) applies cleanly, (b)/(c) are refused with the cache provably unchanged, (d) reports success with no changes; interrupt an apply and re-run to confirm convergence.

**Acceptance Scenarios**:

1. **Given** a diff cache whose structure is incomplete or whose metadata checksums do not match (tampered/missing file), **When** the apply script runs, **Then** it refuses with a clear error naming the problem and the existing system cache is unchanged.
2. **Given** a diff whose base state does not match the cache's applied state (an intermediate diff was missed), **When** the apply script runs, **Then** it refuses with a clear error telling the operator an earlier diff must be applied first, and the existing cache is unchanged.
3. **Given** an apply interrupted or failing midway (power loss, media removed, disk full), **When** the apply script runs again, **Then** it converges cleanly — no partial files, no inconsistent metadata, and the system keeps working.
4. **Given** a diff with nothing new to deliver (already fully applied), **When** the apply script runs, **Then** it reports success and changes nothing.

---

### Edge Cases

- No new packages since the system's last snapshot → empty diff, zero reported, success (US1 AS2).
- Unknown or retired system moniker → refused with the spec 010 error convention; no diff, no snapshot (US1 AS3).
- Pending binary whose pool file is missing or whose index entry cannot be confirmed → reported, excluded from the diff, never marked delivered (US1 AS4).
- Diff produced, then the admin runs the export again before shipping → the second run is empty (the snapshot advanced at the first run); the admin ships the first (non-empty) diff.
- A package already delivered at an older version that is then superseded by a new current version → the new version is in the pending set and is shipped; the served metadata presents only the current version.
- A new package whose dependencies are already in the operator's cache → resolves from the existing cache, no network.
- A new package whose dependencies are also new → both are in the pending set and both ship in the diff.
- Diff with zero new packages (all pending already delivered or none exist) → the apply reports nothing to do and changes nothing (US3 AS4).
- Diff whose release or architecture differs from the installed system / existing cache → refused, no changes (US2 AS3).
- Diff applied out of order (base-state mismatch, an intermediate diff missed) → refused with guidance; the cache is unchanged, so apt never sees a package whose file is missing (US3 AS2).
- Corrupt or tampered diff (bad checksum, missing file, malformed metadata) → refused, existing cache unchanged (US3 AS1).
- Interrupted or failed apply (power loss, media removed, disk full) → cache keeps working; re-run converges (US3 AS3).
- Operator applies a diff without ever having bootstrapped (no existing cache/configuration) → clear error instructing the spec 012 bootstrap first; nothing changed.
- Existing pool already contains a file the diff also carries (e.g. a file reached by more than one binary) → copied/verified once, no duplicate.
- Standard output is not a terminal (piped, logged) → progress degrades to periodic lines, keeping logs readable (consistent with spec 011).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The administrator MUST be able to produce, for a chosen sync system, a minimal diff cache whose content is exactly the packages that are current, verified, and not yet delivered to that system — the delta since that system's last snapshot (spec 010 pending set).
- **FR-002**: The diff cache MUST be apt-consumable: it MUST carry the new packages' files and the apt metadata needed for the operator's package tooling to present the prior packages and the new ones together, plus an apply script for the operator.
- **FR-003**: The diff payload MUST be minimal — it MUST carry only new package files and the (small) metadata needed to present them; content already delivered to the system MUST NOT be re-shipped (Constitution IV).
- **FR-004**: A delta export for a moniker that does not exist in the registry MUST fail with a clear error, a non-zero exit, and no generated diff and no snapshot.
- **FR-005**: The delta export MUST set a new snapshot against the system as part of the operation, marking exactly the binaries it actually ships as delivered, so the next delta for that system is again only what's new. A binary excluded from the diff (missing or unconfirmable pool file) MUST NOT be marked delivered.
- **FR-006**: The delta export MUST be idempotent: a second run for the same system after a successful first run MUST produce an empty diff and change nothing (the snapshot already advanced).
- **FR-007**: The operator MUST be able to apply a diff cache into the existing system cache with a single action, with no manual editing of package-source configuration.
- **FR-008**: After applying, the operator's package tooling MUST work with no network access: refreshing package metadata MUST list the new packages alongside the prior ones, and installing a new package MUST succeed from the cache.
- **FR-009**: Applying a diff MUST NOT break the operator's system: previously installed packages MUST keep working, and every package previously offered MUST remain offered.
- **FR-010**: The apply MUST validate the diff before changing anything: complete structure, release and architecture matching the existing cache, and payload metadata integrity. An invalid or mismatched diff MUST be refused with a clear error and MUST NOT change the existing cache.
- **FR-011**: The apply MUST detect out-of-order application — a diff whose base state does not match the cache's applied state (an intermediate diff was missed) — and MUST refuse with a clear error telling the operator to apply the earlier diff first, leaving the cache unchanged.
- **FR-012**: Re-applying an already-applied diff MUST be safe and idempotent: no duplicate files, no conflicting metadata, no disruption to the working system.
- **FR-013**: Interrupted or failed applies MUST leave the existing cache working: no partial files and no inconsistent metadata, and a re-run MUST converge to the same state as a single clean run.
- **FR-014**: The process MUST be repeatable without bound: each cycle (delta export + apply) advances the system's snapshot, so the next cycle ships only what is new since the previous one.

### Key Entities *(include if feature involves data)*

- **PendingSet / SyncState (existing, derived, spec 010)**: current + verified binaries not yet delivered to a chosen system — the delta source the export consumes; derived by a single query, never stored.
- **DiffCache (derived, generated)**: The minimal handoff payload — the new packages' pool files, the apt metadata (indexes / `Release` / signature) presenting prior + new, and the apply script. Produced by the administrator's delta export; a small fraction of the cache size (only new files + metadata).
- **Delivery record (existing, spec 010)**: `systems` monikers + `binaries.sync_mask` bits; the delta export writes the new snapshot (marking the shipped set delivered), and the diff carries the base state marker used by the apply's ordering check.
- **System cache (existing, operator side)**: The bootstrapped `{cache}` + configured apt source from spec 012; the apply merges the diff into it in place. No schema changes (the spec 010/011 freeze holds).
- **Applied-state marker (new, derived, operator side)**: The cache's record of which diff base has been applied — written by the bootstrap and advanced by each apply — enabling the out-of-order check (FR-011).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The delta between a system's two snapshots ships exactly the new, verified packages — 100% of the pending set included and 0% of already-delivered content re-shipped — on the administrator side.
- **SC-002**: On the operator's offline system, after applying a diff, refreshing package metadata lists all prior plus new packages, and installing a new package succeeds with zero network access and zero manual configuration steps.
- **SC-003**: 0% of previously installed packages are disrupted by an apply — the system keeps working and every previously offered package remains available.
- **SC-004**: An invalid, corrupt, mismatched, or out-of-order diff is refused with a clear error, and the existing cache is provably unchanged (no partial files, no metadata change).
- **SC-005**: Re-runs are idempotent — a second delta export ships an empty diff and a second apply changes nothing — and the whole cycle can repeat with each new snapshot without bound.
- **SC-006**: The handoff payload is minimal by construction — only new package files and small metadata cross the admin/operator boundary, with no duplication of already-delivered cache content.

## Assumptions

- **Persona split**: The delta export is administrator-only (runs on the networked machine with the database, pool, and registry, like `handoff`); the apply is operator-only (runs on the offline system against the existing cache). This is the first feature where both personas actively operate.
- **Baseline**: The operator's system is already bootstrapped to a full cache via spec 012 (whose handoff sets the baseline snapshot) before any diff is applied; the diff flow repeats on top of that baseline. Applying a diff without a bootstrap is an error (see Edge Cases).
- **Command shape**: The delta export is a new admin-only command, `package_mgmt sync --system <moniker>` (the feature's namesake), and the apply is a script shipped in the diff (`apply.sh`), mirroring the spec 012 `bootstrap.sh` precedent. The spec 012 `handoff` command (full) is unchanged.
- **Existing `sync` is renamed**: roles.md documented a `sync` command for apt-offline bundle bulk-ingest (an alternative cache build-up path, never implemented); to give the delta export the `sync` name, that entry is renamed to `ingest` in roles.md (a documentation-only change).
- **Binary delivery**: Like the spec 012 handoff, the diff carries binary packages (the delivered unit); `deb-src` exposure on the operator side remains as delivered by the initial handoff. Binary-source consistency (Constitution III) is unchanged — the diff ships what the admin verified.
- **Minimality**: The diff carries only new files plus the small apt metadata needed to present prior + new; indexes are a small fraction of cache size, so shipping complete presentable metadata does not violate minimality (Constitution IV).
- **Integrity model**: The diff's apt metadata is signed per the Constitution gate, and the operator-side apply preflight is structural (structure + release/architecture match + payload metadata integrity), consistent with the spec 012 deferral — deep per-file pool verification is the administrator's pre-ship `verify` responsibility.
- **Ordering**: The administrator ships and the operator applies diffs in order; the base-state check (FR-011) detects and refuses a missed diff so apt never sees a package whose file is missing.
- **Scope boundary**: This feature delivers the minimal-diff sync flow only. It does not cover reverting an applied diff (removing new packages), operator-side cache pruning, or anything beyond apt consumption of the merged cache (Constitution IX).
- **Existing behavior**: All existing commands' output and semantics are unchanged by this feature, as are the spec 010/011 schema and freeze.

## Clarifications

### Session

Design decisions carried over from the spec 010/012 context; no open items.

- Command naming: the delta export is the `package_mgmt sync --system <moniker>` command (decided at planning) — the feature's namesake; roles.md's documented-but-unimplemented bulk-ingest `sync` is renamed `ingest` (doc-only). The operator-side apply is the `apply.sh` script shipped in the diff.

- Delta source: the pending set (current + verified + not delivered to the system) from spec 010 IS the delta; this feature is the "future sync/export command that consumes the pending set" anticipated in the spec 010 assumptions.
- Snapshot timing: like spec 012's `handoff --system` (FR-011), the delta export takes the new snapshot as part of the operation, marking exactly what it ships — the snapshot is the delivery record, so the process stays repeatable.
- Operator cache semantics: "pull into the system cache" means merging into the operator's existing bootstrapped cache in place, so apt presents prior + new and existing installs are untouched; the operator never runs the project tooling.
- Ordering safety: a base-state marker on the diff vs. the cache's applied state detects missed/intermediate diffs and refuses them, so apt can never see a package whose file is absent.
