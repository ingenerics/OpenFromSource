# Feature Specification: Local Patch Lane

**Feature Branch**: `021-local-patch-lane`
**Status**: Draft

**Input**: User description: "Spec local patch lane - The case '4. Patch lane: deploying locally rebuilt/modified packages through the same channel' from high_value_alternative_use_cases.md. Create a new pseudo component `local`. Command syntax `import-deb <file>` The `--component local` is redundant, we only allow to import into `local`. An optional `--source <file>` is desired, however. We already have an (implicit) priority between components, without explicit component specification in the command, the `local` component should have highest prio."

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Curates the cache; builds or patches a package locally and imports the result into the cache's `local` component for distribution |
| **Operator** | Offline system (no network) | Installs and upgrades packages — including locally patched ones — from the pre-built cache through the ordinary apt channel |

**Phase 1 — Import**: The administrator takes a locally built or modified `.deb` (e.g. the relaxed-pin `libxkbcommon-x11-0` rebuild from the version-drift tutorial) and registers it in the cache under a new pseudo-component `local`. The command re-resolves the package's declared dependencies against the cache, copies the payload into the pool, and marks it verified. Optionally, the rebuilt source is imported alongside so the pair is provenance-traceable. Run by the Administrator.

**Phase 2 — Distribution**: The `local` component flows through the existing `sync`/`handoff` machinery exactly like `main`: exclusion filters carry it, the handoff's apt configuration lists it, and the operator installs and upgrades the patched package with ordinary apt tooling — no `dpkg -i` on removable media. When the resolver selects a package without an explicit component, `local` wins over every upstream component. Run by the Administrator (ship) and Operator (consume).

---

### User Story 1 - Import a Locally Built Package into the `local` Component (Priority: P1)

The administrator has a locally built or patched package — a `.deb` they produced themselves, such as the `libxkbcommon-x11-0_1.7.0-2_amd64.deb` rebuilt with the relaxed `>= 1.7.0` pin from the version-drift tutorial. They run `import-deb <file>`. The package is registered in the cache under the `local` component with its declared version, architecture, and dependencies; its payload is copied into the pool and marked verified; and it becomes a first-class citizen of the cache — listed, verifiable, resolvable, and installable like anything else. The command never asks which component: every import targets `local`.

**Why this priority**: This is the essence of the feature — turning "curate and optionally modify" from a README bullet into a concrete action. Without it there is no patch lane at all.

**Independent Test**: Build a trivial local `.deb` whose dependencies are already in the cache, run `import-deb <file>`, then confirm the package is registered under `local`, verified, present in the pool, resolvable from the cache index, and installable — all without touching any upstream archive.

**Acceptance Scenarios**:

1. **Given** a locally built `.deb` file whose declared dependencies are all satisfiable from the cache, **When** the administrator runs `import-deb <file>`, **Then** the package is registered in the database under the `local` component with its declared version, architecture, and dependencies; the payload is copied into the pool; and the record is marked verified.
2. **Given** a locally built `.deb` file, **When** the administrator runs `import-deb <file> --component <anything>` (or any other component selector), **Then** the command rejects the extra option — there is no component selector; every import targets `local`.
3. **Given** an imported `local` package, **When** the administrator runs the standard `list`, `show`, and `verify` commands, **Then** the package appears exactly like any other cached package — listed, inspectable, and integrity-verified.

---

### User Story 2 - Ship the Patch Through the Same Channel (Priority: P1)

The locally patched package reaches operators the same way upstream packages do. The administrator runs the ordinary `sync`/`handoff` flow; the `local` component is carried through the existing exclusion filters; and the handoff's apt configuration lists `local` alongside the other components. The operator applies the diff or handoff, and the patched package installs and upgrades through ordinary apt — no manual `dpkg -i`, no removable-media side channel, full provenance preserved.

**Why this priority**: The case's whole point is "distributed with full provenance instead of `dpkg -i` on removable media". Distribution is what makes the curation story real.

**Independent Test**: Import a package into `local`, run `sync`/`handoff`, transfer to an offline box, apply, and confirm the package appears in the operator's apt index, installs, and is upgradeable — with the `local` component present in the configured apt sources.

**Acceptance Scenarios**:

1. **Given** a cache containing `local`-component packages, **When** the administrator runs `handoff` and `sync`, **Then** the `local` component is emitted in the handoff's apt configuration (`deb ... <release> main local`), the packages survive the exclusion filters, and the diff/index includes them.
2. **Given** a diff or handoff containing a `local` package, **When** the operator applies it and runs `apt install <package>`, **Then** the package installs cleanly from the cache with no network access and no manual `dpkg -i`.
3. **Given** a `local` package already installed on the operator, **When** a newer `local` revision is synced, **Then** `apt upgrade` updates it to the newer revision through the same channel.

---

### User Story 3 - Import the Rebuilt Source for Provenance (Priority: P2)

The administrator also wants the rebuilt source recorded, so the patched binary is not an orphan: it is linked to the exact source it was built from, and the pair is deterministically rebuildable later. They run `import-deb <file> --source <sourcedir>`, pointing at a **directory** that contains the complete rebuilt source package — the `.dsc` *and* every file it references (the upstream tarball and the Debian packaging tarball, exactly as `dpkg-source -x <dsc>` needs them all present together). A bare `.dsc` alone would deliver nothing, so a directory holding the whole set is required. The source is registered under `local` and linked to the imported binary.

**Why this priority**: Provenance and "you don't have to trust what you can't rebuild" are core to the project (Constitution III). This is the SBOM-completeness half of the case, but a binary alone still delivers value, so it ranks below US1/US2.

**Independent Test**: Import a binary with `--source`, then confirm the source is registered under `local`, linked to the binary, and that the recorded linkage matches a rebuild of that source.

**Acceptance Scenarios**:

1. **Given** a locally built binary and a directory containing the complete rebuilt source package (`.dsc` plus every file it references), **When** the administrator runs `import-deb <file> --source <sourcedir>`, **Then** the source is registered under the `local` component with all of its files, linked to the imported binary, and every referenced file is verified against the `.dsc`'s declared checksums — making the pair provenance-traceable and rebuildable.
2. **Given** an import without `--source`, **When** the binary is imported, **Then** the binary is registered with no source linkage (source remains optional), and the cache's integrity checks do not fail on the missing source.
3. **Given** a `--source` path that is missing, unreadable, not a directory, or a directory missing any file the `.dsc` references (or containing files whose checksums do not match the `.dsc`), **When** the import runs, **Then** a clear error names the problem and the command fails without registering the binary.

---

### User Story 4 - `local` Wins When No Component Is Specified (Priority: P2)

When the resolver picks a package without an explicit component request, a `local` package takes precedence over the same-named package in any upstream component **at the same version**. So a patched package in `local` shadows the stock one wherever resolution happens implicitly — exactly the shadowing the patch lane is for.

**Why this priority**: This is the implicit-priority behavior the description calls out; it makes the patch actually take effect without forcing every downstream reference to name `local`.

**Independent Test**: With the same package name present in both `main` and `local`, resolve it without an explicit component and confirm the `local` version is selected.

**Acceptance Scenarios**:

1. **Given** the same package name at the same version present in an upstream component and in `local`, **When** the resolver selects it without an explicit component, **Then** the `local` version is chosen over every upstream component (version-first ordering, with `local` as the highest-priority component tie-break).
2. **Given** no `local` version of a package, **When** the resolver selects it, **Then** the existing upstream component priority applies unchanged (behavior for non-`local` packages is unaffected).
3. **Given** a `local` version that is strictly older than an available upstream version, **When** the resolver selects it without an explicit component, **Then** the newer upstream version is chosen — upgrade semantics are preserved; `local` only breaks ties at equal version.

---

### Edge Cases

- What if the `.deb` file is missing, unreadable, or corrupt? → A clear error names the file, the command exits non-zero, and nothing is registered or copied (US1, FR-008).
- What if the file is not a valid Debian package (wrong type/format)? → A clear error, non-zero exit, zero changes.
- What if the imported binary declares a dependency that is not satisfiable from the cache? → The import fails with an error naming the unsatisfied dependency and no partial registration (FR-003). The administrator resolves the gap (e.g. by importing the dependency too) and re-runs.
- What if a package with the same name, version, and architecture is already in `local`? → Re-import is idempotent: no duplicate records, payload verified once.
- What if the payload architecture differs from the host's default? → The architecture is recorded as declared; the cache remains multi-architecture-aware and installs what is applicable.
- What if `--source` points at a path that is not a directory, or a directory missing any file the `.dsc` references, or files whose checksums do not match the `.dsc`? → A clear error names the problem; the binary is not registered (US3 AS3).
- What if a `local` package shares a name with an upstream package at a different version? → Resolution is version-first: the highest version wins; at equal version the `local` component tie-break decides in its favour (US4). An explicit component request still works.
- What if the handoff/deb line already lists multiple components? → `local` is added to the existing per-release multi-component list; no new release is introduced.
- What if the imported payload is large? → Import copies a single file with integrity verification; the pool copy is a plain file copy.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The `import-deb` command MUST accept exactly one `.deb` file path per invocation (multiple `.deb` paths are an error) and MUST register the import under the `local` pseudo-component; the command MUST NOT expose or accept a component selector (every import targets `local`, and any attempt to name a component is an error).
- **FR-002**: The command MUST register the imported binary's metadata (package name, version, architecture, declared dependencies) in the database under the `local` component BEFORE any payload is copied, consistent with database-first resolution.
- **FR-003**: The command MUST re-resolve the imported binary's declared dependencies against the cache's index; if any declared dependency cannot be satisfied from the cache, the import MUST fail with an error naming the unsatisfied dependency and MUST NOT register or copy anything (no partial import).
- **FR-004**: On success, the command MUST copy the imported payload into the pool under the `local` component, compute its integrity checksum, and mark the record verified. The `local` component MUST use exactly the same directory layout as every other component — the same pool structure, the same per-component index paths (`dists/<release>/<component>/binary-<arch>/...`), and the same handoff/diff paths — so `local` is indistinguishable from upstream components in how it is stored, indexed, and served.
- **FR-005**: An optional `--source <dir>` MUST import the complete rebuilt source package from the given directory — the `.dsc` plus every file it references, each verified against the `.dsc`'s declared checksums — register it under the `local` component, and link it to the imported binary so the pair is deterministically rebuildable and provenance-traceable. A source path that cannot supply the complete, checksum-valid file set MUST be rejected.
- **FR-006**: The `local` component MUST have the highest priority in implicit component resolution: when no explicit component is requested, a `local` package MUST be preferred over the same-named package at the same version in any upstream component (version-first ordering, with `local` as the highest-priority component tie-break); a strictly newer upstream version still wins.
- **FR-007**: `sync` and `handoff` MUST carry `local`-component packages through the existing exclusion filters, and the handoff's apt configuration MUST include the `local` component so the operator can install and upgrade them with ordinary apt.
- **FR-008**: Invalid input — missing, unreadable, corrupt, or wrong-type files; unsatisfiable declared dependencies; or invalid `--source` artifacts — MUST produce a clear error and a non-zero exit with zero database and pool changes.
- **FR-009**: Re-importing an identical binary (same name, version, architecture) MUST be idempotent — no duplicate records, with the payload verified once.
- **FR-010**: Imported `local` packages MUST be subject to the same integrity machinery as upstream packages — they flow through `verify` and are checksum-checked before installation.

### Key Entities *(include if feature involves data)*

- **Binary (DB table, `component = local`)**: An imported package record carrying name, version, architecture, declared dependencies, and the `verified` flag — registered under the new pseudo-component.
- **Source (DB table, `component = local`)**: An optional imported rebuilt-source record (name, source_version) linked to its binary.
- **BinaryToSource (DB link table)**: Links an imported binary to its rebuilt source for deterministic-rebuild provenance (populated when `--source` is given).
- **Component priority (resolver ordering)**: `local` sits above `main` in the existing implicit component ordering, so implicit resolution prefers it.
- **Pool payload (local)**: The imported `.deb` file stored under the `local` component's pool area. It follows exactly the same pool layout as archive-fetched components (`pool/<component>/<prefix>/<package>/...`) — only the payload source differs (provided at import time, never fetched from an archive).
- **Handoff/Diff index entries**: The `local` component appears in the operator's apt configuration and per-component indexes produced by `handoff`/`sync`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A locally built `.deb` is imported into the `local` component with a single command and installs on an offline operator via the ordinary apt channel with zero network access and no `dpkg -i`.
- **SC-002**: 100% of an imported package's declared dependencies are satisfied from the cache, or the import fails with a clear error and zero partial changes.
- **SC-003**: When the same package name exists at the same version in both `local` and an upstream component, implicit resolution selects the `local` version 100% of the time; a strictly newer upstream version still wins.
- **SC-004**: `sync`/`handoff` deliver `local` packages exactly like upstream ones — an operator applying the result sees the package in the apt index and can install and upgrade it.
- **SC-005**: Every imported binary that also imports its source (`--source`) is linked to that source, and the pair is deterministically rebuildable (provenance recorded).

## Assumptions

- **Persona**: `import-deb` is an administrator-side command, run on the networked build box alongside `add` and `ensure-base`; the operator never runs it.
- **Component scope**: `local` is a component within the existing default release, not a new release — the handoff's per-release multi-component `deb` line simply gains `local` (FR-007).
- **No archive fetch**: `local` packages are never fetched from any archive; the payload is exactly the `.deb` supplied at import time, and the record is verified from that payload.
- **Identical layout**: The `local` component uses exactly the same directory layout as every other component — pool, per-component indexes, and handoff/diff paths — so no special-casing is needed anywhere downstream; only the payload origin differs (FR-004).
- **No component flag**: Because only `local` is permitted, the command exposes no `--component` option at all; an unknown option such as `--component foo` is a normal CLI error (FR-001).
- **Single file**: `import-deb` accepts exactly one positional `.deb` path per invocation; passing more than one `.deb` is an error (FR-001). `--source <dir>`, when given, applies to that single imported binary.
- **`--source` form**: `--source <dir>` points at a directory containing the complete rebuilt source package — the `.dsc` and every file it references (upstream tarball and Debian packaging tarball, exactly the set `dpkg-source -x <dsc>` requires to be present together). A bare `.dsc` alone cannot be unpacked, so a directory holding the whole set is required; each referenced file is verified against the `.dsc`'s declared checksums. The files are stored in the source pool under the same layout convention as binary components (FR-005).
- **Duplicate imports**: Re-importing an identical binary is idempotent rather than an error (FR-009).
- **Architecture**: Payload architecture is recorded as declared; the cache remains multi-architecture-aware, and the host's default architecture (amd64) is the primary target.
- **Scope boundary**: This feature covers importing and distributing already-built packages. It does not build packages, does not modify upstream binaries, and does not change how upstream packages are fetched.

## Clarifications

### Session

- Q: What is the exact `local` priority semantics — "always wins" or a tie-break within the existing component priority? → A: Version-then-component tie-break (option B). `local` is the highest-priority component, so it wins at equal version; a strictly newer upstream version still wins (upgrade semantics preserved). Resolves the earlier spec/design contradiction (spec.md US4 AS1 + Edge Cases vs research.md §2). Applied to US4, FR-006, SC-003, and the Edge Cases.
- Q: May `import-deb` accept multiple `.deb` files, and if so how does `--source` apply? → A: No — `import-deb` accepts exactly one `.deb` per invocation; multiple `.deb` paths are an error (user decision). `--source <dir>`, when given, applies to that single imported binary. Applied to FR-001, the Edge Cases, and the "Single file" Assumption.
