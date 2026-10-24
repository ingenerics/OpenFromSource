---

description: "Task list for Alpha Cut-off"

---

# Tasks: Alpha Cut-off

**Input**: Design documents from `/specs/020-alpha-cut-off/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (`cli.md`), quickstart.md

**Tests**: Tests ARE required. The spec's "User Scenarios & Testing (mandatory)" mandates independent tests +
acceptance scenarios per story, and the project convention ships every phase with its regression tests (TDD).
Tests MUST be written and FAIL before implementation, and must be green under **both** documented runners
(`pytest` and `python -m unittest discover -s tests`) at every phase.

**Organization**: Four user-story phases (US1 constraint-aware alternative probes N-3, US2 distributable trust
anchor N-4, US3 `init` connectivity guidance N-1, US4 graybeard stand-alone N-2), grouped so each story is
independently implementable and testable. Phase order follows the plan's Phase Map (1→4).

**Clarifications incorporated (spec `## Clarifications` / Session — review triage)**:
- N-1 (US3): accepted as a **documented limitation** — `init` requires connectivity; an offline/partial `init`
  is not a usable cache (start again from scratch). **No `init`/resolver code change**; the offline exit-0
  "offline mode" behavior (P-4) is preserved.
- N-2 (US4): accepted as a **documented limitation** — graybeard is pulled out of the grow-deeper chain and is a
  stand-alone, fresh-cache-only profile; `regular_user → developer` (including deepening developer depth) remains
  supported. **No resolver code change**; spec 014's historical wording is superseded, not retro-edited.
- N-3 (US1): **implemented** — both "already satisfied" probes become constraint-aware, mirroring the build-dep
  link block; the provides-join (virtual-member) leg keeps its over-inclusive semantics (FR-003).
- N-4 (US2): **implemented** — the keyring is declared as package data (`[tool.setuptools.package-data]`);
  the built wheel/sdist is inspected before tagging (FR-006); install modes are documented. Empirically verified
  in research that the legacy backend honors the pyproject declaration.

**Cross-cutting constraints (all phases)**:
- **No schema change** — the spec 010/011 freeze is absolute (`specs/020-alpha-cut-off/data-model.md`).
- The 014 exit-code/JSON contract table is **extended**, never contradicted; `tests/contract/test_cli.py` stays
  green throughout (post-019 baseline: contract suite green per 019's T068, 84 + 15 subtests).
- No new runtime dependencies; no new network call sites; no new CLI commands/flags.
- Baseline this cycle: **707 passed / both runners** (per `tests/REGRESSION.md`, post-019).
- TDD: tests red first, green after; both runners green at every phase; ruff clean on touched files.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US4)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Baseline & Environment)

**Purpose**: Establish a verified baseline before any change — confirms both documented runners execute, the
operator-script prerequisites are available, and the repo reflects the post-clarify state the plan's line numbers
assume.

- [X] T001 Verify baseline test state: run both documented runners (`pytest` and `python -m unittest discover -s tests`) from a clean checkout; record the green baseline as the final-phase gate baseline — **baseline this cycle: `pytest` → 710 passed + 56 subtests; `python -m unittest discover -s tests` → Ran 710 tests, OK; contract suite → 85 passed + 15 subtests; ruff clean** (verified: pytest → 710 passed, 56 subtests (49.3s); unittest → Ran 710 tests, OK (48.2s); contract → 85 passed + 15 subtests; ruff check clean. NOTE: `tests/REGRESSION.md` says 707 / 68 contract — stale; corrected at T024)
- [X] T002 [P] Verify the offline-safe test prerequisites per `specs/020-alpha-cut-off/quickstart.md`: `bash`, `apt-get`, `gpg`, `gpgv` are on PATH (operator-script and handoff tests depend on them) and the local fixtures (`tests/fixtures/` scratch-cache/index builders, `make_test_iso.py`) import cleanly in the venv (verified: bash/apt-get/gpg/gpgv all on PATH; `tests.fixtures.make_test_iso` and `tests.fixtures.signed_archive` import cleanly in the venv)
- [X] T003 [P] Verify repo state: on branch `main` with the venv active, and confirm the 020 spec's `## Clarifications` / Session reflects the four triage decisions (N-1 docs-only, N-2 docs-only, N-3 implemented, N-4 implemented) — the tasks below depend on them (verified: branch `main`, venv Python 3.13.5 imports `package_mgmt`; spec Clarifications Session has all four Q/A triage bullets)

---

## Phase 2: Foundational (Cross-Cutting Guards)

**Purpose**: Lock in the guards every phase must respect. No new error-handling/exception infrastructure is
required — the feature reuses existing machinery (`satisfies_constraint`, `_print_error`, exit codes) and the
existing service types.

**⚠️ CRITICAL**: These guards are prerequisite evidence — verify them before any phase, re-verify in the final
phase.

- [X] T004 Confirm the schema-freeze baseline: the feature changes no tables/columns; record that NO phase may add/alter columns — all fixes are behavioral or documentation (`specs/020-alpha-cut-off/data-model.md`); `EXPECTED_SCHEMA_SQL` defines exactly the 8 frozen tables (`packages`, `binaries`, `sources`, `binary_to_source`, `source_build_deps`, `provides`, `virtual_resolutions`, `systems`) (confirmed: `database.py:EXPECTED_SCHEMA_SQL` — exactly those 8 tables; feature is behavioral/build/docs only)
- [X] T005 [P] Confirm the baseline exit-code / JSON contracts from `specs/014-base-profiles/contracts/cli.md` hold and that the 020 contract (`specs/020-alpha-cut-off/contracts/cli.md`) extends, never contradicts, them — `tests/contract/test_cli.py` green (confirmed: 014 contract table present; contract suite green → 85 passed + 15 subtests; 020 adds the alternative-resolution + packaging assertions as extension only)
- [X] T006 [P] Record the pre-fix behavior baselines for US1–US4 with exact anchors (from `specs/020-alpha-cut-off/research.md`): `resolver.py:1059-1069` runtime "already satisfied" probe is existence-only (no constraint check); `resolver.py:693-703` build-dep twin is existence-only; `resolver.py:716-744` link block is the constraint-aware pattern to mirror; `pyproject.toml` has no `[tool.setuptools.package-data]`; `docs/profiles/graybeard.toml:20-22` names graybeard in the grow-deeper chain; `docs/profiles/regular_user.toml:16` ends the chain at graybeard (all anchors verified in source)

**Checkpoint**: Guards verified — user story phases may proceed.

---

## Phase 3: User Story 1 - Version-Skewed Alternative Groups Resolve Correctly (Priority: P1) 🎯 MVP

**Goal**: Both "already satisfied" alternative-group probes — the runtime probe (`resolver.py:1059-1069`) and its
build-dep twin (`resolver.py:693-703`) — become constraint-aware, mirroring the sibling build-dep link block
(`resolver.py:716-744`): a member whose `current` row fails its own version constraint MUST NOT satisfy the group;
a resolvable alternative is brought into the closure with the failed-primary error retracted; the provides-join
(virtual-member) leg keeps its over-inclusive semantics (FR-003). *(N-3, FR-001…FR-004)*

**Independent Test**: Pre-register `a 1.0` as `current`; resolve `a (>= 2.0) | b` (index holds `a 1.0`, `b`) →
`b` is resolved into the closure, no error, `add` exits 0, `verify` reports COMPLETE; the build-dep twin links a
resolvable alternative with no "Build tool not resolved" error (`spec.md` US1).

### Tests for User Story 1 (write FIRST, must FAIL before implementation) ⚠️

- [X] T007 [P] [US1] Contract test: version-skewed group — pre-register `a 1.0` current, resolve `app` depending `a (>= 2.0) | b` (index holds `a 1.0`, `b`) → `add` exits 0, no error, `b` registered current, and `verify app` reports COMPLETE, in `tests/contract/test_cli.py` (must FAIL pre-fix: `add` exits 0 but `b` absent and `verify` INCOMPLETE — the N-3 hole) — **verified FAIL pre-fix, PASS post-fix (TestAlternativeGroupResolutionContract)**
- [X] T008 [US1] Unit test: runtime probe — pre-register `a 1.0` current; resolve `app` depending `a (>= 2.0) | b` (index holds `a 1.0`, `b`) → `b` resolved `current`, no errors, in `tests/unit/test_resolver.py` (extend `TestRuntimeAlternativeStatusFilter` or add `TestRuntimeAlternativeConstraintProbe`; must FAIL pre-fix: `a 1.0` suppresses `b`) — **added `TestRuntimeAlternativeConstraintProbe`; verified FAIL pre-fix, PASS post-fix**
- [X] T009 [US1] Unit test: runtime probe regressions — (a) pre-register `a 2.0` satisfying `>= 2.0` → group satisfied by `a`, `b` NOT resolved, no error (behavior unchanged); (b) a virtual member (via `provides` join) with any `current` provider satisfies the group (over-inclusive, FR-003 preserved), in `tests/unit/test_resolver.py` (must PASS pre-fix — guards against over-correction) — **both PASS pre-fix and post-fix**

### Implementation for User Story 1

- [X] T010 [US1] N-3a: make the runtime "already satisfied" probe constraint-aware in `_resolve_one_inner` in `src/package_mgmt/lib/service/resolver.py` (currently `resolver.py:1059-1069`): for each group member, fetch the `current` binary's version and evaluate `satisfies_constraint(version, member_vc)` — primary uses `spec.version_constraint`, alternatives use `alt.version_constraint`; the provides-join (virtual-member) leg stays over-inclusive (status-`current` only, FR-003); only a constraint-wise-satisfying member retracts the failed-primary error (`del stats["errors"][error_mark:]`); otherwise the alternatives are tried (`resolver.py:1070-1081`) — **done via shared `_group_satisfying_member` helper (`virtual_leg="provides"`)**
- [X] T011 [US1] N-3b: make the build-dep twin constraint-aware in `_resolve_build_deps` in `src/package_mgmt/lib/service/resolver.py` (currently `resolver.py:693-703`), mirroring the link block (`resolver.py:716-744`): evaluate each member's current-binary (and `virtual_resolutions` provider) version against its own name's constraint; a resolvable alternative is linked with no residual "Build tool not resolved" error (FR-004). Prefer extracting the link block's satisfaction loop into a shared helper (e.g. `_group_constraint_satisfied(conn, all_names, name_constraints)`) reused by the probes and the link block to prevent future drift — **done: link block's loop extracted into `_group_satisfying_member` (`virtual_leg="resolutions"`), reused by twin probe + link block; alternative resolution now retracts the failed primary's error (del entry_error_mark:alt_mark); new unit test `TestBuildDepFallbackConstraint.test_build_dep_twin_tries_resolvable_alternative_when_primary_too_old` verified FAIL pre-fix ("Build tool not resolved: a | b"), PASS post-fix**
- [X] T012 [US1] Validation: US1 suite green under both runners and `tests/contract/test_cli.py`; ruff clean on the touched files (`src/package_mgmt/lib/service/resolver.py`, `tests/unit/test_resolver.py`, `tests/contract/test_cli.py`) — **verified: pytest → 715 passed + 56 subtests; unittest → Ran 715 tests, OK (baseline 710 + 5 new US1 tests); contract suite green; ruff check clean on all three touched files**

**Checkpoint**: US1 is fully functional — a version-skewed alternative group resolves to the closure `apt` would
install, `add` exit-0 implies `verify` COMPLETE.

---

## Phase 4: User Story 2 - A Standard Install Can Fetch Verified Indexes (Priority: P1)

**Goal**: The archive-signing keyring ships in the built wheel/sdist via `[tool.setuptools.package-data]`, the
artifact is inspected before tagging, and the supported install modes are documented — a standard (non-editable)
install can verify remote indexes. *(N-4, FR-005…FR-006)*

**Independent Test**: Build the wheel (and sdist); the archive listing contains
`package_mgmt/data/debian-archive-keyring.gpg`; a standard install of the wheel runs `init`/`add` against a
signature-verifying mirror without a spurious missing-keyring failure (`spec.md` US2).

### Tests for User Story 2 (write FIRST, must FAIL before implementation) ⚠️

- [X] T013 [P] [US2] Packaging-contract unit test in new `tests/unit/test_packaging.py`: parse `pyproject.toml`, assert `[tool.setuptools.package-data]` declares `package_mgmt = ["data/*.gpg"]`, AND assert the keyring file exists at `src/package_mgmt/data/debian-archive-keyring.gpg` (must FAIL pre-fix: no package-data block) — **created `tests/unit/test_packaging.py` (`TestPackagingKeyring`); verified FAIL pre-fix (package-data missing), PASS post-fix**

### Implementation for User Story 2

- [X] T014 [US2] N-4: add `[tool.setuptools.package-data]` → `package_mgmt = ["data/*.gpg"]` to `pyproject.toml` (the legacy backend honors it — verified empirically in research; no backend change, no `importlib.resources` migration) — **added after `[tool.setuptools.packages.find]`; legacy backend unchanged**
- [X] T015 [P] [US2] N-4 docs (FR-006): state the supported install modes in `docs/001-quickstart-howto.md` near the install instructions (lines ~38 and ~398-418) — the editable-install dev flow AND standard `pip install .` working from the built artifact (which now carries the keyring) — **added a "Two install modes are supported" block in Step 2 + a cross-reference note in "Running the tests"**
- [X] T016 [US2] Build-and-inspect verification (FR-006): build the wheel and sdist (`uv build` or `python -m build`), list the archive contents, assert `package_mgmt/data/debian-archive-keyring.gpg` is present at the runtime-resolved path; optionally install the wheel in a scratch venv and run `init`/`add` against a signature-verifying loopback/mirror; record the output (depends on T014) — **verified: `uv build` → `package_mgmt-0.1.0-py3-none-any.whl` (142 218 B) + `package_mgmt-0.1.0.tar.gz` (131 718 B). Wheel listing contains `package_mgmt/data/debian-archive-keyring.gpg` (55 918 B); sdist contains `src/package_mgmt/data/debian-archive-keyring.gpg`. Standard-install simulation (wheel unpacked, the layout pip installs): runtime `DEBIAN_ARCHIVE_KEYRING` resolves to an existing 55 918-byte keyring; `gpg --keyring` lists the real Debian Archive Automatic Signing Keys — usable, not merely present. Note: signature verification is skipped for loopback mirrors (`release_base_for` → None), so the install-mode claim is proven by the resolved-present-and-valid keyring at the installed path**
- [X] T017 [US2] Validation: US2 tests green under both runners; the build-and-inspect output (T016) recorded as the pre-tag evidence; ruff clean on `tests/unit/test_packaging.py` — **verified: pytest → 717 passed + 56 subtests; unittest → Ran 717 tests, OK (715 + 2 new packaging tests); ruff check clean on all touched files**

**Checkpoint**: US2 is fully functional — the wheel/sdist carries the trust anchor and a standard install works
against real archives.

---

## Phase 5: User Story 3 - `init` Guidance Is Honest About Connectivity (Priority: P2)

**Goal**: The quickstart documents that `init` requires access to at least one configured mirror and that an
offline/partial `init` (0 binaries cached) is not a usable cache — the recovery is to resolve connectivity and
start again from scratch. **No `init`/resolver behavior change** (the P-4 offline exit-0 note is preserved). *(N-1,
FR-007)*

**Independent Test**: A reader of `docs/001-quickstart-howto.md` step 4 can determine that `init` requires a
reachable mirror and what to do when it reports 0 binaries cached (`spec.md` US3).

### Implementation for User Story 3

- [X] T018 [US3] N-1 docs (FR-007): in `docs/001-quickstart-howto.md` step 4 (the `init` section, lines ~139-182), add a note after the expected-output block stating: `init` requires access to at least one configured mirror (its job is fetching the indexes and building the virtual-package snapshot); an `init` reporting "0 binaries cached" (offline mode) or a partial fetch is not a usable cache — resolve connectivity and re-run `init` from a clean cache. Do NOT modify `src/package_mgmt/cli/commands/init.py` or `src/package_mgmt/lib/service/resolver.py` — **added an "`init` requires connectivity" callout after the Step 4 expected-output block in `docs/001-quickstart-howto.md`; no source files touched**
- [X] T019 [US3] Validation: doc-only — confirm no `init`/`resolver` source change (diff check), and both runners stay green (no test changes) — **verified: only `docs/001-quickstart-howto.md` changed this phase; `src/package_mgmt/cli/commands/init.py` unchanged vs HEAD; `resolver.py` diff is entirely the prior phase's fix (not this phase); pytest → 717 passed + 56 subtests; unittest → Ran 717 tests, OK**

**Checkpoint**: US3 is complete — the misleading "no providers in the index" dead-end is addressed by up-front
documentation, with zero behavior change.

---

## Phase 6: User Story 4 - Graybeard Is a Stand-Alone, Fresh-Cache-Only Profile (Priority: P2)

**Goal**: The profile documentation pulls graybeard out of the grow-deeper chain (stand-alone, fresh-cache-only,
consistent with `ensure-base`'s fresh-only refusal) and restates the supported direction as
`regular_user → developer` (including deepening developer depth). **No resolver behavior change**. *(N-2, FR-008)*

**Independent Test**: A reader of `docs/profiles/*.toml` can determine that graybeard is fresh-cache-only and not
part of the grow-deeper chain, and that the supported direction is `regular_user → developer` (`spec.md` US4).

### Tests for User Story 4 (write FIRST, must FAIL before implementation) ⚠️

- [X] T020 [P] [US4] Update the profile-file-data test in `tests/unit/test_config.py` (`TestProfileFilesAreData`) to assert the new grow-deeper wording: `graybeard.toml` does NOT name graybeard in a grow-deeper chain and states the fresh-cache-only stance; `regular_user.toml`'s grow-deeper direction ends at `developer` (must FAIL pre-fix: the current wording includes graybeard in the chain) — **added `test_grow_deeper_direction_never_mentions_graybeard` (no profile line may pair "graybeard" with the "->" chain arrow) and `test_graybeard_states_fresh_cache_only_standalone` (asserts "fresh" + "not part of a grow-deeper chain"); verified FAIL pre-fix (all three profiles), PASS post-fix**

### Implementation for User Story 4

- [X] T021 [US4] N-2 docs (FR-008): edit `docs/profiles/graybeard.toml` (lines ~20-22) — replace the grow-deeper chain line with a stand-alone, fresh-cache-only statement (run from the get-go, consistent with `ensure-base`'s fresh-only refusal; keep the FR-014 retention sentence about sources never being pruned) — **done: graybeard.toml now states stand-alone / fresh-cache-only / run from the get-go / reusing a grown cache not supported (matching `ensure-base`'s fresh-only refusal); the sources-never-pruned retention claim is kept (written without the FR code tag)**
- [X] T022 [US4] N-2 docs (FR-008): edit `docs/profiles/regular_user.toml` (line ~16) — the grow-deeper direction ends at `developer` (drop graybeard); confirm `docs/profiles/developer.toml` (line ~17) already stops at developer and align wording if needed. Spec 014's historical chain wording is superseded, NOT retro-edited — **done: regular_user.toml direction now ends at developer (graybeard dropped); developer.toml chain trimmed to regular_user -> developer (depth 1) -> developer (depth N); spec 014 wording not touched**
- [X] T023 [US4] Validation: US4 test green under both runners; the three profile docs are internally consistent (grow-deeper direction ends at developer; graybeard is fresh-cache-only); ruff clean on `tests/unit/test_config.py` — **verified: pytest → 719 passed + 59 subtests; unittest → Ran 719 tests, OK (717 + 2 new US4 tests); ruff check clean on test_config.py; the three profiles consistently end the grow-deeper direction at developer and graybeard.toml is fresh-cache-only**

**Checkpoint**: US4 is complete — the supported profile-transition contract is unambiguous and enforced by the
profile-file-data test.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final full-suite gate, pre-tag evidence, and documentation hygiene across the feature.

- [X] T024 [P] Full-suite gate: both documented runners green on the whole corpus (`pytest` and `python -m unittest discover -s tests`); record the final baseline (expected 707 + new N-3/N-4 tests) and refresh `tests/REGRESSION.md` if its contract-suite line is stale (review §3 noted the post-019 baseline contradicts 019's T068 — regenerate at tag time) — **verified: pytest → 719 passed + 59 subtests; unittest → Ran 719 tests, OK (final baseline 719, +12 over the 707 post-019 baseline); contract suite → 86 passed + 15 subtests (was stale at 68); `tests/REGRESSION.md` refreshed to the post-020 baseline**
- [X] T025 [P] Ruff clean on all touched files (`src/package_mgmt/lib/service/resolver.py`, `tests/unit/test_resolver.py`, `tests/unit/test_packaging.py`, `tests/unit/test_config.py`, `tests/contract/test_cli.py`) — **verified: ruff check clean on all five touched files**
- [X] T026 [P] Run `specs/020-alpha-cut-off/quickstart.md` validation end-to-end: Scenario 1a/1c (alternative-group resolution + build-dep), 2a (packaging test), 2b/2c (build-and-inspect + standard install), 3 (init guidance read), 4 (profile docs read) — **verified: Scenario 1a/1c → `TestRuntimeAlternativeConstraintProbe` + `TestBuildDepFallbackConstraint` green; 2a → `TestPackagingKeyring` green (9 automated tests passed together); 2b/2c → build-and-inspect + standard-install evidence recorded in T016; 3 → docs/001 Step 4 carries the connectivity note (lines 192-198); 4 → all three profile TOMLs read: graybeard fresh-cache-only/stand-alone, regular_user & developer grow-deeper end at developer**
- [X] T027 [P] Pre-tag final FR-006 gate: build wheel + sdist and inspect that `package_mgmt/data/debian-archive-keyring.gpg` is present; record the artifact listing as release evidence — **verified: `uv build` → `package_mgmt-0.1.0-py3-none-any.whl` (142 218 B) and `package_mgmt-0.1.0.tar.gz` (131 716 B); wheel listing contains `package_mgmt/data/debian-archive-keyring.gpg` (55 918 B); sdist contains `src/package_mgmt/data/debian-archive-keyring.gpg`; recorded as release evidence**
- [X] T028 [P] Verify the agent context: `AGENTS.md` SPECKIT block points at `specs/020-alpha-cut-off/plan.md` / `spec.md` (refreshed during planning; confirm it survived) — **verified: `AGENTS.md` SPECKIT block points at `specs/020-alpha-cut-off/plan.md` and `spec.md` (feature 020) — survived**

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories.
- **User Stories (Phases 3-6)**: All depend on Foundational completion; then proceed in priority order
  (US1, US2 are P1; US3, US4 are P2).
- **Polish (Phase 7)**: Depends on all user stories being complete.

### User Story Dependencies

- **US1 (N-3, P1)**: Independent — resolver probes + tests; no dependency on other stories.
- **US2 (N-4, P1)**: Independent — packaging metadata + tests + docs; no dependency on other stories.
- **US3 (N-1, P2)**: Independent — `docs/001` edit; no dependency on other stories.
- **US4 (N-2, P2)**: Independent — `docs/profiles/*` + `test_config.py`; no dependency on other stories.

### Within Each User Story

- Tests MUST be written and FAIL before implementation.
- Implementation before validation; story complete before moving to the next priority.
- US1's T008/T009 (runtime probe tests) and T010/T011 (resolver fixes) are sequential (same files).

### Parallel Opportunities

- Setup tasks T002/T003 marked [P] run in parallel.
- Foundational tasks T005/T006 marked [P] run in parallel.
- Once Foundational completes, all four user stories can start in parallel (different files per story):
  US1 (`resolver.py` + `test_resolver.py` + `test_cli.py`), US2 (`pyproject.toml` + `test_packaging.py` +
  `docs/001`), US3 (`docs/001` — NOTE: shares `docs/001` with US2's T015, so US2/US3 should not both edit
  `docs/001` simultaneously; sequence T015 and T018 or merge them), US4 (`docs/profiles/*` + `test_config.py`).
- Polish tasks T024-T028 marked [P] run in parallel.

### Parallel Example: User Story 1

```bash
# Launch the contract test and the runtime-probe unit tests together (different files):
Task: "T007 [P] [US1] Contract test in tests/contract/test_cli.py"
Task: "T008 [US1] Runtime probe unit tests in tests/unit/test_resolver.py"

# After both red, implement the two probes sequentially (same file resolver.py):
Task: "T010 [US1] Runtime probe fix in src/package_mgmt/lib/service/resolver.py"
Task: "T011 [US1] Build-dep twin fix in src/package_mgmt/lib/service/resolver.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (baseline).
2. Complete Phase 2: Foundational (guards).
3. Complete Phase 3: User Story 1 (N-3 constraint-aware probes) — the only code change that touches the
   resolver's core resolution behavior.
4. **STOP and VALIDATE**: US1 independently testable — a version-skewed alternative group resolves to the
   `apt`-installable closure, `add` exit-0 implies `verify` COMPLETE.

### Incremental Delivery

1. Complete Setup + Foundational → guards in place.
2. Add US1 (N-3, P1) → test independently → the alpha's installable-cache promise is closed for version-skewed
   caches.
3. Add US2 (N-4, P1) → test independently → a standard install can verify real archives (pre-tag gate).
4. Add US3 (N-1, P2) and US4 (N-2, P2) → doc phases, each independently verifiable.
5. Polish (Phase 7) → full-suite gate + pre-tag FR-006 evidence.

### Parallel Team Strategy

With multiple developers:
1. Team completes Setup + Foundational together.
2. Once Foundational is done: Developer A takes US1 (N-3), Developer B takes US2 (N-4), Developer C takes
   US4 (N-2) — US3 (N-1) follows US2's `docs/001` edit to avoid a same-file conflict.
3. Stories integrate independently; Phase 7 collates the pre-tag evidence.

---

## Notes

- [P] tasks = different files, no dependencies (watch the US2/US3 shared `docs/001` file).
- [Story] label maps task to specific user story for traceability.
- Each user story is independently completable and testable.
- Verify tests fail before implementing; both runners green after.
- Commit after each task or logical group.
- No schema changes, no new runtime dependencies, no new CLI commands/flags.
- Pre-tag gate (T027) is the FR-006 build-and-inspect — the alpha tag must not proceed without it.
