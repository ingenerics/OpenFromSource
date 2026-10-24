# Tasks: Virtual Package Resolution

**Input**: Design documents from `/specs/006-virtual-package-resolution/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Not requested — no test tasks generated.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **CLI tool**: `src/package_mgmt/` at repository root
- **Cache directory**: admin-managed package cache with apt repository layout: `cache/pool/`, `cache/dists/`
- **Metadata**: `cache/package_mgmt.db` for local index

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

*No tasks — project structure already exists. The schema from spec 005 is already in place.*

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema changes that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T001 Add `provides` table to `src/package_mgmt/lib/model/database.py` — natural key `(package_name, version, architecture, release, component, virtual_name)`, index on `virtual_name`, no FK to binaries
- [X] T002 Add `virtual_resolutions` table to `src/package_mgmt/lib/model/database.py` — `virtual_name TEXT PRIMARY KEY`, `resolved_binary_id INTEGER NOT NULL REFERENCES binaries(id)`

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 - Record Provides Relationships (Priority: P1) 🎯 MVP

**Goal**: During `package_mgmt init`, the system parses `Provides` fields from all binary Packages.gz stanzas and inserts rows into the `provides` table using the natural key. No `binaries` rows are created during init.

**Independent Test**: Run `package_mgmt init` with a real or test Debian index. Query `SELECT COUNT(*) FROM provides` — returns non-zero. Query `SELECT package_name FROM provides WHERE virtual_name = 'awk'` — returns gawk, mawk, original-awk.

### Implementation for User Story 1

- [X] T003 [P] [US1] Add `populate_provides_table()` to `src/package_mgmt/lib/service/initializer.py` — iterate over all `BinaryPackage` entries in the `RepositoryIndex`, parse `provides_raw` field, insert natural-key rows into `provides` table using `INSERT OR IGNORE`
- [X] T004 [P] [US1] Ensure `BinaryPackage.provides_raw` is parsed from Packages.gz in `src/package_mgmt/lib/service/index_loader.py` — verify the field is captured during stanza parsing (spec 005 already added this, confirm it's present)
- [X] T005 [P] [US1] Add `parse_provides_field()` helper in `src/package_mgmt/lib/service/index_loader.py` — reuse `DependencyGraph.parse_depends_line()` to parse the Provides field into `(virtual_name, version)` tuples
- [X] T006 [US1] Update `handle_init()` in `src/package_mgmt/cli/commands/init.py` — after loading the binary index, call `populate_provides_table()` and report provides count in output

**Checkpoint**: At this point, User Story 1 should be fully functional. The `provides` table contains all provides relationships from the index.

---

## Phase 4: User Story 2 - Resolve Virtual Dependencies (Priority: P2)

**Goal**: The resolver detects virtual dependency references, checks `virtual_resolutions` first, queries `provides` for candidates, selects provider deterministically, creates a `binaries` record, and records the resolution. Subsequent encounters reuse the cached resolution.

**Independent Test**: Run `package_mgmt add vifm` — zero "Package not found" errors for known virtual packages (`awk`, `c-compiler`, `debhelper-compat`, `perlapi-*`). Query `SELECT * FROM virtual_resolutions` — shows resolved virtual names.

### Implementation for User Story 2

- [X] T007 [P] [US2] Add `check_virtual_resolution()` helper in `src/package_mgmt/lib/service/resolver.py` — query `virtual_resolutions` by `virtual_name`, return `resolved_binary_id` if found, None otherwise
- [X] T008 [P] [US2] Add `query_provides()` helper in `src/package_mgmt/lib/service/resolver.py` — query `provides` table by `virtual_name`, return list of candidate providers with their natural key fields
- [X] T009 [P] [US2] Add `record_virtual_resolution()` helper in `src/package_mgmt/lib/service/resolver.py` — insert into `virtual_resolutions` with `INSERT OR REPLACE`
- [X] T010 [P] [US2] Add `select_provider()` helper in `src/package_mgmt/lib/service/resolver.py` — deterministic selection: filter by version constraint, sort alphabetically by package_name, prefer already-resolved packages in `self._visited`
- [X] T011 [US2] Update `_resolve_one()` in `src/package_mgmt/lib/service/resolver.py` — when a dependency name is not found in `binary_packages`, first check `virtual_resolutions`, then query `provides`, select provider, look up metadata from loaded index, create `binaries` record, record resolution, continue recursion
- [X] T012 [US2] Handle unresolvable virtual in `_resolve_one()` — if no provider found for virtual name, log error, add to `stats["errors"]`, return None (best-effort per FR-009)

**Checkpoint**: At this point, User Story 2 should be functional. Virtual dependencies are resolved during `add`, and results persist in `virtual_resolutions` across invocations.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [X] T013 [P] Add `provides` and `virtual_resolutions` to `KNOWN_TABLES` in `src/package_mgmt/cli/commands/show.py` — support `--tables provides` and `--tables virtual_resolutions`
- [X] T014 [P] Update `_query_table()` in `src/package_mgmt/cli/commands/show.py` to handle provides and virtual_resolutions table lookups
- [X] T015 Update existing tests in `tests/unit/test_database.py` to expect the new tables
- [X] T016 Run existing unit tests to verify all pass (29/29 passing)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — already complete
- **Phase 2 (Foundational)**: No dependencies — BLOCKS all user stories
- **Phase 3 (US1)**: Depends on Phase 2 — needs provides table in schema
- **Phase 4 (US2)**: Depends on Phase 3 — needs provides table populated
- **Phase 5 (Polish)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational — No dependencies on other stories
- **US2 (P2)**: Depends on US1 — needs provides table populated during init; can be developed against test fixtures (manually seeded provides table) without waiting for US1 init changes

### Within Each User Story

- Models before services
- Services before CLI commands
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- T003, T004, T005 can run in parallel (initializer, index_loader, different responsibilities)
- T007, T008, T009, T010 can run in parallel (all resolver helpers, different queries)
- T013 and T014 can run in parallel (show command, single file but different additions)

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 2: Foundational (T001-T002)
2. Complete Phase 3: User Story 1 (T003-T006)
3. **STOP and VALIDATE**: Run `package_mgmt init` and verify `provides` table has correct entries
4. Deploy/demo if ready

### Incremental Delivery

1. Complete Foundational → Foundation ready
2. Add User Story 1 → Provides table populated during init (MVP!)
3. Add User Story 2 → Virtual dependencies resolved during add
4. Polish → Show command support, test updates

### Parallel Team Strategy

With multiple developers:
1. Complete Foundational together
2. Once Foundational done:
   - Developer A: User Story 1 (T003-T006)
   - Developer B: User Story 2 helpers (T007-T010), then core (T011-T012)
3. Stories complete and integrate independently

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
