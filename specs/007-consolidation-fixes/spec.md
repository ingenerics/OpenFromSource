# Feature Specification: Consolidation Fixes

**Feature Branch**: `007-consolidation-fixes`
**Status**: Draft

**Input**: User description: "Go over the entire code base, process the specs in chronological order and analyse the current state of the project. Propose 10 action points which would add the most value, either in aligning the code and values or by fixing code. Add these to a new Spec for traceability and get started."

**Source**: Full-codebase audit of specs 001–006 implementation state. Ten action points were proposed; each maps to one user story below.

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Resolves, registers, and verifies packages into the cache database |
| **Operator** | Offline system (no network) | Consumes the pre-populated cache; depends on correct verify semantics |

**Phase 1 — Resolver Correctness**: Complete build-tool resolution, circular dependency detection, correct fallback semantics.
**Phase 2 — Verify & View Correctness**: Current-tree-only verification, missing build-tool reporting, full `show --deps`.
**Phase 3 — Resolution Determinism**: Constraint-aware selection, per-pair source validation, idempotent provides.
**Phase 4 — Engineering Health**: Dev/test infrastructure, contract & constitution reconciliation.

---

### User Story 1 - Complete Build-Tool Dependency Resolution (Priority: P1)

The administrator adds a package with deep build-tool chains (e.g. `gcc` requires `binutils` requires a linker). Today the resolver stops expanding build dependencies after `max_build_dep_report_depth` iterations (default 2), so build tools deeper than that are silently never resolved — even though documentation and the config comment promise the pool is complete. After this fix, the full transitive build-tool tree is always resolved, and the reporting depth setting only filters `verify --graph` output.

**Why this priority**: Silent incomplete resolution undermines the core offline guarantee (Constitution I). The operator discovers missing tools only at build time.

**Independent Test**: `package_mgmt add <pkg>` followed by a database query shows `source_build_deps` links deeper than 2 levels for a package with a deep build-tool chain; `verify --graph` with `max_build_dep_report_depth = 2` reports only depth-≤2 unresolved deps while deeper links exist and are complete.

**Acceptance Scenarios**:

1. **Given** a source package whose build tool (e.g. `gcc`) has its own build dependencies, **When** the admin runs `add`, **Then** build-tool sources are expanded transitively until no new build-dependency sources remain, regardless of `max_build_dep_report_depth`.
2. **Given** `max_build_dep_report_depth = 2` in the config, **When** `verify --graph` runs on a fully resolved cache, **Then** the unresolved-dependency report excludes binaries linked at depth > 2, while the database contains those links.
3. **Given** a pathological chain that never terminates, **When** resolution runs, **Then** a documented safety cap prevents an infinite loop and logs a warning.

---

### User Story 2 - Circular Dependency Detection and Annotation (Priority: P1)

The administrator adds a package whose dependency tree contains a cycle (e.g. `a → b → c → a`). Today the resolver's visited-set silently skips the repeat visit and `verify` always prints "Circular dependencies: 0" (hardcoded stub). After this fix the resolver detects cycles (a dependency referencing a package on the current resolution path), annotates the record, and `verify --graph` reports the annotated cycles and reflects them in the exit code.

**Why this priority**: Spec 002 FR-007 is an explicit obligation; silent cycle swallowing hides real resolution gaps.

**Independent Test**: Construct a cycle `a → b → c → a`. Resolve `a`. Query the `binaries` table — the cycle-closing record is marked circular. Run `verify --graph` — the cycle is listed and the exit code is non-zero.

**Acceptance Scenarios**:

1. **Given** a dependency tree containing a cycle, **When** resolution completes, **Then** the binary record closing the cycle is marked `circular` and the dependency spec is annotated, without infinite recursion.
2. **Given** a diamond dependency (shared dependency reached via two paths, not a cycle), **When** resolution completes, **Then** the shared dependency is not falsely marked circular.
3. **Given** circular records in the database, **When** the admin runs `verify --graph`, **Then** each circular dependency is listed with the packages it connects. **AMENDED (T039)**: cycles are informational — real Debian trees contain benign cycles (44 found in a vifm-only cache, e.g. `libgcc-s1 <-> libc6`, `dmsetup <-> libdevmapper1.02.1`) that dpkg resolves by version; they no longer drive the COMPLETE/INCOMPLETE status or the exit code. Only unresolved dependencies, unlinked binaries, and unresolved/missing build tools fail verification.
4. **Given** a fully acyclic tree, **When** `verify --graph` runs, **Then** "Circular dependencies: 0" is printed and no records are marked circular.

---

### User Story 3 - Verify the Current Resolution Tree Only (Priority: P1)

The administrator runs `verify --graph` after resolving packages across releases. Today the command counts *all* `binaries` rows — including `superseded` ones — and treats a superseded row as satisfying an unresolved dependency. Example: after `add hyprland --source trixie-backports` supersedes `libdrm2` 2.4.110 with 2.4.120, a tree depending on the base version is reported COMPLETE because the old row still exists. After this fix, verification reflects the coherent `status='current'` tree.

**Why this priority**: Verify is the pre-handoff gate (spec 004 US3). False COMPLETE status is worse than none.

**Independent Test**: Build a cache where a superseded version satisfies a dependency only at the superseded version. `verify --graph` reports the dependency unresolved and exits non-zero.

**Acceptance Scenarios**:

1. **Given** superseded rows in `binaries`, **When** `verify --graph` runs, **Then** `Total binaries` counts `status='current'` rows only.
2. **Given** a dependency name that exists only as a `superseded` row, **When** verification checks it, **Then** it is reported unresolved.
3. **Given** an unlinked binary that is `superseded`, **When** verification runs, **Then** it is not reported as unlinked.
4. **Given** all rows current and linked, **When** verification runs, **Then** status is COMPLETE and the exit code is 0.

---

### User Story 4 - No Arbitrary Build-Tool Linking (Priority: P1)

The administrator adds a source package whose build dependency cannot be resolved (e.g. the build tool is not in any configured source). Today the resolver falls back to linking *any* most-recent `binaries` row with the same name (resolver.py fallback block), which both misrepresents the record and hides the gap from `verify`. After this fix a failed build-tool resolution creates no link, is reported as an error, and is surfaced by `verify --graph`.

**Why this priority**: Arbitrary linking produces a database that claims completeness it doesn't have — the opposite of the Database-First contract.

**Independent Test**: Configure sources that lack a build tool referenced by a source's Build-Depends. Add the package. Query `source_build_deps` — no link exists for the missing tool; `add` reports the error; `verify --graph` reports the unresolved build dependency and exits non-zero.

**Acceptance Scenarios**:

1. **Given** a build dependency whose tool is absent from all configured sources, **When** `add` resolves it, **Then** no `source_build_deps` link is created, an error is reported, and resolution of other packages continues.
2. **Given** a resolvable build dependency, **When** `add` resolves it, **Then** the link points exactly to the binary record created for that tool — never an arbitrary row.
3. **Given** a missing build tool, **When** `verify --graph` runs, **Then** it is reported as a missing build tool and the exit code is non-zero.

---

### User Story 5 - Full `show --deps` Output (Priority: P2)

The administrator inspects a package with `package_mgmt show vifm --deps`. Today the flag only widens the queried rows: text mode never appends the dependency tree and JSON never includes the `dependency_tree` key promised by spec 003 FR-010/FR-011. After this fix, `show --deps` output matches the spec: tables followed by the same tree as `list --deps`, and JSON containing both `tables` and `dependency_tree` keys.

**Why this priority**: Explicit spec obligation (spec 003, User Story 2) currently unimplemented; the flag's current behavior diverges from its documentation.

**Independent Test**: Run `show vifm --deps` (text) — output contains the tables followed by a tree identical to `list --deps vifm`. Run `show vifm --deps --format json` — JSON contains top-level `tables` and `dependency_tree` keys.

**Acceptance Scenarios**:

1. **Given** resolved records for a package, **When** the admin runs `show <pkg> --deps` in text mode, **Then** the table view is followed by the dependency tree in the `list --deps` format.
2. **Given** the same invocation with `--format json`, **When** the output is parsed, **Then** it contains both `tables` and `dependency_tree` top-level keys.
3. **Given** `show <pkg>` without `--deps`, **When** the output is produced, **Then** the tables-only behavior is unchanged.

---

### User Story 6 - Constraint-Aware Dependency Selection (Priority: P2)

The administrator adds a package from backports whose transitive dependency must satisfy a version constraint (e.g. `libdrm (>= 2.4.120)`) or whose dependency is a version-constrained virtual package (e.g. `awk (>= 5.0)`). Today the resolver drops the dependency's version constraint at every selection point: source fallback always picks the highest version in the primary source, and virtual provider selection always evaluates `None`. After this fix, constraints are propagated into selection and only satisfying candidates are chosen, per spec 005 US4 AS3 and spec 006's "most restrictive constraint" edge case.

**Why this priority**: Constraint-free selection can pick providers that don't satisfy the recorded dependency, producing a database that contradicts its own `binaries.dependencies` field.

**Independent Test**: Given a dependency with a version constraint and candidates in multiple sources, resolution selects a candidate satisfying the constraint even when a higher non-satisfying version exists in the primary source; virtual dependencies with constraints only resolve to satisfying providers.

**Acceptance Scenarios**:

1. **Given** a dependency `libdrm (>= 2.4.120)` and a primary source offering `libdrm 2.4.110`, **When** the resolver selects, **Then** the constraint is checked and the version from the fallback source (2.4.120) is selected instead.
2. **Given** a virtual dependency with a version constraint and multiple providers, **When** the resolver selects, **Then** the constraint filters candidates and only a satisfying provider is chosen.
3. **Given** no provider satisfies the constraint, **When** the resolver processes the dependency, **Then** it is reported as unsatisfiable (best-effort, resolution continues).

---

### User Story 7 - Per-Pair Duplicate Source Validation (Priority: P2)

The administrator configures overlapping sources — e.g. `trixie/main` plus `trixie/main,contrib`. Today duplicate detection compares whole source tuples, so the repeated `(trixie, main)` pair is not rejected, violating spec 005 FR-004. After this fix, every `(release, component)` pair across all sources must be unique or the config is rejected.

**Why this priority**: Overlapping sources produce ambiguous index merges and duplicate-provider selection.

**Independent Test**: Load a config with `[[archive.source]]` entries whose component lists overlap. The system reports a duplicate `(release, component)` error and exits non-zero.

**Acceptance Scenarios**:

1. **Given** two sources sharing any `(release, component)` pair (identical or overlapping component lists), **When** config is loaded, **Then** a clear duplicate-pair error names the offending pair.
2. **Given** two sources with disjoint pairs, **When** config is loaded, **Then** both are accepted.
3. **Given** a valid single-source config, **When** config is loaded, **Then** no behavioral change.

---

### User Story 8 - Idempotent Provides and Resolution Hygiene (Priority: P2)

The administrator re-runs `package_mgmt init` after the index changes. Today `populate_provides_table` only inserts (`INSERT OR IGNORE`), so provides rows removed from the index remain forever, and cached `virtual_resolutions` can reference binaries that no longer exist (fresh database) or that have been superseded by a later resolution. After this fix, re-init repopulates the provides table from the current index (stale rows removed), orphaned virtual resolutions are cleaned, and the cached-resolution re-check also rejects superseded providers.

**Why this priority**: The database is the source of truth (spec 006); stale index metadata and dangling resolution records break deterministic re-resolution.

**Independent Test**: Run `init`, delete a provides row's source package from a modified index, re-run `init` — the row is gone. Create a `virtual_resolutions` row pointing at a missing binary — re-init removes it. Resolve a virtual, supersede its provider by another path, re-resolve — the cached entry is not reused.

**Acceptance Scenarios**:

1. **Given** a previously populated `provides` table, **When** `init` runs again with the same index, **Then** the table contains exactly the index's provides relationships (no duplicates, no stale rows).
2. **Given** a `virtual_resolutions` row referencing a nonexistent binary, **When** `init` runs, **Then** the orphaned row is removed.
3. **Given** a cached virtual resolution whose provider binary is `superseded`, **When** `add` encounters the virtual name, **Then** the resolver re-resolves rather than reusing the superseded provider.

---

### User Story 9 - Test and Development Infrastructure (Priority: P3)

A developer clones the repository and wants to run the 29 existing tests. Today there is no declared dev tooling in the project metadata (pytest/ruff), no CI workflow, and specs 005's quickstart validation (T032) was never completed. Note: the suite itself is unittest-based and runs with the system `python3-debian` (apt package, already installed) via `python3 -m unittest` — pytest is a convenience. After this fix, dev dependencies are declared, bootstrap steps are documented, quickstart validations for specs 005 and 006 are run and recorded, and a CI workflow runs the test suite.

**Why this priority**: Without a runnable test baseline, every change in this spec risks silent regression; the 29-test claim cannot currently be verified.

**Independent Test**: On a fresh clone, the documented bootstrap installs the tool and its dev dependencies; the full test suite passes; CI executes the same suite.

**Acceptance Scenarios**:

1. **Given** a fresh clone with system `python3-debian` installed, **When** the documented bootstrap is followed, **Then** the full suite runs green via `python3 -m unittest discover -s tests` (pytest optional).
2. **Given** the repository on a CI platform, **When** a push occurs, **Then** the suite runs and reports results.
3. **Given** the quickstart documents for specs 005 and 006, **When** their validation scenarios are executed, **Then** all pass and the tasks (005 T032) are marked complete.

---

### User Story 10 - Contract and Constitution Reconciliation (Priority: P3)

The administrator reads command output. Today several behaviors drift from the published CLI contracts (specs 005/006): `add` never prints the virtual-resolution count, `show --tables virtual_resolutions` prints raw IDs instead of the contracted joined view, `config --format json` omits `fetch_source`, and the virtual-package error wording differs. Separately, the code violates Constitution VI (raw dict/tuple shapes returned from `resolve()` and `select_provider()`), and the configured `log_level`/`log_dir` are never used. After this fix, output matches the contracts, domain shapes are typed, and logging honors configuration.

**Why this priority**: Contracts are the project's behavioral traceability mechanism (Constitution VIII); drift silently invalidates them. Constitution VI type-safety violations accumulate debt.

**Independent Test**: Run each affected command and diff against the contract examples; static inspection confirms no bare-domain dict/tuple returns in the resolver; log files appear in the configured `log_dir` at the configured level.

**Acceptance Scenarios**:

1. **Given** a resolution involving virtual packages, **When** `add` completes, **Then** output includes the virtual resolution count (e.g. `(8 dependencies, 2 virtual)`).
2. **Given** `show gawk --tables virtual_resolutions`, **When** the command runs, **Then** output shows the resolved provider name and version, not raw binary IDs.
3. **Given** `config --format json`, **When** the command runs, **Then** `fetch_source` is present.
4. **Given** `log_level` and `log_dir` in the config, **When** any command runs, **Then** logs are written to `log_dir` at the configured level.
5. **Given** the resolver's public service functions, **When** they are inspected, **Then** no domain-carrying return uses a bare `dict` or tuple — typed structures are used.

---

### Edge Cases

- What happens to previously recorded `build_dep_depth` values when resolution depth semantics change? → Depths keep their meaning (reporting filter); deeper links are simply recorded with larger depth values.
- Can the unbounded build-tool loop stall on a self-referential build dependency? → Cycles are detected per US2; the safety cap bounds pathological cases.
- What if a cycle spans build-tool resolution (gcc → g++ → gcc)? → The ancestry-based detection applies to the same `_resolve_one` used for build tools.
- What if a dependency constraint references a version syntax the parser cannot compare? → `debian.debian_support.Version` semantics apply; unparseable constraints log a warning and behave as unconstrained.
- What if a config's overlapping pairs are identical to pre-existing databases? → Validation runs at load time; existing databases are unaffected, re-created by the developer per spec 005's development-stage rule.
- What if the index fetch fails during re-`init` (no network)? → Provides table is only repopulated from a successfully loaded index; a failed load leaves the previous snapshot untouched.
- Does removing arbitrary build-tool linking change behavior for already-populated databases? → No migration is performed; verify on existing data reports the gaps it previously masked.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST resolve the full transitive build-tool dependency tree during `add`, independent of any reporting-depth setting, iterating until no new build-dependency sources remain.
- **FR-002**: `max_build_dep_report_depth` MUST only filter which unresolved dependencies `verify --graph` reports, never what is resolved.
- **FR-003**: Build-tool resolution MUST terminate: cycles (US2 detection) and a documented safety cap (log warning) bound the loop.
- **FR-004**: The resolver MUST detect circular dependencies by ancestry (dependency references a package on the current resolution path) and mark the closing binary record `circular`.
- **FR-005**: Diamond dependencies (shared dependency, no cycle) MUST NOT be marked circular.
- **FR-006**: `verify --graph` MUST report annotated circular dependencies with the packages they connect, and MUST return a non-zero exit code when any are present.
- **FR-007**: `verify --graph` MUST count and check only `status='current'` binaries for totals, unresolved dependencies, and unlinked binaries.
- **FR-008**: `verify --graph` MUST report missing build tools computed from `sources.build_depends` vs `source_build_deps` and the current binaries table — no hardcoded counts.
- **FR-009**: When a build-tool dependency cannot be resolved, the system MUST NOT create a `source_build_deps` link to any other binary; it MUST report the error and continue (best-effort).
- **FR-010**: `show --deps` in text mode MUST append the dependency tree (same output as `list --deps`) after the tables.
- **FR-011**: `show --deps --format json` MUST emit both `tables` and `dependency_tree` top-level keys.
- **FR-012**: The resolver MUST propagate each dependency spec's version constraint into selection for real packages (source fallback) and virtual packages (provider filtering).
- **FR-013**: If no candidate satisfies a dependency's version constraint, the resolver MUST report the dependency as unsatisfiable and continue resolving (best-effort).
- **FR-014**: Configuration validation MUST reject any two configured sources that share a `(release, component)` pair, naming the pair in the error.
- **FR-015**: Re-running `init` MUST repopulate the `provides` table to exactly match the loaded index (stale rows removed) and MUST remove `virtual_resolutions` rows referencing nonexistent binaries.
- **FR-016**: The cached `virtual_resolutions` re-check MUST treat a superseded provider as invalid and re-resolve.
- **FR-017**: Dev dependencies for running the test suite MUST be declared in the project metadata and bootstrap steps documented.
- **FR-018**: Quickstart validation scenarios for specs 005 and 006 MUST pass and be recorded (task 005 T032 closed).
- **FR-019**: CLI output MUST match the published contracts: `add` reports virtual resolution count; `show --tables virtual_resolutions` joins provider name/version; `config --format json` includes `fetch_source`.
- **FR-020**: Logging MUST honor the configured `log_level` and `log_dir`; resolver service functions MUST use typed return shapes (no bare domain dicts/tuples, Constitution VI).

### Key Entities *(include if feature involves data)*

- **Binary (DB table, extended)**: Gains `circular INTEGER NOT NULL DEFAULT 0` — set when a dependency of this binary closes a resolution cycle. Additive DDL, schema version stays 1.
- **DependencySpec (model)**: Gains a `circular: bool` field serialized into `binaries.dependencies` JSON (default false; existing rows unaffected).
- **ResolutionStats (new type)**: Frozen dataclass replacing the bare `stats` dict returned by `PackageResolver.resolve()` — fields: packages, binaries, sources, build_tools, build_dep_links, errors, resolved_source, fallback_count, virtual_count.
- **ProviderCandidate (new type)**: Frozen dataclass replacing bare provider tuples in `query_provides()` / `select_provider()`.
- **Provides (DB table, unchanged)**: Index snapshot; repopulated exactly on re-init.
- **VirtualResolution (DB table, unchanged)**: Hygiene enforced on re-init and re-resolution.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A package with a build-tool chain deeper than 2 levels resolves build-tool links at depth 3+ — verifiable by querying `source_build_deps.build_dep_depth`.
- **SC-002**: A constructed cycle `a → b → c → a` resolves without error, marks one record circular, and makes `verify --graph` exit non-zero with the cycle listed.
- **SC-003**: A cache containing superseded rows verifies against the current tree only — superseded rows neither count nor satisfy dependencies; a scenario that was falsely COMPLETE becomes INCOMPLETE.
- **SC-004**: `verify --graph` reports real missing-build-tool counts; the output contains no hardcoded zero.
- **SC-005**: `show vifm --deps` text output matches `list --deps vifm` for the tree section; JSON validates with `tables` and `dependency_tree` keys.
- **SC-006**: A version-constrained dependency (real and virtual) always resolves to a constraint-satisfying candidate in a controlled index scenario.
- **SC-007**: Overlapping `(release, component)` configs are rejected; disjoint configs pass.
- **SC-008**: Re-running `init` produces a `provides` table exactly matching the index (row count equal to a fresh init) and no orphaned `virtual_resolutions` rows.
- **SC-009**: The full test suite passes on a fresh clone via documented bootstrap, and CI runs the suite.
- **SC-010**: Affected CLI outputs diff cleanly against the updated contract examples in `contracts/cli.md`.

## Assumptions

- **No Schema Version Bump**: Per spec 005's development-stage rule, `SCHEMA_VERSION` stays 1; the `circular` column is additive DDL. Developers recreate databases as needed.
- **Resolution Depth vs Reporting Depth**: The existing `max_build_dep_report_depth` setting keeps its documented reporting semantics (docs/002-build-dep-depth.md); only the accidental resolution coupling is removed.
- **Constraint Semantics**: Constraint evaluation uses the existing `debian.debian_support.Version` comparison logic; unparseable constraints degrade to unconstrained selection with a warning.
- **Best-Effort Continuation**: Failed build-tool or virtual resolutions are reported and skipped; other packages continue (existing pattern).
- **No Migration**: Existing databases are not migrated; behavior changes apply to new resolutions and re-runs of `init`/`verify`.
- **Scope Boundary**: No new commands, no new tables (beyond the additive column), no changes to schema versioning, download, or apt-layout behavior.

## Clarifications

### Session

- Q: Should `max_build_dep_report_depth` be renamed or kept? → A: Keep the name and semantics; only the resolution coupling is removed. Renaming would churn contracts for no user value.
- Q: Where is circularity stored? → A: A `circular` column on `binaries` plus a `circular` flag on the offending DependencySpec in `binaries.dependencies`. Both additive.
- Q: Does US8 require deleting `virtual_resolutions` rows whose provider was superseded by a newer current row? → A: No deletion there — re-resolution updates the row (FR-016). Deletion is only for rows referencing nonexistent binaries (fresh/cleaned databases).
- Q: Should `config` command gain `log_dir` in text output? → A: Yes, for parity with the config contract; it is currently missing.

### Session (US8 revert & design conclusion)

- Q: Does re-running `init` start from a clean slate? → A: Only for the `provides`
  index snapshot (full-table rebuild matching the loaded merged index). All resolution
  state (`packages`, `binaries`, `sources`, links, `status`, verified flags, superseded
  history) and valid `virtual_resolutions` entries are preserved.
- Q: Can the full-table rebuild wipe rows when one source fails to load during re-init?
  → A: Theoretically yes, but this requires user intervention (deleting a cached index
  file while offline) or a degraded first init. Index files are static after the first
  successful init (spec 005: index refresh out of scope) and re-init loads from the
  local cache without fetching; a corrupt file crashes init loudly instead of producing
  a partial table. Per-source reconciliation was considered and rejected as unnecessary
  complexity.
- Q: This is really an edge case, no? How could the Packages.gz be deleted/fail to load, 
  other then by user intervention? It's a static file in the cache.
  → A: You're right — it's essentially theoretical. 
  The only paths to a partial wipe are deleting the cached file while offline, or a 
  deliberately degraded first init — both are user intervention, exactly as you said. 
  The per-source reconciliation would be complexity for a case that can't occur in the 
  supported workflow, so I agree: keep the simple full-table rebuild, no change needed.
- Q: Was US8 implemented? → A: A first implementation of US8 (T024–T027) was completed
  and then reverted (git rollback to `phase 8`); tasks.md Phase 9 carries
  the full conclusion note. Re-implement per that note when resumed.

### Session (US9 quickstart validation — real-mirror findings)

The 005/006 quickstart scenarios were run end-to-end against the real Debian archive
(trixie/main + trixie-backports/main). Resolution, source-targeted adds, status
history, provides population, and verify all worked; three real defects were found
and fixed (all covered by unit tests, full suite 98/98):

1. **Spec 006 FR-002 amendment — provides natural key**: the PK
   `(package_name, version, architecture, release, component, virtual_name)` omits
   `provides_version`, so multi-versioned provides (e.g. `debhelper` providing
   `debhelper-compat (= 9) … (= 13)`) collapse into one row. The PK now includes
   `provides_version`. Spec 006 data-model.md carries the amendment marker.
2. **Legacy comparison operators**: `compare_versions()` now handles Debian legacy
   `>>` (strictly greater) alongside modern `>`; previously `perl (>> 5.005)`-style
   constraints always compared false.
3. **Dual real+virtual names**: when a name is both a real package and a provides
   (e.g. `python3-multipart` real 1.2.1 and provided (= 0.0.20)), a constraint
   unsatisfiable by real versions now falls back to virtual provider selection
   instead of failing (dpkg-like behavior).
