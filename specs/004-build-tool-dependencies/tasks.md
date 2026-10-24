---

description: "Task list for Build-Tool Dependencies feature"

---

# Tasks: Build-Tool Dependencies

**Input**: Design documents from `specs/004-build-tool-dependencies/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Not explicitly requested — implementation tasks only. Independent test criteria defined per story.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **CLI tool**: `src/package_mgmt/` at repository root
- **Config**: `project.defaults.toml` at repository root
- **Cache directory**: admin-managed package cache under `{base_dir}/` (configurable)
- **Metadata**: `{base_dir}/package_mgmt.db` — SQLite database

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Config and project setup for the build-tool feature

- [x] T001 Add `[fetch] source = true` to `project.defaults.toml` in repository root

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema and config changes that MUST be complete before ANY user story

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T002 Add `build_depends` TEXT column to `sources` table and create `source_build_deps(source_id, binary_id, verified)` table in `src/package_mgmt/lib/model/database.py`
- [x] T003 Add `fetch_source: bool = True` field to `ProjectConfig` dataclass in `src/package_mgmt/lib/model/config.py`
- [x] T004 [P] Add `("fetch", "source"): "fetch_source"` entry to `_KEY_MAP` in `src/package_mgmt/lib/service/config_loader.py`

**Checkpoint**: Foundation ready — user story implementation can begin

---

## Phase 3: User Story 1 - Record Build-Dependency Metadata (Priority: P1) 🎯 MVP

**Goal**: Parse Build-Depends from Sources.gz and persist structured build-dependency metadata in the database alongside each source record.

**Independent Test**: After `package_mgmt add <pkg>` with `[fetch] source = true`, query `sources.build_depends` via sqlite3 and confirm it contains a JSON array of parsed build-dependency entries. Query `source_build_deps` and confirm it has entries linking the source to its build-tool binaries.

### Implementation for User Story 1

- [x] T005 [P] [US1] Add `BuildDependencySpec` frozen dataclass (fields: name, version_constraint, architecture, build_context, profiles) with `build_context` enum ('build', 'build-arch', 'build-indep') in `src/package_mgmt/lib/model/dependency.py`
- [x] T006 [US1] Implement helper to parse Sources.gz `Build-Depends`, `Build-Depends-Arch`, `Build-Depends-Indep` fields into a unified JSON array of BuildDependencySpec objects, reusing the existing `DependencyGraph.parse_depends_line()` grammar, in `src/package_mgmt/lib/model/dependency.py`
- [x] T007 [US1] Extend the resolver's source-linking step to store the parsed build-dep JSON in `sources.build_depends` during resolution in `src/package_mgmt/lib/service/resolver.py`
- [x] T008 [US1] Populate `source_build_deps(source_id, binary_id)` entries when a source-to-build-tool binary link is established during resolution in `src/package_mgmt/lib/service/resolver.py`
- [x] T009 [US1] Handle empty/missing Build-Depends field gracefully — store NULL in `sources.build_depends`, no `source_build_deps` entries created, no error raised in `src/package_mgmt/lib/service/resolver.py`

**Checkpoint**: US1 complete — build-dependency metadata is parsed, persisted, and queryable via sqlite3.

---

## Phase 4: User Story 2 - Resolve and Cache Build Tools (Priority: P2)

**Goal**: When `[fetch] source = true`, resolve the full transitive build-tool dependency tree and populate the database with build-tool binary records and source_build_deps links. When `false`, skip entirely.

**Independent Test**: With `source = true` in config, add a package with known build deps (e.g., `gcc`, `debhelper`). Query `source_build_deps` and confirm entries exist linking the source to its build-tool binaries. With `source = false`, add the same package and confirm zero `source_build_deps` entries and no build-tool records in `binaries`.

### Implementation for User Story 2

- [x] T010 [US2] Guard source/build-dep resolution behind `fetch_source` config check — when `False`, skip source lookups, no `sources` records, no `source_build_deps` links in `src/package_mgmt/lib/service/resolver.py`
- [x] T011 [US2] For each entry in a source's parsed `build_depends` JSON, resolve the build-tool binary by reusing `_resolve_one()` — the same recursion used for runtime deps — creating `binaries` records and `source_build_deps` links in `src/package_mgmt/lib/service/resolver.py`

**Checkpoint**: US2 complete — build tools are resolved and cached according to the `[fetch] source` config setting.

---

## Phase 5: User Story 3 - Verify Build-Tool Completeness (Priority: P3)

**Goal**: Extend `verify --graph` to report build-tool-specific statistics, treating missing build-tool entries as unresolved dependencies.

**Independent Test**: Add a package with build deps, verify output includes a "Build-Tool Dependencies" section. Remove a build-tool binary record from the DB, re-run verify, confirm the missing tool is reported and status is INCOMPLETE.

### Implementation for User Story 3

- [x] T013 [US3] Extend `_find_unresolved_deps` in `src/package_mgmt/cli/commands/verify.py` to also check `source_build_deps` links — for each entry, verify the referenced `binary_id` exists in the `binaries` table
- [x] T014 [US3] Add a "Build-Tool Dependencies" section to `handle_verify_graph` output in `src/package_mgmt/cli/commands/verify.py` showing: total build-dep links, total build tools, unresolved build deps, missing build tools, and COMPLETE/INCOMPLETE status
- [x] T015 [US3] Include build-tool issues in the overall exit code — unresolved build deps trigger `EXIT_ERROR` same as unresolved runtime deps in `src/package_mgmt/cli/commands/verify.py`

**Checkpoint**: US3 complete — verify reports build-tool cache status and catches missing build tools.

---

## Phase 6: Augment Table Views

**Purpose**: Extend the `show` command from spec 003 to display build-tool data, as requested by the user.

**Goal**: The `source_build_deps` table appears in `package_mgmt show` output, and when `--deps` is used, the binaries section includes build-tool binaries alongside runtime transitive deps.

**Independent Test**: Run `package_mgmt show <pkg> --deps` and confirm the binaries section includes build-tool binary records (e.g., `debhelper`, `gcc`) that are linked via `source_build_deps` rather than via runtime dependency edges.

- [x] T016 [P] Add `"source_build_deps"` to the `KNOWN_TABLES` whitelist in `src/package_mgmt/cli/commands/show.py` so it appears in `package_mgmt show` output. Map it to `sources.package_name` via a JOIN similar to `binary_to_source`: `SELECT sbd.* FROM source_build_deps sbd JOIN sources s ON s.id = sbd.source_id WHERE s.package_name = ?`
- [x] T017 Extend `_collect_dep_names()` in `src/package_mgmt/cli/commands/show.py` to also include build-tool binary names from `source_build_deps` — query `SELECT b.package_name FROM source_build_deps sbd JOIN binaries b ON b.id = sbd.binary_id JOIN sources s ON s.id = sbd.source_id WHERE s.package_name IN ({placeholders})` and merge the results into the collected names list, so `--deps` shows build-tool tables alongside runtime deps

**Checkpoint**: Table views reflect the new build-tool schema and `--deps` includes build-tool data.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final touches, documentation, and validation

- [x] T018 [P] Update `specs/004-build-tool-dependencies/contracts/cli.md` if any CLI behaviour changes during implementation
- [x] T019 Revise `specs/004-build-tool-dependencies/quickstart.md` to replace raw `sqlite3` queries with `package_mgmt show` CLI commands where possible — use the same package examples as existing quickstarts (`vifm`, `nano`, `perl`) instead of `hello` or `linux`, and follow the formatting conventions from `specs/002-dependency-resolution/quickstart.md` and `specs/003-table-view/quickstart.md`
- [x] T020 [P] Run the validation scenarios in `specs/004-build-tool-dependencies/quickstart.md` to confirm end-to-end correctness

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational
- **User Story 2 (Phase 4)**: Depends on US1 (needs parsed build-dep metadata)
- **User Story 3 (Phase 5)**: Depends on US1 and US2 (needs resolved build-tool records to verify)
- **Table Views (Phase 6)**: Depends on US1 (needs schema + data), can run in parallel with US2/US3
- **Polish (Phase 7)**: Depends on all prior phases

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational — no dependencies on other stories
- **User Story 2 (P2)**: Depends on US1 (needs parsed `build_depends` JSON from sources table)
- **User Story 3 (P3)**: Depends on US1 + US2 (needs both metadata and resolved binaries)
- **Table Views (Phase 6)**: Depends on Foundational (schema change) only — can start any time after Phase 2

### Within Each User Story

- Models before services
- Services before CLI commands
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- T004 (config_loader.py) can run in parallel with T003 (config.py)
- T005 (BuildDependencySpec dataclass) can run in parallel with T006 (parsing logic) since they touch different files
- Phase 6 (Table Views) can run in parallel with US2/US3 since it depends only on Foundational schema changes
- All [P] tasks within a phase can run in parallel

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1 — build-dep metadata parsing and persistence
4. **STOP and VALIDATE**: Run sqlite3 queries to confirm `sources.build_depends` and `source_build_deps` are populated
5. Deploy/demo if ready (MVP delivers: build-dependency metadata is recorded)

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add User Story 1 → metadata persisted → Test independently → Deploy (MVP!)
3. Add User Story 2 → build tools cached → Test independently → Deploy
4. Add User Story 3 → verify completeness → Test independently → Deploy
5. Add Phase 6 → table views augmented → Test independently → Deploy
6. Polish → final validation

### Parallel Team Strategy

With multiple developers:
1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: Phase 6 (Table Views — independent, only needs schema)
   - Developer B: User Story 1 (metadata parsing)
3. After US1 completes:
   - Developer A: User Story 2 (resolution + caching)
   - Developer B: User Story 3 (verify)
4. All stories integrate independently — US1 populates data, US2 caches, US3 verifies

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
- The `show` command's dynamic column discovery via `PRAGMA table_info()` means the `build_depends` column on `sources` requires no code changes — only the `source_build_deps` whitelist addition is needed
