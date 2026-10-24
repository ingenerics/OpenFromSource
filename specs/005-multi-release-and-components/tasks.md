# Tasks: Multi-Release and Multi-Component Support

**Input**: Design documents from `/specs/005-multi-release-and-components/`

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

*No tasks — project structure already exists.*

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema, model, and config changes that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T001 [P] Create `ArchiveSource` dataclass in `src/package_mgmt/lib/model/config.py` — fields: `mirror: str`, `release: str`, `components: tuple[str, ...]`
- [X] T002 [P] Update `ProjectConfig` in `src/package_mgmt/lib/model/config.py` — replace scalar `release`/`component` with `sources: tuple[ArchiveSource, ...]`
- [X] T003 Update `src/package_mgmt/lib/model/database.py` — add `release` and `component` columns to `binaries` and `sources` tables; add `status` column to `binaries`; keep `SCHEMA_VERSION = 1`
- [X] T004 [P] Add `release: str` and `component: str` fields to `BinaryPackage` in `src/package_mgmt/lib/model/package.py`
- [X] T005 [P] Add `release: str` and `component: str` fields to `SourcePackage` in `src/package_mgmt/lib/model/package.py`

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 2 - Multi-Source Configuration (Priority: P1) 🎯 MVP

**Goal**: Administrators can configure multiple Debian archive sources in `project.defaults.toml` using `[[archive.source]]` entries. The configuration is validated: duplicate `(release, component)` pairs are rejected, and list-of-sources format is required.

**Independent Test**: Write a `project.defaults.toml` with two `[[archive.source]]` entries. Run `package_mgmt config` — both sources are displayed with mirror, release, and components. Re-run with a duplicate pair — error is reported.

### Implementation for User Story 2

- [X] T006 [P] [US2] Update `project.defaults.toml` — replace scalar `[archive]` with `[[archive.source]]` array format in `project.defaults.toml`
- [X] T007 [US2] Implement `[[archive.source]]` parsing in `src/package_mgmt/lib/service/config_loader.py` — handle array-of-tables, validate no duplicate `(release, component)`, construct `ArchiveSource` objects
- [X] T008 [US2] Update `project.defaults.toml` line references in config loader — remove old `_KEY_MAP` entries for `archive_mirror`, `release`, `component`; no backward compat fallback
- [X] T009 [US2] Update config CLI command output in `src/package_mgmt/cli/commands/config.py` to display all archive sources with mirror, release, and components

**Checkpoint**: At this point, User Story 2 should be fully functional. Multi-source config is parsed, validated, and displayed.

---

## Phase 4: User Story 1 - Debian-Compatible Cache Layout (Priority: P1)

**Goal**: After `package_mgmt init`, the cache directory mirrors a real Debian apt repository. Binary cache has `dists/{release}/{component}/binary-amd64/Packages.gz` for each source, source cache mirrors the same, and both share a common `pool/{component}/` tree.

**Independent Test**: Run `package_mgmt init` with two configured sources. Inspect `dists/` — contains separate release directories. Inspect `pool/` — contains component subdirectories. The layout passes an apt structural audit.

### Implementation for User Story 1

- [X] T010 [P] [US1] Update `initialize_caches()` in `src/package_mgmt/lib/service/initializer.py` — accept list of sources, create `dists/{release}/{component}/binary-{arch}/` and `dists/{release}/{component}/source/` per source, and `pool/{component}/` per component
- [X] T011 [P] [US1] Update `load_binary_index()` in `src/package_mgmt/lib/service/index_loader.py` — accept `release` and `component` params, tag each `BinaryPackage` with origin
- [X] T012 [P] [US1] Update `load_source_index()` in `src/package_mgmt/lib/service/index_loader.py` — accept `release` and `component` params, tag each `SourcePackage` with origin
- [X] T013 [US1] Update `handle_init()` in `src/package_mgmt/cli/commands/init.py` — iterate over `config.sources`, construct per-source index paths, load and tag indexes, report per-source statistics
- [X] T014 [US1] Update init command output — print per-source statistics (binaries loaded per source)

**Checkpoint**: At this point, User Story 1 should be fully functional. Init creates a Debian-mirroring cache tree and tags packages with provenance.

---

## Phase 5: User Story 3 - Provenance-Tracked Database Schema (Priority: P1)

**Goal**: The database tables capture release/component provenance for every record. The `binaries` table tracks resolution currency via the `status` flag (`'current'` / `'superseded'`). Queries can filter by release, component, and status.

**Independent Test**: After init with two sources, query `SELECT DISTINCT release FROM binaries` — returns all configured releases. Query `SELECT DISTINCT status FROM binaries` — returns `'current'` (no superseded entries until resolution occurs).

### Implementation for User Story 3

- [X] T015 [P] [US3] Update `upsert_binary()` in `src/package_mgmt/lib/service/resolver.py` — add `release` and `component` parameters to INSERT, update UNIQUE constraint to match new schema
- [X] T016 [P] [US3] Update `upsert_source()` in `src/package_mgmt/lib/service/resolver.py` — add `release` and `component` parameters to INSERT, update UNIQUE constraint
- [X] T017 [US3] Update all callers of `upsert_binary()` and `upsert_source()` to pass release/component from the `BinaryPackage`/`SourcePackage` origin

**Checkpoint**: At this point, User Story 3 should be functional. All binaries and sources in the DB carry provenance metadata.

---

## Phase 6: User Story 4 - Source-Targeted Resolution (Priority: P2)

**Goal**: The `add` command resolves from the first configured source by default. The `--source <release>` flag overrides the primary resolution anchor. Transitive dependencies first check the specified source, then fall back to other configured sources.

**Independent Test**: Run `package_mgmt add vifm` — resolves from first config source. Run `package_mgmt add hyprland --source trixie-backports` — hyprland resolved from backports, dependencies from backports first, then fallback.

### Implementation for User Story 4

- [X] T018 [P] [US4] Update `ensure_indexes()` in `src/package_mgmt/lib/service/resolver.py` — load indexes for ALL configured sources, not just one; merge into single `RepositoryIndex`
- [X] T019 [P] [US4] Add suite priority ordering — define deterministic priority: `main` > `contrib` > `non-free` > `non-free-firmware`, user-configured suites ordered by config position
- [X] T020 [US4] Update `_resolve_one()` in `src/package_mgmt/lib/service/resolver.py` — accept optional `primary_source` parameter; when set, search primary source first, fall back to other sources in priority order
- [X] T021 [US4] Update `PackageResolver.resolve()` — accept optional `source` parameter, pass to `_resolve_one()` as `primary_source`
- [X] T022 [US4] Update `resolve()` call chain in `src/package_mgmt/cli/commands/add.py` — pass `--source` flag value to resolver when provided
- [X] T023 [US4] Update `_resolve_build_deps()` in `src/package_mgmt/lib/service/resolver.py` — use same source-targeted logic as `_resolve_one()`

**Checkpoint**: At this point, User Story 4 should be functional. Source-targeted resolution works with fallback.

---

## Phase 7: User Story 5 - Append-Only Resolution History (Priority: P2)

**Goal**: When a package is resolved at a different version than the existing `status='current'` entry, the old row is marked `status='superseded'` and a new row is inserted as `status='current'`. At any point, exactly one row per `(package_name, architecture)` has `status='current'`. Full history remains queryable.

**Independent Test**: Add vifm (resolves libdrm 2.4.110 as current). Add hyprland --source backports (upgrades libdrm to 2.4.120). Query binaries for libdrm — two rows: one superseded (2.4.110, trixie), one current (2.4.120, trixie-backports).

### Implementation for User Story 5

- [X] T024 [P] [US5] Add status management helper in `src/package_mgmt/lib/service/resolver.py` — function to check for existing `status='current'` row for `(package_name, architecture)`, return version if found
- [X] T025 [US5] Update `_resolve_one()` in `src/package_mgmt/lib/service/resolver.py` — before inserting a new binaries row, check for existing `status='current'` row; if version differs, update old to `'superseded'`; if same version, reuse existing row
- [X] T026 [US5] Ensure `_resolve_one()` returns correct `binary_id` after status transition — the caller should reference the new `'current'` row

**Checkpoint**: At this point, User Story 5 should be functional. Resolution history is append-only with transparent status tracking.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [X] T027 [P] Update `package_mgmt list` command — add `--status` filter (`current`, `superseded`, `all`)
- [X] T028 [P] Update existing tests in `tests/unit/test_database.py` — expect new columns (release, component, status) in schema
- [X] T029 [P] Update existing tests in `tests/unit/test_config.py` — multi-source config format
- [X] T030 [P] Update existing tests in `tests/integration/test_init_flow.py` — multi-source init
- [X] T031 Add integration tests for end-to-end scenarios in `tests/integration/test_multi_source.py` — verify init with two sources, add with --source flag, status transitions
- [X] T032 Run quickstart.md validation scenarios to verify end-to-end flow

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — already complete
- **Phase 2 (Foundational)**: No dependencies — BLOCKS all user stories
- **Phase 3 (US2)**: Depends on Phase 2 — needs ArchiveSource and ProjectConfig changes
- **Phase 4 (US1)**: Depends on Phase 2 + Phase 3 — needs config sources list for cache dir creation
- **Phase 5 (US3)**: Depends on Phase 2 — needs updated schema and model fields
- **Phase 6 (US4)**: Depends on Phase 2 + Phase 5 — needs release/component in resolver; needs index_loader from US1
- **Phase 7 (US5)**: Depends on Phase 6 — needs source-targeted resolution working before status transitions
- **Phase 8 (Polish)**: Depends on all user stories being complete

### User Story Dependencies

- **US2 (P1)**: Can start after Foundational — No dependencies on other stories
- **US1 (P1)**: Depends on US2 (config sources list) — needs configured sources to create directories
- **US3 (P1)**: Can start after Foundational — No dependencies on other stories
- **US4 (P2)**: Depends on US1 (index loading) and US3 (provenance in binaries)
- **US5 (P2)**: Depends on US4 — needs source-targeted resolution before version upgrades can occur

### Within Each User Story

- Models before services
- Services before CLI commands
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- T001 and T002 can run in parallel (different config.py changes)
- T004 and T005 can run in parallel (different package models)
- T015 and T016 can run in parallel (upsert_binary and upsert_source)
- T018 and T019 can run in parallel (index loading and priority ordering)
- T027, T028, T029, T030 can all run in parallel (CLI command, test updates)

---

## Implementation Strategy

### MVP First (User Story 2 Only)

1. Complete Phase 2: Foundational (T001-T005)
2. Complete Phase 3: User Story 2 (T006-T009)
3. **STOP and VALIDATE**: Run `package_mgmt config` with multi-source config
4. Deploy/demo if ready

### Incremental Delivery

1. Complete Foundational → Foundation ready
2. Add User Story 2 → Multi-source config works
3. Add User Story 1 → Cache layout works
4. Add User Story 3 → Provenance tracking in DB
5. Add User Story 4 → Source-targeted resolution
6. Add User Story 5 → Version history and status transitions
7. Polish → Tests and CLI updates

### Parallel Team Strategy

With multiple developers:
1. Complete Foundational together
2. Once Foundational done:
   - Developer A: US2 (config_loader) + US1 (init, cache layout)
   - Developer B: US3 (resolver provenance) + US4 (source-targeted resolution)
   - Developer C: waits for US4, then US5 (status transitions)
3. Stories complete and integrate sequentially

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
