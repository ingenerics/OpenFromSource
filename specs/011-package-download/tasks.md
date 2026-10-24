---

description: "Task list for feature 011 - Package Download"

---

# Tasks: Package Download

**Input**: Design documents from `/specs/011-package-download/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/cli.md, quickstart.md

**Tests**: Test tasks are included — the implementation plan (plan.md) specifies unit, integration, and contract tests for every story; write them first, verify they fail, then implement.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **CLI tool**: `src/package_mgmt/` (Python package; CLI entry `package_mgmt`)
- **Tests**: `tests/unit/`, `tests/integration/`, `tests/contract/` (unittest style; run with `python3 -m unittest discover -s tests`)
- **Fixture**: `tests/fixtures/make_test_iso.py` (scratch archive; extended with pool files + local HTTP server)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: No setup required — the project structure, CLI scaffolding, error-handling conventions (`_print_error` JSON to stderr, exit codes 0/1/101/102), and test layout exist from prior features. Work starts directly in the Foundational phase.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Model/loader extensions, the fixture's pool+server support, and the progress renderer — everything every user story builds on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T001 Extend `src/package_mgmt/lib/model/package.py`: add `size: str | None` to `BinaryPackage`; add a frozen `SourceFile` dataclass (`name: str`, `size: int | None`, `sha256: str | None`) for parsed source files (research Decision 1)
- [X] T002 [P] Extend `src/package_mgmt/lib/service/index_loader.py`: parse `Size` for binary entries; parse `Directory` + `Checksums-Sha256` for source entries into a `list[SourceFile]` on `SourcePackage` (replacing the raw `files` usage; keep the raw field for compatibility)
- [X] T003 [P] Extend `tests/fixtures/make_test_iso.py`: add `Size` to `_index_text` Packages entries; add `Directory` + `Files` + `Checksums-Sha256` blocks to the Sources entries (`_gzip_bytes` + `_index_text` already exist; new fields must be consistent with the pool files written by T004)
- [X] T004 [P] Extend `tests/fixtures/make_test_iso.py`: add `write_pool_files(base_dir)` (deterministic bytes at every index `Filename` path under `binary/pool/` plus source files under `binary/pool/` per `Directory`/file lists, since the mirror serves one shared pool) and a threaded HTTP server helper `serve_pool(base_dir, port)` rooted at the pool for integration tests (research Decision 5)
- [X] T005 [P] Add parsing tests in `tests/unit/test_index_loader.py`: `Size` lands on `BinaryPackage`; `Directory` + `Checksums-Sha256` parse into `SourceFile` lists; missing checksums yield `None` entries
- [X] T006 Implement the phase progress renderer in `src/package_mgmt/lib/util/progress.py`: `PhaseProgress(total_files, total_bytes)` — elapsed-time line + progress bar beneath it (files + bytes), in-place `\r` updates when stdout is a TTY, periodic one-line updates when not; `done()` clears the bar; no dependencies (research Decision 3)
- [X] T007 [P] Add unit tests in `tests/unit/test_progress.py`: bar rendering shapes, elapsed formatting, non-TTY degradation (captured output is line-per-update), zero-total handling

**Checkpoint**: Parsing + fixture pool/server + progress renderer ready — user story implementation can begin.

---

## Phase 3: User Story 1 - Fill the Pool: Download Every Unverified Binary (Priority: P1) 🎯 MVP

**Goal**: `download` downloads every pending current binary from the mirror into the pool, checksum-verifies it against the cached index, marks it verified, with live progress and a summary (spec US1, FR-001..FR-005, FR-010, FR-013, FR-014).

**Independent Test**: Hydrate a scratch cache with a small tree (`vifm` + deps) via the local HTTP mirror fixture, run `download` — every `.deb` at its index `Filename` path, every current binary verified, second run downloads nothing (spec US1; quickstart scenarios 1–5).

### Tests for User Story 1 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementing.**

- [X] T008 [P] [US1] Unit tests in `tests/unit/test_downloader.py`: pending-binary set enumeration from the DB; metadata resolution from a loaded index (missing entry → unconfirmable error, FR-002); dedupe by destination path (FR-010); temp-file → verify → `os.replace` flow and checksum-mismatch removal (FR-004); per-file verified commit (use a stubbed opener, no network)
- [X] T009 [P] [US1] Contract tests in `tests/contract/test_cli.py`: progress line shapes (elapsed line + bar, non-TTY line), binary-phase summary block, `Total:` line, exit 0 / exit 1 with failures listed (cap shape), JSON errors for unconfirmable records and missing cached index (contracts/cli.md)
- [X] T010 [P] [US1] Integration tests in `tests/integration/test_download_flow.py`: end-to-end happy path (all `.deb` present + verified, SC-001), idempotent re-run (SC-004), delete-one-file re-run, corrupt-file mismatch (SC-002), 404 file (permanent failure, others continue)

### Implementation for User Story 1

- [X] T011 [US1] Implement the download engine in `src/package_mgmt/lib/service/downloader.py`: pending-binary query (FR-001); per-(release, component) index loading + `(name, version) → (filename, size, sha256)` lookup maps (reusing `load_binary_index` from T002); URL = `mirror.rstrip("/") + "/" + filename`; download to `*.part` in the destination dir → SHA-256 verify → `os.replace` into `{binary_cache}/{Filename}`; mismatch → remove part + report expected/actual; per-file `verified = 1` commit; dedupe by destination path; collect failures without blocking other files (FR-003/FR-004/FR-005/FR-010/FR-011)
- [X] T012 [US1] Implement `src/package_mgmt/cli/commands/download.py`: `handle_download(db_path, config)` — `validate_schema` on entry (stale DB → 101), run the binary phase through the engine with `PhaseProgress` (T006), print the binary-phase summary block and `Total:` (FR-013/FR-014), return exit 0/1
- [X] T013 [US1] Register and dispatch `download` in `src/package_mgmt/cli/main.py` (no arguments; handler receives `config.db_path` and `config`), following the existing `_register_*` pattern

**Checkpoint**: User Story 1 fully functional — binary downloads verified end to end with progress and summary.

---

## Phase 4: User Story 2 - Fetch the Sources: Download Linked Source Artifacts (Priority: P2)

**Goal**: With `fetch_source` enabled, `download` fetches every file of every source linked to a current binary, verifying each against the source index, and marks the source verified only when all its files are verified (spec US2, FR-006..FR-009).

**Independent Test**: Hydrate with `fetch_source = true`, run `download` — every source file (`.dsc` + tarballs) present in the source pool, `sources.verified = 1`; a cache registered with `fetch_source = false` downloads no source files and exits 0 (spec US2 AS1/AS3).

### Tests for User Story 2 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementing.**

- [X] T014 [P] [US2] Unit tests in `tests/unit/test_downloader.py`: pending-source set (linked-to-current-binary only, FR-009); source file resolution from `Directory` + `Checksums-Sha256`; all-or-nothing source verification (one bad file → source stays unverified, FR-006/FR-007); `fetch_source = false` → no source work (FR-008)
- [X] T015 [P] [US2] Integration tests in `tests/integration/test_download_flow.py`: source phase downloads every linked source's files and sets `sources.verified = 1` (SC-003); a corrupt source file leaves the source unverified with a per-file failure; `fetch_source = false` cache downloads binaries only, exit 0

### Implementation for User Story 2

- [X] T016 [US2] Extend the download engine in `src/package_mgmt/lib/service/downloader.py`: pending-source query (FR-001/FR-009); per-(release, component) `Sources.gz` loading (T002 parsing) + `Directory`/file resolution; source files go to `{source_cache}/{Directory}/{name}`; set `sources.verified = 1` only after ALL its files verified; skip entirely when `fetch_source` is false (FR-006/FR-007/FR-008)
- [X] T017 [US2] Extend `src/package_mgmt/cli/commands/download.py`: run the source phase after the binary phase with its own `PhaseProgress` and summary block ("nothing to download" single line when the phase is empty), keep the combined `Total:` line (FR-013/FR-014)

**Checkpoint**: User Stories 1 AND 2 both work independently — binaries and sources verified under any config.

---

## Phase 5: User Story 3 - Safe Runs: Interruptions, Failures, and Re-runs (Priority: P2)

**Goal**: Mirror-polite, convergence-safe runs: exponential backoff honoring `Retry-After`, permanent-vs-transient failure classification, no partial files, readable failure reporting (spec US3, FR-011/FR-012/FR-015; SC-007).

**Independent Test**: Point the config at a rate-limited test server (429 the first N requests per path, then succeed) — the run still completes without refusal; a run against a missing index errors clearly; interrupted runs converge (spec US3 AS1-AS3; quickstart scenarios 6 and 8).

### Tests for User Story 3 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementing.**

- [X] T018 [P] [US3] Integration tests in `tests/integration/test_download_flow.py`: rate-limited server variant (first N requests per path → `429` + `Retry-After`, then success) completes with zero refusals (SC-007); interrupted-run convergence (kill mid-download leaves no `*.part` files; re-run converges to a clean state, SC-005); missing cached index → clear JSON error, nothing downloaded (FR-012, quickstart scenario 8)

### Implementation for User Story 3

- [X] T019 [US3] Add the politeness/retry layer to the download engine in `src/package_mgmt/lib/service/downloader.py`: shared opener with descriptive User-Agent (`package_mgmt/<version> (OpenFromSource offline cache builder)`); classify transient failures (`429`/`503`/connection errors → retry) vs permanent (`404` and other statuses → per-file failure); bounded retries with exponential backoff (1 s base × 2, 30 s cap, jitter, ≤ 5 attempts) honoring a server-provided `Retry-After` (FR-015, research Decision 2)
- [X] T020 [US3] Polish failure reporting in `src/package_mgmt/cli/commands/download.py`: per-phase failure lists capped at 50 on the console with the full list in the log (same convention as `add --file`/`ensure-base`); summary block also written to the log (contracts/cli.md, Constitution VIII)

**Checkpoint**: All user stories independently functional — polite, convergent, safely reportable downloads.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verification, consistency, and final validation across all stories.

- [X] T021 [P] Run the full test suite (`python3 -m unittest discover -s tests`) and `ruff check src tests` (if ruff is available; otherwise a manual style pass on changed files) — all green
- [X] T022 [P] Execute every scenario in `specs/011-package-download/quickstart.md` end to end against a scratch cache with the local HTTP mirror (happy path, idempotency, sources, delete-one-file, checksum mismatch, rate-limited server, `fetch_source = false`, missing index)
- [X] T023 [P] Documentation consistency pass: verify `data-model.md` (download set, transitions, freeze statement) and `contracts/cli.md` (progress shapes, summaries, exit codes, error JSONs) match the implemented behavior; update `AGENTS.md` SPECKIT context if the feature pointer changed

---

## Phase 7: Review Fixes (Post-Implementation Audit)

**Purpose**: Items raised by an independent review of the implemented feature (plus one
field report): unmet FR-015 connection reuse, per-run pool re-hashing, arch-blind index
lookup, silent `add` with no arguments, a CI dependency-name bug, dead code, progress
TTY handling, and duplicated phase summaries in `download` output.

- [X] T024 Fix duplicate phase-summary rows in `src/package_mgmt/cli/commands/download.py`: the summary block is printed to stdout exactly once; the log receives a concise one-line summary plus per-failure warnings (no block re-print, which duplicated every row on the console). Update the log sentence in `specs/011-package-download/contracts/cli.md`; add a contract test asserting the block appears once and the log line is the concise form
- [X] T025 Implement FR-015 connection reuse in `src/package_mgmt/lib/service/downloader.py`: replace the urllib-per-request `_default_opener` with `_PooledOpener` — one keep-alive `http.client` connection per mirror origin, dropped (evicted) on any request- or read-side error so retries start fresh; exceptions mirror urllib (`HTTPError`/`URLError`) so the retry layer is unchanged; close the opener at phase end. Unit tests in `tests/unit/test_downloader.py` with a real HTTP/1.1 server: one connection reused across files; a mid-read RST (SO_LINGER) evicts the connection and the next request uses a fresh one; end-to-end engine recovery over real HTTP after a mid-read reset
- [X] T026 Short-circuit verified-record re-checks in `src/package_mgmt/lib/service/downloader.py`: skip the O(file) re-hash when the pool file's stat size equals the index `Size` (re-hash + re-download only on mismatch/missing; a verified record is otherwise untouched). Unit tests: matching size → no read, no download, stays verified; wrong size → re-hashed, re-downloaded, re-verified
- [X] T027 Match the record's architecture in `_lookup_entry` (`src/package_mgmt/lib/service/downloader.py`): select the index entry whose `Architecture` equals the binary record's, falling back to a plain version match (source entries carry no arch); `_group_current` now selects `architecture`. Unit test: a multi-arch index (same name+version under `amd64`/`all`) resolves each record to its own `Filename`; an unknown arch falls back
- [X] T028 `add` with no arguments errors instead of exiting 0 silently: `src/package_mgmt/cli/commands/add.py` returns `{"error": "No packages specified; provide package name(s) or --file", "exit_code": 1}` like the `--file` empty-list convention; documented in `specs/009-add-from-file/contracts/cli.md` with a contract test (nothing processed)
- [X] T029 Fix the runtime dependency name and CI: `pyproject.toml` declares `python3-debian` (an apt package name, not on PyPI — forcing `pip install --no-deps` in CI), so rename the dependency to `python-debian` and drop `--no-deps` (and the apt install step) in `.github/workflows/ci.yml`
- [X] T030 Remove dead/misfired code: delete the never-called `_query_table` from `src/package_mgmt/cli/commands/show.py`; replace the phantom config read `max_build_dep_resolution_iterations` in `src/package_mgmt/lib/service/resolver.py` with a module constant `MAX_BUILD_DEP_ITERATIONS = 100` (the spec-007 safety cap)
- [X] T031 Polish: TTY-aware resolution progress — new `make_resolved_progress()` in `src/package_mgmt/lib/util/progress.py` (in-place `\rResolved X/Y` on a terminal, one clean line per update when piped/logged), wired into `add.py` and `ensure_base.py`; derive `USER_AGENT` version from package metadata (`importlib.metadata`, `0.0.0` fallback) instead of a hardcoded duplicate of `pyproject.toml`. Unit tests in `tests/unit/test_progress.py`; updated contract/integration tests that previously asserted the `\r` garbage in non-TTY output

**Checkpoint**: Review items closed — all fixes tested (325 tests green), lint clean
relative to baseline, contracts updated.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No tasks — existing structure reused
- **Foundational (Phase 2)**: T001 first (model); T002 depends on T001; T005 depends on T001+T002; T006 independent; T003/T004 parallel; T007 depends on T006 — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational (T011 needs T002/T006)
- **User Story 2 (Phase 4)**: Depends on Foundational + US1 engine (T016 extends T011)
- **User Story 3 (Phase 5)**: Depends on US1 engine (T019 extends T011); T018/T020 parallelizable with US2 work
- **Polish (Phase 6)**: Depends on all user stories complete

### User Story Dependencies

- **User Story 1 (P1)**: No dependencies beyond Foundational
- **User Story 2 (P2)**: Extends the US1 engine (source phase) — same files, sequential
- **User Story 3 (P2)**: Extends the US1 engine (retry layer) + US1 reporting — same files, sequential

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Unit tests → contract tests → integration tests → engine → command handler → CLI wiring
- Story complete before moving to the next priority

### Parallel Opportunities

- Foundational: T002 ∥ T003 ∥ T004 ∥ T006 (after T001); T005/T007 after their dependencies
- US1 tests: T008 ∥ T009 ∥ T010
- US2 tests: T014 ∥ T015; US3 tests: T018 alone (after US1 engine exists)
- Polish: T021 ∥ T022 ∥ T023
- US2 and US3 both touch `downloader.py` — sequential after US1

---

## Parallel Example: After Foundational

```bash
# Developer A — User Story 1:
Task: "T008 unit tests in tests/unit/test_downloader.py"
Task: "T009 contract tests in tests/contract/test_cli.py"
Task: "T010 integration tests in tests/integration/test_download_flow.py"
Task: "T011 engine in src/package_mgmt/lib/service/downloader.py"
Task: "T012 handler in src/package_mgmt/cli/commands/download.py"
Task: "T013 wiring in src/package_mgmt/cli/main.py"

# Developer B — User Story 3 tests (independent files):
Task: "T018 interrupted-run + rate-limited server tests in tests/integration/test_download_flow.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 2: Foundational (model/loader/fixture/progress — CRITICAL, blocks everything)
2. Complete Phase 3: User Story 1 (binary downloads) — tests first, engine, handler, CLI wiring
3. **STOP and VALIDATE**: `download` on the scratch vifm tree downloads, verifies, and summarizes; full suite green
4. Deploy/demo the binary download if ready

### Incremental Delivery

1. Foundational → binary download (US1) → **MVP checkpoint**
2. Add source phase (US2) → rebuild-capable cache under `fetch_source`
3. Add politeness + reporting polish (US3) → safe, mirror-friendly long runs
4. Polish: lint, full suite, quickstart validation

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to the spec user story for traceability
- All user-facing errors use `_print_error()` (JSON to stderr); exit codes: 0 success, 1 failure, 101 stale schema, 102 config
- No schema changes — the feature only sets `binaries.verified` / `sources.verified`
- No real downloads: every scenario runs against the local HTTP fixture (research Decision 5)
- Files are visible in the pool only when fully received and checksum-verified (`*.part` → verify → `os.replace`)
- One in-flight request per mirror; bounded backoff honoring `Retry-After`; descriptive User-Agent (FR-015)
- Commit after each task or logical group
- Stop at any checkpoint to validate the story independently
