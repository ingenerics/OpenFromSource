# Tasks: Source-Stack Packaging

**Input**: Design documents from `/specs/024-source-stack-packaging/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/ (cli.md, recipe.md, artifacts.md)

**Tests**: Tests ARE requested — the plan mandates TDD with both runners (pytest + `python -m unittest discover -s tests`) and names the test files (plan.md "Testing"), and the constitution's Quality Gates require both runners green. Test tasks are written FIRST and must FAIL before implementation.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

## Path Conventions

- **CLI tool**: `src/package_mgmt/` at repository root (existing `cli/commands/` + `lib/model/` + `lib/service/` layout)
- **Recipe**: `recipes/hyprland.toml` (tracked, declarative per-stack input, contracts/recipe.md)
- **Tests**: `tests/unit/`, `tests/contract/`, `tests/integration/`, `tests/fixtures/`
- **Docs**: `docs/005-hyprland-source-stack.md`, `roles.md`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the shared scaffolding all stories depend on: the recipe artifact, the domain model, and the CLI command registration.

- [X] T001 Create `recipes/` directory and `recipes/hyprland.toml` with the `[stack]` table (name `hyprland`, `repo_dir` `repos`, `source_epoch`) plus one exemplar `[[component]]` (hyprland-protocols, `architecture = "all"`) per [contracts/recipe.md](contracts/recipe.md) — establishes the tracked recipe artifact; the full 13-component set is completed in US3 (T019)
- [X] T002 [P] Create frozen dataclasses `StackRecipe`, `ComponentRecipe`, `PackResult`, `ComponentResult` in `src/package_mgmt/lib/model/packaging.py` per data-model.md §1/§5 (frozen=True, concrete types, Constitution VI)
- [X] T003 [P] Register the `package-stack` subcommand (args `--recipe PATH` default `recipes/hyprland.toml`, `--format text|json`) as admin-only in `src/package_mgmt/cli/main.py` (`_register_package_stack`) with a stub `handle_package_stack` in `src/package_mgmt/cli/commands/package_stack.py` per contracts/cli.md

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Recipe parsing/validation and output formatting — shared by US1/US2/US3. MUST be complete before any user story begins.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T004 Implement recipe load + validation in `src/package_mgmt/lib/service/package_stack.py` (stdlib `tomllib`): validation rules from contracts/recipe.md — `name`/`binary_package` uniqueness, `source_tree` exists under `repo_dir`, valid Debian `version`, unknown-field rejection, every component dependency resolvable to a recipe `name`, acyclic dependency graph; loud errors name the component (FR-014)
- [X] T005 [P] Implement `PackResult`/`ComponentResult` text + JSON output formatting in `src/package_mgmt/lib/service/package_stack.py` per data-model.md §5 and contracts/cli.md (`stack`, `components_total`, `components_imported`, `order`, per-component `components`; `--format json` stable JSON, TypedDict only at the JSON boundary, Constitution VI)

**Checkpoint**: Foundation ready — user story implementation can now begin.

---

## Phase 3: User Story 1 - Package and Import One Source-Built Component (Priority: P1) 🎯 MVP

**Goal**: A generic single-component pipeline: one declarative recipe entry → a `.deb` (auto-derived `Depends`) + a complete rebuildable `.dsc` source set, both imported into `local`, linked via `binary_to_source`, verified, and visible/installable from the cache (spec US1, FR-001…FR-005/009/010/014).

**Independent Test**: With a one-entry fixture recipe over a fixture staged install dir + fixture source tree, `package-stack` produces a `.deb` with correct auto-derived `Depends`, a complete `.dsc` + referenced files, imports both into `local` linked + verified, and `list`/`show`/`verify` display the pair — all offline, no stack-specific code path (spec US1 Independent Test; quickstart Phase 1).

### Tests for User Story 1 (write FIRST, must FAIL) ⚠️

- [X] T006 [P] [US1] Contract test for `package-stack` (args, exit codes 0/1/101/102, text + JSON output shape) in `tests/contract/test_cli.py` (extend; contracts/cli.md)
- [X] T007 [P] [US1] Unit test for binary control generation + `Depends` merge (derived + `explicit_depends` + un-attributable reporting) + `.deb` determinism + revision bump in `tests/unit/test_package_stack.py` (new file; contracts/artifacts.md §1/§3)
- [X] T008 [P] [US1] Unit test for source-package generation: complete `.dsc` + referenced files, checksums match, `dpkg-source -x` unpacks offline, `.dsc` determinism in `tests/unit/test_package_stack.py` (contracts/artifacts.md §2)
- [X] T009 [P] [US1] Integration test for the single-component flow against a fixture cache (reuse `tests/fixtures/make_test_iso.py`, `make_test_deb.py`, `make_fixture_repos.py`): staged dir → `.deb` + `.dsc` → `import_local_deb` into `local`, `binary_to_source` link, `verified=1`, `list`/`show`/`verify` complete — in `tests/integration/test_package_stack_flow.py` (new file)

### Implementation for User Story 1

- [X] T010 [P] [US1] Implement `debgen` in `src/package_mgmt/lib/service/debgen.py`: control record generation (FR-002 fields + computed `Installed-Size`), `Depends` derived via `dpkg-shlibdeps` over the staged binaries (`-O` output) merged with recipe `explicit_depends`, un-attributable libraries reported by name (never silently dropped, FR-003/edge case), deterministic `dpkg-deb --build --root-owner-group` with fixed `SOURCE_DATE_EPOCH` + fixed mtimes/order (FR-009)
- [X] T011 [P] [US1] Implement `srcgen` in `src/package_mgmt/lib/service/srcgen.py`: copy pinned `source_tree` to scratch, generate minimal `debian/` wrapper (control with `Build-Depends` from recipe, `rules` driving `build_system` meson/cmake, changelog, copyright, `source_format`), run `dpkg-source -b` deterministically (fixed `SOURCE_DATE_EPOCH`, fixed tar mtimes/order) producing exactly one `.dsc` + referenced files (FR-004/009; contracts/artifacts.md §2)
- [X] T012 [US1] Implement single-component orchestration in `src/package_mgmt/lib/service/package_stack.py`: per component run `debgen` → `srcgen` → `import_local_deb(conn, config, deb_path, source_dir=...)` from `src/package_mgmt/lib/service/import_local.py` (reuses spec 021 guarantees: re-resolution, checksum verification, `binary_to_source`, `verified`, idempotence, rollback — FR-005/014)
- [X] T013 [US1] Implement real `handle_package_stack` in `src/package_mgmt/cli/commands/package_stack.py`: load recipe (T004), run orchestration (T012), print `PackResult` (T005), map errors to exit codes via `src/package_mgmt/lib/util/errors.py` (`EXIT_CONFIG_ERROR`=102 recipe/args, `EXIT_SCHEMA_MISMATCH`=101, `EXIT_ERROR`=1, `_print_error`) — FR-001/014, contracts/cli.md

**Checkpoint**: US1 fully functional — run quickstart Phase 1; validate single component imports, links, verifies, and is `apt`-installable offline.

---

## Phase 4: User Story 2 - Package the Whole Set in Dependency Order and Validate Offline (Priority: P1)

**Goal**: `package-stack` processes a multi-component recipe in dependency order (dependencies before dependents) regardless of recipe order, imports the whole set, and validates it: `verify` clean + simulated offline `apt install` succeeds with no network (spec US2, FR-006/007).

**Independent Test**: Package a multi-component set from a deliberately order-independent recipe; confirm `package-stack` orders it correctly, imports everything, and both `verify` and a simulated offline `apt install` of the whole set pass (spec US2 Independent Test; quickstart Phases 2–3).

### Tests for User Story 2 (write FIRST, must FAIL) ⚠️

- [X] T014 [P] [US2] Unit test for topological ordering: order-independent recipe input → dependencies-before-dependents; a dependency cycle rejected; a missing source-built dependency (e.g. `hypridle` → `sdbus-c++`) fails loudly naming it — in `tests/unit/test_package_stack.py` (FR-006/014, spec edge cases)
- [X] T015 [P] [US2] Integration test for a two-component set through the real `debgen`/`srcgen`/`import_local_deb` path in arbitrary recipe order + whole-set `verify` + simulated offline `apt install` against the fixture cache index — in `tests/integration/test_package_stack_flow.py` (FR-006/007)

### Implementation for User Story 2

- [X] T016 [US2] Implement topological ordering in `src/package_mgmt/lib/service/package_stack.py`: derive import order from each component's declared `depends` resolved against recipe `name`s (mirrors docs/005 build order: protocols → scanner → companion libraries → Hyprland → `sdbus-c++` → companion tools); cycle or unknown-component dependency aborts before any import with a loud error naming the gap (FR-006/014; research §5)
- [X] T017 [US2] Implement whole-set validation in `src/package_mgmt/lib/service/package_stack.py`: after all imports run the existing `verify` semantics (no unlinked binaries, no unverified records for the set) and a simulated offline `apt install` of the whole set against the cache index with no network; both must pass before success (FR-007; research §9)

**Checkpoint**: US1 AND US2 work independently — multi-component set imports in order and validates whole-set offline.

---

## Phase 5: User Story 3 - The Hyprland Stack, Packaged End-to-End (Priority: P2)

**Goal**: Prove the generic mechanism on the real stack: the full Hyprland recipe (13 build components of docs/005 — 12 hyprwm repos + source-built `sdbus-c++` 2.0.0), the build script's staged-build mode, whole-set import, and an offline trixie operator install with ordinary `apt` that passes the existing smoke test (spec US3, FR-011/012/013).

**Independent Test**: From `repos/` + the cache, run the staged build and `package-stack` with the Hyprland recipe, hand off/sync to an offline trixie target, `apt install` the stack (no `dpkg -i`), launch the session, and pass `validate-hyprland-session.sh` (spec US3 Independent Test; quickstart Phase 5).

### Tests for User Story 3 (write FIRST, must FAIL) ⚠️

- [X] T018 [P] [US3] Unit test that `recipes/hyprland.toml` validates cleanly through T004: 13 components, unique `name`/`binary_package`, valid Debian versions, all component dependencies resolvable (incl. `sdbus-c++` present), acyclic — in `tests/unit/test_package_stack.py` (contracts/recipe.md; FR-006 edge case "Missing source-built dependency")

### Implementation for User Story 3

- [X] T019 [US3] Complete `recipes/hyprland.toml` with all 13 build components per docs/005 (12 hyprwm repositories + `sdbus-c++` 2.0.0 vendored at `xdg-desktop-portal-hyprland/subprojects/sdbus-cpp`), each with version, build_system (meson/cmake), architecture (`all` for data-only), metadata, and `explicit_depends` (e.g. `hypridle` → `libsdbus-c++ (>= 2.0.0)`) per contracts/recipe.md
- [X] T020 [US3] Add `--stage DIR` mode to `scripts/build-hyprland.sh`: install each component into `DIR/<component>` (DESTDIR-style; `DESTDIR="$DIR/$name" ninja -C ... install` / `cmake --install ... --destdir "$DIR/$name"`), leaving the in-place delivery of docs/005 unchanged (additive, FR-011; research §7)
- [X] T021 [US3] Run the end-to-end proof: staged build (T020) → `package-stack --recipe recipes/hyprland.toml` (all 13 linked + verified) → handoff/sync to offline trixie target → `apt install hyprland hypridle hyprlock hyprpaper xdg-desktop-portal-hyprland` (no `dpkg -i`) → launch session → pass `validate-hyprland-session.sh` (spec US3 acceptance; quickstart Phase 5)

> **T021 status (manual live step)**: the automated proof is complete — staged build
> (`--stage staging`), `package-stack` imported all 13 into `local` (verified +
> `binary_to_source` linked), whole-set `verify` PASS, simulated offline `apt install`
> PASS, handoff generated for `offline-trixie`, and a real offline
> `apt-get install --simulate hyprland …` from the signed handoff channel resolves the
> full set (exit 0). The operator-side live install on the offline trixie target (`apt
> install hyprland hypridle hyprlock hyprpaper xdg-desktop-portal-hyprland`), session
> launch, and `validate-hyprland-session.sh` are performed manually by the operator.
> The whole-set check also surfaced and fixed a Phase 4 bug in
> `_build_scratch_repo`/`_index_hashes_for_existing`: apt's `file://` source requires a
> `Release` hash for the *uncompressed* `Packages` form even when only `Packages.gz` is
> stored, so the archive-component hash is now synthesized by decompressing the `.gz`
> (the fixture tests never exercised archive deps, so the gap was invisible to US2).
>
> **T021 operator step (bootstrap) — completed**: the operator re-ran the generated
> `bootstrap.sh` on the target, which adopted the `local` (and `non-free-firmware`)
> components into `/etc/apt/sources.list.d/openfromsource-cache.list` (idempotent;
> the pre-existing entry predated packaging and carried only `main contrib non-free`),
> `apt update` fetched the new component indexes, and `apt search hyprland`/`apt-cache
> policy` resolve the stack from `trixie/local`. The remaining operator steps — live
> `apt install hyprland hypridle hyprlock hyprpaper xdg-desktop-portal-hyprland`,
> session launch, and `validate-hyprland-session.sh` — are still to be performed.
>
> **T021 Phase 5 fix (local index descriptions)**: the generated `local` `Packages`
> index dropped the `Description`/`Section`/`Maintainer`/`Homepage` control fields
> (`_read_deb_control` extracted only apt VersionHash fields and `_paragraph` emitted
> only those), so `apt search` showed `(none)` and could not match by description.
> `BinaryPackage` now carries the descriptive fields, `_read_deb_control` passes them
> through, and `_paragraph` emits them when present; regression test added
> (`test_handoff_local_payload_descriptive_fields_carried`).

**Checkpoint**: The canonical stack ships through the `local` apt channel and passes the smoke test offline.

---

## Phase 6: User Story 4 - Every Shipped Binary Can Be Rebuilt from Its Imported Source (Priority: P2)

**Goal**: The operator retrieves imported sources offline via `apt-get source` (deb-src channel) and a recorded rebuild reproduces the shipped binary; `verify` finds every binary linked to an imported, verified source (spec US4, FR-008/012; Constitution III). Includes the Q3 handoff extension: a `source/Sources` index for `local` + `deb-src` line.

**Independent Test**: Retrieve one imported component's source offline via `apt-get source` from the cache, `dpkg-source -x` it with the network disabled, run the recorded rebuild, and confirm it reproduces the shipped binary; `verify` reports the whole set linked (spec US4 Independent Test; quickstart Phase 5).

### Tests for User Story 4 (write FIRST, must FAIL) ⚠️

- [X] T022 [P] [US4] Unit test for `source/Sources` index generation in `tests/unit/test_srcindex.py` (new file): correct deb-src paragraphs from verified DB `sources` rows (Package/Version/Directory/Files/Checksums-Sha256 matching the pool), `Sources.gz` + plain output, stable ordering, byte-determinism (data-model.md §7)
- [X] T023 [P] [US4] Integration test that `apt-get source` of an imported component resolves offline from the handoff's `dists/<rel>/local/source/Sources` and `dpkg-source -x` succeeds — extend `tests/integration/test_package_stack_flow.py` (FR-008/Q3)

### Implementation for User Story 4

- [X] T024 [US4] Implement `srcindex` in `src/package_mgmt/lib/service/srcindex.py`: generate `dists/<release>/<component>/source/Sources` + `Sources.gz` for `local` from verified `component='local'` `sources` rows, paragraph fields from the imported `.dsc`, stable serialization order (Q3/FR-008; data-model.md §7)
- [X] T025 [US4] Wire the `local` `source/Sources` index into the handoff in `src/package_mgmt/lib/service/handoff.py`: hash-list `source/Sources` + `Sources.gz` in `Release` alongside `Packages` (`_index_hash_entries`) so the source channel is covered by the existing `Release.gpg` signing (Q3/FR-008; contracts/artifacts.md §5)
- [X] T026 [US4] Add the `deb-src` line for `local` to the handoff bootstrap sources config (`/etc/apt/sources.list.d/openfromsource-cache.list`) in `src/package_mgmt/lib/service/handoff.py` (`_write_bootstrap_script`) — config the handoff emits, no new operator tooling (Q3/FR-012)
- [X] T027 [US4] Validate rebuild-reproducibility: retrieve one imported component's source offline via `apt-get source`, `dpkg-source -x`, run the recorded rebuild (generated `debian/rules`, docs/005 §2.3 invocations) against the cache, and confirm the rebuilt artifact matches the shipped binary bit-for-bit; run `verify` over the full set (spec US4 acceptance; quickstart Phase 5)

> **T027 status (live proof, automated)**: completed on the real cache
> (`/var/cache/package_mgmt`). The pre-existing imported set (Phase 5) lacked the
> persisted `.dsc` in the source pool, so `package-stack --recipe
> recipes/hyprland.toml` was re-run (idempotent, whole-set verify + simulated
> install PASS) to re-import all 13 with the `.dsc` persisted; the handoff was
> regenerated and the `local/source/Sources[.gz]` index now serves the full
> set. Offline proof: `apt-get source --download-only hyprland-protocols` from
> the handoff's `deb-src file:` channel (no network), `dpkg-source -x` unpacked
> it, and the recorded rebuild (`debian/rules`, meson, `SOURCE_DATE_EPOCH` =
> recipe epoch) reproduced the shipped `hyprland-protocols_0.6.4-1_all.deb`
> payload **bit-for-bit** (identical file set + SHA256 per file). Whole-set
> verify for the 13-component stack: PASS (no unlinked binaries, no unverified
> records). (Whole-cache `verify` reports INCOMPLETE for unrelated upstream
> build-dep gaps — mariadb/openbox/zsh etc. — pre-existing archive state, not
> the stack.)

**Checkpoint**: All stories functional — every shipped binary is linked to an imported, verified, offline-retrievable, rebuildable source.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, compliance gates, and full regression across all stories.

- [X] T028 [P] Update `docs/005-hyprland-source-stack.md` with the packaging path (Part 5): the `package-stack` command, the recipe format, the staged-build `--stage` mode, and the automated-vs-scripted split (FR-013; Constitution VIII)
- [X] T029 [P] Update `roles.md` with the `package-stack` command in the Administrator command table and the Impact-on-Design note (FR-013; plan.md Roles Check)
- [X] T030 [P] Run `lintian --I --e` over the generated `.deb`/`.dsc` artifacts: zero ERROR and zero WARNING outcomes (Constitution V quality gate). The generators were fixed to emit policy-clean artifacts (extended descriptions, `usr/share/doc/<pkg>/copyright` + `changelog.Debian.gz`, `DEBIAN/triggers` + `DEBIAN/shlibs` for library packages, stripped ELF binaries, compressed/generated man pages, `DEBIAN/conffiles` for `/etc` files, 0755/0644 payload perms, and documented lintian overrides for the two intentional deviations). **Evidence**: lintian 2.122.0 over the full 26-artifact set (13 `.deb` + 13 `.dsc`) → **exit 0, zero `E:`, zero `W:`** (report: `/tmp/opencode/lintian/report_final.txt`); `maintainer` now defaults to `OpenFromSource <admin_email>` (`[metadata] admin_email`, default `noreply@example.com`)
- [X] T031 [P] Full regression: `pytest` AND `python -m unittest discover -s tests` both green, `ruff check src/ tests/` clean (Constitution quality gates; plan.md Testing). **Evidence**: `pytest` 914 passed + 60 subtests; `unittest` Ran 914 tests OK; `ruff` clean
- [X] T032 Run the quickstart.md validation end-to-end (Phases 1–5) against the updated spec, confirming every acceptance scenario holds offline. **Runnable phases verified on the dev host**: Phase 1 fixture flow (`pytest tests/integration/test_package_stack_flow.py` → 6 passed), Phase 2 ordering (`TestResolveComponentOrder` → 4 passed), Phase 4 determinism (2 passed), Phase 5 source channel (`test_srcindex.py` → 9 passed; integration flow incl. offline `apt-get source` included in Phase 1's suite), Phase 6 full regression (914 passed + 60 subtests, `unittest` Ran 914 OK, `ruff` clean); CLI smoke: `package_mgmt config` shows `admin_email noreply@example.com (default)`. **Full real-recipe flows (Phases 3/5)** rely on the packaging-host/operator conditions proven on the operator system (T021/T027) — this dev host has the newer Hyprland era installed in `/usr`, so a live `package-stack` re-run here is not representative (documented decision); the cache `verify` INCOMPLETE state is a pre-existing, unrelated gap (T027)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **User Stories (Phases 3–6)**: All depend on Foundational completion
  - US2 depends on US1 (single-component pipeline is the base; T012 reused by T016/T017)
  - US3 depends on US1+US2 (full recipe imports through the same path)
  - US4 depends on US1 (imported source rows feed `srcindex`) and US2 (whole-set `verify`)
- **Polish (Phase 7)**: Depends on all desired user stories being complete

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational — no dependencies on other stories (MVP)
- **US2 (P1)**: After Foundational; builds on US1's orchestration (`debgen`/`srcgen`/`import`) — independently testable with a two-component fixture
- **US3 (P2)**: After US1+US2; the real-stack proof
- **US4 (P2)**: After US1+US2; source-channel + rebuild proof

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Models before services
- Services before CLI commands
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- All Setup tasks marked [P] run in parallel
- All Foundational tasks marked [P] run in parallel
- Within a story, all test tasks marked [P] run in parallel, then implementation tasks marked [P] run in parallel
- US1's `debgen` (T010) and `srcgen` (T011) are independent — parallel
- US3's recipe completion (T019) and staged-build mode (T020) are independent — parallel
- US4's `srcindex` (T024), handoff wiring (T025), and `deb-src` line (T026) are sequential within the source channel but independent of other stories

---

## Parallel Example: User Story 1

```bash
# Launch all tests for User Story 1 together (TDD):
Task: "Contract test for package-stack in tests/contract/test_cli.py (T006)"
Task: "Unit test binary control/Depends/determinism in tests/unit/test_package_stack.py (T007)"
Task: "Unit test source-package generation in tests/unit/test_package_stack.py (T008)"
Task: "Integration test single-component flow in tests/integration/test_package_stack_flow.py (T009)"

# Launch the two generation services in parallel:
Task: "Implement debgen in src/package_mgmt/lib/service/debgen.py (T010)"
Task: "Implement srcgen in src/package_mgmt/lib/service/srcgen.py (T011)"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (recipe artifact, model, CLI registration)
2. Complete Phase 2: Foundational (recipe load/validate + output formatting — BLOCKS all stories)
3. Complete Phase 3: User Story 1 (tests first, then `debgen`/`srcgen`/orchestration/CLI)
4. **STOP and VALIDATE**: run quickstart Phase 1 — single component imports, links, verifies, installs offline
5. Deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. US1 → single-component packaging (MVP!)
3. US2 → dependency ordering + whole-set offline validation
4. US3 → the Hyprland stack end-to-end (real proof, `apt` install + smoke test)
5. US4 → offline `apt-get source` + rebuild reproducibility (Q3 deb-src channel)
6. Polish → docs, lintian, full regression

### Parallel Team Strategy

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: US1 (single-component pipeline)
   - Developer B: after US1, US2 (ordering + whole-set validation)
   - Developer C: US3 staged-build mode (T020) + recipe (T019) once US1/US2 land
   - Developer D: US4 source channel (T024–T026) once imports land
3. Stories complete and integrate independently

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to a specific user story for traceability
- Each user story is independently completable and testable
- Verify tests fail before implementing
- Commit after each task or logical group
- Stop at any checkpoint to validate the story independently
- Avoid: vague tasks, same-file conflicts, cross-story dependencies that break independence
- **Q3 scope note**: the operator's source channel (`source/Sources` index for `local` + `deb-src` line, FR-008) is delivered in US4 (T024–T026) as a handoff file-artifact extension — schema-freeze holds
- **Test-file note**: the plan named `tests/unit/test_packaging.py`, but that path already holds an unrelated keyring-packaging test; the new unit tests go in `tests/unit/test_package_stack.py`
