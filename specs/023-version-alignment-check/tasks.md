---

description: "Task list for feature implementation - Version Alignment Check"

---

# Tasks: Version Alignment Check

**Input**: Design documents from `/specs/023-version-alignment-check/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Tests ARE requested — the plan mandates TDD ("tests written first; both runners + contract suite green at every phase") and quickstart.md Phase 1 asserts them. Unit, contract, and integration test tasks are therefore included and written FIRST (expected to fail) before implementation.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **CLI tool**: `src/package_mgmt/` at repository root (existing package — new code adds `cli/commands/align_repo.py`, `lib/model/alignment.py`, `lib/service/alignment.py`, `lib/service/buildmeta.py`)
- **Version manifest**: `manifests/hyprland-<era>.toml` (git-tracked; produced by `align-repo`)
- **Acquired sources**: `repos/` at project root (git-ignored)
- **Documented procedure**: `docs/005-hyprland-source-stack.md` (new)
- **Baseline (read-only)**: cache DB `binaries` (trixie/main, verified) + cached `Packages.gz` via `lib/service/index_loader.py`
- **Existing infra to reuse (no new runtime deps)**: `lib/util/versions.py` (`debian.debian_support.Version`), `lib/util/errors.py` (`_print_error`, `EXIT_ERROR`/`EXIT_CONFIG_ERROR`/`EXIT_SCHEMA_MISMATCH`), `tests/fixtures/make_test_deb.py` (fixture cache builder)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and repo hygiene for this feature

- [x] T001 Add `repos/` to the project-specific section of `.gitignore` (acquired source trees stay untracked; `manifests/` remains git-tracked so the produced manifest is committed)
- [x] T002 Verify baseline test runners are green before feature work: `pytest` and `python -m unittest discover -s tests` (baseline 808) plus `ruff check` pass

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared model + fixtures that MUST exist before ANY user story (both `align-repo` modes, all three service/CLI layers, and the test suites depend on them)

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T003 Create frozen dataclass model module `src/package_mgmt/lib/model/alignment.py` with `VersionManifest`, `ManifestRepo`, `DependencyRequirement`, `AlignmentReport`, `UnmetRequirement` per data-model.md §1/§3/§5 (frozen=True, concrete types — Constitution VI)
- [x] T004 [P] Create fixture source repos under `tests/fixtures/repos/` — small git repos each with a real `meson.build` or `CMakeLists.txt`, a `VERSION` file, and a few git tags (a common era prefix plus one newer non-aligning era) so era enumeration and both parsers are testable end-to-end; reuse the existing `tests/fixtures/make_test_deb.py` cache builder for the fixture baseline

**Checkpoint**: Foundation ready — `align-repo` implementation (US1) can begin

---

## Phase 3: User Story 1 - Determine the Version-Aligned Source Set (`align-repo`) (Priority: P1) 🎯 MVP

**Goal**: The productized admin command `package_mgmt align-repo` scans the acquired repos, finds the newest satisfying era, and produces the machine-checkable version manifest (search mode) or re-validates a given manifest against the current cache and repos (`--verify` mode) (contracts/align-repo.md, contracts/cli.md).

**Independent Test**: `package_mgmt align-repo --repo-dir <fixture repos>` exits 0 and writes a valid `manifests/hyprland-<era>.toml` on an aligning set; exits 1 naming the newest era's unmet requirements on a non-aligning set; `--verify <manifest>` exits 0 PASS / 1 FAIL against the current cache and never writes or searches. Verifiable entirely from fixture data, before any source is built.

### Tests for User Story 1 (TDD — write FIRST, confirm they FAIL before implementation) ⚠️

- [x] T005 [P] [US1] Write unit tests in `tests/unit/test_alignment.py` — era enumeration from git tags (newest-first, common era label), meson parser, cmake parser (`find_package(Name ver REQUIRED)`, `pkg_check_modules(... mod>=ver ...)`, `${VAR}` variable expansion), pkg-config→Debian mapping, `unknown_module` loud failure, Debian version semantics (boundary `>=` exact match, epoch, too-low), report aggregation, manifest write/load, exit semantics — run and confirm they FAIL
- [x] T006 [P] [US1] Write contract tests in `tests/contract/test_cli.py` (extend) — `align-repo` argument/help validation, exit codes 0/1/102/101, stable JSON output shape in search vs verify mode — run and confirm they FAIL
- [x] T007 [P] [US1] Write integration test `tests/integration/test_align_repo_flow.py` — end-to-end on the fixture cache + fixture repos: search success writes the manifest, search failure names the newest era's unmet requirements, `--verify` PASS/FAIL against the current cache — run and confirm it FAILS

### Implementation for User Story 1

- [x] T008 [US1] Implement build-metadata parsers in `src/package_mgmt/lib/service/buildmeta.py` — `parse_meson_requirements` and `parse_cmake_requirements` (line-based, no parser library per plan): `dependency('<mod>', version: '<op><ver>')`, `required:`/`get_option()`-gated optional detection, `find_package(<Name> <ver> REQUIRED)`, `pkg_check_modules(... <mod><op><ver> ...)`, `${VAR}` expansion, and `source_ref` (`repo:<relative-path>:<line>`) traceability
- [x] T009 [US1] Implement the alignment service in `src/package_mgmt/lib/service/alignment.py` — era enumeration from git tags (newest-first, common prefix), `load_baseline` (DB `binaries` `release='trixie'` `component='main'` `verified=1` cross-checked against `load_binary_index` for `Provides`/`:any`), curated pkg-config→Debian mapping table (research §5; unknown module → `unknown_module` unmet), two-tier validation (cache tier vs baseline; ecosystem tier vs provided versions from `repos/<name>/VERSION` falling back to tag), Debian version comparison via `lib/util/versions.py`, manifest writing (`manifests/hyprland-<era>.toml` per contracts/version-manifest.md), and report aggregation
- [x] T010 [US1] Implement CLI handler `handle_align_repo` in `src/package_mgmt/cli/commands/align_repo.py` — search + `--verify` modes, `--repo-dir` (default `repos/`), `--format text|json` with a `TypedDict` at the JSON output boundary (Constitution VI), exit 0/1/101/102, load errors via the shared `_print_error` convention
- [x] T011 [US1] Register the `align-repo` subcommand (admin-only) in `src/package_mgmt/cli/main.py`
- [x] T012 [US1] Make all new tests pass (T005–T007) under both `pytest` and `python -m unittest discover -s tests`; `align-repo` is now independently testable (US1 acceptance, FR-001…FR-005, FR-013 search/verify)

**Checkpoint**: User Story 1 is fully functional and testable independently (MVP complete)

---

## Phase 4: User Story 2 - Acquire the Pinned Sources into `repos/` (Priority: P1)

**Goal**: The manifest-driven, documented acquisition procedure (Q6 — documented procedure, not productized tooling) downloads each selected repo into `repos/`, pinned to the manifest commit with submodules, so no network is required to build thereafter (FR-006).

**Independent Test**: For every repo in the manifest, `git rev-parse HEAD` in `repos/<name>` equals the manifest commit; submodules are present at their recorded commits; the tree is buildable offline (US2 acceptance, quickstart Phase 3). Authenticity is SHA-pinning/TOFU only (Q10).

- [x] T013 [US2] Write the acquisition section of `docs/005-hyprland-source-stack.md` — per-repo clone into `repos/`, checkout of the manifest commit, submodule capture at recorded commits, TOFU SHA-pinning note (Q10), and the offline-after-acquisition guarantee (FR-006); the procedure is driven by the manifest produced by `align-repo` (US1)
- [x] T014 [US2] Execute the documented acquisition against the real manifest and verify integrity: every `repos/<name>` `HEAD` equals the manifest SHA and submodules are pinned (quickstart Phase 3); a mismatched tree is reported and refused

**Checkpoint**: `repos/` is complete and self-contained — the build (US3) can proceed offline

---

## Phase 5: User Story 3 - Build the Core Stack from the Cache (Priority: P1)

**Goal**: The documented bottom-up build procedure (Q6) compiles the full stack against the cache as the sole package/source provider, network disabled, at the cached versions (FR-007/008/009).

**Independent Test**: With the network disabled and the cache as the only package/source provider, following `docs/005` builds all 12 components bottom-up with zero failures; a clean-state repeat produces identical artifacts (US3 acceptance, quickstart Phase 4, SC-002).

- [x] T015 [US3] Write the bottom-up build section of `docs/005-hyprland-source-stack.md` — the FR-008 order (hyprland-protocols → hyprwayland-scanner → hyprgraphics → hyprutils → hyprlang → aquamarine → hyprcursor → Hyprland → hypridle/hyprlock/hyprpaper/xdg-desktop-portal-hyprland), fully offline with the cache as sole provider and cached versions only (FR-007); any build tool/helper missing from the cache is added to the cache and recorded in the dependency graph before building (FR-014)
- [x] T016 [US3] Document build reproducibility in `docs/005` — pinned commits, recorded build environment, and a clean-state rebuild check producing identical artifacts (FR-009)

**Checkpoint**: The stack compiles offline from the frozen tree — alignment (US1) proven correct

---

## Phase 6: User Story 4 - Reach a Functional Hyprland Setup (Priority: P2)

**Goal**: In-place installation from the cache (Q1) reaches a functional Hyprland session, validated by the in-project scripted smoke test (Q9), repeatable on a fresh target (FR-010/011).

**Independent Test**: The scripted smoke test starts the session offline, asserts the compositor and the essential companions (hyprpaper, hyprlock, hypridle, xdg-desktop-portal-hyprland) are running, and exits pass/fail; the same procedure on a fresh target yields the same result (US4 acceptance, quickstart Phase 5, SC-003).

- [x] T017 [US4] Create the scripted functional-session smoke test `scripts/validate-hyprland-session.sh` — starts the session offline on a target VT/display, asserts the compositor and the essential companion processes (per Q2) are running, and exits pass/fail (Q9)
- [x] T018 [US4] Write the functional-setup section of `docs/005-hyprland-source-stack.md` — in-place install from the cache (Q1), session launch, smoke-test usage, and the fresh-target repeatability procedure (FR-010/011)

**Checkpoint**: A functional, validated Hyprland session runs offline using only cached packages

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Improvements and validation affecting multiple user stories

- [x] T019 Review `docs/005-hyprland-source-stack.md` end-to-end for coherence (FR-012, Constitution VIII): version manifest → acquisition → build → functional setup → validation read as one reproducible workflow a future Administrator/Operator can follow without external lookup
- [x] T020 [P] Full regression: `pytest` and `python -m unittest discover -s tests` green (baseline 808 + new), `ruff check` clean, and `tests/contract/test_cli.py` passes with existing cases unchanged
- [x] T021 Run `quickstart.md` validation end-to-end (Phases 1–5) and confirm every expected outcome

---

## Out of Scope (recorded, not implemented)

- **User Story 5 — Package into the cache `local` component**: explicit follow-up (US5/FR-011), NOT part of this feature. No tasks here; the requirement is preserved in spec.md so the follow-up does not lose it.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS US1
- **User Stories**: US1 (Phase 3) depends on Foundational. US2/3/4 (Phases 4–6) are documented procedures driven by the US1 manifest and can be authored in parallel once the manifest concept is fixed, but their execution/validation depends on US1's output and each other (acquisition → build → functional setup)
- **Polish (Phase 7)**: Depends on all user stories

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational — no dependencies on other stories (MVP)
- **User Story 2 (P1)**: Depends on US1 (manifest produced by `align-repo`) — document procedure, verify against real repos
- **User Story 3 (P1)**: Depends on US2 (repos/ acquired) and the US1 manifest
- **User Story 4 (P2)**: Depends on US3 (built stack) — plus the US1 manifest for companion scope
- **User Story 5 (P3)**: Follow-up, out of scope

### Within Each User Story

- US1: tests (T005–T007) written and FAILING before implementation (T008–T011); models/services before CLI; CLI registered before tests go green
- US2/3/4: documentation procedure first, then execution/validation of that procedure

### Parallel Opportunities

- T003/T004 (Foundational) can run in parallel
- T005/T006/T007 (US1 test files) can run in parallel — different files, all expected to FAIL first
- US2/US3/US4 documentation tasks (T013, T015/T016, T018) can be authored in parallel once the manifest shape is settled
- T020 (regression) can run alongside final doc review

---

## Parallel Example: User Story 1

```bash
# Launch all US1 test tasks together (TDD — each expected to fail first):
Task: "Write unit tests in tests/unit/test_alignment.py"
Task: "Write contract tests in tests/contract/test_cli.py"
Task: "Write integration test tests/integration/test_align_repo_flow.py"

# After T008–T011 (implementation) is complete:
Task: "Make all new tests pass under both runners"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — model + fixtures block everything)
3. Complete Phase 3: User Story 1
   - **Error Handling Check**: load errors mapped to exit 102 (`EXIT_CONFIG_ERROR`), stale/missing DB to 101 (`EXIT_SCHEMA_MISMATCH`), alignment failures to 1 (`EXIT_ERROR`) via the shared `_print_error` convention
   - **Logging Check**: selective structured logging only (project convention — do not replace print() output)
4. **STOP and VALIDATE**: `align-repo` search + `--verify` pass on fixtures; no user story yet depends on a real era being pinned
5. Deploy/demo if ready

### Incremental Delivery

1. Complete Setup + Foundational → foundation ready
2. Add User Story 1 → test independently → Deploy/Demo (**MVP!** — the productized tool and the manifest are done)
3. Add User Story 2 (acquisition doc + real repos) → validate `repos/` HEAD == manifest → Deploy/Demo
4. Add User Story 3 (build doc) → offline build from cache → Deploy/Demo
5. Add User Story 4 (functional setup + scripted smoke test) → validated offline session → Deploy/Demo
6. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: US1 (tests + model + services + CLI)
   - Developer B (can start after manifest shape is fixed): US2 acquisition doc
   - Developer C (after US2): US3 build doc, then US4 functional setup + smoke test
3. Stories complete and integrate independently; US1 remains the only productized code

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- Verify tests fail before implementing (TDD, US1)
- Commit after each task or logical group
- Stop at any checkpoint to validate the story independently
- Avoid: vague tasks, same-file conflicts, cross-story dependencies that break independence
- Constitution gates to re-check on every phase: schema freeze (no DB changes, Q5), no new runtime dependencies, both test runners + contract suite green, `docs/005` current (Constitution VIII), scoped to one implementable increment (Constitution IX)
