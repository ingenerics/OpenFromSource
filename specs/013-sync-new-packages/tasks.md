---

description: "Task list for feature 013 - Sync New Packages"

---

# Tasks: Sync New Packages

**Input**: Design documents from `/specs/013-sync-new-packages/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/cli.md, quickstart.md

**Tests**: Test tasks are included — the implementation plan (plan.md) specifies unit, integration, and contract tests for every story; write them first, verify they fail, then implement (project convention, as in specs 008-012).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **CLI tool**: `src/package_mgmt/` (Python package; CLI entry `package_mgmt`)
- **Admin cache**: `{binary_cache}/` — `dists/{release}/{component}/binary-amd64/Packages.gz` (archive indexes, UNTOUCHED), `pool/` (verified files), `package_mgmt.db`, `handoff/` (baseline spec 012 artifacts — READ only by `sync`)
- **Diff (new)**: `{binary_cache}/diff/` — `apply.sh`, `offline-cache.gpg`, `manifest.txt` (one line per indexed file: `new|delivered <sha256> <size> <pool-rel-path>`; the `delivered` lines are the base set), `pool/` (only the pending files), `dists/{release}/{Release, Release.gpg, {component}/binary-amd64/Packages.gz}` (COMPLETE indexes — prior + new)
- **Operator system**: `/etc/apt/sources.list.d/openfromsource-cache.list` (UNCHANGED by the apply — keeps pointing at `file:{cache}/handoff`)
- **Tests**: `tests/unit/`, `tests/integration/`, `tests/contract/` (pytest; operator-side `apply.sh` logic is unit-tested by sourcing its functions with scratch paths — no root, no test hooks; real `apt-get` consumption runs against scratch apt dirs — see contracts/cli.md)
- **Fixture**: `tests/fixtures/` (existing scratch archive + pool; baseline via `handoff` + `systems add`)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: No setup required — the project structure, CLI scaffolding, error-handling conventions (`_print_error` JSON to stderr, exit codes 0/1/101/102), test layout, and pinned tooling (ruff `<0.12` in the dev extra) exist from prior features. The snapshot service (`pending_count`, `system_bit`, `mark_delivered`) and the handoff generator internals this feature reuses already exist. Work starts directly in the Foundational phase.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The one shared piece every user story builds on: the diff path helpers.

- [X] T001 [P] Add diff path helpers to `src/package_mgmt/lib/util/paths.py`: `diff_dir(binary_cache)`, `diff_release_dir(binary_cache, release)`, `diff_index_path(binary_cache, release, component)`, `diff_manifest_path(binary_cache)`, `diff_script_path(binary_cache)`, `diff_pool_path(binary_cache)` (all under `{binary_cache}/diff/` per data-model.md); add unit tests in `tests/unit/test_paths.py` covering the layout (apply.sh, manifest.txt, offline-cache.gpg, pool/, dists/{release}/{Release, Release.gpg, {component}/binary-amd64/Packages.gz})

**Checkpoint**: Diff path helpers ready — user story implementation can begin.

---

## Phase 3: User Story 1 - Ship Only What's New (Priority: P1) 🎯 MVP

**Goal**: The admin runs `package_mgmt sync --system <moniker>` after growing the cache, producing `{binary_cache}/diff/` — only the new, verified packages since the system's last snapshot (complete per-release indexes covering prior + new, a new-only `pool/`, `manifest.txt`, signed `Release`/`Release.gpg` with the baseline handoff's key, `offline-cache.gpg`, and `apply.sh`) — and takes the delivery snapshot marking exactly the shipped set (spec US1, FR-001/FR-003/FR-005/FR-006).

**Independent Test**: Fixture cache → `handoff --system <moniker>` (baseline snapshot) → `add`/`download` new small packages → `sync --system <moniker>` → the diff contains exactly the new packages' files and none of the already-delivered ones (verify `diff/pool/` and the manifest); a second `sync` produces `New packages: 0` and no new diff (the snapshot already advanced).

### Tests for User Story 1 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementing.**

- [X] T002 [P] [US1] Unit tests in `tests/unit/test_diff_generator.py`: pending set = current + verified with the system's `sync_mask` bit unset; per-(release, component) `Packages.gz` paragraphs cover ALL current verified binaries (delivered + new) with `Filename`/`Size`/`SHA256` from the cached index; `manifest.txt` lines `new|delivered <sha256> <size> <pool-rel-path>` — new = pending files present in `diff/pool/`, delivered = already-marked files; `diff/pool/` contains ONLY the pending files (copied from `{binary_cache}/pool/`); `Release`/`Release.gpg` valid and signed with the same key as the baseline handoff (verify with `gpgv` against `offline-cache.gpg`); the snapshot marks exactly the shipped pending ids (re-run: no double-counting)
- [X] T003 [P] [US1] Contract tests in `tests/contract/test_cli.py`: `sync --system <moniker>` stdout shape (Sync ready / Release / Components / New packages / Total offered / Signed / Apply / Snapshot lines), exit 0; missing `--system` → `{"error": "sync requires --system <moniker>", "exit_code": 1}`; unknown/retired moniker → `{"error": "Unknown system '<name>'", "exit_code": 1}` and nothing generated; zero pending → `New packages: 0` + success (contracts/cli.md)
- [X] T004 [P] [US1] Integration test in `tests/integration/test_diff_flow.py` (admin half): build the fixture cache, `systems add` a moniker, `handoff --system <m>` (baseline snapshot), register + download new small packages, then `sync --system <m>` — assert `diff/pool/` has exactly the new files, the complete `Packages.gz` covers prior + new, the manifest/base-set is correct, and the moniker's `sync_mask` bit is set for the new ids; re-run `sync` → `New packages: 0`, no new diff

### Implementation for User Story 1

- [X] T005 [US1] Implement `generate_diff(config, conn, system_bit)` in `src/package_mgmt/lib/service/handoff.py`: reuse the verified-record walk, `load_binary_index`, `lookup_entry`, `_paragraph`, `_write_gzip`, `_release_text`, and `_write_key_and_signatures` machinery from `generate_handoff` (research Decision 1 — the diff IS a partial handoff); classify each current+verified record by the system bit (delivered vs pending); emit the COMPLETE per-(release, component) `Packages.gz` (prior + new) + `Release` + `Release.gpg` signed with the same cache-local key; write `manifest.txt` (one `new|delivered <sha256> <size> <pool-rel-path>` line per indexed file) and copy `offline-cache.gpg` from the handoff; copy ONLY the pending binaries' pool files into `diff/pool/` (temp + verify + rename, no symlink — they must physically ship); excluded/unconfirmable pending records reported and never shipped; return the shipped pending ids + stats for the summary (contracts/cli.md)
- [X] T006 [US1] Emit the operator script: add the `apply.sh` template (embedded in `src/package_mgmt/lib/service/handoff.py`, written into the diff) implementing the contracts/cli.md contract — arg parsing (`[<cache-dir>]` optional, used as-is, default = diff's parent), root check, diff structure preflight, base-set check (every `delivered` path exists in the operator's pool), copy + verify new files into the pool atomically, replace the handoff `dists/` with the diff's complete ones, `apt update`, exit 0/1, plain-text errors; the flow is structured as unit-testable functions with explicit paths (no test-only hooks)
- [X] T007 [US1] Implement `src/package_mgmt/cli/commands/sync.py`: `handle_sync(db_path, config, system)` — `validate_schema` on entry (stale DB → 101), moniker validation via the snapshot service (unknown/retired → `Unknown system '<name>'`, exit 1), pending-count fast path (`0` → report `New packages: 0` + success), run the T005 `generate_diff`, `mark_delivered(conn, system, shipped_ids)`, print the summary block incl. `Snapshot: N binaries marked for '<moniker>'`; excluded/unconfirmable pending records → warning to the log + count + command error per contracts/cli.md; `_print_error` JSON convention
- [X] T008 [US1] Register and dispatch `sync` in `src/package_mgmt/cli/main.py`: `sync --system <moniker>` (required option), following the existing `_register_*` pattern

**Checkpoint**: User Story 1 fully functional — the admin produces a minimal diff with a working `apply.sh`, the snapshot advances, and a re-run ships nothing new.

---

## Phase 4: User Story 2 - Pull the Diff Into the System Cache (Priority: P1)

**Goal**: The operator runs `{cache}/diff/apply.sh [<cache-dir>]` against the existing bootstrapped cache; the new packages merge in, `apt update` (offline) lists prior + new, installing a new package works from the cache, and nothing previously installed or offered is disturbed (spec US2, FR-007/FR-008/FR-009/FR-010).

**Independent Test**: Bootstrap a scratch cache (baseline handoff applied, sources entry pointing at `file:{cache}/handoff`), produce a diff, run `apply.sh` against the scratch cache, then real `apt-get` with scratch apt dirs — `update` lists the new package alongside the prior ones, `apt-get download` of the new package succeeds with no network, and every prior package remains offered.

### Tests for User Story 2 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementing.**

- [X] T009 [P] [US2] Unit tests in `tests/unit/test_apply_script.py` (sourcing the script's functions with scratch paths): diff structure preflight refusal (missing `manifest.txt`/`Release.gpg`/`Packages.gz`/`pool` → `Diff not found at {resolved}/diff — is the diff complete?`, nothing written); release-mismatch refusal (`Release mismatch ... Refusing to apply.`); non-root refusal (`Not running as root — run with sudo.`); copy + verify — a new file enters the operator's pool only after its SHA256 (and size) match the manifest (temp + rename, no partial files); replace-handoff-dists — the handoff `dists/{release}/Packages.gz`/`Release`/`Release.gpg` are replaced with the diff's complete ones (per-file atomic)
- [X] T010 [P] [US2] Integration test in `tests/integration/test_diff_flow.py` (operator half): baseline handoff applied to a scratch cache → `sync` a diff → run `apply.sh` against the scratch cache → real `apt-get` with scratch `Dir::State::lists`/`Dir::Cache`/`sourcelist` (`deb [signed-by={cache}/handoff/offline-cache.gpg] file:{cache}/handoff {release} {components}`) — `update` lists the new package alongside the prior ones and `apt-get download` of the new package succeeds from the cache offline; every prior package remains offered (FR-009)

### Implementation for User Story 2

- [X] T011 [US2] Complete the merge behavior in the `apply.sh` template in `src/package_mgmt/lib/service/handoff.py`: `apply_configure(cache_dir, ...)` ordering — structure preflight → base-set check → copy new files (verify SHA256 + size against `manifest.txt`, skip if already present with a matching checksum) → replace the handoff `dists/` per release (atomic per file, refreshing `Valid-Until`) → `apt update`; `offline-cache.gpg` copied over if the handoff lacks one; the apt sources entry is never touched (it keeps pointing at `file:{cache}/handoff`) (FR-007/FR-008/FR-009/FR-010)

**Checkpoint**: User Stories 1 AND 2 work independently — the operator merges a diff into an existing cache and apt consumes prior + new offline.

---

## Phase 5: User Story 3 - Safe Re-runs, Ordering, and Clean Failures (Priority: P2)

**Goal**: Out-of-order diffs are detected and refused via the manifest base set (FR-011), corrupt/tampered diffs are refused (FR-010), re-applies are idempotent (FR-012), and interrupted/failed applies leave the cache working and converge on re-run (FR-013) — apt never sees a package whose file is missing.

**Independent Test**: `apply.sh` refuses (a) a diff whose `delivered` file is missing from the operator's pool (a missed intermediate diff — `Missing intermediate sync: ...`, nothing changed), (b) a tampered `diff/pool/` file or manifest line (`Checksum mismatch ...`, nothing copied), (c) an existing pool file with a different checksum (conflict refusal); re-applying an applied diff reports nothing to do; interrupting an apply leaves no partial files and a re-run converges to a single clean run.

### Tests for User Story 3 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementing.**

- [X] T012 [P] [US3] Unit tests in `tests/unit/test_apply_script.py`: ordering refusal — delete a `delivered` file from the operator's pool → `Missing intermediate sync: {rel-path} is not in the cache pool — an earlier diff must be applied first. Refusing to apply.`, nothing changed; corrupt-diff refusal — tamper a file in `diff/pool/` (or its manifest line) → `Checksum mismatch for {rel-path}: expected {sha256}, got {actual}. Refusing to apply.`, nothing copied; existing-file conflict — a pool file present with a different checksum → `Existing file conflict: {rel-path} ... Refusing to apply.`
- [X] T013 [P] [US3] Unit tests in `tests/unit/test_apply_script.py`: re-apply of an already-applied diff reports `New files: 0 (already applied)` and changes nothing (idempotent); interrupted/failed apply — a re-run after a mid-copy interruption (temp file present, handoff partially replaced) converges to the same state as a single clean run (no partial files, consistent indexes); simulated `apt update` failure after the merge (unreadable handoff index) → merged files/indexes left in place, clear error, non-zero return (FR-013)

### Implementation for User Story 3

- [X] T014 [US3] Ordering + integrity + failure semantics in the `apply.sh` template in `src/package_mgmt/lib/service/handoff.py`: enforce the base-set presence check BEFORE any change (FR-011 — a missed intermediate diff is refused with clear guidance, so apt never sees a package whose file is missing); verify every new file against the manifest before it enters the pool (FR-010); per-file atomic writes (temp + rename) so an interruption leaves no partial file and a re-run converges (FR-013); idempotent re-apply (matching-checksum files skipped) (FR-012); `apt update` failure leaves the merged state in place for inspection, error text and exit codes exactly per contracts/cli.md

**Checkpoint**: All user stories independently functional — ordering, integrity, idempotency, and failure convergence are enforced on the operator side.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verification, consistency, and final validation across all stories.

- [X] T015 [P] Run the full test suite (`pytest tests/ -q`) and `ruff check src/ tests/` (ruff pinned per the `dev` extra, `<0.12`) — all green, no new violations
- [X] T016 [P] Execute every scenario in `specs/013-sync-new-packages/quickstart.md` end to end (sync diff with snapshot, apply with real `apt-get` on scratch dirs, release/missed-diff/corrupt refusals, re-runs and failure convergence) against the fixture cache
- [X] T017 [P] Documentation consistency pass: verify `roles.md` (admin `sync`, operator `{cache}/diff/apply.sh`, bulk-ingest `sync` renamed `ingest`), `contracts/cli.md`, and `data-model.md` match the implemented behavior; update `AGENTS.md` SPECKIT context if the feature pointer changed

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No tasks — existing structure reused
- **Foundational (Phase 2)**: T001 — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational (T005-T008 need T001); tests T002 ∥ T003 ∥ T004 before implementation
- **User Story 2 (Phase 4)**: Depends on US1 (T009/T010 need the diff + `apply.sh` from US1; T011 extends the same script — sequential)
- **User Story 3 (Phase 5)**: Depends on US2 (T014 extends the same `apply.sh` — sequential)
- **Polish (Phase 6)**: Depends on all user stories complete

### User Story Dependencies

- **User Story 1 (P1)**: No dependencies beyond Foundational
- **User Story 2 (P1)**: Requires the diff artifact and the emitted `apply.sh` from US1 — sequential
- **User Story 3 (P2)**: Extends the `apply.sh` merge from US2 — sequential
- All three stories share `src/package_mgmt/lib/service/handoff.py` (generator + `apply.sh` template) — do not parallelize across stories on that file

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Unit tests → contract tests → integration tests → generator/script → CLI handler → wiring
- Story complete before moving to the next priority

### Parallel Opportunities

- Foundational: T001
- US1 tests: T002 ∥ T003 ∥ T004
- US2 tests: T009 ∥ T010
- US3 tests: T012 ∥ T013
- Polish: T015 ∥ T016 ∥ T017
- Cross-story parallelism is limited: US2/US3 extend `apply.sh` in `handoff.py` (same file as US1) — run the stories sequentially

---

## Parallel Example: After Foundational

```bash
# Developer A — User Story 1 (admin delta export):
Task: "T002 unit tests in tests/unit/test_diff_generator.py"
Task: "T003 contract tests in tests/contract/test_cli.py"
Task: "T004 integration test (admin half) in tests/integration/test_diff_flow.py"
Task: "T005 generate_diff in src/package_mgmt/lib/service/handoff.py"
Task: "T006 apply.sh template in src/package_mgmt/lib/service/handoff.py"
Task: "T007 handler in src/package_mgmt/cli/commands/sync.py"
Task: "T008 wiring in src/package_mgmt/cli/main.py"

# Developer B — US1/2/3 test files (independent files, can be drafted early):
Task: "T009 US2 unit tests in tests/unit/test_apply_script.py"
Task: "T010 US2 integration test (operator half) in tests/integration/test_diff_flow.py"
Task: "T012 US3 ordering/integrity unit tests in tests/unit/test_apply_script.py"
Task: "T013 US3 idempotency/failure unit tests in tests/unit/test_apply_script.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 2: Foundational (diff path helpers — CRITICAL, blocks everything)
2. Complete Phase 3: User Story 1 — tests first, `generate_diff`, `apply.sh` template, CLI handler, wiring
3. **STOP and VALIDATE**: `sync --system <moniker>` on the fixture cache (after a baseline `handoff`) produces a diff with exactly the new files, and a re-run ships nothing new
4. Deploy/demo the admin-side incremental sync if ready

### Incremental Delivery

1. Foundational → admin delta export (US1) → **MVP checkpoint**
2. Add operator apply (US2) → the diff merges into the cache and apt consumes prior + new offline
3. Add safety/ordering/failures (US3) → refused missed/corrupt diffs, idempotent re-applies, clean convergence
4. Polish: lint, full suite, quickstart validation, docs consistency

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to the spec user story for traceability
- All user-facing errors use `_print_error()` (JSON to stderr); exit codes: 0 success, 1 failure, 101 stale schema, 102 config
- No schema changes — `sync` only READS the database (verified records + the spec 010 `sync_mask`), writes the snapshot via `mark_delivered`, and generates `{binary_cache}/diff/`; the cache's own `dists/` archive indexes are never modified, and the baseline `{binary_cache}/handoff/` is never rewritten by `sync`
- The diff carries the COMPLETE per-release indexes (prior + new) so the operator-side apply is a wholesale replacement of the handoff `dists/` — no bash index merging (research Decision 2); minimality applies to the shipped pool bytes only (FR-003, Constitution IV)
- The manifest `delivered` lines are the base set — the apply checks them for existence before any change, which detects missed intermediate diffs (FR-011) without any new state or schema change (research Decision 3)
- Operator-side behavior is unit-tested via the script's plain functions (sourced with scratch paths — no test hooks, no root, no host modification); real `apt-get` update/install runs against scratch apt dirs (no root, no network) — see the contracts/cli.md testability section
- The apt source stays `file://` with `signed-by` the handoff's `offline-cache.gpg` (unchanged from spec 012); the signed `Release.gpg` ships for Constitution compliance and is enforced by apt
- The snapshot (FR-005) uses the exact spec 010 `mark_delivered` semantics — re-running `sync` re-marks without double-counting (bitwise OR)
- Commit after each task or logical group
- Stop at any checkpoint to validate the story independently

### Follow-up backlog (out of 013 scope — track in future specs, not here)

- Reverting an applied diff (removing new packages from the operator's cache) — excluded by Constitution IX (spec assumptions)
- Operator-side cache pruning (removing superseded pool files) — excluded
- `deb-src` in the diff (source exposure on the operator side) — unchanged from spec 012 Decision 7
- Deep operator-side pool verification (structural + new-file integrity only; the delivered base set is presence-checked) — consistent with spec 012's deferral
