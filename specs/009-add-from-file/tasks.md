# Tasks: Add from File

**Input**: Design documents from `/specs/009-add-from-file/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Unit tests are added alongside each story (project convention); the suite runs via `PYTHONPATH=src python3 -m unittest discover -s tests` (system python3-debian).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

**Validation asset**: The scratch archive fixture (`tests/fixtures/make_test_iso.py` + `write_archive`) from spec 008 is reused for integration scenarios; no real archive needed.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Include exact file paths in descriptions

## Path Conventions

- **CLI tool**: `src/package_mgmt/` at repository root
- **Batch engine**: `PackageResolver.resolve_batch` in `src/package_mgmt/lib/service/resolver.py` (spec 008 Phase 9 — reused unchanged)
- **Batch boundary type**: `PackageRequest` / new `PackageListResult` in `src/package_mgmt/lib/model/resolution.py`

---

## Phase 1: Foundational (Blocking Prerequisites)

**Purpose**: Typed manifest parsing + all-or-nothing validation — blocking for both user stories (per research §1/§2)

- [X] T001 [P] Add the frozen `PackageListResult` dataclass (`requests: tuple[PackageRequest, ...]`, `errors: tuple[str, ...]`) to `src/package_mgmt/lib/model/resolution.py` — the typed parse result per `data-model.md` (valid requests in file order + one message per malformed line, each naming the file and line number)
- [X] T002 Implement `parse_package_list(path: str) -> PackageListResult` in `src/package_mgmt/cli/commands/add.py` — read the UTF-8 file; one package per line (`name` or `name=version` via the existing `_parse_pkg_spec`); trim surrounding whitespace; skip blank lines and lines starting with `#`; collect EVERY malformed line as `Malformed line N in <path>: '<line>'`; an unreadable/missing file raises (caller maps to the contract error); depends on T001
- [X] T003 Add unit tests in `tests/unit/test_add_file.py` (depends on T001 + T002) — valid lines become `PackageRequest`s in order; pins via `=`; trim/blank/`#` handling; multiple malformed lines ALL reported with file + line numbers; unreadable file raises; empty/all-comment file yields zero requests

**Checkpoint**: Manifest parsing and validation complete and unit-tested. Foundation ready — user story implementation can begin.

---

## Phase 2: User Story 1 - One File, One Run (Priority: P1) 🎯 MVP

**Goal**: `add --file <path>` resolves every listed package in a single batch invocation with identical semantics to positional `add` (metadata only, `verified` flags unset, resolution per app settings) and batch-style reporting (progress + summary, errors capped at 50 on the console, full list in the log).

**Independent Test**: A file with N names (some pinned) hydrates the database with exactly the same records as positional `add` on the same names; every record `verified = 0`; the run prints `Resolved X/Y` progress and the summary block per `contracts/cli.md`.

### Implementation for User Story 1

- [X] T004 [P] [US1] Add the `--file` option (`action="append"`) to the `add` parser in `src/package_mgmt/cli/main.py`; the positional `package` argument becomes optional (`nargs="*"`); dispatch passes `args.files` to `handle_add`
- [X] T005 [US1] Implement the file mode of `handle_add(packages, source=None, files=None)` in `src/package_mgmt/cli/commands/add.py` — reject mixed input (both files and positionals → `_print_error` + `EXIT_ERROR`, nothing processed, per contracts); parse all files via `parse_package_list` (T002); resolve with ONE `resolve_batch(requests, source=...)` call using the `\rResolved X/Y` progress callback (pattern from `ensure_base.py`); print the batch summary block (`Packages requested`, `Packages resolved`, `Errors:` list capped at 50 via `CONSOLE_ERROR_CAP`, full list to `logger.warning`, summary also logged); **positional-only mode output stays byte-identical**; `verified` flags never touched (depends on T002)
- [X] T006 [US1] Add integration test in `tests/integration/test_add_file_flow.py` (depends on T004 + T005) — end-to-end `add --file` against a scratch cache (init → write_archive fixture → file with 6 names + 2 pins): DB tables (binaries/packages/sources/binary_to_source) identical to a positional `add` on the same names in a parallel cache; `verified = 1` count 0; output contains `Resolved 6/6`, the summary fields, and `Errors:` (quickstart §1/§2)
- [X] T007 [P] [US1] Extend the CLI contract test in `tests/contract/test_cli.py` (anytime after T005) — file-mode summary block matches `contracts/cli.md` (progress line, `Packages requested`/`Packages resolved`, `Errors: N` list, and the 50-cap wording `(showing first 50; N more in the log)` with 61 constructed errors); mixed-input error JSON

**Checkpoint**: One-command manifest hydration works end-to-end with batch reporting (MVP).

---

## Phase 3: User Story 2 - File Quality: Clear Errors, No Partial Runs (Priority: P2)

**Goal**: Missing, malformed, or empty file input produces clear errors (naming the file and line) with a non-zero exit and zero database changes; fixed files re-run idempotently.

**Independent Test**: Run `add --file` with a missing file, a file with a malformed line, and an empty file — each errors naming the file (and line), exits non-zero, and leaves the database untouched, including the valid lines of a partially-bad file; re-running a fixed file succeeds with no duplicates.

### Implementation for User Story 2

- [X] T008 [US2] Wire the CLI-level input error paths in `src/package_mgmt/cli/commands/add.py` (depends on T002) — unreadable file → `{"error": "Cannot read package list <path>: ..."}`; any malformed line → all errors joined and reported (each naming file + line) with nothing processed; zero names → `{"error": "No package names found in <path>"}`; all via `_print_error` + `EXIT_ERROR`, before any resolution or database write
- [X] T009 [US2] Add integration test in `tests/integration/test_add_file_flow.py` (depends on T008) — missing file, malformed line (valid lines in the same file NOT processed), empty file → error, exit `EXIT_ERROR`, database untouched (binaries count 0)
- [X] T010 [US2] Add integration test in `tests/integration/test_add_file_flow.py` — idempotent re-run: `add --file` twice → identical table counts/contents (no duplicate rows, quickstart §8)
- [X] T011 [P] [US2] Extend the contract test in `tests/contract/test_cli.py` (anytime after T008) — error JSON payloads per `contracts/cli.md`: malformed line naming file+line, `Cannot read package list`, `No package names found`, mixed-input message, each with `exit_code: 1`

**Checkpoint**: Damaged or empty manifests are diagnosed atomically; re-runs converge.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, contracts verification, quickstart validation, suite green

- [X] T012 [P] Update `docs/001-quickstart-howto.md` with the `add --file` usage (file format, mutually exclusive with positional names, batch reporting) per Constitution VIII
- [X] T013 [P] Verify the CLI contract examples in `contracts/cli.md` — run the file-mode summary and each error case against real output; fix discrepancies
- [X] T014 Run quickstart.md validation — scenarios 1–9 against a scratch cache (parity, batch output, mixed-input/malformed/missing/empty errors, comments/duplicates/pins, idempotency, positional mode unchanged); record results
- [X] T015 Run the full suite — `PYTHONPATH=src python3 -m unittest discover -s tests` green (unit + integration + contract); fix any regressions

**Checkpoint**: Feature documented, contracts verified, quickstart validated, suite green.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 1)**: No dependencies — can start immediately (BLOCKS all user stories)
- **User Story 1 (Phase 2)**: Depends on Foundational completion (parser + validation)
- **User Story 2 (Phase 3)**: Depends on Foundational completion and US1's file mode
- **Polish (Phase 4)**: Depends on both user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Phase 1 — the MVP slice (valid manifests resolve with batch reporting)
- **User Story 2 (P2)**: Depends on US1 (same `handle_add` code path) — robustness/error surfaces

### Within Each User Story

- Unit tests accompany implementation (project convention, not strict TDD)
- Models before services; parser before CLI wiring
- Integration tests after implementation; contract tests parallel-safe

### Parallel Opportunities

- Foundational: T001 [P] parallel with T002 (different files; T002 imports the type from T001 — coordinate the dataclass shape first); T003 follows T001 + T002
- US1: T004 [P] (main.py) parallel with T005 (add.py); T006 follows T004 + T005; T007 [P] (contract) anytime after T005
- US2: T008 → T009/T010 (same test file, sequential); T011 [P] anytime after T008
- Polish: T012/T013 [P] parallel; T014 follows T012–T013; T015 last

## Parallel Example: Foundational Phase

```bash
# Launch in parallel (coordinate the PackageListResult shape first):
Task: "T001 PackageListResult dataclass (src/package_mgmt/lib/model/resolution.py)"
Task: "T002 parse_package_list (src/package_mgmt/cli/commands/add.py)"
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1: Foundational — parser + all-or-nothing validation (CRITICAL — blocks both stories)
2. Phase 2: User Story 1 — `--file` wiring, batch resolution, batch reporting
3. **STOP and VALIDATE**: run quickstart §1/§2 against a scratch cache; demo manifest hydration
4. Then US2 — file-quality error surfaces and idempotency

### Incremental Delivery

1. Foundational → parser ready, unit-tested
2. US1 → test independently → MVP demo (manifest → hydrated DB with batch output)
3. US2 → test independently → robust error handling live
4. Polish → docs, contracts verification, quickstart validation, suite green

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to the spec's user stories for traceability
- The batch engine (`resolve_batch`) and `PackageRequest` are reused UNCHANGED from spec 008 Phase 9 — no resolver/model/schema changes (plan.md Constraints)
- Positional-only `add` output must stay byte-identical (contracts/cli.md §Reporting modes; existing 006/007 contract tests are the regression net)
- The parser lives in `cli/commands/add.py` next to `_parse_pkg_spec` (research §5); `PackageListResult` is a model type (Constitution VI)
- Commit after each task or logical group; stop at checkpoints to validate independently
