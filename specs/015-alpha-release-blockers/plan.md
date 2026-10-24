# Implementation Plan: Alpha Release Blockers

**Branch**: `main` (no feature branch; work lands on `main` before the alpha tag) | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/015-alpha-release-blockers/spec.md`

## Summary

Six independent alpha-blocking defects, each with a distinct technical character, must be fixed before the
project is tagged alpha. The **only thing the phases share is that each is a blocker** — the phases do not build
on one another and touch disjoint code paths, so this plan deliberately treats **each phase as its own coherent
workstream** with its own research decision, design, artifacts, and tests. They are grouped in one spec only
because all six must land before the release.

| Phase | Blocker | Nature | Primary area |
|-------|---------|--------|--------------|
| 1 | B-1 `Pre-Depends` never parsed | data-model / resolution | `index_loader.py`, `resolver.py`, `handoff.py` (Packages writer) |
| 2 | B-2 resolver depth & batch consistency (P0-1/H-1 + P0-2 + F-14) | resolver algorithm (in-memory batch state) | `resolver.py` |
| 3 | B-3 diff & delivery ordering | state-transition ordering | `handoff.py` (`generate_diff`), `cli/commands/sync.py` |
| 4 | B-4 bootstrap on linkless media | generated shell script | `handoff.py` (`BOOTSTRAP_SCRIPT`) |
| 5 | B-5 frozen `Date` / imposed expiry | generated repository artifact | `handoff.py` (`_release_epoch`, `_release_text`) |
| 6 | B-6 non-JSON stderr + red documented runner | CLI error contract / CI | `cli/commands/config.py`, `cli/commands/handoff.py`, test isolation |

Cross-cutting constraints that apply to all phases: **no schema change** (spec 010/011 freeze), **contracts in
`specs/014-base-profiles/contracts/cli.md` continue to hold** (or are extended, never contradicted), and every
change lands with its regression tests and is verified under **both** documented runners (`pytest` and
`python -m unittest discover -s tests`).

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default). No new runtime dependencies (stdlib + `python-debian`).

**Database**: SQLite 3.x — **unchanged schema** for all six phases. Phase 2 adds only in-memory batch state; all
other phases are behavior/artifact changes.

**Primary Dependencies**: unchanged — `python-debian`, `sqlite3` (stdlib), `tomllib` (stdlib), stdlib HTTP. The
generated operator artifacts use bash + apt on the operator side (unchanged).

**Storage**: unchanged cache layout. Phase 1 extends the dependency payload stored per binary; Phase 5 changes the
generated `Release` dates; Phases 3/4/6 change artifact/script content and ordering, not layout.

**Testing**: pytest + `python -m unittest discover -s tests` (both runners — see Phase 6). Shell-level tests
source the generated scripts with scratch paths (existing pattern in `test_bootstrap_script.py`,
`test_apply_script.py`). Integration tests use the local HTTP mirror fixture (`tests/fixtures/make_test_iso.py`)
and the hermetic apt harness (`tests/integration/test_handoff_flow.py`).

**Target Platform**: Debian Trixie LTS amd64 — admin-side only; operator consumes generated artifacts unchanged.

**Project Type**: CLI tool for offline Debian package cache management (admin builds, operator consumes).

**Performance Goals**: unchanged. Phase 2 must not regress resolution speed; Phase 3 must not add I/O beyond a
per-record ordering change.

**Constraints**: schema freeze; offline operator; signed Release files; both documented test runners green;
contracts (exit codes, JSON-on-stderr, generated-artifact shapes) preserved or explicitly extended.

**Scale/Scope**: unchanged cache scale; each phase is a small, targeted change with dedicated regression tests.

### Per-phase technical approach

Each phase below states **Where** (file:line at base commit `995cf3e`), **Approach**, **Tests**, and **Risks**.
Fuller evidence for every defect is in `review/02_alpha_kimi_k3/ALPHA_REVIEW.md` (+ `ALPHA_REVIEW_FINDINGS.md`)
and `review/03_alpha_glm_5_3/ALPHA_REVIEW.md`; execution order is `review/04_alpha_summary/01_alpha_blockers.md`.

#### Phase 1 — Complete Runtime Closures (B-1, Pre-Depends)

**Where.** `index_loader.py:45-66` (`load_binary_index` reads only `Depends`); the resolver parses the stored
deps at `resolver.py:707-711`; the handoff/diff Packages writers emit `Depends:` only (`handoff.py:684-699`,
`handoff.py:691`). Zero occurrences of `Pre-Depends` in `src/`.

**Approach.**
1. Add an optional `pre_depends_raw` field to `BinaryPackage` (`model/package.py`).
2. In `load_binary_index`, read `Pre-Depends` alongside `Depends`.
3. Resolution: merge `Depends + Pre-Depends` into the dependency set used by the closure walk (parse the merged
   text with the existing `DependencyGraph.parse_depends_line`), so every hard requirement lands in the cache.
   Duplicates between the two fields must not create duplicate records (deduplicate on parse).
4. Generated artifacts: emit a faithful `Pre-Depends:` stanza in the handoff/diff `Packages.gz` when present, so
   the operator's dpkg preserves install-before-configure ordering (in addition to the merged `Depends:`).

**Tests.** Unit: `load_binary_index` captures `Pre-Depends`; merge produces one entry for a duplicated
Depends+Pre-Depends and one for a Pre-Depends-only target; fixture package with a Pre-Depends-only requirement →
closure includes it. Integration: `add` + `download` the fixture → `verify --graph` COMPLETE; handoff `Packages.gz`
contains the `Pre-Depends:` field; offline install simulation succeeds. Census script (per spec §completion) to
size real-world impact on the alpha package set.

**Risks.** Debian's `Pre-Depends` rarely omits the `Depends` duplicate in practice, but the census decides; the fix
is safe regardless (over-inclusion is correct for a cache). Keeping the emitted field faithful avoids any dpkg
ordering regression.

#### Phase 2 — Order-Independent Source Coverage (B-2, resolver cluster)

**Where.** `resolver.py`: first-visit depth early return `510-516` + source gate `742`; cached-virtual fast path
`638-668`; batch rollback `314-319`.

**Approach.** One coherent change to the resolver's batch walk; no schema change:
1. **Min-level tracking (P0-1/H-1)**: add `_batch_levels: dict[str, int]` recording the minimum level observed per
   name. On re-encounter at a strictly smaller level that is now in scope, re-run the source-registration block for
   a binary that has no source yet (extract `742-771` into a helper; all upserts are already idempotent).
2. **Cached-virtual source gate (P0-2)**: move the cached-virtual early return (`664/668`) behind the in-scope
   evaluation so a virtually-provided binary can gain a source when reached in scope — including on later runs.
3. **Batch rollback hygiene (F-14)**: after a segment rollback, rebuild `_visited`/`_batch_registered`/
   `_batch_constraints` from the DB (re-seed registered current binaries for names seen so far), so later segments
   never treat uncommitted names as resolved.

**Tests.** Diamond fixtures at depths `0/1/N/-1` asserting the **minimum**-level source set (the missing SC-006
matrix rows); virtual-first-deep-then-shallow cross-batch test; monkeypatched `upsert_binary` mid-segment failure →
later segment re-resolves the dep or reports an error, never silently skips.

**Risks.** This is the trickiest logic (recursion semantics). Mitigate by landing the three changes together with
their tests and by leaning on the existing depth-matrix scaffolding (`test_resolver_batch.py`,
`test_virtual_resolution.py`, `test_resolver_build_deps.py`).

#### Phase 3 — Safe Diff & Delivery Ordering (B-3)

**Where.** `handoff.py:1033-1047` (`generate_diff` appends stanza+manifest before `_copy_verified`); on copy
failure the in-memory paragraph/manifest entries are not retracted, then written at `1055-1063`.
`cli/commands/sync.py:65-79` calls `mark_delivered` before the `excluded_pending` check at `94`.

**Approach.** Reorder to validate-then-commit:
1. In `generate_diff`: append the `Packages` stanza and manifest line **only after** `_copy_verified` succeeds
   (or retract on failure) — the diff never references a payload it does not contain.
2. In `handle_sync`: generate the diff, check both `excluded_delivered` and `excluded_pending`, and **only on a
   clean result** call `mark_delivered`. A failed sync leaves delivery state untouched.

**Tests.** Fixture with a missing/unreadable pool file → `sync` exits non-zero, `diff/` contains no stanza/manifest
entry for the failed record, `delivered` markers unchanged; repaired re-run succeeds and advances only the shipped
ids (`test_diff_generator.py`, `test_sync_snapshot_flow.py`).

**Risks.** Low; a strict ordering change with a clear failure-mode test. Watch for the interaction with
`excluded_delivered` (defect state) — that path already returns before `mark_delivered` and must keep doing so.

#### Phase 4 — Fail Hard on Linkless Media (B-4)

**Where.** `handoff.py` `BOOTSTRAP_SCRIPT`: `ensure_pool_link` (`117-124`) ignores `ln -s` failure;
`handoff_complete` (`87-96`) checks `$cache_dir/pool` but not the `handoff/pool` link or the `Release` file;
`write_sources_entry` (`128-141`) writes a `deb` line pointing at `file:$handoff_dir`.

**Approach.** (operator-side shell changes in the shipped template)
1. `ensure_pool_link`: capture `ln -s` exit status; on failure, **fail hard** — exit non-zero with a clear,
   actionable message, without configuring a broken apt source. There is **no fallback**: on media that cannot
   carry a symlink the pool is unreachable through `$handoff_dir` (a linkless `deb` line cannot reach the sibling
   `../pool`), so the only correct behavior is a hard, clear failure.
2. `handoff_complete`: additionally verify the `handoff/pool` link resolves to a directory and the per-release
   `Release`/`Release.gpg` files exist before reporting success.

**Tests.** Shell-level: run the sourced functions with scratch dirs — link-creation failure → non-zero exit with a
clear message and no apt source configured; missing link/Release → `handoff_complete` non-zero. Manual validation:
FAT-formatted loop-device run of a generated `bootstrap.sh` (completion checklist) must fail hard — never report
success with an unreachable pool.

**Risks.** Low; script-level change confined to the template. The FAT loop-device validation is the authoritative
acceptance evidence and is part of the feature's completion checklist.

#### Phase 5 — No-Expiry Repository & Generation-Time Dating (B-5)

**Where.** `handoff.py:644-658` (`_release_epoch`: `SOURCE_DATE_EPOCH` override else `os.stat(binary_cache).st_mtime`)
and `_release_text` (`734-740`, emits `Date` + `Valid-Until = epoch + 365 d`); constant `VALID_UNTIL_DAYS` at
`handoff.py:63`. Test to update: `tests/unit/test_handoff.py:176` currently asserts a `Valid-Until:` line.

**Approach.** (decision D9 — no deadline, generation-time dating)
1. **Drop the expiry**: stop emitting the `Valid-Until:` field in `_release_text` entirely. apt does not enforce an
   expiry when the field is absent (it is optional), so the operator repository never expires. Remove the now-unused
   `VALID_UNTIL_DAYS` constant.
2. **Date = generation time**: `_release_epoch` defaults to `int(time.time())` at generation; keep the
   `SOURCE_DATE_EPOCH` override (explicit opt-in for reproducible, byte-identical regeneration, with `Date` pinned
   to that input). Invalid `SOURCE_DATE_EPOCH` keeps the existing warning + fallback. This removes the frozen-mtime
   anchor so the stale-date bug cannot recur if a deadline field is ever re-added.
3. Update the `_release_epoch` docstring (it currently documents the frozen-mtime behavior as intended) and the
   `apply.sh` template comment at `handoff.py:412` ("Valid-Until is refreshed").

**Tests.** `test_handoff.py`: regenerate → `Date` advances (generation time, no dependence on the cache mtime) and
the `Release` contains **no** `Valid-Until` field (update the assertion at line 176); with `SOURCE_DATE_EPOCH` set →
`Date` pinned and byte-identical across runs; invalid `SOURCE_DATE_EPOCH` → warning + generation-time fallback.
Integration/acceptance: a handoff generated long ago is accepted by apt (no expiry enforced) — covered by the
hermetic apt harness and the manual validation in `quickstart.md`.

**Risks.** Low. Confirm apt accepts a `Release` without `Valid-Until` (it does — the field is optional; absence means
no freshness check) and that no test asserts the field's presence beyond `test_handoff.py:176`.

#### Phase 6 — Clean Machine-Readable Errors & Green Documented Runner (B-6)

**Where.** `cli/commands/config.py:14-21` (`logger.error` before `_print_error`); same pattern in `handoff.py`
CLI error paths; the 8 failing tests are in `tests/contract/test_cli.py`
(`TestConfigContract`, `test_invalid_values_for_both_depth_settings_exit_102`, `TestHandoffGenerator`).

**Approach.** Fix the product defect, then make the runners agree:
1. Product: in `--format json` mode, console log output must not be written to stderr ahead of the JSON payload —
   route the logging calls to the log file (or suppress console logs) on the error paths of `config`, `handoff`,
   `sync`, and any other JSON-capable command.
2. Tests: add logging isolation so the suite behaves identically under both `pytest` and `python -m unittest
   discover -s tests` (root the `lastResort`-handler dependency), so both documented runners are green.

**Tests.** The 8 currently-failing tests pass under both runners; a `--format json` error emits exactly one JSON
document on stderr (asserted in `tests/contract/test_cli.py`).

**Risks.** Low. Must not change the JSON payload shape itself (only remove the noise), and must not suppress
legitimate logs in text mode or in the log file.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- ✅ **I. Offline-First Availability** — Phases 1, 3, 4, 5 directly strengthen offline installability (complete
  closures, applicable diffs, working bootstrap, non-expiring repos); Phase 6 hardens the error contract; none
  introduce network calls.
- ✅ **II. Dependency Alignment** — Phase 1 closes the Pre-Depends hole in the runtime closure (the installability
  guarantee); Phase 2 restores the developer profile's in-scope source coverage (order-independent). Phase 3 keeps
  delivery records aligned with actually-shipped payloads.
- ✅ **III. Binary-Source Consistency** — Phase 2 ensures in-scope binaries get their sources regardless of visit
  order and that virtually-provided binaries can be repaired — strengthening the rebuild promise.
- ✅ **IV. Minimal Local Cache** — no phase adds caching of superfluous packages; Phase 2's minimum-level rule
  prevents both under-fetch (missed in-scope sources) and over-fetch (out-of-scope source work).
- ✅ **V. Debian Policy Compliance** — Phase 1 implements Debian's Pre-Depends semantics faithfully; Phase 4/5 make
  the operator repository standards-correct (reachable pool, valid Release window).
- ✅ **VI. Code Quality & Type Safety** — all changes are typed (`_batch_levels: dict[str, int]`, `pre_depends_raw:
  str | None`, etc.); no `Any`, no implicit dict shapes; follows existing resolver/dataclass conventions.
- ✅ **VII. Database-First Resolution** — schema freeze holds; Phase 2 keeps resolution DB-first and only extends
  in-memory batch state; Phase 1 still populates dependencies before download.
- ✅ **VIII. Documentation & Infrastructure Traceability** — plan/research/data-model/contracts/quickstart ship in
  this feature; Phase 5 updates the `_release_epoch` docstring; docs-refresh backlog items (file 03) are explicitly
  out of scope for this feature but noted.
- ✅ **IX. Incremental Spec Scope** — 6 stories / 13 FRs (within limits), and each phase is an independently
  implementable and testable increment, exactly as the spec intends.

**Gate result: PASS** — no violations requiring Complexity Tracking.

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] Commands remain assigned to the correct persona: Phases 1–3, 5, 6 change admin-side behavior
  (`add`/`ensure-base`/`verify`/`config`/`sync`/`handoff`); Phase 4 changes the operator-side `bootstrap.sh`
  template (shipped in the handoff, run by the operator).
- [x] No persona-dependent behavior leaks across the offline/online boundary — operator artifacts (handoff/diff)
  change only in content (Pre-Depends stanza, bootstrap link handling, Release dates), never in how they are
  produced/consumed.
- [x] Cache handoff expectations are reflected — Phases 3/4/5 harden exactly the handoff/diff/sync flow the
  operator consumes.
- [x] No new personas or role changes — [roles.md](../memory/roles.md) needs no update.

## Project Structure

### Documentation (this feature)

```text
specs/015-alpha-release-blockers/
├── plan.md              # This file (/speckit.plan command output)
├── spec.md              # Feature specification
├── research.md          # Phase 0 output: per-phase decisions
├── data-model.md        # Phase 1 output: state/behavior semantics (no schema change)
├── quickstart.md        # Phase 1 output: per-phase validation guide
├── contracts/           # Phase 1 output
│   ├── cli.md           # CLI error contract (Phases 3 & 6) + delivery-state contract
│   └── artifacts.md     # Operator artifact contracts (Phases 1, 4, 5)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

Changes are small and localized per phase; each is listed with its primary files:

```text
# Phase 1 — Pre-Depends
src/package_mgmt/lib/model/package.py        # + BinaryPackage.pre_depends_raw (str | None)
src/package_mgmt/lib/service/index_loader.py # read Pre-Depends; merge into dependency set
src/package_mgmt/lib/service/resolver.py     # unchanged merge point (parses depends_raw)
src/package_mgmt/lib/service/handoff.py      # emit Pre-Depends: stanza in Packages writers
tests/fixtures/signed_archive.py             # + fixture with Pre-Depends-only requirement

# Phase 2 — Resolver depth & batch consistency
src/package_mgmt/lib/service/resolver.py     # _batch_levels; source-block helper; cached-virtual gate; rollback re-seed
tests/unit/test_resolver_batch.py            # + diamond fixtures (depths 0/1/N/-1)
tests/unit/test_virtual_resolution.py        # + cross-batch shallow-repair test
tests/unit/test_resolver_build_deps.py       # + min-level source assertions

# Phase 3 — Diff & delivery ordering
src/package_mgmt/lib/service/handoff.py      # generate_diff: append stanza/manifest after successful copy
src/package_mgmt/cli/commands/sync.py        # mark_delivered only on clean result (after pending/delivered checks)
tests/unit/test_diff_generator.py
tests/integration/test_sync_snapshot_flow.py

# Phase 4 — Fail hard on linkless media
src/package_mgmt/lib/service/handoff.py      # BOOTSTRAP_SCRIPT: ensure_pool_link fails hard on ln failure; handoff_complete link+Release check
tests/unit/test_bootstrap_script.py

# Phase 5 — No-expiry repository & generation-time dating
src/package_mgmt/lib/service/handoff.py      # _release_epoch: generation-time default; drop Valid-Until; SOURCE_DATE_EPOCH opt-in
tests/unit/test_handoff.py                   # + Date-advances / no-Valid-Until / reproducibility asserts

# Phase 6 — Clean machine-readable errors & green documented runner
src/package_mgmt/cli/commands/config.py      # JSON-mode: logs away from stderr
src/package_mgmt/cli/commands/handoff.py     # same pattern on CLI error paths
tests/contract/test_cli.py                   # + logging isolation; error-stream purity asserts
tests/__init__.py / conftest                 # runner-independent logging isolation (if needed)
```

**Structure Decision**: no new modules or files required beyond the existing layout; every phase modifies existing
files and extends existing test modules. This keeps the change surface minimal for a pre-alpha blocker batch and
matches the spec's "no schema change, localized behavior fixes" constraint.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No constitution violations — the spec/plan stays within all nine principles and the quality gates; no Complexity
Tracking required.
