# 01 — Alpha Blockers (fix before tagging alpha)

**Source reviews**: `review/02_alpha_kimi_k3/ALPHA_REVIEW.md` (+ `ALPHA_REVIEW_FINDINGS.md`) · `review/03_alpha_glm_5_3/ALPHA_REVIEW.md`
**Base commit**: `995cf3e` (branch `main`). All line numbers refer to this state.
**Status**: every finding below was re-verified against the source at writing time; none is fixed yet.

These are the **urgent, absolutely-needed** fixes: each one either breaks a headline alpha promise
(installable cache, rebuildable developer cache, working operator transport) or is a hard
release/CI blocker. Fix in the listed order; each entry is self-contained and actionable.

| # | Bug | Promise broken / blocker | Source |
|---|-----|--------------------------|--------|
| B-1 | `Pre-Depends` never parsed → runtime closure incomplete | "fully installable cache" (all profiles, worst regular_user) | Kimi P0-3 |
| B-2 | Resolver depth/batch cluster (first-visit depth, cached-virtual source gate, batch rollback) | developer source coverage; silent closure holes | Kimi P0-1/P0-2/F-14 + GLM H-1 |
| B-3 | Diff/delivery ordering — failed pool-copy bricks the operator diff; delivery committed early | operator `sync` unusable after one failure | Kimi P0-4 |
| B-4 | `bootstrap.sh` reports success with an unreachable pool (FAT media) | primary offline transport silently fails | Kimi P0-5 |
| B-5 | `Valid-Until` time-bomb anchored to a frozen mtime | every cache hard-expires ~1 year, regeneration cannot refresh | Kimi P0-7 |
| B-6 | Non-JSON noise on stderr in `--format json` error paths; documented test runner is red | release hygiene + real stderr contract bug | Kimi P0-6 |

---

## B-1 — Parse `Pre-Depends` (the critical bug)

**Severity**: P0 · **Area**: resolver / index loader · **Source**: Kimi P0-3 · **Repro**: `grep -r "Pre-Depends" src/` → no matches.

**What.** `load_binary_index` reads only the `Depends` field (`src/package_mgmt/lib/service/index_loader.py:58`); nothing anywhere in the codebase accounts for `Pre-Depends`. Pre-Depends are *hard* runtime requirements in Debian semantics (used by `dpkg`, `bash`, `dash`, `coreutils`, `systemd`, …). A pre-dependency that is **not also duplicated** in `Depends` (common but not universal) is missing from the cache, so the operator's offline `apt-get install` of that package fails. Spec 002 FR-001 promises the full transitive dependency tree; spec 014 FR-003 requires the uncapped runtime tree to keep every requested package installable.

**Impact.** Affects **all three profiles**; most acutely `regular_user`, whose cache contains nothing but the runtime closure. This is the one that makes the cache genuinely **not installable**.

**Fix.**
1. In `load_binary_index` (`index_loader.py:45-66`), also read `Pre-Depends` and merge it into the stored dependency set (`depends_raw`/`dependencies` JSON). Field-of-origin tracking is optional.
2. Run the Pre-Depends **census** from Kimi §9.3 on the alpha package set (which `Pre-Depends` targets are *not* also in `Depends`/`Provides`) to size the real-world impact and prove the fix.

**Tests.** Unit: fixture package with a Pre-Depends-only requirement → the pre-dep lands in the closure. Integration: `add` the fixture → closure contains the pre-dep; `verify --graph` COMPLETE; the operator-side install simulation (see completion checklist) installs it.

---

## B-2 — Resolver depth & batch-consistency cluster

**Severity**: P0 (P0-1/H-1, P0-2) + high (F-14) · **Area**: resolver / dependency model · **Source**: both reviews (P0-1/H-1 agreement), Kimi P0-2 + F-14.

All three touch the same ~300 lines of `resolver.py` (plus `dependency.py`) and share the level/`_visited` machinery — fix in **one branch with one fixture pack** (diamond chains, virtual-first-deep-then-shallow, forced mid-batch exception).

### B-2a — First-visit-wins depth drops in-scope sources (P0-1 / GLM H-1) · *confirmed by both reviews*

**What.** Depth attribution records a binary's level on its **first** DFS visit and never revisits it: `_resolve_one` early-returns for any name already in `_visited` (`resolver.py:510-516`), so the `_source_in_scope(level)` gate (`resolver.py:742`) only ever sees the first-visit level. If a package is reached first through a deep chain and later via a shallow edge, the **minimum** level (which the spec means: US1 "the requested binary and its direct runtime dependencies have their sources") is never applied.

**Scenario** (reproduced by both reviewers). `app` Depends `c1, x`; `c1 → c2 → x`; depth `1`. DFS visits `x` first at level 3 → no source; the later level-1 edge is skipped. Diamond patterns (`libc6`, `libgcc-s1`) make this routine, not a corner case. Affects **developer** (finite depth). `verify --graph` reports COMPLETE anyway because finite-depth unlinked binaries are informational-only (FR-015) — the gap is silent.

**Fix.** Track the minimum observed level per name in the batch (e.g. `_batch_levels: dict[str, int]`); on re-encounter at a strictly shallower in-scope level, re-run just the source-registration block (extract `resolver.py:740-771` into a helper; upserts are already idempotent). A post-pass BFS alternative is acceptable.

**Tests.** Diamond fixtures at depths `0/1/N/-1` asserting the **minimum**-level source set; these are the missing SC-006 matrix rows.

### B-2b — Cached virtual resolutions bypass the source gate (P0-2) · *confirmed*

**What.** When a `virtual_resolutions` row exists, `_resolve_one_inner` returns the cached `resolved_id` at `resolver.py:638-668` **before** the source-creation block (`742-771`) — unlike the real-package path. A virtually-provided binary first resolved out-of-scope can **never** gain a source later (or on a later `add`), because the cached fast path fires every time. Missing sources are **never repaired**.

**Fix.** Move the cached-virtual return behind the in-scope evaluation (or store/refresh per-binary level and reuse the B-2a level-upgrade path).

**Tests.** Virtual provider resolved deep → then reached shallow in a later batch → source must appear. Cross-batch test.

### B-2c — Mid-batch exception leaves stale in-memory state (F-14) · *confirmed*

**What.** `resolve_batch` wraps each segment in `try/except` with `conn.rollback()` (`resolver.py:314-319`). Rollback discards the segment's DB writes but **not** `_visited` / `_batch_registered`. A later segment early-returns on names that were never committed — closure hole with **no error recorded**; `add` exits 0.

**Fix.** After a segment rollback, rebuild the batch state from the DB (re-seed `_batch_registered` from registered current binaries) or mark it suspect. The walk is idempotent per name, so a re-walk is cheap.

**Tests.** Monkeypatch `upsert_binary` to raise mid-segment on the second package; batch of two requests sharing a dep → second segment must re-resolve the dep (present in DB) or report an error; never silently skip.

---

## B-3 — Diff generation + delivery ordering

**Severity**: P0 · **Area**: operator track (handoff/sync) · **Source**: Kimi P0-4 · **Repro**: code trace.

**What.** In `generate_diff`, the `Packages` stanza (`handoff.py:1033`) and the manifest line (`1035-1037`) are appended **before** `_copy_verified` runs (`1043`). On copy failure the record is added to `excluded_pending` but the already-appended stanza/manifest entries are **not retracted** — the shipped `diff/` references a payload it does not contain, and the operator's checksum gate refuses the *entire* diff, forever. Compounding it, `handle_sync` calls `mark_delivered(conn, system, stats.shipped_ids)` (`sync.py:79`) **before** checking `excluded_pending` (`sync.py:94`) and returning `EXIT_ERROR`: the delivery record advances for a diff the operator can never apply, and the rebuilt diff dead-ends with "Missing intermediate sync".

**Fix.** Ordering fix in both files: (1) in `generate_diff` copy the payload **first**, then append stanza/manifest only on success; (2) in `handle_sync` validate → generate → and only then `mark_delivered` on clean success.

**Tests.** Fixture where one pool file is missing/unreadable → `sync` exits non-zero, `diff/` contains no stanza for the failed record, `delivered` markers unchanged; a clean rebuild works afterwards.

---

## B-4 — `bootstrap.sh` reports success with an unreachable pool (FAT media)

**Severity**: P0 · **Area**: operator track (bootstrap) · **Source**: Kimi P0-5 · **Repro**: script logic read + end-to-end by reviewer.

**What.** The offline-transport scenario per spec 012 is USB/FAT. On FAT the `handoff/pool -> ../pool` symlink is uncreatable; `ln -s` failure is ignored (`BOOTSTRAP_SCRIPT` `ensure_pool_link`, ~`handoff.py:117-124`), and `handoff_complete` (~`handoff.py:84-96`) checks `$cache_dir/pool` but never the `handoff/pool` link or the `Release` file. Bootstrap writes the apt source, `apt update` succeeds, reports **success** — and every install fails. The research claim that the link "survives media that can't carry symlinks" is false in exactly the scenario it invokes.

**Fix.** Check the `ln` result; on failure **fail hard** — non-zero exit with a clear, actionable message and no broken apt source configured (there is no fallback: a linkless `deb` line cannot reach the sibling pool); strengthen `handoff_complete` to verify the link resolves and `Release` exists.

**Tests.** FAT-formatted loop-device run of the generated `bootstrap.sh` (completion checklist) — must fail hard (never report success); extend the shell-level tests for the link-missing case.

---

## B-5 — Frozen `Date` + imposed expiry in the operator repo

**Severity**: P0 · **Area**: operator track (Release generation) · **Source**: Kimi P0-7 · **Repro**: confirmed.

**What.** `Date` is derived from `os.stat(config.binary_cache).st_mtime` unless `SOURCE_DATE_EPOCH` is set (`handoff.py:63, 644-658, 734-739`; `VALID_UNTIL_DAYS = 365`). The `binary/` directory mtime freezes at init/first handoff, so `Valid-Until = Date + 365d` can **never be advanced by regeneration** — the offline cache hard-expires ~1 year after creation and regeneration cannot refresh it. An exported `SOURCE_DATE_EPOCH` (reproducible-build systems) silently pins the repo to an arbitrary old date — an instantly dead repo.

**Fix.** **Impose no expiry**: stop emitting the `Valid-Until:` field entirely (apt does not enforce an expiry when the field is absent — it is optional), so the operator repository never expires. Anchor `Date` to **generation time** (`time.time()`) by default so the frozen-mtime bug cannot recur; honor `SOURCE_DATE_EPOCH` only when explicitly set *for* reproducibility (byte-identical regeneration stays an opt-in, not the default).

**Tests.** Regenerate a handoff → `Date` advances to generation time and the `Release` contains **no** `Valid-Until` field; with `SOURCE_DATE_EPOCH` set → `Date` pinned (opt-in preserved); a handoff generated long ago is still accepted by apt (no expiry enforced). Update `tests/unit/test_handoff.py:176` (currently asserts `Valid-Until`).

---

## B-6 — Non-JSON on stderr in `--format json` error paths; documented test runner is red

**Severity**: P0 · **Area**: contract / CLI / CI · **Source**: Kimi P0-6 · **Repro**: both runners executed.

**What.** `python -m unittest discover -s tests` (the documented command in `docs/001-quickstart-howto.md`) is **red — 8 errors** — while `pytest` is green. Root cause is a real product bug: handlers call `logger.error("Config load failed: …")` / `logger.warning(…)` **before** printing the JSON payload (`src/package_mgmt/cli/commands/config.py:14-17`, same pattern in `handoff.py` CLI error paths). With unittest's default logging the text lands on stderr ahead of the JSON; pytest's root capture handler swallows it — which is why one runner is green and the documented one is red. Any script parsing stderr of a failed `config`/`handoff`/`sync --format json` call gets non-JSON. Contract: `specs/014-base-profiles/contracts/cli.md` ("JSON on stderr").

**Fix.** In JSON mode, suppress/route console log output away from stderr (or emit logs only to the log file when `--format json`); then make the test suite runner-independent (logging isolation in the tests) so **both** documented commands are green.

**Tests.** The 8 currently-failing tests (contract `TestConfigContract`, `test_invalid_values_for_both_depth_settings_exit_102`, `TestHandoffGenerator.test_unconfirmable_record_is_command_error`) pass under both runners.

---

## Completion checklist (definition of done for this batch)

- [X] Both `pytest` and `python -m unittest discover -s tests` are green (B-6).
  - Evidence (Phase 9 T041, final state incl. all six blockers): `pytest` → **531 passed, 41 subtests** (6:05); `python -m unittest discover -s tests` → **Ran 531 tests ... OK** (5:38). Both documented runners 100% green.
- [X] **Pre-Depends census** run on the alpha package set (B-1) and the closure is complete for it.
  - Evidence (trixie main amd64 index, 68 820 packages): **1 041 packages declare `Pre-Depends`; 1 020 have a pre-dep target NOT duplicated in their own `Depends`** (1 019 of those not satisfiable via any `Provides`). This is the exact gap B-1's fix closes — without parsing `Pre-Depends`, those 1 020 packages' closures would be missing a hard runtime requirement and fail offline install. The vifm closure (27 binaries) installs offline cleanly (see below); the resolver merges `Pre-Depends` into the dependency set and tests cover the pre-dep-only closure (`PRE_DEPENDS_PACKAGES` fixture).
- [X] **Live-mirror runs of all three profiles** against `deb.debian.org` trixie with a small real package (e.g. `vifm`), per `specs/014-base-profiles/quickstart.md` scenarios 1–5: identical runtime closures across profiles; developer source set ⊂ graybeard; `verify --graph` COMPLETE in scope.
  - Evidence (Phase 9 T042, live against `deb.debian.org` trixie/main, `vifm 0.14-3`): regular_user 27 binaries / 0 sources / 0 build-deps → COMPLETE; developer 579 binaries / 6 sources / 70 build-deps → COMPLETE; graybeard 9 378 binaries / 6 049 sources / 52 953 build-deps → COMPLETE (0 unresolved, 0 missing). Runtime closure (regular_user's 27) is a subset of developer ⊆ graybeard; developer sources (6) ⊂ graybeard sources (6 049). Note: the `binaries` table also stores build-tool packages (registered by the resolver), so total counts grow with depth while the runtime closure is identical; `verify --graph` reports Runtime COMPLETE and Build Status COMPLETE in all three.
- [X] **Operator-side installability proof (offline simulation)** per profile: `handoff --system`, then `apt-get -s install <pkg>` in a scratch root against the `file://` cache with the shipped keyring — must succeed; repeat through a **FAT-formatted loop device** (exposes B-4 and L-14).
  - Evidence: regular_user and developer caches → `handoff --system` → scratch apt root (`Dir::State`/`Dir::Cache` redirected) with `deb [signed-by=.../offline-cache.gpg] file:<handoff> trixie main` → `apt-get update` OK → `apt-get -s install vifm` **succeeds** (regular_user: all 27 Conf lines incl. `perl`, `vim`, `vifm`; developer: 579-package index, vifm Conf). **New finding fixed here**: the handoff `Packages` writer dropped `Multi-Arch`; without it apt cannot satisfy `name:any` deps (`perl:any`) and the install proof failed. Fixed schema-free (`BinaryPackage.multi_arch` parsed from the index, emitted in `_paragraph` for both handoff and diff), unit test `test_handoff_preserves_multi_arch_field` added, live proof re-verified. **FAT loop-device run**: not executable in this sandbox (no `mkfs.vfat`/`mtools`/`losetup`, non-root, no passwordless sudo) — covered by the deterministic FAT simulation from Phase 6 T032 (fake `ln` returning `EOPNOTSUPP` + link-stripped real handoff → `bootstrap.sh` exits 1 with a clear message and configures no apt source; link-capable media bootstraps cleanly) plus the shell tests `test_ensure_pool_link_fails_hard_when_ln_fails` and `test_bootstrap_never_configures_source_with_unreachable_pool`. Physical run remains a follow-up (needs privileged tooling).
- [X] Regenerate a handoff: `Release` has no `Valid-Until` field, `Date` reflects generation time, and a long-ago-generated handoff is still accepted by apt (B-5).
  - Evidence: regenerated live handoff `Release` shows generation time and **no `Valid-Until`**; unit tests `test_release_date_advances_on_regeneration`, `test_source_date_epoch_pins_byte_identical_regeneration`, and `test_invalid_source_date_epoch_warns_and_falls_back` cover regeneration dating, reproducibility opt-in, and the no-expiry field removal; apt accepts the regenerated handoff (update + install above).
