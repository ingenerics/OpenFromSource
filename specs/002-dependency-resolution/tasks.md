---

description: "Task list for package dependency resolution"

---

# Tasks: Package Dependency Resolution

**Input**: Design documents from `specs/002-dependency-resolution/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/cli.md

**Tests**: Not explicitly requested in spec — validation is via Independent Test criteria per user story.

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
- **Repository indexes**: `{base_dir}/binary/dists/{release}/{component}/binary-{arch}/Packages.gz` and `{base_dir}/source/dists/{release}/{component}/source/Sources.gz`
- **Database**: `{base_dir}/package_mgmt.db` — existing tables: packages, binaries, sources, binary_to_source

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic dependencies

- [X] T001 Add `python3-debian` to `pyproject.toml` dependencies section
- [X] T002 Create new file structure (`src/package_mgmt/lib/model/dependency.py`, `src/package_mgmt/lib/model/package.py`, `src/package_mgmt/lib/model/repository.py`, `src/package_mgmt/lib/service/resolver.py`, `src/package_mgmt/lib/service/index_loader.py`, `src/package_mgmt/lib/util/versions.py`, `src/package_mgmt/cli/commands/add.py`, `src/package_mgmt/cli/commands/list.py`, `src/package_mgmt/cli/commands/verify.py`)
- [X] T003 [P] Add Debian mirror URL and index path config keys to `project.defaults.toml` and `src/package_mgmt/lib/model/config.py` — `archive_mirror`, `release`, `component`
- [X] T004 [P] Add index path resolution to `src/package_mgmt/lib/util/paths.py` — helpers for constructing Packages.gz / Sources.gz paths from config

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T005 Create version parsing utilities in `src/package_mgmt/lib/util/versions.py` — thin wrapper around `debian.debian_support.Version` for parsing, comparing, and extracting epoch/upstream/debian_revision; helper `source_version_from_binary()` strips `+bN` suffix; helper `sort_versions()` for picking latest
- [X] T006 [P] Create index entry models in `src/package_mgmt/lib/model/package.py` — `BinaryPackage` (dataclass with package, version, architecture, depends_raw, source_field, filename, sha256) and `SourcePackage` (dataclass with package, version, directory, files list, build_depends)
- [X] T007 [P] Create dependency types in `src/package_mgmt/lib/model/dependency.py` — `DependencySpec` dataclass (name, version_constraint, architecture, dep_type) with JSON serialization; `DependencyGraph` namespace for traversal helpers
- [X] T008 [P] Create repository index model in `src/package_mgmt/lib/model/repository.py` — `RepositoryIndex` dataclass holding dicts of package name → list of BinaryPackage/SourcePackage; `lookup_binary(name)` and `lookup_source(name)` methods returning the latest version per FR-014
- [X] T009 Create index loader service in `src/package_mgmt/lib/service/index_loader.py` — `load_binary_index(path)` parses Packages.gz via `gzip.open` + `deb822.Packages.iter_paragraphs`; `load_source_index(path)` parses Sources.gz similarly; `fetch_and_load_index(url, dest_path)` downloads via HTTP if not cached; returns `RepositoryIndex`

**Checkpoint**: Foundation ready — index parsing and version handling complete, user stories can begin

---

## Phase 3: User Story 1 — Binary & Source Dependency Resolution (Priority: P1) 🎯 MVP

**Goal**: The `package_mgmt add` command resolves a package's full transitive dependency tree and populates all four database tables (packages, binaries, sources, binary_to_source) with `verified = 0`.

**Independent Test**: Run `package_mgmt add vifm` against a Debian Trixie repository index. The `packages` table contains vifm + all transitively-resolved packages. The `binaries` table contains vifm's binary entry plus perl and perl's dependencies. The `sources` table contains source entries for every binary. The `binary_to_source` table links each binary to its source. All records have `verified = 0`. Re-running produces identical state.

### Implementation for User Story 1

- [X] T010 [P] [US1] Create DB upsert helpers in `src/package_mgmt/lib/service/resolver.py` — `upsert_package(conn, name, version)`, `upsert_binary(conn, ...)`, `upsert_source(conn, ...)`, `link_binary_to_source(conn, binary_id, source_id)` that handle idempotent insert-or-update by identity key per FR-006
- [X] T011 [P] [US1] Create dependency resolver engine in `src/package_mgmt/lib/service/resolver.py` — `resolve_package(name, index, conn)` performs BFS transitive closure: look up binary in index, create records, recurse into each dependency name from Depends field; detects circular dependencies via visited set (FR-007); handles best-effort multi-package (FR-008)
- [X] T012 [US1] Implement index acquisition in `src/package_mgmt/lib/service/resolver.py` — before resolution, call `index_loader` to fetch and parse Packages.gz/Sources.gz if not already cached locally; raise clear error on fetch failure
- [X] T013 [US1] Implement source resolution in `src/package_mgmt/lib/service/resolver.py` — for each resolved binary, look up its source package via the `Source` field in Packages.gz stanza (or same name if absent); create source record and binary_to_source link
- [X] T014 [US1] Implement `package_mgmt add` command in `src/package_mgmt/cli/commands/add.py` — parse `pkg=version` args, call resolver engine for each named package, print summary: packages resolved, binaries recorded, sources linked per contracts/cli.md
- [X] T015 [US1] Wire `add` subcommand into argparse in `src/package_mgmt/cli/main.py` — `package_mgmt add <pkg>[=version]...` dispatches to `commands/add.py`
- [X] T016 [US1] Add validation and error handling for US1 — unresolvable package raises error; unparseable index raises error with exit code; use `_print_error()` for JSON stderr output; use custom exceptions; map to exit codes
- [X] T017 [US1] Add logging for add operations at operation boundaries (index fetch start/complete, resolution start, packages resolved count, source linking complete)

**Checkpoint**: `package_mgmt add` works standalone — resolves dependencies, populates DB, idempotent on re-run.

---

## Phase 4: User Story 2 — Dependency Graph Inspection (Priority: P2)

**Goal**: The administrator can inspect the resolved dependency graph via `list --deps` and check completeness via `verify --graph`.

**Independent Test**: After `package_mgmt add vifm`, run `package_mgmt list --deps vifm` — output shows vifm and all transitive deps with binary/source/verified annotations. Run `package_mgmt verify --graph` — output shows statistics including unresolved dependencies and unlinked binaries.

### Implementation for User Story 2

- [X] T018 [P] [US2] Implement `package_mgmt list --deps` command in `src/package_mgmt/cli/commands/list.py` — query database for dependency graph via recursive join on `binaries.dependencies` → `packages.name`; output tree with annotations (binary ✓/✗, source ✓/✗, verified ✓/✗); support `--format text|json` per contracts/cli.md
- [X] T019 [P] [US2] Implement `package_mgmt verify --graph` command in `src/package_mgmt/cli/commands/verify.py` — query database for counts: total packages, binaries, sources; find unresolved deps (entries in JSON dependencies without matching binary record); find unlinked binaries (no binary_to_source); output formatted report per contracts/cli.md
- [X] T020 [US2] Wire `list` and `verify` subcommands into argparse in `src/package_mgmt/cli/main.py` — `package_mgmt list --deps <pkg>` dispatches to `commands/list.py`; `package_mgmt verify --graph` dispatches to `commands/verify.py`
- [X] T021 [US2] Add validation, error handling, and logging for list/verify — package not found in DB raises error with exit code; use `_print_error()` for JSON stderr

**Checkpoint**: `package_mgmt list --deps` and `verify --graph` work against the populated database.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [X] T022 Verify and update `project.defaults.toml` — ensure archive_mirror, release, component keys are present and match resolver expectations
- [X] T023 Run quickstart.md validation scenarios end-to-end — add vifm, list --deps, verify --graph, idempotent re-add, unresolvable package error
- [X] T038 [P] Write unit tests for tree building and graph display in `tests/unit/test_list.py` — compact vs full mode suppression, circular dependency detection, tree structure correctness
- [X] T024 Code cleanup and refactoring
  - **Check**: Error handling consistency (custom exceptions, exit codes, JSON error reporting)
  - **Check**: Logging strategy (selective structured logging, not replacing print())
  - **Check**: All domain dataclasses are `frozen=True` per Constitution VI
  - **Check**: Idempotent upsert correctness

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **User Stories (Phase 3-4)**: All depend on Foundational phase completion
  - US1 and US2 can proceed in parallel (if staffed) since US2 only queries the database populated by US1
  - Or sequentially in priority order (P1 → P2)
- **Polish (Phase 5)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Dependency resolution — no dependencies on other stories
- **User Story 2 (P2)**: Graph inspection — depends on database being populated (US1 must have run at least once to create records)

### Within Each User Story

- Models before services
- Services before CLI commands
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- All Foundational tasks marked [P] can run in parallel (within Phase 2)
- Once Foundational phase completes, models within a story marked [P] can run in parallel
- US1 and US2 can be staffed in parallel after Foundational completion

---

## Parallel Example: User Story 1

```bash
# Launch all models for User Story 1 together:
Task: "Create DB upsert helpers in src/package_mgmt/lib/service/resolver.py"
Task: "Create dependency resolver engine in src/package_mgmt/lib/service/resolver.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1 — `package_mgmt add` works
   - **Error Handling Check**: Custom exceptions defined, exit codes mapped, JSON error reporting
   - **Logging Check**: Selective structured logging
4. **STOP and VALIDATE**: Test `package_mgmt add vifm` independently

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test → Deploy/Demo (MVP: add + resolve + populate DB)
   - **Error Handling Check**: Resolution errors, index fetch errors, exit codes mapped
   - **Logging Check**: Selective structured logging at operation boundaries
3. Add User Story 2 → Test → Deploy/Demo (inspection works)
   - **Error Handling Check**: Missing package errors, exit codes mapped
   - **Logging Check**: Selective structured logging
4. Each story adds value without breaking previous stories

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
- Index download (Packages.gz / Sources.gz) is a required step of `add` and is distinct from package payload downloads — the Constitution VII no-download constraint applies to package payloads only
