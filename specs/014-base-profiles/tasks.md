# Tasks: 014 — Base Profiles

**Input**: Design documents from `/specs/014-base-profiles/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/cli.md

**Tests**: The mandatory **regression test matrix** (plan.md §"Mandatory regression test matrix", spec SC-006) is explicitly requested by the user and the plan — test tasks are therefore REQUIRED and are listed FIRST within each user-story phase (TDD: write/run them failing before implementation).

**Organization**: Tasks are grouped by user story so each story can be implemented and tested independently.

## Format: `- [ ] [ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3). Setup/Foundational/Polish phases carry no story label.
- Include exact file paths in descriptions.

## Path Conventions

- CLI tooling: `src/package_mgmt/` at repository root
- Project settings: `project.defaults.toml` (defaults) + `project.local.toml` (overlay)
- Profile presets (new, data-only): `docs/profiles/*.toml`
- Tests: `tests/unit/`, `tests/integration/`, `tests/contract/`
- Roles doc: `.specify/memory/roles.md`

## Depth-Value Convention (used throughout)

Both `max_source_dep_depth` and `max_build_dep_report_depth` use: `-1` = unlimited (report all), `0` = none (nothing fetched/reported), positive `N` = up to `N` levels. `ProjectConfig` dataclass defaults match the shipped regular_user presets exactly: `fetch_source = False`, `max_source_dep_depth = 0` (one default story; `-1` is an explicit opt-in). No DB schema changes (spec 010/011 freeze).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Baseline and orientation on the existing codebase (incremental feature on an existing CLI)

- [X] T001 Run the baseline `pytest` suite (`python -m pytest`) and record the pre-change pass count as a regression snapshot (tests/) — baseline 458 passed, 0 failed; recorded in tests/REGRESSION.md
- [X] T002 [P] Inspect the exact entry points this feature extends and confirm field/key names: `ProjectConfig` (src/package_mgmt/lib/model/config.py:27-28), `_KEY_MAP` (src/package_mgmt/lib/service/config_loader.py:10-20), `handle_verify_graph` + `_find_unresolved_deps` (src/package_mgmt/cli/commands/verify.py:12,73), resolver source-record creation (src/package_mgmt/lib/service/resolver.py:744), `main.py` verify wiring (src/package_mgmt/cli/main.py:210)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The config field, its validation, and its display — required by ALL user stories. MUST complete before any story.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 Extend the `ProjectConfig` dataclass in src/package_mgmt/lib/model/config.py: add `max_source_dep_depth: int = 0`; change `fetch_source`'s default to `False` so dataclass defaults match the shipped regular_user presets (spec Assumptions "Defaults")
- [X] T004 [P] Add `("resolution", "max_source_dep_depth")` to `_KEY_MAP` in src/package_mgmt/lib/service/config_loader.py and validate BOTH depth settings as integer `>= -1` (value below `-1`, `1.5`, or `true` → `ConfigError` naming the key, `EXIT_CONFIG_ERROR` 102, before any command runs); record the key in `overridden_keys` for the `config` command (FR-005)
- [X] T005 [P] Fix existing tests broken by the `fetch_source` default flip: tests/contract/test_cli.py `test_json_format_structure` (its TOML omits `[fetch] source`; add `[fetch] source = true` to the fixture or assert `False`) and tests/unit/test_ensure_base.py `_scratch_config` (construct `ProjectConfig(...)` with `fetch_source=True`)
- [X] T006 [P] Show `max_source_dep_depth` in `package_mgmt config` (src/package_mgmt/cli/commands/config.py): text line `max_source_dep_depth: 0 (default|override...)` and JSON `resolution.max_source_dep_depth` + `max_source_dep_depth_override`, mirroring the existing `max_build_dep_report_depth` convention (FR-010)
- [X] T007 [P] Unit tests in tests/unit/test_config.py: dataclass defaults match shipped presets (`fetch_source == False`, `max_source_dep_depth == 0`); `-2`, `1.5`, `true` → `ConfigError` for BOTH `max_source_dep_depth` and `max_build_dep_report_depth`; absent key → default (FR-002/FR-005/FR-017)
- [X] T008 [P] Contract tests in tests/contract/test_cli.py: `config` text shows `max_source_dep_depth` and `fetch_source: false (default)`; JSON `resolution.max_source_dep_depth` + `_override`; invalid depth → JSON error `{"error": "...", "exit_code": 102}` (FR-010/FR-005)

**Checkpoint**: Foundation ready — `ProjectConfig` carries both depth settings with validation, `config` displays them, and the suite is green (with fixed tests).

---

## Phase 3: User Story 1 - Depth-Limited Source Support (Priority: P1) 🎯 MVP

**Goal**: `max_source_dep_depth` enforced during resolution — sources and build tools are provided only for binaries within the configured depth, the runtime binary closure is ALWAYS complete, and the depth-0 warning fires when source fetching is enabled (FR-003/FR-004/FR-016).

**Independent Test**: With `max_source_dep_depth = 1` and `[fetch] source = true`, `add` a small package → the requested binary + full runtime closure are resolved; sources and build tools are present for the requested set and its direct runtime deps only; no source beyond depth 1 is registered. With `-1`, sources/build tools exist for the entire closure (pre-feature behavior).

### Tests for User Story 1 (REQUIRED — regression matrix Setting A + resolver invariants) ⚠️

> **NOTE: Write these FIRST, ensure they FAIL before implementation**

- [X] T009 [P] [US1] Unit test in tests/unit/test_resolver_batch.py: per-binary level accounting — source records created only for binaries within `max_source_dep_depth`; the runtime `binaries` closure is byte-identical across `-1`/`0`/`1`/`N` (FR-003)
- [X] T010 [P] [US1] Unit test in tests/unit/test_resolver_build_deps.py: the source/build-tool set at a finite depth is a strict subset of the `-1` set; `-1` equals pre-feature full-source behavior (FR-011)
- [X] T011 [P] [US1] Unit test in tests/unit/test_resolver_build_deps.py: a boundary build tool (one level beyond the source it serves) is resolved as an installable binary with its full runtime closure but gets NO source record of its own (FR-013/FR-003, Constitution III scoped exception)
- [X] T012 [P] [US1] Regression matrix Setting A (SC-006) as parametrized unit tests in tests/unit/test_resolver_batch.py — rows: unset (defaults → binaries only, no warning); `-1`+true (full closure == pre-feature); `-1`+false (binaries only); `0`+true (nothing fetched, warning, exit 0); `0`+false (binaries only, no warning); `1`+true; `N`+true (strict subset, every source buildable)
- [X] T013 [P] [US1] Unit test for the depth-0 warning (FR-016): `[fetch] source = true` + `max_source_dep_depth = 0` → warning printed exactly once and command still exits 0; `source = false` → no warning
- [X] T014 [P] [US1] Integration test in tests/integration/test_add_file_flow.py: `add` a small package under a finite depth against the local HTTP mirror fixture → resolved source/build-tool set matches the depth; runtime closure identical to an unlimited run

### Implementation for User Story 1

- [X] T015 [US1] Add per-binary **level** accounting (requested set = 0, runtime dependency = parent + 1) to the runtime pass (`_resolve_one` / `_resolve_one_inner`) in src/package_mgmt/lib/service/resolver.py
- [X] T016 [US1] Gate source-record creation (`upsert_source` + `link_binary_to_source`, resolver.py:744) on `level <= max_source_dep_depth` (or `-1` = unlimited); binaries beyond the depth are still registered but get no source record and no build-tool links — no error (FR-004)
- [X] T017 [US1] Ensure the build-tool pass (`_resolve_build_deps`, resolver.py:392) processes only the in-scope sources so every fetched source has its build tools (FR-013) and boundary build tools resolve as binaries without their own sources
- [X] T018 [US1] Emit the depth-0 warning in `add` (src/package_mgmt/cli/commands/add.py) and `ensure-base` (src/package_mgmt/cli/commands/ensure_base.py) when `fetch_source = true` and `max_source_dep_depth = 0`; the command still exits 0 (FR-016)
- [X] T019 [US1] Confirm `ensure-base` enforces the same depth via the shared `resolve_batch` path (FR-004, no separate logic) and add an integration test in tests/integration/test_ensure_base_flow.py proving the base set respects a finite depth

**Checkpoint**: US1 fully functional and independently testable — the depth knob bounds source/build-tool support; the runtime closure is never truncated.

---

## Phase 4: User Story 2 - Three Showcase Profile Settings (Priority: P1)

**Goal**: Ship three complete, standalone project-setting TOML files (`regular_user`, `developer`, `graybeard`) under `docs/profiles/` as data files only, plus the regular_user presets in `project.defaults.toml` (FR-006/FR-007/FR-008/FR-009, SC-003).

**Independent Test**: Take each profile file in turn as the project setting, `add` the same package → the cache shape matches the profile (regular_user: no sources/build tools; developer: sources + build tools within depth, full runtime closure; graybeard: full recursion).

### Tests for User Story 2 (REQUIRED) ⚠️

> **NOTE: Write these FIRST, ensure they FAIL before the profile files exist**

- [X] T020 [P] [US2] Integration test in tests/integration/test_add_file_flow.py: each profile file used as `project.defaults.toml` → `add` the same package → resolved binary/source/build-tool set matches the profile and the runtime closure is identical (US2 AS1-AS3)
- [X] T021 [P] [US2] Integration test in tests/integration/test_download_flow.py: `download` under the `regular_user` profile → source phase skipped, `Sources: 0`, exit 0
- [X] T022 [P] [US2] Contract test in tests/contract/test_cli.py: each profile file loads as a valid, complete configuration and the applied settings (incl. both depth settings) appear in `package_mgmt config` text + JSON (US2 AS4, FR-010)
- [X] T023 [P] [US2] Unit test: the three profile files are plain TOML data (no code, no classes) under `docs/profiles/` (US2 AS5)

### Implementation for User Story 2

- [X] T024 [P] [US2] Create docs/profiles/regular_user.toml — complete standalone project setting (paths, database, logging, archive, fetch, resolution); `[fetch] source = false`, `[resolution] max_source_dep_depth = 0`; inline comments describing the intended use (FR-006/FR-007)
- [X] T025 [P] [US2] Create docs/profiles/developer.toml — `[fetch] source = true`, `[resolution] max_source_dep_depth = 1`; inline comments (FR-006/FR-008)
- [X] T026 [P] [US2] Create docs/profiles/graybeard.toml — `[fetch] source = true`, `[resolution] max_source_dep_depth = -1` (unlimited); inline comments; the `ensure-base` profile (FR-006/FR-009)
- [X] T027 [P] [US2] Update project.defaults.toml to the regular_user presets: `[fetch] source = false`, `[resolution] max_source_dep_depth = 0`; update the `max_build_dep_report_depth` comment to the `-1`/`0`/`N` convention (SC-003)
- [X] T028 [US2] Document each profile's intended use and the grow-deeper direction (regular_user → developer → graybeard) inline in the profile files, and add a short note to .specify/memory/roles.md that profiles are admin-side config presets (FR-012)

**Checkpoint**: US2 complete — the three presets are drop-in `project.defaults.toml` replacements and produce their intended cache shapes.

---

## Phase 5: User Story 3 - Profile Shape Is Verifiable and Sizeable (Priority: P2)

**Goal**: The profile's intended cache shape is observable: `verify --graph` reports COMPLETE for a correctly-scoped finite-depth cache (unlinked/out-of-scope binaries are informational-only, never defects), `max_build_dep_report_depth` follows the `-1`/`0`/`N` convention without regressions, and the developer source set is a strict subset of graybeard's with an identical runtime closure (FR-015/FR-017, SC-004/SC-006). No schema change.

**Independent Test**: Add the same package under developer (finite) and graybeard (`-1`); compare resolved sets and `verify --graph` — developer sources/build tools ⊂ graybeard, runtime closure identical, both COMPLETE; `max_build_dep_report_depth` at `-1`/`0`/`2` behaves per convention.

### Tests for User Story 3 (REQUIRED — regression matrix Setting B + Setting C + depth gate) ⚠️

> **NOTE: Write these FIRST, ensure they FAIL before implementation**

- [X] T029 [P] [US3] Regression matrix Setting B (SC-006/FR-017) as parametrized unit tests in tests/unit/test_verify.py — rows: unset (default `2`); `-1` (report all == prior "`0` = report all", no regression); `0` (no build-tool levels reported, only the depth-0 runtime check); `2`; `N`
- [X] T030 [P] [US3] Unit test in tests/unit/test_verify.py: verify depth gate (FR-015) — at a finite `max_source_dep_depth`, binaries without a `binary_to_source` link are informational-only (never defects) and the cache reports COMPLETE; at `-1` the strict unlinked check applies unchanged (no regression)
- [X] T031 [P] [US3] Regression matrix Setting C (SC-006) in tests/contract/test_cli.py: `max_source_dep_depth = 0` + `max_build_dep_report_depth = -1`; `-1` + `0`; invalid values `-2`/`1.5`/`true` for BOTH settings → `ConfigError`, exit 102
- [X] T032 [P] [US3] Integration test in tests/integration/test_add_file_flow.py: same package under developer vs graybeard → developer sources/build tools strictly subset of graybeard, runtime closure identical, both verify COMPLETE (SC-004)

### Implementation for User Story 3

- [X] T033 [US3] Pass the configured `max_source_dep_depth` into `handle_verify_graph` from src/package_mgmt/cli/main.py (alongside the existing `max_build_dep_report_depth` at main.py:210)
- [X] T034 [US3] Rework `_find_unresolved_deps` depth semantics in src/package_mgmt/cli/commands/verify.py:73-96 — `-1` = check all (the prior `0` behavior, no regression), `0` = only the always-on depth-0 runtime check, positive `N` = up to `N` (FR-017)
- [X] T035 [US3] Implement the FR-015 depth gate in src/package_mgmt/cli/commands/verify.py: at a finite `max_source_dep_depth`, exclude unlinked binaries from `has_issues`/`runtime_status` and surface them as an informational "N binaries beyond source depth or missing" count; at `-1` keep the strict unlinked check. No schema change.

**Checkpoint**: US3 complete — the profile's cache shape is verifiable, the report-depth convention is regression-safe at both `0` and `-1`, and the in-scope cache reports COMPLETE.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements and validations affecting multiple user stories.

- [X] T036 [P] Run the full mandatory regression test matrix (SC-006) and confirm every row in plan.md's Setting A/B/C tables passes
- [X] T037 [P] Run quickstart.md validation scenarios end-to-end (Scenarios 1-5 and Scenario 3b special-value matrix)
- [X] T038 [P] Document the version-drift rationale (why runtime binaries are never capped — Constitution II) and the retention/grow-deeper guidance (FR-012/FR-014) in the profile files / a short docs note
- [X] T039 [P] Error-handling & logging consistency check on all new code (depth-0 warning, `ConfigError` exit-102 path, verify gate): use `_print_error`/existing exit codes; do not replace print() with logging
- [X] T040 Run the full `pytest` suite and confirm green with no regressions (including the fixed test_cli / test_ensure_base tests)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories (config field + validation + display)
- **User Stories (Phase 3+)**: All depend on Foundational completion
  - US1 (engine) and US2 (data files) can proceed in parallel once Foundational is done; US2's end-to-end tests depend on US1's resolver behavior
  - US3 depends on US1 (resolver produces the scoped cache the gate reads) and Foundational (config plumbing)
- **Polish (Final Phase)**: Depends on all desired user stories being complete

### User Story Dependencies

- **US1 (P1, MVP)**: After Foundational — no dependencies on other stories
- **US2 (P1)**: After Foundational — profile files are independent of US1; their integration/contract tests exercise US1's resolver, so run those after US1
- **US3 (P2)**: After Foundational + US1 — reads the depth from config and the scoped cache from the resolver

### Within Each User Story

- Tests FIRST (fail), then implementation (models/services before CLI wiring)
- US1: level accounting (T015) → source gating (T016) → build-tool scoping (T017) → warning (T018) → ensure-base verification (T019)
- US3: `_find_unresolved_deps` semantics (T034) and depth gate (T035) both depend on T033 (wiring the config value through main.py)

### Parallel Opportunities

- Setup: T001 and T002 are independent
- Foundational: T004-T008 are parallel after T003 adds the field
- US1 tests T009-T014: all parallel (different files / distinct assertions)
- US1 implementation: T015→T016→T017 sequential; T018 independent; T019 after
- US2: T024-T027 parallel (different files); T028 after the files exist
- US3 tests T029-T032: all parallel; implementation T033→T034/T035
- Polish: T036-T039 parallel; T040 last

---

## Parallel Example: User Story 1 (MVP)

```bash
# Launch all US1 tests together (write failing first):
Task: "Regression matrix Setting A (SC-006) in tests/unit/test_resolver_batch.py"
Task: "Resolver level-accounting invariants in tests/unit/test_resolver_batch.py"
Task: "Strict-subset + backward-compat at -1 in tests/unit/test_resolver_build_deps.py"
Task: "Boundary build-tool no-own-source in tests/unit/test_resolver_build_deps.py"
Task: "Depth-0 warning (FR-016) unit test"
Task: "Finite-depth add integration test in tests/integration/test_add_file_flow.py"

# Then implement sequentially:
Task: "Level accounting in src/package_mgmt/lib/service/resolver.py"
Task: "Source-record gating in src/package_mgmt/lib/service/resolver.py"
Task: "Build-tool scoping in src/package_mgmt/lib/service/resolver.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (baseline snapshot)
2. Complete Phase 2: Foundational (config field + validation + display; fix the two fetch_source-default tests)
3. Complete Phase 3: User Story 1 — depth-limited source support + Setting A regression matrix
   - **Error Handling Check**: `ConfigError` → exit 102; depth-0 warning → printed, command still exits 0
4. **STOP and VALIDATE**: US1 independently testable; deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → foundation ready (config carries both depth settings)
2. US1 → Setting A regression matrix green → MVP
3. US2 → profile files + project.defaults.toml → Setting A/B coverage via profiles
4. US3 → verify depth gate + Setting B/C regression matrix → SC-006 complete
5. Polish → quickstart + full matrix + docs

### Parallel Team Strategy

- Team completes Setup + Foundational together
- Once Foundational is done:
  - Developer A: US1 (engine — critical path)
  - Developer B: US2 (profile files — data-only, parallelizable)
- After US1: Developer A: US3 (verify gate + Setting B/C)

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps the task to its user story for traceability
- The mandatory regression matrix (SC-006) is the acceptance proof that both depth settings behave correctly at `0` and `-1` without regressions — do not merge without it green
- The `fetch_source` dataclass default flips to `False` (Foundational T003): existing tests relying on the old `True` default are fixed in T005
- No DB schema changes anywhere in this feature (spec 010/011 freeze) — FR-015 is implemented config-only
- Commit after each task or logical group; stop at any checkpoint to validate the story independently
- Cache handoff flow is unaffected: admin builds (with the chosen profile) → verify → ship USB → operator installs
