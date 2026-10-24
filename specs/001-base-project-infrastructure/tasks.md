---

description: "Task list for base project infrastructure"

---

# Tasks: Base Project Infrastructure

**Input**: Design documents from `specs/001-base-project-infrastructure/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/cli.md

**Tests**: Unit tests for each user story, contract tests for CLI output, and integration tests for the full init flow. See plan.md for test file layout.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **CLI tool**: `src/package_mgmt/` — Python package at repository root
- **Package name**: `package_mgmt` (used for imports and console_scripts entry point)
- **Config files**: `project.defaults.toml` (version-controlled), `project.local.toml` (git-ignored)
- **Cache directories**: `{base_dir}/binary/` and `{base_dir}/source/` each with Debian apt layout

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [X] T001 Create project directory structure (`src/package_mgmt/cli/commands/`, `src/package_mgmt/lib/model/`, `src/package_mgmt/lib/service/`, `src/package_mgmt/lib/util/`, `tests/unit/`, `tests/contract/`, `tests/integration/`)
- [X] T002 Create `pyproject.toml` with build config and `console_scripts` entry point: `package_mgmt = "package_mgmt.cli:main"`
- [X] T003 [P] Create `project.defaults.toml` with default config keys per research.md (paths, database, logging)
- [X] T004 [P] Create `.gitignore` with entries for `project.local.toml`, `__pycache__/`, `*.pyc`, `.venv/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

**⚠️ ERROR HANDLING & LOGGING**: Initialize error handling infrastructure and define logging strategy BEFORE user story work begins.

- [X] T005 Create base exception `PackageMgmtError` in `src/package_mgmt/lib/util/errors.py`
- [X] T006 [P] Create command-specific exceptions: `ConfigError`, `InitError`, `DatabaseError` in `src/package_mgmt/lib/util/errors.py`
- [X] T007 [P] Implement `_print_error()` helper for JSON-formatted error output to stderr in `src/package_mgmt/lib/util/errors.py`
- [X] T008 [P] Define exit code constants (`EXIT_SUCCESS=0`, `EXIT_ERROR=1`, `EXIT_PERMISSION=100`, `EXIT_SCHEMA_MISMATCH=101`, `EXIT_CONFIG_ERROR=102`) in `src/package_mgmt/lib/util/errors.py`
- [X] T009 [P] Create path resolution utilities in `src/package_mgmt/lib/util/paths.py` — helpers for expanding `{base_dir}` placeholders and validating absolute paths
- [X] T010 [P] Create CLI argument parser skeleton in `src/package_mgmt/cli/main.py` with argparse root and subcommand registration pattern

**Checkpoint**: Foundation ready — user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Project Configuration and Constants (Priority: P1) 🎯 MVP

**Goal**: A developer or admin can inspect project configuration with `package_mgmt config`, seeing defaults with clear override indicators.

**Independent Test**: Clone the repo, run `package_mgmt config` — prints full set of default variables without errors. Create `project.local.toml` with a custom `base_dir`, re-run — shows overridden value marked as `(override)`.

### Implementation for User Story 1

- [X] T011 [P] [US1] Create `ProjectConfig` dataclass in `src/package_mgmt/lib/model/config.py` with fields: `base_dir`, `binary_cache`, `source_cache`, `db_path`, `log_level`, `log_dir`; all path fields use `{base_dir}` interpolation by default
- [X] T012 [P] [US1] Create config loading service in `src/package_mgmt/lib/service/config_loader.py` — loads `project.defaults.toml` via `tomllib`, merges values from `project.local.toml` if present, returns `ProjectConfig` instance; validates `base_dir` is absolute
- [X] T013 [US1] Implement `package_mgmt config` command in `src/package_mgmt/cli/commands/config.py` — reads config, prints each key with its source annotation (`(default)` or `(override: project.local.toml)`); supports `--format {text,json}` per contracts/cli.md
- [X] T014 [US1] Wire `config` subcommand into argparse root in `src/package_mgmt/cli/main.py` — `package_mgmt config` dispatches to `commands/config.py`
- [X] T015 [US1] Add validation and error handling for US1 — unparseable TOML raises `ConfigError` with exit code 102; unknown keys in override file are silently ignored (forward compat)
  - **Required**: Use `_print_error()` for all user-facing errors (JSON format)
  - **Required**: Use custom exceptions; map to appropriate exit codes
- [X] T016 [US1] Add logging for config operations at INFO level (config loaded, override detected, validation result)

**Checkpoint**: `package_mgmt config` works standalone — prints defaults and overrides.

### Unit Tests for User Story 1

- [X] T033 [US1] Write unit tests for config in `tests/unit/test_config.py` — ProjectConfig dataclass, config loading, TOML parsing, override detection, path expansion

---

## Phase 4: User Story 2 - Cache Directory Initialisation (Priority: P2)

**Goal**: An administrator or operator runs `package_mgmt init` and both `binary/` and `source/` cache trees are created with correct Debian apt layout and permissions.

**Independent Test**: Run `package_mgmt init --base-dir /tmp/pkg_test` — both `/tmp/pkg_test/binary/pool/`, `/tmp/pkg_test/binary/dists/`, `/tmp/pkg_test/source/pool/`, `/tmp/pkg_test/source/dists/` exist with `755` permissions. Re-run — exits successfully without errors.

### Implementation for User Story 2

- [X] T017 [P] [US2] Create `BinaryCache` and `SourceCache` dataclasses in `src/package_mgmt/lib/model/cache.py` — each stores `path` (resolved string) and tracks `exists` state
- [X] T018 [US2] Create cache initializer service in `src/package_mgmt/lib/service/initializer.py` — creates `binary/pool/<component>/<letter>/`, `binary/dists/<release>/<component>/binary-<arch>/`, and symmetric paths for source cache; sets `755` permissions; detects existing state and skips creation when already present; handles missing parent directories by creating them
- [X] T019 [US2] Implement `package_mgmt init` command in `src/package_mgmt/cli/commands/init.py` — reads config or `--base-dir` override, calls initializer service, prints summary per contracts/cli.md (base dir, binary cache, source cache, status)
- [X] T020 [US2] Wire `init` subcommand into argparse root in `src/package_mgmt/cli/main.py` — `package_mgmt init` dispatches to `commands/init.py`
- [X] T021 [US2] Add validation and error handling for US2 — permission errors raise `InitError` with exit code 100; readonly filesystem detected and reported
  - **Required**: Use `_print_error()` for all user-facing errors (JSON format)
  - **Required**: Use custom exceptions; map to appropriate exit codes
- [X] T022 [US2] Add logging for init operations (directory created, already existed, permission denied)

**Checkpoint**: `package_mgmt init` creates both cache trees with idempotent re-run.

### Unit Tests for User Story 2

- [X] T034 [US2] Write unit tests for cache init in `tests/unit/test_initializer.py` — BinaryCache/SourceCache dataclasses, directory creation, permissions, idempotency

---

## Phase 5: User Story 3 - Database Schema Initialisation (Priority: P3)

**Goal**: Running `package_mgmt init` also creates the SQLite database with schema matching Constitution Principle VII. Re-running validates schema without data loss.

**Independent Test**: Run `package_mgmt init --base-dir /tmp/pkg_test` — verify `package_mgmt.db` exists with 4 tables (packages, binaries, sources, binary_to_source) each having a `verified` column. Use `PRAGMA user_version` to check version is 1.

### Implementation for User Story 3

- [X] T023 [P] [US3] Create database schema definition in `src/package_mgmt/lib/model/database.py` — stores `SCHEMA_VERSION = 1` constant and `EXPECTED_SCHEMA_SQL` containing CREATE TABLE statements for packages, binaries, sources, binary_to_source per data-model.md column definitions
- [X] T024 [US3] Create database initializer service in `src/package_mgmt/lib/service/db_init.py` — connects to `{db_path}`, creates tables if absent, sets `PRAGMA user_version = 1` on first init; on re-init reads `PRAGMA user_version` and compares to `SCHEMA_VERSION` — mismatch raises `DatabaseError` with exit code 101
- [X] T025 [US3] Integrate database init into `init` command in `src/package_mgmt/cli/commands/init.py` — after cache directories are created, call db_init service; include database path and schema version in stdout summary
- [X] T026 [US3] Add validation and error handling for US3 — corrupted database file detected and reported; schema mismatch does not overwrite data without `--force` flag
  - **Required**: Use `_print_error()` for all user-facing errors (JSON format)
  - **Required**: Use custom exceptions; map to appropriate exit codes
- [X] T027 [US3] Add logging for database init (database created, schema validated, version matched/mismatched)

**Checkpoint**: `package_mgmt init` is complete — creates cache trees and database; re-init validates existing state.

### Unit Tests for User Story 3

- [X] T035 [US3] Write unit tests for database in `tests/unit/test_database.py` — schema definition, DB creation, version checking, schema mismatch detection

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [X] T028 [P] Add `__init__.py` to all Python packages (`src/package_mgmt/cli/`, `src/package_mgmt/cli/commands/`, `src/package_mgmt/lib/`, `src/package_mgmt/lib/model/`, `src/package_mgmt/lib/service/`, `src/package_mgmt/lib/util/`)
- [X] T029 [P] Add `__main__.py` to `src/package_mgmt/cli/` for `python -m package_mgmt` fallback
- [X] T030 Verify and update `project.defaults.toml` to match final config key set
- [X] T031 Run quickstart.md validation scenarios end-to-end
- [X] T032 Code cleanup and refactoring
  - **Check**: Error handling consistency (custom exceptions, exit codes, JSON error reporting)
  - **Check**: Logging strategy (selective structured logging, not replacing print())
  - **Check**: All `frozen=True` dataclasses for domain objects per Constitution VI
- [X] T036 Write contract tests in `tests/contract/test_cli.py` — CLI output format verification per contracts/cli.md
- [X] T037 Write integration tests in `tests/integration/test_init_flow.py` — full init flow end-to-end

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **User Stories (Phase 3-5)**: All depend on Foundational phase completion
  - User stories can proceed in parallel (if staffed)
  - Or sequentially in priority order (P1 → P2 → P3)
- **Polish (Phase 6)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Config system — no dependencies on other stories
- **User Story 2 (P2)**: Cache init — depends on config from US1 for path resolution
- **User Story 3 (P3)**: Database init — depends on config from US1 for `db_path` and cache dirs from US2 for parent directory

### Within Each User Story

- Models before services
- Services before CLI commands
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- All Foundational tasks marked [P] can run in parallel (within Phase 2)
- Once Foundational phase completes, all user stories can start in parallel (if team capacity allows)
- Models within a story marked [P] can run in parallel

---

## Parallel Example: User Story 1

```bash
# Launch all models for User Story 1 together:
Task: "Create ProjectConfig dataclass in src/package_mgmt/lib/model/config.py"
Task: "Create config loading service in src/package_mgmt/lib/service/config_loader.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1 — `package_mgmt config` works
   - **Error Handling Check**: `ConfigError` defined, exit codes mapped
   - **Logging Check**: Selective structured logging
4. **STOP and VALIDATE**: Test `package_mgmt config` independently

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test → Deploy/Demo (MVP: config inspection)
   - **Error Handling Check**: `ConfigError` defined, exit codes 0/102 mapped
   - **Logging Check**: Selective structured logging
3. Add User Story 2 → Test → Deploy/Demo (cache init works)
   - **Error Handling Check**: `InitError` defined, exit code 100 mapped
   - **Logging Check**: Selective structured logging
4. Add User Story 3 → Test → Deploy/Demo (full init with database)
   - **Error Handling Check**: `DatabaseError` defined, exit code 101 mapped
   - **Logging Check**: Selective structured logging
5. Each story adds value without breaking previous stories

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
