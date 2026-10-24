# Implementation Plan: Package Dependency Resolution

**Branch**: `002-dependency-resolution` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-dependency-resolution/spec.md`

## Summary

This feature delivers the dependency resolution engine for the `package_mgmt add` command. Given one or more package names and locally-available repository index metadata (Packages.gz, Sources.gz), the system resolves the full transitive dependency tree, creates records in all four database tables (packages, binaries, sources, binary_to_source), and marks them as unverified. No package payloads are downloaded — this is database-first population per Constitution Principle VII. The design uses `python3-debian` for version parsing and index parsing, stores version strings exactly as they appear in the archive (preserving `+bN` suffixes for binary versions), and structures dependency specs as JSON in the `binaries.dependencies` column.

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default)

**Primary Dependencies**: `python3-debian` (for `deb822.Packages`/`deb822.Sources` index parsing and `debian.debian_support.Version` for version handling), `sqlite3` (stdlib), `gzip` (stdlib), `json` (stdlib)

**Storage**: SQLite database — existing `packages`, `binaries`, `sources`, `binary_to_source` tables populated during resolution; `binaries.dependencies` stores JSON arrays of `DependencySpec` objects

**Testing**: pytest; temporary directories for index metadata isolation; known test packages (`vifm`, `nano`, `perl`) from Debian Trixie archive

**Target Platform**: Debian Trixie LTS amd64 / x86_64

**Project Type**: CLI tool — this feature delivers the `add` subcommand with dependency resolution and the `list --deps` / `verify --graph` inspection commands

**Performance Goals**: Resolution for a package with 10 transitive dependencies completes in under 2 seconds (index parsing I/O dominates); the resolver handles 1000+ transitive deps without degradation

**Constraints**: 
- Must work offline — index metadata must be available locally before resolution begins
- Must handle version format differences between binary (with `+bN`) and source (without `+bN`)
- Must store version strings exactly as they appear in the repository index (for downstream download URL construction)
- Must not download any package payloads during resolution
- Must detect circular dependencies and terminate gracefully
- Must be idempotent — re-running `add` for the same package updates existing records

**Scale/Scope**: Single-spec resolution of a package's transitive dependency closure; target is typically 1-50 packages per request, but must handle up to 1000+

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Offline-First Availability | ✅ Pass | Resolution operates on locally-available index metadata — no network calls |
| II. Dependency Alignment | ✅ Pass | Resolved dependencies are recorded; actual version satisfaction checking is a downstream concern |
| III. Binary-Source Consistency | ✅ Pass | Every binary must have a linked source record (core requirement) |
| IV. Minimal Local Cache | ✅ Pass | Only requested package + its transitive closure are resolved |
| V. Debian Policy Compliance | ✅ Pass | No packages are built or installed in this feature |
| VI. Code Quality & Type Safety | ✅ Pass | frozen dataclasses for domain objects; TypedDict for JSON dependency boundary |
| VII. Database-First Resolution | ✅ Pass | THIS IS THE FEATURE — all tables populated before any download |
| VIII. Documentation & Infrastructure Traceability | ✅ Pass | Plan, data-model, contracts, quickstart all created |
| IX. Incremental Spec Scope | ✅ Pass | Resolution only — no download, no installation, no package building |

All applicable principles pass. No violations requiring complexity tracking.

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../../.specify/memory/roles.md).*

- [x] Commands are assigned to the correct persona
  - `package_mgmt add` — admin-only (fetches/resolves on networked system)
  - `package_mgmt list --deps` — shared (both personas inspect cache contents)
  - `package_mgmt verify --graph` — shared (both verify completeness)
- [x] No persona-dependent behavior leaks across the offline/online boundary
- [x] Cache handoff expectations are reflected in the architecture
  - Resolved dependency graph is serialized in the database, which is part of the handoff
- [ ] If this feature introduces new personas or changes existing roles, [roles.md](../../.specify/memory/roles.md) has been updated accordingly
  - No role changes needed; existing admin/operator split is sufficient

## Project Structure

### Documentation (this feature)

```text
specs/002-dependency-resolution/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── cli.md           # CLI command contracts
├── checklists/
│   └── requirements.md  # Spec quality checklist
├── spec.md              # Feature specification
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
src/
└── package_mgmt/
    ├── __init__.py
    ├── __main__.py
    ├── cli/
    │   ├── __init__.py
    │   └── commands/
    │       ├── __init__.py
    │       ├── add.py        # `package_mgmt add` — dependency resolution + DB population
    │       ├── list.py       # `package_mgmt list --deps` — dependency graph display
    │       └── verify.py     # `package_mgmt verify --graph` — completeness stats
    └── lib/
        ├── __init__.py
        ├── model/
        │   ├── __init__.py
        │   ├── dependency.py # DependencyGraph, DependencySpec types
        │   ├── package.py    # BinaryPackage, SourcePackage index entry models
        │   └── repository.py # RepositoryIndex — parsed Packages.gz/Sources.gz
        ├── service/
        │   ├── __init__.py
        │   ├── resolver.py   # Dependency resolution engine (transitive closure)
        │   └── index_loader.py # Index parsing + loading from disk
        └── util/
            ├── __init__.py
            └── versions.py   # Version helpers (extract source version from binary, etc.)
```

### Configuration (existing, unchanged)

```text
pyproject.toml                # Build config, console_scripts entry point
project.defaults.toml         # Shipped defaults (version-controlled)
project.local.toml            # Local overrides (git-ignored)
```

### Runtime Index Layout (consumed, not created)

```text
{base_dir}/
├── binary/
│   └── dists/
│       └── {release}/
│           └── {component}/
│               └── binary-{arch}/
│                   └── Packages.gz    # Input binary index
├── source/
│   └── dists/
│       └── {release}/
│           └── {component}/
│               └── source/
│                   └── Sources.gz     # Input source index
└── package_mgmt.db                    # Output — populated during resolution
```

**Structure Decision**: New subcommand files follow the existing `src/package_mgmt/cli/commands/` pattern. Resolution logic lives in `src/package_mgmt/lib/service/resolver.py` with index loading in `index_loader.py`. New model files for index entries and dependency structures go under `lib/model/`. Version helpers in `lib/util/versions.py`.

## Complexity Tracking

> No constitution violations — this section is intentionally left blank.
