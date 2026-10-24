# Tasks: Apt VersionHash Fidelity

**Input**: Design documents from `/specs/022-apt-version-hash/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: The feature is a retrospective — the corrective work is already implemented and
verified in the working tree (parity scan 0/207, 806 tests green, `ruff check` clean). The
unit regression tests below were explicitly produced by this feature (plan Technical
Context, quickstart Phase 2) and both test runners are mandatory (Constitution).

**Organization**: Tasks are grouped by user story to enable independent implementation and
testing of each story. Completed retrospective work is marked `- [x]`; the clarifications
from `/speckit.clarify` added requirements that are **not yet in the
code** — those are marked `- [ ]` and are the remaining work.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **CLI tool**: `src/` at repository root for the Python tooling
- **Cache directory**: admin-managed package cache with apt repository layout:
  `cache/pool/`, `cache/dists/` (real cache at `/var/cache/package_mgmt/binary`)
- **Metadata**: `cache/package_mgmt.db` for local index
- Paths shown assume single project — adjusted per plan.md structure

---

## Phase 1: Setup & Planning (Documentation)

**Purpose**: Specification and planning artifacts for the feature (all completed)

- [x] T001 Write feature spec.md — user stories US1–US4 with priorities, FR-001..FR-007,
  SC-001..SC-005, edge cases in `specs/022-apt-version-hash/spec.md`
- [x] T002 Record clarifications in `specs/022-apt-version-hash/spec.md` under
  `## Clarifications` (retrospective framing, in-project VersionHash contract,
  degraded-fallback parity preservation, per-package warning, apt 3.x contract anchoring)
- [x] T003 Produce plan.md in `specs/022-apt-version-hash/plan.md` (retrospective plan:
  two design centers, constitution check, project structure, schema freeze note)
- [x] T004 Produce research.md in `specs/022-apt-version-hash/research.md` (apt 3.0.3
  `debListParser::VersionHash`: field set/order, DJB-2 hash, normalisation rules, merge
  semantics, root cause, 7/207 → 0/207 evidence)
- [x] T005 Produce data-model.md in `specs/022-apt-version-hash/data-model.md` (field
  provenance by component, parity-preserving degraded fallback row)
- [x] T006 Produce contracts/packages-stanza.md in
  `specs/022-apt-version-hash/contracts/packages-stanza.md` (emitted `Packages` stanza
  contract; no CLI contract change)
- [x] T007 Produce quickstart.md in `specs/022-apt-version-hash/quickstart.md` (parity
  scan, unit/contract regression, operator scenarios, validation log)
- [x] T008 Validate spec against `specs/022-apt-version-hash/checklists/requirements.md`
  (16/16 items passing)

---

## Phase 2: Foundational (Model & Index Capture)

**Purpose**: The VersionHash field plumbing every emission path relies on (completed)

- [x] T009 Confirm baseline test suite green (806 tests via `pytest` and
  `python -m unittest discover -s tests`) and `ruff check` clean before corrective work
- [x] T010 [P] Add VersionHash input fields to `BinaryPackage` (`depends_field`,
  `installed_size`, `conflicts_raw`, `breaks_raw`, `replaces_raw`) in
  `src/package_mgmt/lib/model/package.py`
- [x] T011 [P] Capture VersionHash fields verbatim in `load_binary_index` (archive index
  source) in `src/package_mgmt/lib/service/index_loader.py`
- [x] T012 [P] Add `test_version_hash_fields_captured` asserting `load_binary_index`
  captures all VersionHash fields in `tests/unit/test_index_loader.py`

**Checkpoint**: Foundation ready — emission path groundwork complete.

---

## Phase 3: User Story 2 - Repo Stanzas Carry the VersionHash Fields Verbatim (Priority: P1)

**Goal**: Handoff/diff stanzas reproduce the VersionHash-relevant control fields
(`Installed-Size`, `Depends`, `Pre-Depends`, `Conflicts`, `Breaks`, `Replaces`) verbatim
from the actual package control, including their absence — the mechanism behind US1
(FR-001/002/003).

**Independent Test**: For a package whose `.deb` control has `Pre-Depends` but no
`Depends`, generate the handoff and confirm the stanza contains `Pre-Depends` but no
`Depends` line, and its VersionHash equals the `.deb` control's (quickstart Scenario 1b).

### Completed (retrospective implementation)

- [x] T013 [P] [US2] Implement `_read_deb_control` (reads the `local` `.deb` control via
  `debian.debfile.DebFile`; returns `{}` for an unreadable payload) in
  `src/package_mgmt/lib/service/handoff.py`
- [x] T014 [P] [US2] Implement `_local_binary_entry` populating the VersionHash fields
  from the payload control (readable path authoritative; missing `Depends` stays missing)
  in `src/package_mgmt/lib/service/handoff.py`
- [x] T015 [US2] Emit VersionHash fields in `_paragraph` in the fixed order — `Installed-Size`,
  `Depends` (from `depends_field` **only**, never the merged `depends_raw` view),
  `Pre-Depends`, `Conflicts`, `Breaks`, `Replaces` — in `src/package_mgmt/lib/service/handoff.py`
- [x] T016 [P] [US2] Add regression test `test_handoff_no_invented_depends_for_pre_depends_only_archive_package`
  (archive pre-depends-only stanza has no `Depends` line) in `tests/unit/test_handoff.py`
- [x] T017 [P] [US2] Add regression test `test_handoff_local_pre_depends_only_no_invented_depends`
  (readable `local` pre-depends-only stanza has no invented `Depends`) in
  `tests/unit/test_handoff.py`
- [x] T018 [P] [US2] Add verbatim-carry tests — `test_handoff_local_payload_control_fields_carried_verbatim`,
  `test_handoff_local_depends_keeps_original_control_text`,
  `test_diff_local_payload_control_fields_carried_verbatim` — in `tests/unit/test_handoff.py`
- [x] T019 [US2] Run the VersionHash parity scan over the real cache — **0/207** mismatches
  after the fix (quickstart Phase 1; before: 7/207, all a spurious `Depends`)

### Remaining (clarified FR-006 — parity-preserving fallback + warning)

> These come from the `/speckit.clarify` session: Q1 (degraded fallback must stay
> parity-correct, never emitting the merged `depends_raw` as `Depends`) and Q2 (a
> per-package warning must flag the affected package). The current code still emits the
> merged view on an unreadable payload and warns nothing.

- [x] T020 [P] [US2] Capture the original VersionHash field text (`Depends`, `Pre-Depends`,
  `Installed-Size`, `Conflicts`, `Breaks`, `Replaces`) at `local` import time in
  `src/package_mgmt/lib/service/import_local.py` so the generation-time DB-only fallback
  can reproduce it — **constraint**: schema freeze (spec 010/011), so store within the
  existing serialized dependency JSON in `binaries.dependencies`, backward-compatible
  (old rows degrade gracefully)
- [x] T021 [US2] Make the unreadable-payload fallback parity-preserving: emit the
  VersionHash fields from the stored original values — never the merged `depends_raw` as
  `Depends` — in `_local_binary_entry` / `_paragraph` in
  `src/package_mgmt/lib/service/handoff.py` (FR-006 per clarified parity requirement)
- [x] T022 [US2] Emit a per-package warning flagging the affected package when a `local`
  payload is unreadable and the DB-only fallback is used, wired into the existing
  handoff/diff warnings summary (`generate_handoff`/`generate_diff` stats warnings in
  `src/package_mgmt/lib/service/handoff.py`; surfaced by
  `src/package_mgmt/cli/commands/handoff.py` `_print_summary`) (FR-006 per clarified
  warning requirement)
- [x] T023 [P] [US2] Update `test_handoff_local_unreadable_payload_falls_back_to_db_reconstruction`
  in `tests/unit/test_handoff.py` to assert the new parity-preserving emission and the
  per-package warning (replaces the current assertions of no VersionHash fields)

**Checkpoint**: At this point User Story 2 is fully parity-correct, including the
degraded path.

---

## Phase 4: User Story 1 - An Installed Package Is Not Reported as Upgradable (Priority: P1) 🎯 MVP

**Goal**: The operator sees a package as upgradable **only** when a strictly newer version
is genuinely available — an identical installed version merges with the repo record and is
not listed (SC-001/SC-003).

**Independent Test**: Install a package at the version the cache holds, run `apt update`
against the handoff source, and confirm `apt list --upgradable` does not list it — while a
package for which the cache genuinely holds a newer version still does (quickstart
Phase 3, Scenarios 3a/3b).

- [x] T024 [US1] Confirm upgrade semantics preserved — a strictly newer cached version
  still surfaces as upgradable (existing upgrade tests; apt's version comparison precedes
  the hash check in `MergeListVersion`) — research §4

**Checkpoint**: User Story 1 validated end-to-end on the operator.

---

## Phase 5: User Story 3 - The VersionHash Contract Is Documented (Priority: P2)

**Goal**: The knowledge enabling this correctness — apt's VersionHash algorithm, field set
and order, hashing rules, and reference implementation location — lives in-project, so
future work does not need external lookup and cannot silently regress it (FR-007, SC-005).

**Independent Test**: A reader of the spec/plan can determine, without external lookup,
which fields contribute to apt's VersionHash, in what order, with what normalisation
rules, and where the reference implementation lives.

- [x] T027 [P] [US3] Document the VersionHash contract (field set `{Installed-Size,
  Depends, Pre-Depends, Conflicts, Breaks, Replaces}`, order, DJB-2 hash, normalisation
  rules, merge semantics, apt 3.0.3 reference location) in `specs/022-apt-version-hash/research.md`
- [x] T028 [P] [US3] State contract stability across the apt 3.x series (apt 3.0.3 as the
  reference snapshot; no re-verification note required within the series) in
  `specs/022-apt-version-hash/spec.md` FR-007 and `specs/022-apt-version-hash/research.md`
- [x] T029 [US3] Confirm SC-005 — a developer can derive the field set, order, and
  normalisation rules from the project artifacts alone (self-check across spec/plan/research)

---

## Phase 6: User Story 4 - Genuine Upgrades Still Work Across All Components and Channels (Priority: P2)

**Goal**: The fix must not blunt upgrades — a strictly newer version still presents as
upgradable and installs, across every component (`main`/`contrib`/`non-free`/backports/
`local`) and both channels (baseline `handoff` and incremental `diff`/`sync`) (FR-004/005).

**Independent Test**: With a package at an older installed version and a newer version in
the cache, confirm it appears upgradable and installs; repeat across `main`, `local`, the
handoff index, and the diff index.

- [x] T030 [P] [US4] Confirm diff-channel verbatim carry via
  `test_diff_local_payload_control_fields_carried_verbatim` (shared `_paragraph` path) in
  `tests/unit/test_handoff.py`
- [x] T031 [P] [US4] Confirm `Multi-Arch` and `Provides` are still emitted unchanged
  (`name:any` resolution and virtual-package installs keep working) in `_paragraph` in
  `src/package_mgmt/lib/service/handoff.py`
- [x] T032 [US4] Confirm all components and both channels share the same `_paragraph`
  emission path — no per-component or per-channel deviation (contract Phase 4)

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final regression gate. 

- [x] T033 Full regression after all changes: `pytest` + `python -m unittest discover -s
  tests` green and `tests/contract/test_cli.py` unchanged (quickstart Regression)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup/Planning (Phase 1)**: No dependencies — already complete.
- **Foundational (Phase 2)**: Depends on Phase 1 — already complete; blocks US2.
- **User Story 2 (Phase 3)**: Depends on Phase 2. The retrospective portion (T013–T019) is
  complete; the remaining work (T020–T023) is the only pending mechanism work.
- **User Story 1 (Phase 4)**: Depends on US2 completion (parity emission, T013–T023);
  operator-level validation is performed manually by the Administrator.
- **User Story 3 (Phase 5)**: Independent — documentation only.
- **User Story 4 (Phase 6)**: Depends on US2 (shared `_paragraph` path); verification done.
- **Polish (Phase 7)**: T033 (final regression) depends on US2 remaining tasks (T020–T022)
  being complete.

### User Story Dependencies

- **User Story 1 (P1)**: Operator scenarios depend on US2 (parity emission) + rollout.
- **User Story 2 (P1)**: Independent of other stories; the mechanism everything else relies
  on. Remaining open tasks must be done first.
- **User Story 3 (P2)**: No dependencies — can be done any time.
- **User Story 4 (P2)**: Depends on US2's shared emission path.

### Within Each User Story

- Tests (T016–T018, T023) verify the mechanism before/with implementation.
- Models before services (Phase 2 model → US2 emission).
- Story complete before moving to the next priority.

### Parallel Opportunities

- Phase 1 docs tasks: fully parallel.
- Phase 2: T010, T011, T012 in parallel.
- US2: T013, T014, T016, T017, T018 in parallel; remaining T020 and T022 in parallel.
- US3: T027, T028 in parallel.
- US4: T030, T031 in parallel.
- Polish: T033 (final regression) is last, after US2 remaining tasks.

---

## Parallel Example: User Story 2 (remaining)

```bash
# Launch the storage + warning tasks together (independent files):
Task: "Capture original VersionHash field text at local import (import_local.py)"
Task: "Emit per-package warning on degraded fallback (handoff.py warnings summary)"

# Then the fallback emission change (depends on the storage task):
Task: "Make unreadable-payload fallback parity-preserving (_local_binary_entry/_paragraph)"

# Update the regression test to the new behavior:
Task: "Update test_handoff_local_unreadable_payload_falls_back_to_db_reconstruction"
```

---

## Implementation Strategy

### MVP First (User Story 2 mechanism → rollout)

Because the feature is a retrospective, the MVP is completing the clarified FR-006 work and
shipping the corrected index:

1. Complete US2 remaining tasks (T020 capture → T021 parity-preserving fallback → T022
   warning → T023 test update).
2. Administrator regenerates/re-exports the handoff and validates US1 operator scenarios
   (manual, outside these tasks).
3. Final full regression gate (T033).

### Incremental Delivery

1. Planning + Foundation (Phases 1–2) — done.
2. US2 mechanism (Phase 3) — retrospective portion done; remaining clarifications to close.
3. US2 complete → Administrator regenerates + re-exports handoff (manual) → deploy/demo.
4. US1 operator validation (manual) → US3/US4 already verified → final regression (T033).

### Parallel Team Strategy

With multiple developers:

1. Developer A: US2 remaining storage + fallback (T020, T021).
2. Developer B: US2 warning + test update (T022, T023).
3. After US2 completes: the Administrator regenerates/exports the handoff and validates
   the US1 operator scenarios (manual), then the final regression gate (T033).

---

## Notes

- **[P]** tasks = different files, no dependencies.
- **[Story]** label maps task to the specific user story for traceability.
- Completed retrospective tasks are marked `- [x]`; verify with the committed state
  (parity scan 0/207, 806 tests green, `ruff check` clean).
- The `/speckit.clarify` session added FR-006 requirements (parity-preserving degraded
  fallback, per-package warning) that are **not yet implemented** — these are the open
  tasks T020–T023.
- The generated handoff must be regenerated and re-exported for the operator to see the fix
  (quickstart Validation Log) — done manually by the Administrator, not tracked as a task.
- US1 operator-level validation (quickstart Phase 3 scenarios) is likewise performed
  manually by the Administrator after regeneration/export, not tracked as a task.
- Commit after each task or logical group.
