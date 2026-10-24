---

description: "Task list for Polish Backlog (Merged Low Findings)"

---

# Tasks: Polish Backlog (Merged Low Findings)

**Input**: Design documents from `/specs/019-polish-backlog/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (`cli.md`), quickstart.md

**Tests**: Tests ARE required. The spec's "User Scenarios & Testing (mandatory)" mandates independent tests +
acceptance scenarios per story, and the project convention (015/016/017/018) ships every phase with its
regression tests. Tests MUST be written and FAIL before implementation, and must be green under **both**
documented runners (`pytest` and `python -m unittest discover -s tests`) at every phase.

**Organization**: Seven phases (US1 exit-code/side-effect honesty, US2 flag/argument consistency, US3 honest
sync/verify reports, US4 deterministic resolution, US5 accurate database lifecycle, US6 path/download
robustness, US7 doc/test hygiene), grouped by user story so each story is independently implementable and
testable. Phase order follows the plan's Phase Map (1→7).

**Clarifications incorporated (spec `## Clarifications` / Session)**:
- US6 (P-21): a cache path containing spaces is **refused** with a clear, actionable diagnostic (no space-
  escaping) — the "working entry" branch is dropped.
- US5 (P-17 / FR-016): the `packages.status` lifecycle is minimal **`pending → current`** (no `superseded`/
  `failed` states).
- US2 (P-7b / FR-006): `list --deps X --status all` together is a **usage error** (mutually exclusive, exit 1);
  each flag alone works as before. This supersedes the current silent status-wins behavior.
- US2 (P-2 / FR-004): the decorative `verify --graph` flag is **removed**; bare `verify` is the single
  graph-verification command; 014/018 doc references updated in-cycle.

**Cross-cutting constraints (all phases)**:
- **No schema change** — the spec 010/011 freeze is absolute; every fix is behavioral, within existing columns
  (`specs/019-polish-backlog/data-model.md`). P-13 changes transaction ownership, not schema.
- The 014 exit-code/JSON contract table is **extended**, never contradicted; `tests/contract/test_cli.py` stays
  green throughout.
- No new runtime dependencies; no new network call sites (P-20 only caps an existing retry delay; P-21 guards an
  operator file). Work lands on `main` in the same alpha release (after 015-018).
- Baseline this cycle (verified): `pytest` → **637 collected**; `python -m unittest discover -s tests`
  → **Ran 637 tests, OK**; `pytest tests/contract -q` → **68 passed + 15 subtests**.
- TDD: tests red first, green after; both runners green at every phase.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US7)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Baseline & Environment)

**Purpose**: Establish a verified baseline before any change — confirms both documented runners execute,
operator-script prerequisites are available, and the repo reflects the post-clarify state the plan's line
numbers assume.

- [X] T001 Verify baseline test state: run both documented runners (`pytest` and `python -m unittest discover -s tests`) from a clean checkout; record the green baseline as the final-phase gate baseline — **baseline this cycle: `pytest` → 637 collected; `python -m unittest discover -s tests` → Ran 637 tests, OK; `pytest tests/contract -q` → 68 passed + 15 subtests** (verified: pytest → 637 collected; unittest → Ran 637 tests, OK (47.2s); contract → 68 passed + 15 subtests)
- [X] T002 [P] Verify the offline-safe test prerequisites per `specs/019-polish-backlog/quickstart.md`: `bash`, `apt-get`, `gpg`, `gpgv` are on PATH (operator-script and handoff tests depend on them) and the local fixtures (`tests/fixtures/make_test_iso.py` with `write_archive`, `write_pool_files`, plus the scratch-cache/index builders used by 015-018) import cleanly (verified: bash/apt-get/gpg/gpgv all on PATH; fixtures import cleanly in the venv)
- [X] T003 [P] Verify repo state: on branch `main` with the venv active, and confirm the 019 spec's `## Clarifications` / Session reflects the three confirmed decisions (P-21 space-path refusal; FR-016 `pending → current`; `list` flags mutually exclusive) — the tasks below depend on them (verified: branch `main`, venv Python 3.13.5 imports `package_mgmt`; spec Clarifications Session has all three decisions)

---

## Phase 2: Foundational (Cross-Cutting Guards)

**Purpose**: Lock in the guards every phase must respect. No new error-handling/exception infrastructure is
required — the feature reuses the existing `_print_error`/exit-code machinery (`DatabaseError`/`InitError`/exit
codes, the shared `validate_schema_or_error`, `ConfigError`/exit 102) and existing service types.

**⚠️ CRITICAL**: These guards are prerequisite evidence — verify them before any phase, re-verify in the final
phase.

- [X] T004 Confirm the schema-freeze baseline: the feature changes no tables/columns; record that NO phase may add/alter columns — all fixes are behavioral (`specs/019-polish-backlog/data-model.md`); `EXPECTED_SCHEMA_SQL` defines exactly the 8 frozen tables (`packages`, `binaries`, `sources`, `binary_to_source`, `source_build_deps`, `provides`, `virtual_resolutions`, `systems`) (confirmed: `database.py:EXPECTED_SCHEMA_SQL` — exactly those 8 tables; feature is behavioral only)
- [X] T005 [P] Confirm the baseline exit-code / JSON contracts from `specs/014-base-profiles/contracts/cli.md` hold (exit `0`/`1`/`101`/`102`, JSON-on-stderr) and that the 019 contract (`specs/019-polish-backlog/contracts/cli.md`) extends, never contradicts, them — `pytest tests/contract -q` green (68 passed + 15 subtests; 014 exit codes 0/1/101/102 + JSON-on-stderr hold; 019 adds the consolidated table incl. 100 `EXIT_PERMISSION` — extension only)
- [X] T006 [P] Record the pre-fix behavior baselines for US1–US7 with exact anchors (from `review/04_alpha_summary/03_polish_backlog_docs.md` §1 and `specs/019-polish-backlog/research.md`): `add.py:83-88` broad `except → EXIT_ERROR`; `init.py:109-112` all-`InitError → 100` and `init.py:135-148` bare "initialized"; `ensure_base.py:64-68` DB-before-work; `main.py:120-123` decorative `--graph` and `main.py:199-203` silent status-wins; `verify.py:101-105` no depth clause on `--package` and `verify.py:254-322` build-dep no depth; `systems.py:26-52` no moniker validation and `43-45` leaked conn; `add.py:16-20` empty-pin unpinned; `sync.py:56-61` zero-pending early-return; `repository.py:85-88` inverted component tie; `resolver.py:103-108` LIMIT-1 no ORDER BY, `706-722` constraint-blind fallback, `1023-1031` no status filter, `912-915` same-release virtual reuse, `956` resolved-as-requested, `111-130` circular not reset; `initializer.py:65-84` nested commit + skip counting; `verify.py:195-212` circular superseded; `paths.py:8-12` dead `ValueError`; `downloader.py:204-211` uncapped Retry-After; `handoff.py:151` unguarded space path (all anchors verified in source)

**Checkpoint**: Guards verified — user story phases may proceed.

---

## Phase 3: User Story 1 - Command Failures Honor the Exit-Code Contract and Leave No Artifacts (Priority: P1) 🎯 MVP

**Goal**: `add` maps a stale-schema `DatabaseError` to exit 101 (matching its siblings); `init` distinguishes
permission failures (100) from other failures (1) and flags offline mode in its summary when no mirror is
reachable; a failed `ensure-base` on a fresh cache creates **no database file** (spec 008 US1 AS2). *(P-1, P-4,
P-5)*

**Independent Test**: A stale-schema `add` exits 101 (not 1); an ENOSPC-style `init` exits 1 (not 100) and a
mirror-unreachable `init` exits 0 with an offline note in the summary; a failed `ensure-base` on a fresh cache
leaves no database file (`spec.md` US1).

### Tests for User Story 1 (write FIRST, must FAIL before implementation) ⚠️

- [X] T007 [P] [US1] Unit test: `handle_add` on a stale-schema database → exit 101 (the sibling code), never 1, in `tests/unit/test_add.py` (must FAIL pre-fix: broad `except Exception → EXIT_ERROR` maps it to 1) (written red, now green)
- [X] T008 [P] [US1] Unit test: `handle_init` maps a `PermissionError`-origin `InitError` → 100 and a non-permission `InitError` (ENOSPC-style `OSError`) → 1; a mirror-unreachable `init` (0 binaries) exits 0 with the offline note in the summary in `tests/unit/test_init.py` (must FAIL pre-fix: every `InitError` → 100 and the summary says a bare "initialized") (written red, now green)
- [X] T009 [P] [US1] Integration test: `handle_ensure_base` on a fresh cache with no usable image exits non-zero and leaves **no `package_mgmt.db` file** behind in `tests/integration/test_ensure_base_flow.py` (must FAIL pre-fix: `initialize_database` runs first and creates the DB) (written red, now green)
- [X] T010 [P] [US1] Contract test: stale-schema `add` → exit 101 + JSON; `init` permission → 100 / ENOSPC-style → 1 / offline summary line; failed `ensure-base` → non-zero with no DB file in `tests/contract/test_cli.py` (written red, now green)

### Implementation for User Story 1

- [X] T011 [US1] P-1: in `handle_add`, catch `DatabaseError` (from `initialize_database`) separately and return `EXIT_SCHEMA_MISMATCH (101)`; keep the broad handler for real bugs (exit 1) in `src/package_mgmt/cli/commands/add.py`
- [X] T012 [US1] P-4a: give `InitError` a `permission: bool = False` attribute; set it `True` only in the `except PermissionError` branch of `_ensure_dir` (all other `OSError` paths stay `False`); in `handle_init`, map `e.permission → EXIT_PERMISSION (100)` else `EXIT_ERROR (1)` in `src/package_mgmt/lib/service/initializer.py` and `src/package_mgmt/cli/commands/init.py`
- [X] T013 [US1] P-4b: when the merged index has 0 binaries (all mirrors unreachable), print `Status: initialized (no mirror reachable — offline mode, 0 binaries cached)` in the summary instead of a bare "initialized"; exit stays 0 in `src/package_mgmt/cli/commands/init.py`
- [X] T014 [US1] P-5: reorder `ensure_base` so DB creation happens **after** the failure-prone discovery — `find_installer_image` → `_cleanup_stale_temp_dirs`/`mkdtemp` → open `IsoImage` + `_load_image_indexes` (no DB) → only then `if os.path.exists(db_path): _assert_fresh_cache(...)`; `initialize_database(db_path)`; `sqlite3.connect`; begin the transaction — and drop the up-front `initialize_database` call in `handle_ensure_base`, adding `except DatabaseError → 101` in `src/package_mgmt/lib/service/ensure_base.py` and `src/package_mgmt/cli/commands/ensure_base.py`
- [X] T015 [US1] Validation: US1 suite green under both runners and `tests/contract/test_cli.py`; ruff clean on the touched files (verified: `pytest` → 646 passed + 56 subtests; `unittest` → Ran 646 tests, OK (47.9s); contract → 72 passed + 15 subtests; ruff clean on all touched files)

**Checkpoint**: US1 is fully functional — exit codes match the contract and no failed run leaves a stray database or an un-flagged offline state.

---

## Phase 4: User Story 2 - Command Flags and Arguments Behave as Documented and Consistently (Priority: P2)

**Goal**: The decorative `verify --graph` flag is removed (bare `verify` is the single graph-verification
command); `verify --package X` applies the report-depth filter; `systems add` rejects empty/whitespace/newline
monikers and never leaks the connection; `add` rejects an empty pin (`name=`) consistently in both paths;
`list --deps X --status all` together is a usage error (mutually exclusive). *(P-2, P-3, P-7)*

**Independent Test**: A parametrized matrix — `verify` (no `--graph`), `verify --package X` under a finite depth
(respects the filter), `systems add ""`/`$'a b'`/`$'a\nb'` (rejected, no leak), `add "vim="` and the same `--file`
line (both rejected), `list --deps X --status all` (usage error) (`spec.md` US2).

### Tests for User Story 2 (write FIRST, must FAIL before implementation) ⚠️

- [X] T016 [P] [US2] Unit test: `verify --graph` is a usage error (flag removed) and bare `verify` still runs the graph verification; `_find_unresolved_deps` with `--package X` applies the depth filter in `tests/unit/test_verify.py` (must FAIL pre-fix: `--graph` still accepted and the `if package:` branch has no depth clause) (written red, now green)
- [X] T017 [P] [US2] Unit test: `handle_systems_add` rejects `""`, `"a b"`, and `$'a\nb'` with a clean error and no row; the duplicate-moniker `IntegrityError` path closes the connection in `tests/unit/test_systems.py` (must FAIL pre-fix: bad monikers are accepted and the error path leaks the connection) (written red, now green)
- [X] T018 [P] [US2] Unit test: `_parse_pkg_spec("vim=")` positional path rejects the empty pin identically to the `--file` path in `tests/unit/test_add.py` (must FAIL pre-fix: positional `""` resolves unpinned) (written red, now green)
- [X] T019 [P] [US2] Unit test: `handle_list_deps` (via `main` dispatch) with both `--deps X` and `--status all` → clean usage error (exit 1); each flag alone works as before in `tests/unit/test_list.py` (must FAIL pre-fix: `--status` silently wins) (written red, now green)
- [X] T020 [P] [US2] Contract test: `verify --graph` → usage error; `systems add ""` → exit 1 + JSON, no row; `add "vim="` → exit 1; `list --deps X --status all` → exit 1 with the mutual-exclusion message in `tests/contract/test_cli.py` (written red, now green)

### Implementation for User Story 2

- [X] T021 [US2] P-2a: remove the `--graph` argument from `_register_verify` (registration + help text) in `src/package_mgmt/cli/main.py`; update the doc/contract references to `verify --graph` (014 contract table, 018 quickstart, 019 contract `contracts/cli.md`) to bare `verify` in the same cycle (Constitution VIII)
- [X] T022 [P] [US2] P-2b: add the same `LEFT JOIN source_build_deps … COALESCE(sbd.min_depth, 0) <= ?` depth clause to the `if package:` branch of `_find_unresolved_deps` so `verify --package X` matches the full-cache filter in `src/package_mgmt/cli/commands/verify.py`
- [X] T023 [US2] P-3: validate the moniker in `handle_systems_add` — reject empty/whitespace/newline names with a clean `_print_error` (exit 1), no INSERT; wrap the handler body in `try/finally` so `conn.close()` runs on every path (IntegrityError included) in `src/package_mgmt/cli/commands/systems.py`
- [X] T024 [P] [US2] P-7a: reject `name=` (empty pin) in the positional-argument path of `handle_add` exactly like the `--file` path (clean error naming the offending argument, exit 1) in `src/package_mgmt/cli/commands/add.py`
- [X] T025 [P] [US2] P-7b: add a both-given guard in `main()`'s `list` dispatch — `if status and args.deps:` → `_print_error` mutual-exclusion message, exit 1 — before calling `handle_list_deps` in `src/package_mgmt/cli/main.py`
- [X] T026 [US2] Docs: document the removed `--graph` and the mutually-exclusive `--deps`/`--status` in `specs/019-polish-backlog/contracts/cli.md` Phase 2 (usage-error examples, assertions) and in `--help` text where applicable (019 Phase 2 section already carries both; 014 contract `verify` section heading + 018 quickstart invocation updated to bare `verify`)
- [X] T027 [US2] Validation: US2 suite green under both runners and `tests/contract/test_cli.py`; ruff clean on the touched files (verified: `pytest` → 666 passed + 56 subtests; `unittest` → Ran 666 tests, OK (47.5s); contract → green; ruff clean on all touched files)

**Checkpoint**: US2 is fully functional — flags/arguments behave as documented and consistently across paths.

---

## Phase 5: User Story 3 - Sync and Verify Reports Are Honest (Priority: P2)

**Goal**: A zero-pending `sync` regenerates the diff as **empty** (spec 013 AS2 — never a stale non-empty
`diff/`), and `verify`'s "Unresolved build deps"/"Missing build tools" lists honor `max_build_dep_report_depth`
(`0` = none, `-1` = all, `N` = up to `N`) in both the full-cache and `--package` paths (014 FR-017). *(P-6, P-18)*

**Independent Test**: After a successful `sync`, re-run with nothing pending — the diff directory is empty
(previous content cleared); `verify` under `max_build_dep_report_depth = 0` reports no build-tool levels, and
under `-1`/`N` the full/limited set, for both the full cache and `--package X` (`spec.md` US3).

### Tests for User Story 3 (write FIRST, must FAIL before implementation) ⚠️

- [X] T028 [P] [US3] Unit test: `handle_sync` with zero pending packages after a previous non-empty diff leaves an **empty** regenerated `diff/` (stale files removed), exit 0, snapshot unchanged in `tests/unit/test_sync.py` (must FAIL pre-fix: the early-return leaves stale content)
- [X] T029 [P] [US3] Unit test: `_find_build_dep_issues` respects the report depth — `0` reports no build-tool levels, `-1` all, `N` up to `N` — in both the full-cache and `--package` paths in `tests/unit/test_verify.py` (must FAIL pre-fix: build-dep lists are never filtered)
- [X] T030 [P] [US3] Integration test: via the CLI flow, a zero-pending `sync` for a system with a prior non-empty diff regenerates an empty diff in `tests/integration/test_sync_flow.py`
- [X] T031 [P] [US3] Contract test: zero-pending `sync` summary (`New packages: 0`, empty diff artifacts present); `verify` at depth `0`/`-1`/`N` in `tests/contract/test_cli.py`

### Implementation for User Story 3

- [X] T032 [US3] P-6: on the zero-pending branch of `handle_sync`, regenerate the diff as empty (empty `Packages.gz`/`Release` + `apply.sh` with `New files: 0` outcome) instead of early-returning, so `diff/` is never stale; keep the summary and no-`mark_delivered` semantics in `src/package_mgmt/cli/commands/sync.py`
- [X] T033 [US3] P-18: thread `max_report_depth` into `_find_build_dep_issues` and filter both the "Unresolved build deps" and "Missing build tools" sources by `min(build_dep_depth) <= max_report_depth` (mirroring the runtime-dep `COALESCE(sbd.min_depth, 0) <= ?` pattern), in the full-cache and `--package` paths in `src/package_mgmt/cli/commands/verify.py`
- [X] T034 [US3] Validation: US3 suite green under both runners and `tests/contract/test_cli.py`; ruff clean on the touched files (verified: `pytest` → 676 passed + 56 subtests; `unittest` → Ran 676 tests, OK (48.5s); contract → 81 passed + 15 subtests; ruff clean on all touched files)

**Checkpoint**: US3 is fully functional — a zero-pending sync never ships stale content and the build-dep report is honest.

---

## Phase 6: User Story 4 - The Resolver Selects and Links Correct, Deterministic Entries (Priority: P1)

**Goal**: On an exact-version tie across components `main` (highest priority) wins and the pool path reflects it
(006 FR-005); current-binary selection is deterministic (never an arbitrary pick); the build-dep fallback links
only a constraint-satisfying entry and leaves no residual error; the runtime-alternative probe considers only
`current` rows; a cached virtual resolution picks up a same-release provider upgrade after an index refresh.
*(P-9, P-10, P-11, P-12, P-16)*

**Independent Test**: A fixture with the same package/version current in `main`/`contrib`/`non-free` — the
resolver selects `main`; a mismatched-version build-dep fallback is not linked and a valid link leaves no
residual error; an alternative group satisfied only by superseded rows does not suppress resolution; an index
refresh upgrading a provider in the primary source is picked up by a cached virtual resolution (`spec.md` US4).

### Tests for User Story 4 (write FIRST, must FAIL before implementation) ⚠️

- [X] T035 [P] [US4] Unit test: `_best_entry`/`lookup_source*` with the same version in `main`/`contrib`/`non-free` selects `main` (component order ascending wins the tie) in `tests/unit/test_repository.py` (must FAIL pre-fix: `non-free` wins) (written red, now green)
- [X] T036 [P] [US4] Unit test: `_find_current_binary` with two current rows for the same package+arch returns the same deterministic row on repeated calls (defined ordering) in `tests/unit/test_resolver.py` (must FAIL pre-fix: arbitrary `LIMIT 1`) (written red, now green)
- [X] T037 [P] [US4] Unit test: the build-dep fallback does not link a candidate that fails the entry's version constraint, and links a valid candidate with **no residual "unresolved" error** in `tests/unit/test_resolver.py` (must FAIL pre-fix: constraint-blind link + residual error) (written red, now green)
- [X] T038 [P] [US4] Unit test: a runtime-alternative group satisfied only by superseded rows does not suppress alternative resolution (probe filters `status='current'`) in `tests/unit/test_resolver.py` (must FAIL pre-fix: superseded-only rows suppress it) (written red, now green)
- [X] T039 [P] [US4] Unit test: a cached virtual resolution re-evaluates refreshed-index candidates on a same-release provider upgrade and picks up the upgrade in `tests/unit/test_resolver.py` (must FAIL pre-fix: the same-release branch reuses the cache blindly) (written red, now green)

### Implementation for User Story 4

- [X] T040 [P] [US4] P-9: fix the component tie-break — sort candidates by `(version, -component_order)` with `reverse=True` (or equivalently ascending order) so `main` (0) wins exact-version ties, in `_best_entry` and both `lookup_source`/`lookup_source_by_source` in `src/package_mgmt/lib/model/repository.py`
- [X] T041 [P] [US4] P-16: add `ORDER BY id` to `_find_current_binary`'s query so multi-row matches resolve deterministically in `src/package_mgmt/lib/service/resolver.py`
- [X] T042 [US4] P-10: in the build-dep fallback, re-check `satisfies_constraint(candidate_version, entry version_constraint)` (and the provider version for a virtual) before linking; retract the residual error pushed by the failed `_resolve_one` once a valid link is made (mirror the alternative-group retraction) in `src/package_mgmt/lib/service/resolver.py`
- [X] T043 [P] [US4] P-11: add `AND status = 'current'` to the runtime-alternative "already satisfied" binaries probe and its provides-join probe (mirroring the build-dep twin) in `src/package_mgmt/lib/service/resolver.py`
- [X] T044 [P] [US4] P-12: in the cached-virtual same-release branch, run a fresh `select_provider` over `query_provides` + the refreshed index and re-resolve when the candidate is a higher version of the same provider (or a higher-priority provider); reuse the cache otherwise in `src/package_mgmt/lib/service/resolver.py`
- [X] T045 [US4] Validation: US4 suite green under both runners and `tests/contract/test_cli.py`; ruff clean on the touched files (verified: `pytest` → 689 passed + 56 subtests; `unittest` → Ran 689 tests, OK (47.2s); contract → 81 passed + 15 subtests; ruff clean on all touched files)

**Checkpoint**: US4 is fully functional — selection is deterministic and every link/probe is constraint-aware.

---

## Phase 7: User Story 5 - The Database Lifecycle Is Consistent and Accurate (Priority: P2)

**Goal**: Provides-table population performs no nested commit and counts only real inserts (`init` commits
explicitly); stale `provides` rows (releases/indexes no longer configured) are swept on refresh; the circular
report counts only `current` rows and the `circular` flag resets on upsert; `packages.status` transitions
`pending → current` and `requested_version` holds only the actually-requested version. *(P-13, P-14, P-15, P-17)*

**Independent Test**: Refresh the provides table while a release is removed from config — stale rows are gone and
counts reflect only real inserts; a re-upserted circular binary loses its flag and `verify`/`list --deps` agree;
after resolution `packages.status` is `current` and a transitive dep shows no fabricated requested version
(`spec.md` US5).

### Tests for User Story 5 (write FIRST, must FAIL before implementation) ⚠️

- [X] T046 [P] [US5] Unit test: `populate_provides_table` issues no nested commit inside the caller's transaction and reports 0 inserts when all rows already exist (no phantom count) in `tests/unit/test_initializer.py` (must FAIL pre-fix: internal `commit()` + every attempt counted) (written red, now green)
- [X] T047 [P] [US5] Unit test: after a release is removed from config and the index refreshed, its `provides` rows are swept (never selectable) in `tests/unit/test_initializer.py` (must FAIL pre-fix: no `DELETE` exists) (written red, now green)
- [X] T048 [P] [US5] Unit test: a re-upserted circular binary loses its flag, and `_find_circular` counts only `current` rows (so `verify` and `list --deps` agree) in `tests/unit/test_resolver.py` and `tests/unit/test_verify.py` (must FAIL pre-fix: flag never reset, superseded rows counted) (written red, now green)
- [X] T049 [P] [US5] Unit test: after resolution `packages.status` transitions to `current` and a transitive dep's `requested_version` is empty/NULL (not the resolved version) in `tests/unit/test_resolver.py` (must FAIL pre-fix: status stuck at `pending`, resolved version stored as requested) (written red, now green)

### Implementation for User Story 5

- [X] T050 [US5] P-13: remove the internal `conn.commit()` from `populate_provides_table` (the caller owns the transaction — `ensure_base`'s `BEGIN…commit` stays the single Phase-A transaction) and count real inserts via `cursor.rowcount` (0 for skipped duplicates); add an explicit `conn.commit()` in `handle_init` after the call in `src/package_mgmt/lib/service/initializer.py` and `src/package_mgmt/cli/commands/init.py`
- [X] T051 [P] [US5] P-14: before re-populating from a refreshed index, sweep stale rows — `DELETE FROM provides WHERE (release, component) NOT IN (configured set)` — so no-longer-configured releases/indexes never leave selectable providers in `src/package_mgmt/lib/service/initializer.py`
- [X] T052 [P] [US5] P-15: reset the `circular` flag in `upsert_binary`'s `ON CONFLICT DO UPDATE` (`circular = 0`), and add `AND status = 'current'` to `_find_circular` in `src/package_mgmt/lib/service/resolver.py` and `src/package_mgmt/cli/commands/verify.py`
- [X] T053 [US5] P-17: set `packages.status = 'current'` when the resolver registers a current binary (lifecycle `pending → current`), and pass `requested_version` (None for transitive deps) instead of `requested_version or binary_entry.version` in `src/package_mgmt/lib/service/resolver.py`
- [X] T054 [US5] Validation: US5 suite green under both runners and `tests/contract/test_cli.py`; ruff clean on the touched files (verified: `pytest` → 696 passed + 56 subtests; `unittest` → Ran 696 tests, OK (48.1s); contract → 81 passed + 15 subtests; ruff clean on all touched files)

**Checkpoint**: US5 is fully functional — the stored model is truthful and transactional.

---

## Phase 8: User Story 6 - Path and Download Robustness (Priority: P2)

**Goal**: A relative `base_dir` is rejected at load (exit 102, key named); a server `Retry-After` is capped at
the internal backoff bound (30 s); a cache path containing spaces is **refused** by the handoff with a clear,
actionable diagnostic and no broken `deb` entry. *(P-19, P-20, P-21)*

**Independent Test**: Load a config with a relative `base_dir` → clean config error (102, key named); a download
response with `Retry-After: 86400` retries after at most the cap; a handoff from a space-containing path fails
with the diagnostic and writes no `deb` line (`spec.md` US6).

### Tests for User Story 6 (write FIRST, must FAIL before implementation) ⚠️

- [X] T055 [P] [US6] Unit test: `validate_absolute` with a relative path raises the `ValueError` (so `load_config` wraps it into `ConfigError` naming `[paths] base_dir`); absolute paths unchanged in `tests/unit/test_paths.py` (must FAIL pre-fix: the check never fires — silent CWD reinterpretation) (written red, now green)
- [X] T056 [P] [US6] Unit test: `_backoff_delay` with `Retry-After: 86400` returns ≤ `BACKOFF_CAP` (30 s) and with `Retry-After: 2` returns 2 s in `tests/unit/test_downloader.py` (must FAIL pre-fix: the override is uncapped) (written red, now green)
- [X] T057 [P] [US6] Unit test: `write_sources_entry` with a space-containing `handoff_dir` emits the clear diagnostic (or the generator refuses) and writes **no broken `deb` line**; space-free paths behave unchanged in `tests/unit/test_handoff.py` (must FAIL pre-fix: a broken whitespace-delimited line is written) (written red, now green)
- [X] T058 [P] [US6] Contract test: relative `base_dir` in defaults or local overrides → exit 102 + JSON naming the key; handoff from a space-containing path → exit 1 with the diagnostic in `tests/contract/test_cli.py` (written red, now green)

### Implementation for User Story 6

- [X] T059 [P] [US6] P-19: in `validate_absolute`, check `os.path.isabs(expanded)` on the `os.path.expanduser` result **before** `os.path.abspath`, raising the existing `ValueError` for a relative path (already wrapped into `ConfigError` at `config_loader.py:246-247`) in `src/package_mgmt/lib/util/paths.py`
- [X] T060 [P] [US6] P-20: cap the `Retry-After` override at the internal bound — `return min(BACKOFF_CAP, max(0.0, retry_after))` — in `_backoff_delay` in `src/package_mgmt/lib/service/downloader.py`
- [X] T061 [US6] P-21: in `write_sources_entry` (and/or its generator), detect a space in `$handoff_dir` and refuse with a clear, actionable diagnostic naming the path and the remedy (a space-free path or mount point); never emit a broken `deb` line in `src/package_mgmt/lib/service/handoff.py`
- [X] T062 [US6] Validation: US6 suite green under both runners and `tests/contract/test_cli.py`; ruff clean on the touched files (verified: `pytest` → 707 passed + 56 subtests; `unittest` → Ran 707 tests, OK (47.9s); contract → 84 passed + 15 subtests; ruff clean on all touched files)

**Checkpoint**: US6 is fully functional — no silent misconfiguration, no unbounded stall, no broken deb entry.

---

## Phase 9: User Story 7 - Doc and Test Artifacts Match Behavior (Priority: P3)

**Goal**: The downloader's `_stat_size_matches` docstring and the size-match test name describe the actual
conservative re-hash behavior (a size match does not skip the re-hash); the mirror-normalization tests are
declared under a dedicated `TestMirrorNormalization` class with unchanged coverage. *(P-22, P-23)*

**Independent Test**: The docstring and the renamed size-match test describe the conservative re-hash behavior;
the mirror tests run under `TestMirrorNormalization` with the suite total unchanged (`spec.md` US7).

### Tests for User Story 7 (write FIRST)

- [X] T063 [P] [US7] Unit test: rename the size-match test to describe the conservative behavior (re-hash but no re-fetch), e.g. `test_verified_file_with_matching_size_is_rehashed_but_not_refetched`, and assert the re-hash happens in `tests/unit/test_downloader.py` (done: renamed; `_file_sha256` spy asserts the re-hash runs — green)
- [X] T064 [P] [US7] Unit test: hoist the mirror-normalization tests into a dedicated `TestMirrorNormalization` class (same bodies, same coverage) in `tests/unit/test_config.py` (done: 5 tests hoisted, count unchanged — green)

### Implementation for User Story 7

- [X] T065 [US7] P-22: correct the `_stat_size_matches` docstring to state that a size match short-circuits only the existence/size check and the checksum is **always** re-verified (docs backwards, code right — no behavior change) in `src/package_mgmt/lib/service/downloader.py`
- [X] T066 [US7] P-23: declare the mirror-normalization tests under `TestMirrorNormalization` in `tests/unit/test_config.py` (structural only — test count unchanged)
- [X] T067 [US7] Validation: US7 suite green under both runners; ruff clean on the touched files (verified: `pytest` → 707 passed + 56 subtests; `unittest` → Ran 707 tests, OK (49.0s); ruff clean on all touched files)

**Checkpoint**: US7 is fully functional — labels match behavior and coverage is preserved.

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Release gate — this feature is not complete until this phase passes.

- [X] T068 [P] Run the full suite under both documented runners from a clean checkout (`pytest` and `python -m unittest discover -s tests`) and `pytest tests/contract -q` — all 100% green (baseline 637/637 OK, contract 68 + 15 subtests, plus the new tests from US1–US7) (verified: `pytest` → 707 passed + 56 subtests; `unittest` → Ran 707 tests, OK (48.9s); contract → 84 passed + 15 subtests)
- [X] T069 [P] Run the `specs/019-polish-backlog/quickstart.md` validation scenarios for all seven phases (add→101/init exit split+offline/ensure-base no-DB; verify --graph removed + --package depth + moniker/empty-pin/list mutual exclusion; zero-pending empty diff + build-dep depth; deterministic main-wins resolution; provides no-nested-commit+sweep+circular+status; relative base_dir/Retry-After cap/handoff space diagnostic; doc/test hygiene) — record evidence (verified: per-phase quickstart commands all green — P1 19 passed, P2 82 passed, P3 51 passed, P4 15 passed, P5 64 passed, P6 118 passed, P7 104 passed + 34 subtests; contract suite 84 + 15 subtests; manual CLI: relative `base_dir` → exit 102 naming `[paths] base_dir`)
- [X] T070 [P] Constitution compliance re-check: no schema change, no new runtime dependencies, no new network call sites, contracts extended/updated but never contradicted — cross-check Phases 3–9 against `contracts/cli.md` and `data-model.md`; re-verify the contract deltas hold under both runners (verified: `database.py` untouched across the full feature range; `EXPECTED_SCHEMA_SQL` = the 8 frozen tables; no pyproject/setup/requirements changes; no new network-call primitives in the feature diff; contract deltas hold under both runners)
- [X] T071 [P] Documentation sync: verify `specs/019-polish-backlog/` (plan, research, data-model, contracts, quickstart) plus the updated 014 contract table / 018 quickstart reflect the implemented behavior — including the three clarifications (P-21 space-path refusal, FR-016 `pending → current`, `list` flags mutually exclusive) and the `verify --graph` removal — and that the plan's Phase Map anchors still match the landed code (verified: 019 contracts/data-model/quickstart carry all three clarifications + `--graph` removal; 014 contract `verify` section heading updated to bare `verify`; 018 quickstart uses bare `verify`; a stray `verify --graph` example in 018 `contracts/cli.md` was corrected to bare `verify`; plan Phase Map anchors match the landed code)

**Checkpoint**: Feature complete — all seven phases implemented, all stories independently verified, both runners green.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — baseline verification first
- **Foundational (Phase 2)**: Depends on Setup — guards verified before any story
- **User Stories (Phases 3-9 / US1-US7)**: Depend on Setup + Foundational guards
- **Polish (Phase 10)**: Depends on all seven user stories

### User Story Dependencies (recommended execution order per the plan)

- **US1 (P-1, P-4, P-5 exit-code/side-effect honesty)** — independent; MVP candidate
- **US2 (P-2, P-3, P-7 flags/arguments)** — independent of US1
- **US3 (P-6, P-18 honest reports)** — independent
- **US4 (P-9…P-12, P-16 deterministic resolution)** — independent (`repository.py` + `resolver.py`)
- **US5 (P-13…P-17 database lifecycle)** — **depends on US1** (Phase 1's `ensure_base` DB-creation reorder must
  land before Phase 7's provides-transaction change in the same service); also touches `resolver.py`/`verify.py`
  shared with US4 — sequence US4 → US5
- **US6 (P-19, P-20, P-21 path/download)** — independent
- **US7 (P-22, P-23 hygiene)** — independent (adjacent to US6 only by file, `downloader.py` docstring vs code —
  can land anytime)

> **Note**: US4 and US5 both touch `resolver.py` (+ `verify.py` for P-15's circular report) but disjoint
> functions; keep same-file edits within a story sequential and prefer US4 → US5 order. US1 and US5 both touch
> `ensure_base.py`/`initializer.py` — US1 must land first. US2's `verify --graph` removal must land together with
> the contract-suite update (014/018 refs).

### Within Each User Story

- Tests (required for this feature) MUST be written and FAIL before implementation
- Fixtures/unit tests before implementation; integration/contract validation after
- Story complete and independently green (both runners + contract) before moving to the next

### Parallel Opportunities

- US1, US2, US3, US4, US6, and US7 are mutually independent and can run in parallel after the Foundational guards
- US5 follows US1 (and preferably US4) — sequential
- All tests within a story marked [P] run in parallel

---

## Parallel Example: User Story 6

```bash
# Launch all US6 tests together (must FAIL first):
Task: "relative base_dir rejection tests in tests/unit/test_paths.py"
Task: "Retry-After cap tests in tests/unit/test_downloader.py"
Task: "handoff space-path diagnostic tests in tests/unit/test_handoff.py"
Task: "contract exit-102 + handoff diagnostic tests in tests/contract/test_cli.py"

# After the tests, launch the implementation:
Task: "validate_absolute isabs-before-abspath in src/package_mgmt/lib/util/paths.py"
Task: "Retry-After cap in src/package_mgmt/lib/service/downloader.py"
Task: "write_sources_entry space guard in src/package_mgmt/lib/service/handoff.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1 (exit-code contract + no stray side effects)
   - **Error Handling Check**: reuse `DatabaseError`/`InitError`/exit codes; no new exception types beyond the
     `InitError.permission` flag
   - **Logging Check**: keep existing `logger.warning`/`_print_error` patterns; add the offline-mode summary note
4. **STOP and VALIDATE**: US1 Independent Test (stale-schema add → 101; ENOSPC init → 1; mirror-unreachable init →
   offline note; failed ensure-base → no DB file)
5. Deploy/demo if ready

### Incremental Delivery (per the plan's recommended order)

1. Setup + Foundational → Foundation ready
2. US1 (P-1/P-4/P-5) → test independently (contract exit codes, no stray DB, offline summary) → checkpoint
3. US2 (P-2/P-3/P-7) → test independently (verify --graph gone, --package depth, moniker/empty-pin/list
   mutual-exclusion) → checkpoint
4. US3 (P-6/P-18) → test independently (empty diff, build-dep depth) → checkpoint
5. US4 (P-9…P-12/P-16) → test independently (main wins, deterministic, constraint-aware) → checkpoint
6. US5 (P-13…P-17) — after US1 (+US4) → test independently (provides lifecycle, circular, status) → checkpoint
7. US6 (P-19/P-20/P-21) → test independently (relative base_dir, Retry-After cap, space diagnostic) → checkpoint
8. US7 (P-22/P-23) → test independently (docstring/test-name, test class) → checkpoint
9. Phase 10: completion checklist + both-runners gate

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is confirmed:
   - Developer A: US1 (exit-code/side-effect honesty)
   - Developer B: US2 (flags/arguments)
   - Developer C: US3 (honest reports)
   - Developer D: US4 (deterministic resolution)
   - Developer E: US6 (path/download robustness)
   - Developer F: US7 (hygiene)
3. After US1 (+US4) land: Developer G: US5 (database lifecycle)
4. Each story lands independently; Phase 10 gates the feature

---

## Notes

- [P] tasks = different files, no dependencies (same-file edits within a story are sequential; US1 → US5 ordered)
- [Story] label maps task to the user story in `spec.md`
- Tests required for this feature; write them red first
- All database reads/writes go through the frozen spec 010/011 schema; **no schema changes** (Constitution VII);
  no new runtime dependencies; no new network call sites
- Contract-preserving: 014 exit-code table extended for US1/US2/US6; `verify --graph` refs updated (014/018);
  the three clarifications (P-21, FR-016, P-7b) are locked in the spec `## Clarifications`
- Commit after each task or logical group; all changes land on `main`
- Verify each phase under BOTH documented runners before moving on
