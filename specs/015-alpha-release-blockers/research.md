# Research: Alpha Release Blockers

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

**Sources**: The defect evidence and fix directions below are consolidated from
`review/02_alpha_kimi_k3/ALPHA_REVIEW.md` + `ALPHA_REVIEW_FINDINGS.md` and
`review/03_alpha_glm_5_3/ALPHA_REVIEW.md`, re-verified against the code at base commit `995cf3e` during
planning. No open technical unknowns remained for Phase 0 (the reviews resolved them); this file records the
**decisions** this feature takes on top of that evidence.

Each phase is a separate workstream (the phases share only "all are blockers"), so decisions are grouped per phase.

---

## Phase 1 — Complete Runtime Closures (Pre-Depends)

### D1: Merge `Pre-Depends` into the resolution dependency set (and emit it faithfully)

- **Decision**: Read `Pre-Depends` in `load_binary_index`, add `BinaryPackage.pre_depends_raw`, merge
  `Depends + Pre-Depends` (deduplicated) into the dependency set the closure walk resolves, **and** emit a
  faithful `Pre-Depends:` stanza in the generated handoff/diff `Packages.gz` when present.
- **Rationale**: FR-001/FR-002 require the closure to include every hard runtime requirement. The resolver already
  parses `binary_entry.depends_raw` (`resolver.py:707-711`), so merging at load time needs no resolver change.
  Emitting the field faithfully preserves dpkg's install-before-configure ordering guarantee on the operator side —
  `Depends:` alone would make installability work but would lose the ordering semantics.
- **Alternatives considered**:
  - *Merge-only (no faithful emission)*: minimal, but the operator's dpkg would not see `Pre-Depends`, silently
    losing ordering guarantees. Rejected for correctness.
  - *Dedicated `pre_depends` column in the DB*: schema change — rejected (spec 010/011 freeze). The merged set is
    stored in the existing `dependencies` JSON like today.

### D2: Pre-Depends census before/with the fix

- **Decision**: Run the census from the review (which `Pre-Depends` targets are *not* also in `Depends`/`Provides`)
  on the alpha package set, as part of the phase's acceptance evidence.
- **Rationale**: Sizes the real-world impact and proves the fix changes the closure for real packages, not just the
  fixture.
- **Alternatives considered**: skipping the census and shipping the fixture-only fix — cheaper but leaves the
  severity claim unmeasured. Kept the census (it is cheap and is in the spec's completion checklist).

---

## Phase 2 — Order-Independent Source Coverage (resolver cluster)

### D3: In-memory minimum-level tracking with a source-repair path

- **Decision**: Add `_batch_levels: dict[str, int]` to `PackageResolver` (min level per name, in-memory, batch
  scope); on re-encounter at a strictly smaller in-scope level, re-run the source-registration block for a binary
  with no source yet. Extract `resolver.py:742-771` into a helper (all upserts idempotent).
- **Rationale**: The spec's semantics are *minimum graph distance* (US1: "the requested binary and its direct
  runtime dependencies have their sources"); only a per-batch min-level map implements that without a schema change.
  Both reviews independently confirmed the first-visit-wins defect (P0-1/H-1).
- **Alternatives considered**:
  - *BFS-level post-pass before the build-dep pass*: correct but recomputes the whole walk; the min-level map is
    cheaper and localized. Adopted the map.

### D4: Route the cached-virtual fast path behind the in-scope evaluation

- **Decision**: In `_resolve_one_inner`, the cached-virtual early return (`resolver.py:664/668`) must not skip the
  source-creation block when the current encounter is in scope — reuse the D3 level-upgrade path so virtually
  provided binaries can gain sources across runs.
- **Rationale**: FR-004 explicitly requires cross-run repair; the cached fast path otherwise fires every time and
  the source is never created (P0-2, confirmed).
- **Alternatives considered**: persisting per-binary levels in the DB (schema change — rejected); re-running the
  whole source block on every virtual hit (wasteful). Adopted the shared level-upgrade path.

### D5: Re-seed batch state after a segment rollback

- **Decision**: After a segment's `conn.rollback()` (`resolver.py:314-319`), rebuild `_visited` /
  `_batch_registered` / `_batch_constraints` from the DB (re-seed registered current binaries for names seen so
  far) so later segments never treat uncommitted names as resolved (F-14).
- **Rationale**: FR-005; the failure mode is invisible (silent closure holes, `add` exits 0). The walk is
  idempotent per name, so re-seeding is cheap and correct.
- **Alternatives considered**: poisoning the whole batch to fail fast — worse operator experience; re-seeding keeps
  the intended best-effort batch semantics.

---

## Phase 3 — Safe Diff & Delivery Ordering

### D6: Append stanza/manifest only after a successful pool copy

- **Decision**: In `generate_diff`, move the `Packages` stanza and manifest-line append (`handoff.py:1033-1037`)
  to **after** `_copy_verified` succeeds (or retract on failure) — the diff never references a payload it does not
  contain.
- **Rationale**: FR-006. A copy failure currently leaves a stanza+manifest that is later written to `diff/`
  (`1055-1063`), permanently poisoning the operator's checksum gate.
- **Alternatives considered**: cleaning the whole diff on failure — heavier; per-record ordering is sufficient and
  keeps partial-diff semantics for the successful records.

### D7: Advance delivery only on a clean sync result

- **Decision**: In `handle_sync`, call `mark_delivered` only after both `excluded_delivered` and `excluded_pending`
  checks pass (i.e. after `generate_diff` returns a clean result), not before.
- **Rationale**: FR-007. Currently `mark_delivered` runs at `sync.py:79` before the `excluded_pending` check at
  `94`, so a failed sync still advances delivery and dead-ends the next one ("Missing intermediate sync").
- **Alternatives considered**: none — the ordering fix is the minimal correct change and preserves the existing
  exit-code contract.

---

## Phase 4 — Fail Hard on Linkless Media

### D8: Fail hard when the pool cannot be reached through the handoff

- **Decision**: `ensure_pool_link` must capture the `ln -s` exit status; when the link cannot be created (FAT), the
  bootstrap must **fail hard** — exit non-zero with a clear, actionable message, without configuring a broken apt
  source. There is **no fallback**: link works → continue; link fails → hard failure. `handoff_complete` must
  additionally verify the `handoff/pool` link resolves to a directory and the per-release `Release`/`Release.gpg`
  exist.
- **Rationale**: FR-008/FR-009. On FAT the pool is a sibling of `handoff/` on the same media; a linkless `deb` line
  pointing at `file:$handoff_dir` cannot reach `../pool`, so there is no transportable source configuration — the
  only correct behavior is "link works" or "fail hard, never report success".
- **Alternatives considered**:
  - *Fall back to a real-path `deb` line*: the pool lives one level above the handoff; a `file:` source can only
    point at one directory, so this cannot serve both `dists/` and `pool/` without a link. Rejected as non-working.
  - *Copy the pool under `handoff/`*: bloats the handoff and duplicates data. Rejected.
  - *Silently warn and continue*: exactly the current (broken) behavior. Rejected.

---

## Phase 5 — Refreshable Repository Validity

### D9: Impose no expiry; anchor `Date` to generation time; keep `SOURCE_DATE_EPOCH` as explicit opt-in

- **Decision**: (1) Do **not** emit a `Valid-Until` field in the generated `Release` files at all — apt does not
  enforce an expiry when the field is absent (it is optional), so the operator repository never expires. (2)
  `_release_epoch` defaults to `int(time.time())` at generation (never the frozen cache mtime), so the
  repository's `Date` is truthful and the stale-mtime bug cannot creep back if behavior changes later. (3)
  `SOURCE_DATE_EPOCH` remains the explicit opt-in for reproducible (byte-identical) regeneration; invalid values
  keep the existing warning + fallback.
- **Rationale**: FR-010/FR-011. The current mtime anchor (`os.stat(binary_cache).st_mtime`) freezes at first
  handoff, so regeneration can never advance `Date`/`Valid-Until` and every cache hard-expires ~1 year after
  creation; `SOURCE_DATE_EPOCH` can additionally pin a repo to an instantly-dead date. Removing `Valid-Until`
  entirely removes the deadline (no arbitrary window, no maintenance cadence imposed), and dating by generation
  time keeps the field correct and future-proof.
- **Alternatives considered**:
  - *Keep `Valid-Until` but refresh it on regeneration* (the original B-5 direction): still imposes an expiry the
    operator must stay ahead of; a long (e.g. 10-year) window is arbitrary and still a deadline. Rejected in favor
    of no deadline.
  - *Keep the mtime anchor but drop `Valid-Until`*: no expiry, but the `Date` stays frozen/incorrect and the bug
    returns the moment a deadline field is re-added. Rejected — generation-time dating prevents recurrence.
  - *Make byte-identical regeneration the default*: impossible while `Date` reflects generation time. Rejected;
    reproducibility stays an explicit opt-in.

---

## Phase 6 — Clean Machine-Readable Errors & Green Documented Runner

### D10: In `--format json` mode, keep console log text off stderr; add runner-independent test logging isolation

- **Decision**: (1) On the JSON-capable error paths (`config.py:14-21`, `handoff.py` CLI error paths, and any
  other JSON command), route console log output to the log file / suppress it so stderr carries exactly the JSON
  payload. (2) Add logging isolation to the tests so the suite behaves identically under both `pytest` and
  `python -m unittest discover -s tests`.
- **Rationale**: FR-012/FR-013. The 8 unittest failures share one root cause: handlers `logger.error(...)` before
  printing JSON; unittest's default logging (`lastResort`) writes that text to stderr ahead of the payload, while
  pytest's capture swallows it — which is why one runner is green and the documented one is red.
- **Alternatives considered**: changing the payload shape (rejected — contract); globally silencing logging in
  text mode (rejected — loses diagnostics); only fixing tests without fixing the product (rejected — the stderr
  contract bug is real and breaks scripts).
