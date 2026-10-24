# Tasks: Ensure Base Packages

**Input**: Design documents from `/specs/008-ensure-base-packages/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Unit tests are added alongside each story (project convention); the suite runs via `PYTHONPATH=src python3 -m unittest discover -s tests` (system python3-debian + python3-pycdlib).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

**Validation asset**: The real offline DVD installer image is available at `/var/cache/package_mgmt/iso/debian-13.6.0-amd64-DVD-1.iso` (Debian 13.6.0 trixie, 3.99 GB, SHA256 in `/var/cache/package_mgmt/iso/sha256sum`). Use it for real-image validation (Polish phase).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **CLI tool**: `src/package_mgmt/` at repository root
- **Cache directory**: admin-managed package cache at `/var/cache/package_mgmt` (binary/, source/, iso/, logs/, package_mgmt.db)
- **Metadata**: `package_mgmt.db` for local index (SQLite)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Runtime dependency + `iso_dir` config plumbing that MUST precede user story work

- [X] T001 Verify the system dependency `python3-pycdlib` imports and opens the real DVD image (`python3 -c "from pycdlib import PyCdlib; ...open('/var/cache/package_mgmt/iso/debian-13.6.0-amd64-DVD-1.iso')"` — expect `has_rock_ridge() == True`); record the dependency in `docs/003-ensure-base.md` prerequisites (Constitution VIII)
- [X] T002 Add `iso_dir: str = "{base_dir}/iso"` field to `ProjectConfig` in `src/package_mgmt/lib/model/config.py`
- [X] T003 Add `(("paths", "iso_dir"))` to `_KEY_MAP` and `"iso_dir"` to the `expand_path` loop in `src/package_mgmt/lib/service/config_loader.py`
- [X] T004 Add `iso_dir = "{base_dir}/iso"` under `[paths]` in `project.defaults.toml`
- [X] T005 Create the (empty) `iso_dir` in `initialize_caches()` in `src/package_mgmt/lib/service/initializer.py`
- [X] T006 [P] Add `iso_dir` to text and JSON output of `handle_config()` in `src/package_mgmt/cli/commands/config.py`; extend the config contract test in `tests/contract/test_cli.py` (per `contracts/cli.md`)

**Checkpoint**: `iso_dir` is configurable, created by `init`, visible in `config`; pycdlib verified.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: ISO reading layer + synthetic-image test fixture — blocking for all user stories

- [X] T007 Add `IsoError(PackageMgmtError)` to `src/package_mgmt/lib/util/errors.py`
- [X] T008 Implement `IsoImage` in `src/package_mgmt/lib/service/iso_reader.py` (pycdlib-backed; **NOTE: Debian package API is `PyCdlib` (not `IsoFile`), all methods are kwargs-only, and `open_file_from_iso(...).read()` is broken in 1.12.0+ds1 — use `get_file_from_iso_fp(fp, rr_path)` instead**):
  - `open(path)` — `PyCdlib().open()`; `has_rock_ridge()` must be True, else `IsoError`
  - `release` / `components` — facade `list_children(rr_path='/dists')`, SKIP symlinks (`e.rock_ridge.is_symlink`; descending into them raises `PyCdlibInvalidInput`), use top-level `Release` `Codename` when present
  - `read_file(rr_path) -> bytes` — via `get_file_from_iso_fp`
  - `file_paths() -> set[str]` — facade `walk(rr_path='/')` collecting file paths (RR names); each `e.rock_ridge.name()` returns `bytes`
  - `close()` — always closes in a `finally`
  - Raises `IsoError` for unreadable/non-ISO files and missing `dists/` or `pool/` (spec edge case: no `dists/` or no `pool/` → clear error)
- [X] T009 [P] Create `tests/fixtures/make_test_iso.py` — synthetic Rock Ridge ISO builder using `PyCdlib.new()` + `add_facade(RockRidgeFacade)` + `add_directory`/`add_fp` + `write`; options: `--omit <rr_path>` (drop one pool file), `--minimal` (few packages, below threshold), `--no-pool` (no `pool/` tree), `--corrupt-index` (garbage bytes as `Packages.gz`); builds a mini `dists/trixie/main/binary-amd64/Packages.gz` (+ a small `contrib` component) and matching pool `.deb` placeholders
- [X] T010 Add unit tests in `tests/unit/test_iso_reader.py` (depends on T008 + T009) — open/list/read/walk against a fixture image; `file_exists` on real vs omitted path; non-ISO file raises `IsoError`; `--no-pool` fixture raises `IsoError`; `close()` in `finally`

**Checkpoint**: IsoImage reads any Rock Ridge installer image; fixtures buildable. Foundation ready — user story implementation can begin.

---

## Phase 3: User Story 1 - One-Command Ingestion (Priority: P1) 🎯 MVP

**Goal**: `package_mgmt ensure-base` registers every binary from the image's index into the database (`verified = 0`), with provenance and a summary.

**Independent Test**: Place a fixture image in `iso_dir`, run `ensure-base` — DB contains binary/packages/provides records for every indexed package with image provenance and `verified = 0`; summary printed; no resolution performed yet.

### Implementation for User Story 1

- [X] T011 [P] [US1] Register the `ensure-base` subcommand in `build_parser()` and dispatch in `main()` in `src/package_mgmt/cli/main.py`
- [X] T012 [US1] Implement image discovery in `src/package_mgmt/cli/commands/ensure_base.py` — scan `config.iso_dir` for `*.iso` files only (ignore README/sha256sum files); zero → error `No installer image found in <iso_dir>`; more than one → error naming ALL files (FR-010); exactly one → proceed; exit `EXIT_ERROR` without touching the DB
- [X] T013 [US1] Implement Phase A part 1 in `src/package_mgmt/lib/service/ensure_base.py` — discover release + components from the image (skip `dists/stable` symlink; `binary-amd64` index dirs only), extract each component's `Packages.gz` to a temp file under the cache via the facade's `get_file_from_iso(local_path, rr_path)` (positional signature, pycdlib 1.12.0), then load with the existing `load_binary_index(path, release, component)` in `src/package_mgmt/lib/service/index_loader.py`; a corrupt index must raise a clear error (no partial writes — the Phase A transaction rolls back)
- [X] T014 [US1] Implement Phase A part 2 in `src/package_mgmt/lib/service/ensure_base.py` — register every binary with the existing `upsert_binary` (dependencies serialized via `DependencyGraph.parse_depends_line` + `serialize_depends`), register `packages` rows via `upsert_package` (requested_version = image version), populate `provides` via the existing `populate_provides_table`; ALL records `verified = 0` (FR-005); single transaction, committed only after full Phase A success
- [X] T015 [US1] Implement the netinst/minimal warning in `src/package_mgmt/lib/service/ensure_base.py` — total index entries across components below `ISO_MIN_FULL_DVD_BINARIES = 1000` (documented constant; measured: netinst ~494–520, DVD-1 ~4,400+) → warning per FR-011; processing continues
- [X] T016 [US1] Add the typed `EnsureBaseStats` frozen dataclass (fields per `data-model.md`) and the summary printer (FR-013: image line, binary/provides counts, mismatches, resolution results, warnings, errors) in `src/package_mgmt/lib/service/ensure_base.py`
- [X] T017 [US1] Wire error handling and exit codes in `src/package_mgmt/cli/commands/ensure_base.py` — `_print_error()` JSON-to-stderr for all user-facing errors; `IsoError`/`DatabaseError`/`ConfigError` → mapped exit codes (`EXIT_ERROR`=1, `EXIT_CONFIG_ERROR`=102); log the summary block (operation boundary) per Constitution VIII
- [X] T018 [US1] Add unit tests in `tests/unit/test_ensure_base.py` — fixture image ingestion creates binaries/packages/provides rows with image provenance and `verified = 0`; `--minimal` fixture triggers the netinst warning; zero/multiple image files produce the contracted errors; `--corrupt-index` fixture produces a clear error and leaves the DB untouched (no partial writes, spec edge case)
- [X] T019 [US1] Add integration test in `tests/integration/test_ensure_base_flow.py` — end-to-end `ensure-base` against a fixture image in a scratch cache (init → ensure-base → sqlite queries per quickstart §1)
- [X] T020 [P] [US1] Extend the CLI contract test in `tests/contract/test_cli.py` — `ensure-base` summary output matches `contracts/cli.md` (text + exit codes)

**Checkpoint**: One-command ingestion works end-to-end on fixture images (MVP).

---

## Phase 4: User Story 2 - Registration Integrity and Same-as-`add` Verified Flags (Priority: P1)

**Goal**: index↔image cross-check warns on mismatches; `verified` flags are never mutated by the command.

**Independent Test**: A fixture image with `--omit` produces the mismatch warning naming the path; every registered record stays `verified = 0` (same as `add`).

### Implementation for User Story 2

- [X] T021 [US2] Implement the cross-check in `src/package_mgmt/lib/service/ensure_base.py` — collect index `Filename` paths from all components, compare against `IsoImage.file_paths()` (one facade walk), report each mismatch as a warning naming the path (FR-005); mismatches MUST NOT change any flag and MUST NOT fail the run
- [X] T022 [US2] Add unit tests in `tests/unit/test_ensure_base.py` — fixture with `--omit`: mismatch warning emitted, record still registered with `verified = 0`, run succeeds; complete fixture: no mismatch warnings
- [X] T023 [US2] Add integration coverage in `tests/integration/test_ensure_base_flow.py` — `--omit` fixture end-to-end; assert all `verified = 0` after the run (quickstart §2)
- [X] T024 [US2] Add contract coverage in `tests/contract/test_cli.py` — `Index/image mismatches: 1` output block per `contracts/cli.md`

**Checkpoint**: Damaged images are diagnosed at ingestion; flag semantics match `add` exactly.

---

## Phase 5: User Story 3 - Behaves the Same as `add` for the Base Set (Priority: P2)

**Goal**: dependencies, build-dependencies, and sources resolve exactly as `add` (per app settings) with progress and capped error reporting.

**Independent Test**: After ingestion, the DB contains resolved dependency trees, sources, and `binary_to_source` links matching a plain `add` on the same names; progress and capped errors per FR-014.

### Implementation for User Story 3

- [X] T025 [US3] Implement Phase B in `src/package_mgmt/lib/service/ensure_base.py` — for each unique binary package name from the image (sorted, deterministic): `PackageResolver(config, conn).resolve(name)`; respect `config.fetch_source` and archive access per app settings; do NOT mutate `verified` flags
- [X] T026 [US3] Implement progress display in `src/package_mgmt/cli/commands/ensure_base.py` — single in-place line `Resolved X/Y` updated per package (FR-014)
- [X] T027 [US3] Implement error aggregation + cap in `src/package_mgmt/cli/commands/ensure_base.py` — errors listed after the batch, capped at 50 on the console with the FULL list written to the log (FR-014); exit `EXIT_ERROR` when errors exist (same rule as `add`)
- [X] T028 [US3] Add unit tests in `tests/unit/test_ensure_base.py` — resolution runs per name (synthetic archive index): deps/build-deps/sources/links created; error cap (61 errors → 50 shown + log note); `verified` flags remain 0
- [X] T029 [US3] Add integration test in `tests/integration/test_ensure_base_flow.py` — `ensure-base` results on fixture names equal `add` results on the same names (binary/source/link counts)

**Checkpoint**: `ensure-base` = `add` for the base set, with batch-appropriate output.

---

## Phase 6: User Story 4 - Idempotent Re-runs and Safe Failure (Priority: P2)

**Goal**: re-runs converge to identical state; interrupted/failed runs never corrupt data.

**Independent Test**: Run `ensure-base` twice — identical table counts/contents; interrupt mid-run, re-run — completes without duplicates.

### Implementation for User Story 4

- [X] T030 [US4] Harden temp-file lifecycle in `src/package_mgmt/lib/service/ensure_base.py` — temp files under the cache, cleaned in a `finally` (also on `KeyboardInterrupt`); Phase A single transaction, Phase B per-name commits (existing `add` behavior); no writes before Phase A succeeds
- [X] T031 [US4] Add unit test in `tests/unit/test_ensure_base.py` — run the workflow twice against the same DB → identical row counts and contents for all affected tables (quickstart §4)
- [X] T032 [US4] Add integration test in `tests/integration/test_ensure_base_flow.py` — simulate an interrupted run (kill the process mid-Phase B), re-run → converges with no duplicates; image file removed between runs → clear error, DB untouched

**Checkpoint**: Re-runs and interrupted runs are safe.

---

## Phase 7: User Story 5 - Image Records Work with Existing Tooling (Priority: P2)

**Goal**: `list`, `show`, `verify --graph`, and `add` treat image records exactly like any other record — with zero changes to those commands.

**Independent Test**: After `ensure-base`, run `list`/`show`/`verify --graph`/`add` against image records — output identical in format to add-fetched records.

### Implementation for User Story 5

- [X] T033 [US5] Add integration test in `tests/integration/test_ensure_base_flow.py` — `list` and `show <pkg>` on an image record produce the same field/format output as on an add-fetched record
- [X] T034 [US5] Add integration test — `verify --graph` counts and checks image records with the same rules (current-tree only); `add <dep>` whose dependency resolves to an image record satisfies it without duplication (quickstart §5)
- [X] T035 [P] [US5] Add contract test in `tests/contract/test_cli.py` — `list`/`show` output for image records matches the existing 003/007 contract examples

**Checkpoint**: Image records are indistinguishable from add-fetched records to existing tooling.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, contracts verification, real-image validation, suite green

- [X] T036 Write `docs/003-ensure-base.md` — howto (prereqs incl. `apt install python3-pycdlib`, workflow, verified-flag semantics, admin-only note) per Constitution VIII
- [X] T037 [P] Update `.specify/memory/roles.md` — add `ensure-base` to the administrator's command table (admin-only, like `add`; operator never runs it)
- [X] T038 Verify the CLI contract examples in `contracts/cli.md` — run the text/JSON `config` output (iso_dir present) and `ensure-base` summary/mismatch/error blocks against real output; fix discrepancies
- [X] T039 [P] Run quickstart.md validation — scenarios 1–7 against fixture images; then **scenario 8 with the real DVD image** at `/var/cache/package_mgmt/iso/debian-13.6.0-amd64-DVD-1.iso` (expect ~4,400+ binary packages, no netinst warning, `Resolved X/Y` progress, errors capped at 50); record results
- [X] T040 Run the full suite — `PYTHONPATH=src python3 -m unittest discover -s tests` green (unit + integration + contract); fix any regressions

**Checkpoint**: Feature documented, contracts verified, real-DVD validation recorded, suite green.

---

## Phase 9: Batch-First Resolution Refactor

**Purpose**: Make the batch the first-class resolution shape (user directive). Real-DVD validation (T039) measured the per-name design at ~1.5 s/name with `fetch.source = true` (~2 h for the 4,447-name batch): `_resolve_build_deps` re-scans all ~37,806 sources on every name, the runtime tree is re-walked per name (~44 M package visits), and `binary_to_source` re-links accumulate (~45 M rows, no UNIQUE constraint). Batch processing does NOT mean parallel — each main operation runs for the whole batch before the next operation starts, and dependencies are walked **once per batch invocation**. No user-facing behavior changes: single-name `add` is a batch of one; `add` multi-name and `ensure-base` Phase B share one code path. No spec.md FR changes (internal refactor).

**Independent Test**: `resolve_batch([PackageRequest("a"), PackageRequest("b")])` final DB state == sequential `resolve("a")` + `resolve("b")` for the same names, with each dependency visited once (instrumented walk); real-DVD `ensure-base` with `fetch.source = true` completes the full 4,447-name batch in ≤ 15 minutes (measured ~2 h before).

### Implementation for the Batch Refactor

- [X] T041 [P] Add the link-table hygiene fix — the `binary_to_source` table gains `UNIQUE(binary_id, source_id)` **directly in the schema** (`src/package_mgmt/lib/model/database.py`); no additive DDL on open and no version bump (KISS — a dev DB with the old shape is rebuilt from scratch); `link_binary_to_source` (`INSERT OR IGNORE`) becomes a true no-op on re-link; the now-dead `_dedupe_binary_to_source` in `ensure_base.py` is **removed** with its Phase C call. **Note**: the pair `(binary_id, source_id)` already disambiguates provenance (main vs backports) — `binaries`/`sources` are each UNIQUE per `(package_name, version, architecture, release, component)`, so different releases map to different ids; no release/component columns on the link table are needed
- [X] T042 Implement `resolve_batch(requests: Iterable[PackageRequest], source: str | None = None, progress: Callable[[int, int], None] | None = None)` in `src/package_mgmt/lib/service/resolver.py` — the batch core:
  - Add the frozen `PackageRequest` dataclass (`name: str`, `requested_version: str | None = None`) to `src/package_mgmt/lib/model/resolution.py` — a name and its requested version travel together (no flat `names` list; pins like `vim=1.2.3` are first-class), per Constitution VI. **Boundary-only type**: consumed at the public entry points (`resolve_batch`, `resolve`); internal walk helpers (`_resolve_one`, `_resolve_build_deps`, …) keep their existing plain-argument signatures unchanged and are called per (name, requested_version) during the walk
  - **Runtime pass (walk once)**: shared `_visited`/`_ancestry` across all names; each package is registered once; per-package incoming constraint collection — select a version satisfying **all** accumulated constraints (supersede + re-walk the subtree when a later constraint invalidates an earlier selection); source links created once per (binary, source) pair at visit time (idempotent under T041)
  - **Per-name commits** as safety checkpoints (US4: interrupted runs converge; progress preserved)
  - **Progress callback** `(done, total)` per name; **per-name error attribution** preserved (`name: error`); per-name `ResolutionStats` for `add`'s per-name output lines
  - **Build-dep pass**: the existing wave logic (`_resolve_build_deps`) runs **once per batch**, not per name
  - Existing `resolve(name, requested_version=None, source=None)` delegates to `resolve_batch([PackageRequest(name, requested_version)], source)` — single code path, `add` single-name behavior bit-identical (output lines, stats, exit codes)
- [X] T043 Wire `add` in `src/package_mgmt/cli/commands/add.py` — `_parse_pkg_spec` builds a `PackageRequest` list (name + optional `=version` pin) and the per-name `resolve()` loop becomes one `resolve_batch(requests)` call; keep the per-name "Package X resolved from ..." lines, per-name error listing, totals aggregation, and `EXIT_ERROR` rule unchanged
- [X] T044 Wire `ensure-base` Phase B in `src/package_mgmt/lib/service/ensure_base.py` — `_resolve_base_packages` calls `resolve_batch([PackageRequest(n) for n in names], progress=...)`; per-name progress line (`Resolved X/Y`), error aggregation + 50-console-cap, and summary counts unchanged; `verified` flags never mutated
- [X] T045 Add unit tests in `tests/unit/test_resolver_batch.py` (+ extend `tests/unit/test_ensure_base.py`) — walk-once guarantee (shared dep visited once, via instrumented counter); constraint collection: two names with conflicting constraints on one dep → single version satisfying both, else supersede + subtree re-walked; `resolve(name)` ≡ `resolve_batch([PackageRequest(name)])` (identical final state + stats); batch vs sequential `add` on the same names (identical final state); per-name error attribution; interrupt mid-batch → re-run converges (extend US4 coverage to the batch path); link idempotency under the new index (re-runs add zero rows)
- [X] T046 Add integration + performance validation — `tests/integration/test_ensure_base_flow.py` and `tests/contract/test_cli.py` stay green (T029/T033/T034 parity tests are the batch refactor's regression net); re-run quickstart scenario 8 on the real DVD with `fetch.source = true`: full 4,447-name batch ≤ 15 min (target; record measured time in `specs/008-ensure-base-packages/validation.md`, replacing the ~2 h projection)
- [X] T047 [P] Docs-in-cycle per Constitution VIII — update `docs/003-ensure-base.md` (batch resolution note; drop the per-name re-walk caveat) and re-record the real-DVD numbers in `specs/008-ensure-base-packages/validation.md` (replace the ~2 h projection with the measured batch time); `research.md` §6 and `plan.md` Performance Goals are already amended with this task addition

**Checkpoint**: Batch is the core resolution shape; deps walked once per invocation; real-DVD `source=true` batch ≤ 15 min; full suite green with parity tests proving batch ≡ sequential semantics.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational completion; then in priority order
- **Polish (Phase 8)**: Depends on all user stories being complete
- **Batch Refactor (Phase 9)**: Depends on all phases 1–8 (refactors the resolution core they produced); no user-visible FR changes

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Phase 2 — no story dependencies (MVP)
- **User Story 2 (P1)**: Depends on US1 (shares `ensure_base.py` Phase A) — small, sequential follow-up
- **User Story 3 (P2)**: Depends on US1 (adds Phase B to the same workflow)
- **User Story 4 (P2)**: Depends on US3 (temp lifecycle spans Phase B)
- **User Story 5 (P2)**: Depends on US1 (records must exist); verifies existing commands only

### Within Each User Story

- Unit tests accompany implementation (project convention, not strict TDD)
- Models/config before services; services before CLI wiring
- Integration tests after unit tests; contract tests parallel-safe

### Parallel Opportunities

- T001–T006: T006 [P] parallel with T002–T005
- Foundational: T009 [P] parallel with T007–T008; T010 follows T008 + T009
- US1: T011 [P] (main.py) parallel with T013–T016; T020 [P] (contract) anytime after T012
- US2/US4: T022–T024 build on US1 tests; T031/T032 can run after US3
- US5: T035 [P] parallel with T033/T034
- Polish: T037/T039 [P] parallel with T036/T038/T040
- Batch refactor: T041 [P] (schema) parallel with T042 (resolver); T043/T044 follow T042; T045 follows T042 (+T041 for link assertions); T046 follows T042–T044; T047 [P] anytime after T042

## Parallel Example: Foundational Phase

```bash
# Launch in parallel:
Task: "T009 fixture builder (tests/fixtures/make_test_iso.py)"
Task: "T008 IsoImage reader (src/package_mgmt/lib/service/iso_reader.py)"
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1: Setup → Phase 2: Foundational (CRITICAL — blocks all stories)
2. Phase 3: User Story 1 — ingestion works end-to-end on fixture images
3. **STOP and VALIDATE**: run quickstart §1 against a fixture image; demo ingestion
4. Then US2 (verified semantics + mismatch diagnostics) — both P1 stories complete the MVP scope

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. US1 → test independently → MVP demo
3. US2 → test independently → integrity diagnostics live
4. US3 → test independently → full `add`-like resolution
5. US4 → test independently → re-run safety
6. US5 → test independently → tooling interop proven
7. Polish → docs, contracts, real-DVD validation, suite green

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to the spec's user stories for traceability
- pycdlib 1.12.0 (Debian) API notes in T008: class `PyCdlib`, kwargs-only methods, facade `rr_path`, `rock_ridge.name()` returns bytes, symlink descent raises, `open_file_from_iso().read()` broken — use `get_file_from_iso_fp(fp, rr_path)`
- Real-DVD facts (verified on `debian-13.6.0-amd64-DVD-1.iso`): opens in ~0.4s with Rock Ridge; dists = stable(symlink)+trixie; main index = 4,412 entries, contrib = 3; facade walk = 4,483 `.deb` files; netinst threshold 1,000 holds
- Commit after each task or logical group; stop at checkpoints to validate independently
- Cache handoff flow: admin runs init → ensure-base → verify → ship USB with the image in `iso_dir` → operator verifies and rebuilds
