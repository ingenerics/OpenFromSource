<!--
SPECKIT SYNC IMPACT REPORT
==========================
Version change: 1.1.0 → 1.2.0 (MINOR)
Modified principles:
  - III. Binary-Source Consistency: refined to allow version divergence
    between binary and source (tracked via binary_to_source link table);
    previously mandated same version strings
Added sections:
  - VII. Database-First Resolution (new core principle)
  - VIII. Documentation & Infrastructure Traceability (new core principle)
  - IX. Incremental Spec Scope (new core principle)
  - Key Python Libraries: added "sqlite3" and "python3-debian"
  - Quality Gates: added DB populate-before-download, verified field,
    spec scope size, and documentation checks
Removed sections: N/A
Templates requiring updates:
  - .specify/templates/plan-template.md: ✅ updated — SQLite in Technical
    Context, DB-first approach referenced
  - .specify/templates/spec-template.md: ✅ updated — FR-006 reworded for
    version divergence; FR-007/FR-008 added for DB-first; entities reference
    DB schema
  - .specify/templates/tasks-template.md: ✅ updated — Foundational phase
    includes DB schema creation, populate-before-download; task numbering
    shifted
  - .specify/templates/checklist-template.md: ✅ updated — Database-First
    Resolution category added with checklist items
Follow-up TODOs:
  - Update roles.md to reference the DB-first approach in the admin's
    `add` workflow description
  - Review all open specs for alignment with IX (Incremental Spec Scope)
-->

# OpenFromSource Constitution

## Core Principles

### I. Offline-First Availability

All packages and their required dependencies MUST be available locally for the
system to function without network connectivity. A complete metadata index and
package cache MUST be established before going offline. The system MUST support
full installation, removal, and dependency resolution without any network calls.
No network calls are permitted during normal operation — all metadata must be
pre-synced and cached locally.

### II. Dependency Alignment

Every added package MUST resolve to a version whose dependency tree is fully
satisfiable by the local package cache. When a fast-evolving source package
(e.g. Hyprland) has dependencies that diverge from the Debian LTS base, the
system MUST find a compatible version whose dependencies align with locally
cached packages before retrieval. The system MUST analyze each new spec to
determine its dependency footprint and validate alignment against the local
cache.

### III. Binary-Source Consistency

For every binary package in the local cache, a corresponding source package
MUST be available such that the binary can be deterministically rebuilt from
that source. Source and binary version strings MAY differ — the mapping is
tracked via the `binary_to_source` link table, as a single source package may
produce multiple binary packages at varying version strings. Both binary and
source MUST satisfy deterministic build requirements: identical inputs produce
identical outputs.

### IV. Minimal Local Cache

Only required dependencies MUST be cached locally. Each new package
specification MUST be analyzed for its dependency tree to identify which
dependencies are actually needed (and which are transitive-only). The system
MUST avoid caching superfluous or optional packages that are not strictly
necessary for a working installation. Space-efficient caching is a
non-negotiable constraint.

### V. Debian Policy Compliance

All managed packages MUST conform to the Debian Policy Manual and DFSG licensing
requirements. Package builds from source MUST pass lintian with zero ERROR or
WARNING outcomes. All package licenses MUST be vetted against Free Software
Foundation guidelines. Non-free and restricted packages MUST be categorized
correctly and their licenses MUST be explicitly declared in metadata.

### VI. Code Quality & Type Safety

All public function signatures MUST use concrete types — no `dict[str, Any]`,
`Any` without justification, or implicit shape contracts in docstrings.
Dataclasses (immutable, `frozen=True`) are the default for domain objects.
`TypedDict` is permitted only at JSON I/O boundary adapters where runtime
dicts are unavoidable. The `Any` type is permitted only for: (1) generic
utility functions (`print_json`, `format_table`), (2) third-party library
stubs (e.g. `sqlite3.Cursor`), and (3) explicit cross-layer adapters. Every
dict return value that carries domain meaning must be accompanied by a
dataclass or `TypedDict` that documents the keys and their types — implicit
dict shapes in return annotations are prohibited.

### VII. Database-First Resolution

All package metadata MUST be populated in the SQLite database **before** any
download or fetch operation begins. The database schema MUST include at
minimum the following tables:

- **packages** — base package records with name, requested version, status
- **binaries** — binary package entries with package_name, version,
  architecture, and dependency references for graph construction
- **sources** — source package entries with package_name and source_version
  (which MAY differ from binary versions); one-to-many relationship with
  binaries
- **binary_to_source** — link table mapping binary packages to their
  corresponding source packages for deterministic rebuild tracking

Every record MUST include a `verified` field that flags whether its data has
been confirmed as retrievable from the configured archives. Records are
created upfront from the specification alone — before any network operations —
enabling:

- Validation of completeness before execution (user is prompted if any
  `verified` field is false after planning)
- Dependency graph construction from pre-populated data
- apt-offline signature generation driven by the database
- Debugging via SQL queries against the current state without re-executing
  downloads

### VIII. Documentation & Infrastructure Traceability

Documentation MUST be kept current as the project evolves. Build and install
scripts, along with their dependencies and supporting frameworks, MUST be
clearly tracked and documented. Every helper package required for the build
or install toolchain MUST be recorded as part of the dependency graph, so
that the complete software stack — including tooling — can be reproduced
offline.

### IX. Incremental Spec Scope

Feature specifications MUST be kept to a manageable size that permits
independent validation. No single spec SHOULD encompass an entire workflow
end-to-end if it can be decomposed into independently implementable and
testable increments. Each spec SHOULD produce a demonstrable outcome that
can be validated before the next spec begins.

## Technology & Target Platform

**Target**: Debian Trixie LTS amd64 / x86_64 as primary target; backporting
to Debian Bookworm where alignment permits.

**Language**: Python 3.13.x (Trixie-default version) for the core dependency
resolution and package management framework.

**Database**: SQLite 3.x (bundled with Python stdlib) for the local metadata
store; schema migrations MUST be versioned and backward-compatible.

**Packaging Tools**: dpkg-dev, apt, debhelper, dh_make, fakeroot for building
and managing Debian packages.

**Key Python Libraries**: python-apt, python-debian, sqlite3 (stdlib),
requests (for initial metadata sync only), cryptography (for GPG signature
verification).

**Testing**: pytest for the Python framework; debci for dependency
validation; lintian for Debian Policy compliance; piuparts for offline
install/remove verification.

## Quality Gates

- Every Release file in the local repository MUST be GPG-signed
- Each package MUST be verified for integrity (checksum + signature) before
  installation
- `dpkg-buildpackage -sa -nc -us` MUST pass without errors for all source
  packages built locally
- `lintian --I --e` MUST return zero ERROR and zero WARNING outcomes
- Dependency resolution for 10,000+ package indices MUST complete within 5
  seconds
- All builds MUST be reproducible — identical inputs produce identical outputs
- Piuparts tests MUST pass for both offline install and remove scenarios
- Database records MUST be populated and `verified` BEFORE any download
  operation starts
- Each database record MUST carry a `verified` field; the system MUST NOT
  proceed to download if any required record remains unverified after
  planning
- Feature specs MUST be scoped to fit within a single implementable and
  testable increment; any spec exceeding 10 user stories or 20 functional
  requirements SHOULD be decomposed
- Documentation updates MUST be included in the same feature cycle as the
  code changes they describe

## Governance

This Constitution supersedes all other development practices and decisions for
the OpenFromSource project.

**Versioning Policy**: The constitution uses semantic versioning
(MAJOR.MINOR.PATCH). Changes are bumped as follows:

- MAJOR: Backward-incompatible governance or principle removals/redefinitions
- MINOR: New principle, section added, or materially expanded guidance
- PATCH: Clarifications, wording improvements, typo fixes, non-semantic
  refinements

**Compliance**: All feature specs, implementation plans, and tasks MUST verify
compliance with the principles in this constitution before proceeding.

**Amendments**: Changes to this constitution MUST be documented explicitly,
include the rationale for the change, and undergo review before adoption. The
`CONSTITUTION_VERSION` MUST be incremented according to the versioning policy
above.

**Version**: 1.2.0
