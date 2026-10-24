# 03 — Polish, Backlog & Docs (post-alpha, or low-risk cleanup)

**Source reviews**: `review/02_alpha_kimi_k3/ALPHA_REVIEW.md` (+ `ALPHA_REVIEW_FINDINGS.md`) · `review/03_alpha_glm_5_3/ALPHA_REVIEW.md`
**Base commit**: `995cf3e`. All line numbers refer to this state.

Deferred beyond `01_alpha_blockers.md` and `02_high_priority_followups.md`. Includes the merged low-severity
findings (Kimi `L-1…L-18` + GLM `L-8…L-16`, deduplicated), the documentation refresh, spec/doc drift
reconciliation, and the test-coverage gaps both reviews flagged. Nothing here is a release blocker.

---

## 1. Polish backlog (merged low findings)

Deduplicated between the two reviews. IDs: `P-` + number (this file's own numbering); the review source for each is
given in the last column.

### CLI / exit-code / UX

| ID | Issue | Where | Source |
|----|-------|-------|--------|
| P-1 | `add` maps stale-schema `DatabaseError` to exit 1; every sibling command uses 101 per the contract table. | `cli/commands/add.py:83-88` | Kimi L-1 |
| P-2 | `verify --graph` flag is decorative — `main()` runs the graph verification regardless; bare `verify` behaves identically. Also `verify --package X` bypasses the report-depth filter entirely (no depth clause on the `if package:` branch). | `cli/main.py:120-123,208-215`; `verify.py:96-100` | GLM L-8 ≈ Kimi L-15 |
| P-3 | `systems add ""` and names containing newlines are accepted (spec says "any word"); empty moniker can never be snapshotted; `IntegrityError` paths leak the connection. | `cli/commands/systems.py:26-52,85-107` | Kimi L-9 |
| P-4 | Any `InitError` maps to exit 100 ("permission") even for ENOSPC etc.; unreachable mirror → `init` exits 0 "initialized" with 0 binaries cached (offline tolerance is fine, but the summary should say so). | `cli/commands/init.py:109-112`, `initializer.py:14-21` | Kimi L-10 |
| P-5 | Failed `ensure-base` on a fresh cache still creates the DB — spec 008 US1 AS2 says "exits non-zero without touching the database". | `cli/commands/ensure_base.py:63-67` | Kimi L-11 |
| P-6 | Zero-pending `sync` early-returns without regenerating, leaving a previous non-empty `diff/` on disk (spec 013 AS2 expects an *empty diff*). | `cli/commands/sync.py:60-64` | Kimi L-13 |
| P-7 | `add "vim="` (empty pin) silently unpinned here but malformed in `--file`; `--deps X --status all` precedence undocumented. | `cli/commands/add.py:16-20 vs 43-47`; `list.py:16-28` | Kimi L-15 (partial) |
| P-8 | Unknown config keys are silently ignored — a typo like `max_source_deph` or `[fetch] sources = true` silently falls back to defaults (regular_user). Rejecting unknown keys in known sections would be friendlier for an alpha. | `config_loader.py:185-189` | GLM L-11 |

### Resolver / data model

| ID | Issue | Where | Source |
|----|-------|-------|--------|
| P-9 | Component priority inverted on exact version ties: `reverse=True` on `(version, order)` prefers non-free > contrib > main; spec 006 FR-005 implies main should win. Wrong component → wrong pool path. | `repository.py:6-15, 53-73` | Kimi L-2 |
| P-10 | Build-dep fallback links any current binary by name without re-checking the entry's version constraint — wrong link *and* residual error for one dep. | `resolver.py:468-483` | Kimi L-3 |
| P-11 | Runtime-alternative "already satisfied" probe lacks `status='current'` (the build-dep twin has it); superseded-only rows can suppress alternative resolution. | `resolver.py:796-805` vs `447-455` | Kimi L-4 |
| P-12 | Cached virtual re-resolution ignores same-release provider upgrades after an index refresh (spec 006 FR-005 partial). | `resolver.py:650-668` | Kimi L-5 |
| P-13 | `populate_provides_table` commits inside the caller's transaction (breaks ensure-base's "single transaction" claim) and counts `INSERT OR IGNORE` skips as inserted. | `initializer.py:83` | Kimi L-6 |
| P-14 | `provides` rows from no-longer-configured releases/indexes are never swept; stale providers can be selected, then fail with "not found in index". | `initializer.py:65-85`, `resolver.py:134-146` | Kimi L-7 |
| P-15 | Circular report counts superseded rows; `circular` flag never reset on upsert — `verify` and `list --deps` can disagree. | `verify.py:181-198`, `resolver.py:63-73` | Kimi L-8 |
| P-16 | `_find_current_binary` uses `LIMIT 1` without `ORDER BY` — arbitrary choice if the same package+arch is current in two releases/components. | `resolver.py:46-51` | Kimi L-18 |
| P-17 | `packages.status` never transitions (always `pending`; nothing updates it), and `requested_version` stores the *resolved* version for every transitive dep, so `show`/`list` present misleading "requested" data. | `resolver.py:35-43,705` | GLM L-13 |
| P-18 | `Unresolved build deps` / `Missing build tools` lists are not filtered by `max_build_dep_report_depth` (only the runtime-dep check of build tools is); a strict reading of FR-017 ("`0` = no build-tool levels reported") is not met. | `verify.py:30,240-301` | GLM L-10 |
| P-19 | `validate_absolute`'s `ValueError` is dead code (`os.path.abspath` always returns an absolute path); a relative `base_dir` is silently reinterpreted relative to the CWD instead of being rejected. | `util/paths.py:8-12` | GLM L-16 |

### Downloader / operator

| ID | Issue | Where | Source |
|----|-------|-------|--------|
| P-20 | Server `Retry-After` honored uncapped (86400 s stalls a run for a day); internal backoff is capped at 30 s, the override path is not. | `downloader.py:140-147` | Kimi L-12 |
| P-21 | Cache paths with spaces silently break the one-line `deb` entry (whitespace-delimited format); no guard/diagnostic — mount points like `/media/…/My USB` are plausible on operator media. | `handoff.py` `BOOTSTRAP_SCRIPT` `write_sources_entry` | Kimi L-14 |
| P-22 | Downloader docstring/test-name claim the opposite of the (correct, conservative) behavior re: size-match re-hash. Docs backwards, code right. | `downloader.py:275-284`, `tests/unit/test_downloader.py:849` | Kimi L-16 |

### Tests (cosmetic)

| ID | Issue | Where | Source |
|----|-------|-------|--------|
| P-23 | `TestMirrorNormalization` class header missing — tests live inside `TestProfileFilesAreData` (they run; cosmetic). | `tests/unit/test_config.py:400-475` | Kimi L-17 |

---

## 2. Documentation refresh

GLM §8 (all static-inspection, all still current):

| Doc | Problem | Action |
|-----|---------|--------|
| `docs/001-quickstart-howto.md:91-102,115,209` | Shows `[fetch] source = true` as the default and calls source fetching "(default)" — the shipped default is now `source = false` (regular_user presets). | Correct to the new default; a novice must not believe sources are fetched. |
| `docs/001-quickstart-howto.md:555-559` | "…validates the resolved tree **and pool integrity**" — `verify` never touches the pool. | Correct the claim. |
| `docs/001-quickstart-howto.md:176-179` | Claims init "repopulates the provides snapshot … and sweeps orphaned virtual resolutions" — neither is implemented (see §3). | Correct or implement (see §3). |
| `docs/002-build-dep-depth.md:25,123` | Still documents the OLD convention ("Set to 0 to report all levels"); spec 014 redefined `0` = none, `-1` = all. The code follows the new convention; the doc contradicts both the code and FR-017. | Rewrite to the `-1/0/N` convention. |
| `docs/003-ensure-base.md:66` | "nothing sets `verified = 1` today" — stale since spec 011 (`download` sets it). | Correct. |
| `AGENTS.md` | SpeCKIT block says "Run `/speckit.tasks`" — all tasks are already checked. | Refresh the block. |
| `tests/REGRESSION.md` | Baseline still the pre-014 number (458); current suite is 505 (+41 subtests) and post-fix counts will differ. | Refresh after the fix batches. |

---

## 3. Spec / doc drift reconciliation

GLM L-14 — accepted but undocumented drift; reconcile either code or docs/specs:

- Spec 001's data model requires `verified` columns on `packages` and `binary_to_source` — **absent** from
  `model/database.py`.
- Spec 007 US8's intended `DELETE`-then-INSERT provides rebuild and the orphaned-`virtual_resolutions` sweep are
  **not implemented**: `populate_provides_table` is `INSERT OR IGNORE`, and there is no `DELETE FROM` anywhere in
  `src/`. `docs/001-quickstart-howto.md:176-179` still promises both.
- Practical risk is low (snapshot-in-time indexes) — decide explicitly: implement the rebuild + sweep, or amend the
  docs/spec to the implemented behavior.

---

## 4. Test-coverage gaps to close alongside the fixes

Both reviews called out tests that would have caught the P0/P1 bugs; add them with the fix that they pin:

| Gap | Catches | Add with |
|-----|---------|----------|
| No diamond/multi-path depth test (all depth fixtures are chains/trees) | B-2a (P0-1/H-1) | `tests/unit/test_resolver_batch.py` (the SC-006 matrix's missing rows) |
| No virtual-first-deep-then-shallow cross-batch test | B-2b (P0-2) | `tests/unit/test_virtual_resolution.py` |
| No test with `verified = 1` rows present before an `ensure-base` re-run | H-2 | `tests/unit/test_ensure_base.py` |
| No test for ensure-base over a cache holding a different current version | M-3 | `tests/unit/test_ensure_base.py` |
| No test that a *direct* dependency version-constraint violation is reported by `verify` (only provider cases covered) | F-13 / M-6 | `tests/unit/test_verify.py` |
| No test that `verify`/`list`/`show` fail gracefully on a missing DB | F-22 / M-5 | `tests/unit/test_verify.py`, `test_list.py` |
| No Pre-Depends fixture | B-1 | `tests/fixtures/signed_archive.py` + integration |
| No shell-level test running generated `apply.sh` asserting clean stderr | F-19 | `tests/unit/test_apply_script.py` |
| No redirect / truncated-index / mid-index-drop fixtures | F-16, F-23 | `tests/unit/test_downloader.py`, `test_index_loader.py` |
| No component-drop exclusion test for handoff | F-18 | `tests/unit/test_handoff.py` |

---

## 5. Process reminders (from the reviews)

- After every fix batch, run **both** `pytest` and `python -m unittest discover -s tests` — a green pytest alone
  currently proves less than it should (B-6).
- Keep SDD artifacts truthful: where specs/docs promised behavior the code must now gain, update the spec
  clarifications/docs alongside the fix (the project's own AGENTS.md rule).
- Refresh `tests/REGRESSION.md` after each batch so the next feature has an accurate baseline.
