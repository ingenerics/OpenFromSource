---

description: "Task list for Alpha Release Blockers"

---

# Tasks: Alpha Release Blockers

**Input**: Design documents from `/specs/015-alpha-release-blockers/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (`cli.md`, `artifacts.md`), quickstart.md

**Tests**: Tests ARE required. Each phase ships with its regression tests (spec's "User Scenarios & Testing (mandatory)", plan's per-phase Tests). Tests MUST be written and FAIL before implementation, except where noted (e.g. updating an existing assertion in `tests/unit/test_handoff.py`).

**Organization**: Six independent blocker phases (B-1…B-6), each its own workstream. Phases touch disjoint code paths and do not build on one another; they are grouped only because all six must land before the alpha tag. Tasks are grouped by user story so each story can be implemented and tested independently.

**Cross-cutting constraints (all phases)**:
- **No schema change** — the spec 010/011 freeze is absolute (see `specs/015-alpha-release-blockers/data-model.md`).
- Contracts in `specs/014-base-profiles/contracts/cli.md` continue to hold or are extended, never contradicted.
- Every change lands with its regression tests and must be green under **both** documented runners: `pytest` and `python -m unittest discover -s tests`.
- Work lands on `main` (no feature branch) before the alpha tag.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US6)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Baseline & Environment)

**Purpose**: Establish a verified baseline before any blocker is touched — confirms both documented runners execute, fixtures are available, and the repo is at the expected base.

- [X] T001 Verify baseline test state: run both documented runners (`pytest` and `python -m unittest discover -s tests`) from a clean checkout; record the failing tests (expected: the 8 tests in `tests/contract/test_cli.py` under `python -m unittest`) as the Phase 6 baseline
- [X] T002 [P] Verify the offline-safe test prerequisites per `specs/015-alpha-release-blockers/quickstart.md`: the local mirror fixture (`tests/fixtures/make_test_iso.py`) and the hermetic apt harness (`tests/integration/test_handoff_flow.py`) runnable
- [X] T003 [P] Verify repo state: on branch `main` with the venv active, and the plan's base commit `995cf3e` reachable

---

## Phase 2: Foundational (Cross-Cutting Guards)

**Purpose**: The six phases are independent, so there is no blocking shared infrastructure; this phase locks in the three guards every phase must respect. No user story work may violate these.

**⚠️ CRITICAL**: These guards are prerequisite evidence — verify them before any phase, re-verify in Phase 9.

- [X] T004 Confirm the schema-freeze baseline: current SQLite tables match spec 010/011 (`packages`, `binaries`, `sources`, `binary_to_source`, `source_build_deps`, `provides`, `virtual_resolutions`, `systems`); record that NO phase may add/alter columns
- [X] T005 [P] Confirm the baseline exit-code / JSON contracts from `specs/014-base-profiles/contracts/cli.md` hold (exit codes `0`/`1`/`101`/`102`, JSON-on-stderr) — the guard that Phases 3 and 6 must preserve
- [X] T006 [P] Record baseline evidence for Phases 1 and 5: zero occurrences of `Pre-Depends` in `src/` today, and `tests/unit/test_handoff.py:176` asserts a `Valid-Until:` line (both change in their phases)

**Checkpoint**: Guards verified — phases may proceed in parallel.

---

## Phase 3: User Story 1 - Complete, Installable Runtime Closures (Pre-Depends) (Priority: P1) 🎯 MVP

**Goal**: Every hard runtime requirement (including Pre-Depends targets not duplicated in `Depends`) lands in the cache, so the shipped cache is genuinely installable offline. *(B-1)*

**Independent Test**: Add a fixture package whose closure includes a Pre-Depends-only requirement, download, hand off, and run an offline `apt-get install` simulation — install succeeds with zero missing packages and the Pre-Depends target appears in the cache (`spec.md` US1).

### Tests for User Story 1 (write FIRST, must FAIL before implementation) ⚠️

- [X] T007 [P] [US1] Unit test: `load_binary_index` captures `Pre-Depends` alongside `Depends` in `tests/unit/test_index_loader.py`
- [X] T008 [P] [US1] Unit test: the merged closure contains exactly one entry for a target duplicated across `Depends`+`Pre-Depends` and one for a Pre-Depends-only target in `tests/unit/test_resolver_batch.py`
- [X] T009 [P] [US1] Add a fixture package with a Pre-Depends-only runtime requirement (not listed in `Depends`) to `tests/fixtures/signed_archive.py`
- [X] T010 [P] [US1] Integration test: `add` + `download` the Pre-Depends fixture → `verify --graph` COMPLETE; handoff `Packages.gz` carries a `Pre-Depends:` stanza; offline install simulation resolves from the cache in `tests/integration/test_download_flow.py` and `tests/integration/test_handoff_flow.py`

### Implementation for User Story 1

- [X] T011 [US1] Add optional `pre_depends_raw: str | None` field to `BinaryPackage` in `src/package_mgmt/lib/model/package.py` (depends on T007/T008 passing as red)
- [X] T012 [P] [US1] Read `Pre-Depends` alongside `Depends` in `load_binary_index` (`src/package_mgmt/lib/service/index_loader.py:45-66`) and merge the two (deduplicated on parse) into the dependency set the closure walk resolves — resolver merge point (`resolver.py:707-711`) stays unchanged
- [X] T013 [P] [US1] Emit a faithful `Pre-Depends:` stanza (in addition to merged `Depends:`), in Debian control-field order, in the handoff/diff `Packages.gz` writers when present (`src/package_mgmt/lib/service/handoff.py:684-699`) so dpkg preserves install-before-configure ordering
- [X] T014 [US1] Run the Pre-Depends census on the alpha package set and record which Pre-Depends targets are *not* also in `Depends`/`Provides`, confirming the closure now includes them (acceptance evidence per `spec.md` §completion)

**Checkpoint**: US1 is fully functional — a Pre-Depends-only closure installs offline, `verify --graph` is COMPLETE, the field is emitted, and the census is recorded.

---

## Phase 4: User Story 2 - Order-Independent Source Coverage (Resolver Depth & Batch) (Priority: P1)

**Goal**: Source/build-tool coverage for finite-depth profiles is independent of graph traversal order (minimum-level semantics), virtual sources are repairable across runs, and batch resolution is failure-safe. *(B-2: P0-1/H-1 + P0-2 + F-14)*

**Independent Test**: Build a diamond fixture (`app` Depends `c1, x`; `c1` Depends `x`; depth 1) and assert `x` receives its source and build tools regardless of `Depends`-line ordering; force a mid-batch failure and assert no later segment silently skips a package (`spec.md` US2).

### Tests for User Story 2 (write FIRST, must FAIL before implementation) ⚠️

- [X] T015 [P] [US2] Add diamond fixtures at depths `0`/`1`/`N`/`-1` asserting the **minimum**-level source set (the missing SC-006 matrix rows) in `tests/unit/test_resolver_batch.py`
- [X] T016 [P] [US2] Add the virtual-first-deep-then-shallow cross-batch test — a virtually-provided binary first resolved out of scope gains its source when later reached in scope in `tests/unit/test_virtual_resolution.py`
- [X] T017 [P] [US2] Add the mid-segment failure test: monkeypatched `upsert_binary` failure → the later segment re-resolves the shared dependency or reports an explicit error, never silently skips in `tests/unit/test_resolver_build_deps.py`

### Implementation for User Story 2 (all in `src/package_mgmt/lib/service/resolver.py` — sequential, same file)

- [X] T018 [US2] Extract the source-registration block (`resolver.py:742-771`) into a helper (all upserts already idempotent)
- [X] T019 [US2] Add `_batch_levels: dict[str, int]` minimum-level tracking (batch-scoped, in-memory) to `PackageResolver`; on re-encounter at a strictly smaller in-scope level, re-run the source-registration helper for a binary with no source yet
- [X] T020 [US2] Route the cached-virtual early return (`resolver.py:664/668`) behind the in-scope evaluation so a virtually-provided binary can gain a source when reached in scope — including on later runs
- [X] T021 [US2] After a segment rollback (`resolver.py:314-319`), rebuild `_visited` / `_batch_registered` / `_batch_constraints` (and `_batch_levels`) from the DB so later segments never treat uncommitted names as resolved
- [X] T022 [US2] Regression/perf validation: at `-1` depth the source set is byte-identical to baseline; finite-depth sets stay strict subsets of the `-1` set; resolution speed does not regress (quality gate: 10,000+ indices in 5s)

**Checkpoint**: US2 is fully functional — source set is order-independent, virtual sources are repairable, and no silent closure holes remain.

---

## Phase 5: User Story 3 - Safe Diff & Delivery Ordering (Priority: P1)

**Goal**: A failed pool copy never poisons the operator's incremental delivery — the diff only references successfully copied payloads, and delivery state advances only on a clean result. *(B-3)*

**Independent Test**: Run a sync where one pool file is missing/unreadable — the command exits non-zero, the produced diff contains no stanza for the failed record, and no delivery record advances; a subsequent clean run works (`spec.md` US3).

### Tests for User Story 3 (write FIRST, must FAIL before implementation) ⚠️

- [X] T023 [P] [US3] Unit test: missing/unreadable pool file → `generate_diff` produces no `Packages` stanza / manifest entry for the failed record in `tests/unit/test_diff_generator.py`
- [X] T024 [P] [US3] Integration test: failed sync exits non-zero, delivery record for the system is unchanged (failed record still pending), and a repaired clean re-run advances exactly the shipped ids in `tests/integration/test_sync_snapshot_flow.py`

### Implementation for User Story 3

- [X] T025 [P] [US3] Reorder `generate_diff` to append the `Packages` stanza and manifest line **only after** `_copy_verified` succeeds (retract on failure) so the diff never references a payload it does not contain in `src/package_mgmt/lib/service/handoff.py:1033-1063`
- [X] T026 [P] [US3] Move `mark_delivered` in `handle_sync` to **after** both the `excluded_delivered` and `excluded_pending` checks (i.e. only on a clean `generate_diff` result) in `src/package_mgmt/cli/commands/sync.py:65-94`
- [X] T027 [US3] Validation: confirm the `excluded_delivered` defect-state path still returns before `mark_delivered`, and the exit-code contract is preserved (`0` clean / `1` any excluded; per `contracts/cli.md` Phase 3 table)

**Checkpoint**: US3 is fully functional — a failed sync leaves no poisoned diff and no advanced delivery state.

---

## Phase 6: User Story 4 - Fail Hard on Linkless Media (Priority: P2)

**Goal**: The operator bootstrap fails hard with a clear, actionable message and non-zero exit when the pool cannot be reached through the handoff (e.g. FAT media) — it never reports success with an unreachable pool, and the completion check actually verifies reachability. *(B-4)*

**Independent Test**: Run the generated bootstrap on a FAT-formatted loop device (or an equivalent no-symlink scratch area) — it fails hard with a clear message and never reports success while the pool is unreachable (`spec.md` US4).

### Tests for User Story 4 (write FIRST, must FAIL before implementation) ⚠️

- [X] T028 [P] [US4] Shell-level test: `ln -s` failure → non-zero exit, clear actionable message, and no apt source configured in `tests/unit/test_bootstrap_script.py`
- [X] T029 [P] [US4] Shell-level test: missing `handoff/pool` link or per-release `Release`/`Release.gpg` → `handoff_complete` returns non-zero in `tests/unit/test_bootstrap_script.py`

### Implementation for User Story 4 (operator-side shell changes in the shipped `BOOTSTRAP_SCRIPT` template — same file, sequential)

- [X] T030 [US4] `ensure_pool_link`: capture the `ln -s` exit status; on failure **fail hard** — exit non-zero with a clear, actionable message, no fallback, without configuring a broken apt source (in `BOOTSTRAP_SCRIPT`, `src/package_mgmt/lib/service/handoff.py:117-124`)
- [X] T031 [US4] `handoff_complete`: additionally verify the `handoff/pool` link resolves to a directory and each configured release has its `Release` + `Release.gpg` present before reporting success (`src/package_mgmt/lib/service/handoff.py:87-96`)
- [X] T032 [US4] Manual acceptance: FAT-formatted loop-device run of a generated `bootstrap.sh` must fail hard (record evidence for the completion checklist); a valid link-capable cache must still bootstrap cleanly (regression)

**Checkpoint**: US4 is fully functional — the bootstrap never reports "success with unreachable pool".

---

## Phase 7: User Story 5 - No-Expiry Repository & Generation-Time Dating (Priority: P2)

**Goal**: The operator repository imposes no enforced validity deadline (no `Valid-Until`), and its `Date` reflects generation time (never a frozen cache mtime); reproducible byte-identical regeneration remains an explicit opt-in via `SOURCE_DATE_EPOCH`. *(B-5 / decision D9)*

**Independent Test**: Generate a handoff, then regenerate it — the repository's `Date` advances to the regeneration time and the repository imposes no expiry (apt accepts it regardless of its age); the reproducibility path still produces a pinned, deterministic result when explicitly requested (`spec.md` US5).

### Tests for User Story 5

- [X] T033 [US5] Update `tests/unit/test_handoff.py:176`: assert the generated `Release` contains **no** `Valid-Until:` field; add asserts for `Date` advancing on regeneration (generation time, not cache mtime), `SOURCE_DATE_EPOCH` pinned + byte-identical regeneration, and invalid `SOURCE_DATE_EPOCH` → warning + generation-time fallback

### Implementation for User Story 5 (all in `src/package_mgmt/lib/service/handoff.py` — sequential, same file)

- [X] T034 [US5] Stop emitting the `Valid-Until:` field in `_release_text` entirely (absence ⇒ apt enforces no expiry) and remove the now-unused `VALID_UNTIL_DAYS` constant (`handoff.py:63`, `734-740`)
- [X] T035 [US5] Default `_release_epoch` to `int(time.time())` at generation, keep the `SOURCE_DATE_EPOCH` override (explicit opt-in; invalid value keeps the existing warning + fallback), and update the `_release_epoch` docstring that currently documents the frozen-mtime behavior as intended (`handoff.py:644-658`)
- [X] T036 [US5] Update the `apply.sh` template comment ("Valid-Until is refreshed") at `src/package_mgmt/lib/service/handoff.py:412` to reflect no-expiry + generation-time dating

**Checkpoint**: US5 is fully functional — no expiry deadline, truthful generation-time `Date`, reproducibility is opt-in only.

---

## Phase 8: User Story 6 - Clean Machine-Readable Errors & Green Documented Runner (Priority: P2)

**Goal**: Machine-readable error output is pure (exactly one JSON document on stderr, no log/diagnostic text), and every documented test command passes from a clean checkout. *(B-6)*

**Independent Test**: Trigger a JSON-mode configuration error and assert the error stream contains exactly one JSON document; run every documented test command from a clean checkout and assert they all pass (`spec.md` US6).

### Tests for User Story 6 (write FIRST, must FAIL before implementation) ⚠️

- [X] T037 [P] [US6] Add/update the contract tests so the 8 previously-failing cases in `tests/contract/test_cli.py` (`TestConfigContract`, `test_invalid_values_for_both_depth_settings_exit_102`, `TestHandoffGenerator`) assert stderr carries **exactly one** JSON document on `--format json` error exits for config/handoff/sync
- [X] T038 [P] [US6] Add runner-independent logging isolation (root the `lastResort`-handler dependency) in `tests/__init__.py` / conftest so the suite behaves identically under `pytest` and `python -m unittest discover -s tests`

### Implementation for User Story 6

- [X] T039 [US6] In `--format json` mode, route console log output to the log file (or suppress console logs) on the error paths of `config` (`src/package_mgmt/cli/commands/config.py:14-21`), `handoff` (`src/package_mgmt/cli/commands/handoff.py`), `sync`, and any other JSON-capable command — stderr carries only the JSON payload; text mode and the log file are unaffected; the JSON payload shape is unchanged
- [X] T040 [US6] Verification: the 8 previously-failing tests pass under **both** runners; the full suite is green from a clean checkout under `pytest` and `python -m unittest discover -s tests` (FR-013)

**Checkpoint**: US6 is fully functional — machine-readable error stream is pure JSON and both documented runners are green.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Release gate — the alpha tag is not created until this phase passes.

- [X] T041 [P] Run the full suite under both documented runners from a clean checkout (`pytest` and `python -m unittest discover -s tests`) — both must be 100% green
- [X] T042 Run the release completion checklist in `review/04_alpha_summary/01_alpha_blockers.md`: live-mirror 3-profile runs, offline installability proof (incl. FAT loop-device bootstrap run), Pre-Depends census, both runners green — record evidence
- [X] T043 [P] Constitution compliance re-check: no schema change, no new runtime dependencies, no network calls added, contracts preserved or explicitly extended (never contradicted) — cross-check Phases 1-6 against `specs/015-alpha-release-blockers/contracts/`
- [X] T044 [P] Documentation sync: verify `specs/015-alpha-release-blockers/` (plan, research, data-model, contracts, quickstart) reflect the implemented behavior — e.g. the `_release_epoch` docstring, `apply.sh` comment, and the artifacts contract for `Pre-Depends`/`Release`/`bootstrap.sh`

**Checkpoint**: Alpha-ready — all six blockers fixed, all phases independently verified, completion checklist green.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — guards verified before any story
- **User Stories (Phase 3+ / US1-US6)**: Depend only on Setup + Foundational guards; the six phases touch **disjoint** code paths and do not build on one another (plan's explicit structure)
- **Polish (Phase 9)**: Depends on all six user stories

### User Story Dependencies

- **US1 (P1) Pre-Depends**: independent — `package.py`, `index_loader.py`, `handoff.py` Packages writers, fixture
- **US2 (P1) Resolver depth/batch**: independent — `resolver.py` only
- **US3 (P1) Diff/delivery ordering**: independent — `handoff.py` `generate_diff`, `cli/commands/sync.py`
- **US4 (P2) Linkless media**: independent — `handoff.py` `BOOTSTRAP_SCRIPT` template
- **US5 (P2) No-expiry repo**: independent — `handoff.py` `_release_epoch`/`_release_text`/`VALID_UNTIL_DAYS`
- **US6 (P2) Clean errors & green runner**: independent — `cli/commands/config.py`, `cli/commands/handoff.py`, `tests/contract/test_cli.py`, test-layer logging isolation

> **Note**: US1 and US3 both touch `handoff.py` but disjoint functions (Packages writers vs `generate_diff`); US3/US4/US5 also touch `handoff.py` in disjoint functions. Treat edits to the same file as sequential within the same story only.

### Within Each User Story

- Tests (required for this feature) MUST be written and FAIL before implementation
- Models before services; services before CLI/integration
- Core implementation before integration/validation
- Story complete and independently green before moving to the next priority

### Parallel Opportunities

- All Setup and Foundational tasks marked [P] run in parallel
- Once Foundational completes, all six user stories can start in parallel (disjoint code)
- All tests within a story marked [P] run in parallel
- US1: T011 (model) → then T012 (index_loader) and T013 (handoff) in parallel
- US3: T025 (handoff) and T026 (sync.py) in parallel
- US6: T037 (test_cli) and T038 (tests/__init__.py) in parallel

---

## Parallel Example: User Story 1

```bash
# Launch all tests for User Story 1 together (must FAIL first):
Task: "Unit test: load_binary_index captures Pre-Depends in tests/unit/test_index_loader.py"
Task: "Unit test: merged closure dedup in tests/unit/test_resolver_batch.py"
Task: "Add Pre-Depends-only fixture to tests/fixtures/signed_archive.py"
Task: "Integration test: add/download/verify/handoff Pre-Depends fixture in tests/integration/test_download_flow.py + test_handoff_flow.py"

# After the model field (T011), launch implementation together:
Task: "Merge Pre-Depends in load_binary_index (src/package_mgmt/lib/service/index_loader.py)"
Task: "Emit Pre-Depends: stanza in handoff/diff Packages writers (src/package_mgmt/lib/service/handoff.py)"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (baseline)
2. Complete Phase 2: Foundational (guards — no blocking infra, but verify before stories)
3. Complete Phase 3: User Story 1 (Pre-Depends) — the story that can make the cache genuinely **not installable**
4. **STOP and VALIDATE**: run the US1 Independent Test (offline install simulation + `verify --graph` COMPLETE)
5. Deploy/demo if ready

### Incremental Delivery (per plan's execution order)

1. Setup + Foundational → baseline locked
2. Add US1 → test independently (offline-installable closure) → checkpoint
3. Add US2 → test independently (order-independent source coverage) → checkpoint
4. Add US3 → test independently (safe diff & delivery) → checkpoint
5. Add US4 → test independently (fail-hard bootstrap) → checkpoint
6. Add US5 → test independently (no-expiry, generation-time `Date`) → checkpoint
7. Add US6 → test independently (pure JSON errors; both runners green) → checkpoint
8. Phase 9: completion checklist + both-runners gate → alpha tag

### Parallel Team Strategy

With multiple developers (phases touch disjoint code):

1. Team completes Setup + Foundational together
2. Once Foundational is done, developers take phases in parallel:
   - Developer A: US1 (Pre-Depends)
   - Developer B: US2 (resolver depth/batch)
   - Developer C: US3 (diff/delivery ordering)
   - Developer D: US4 (linkless media) or US5 (no-expiry repo)
   - Developer E: US6 (clean errors / green runner)
3. Each phase lands independently; Phase 9 gates the alpha tag

---

## Notes

- [P] tasks = different files, no dependencies (same-file edits within a story are sequential)
- [Story] label maps task to the user story in `spec.md`
- Tests required for this feature; write them red first (update-only noted for `tests/unit/test_handoff.py:176`)
- All database reads/writes go through the pre-populated schema; **no downloads during planning** (Database-First, constitution VII)
- Commit after each task or logical group; all changes land on `main`
- Verify each phase under BOTH documented runners before moving on
- The alpha tag is NOT created until Phase 9 passes
