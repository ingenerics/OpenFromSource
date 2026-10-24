---

description: "Task list template for feature implementation"

---

# Tasks: [FEATURE NAME]

**Input**: Design documents from `/specs/[###-feature-name]/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: The examples below include test tasks. Tests are OPTIONAL - only include them if explicitly requested in the feature specification.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **CLI tool**: `src/` at repository root for the Python tooling
- **Cache directory**: admin-managed package cache with apt repository layout: `cache/pool/`, `cache/dists/`
- **Metadata**: `cache/package_mgmt.db` for local index
- Paths shown below assume single project - adjust based on plan.md structure

<!--
  ============================================================================
  IMPORTANT: The tasks below are SAMPLE TASKS for illustration purposes only.

  The /speckit.tasks command MUST replace these with actual tasks based on:
  - User stories from spec.md (with their priorities P1, P2, P3...)
  - Feature requirements from plan.md
  - Entities from data-model.md
  - Endpoints from contracts/

  Tasks MUST be organized by user story so each story can be:
  - Implemented independently
  - Tested independently
  - Delivered as an MVP increment

  DO NOT keep these sample tasks in the generated tasks.md file.
  ============================================================================
-->

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [ ] T001 Create project structure per implementation plan (`src/cli/`, `src/lib/`, `tests/`)
- [ ] T002 Initialize Python project with packaging dependencies (`python-apt`, `python-debian`, `cryptography`)
- [ ] T003 [P] Configure linting and formatting tools (ruff, mypy)
- [ ] T004 [P] Create basic CLI scaffolding (argparse with subcommand stubs: `add`, `remove`, `list`, `verify`, `sync`, `bootstrap`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

**⚠️ ERROR HANDLING & LOGGING**: Initialize error handling infrastructure and define logging strategy BEFORE user story work begins. See `docs/00x_error_handling.md` for:

- Exception hierarchy (base class + command-specific exceptions)
- Exit codes (0, 1, 100, 101-199)
- JSON error reporting format
- Selective logging strategy (DO NOT replace all print() calls)

Examples of foundational tasks (adjust based on your project):

- [ ] T005 Design and create SQLite schema (`packages`, `binaries`, `sources`, `binary_to_source`) in `src/lib/model/database.py`
- [ ] T006 [P] Implement database populator — creates all records from a package spec BEFORE any download starts; sets `verified` field
- [ ] T007 [P] Implement validation query — verify all records are `verified=true` before proceeding to download
- [ ] T008 Setup apt repository layout in the cache directory (`pool/`, `dists/`, `Release`, `Release.gpg`)
- [ ] T009 [P] Implement `Packages.gz` / `Sources.gz` metadata indexing (parse and write apt indices)
- [ ] T010 [P] Implement GPG signature verification for `Release` files
- [ ] T011 [P] Implement SHA256 checksum verification for downloaded packages
- [ ] T012 Create metadata cache and local package index (SQLite-driven)
- [ ] T013 [P] Configure error handling (exception hierarchy: `PackageMgmtError`, `SyncError`, `BootstrapError`, `BuildError`, `DatabaseError`)
  - **Reference**: See `docs/00x_error_handling.md`
- [ ] T014 Configure logging infrastructure (selective structured JSON logging)
  - **Guideline**: Log only for debugging-critical operations; do not replace all print() with logging
  - **Reference**: See `docs/00x_error_handling.md`
- [ ] T015 Setup runtime configuration management (cache directory path, database path, apt sources config, GPG keyring path)

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - [Title] (Priority: P1) 🎯 MVP

**Goal**: [Brief description of what this story delivers]

**Independent Test**: [How to verify this story works on its own]

### Tests for User Story 1 (OPTIONAL - only if tests requested) ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T016 [P] [US1] Contract test for [CLI subcommand / library API] in tests/contract/test_[name].py
- [ ] T017 [P] [US1] Integration test for [user journey] in tests/integration/test_[name].py

### Implementation for User Story 1

- [ ] T018 [P] [US1] Create [Entity1] model in src/lib/model/[entity1].py
- [ ] T019 [P] [US1] Create [Entity2] model in src/lib/model/[entity2].py
- [ ] T020 [US1] Populate database tables (packages, binaries, sources, binary_to_source) upfront from spec
- [ ] T021 [US1] Implement database validation — verify all records have `verified=true` before download
- [ ] T022 [US1] Implement [Service] in src/lib/service/[service].py (depends on T018, T019)
- [ ] T023 [US1] Implement [CLI command / feature] in src/cli/[location]/[file].py
- [ ] T024 [US1] Add validation and error handling
  - **Required**: Use `_print_error()` for all user-facing errors (JSON format)
  - **Required**: Use custom exceptions from infrastructure; map to appropriate exit codes
  - **Reference**: See `docs/00x_error_handling.md`
- [ ] T025 [US1] Add logging for user story 1 operations
  - **Guideline**: Log selectively - only for debugging-critical operations at operation boundaries
  - **Note**: Do NOT replace print() calls with logging; keep user-facing output via print()
  - **Reference**: See `docs/00x_error_handling.md`

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - [Title] (Priority: P2)

**Goal**: [Brief description of what this story delivers]

**Independent Test**: [How to verify this story works on its own]

### Tests for User Story 2 (OPTIONAL - only if tests requested) ⚠️

- [ ] T026 [P] [US2] Contract test for [CLI subcommand / library API] in tests/contract/test_[name].py
- [ ] T027 [P] [US2] Integration test for [user journey] in tests/integration/test_[name].py

### Implementation for User Story 2

- [ ] T028 [P] [US2] Create [Entity] model in src/lib/model/[entity].py
- [ ] T029 [US2] Implement [Service] in src/lib/service/[service].py
- [ ] T030 [US2] Implement [CLI command / feature] in src/cli/[location]/[file].py
- [ ] T031 [US2] Integrate with User Story 1 components (if needed)
  - **Required**: Follow error handling pattern established in US1 (custom exceptions, exit codes)
  - **Required**: Follow logging strategy (selective structured logging only)
  - **Required**: Operate against the pre-populated database, not the filesystem

**Checkpoint**: At this point, User Stories 1 AND 2 should both work independently

---

## Phase 5: User Story 3 - [Title] (Priority: P3)

**Goal**: [Brief description of what this story delivers]

**Independent Test**: [How to verify this story works on its own]

### Tests for User Story 3 (OPTIONAL - only if tests requested) ⚠️

- [ ] T032 [P] [US3] Contract test for [CLI subcommand / library API] in tests/contract/test_[name].py
- [ ] T033 [P] [US3] Integration test for [user journey] in tests/integration/test_[name].py

### Implementation for User Story 3

- [ ] T034 [P] [US3] Create [Entity] model in src/lib/model/[entity].py
- [ ] T035 [US3] Implement [Service] in src/lib/service/[service].py
- [ ] T036 [US3] Implement [CLI command / feature] in src/cli/[location]/[file].py
  - **Required**: Use `_print_error()` for errors; define custom exceptions if needed
  - **Required**: Follow logging strategy (selective structured logging only)
  - **Required**: All database reads come from pre-populated tables; no downloads during planning
  - **Reference**: See `docs/00x_error_handling.md`

**Checkpoint**: All user stories should now be independently functional

---

[Add more user story phases as needed, following the same pattern]

---

## Phase N: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] TXXX [P] Documentation updates in docs/
- [ ] TXXX Code cleanup and refactoring
  - **Check**: Error handling consistency (custom exceptions, exit codes, JSON error reporting)
  - **Check**: Logging strategy (selective structured logging, not replacing print())
- [ ] TXXX Performance optimization across all stories
- [ ] TXXX [P] Additional unit tests (if requested) in tests/unit/
- [ ] TXXX Security hardening
- [ ] TXXX Run quickstart.md validation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
  - User stories can then proceed in parallel (if staffed)
  - Or sequentially in priority order (P1 → P2 → P3)
- **Polish (Final Phase)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P2)**: Can start after Foundational (Phase 2) - May integrate with US1 but should be independently testable
- **User Story 3 (P3)**: Can start after Foundational (Phase 2) - May integrate with US1/US2 but should be independently testable

### Within Each User Story

- Tests (if included) MUST be written and FAIL before implementation
- Models before services
- Services before CLI commands
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- All Foundational tasks marked [P] can run in parallel (within Phase 2)
- Once Foundational phase completes, all user stories can start in parallel (if team capacity allows)
- All tests for a user story marked [P] can run in parallel
- Models within a story marked [P] can run in parallel
- Different user stories can be worked on in parallel by different team members

---

## Parallel Example: User Story 1

```bash
# Launch all tests for User Story 1 together (if tests requested):
Task: "Contract test for [CLI subcommand / library API] in tests/contract/test_[name].py"
Task: "Integration test for [user journey] in tests/integration/test_[name].py"

# Launch all models for User Story 1 together:
Task: "Create [Entity1] model in src/lib/model/[entity1].py"
Task: "Create [Entity2] model in src/lib/model/[entity2].py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1
   - **Error Handling Check**: Custom exceptions defined, exit codes mapped, JSON error reporting
   - **Logging Check**: Selective structured logging, not replacing print()
4. **STOP and VALIDATE**: Test User Story 1 independently
5. Deploy/demo if ready

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → Deploy/Demo (MVP!)
   - **Error Handling Check**: Custom exceptions defined, exit codes mapped, JSON error reporting
   - **Logging Check**: Selective structured logging, not replacing print()
3. Add User Story 2 → Test independently → Deploy/Demo
   - **Error Handling Check**: Custom exceptions defined, exit codes mapped, JSON error reporting
   - **Logging Check**: Selective structured logging, not replacing print()
4. Add User Story 3 → Test independently → Deploy/Demo
   - **Error Handling Check**: Custom exceptions defined, exit codes mapped, JSON error reporting
   - **Logging Check**: Selective structured logging, not replacing print()
5. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1
   - Developer B: User Story 2
   - Developer C: User Story 3
3. Stories complete and integrate independently

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Verify tests fail before implementing
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
- Cache handoff flow: admin builds → verify → ship USB → operator verifies → installs
