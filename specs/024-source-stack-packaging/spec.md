# Feature Specification: Source-Stack Packaging

**Feature Branch**: `024-source-stack-packaging`
**Status**: Draft

**Input**: User description: "spec packaging before import - Packaging the stack's binaries and their sources into the cache local component for offline apt install. Preserving the binary↔source linkage so every shipped binary can be rebuilt from the imported source. This spec is not tied to a single workflow (such as Hyprland) and should be solved generically. However, we don't want to overengineer either. Investigate what can be automated and what parts are better off being scripted. Use common sense. Think hard."

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Builds source-built components (e.g. the Hyprland stack from `docs/005`), packages their binaries **and** sources, and imports the result into the cache's `local` component for distribution |
| **Operator** | Offline system (no network) | Installs the packaged stack from the cache with ordinary `apt`, and can retrieve any component's source offline to rebuild it |

**Phase 1 — Package & Import a Component (generic)**: The administrator takes a source-built component — a pinned source tree and its staged install artifacts — and turns it, generically, into a Debian binary package (`.deb`) and a matching, rebuildable Debian source package (`.dsc` + referenced files). Both are imported into the cache's `local` component (the spec 021 lane) with the binary↔source linkage recorded, so the pair is installable and provenance-traceable. Run by the Administrator.

**Phase 2 — Package the Whole Set & Distribute**: The same mechanism consumes a multi-component recipe (e.g. the Hyprland stack), processes it in dependency order so every binary's dependencies resolve from the cache, and validates that the whole set installs offline. The `local` packages then flow to the Operator through the existing handoff/sync channel unchanged; the Operator installs them with ordinary `apt` and can rebuild any component from its imported source offline. Run by the Administrator (ship) and Operator (consume).

---

### User Story 1 - Package and Import One Source-Built Component (Priority: P1)

The administrator has built a source-built component from a pinned source tree (e.g. `hyprlang`, `hyprutils`, `sdbus-c++`, or any future source-built project) and staged its install artifacts into a per-component directory. They run one command with a small declarative recipe entry for that component. The component is packaged: a Debian binary package is produced from the staged files with a complete control record whose `Depends` is **derived automatically** from the binaries' linked runtime libraries, and a rebuildable Debian source package is produced from the pinned source tree at the recorded version. Both are imported into the `local` component and linked (`binary_to_source`), marked verified, and the package is installable from the cache like anything else. The tool contains **no knowledge of any specific stack** — the component is described entirely by the recipe.

**Why this priority**: This is the essence of the feature — a generic "packaging before import" step. Everything else (whole-set ordering, the Hyprland proof, rebuild validation) builds on this single-component pipeline. It is independently valuable: any source-built component can be distributed offline through the cache.

**Independent Test**: Build a trivial source-built component (or use one already built in `repos/`), stage its install, describe it in a one-entry recipe, run the packaging command, and confirm: a `.deb` with a correct auto-derived `Depends`, a complete `.dsc` + referenced source files, both registered under `local` and linked, verified, listed by `list`/`show`, and installable from the cache index — all offline, with no stack-specific code path.

**Acceptance Scenarios**:

1. **Given** a source-built component's staged install directory and its pinned source tree, **When** the administrator runs the packaging command with a one-entry recipe, **Then** a binary package and a complete, checksum-valid source package are produced, imported into `local`, and linked — the binary is `verified`, installable from the cache, and its source is retrievable from the cache.
2. **Given** a staged component whose binaries link against system libraries, **When** its `Depends` is derived, **Then** every linked runtime library is attributed to a package present in the cache (or the set being imported), and any library that cannot be attributed is reported by name — never silently dropped.
3. **Given** a recipe entry that declares explicit additional dependencies (e.g. a companion library not registered as a package on the build host), **When** the package is built, **Then** the declared dependencies are merged into the derived `Depends` and the package still imports and installs.

---

### User Story 2 - Package the Whole Set in Dependency Order and Validate Offline (Priority: P1)

The administrator packages a whole set of mutually dependent components (e.g. the Hyprland stack: protocols → scanner → companion libraries → Hyprland → source-built dependency → companion tools) from a multi-entry recipe. The packaging command processes the set in dependency order, so when each binary is imported its `Depends` is satisfiable from the cache — the frozen trixie tree plus the components already imported. The command then validates the completed set: `verify` reports no unlinked or unverified records, and a simulated offline `apt install` of the whole set succeeds against the cache index, with no network access.

**Why this priority**: A stack is only useful as a whole. The bottom-up ordering and the whole-set installability check turn the single-component pipeline into a distributable product, and they are the concrete proof that the auto-derived dependency graph is correct.

**Independent Test**: Package a multi-component set from a recipe with deliberately order-independent recipe entry order, and confirm the tool orders it correctly (dependencies before dependents), imports everything, and that a simulated offline install of the whole set plus `verify` both pass.

**Acceptance Scenarios**:

1. **Given** a multi-component recipe whose entries are listed in arbitrary order, **When** the packaging command runs, **Then** components are imported in dependency order and every binary's `Depends` resolves from the cache (frozen trixie + already-imported `local` packages).
2. **Given** the completed imported set, **When** `verify` runs, **Then** it reports no unlinked binaries and no unverified records for the set.
3. **Given** the completed imported set and the regenerated handoff/index, **When** an offline install of the whole set is simulated against the cache, **Then** it succeeds with no network access and no unmet dependencies.

---

### User Story 3 - The Hyprland Stack, Packaged End-to-End (Priority: P2)

The generic mechanism is proven on the real stack. A recipe for the Hyprland stack (the 13 build components of `docs/005` — the 12 hyprwm repositories plus the source-built D-Bus binding `sdbus-c++`) is written once, the existing build script gains a **staging mode** that installs each component into its own staging directory, and the packaging command imports the whole set. The Operator then installs Hyprland and its companions on an offline trixie system with ordinary `apt` from the cache (`apt install hyprland hypridle hyprlock hyprpaper xdg-desktop-portal-hyprland`), and the resulting session passes the existing scripted smoke test (`validate-hyprland-session.sh`).

**Why this priority**: This is the concrete, user-visible validation that the generic mechanism works on the project's canonical stack, and it replaces the in-place delivery model of `docs/005` with the constitution-aligned, handoff-friendly `apt` channel (the recorded follow-up of spec 023 US5).

**Independent Test**: From `repos/` + the cache, run the staged build and the packaging command with the Hyprland recipe, hand off / sync to an offline trixie target, `apt install` the stack, launch the session, and run the smoke test — all with no network and no `dpkg -i`.

**Acceptance Scenarios**:

1. **Given** the pinned `repos/` tree and the cache, **When** the staged build and packaging command run with the Hyprland recipe, **Then** every stack component (including the source-built `sdbus-c++`) is imported into `local` as a linked, verified binary+source pair.
2. **Given** the handoff to an offline trixie target, **When** the operator runs `apt install` for the stack, **Then** it installs cleanly from the cache with no network access and no manual `dpkg -i`.
3. **Given** the installed stack, **When** the session is launched and the smoke test runs, **Then** it passes (compositor + essential companions running), matching the in-place-built outcome.

---

### User Story 4 - Every Shipped Binary Can Be Rebuilt from Its Imported Source (Priority: P2)

The binary↔source linkage is not just recorded — it is demonstrated. For every imported binary, the linked source is retrievable offline from the cache (`apt-get source`), and a documented rebuild of that source (the same pinned tree and recorded build steps the package records) reproduces the shipped binary. `verify` finds every binary linked to an imported source. This is the constitution's Binary-Source Consistency (Principle III) made concrete for the source-built stack.

**Why this priority**: "You don't have to trust what you can't rebuild" is the project's core trust story. The in-place delivery of `docs/005` had the sources in `repos/`; this story proves the packaged artifact carries the same guarantee through the `local` channel.

**Independent Test**: Take one imported component (e.g. `hyprlang`), retrieve its source offline via `apt-get source` from the cache, run the recorded rebuild, and compare the resulting artifact against the shipped binary; confirm `verify` reports the whole set linked.

**Acceptance Scenarios**:

1. **Given** an imported stack component, **When** the operator retrieves its source from the cache offline (`apt-get source`), **Then** the complete source package (`.dsc` + referenced files) is retrieved, and it unpacks (`dpkg-source -x`) with the network disabled.
2. **Given** the unpacked imported source, **When** the documented rebuild runs against the cache, **Then** it reproduces the shipped binary (identical inputs → identical outputs).
3. **Given** the full imported set, **When** `verify` runs, **Then** every binary is linked to an imported, verified source — no unlinked binaries, no unverified records.

---

### Edge Cases

- **Un-attributable runtime library**: a staged binary links a library the packaging host cannot map to a Debian package (e.g. a companion library installed outside dpkg) → the tool reports it by name and the recipe supplies the explicit dependency; it is never silently dropped.
- **Emitted version constraint not satisfiable**: auto-derived `Depends` carries a version constraint (e.g. a companion lib version) that the imported `local` version does not satisfy → the tool relaxes or reconciles the constraint for the imported set (or fails loudly naming the pair) so the whole-set install stays satisfiable.
- **Component dependency missing from the cache**: a system library dependency of a packaged binary is not in the frozen trixie tree → the import fails naming the gap (reusing `import-deb`'s re-resolution), and the administrator must resolve it before the set can ship — never a silent or partial import.
- **Corrupt or incomplete staged files / source tree**: a staged directory or pinned source tree that cannot be packaged (missing files, dirty tree, unreadable) → a clear error names the component and the problem; nothing is registered or copied.
- **Source package not rebuildable**: the generated source package does not unpack cleanly (`dpkg-source -x`) or the recorded rebuild does not reproduce the binary → the packaging of that component fails loudly; rebuildability is validated, not assumed.
- **Reproducibility / determinism**: two packaging runs over identical inputs MUST produce identical `.deb` and `.dsc` artifacts (fixed timestamps/ordering); a non-deterministic artifact is a packaging defect.
- **Re-packaging the same upstream version**: rebuilding a component at the same upstream version after a change → a new, distinct Debian revision so `apt upgrade` on the operator still sees it; re-importing the identical pair stays idempotent (`import-deb`'s existing behavior).
- **Large sources**: source packages for the stack can be large → the pool copy is a plain, integrity-checked file copy (existing `import-deb` behavior); no special handling.
- **Services / config / data files**: a component installs non-`/usr/bin` files (e.g. portal service files, sessions, wallpapers) → file ownership is decided by the staged install (the staging directory IS the package data root), so nothing is missed or mis-attributed.
- **Multi-architecture**: payload architecture is recorded as declared; amd64 is the primary target and the stack is built/imported for it.
- **Missing source-built dependency in the recipe**: a component's source-built dependency (e.g. `sdbus-c++` for hypridle/hyprlock) is absent from the recipe → the dependent's `Depends` is unsatisfiable at import and the command fails naming it, so the recipe must include every source-built component the set needs.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The feature MUST provide a generic packaging command (`package-stack`) that consumes a declarative recipe and packages + imports every component it describes; the tool MUST contain no stack-specific knowledge — a component is fully described by its recipe entry (source tree, version, staged install directory, package metadata, explicit dependencies).
- **FR-002**: For each component, the command MUST produce a Debian binary package from the staged install directory with a complete control record (Package, Version, Architecture, Depends, Description, Maintainer, Section, Homepage), built with standard Debian packaging tooling.
- **FR-003**: The binary package's `Depends` MUST be derived automatically from the staged binaries' linked runtime libraries, attributed to packages present in the cache (or the set being imported); any library that cannot be attributed MUST be reported by name (the recipe may add or override explicit dependencies, which are merged in).
- **FR-004**: For each component, the command MUST produce a complete, rebuildable Debian source package (`.dsc` + every referenced file) from the pinned source tree at the recorded version, such that `dpkg-source -x` succeeds offline and the recorded rebuild reproduces the binary.
- **FR-005**: The command MUST import each binary and its source package into the cache's `local` component and record the binary↔source linkage (`binary_to_source`), reusing the spec 021 import machinery and its guarantees: dependencies re-resolved against the cache, every source file checksum-verified against its `.dsc`, and records marked verified.
- **FR-006**: For a multi-component recipe, the command MUST process components in dependency order (dependencies before dependents) so every binary's `Depends` resolves from the cache at import time — the frozen trixie tree plus the already-imported `local` components — regardless of the order the recipe lists them in.
- **FR-007**: The completed imported set MUST pass the existing integrity machinery (`verify` reports no unlinked binaries and no unverified records) and a simulated offline `apt install` of the whole set MUST succeed against the cache index with no network access.
- **FR-008**: Every imported binary's linked source MUST be retrievable offline from the cache via the apt source channel (`apt-get source`), unpackable offline, and rebuildable by the recorded build to reproduce the shipped binary (Constitution III). To enable this, the handoff MUST generate a `source/Sources` (deb-src) index for the `local` component alongside its binary `Packages.gz`, and the operator's sources configuration MUST include the `deb-src` entry for `local` (config only, no new operator tooling).
- **FR-009**: Packaging MUST be deterministic: identical inputs (same staged files, same pinned source tree, same recipe) MUST produce identical `.deb` and `.dsc` artifacts (fixed timestamps and ordering), consistent with the project's reproducible-build requirement.
- **FR-010**: Re-packaging a component at the same upstream version MUST produce a distinct Debian revision so `apt upgrade` semantics hold on the operator; re-importing an identical pair MUST stay idempotent (no duplicate records, payload verified once).
- **FR-011**: The per-stack knowledge MUST live outside the tool: the recipe file and the staged build (the existing per-stack build script gaining a staging mode that installs each component into its own directory) are provided per stack; the tool packages already-staged artifacts and never builds them.
- **FR-012**: Imported stack packages MUST flow to the operator through the existing handoff/sync channel unchanged (spec 021) and install with ordinary `apt`, offline, with no `dpkg -i`. The handoff channel is extended additively with the `source/Sources` index for `local` (FR-008); the binary package flow is unchanged.
- **FR-013**: The documentation MUST be updated in the same cycle (Constitution VIII): `docs/005` gains the packaging path, `roles.md` gains the new command, and the recipe format plus the automated-vs-scripted split are recorded so a future Administrator can reproduce the outcome.
- **FR-014**: Any packaging or import failure MUST be loud and atomic: a clear error names the component and the problem, and no partial records or partial pool files remain (reusing `import-deb`'s atomicity).

### Key Entities *(include if feature involves data)*

- **Packaging recipe**: The declarative, per-stack file (tracked, e.g. `recipes/hyprland.toml`) describing every component to package: source tree path, version, staged install directory, binary package name, metadata (description, maintainer, section, homepage), and explicit dependencies/overrides. The single input the generic tool needs — the boundary between "generic tool" and "per-stack knowledge".
- **Staged install directory**: The per-component `DESTDIR`-style directory holding exactly the files that component installs; it becomes the binary package's data root (file attribution).
- **Generated binary package (`.deb`)**: The Debian binary produced from a staged directory with an auto-derived `Depends` (FR-002/FR-003).
- **Generated source package (`.dsc` + files)**: The complete, rebuildable Debian source produced from the pinned tree at the recorded version (FR-004).
- **`local` component rows (DB)**: The imported binary and source records plus the `binary_to_source` linkage — the existing spec 021 / Constitution III schema, populated by this feature.
- **Staged build script**: The per-stack script (e.g. `scripts/build-hyprland.sh` gaining `--stage DIR`) that builds each component into its staging directory; the scripted half of the split (FR-011).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A source-built component with no Debian packaging of its own is packaged and imported into `local` with a single command; its `Depends` is derived automatically with no manual dependency list, and it installs from the cache offline.
- **SC-002**: A multi-component set imports in dependency order regardless of recipe ordering; 100% of the set's `Depends` resolve from the cache, and a simulated offline install of the whole set succeeds with zero unmet dependencies.
- **SC-003**: The Hyprland stack installs on an offline trixie operator via ordinary `apt` from the cache (no `dpkg -i`), and the resulting session passes the existing scripted smoke test.
- **SC-004**: 100% of imported binaries are linked to an imported, verified source; `verify` reports no unlinked or unverified records; every source is retrievable offline via `apt-get source`, and a documented rebuild of at least one component reproduces its shipped binary bit-for-bit.
- **SC-005**: Packaging is reproducible — two runs over identical inputs produce identical `.deb`/`.dsc` artifacts; re-packaging the same upstream version yields a distinct revision that `apt upgrade` on the operator applies.

## Assumptions

- **Automation vs scripting split (the design question)**: The generic, reusable work is automated in the tool: reading the recipe, binary-package generation, automatic `Depends` derivation, deterministic source-package generation, dependency-order processing, import+linkage (reusing spec 021), and whole-set verification (FR-001…FR-010). The per-workflow knowledge is scripted/declarative: the recipe file and the staged build (an extension of the existing per-stack build script) (FR-011). This split is deliberate: packaging, ordering, derivation, and verification are identical for every source-built stack and would be error-prone to hand-do; the component list, metadata, and build steps are different for every stack and belong in data, not code.
- **No overengineering**: The tool reuses the standard Debian packaging tools already in the project's toolset (`dpkg-deb`, `dpkg-shlibdeps`, `dpkg-source`); it does not reimplement package formats. It generates a minimal `debian/` wrapper that drives each component's own build system (meson/cmake — exactly how the stack already builds) rather than a full debhelper framework. It does not build components (building stays in the per-stack script) and does not touch the operator-side channel (handoff/sync/apt already carry `local`, spec 021).
- **`Depends` derivation**: automated derivation (via standard Debian tooling) covers system libraries; source-built companion libraries (not registered as packages on the build host) are covered by recipe-declared explicit dependencies. Constraint reconciliation for the imported set is handled so the whole-set install stays satisfiable (Edge Cases).
- **One runtime binary package per component**: for the stack, each component maps to one runtime binary package (no `-dev`/`-dbgsym` split); the data model supports more if a recipe asks.
- **Version scheme**: `<upstream-version>-1` (Debian revision) by default; a re-package of the same upstream version bumps the revision (FR-010); the recipe can override.
- **Staging**: packaging consumes already-staged install trees; the existing in-place install workflow of `docs/005` remains unchanged, and the build script gains an additive staging mode (FR-011).
- **Packaging host**: the administrator's build host doubles as the packaging host (it has the stack installed and the cache's build deps, so library attribution works); the command is admin-side, next to `import-deb`.
- **Scope**: The packaged set is the source-built stack (e.g. the 13 build components of `docs/005` — the 12 hyprwm repositories plus the source-built D-Bus binding `sdbus-c++` at 2.0.0). Upstream trixie packages are not re-packaged. The four niche hyprwm tools remain out of scope (as in spec 023).
- **Operator flow**: The operator consumes through the existing handoff/sync channel; no new operator-side tooling is in scope. The handoff is extended additively with a `source/Sources` (deb-src) index for `local`, and the operator's sources config gains a `deb-src` line for `local`, so `apt-get source` works offline (FR-008); the binary channel is unchanged (FR-012).

## Clarifications

### Session

- Q1 — Automation vs scripting boundary: → A: Generic, reusable steps are automated in a new `package-stack` command (packaging, auto-`Depends`, source-package generation, ordering, import+linkage, whole-set verification); per-stack knowledge stays in a recipe file and a staged-build mode of the existing build script. No [NEEDS CLARIFICATION] markers remain — the user's instruction to "use common sense / think hard" is resolved by this explicit split (FR-001/FR-011, Assumptions).
- Q2 — Proof set: → A: The generic mechanism is delivered with the Hyprland stack as the concrete end-to-end proof (US3), since that is the project's canonical source-built set and the recorded follow-up of spec 023 US5; the mechanism itself is recipe-driven and stack-agnostic.
- Q3 — Operator source-retrieval channel (FR-008): → A: Extend the handoff additively to generate a `source/Sources` (deb-src) index for the `local` component alongside its existing binary `Packages.gz`, and add a `deb-src` line for `local` to the operator's sources configuration — so the operator retrieves imported sources offline with standard `apt-get source`, reusing the signed Release channel. The binary package flow through handoff/sync stays unchanged (FR-012); this is config/index work, no new operator tooling.
