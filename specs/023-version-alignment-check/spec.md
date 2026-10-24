# Feature Specification: Version Alignment Check

**Feature Branch**: `023-version-alignment-check`
**Status**: Draft

**Input**: User description: "Spec version alignment check - To prevent version drift lets find the historical Hyprland sources that would match against the ensure-base cache we have installed. Take the info from the current session along and work it out into a spec. First part is determining the right versions in the git repos of interest (also downloading those repos to `repos` dir in root. Next a comprehensive workflow with documentation is desired to build the entire stack and get to a functional setup."

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Determines the version-aligned Hyprland source set, downloads the pinned sources into `repos/`, builds and validates the stack against the cache |
| **Operator** | Offline system (no network) | Receives the validated cache (and, depending on the distribution model, the built artifacts) and reaches a functional Hyprland setup using only cached packages |

**Phase 1 — Version Alignment & Source Acquisition**: The Administrator determines which historical Hyprland release "era" — Hyprland plus its lockstep companions (aquamarine, hyprutils, hyprlang, hyprcursor, hyprgraphics, hyprwayland-scanner, hyprland-protocols, hyprpaper, hypridle, hyprlock, xdg-desktop-portal-hyprland) — has every declared build and runtime dependency requirement satisfied by the frozen trixie apt tree cached locally, and downloads those exact source trees (repos + submodules) into a `repos/` directory at the project root, pinned to precise commits. The four niche tools (hyprpicker, hyprpolkitagent, hyprland-qtutils, hyprsunset) are out of scope entirely — not researched, not acquired, not built (Q4). Run by the Administrator.

**Phase 2 — Build & Functional Setup**: The stack is built bottom-up against the cache (the sole package/source provider, no network) and installed in place on the target; a functional Hyprland setup is produced and validated: a session launches with the essential companion daemons running. For this feature the artifacts are installed directly on the target from the cache; packaging the stack into the cache's `local` component for offline `apt` installation is an explicitly scoped follow-up. Run by the Administrator (validation) and the Operator (consumption).

---

### User Story 1 - Determine the Version-Aligned Hyprland Source Set (Priority: P1)

The Administrator researches the Hyprland git history and selects a single coherent release era — one Hyprland ref plus the matching companion refs — such that every dependency version each project declares (build-time and runtime) is satisfied by a package version already present in the cached trixie tree. The selection deliberately excludes versions whose dependencies are only available via `trixie-backports` or sid, because those introduce version drift and eventual system breakage. The result is recorded as a machine-checkable version manifest.

**Why this priority**: This is the crux of the feature and the constitution's Principle II in action (Hyprland is the named canonical example). Without the right era, either the build fails against the frozen tree or the cache drifts toward backports/sid. Everything downstream depends on getting the alignment right.

**Independent Test**: The version manifest lists each repo's tag + commit SHA. An automated alignment check (`align-repo`, Q7) determines the newest satisfying era and produces the manifest on success; it compares every dependency version requirement declared by each ref (in its build metadata) against the cached trixie versions and reports zero unmet requirements. The check can be run in isolation before any source is built.

**Acceptance Scenarios**:

1. **Given** the cached trixie apt tree versions, **When** the Administrator runs the alignment check over the chosen manifest, **Then** every declared dependency version requirement of every ref is satisfied by a cached version (no unmet requirements).
2. **Given** a candidate Hyprland ref, **When** it is checked, **Then** it does not require any dependency version that exists only in `trixie-backports` or sid (no version drift by construction).
3. **Given** the chosen era, **When** companion refs are cross-checked against each other and against the chosen Hyprland ref, **Then** each companion's requirements are satisfied by the selected set (the ecosystem is internally consistent, as expected from lockstep releases).

---

### User Story 2 - Acquire the Pinned Sources into `repos/` (Priority: P1)

The Administrator downloads every selected repository — and any submodules the chosen refs vendor (e.g. Hyprland's own wlroots fork or protocol headers in eras that use them) — into a `repos/` directory at the project root, each checked out at the exact commit recorded in the version manifest. The acquisition is complete and self-contained: after it finishes, no network access is required to build.

**Why this priority**: The sources are the build inputs. They must be fully and deterministically captured at alignment time, because the Operator's environment is offline. A partial or unpinned acquisition silently breaks reproducibility later.

**Independent Test**: For every repo in the manifest, the checked-out tree in `repos/` matches the pinned commit (`git rev-parse HEAD` equals the manifest SHA), submodules are present and at their recorded commits, and the tree contains the files the build needs. This is verifiable offline. Source authenticity relies on this SHA pinning (TOFU, Q10) — no GPG signature verification of git tags/commits is performed during acquisition.

**Acceptance Scenarios**:

1. **Given** the version manifest, **When** the sources are acquired into `repos/`, **Then** each repo's `HEAD` equals the manifest's pinned SHA and no network is required thereafter.
2. **Given** a chosen ref that vendors submodules, **When** acquisition completes, **Then** all submodules are present at their recorded commits (the tree is buildable offline as-is).
3. **Given** the `repos/` directory, **When** a build is attempted with the network disabled, **Then** no source file is missing (nothing is lazily fetched at build time).

---

### User Story 3 - Build the Core Stack from the Cache (Priority: P1)

The stack builds bottom-up against the cache as the sole package and source provider: the wayland/protocol tooling first, then the companion libraries in dependency order, then Hyprland itself. Any build tool or helper package the build requires that is missing from the cache is added to the cache and recorded in the dependency graph (Constitution VIII) before the build proceeds. Every component compiles with the trixie system libraries already cached (wayland 1.23, libdrm, mesa/GBM, glslang, libinput, xkbcommon, etc.) — no backport or sid library is pulled in.

**Why this priority**: A working build against the frozen tree is the concrete proof that the alignment (US1) was correct. It is the highest-value validation and unblocks everything in Phase 2.

**Independent Test**: With the network disabled, run the documented build for the full stack using only the cache; every component produces its expected artifacts (libraries, headers, binaries) with no build failures.

**Acceptance Scenarios**:

1. **Given** a Debian trixie amd64 system whose only package source is the cache, **When** the bottom-up build runs with the network disabled, **Then** every component of the chosen set builds successfully with zero failures.
2. **Given** a build tool required by a component that is not in the cache, **When** the build is attempted, **Then** the tool is added to the cache and recorded as a dependency before building (no silent use of an out-of-cache tool).
3. **Given** the pinned refs, **When** the build is repeated from a clean state, **Then** it produces the same artifacts (reproducible inputs → reproducible outputs).

---

### User Story 4 - Reach a Functional Hyprland Setup (Priority: P2)

The Administrator (and subsequently the Operator, per the distribution model) reaches a functional Hyprland setup: the compositor launches a session, and the essential companion components are present and running (wallpaper, screen locker, idle daemon, portal). The scope of "functional" is fixed by Q2 (essential companions: hyprpaper, hyprlock, hypridle, xdg-desktop-portal-hyprland), and the delivery mechanism by Q1 (in-place installation on the target for this feature). A validation procedure confirms the session works using only cached packages.

**Why this priority**: The ultimate goal is a usable system, not just a successful compile. This story turns "the stack builds" into "the user has a working Hyprland environment offline".

**Independent Test**: On the target, launch the Hyprland session from the installed/cached artifacts with the network disabled; confirm the compositor starts, the configured companion daemons are running, and the session is interactive. The session is validated by an in-project scripted smoke test (Q9): it starts the session offline (on a target VT/display), asserts the compositor and the essential companion processes (per Q2) are running, and exits pass/fail — repeatable on a fresh target.

**Acceptance Scenarios**:

1. **Given** a target trixie system with the built stack available from the cache, **When** the operator starts Hyprland, **Then** a usable Wayland session starts with no network access.
2. **Given** the selected companion set, **When** the session is running, **Then** each selected companion (per Q2) is present and operational.
3. **Given** a fresh target, **When** the documented setup procedure is followed from the cache, **Then** the same functional session results (procedure is repeatable, not a one-off).

---

### User Story 5 - Package the Stack into the Cache `local` Component (Priority: P3 — follow-up, out of scope for this feature)

The intended eventual distribution model is to package the built stack as Debian packages **together with their source packages** and import them into the cache's `local` component (the spec 021 path), making the stack installable on the Operator's system with ordinary `apt` from the cache, offline. The sources are imported alongside the binaries — the `.dsc` and referenced source files for every packaged component, checksum-verified via the spec 021 `import-deb --source` mechanism — so the `binary_to_source` linkage (Constitution III) holds: every shipped binary can be deterministically rebuilt from the imported source, and `verify` finds no unlinked or unverified records. Per Q1 this is **not** implemented in this feature (in-place installation is used for now); it is recorded here so the follow-up is explicit and does not lose the requirement.

**Why this priority**: It is the constitution-aligned, handoff-friendly delivery path and the user's stated ideal (Q1 "ideally A"); it is deferred because it substantially enlarges scope (per-component `debian/` packaging with lintian-clean output, plus source-archive assembly and checksum verification for every component).

**Independent Test**: (Follow-up) With the binaries **and their source packages** imported into the cache's `local` component, an offline `apt install` of the stack succeeds from the cache alone, and the complete source tree for every component can be retrieved offline via `apt-get source` from the cache.

**Acceptance Scenarios**:

1. **(Follow-up) Given** the built artifacts, **When** they and their source packages (`.dsc` + referenced files) are imported into the `local` component and the handoff index is regenerated, **Then** `apt install` of the stack succeeds offline from the cache.
2. **(Follow-up) Given** the imported packages, **When** `verify` runs, **Then** the stack's binaries and sources are linked and consistent (no unlinked or unverified records) — every binary has a matching source record via `binary_to_source` (Constitution III).
3. **(Follow-up) Given** a component's imported source, **When** the Operator runs `apt-get source` against the cache, **Then** the complete source tree (`.dsc` + referenced files) is retrieved offline.

---

### Edge Cases

- **Era ambiguity**: more than one Hyprland release era satisfies the alignment criterion → the manifest selection rule must prefer the newest satisfying era, and the choice must be recorded with its rationale in the version manifest (or surfaced to the Administrator for a decision when eras are not strictly ordered).
- **Companion drift within an era**: a companion bumped a dependency requirement mid-era (e.g. hyprlang required a newer hyprutils than the chosen Hyprland pins) → the alignment check must fail loudly with the specific unmet requirement and the offending pair, rather than silently building a stale pairing.
- **Boundary version (minimum exactly met)**: a requirement `>= V` where the cache holds exactly `V` → satisfied (the alignment check must use Debian version-comparison semantics, where an exact match satisfies `>=`).
- **Backports-only dependency**: a candidate ref requires a version available only in `trixie-backports` or sid → the ref is rejected (drift by construction), and the check names the offending dependency and version.
- **Vendored submodules**: the chosen era vendors a wlroots fork or protocol headers → the submodules must be captured at acquisition time at their recorded commits; a missing submodule is a failed acquisition, not a build-time surprise.
- **Compiler/language capability**: the era's C++ standard requirements must be satisfiable by the cached toolchain (gcc-13 13.3 / gcc-14 14.2); a ref requiring a newer compiler feature is treated as an unmet requirement and rejected.
- **Missing build tool in cache**: a required build tool (e.g. a specific meson feature, cmake module, or helper) absent from the cache → added to the cache and recorded as a dependency before building (Constitution VIII), never silently supplied from outside the cache.
- **Corrupt/incomplete source acquisition**: a `repos/` tree whose `HEAD` does not match the manifest SHA → acquisition must be re-run and the mismatch reported; a build from a mismatched tree is refused.
- **Offline reproducibility**: the build must not perform any network call; any attempt to fetch (git submodule update over the network, pip, curl) is treated as a failure and must have been pre-supplied in `repos/` or the cache.
- **Source authenticity (TOFU, Q10)**: the acquired sources are authenticated by pinning only — `HEAD` must equal the manifest SHA and submodules must be at their recorded commits; no GPG signature verification of git tags or commits is performed during acquisition. The pinned SHA makes any post-acquisition tampering detectable at verify time (FR-013).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The feature MUST determine a version-aligned source set for Hyprland and its companion projects such that every declared build- and runtime-dependency version requirement of every selected ref is satisfied by a package version present in the cached trixie tree.
- **FR-002**: The selected refs MUST be mutually consistent: each companion's dependency requirements MUST be satisfied by the selected set and by the selected Hyprland ref (a single lockstep release era).
- **FR-003**: The selection MUST exclude any ref whose dependencies are satisfiable only via `trixie-backports` or sid — the feature MUST NOT introduce version drift into the cache by construction.
- **FR-004**: The alignment MUST be validated against the exact cached trixie tree (the database and cached indexes), not against an assumed or stale version list; the validation MUST be automated and MUST fail, naming the unmet requirement and the offending ref, if any requirement is unmet.
- **FR-005**: The feature MUST record the outcome of the version research as a machine-checkable version manifest listing, for every repository: the repo, the selected tag, the exact commit SHA, and the reason the era was chosen. The manifest MUST be produced by the productized `align-repo` command, which determines the newest satisfying era and writes the manifest on success (Q7).
- **FR-006**: The feature MUST download the selected sources into a `repos/` directory at the project root, each checked out at the manifest's commit SHA, including any vendored submodules at their recorded commits, such that no network access is required to build thereafter.
- **FR-007**: The build MUST run against the cache as the sole package and source provider, with the network disabled; every build and runtime dependency MUST be the version recorded in the cached database / `Packages.gz` (the frozen trixie tree) — no dependency may come from outside the cache or at a version not recorded there; any build tool or helper required by the stack that is missing from the cache MUST be added to the cache and recorded in the dependency graph before building.
- **FR-008**: The feature MUST define and enforce a bottom-up build order for the stack (protocol/wayland tooling → companion libraries in dependency order → Hyprland → companion tools), so each component builds against its already-built dependencies.
- **FR-009**: Builds MUST be reproducible: pinned commits, a recorded build environment, and identical inputs MUST produce identical outputs.
- **FR-010**: The feature MUST produce a functional Hyprland setup — a session that launches and runs the essential companion components (hyprpaper, hyprlock, hypridle, xdg-desktop-portal-hyprland; plus the companion libraries they require) using only cached packages at the cached versions, with a documented validation procedure (the scripted smoke test, Q9).
- **FR-011**: For this feature the built artifacts MUST be installed in place on the target directly from the cache (Q1). Packaging the stack's binaries and their sources into the cache's `local` component and installing offline via `apt` (spec 021) is the intended follow-up (US5) and MUST be documented as such; it is not required by this feature.
- **FR-012**: The workflow MUST be documented in-project (Constitution VIII): the version manifest, the acquisition procedure, the build order and commands, the functional-setup procedure, and the validation steps, so a future Administrator or Operator can reproduce the outcome without external lookup.
- **FR-013**: The alignment check and acquisition MUST be re-runnable: `align-repo --verify <manifest>` re-validates a given manifest against the current cache and `repos/` (Q8), and the default search mode re-derives the era from them; a change to the cached tree or a re-run from a clean cache MUST re-validate and fail if the alignment no longer holds (Q7/Q8).
- **FR-014**: The feature MUST record every helper package and build tool it requires as part of the cache's dependency graph, so the complete build toolchain is reproducible offline (Constitution VIII).

### Key Entities *(include if feature involves data)*

- **Version manifest**: The alignment matrix — for each repository, the chosen tag, pinned commit SHA, and selection rationale; the machine-checkable artifact that US1 and FR-005/FR-013 validate. It is a file artifact (git-tracked), **produced by the `align-repo` command on success (Q7)**; the SQLite database is used read-only as the alignment baseline, and the source set is not recorded in DB tables (Q5).
- **`repos/` source tree**: The project-root directory holding each selected repository checked out at its pinned commit, with submodules, complete and buildable offline.
- **Cached trixie tree**: The frozen apt tree in the cache (database + cached indexes + pool) — the alignment baseline whose per-package versions every requirement is checked against.
- **Alignment check (`align-repo`)**: The productized tool with two modes (Q7/Q8): the default search mode scans the acquired repos in a directory, determines the newest satisfying era, and produces the version manifest on success — or reports failure naming the newest era's unmet requirements; the `--verify <manifest>` mode re-validates an existing manifest against the current cache and repos without searching or writing. It is delivered as maintained, in-project tooling (committed and tested), re-runnable per FR-013; source acquisition and the build are documented procedures driven by the manifest (Q6/Q7).
- **Build order**: The deterministic bottom-up sequence in which components are compiled (FR-008).
- **`local` component (conditional on Q1)**: The cache component into which packaged, built artifacts are imported for offline `apt` installation (spec 021 path).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The alignment check reports zero unmet dependency version requirements across the entire selected set, validated automatically against the cached trixie tree.
- **SC-002**: The full stack builds with the network disabled, using the cache as the sole package/source provider, with 100% of components building successfully.
- **SC-003**: A functional Hyprland session launches on the target using only cached packages, with the selected companion components running; the session passes the in-project scripted smoke test (Q9) and the procedure produces the same result on a fresh target (repeatable).
- **SC-004**: No selected ref requires a dependency version that exists only in `trixie-backports` or sid (version-drift-free by construction, verified by the check).
- **SC-005**: The complete workflow (version determination, acquisition, build, functional setup) is reproducible from a clean cache, and a reader can reproduce it from the in-project documentation alone.

## Assumptions

- **Target platform**: Debian trixie amd64 (the frozen apt tree the cache holds). Backporting to Bookworm is out of scope.
- **Alignment baseline — the cached trixie tree** (captured from the cache database during this session). The key versions every requirement is checked against include: wayland 1.23.1, wayland-protocols 1.44, libdrm 2.4.124, mesa/GBM 25.0.7, glslang 15.1.0, libinput 1.28.1, systemd/libudev 257.13, libxkbcommon 1.7.0, xwayland 24.1.6, cairo 1.18.4, pango 1.56.3, pixman 0.44.0, libseat 0.9.1, libdisplay-info 0.2.0, hwdata 0.394, libtomlplusplus 3.4.0, libzip 1.11.3, libjpeg 2.1.5, libpng 1.6.48, libmagic 5.46, pam 1.7.0, cmake 3.31.6, meson 1.7.0, ninja 1.12.1, gcc-13 13.3.0, gcc-14 14.2.0, nlohmann-json 3.11.3, spdlog 1.15.2, libfmt 10.1.1, libuv 1.50.0, Qt 6.8.2.
- **Hyprland ecosystem availability**: the hypr ecosystem is not packaged in trixie `main`; `trixie-backports` carries newer versions built against sid-era dependencies. The feature therefore builds the ecosystem from source against the frozen tree, deliberately excluding backports.
- **Lockstep releases**: the hypr ecosystem releases in lockstep; a single release era yields a mutually consistent set (US1/FR-002), so the search targets "the newest Hyprland era whose dependency requirements the frozen tree satisfies".
- **`repos/` location**: the pinned sources live in a `repos/` directory at the project root (per the user's instruction).
- **Build host**: a Debian trixie amd64 system with the cache as the only package/source provider; the documented procedure is identical for the Administrator (validation) and the Operator (consumption).
- **Dependency version pinning (Q3)**: every build and runtime dependency must be installable from the cache, and its version must be exactly the version recorded in the cached database / `Packages.gz` (the frozen trixie tree). No dependency may come from outside the cache, and no dependency may be at a version not recorded in the cache — in particular nothing from `trixie-backports` or sid. If a required dependency version is not in the cache, it must be added to the cache first (at a version the tree records) before the build proceeds.
- **Distribution (Q1)**: for this feature the stack is built and installed in place on the target directly from the cache. Packaging the stack's binaries and their sources into `.deb`s/`.dsc`+files and importing them into the cache's `local` component (spec 021) for offline `apt` installation is the user's ideal and is recorded as an explicit follow-up (US5), not implemented here.
- **Companion scope (Q2/Q4)**: the functional setup requires Hyprland plus the essential companions hyprpaper, hyprlock, hypridle, and xdg-desktop-portal-hyprland, together with the companion libraries Hyprland and these tools require (aquamarine, hyprutils, hyprlang, hyprcursor, hyprgraphics, hyprwayland-scanner, hyprland-protocols). The niche hyprwm tools (hyprpicker, hyprpolkitagent, hyprland-qtutils, hyprsunset) are out of scope entirely — not researched, acquired, or built, and absent from the version manifest and `repos/` (Q4). Third-party bars, launchers, and menu systems (e.g. waybar, rofi, wofi) are out of scope unless requested.
- **Version determination is a planning/implementation deliverable**: the actual chosen era, tags, and commit SHAs are produced and validated during the plan/tasks phase (the version manifest, FR-005), not hard-coded in this spec; the spec defines the selection criterion and the validation that the choice must pass.
- **Database role (Q5)**: the SQLite database is used read-only as the alignment baseline (the frozen trixie tree). The selected source set is recorded only in the file-based version manifest — no new DB schema or population step covers the git source trees. Constitution VII (DB-first) is satisfied by the cached apt metadata already in place; DB records for the stack's binaries/sources arrive with the packaging follow-up (US5/FR-011).
- **Delivery model (Q6)**: the alignment check is productized as maintained, in-project tooling (committed, tested, re-runnable). Source acquisition and the bottom-up build are delivered as documented procedures (FR-012) driven by the version manifest; they are not productized test targets in this feature.

## Clarifications

### Session

- Q1 — Distribution/installation model: → A: In-place build and install on the target directly from the cache for this feature (option B); the ideal of packaging into `.deb`s and importing into the cache's `local` component for offline `apt` installation (option A) is recorded as an explicit follow-up (US5, FR-011).
- Q2 — "Functional setup" scope: → A: Core Hyprland plus the essential companions hyprpaper, hyprlock, hypridle, and xdg-desktop-portal-hyprland (option B). The niche hyprwm tools (hyprsunset, hyprland-qtutils, hyprpolkitagent; hyprpicker treated as optional/out of the essential set) are out of scope unless requested. *(hyprpicker's status is superseded by Q4: fully out of scope — not researched, acquired, or built.)*
- Q3 — Build execution constraint: → A: Fully offline — all dependencies must be installable from the cache, at exactly the versions recorded in the cached database / `Packages.gz` (the frozen trixie tree); nothing from outside the cache and nothing at a version the cache does not record, in particular nothing from `trixie-backports` or sid (option A plus the explicit version-pinning constraint; FR-007).
- Q4 — Acquisition/build scope vs the lockstep family: → A: Only the 12 essential projects (Hyprland + aquamarine, hyprutils, hyprlang, hyprcursor, hyprgraphics, hyprwayland-scanner, hyprland-protocols, hyprpaper, hypridle, hyprlock, xdg-desktop-portal-hyprland) are researched, acquired into `repos/`, and built. The four niche tools (hyprpicker, hyprpolkitagent, hyprland-qtutils, hyprsunset) are out of scope entirely — not in the version manifest, not in `repos/`, not built (option A). Phase 1 and the companion-scope assumption updated to match.
- Q5 — Database-first application to the source set: → A: File-only manifest (option A). The SQLite database is used read-only as the alignment baseline; no new schema or population step records the git source trees. Constitution VII is satisfied by the cached apt metadata already in place; DB records for the stack's binaries/sources arrive with the packaging follow-up (US5/FR-011).
- Q6 — Delivery model: → A: The alignment check is productized as maintained, in-project tooling (committed, tested, re-runnable per FR-013). Source acquisition and the bottom-up build are delivered as documented procedures (FR-012) driven by the version manifest; they are not productized test targets in this feature (option A). *(Command shape refined by Q7: the productized tool is `package_mgmt align-repo --repo-dir DIR`, which produces the version manifest with success or failure.)*
- Q7 — Era-search delivery: → B (specific command): The era search is productized as re-runnable tooling. The bare-minimum command is `package_mgmt align-repo --repo-dir DIR`: it scans the acquired repos in `DIR`, determines the newest satisfying era, and produces the version manifest (`manifests/hyprland-<era>.toml`) with success or failure — exit 0 + manifest written on success; exit 1 (no era aligns) naming the newest era's unmet requirements. The era search and its validation are therefore the same re-runnable tool (FR-013); acquisition and build remain documented procedures (docs/005).
- Q8 — Manifest re-validation mode: → A: `align-repo` gains a `--verify MANIFEST` mode in addition to the default search-and-produce mode. It validates the given manifest against the *current* cache (`Packages.gz` and the DB) and `repos/`, re-running the same cache-tier + ecosystem-tier checks, and reports PASS (exit 0) or FAIL (exit 1) naming any unmet requirement — it never searches eras or writes a manifest. This is the explicit FR-013 re-validation mechanism after a cache/repos change.
- Q9 — Functional-session validation method: → A: Scripted, repeatable smoke test (option A). The functional setup is validated by an in-project validation script that starts the session offline (on a target VT/display), asserts the compositor and the essential companion processes (per Q2) are running, and exits pass/fail; repeatable on a fresh target. Applied to US4 Independent Test, SC-003, and FR-010.
- Q10 — Source acquisition integrity: → A: SHA pinning only, trust-on-first-use (option A). Acquisition authenticates sources by verifying `git rev-parse HEAD` equals the manifest SHA and submodules sit at their recorded commits; no GPG signature verification of git tags/commits is performed. The pinned SHA makes any tampering detectable at verify time (FR-013). Applied to US2 Independent Test and Edge Cases (source authenticity bullet).
