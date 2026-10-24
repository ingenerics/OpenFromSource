# Implementation Plan: Resolver & Dependency Correctness

**Branch**: `main` (work lands on `main` in the same alpha release, after 015) | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/016-resolver-dependency-correctness/spec.md`

**Design context**: This is the first of three follow-up features carved from `review/04_alpha_summary/02_high_priority_followups.md` (per the agreed split: 016 = this, 017 = download/cache integrity, 018 = operator track & CLI/config). All six entries (F-8, F-9+F-10, F-11, F-12, F-13, F-15) are the **Resolver & dependency model** cluster. The review's line numbers refer to base commit `995cf3e`; **015's blocker work has since landed**, so every defect below was re-verified against the **current working tree** and all line numbers are current.

## Summary

Six confirmed resolver/dependency-model defects, all on the admin-side `add`/`verify` path. Unlike 015 (six unrelated blockers), these **share two source files** (`resolver.py`, `dependency.py`) and one fixture pack, exactly as the review's cross-cutting note directs ("cluster the resolver work … one fixture pack"). The plan therefore presents each spec phase with its own Where/Approach/Tests/Risks **and** a recommended implementation ordering that puts the shared dependency-model work first.

| Phase | Entry | Defect | Primary area | Depends on |
|-------|-------|--------|--------------|------------|
| 1 | F-8 | Satisfied alternative leaves the failed primary's error behind → `add`/`--file` reports a resolved group as failed | `resolver.py` alt walk + `add.py` exit mapping | — |
| 2 | F-9+F-10 | Source linkage is version- and release-blind (binNMU/backports → wrong source version, Constitution III violation) | `resolver.py` `_register_source_for` + `repository.py` | — |
| 3 | F-11 | Per-alternative version constraints dropped (model only stores names) | `dependency.py` model + `resolver.py`/`verify.py` walks | — (foundation for P1's alt walk and P5's alt walk) |
| 4 | F-12 | `[arch]` restrictions and multi-token build profiles silently dropped by the parser | `dependency.py` parser | — (same parser/model as P3) |
| 5 | F-13 | `verify` reports version-constrained deps satisfied on mere presence | `verify.py` `_dep_satisfied` | P3 (alt-with-constraint walk) |
| 6 | F-15 | Build-dep pass re-walks every historical source on each `add` | `resolver.py` `_resolve_build_deps` + `upsert_source` | — |

**Clustering & recommended order**: P3+P4 (the parser/model work) should land **first**, because the `Alternative` model they introduce is what P1's alternative-group walk and P5's verify alternative walk both consume, and P3/P4 share one fixture pack (per the review). Then P1, P2, P5, P6 — each independently testable. The spec's phase order is the feature's story order; this plan's per-phase "Depends on" column captures the only hard ordering constraint (P5's alternative walk needs the P3 model; nothing else is blocked).

**Cross-cutting constraints** (all phases): **no schema change** (spec 010/011 freeze — the per-alternative constraint model is internal JSON only, with a backward-compatible reader for existing stored rows), **both documented runners green** (`pytest` and `python -m unittest discover -s tests`), **contracts preserved** (exit codes and `--file` summary shape in `specs/014-base-profiles/contracts/cli.md`; F-8's fix must not change the summary's meaning, only make it truthful).

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default). No new runtime dependencies (stdlib + `python-debian`).

**Database**: SQLite 3.x — **unchanged schema**. P3 changes only the serialized `dependencies` / `build_depends` JSON (internal shape); `from_json` stays backward-compatible with legacy string-form alternatives so existing caches keep working. P6 changes `upsert_source`'s return contract, not the schema.

**Primary Dependencies**: unchanged — `python-debian` (`debian.debian_support.Version` in `versions.py`), `sqlite3`, stdlib. `versions.py` already ships `source_version_from_binary` (binNMU `+bN` stripping) and `satisfies_constraint` — both are reused by P2 and P5, not reimplemented.

**Storage**: unchanged cache layout and DB tables. P2 adds exact-version source lookup capability to the in-memory `RepositoryIndex`; P6 bounds the build-dep query to the current batch.

**Testing**: pytest + `python -m unittest discover -s tests`. Existing scaffolding to extend: `tests/unit/test_resolver_batch.py`, `tests/unit/test_resolver_build_deps.py` (fixture `_binary`/`_source` helpers), `tests/unit/test_virtual_resolution.py`, `tests/unit/test_constraints.py` (constraint-resolution fixtures), `tests/unit/test_verify.py` (`VerifyTestBase`). New: `tests/unit/test_dependency.py` for the parser/model changes (P3/P4). The local mirror fixture `tests/fixtures/make_test_iso.py` (incl. a backports variant) covers integration-style linkage tests.

**Target Platform**: Debian Trixie LTS amd64. Target architecture for P4's `[arch]` filtering defaults to `amd64`.

**Project Type**: CLI tool for offline Debian package cache management (admin builds via `add`/`verify`, operator consumes).

**Performance Goals**: P6 must remove the per-`add` re-walk of every historical source (the O(all-sources) behavior on a mature graybeard cache); it must not regress the single-batch walk. No other phase has a perf budget change.

**Constraints**: schema freeze; offline operator; both runners green; CLI contracts (exit codes, `--file` summary, JSON error shape) preserved; Constitution III (exact binary→source linkage) restored by P2; no silent drops (P4 adds WARNINGs).

**Scale/Scope**: cache with tens of thousands of sources (graybeard); P6 is the phase whose benefit scales with that; all other phases are small, targeted behavior fixes with dedicated regression tests.

### Per-phase technical approach

Each phase states **Where** (current line numbers), **Approach**, **Tests**, and **Risks**. Full defect evidence is in `review/04_alpha_summary/02_high_priority_followups.md`.

#### Phase 1 — Resolved Alternative Groups Exit Cleanly (F-8)

**Where.** `resolver.py` `_resolve_one_inner` alternative walk (`900-935`): when the primary `_resolve_one(dep_name, …)` returns `None`, an error string has already been pushed into `stats["errors"]` (`754`, `773-775`, `782`, `828-834`) by the failed primary attempt; the group is then considered satisfied either because an alternative already exists (`any_satisfied`, `921-930`) or an alternative resolves (`931-935`), but the primary's error is **not retracted**. The polluted error list then drives `add.py:138-143` (`EXIT_ERROR` when `total.errors`) and `_handle_add_files` `185-212` (a request is counted as failed when `stats.errors` is non-empty).

**Approach.** Resolve the alternative group as a unit with error rollback (decision D1):
1. In the `dep_specs` loop of `_resolve_one_inner`, snapshot `error_mark = len(stats["errors"])` **before** the primary `_resolve_one` call (`918`).
2. After the group handling (`919-935`), if the group is satisfied — the primary resolved, or an alternative is already present/satisfied (`any_satisfied`), or an alternative resolves — **truncate** `del stats["errors"][error_mark:]` so the failed primary's spurious error disappears.
3. If the **whole group** fails (no member present/resolved), keep the primary's error (or append a single group error) — the request is genuinely failed.
4. Keep per-segment error attribution untouched: each segment owns its own `stats` dict, and `_segment_failed` bookkeeping (per-name, `700-715`) is unchanged.
5. No change to `add.py` exit mapping — once the error list is truthful, `EXIT_ERROR`/summary math is correct as-is.

**Tests.** `tests/unit/test_resolver_batch.py` (new class) + `tests/unit/test_add_file.py`:
- `a | b` with only `b` present → closure contains `b`, `errors == []`, `resolve_batch` returns an empty error list, `handle_add` exits 0.
- `a | b` with only `b` present via a `--file` manifest → summary shows the request resolved, zero errors.
- `a | b | c` with `b` satisfiable at a constraint-valid version → clean; with `c` only → clean.
- `a | b` with **both** absent → one error for the group, request counted failed.
- Mixed batch: a resolved group next to a genuinely failing segment → errors attributed only to the failing segment.

**Risks.** Low. Watch the `_segment_failed` interplay: the failed primary's name stays in `_segment_failed` after truncation (per-segment attribution), so a later **independent** hard dep on that name within the same segment would early-return without an error (`700-701`). This matches the review's "keep per-segment error attribution" and is acceptable; a regression test pins the group-satisfied case. Must not truncate errors belonging to earlier, genuinely-failed segments — the snapshot is per-spec in the current segment's stats only.

#### Phase 2 — Binaries Link to the Exact Source Version and Release (F-9+F-10)

**Where.** `resolver.py` `_register_source_for` (`426-467`):
- `437`: `src_name, _ = parse_source_field(...)` — the `Source:` version is discarded.
- `439-444`: source is looked up by the **primary** release first (`lookup_source_by_source`), else globally — never by `binary_entry.release`/`component` (F-10, backports binaries search primary-release sources).
- `446-449`: `src_version = src_entry.version` — the index's **newest** version, not the binary's declared/derived one (F-9).
`repository.py` `lookup_source`/`lookup_source_by_source` (`53-67`) return the highest-versioned entry.

**Approach.** Exact-version, release-preferring source selection (decision D2):
1. `_register_source_for`: `src_name, declared_version = parse_source_field(...)`; `expected_version = declared_version or source_version_from_binary(binary_entry.version)` (reuses `versions.py:44-52`, which already strips `+bN`).
2. Add `RepositoryIndex.lookup_source_exact(name, version, release=None, component=None) -> SourcePackage | None` (exact `(name, version)`; optional release/component scoping) in `repository.py`, next to the existing `lookup_source*` methods.
3. Lookup order: (a) exact in `binary_entry.release` + `component`; (b) exact in the primary release (only when different); (c) exact anywhere; (d) **fallback** to newest-in-release (`lookup_source_by_source(binary_entry.release)` then `lookup_source`) — and when a fallback is used because no exact version matched, log a `WARNING` (divergence is never silent, spec FR-005).
4. `src_version`/`src_release`/`src_component` (`446-465`) follow the chosen `src_entry`; when no source exists at all, keep the derived `expected_version` and the binary's release/component (existing behavior) — still no silent link.

**Tests.** `tests/unit/test_resolver_batch.py` (or a new `test_source_linkage.py`) + `tests/unit/test_handoff.py` (backports fixture already at `test_handoff.py:505-507`):
- Sources `{foo 1.0-1, foo 2.0-1}` + binary `foo-bin 1.0-1+b1` with `Source: foo (1.0-1)` → links `foo 1.0-1` (not 2.0-1).
- Binary with no declared `Source:` version and version `x.y-z+b1` → links `x.y-z` (derived, `+b1` stripped).
- Backports binary (`release=trixie-backports`) → source resolved from the backports release, not primary.
- No exact version in any release → newest-in-release used **and** a divergence warning logged; test asserts the warning via `assertLogs`.
- Exact-version source not present at all → existing fallback (derived version + binary's release/component) unchanged.

**Risks.** Low-medium. `parse_source_field` (`resolver.py:120-128`) already returns the optional version — no parser change needed, only call-site usage. Keep `lookup_source_exact`'s semantics tight (exact version match only) so the fallback path is the only place divergence is logged, avoiding double-logging.

#### Phase 3 — Every Alternative Keeps Its Own Version Constraint (F-11)

**Where.** `dependency.py`: `DependencySpec.alternatives` is `tuple[str, ...]` (`6-40`); `parse_depends_line` keeps the constraint only for the first alternative (`73-77`) and pushes only names for the rest. Consumers iterate alternatives as bare strings: `resolver.py:920` (`all_names = [dep_name] + list(spec.alternatives)`), `resolver.py:932-935` (alt resolution with **no** constraint), `resolver.py:585-586` (build-dep alt names), `verify.py:139` (`[a.split(":")[0] for a in spec.alternatives]`), `verify.py:280-294` (build-dep issue walk, `nvc = entry.get("version_constraint") if n == dep_names[0] else None`).

**Approach.** Introduce a per-alternative constraint model (decision D3):
1. New frozen dataclass `Alternative(name: str, version_constraint: dict | None = None, architecture: str | None = None)` in `dependency.py`.
2. `DependencySpec.alternatives: tuple[Alternative, ...]`; `BuildDependencySpec.alternatives` likewise.
3. `parse_depends_line`: capture each alternative's own `:arch`, `(op ver)` constraint, and store an `Alternative` per member (the first alternative still populates the spec's primary fields, for compatibility with existing call sites).
4. `to_json`/`from_json`: alternatives serialize as a list of `{name, version_constraint?, architecture?}` dicts. `from_json` **reads legacy string-form alternatives** (a string element is wrapped as `Alternative(name, None, None)`) so pre-existing stored rows keep loading — no schema change, no forced re-parse.
5. Update consumers to use `Alternative.name` and each alternative's own constraint:
   - `resolver.py:919-935`: build `all_names` from `[dep_name] + [a.name for a in spec.alternatives]`; when resolving an alternative (`932-935`), pass `alt.version_constraint`.
   - `resolver.py:585-586` (`_resolve_build_deps`): a small helper `_alt_names(entry)` that handles both dict-form (new) and string-form (legacy) alternatives from the `build_depends` JSON.
   - `verify.py:139`: `[a.name for a in spec.alternatives]`.
   - `verify.py:280-294` (`_find_build_dep_issues`): give each alternative its own constraint (dict-form) instead of "first gets the constraint, rest get `None`".
6. No DB change; `serialize_depends`/`deserialize_depends` round-trip the new shape.

**Tests.** New `tests/unit/test_dependency.py`:
- `parse_depends_line("foo (= 9.9) | bar (= 2.0)")` → primary `foo (= 9.9)`, one `Alternative("bar", {"operator": "=", "version": "2.0"})`.
- Round-trip: `deserialize_depends(serialize_depends(specs))` preserves every alternative's constraint.
- Legacy string-form alternatives (hand-built old JSON) still deserialize (backward compat).
- Resolution: `foo (= 9.9) | bar (= 2.0)` with only `bar 1.0` in the index → an error, **no** silent `bar 1.0` link (constraint `= 2.0` unsatisfied).
- Resolution: `foo | bar (= 2.0)` with `bar 2.0` → satisfied via `bar` under its own constraint.
- `_resolve_build_deps`: a build-dep alternative with its own constraint is honored (only the satisfying member links).

**Risks.** **Highest-risk phase**: the model change touches every alternative consumer. Mitigations: (a) the backward-compatible `from_json` prevents cache regressions; (b) the fixture pack is built once for P3+P4 (per the review) and reused by P1/P5; (c) run both runners (unittest is sensitive to import-order/state differences). Watch the build-dep JSON (`build_depends` column) — it must round-trip `Alternative` dicts too, and `verify`'s build-dep walk (`240-302`) parses that JSON directly.

#### Phase 4 — Architecture Restrictions and Build Profiles Are Honored (F-12)

**Where.** `dependency.py` `parse_depends_line` regex (`66`): `^([a-zA-Z0-9][a-zA-Z0-9+._-]*)(?::([a-zA-Z0-9]+))?\s*(?:\(([<>=!]+)\s*([^)]+)\))?\s*(?:<(!?\w+)>)?\s*$` — no `[arch]` group, single `<profile>` token only; anything trailing fails the anchored `$` → the whole alternative is silently `continue`d (`68-71`). `gperf [amd64 i386]` and `debhelper-compat (= 13) <!nocheck !cross>` are dropped with zero diagnostics (and are missing from stored `dependencies` JSON, violating spec 002 FR-009).

**Approach.** Extend the parser; filter by target arch; never drop silently (decision D4):
1. Extend the regex to capture an optional arch-restriction group `[<token> <token> ...]` (each token optionally `!`-negated) and **multiple** `<profile>` tokens after the constraint.
2. Store on the spec: `arch_restriction: tuple[str, ...] | None` and `profiles: tuple[str, ...] | None` (add to both `DependencySpec` and `BuildDependencySpec`, with `to_json`/`from_json`).
3. Filtering: give `parse_depends_line` a `target_arch: str = "amd64"` parameter (and thread it through `parse_build_depends_field`). A restriction that **includes** the target arch → keep; one that **excludes** it (negated token for target arch, or a list not containing it) → drop (and drop only then). Multi-profile tokens are evaluated conservatively — over-inclusive (keep the dep); profiles do not gate caching.
4. Never drop silently: any token the extended parser still cannot match → `logger.warning(...)` naming the token, and the **rest of the line still parses** (the failing member is skipped with a warning, not the whole line).

**Tests.** `tests/unit/test_dependency.py` (shared with P3):
- `foo [amd64]` → kept; `foo [i386]` → dropped; `foo [!amd64]` → dropped; `foo [amd64 i386]` → kept.
- `debhelper-compat (= 13) <!nocheck !cross>` → kept with constraint intact and both profile tokens recorded.
- Multiple arch tokens with negation mixed (`[amd64 !i386]`) → kept.
- Unparseable token → `WARNING` logged and the remainder of the line still produces specs.
- `parse_build_depends_field` with a `[arch]`/profile build-dep line behaves the same.

**Risks.** Medium. The regex is the sharp edge — Debian's dependency grammar (arch lists, profiles, multi-token) is gnarly; the conservative direction (over-inclusive) is explicitly safe for a cache (a kept-but-unneeded dep is a small cache cost; a dropped-but-needed dep is a silent hole). Keep the anchored match, but structure it so a malformed member warns instead of aborting the whole line. Verify the existing `test_resolver_batch.py` fixtures still parse unchanged (regression).

#### Phase 5 — Verify Checks Version Constraints, Not Just Presence (F-13)

**Where.** `verify.py` `_dep_satisfied` (`135-157`): the direct-name path (`141-145`) returns `True` on mere presence — `SELECT 1 FROM binaries WHERE package_name = ? AND status = 'current' LIMIT 1` — never evaluating `spec.version_constraint`; only the provides path (`146-156`) checks the constraint. `_find_unresolved_deps` (`92-132`) calls it per dep; `_find_build_dep_issues` (`280-294`) only applies the constraint to the first alternative.

**Approach.** Constraint-aware direct-name satisfaction + alternative walk (decision D5):
1. `_dep_satisfied` direct-name path: select the current binary's **version** (`SELECT version FROM binaries WHERE package_name = ? AND status = 'current' LIMIT 1`) and return `satisfies_constraint(version, spec.version_constraint)`; a too-old/too-new current binary is unsatisfied.
2. Alternative walk: iterate `spec.alternatives` as `Alternative`s (P3), each with its **own** constraint — a member satisfies the group only if a current binary satisfies that member's constraint (P3's `Alternative.version_constraint`).
3. Providers keep the documented over-inclusive behavior: the provides path stays as-is (`146-156`, already constraint-checked) — no narrowing.
4. `_find_build_dep_issues` (`285`, `293`): replace `nvc = entry.get("version_constraint") if n == dep_names[0] else None` with per-alternative constraints from the dict-form JSON (P3).

**Tests.** `tests/unit/test_verify.py` (extend `VerifyTestBase`):
- `app` Depends `libfoo (>= 2.0)` with only `libfoo 1.5` current → `_dep_satisfied` False, `verify --graph` INCOMPLETE, non-zero exit.
- `libfoo (= 1.5)` with `libfoo 1.5` → COMPLETE.
- Alternative group satisfied only by the second alternative (with its own constraint) → COMPLETE.
- Versionless direct dep with any current binary → still satisfied (regression).
- Provider-satisfied dep → still over-inclusive (regression).

**Risks.** Low. The fix tightens the completeness gate toward apt's own install check (spec FR-011); the only behavioral risk is flagging previously-"complete" caches — which is the intended correction. Ensure `verify`'s build-dep walk keeps the over-inclusive provider behavior for tools (it has a separate path, `_linked_binary_satisfies`, unchanged).

#### Phase 6 — The Build-Dep Pass Stays Batch-Scoped (F-15)

**Where.** `resolver.py` `_resolve_build_deps` (`552-643`): `processed_ids: set[int] = set()` (`557`) is per-call, and the candidate query (`568-571`) selects **every** source with `build_depends IS NOT NULL` not in `processed_ids` — every `add` re-iterates all historical sources (steady slowdown on a mature ~25k-source cache) and re-resolves build tools for out-of-scope retained sources after a deeper run.

**Approach.** Process only sources created in the current batch (decision D6):
1. `upsert_source` (`82-99`) reports whether it inserted a new row or updated an existing one — change its return to `(source_id: int, inserted: bool)` (or a tiny result object). Update the single caller `_register_source_for` (`465`).
2. Resolver tracks `self._batch_inserted_sources: set[int]`; `_register_source_for` records the id when `inserted` is True; cleared at batch start (`305-310`).
3. `_resolve_build_deps` (`568-571`) selects only sources whose id is in `self._batch_inserted_sources` (and not yet `processed_ids`) — the query is bounded by the batch's sources, not the whole table. Sources registered **during** the pass (a build tool's own source, via `_register_source_for` inside `_resolve_one`) are added to `_batch_inserted_sources` and picked up by the existing `while True` loop, so transitive build-tool expansion is preserved.
4. Retained sources (pre-existing, `ON CONFLICT`-updated) are never in `_batch_inserted_sources` → never re-processed → a later shallower-depth run resolves zero new build tools for them (retention = keep, not keep extending).

**Tests.** `tests/unit/test_resolver_build_deps.py` (extend):
- Two sequential `add` runs on the same resolver/cache: the second run's `build_tools`/`build_dep_links` counters count only its own sources (assert the second run resolves none of the first run's retained sources).
- Shallow-after-deep: run deep, then shallow over the same retained sources → zero new build tools for retained sources.
- A fresh batch still resolves its full transitive build-tool chain (the `while True` expansion keeps working — regression guard for the bounded query).
- Mid-pass registration: a build tool whose source is registered during the pass is processed in a later iteration.

**Risks.** Medium. The bounded query must not break transitive expansion (the loop's correctness depends on `_batch_inserted_sources` growing during the pass). Watch the interaction with the existing `_resolve_build_deps` error attribution (errors attach to the first segment only, `358-365`) — batch-scoped processing means unresolved-tool errors only surface for batch-created sources, which is the intended scope. `upsert_source`'s return-type change is the only signature change; grep for other callers.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- ✅ **I. Offline-First Availability** — all phases are admin-side resolution/verify fixes that make the cache more complete and truthful before handoff; none introduce network calls; P6 keeps `add` fast on large caches (offline buildability).
- ✅ **II. Dependency Alignment** — P1/P3 restore the intended alternative-group semantics (`foo (= 9.9) | bar (= 2.0)` resolves only against constraints); P5 makes `verify`'s completeness gate match the operator's own apt install check.
- ✅ **III. Binary-Source Consistency** — P2 is a direct restoration: exact source version/release linkage under binNMU/backports (the review names this a Constitution III violation `download`/`verify` both accept today).
- ✅ **IV. Minimal Local Cache** — P4 drops arch-excluded deps (less cache waste) while never silently dropping needed ones; P6 stops re-extending out-of-scope retained sources (scope leak fix).
- ✅ **V. Debian Policy Compliance** — P4 implements Debian's dependency grammar faithfully (`[arch]` groups, multi-token profiles); P2/P3 follow Debian control-field semantics.
- ✅ **VI. Code Quality & Type Safety** — new model types are frozen dataclasses (`Alternative`), typed fields (`tuple[Alternative, ...]`, `arch_restriction: tuple[str, ...] | None`, `profiles: tuple[str, ...] | None`); no `Any`, no implicit dict shapes; the `from_json` legacy wrapper is the one explicit cross-layer adapter.
- ✅ **VII. Database-First Resolution** — schema freeze holds; P3 changes only serialized JSON (internal shape); resolution remains DB-first.
- ✅ **VIII. Documentation & Infrastructure Traceability** — plan/research/data-model/contracts/quickstart ship in this feature; the data-model documents the JSON shape change and backward compatibility.
- ✅ **IX. Incremental Spec Scope** — 6 stories / 13 FRs (within limits); each phase is an independently implementable and testable increment; the plan's ordering note clusters the shared parser/model work without merging phases.

**Gate result: PASS** — no violations requiring Complexity Tracking.

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] Commands remain assigned to the correct persona: all six phases change admin-side behavior (`add`, `verify`, and their `--file`/graph output). The operator is affected only through a more correct, installable cache (no operator-side commands change).
- [x] No persona-dependent behavior leaks across the offline/online boundary — no operator artifact (handoff/diff/bootstrap/apply) changes in this feature.
- [x] Cache handoff expectations are reflected — P2 restores the exact binary→source linkage the handoff/rebuild promise depends on; P5 tightens the pre-handoff completeness gate.
- [x] No new personas or role changes — [roles.md](../memory/roles.md) needs no update.

## Project Structure

### Documentation (this feature)

```text
specs/016-resolver-dependency-correctness/
├── plan.md              # This file (/speckit.plan command output)
├── spec.md              # Feature specification
├── research.md          # Phase 0 output: per-phase decisions (D1–D6)
├── data-model.md        # Phase 1 output: dependency model + JSON shape (no schema change)
├── quickstart.md        # Phase 1 output: per-phase validation guide
├── contracts/           # Phase 1 output
│   └── cli.md           # CLI/output contract deltas (add --file summary, verify status)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

All changes are in existing files; the only new test module is `tests/unit/test_dependency.py`.

```text
# Phase 1 — F-8 alternative-group success
src/package_mgmt/lib/service/resolver.py     # _resolve_one_inner: error_mark snapshot + truncate on group success
tests/unit/test_resolver_batch.py            # + alternative-group success/failure fixtures
tests/unit/test_add_file.py                  # + --file batch: resolved group not counted failed

# Phase 2 — F-9+F-10 exact source linkage
src/package_mgmt/lib/model/repository.py     # + lookup_source_exact(name, version, release, component)
src/package_mgmt/lib/service/resolver.py     # _register_source_for: parse/derive version; release-preferring exact lookup + divergence warning
tests/unit/test_resolver_batch.py            # + source-version/backports linkage fixtures (or new test_source_linkage.py)
tests/unit/test_handoff.py                   # backports linkage assertion (uses existing backports fixture)

# Phase 3 — F-11 per-alternative constraints
src/package_mgmt/lib/model/dependency.py     # + Alternative dataclass; DependencySpec/BuildDependencySpec.alternatives typed; to_json/from_json (+legacy reader); parse_depends_line captures per-alt constraints
src/package_mgmt/lib/service/resolver.py     # alt walk (919-935) + build-dep alt names (585-586) use Alternative + own constraint
src/package_mgmt/cli/commands/verify.py      # _dep_satisfied alt names (139); _find_build_dep_issues per-alt constraints (280-294)
tests/unit/test_dependency.py                # NEW: parser/model round-trip, legacy compat, constraint-resolution fixtures

# Phase 4 — F-12 arch restrictions & build profiles
src/package_mgmt/lib/model/dependency.py     # regex + arch_restriction/profiles fields; target_arch param; WARNING on unparseable tokens
tests/unit/test_dependency.py                # + arch/profile keep/drop/warn fixtures (shared pack with P3)

# Phase 5 — F-13 constraint-aware verify
src/package_mgmt/cli/commands/verify.py      # _dep_satisfied direct-name path checks version; alternatives with own constraints
tests/unit/test_verify.py                    # + too-old/exact/second-alternative/provider fixtures

# Phase 6 — F-15 batch-scoped build-dep pass
src/package_mgmt/lib/service/resolver.py     # upsert_source returns (id, inserted); _batch_inserted_sources; _resolve_build_deps bounded query
tests/unit/test_resolver_build_deps.py       # + two-run counters, shallow-after-deep, transitive-expansion regression
```

**Structure Decision**: no new modules or files required beyond `tests/unit/test_dependency.py`. Every phase modifies existing files and extends existing test modules, keeping the change surface minimal and matching the spec's "no schema change, localized behavior fixes" constraint. The parser/model foundation (P3+P4) is one commit set with one fixture pack, per the review's clustering directive.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No constitution violations — the plan stays within all nine principles and the quality gates; no Complexity Tracking required.
