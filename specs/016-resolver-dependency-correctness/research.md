# Research: Resolver & Dependency Correctness

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

**Sources**: The defect evidence and fix directions below are consolidated from
`review/02_alpha_kimi_k3/ALPHA_REVIEW.md` + `ALPHA_REVIEW_FINDINGS.md` and `review/03_alpha_glm_5_3/ALPHA_REVIEW.md`
via `review/04_alpha_summary/02_high_priority_followups.md`, and were **re-verified against the current working
tree** during planning (015's blocker work has landed, so the review's line numbers are stale; this feature's plan
uses current line numbers). No open technical unknowns remained for Phase 0 — the reviews resolved them. This file
records the **decisions** this feature takes on top of that evidence.

The six entries share `resolver.py` + `dependency.py` and one fixture pack (the review's cross-cutting note); the
decisions are grouped per phase but note the shared parser/model foundation (D3/D4 land together).

---

## Phase 1 — Resolved Alternative Groups Exit Cleanly (F-8)

### D1: Resolve the alternative group as a unit; truncate the primary's spurious error on group success

- **Decision**: In `_resolve_one_inner`'s dependency loop, snapshot `error_mark = len(stats["errors"])` before the
  primary attempt; when the group ends up satisfied (primary resolved, an alternative already present/satisfied, or
  an alternative resolves), truncate `del stats["errors"][error_mark:]`. An error is kept only when the **whole
  group** fails. Per-segment error attribution and `_segment_failed` bookkeeping are unchanged.
- **Rationale**: FR-001/FR-002. The failed primary's error string is appended by the inner resolver before the
  alternative walk runs; the current code never retracts it, so `add` exits 1 and `--file` counts a resolved request
  as failed whenever the first alternative is absent (routine in real archives). Truncation makes the error list
  truthful without changing the primary-first resolution order.
- **Alternatives considered**:
  - *Pre-resolve the whole group before the primary*: changes resolution preference (primary would no longer be
    preferred) and rewrites the walk. Rejected — the snapshot/truncate keeps "primary first, fall back" intact.
  - *Clean errors at the `add.py` boundary*: masks the pollution instead of fixing it; per-segment attribution would
    blur. Rejected — fix at the source of the error.

---

## Phase 2 — Binaries Link to the Exact Source Version and Release (F-9+F-10)

### D2: Exact-version, release-preferring source selection with a logged fallback

- **Decision**: `_register_source_for` parses the `Source:` field's declared version, else derives the expected
  source version from the binary version via the existing `source_version_from_binary` (already strips `+bN`). A new
  `RepositoryIndex.lookup_source_exact(name, version, release=None, component=None)` is used in this order:
  (a) exact in the binary's own release+component; (b) exact in the primary release; (c) exact anywhere;
  (d) newest-in-release fallback — **logging a divergence warning** whenever the fallback is taken.
- **Rationale**: FR-003/004/005. F-9 discards the `Source:` version and links the newest index source; F-10 searches
  the primary release's sources for a fallback-release binary. Under binNMU/backports both produce a wrong
  binary→source link that `download`/`verify` accept — a Constitution III violation. Exact `(name, version)` lookup
  in the binary's own release restores the rebuild promise; the logged fallback satisfies "never silent".
- **Alternatives considered**:
  - *Keep newest-in-release but prefer the binary's release*: still wrong for version-skewed sources (2.0-1 vs the
    binary's 1.0-1+b1). Rejected.
  - *Derive the version only when no `Source:` version exists, then look up globally*: loses release fidelity.
    Rejected — the binary's own release is tried first.
  - *Persist a source-version column*: schema change. Rejected (freeze).

---

## Phase 3 — Every Alternative Keeps Its Own Version Constraint (F-11)

### D3: Introduce an `Alternative` model; backward-compatible JSON reader

- **Decision**: New frozen dataclass `Alternative(name, version_constraint=None, architecture=None)`; both
  `DependencySpec.alternatives` and `BuildDependencySpec.alternatives` become `tuple[Alternative, ...]`.
  `parse_depends_line` captures each alternative's own constraint/arch. `to_json`/`from_json` serialize alternatives
  as `{name, version_constraint?, architecture?}` dicts, and `from_json` **accepts legacy string-form alternatives**
  (a string element wraps as `Alternative(name, None, None)`). All consumers (resolver runtime walk + build-dep walk,
  verify's `_dep_satisfied` and build-dep issue walk) switch to `Alternative.name` and per-alternative constraints.
- **Rationale**: FR-006/007. The model currently stores only alternative names; constraints on any but the first
  member are dropped, so `foo (= 9.9) | bar (= 2.0)` can register `bar 1.0` silently — "every fetched source is
  buildable" (FR-013) no longer holds. A typed per-alternative model with a lenient reader preserves existing caches
  (no schema change, no forced re-parse) while making the constraint loss impossible.
- **Alternatives considered**:
  - *Keep names and add a parallel constraint map*: two sources of truth, fragile. Rejected.
  - *New DB table for alternatives*: schema change. Rejected (freeze).
  - *Force re-parse of stored JSON on upgrade*: breaks offline caches that cannot re-fetch indexes. Rejected — the
    lenient reader is the explicit cross-layer adapter (Constitution VI allows these at I/O boundaries).

---

## Phase 4 — Architecture Restrictions and Build Profiles Are Honored (F-12)

### D4: Extended parser with target-arch filtering and never-silent warnings

- **Decision**: Extend `parse_depends_line`'s regex to capture an optional `[arch list]` group and **multiple**
  `<profile>` tokens; store `arch_restriction: tuple[str, ...] | None` and `profiles: tuple[str, ...] | None` on
  both spec types. The parser gains a `target_arch="amd64"` parameter (threaded through `parse_build_depends_field`)
  and keeps a dependency when the restriction includes the target arch, drops it only when the restriction excludes
  the target arch, and treats multi-profile tokens conservatively (over-inclusive — kept). Any token that still
  cannot be parsed produces a `WARNING` while the rest of the line continues to parse.
- **Rationale**: FR-008/009/010. Today `gperf [amd64 i386]` and `debhelper-compat (= 13) <!nocheck !cross>` fail the
  anchored regex and are silently dropped — missing from the stored `dependencies` JSON (spec 002 FR-009). Keeping
  only arch-excluded deps dropped and warning on unparseable tokens removes the silent-loss path; over-inclusion is
  the safe direction for a cache.
- **Alternatives considered**:
  - *Parse-only + filter at every call site*: more places to apply the target arch, higher risk of a silent gap.
    Rejected — a single parser decision point is auditable.
  - *Reject unparseable lines entirely*: breaks real-world dependency lines and cascades failures. Rejected.
  - *Evaluate profile gating (which profiles are active)*: the cache is deliberately over-inclusive; profile
    semantics would require build-context knowledge the resolver does not carry. Rejected — conservative keep.

---

## Phase 5 — Verify Checks Version Constraints, Not Just Presence (F-13)

### D5: Constraint-aware direct-name satisfaction; alternatives walked with their own constraints

- **Decision**: `_dep_satisfied`'s direct-name path selects the current binary's **version** and returns
  `satisfies_constraint(version, spec.version_constraint)` instead of answering on presence. Alternatives are walked
  as `Alternative`s, each checked against its own constraint. The provides path keeps its existing (already
  constraint-checked) behavior — providers stay over-inclusive, unchanged.
- **Rationale**: FR-011. The current direct-name path returns True for any current binary regardless of the
  constraint, so `app` Depends `libfoo (>= 2.0)` with only `libfoo 1.5` reports COMPLETE — a completeness gate weaker
  than the operator's apt install check. Tightening the direct path makes `verify` honest; the build-dep issue walk
  (`_find_build_dep_issues`) also switches to per-alternative constraints (D3) so it matches.
- **Alternatives considered**:
  - *Check the constraint on the direct path but leave alternatives name-only*: alternatives with constraints would
    still be checked unconstrained. Rejected — D3 makes the per-alternative check natural.
  - *Tighten providers too*: would change documented over-inclusive behavior and risk false INCOMPLETE for valid
    provider setups. Rejected — providers stay as documented.

---

## Phase 6 — The Build-Dep Pass Stays Batch-Scoped (F-15)

### D6: `upsert_source` reports insert-vs-existing; the build-dep pass processes only batch-created sources

- **Decision**: `upsert_source` returns `(source_id, inserted: bool)`; the resolver keeps `_batch_inserted_sources:
  set[int]` (cleared per batch, filled by `_register_source_for` when `inserted` is True), and `_resolve_build_deps`
  selects only sources whose id is in that set (and not yet `processed_ids`). Retained sources (pre-existing,
  `ON CONFLICT`-updated) are never in the set → never re-processed. Sources registered during the pass (a build
  tool's own source) are added to the set and picked up by the existing `while True` loop, so transitive build-tool
  expansion is preserved.
- **Rationale**: FR-012/013. `processed_ids` is per-call and the candidate query walks the whole `sources` table
  (`build_depends IS NOT NULL`), so every `add` re-iterates all historical sources — monotonically slower on a
  mature graybeard cache — and a later shallower run re-extends out-of-scope retained sources. Restricting to
  batch-created sources makes the query bounded and gives retention its intended "keep, not keep extending" meaning.
- **Alternatives considered**:
  - *Snapshot pre-existing source ids at batch start and filter with `NOT IN`*: avoids changing `upsert_source`, but
    `NOT IN (25k ids)` is slow and the snapshot must be exact. Rejected in favor of the explicit insert marker.
  - *Poison the batch when any retained source is touched*: too aggressive; retention is legitimate. Rejected.
  - *Keep re-walking everything but make it idempotent-cheap*: the review's `processed_ids` seeding idea; still
    scans all rows every run. Rejected — the bounded query is the actual perf fix.
