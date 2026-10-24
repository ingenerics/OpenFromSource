---

description: "Task list for Resolver & Dependency Correctness"

---

# Tasks: Resolver & Dependency Correctness

**Input**: Design documents from `/specs/016-resolver-dependency-correctness/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (`cli.md`), quickstart.md

**Tests**: Tests ARE required. The spec's "User Scenarios & Testing (mandatory)" and the plan's per-phase Tests mandate them; the project convention (015) ships every phase with its regression tests. Tests MUST be written and FAIL before implementation.

**Organization**: Six resolver/dependency phases (F-8, F-9+F-10, F-11, F-12, F-13, F-15), grouped by user story so each story is independently implementable and testable. The shared parser/model work (US3+F-11, US4+F-12) is the foundation and — per the plan's clustering directive — is implemented **first** as one commit set with one fixture pack; its dependency on the execution order is spelled out in the Dependencies section.

**Cross-cutting constraints (all phases)**:
- **No schema change** — the spec 010/011 freeze is absolute; only serialized dependency JSON (internal shape) changes, with a backward-compatible reader (see `specs/016-resolver-dependency-correctness/data-model.md`).
- Contracts in `specs/014-base-profiles/contracts/cli.md` and `specs/016-resolver-dependency-correctness/contracts/cli.md` hold or are extended, never contradicted.
- Every change lands with its regression tests and must be green under **both** documented runners: `pytest` and `python -m unittest discover -s tests`.
- No new runtime dependencies; no network calls added; work lands on `main` in the same alpha release (after 015).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US6)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Baseline & Environment)

**Purpose**: Establish a verified baseline before any resolver change — confirms both documented runners execute, fixtures are available, and the repo reflects the post-015 state the plan's line numbers assume.

- [X] T001 Verify baseline test state: run both documented runners (`pytest` and `python -m unittest discover -s tests`) from a clean checkout; record the green baseline (expected: the full 015 suite passes under both) as the Phase 9 gate baseline — **evidence: pytest 531 passed + 41 subtests (328.97s); `python -m unittest discover -s tests`: Ran 531 tests, OK (318.37s), exit 0**
- [X] T002 [P] Verify the offline-safe test prerequisites per `specs/016-resolver-dependency-correctness/quickstart.md`: the local mirror fixture (`tests/fixtures/make_test_iso.py`, incl. the backports variant used at `tests/unit/test_handoff.py:505-507`) and the hermetic apt harness (`tests/integration/test_handoff_flow.py`) runnable — **evidence: `write_archive` (make_test_iso.py:175) + `write_backports_archive` (make_test_iso.py:250) present; `test_handoff_flow.py`/`test_download_flow.py`/`signed_archive.py` present; fixtures import OK**
- [X] T003 [P] Verify repo state: on branch `main` with the venv active, and confirm 015's resolver work is present (e.g. `_batch_levels`, `_maybe_repair_min_level`, `_re_seed_batch_state_from_db` in `src/package_mgmt/lib/service/resolver.py`) — the current line numbers in this task list depend on it — **evidence: branch `main`, clean tree, venv present, 13 matches for the 015 symbols in resolver.py**

---

## Phase 2: Foundational (Cross-Cutting Guards)

**Purpose**: Lock in the three guards every phase must respect. No new error-handling/exception infrastructure is required — this feature reuses the existing `_print_error`/exit-code machinery.

**⚠️ CRITICAL**: These guards are prerequisite evidence — verify them before any phase, re-verify in Phase 9.

- [X] T004 Confirm the schema-freeze baseline: current SQLite tables match spec 010/011 (`packages`, `binaries`, `sources`, `binary_to_source`, `source_build_deps`, `provides`, `virtual_resolutions`, `systems`); record that NO phase may add/alter columns — the dependency-model change is serialized-JSON only (`specs/016-resolver-dependency-correctness/data-model.md`) — **evidence: `EXPECTED_SCHEMA_SQL` in `src/package_mgmt/lib/model/database.py` defines exactly those 8 tables; recorded as the freeze guard**
- [X] T005 [P] Confirm the baseline exit-code / JSON contracts from `specs/014-base-profiles/contracts/cli.md` hold (exit `0`/`1`/`101`/`102`, JSON-on-stderr) — the guard US1 and US5 must preserve — **evidence: `tests/contract/test_cli.py` → 58 passed, 11 subtests (exit-code + JSON-error contract tests green)**
- [X] T006 [P] Record baseline evidence for US3/US4/US5: today `parse_depends_line` (`src/package_mgmt/lib/model/dependency.py:51-86`) drops non-first-alternative constraints and `[arch]`/multi-profile tokens, and `_dep_satisfied` (`src/package_mgmt/cli/commands/verify.py:135-157`) answers on presence for the direct-name path — the behaviors those phases change — **evidence (pre-fix, live): `foo (= 9.9) | bar (= 2.0)` → alternatives `('bar',)` (bar's `(= 2.0)` dropped); `gperf [amd64 i386]` → 0 specs (silently dropped); `debhelper-compat (= 13) <!nocheck !cross>` → 0 specs (silently dropped); `_dep_satisfied('libfoo 1.5' vs `>= 2.0`)` → True (presence-only)**

**Checkpoint**: Guards verified — user story phases may proceed.

---

## Phase 3: User Story 1 - Resolved Alternative Groups Exit Cleanly (Priority: P1) 🎯 MVP

**Goal**: A request whose alternative group resolves (first alternative absent but a later member present) exits 0 with zero errors; `--file` counts it resolved. An error is recorded only when the whole group fails. *(F-8)*

**Independent Test**: Add a fixture `a | b` where only `b` exists — the closure contains `b`, `errors == []`, and `add` exits 0; a batch `--file` run over mixed entries shows zero errors for the resolved group (`spec.md` US1).

### Tests for User Story 1 (write FIRST, must FAIL before implementation) ⚠️

- [X] T007 [P] [US1] Unit test: `a | b` with only `b` present → `resolve_batch` returns an empty error list and the closure contains `b` in `tests/unit/test_resolver_batch.py` — **evidence: `TestAlternativeGroupResolution::test_group_satisfied_by_later_alternative_is_clean` passes**
- [X] T008 [P] [US1] Unit test: `a | b | c` satisfied by a later alternative → clean; `a | b` with **both** absent → exactly one group error in `tests/unit/test_resolver_batch.py` — **evidence: `test_group_satisfied_by_third_alternative_is_clean` + `test_whole_group_failure_reports_exactly_one_error` pass**
- [X] T009 [P] [US1] Integration test: `--file` manifest with a satisfied alternative group → summary shows the request resolved and `Errors: 0` in `tests/unit/test_add_file.py` — **evidence: `AddFileBatchFlowTest` (`test_satisfied_alternative_group_counts_as_resolved`, `test_fully_failed_group_counts_as_failed_with_one_error`) pass**

### Implementation for User Story 1

- [X] T010 [US1] Implement the alternative-group error rollback in `_resolve_one_inner` (`src/package_mgmt/lib/service/resolver.py:900-935`): snapshot `error_mark = len(stats["errors"])` before the primary `_resolve_one` call; when the group is satisfied (primary resolved, an alternative present/satisfied, or an alternative resolves) truncate `del stats["errors"][error_mark:]`; keep the primary/group error only when every member fails; preserve per-segment attribution and `_segment_failed` bookkeeping — **implemented: per-alternative `alt_mark` snapshot preserves a resolved member's own subtree errors (only failed members' errors are retracted); a fully-failed group reports exactly one `Alternative group 'a | b' could not be resolved` error**
- [X] T011 [US1] Regression validation: a mixed batch (satisfied group next to a genuinely failing segment) attributes errors only to the failing segment; `add`'s exit mapping (`src/package_mgmt/cli/commands/add.py:138-143`, `--file` summary `182-212`) is correct once errors are truthful — assert against `specs/016-resolver-dependency-correctness/contracts/cli.md` Phase 1 — **evidence: `test_mixed_batch_attributes_errors_only_to_failing_segment`; `tests/contract/test_cli.py` → 58 passed, 11 subtests; `pytest tests/unit tests/contract` → 480 passed + 41 subtests; `python -m unittest` on the resolver/add-file/verify modules → OK**

**Checkpoint**: US1 is fully functional — a satisfied alternative group never reports failure; errors appear only when the whole group fails.

---

## Phase 4: User Story 2 - Binaries Link to the Exact Source Version and Release (Priority: P1)

**Goal**: Every binary links to its exact source version in its own release/component — never a version/release-blind guess — with divergence logged when a fallback is used. *(F-9+F-10)*

**Independent Test**: With source versions `{1.0-1, 2.0-1}` and a binary `foo-bin 1.0-1+b1` declaring `Source: foo (1.0-1)`, resolution links `foo 1.0-1`; a backports fixture links its source from the backports release (`spec.md` US2).

### Tests for User Story 2 (write FIRST, must FAIL before implementation) ⚠️

- [X] T012 [P] [US2] Unit test: sources `{foo 1.0-1, foo 2.0-1}` + binary `foo-bin 1.0-1+b1` with `Source: foo (1.0-1)` → links `foo 1.0-1` (not 2.0-1) in `tests/unit/test_source_linkage.py` (new module) — **evidence: `TestExactSourceVersionLinkage::test_declared_source_version_wins_over_newest` passes (was red pre-fix: linked 2.0-1)**
- [X] T013 [P] [US2] Unit test: no declared `Source:` version + binary version `x.y-z+b1` → derived source version `x.y-z` (binNMU `+bN` stripped, reusing `src/package_mgmt/lib/util/versions.py:44-52`) in `tests/unit/test_source_linkage.py` — **evidence: `test_bin_nmu_derived_version_when_no_declared_source_version` passes**
- [X] T014 [P] [US2] Unit test: backports binary → source resolved from the backports release, not the primary; exact version absent → newest-in-release fallback **and** a divergence `WARNING` (via `assertLogs`) in `tests/unit/test_source_linkage.py` — **evidence: `TestBackportsSourceLinkage::test_backports_binary_links_source_from_its_own_release` + `test_exact_version_absent_falls_back_with_divergence_warning` pass**

### Implementation for User Story 2

- [X] T015 [P] [US2] Add `RepositoryIndex.lookup_source_exact(name, version, release=None, component=None) -> SourcePackage | None` (exact `(name, version)` match, optional release/component scoping, no version ordering) to `src/package_mgmt/lib/model/repository.py` (next to `lookup_source`/`lookup_source_by_source`, `53-67`) — **implemented after `lookup_source_by_source`**
- [X] T016 [US2] Rework `_register_source_for` (`src/package_mgmt/lib/service/resolver.py:426-467`): use the `Source:` field's declared version, else derive via `source_version_from_binary`; look up exact in the binary's own `(release, component)` → primary release → anywhere, then newest-in-release fallback with a logged divergence warning; set `src_version`/`src_release`/`src_component` from the chosen entry (depends on T015) — **implemented; divergence is `logger.warning` (log-only per contracts/cli.md diagnostics note)**
- [X] T017 [US2] Regression: when no source exists at all, keep the existing fallback (derived version + binary's release/component, `resolver.py:463-465`); assert the backports linkage end-to-end in `tests/unit/test_handoff.py` (uses the existing backports fixture at `test_handoff.py:505-507`) — **evidence: `test_no_source_exists_keeps_derived_version_fallback` (green pre- and post-fix); `TestMultiReleaseHandoff::test_backports_binary_resolves_source_from_backports_release` (resolves `backport-tool` through the full index → source record in `trixie-backports`); `pytest tests/unit tests/contract` → 486 passed + 41 subtests; `python -m unittest` on test_source_linkage/test_resolver_batch → OK**

**Checkpoint**: US2 is fully functional — exact `(name, version)` source linkage in the binary's own release; divergence is never silent.

---

## Phase 5: User Story 3 - Every Alternative Keeps Its Own Version Constraint (Priority: P2)

**Goal**: Every alternative in an `a | b` group keeps its own version constraint through parse, store, and resolve; a group is satisfied only when a member satisfies its own constraint. *(F-11 — the shared model foundation)*

**Independent Test**: Round-trip `foo (= 9.9) | bar (= 2.0)` preserving `bar`'s constraint; resolve a group where only an unsatisfying-version alternative exists and assert an error with no silent link (`spec.md` US3).

### Tests for User Story 3 (write FIRST, must FAIL before implementation) ⚠️

- [X] T018 [P] [US3] Unit test: `parse_depends_line("foo (= 9.9) | bar (= 2.0)")` preserves `bar`'s `(= 2.0)`; `deserialize_depends(serialize_depends(specs))` round-trips every alternative's constraint in `tests/unit/test_dependency.py` (NEW module) — **evidence: `TestPerAlternativeConstraints` (`test_second_alternative_keeps_its_own_constraint`, `test_round_trip_preserves_every_alternative_constraint`, `test_alternative_without_constraint_is_none`, `test_alternative_with_arch_captured`) pass (was red pre-fix: alternatives were name-only)**
- [X] T019 [P] [US3] Unit test: legacy string-form alternatives (hand-built old JSON) still deserialize as `Alternative(name, None, None)` — backward compatibility in `tests/unit/test_dependency.py` — **evidence: `TestLegacyAlternativeDeserialization` (`test_legacy_string_alternatives_deserialize`, `test_legacy_alternatives_absent`) pass**
- [X] T020 [P] [US3] Unit test: resolution of `foo (= 9.9) | bar (= 2.0)` with only `bar 1.0` in the index → an error, **no** silent `bar 1.0` link; `foo | bar (= 2.0)` with `bar 2.0` → satisfied via `bar` under its own constraint in `tests/unit/test_resolver_batch.py` — **evidence: `TestAlternativeGroupResolution::test_alternative_unsatisfied_constraint_errors_and_never_links` (was red pre-fix: 0 errors, `bar 1.0` linked silently) + `test_alternative_satisfied_under_its_own_constraint_links` pass**

### Implementation for User Story 3

- [X] T021 [US3] Add the frozen `Alternative(name, version_constraint=None, architecture=None)` dataclass and re-type `DependencySpec.alternatives` and `BuildDependencySpec.alternatives` to `tuple[Alternative, ...]` in `src/package_mgmt/lib/model/dependency.py` — **implemented: `Alternative` (frozen, with `to_json`/`from_json`) + `_parse_alternatives` normalize helper; both spec types re-typed; `index_loader.py::_format_dep_spec` renders `Alternative` (name + own constraint)**
- [X] T022 [US3] Update `to_json`/`from_json` (alternatives serialize as `{name, version_constraint?, architecture?}` dicts; `from_json` reads legacy string elements) and `parse_depends_line` (`dependency.py:66-85`) to capture each alternative's own constraint/arch in `src/package_mgmt/lib/model/dependency.py` (same file, sequential after T021) — **implemented: dict-form alternatives serialized; legacy string elements wrap as `Alternative(name, None, None)`; `parse_depends_line` builds an `Alternative` per member with its own `(op ver)` and `:arch`**
- [X] T023 [P] [US3] Update the resolver alternative walk (`src/package_mgmt/lib/service/resolver.py:919-935`) to use `Alternative.name` and pass each alternative's own constraint; add an `_alt_names(entry)` helper handling dict-form/legacy build-dep JSON at `resolver.py:585-586` — **implemented: walk uses `a.name` + `alt.version_constraint`; group error joins `a.name`; `_alt_names(entry)` + `_alt_specs(entry)` helpers; the build-dep alt loop passes each member's own constraint**
- [X] T024 [P] [US3] Update `verify`'s `_dep_satisfied` alternative names (`src/package_mgmt/cli/commands/verify.py:139`) to `[a.name for a in spec.alternatives]` and `_find_build_dep_issues` to give each alternative its own constraint (`verify.py:280-294`) — **implemented: `_dep_satisfied` uses `a.name`; `_find_build_dep_issues` builds a name→constraint map handling dict-form and legacy string-form build-dep JSON**
- [X] T025 [US3] Regression: run `tests/unit/test_resolver_batch.py`, `tests/unit/test_virtual_resolution.py`, `tests/unit/test_verify.py` — single-alternative deps and existing fixtures are unchanged (backward compat) — **evidence: 198 passed + 7 subtests on the affected modules; `pytest tests/unit tests/contract` → 496 passed + 41 subtests; `pytest tests/integration` → 58 passed (12.5s); `python -m unittest discover -s tests` → 554 tests, OK**

**Checkpoint**: US3 is fully functional — per-alternative constraints survive parse/store/resolve; no silent unconstrained registration.

---

## Phase 6: User Story 4 - Architecture Restrictions and Build Profiles Are Honored (Priority: P2)

**Goal**: `[arch]` restrictions are honored (kept when they include `amd64`, dropped only when they exclude it), multi-token `<profile>` tokens are accepted conservatively, and no dependency token is dropped silently. *(F-12)*

**Independent Test**: Fixtures `foo [amd64]` (kept), `foo [i386]`/`foo [!amd64]` (dropped), a multi-profile token kept with its constraint, and an unparseable token producing a warning with the remainder still parsed (`spec.md` US4).

### Tests for User Story 4 (write FIRST, must FAIL before implementation) ⚠️

- [X] T026 [P] [US4] Unit tests: `foo [amd64]` kept, `foo [i386]` and `foo [!amd64]` dropped, `foo [amd64 i386]` and `foo [amd64 !i386]` kept in `tests/unit/test_dependency.py` — **evidence: `TestArchRestrictionFiltering` (7 tests: keep/drop/negated/multi-token/mixed-negation/alternative-drop/primary-fallthrough/target_arch override) pass (was red pre-fix: `gperf [amd64 i386]` → 0 specs, silently dropped)**
- [X] T027 [P] [US4] Unit tests: `debhelper-compat (= 13) <!nocheck !cross>` kept with its constraint and both profile tokens recorded; an unparseable token → `assertLogs` WARNING and the remainder of the line still parses in `tests/unit/test_dependency.py` — **evidence: `TestBuildProfiles` (`test_multi_profile_tokens_kept_with_constraint`, `test_multiple_profile_groups_accumulate`, `test_profile_round_trip`, `test_unparseable_token_warns_and_remainder_parses`) pass**
- [X] T028 [P] [US4] Unit tests: `parse_build_depends_field` (`dependency.py:130-142`) honors `[arch]`/multi-profile build-dep lines the same way in `tests/unit/test_dependency.py` — **evidence: `TestBuildDependsArchProfiles` (`test_build_depends_field_honors_arch_and_profiles`, `test_build_depends_field_keeps_arch_including_tool`) pass**

### Implementation for User Story 4

- [X] T029 [US4] Extend the `parse_depends_line` regex (`src/package_mgmt/lib/model/dependency.py:66`) to capture an optional `[arch list]` group (tokens optionally `!`-negated) and **multiple** `<profile>` tokens; add `arch_restriction: tuple[str, ...] | None` and `profiles: tuple[str, ...] | None` to both spec types with `to_json`/`from_json` (depends on US3's model) — **implemented: `_ALT_PATTERN` captures `(\[[^\]]*\])?` + `((?:\s*<[^<>]+>)*)`; `arch_restriction`/`profiles` added to `DependencySpec` and `BuildDependencySpec` (profiles re-typed to `tuple`), with `to_json`/`from_json` (`_tuple_or_none` normalize); `_arch_restriction_from`/`_profiles_from` helpers**
- [X] T030 [US4] Add `target_arch="amd64"` param to `parse_depends_line` and thread it through `parse_build_depends_field`; keep a dep when the restriction includes the target arch, drop only when it excludes; emit a `logger.warning` for any token that still cannot be parsed while the remainder parses (never silent) in `src/package_mgmt/lib/model/dependency.py` (same file, sequential after T029) — **implemented: `_arch_allows(restriction, target_arch)` keep/drop rule (per member, so an arch-excluded alternative is dropped and an arch-excluded primary falls through); `logger.warning("Cannot parse dependency token: %r", alt)` for unmatched tokens; `parse_build_depends_field` threads `target_arch` and copies `arch_restriction`/`profiles`; `index_loader.py::_format_dep_spec` renders a kept `[arch]` group**
- [X] T031 [US4] Regression: run `tests/unit/test_resolver_batch.py`, `tests/unit/test_virtual_resolution.py`, `tests/unit/test_verify.py` — arch-less deps parse unchanged — **evidence: 163 passed + 7 subtests on the affected modules; `pytest tests/unit tests/contract` → 510 passed + 41 subtests; `pytest tests/integration` → 58 passed (12.5s); `python -m unittest discover -s tests` → 568 tests, OK**

**Checkpoint**: US4 is fully functional — arch restrictions and build profiles are honored; nothing is dropped silently.

---

## Phase 7: User Story 5 - Verify Checks Version Constraints, Not Just Presence (Priority: P2)

**Goal**: `verify` reports a version-constrained direct-name dependency satisfied only when the current binary's version satisfies the constraint — matching the operator's install check — with alternatives walked under their own constraints and providers over-inclusive. *(F-13)*

**Independent Test**: A `>=` constraint with only a too-old binary reports INCOMPLETE with a non-zero exit; an exact match is clean; an alternative group satisfied only by its second alternative is clean (`spec.md` US5).

### Tests for User Story 5 (write FIRST, must FAIL before implementation) ⚠️

- [X] T032 [P] [US5] Unit test: `libfoo (>= 2.0)` with only `libfoo 1.5` current → `_dep_satisfied` False, `verify --graph` Runtime INCOMPLETE, exit 1 in `tests/unit/test_verify.py` — **evidence: `TestVerifyVersionConstraints::test_too_old_binary_fails_ge_constraint` passes (was red pre-fix: `_dep_satisfied` answered True on presence)**
- [X] T033 [P] [US5] Unit tests: exact `(= 1.5)` with `libfoo 1.5` → COMPLETE; alternative group satisfied only by its second alternative (with its own constraint) → COMPLETE; versionless direct dep satisfied (regression); provider-satisfied dep over-inclusive (regression) in `tests/unit/test_verify.py` — **evidence: `test_exact_constraint_satisfied_is_complete`, `test_exact_constraint_mismatch_is_incomplete`, `test_alternative_satisfied_by_second_under_own_constraint`, `test_alternative_own_constraint_unsatisfied_is_incomplete`, `test_versionless_direct_dep_still_satisfied`, `test_provider_satisfied_dep_stays_over_inclusive` pass**

### Implementation for User Story 5

- [X] T034 [US5] Rework `_dep_satisfied` (`src/package_mgmt/cli/commands/verify.py:135-157`): the direct-name path selects the current binary's version and returns `satisfies_constraint(version, spec.version_constraint)`; walk alternatives with their own constraints (US3 model); the provides path keeps its existing constraint-checked, over-inclusive behavior (depends on US3) — **implemented: candidates = primary (with `spec.version_constraint`) + each `Alternative` (with its own `version_constraint`); new `_direct_name_satisfies` selects `version` and checks `satisfies_constraint`; provides path unchanged per-candidate**
- [X] T035 [US5] Update `_find_build_dep_issues` (`src/package_mgmt/cli/commands/verify.py:240-302`): give each alternative its own constraint from the dict-form JSON (replace `nvc = entry.get("version_constraint") if n == dep_names[0] else None`) — **implemented in US3/T024 (per-alt `name_vc` map handles dict-form and legacy string-form); US5 adds the constraint-verification regression tests `test_build_dep_alternative_own_constraint_unsatisfied_by_link` + `test_build_dep_alternative_own_constraint_satisfied_by_link`**
- [X] T036 [US5] Validation: verify contract per `specs/016-resolver-dependency-correctness/contracts/cli.md` Phase 5 — previously-correct caches stay COMPLETE; an unsatisfied constraint becomes INCOMPLETE; status labels and exit semantics unchanged — **evidence: `tests/contract/test_cli.py` → 58 passed, 11 subtests (exit-code + JSON-error contracts green); `pytest tests/unit tests/contract` → 519 passed + 41 subtests; `python -m unittest` on test_verify → 36 tests OK; `pytest tests/integration` → 58 passed**

**Checkpoint**: US5 is fully functional — `verify` matches the operator's install check on direct-name deps; providers stay over-inclusive.

---

## Phase 8: User Story 6 - The Build-Dep Pass Stays Batch-Scoped (Priority: P2)

**Goal**: The build-tool resolution pass processes only sources created in the current batch; retained sources are never re-walked or re-extended. *(F-15)*

**Independent Test**: Two sequential `add` runs — the second run's build-tool counters count only its own sources; a shallow-after-deep re-run resolves zero new build tools for retained sources (`spec.md` US6).

### Tests for User Story 6 (write FIRST, must FAIL before implementation) ⚠️

- [X] T037 [P] [US6] Unit test: two sequential `add` runs on the same cache — the second run's `build_tools`/`build_dep_links` counters count only its own sources (retained sources not re-processed) in `tests/unit/test_resolver_build_deps.py` — **evidence: `TestBatchScopedBuildDeps::test_second_run_counters_count_only_its_own_sources` passes (was red pre-fix: run 2 `build_dep_links` was 2 — app1's retained source was re-walked; now 1)**
- [X] T038 [P] [US6] Unit test: shallow-after-deep re-run resolves zero new build tools for retained sources; a fresh batch still resolves its full transitive build-tool chain (regression guard for the bounded query) in `tests/unit/test_resolver_build_deps.py` — **evidence: `TestBatchScopedShallowAfterDeep::test_shallow_after_deep_resolves_zero_new_build_tools` (was red pre-fix: 3 links re-resolved for retained sources; now 0) + `test_fresh_batch_resolves_full_transitive_chain` (links `{bt1:1, bt2:2, bt3:3}` — the `while True` expansion keeps working)**

### Implementation for User Story 6 (all in `src/package_mgmt/lib/service/resolver.py` — sequential, same file)

- [X] T039 [US6] Change `upsert_source` to return `(source_id: int, inserted: bool)` (`resolver.py:82-99`); update the single caller `_register_source_for` (`resolver.py:465`) — **implemented: pre-query distinguishes insert-vs-update (`rowcount` is unreliable with `RETURNING`); `_register_source_for` unpacks the tuple**
- [X] T040 [US6] Add `_batch_inserted_sources: set[int]` to `PackageResolver`, cleared at batch start (`resolver.py:305-310`); record inserted source ids in `_register_source_for` — **implemented: field on `__init__`, cleared alongside the other batch state in `resolve_batch`, populated in `_register_source_for` when `inserted` is True**
- [X] T041 [US6] Bound `_resolve_build_deps` (`resolver.py:552-643`): select only sources whose id is in `_batch_inserted_sources` (and not yet `processed_ids`); sources registered during the pass join the set so the `while True` transitive expansion keeps working — **implemented: `batch_ids = _batch_inserted_sources - processed_ids` drives an `IN (...)` query; empty set → no pass; mid-pass registrations grow the set so the loop keeps resolving**
- [X] T042 [US6] Validation: retained sources are never re-processed (retention = keep, not keep extending); build-error attribution to the first segment (`resolver.py:358-365`) is unchanged; a shallow-after-deep run resolves zero new build tools — **evidence: `pytest` → 580 passed + 41 subtests; `python -m unittest discover -s tests` → 580 tests OK; `pytest tests/integration` → 58 passed; first-segment error attribution logic untouched (existing `test_missing_build_tool_creates_no_link` green)**

**Checkpoint**: US6 is fully functional — no per-`add` re-walk of historical sources; retention means keep, not keep extending.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Release gate — this feature is not complete until this phase passes.

- [X] T043 [P] Run the full suite under both documented runners from a clean checkout (`pytest` and `python -m unittest discover -s tests`) — both must be 100% green — **evidence: `.venv/bin/python -m pytest -q` → 580 passed + 41 subtests (36.28s); `.venv/bin/python -m unittest discover -s tests` → Ran 580 tests, OK (35.838s)**
- [X] T044 Run the `specs/016-resolver-dependency-correctness/quickstart.md` validation scenarios for all six phases (manual acceptance: `add` exit-0 on a satisfied alternative group, exact source linkage via SQL, `verify --graph` constraint behavior, two-run build-tool counters) — record evidence — **evidence: scripted manual acceptance (real `handle_add`/`handle_verify_graph` + SQL) → 6/6 PASS: (1) `pkg` Depends `ghost | realpkg` (only realpkg present) → `add` rc=0; (2) `--file` manifest → `Errors: 0`, rc=0; (3) `foo-bin` `Source: foo (1.0-1)` → `sources` row `('foo','1.0-1','trixie','main')`; (4) `app` Depends `libfoo (>= 2.0)` with libfoo 1.5 → verify rc=1, Runtime INCOMPLETE; (5) `app` Depends `libfoo (= 1.5)` with libfoo 1.5 → verify rc=0, Runtime COMPLETE; (6) two sequential `add` runs → run 2 build-dep counters `links=1, tools=1` (only its own source); per-phase quickstart `pytest` on the affected modules → 165 passed + 7 subtests**
- [X] T045 [P] Constitution compliance re-check: no schema change, no new runtime dependencies, no network calls added, contracts preserved or explicitly extended (never contradicted) — cross-check Phases 1-6 against `contracts/cli.md` and `data-model.md`; re-verify the F-8/F-13 contract deltas hold under both runners — **evidence: `git diff 6f1f946..HEAD` shows `src/package_mgmt/lib/model/database.py` unchanged (schema frozen); no changes to `pyproject.toml`/`setup.py`/requirements; new imports are stdlib-only (`logging`, `re` in `dependency.py`); `urlopen` pre-exists in `downloader.py` (`_download`) and the only downloader delta is the `SOCKET_TIMEOUT` on the existing pooled connection from `dc7df10 fix integration test hang` — not a new network call; `tests/contract/test_cli.py` (F-8/F-13 deltas) → pytest 58 passed + 11 subtests, unittest Ran 58 tests OK**
- [X] T046 [P] Documentation sync: verify `specs/016-resolver-dependency-correctness/` (plan, research, data-model, contracts, quickstart) reflect the implemented behavior — e.g. the `_register_source_for` divergence warning, `upsert_source` return change, and the `Alternative` JSON shape — **evidence: data-model.md:63-68 documents the `_register_source_for` linkage rule incl. "divergence is never silent" (matches `resolver.py:464-470` `logger.warning`); data-model.md:82-84 documents `upsert_source` returning `(id, inserted)` (matches `resolver.py:82-110`); data-model.md:26-41 + plan.md:100-103 document the `Alternative` `{name, version_constraint?, architecture?}` JSON shape with legacy string-form `from_json` (matches `dependency.py:75-92`)**

**Checkpoint**: Feature complete — all six phases implemented, all stories independently verified, both runners green.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup — guards verified before any story
- **User Stories (Phase 3+ / US1–US6)**: Depend on Setup + Foundational guards
- **Polish (Phase 9)**: Depends on all six user stories

### User Story Dependencies (recommended execution order per the plan)

The plan's clustering directive: **US3 (F-11) + US4 (F-12) land first** as one commit set with one fixture pack (they share `dependency.py`), because the `Alternative` model they introduce is what US1's and US5's alternative-constraint behavior consumes.

- **US3 (F-11) model + US4 (F-12) parser — implement FIRST** (the foundation)
- **US1 (F-8)**: independent core (error rollback works with the current model); its alternative-constraint refinement re-uses the US3 model — no hard dependency, but cleanest after US3
- **US2 (F-9+F-10)**: fully independent — `repository.py` + `resolver.py` `_register_source_for`
- **US5 (F-13)**: **depends on US3** for its alternative-with-constraint walk; the direct-name version check is independent
- **US6 (F-15)**: fully independent — `resolver.py` `_resolve_build_deps` + `upsert_source`

> **Note**: US1, US2, US3, US6 all touch `src/package_mgmt/lib/service/resolver.py` but disjoint functions (alt walk vs `_register_source_for` vs `_resolve_build_deps`/`upsert_source`). Treat edits to the same file as sequential within the same story only.

### Within Each User Story

- Tests (required for this feature) MUST be written and FAIL before implementation
- Models before services; services before CLI/integration
- Core implementation before validation
- Story complete and independently green before moving to the next

### Parallel Opportunities

- All Setup and Foundational tasks marked [P] run in parallel
- Once Foundational completes, the independent stories (US2, US6) can start in parallel with the foundation (US3+US4)
- All tests within a story marked [P] run in parallel
- US2: T015 (repository.py) [P], then T016 (resolver.py, depends T015)
- US3: T021 → T022 (dependency.py sequential); T023 (resolver.py) and T024 (verify.py) [P] after the model
- US6: T039 → T040 → T041 sequential (same file); T042 validation

---

## Parallel Example: User Story 2

```bash
# Launch all tests for User Story 2 together (must FAIL first):
Task: "Exact-version source linkage test in tests/unit/test_source_linkage.py"
Task: "BinNMU-derived version test in tests/unit/test_source_linkage.py"
Task: "Backports-release + divergence-warning test in tests/unit/test_source_linkage.py"

# After the model method (T015), launch implementation together:
Task: "Add RepositoryIndex.lookup_source_exact in src/package_mgmt/lib/model/repository.py"
Task: "Rework _register_source_for exact/release-preferring lookup in src/package_mgmt/lib/service/resolver.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (baseline)
2. Complete Phase 2: Foundational (guards)
3. Complete Phase 3: User Story 1 (F-8 error rollback) — the most user-visible false-failure fix; its core is independent of the dependency-model change
4. **STOP and VALIDATE**: run the US1 Independent Test (`a | b` with only `b` → exit 0, zero errors; `--file` summary clean)
5. Deploy/demo if ready

> Note: the plan recommends implementing the US3+US4 parser/model foundation **first** to avoid touching the alternative walk twice. If US1 is demoed first, its alternative-constraint refinement is finalized after US3 lands — do not re-add constraint handling to the US1 walk before US3.

### Incremental Delivery (per the plan's recommended order)

1. Setup + Foundational → baseline locked
2. US3 + US4 (parser/model foundation) → one commit set, one fixture pack → both runners green
3. US1 (F-8) → test independently (satisfied group exits 0) → checkpoint
4. US2 (F-9+F-10) → test independently (exact source linkage) → checkpoint
5. US5 (F-13) → test independently (constraint-aware verify) → checkpoint
6. US6 (F-15) → test independently (batch-scoped build-deps) → checkpoint
7. Phase 9: completion checklist + both-runners gate

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: US3 + US4 (parser/model foundation)
   - Developer B: US2 (source linkage) — independent
   - Developer C: US6 (batch-scoped build-deps) — independent
3. After US3 lands: Developer A continues US1 (or takes US5, which depends on US3)
4. Each story lands independently; Phase 9 gates the feature

---

## Notes

- [P] tasks = different files, no dependencies (same-file edits within a story are sequential)
- [Story] label maps task to the user story in `spec.md`
- Tests required for this feature; write them red first (the only pre-existing assertions to touch are in `tests/unit/test_handoff.py` backports linkage, extended not contradicted)
- All database reads/writes go through the pre-populated schema; **no downloads during planning/resolution** (Database-First, constitution VII); no new runtime dependencies
- Commit after each task or logical group; all changes land on `main`
- Verify each phase under BOTH documented runners before moving on
