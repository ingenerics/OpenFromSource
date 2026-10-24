---

description: "Task list for table view"

---

# Tasks: Table View

**Input**: Design documents from `specs/003-table-view/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), contracts/cli.md

**Tests**: Contract tests for output format; unit tests for table formatting logic.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Include exact file paths in descriptions

## Path Conventions

- **CLI tool**: `src/package_mgmt/` — Python package at repository root
- **Database**: `{base_dir}/package_mgmt.db` — existing tables: packages, binaries, sources, binary_to_source
- **No new models or services** — the `show` command queries the DB directly

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization

- [X] T001 Create `src/package_mgmt/cli/commands/show.py` file for the `show` command

---

## Phase 2: User Story 1 — Basic Table View (Priority: P1) 🎯 MVP

**Goal**: `package_mgmt show <package>` displays matching rows from all tables in formatted text tables or JSON.

**Independent Test**: Run `package_mgmt show vifm` against a populated database — output shows four formatted tables with matching rows. Run with `--tables binaries` — only the binaries table appears. Run with `--format json` — output is valid JSON with table-name keys.

### Implementation for User Story 1

- [X] T002 [US1] Implement `handle_show(db_path, package, tables, fmt)` in `src/package_mgmt/cli/commands/show.py` — query each table by package-name column, collect rows, return structured data
- [X] T003 [US1] Implement text table formatter in `src/package_mgmt/cli/commands/show.py` — headers, aligned columns, separator lines, NULL displayed as `NULL`
- [X] T004 [US1] Implement JSON formatter in `src/package_mgmt/cli/commands/show.py` — `json.dumps` with table-name keys and row-array values, NULL → `null`
- [X] T005 [US1] Implement `--tables` filtering in `src/package_mgmt/cli/commands/show.py` — parse comma-separated list, validate against known table names, error on unknown
- [X] T006 [US1] Wire `show` subcommand into argparse in `src/package_mgmt/cli/main.py` — `package_mgmt show <package>` dispatches to `commands/show.py`
- [X] T007 [US1] Add validation and error handling — invalid table name raises error with exit code 1; database connection error with exit code 1; use `_print_error()` for JSON stderr

**Checkpoint**: `package_mgmt show` works standalone with text and JSON output, table filtering, and error handling.

---

## Phase 3: User Story 2 — Dependency Integration (Priority: P2)

**Goal**: `--deps` flag appends the dependency tree to the `show` output.

**Independent Test**: Run `package_mgmt show vifm --deps` — output includes the table view followed by the dependency tree matching `list --deps vifm`.

### Implementation for User Story 2

- [X] T008 [P] [US2] Add `--deps` flag support in `src/package_mgmt/cli/commands/show.py` — append dependency tree output (reuse `list.py` tree builder) after tables
- [X] T009 [US2] Wire `--deps` into the `show` subcommand argument parser and dispatch

**Checkpoint**: `package_mgmt show vifm --deps` shows both table data and dependency tree.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Verification and cleanup

- [X] T010 Run quickstart.md validation scenarios end-to-end — show with tables/json, table filtering, invalid table name, empty results
- [X] T011 Code cleanup and refactoring
  - **Check**: Error handling consistency (exit codes, JSON error reporting)
  - **Check**: Output format matches contracts/cli.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **US1 (Phase 2)**: Depends on Setup completion
- **US2 (Phase 3)**: Depends on US1 completion (builds on `show` command)
- **Polish (Phase 4)**: Depends on all desired user stories being complete

### Within Each User Story

- Core implementation before formatting
- Formatting before CLI wiring
- Story complete before moving to next priority

### Parallel Opportunities

- No meaningful parallel opportunities in this feature — single-file implementation

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: User Story 1 — `package_mgmt show` works
3. **STOP and VALIDATE**: Test `show` with text/json formats, table filtering

### Incremental Delivery

1. Add User Story 1 → Test → Deploy/Demo (MVP: `show` with tables)
2. Add User Story 2 → Test → Deploy/Demo (`--deps` integration)
3. Polish → Run validation scenarios

---

## Notes

- No new models, services, or external dependencies — this is a convenience view over the existing database
- Column names are discovered dynamically via `PRAGMA table_info()` — no hardcoded column lists
- Table name validation is explicit: valid = `packages`, `binaries`, `sources`, `binary_to_source`
