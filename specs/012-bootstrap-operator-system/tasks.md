---

description: "Task list for feature 012 - Bootstrap Operator System"

---

# Tasks: Bootstrap Operator System

**Input**: Design documents from `/specs/012-bootstrap-operator-system/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/cli.md, quickstart.md

**Tests**: Test tasks are included — the implementation plan (plan.md) specifies unit, integration, and contract tests for every story; write them first, verify they fail, then implement (project convention, as in specs 008-011).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **CLI tool**: `src/package_mgmt/` (Python package; CLI entry `package_mgmt`)
- **Admin cache**: `{binary_cache}/` — `dists/{release}/{component}/binary-amd64/Packages.gz` (archive indexes, UNTOUCHED), `pool/` (verified files), `package_mgmt.db`
- **Handoff (new)**: `{binary_cache}/handoff/` — `bootstrap.sh`, `offline-cache.gpg`, `pool -> ../pool`, `dists/{release}/{Release,Release.gpg,{component}/binary-amd64/Packages.gz}`
- **Operator system**: `/etc/apt/sources.list.d/openfromsource-cache.list`
- **Tests**: `tests/unit/`, `tests/integration/`, `tests/contract/` (pytest; operator-side `bootstrap.sh` logic is unit-tested by sourcing its functions with scratch paths — no root, no test hooks; real `apt-get` consumption runs against scratch apt dirs — see contracts/cli.md)
- **Fixture**: `tests/fixtures/make_test_iso.py` (existing scratch archive + pool)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: No setup required — the project structure, CLI scaffolding, error-handling conventions (`_print_error` JSON to stderr, exit codes 0/1/101/102), test layout, and pinned tooling (ruff `<0.12` in the dev extra) exist from prior features. Work starts directly in the Foundational phase.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The two shared pieces every user story builds on: handoff path helpers and the reusable snapshot marking.

- [X] T001 [P] Add handoff path helpers to `src/package_mgmt/lib/util/paths.py`: `handoff_dir(binary_cache) -> str` (returns `{binary_cache}/handoff`) and any path composition helpers the generator needs; add unit tests in `tests/unit/test_paths.py` (create if absent) covering the layout from `data-model.md` (bootstrap.sh, offline-cache.gpg, pool symlink, dists/{release}/{Release, Release.gpg, {component}/binary-amd64/Packages.gz})
- [X] T002 [P] Extract the snapshot marking from `src/package_mgmt/cli/commands/snapshot.py` into a reusable service `src/package_mgmt/lib/service/snapshot.py`: `take_snapshot(conn, system_name) -> int` (validates the moniker against `systems` (name + retired = 0), returns the count of current+verified binaries, sets `sync_mask |= bit` for all of them, commits); make `cli/commands/snapshot.py`'s `handle_snapshot` a thin handler over it (keeping its contract tests green); `handoff` will reuse it (research Decision 8)

**Checkpoint**: Path helpers and snapshot service ready — user story implementation can begin.

---

## Phase 3: User Story 1 - One Command, Offline-Ready (Priority: P1) 🎯 MVP

**Goal**: The admin runs `package_mgmt handoff --system <moniker>` (taking the delivery snapshot, spec 010 semantics), producing `{binary_cache}/handoff/`; the operator runs `{cache}/handoff/bootstrap.sh [<cache-dir>]` on a freshly DVD-installed offline system and `apt update` + `apt install` work with zero network and zero manual config (spec US1, FR-001/FR-002/FR-007/FR-011).

**Independent Test**: Fixture cache → `handoff --system <moniker>` → inspect artifacts (filtered `Packages.gz`, `Release`, `Release.gpg`, `offline-cache.gpg`, `bootstrap.sh`, `pool` symlink) → run the `bootstrap.sh` logic (unit-tested via its functions) → real `apt-get update` and `apt-get install --download-only` of a fixture package succeed (exact mechanics verified in research.md Empirical Appendix).

### Tests for User Story 1 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementing.**

- [X] T003 [P] [US1] Unit tests in `tests/unit/test_handoff.py`: verified-set enumeration (only `status='current' AND verified=1` with an index entry and existing pool file, arch-aware lookup per spec 011); filtered `Packages.gz` paragraphs carry exactly `Package`/`Version`/`Architecture`/`Depends`/`Provides`/`Filename`/`Size`/`SHA256` (Filename matches the real pool path); `Release` fields (Suite/Codename = release, Architectures: amd64, Components present, `Valid-Until` ~1y, `MD5Sum`/`SHA1`/`SHA256`/`SHA512` with paths relative to `dists/{release}/`); `Release.gpg` is a valid armored detached signature of `Release` (verify with `gpgv` against a dearmored keyring); `offline-cache.gpg` is the matching armored public key
- [X] T004 [P] [US1] Contract tests in `tests/contract/test_cli.py`: `handoff --system <moniker>` stdout shape (Handoff ready / Release / Components / Packages counts / Signed / Bootstrap / Snapshot lines), exit 0; missing `--system` → `{"error": "handoff requires --system <moniker>", "exit_code": 1}`; unknown/retired moniker → `{"error": "Unknown system '<name>'", "exit_code": 1}` and nothing generated (contracts/cli.md)
- [X] T005 [P] [US1] Integration test in `tests/integration/test_handoff_flow.py`: build the fixture cache (write_archive + pool files), `systems add` a moniker, run `handoff`, then against the real handoff run `apt-get` with scratch `Dir::State::lists`/`Dir::Cache`/`sourcelist` (`deb [signed-by={handoff}/offline-cache.gpg] file:{handoff} {release} {components}`) — `update` succeeds with **zero warnings** (Release has no blank line before the hash blocks; `Packages` + `Packages.gz` hash entries; signature verified via `signed-by`) and `apt-get download` copies a fixture `.deb` out; also assert the snapshot: the moniker's `sync_mask` bit is set on all verified binaries (re-run: no double-counting, count unchanged)

### Implementation for User Story 1

- [X] T006 [US1] Implement the handoff generator in `src/package_mgmt/lib/service/handoff.py`: enumerate the verified set (DB query + `load_binary_index` + the arch-aware `_lookup_entry` pattern from `src/package_mgmt/lib/service/downloader.py`); emit per-(release, component) filtered `Packages.gz` under `handoff/dists/{release}/{component}/binary-amd64/`; emit `Release` (Origin/Label/Suite/Codename/Architectures/Components/Date/Valid-Until +1y + checksum blocks with relative paths) and its armored detached signature `Release.gpg` (a clearsigned `InRelease` breaks apt's `file://` index fetch — research.md Decisions 2/5); generate the cache-local GPG key (`gpg --batch --quick-gen-key`, no passphrase) into the handoff, export armored `offline-cache.gpg`; create the `pool -> ../pool` symlink; all idempotent (regeneration overwrites cleanly); skip components whose filtered index would be empty; return counts for the summary (contracts/cli.md)
- [X] T007 [US1] Emit the operator script: add the `bootstrap.sh` template (embedded in `src/package_mgmt/lib/service/handoff.py`, written with the generated key filename/release/components filled in) implementing the contracts/cli.md contract — arg parsing (`[<cache-dir>]` optional, used as-is, default = handoff's parent; `--remove`), root check, structure preflight, `openfromsource-cache.list` write, `pool` symlink re-creation, `apt update`, `--remove` delete, exit 0/1, plain-text errors; the flow is structured as unit-testable functions with explicit paths (no test-only hooks)
- [X] T008 [US1] Implement `src/package_mgmt/cli/commands/handoff.py`: `handle_handoff(db_path, config, system)` — `validate_schema` on entry (stale DB → 101), moniker validation via the T002 snapshot service (unknown/retired → `Unknown system '<name>'`, exit 1), take the snapshot (marks all current+verified binaries — research Decision 8), run the T006 generator, print the summary block incl. the `Snapshot: N binaries marked for '<moniker>'` line; zero verified records → succeed with the warning line; excluded verified records → warning to the log + count; `_print_error` JSON convention for command errors
- [X] T009 [US1] Register and dispatch `handoff` in `src/package_mgmt/cli/main.py`: `handoff --system <moniker>` (required option), following the existing `_register_*` pattern

**Checkpoint**: User Story 1 fully functional — admin generates a handoff, operator bootstraps a scratch system, and real `apt-get` installs from the cache offline.

---

## Phase 4: User Story 2 - Exactly What the Cache Holds (Priority: P2)

**Goal**: The offered set equals the verified pool exactly — nothing phantom (FR-003); invalid/mismatched caches are refused before anything is configured (FR-004); the source is accepted with no manual trust workarounds (FR-009/SC-006); the delivery snapshot records what was handed off (FR-011).

**Independent Test**: Against three fixture-cache states — (a) complete, (b) verified record whose pool file was deleted, (c) handoff release ≠ the "system" release — confirm (a) lists exactly the verified content and installs cleanly, and (b)/(c) are refused with clear errors and no sources entry.

### Tests for User Story 2 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementing.**

- [X] T010 [P] [US2] Unit tests in `tests/unit/test_handoff.py`: exclusion behavior — a verified record with no resolvable index entry or missing pool file is excluded from the generated index and reported (log warning + summary count; a record excluded for reasons `verify` cannot fix → command error per contracts/cli.md); regenerated handoff after the pool file is restored includes the record again; `InRelease` signature verifies with `gpgv` against `offline-cache.gpg`
- [X] T011 [P] [US2] Unit tests in `tests/unit/test_bootstrap_script.py` (sourcing the script's functions with scratch paths): structure preflight refusal (delete `Release.gpg` or the `pool` target → `Handoff not found at {resolved}/handoff — is the cache complete?`, nothing written); release-mismatch refusal (script sees `VERSION_CODENAME` ≠ handoff release → `Release mismatch ... Refusing to configure.`, nothing written); non-root refusal (`Not running as root — run with sudo.`); `pool` symlink re-created when missing; sources entry contains exactly `deb [signed-by={resolved}/handoff/offline-cache.gpg] file:{resolved}/handoff {release} {components}`

### Implementation for User Story 2

- [X] T012 [US2] Generator exclusion reporting in `src/package_mgmt/lib/service/handoff.py`: distinguish excludable-by-verify records (pool file missing → excluded with log warning + summary count) from unconfirmable records (no index entry/checksum → command error, `_print_error` JSON per contracts/cli.md); never include a record whose pool file is absent (FR-003)
- [X] T013 [US2] Script preflight hardening in the `bootstrap.sh` template in `src/package_mgmt/lib/service/handoff.py`: full validation-before-write ordering (structure checks per component, release match against `/etc/os-release` `VERSION_CODENAME`, resolved-path reporting in errors) — nothing is written until validation passes (FR-004/FR-006)

**Checkpoint**: User Stories 1 AND 2 work independently — the offered set is provably exact, and invalid/mismatched caches are refused cleanly.

---

## Phase 5: User Story 3 - Safe Re-runs and a Clean Exit (Priority: P2)

**Goal**: The bootstrap is idempotent, failures before the entry write leave no trace, an `apt update` failure after the write leaves the entry in place for inspection (FR-006 as clarified), and `--remove` restores the pre-bootstrap state (FR-005/FR-008).

**Independent Test**: Run `bootstrap.sh` twice (second run: success, one entry, no duplicates); interrupt before the write (no entry exists); force an `apt update` failure after the write (entry remains, error reported, exit 1); `--remove` (entry gone, nothing else touched).

### Tests for User Story 3 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementing.**

- [X] T014 [P] [US3] Unit tests in `tests/unit/test_bootstrap_script.py`: re-run of `write_sources_entry` over a configured dir (single entry, no duplicates); `remove_sources_entry` deletes exactly `openfromsource-cache.list` (other files in `sources.list.d/` untouched); unknown option → `No such option: {arg}. Usage: bootstrap.sh [<cache-dir>] [--remove]`, exit 1; relative cache-dir resolution (T013)
- [X] T015 [P] [US3] Unit test in `tests/unit/test_bootstrap_script.py`: simulated `apt update` failure (unreadable handoff index) after the entry was written → entry remains in place, clear error on stderr, non-zero return (FR-006 Option B — matches the contract line `apt update failed — see output above; the source entry was left in place for inspection.`)

### Implementation for User Story 3

- [X] T016 [US3] Idempotency + removal + failure semantics in the `bootstrap.sh` template in `src/package_mgmt/lib/service/handoff.py`: atomic write of `/etc/apt/sources.list.d/openfromsource-cache.list` (temp file + rename, overwriting only its own prior content), `--remove` deletes the entry only, `apt update` failure leaves the entry and reports per contracts/cli.md, all error texts and exit codes exactly per the contract; the flow is exposed as unit-testable functions (`write_sources_entry`, `remove_sources_entry`, `run_apt_update`, `bootstrap_configure`) — no test-only hooks

**Checkpoint**: All user stories independently functional — safe re-runs, clean removal, and defined failure behavior.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verification, consistency, and final validation across all stories.

- [X] T017 [P] Run the full test suite (`pytest tests/ -q`) and `ruff check src/ tests/` (ruff pinned per the `dev` extra, `<0.12`) — all green, no new violations
- [X] T018 [P] Execute every scenario in `specs/012-bootstrap-operator-system/quickstart.md` end to end (handoff with snapshot, bootstrap with real `apt-get` on scratch dirs, invalid/mismatch refusals, re-run/removal) against the fixture cache
- [X] T019 [P] Documentation consistency pass: verify `roles.md` (`handoff` admin command, `{cache}/handoff/bootstrap.sh` operator entry), `contracts/cli.md`, and `data-model.md` match the implemented behavior; update `AGENTS.md` SPECKIT context if the feature pointer changed

---

## Phase 7: Integrity Story & Documentation Correction

**Purpose**: Reconcile the spec's US2 claim with the implemented behavior — the bootstrap preflight is structural-only (structure + release match); the operator cannot run `package_mgmt verify` on a bare install, so deep checksum verification is deferred, not enforced at bootstrap.

- [X] T020 [P] Remove the US2 "checksum-inconsistent pool refused at bootstrap" claim from `spec.md` (US2 given/when/then), `research.md` Decision 6, and any matching language in `contracts/cli.md`; restate the bootstrap preflight as structural-only (structure + release match) and record that operator-side checksum verification is deferred; fix `.specify/memory/roles.md` if it lists `verify`/`list` for the operator

**Checkpoint**: The spec and docs accurately describe the implemented (structural) preflight; the integrity story is consistent across all documents.

---

## Phase 8: Security Hardening

**Purpose**: Close the supply-chain and cache-integrity weaknesses: the archive indexes are fetched unauthenticated over plain HTTP (no Release/InRelease verification anywhere), and a crafted index `Filename` can write outside the cache.

- [X] T021 [P] Path-traversal guard: normalize + prefix-check `entry.filename` before joining it into `{binary_cache}` in `src/package_mgmt/lib/service/downloader.py` (dest join) and `src/package_mgmt/lib/service/handoff.py` (pool-path join) — reject `..` segments, absolute paths, and filenames outside `pool/`; unit tests with crafted index entries (`Filename: ../../...`, absolute path) asserting the record is excluded/handled safely and nothing is written outside the cache
- [X] T022 [P] Switch the default mirror to HTTPS: `project.defaults.toml` and `src/package_mgmt/lib/model/config.py` (`http://deb.debian.org/debian` → `https://...`); normalize any `http://` → `https://` in `config_loader`/fetch; update `docs/001-quickstart-howto.md` and the quickstart docs
- [X] T023 [P] Archive Release/InRelease signature verification at index-fetch time in `src/package_mgmt/lib/service/index_loader.py` `fetch_index` (currently a bare `urllib.request.urlretrieve`): fetch and verify `InRelease` (or `Release` + `Release.gpg`) against a pinned Debian archive keyring before trusting index hashes; refuse to load indexes on mismatch; document the approach (research decision) and add unit + integration tests

**Checkpoint**: Indexes are fetched over HTTPS and authenticated; a crafted or MITM'd index is refused before it can influence resolution or write paths.

---

## Phase 9: Tooling / CI Determinism

**Purpose**: The CI lint job installs ruff unpinned (`.github/workflows/ci.yml`) while the dev extra pins `ruff<0.12`, and no `[tool.ruff]` config exists — the lint gate can drift and fail wholesale on newer ruff releases.

- [X] T024 [P] Fix CI lint determinism: pin ruff in `.github/workflows/ci.yml` to the dev-extra range (`pip install "ruff>=0.9,<0.12"` or reuse `pip install -e ".[dev]"`) and add a `[tool.ruff]` config to `pyproject.toml` (target-version, line-length, and any ignores needed); verify `ruff check src/ tests/` passes with the pinned version

**Checkpoint**: The CI lint job and local dev-extra ruff are the same version and both green.

---

## Phase 10: Handoff Robustness

**Purpose**: The generator's writes are non-atomic (a kill mid-generation truncates `Packages.gz`/`bootstrap.sh`) and `Release` uses `time.gmtime` (non-reproducible, inconsistent with the `mtime=0` gzip); the signing key is regenerated every run (no stable trust anchor).

- [X] T025 [P] Make the handoff generator's writes atomic and reproducible in `src/package_mgmt/lib/service/handoff.py`: write `Packages.gz`/`Release`/`Release.gpg`/`bootstrap.sh`/key via temp-file + rename (matching the operator script's atomic write); replace `time.gmtime` in `_release_text` with a deterministic date (e.g. an env-overridable `SOURCE_DATE_EPOCH` or the cache's own timestamp) consistent with the `mtime=0` gzip; unit test: regenerate twice → byte-identical `Release`/`Packages.gz`
- [X] T026 Stable handoff trust anchor: persist the generated GPG key across runs (store the keyring under the cache, e.g. `{cache}/handoff/.gnupg`, instead of a throwaway tempdir) so `offline-cache.gpg` does not change on regeneration; unit/integration test: regenerating the handoff leaves `offline-cache.gpg` unchanged (same file as T025 — sequential)

**Checkpoint**: Regeneration is atomic (no truncated artifacts) and produces byte-identical, stably-signed output.

---

## Phase 11: Code-Quality Debt

**Purpose**: Pay down Constitution VI items that are safe, mechanical cleanups: dead code, duplicated validation wrappers, a private-import leak, and swallowed errors.

- [X] T027 [P] Remove dead code: `load_full_index` (`src/package_mgmt/lib/service/index_loader.py`), `deserialize_build_depends` (`src/package_mgmt/lib/model/dependency.py`), `sort_versions` (`src/package_mgmt/lib/util/versions.py`) plus now-unused imports; `ruff check src/ tests/` green
- [X] T028 [P] Dedupe the four identical `_validate(db_path)` wrappers (`cli/commands/{download,handoff,snapshot,systems}.py`) into one shared helper; promote the private `downloader._lookup_entry` import (`src/package_mgmt/lib/service/handoff.py`) to a public helper used by both modules
- [X] T029 [P] Error-surface hygiene: `src/package_mgmt/cli/main.py` stop swallowing the real config error (`except Exception: config = None`); `resolver.ensure_indexes` translate index-fetch failures into a clear message instead of surfacing a raw `FileNotFoundError`

**Checkpoint**: Dead code removed, duplication eliminated, and errors surface clearly — full suite still green.

---

## Phase 12: Documentation / Process Refresh

**Purpose**: `AGENTS.md` SPECKIT pointer and `docs/001-quickstart-howto.md` are stale relative to the shipped command surface.

- [X] T030 [P] Refresh `AGENTS.md` SPECKIT block (the "Next step: run /speckit.tasks" pointer is stale — tasks exist and are all checked) and `docs/001-quickstart-howto.md` (the `--help` block misses `ensure-base`/`download`/`systems`/`snapshot`/`handoff`; "Next steps" still points at the shipped download feature)

**Checkpoint**: Pointers and docs match the implemented command surface.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No tasks — existing structure reused
- **Foundational (Phase 2)**: T001 ∥ T002 — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational (T006 needs T001/T002)
- **User Story 2 (Phase 4)**: Depends on US1 (T012/T013 extend the generator and script — same files, sequential)
- **User Story 3 (Phase 5)**: Depends on US1 (T016 extends the script template — same file, sequential)
- **Polish (Phase 6)**: Depends on all user stories complete
- **Integrity/Docs (Phase 7)**: Docs-only, no code dependencies
- **Security (Phase 8)**: T021 ∥ T022 ∥ T023 — T023 touches `fetch_index` shared with `init`/`ensure-base`; verify against the full suite
- **CI/Tooling (Phase 9)**: Independent
- **Handoff robustness (Phase 10)**: T025 → T026 (both touch `handoff.py` — sequential)
- **Code quality (Phase 11)**: T027 ∥ T028 ∥ T029
- **Docs (Phase 12)**: Independent

### User Story Dependencies

- **User Story 1 (P1)**: No dependencies beyond Foundational
- **User Story 2 (P2)**: Extends the US1 generator (`handoff.py`) and script template — sequential
- **User Story 3 (P2)**: Extends the US1 script template — sequential
- All three stories share `src/package_mgmt/lib/service/handoff.py` — do not parallelize across stories on that file

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Unit tests → contract tests → integration tests → generator/script → CLI handler → wiring
- Story complete before moving to the next priority

### Parallel Opportunities

- Foundational: T001 ∥ T002
- US1 tests: T003 ∥ T004 ∥ T005
- US2 tests: T010 ∥ T011
- US3 tests: T014 ∥ T015
- Polish: T017 ∥ T018 ∥ T019
- Phase 8: T021 ∥ T022 ∥ T023
- Phase 10: T025 → T026 (same file, `handoff.py`)
- Phase 11: T027 ∥ T028 ∥ T029

---

## Parallel Example: After Foundational

```bash
# Developer A — User Story 1:
Task: "T003 unit tests in tests/unit/test_handoff.py"
Task: "T004 contract tests in tests/contract/test_cli.py"
Task: "T005 integration test in tests/integration/test_handoff_flow.py"
Task: "T006 generator in src/package_mgmt/lib/service/handoff.py"
Task: "T007 bootstrap.sh template in src/package_mgmt/lib/service/handoff.py"
Task: "T008 handler in src/package_mgmt/cli/commands/handoff.py"
Task: "T009 wiring in src/package_mgmt/cli/main.py"

# Developer B — Foundational + US2/3 test files (independent files):
Task: "T001 path helpers in src/package_mgmt/lib/util/paths.py"
Task: "T002 snapshot service in src/package_mgmt/lib/service/snapshot.py"
Task: "T010 US2 unit tests in tests/unit/test_handoff.py"
Task: "T014 US3 integration tests in tests/integration/test_bootstrap_flow.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 2: Foundational (path helpers + snapshot service — CRITICAL, blocks everything)
2. Complete Phase 3: User Story 1 — tests first, generator, script template, CLI handler, wiring
3. **STOP and VALIDATE**: `handoff --system <moniker>` on the fixture cache, then `bootstrap.sh` + real `apt-get` on scratch dirs — installs work fully offline
4. Deploy/demo the binary offline install if ready

### Incremental Delivery

1. Foundational → handoff + bootstrap baseline (US1) → **MVP checkpoint**
2. Add validation/exactness (US2) → provably exact offered set, clean refusals
3. Add safety/re-runs/removal (US3) → idempotent, recoverable operator flow
4. Polish: lint, full suite, quickstart validation

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to the spec user story for traceability
- All user-facing errors use `_print_error()` (JSON to stderr); exit codes: 0 success, 1 failure, 101 stale schema, 102 config
- No schema changes — the handoff only READS the database and writes the spec 010 `sync_mask` via the snapshot; the cache's own `dists/` archive indexes are never modified
- Operator-side behavior is unit-tested via the script's plain functions
  (sourced with scratch paths — no test hooks, no root, no host modification);
  real `apt-get` update/install runs against scratch apt dirs (no root, no
  network) — see the contracts/cli.md testability section and research.md
  Empirical Appendix
- The apt source is `file://` with `signed-by` the handoff's `offline-cache.gpg`; the signed `Release.gpg` ships for Constitution compliance and is enforced by apt (research Decision 2 — `Release` without a blank line before the hash blocks + `Packages`/`Packages.gz` hash entries + `signed-by` yields a clean, signature-verified `apt update`)
- The snapshot (FR-011) uses the exact spec 010 marking semantics — re-running `handoff` re-marks without double-counting (bitwise OR)
- Commit after each task or logical group
- Stop at any checkpoint to validate the story independently

### Follow-up backlog (out of 012 scope — track in future specs, not here)

- `sync` command (admin, apt-offline bundle ingest) — deferred in specs 010/002
- Operator `bootstrap`/rebuild (reinstall the cached stack on a fresh OS) — deferred out of 012 by Constitution IX
- Operator-side checksum verification in `bootstrap.sh` (structural preflight only; deep checks deferred — see T020)
- Index refresh / incremental updates, multi-arch, HTTP Range resume, `deb-src` in the handoff — out of scope
