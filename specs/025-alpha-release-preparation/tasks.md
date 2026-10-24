# Tasks: Alpha-Release Preparation

**Input**: Design documents from `/specs/025-alpha-release-preparation/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Test tasks are included below — TDD is required by plan.md (`TDD: tests
written first; the 914 baseline re-recorded at the end`, FR-020) and the spec's
acceptance scenarios name specific test files. Write each test FIRST (expect it to
fail), then implement.

**Organization**: Tasks are grouped by user story to enable independent implementation
and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **CLI tool**: `src/package_mgmt/` at repository root (lib/ = services + model + util, cli/ = commands)
- **Cache directory**: admin-managed package cache with apt repository layout: `cache/pool/`, `cache/dists/`
- **Metadata**: `cache/package_mgmt.db` for local index
- **Tests**: `tests/unit/`, `tests/integration/`, `tests/contract/`, `tests/fixtures/`
- **Docs**: `README.md`, `docs/001-quickstart-howto.md`, `AGENTS.md`, `manifests/`

---

## Phase 1: Setup (Baseline)

**Purpose**: Confirm the recorded 914-test baseline is green before any edits, so every
subsequent change is attributable. No new project scaffolding — this is a hardening pass
over an existing, shipped codebase (schema frozen per spec 010/011).

- [x] T001 Verify the recorded baseline is green on the feature branch before edits: run `pytest`, `python -m unittest discover -s tests`, `ruff check src/ tests/`, and `pytest tests/contract -q` — all must pass at the 914 baseline (`tests/REGRESSION.md`), zero failures

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared helper homes that multiple user stories consume. These MUST exist
before US2/US3/US5/US8 re-point to them. Creation only — re-pointing happens in the
consuming story.

**⚠️ CRITICAL**: US2 (downloader fs re-point), US3 (fixtures), US5 (handoff fs re-point),
and US8 (consolidation) cannot complete until this phase is done.

- [x] T002 [P] Create shared filesystem/hash home `src/package_mgmt/lib/util/fs.py` (F1) with a uniform API: `file_sha256(path, errors="raise"|"none")`, `atomic_write_bytes(path, data, mode=0o644)`, `atomic_copy(src, dst, verify=None)` — typed signatures per Constitution VI; no callers yet
- [x] T003 [P] Add the shared dependency-satisfaction predicate `deps_satisfied(conn, name, spec) -> bool` to `src/package_mgmt/lib/model/dependency.py` (F2), capturing current semantics (direct current-row constraint check, then versioned-`provides` fallback); no consumers yet
- [x] T004 [P] Create `tests/fixtures/db.py` (F9) consolidating `_insert_binary`, `_config`, and the fixture `_pool_prefix` from `tests/fixtures/make_test_deb.py:177` — one update point for schema fixtures; no consumers re-pointed yet

**Checkpoint**: Shared homes (`fs.py`, `deps_satisfied`, `tests/fixtures/db.py`) exist and import cleanly; existing suite still green (no behavior change).

---

## Phase 3: User Story 1 - Every Command Works From a Standard Install (Priority: P1) 🎯 MVP

**Goal**: A documented `pip install .` with **no extra packages** imports and runs every
`package_mgmt` command — including `--help` and `ensure-base`'s ISO-reading path — with
no `ModuleNotFoundError`, and the built wheel ships the archive-signing keyring.

**Independent Test**: In a fresh venv, `pip install .` (no extras) → `package_mgmt --help`
succeeds; the same check against the built wheel (not editable) succeeds; a fixture-ISO
`ensure-base` run works from the plain install.

### Tests for User Story 1 (TDD — write first, expect FAIL)

- [X] T005 [P] [US1] Write the declarative contract check that `pyproject.toml [project] dependencies` contains `pycdlib` in `tests/unit/test_packaging_install.py` (fails until T006; keeps the keyring assertions of `tests/unit/test_packaging.py` in place)

### Implementation for User Story 1

- [X] T006 [US1] Move `pycdlib` from `[project.optional-dependencies] dev` into `[project] dependencies` in `pyproject.toml` (R-1; `iso_reader.py:12` imports it at module top) so `dependencies = ["python-debian", "pycdlib"]` — closes the documented-install break and review 020/N-4
- [X] T007 [US1] Run the release checks (quickstart Phase 1): fresh venv → `pip install .` (no extras) → `python -c "import package_mgmt.cli.main"` and `package_mgmt --help` succeed — T005 and T006 are green together
- [X] T008 [US1] Update the install claim in `docs/001-quickstart-howto.md` Step 1/2: `pycdlib` is now a runtime dependency (the note currently names `python-debian` as the project's one runtime dependency), so a plain `pip install .` runs every command

**Checkpoint**: T005/T006 green; `pip install .` (no extras) + `--help` verified in a fresh venv.

---

## Phase 4: User Story 2 - `download` Stays Clean After Local Imports (Priority: P1)

**Goal**: After any local `.deb` import or `package-stack`, the next `download` — and every
subsequent one — exits 0 with zero command errors, even with nothing to fetch; `local`
records stay `verified` and are never spuriously re-checked against a nonexistent
`<release>/local` index.

**Independent Test**: Import a `local` deb, then run `download` — exit 0, zero command
errors, no "cached index missing for <release>/local" message, the local record stays
`verified`; repeat with mixed archive+local packages and it stays clean.

### Tests for User Story 2 (TDD — write first, expect FAIL)

- [X] T009 [P] [US2] Write the engine-level regression test in `tests/unit/test_downloader.py`: import a `local` deb, then `download_pending_binaries` → zero command errors and the local row stays `verified` (closes the coverage hole — the only existing test exercises the SQL helper, not `_group_current`)
- [X] T010 [P] [US2] Write the integration test in `tests/integration/test_import_deb_flow.py`: import `local` deb → `download` succeeds with zero command errors, no missing-`local`-index error

### Implementation for User Story 2

- [X] T011 [US2] Add `AND component != 'local'` to the binary grouping query in `_group_current` (`src/package_mgmt/lib/service/downloader.py:713-723`), mirroring the existing exclusion in `_group_sources_to_check` (`downloader.py:691-710`) — local rows are verified at import from the supplied payload (R-2/F5)
- [X] T012 [US2] Re-point `downloader._file_sha256` (`downloader.py:353-364`) to the shared `file_sha256` in `src/package_mgmt/lib/util/fs.py` (F1 call site; depends on T002)

**Checkpoint**: T009/T010/T011/T012 green; `download` after import exits 0 with zero command errors.

---

## Phase 5: User Story 3 - Importing a Newer Local Revision Supersedes the Older One (Priority: P1)

**Goal**: Importing a newer `local` version supersedes the prior `current` local row —
exactly one `current` row per package/architecture, handoff/diff emit only the newest
stanza, the stale payload is retained (content-addressed, still checksummed on sync), and
reads become deterministic.

**Independent Test**: Import `patch-tool 1.2-3`, then `1.3-0`; confirm exactly one
`current` local row, the index emits only `1.3-0`, the `1.2-3` payload remains in the pool,
and a lagging operator can still install `1.2-3` from a snapshot that references it.

### Tests for User Story 3 (TDD — write first, expect FAIL)

- [X] T013 [P] [US3] Write the cross-version supersede unit test in `tests/unit/test_import_deb.py`: import `1.2-3` then `1.3-0` → exactly one `current` local row, handoff/diff emit only `1.3-0`, `1.2-3` payload retained and still checksummed on sync, and a re-import of the identical version stays idempotent (FR-004/FR-005)
- [X] T014 [P] [US3] Write the shared-primitive test for `supersede_current_binary` (used by both the archive path and the import lane) in `tests/unit/test_resolver.py`

### Implementation for User Story 3

- [X] T015 [US3] Extract the resolver's existing supersede dance (`resolver.py:1118-1138`: `_find_current_binary` → mark old `status='superseded'` → upsert new) into a reusable helper `supersede_current_binary(conn, package_name, architecture, version, release, component, deps_json)` in `src/package_mgmt/lib/service/resolver.py`; re-point the archive path to it (F8 — one supersede semantics)
- [X] T016 [US3] Call `supersede_current_binary` from `import_local_deb` (`src/package_mgmt/lib/service/import_local.py:296-403`) before the upsert, scoped to `component='local'`; stale payloads are **retained** (no delete) so lagging operators still install (R-3)

**Checkpoint**: T013/T014/T015/T016 green; one `current` local row per name/arch after a newer-version import.

---

## Phase 6: User Story 4 - `align-repo` Validates Against the Configured Baseline (Priority: P1)

**Goal**: `align-repo` derives its baseline from the configured first archive source
(release/component) instead of a hard-coded `trixie/main`, and reports the correct era
verdict on any configured source layout with no false "everything unmet".

**Independent Test**: Run `align-repo` under a config whose first source is not
`trixie/main` (and with no `trixie/main` index cached); it validates against the actual
configured baseline and reports a meaningful verdict.

### Tests for User Story 4 (TDD — write first, expect FAIL)

- [x] T017 [P] [US4] Write the config-driven baseline variant test in `tests/unit/test_alignment.py`: a non-trixie / first-component-not-`main` config yields the correct verdict (no blanket "everything unmet"), and the standard trixie/main verdict is unchanged (FR-006)

### Implementation for User Story 4

- [x] T018 [US4] Derive the baseline release/component from `config.sources[0]` (`release`, `components[0]`) in `load_baseline` (`src/package_mgmt/lib/service/alignment.py:177-202`) and refresh `BASELINE_LABEL` (`alignment.py:29`) from the same source (R-4)
- [x] T019 [US4] Update `_packages_gz_path` (`src/package_mgmt/cli/commands/align_repo.py:149-150`) to build the cached-index path from the configured baseline instead of pinning `trixie/main`; align-repo already runs under a loaded config (`cli/main.py`), so no new CLI plumbing

**Checkpoint**: T017/T018/T019 green; correct verdict on non-baseline configs, standard trixie/main unchanged.

---

## Phase 7: User Story 5 - The Private Signing Key Stays Off Operator Media by Default (Priority: P1)

**Goal**: The cache-local signing keyring no longer lives inside the handoff payload tree;
a generated handoff payload contains only the **public** `offline-cache.gpg`. The secret
keyring is relocated to `{base_dir}/.gnupg-handoff/`, no migration, and the copy-time
decision is documented.

**Independent Test**: Generate a handoff, inspect the payload tree for private key material
(none present — public key only), and confirm the docs record the copy-time decision
(exclude the keyring location unless the operator is a trusted relay).

### Tests for User Story 5 (TDD — write first, expect FAIL)

- [x] T020 [P] [US5] Write the path test in `tests/unit/test_paths.py`: `handoff_gnupg_dir` resolves to `{base_dir}/.gnupg-handoff/`, outside the payload tree (FR-007)
- [x] T021 [P] [US5] Write the handoff payload test in `tests/unit/test_handoff.py`: after `generate_handoff`, the payload tree contains the public `offline-cache.gpg` but **no** secret keyring material (FR-007/FR-008)

### Implementation for User Story 5

- [x] T022 [US5] Change `handoff_gnupg_dir` to `{base_dir}/.gnupg-handoff/` in `src/package_mgmt/lib/util/paths.py:55-59` (R-5; derived path, no new config key)
- [x] T023 [US5] Update `_ensure_key` and the key/signature writers in `src/package_mgmt/lib/service/handoff.py:1086-1095` to use the new home; key still generated once and reused (stable trust anchor); **no migration** — legacy in-tree keyrings are unused, a fresh key is generated at the new location
- [x] T024 [US5] Re-point the handoff fs helpers (`_atomic_write` `handoff.py:705-726`, `_copy_verified` `handoff.py:1178-1196`, `_file_sha256` `handoff.py:1170-1175`) to `src/package_mgmt/lib/util/fs.py` — `_copy_verified` becomes `atomic_copy(..., verify=True)` (F1 call site; depends on T002)
- [x] T025 [US5] Document the key-handling posture in `README.md` and `docs/001-quickstart-howto.md`: a plain copy of `{base_dir}` carries the keyring; admins SHOULD exclude `.gnupg-handoff/` from operator media unless the operator is a trusted relay that must re-sign (FR-008)

**Checkpoint**: T020/T021/T022/T023/T024/T025 green; handoff payload is public-key-only and the copy posture is documented.

---

## Phase 8: User Story 6 - Graybeard `ensure-base` Warns When Trixie Components Are Incomplete (Priority: P1)

**Goal**: Under graybeard config (`[fetch] source = true` AND `[resolution]
max_source_dep_depth = -1`) with trixie sources missing any of `main`/`contrib`/
`non-free`/`non-free-firmware`, `ensure-base` output **starts with** a descriptive,
non-blocking warning naming the missing components (first stdout lines + first entry of
the run's structured warnings).

**Independent Test**: Under a graybeard config with only `main`, `ensure-base` output
begins with the missing-component warning; with all four components the warning
disappears; under a developer config (finite depth) no such warning.

### Tests for User Story 6 (TDD — write first, expect FAIL)

- [x] T026 [P] [US6] Write the helper unit test in `tests/unit/test_config.py`: the graybeard missing-component helper returns the warning string only for `source=true` + `depth=-1` + missing canonical component, and `None` for complete graybeard and all non-graybeard configs (FR-009)
- [x] T027 [P] [US6] Write the service test in `tests/unit/test_ensure_base.py`: the warning is appended as the **first** entry of `EnsureBaseStats.warnings` under an incomplete graybeard config (FR-009)
- [x] T028 [P] [US6] Write the contract test in `tests/contract/test_cli.py` (additive): text mode — the warning is the first stdout line before the summary and names the missing components; complete graybeard / developer / regular_user configs emit no such warning (FR-009)

### Implementation for User Story 6

- [x] T029 [US6] Add the graybeard missing-component warning helper to `src/package_mgmt/lib/service/config_loader.py` alongside `source_depth_zero_warning` (`config_loader.py:70-82`): returns a warning string when graybeard config AND the union of trixie-source components is missing any of the four canonical components, else `None`
- [x] T030 [US6] In `src/package_mgmt/lib/service/ensure_base.py`, append the warning as the first entry of `EnsureBaseStats.warnings` under the graybeard/incomplete condition (advisory, non-blocking)
- [x] T031 [US6] In `src/package_mgmt/cli/commands/ensure_base.py`, emit the warning before the summary so it is the first stdout lines and present in machine-readable warnings

**Checkpoint**: T026/T027/T028/T029/T030/T031 green; graybeard `ensure-base` opens with the warning and no warning otherwise.

---

## Phase 9: User Story 7 - The Release Paper-Trail Is Complete (Priority: P2)

**Goal**: The release-facing records are current and correct before the alpha tag: README
figures, a real GPL-3.0-or-later `LICENSE` file, the `AGENTS.md` SPECKIT block, and the
Hyprland manifest's `reason`.

**Independent Test**: Review the README figures, confirm a `LICENSE` file exists and is
referenced, confirm the `AGENTS.md` block is current, and confirm the Hyprland manifest's
`reason` records the manual pin.

### Implementation for User Story 7

- [x] T032 [US7] Update `README.md` figures: 24 feature specs (`README.md:98` currently reads 22) and 914 recorded tests (`README.md:101` currently reads 808), and resolve the dangling `LICENSE` ToDo note (`README.md:15`) to reference the new file (R-6)
- [x] T033 [US7] Add the `LICENSE` file at the project root with the GPL-3.0-or-later text (user decision, Clarifications Q4) — resolves the `LICENSE` ToDo (R-6, FR-010, SC-008)
- [x] T034 [US7] Refresh the `AGENTS.md` SPECKIT block to reflect implemented status (spec 024 complete; this feature current — see AGENTS.md:7-17)
- [x] T035 [US7] Update `manifests/hyprland-0.49.toml`'s `reason` to record the manual build-verified 0.49 pin (matching `docs/005`), so `align-repo --verify` re-validation is self-consistent (R-6)

**Checkpoint**: README figures current, `LICENSE` file present and referenced, AGENTS.md block current, manifest `reason` records the manual pin.

---

## Phase 10: User Story 8 - Code-Review Low-Hanging Fruit Is Taken Along (Priority: P2)

**Goal**: The code review's consolidation and craft items land with the alpha — shared
fs/hash helpers (remaining F1 sites), one dependency-satisfaction rule (F2 consumers),
public re-exports (F3), one `local`-entries generator (F4), packaging/hash helper
consolidation (F6/F7), test scaffolding re-pointing (F9), craft sweeps (F10/R-8), config
path validation (R-10), prerequisites matrix (R-11), and fetch-hook consistency (R-9). No
behavior change beyond the intended package-stack warning.

**Independent Test**: The full recorded suite (both runners, ruff, contract suite) is green
after all edits, with no output-shape regressions except the new package-stack warning on
unreadable payloads.

### Implementation for User Story 8

- [X] T036 [US8] Expose the service-side private helpers under public names (F3): `_pool_prefix`, `_local_binary_entry`, `_paragraph`, `_write_gzip` → `pool_prefix`, `local_binary_entry`, `paragraph`, `write_gzip` in `src/package_mgmt/lib/service/handoff.py`; update consumers `src/package_mgmt/lib/service/package_stack.py:38-43` and `src/package_mgmt/lib/service/srcindex.py:19-20` (zero behavior change)
- [X] T037 [US8] Expose `_print_error` → `print_error` in `src/package_mgmt/lib/util/errors.py:44` and update the ~12 command modules importing it (`cli/commands/config.py:6`, `init.py:16`, `list.py:7`, `show.py:7`, and the rest) (F3)
- [X] T038 [US8] Create the `local_entries()` generator yielding `(rid, name, version, entry_or_none, used_fallback)` per verified current `local` row (looping `local_binary_entry` + `paragraph`) in `src/package_mgmt/lib/service/handoff.py` (F4)
- [X] T039 [US8] Use `local_entries()` in `generate_handoff`'s local branch (`handoff.py:1521-1551`) and `generate_diff`'s local branch (`handoff.py:1311-1358`) (F4)
- [X] T040 [US8] Use `local_entries()` in `_local_packages_text` (`src/package_mgmt/lib/service/package_stack.py:538-555`) and change the call site to **warn** on unreadable payloads instead of silently skipping (F4 behavior change)
- [X] T041 [P] [US8] Write the test in `tests/unit/test_package_stack.py`: an unreadable `local` payload during `package-stack` surfaces a warning naming the component, not a silent skip (FR-014)
- [X] T042 [US8] Consolidate the packaging helpers (F6): `_changelog_date` + `_FALLBACK_MAINTAINER` + maintainer resolution into one module shared by `src/package_mgmt/lib/service/debgen.py:44-54,115-116` and `src/package_mgmt/lib/service/srcgen.py:29-37,279`
- [X] T043 [US8] Add one shared `read_dsc_files(dsc)` helper in `src/package_mgmt/lib/service/packaging_util.py` used by `import_local`'s source validation (`import_local.py:184-197`); `srcgen._dsc_file_count` keeps the original count expression (`len(Checksums-Sha256 or Files)`, not the unique-name union) so the reported `source_files` count is byte-compatible with the pre-consolidation behavior (F6)
- [X] T044 [US8] Add the shared release-hash helper `hash_entries_for_pair(component, plain_rel, plain_data, gz_rel, gz_data)` with the decompress-on-demand variant preserved as an option (F7); re-point `handoff._index_hash_entries` (`handoff.py:1000-1020`) and `srcindex.source_index_hash_entries` (`srcindex.py:112-135`)
- [X] T045 [US8] Re-point `import_local._file_sha256` (`import_local.py:76-81`) and `_copy_atomic` (`import_local.py:84-103`) to `src/package_mgmt/lib/util/fs.py` (F1 call site; depends on T002)
- [X] T046 [US8] Re-point `index_loader._write_index_atomic` (`index_loader.py:242-257`) to `atomic_write_bytes` in `src/package_mgmt/lib/util/fs.py` (F1 call site; depends on T002)
- [X] T047 [US8] `srcindex._file_meta` (`srcindex.py:50-58`) computes md5 **and** sha256 in one bounded-chunk pass rather than calling the shared `file_sha256` (a second read would double payload I/O); `file_sha256` in `src/package_mgmt/lib/util/fs.py` remains the shared single-digest helper for the other F1 call sites (F1 call site; depends on T002)
- [X] T048 [US8] Re-point `import_local._dep_satisfied` (`import_local.py:114-131`) to the shared `deps_satisfied` in `src/package_mgmt/lib/model/dependency.py` (F2; depends on T003)
- [X] T049 [US8] Re-point `cli/commands/verify._dep_satisfied` (`cli/commands/verify.py:157-188`) to the shared `deps_satisfied` (F2; depends on T003)
- [X] T050 [US8] Have `resolver.select_provider` (`src/package_mgmt/lib/service/resolver.py:234-264`) consult the shared `deps_satisfied` predicate (F2; depends on T003)
- [X] T051 [P] [US8] Write the focused predicate test in `tests/unit/test_dependency.py`: the shared `deps_satisfied` rule (direct current-row check + versioned-`provides` fallback) matches the three consumers' prior semantics (FR-012)
- [X] T052 [US8] Apply the craft sweep `from typing import Set` → `set` in `src/package_mgmt/lib/service/config_loader.py:3` (F10/R-8)
- [X] T053 [US8] Remove the dead `_file_sha256(dest)` call and its misleading comment in `src/package_mgmt/lib/service/import_local.py:373` (R-8)
- [X] T054 [US8] Rename the shadowing exception `ImportError` → `ImportDebError` in `src/package_mgmt/lib/service/import_local.py:53` (and its raise sites) (F10/R-8)
- [X] T055 [US8] Type the resolver's internal `stats: dict` accumulator as a mutable dataclass (`src/package_mgmt/lib/service/resolver.py:447-512`), keeping the error-list-retraction sites (index marks) on a mutable list (F10)
- [X] T056 [US8] Forward `fetch_fn` on the loopback/unverified index-fetch path (`index_loader.py:355-358`), matching the verified path (`index_loader.py:361,377`) (R-9)
- [X] T057 [P] [US8] Write the test in `tests/unit/test_index_loader.py`: `fetch_index` forwards `fetch_fn` on the unverified path (R-9)
- [X] T058 [US8] Validate non-`base_dir` path overrides as absolute **after** `expand_path` in `src/package_mgmt/lib/service/config_loader.py:246-252` (`binary_cache`, `source_cache`, `iso_dir`, `db_path`, `log_dir`); reject a relative override with a clear `ConfigError` (exit 102) so cache locations are never silently CWD-dependent (R-10)
- [X] T059 [P] [US8] Write the test in `tests/unit/test_config.py`: a relative path override other than `base_dir` is rejected with a clear config error (R-10)
- [X] T060 [US8] Add the per-command external-tool prerequisites matrix to `docs/001-quickstart-howto.md`: command → required tools/packages (`gpgv` index verification, `gpg` handoff, `dpkg-deb`/`dpkg-shlibdeps`/`readelf`/`strip` debgen, `dpkg-source` srcgen, `apt-get` package-stack simulation, `dpkg`/`ar` import-deb readers) with their failure modes (R-11)
- [X] T061 [US8] Consolidate the near-duplicate test scaffolding across the ~12 test modules (`test_sync.py`, `test_diff_generator.py`, `test_handoff.py`, `test_srcindex.py`, `test_downloader.py`, `test_import_deb.py`, `test_systems.py`, `test_snapshot.py`, `test_apply_script.py`, `test_bootstrap_script.py`, `test_resolver_build_deps.py`, and `tests/fixtures/make_test_deb.py:177`) to import `_insert_binary`/`_config`/`_pool_prefix` from `tests/fixtures/db.py` (F9; depends on T004) — one update point, no behavior change

**Checkpoint**: All US8 tasks green; full suite (both runners + ruff + contract) passes with no output-shape regressions except the intended new package-stack warning.

---

## Phase 11: User Story 9 - Release Verification: Baseline, Artifact, Physical Proofs, Review-07 Findings Remediation (Priority: P2)

**Goal**: The release checks are executed and recorded: the test baseline re-runs green
under both runners with ruff clean and the contract suite green and is re-recorded; the
built wheel is inspected; the FAT-USB bootstrap and graybeard DVD-1 physical proofs are
executed and appended to the release notes; and the two Review-07 medium findings — NEW-A
(component-blind same-version supersede) and NEW-B (diff omits the `local` source/Sources
channel) — are remediated with tests before the tag.

**Independent Test**: Run the recorded baseline after the fixes, inspect the built wheel in
a fresh no-extras environment, and execute the FAT-USB and DVD-1 physical proofs; all
results are appended to the release notes. NEW-A: importing a local `.deb` at the same
version as a current archive row creates a `current` `local` row and never marks the archive
row `verified`; NEW-B: the diff's `local` Release hash-lists `source/Sources` when a `local`
component is served, and `diff_complete` requires it.

### Implementation for User Story 9

- [x] T062 [US9] Re-run the full baseline after all fixes and re-record it in `tests/REGRESSION.md` at/above the 914-test baseline: `pytest`, `python -m unittest discover -s tests`, `ruff check src/ tests/`, `pytest tests/contract -q` all green (FR-020)
- [x] T063 [US9] Build the wheel (`pip wheel .`) and inspect it in a fresh no-extras venv: import + `--help` succeed, the wheel ships `package_mgmt/data/debian-archive-keyring.gpg` and `pycdlib` is installed as a runtime dep, and a fixture-ISO `ensure-base` runs from the plain installed wheel (FR-002; closes review 020/N-4)
- [x] T064 [US9] Execute the FAT-USB bootstrap physical proof: build a cache, copy to a FAT-USB stick, run `bootstrap.sh` on the target, `apt update` + `apt install` from the cache with no network — append the result to the release notes (FR-020)
- [x] T065 [US9] Execute the graybeard DVD-1 physical proof: `init` + `ensure-base` from a Debian Trixie DVD-1 image under the graybeard config, offline install simulation — append the result to the release notes (FR-020)

### Tests for Review-07 Findings Remediation (TDD — write first, expect FAIL)

- [x] T074 [P] [US9] Write the archive-collision regression test in `tests/unit/test_resolver.py`: with a `current` archive row (`component='main'`) at `patch-tool 1.2-3`, importing a local `.deb` of the **same** version via `import_local_deb` creates a distinct `current` `local` row (archive row superseded), the archive row is NOT marked `verified` by the import, and the handoff emits the `local` stanza, not the upstream one (NEW-A; residual gap in R-3)
- [x] T076 [P] [US9] Write the diff source-channel test in `tests/unit/test_diff_generator.py`: after `generate_diff` with a served `local` component and verified `local` source rows, the diff's `local` Release hash-lists `source/Sources` and the index exists under `diff/dists/<rel>/local/source/`, matching `generate_handoff` (NEW-B)

### Implementation for Review-07 Findings Remediation

- [x] T075 [US9] Scope `supersede_current_binary`'s same-version early return (`src/package_mgmt/lib/service/resolver.py:159-163`) to the incoming `component`: reuse an existing `current` row only when it belongs to the same component; a different-component same-version row is superseded and a new `local` row is created — `_find_current_binary` (`resolver.py:131-137`) stays the finder, but the reuse decision becomes component-aware (NEW-A)
- [x] T077 [US9] Mirror the handoff's `local` source-index emission in `generate_diff` (`src/package_mgmt/lib/service/handoff.py:1405-1421`): for a served `local` component, call `srcindex.write_source_index` and include the `source/Sources` hash entries in the diff Release, matching `generate_handoff` (`handoff.py:1618-1632`) (NEW-B)
- [x] T078 [US9] Require the `local` `source/Sources` index in `diff_complete` (`handoff.py:361-371`) when a `local` component is served, mirroring `handoff_complete` (`handoff.py:106-107`), so a diff that cannot carry the `local` source channel is refused rather than silently degrading the operator's `deb-src local` (NEW-B)

**Checkpoint**: Baseline re-recorded green; wheel inspected; both physical proofs executed and appended to the release notes; Review-07 findings remediated (NEW-A, NEW-B) with T074/T075/T076/T077/T078 green.

---

## Phase 12: User Story 10 - Tutorial Track, Authored Jointly, Then the Alpha Version Bump (Priority: P3)

**Goal**: The Administrator and the agent collaboratively add new tutorials (none
pre-existing; topics/scope and a **new** tutorial style agreed jointly, distinct from
`docs/001`–`docs/005`) that stay consistent with the implemented behavior; at the end of
the phase the project version is set to `0.1.0-alpha`.

**Independent Test**: A tutorial added in a working session is complete, follows the agreed
tutorial style, and its steps match the implemented behavior; after the tutorials are
agreed, `pyproject.toml` reports `0.1.0-alpha` and the README/quickstart references are
consistent.

### Implementation for User Story 10

- [x] T066 [US10] Collaborative working session 1: agree the tutorial topics and scope, and define the **new** tutorial style (distinct from `docs/001`–`docs/005`), reproducible from the docs and cache alone (FR-021)
- [x] T067 [US10] Author the agreed tutorial(s) under `tutorials/` in the agreed new style, with the Administrator reviewing and steering (FR-021)
- [x] T068 [US10] Validate each tutorial's commands and steps against the implemented behavior from Phases 1–2 (walk each tutorial against the shipped tool; correct any drift in the same working session) (FR-021, US10 SC-009)
- [x] T069 [US10] Set the project version to `0.1.0-alpha` (from `0.1.0`) in `pyproject.toml:7` — the Phase-3 exit gate, once the tutorials are complete and agreed (FR-021)
- [x] T070 [US10] Update the release-facing references (README, quickstart) to be consistent with `0.1.0-alpha` (FR-021)

**Checkpoint**: Tutorial set present in the agreed style and behavior-consistent; `pyproject.toml` version is `0.1.0-alpha`; README/quickstart references consistent.

---

## Phase 13: Polish & Cross-Cutting Concerns

**Purpose**: Final validation that every constitution gate is green and no unintended
behavior change slipped in.

- [x] T071 [P] Run the full validation: `pytest`, `python -m unittest discover -s tests`, `ruff check src/ tests/`, `pytest tests/contract -q` all green; run every quickstart phase (specs/025-alpha-release-preparation/quickstart.md) and confirm each expected result
- [x] T072 Confirm the only intended output-shape changes are: the graybeard `ensure-base` first-line warning, the package-stack unreadable-payload warning, the `align-repo` verdict change for non-baseline configs, the `local`-import behavior when a same-version archive row is current (NEW-A: a `local` row is created and the archive row is superseded, not marked `verified`), and the diff `local` Release now shipping the `source/Sources` index + hashes (NEW-B) — no other observable behavior changed (contract suite + diff review)
- [x] T073 Confirm the `AGENTS.md` SPECKIT block and `tests/REGRESSION.md` reflect the completed feature (agent-context refresh + re-recorded baseline) and update `AGENTS.md` via `/speckit.agent-context.update` after tasks complete

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately (baseline verification)
- **Foundational (Phase 2)**: Depends on Setup — creates the shared homes (`fs.py`,
  `deps_satisfied`, `tests/fixtures/db.py`) that US2/US3/US5/US8 consume
- **User Stories (Phase 3+)**: Each P1 story is independently implementable/testable after
  Foundational; P2 (US7/US8/US9) and P3 (US10) layer on top; the release checks (US9)
  require Phases 3–8 complete
- **Polish (Phase 13)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: after Foundational (T002/T003/T004 not needed — packaging-only) — no story deps
- **US2 (P1)**: T002 (fs.py) for its T012 re-point — no story deps
- **US3 (P1)**: T004 (fixtures/db.py) for shared scaffolding — no story deps
- **US4 (P1)**: no deps beyond Foundational
- **US5 (P1)**: T002 (fs.py) for its T024 re-point — no story deps
- **US6 (P1)**: no deps beyond Foundational
- **US7 (P2)**: no code deps — docs/manifest sweep, can run alongside any P1 story
- **US8 (P2)**: depends on T002 (fs.py), T003 (deps_satisfied), T004 (fixtures/db.py)
- **US9 (P2)**: depends on all P1 fixes + US8 (the full fixed tree)
- **US10 (P3)**: depends on Phases 1–2 behavior being implemented (tutorials must match the
  shipped tool)

### Within Each User Story

- Tests (where present) MUST be written and FAIL before implementation
- Shared homes before re-pointing; predicate/helper creation before consumers
- Story complete before moving to the next priority

### Parallel Opportunities

- All Setup/Foundational [P] tasks run in parallel
- All P1 stories (US1…US6) can proceed in parallel once Foundational is done (different
  files; the handful of fs.py re-points in US2/US5 depend only on T002, which is in
  Foundational)
- All [P]-marked test tasks within a story run in parallel
- US7 (docs/manifest) runs alongside any P1 story; US8's [P] items (package-stack test,
  predicate test, index-loader test, config test) run in parallel

---

## Parallel Example: Foundational + P1 Stories

```bash
# Launch the shared homes together (Phase 2):
Task: "T002 Create src/package_mgmt/lib/util/fs.py"
Task: "T003 Add deps_satisfied to src/package_mgmt/lib/model/dependency.py"
Task: "T004 Create tests/fixtures/db.py"

# Once Phase 2 is done, launch P1 stories in parallel (different files):
Task: "T006 Move pycdlib into [project] dependencies in pyproject.toml"        # US1
Task: "T011 Exclude component='local' in _group_current in downloader.py"      # US2
Task: "T015 Extract supersede_current_binary in resolver.py"                   # US3
Task: "T018 Derive align-repo baseline from config.sources[0] in alignment.py" # US4
Task: "T022 Relocate handoff_gnupg_dir in paths.py"                            # US5
Task: "T029 Add graybeard warning helper in config_loader.py"                  # US6

# Launch the US8 test tasks together:
Task: "T041 package-stack unreadable-payload warning test"
Task: "T051 shared deps_satisfied predicate test"
Task: "T057 index_loader fetch_fn forwarding test"
Task: "T059 relative path override rejection test"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (baseline green)
2. Complete Phase 2: Foundational (shared homes — unblocks the fs re-points)
3. Complete Phase 3: User Story 1 (`pycdlib` runtime dep + declarative check + install claim)
4. **STOP and VALIDATE**: fresh-venv no-extras `pip install .` + `--help` succeeds
5. Continue with P1 stories US2→US6, each independently validated

### Incremental Delivery

1. Setup + Foundational → shared homes in place, suite still green
2. US1 → `pip install .` works (MVP!) → validate → next
3. US2 → `download` clean after local imports → validate
4. US3 → local supersede correct → validate
5. US4 → config-driven baseline → validate
6. US5 → public-key-only handoff → validate
7. US6 → graybeard warning → validate
8. US7 → paper-trail complete → validate
9. US8 → consolidation + craft, suite green → validate
10. US9 → baseline re-record + wheel + physical proofs → validate
11. US10 → tutorials + `0.1.0-alpha` → validate (exit gate)

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done, P1 stories (US1–US6) split across developers
3. US7 (docs) runs alongside; US8 consolidation after the shared homes; US9/US10 last

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to the user story for traceability
- Each user story is independently completable and testable; tests written first (TDD)
- Commit after each task or logical group
- Stop at any checkpoint to validate the story independently
- Avoid: vague tasks, same-file conflicts, cross-story dependencies that break independence
- Schema freeze (spec 010/011): no `database.py` changes, no new tables/columns/migrations —
  R-3 reuses the existing `status='superseded'` value; F9 consolidates test fixtures only
- The only dependency change is `pycdlib` into runtime `dependencies` (R-1); no new CLI
  commands; the only output-shape changes are the graybeard warning, the package-stack
  warning, and the align-repo verdict on non-baseline configs
- Out of scope (per user instruction): L-1…L-12 / R-7 backlog and the residual N-2b note
- Cache handoff flow: admin builds → verify → ship USB → operator verifies → installs
