# Implementation Plan: Build-Tool Dependencies

**Branch**: `004-build-tool-dependencies` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/004-build-tool-dependencies/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Extend the existing dependency resolution to handle Build-Depends from Sources.gz. Parse build-dependency metadata into the database, resolve build-tool binary packages through the same binaries infrastructure, link sources to their build tools via a new `source_build_deps` table, and extend `verify --graph` to report build-tool cache completeness. No separate cache directory or role column — build tools share the binary cache and are distinguished only by their entries in the link table.

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default), Debian packaging tools (dpkg-dev, debhelper, fakeroot)

**Database**: SQLite 3.x — metadata store with packages, binaries, sources, binary_to_source tables; populated BEFORE downloads. Adds `sources.build_depends` column and `source_build_deps` table.

**Primary Dependencies**: python-debian (for Sources.gz Build-Depends field parsing via deb822), sqlite3 (stdlib), tomllib (stdlib, for TOML config parsing — already used in config_loader.py)

**Storage**: Existing SQLite database — build-tool binaries are recorded as `binaries` table entries alongside runtime packages. No new cache directory, no payload download.

**Testing**: pytest for Python tooling. Existing test patterns (unittest.TestCase, temp dirs, direct function import) apply. New tests needed for: Build-Depends parsing from Sources.gz, source_build_deps upsert, verify output with build-tool section, config loading with `[fetch] source` setting, resolver behaviour when `source = false`.

**Target Platform**: Debian Trixie LTS amd64 / x86_64

**Project Type**: CLI tool for offline Debian LTS package management — admin builds cache on networked system, operator consumes on offline system

**Performance Goals**: Build-dependency resolution for a 1000-source index completes within the same 5-second threshold as runtime resolution. The `source_build_deps` JOIN cost is negligible (indexed FKs).

**Constraints**: Fully offline runtime after cache handoff (no network calls). Build-tool resolution must not introduce new network fetches beyond the existing index fetch. Database-First: graph populated before any download.

**Scale/Scope**: Adds `build_depends` column to sources (avg <1KB per source, ~10K sources = ~10MB) and `source_build_deps` table (entry per source-to-build-tool pair, ~5-50 per source). No new cache filesystem footprint beyond the build-tool `.deb` files themselves.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Principle I (Offline-First Availability)**: ✅ Build tools are resolved and cached during the admin's networked session. The operator receives a cache that includes all build tools needed for offline source rebuilds. No network calls needed at runtime.

**Principle II (Dependency Alignment)**: ✅ Build-tool dependencies are resolved from the same Debian archive as runtime packages. Version constraints in Build-Depends are parsed and matched against available versions, same as binary Depends.

**Principle III (Binary-Source Consistency)**: ✅ The `source_build_deps` table mirrors the `binary_to_source` pattern, ensuring every source package's build-tool requirements are tracked with the same verified-flag lifecycle.

**Principle IV (Minimal Local Cache)**: ✅ Only the build tools actually required by cached sources are resolved and cached. A single `[fetch] source` setting (default `true`) controls whether source and build-tool data is fetched — when set to `false` in `project.local.toml`, only binary packages are cached, minimizing cache size when offline source rebuilds are not needed.

**Principle V (Debian Policy Compliance)**: ✅ Build-Depends parsing reuses the same dependency field grammar as Depends (Debian Policy §4.2). No new compliance surface.

**Principle VI (Code Quality & Type Safety)**: ✅ The `BuildDependencySpec` follows the same frozen-dataclass + JSON-serialization pattern as `DependencySpec`. No `dict[str, Any]` return types.

**Principle VII (Database-First Resolution)**: ✅ Build-tool dependency graph is computed and populated in `source_build_deps` before any download. The same `verified`-flag lifecycle applies.

**Principle VIII (Documentation & Infrastructure Traceability)**: ✅ Every build tool is recorded in the database with its version and source linkage. The `list --deps --include-build-deps` command surfaces this for audit.

**Principle IX (Incremental Spec Scope)**: ✅ This feature is a single coherent increment: extend data model, extend resolver, extend verify. 3 user stories, 9 functional requirements — well within the 10-story / 20-FR limit.

**GATE RESULT**: ✅ PASS — No violations.

## Roles Check

- [x] Commands are assigned to the correct persona (admin-only — `add` with build-deps is the admin's networked workflow)
- [x] No persona-dependent behavior leaks across the offline/online boundary (build-tool resolution uses same indexes as runtime)
- [x] Cache handoff expectations are reflected in the architecture (build tools are in the binary cache, transferred as part of the standard handoff)
- [x] No new personas or role changes introduced

## Project Structure

### Documentation (this feature)

```text
specs/004-build-tool-dependencies/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── cli.md           # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

Changes are additive to the existing structure. No new files needed — existing files are modified.

```text
src/package_mgmt/
├── cli/
│   ├── main.py                    # + --include-build-deps on list
│   └── commands/
│       ├── list.py                # + build-tool tree display when --include-build-deps is set
│       └── verify.py              # + build-tool statistics section
├── lib/
│   ├── model/
│   │   ├── config.py              # + fetch_source field (default True)
│   │   └── database.py            # + build_depends column on sources, source_build_deps table
│   ├── service/
│   │   ├── config_loader.py       # + _KEY_MAP entry for [fetch] source
│   │   ├── resolver.py            # + build-dep parsing + resolution conditioned on config
│   │   └── index_loader.py        # (no change — build_depends already parsed from Sources.gz)
│   └── util/
│       └── (no changes)
├── project.defaults.toml          # + [fetch] source = true
├── tests/
│   ├── unit/
│   │   ├── test_config.py         # + test [fetch] settings loading, defaults, overrides
│   │   ├── test_database.py       # + schema v2 migration, source_build_deps constraints
│   │   └── test_dependency.py     # + BuildDependencySpec parsing tests
│   ├── integration/
│   │   └── test_resolver.py       # + build-tool resolution end-to-end (enabled/disabled)
│   └── contract/
│       └── test_cli.py            # + verify --graph build-tool section
```

**Structure Decision**: Follows the existing single-package layout. All changes are modifications to existing files — no new modules or packages required. The resolver gains a build-tool resolution step that reuses the same `_resolve_one()` recursion for each build-dep entry found in the source's `build_depends` JSON.

## Complexity Tracking

> No Constitution Check violations — no complexity justification needed.

