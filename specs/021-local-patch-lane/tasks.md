---

description: "Task list for Local Patch Lane"

---

# Tasks: Local Patch Lane

**Input**: Design documents from `/specs/021-local-patch-lane/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (`cli.md`), quickstart.md

**Tests**: Tests ARE required. The spec's "User Scenarios & Testing (mandatory)" mandates independent tests +
acceptance scenarios per story, and the project convention ships every phase with its regression tests (TDD).
Tests MUST be written and FAIL before implementation, and must be green under **both** documented runners
(`pytest` and `python -m unittest discover -s tests`) at every phase.

**Organization**: Four user-story phases (US1 import a `.deb` into `local`, US2 ship through the same channel,
US3 `--source` provenance, US4 `local`-highest-priority resolution), grouped so each story is independently
implementable and testable. Phase order follows the plan's design: US1 is the MVP and the prerequisite for US2
and US3; US4 is independent and parallelizable with US2/US3 after the foundational phase.

**Clarifications incorporated (spec `## Clarifications` / Session)**:
- Q1 (US4): `local` priority = **version-then-component tie-break** — `local` is the highest-priority component
  and wins at equal version; a strictly newer upstream version still wins (upgrade semantics preserved). Resolves
  the earlier spec/design contradiction; spec US4/FR-006/SC-003 now match `research.md` §2.
- Q2 (US1): `import-deb` accepts **exactly one** `.deb` per invocation; multiple `.deb` paths are an error.
  `--source <dir>`, when given, applies to that single binary.

**Cross-cutting constraints (all phases)**:
- **No schema change** — the spec 010/011 freeze is absolute; `local` is a value of the existing `component`
  column and `binary_to_source` is reused (`specs/021-local-patch-lane/data-model.md`). NO `database.py` edits.
- **No new runtime dependencies** — python-debian (`debian.debfile.DebFile`, `debian.deb822`) is already a
  project dependency; no new network call sites; no new archive fetches for `local` (payload is the supplied
  `.deb`).
- The 014 exit-code/JSON contract table is **extended**, never contradicted; `tests/contract/test_cli.py` stays
  green throughout.
- **Layout parity** (FR-004): `local` uses exactly the same pool/index/handoff/diff layout as every other
  component — no downstream special-casing.
- Baseline this cycle: **730 passed** (578 unit / 86 contract / 66 integration) per the last full-suite run.
- TDD: tests red first, green after; both runners green at every phase; ruff clean on touched files.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US4)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Baseline & Environment)

**Purpose**: Establish a verified baseline before any change — confirms both documented runners execute, the
`.deb`/`.dsc` parsing dependency is available, and the repo reflects the post-clarify state the plan's line
numbers assume.

- [X] T001 Verify baseline test state: run both documented runners (`pytest` and `python -m unittest discover -s tests`) from a clean checkout and `ruff check`; record the green baseline as the final-phase gate baseline — **baseline this cycle: pytest → 730 passed / unittest → Ran 730 tests, OK (578 unit / 86 contract / 66 integration), ruff clean** (run and record actual numbers)
- [X] T002 [P] Verify repo state: feature directory `specs/021-local-patch-lane/` has all artifacts (`plan.md`, `research.md`, `data-model.md`, `contracts/cli.md`, `quickstart.md`), and the spec's `## Clarifications` / Session contains the two Q/A bullets (priority tie-break Q1; single-`.deb` Q2) — the tasks below depend on them
- [X] T003 [P] Verify prerequisites: `python3 -c "from debian import debfile"` imports (python-debian present) and the local test fixtures (`tests/fixtures/` scratch-cache/index builders) import cleanly in the venv — the `.deb`/`.dsc` parsing and handoff/diff tests depend on them

---

## Phase 2: Foundational (Cross-Cutting Guards)

**Purpose**: Lock in the guards every phase must respect. No new error-handling/exception infrastructure is
required — the feature reuses existing machinery (`DependencyGraph`, `upsert_binary`/`upsert_source`,
`_print_error`, exit codes) and existing service types.

**⚠️ CRITICAL**: These guards are prerequisite evidence — verify them before any phase, re-verify in the final
phase.

- [X] T004 Confirm the schema-freeze baseline: the feature changes no tables/columns; record that NO phase may
  add/alter columns — `local` is a value of the existing `binaries.component` / `sources.component` column
  (`src/package_mgmt/lib/model/database.py:16,41`) and `binary_to_source`
  (`database.py:31-36`) is reused for imported provenance; the existing
  `UNIQUE(package_name, version, architecture, release, component)` already permits a `local` row alongside the
  upstream row (`specs/021-local-patch-lane/data-model.md`)
- [X] T005 [P] Confirm the baseline exit-code / JSON contracts from `specs/014-base-profiles/contracts/cli.md`
  hold and that the 021 contract (`specs/021-local-patch-lane/contracts/cli.md`) extends, never contradicts,
  them — `tests/contract/test_cli.py` green
- [X] T006 [P] Record the pre-fix behavior anchors for US1–US4 (from `specs/021-local-patch-lane/research.md`):
  `_COMPONENT_PRIORITY` (`src/package_mgmt/lib/model/repository.py:8-18`, `main`=0…, no `local`), `_best_entry`
  version-then-component ordering (`repository.py:98-104`), `ensure_indexes()` archive-only merge
  (`src/package_mgmt/lib/service/resolver.py:284-317`, no `local` injection), `_component_bases`
  (`src/package_mgmt/lib/service/handoff.py:712-723`, config components only), download pending set
  (`src/package_mgmt/lib/service/downloader.py:261-264`, `verified=0` only) — (all anchors verified in source)

**Checkpoint**: Guards verified — user story phases may proceed.

---

## Phase 3: User Story 1 - Import a Locally Built `.deb` into the `local` Component (Priority: P1) 🎯 MVP

**Goal**: `import-deb <file>` registers a single locally built/patched `.deb` into the `local` component: parse
the control (`debian.debfile.DebFile`), re-resolve declared `Depends`/`Pre-Depends` against the cache (fail
atomically with zero changes on any unsatisfiable dependency), register the binary row (`component='local'`,
`verified=0`), copy the payload to `pool/local/<prefix>/<pkg>/<pkg>_<ver>_<arch>.deb` (identical layout to every
other component), compute the SHA256, mark `verified=1`. Exactly one `.deb` per invocation; no component
selector. *(FR-001…FR-004, FR-008…FR-010; Q2)*

**Independent Test**: Build a trivial local `.deb` whose dependencies are already in the cache; run
`import-deb <file>`; confirm the package is registered under `local`, `verified`, present in the pool, shown by
`list`/`show`, reported COMPLETE by `verify`, and installable — all without touching any upstream archive
(`spec.md` US1).

### Tests for User Story 1 (write FIRST, must FAIL before implementation) ⚠️

- [X] T007 [P] [US1] Contract test: `import-deb` CLI contract in `tests/contract/test_cli.py` — (a) happy path:
  a valid single `.deb` with satisfiable deps imports into `local` (`component='local'`, `verified=1`), exit 0;
  (b) a `.deb` declaring an unsatisfiable dependency fails with an error naming the dependency, exit 1, zero
  changes; (c) missing/unreadable/non-`.deb` file → clear error, exit 1, zero changes; (d) no component selector
  (`import-deb x.deb --component foo` → argparse error, nothing imported); (e) multiple `.deb`s
  (`import-deb a.deb b.deb` → error, nothing imported) — **must FAIL pre-fix (`import-deb` subcommand absent)**
- [X] T008 [US1] Unit tests in new `tests/unit/test_import_deb.py`: (a) control parse → correct
  `(package_name, version, architecture, dependencies)` from a fixture `.deb`; (b) satisfiable deps → import
  registers `component='local'`, payload at `pool/local/<prefix>/<pkg>/<pkg>_<ver>_<arch>.deb`, `verified=1`;
  (c) unsatisfiable declared dep → atomic failure, zero DB rows and zero pool files; (d) re-import of the
  identical binary → idempotent, no duplicate rows, payload re-verified; (e) invalid file → clear error, zero
  changes — **must FAIL pre-fix (module absent)**

### Implementation for User Story 1

- [X] T009 [US1] Implement the import core in new `src/package_mgmt/lib/service/import_local.py`: parse the
  `.deb` control with `debian.debfile.DebFile` (`Package`, `Version` validated via
  `debian.debian_support.Version`, `Architecture`, `Depends`/`Pre-Depends` parsed via `DependencyGraph` /
  `parse_build_depends_field` from `src/package_mgmt/lib/model/dependency.py`, `Provides`, `Multi-Arch`);
  re-resolve every declared dependency against the cache (`RepositoryIndex.lookup_binary_satisfying`,
  `src/package_mgmt/lib/model/repository.py:44-60`, plus current DB rows) — an unsatisfiable requirement aborts
  with an error naming it (FR-003/008); on success, in ONE transaction: `upsert_binary(...)` with
  `component='local'` (reusing `src/package_mgmt/lib/service/resolver.py:124-139` insert-or-update, `verified=0`),
  copy the payload to `pool/local/<prefix>/<pkg>/<pkg>_<ver>_<arch>.deb` via a `*.part` → `os.replace` (same
  pattern as `downloader.py`), compute SHA256, set `verified=1`; re-import is idempotent via the `ON CONFLICT`
  path (FR-009)
- [X] T010 [US1] Implement the CLI handler in new `src/package_mgmt/cli/commands/import_deb.py`: exactly one
  positional `.deb` (more than one → error), optional `--source PATH`, NO component selector; map failures
  through `_print_error` with the standard exit codes (0 / 1 / 102 config etc., extending the 014 table); print
  a one-line success (`Imported <pkg> <ver> (<arch>) into local component (verified)`); wire `--source` through
  to the core (US3 fills the source path)
- [X] T011 [US1] Register the `import-deb` subcommand in `src/package_mgmt/cli/main.py` (single `deb` positional,
  optional `--source PATH`; import and dispatch `handle_import_deb`) — modeled on the `add`/`ensure-base` wiring
  (`main.py:79-87,193`)
- [X] T012 [US1] Guard/regression: confirm `download` ignores `local` records (they are `verified=1` at import
  and therefore out of the pending set `downloader.py:261-264`) and that `list`/`show`/`verify` surface the
  `local` rows (verify's dependency check, `src/package_mgmt/cli/commands/verify.py:182-188`, is satisfied);
  add assertions where the existing tests do not cover a `local`-component row
- [X] T013 [US1] Validation: US1 suite green under both runners and `tests/contract/test_cli.py`; ruff clean on
  the touched files (`src/package_mgmt/lib/service/import_local.py`, `src/package_mgmt/cli/commands/import_deb.py`,
  `src/package_mgmt/cli/main.py`, `tests/unit/test_import_deb.py`, `tests/contract/test_cli.py`)

**Checkpoint**: US1 is fully functional — a locally built `.deb` imports into `local` and is a first-class,
verified, installable cache citizen.

---

## Phase 4: User Story 2 - Ship the Patch Through the Same Channel (Priority: P1)

**Goal**: `handoff`/`sync` serve the `local` component exactly like `main`: the default release's `deb` line
gains `local` (per-release multi-component), the `local` `Packages.gz` is generated from the DB records (no
archive index to confirm against — the record's own metadata is authoritative, `_paragraph` fed directly),
exclusion filters carry it, and the operator installs/upgrades with ordinary apt. *(FR-007; research §4)*

**Independent Test**: Import a package into `local`, run `sync`/`handoff`, transfer to an offline box, apply,
and confirm the package appears in the operator's apt index, installs, and upgrades — with `local` present in
the configured apt sources (`spec.md` US2).

### Tests for User Story 2 (write FIRST, must FAIL before implementation) ⚠️

- [X] T014 [P] [US2] Unit tests in `tests/unit/test_handoff.py` (extend): (a) with current+verified `local`
  records, `generate_handoff`/`generate_diff` emit the `local` component — a `Packages.gz` paragraph built from
  the DB record (correct `Package`/`Version`/`Architecture`/`Depends`/`Filename`/`Size`/`SHA256`), the
  default-release `deb` line lists `local`, and the `Release` `Components:` line includes it; (b) with NO local
  records, no bogus `local` component is emitted (empty cache unchanged); (c) a `local` record with a missing
  pool file is excluded with a warning (same integrity posture as other components) — **must FAIL pre-fix
  (`local` excluded via `_component_bases`/`excluded_config`)**

### Implementation for User Story 2

- [X] T015 [US2] Extend the served component set in `src/package_mgmt/lib/service/handoff.py`: when at least one
  current+verified `local` binary exists (query the DB), add `(default_release, 'local')` to the served
  `bases`/`base_set` used by `generate_handoff` and `generate_diff` (currently derived only from configured
  sources via `_component_bases`, `handoff.py:712-723`) so `local` records are no longer routed to
  `excluded_config`; when no `local` records exist, behavior is unchanged
- [X] T016 [US2] Generate the `local` component index from the DB: for `local` records (no archive index to
  confirm against), build the Packages paragraph directly from the record + payload metadata via the existing
  `_paragraph` builder (`handoff.py:726-756`) — `Filename` derived from the pool layout convention,
  `Size`/`SHA256` from the copied payload; write `dists/<release>/local/binary-<arch>/Packages[.gz]` with the
  `Packages` + `Packages.gz` hash entries (same `_index_hash_entries` path); include `local` in the emitted
  `deb` line (`bootstrap.sh:161`) and the `Release` `Components:` line (`handoff.py:805-814`); apply the same
  logic to the diff generation (research §4, FR-007)
- [X] T017 [US2] Integration test in new `tests/integration/test_import_deb_flow.py`: import a local `.deb` →
  `handoff` → assert `local` in the generated `deb` line and the `local/binary-amd64/Packages.gz` index →
  transfer to a scratch operator cache → apply → `apt-get install <pkg>` installs offline → sync a newer local
  revision → `apt-get upgrade` updates it — **must FAIL pre-fix (no `local` in handoff output)**
- [X] T018 [US2] Validation: US2 suite green under both runners and `tests/contract/test_cli.py`; ruff clean on
  `src/package_mgmt/lib/service/handoff.py` and the touched tests

**Checkpoint**: US1 AND US2 work together — a patched package is imported once and distributed/installed through
the ordinary channel.

---

## Phase 5: User Story 3 - Import the Rebuilt Source for Provenance (Priority: P2)

**Goal**: `import-deb <file> --source <dir>` imports the complete rebuilt source package from a **directory**
holding the `.dsc` plus every file it references (`Files`/`Checksums-Sha256`), each verified against the
`.dsc`'s declared checksums (a bare `.dsc` cannot be `dpkg-source -x`'d), registers it under `component='local'`,
copies the set to the source pool, and links it to the imported binary via `binary_to_source` so the pair is
deterministically rebuildable. *(FR-005; research §5a)*

**Independent Test**: Import a binary with `--source <dir>` (complete, checksum-valid set), then confirm the
source is registered under `local`, verified, linked to the binary, and every referenced file present and
checksum-valid — and that the recorded linkage matches a rebuild of that source (`spec.md` US3).

### Tests for User Story 3 (write FIRST, must FAIL before implementation) ⚠️

- [X] T019 [P] [US3] Unit tests in `tests/unit/test_import_deb.py` (extend): (a) complete checksum-valid
  directory → `sources` row `component='local'`, `verified=1`, every referenced file copied to
  `source/pool/local/<prefix>/<pkg>/…`, `binary_to_source` link present; (b) directory missing any referenced
  file → clear error naming it, exit non-zero, binary NOT registered (zero changes); (c) file whose checksum does
  not match the `.dsc` → error, zero changes; (d) `--source` pointing at a file / non-directory → error, zero
  changes; (e) no `--source` → no source row, no link, `verify` still passes — **must FAIL pre-fix (no source
  import path)**
- [X] T020 [P] [US3] Contract test in `tests/contract/test_cli.py`: `--source <dir>` happy path (`Source linked
  (…, local, N files verified against dsc)`, exit 0); incomplete set → error, exit 1, binary not registered;
  `--source` as a file → error, exit 1

### Implementation for User Story 3

- [X] T021 [US3] Extend `src/package_mgmt/lib/service/import_local.py` with the source path: parse the `.dsc`
  via `debian.deb822` (`Files`/`Checksums-Sha256` → file list + expected hashes), require a directory argument,
  validate every referenced file is present and SHA256-matches the `.dsc`, copy the set to
  `source/pool/local/<prefix>/<pkg>/…` (same layout convention as binary components), register the source row
  (`component='local'`, `verified=1`) and link via `binary_to_source` — all in the same atomic transaction as
  the binary import (FR-005/008; research §5a)
- [X] T022 [US3] Wire `--source` through the CLI handler (`src/package_mgmt/cli/commands/import_deb.py`) and
  confirm the linkage is visible via `list --include-build-deps` / `show`; without `--source` the binary imports
  with no source link and `verify` does not fail on the missing source (US3 AS2)
- [X] T023 [US3] Validation: US3 suite green under both runners and `tests/contract/test_cli.py`; ruff clean on
  `src/package_mgmt/lib/service/import_local.py`, `src/package_mgmt/cli/commands/import_deb.py`, and the touched
  tests

**Checkpoint**: US3 complete — imported binaries are provenance-traceable to the exact patched source that
rebuilt them.

---

## Phase 6: User Story 4 - `local` Wins at Equal Version in Implicit Resolution (Priority: P2)

**Goal**: `local` becomes the highest-priority component (version-then-component tie-break, per Clarification
Q1) and the resolver can see it: `_COMPONENT_PRIORITY` gains `local` ahead of `main`, and `ensure_indexes()`
injects current+verified `local` binaries/sources from the DB into the merged `RepositoryIndex`, so implicit
resolution prefers a `local` package over the same-named upstream package at equal version while a strictly
newer upstream still wins. *(FR-006; research §2, §3)*

**Independent Test**: With the same `(name, version, arch)` present in `main` and `local`, resolve it without an
explicit component and confirm the `local` version is selected; with a strictly newer upstream, confirm it wins
(`spec.md` US4).

### Tests for User Story 4 (write FIRST, must FAIL before implementation) ⚠️

- [X] T024 [P] [US4] Unit tests in `tests/unit/test_repository.py` (extend): `_component_order('local')` is the
  highest priority (ahead of `main`); `_best_entry` selects `local` over `main` at equal version; a strictly
  newer upstream version still wins over an older `local` version; with no `local` copy, the existing component
  order is unchanged — **must FAIL pre-fix (`local` not in `_COMPONENT_PRIORITY`)**
- [X] T025 [P] [US4] Unit tests in `tests/unit/test_resolver.py` (extend): `ensure_indexes()` injects
  current+verified `local` binaries/sources from the DB into the merged index (visible to `lookup_binary*` /
  `lookup_source` with `release=default`, `component='local'`);   unverified or non-current `local` rows are NOT
  injected; a dependency resolvable to a same-version `local` package selects the `local` one — **must FAIL
  pre-fix (no injection)**
- [X] T026 [P] [US4] Add `"local"` as the highest-priority entry (value `-1`, ahead of `main`=0) to
  `_COMPONENT_PRIORITY` in `src/package_mgmt/lib/model/repository.py:8-18` (version-then-component tie-break
  preserved — `_best_entry` and `lookup_source` sort unchanged)
- [X] T027 [P] [US4] Inject current+verified `local` binaries/sources from the DB in
  `ensure_indexes()` (`src/package_mgmt/lib/service/resolver.py:284-317`), appended to `merged_binary` /
  `merged_source` after the configured archive components, as `BinaryPackage`/`SourcePackage` instances with
  `release=default`, `component='local'`, parsed dependencies/provides (research §3); DB-read only, no index
  file, no fetch
- [X] T028 [US4] Validation: US4 suite green under both runners and `tests/contract/test_cli.py`; ruff clean on
  `src/package_mgmt/lib/model/repository.py`, `src/package_mgmt/lib/service/resolver.py`, and the touched tests

**Checkpoint**: US4 complete — `local` shadows stock at equal version, and all four user stories work.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Documentation traceability, end-to-end validation, and the final green gate.

- [X] T029 [P] Documentation: add the `import-deb` row to the Administrator command table in
  `.specify/memory/roles.md` (purpose/phase, next to `add`/`ensure-base`) — Constitution VIII
  (Documentation & Infrastructure Traceability)
- [X] T030 [P] Documentation: reference the local patch lane where the README/docs discuss "curate and
  optionally modify" (link `specs/021-local-patch-lane/spec.md`); the case is already marked IMPLEMENTED in
  `review/05_alpha_final_kimi_k3/high_value_alternative_use_cases.md` (case 4)
- [X] T031 Run the full `specs/021-local-patch-lane/quickstart.md` validation end-to-end (import happy path +
  atomic failure + idempotency; `--source` complete/incomplete; priority tie-break; handoff `deb` line with
  `local`; offline install/upgrade) and record the outputs
- [X] T032 Final gate: full suite green under **both** runners + `tests/contract/test_cli.py` + `ruff check`
  clean on all touched files; record final counts (baseline 730; expect baseline + new US tests) — update
  `tests/REGRESSION.md` if the project convention records per-cycle baselines

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **User Story 1 (P1)**: Depends on Foundational — no dependencies on other stories (MVP)
- **User Story 2 (P1)**: Depends on Foundational AND US1 (serves `local` records US1 creates)
- **User Story 3 (P2)**: Depends on Foundational AND US1 (extends the import core; can be parallel with US2)
- **User Story 4 (P2)**: Depends on Foundational only — independent, parallelizable with US2/US3
- **Polish (Phase 7)**: Depends on all desired user stories being complete

### User Story Dependencies

- **US1 → US2**: US2's handoff/diff serving has nothing to serve without US1 imports (T015/T016 read `local`
  records). Keep US2 test-first on the handoff logic but validate end-to-end after US1.
- **US1 → US3**: US3 extends `import_local.py`; the CLI `--source` flag exists from US1 but the source path is
  implemented here.
- **US4**: fully independent (repository + resolver changes); parallelizable with US2 and US3.

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Core service before CLI wiring (US1/US3); DB-first ordering (register → copy → verify) in one transaction
- Validation task (both runners + ruff) closes each phase

## Parallel Opportunities

- Phase 1: T002, T003 parallel
- Phase 2: T005, T006 parallel
- US1 tests: T007, T008 parallel
- US3 tests: T019, T020 parallel; US3 impl T021 → T022 sequential
- US4 tests: T024, T025 parallel; US4 impl T026, T027 parallel
- After US1: US2 and US3 can proceed in parallel; US4 proceeds in parallel with both (independent files:
  `handoff.py` vs `import_local.py` vs `repository.py`/`resolver.py`)
- Polish: T029, T030 parallel

---

## Parallel Example: After US1 (Phase 3) completes

```bash
# Developer A — User Story 2 (handoff/diff):
Task: "Unit tests in tests/unit/test_handoff.py (local component served / empty / missing-pool)"
Task: "Extend _component_bases + generate local index in src/package_mgmt/lib/service/handoff.py"
Task: "Integration test tests/integration/test_import_deb_flow.py (import → handoff → apply → install)"

# Developer B — User Story 3 (source provenance):
Task: "Unit tests in tests/unit/test_import_deb.py (--source validation, linkage)"
Task: "Extend import_local.py with --source <dir> dsc-verified source import + binary_to_source"

# Developer C — User Story 4 (priority, independent):
Task: "Unit tests in tests/unit/test_repository.py + tests/unit/test_resolver.py"
Task: "_COMPONENT_PRIORITY['local'] in repository.py + local injection in resolver.ensure_indexes()"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1 (`import-deb` core + CLI + contract/unit tests)
   - **Error handling**: `_print_error` + standard exit codes (extending 014)
   - **Logging**: selective, at operation boundaries; user output via print()
4. **STOP and VALIDATE**: `import-deb` works standalone — import, list, show, verify, installable
5. Deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. US1 → test independently → demo (MVP: import a patched `.deb` into `local`)
3. US2 → test independently → demo (the patch flows through `handoff`/`sync`; operators install via apt)
4. US3 → test independently → demo (`--source <dir>` provenance)
5. US4 → test independently → demo (`local` wins at equal version)
6. Polish → final gate (both runners + ruff + quickstart.md)

### Parallel Team Strategy

1. Team completes Setup + Foundational together
2. Once Foundational is done: US1 first (blocks US2/US3)
3. After US1: Developer A on US2, Developer B on US3, Developer C on US4 (independent files)
4. Stories integrate independently; final gate in Phase 7

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to a specific user story for traceability
- Each user story is independently completable and testable
- Verify tests fail before implementing (TDD)
- Commit after each task or logical group
- Stop at any checkpoint to validate the story independently
- Avoid: vague tasks, same-file conflicts, cross-story dependencies that break independence
- Cache handoff flow: admin builds cache (incl. `import-deb` local lane) → verify → ship USB → operator applies
  → installs
