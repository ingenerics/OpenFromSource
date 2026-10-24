---

description: "Task list for feature 010 - Sync Mask & Snapshot"

---

# Tasks: Sync Mask & Snapshot

**Input**: Design documents from `/specs/010-sync-mask-snapshot/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/cli.md, quickstart.md

**Tests**: Test tasks are included — the implementation plan (plan.md) specifies unit, integration, and contract tests for every story; write them first, verify they fail, then implement.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **CLI tool**: `src/package_mgmt/` (Python package; CLI entry `package_mgmt`)
- **Tests**: `tests/unit/`, `tests/integration/`, `tests/contract/` (unittest style; run with `python3 -m unittest discover -s tests`)
- **Scratch archives**: `tests/fixtures/make_test_iso.py` (spec 008 fixture for integration tests)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: No setup required — the project structure, CLI scaffolding, error-handling conventions (`_print_error` JSON to stderr, exit codes 0/1/100-102 in `src/package_mgmt/lib/util/errors.py`), and test layout already exist from prior features. Work starts directly in the Foundational phase.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema plumbing that MUST be complete before ANY user story can be implemented.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [x] T001 Extend `EXPECTED_SCHEMA_SQL` in `src/package_mgmt/lib/model/database.py`: add the `systems` table (id PK AUTOINCREMENT, name TEXT NOT NULL, bit INTEGER NOT NULL UNIQUE, note TEXT, retired INTEGER NOT NULL DEFAULT 0), the partial unique index `uq_systems_active_name ON systems(name) WHERE retired = 0`, and the `binaries.sync_mask` column (`INTEGER NOT NULL DEFAULT 0`); `SCHEMA_VERSION` MUST stay 1 (FR-013)
- [x] T002 [P] Add stale-object detection to `src/package_mgmt/lib/service/db_init.py`: when `PRAGMA user_version` matches but the `systems` table or the `binaries.sync_mask` column is missing (a pre-feature development database), raise `DatabaseError` with a message describing the documented delete-and-rebuild procedure (delete the DB, re-run `init` + `ensure-base` + `add --file`); expose it as a shared validate helper (e.g. `validate_schema(db_path)`) callable from the new command entry points (FR-012)
- [x] T003 [P] Extend `tests/unit/test_database.py`: schema-object tests (systems table + `sync_mask` column exist after `initialize_database`; version still 1; unique-active-name index enforces duplicate monikers only among active rows) and stale-DB tests (a database created from the old schema raises `DatabaseError` with the rebuild message)

**Checkpoint**: Schema v1 in place with the new objects; stale development databases fail with a clear rebuild message; foundation is ready for user story implementation.

---

## Phase 3: User Story 1 - Know Your Targets: A Registry of Sync Systems (Priority: P1) 🎯 MVP

**Goal**: The `systems` command manages the registry of sync targets — add/list/rename/remove with unique monikers, opaque auto-assigned bits (never shown, never reused), soft-delete removal, and delivered counts in the listing.

**Independent Test**: Manage a registry without touching the database directly — `systems add` several names (one with `--note`), `systems list`, `systems rename`, `systems remove`, re-add the removed name, attempt a duplicate add — every effect verifiable through `systems list` alone; no numeric identifier ever appears in any output (spec US1, contracts/cli.md).

### Tests for User Story 1 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementing.**

- [x] T004 [P] [US1] Unit tests for the registry lifecycle in `tests/unit/test_systems.py`: add with/without note, duplicate moniker rejected, rename preserves bit and delivered marks, rename-to-existing rejected, remove soft-deletes (invisible to list, deliveries kept), re-add of removed moniker gets a fresh bit and appears never-synced, bit assignment is monotonic (first = 2, next = 4, 8, ...) and never reuses a retired bit, registry-full error at the 62-system ceiling, list shows moniker/note/delivered-count
- [x] T005 [P] [US1] Contract tests for `systems` output and error formats in `tests/contract/test_cli.py`: `Added system 'X'`, `Renamed system ...`, `Removed system ...`, list table header `NAME NOTE DELIVERED`, duplicate/unknown errors as JSON on stderr with exit 1, no bit value in any output (contracts/cli.md)

### Implementation for User Story 1

- [x] T006 [US1] Implement the registry command module in `src/package_mgmt/cli/commands/systems.py`: `handle_systems_add/list/rename/remove` operating on the database — bit assignment as `COALESCE(MAX(bit), 1) * 2` over ALL rows (retired included), ceiling at `2^62`; add (duplicate active moniker → error), list (JOIN delivered count: current binaries with the bit set), rename (active-only, duplicate target → error), remove (soft-delete `retired = 1`, unknown → error); all errors via `_print_error` (JSON, exit 1); run the shared schema validate helper on entry (stale DB → `EXIT_SCHEMA_MISMATCH` 101 with rebuild message)
- [x] T007 [US1] Register and dispatch `systems` in `src/package_mgmt/cli/main.py`: a `systems` subparser with nested `add <name> [--note]`, `list`, `rename <name> <new-name>`, `remove <name>` subparsers, and dispatch to `handle_*` (following the existing `_register_*` pattern)

**Checkpoint**: User Story 1 fully functional and testable independently — registry lifecycle and contracts verified.

---

## Phase 4: User Story 2 - Snapshot: This State Has Been Delivered (Priority: P1)

**Goal**: The `snapshot` command marks exactly the current, verified binaries as delivered for a named system (pure assertion), with a read-only `--dry-run` preview; idempotent; the pending set between two snapshots is the delta (spec US2, FR-006..FR-010).

**Independent Test**: Fresh cache → dry-run reports 0 (nothing verified) → simulate downloads (set `verified = 1`) → dry-run reports N → snapshot marks N → re-run reports 0 → register a new package, verify it, dry-run reports exactly the delta; unknown system fails with exit 1 and no changes (spec US2 AS1-AS5).

### Tests for User Story 2 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementing.**

- [x] T008 [P] [US2] Unit tests for snapshot semantics in `tests/unit/test_snapshot.py`: dry-run modifies nothing; real run marks exactly `status = 'current' AND verified = 1` binaries (superseded and unverified excluded); OR accumulation across snapshots; idempotent re-run reports 0; unknown/retired moniker → error, no changes; empty cache → 0 marked; a later `verified` reset does not clear the mask (data-model.md invariant)
- [x] T009 [P] [US2] Contract tests for `snapshot` output and error formats in `tests/contract/test_cli.py`: `<N> pending for '<name>'` (dry-run), `Marked <N> binaries for '<name>'`, unknown-system and missing-`--system` JSON errors with exit 1 (contracts/cli.md)
- [x] T010 [P] [US2] Integration test for the two-points-in-time delta flow in `tests/integration/test_sync_snapshot_flow.py`: use the spec 008 scratch archive fixture (`tests/fixtures/make_test_iso.py`), simulate downloads by updating `verified = 1` directly (no command sets it yet), then: add → verify → snapshot T1 → add more → verify → dry-run = exactly the new current-verified binaries → snapshot T2 → re-run reports 0 (SC-001/SC-002)

### Implementation for User Story 2

- [x] T011 [US2] Implement the snapshot command module in `src/package_mgmt/cli/commands/snapshot.py`: resolve the active system by moniker (unknown/retired → `_print_error`, exit 1); dry-run = count of `status = 'current' AND verified = 1 AND (sync_mask & bit) = 0`; real run = single atomic `UPDATE binaries SET sync_mask = sync_mask | ? WHERE status = 'current' AND verified = 1` (OR semantics, idempotent); print counts per contracts/cli.md; run the shared schema validate helper on entry (stale DB → 101)
- [x] T012 [US2] Register and dispatch `snapshot` in `src/package_mgmt/cli/main.py`: subparser with `--system <name>` (required) and `--dry-run` flags, dispatched to `handle_snapshot` following the existing `_register_*` pattern

**Checkpoint**: User Stories 1 AND 2 both work independently — the delta flow is demonstrable end to end.

---

## Phase 5: User Story 3 - Version History Stays Consistent (Priority: P2)

**Goal**: Fix the superseding hole so re-requesting a previously superseded version leaves exactly one current binary (FR-011); the re-requested version is then markable like any other current, verified binary (US3 AS2).

**Independent Test**: `add pkg=1.0` → `add pkg=2.0` → `add pkg=1.0` — exactly one current binary remains (1.0), 2.0 superseded, history preserved (spec US3).

### Tests for User Story 3 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementing.**

- [x] T013 [P] [US3] Unit tests for the supersede fix in `tests/unit/test_resolver_batch.py`: re-requesting a version whose row exists as `superseded` leaves exactly one current binary (the re-requested version) with the previous one superseded; the superseded row's history is preserved; all other superseding behavior is unchanged (spec US3 AS1, FR-011)

### Implementation for User Story 3

- [x] T014 [US3] Fix `upsert_binary` in `src/package_mgmt/lib/service/resolver.py`: the `ON CONFLICT` update MUST also restore `status = 'current'` (keeping the existing `verified = 0` reset and `dependencies` update) so a re-registered superseded version becomes the single current binary; `sync_mask` MUST NOT be touched (data-model.md invariant)
- [x] T015 [US3] Extend `tests/integration/test_sync_snapshot_flow.py` with the combined scenario: re-request scenario from US3, simulate verification, snapshot — the re-requested version is included in the marked set (US3 AS2)

**Checkpoint**: All user stories independently functional; supersede behavior consistent with snapshot marking.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verification, consistency, and final validation across all stories.

- [x] T016 [P] Run the full test suite (`python3 -m unittest discover -s tests`) and `ruff check src tests` — all green, zero lint errors
- [x] T017 [P] Execute every scenario in `specs/010-sync-mask-snapshot/quickstart.md` end to end against a scratch cache (registry lifecycle, two-points-in-time delta with simulated verification, supersede fix, stale-DB diagnosis with exit 101, unknown-system errors)
- [x] T018 [P] Documentation consistency pass: verify `data-model.md` (DDL, invariants, pending-set query, migration policy) and `contracts/cli.md` (outputs, exit codes, error shapes) match the implemented behavior; update `AGENTS.md` SPECKIT context if the feature pointer changed

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No tasks — existing structure reused
- **Foundational (Phase 2)**: No external dependencies — BLOCKS all user stories (T001, T002, T003)
- **User Story 1 (Phase 3)**: Depends on Foundational — no other story dependencies
- **User Story 2 (Phase 4)**: Depends on Foundational — requires the registry (US1) at integration time (snapshot resolves systems by moniker) but its unit tests are independent
- **User Story 3 (Phase 5)**: Depends on Foundational only — different files (`resolver.py` vs `systems.py`/`snapshot.py`), can run in parallel with US1/US2
- **Polish (Phase 6)**: Depends on all user stories complete

### User Story Dependencies

- **User Story 1 (P1)**: No dependencies beyond Foundational
- **User Story 2 (P1)**: Reads the systems registry (US1) — implement after or alongside US1
- **User Story 3 (P2)**: No dependencies beyond Foundational — independent of US1/US2 files

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Unit tests → contract tests → command implementation → CLI registration
- Story complete before moving to the next priority

### Parallel Opportunities

- Foundational: T002 and T003 can run in parallel with T001? NO — T002/T003 depend on the schema change in T001; only T002 ∥ T003 in parallel
- Tests within a story: T004 ∥ T005, T008 ∥ T009 ∥ T010, T013 alone
- After Foundational: US1 (T006/T007) and US3 (T014) can run in parallel (different files); US2's implementation (T011/T012) can start after US1's registry exists (needs the systems table + moniker resolution at runtime)
- Polish: T016, T017, T018 fully parallel

---

## Parallel Example: After Foundational

```bash
# Developer A — User Story 1 (registry):
Task: "T004 unit tests in tests/unit/test_systems.py"
Task: "T005 contract tests in tests/contract/test_cli.py"
Task: "T006 implement src/package_mgmt/cli/commands/systems.py"
Task: "T007 wire systems into src/package_mgmt/cli/main.py"

# Developer B — User Story 3 (supersede fix, independent files):
Task: "T013 unit tests in tests/unit/test_resolver_batch.py"
Task: "T014 fix upsert_binary in src/package_mgmt/lib/service/resolver.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 2: Foundational (schema + stale-DB detection — CRITICAL, blocks everything)
2. Complete Phase 3: User Story 1 (registry) — tests first, then `systems.py`, then CLI wiring
3. **STOP and VALIDATE**: `systems add/list/rename/remove` work independently; full suite green
4. Deploy/demo the registry if ready

### Incremental Delivery

1. Foundational → registry (US1) → **MVP checkpoint**
2. Add snapshot (US2) → delta flow demonstrable → the feature's core value
3. Add supersede fix (US3) → downgrade path consistent with marking
4. Polish: lint, full suite, quickstart validation

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to the spec user story for traceability
- All user-facing errors use `_print_error()` (JSON to stderr); exit codes: 0 success, 1 error, 101 stale schema, 102 config
- `sync_mask` is written by exactly one operation (the snapshot UPDATE); nothing else may touch it
- Simulated downloads: no command sets `verified = 1` yet (download spec is future) — tests and quickstart set it directly via SQL
- Commit after each task or logical group
- Stop at any checkpoint to validate the story independently
