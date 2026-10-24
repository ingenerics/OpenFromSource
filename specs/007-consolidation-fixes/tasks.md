# Tasks: Consolidation Fixes

**Input**: Design documents from `/specs/007-consolidation-fixes/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Unit tests must be added/updated alongside each story; the suite runs via `python3 -m pytest` (see US9 bootstrap).

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

**Purpose**: Schema/model groundwork that MUST precede user story implementation

- [X] T001 Add `circular INTEGER NOT NULL DEFAULT 0` column to the `binaries` table DDL in `src/package_mgmt/lib/model/database.py` (schema version stays 1)
- [X] T002 Add `circular: bool = False` field to `DependencySpec` in `src/package_mgmt/lib/model/dependency.py` — emit in `to_json()` only when True, default in `from_json()`
- [X] T003 Add `ResolutionStats` frozen dataclass (fields per data-model.md) and `ProviderCandidate` frozen dataclass in `src/package_mgmt/lib/model/` (new file or in resolver — decide in `resolver.py` top-level)

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 2: User Story 1 - Complete Build-Tool Resolution (Priority: P1)

**Goal**: `_resolve_build_deps` expands the transitive build-tool tree to completion; `max_build_dep_report_depth` only filters verify output.

- [X] T004 [US1] Rework `_resolve_build_deps()` in `src/package_mgmt/lib/service/resolver.py` — replace `for iteration in range(max_depth)` with a loop that continues until no new sources with `build_depends` remain; keep `link_depth = iteration + 1`; add safety cap (e.g. 100 iterations) with a logged warning; no longer read `max_build_dep_report_depth` for resolution
- [X] T005 [US1] Confirm `verify.py` remains the only consumer of `max_build_dep_report_depth` (reporting filter) — no resolver coupling left; update the comment in `project.defaults.toml` if it implies resolution truncation
- [X] T006 [US1] Add unit test in `tests/unit/test_resolver_build_deps.py` — deep chain (3+ levels) resolves fully with a low `max_build_dep_report_depth`

**Checkpoint**: Deep build-tool chains resolve; verify filtering unchanged.

---

## Phase 3: User Story 2 - Circular Dependency Detection and Annotation (Priority: P1)

**Goal**: Cycles are detected by ancestry, annotated in DB + JSON, and reported by verify.

- [X] T007 [P] [US2] Add `self._ancestry: set[str]` to `PackageResolver.__init__`; clear it alongside `_visited` in `resolve()` and `_resolve_build_deps()`
- [X] T008 [P] [US2] In `_resolve_one()` in `src/package_mgmt/lib/service/resolver.py` — add/remove `name` from `_ancestry` around dependency recursion; when a dependency name is in `_ancestry`, mark the binary row `circular = 1` (`UPDATE binaries SET circular = 1 WHERE id = ?`), set `circular=True` on the offending spec before serialization, and do not recurse; keep `_visited` suppression for diamonds
- [X] T009 [US2] Implement `_find_circular()` in `src/package_mgmt/cli/commands/verify.py` — query `binaries WHERE circular = 1`, re-parse dependencies, list flagged specs as `pkg -> dep (circular)`; wire into `has_issues` and exit code
- [X] T010 [US2] Add unit tests in `tests/unit/test_virtual_resolution.py` or new `tests/unit/test_circular.py` — cycle detected, diamond not falsely marked, verify lists and exits non-zero

**Checkpoint**: Cycles annotated and reported; diamonds unaffected.

---

## Phase 4: User Story 3 - Verify the Current Tree Only (Priority: P1)

**Goal**: verify counts and checks `status='current'` only; missing build tools computed.

- [X] T011 [P] [US3] Add `AND status = 'current'` to total-binary count, `_find_unresolved_deps` existence checks (including alternatives branch), and `_find_unlinked_binaries` queries in `src/package_mgmt/cli/commands/verify.py`
- [X] T012 [P] [US3] Rework `_find_build_dep_issues()` in `src/package_mgmt/cli/commands/verify.py` — return `(total_links, total_build_tools, unresolved, missing)`; missing = build-dep entries with no link AND no `status='current'` binary; unresolved = entries with no link but a current binary exists (link/version gap); update caller and output (`Missing build tools:` no longer hardcoded)
- [X] T013 [US3] Add unit tests in `tests/unit/test_verify.py` (new) — superseded rows excluded from counts and from satisfying deps; missing build tool reported

**Checkpoint**: verify judges the coherent current tree.

---

## Phase 5: User Story 4 - No Arbitrary Build-Tool Linking (Priority: P1)

**Goal**: failed build-tool resolution creates no link and is reported.

- [X] T014 [US4] Remove the fallback block in `_resolve_build_deps()` in `src/package_mgmt/lib/service/resolver.py` (the `SELECT id FROM binaries ... ORDER BY id DESC LIMIT 1` branch); on `_resolve_one()` returning None, log a warning and append to `stats.errors` (best-effort per FR-009)
- [X] T015 [US4] Add unit test — missing build tool produces no `source_build_deps` row, an error entry, and verify reports it (covered with T013 fixtures or a new case in `tests/unit/test_resolver_build_deps.py`)

**Checkpoint**: no fabricated links; gaps visible in verify.

---

## Phase 6: User Story 5 - Full `show --deps` Output (Priority: P2)

**Goal**: text mode appends the tree; JSON adds `dependency_tree`.

- [X] T016 [P] [US5] In `handle_show()` in `src/package_mgmt/cli/commands/show.py` — when `deps` and `fmt == "json"`, emit `{"tables": ..., "dependency_tree": tree}` where tree = `_build_tree(conn, package)`
- [X] T017 [P] [US5] In `handle_show()` text mode — after printing tables, print a `Dependency Tree` section using `_print_text_tree(_build_tree(conn, package), package, pkg_status)` (reuse list.py builders; fetch packages.status for the root label)
- [X] T018 [US5] Add contract test in `tests/contract/test_cli.py` — JSON contains both top-level keys; text contains the tree section

**Checkpoint**: `show --deps` matches spec 003 FR-010/FR-011.

---

## Phase 7: User Story 6 - Constraint-Aware Selection (Priority: P2)

**Goal**: version constraints propagate into real and virtual selection.

- [X] T019 [P] [US6] Add `version_constraint: dict | None = None` parameter to `_resolve_one()` in `src/package_mgmt/lib/service/resolver.py`; pass `spec.version_constraint` at the dependency recursion site; for real packages, filter candidate entries by `compare_versions()` after source-priority lookup; if none satisfy, append unsatisfiable error and return None
- [X] T020 [P] [US6] Pass the constraint into `select_provider()` — replace the hardcoded `dep_version_constraint = None` for virtual packages; update the "no satisfiable providers" path to distinguish no-candidates (contract message) from constraint-filtered
- [X] T021 [US6] Add unit tests — constrained real dependency falls back across sources; constrained virtual filters providers; no-satisfier case reports best-effort (extend `tests/unit/test_virtual_resolution.py`, new `tests/unit/test_constraints.py`)

**Checkpoint**: selection honors `binaries.dependencies` constraints.

---

## Phase 8: User Story 7 - Per-Pair Duplicate Source Validation (Priority: P2)

**Goal**: any repeated `(release, component)` pair rejects the config.

- [X] T022 [US7] Rework dedup in `_parse_sources()` in `src/package_mgmt/lib/service/config_loader.py` — collect every `(release, component)` pair across all entries; first repeat raises `ConfigError` naming the pair
- [X] T023 [US7] Add unit tests in `tests/unit/test_config.py` — overlapping component lists rejected; disjoint accepted; single source unchanged

**Checkpoint**: FR-014 enforced.

---

## Phase 9: User Story 8 - Idempotent Provides and Resolution Hygiene (Priority: P2)

**Goal**: re-init repopulates provides exactly; orphans cleaned; superseded cached virtuals re-resolved.

**⚠️ REVERTED — design conclusion for later reference**:
A first implementation of T024–T027 was completed and later reverted on user request (git
rollback to `phase 8`). Design discussion concluded the following, which stands for the
re-implementation:

- The full-table rebuild (`DELETE FROM provides` + insert from the merged index, one
  transaction) is the **intended** design. The provides table is an aggregate over all
  configured sources and must exactly mirror the loaded merged index; init is *not* a
  clean slate for resolution state (packages/binaries/sources/history preserved), only
  for this index snapshot.
- The partial-load-wipe concern (one source failing to load wipes its provides rows) is
  **theoretical** and explicitly out of scope: index files are static after the first
  successful init (spec 005 snapshot design — index refresh is out of scope); re-init
  loads from the local cache via `_index_exists` without fetching; a source unreachable
  on first init simply has no rows yet; a corrupt local index crashes init loudly
  (no try/except around `_fetch_per_source`) instead of producing a partial table.
- Per-source reconciliation (`DELETE ... WHERE release = ? AND component = ?` per loaded
  source) was considered and **rejected**: complexity for a scenario that cannot occur
  in the supported snapshot workflow.
- T025 (orphan cleanup) and T026 (superseded cached-virtual re-check) remain valid design
  points; T026's resolver logic was already merged as part of phases 1–5 work (commit
  `8813d2c`) — only its test remains outstanding.

- [X] T024 [P] [US8] Change `populate_provides_table()` in `src/package_mgmt/lib/service/initializer.py` — `DELETE FROM provides` then insert within one transaction (rolled back on insert failure)
- [X] T025 [P] [US8] In `handle_init()` in `src/package_mgmt/cli/commands/init.py` — after successful provides repopulation, delete orphaned rows: `DELETE FROM virtual_resolutions WHERE resolved_binary_id NOT IN (SELECT id FROM binaries)`
- [X] T026 [US8] In `_resolve_one()` in `src/package_mgmt/lib/service/resolver.py` — the cached `virtual_resolutions` re-check additionally rejects a binary whose `status` is not `'current'` (treat as absent, re-resolve)
- [X] T027 [US8] Add unit tests — re-init row-count equality with fresh init (integration `tests/integration/test_init_flow.py`); orphan cleanup; superseded cached provider re-resolved (extend `tests/unit/test_virtual_resolution.py`)

**Checkpoint**: DB as source of truth holds across re-runs.

---

## Phase 10: User Story 9 - Test and Development Infrastructure (Priority: P3)

**Goal**: runnable suite, documented bootstrap, CI, quickstart validations recorded.

- [X] T028 [P] [US9] Add `[project.optional-dependencies] dev = ["pytest>=8", "ruff"]` to `pyproject.toml` (note: the unittest suite runs without pytest using system `python3-debian` — verified baseline unit 61/61, integration 9/9, contract 5/5)
- [X] T029 [P] [US9] Add `.github/workflows/ci.yml` — pytest + ruff on Python 3.13
- [X] T030 [P] [US9] Document bootstrap (`python3 -m unittest` baseline and optional `pip install -e ".[dev]"` + pytest) in `docs/001-quickstart-howto.md`
- [X] T031 [US9] Run the validation scenarios in `specs/005-multi-release-and-components/quickstart.md` and `specs/006-virtual-package-resolution/quickstart.md`; mark 005 T032 and 006 quickstart tasks complete

**T031 validation record (real Debian mirror — deb.debian.org, trixie/main + trixie-backports/main)**:
Ran end-to-end in a scratch config: `init` (68755 binaries / 37633 sources trixie, 3092/857 backports, provides populated), `add vifm`
(8534 deps resolved, idempotent re-add stable), `add hyprland --source trixie-backports` (8819 deps, 8441 from fallback —
source-targeted resolution + append-only status history: 9505 current / 148 superseded, no duplicate-current invariant
violations), `verify --graph` (42 genuine Debian cycles annotated, 1 genuinely unresolvable virtual `librust-hashbrown-0.15+default-dev`,
0 unlinked). The validation exposed THREE real bugs, all fixed and unit-tested in this phase:
1. **Versioned provides collapse** (spec 006 FR-002 PK flaw): `debhelper` provides `debhelper-compat (= 9)…(= 13)` — five
   rows for one virtual — but the natural key omitted `provides_version`, so all but the first were dropped (INSERT OR IGNORE)
   and `vifm`'s `debhelper-compat (= 13)` build-dep wrongly failed. Fix: PK now includes `provides_version` (amendment to
   spec 006 FR-002, see spec.md clarifications and 006 data-model.md).
2. **Legacy `>>`/`<<` operators**: `compare_versions()` only handled modern `>`/`<`; Debian-legacy `>>` (e.g. `perl (>> 5.005)`)
   fell through to `False`, failing valid resolutions. Fix: `>>` → strict greater.
3. **Real+virtual dual names**: `python3-multipart` is both a real package (1.2.1) and provided (= 0.0.20); a constraint
   `<< 0.1` unsatisfied by the real version errored instead of falling back to the versioned provider. Fix: constraint-failed
   real packages fall through to virtual candidate selection (dpkg-like).
Also fixed during validation: build tools already satisfied in earlier waves were reported as "not resolved" (visited-set
interaction with US4 error reporting) — now linked to the existing current/virtual record instead of erroring.

**Checkpoint**: suite green on fresh clone; CI active.

---

## Phase 11: User Story 10 - Contract and Constitution Reconciliation (Priority: P3)

**Goal**: outputs match contracts; resolver returns typed shapes; logging honors config.

- [X] T032 [P] [US10] Track `virtual_count` in `_resolve_one()` stats and print `(N dependencies, M virtual)` in `src/package_mgmt/cli/commands/add.py` (per contracts/cli.md)
- [X] T033 [P] [US10] Update `_query_table()` in `src/package_mgmt/cli/commands/show.py` for `virtual_resolutions` — JOIN `binaries` and project `resolved_package`, `version`, `status`, `release` columns per contract; update KNOWN_TABLES handling if needed
- [X] T034 [P] [US10] Add `fetch_source` to JSON output and `log_dir` to text output in `src/package_mgmt/cli/commands/config.py`
- [X] T035 [P] [US10] Align virtual-package error wording: no candidates → `Virtual package '<name>' has no providers in the index`; constraint-filtered → `no satisfiable providers` (keep both in `resolver.py`)
- [X] T036 [P] [US10] Convert `PackageResolver.resolve()` to return `ResolutionStats` (update `add.py` consumers); convert `query_provides()`/`select_provider()` to `ProviderCandidate` (update resolver and `tests/unit/test_virtual_resolution.py`)
- [X] T037 [US10] Wire config into logging in `src/package_mgmt/cli/main.py` — `_setup_logging(config)` sets level from `log_level` and adds a FileHandler under `log_dir` (created if missing); update `main.py` call sites
- [X] T038 [US10] Update contracts/spec quickstart examples as needed; update existing unit/contract tests for changed output (add virtual count, joined view, config keys, error wording)
- [X] T039 [US10] Fix `verify --graph` build-tool section once and for all — the "Missing build tools" report is virtual-blind: all 11773 entries on a fresh vifm cache were false positives (each name has a `provides` row; the resolver linked the PROVIDER binary, verify only accepts same-name links). Rework satisfaction to be provider- and constraint-aware (links to providers, `provides_version` matching, alternatives), make circular dependencies informational (not INCOMPLETE/exit-code failures), preserve build-dep alternatives in parsing/resolution, and validate end-to-end on a fresh cache.

**T039 validation record**: Root cause confirmed on the user's system cache — **11773/11773 "missing" build tools were false positives** (every name had a `provides` row; the resolver had linked the PROVIDER binary, verify only accepted same-name links); zero genuinely-missing names. Fixed:
1. `satisfies_constraint` moved to `versions.py` (shared by resolver + verify).
2. `_find_build_dep_issues` satisfaction is provider- and constraint-aware: a link to a current binary satisfies the entry if the binary is named after it OR provides it (versioned provides matched against the constraint, unversioned against provider version); alternatives checked per-name with constraints only on the primary.
3. `BuildDependencySpec` now preserves `alternatives` through parsing; the resolver tries alternatives for failed build tools (mirroring runtime dep handling).
4. Circular dependencies are listed but **informational** — real Debian trees contain benign cycles (44 in the vifm tree); they no longer flip status/exit code (spec 007 US2 AS3 amended).
5. The runtime `_find_unresolved_deps` was found with the SAME virtual-blindness in its alternatives branch (e.g. `librust-hashbrown-0.15+default-dev | librust-hashbrown-0.14+default-dev` — the alternative's provider existed and was resolved, yet the dep was reported unresolved) — fixed via shared `_dep_satisfied` helper (name + constraint-aware provider check per alternative).
6. Validation: user's system cache — `Missing build tools: 11787 → 0`, `Unresolved build deps: 0`, exit 0, COMPLETE. Fresh vifm-only pipeline from scratch (default config, real mirror) — `Unresolved deps: 0`, build section 0/0, **Status: COMPLETE, exit 0**. Suite: 110/110.

**Checkpoint**: contracts and code agree; Constitution VI clean.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — BLOCKS all user stories
- **Phase 2 (US1)**: Depends on Phase 1
- **Phase 3 (US2)**: Depends on Phase 1
- **Phase 4 (US3)**: Depends on Phase 1; verify changes are independent of resolver changes
- **Phase 5 (US4)**: Depends on Phase 2 (same function `_resolve_build_deps`)
- **Phase 6 (US5)**: Depends on Phase 1
- **Phase 7 (US6)**: Depends on Phase 1
- **Phase 8 (US7)**: No dependencies
- **Phase 9 (US8)**: Depends on Phase 1
- **Phase 10 (US9)**: Depends on US1–US8 tests existing; CI meaningful only after stories land
- **Phase 11 (US10)**: Depends on Phase 1; typed-shape changes ripple into US2–US8 helpers — do helper signature changes together with their stories where touching the same lines

### User Story Dependencies

- **US1 (P1)**: Independent
- **US2 (P1)**: Independent (different resolver sections; watch `_visited` interplay)
- **US3 (P1)**: Independent (verify.py only)
- **US4 (P1)**: Depends on US1 (same loop)
- **US5 (P2)**: Independent
- **US6 (P2)**: Independent (resolver selection sites; overlaps US2 recursion edits — sequence after US2)
- **US7 (P2)**: Independent
- **US8 (P2)**: Independent (initializer/init; resolver cached-virtual check overlaps US6 — sequence after US6)
- **US9 (P3)**: After stories land
- **US10 (P3)**: After stories land; final contracts pass

### Within Each User Story

- Models before services
- Services before CLI commands
- Core implementation before integration
- Tests alongside code (constitution quality gates)

### Parallel Opportunities

- T001-T003 (setup) then US1–US8 stories are file-disjoint except resolver.py (US1, US2, US4, US6, US8 all touch resolver.py — sequence within that file: US1 → US4 → US2 → US6 → US8)
- T011/T012 (verify.py) independent of resolver work
- T022 (config_loader) independent of everything

---

## Implementation Strategy

### MVP First (Phase 2–5: P1 stories)

1. Complete Phase 1 (T001-T003)
2. US1 (T004-T006) → full build-tool resolution
3. US4 (T014-T015) → honest links (same function)
4. US2 (T007-T010) → circular detection
5. US3 (T011-T013) → current-tree verify
6. **STOP and VALIDATE**: `verify --graph` on a re-resolved cache reports accurately; exit codes correct

### Incremental Delivery

1. Resolver correctness (US1, US2, US4)
2. Verify correctness (US3)
3. View + determinism (US5, US6, US7, US8)
4. Engineering health (US9, US10)

### Parallel Team Strategy

With multiple developers:
1. Developer A: resolver.py sequence (US1 → US4 → US2 → US6 → US8)
2. Developer B: verify.py (US3) + show.py (US5) + config (US7)
3. Developer C: initializer/init (US8 init side) + infra (US9, US10)

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
- resolver.py is the single contention point — sequence edits there US1 → US4 → US2 → US6 → US8
