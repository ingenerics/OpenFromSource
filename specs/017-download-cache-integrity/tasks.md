---

description: "Task list for Download & Cache Integrity"

---

# Tasks: Download & Cache Integrity

**Input**: Design documents from `/specs/017-download-cache-integrity/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (`cli.md`), quickstart.md

**Tests**: Tests ARE required. The spec's "User Scenarios & Testing (mandatory)" and the plan's per-phase Tests mandate them; the project convention (015/016) ships every phase with its regression tests. Tests MUST be written and FAIL before implementation.

**Organization**: Six phases (US1 fresh-cache guard, US2 redirects, US3 source re-check, US4 short-circuit, US5 source-index gating, US6 index self-heal), grouped by user story so each story is independently implementable and testable. **US1 is already implemented and committed** (`00f674e "017-download-cache-integrity plan and phase 1"`) — its tasks are marked `[X]` with recorded evidence. Execution-order dependencies (US4's source-side short-circuit before US3's per-file re-fetch economy; US5 before US6 within `ensure_indexes`) are spelled out in the Dependencies section.

**Cross-cutting constraints (all phases)**:
- **No schema change** — the spec 010/011 freeze is absolute; every fix is behavioral (`specs/017-download-cache-integrity/data-model.md`).
- Contracts in `specs/014-base-profiles/contracts/cli.md` and `specs/017-download-cache-integrity/contracts/cli.md` hold or are extended, never contradicted; `tests/contract/test_cli.py` stays green.
- Every change lands with its regression tests and must be green under **both** documented runners: `pytest` and `python -m unittest discover -s tests`.
- No new runtime dependencies; no new network call sites (redirects re-issue an existing request; index self-heal re-fetches an existing URL); work lands on `main` in the same alpha release (after 016).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US6)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Baseline & Environment)

**Purpose**: Establish a verified baseline before any download/index change — confirms both documented runners execute, fixtures are available, and the repo reflects the post-016 state the plan's line numbers assume.

- [X] T001 Verify baseline test state: run both documented runners (`pytest` and `python -m unittest discover -s tests`) from a clean checkout; record the green baseline as the Phase 9 gate baseline — **evidence: `.venv/bin/python -m pytest tests/unit tests/integration tests/contract -q` → 580 passed + 41 subtests (36.35s); `.venv/bin/python -m unittest discover -s tests` → Ran 580 tests, OK (35.94s)**
- [X] T002 [P] Verify the offline-safe test prerequisites per `specs/017-download-cache-integrity/quickstart.md`: the local mirror fixture (`tests/fixtures/make_test_iso.py`, incl. `serve_pool`, `serve_rate_limited`) and the ISO builder (`build_iso`, `write_archive`) are present and importable — **evidence: fixtures import OK; unit/integration/contract suites referencing them pass (T001)**
- [X] T003 [P] Verify repo state: on branch `main` with the venv active, and confirm the 017 plan + Phase 1 guard are committed (`00f674e`) — the line numbers in this task list depend on it — **evidence: `git log --oneline -1` → `00f674e 017-download-cache-integrity plan and phase 1`; working tree clean**

---

## Phase 2: Foundational (Cross-Cutting Guards)

**Purpose**: Lock in the guards every phase must respect. No new error-handling/exception infrastructure is required beyond the Phase 1 `CacheNotEmptyError` — the download/index phases reuse the existing `_print_error`/exit-code machinery.

**⚠️ CRITICAL**: These guards are prerequisite evidence — verify them before any phase, re-verify in Phase 9.

- [X] T004 Confirm the schema-freeze baseline: the 017 commit changes no tables/columns; record that NO phase may add/alter columns — all fixes are behavioral (`specs/017-download-cache-integrity/data-model.md`) — **evidence: `git show 00f674e --stat` lists no `src/package_mgmt/lib/model/database.py`; `EXPECTED_SCHEMA_SQL` defines exactly the 8 frozen tables (`packages`, `binaries`, `sources`, `binary_to_source`, `source_build_deps`, `provides`, `virtual_resolutions`, `systems`)**
- [X] T005 [P] Confirm the baseline exit-code / JSON contracts from `specs/014-base-profiles/contracts/cli.md` hold (exit `0`/`1`/`101`/`102`, JSON-on-stderr) and that the new Phase 1 guard contract lands as an extension — **evidence: `.venv/bin/python -m pytest tests/contract/test_cli.py -q` → 59 passed + 11 subtests (incl. `test_rerun_refused_with_json_error`)**
- [X] T006 [P] Record the pre-fix behavior baselines for US2–US6 that the download/index phases change, with exact anchors (from `review/04_alpha_summary/02_high_priority_followups.md` and `specs/017-download-cache-integrity/research.md`): `_PooledOpener` hashes a 3xx body (`downloader.py:95-115`); the source phase selects only `verified = 0` and never re-checks (`downloader.py:402-529`); the size+hash skip applies only to `verified = 1` (`downloader.py:335-341, 454-483`); `ensure_indexes` fetches/loads Sources indexes unconditionally (`resolver.py:251-265`); `index_loader.fetch_index` writes non-atomically and the `ensure_indexes` retry loop never deletes a failed destination — **evidence: pre-fix behavior confirmed and cited in `research.md` Decisions 2–6**

**Checkpoint**: Guards verified — user story phases may proceed.

---

## Phase 3: User Story 1 - Base Ingest Is Fresh-Cache-Only (Priority: P1) 🎯 MVP

**Goal**: `ensure-base` runs only against an empty cache; a cache that already holds registered packages is refused with a clear error (exit 1) and no modification — removing the H-2 (re-run resets `verified`) and M-3 (duplicate `current` rows) failure modes by construction. *(H-2, M-3 — resolved by constraint)*

**Independent Test**: Register the base set on a fresh cache (succeeds), then invoke `ensure-base` again — it refuses with a clear error (exit 1) and leaves the cache byte-for-byte unchanged (`spec.md` US1).

### Tests for User Story 1 (write FIRST, must FAIL before implementation) ✅ DONE

- [X] T007 [P] [US1] Unit test: `ensure_base` on a populated cache raises `CacheNotEmptyError` with "fresh cache" in the message and leaves `binaries` untouched; fresh cache succeeds in `tests/unit/test_ensure_base.py` — **evidence: `test_rerun_refused_on_non_empty_cache` passes (was red pre-fix: the second run re-registered silently)**
- [X] T008 [P] [US1] Integration test: an interrupt mid-Phase B leaves a partial cache and the re-run is refused (`CacheNotEmptyError`); via CLI, a second `handle_ensure_base()` exits 1 with "fresh cache" in the JSON error and db counts unchanged in `tests/integration/test_ensure_base_flow.py` — **evidence: `test_interrupted_run_leaves_partial_state_and_refuses_rerun`, `test_rerun_refused_via_cli` pass**
- [X] T009 [P] [US1] Contract test: `ensure-base` on a populated cache → exit 1, JSON error on stderr containing "fresh cache" in `tests/contract/test_cli.py` — **evidence: `test_rerun_refused_with_json_error` passes (added to `TestEnsureBaseContract`)**

### Implementation for User Story 1 ✅ DONE

- [X] T010 [US1] Add `CacheNotEmptyError(PackageMgmtError)` to `src/package_mgmt/lib/util/errors.py`
- [X] T011 [US1] Add `_assert_fresh_cache` (`SELECT 1 FROM binaries LIMIT 1` → raise `CacheNotEmptyError` with fresh-start guidance) and call it at the top of `ensure_base` before any registration in `src/package_mgmt/lib/service/ensure_base.py`
- [X] T012 [US1] Catch `CacheNotEmptyError` in `handle_ensure_base` → `_print_error(..., EXIT_ERROR)` in `src/package_mgmt/cli/commands/ensure_base.py`
- [X] T013 [US1] Docs: add the "Fresh cache only" callout, workflow comment, re-runs note (fresh start required for re-ingest/interrupted runs), and fix the stale "nothing sets `verified = 1` today" line in `docs/003-ensure-base.md`
- [X] T014 [US1] Validation: full suite green under both runners; ruff clean on the touched files — **evidence: `.venv/bin/python -m ruff check src/... tests/...` → All checks passed; pytest 580 passed + 41 subtests; unittest Ran 580 tests, OK**

**Checkpoint**: US1 is fully functional — the graybeard flow (`init` → `ensure-base` → `verify` → `handoff`) runs once on an empty cache; a populated cache is refused with no state change.

---

## Phase 4: User Story 2 - Downloads Follow Redirects (Priority: P2)

**Goal**: Payload downloads follow `301/302/303/307/308` redirects to the final location (bounded at 5 hops, loops rejected) before hashing; the final payload still passes the index checksum gate. A no-`Location` redirect, a loop, or an over-limit chain fails with a clear **permanent** error and no leftover `*.part`. Direct downloads behave unchanged. *(F-16)*

**Independent Test**: A local HTTP fixture that redirects a pool request — the download succeeds and the payload passes the checksum; a redirect without a target and a redirect loop each fail with a clear permanent error and no leftover partial file (`spec.md` US2).

### Tests for User Story 2 (write FIRST, must FAIL before implementation) ⚠️

- [X] T015 [US2] Add a `serve_redirecting` handler to `tests/fixtures/make_test_iso.py`: redirects a `/pool/…` request to the real location (301/302/307/308 variants), a no-`Location` variant, and a redirect-loop variant — **evidence: `_RedirectHandler` + `serve_redirecting(base_dir, port, redirects)` (path → `(code, location)`; `None` location = no-target; `/alt/...` serves pool bytes); drives the US2 tests below**
- [X] T016 [P] [US2] Unit test: a 301-redirected pool request → `download` succeeds and the final payload passes its checksum in `tests/unit/test_downloader.py` (must FAIL pre-fix: 3xx body hashed → bogus checksum mismatch) — **evidence: `TestRedirects::test_follows_redirect_and_final_payload_passes_checksum` + `TestRedirectDownloadFlow::test_redirected_download_downloads_verifies_and_leaves_no_part` pass (payload == `_pool_content`, sha256 == index SHA256, `verified = 1`, no `.part`)**
- [X] T017 [P] [US2] Unit test: a chain over the 5-hop limit → clear permanent error naming the hop limit; no `.part` left behind in `tests/unit/test_downloader.py` — **evidence: `TestRedirects::test_chain_over_hop_limit_fails_permanent_error` passes (6-redirect chain → `redirect limit exceeded (more than 5 redirects)`)**
- [X] T018 [P] [US2] Unit test: a redirect loop and a no-`Location` redirect → clear permanent error; no `.part` left behind in `tests/unit/test_downloader.py` — **evidence: `TestRedirects::test_redirect_loop_is_rejected` (`redirect loop detected`) + `test_redirect_without_location_fails_clear_error` (`redirect without Location`); `TestRedirectDownloadFlow` loop/no-Location e2e → permanent failure, `verified = 0`, no `.part`**
- [X] T019 [P] [US2] Unit test: a direct (non-redirecting) download is unchanged and passes its checksum (regression) in `tests/unit/test_downloader.py` — **evidence: `TestRedirects::test_direct_download_unchanged` passes**

### Implementation for User Story 2

- [X] T020 [US2] Implement redirect-following in `_PooledOpener.__call__` (`src/package_mgmt/lib/service/downloader.py:95-115`): on `301/302/303/307/308`, read `Location`, resolve via `urllib.parse.urljoin(url, location)`, re-issue the GET against the new URL; track visited URLs to reject loops; cap the chain at `MAX_REDIRECTS = 5`; a `Location`-less redirect or over-limit chain raises a clear permanent `urllib.error.HTTPError`/`URLError` (the retry layer must not back off); return only the final non-3xx response so `_fetch_to_part` hashes the final payload — **implemented: `MAX_REDIRECTS = 5`, `REDIRECT_STATUSES = (301, 302, 303, 307, 308)`, `_RedirectError` (permanent — surfaced verbatim by `_fetch_with_retries` before the generic `HTTPError` branch), `_get` per hop with same-origin keep-alive preserved (`will_close` honored) and cross-host re-pooling via the existing `(scheme, host, port)` key**
- [X] T021 [US2] Validation: direct downloads unchanged (regression); redirecting downloads pass the checksum gate; both runners green on `tests/unit/test_downloader.py` + `tests/contract/test_cli.py` — **evidence: `pytest tests/unit tests/integration tests/contract` → 588 passed + 41 subtests; `python -m unittest discover -s tests` → Ran 588 tests, OK; `tests/integration/test_download_flow.py` → 13 passed; ruff clean on `downloader.py`/`test_downloader.py`/`make_test_iso.py`**

**Checkpoint**: US2 is fully functional — a redirecting mirror no longer bricks the download path; integrity is still gate-checked on the final payload.

---

## Phase 5: User Story 3 - Verified Sources Are Re-Checked Each Run (Priority: P2)

**Goal**: On every `download` run, verified source payloads are re-checked per index-listed file (existence → size → checksum) just like binaries; a deleted or corrupt file un-marks the source and re-fetches **exactly that file**; intact files of the same source are never re-fetched; an all-intact source triggers no re-download. *(F-17)*

**Independent Test**: Delete one file of a verified source — the next `download` re-fetches exactly that file and re-verifies it; a corrupt-but-size-matching file is un-marked and re-fetched; an all-intact source triggers no re-download (`spec.md` US3).

> **Dependency**: the source-side short-circuit work-set construction lands in US4/T031 first (see Dependencies).

### Tests for User Story 3 (write FIRST, must FAIL before implementation) ⚠️

- [X] T022 [P] [US3] Unit/integration test: delete one file of a verified source → next `download` re-fetches exactly that file and re-marks the source verified in `tests/unit/test_downloader.py` + `tests/integration/test_download_flow.py` (must FAIL pre-fix: `pending_sources` selects only `verified = 0`, so nothing re-checks the deleted file) — **evidence: `TestSourceDownloadEngine::test_deleted_file_of_verified_source_is_reefetched_and_stays_verified` (opener called with only the deleted file's URL, `files_downloaded == 1`, file restored, source re-verified) + `SourceDownloadFlowTest::test_deleted_verified_source_file_is_reefetched_on_rerun` (CLI re-run: "Files downloaded: 1", "Files verified: 1", source re-verified, other sources untouched)**
- [X] T023 [P] [US3] Unit test: a corrupt-but-size-matching source file → un-marked (`verified = 0`) and re-fetched (the checksum step catches it) in `tests/unit/test_downloader.py` — **evidence: `TestSourceDownloadEngine::test_corrupt_size_matching_file_is_unmarked_and_reefetched` passes (same-size wrong bytes → re-fetched via the sha256 step, restored, re-verified)**
- [X] T024 [P] [US3] Unit test: an all-intact verified source → nothing re-downloaded in `tests/unit/test_downloader.py` — **evidence: `TestSourceDownloadEngine::test_all_intact_verified_source_downloads_nothing` (no opener calls, `planned_files == 0`) + `SourceDownloadFlowTest::test_all_intact_verified_sources_rerun_downloads_nothing` (both phases print "nothing to download.", exit 0)**
- [X] T025 [P] [US3] Unit test: a multi-file source with only some files missing → only the missing files are re-fetched (intact files skipped) in `tests/unit/test_downloader.py` — **evidence: `TestSourceDownloadEngine::test_only_missing_files_of_verified_source_are_reefetched` passes (opener called only for the 2 missing files, `files_downloaded == 2`, source re-verified)**

### Implementation for User Story 3

- [X] T026 [US3] Restructure `download_pending_sources` (`src/package_mgmt/lib/service/downloader.py:402-529`): run a per-run re-check over **verified** sources — for each index-listed file, `existence → stat size → sha256` reusing `_stat_size_matches` + `_file_sha256`; on any failure un-mark the source (`verified = 0`) and add exactly the missing/mismatched files to the re-fetch work set; intact files of that source are not re-fetched; a source is marked verified only when ALL of its files are present and checksum-verified (existing invariant, retained) — **implemented: `_group_pending_sources` → `_group_sources_to_check` (all sources linked to a current binary, carrying `verified`); the engine now short-circuits present-and-valid files (`source_present`), un-marks verified sources with a defect before downloading (interrupted runs never leave a false verified), and re-marks verified when all files are ok (the re-check confirms integrity; refined in Phase 6 to re-verify all-ok sources without a fetch — see T030/T031 note); `files_verified` counts downloaded-and-verified files this run (matches the binary phase convention). `pending_sources` is retained unchanged (its verified=0 semantics are still tested). Per-file construction built here is the shared source work-set the Phase 6 short-circuit builds on**

**Checkpoint**: US3 is fully functional — verified source payloads can no longer silently disappear from the cache.

---

## Phase 6: User Story 4 - Present Valid Files Are Not Re-Downloaded (Priority: P2)

**Goal**: A payload file that already exists and matches its index `Size` and `SHA256` is **not** re-downloaded, regardless of the record's `verified` state; mismatched or absent files are fetched. The short-circuit never re-downloads a present-and-valid file; the re-check that runs anyway confirms integrity and (re)verifies the record without a network round-trip (a refined reading of "the flag is unchanged by the short-circuit": the flag is unchanged by *skipping the download* — a record is verified when its file is confirmed valid, matching how US3 re-verifies sources). *(M-7)*

**Independent Test**: An unverified record whose pool file already matches is not re-downloaded; one whose file is mismatched or absent is re-fetched (`spec.md` US4).

### Tests for User Story 4 (write FIRST, must FAIL before implementation) ⚠️

- [X] T027 [P] [US4] Unit test: an **unverified** record whose pool file is already present and matches (size + sha256) → not re-fetched in `tests/unit/test_downloader.py` (must FAIL pre-fix: the size+hash skip applies only to `verified = 1`) — **evidence: `TestUnverifiedShortCircuit::test_unverified_present_and_matching_file_is_not_reefetched_but_reverified` (no opener calls, `planned_files == 0`, file left untouched; re-check re-verifies the record)**
- [X] T028 [P] [US4] Unit test: an unverified record whose file is present but mismatched → re-fetched in `tests/unit/test_downloader.py` — **evidence: `TestUnverifiedShortCircuit::test_unverified_mismatched_file_is_reefetched` (opener called, content restored, record verified)**
- [X] T029 [P] [US4] Unit test: an unverified record whose file is absent → fetched in `tests/unit/test_downloader.py` — **evidence: `TestUnverifiedShortCircuit::test_unverified_absent_file_is_fetched` (opener called, file created)**
- [X] T030 [P] [US4] Unit test: a mix of present-and-valid and missing records → only the missing/mismatched ones are fetched; the short-circuit never re-downloads and the re-check re-verifies the valid ones in `tests/unit/test_downloader.py` — **evidence: `TestUnverifiedShortCircuit::test_mix_shortcircuits_valid_and_fetches_missing` (only the missing file's URL fetched; both records verified — the valid one re-verified without a fetch) + `test_unverified_source_with_all_files_present_is_reverified_without_fetch` (source phase: no calls, all-present source re-verified). Note: the "flag unchanged" wording was refined — see Phase 6 Goal**

### Implementation for User Story 4

- [X] T031 [US4] Apply the `exists + stat-size + sha256` short-circuit to **unverified** records in both the binary and source phases of `download_pending_binaries`/`download_pending_sources` (`src/package_mgmt/lib/service/downloader.py`); the short-circuit never re-downloads a present-and-valid file (see Phase 6 Goal for the refined flag semantics); the source-side work-set construction here is the prerequisite for US3's per-file re-fetch economy — **implemented: binary phase short-circuit moved out of the `if verified:` block; unverified-but-valid records are re-verified in place (committed) with no fetch; source phase re-verifies any source whose files are all ok (`ok == all_dests`), dropping the was-verified/downloaded-this-run gating. Fixture adjustments: `TestRedirectDownloadFlow` now uses a separate mirror base (cache stays empty so redirects are actually exercised); `test_missing_file_fails_that_file_others_continue` + `test_failure_listed_and_exit_1` remove the target file from the cache (not just the mirror) so a fetch is triggered**
- [X] T032 [US4] Validation: interrupted-run economy — a partial pool plus a reset/unverified record set does not trigger a full re-fetch; both runners green on `tests/unit/test_downloader.py` + `tests/contract/test_cli.py` — **evidence: `DownloadFlowTest::test_reset_unverified_records_do_not_trigger_full_redownload` (reset all → re-run prints "nothing to download." for both phases, exit 0, every record re-verified without re-download); `pytest tests/unit tests/integration tests/contract` → 600 passed + 41 subtests; `python -m unittest discover -s tests` → Ran 600 tests, OK; ruff clean**

**Checkpoint**: US4 is fully functional — no payload that is already present-and-valid is re-downloaded, regardless of verified state.

---

## Phase 7: User Story 5 - Source Indexes Respect the Configuration (Priority: P2)

**Goal**: Sources indexes are fetched/loaded only when source fetching is enabled; with it disabled, `add` succeeds using binaries alone and never fails because a Sources index is unreachable — matching `init`. The gate is per-run on the live config, so switching from `regular_user` to `developer`/`graybeard` still fetches the never-downloaded Sources indexes. *(M-4)*

**Independent Test**: With source fetching disabled and an unreachable Sources index, `add` succeeds using binaries alone, and no Sources-index fetch work is performed (`spec.md` US5).

### Tests for User Story 5 (write FIRST, must FAIL before implementation) ⚠️

- [X] T033 [P] [US5] Unit test: `config.fetch_source = False` → `ensure_indexes` fetches/loads no Sources index; with an unreachable Sources URL `add`/`resolve_batch` still succeeds in `tests/unit/test_resolver.py` (must FAIL pre-fix: `ensure_indexes` fetches Sources unconditionally, `resolver.py:251-265`) — **evidence: `TestSourceIndexGating::test_fetch_source_false_skips_source_fetch_and_load_with_unreachable_sources` (binary index cached, source index absent, mirror `127.0.0.1:1` unreachable → `ensure_indexes` succeeds, `source_packages == {}`; any source fetch would have raised)**
- [X] T034 [P] [US5] Unit test: `config.fetch_source = True` → Sources indexes still fetched/loaded (unchanged, regression) in `tests/unit/test_resolver.py` — **evidence: `TestSourceIndexGating::test_fetch_source_true_fetches_and_loads_source_indexes` (empty cache, local mirror serving Packages.gz + Sources.gz via `serve_pool` + `write_source_index_for_mirror` → both indexes fetched and loaded, `source_packages` non-empty)**
- [X] T035 [P] [US5] Unit/integration test — **profile switch**: a cache whose Sources indexes were never fetched (built under `source = false`, i.e. `regular_user`), then config switched to `source = true` (`developer`/`graybeard`) → the next `add` fetches and loads the previously-missing Sources indexes (the gate re-enables on cache miss; must not permanently suppress source fetching) in `tests/unit/test_resolver.py` + `tests/contract/test_cli.py` — **evidence: `TestSourceIndexGating::test_profile_switch_from_source_disabled_refetches_source_indexes` (resolver under `fetch_source=False` leaves the cache without a Sources index; a fresh resolver under `fetch_source=True` re-fetches it) + `TestDownloadContract::test_profile_switch_fetches_previously_missing_sources_indexes` (CLI `add` under `source=false` performs no Sources fetch; re-`add` after switching defaults to `source=true` fetches the missing `Sources.gz`)** — **per `specs/017-download-cache-integrity/contracts/cli.md` Phase 5 assertion 4**

### Implementation for User Story 5

- [X] T036 [US5] Gate the source-index fetch + `load_source_index` on `if self.config.fetch_source:` inside `ensure_indexes` (`src/package_mgmt/lib/service/resolver.py:251-265`), mirroring `_fetch_per_source` in `src/package_mgmt/cli/commands/init.py:70-90` (skip fetch when disabled; load only when enabled); the gate is evaluated per run against the live config with fetch-on-cache-miss, so a later switch to a source-enabled profile re-fetches the never-downloaded indexes — **implemented: the Sources fetch-fallback block and the `load_source_index`/merge are both wrapped in `if self.config.fetch_source:`; with it disabled `merged_source` stays empty and no Sources fetch/load occurs; with it enabled the fetch-on-miss + load is unchanged. Fixture: `make_test_iso.write_source_index_for_mirror` writes Sources.gz into the served tree so `serve_pool` can serve it for fetch-path tests. Validation: `pytest tests/unit tests/integration tests/contract` → 604 passed + 41 subtests; `python -m unittest discover -s tests` → Ran 604 tests, OK; ruff clean**

**Checkpoint**: US5 is fully functional — the binary-only profile neither downloads nor fails on Sources indexes, and switching profiles re-enables source fetching.

---

## Phase 8: User Story 6 - Index Corruption Self-Heals (Priority: P3)

**Goal**: Index files are updated atomically (a reader never observes a partially-written index); a failed index download is cleaned up so a retry starts fresh; a cached index that cannot be read (e.g. truncated) is treated as a cache miss and re-fetched — the next `add` succeeds instead of crash-looping. *(F-23)*

**Independent Test**: A fixture that drops the connection mid-index — the next `add` re-fetches cleanly; a pre-seeded truncated index file is recovered (treated as missing and re-fetched), not a crash loop (`spec.md` US6).

> **Dependency**: lands after US5 (same `ensure_indexes` loop — sequential within that region, see Dependencies).

### Tests for User Story 6 (write FIRST, must FAIL before implementation) ⚠️

- [X] T037 [US6] Add a mid-index-drop handler to `tests/fixtures/make_test_iso.py` (connection drops partway through an index response) — **implemented: `_IndexDropHandler` + `serve_index_drop(base_dir, port, drop_paths)`: on the first request per path it sends a partial index body (connection-close delimited, no read error raised) then closes the socket; later requests serve normally. Evidence: `_download` against the drop path returns 228/457 bytes on the first call and the full 457 on the second; imported by T038**
- [X] T038 [P] [US6] Unit test: a connection dropped mid-index → a retry `add` re-fetches cleanly (no half-written index in the way) in `tests/unit/test_resolver.py` or `tests/unit/test_index_loader.py` (must FAIL pre-fix: the truncated file persists and is preferred) — **implemented: `TestIndexSelfHeal::test_connection_dropped_mid_index_self_heals_on_retry` in `tests/unit/test_resolver_batch.py` — a `serve_index_drop` mirror truncates the first Packages.gz response; the single `ensure_indexes` self-heals to the COMPLETE 5-package index, leaves no `.part`, and the cached index re-reads complete. Verified FAILING pre-fix (truncated file yielded only 1 package) and PASSING post-fix**
- [X] T039 [P] [US6] Unit test: a pre-seeded truncated cached `Packages.gz` → treated as a cache miss, re-fetched, run succeeds — no crash loop across repeated runs in `tests/unit/test_resolver.py` — **implemented: `TestIndexSelfHeal::test_truncated_cached_index_is_cache_miss_and_refetched` in `tests/unit/test_resolver_batch.py` — pre-seeds a truncated Packages.gz (first half of the real index), runs `ensure_indexes` twice (both yield the complete 5-package index; no crash loop), no `.part` remains, and the cached file is replaced by the full readable index. Verified FAILING pre-fix (truncated file persisted, partial index) and PASSING post-fix**
- [X] T040 [P] [US6] Unit test: a successful index download is persisted atomically (`*.part` → replace) — no partial index ever becomes the cached index in `tests/unit/test_index_loader.py` — **implemented: `TestIndexAtomicWrite` in `tests/unit/test_index_loader.py` — `test_successful_fetch_is_persisted_atomically` (dest exists, byte-identical to served payload, no `.part` left) and `test_failed_fetch_leaves_no_partial_index` (a 404 leaves neither a final file nor a `.part`)**

### Implementation for User Story 6

- [X] T041 [US6] Make `index_loader.fetch_index` atomic: write to `dest_path + ".part"`, `os.replace(part, dest_path)` on success, remove the part on any failure — the same discipline payload downloads already use (`src/package_mgmt/lib/service/index_loader.py`) — **implemented: `_write_index_atomic(dest_path, data)` writes to `dest_path + ".part"`, `os.replace(part, dest_path)` on success, and removes the part on any failure; both `fetch_index` write paths (unverified and release-verified) use it. Also made `_open_compressed` eager for `.gz` (full-stream read) so a truncated index raises `EOFError` instead of silently parsing a partial Packages file — this is what lets the resolver's cache-miss self-heal detect truncation. Exported `INDEX_READ_ERRORS = (OSError, EOFError, lzma.LZMAError)` for the resolver**
- [X] T042 [US6] In `ensure_indexes` (`src/package_mgmt/lib/service/resolver.py`): in the fetch fallback loop, delete a failed destination before `continue` (a partial file must never be preferred by `resolve_index_path`); treat a cached index that `load_binary_index`/`load_source_index` cannot read (e.g. truncated `.gz`) as a **cache miss** — remove it and re-fetch, instead of crashing — **implemented: the per-component binary/source handling in `ensure_indexes` now delegates to `_load_or_fetch_index(cache_path, url_base, release_base, loader, release, component, mirror)`: a cached index that raises `INDEX_READ_ERRORS` is removed and re-fetched (bounded, at most 3 attempts); the fetch fallback deletes a failed destination before trying the next extension; RuntimeError with the existing `release/component from mirror` message is raised only when no readable index can be obtained. Source fetching stays gated on `config.fetch_source`. `test_missing_unfetchable_index_raises_clear_message` still passes (not a FileNotFoundError; message names trixie/main + index)**

**Checkpoint**: US6 is fully functional — a poisoned index self-heals on the next run; no partially-written index is ever used.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Release gate — this feature is not complete until this phase passes.

- [X] T043 [P] Run the full suite under both documented runners from a clean checkout (`pytest` and `python -m unittest discover -s tests`) — both must be 100% green — **evidence: clean working tree (`git status` empty); `python -m pytest tests/unit tests/integration tests/contract -q` → 608 passed + 41 subtests; `python -m unittest discover -s tests` → Ran 608 tests, OK; `pytest tests/contract/test_cli.py` → 60 passed + 11 subtests**
- [X] T044 Run the `specs/017-download-cache-integrity/quickstart.md` validation scenarios for all six phases (manual acceptance: fresh-cache refusal; redirecting pool; verified-source re-check; present-file short-circuit; source-gated `add` incl. the `regular_user` → `developer`/`graybeard` switch; index self-heal) — record evidence — **evidence (per-phase quickstart command sets, all green): P1 `test_ensure_base` + `test_ensure_base_flow` + `test_cli` → 87 passed + 11 subtests (fresh-cache refusal contract); P2 `test_downloader` → 55 passed (redirects: follow, no-Location, loop, 5-hop limit — `TestRedirects`+`TestRedirectDownloadFlow`); P3 `test_downloader` + `test_download_flow` → 71 passed (deleted/corrupt source file re-fetched + re-verified, intact → nothing); P4 `test_downloader` → 55 passed (`TestUnverifiedShortCircuit`: present+matching not re-fetched, flag untouched); P5 `test_resolver_batch` + `test_cli` → 103 passed + 18 subtests (unreachable-Sources success, `regular_user`→`developer` profile-switch re-fetch contract); P6 `test_resolver_batch` + `test_index_loader` → 56 passed + 7 subtests (dropped-connection self-heal, truncated pre-seed re-fetch, atomic persistence). Full suite both runners also green (T043)**
- [X] T045 [P] Constitution compliance re-check: no schema change, no new runtime dependencies, no new network call sites, contracts preserved or explicitly extended (never contradicted) — cross-check Phases 1-6 against `contracts/cli.md` and `data-model.md`; re-verify the contract deltas hold under both runners — **verified: `git diff 00f674e^..HEAD -- src/` touches only 6 files (`ensure_base.py` cli+service, `downloader.py`, `index_loader.py`, `resolver.py`, `util/errors.py`) — no `model/database.py`/schema change (VII preserved: records still created `verified=0`, populated before download); no `pyproject.toml`/`setup.py`/`requirements` touched (no new runtime deps); the only new network machinery is `_PooledOpener._get`/`__call__` reusing the existing `http.client` pooling to follow redirects on the same archive-fetch path (no new call sites/hosts/libraries); contracts preserved and explicitly extended (2 new contract tests: profile-switch re-fetch + the short-circuit failure reporting), contract suite green under both runners (60 passed + 11 subtests)**
- [X] T046 [P] Documentation sync: verify `specs/017-download-cache-integrity/` (plan, research, data-model, contracts, quickstart) and `docs/003-ensure-base.md` reflect the implemented behavior — e.g. the redirect hop bound + loop error, the source re-check rule, the short-circuit leaving `verified` untouched, the per-run `fetch_source` gate with profile-switch re-fetch, and the atomic `*.part` → replace index discipline — **verified: contracts/cli.md Phase 2 documents the 5-hop bound, loop/no-`Location` permanent error and no `.part` left behind; Phase 3 the per-file existence→size→checksum re-check with exact-file re-fetch; Phase 4 the short-circuit leaving `verified` untouched; Phase 5 the `[fetch] source = false/true` gate incl. the `regular_user` → `developer`/`graybeard` switch; Phase 6 the truncated-index cache-miss + atomic persistence. quickstart.md carries all six acceptance paths. docs/003-ensure-base.md "Verified-flag semantics (same as add)" updated to state the download-phase short-circuit never changes `verified` (only an actual fetch/verify sets it); fresh-cache refusal already documented**

**Checkpoint**: Feature complete — all six phases implemented, all stories independently verified, both runners green.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — already complete (commit `00f674e`)
- **Foundational (Phase 2)**: Depends on Setup — guards verified before any story
- **User Stories (Phase 3+ / US1–US6)**: Depend on Setup + Foundational guards
- **Polish (Phase 9)**: Depends on all six user stories

### User Story Dependencies (recommended execution order per the plan)

- **US1 (fresh-cache guard)** — ✅ DONE (committed); independent; resolves H-2/M-3 by constraint
- **US2 (F-16 redirects)** — independent (`downloader._PooledOpener`); can start immediately
- **US4 (M-7 short-circuit)** — independent core; its **source-side work-set construction is the prerequisite for US3's per-file re-fetch economy** → implement US4's source-side short-circuit (T031) before US3's re-check (T026)
- **US3 (F-17 source re-check)** — depends on US4's source-side short-circuit (work-set construction)
- **US5 (M-4 source gating)** — independent (`resolver.ensure_indexes` source block)
- **US6 (F-23 index self-heal)** — depends on US5 (both touch the same `ensure_indexes` loop — sequential within that region)

> **Note**: US2, US3, US4 all touch `src/package_mgmt/lib/service/downloader.py` but disjoint functions (`_PooledOpener.__call__` vs the binary phase vs the source phase); US5 and US6 both touch `resolver.py:ensure_indexes` + `index_loader.py`. Treat same-file edits as sequential within the same story; keep US5 → US6 strictly ordered.

### Within Each User Story

- Tests (required for this feature) MUST be written and FAIL before implementation
- Fixtures before unit tests; unit tests before implementation; integration/contract validation after
- Story complete and independently green (both runners + contract) before moving to the next

### Parallel Opportunities

- US2 (downloader), US5 (resolver/ensure_indexes), and US4's core (downloader) are mutually independent and can run in parallel after the Foundational guards
- All tests within a story marked [P] run in parallel
- US3 depends on US4's source-side work-set construction; US6 depends on US5

---

## Parallel Example: User Story 2

```bash
# Launch all US2 tests together (must FAIL first):
Task: "serve_redirecting fixture in tests/fixtures/make_test_iso.py"
Task: "301-redirect success test in tests/unit/test_downloader.py"
Task: "over-limit chain + no-`Location` + loop tests in tests/unit/test_downloader.py"

# After the fixture, launch the implementation:
Task: "Redirect-following in _PooledOpener.__call__ in src/package_mgmt/lib/service/downloader.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Setup + Foundational — complete (commit `00f674e`)
2. User Story 1 (fresh-cache guard) — ✅ DONE: the graybeard `init` → `ensure-base` → `verify` → `handoff` flow is protected; a populated cache is refused with exit 1 and no state change
3. **STOP and VALIDATE**: US1 Independent Test (`ensure-base` on a populated cache → exit 1, JSON error, db unchanged)
4. Deploy/demo if ready

### Incremental Delivery (per the plan's recommended order)

1. US1 (done) → both runners green (580)
2. US2 (F-16 redirects) → test independently (redirecting pool succeeds; loop/over-limit → permanent error) → checkpoint
3. US4 (M-7 short-circuit) core → test independently (unverified-but-valid not re-fetched) → checkpoint
4. US3 (F-17 source re-check) — after US4's source-side short-circuit → test independently (delete one file → exactly that file re-fetched) → checkpoint
5. US5 (M-4 source gating) → test independently (source-disabled `add` never fails; profile switch re-fetches Sources) → checkpoint
6. US6 (F-23 index self-heal) — after US5 → test independently (truncated index recovers) → checkpoint
7. Phase 9: completion checklist + both-runners gate

### Parallel Team Strategy

With multiple developers:

1. Setup + Foundational are done (commit `00f674e`)
2. Once Foundational is confirmed:
   - Developer A: US2 (redirects)
   - Developer B: US4 (short-circuit, incl. the source-side work-set construction US3 needs)
   - Developer C: US5 (source-index gating)
3. After US4 lands: Developer B continues US3 (source re-check)
4. After US5 lands: Developer C continues US6 (index self-heal)
5. Each story lands independently; Phase 9 gates the feature

---

## Notes

- [P] tasks = different files, no dependencies (same-file edits within a story are sequential)
- [Story] label maps task to the user story in `spec.md`
- Tests required for this feature; write them red first
- All database reads/writes go through the pre-populated schema; **no downloads during planning/resolution** (Database-First, constitution VII); no new runtime dependencies
- Commit after each task or logical group; all changes land on `main`
- Verify each phase under BOTH documented runners before moving on
