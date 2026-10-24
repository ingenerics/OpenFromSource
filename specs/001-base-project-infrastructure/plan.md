# Implementation Plan: Base Project Infrastructure

**Branch**: `001-base-project-infrastructure` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-base-project-infrastructure/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command.

## Summary

This feature delivers the foundational project infrastructure: a TOML-based
configuration system with local override support, initialisation of two
separate cache directories (binary and source) following the Debian apt
repository layout (`pool/` + `dists/`), and creation of the SQLite database
schema (packages, binaries, sources, binary_to_source tables with `verified`
columns) as mandated by Constitution Principle VII. The CLI tool is exposed
as the `package_mgmt` command via a `console_scripts` entry point.

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default)

**Primary Dependencies**: `tomllib` (stdlib), `sqlite3` (stdlib), `pathlib`
(stdlib), `argparse` (stdlib)

**Storage**: SQLite database at `{data_dir}/package_mgmt.db`; two separate
cache directories each following Debian apt layout (`pool/` + `dists/`):
- Binary cache at `{base_dir}/binary/` — `.deb` files, binary Packages.gz
- Source cache at `{base_dir}/source/` — `.dsc`, `.orig.tar.*`, Sources.gz

**Testing**: pytest; temporary directories for init test isolation

**Target Platform**: Debian Trixie LTS amd64 / x86_64

**Project Type**: CLI tool — this feature delivers the `init` subcommand plus
config inspection

**Performance Goals**: Database schema validation completes in under 500ms
on pre-existing database (SC-005); config parsing in under 100ms

**Constraints**: Fully offline; idempotent init (safe to re-run); no
destructive changes to existing data; Python 3.11+ required for `tomllib`

**Scale/Scope**: Single configuration file (`project.defaults.toml` + optional
`project.local.toml`), single database, two cache directories (binary + source,
each with Debian apt layout); this feature does not cover package operations

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Offline-First Availability | ✅ Pass | Init is fully offline — no network needed |
| IV. Minimal Local Cache | ✅ Pass | Only creates directory skeleton, no superfluous files |
| VI. Code Quality & Type Safety | ✅ Pass | Python dataclasses for config; frozen=True domain objects |
| VII. Database-First Resolution | ✅ Pass | DB schema created before any download would begin |
| VIII. Documentation & Infrastructure Traceability | ✅ Pass | Config file IS the documentation; .local files documented |
| IX. Incremental Spec Scope | ✅ Pass | Single spec focused on infrastructure only (3 stories, 12 FRs) |

All applicable principles pass. No violations requiring complexity tracking.

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../../.specify/memory/roles.md).*

- [x] Commands are assigned to the correct persona
  - `package_mgmt init` — shared (both roles run it on their own machine)
  - `package_mgmt config` — shared (both inspect configuration)
- [x] No persona-dependent behavior leaks across the offline/online boundary
- [x] Cache handoff expectations are reflected in the architecture
  - Binary cache and source cache at `/var/cache/package_mgmt/binary/`
    and `/var/cache/package_mgmt/source/` are the agreed handoff paths
- [ ] If this feature introduces new personas or changes existing roles,
  [roles.md](../../.specify/memory/roles.md) has been updated accordingly
  - No role changes needed; roles.md already describes admin + operator

## Project Structure

### Documentation (this feature)

```text
specs/001-base-project-infrastructure/
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
    ├── __main__.py              # `python -m package_mgmt` fallback
    ├── cli/
    │   ├── __init__.py
    │   └── commands/
    │       ├── __init__.py
    │       ├── init.py          # `package_mgmt init` — cache + DB setup
    │       └── config.py        # `package_mgmt config` — inspect settings
    └── lib/
        ├── __init__.py
        ├── model/
        │   ├── __init__.py
        │   ├── config.py        # ProjectConfig dataclass
        │   ├── cache.py         # BinaryCache/SourceCache dataclasses
        │   └── database.py      # Schema definition, DB init, version check
        ├── service/
        │   ├── __init__.py
        │   ├── config_loader.py # Default + override merge logic
        │   ├── initializer.py   # Cache dir creation, permission setting
        │   └── db_init.py       # Database initialization
        └── util/
            ├── __init__.py
            ├── errors.py        # Exceptions, exit codes, _print_error
            └── paths.py         # Resolved path helpers

pyproject.toml                # Build config, console_scripts entry point:
                              #   package_mgmt = "package_mgmt.cli:main"

project.defaults.toml         # Shipped defaults (version-controlled)
project.local.toml            # Local overrides (git-ignored)

tests/
├── unit/
│   ├── test_config.py
│   ├── test_database.py
│   └── test_initializer.py
├── contract/
│   └── test_cli.py           # CLI output contract tests
└── integration/
    └── test_init_flow.py     # Full init → verify → re-init flow
```

**Runtime cache layout** (created by `package_mgmt init`):
```text
/var/cache/package_mgmt/
├── binary/                   # Binary package cache (Debian apt layout)
│   ├── pool/
│   │   └── <component>/
│   │       └── <letter>/          # first letter of package name
│   │           └── <package>_<version>_<arch>.deb
│   └── dists/
│       └── <release>/
│           ├── <component>/
│           │   └── binary-<arch>/
│           │       ├── Packages.gz
│           │       └── Packages.xz
│           ├── Release
│           ├── Release.gpg
│           └── InRelease
├── source/                   # Source package cache (Debian apt layout)
│   ├── pool/
│   │   └── <component>/
│   │       └── <letter>/          # first letter of package name
│   │           ├── <package>_<version>.dsc
│   │           ├── <package>_<version>.orig.tar.*
│   │           └── <package>_<version>.debian.tar.*
│   └── dists/
│       └── <release>/
│           ├── <component>/
│           │   └── source/
│           │       ├── Sources.gz
│           │       └── Sources.xz
│           ├── Release
│           ├── Release.gpg
│           └── InRelease
├── package_mgmt.db           # SQLite metadata database
└── logs/                     # Log output
```

**Structure Decision**: Standard CLI tool layout with `src/package_mgmt/cli/commands/` for
subcommands and `src/package_mgmt/lib/` for reusable logic. Externally, `package_mgmt` is
exposed via `console_scripts` entry point in `pyproject.toml`. Config files at
the project root for discoverability. Tests mirror source layout.

## Complexity Tracking

> No constitution violations — this section is intentionally left blank.
