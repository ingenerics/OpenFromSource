# Research: Consolidation Fixes

**Source**: Audit of specs 001–006. Ten action points identified, one per user story in [spec.md](spec.md).

## 1. Build-Dep Resolution Depth vs Reporting Depth

**Problem found**: `_resolve_build_deps()` in `src/package_mgmt/lib/service/resolver.py` runs `for iteration in range(max_depth)` with `max_depth = config.max_build_dep_report_depth` (default 2). This truncates *resolution* — build tools at depth 3+ are never resolved — contradicting docs/002-build-dep-depth.md ("Build dependencies are resolved fully") and the `project.defaults.toml` comment ("Packages deeper than this are still resolved and fetched").

**Decision**: Uncouple. Resolution iterates until no new build-dependency sources remain, bounded by a safety cap (default 100 iterations, warning at cap). `build_dep_depth` continues to be recorded on links (`iteration + 1`), and verify's `MIN(build_dep_depth) <= max_report_depth` filter stays as the only place the setting acts.

**Alternative rejected**: Adding a second `max_build_dep_resolution_depth` config — the constitution and specs want the pool complete; a knob to make it incomplete is harmful.

## 2. Circular Dependency Detection

**Problem found**: `_find_circular()` in `src/package_mgmt/cli/commands/verify.py` returns `[]` unconditionally; the resolver's `_visited` set conflates cycles with diamonds and discards both silently. Spec 002 FR-007 requires detection + annotation.

**Decision**: Track a recursion-path set `_ancestry` in `PackageResolver` (separate from `_visited`). When a dependency name is in `_ancestry`, it's a genuine cycle: mark the closing binary `circular = 1` (new additive column, schema stays v1) and set `circular: true` on that DependencySpec in its serialized JSON. Diamonds (name in `_visited` but not `_ancestry`) remain silently suppressed. Verify reads `binaries.circular = 1`, re-parses dependencies, and reports each flagged spec as `pkg -> dep`.

**Why column + JSON flag**: The column makes cycles queryable for verify at O(rows-with-circular); the JSON flag preserves *which* dependency closed the cycle for future tooling. Both additive; `DependencySpec.from_json` defaults `circular=False`, so existing rows parse unchanged.

## 3. Verify Against the Current Tree

**Problem found**: `handle_verify_graph` counts all binaries rows and `_find_unresolved_deps`/`_find_unlinked_binaries` match `package_name` without `status='current'`. Superseded rows (spec 005) both inflate counts and satisfy unresolved-dep checks, producing false COMPLETE status.

**Decision**: All verify queries filter `status = 'current'`: totals, unresolved checks (including the alternatives branch), unlinked check, and the provides-based provider check (already filtered). "Missing build tools" is computed — build-dep entries with no `source_build_deps` link and no current binary row — replacing the hardcoded `0`. Return tuple of `_find_build_dep_issues` grows to `(total_links, total_build_tools, unresolved, missing)`.

## 4. Arbitrary Build-Tool Linking

**Problem found**: resolver fallback (when `_resolve_one` returns None for a build tool) links the most recent `binaries` row with that name — often a superseded or unrelated row — which both corrupts the graph and hides the gap from verify.

**Decision**: Remove the fallback. On failure: no link, `stats["errors"]` entry, `add` exits non-zero, and verify reports the missing tool via its build_depends-vs-links check. This restores "the database reflects what was resolved."

## 5. show --deps

**Problem found**: `show.py`'s `--deps` only widens the queried package set; spec 003 FR-010/FR-011 (tree appended in text mode; `dependency_tree` key in JSON) were never implemented.

**Decision**: In `handle_show`, when `deps=True`: text mode prints tables then `_print_text_tree(_build_tree(...))`; JSON mode emits `{"tables": {...}, "dependency_tree": tree}`. Reuses the existing list.py builders (spec 003 assumption: reuse).

## 6. Constraint-Aware Selection

**Problem found**: `_resolve_one` never receives the depending spec's `version_constraint`; real-package selection picks highest version/source priority without checking constraints (spec 005 US4 AS3), and virtual provider selection always passes `None` (spec 006 "most restrictive constraint").

**Decision**: Add `version_constraint: dict | None` parameter to `_resolve_one` and `select_provider`. Callers pass `spec.version_constraint`. For real packages, after `lookup_binary_by_source`/`lookup_binary`, filter candidates by `compare_versions(entry.version, op, target)`; if none satisfy, report unsatisfiable. For virtuals, the constraint filters the provides candidates before prioritization. Unparseable constraints → warn, treat as unconstrained.

## 7. Per-Pair Source Validation

**Problem found**: `_parse_sources` deduplicates whole `(release, tuple(components))` source tuples; overlapping component lists (e.g. `main` + `main,contrib`) slip through, violating spec 005 FR-004.

**Decision**: Collect every `(release, component)` pair across all sources into a set; the first repeat raises `ConfigError` naming the pair. Message format: `Duplicate (release, component) pair: (trixie, main)`.

## 8. Provides Idempotency and Resolution Hygiene

**Problem found**: `populate_provides_table` only inserts; re-init never removes stale provides rows. `virtual_resolutions` can orphan (fresh DB) or point at superseded binaries; the cached re-check in `_resolve_one` compares release only.

**Decision**: `populate_provides_table` runs `DELETE FROM provides` then inserts, inside one transaction, only after the index was fully loaded (failed fetch → previous snapshot untouched). Re-init additionally runs `DELETE FROM virtual_resolutions WHERE resolved_binary_id NOT IN (SELECT id FROM binaries)`. The cached re-check also rejects rows whose binary is superseded (query status; if superseded → treat as absent and re-resolve).

## 9. Test Infrastructure

**Problem found**: No pytest installed, no dev extras in pyproject.toml, spec 005 T032 quickstart validation never run. Note: `python3-debian` IS an installed system package (apt `python3-debian 1.0.1`); the suite is unittest-based and runs out of the box via `PYTHONPATH=src python3 -m unittest discover -s tests/unit` — pytest is a convenience, not a requirement (verified: unit 61/61, integration 9/9, contract 5/5 passing on the real module).

**Decision**: Add `[project.optional-dependencies] dev = ["pytest>=8", "ruff"]` as convenience tooling; document the unittest bootstrap in docs/001-quickstart-howto.md; add `.github/workflows/ci.yml` (pytest + ruff on Python 3.13); run and record 005 T032 + 006 quickstart validations.

## 10. Contracts and Constitution

**Problem found**: `add` omits the "(N virtual)" count (contracts 006); `show --tables virtual_resolutions` prints raw `resolved_binary_id` (contract shows joined name/version); `config --format json` omits `fetch_source`; virtual error wording differs from contract ("has no providers in the index"). `resolve()` returns a bare dict and `query_provides`/`select_provider` return bare tuples (Constitution VI). `log_level`/`log_dir` are dead keys.

**Decision**: Add `virtual_count` to resolution stats and print `(N dependencies, M virtual)`; JOIN binaries in `_query_table` for `virtual_resolutions`; add `fetch_source` to JSON config output (and `log_dir` to text output); align error message. Introduce frozen `ResolutionStats` and `ProviderCandidate` dataclasses, converting resolver returns. `_setup_logging` reads config: set level and add a FileHandler under `log_dir` (created if missing).
