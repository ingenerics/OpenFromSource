---

description: "Task list for Operator Track & CLI/Config Robustness"

---

# Tasks: Operator Track & CLI/Config Robustness

**Input**: Design documents from `/specs/018-operator-cli-robustness/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (`cli.md`), quickstart.md

**Tests**: Tests ARE required. The spec's "User Scenarios & Testing (mandatory)" mandates independent tests +
acceptance scenarios per story, and the project convention (015/016/017) ships every phase with its regression
tests. Tests MUST be written and FAIL before implementation, and must be green under **both** documented runners
(`pytest` and `python -m unittest discover -s tests`) at every phase.

**Organization**: Five phases (US1 handoff exclusion, US2 clean apply output, US3 config type validation, US4
local-override adoption, US5 read-command gating), grouped by user story so each story is independently
implementable and testable. All five stories are Priority P2; the phase order follows the spec's story order
and the plan's Phase Map.

**Clarifications incorporated (spec `## Clarifications` / Session)**:
- US3 (F-20): unknown/mistyped config key names are **rejected** as structured config errors (exit 102, JSON,
  naming the key) — no silent ignore. US3 tasks and the 018 contract Phase 3 reflect this.
- US2 (F-19): the apply count line is exactly `New files: <number>` and the already-applied state is reported
  on a **separate line**. US2 tasks and the 013 contract reflect this.

**Cross-cutting constraints (all phases)**:
- **No schema change** — the spec 010/011 freeze is absolute; every fix is behavioral
  (`specs/018-operator-cli-robustness/data-model.md`).
- The 014 exit-code/JSON contract table is **extended**, never contradicted, for F-20/F-21/F-22; the 012
  (handoff) and 013 (apply) operator-script contracts are **updated** for F-18/F-19 (docs inheritance);
  `tests/contract/test_cli.py` stays green throughout.
- No new runtime dependencies; no new network call sites (F-19 merely redirects the operator's existing
  `apt update` output). Work lands on `main` in the same alpha release (after 016/017).
- Unknown config keys stay silently ignored **unless** noted otherwise — US3 explicitly changes that to a
  structured rejection per the clarification.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US5)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Baseline & Environment)

**Purpose**: Establish a verified baseline before any handoff/config/CLI change — confirms both documented
runners execute, operator-script prerequisites are available, and the repo reflects the post-clarify state the
plan's line numbers assume.

- [X] T001 Verify baseline test state: run both documented runners (`pytest` and `python -m unittest discover -s tests`) from a clean checkout; record the green baseline as the Phase 8 gate baseline — **baseline this cycle: `pytest` → 608 collected; `python -m unittest discover -s tests` → Ran 608 tests, OK (44.6s); `pytest tests/contract/test_cli.py -q` → 60 passed + 11 subtests** (verified: pytest → 608 passed + 41 subtests; unittest → Ran 608 tests, OK; contract → 60 passed + 11 subtests)
- [X] T002 [P] Verify the offline-safe test prerequisites per `specs/018-operator-cli-robustness/quickstart.md`: `bash`, `apt-get`, `gpg`, `gpgv` are on PATH (the operator-script and handoff tests depend on them) and the local fixtures (`tests/fixtures/make_test_iso.py` with `write_archive`, `write_pool_files`) import cleanly (all four tools on PATH; fixtures import cleanly)
- [X] T003 [P] Verify repo state: on branch `main` with the venv active, and confirm the 018 spec's `## Clarifications` / Session reflects the two confirmed decisions (unknown-key rejection; already-applied separate line) — the tasks below depend on them (branch `main`, venv Python 3.13.5; spec Clarifications confirm both decisions)

---

## Phase 2: Foundational (Cross-Cutting Guards)

**Purpose**: Lock in the guards every phase must respect. No new error-handling/exception infrastructure is
required — the feature reuses the existing `_print_error`/exit-code machinery (`ConfigError`, exit 102; the
shared `validate_schema_or_error`, exit 101) and the existing `HandoffError`/`HandoffStats` types.

**⚠️ CRITICAL**: These guards are prerequisite evidence — verify them before any phase, re-verify in Phase 8.

- [X] T004 Confirm the schema-freeze baseline: the feature changes no tables/columns; record that NO phase may add/alter columns — all fixes are behavioral (`specs/018-operator-cli-robustness/data-model.md`); `EXPECTED_SCHEMA_SQL` defines exactly the 8 frozen tables (`packages`, `binaries`, `sources`, `binary_to_source`, `source_build_deps`, `provides`, `virtual_resolutions`, `systems`) (confirmed: `database.py:EXPECTED_SCHEMA_SQL` — exactly those 8 tables; feature is behavioral only)
- [X] T005 [P] Confirm the baseline exit-code / JSON contracts from `specs/014-base-profiles/contracts/cli.md` hold (exit `0`/`1`/`101`/`102`, JSON-on-stderr) and that the 018 contract (`specs/018-operator-cli-robustness/contracts/cli.md`) extends, never contradicts, them — `pytest tests/contract/test_cli.py -q` green (60 passed + 11 subtests; 014 exit codes 0/1/101/102 + JSON-on-stderr hold)
- [X] T006 [P] Record the pre-fix behavior baselines for US1–US5 with exact anchors (from `review/04_alpha_summary/02_high_priority_followups.md` and `specs/018-operator-cli-robustness/research.md`): `generate_handoff` has no `base_set` membership check (`lib/service/handoff.py:1190-1227`); `APPLY_SCRIPT` `apply_configure`/`run_apt_update` let `apt update` stdout leak into `$new_files` (`lib/service/handoff.py:455-518, 554`); only the two depth keys are type-validated and unknown keys are silently ignored (`lib/service/config_loader.py:74-89, 175-181, 186-189`); a non-table `archive` raises `AttributeError` (`config_loader.py:192`); local overrides apply only `if tup in defaults_flat` (`config_loader.py:175-181`); `show`/`list`/`verify` open the DB directly (`cli/commands/show.py:133`, `cli/commands/list.py:14`, `cli/commands/verify.py:18`) (all anchors verified in source)

**Checkpoint**: Guards verified — user story phases may proceed.

---

## Phase 3: User Story 1 - Handoff Reports Out-of-Config Records Loudly (Priority: P2) 🎯 MVP

**Goal**: `generate_handoff` excludes — loudly — any verified record whose `(release, component)` is no longer
in the currently configured set (mirroring `generate_diff`), reports the exclusion in the summary, and never
marks those records delivered. Exclusion is derived from the live config each run, so restoring the component
re-includes the record. *(F-18)*

**Independent Test**: Register and verify a record under `trixie/contrib`, regenerate the handoff with the
configuration reduced to `main` — the record is omitted from the output, reported loudly, and its delivery id
is not advanced (`spec.md` US1).

### Tests for User Story 1 (write FIRST, must FAIL before implementation) ⚠️

- [X] T007 [P] [US1] Unit test: a verified record under `trixie/contrib` with the config reduced to `main`-only → `generate_handoff` omits it from every `Packages.gz`, reports it (new `HandoffStats.excluded_config`), and leaves it out of `delivered_ids` in `tests/unit/test_handoff.py` (must FAIL pre-fix: the record currently passes every check and is marked delivered)
- [X] T008 [P] [US1] Unit test: a mix of in- and out-of-config verified records → only the in-config records appear and are marked delivered; restoring the component to the config and regenerating re-includes the record (exclusion is non-permanent) in `tests/unit/test_handoff.py`
- [X] T009 [P] [US1] Integration test: via the CLI flow, `handle_handoff` prints the exclusion Warning line in the summary and the excluded record's `sync_mask` bit for the system is unchanged in `tests/integration/test_handoff_flow.py` (must FAIL pre-fix: the record is silently marked delivered)

### Implementation for User Story 1

- [X] T010 [US1] Add `base_set = set(bases)` in `generate_handoff` and exclude any record whose `(release, component)` is not in it — `logger.warning(...)` per record, append to `excluded_config`, `continue` before the stanza/delivery append (mirror `generate_diff`'s membership check) in `src/package_mgmt/lib/service/handoff.py`
- [X] T011 [US1] Add `excluded_config: list[str] = field(default_factory=list)` to the `HandoffStats` dataclass and populate it from `generate_handoff` (in-memory report only; no schema change) in `src/package_mgmt/lib/service/handoff.py`
- [X] T012 [P] [US1] Print the exclusion as a visible Warning line in `_print_summary` (mirror the existing `excluded_pool` Warning) when `stats.excluded_config` is non-empty in `src/package_mgmt/cli/commands/handoff.py`
- [X] T013 [P] [US1] Docs: document the out-of-config exclusion Warning in the `package_mgmt handoff` section of `specs/012-bootstrap-operator-system/contracts/cli.md` (loud report, delivery id not advanced, non-permanent)
- [X] T014 [US1] Validation: US1 suite green under both runners and `tests/contract/test_cli.py`; ruff clean on the touched files

**Checkpoint**: US1 is fully functional — a config shrink no longer hides packages from the operator or poisons the delivery record.

---

## Phase 4: User Story 2 - Apply Output Is a Clean Count (Priority: P2)

**Goal**: The operator `apply.sh` keeps `apply_configure`'s stdout reserved for the copied-file count — apt/progress output is routed to a transient log file, so a successful apply prints no shell integer-comparison error, the summary count line is exactly `New files: <number>`, and the already-applied state is reported on a separate line. A zero-file diff reports `0`; an apt failure tails the log and leaves the merged state in place. *(F-19)*

**Independent Test**: Run the generated `apply.sh` against a fixture diff — stderr is empty and the summary matches `New files: <number>`; re-running reports the already-applied path cleanly (`spec.md` US2).

### Tests for User Story 2 (write FIRST, must FAIL before implementation) ⚠️

- [X] T015 [US2] Unit (shell) test: success-path `apply_configure` against a scratch apt state root (real generated handoff + diff, `file:` source) → stdout is exactly the count, stderr empty, apt output lands in the log file, no `[: …: integer expression expected` in `tests/unit/test_apply_script.py` (must FAIL pre-fix: apt stdout leaks into the count capture)
- [X] T016 [P] [US2] Unit (shell) test: re-running `apply_configure` on an already-applied diff returns count `0`; the generated script's summary block is asserted against the contract — count line exactly `New files: <number>` with the already-applied state on a separate line, and a zero-file diff reports `0` in `tests/unit/test_apply_script.py`
- [X] T017 [P] [US2] Integration test: the generated `apply.sh`'s `apply_configure` against a fixture diff (state root) → stderr empty, summary matches `New files: <number>`; re-run reports already-applied cleanly in `tests/integration/test_diff_flow.py`
- [X] T018 [P] [US2] Unit test: update `ApplyScriptAptFailureTest.test_apt_update_failure_leaves_merged_state_in_place` in `tests/unit/test_apply_script.py` to assert the new failure message naming the log file (plus its tailed excerpt) and the merged files/indexes left in place for inspection

### Implementation for User Story 2

- [X] T019 [US2] Route apt output to a transient log file in the `APPLY_SCRIPT` template: `run_apt_update` runs `apt-get … update > "<log>" 2>&1` (e.g. `$cache_dir/diff/.apply-apt.log`), returning apt's exit code, so `apply_configure`'s stdout carries only the count; on apt failure print `apt update failed — see <log> (excerpt below); the merged files and indexes were left in place for inspection.` plus a bounded `tail` of the log in `src/package_mgmt/lib/service/handoff.py`
- [X] T020 [US2] Update the `apply_main` summary in the `APPLY_SCRIPT` template: count line exactly `New files: <number>`; when the count is 0, report the already-applied state on a separate line (e.g. `Already applied: all new files already present in the pool`); exit code stays `0` for merged and already-applied in `src/package_mgmt/lib/service/handoff.py`
- [X] T021 [P] [US2] Docs: update the `apply.sh` Output (stdout) and error-message sections of `specs/013-sync-new-packages/contracts/cli.md` — exact `New files: <number>` count line, separate already-applied line, and the apt-failure message naming the log
- [X] T022 [US2] Validation: US2 suite green under both runners and `tests/contract/test_cli.py`; ruff clean on the touched files

**Checkpoint**: US2 is fully functional — the operator's merge step prints a clean, parseable count and never a shell error.

---

## Phase 5: User Story 3 - Mistyped Config Values Fail Structurally (Priority: P2)

**Goal**: Every configuration value is type-validated against a declarative type map and every key must be
recognized; a mistyped value or an unknown key name yields a `ConfigError` naming the key (and, for a value,
the expected type), routed through the existing exit-102 + JSON path — never a raw traceback. Booleans accept
only real TOML booleans. Correctly-typed config loads unchanged. *(F-20 + clarification: unknown keys rejected)*

**Independent Test**: A parametrized set of mistyped values (wrong-type scalar, non-table table, wrong-type path, string as boolean) and unknown key names — each yields the structured config error with the key named and the JSON payload emitted; a `"yes"` string for a boolean setting is rejected (`spec.md` US3).

### Tests for User Story 3 (write FIRST, must FAIL before implementation) ⚠️

- [X] T023 [P] [US3] Unit test: parametrized mistyped values (`[paths] base_dir = 123`, non-table `archive = 5`, `[paths] binary_cache = 5`, `[fetch] source = "yes"`) → `ConfigError` naming the key and expected type in `tests/unit/test_config.py` (must FAIL pre-fix: `TypeError`/`AttributeError` traceback or silent acceptance)
- [X] T024 [P] [US3] Unit test: `"yes"`/`"true"` strings for `fetch.source` rejected; TOML `true`/`false` accepted (regression) in `tests/unit/test_config.py`
- [X] T025 [P] [US3] Unit test: unknown/mistyped key names (e.g. `binry_cache`, `max_source_dep_th`) → `ConfigError` naming the key (no silent ignore) in `tests/unit/test_config.py` (per the clarification)
- [X] T026 [P] [US3] Contract test: mistyped values and unknown keys → exit 102, JSON payload on stderr naming the key, and `handle_config("json")` emits the payload in `tests/contract/test_cli.py`
- [X] T027 [P] [US3] Unit test: correctly-typed configuration values load unchanged (regression) in `tests/unit/test_config.py`

### Implementation for User Story 3

- [X] T028 [US3] Add `_TYPE_MAP` beside `_KEY_MAP` declaring the expected type for every known key (`str` for path/log keys, `bool` for `fetch.source`, `int` for the two depth keys) in `src/package_mgmt/lib/service/config_loader.py`
- [X] T029 [US3] Validate in the merged loop of `load_config`: raise `ConfigError("[<section>] <key> must be <type>, got: <value!r>")` on a type mismatch; reject **unknown keys** with a structured error naming the key; booleans accept only `type(value) is bool`; guard a non-table `archive` (and non-dict local `[archive]`) as a `ConfigError` naming the key; wrap the `validate_absolute` `ValueError` into `ConfigError`; keep `_validate_depth_settings` for the `>= -1` range rule in `src/package_mgmt/lib/service/config_loader.py`
- [X] T030 [P] [US3] Docs: document the unknown-key rejection in the Phase 3 section of `specs/018-operator-cli-robustness/contracts/cli.md` (exit 102, JSON naming the key; unknown keys no longer silently ignored)
- [X] T031 [US3] Validation: US3 suite green under both runners and `tests/contract/test_cli.py`; ruff clean on the touched files

**Checkpoint**: US3 is fully functional — every config failure is structured, typed, and names the offending key.

---

## Phase 6: User Story 4 - Local Overrides Always Apply and Validate (Priority: P2)

**Goal**: A local override for a **known** key applies even when `project.defaults.toml` lacks the section, and
is flagged as an override; the merged result is validated after overrides are applied, so a bad depth in a
local override fails at load naming the key. `[[archive.source]]` replacement semantics are preserved;
unknown local keys stay ignored. *(F-21)*

**Independent Test**: Defaults without the `[resolution]` section and a local `-2` depth — loading fails with a
structured error naming the key; with a local `2`, the override is applied and flagged as an override
(`spec.md` US4).

### Tests for User Story 4 (write FIRST, must FAIL before implementation) ⚠️

- [X] T032 [P] [US4] Unit test: defaults without `[resolution]` + local valid depth (e.g. `2`) → override applied and flagged in `overridden_keys` in `tests/unit/test_config.py` (must FAIL pre-fix: the override is silently dropped)
- [X] T033 [P] [US4] Unit test: defaults without `[resolution]` + local bad depth (`-2`) → `ConfigError` naming the key in `tests/unit/test_config.py` (must FAIL pre-fix: the bad value is dropped before validation)
- [X] T034 [P] [US4] Contract test: `handle_config("json")` with a local depth override in an absent section → override flag true; with an invalid local depth → exit 102 + JSON naming the key in `tests/contract/test_cli.py`
- [X] T035 [P] [US4] Regression test: `[[archive.source]]` replacement from the local file behaves exactly as before (replaces the defaults' sources) in `tests/unit/test_config.py`

### Implementation for User Story 4

- [X] T036 [US4] Change the local-overlay gate in `load_config` from `if tup in defaults_flat` to `if tup in _KEY_MAP` (adopt known-key overrides regardless of defaults section presence; `overridden_keys` flagged as today; unknown local keys ignored; the `archive` section still skipped by `_flatten` and handled by the dedicated replacement logic) in `src/package_mgmt/lib/service/config_loader.py`
- [X] T037 [US4] Validation: US4 suite green under both runners and `tests/contract/test_cli.py`; ruff clean on the touched files

**Checkpoint**: US4 is fully functional — overrides in trimmed/profile drop-in defaults apply and validate.

---

## Phase 7: User Story 5 - Read Commands Gate on Cache Initialization (Priority: P2)

**Goal**: `show`, `list`, and `verify` route through the shared `validate_schema_or_error` gate their siblings
use: on an uninitialized cache they return the clean error + exit 101 and create **no database file**; on a
schema-mismatched cache they take the identical rebuild path. A valid initialized cache behaves unchanged. *(F-22)*

**Independent Test**: Run `show`/`list`/`verify` with no database present — each exits 101, prints a clean
message, and leaves no database file behind; a schema-mismatched database takes the shared stale-schema path
(`spec.md` US5).

### Tests for User Story 5 (write FIRST, must FAIL before implementation) ⚠️

- [X] T038 [P] [US5] Unit test: `handle_list_deps` with no database (both the `--status` and `--deps` branches) → exit 101, clean message, **no database file created** in `tests/unit/test_list.py` (must FAIL pre-fix: `sqlite3.connect` creates a 0-byte file)
- [X] T039 [P] [US5] Unit test: `handle_verify_graph` with no database → exit 101, clean message, no database file created in `tests/unit/test_verify.py`
- [X] T040 [P] [US5] Contract test: `show`/`list`/`verify` with no database → exit 101, JSON error on stderr, directory listing unchanged (no file created); a schema-mismatched database → the shared rebuild message in `tests/contract/test_cli.py`
- [X] T041 [P] [US5] Regression test: a valid initialized cache → `show`/`list`/`verify` output and exit unchanged in `tests/unit/test_list.py` / `tests/unit/test_verify.py` / `tests/contract/test_cli.py`

### Implementation for User Story 5

- [X] T042 [US5] Call `validate_schema_or_error(db_path)` at the top of `handle_show` (before any `sqlite3.connect`) and return its exit code on failure in `src/package_mgmt/cli/commands/show.py`
- [X] T043 [P] [US5] Call `validate_schema_or_error(db_path)` at the top of `handle_list_deps` (before the `--status`/`--deps` branches) and return its exit code on failure in `src/package_mgmt/cli/commands/list.py`
- [X] T044 [P] [US5] Call `validate_schema_or_error(db_path)` at the top of `handle_verify_graph` and return its exit code on failure in `src/package_mgmt/cli/commands/verify.py`
- [X] T045 [US5] Validation: US5 suite green under both runners and `tests/contract/test_cli.py`; ruff clean on the touched files

**Checkpoint**: US5 is fully functional — the read commands behave exactly like their gated siblings and create no stray file.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Release gate — this feature is not complete until this phase passes.

- [X] T046 [P] Run the full suite under both documented runners from a clean checkout (`pytest` and `python -m unittest discover -s tests`) — both must be 100% green (baseline 608 / 608 OK, contract 60 + 11 subtests, plus the new tests from US1–US5)
- [X] T047 [P] Run the `specs/018-operator-cli-robustness/quickstart.md` validation scenarios for all five phases (handoff out-of-config exclusion; clean apply count incl. already-applied and zero-file; mistyped/unknown config keys → 102; absent-section local override; show/list/verify gating with no file created) — record evidence
- [X] T048 [P] Constitution compliance re-check: no schema change, no new runtime dependencies, no new network call sites, contracts extended/updated but never contradicted — cross-check Phases 3–7 against `contracts/cli.md` and `data-model.md`; re-verify the contract deltas hold under both runners
- [X] T049 [P] Documentation sync: verify `specs/018-operator-cli-robustness/` (plan, research, data-model, contracts, quickstart) plus the updated 012/013/014 contract docs reflect the implemented behavior — including the two clarifications (unknown-key rejection; already-applied separate line) — and that the plan's Phase Map anchors still match the landed code

**Checkpoint**: Feature complete — all five phases implemented, all stories independently verified, both runners green.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — baseline verification first
- **Foundational (Phase 2)**: Depends on Setup — guards verified before any story
- **User Stories (Phase 3+ / US1–US5)**: Depend on Setup + Foundational guards
- **Polish (Phase 8)**: Depends on all five user stories

### User Story Dependencies (recommended execution order per the plan)

- **US1 (F-18 handoff exclusion)** — independent (`generate_handoff` in `lib/service/handoff.py`); MVP candidate
- **US2 (F-19 clean apply)** — independent of US1 (different region of `lib/service/handoff.py`: the `APPLY_SCRIPT` template vs `generate_handoff`); can run in parallel with US1
- **US3 (F-20 config types)** — independent (`config_loader.py`)
- **US4 (F-21 override adoption)** — **depends on US3** (both touch `config_loader.py`; US4's newly-adopted overrides are validated by US3's type map — sequential within that file)
- **US5 (F-22 read-command gating)** — independent (`show.py`/`list.py`/`verify.py`)

> **Note**: US1 and US2 both touch `src/package_mgmt/lib/service/handoff.py` but disjoint regions
> (`generate_handoff` + `HandoffStats` vs the `APPLY_SCRIPT` string template); keep same-file edits within a
> story sequential, but the two stories can land in either order. US3 → US4 must be strictly ordered.

### Within Each User Story

- Tests (required for this feature) MUST be written and FAIL before implementation
- Fixtures/unit tests before implementation; integration/contract validation after
- Story complete and independently green (both runners + contract) before moving to the next

### Parallel Opportunities

- US1 (handoff service + CLI), US2 (apply template), US3 (config types), and US5 (read commands) are mutually independent and can run in parallel after the Foundational guards
- US4 follows US3 (same file, sequential)
- All tests within a story marked [P] run in parallel

---

## Parallel Example: User Story 3

```bash
# Launch all US3 tests together (must FAIL first):
Task: "parametrized mistyped-value ConfigError tests in tests/unit/test_config.py"
Task: "boolean strictness tests in tests/unit/test_config.py"
Task: "unknown-key rejection tests in tests/unit/test_config.py"
Task: "contract exit-102 tests in tests/contract/test_cli.py"

# After the tests, launch the implementation:
Task: "_TYPE_MAP + merged-loop validation + unknown-key rejection in src/package_mgmt/lib/service/config_loader.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1 (handoff out-of-config exclusion)
   - **Error Handling Check**: reuse `HandoffError`/`HandoffStats`; the exclusion is a Warning (exit 0), not a command error
   - **Logging Check**: per-record `logger.warning` + the summary Warning line (loud, but non-fatal)
4. **STOP and VALIDATE**: US1 Independent Test (record under a removed component → omitted, reported, not delivered)
5. Deploy/demo if ready

### Incremental Delivery (per the plan's recommended order)

1. Setup + Foundational → Foundation ready
2. US1 (F-18) → test independently (out-of-config record omitted + reported + not delivered) → checkpoint
3. US2 (F-19) → test independently (stderr empty, `New files: <number>`, already-applied separate line) → checkpoint
4. US3 (F-20) → test independently (mistyped/unknown keys → exit 102 + JSON) → checkpoint
5. US4 (F-21) — after US3 → test independently (absent-section override applies + validates) → checkpoint
6. US5 (F-22) → test independently (show/list/verify → 101, no file created) → checkpoint
7. Phase 8: completion checklist + both-runners gate

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is confirmed:
   - Developer A: US1 (handoff exclusion)
   - Developer B: US2 (apply template)
   - Developer C: US3 (config types)
   - Developer D: US5 (read-command gating)
3. After US3 lands: Developer C continues US4 (override adoption)
4. Each story lands independently; Phase 8 gates the feature

---

## Notes

- [P] tasks = different files, no dependencies (same-file edits within a story are sequential; US3 → US4 ordered)
- [Story] label maps task to the user story in `spec.md`
- Tests required for this feature; write them red first
- All database reads/writes go through the pre-populated schema; **no schema changes** (Constitution VII + spec 010/011 freeze); no new runtime dependencies; no new network call sites
- Contract-preserving: 014 extended for F-20/F-21/F-22; 012/013 operator contracts updated for F-18/F-19 (docs inheritance)
- Commit after each task or logical group; all changes land on `main`
- Verify each phase under BOTH documented runners before moving on
