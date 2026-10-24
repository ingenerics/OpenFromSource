# Implementation Plan: Consolidation Fixes

**Branch**: `007-consolidation-fixes` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/007-consolidation-fixes/spec.md`

## Summary

Remediate the ten highest-value findings of a full-codebase audit of specs 001–006: (1) decouple build-tool resolution depth from the reporting knob, (2) implement circular-dependency detection/annotation, (3) make verify judge only the `current` tree and report real missing build tools, (4) remove arbitrary build-tool linking, (5) implement spec 003 `show --deps` fully, (6) propagate version constraints into real and virtual selection, (7) per-pair duplicate source validation, (8) idempotent provides + resolution hygiene, (9) test/dev infrastructure, (10) contract and Constitution VI reconciliation. No schema version bump; one additive column (`binaries.circular`).

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default)

**Database**: SQLite 3.x — schema version 1 (no bump). Additive DDL: `binaries.circular INTEGER NOT NULL DEFAULT 0`.

**Primary Dependencies**: python-debian (deb822, Version comparison), sqlite3 (stdlib)

**Existing Architecture**:
- `resolver.py`: `PackageResolver` with `_resolve_one` (recursive BFS), `_resolve_build_deps` (depth-capped loop), upserts, virtual resolution helpers
- `verify.py`: `handle_verify_graph` + `_find_unresolved_deps`, `_find_unlinked_binaries`, `_find_circular` (stub), `_find_build_dep_issues`
- `show.py`: `handle_show` + KNOWN_TABLES + `_query_table`; reuses list.py tree builders
- `initializer.py`: `populate_provides_table` (insert-only)
- `config_loader.py`: `_parse_sources` (whole-source dedup)
- `main.py`: `_setup_logging` (ignores config), argparse wiring
- `config.py` (CLI): text/JSON output lacking `fetch_source` (JSON) and `log_dir` (text)
- `add.py`: stats dict printing without virtual count

**What needs to change**:
1. `model/database.py`: +`binaries.circular` column
2. `model/dependency.py`: +`circular` flag on DependencySpec (to_json/from_json)
3. `service/resolver.py`: uncoupled build-dep loop, ancestry-based cycle detection, fallback removal, constraint propagation, superseded cached-virtual rejection, `ResolutionStats`/`ProviderCandidate` types
4. `cli/commands/verify.py`: current-tree-only queries, real circular/missing-build-tool reporting, new `_find_build_dep_issues` signature
5. `cli/commands/show.py`: `--deps` text tree + JSON `dependency_tree` key, joined `virtual_resolutions` view
6. `cli/commands/add.py` + `resolver.py`: virtual count in output
7. `service/config_loader.py`: per-pair (release, component) dedup
8. `service/initializer.py`: DELETE-then-INSERT provides repopulation + orphan cleanup
9. `pyproject.toml` + `.github/workflows/ci.yml` + `docs/001-quickstart-howto.md`: dev extras, CI, bootstrap docs
10. `cli/main.py` + `cli/commands/config.py`: config-driven logging, contract-parity output, aligned virtual error message

**Performance Goals**: No new targets; verify queries gain `status='current'` filters (indexed unique key). Build-tool loop terminates when no new sources appear.

**Constraints**: Schema stays v1; no migration; best-effort continuation preserved; reporting-depth setting keeps documented semantics.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Notes |
|-----------|-------|-------|
| I. Offline-First | ✅ Compliant | Full build-tool resolution strengthens offline completeness |
| II. Dependency Alignment | ✅ Compliant | Constraint-aware selection ensures satisfiable trees |
| III. Binary-Source Consistency | ✅ Compliant | No mapping changes |
| IV. Minimal Local Cache | ✅ Compliant | No new packages cached beyond required trees |
| V. Debian Policy Compliance | ✅ Compliant | No policy-surface changes |
| VI. Code Quality & Type Safety | ✅ Compliant (fixed) | `ResolutionStats`, `ProviderCandidate` replace bare dict/tuples |
| VII. Database-First Resolution | ✅ Compliant | Verify semantics strengthened; no download changes |
| VIII. Documentation & Infrastructure | ✅ Compliant | Contracts updated in same cycle; CI added |
| IX. Incremental Spec Scope | ✅ Compliant | 10 stories / 20 FRs — at the cap, decomposed by phase |

**Gate**: PASS — no violations that require justification.

## Roles Check

- [x] Commands are assigned to the correct persona (admin-only vs operator-only vs shared)
- [x] No persona-dependent behavior leaks across the offline/online boundary
- [x] Cache handoff expectations are reflected in the architecture
- [x] No new personas introduced by this feature

## Project Structure

### Documentation (this feature)

```text
specs/007-consolidation-fixes/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Audit findings and design decisions
├── data-model.md        # Entity/DDL changes
├── quickstart.md        # Validation guide
├── contracts/           # CLI contract definitions
│   └── cli.md
└── tasks.md             # Generated per story
```

### Source Code (files to change)

```text
src/package_mgmt/
├── lib/
│   ├── model/
│   │   ├── database.py       # +binaries.circular
│   │   └── dependency.py     # +DependencySpec.circular
│   └── service/
│       ├── resolver.py       # loop uncoupling, cycles, constraints, types, fallback removal
│       ├── config_loader.py  # per-pair dedup
│       └── initializer.py    # provides repopulation + hygiene
├── cli/
│   ├── main.py               # config-driven logging
│   └── commands/
│       ├── verify.py         # current-tree semantics, real circular/missing counts
│       ├── show.py           # --deps full output, joined virtual_resolutions
│       ├── add.py            # virtual count
│       └── config.py         # fetch_source/log_dir parity
```

## Complexity Tracking

> No constitution violations — this section is intentionally left blank.
