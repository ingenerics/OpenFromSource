# Feature Specification: Base Profiles

**Feature Branch**: `014-base-profiles`
**Status**: Draft

**Input**: User description: "spec base profiles - These profiles are not codified as classes in any way. The idea is to enable 3 different profiles showcased as separate project setting toml files in (for example) the doc folder. We will add tutorials for those later.

Profiles:
- regular_user: only the requested binaries, no sources or build deps. Maps to `sources = false` as currently implemented. Not likely to use `ensure-base`.
- developer: for the requested binaries, also fetch the sources and build deps, but only up to a specified runtime dependency depth (a new setting in toml file). Not likely to use `ensure-base`.
- graybeard: following the constitution principles. All is in the open, so to be able to enable full rebuild, all needs to be fetched recursively. Turtles all the way down. Main use case for `ensure-base`.

The reason for the new `max_runtime_dep_depth` setting is to allow for situations where smaller caches are desired.
To give an example, when adding a couple of packages through the graybeard profile, due to the recursive nature, it quickly amounts to >13700 binary packages and >25000 source packages being resolved. After downloaded this amounts to >30GB of packages."

**Design revision**: The setting is `max_source_dep_depth`. **Depth values** use a clear convention: `-1` = unlimited, `0` = none, positive `N` = within `N` levels (this replaces the old confusing "`0` = unlimited"). The runtime **binaries** are always resolved completely (installability + version alignment, Constitution II); only *sources and build tools* are depth-limited, so every fetched source is buildable. `max_build_dep_report_depth` follows the same convention (`-1` = report all levels, `0` = no build-tool levels reported). The shipped `project.defaults.toml` carries the **regular_user** presets (`[fetch] source = false`, `max_source_dep_depth = 0`) so novice users do not pull all sources by mistake. The `ProjectConfig` dataclass defaults **match the shipped presets exactly** — `fetch_source = False`, `max_source_dep_depth = 0` — so an unconfigured key behaves identically to the shipped regular_user default (one default story); `-1` is an explicit opt-in for unlimited. See [Clarifications](#clarifications) and [Assumptions](#assumptions).

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Chooses a usage profile by dropping the matching project-setting TOML file into place, then grows the cache (`add`, `ensure-base`, `download`, `verify`) accordingly |
| **Operator** | Offline system (no network) | Unchanged — consumes whatever cache the administrator shipped; the profile affects only cache content, never the operator's tooling |

**Phase 1 — Depth-Limited Source Support**: Add the `max_source_dep_depth` project setting (`-1` = unlimited, `0` = none, positive = levels) and enforce it during resolution so sources and build tools are provided only for packages within the configured depth, while the runtime binary closure stays complete; emit the depth-0 warning when source fetching is enabled. Run by the Administrator.

**Phase 2 — Profile Showcase Files**: Ship three complete, standalone project-setting TOML files (`regular_user`, `developer`, `graybeard`) in the docs folder, each a drop-in showcase of the settings for that profile; ship the regular_user presets in `project.defaults.toml`. Run by the Administrator.

**Phase 3 — Verification & Guidance**: Confirm the profile's intended cache shape (`verify` / `list` reflect the depth-limited source set or the full set; graybeard retains full rebuild capability), re-verify the `max_build_dep_report_depth` convention (`-1`/`0`/`N`), and document the intended use of each profile. Run by the Administrator.

---

### User Story 1 - Developer: Source-Ready Cache for the Requested Set (Priority: P1)

The developer profile fetches sources and build tools for the requested binaries and their dependencies up to a configured depth — `max_source_dep_depth` — while always resolving the full runtime binary closure so everything stays installable. Depth values: `-1` = unlimited, `0` = no sources fetched, positive `N` = sources/build tools for binaries within `N` levels. Adding a couple of packages with full recursion (graybeard, `-1`) can resolve to well over thirteen thousand binary and twenty-five thousand source packages (over 30 GB after download); the developer profile trades deep source/build-tool coverage for a small, source-ready cache. Every source in the developer cache is buildable — its build tools are present — and no source is fetched for a package whose build tools would not be available.

**Why this priority**: The source-dep depth cap is the only new engine behavior in this feature and the reason the profiles can differ in cache size. Without it, the developer profile would be indistinguishable from graybeard.

**Independent Test**: Configure `max_source_dep_depth = 1` with source fetching on, `add` a small package — the requested binary and its full runtime dependency closure are resolved, the requested binary and its direct runtime dependencies have their sources and build tools present, and no source beyond depth 1 is fetched; with `-1`, sources and build tools are fetched for the entire closure.

**Acceptance Scenarios**:

1. **Given** `max_source_dep_depth = 1` and `[fetch] source = true`, **When** the administrator adds a package, **Then** the requested binary and its full runtime dependency closure are resolved, the requested binary and its direct runtime dependencies have their sources and build-tool dependencies present, and no source (or build tool) beyond depth 1 is fetched.
2. **Given** the same package with `max_source_dep_depth = -1` (unlimited), **When** the administrator adds it, **Then** sources and build tools are fetched for the entire runtime closure — identical to current behavior.
3. **Given** `max_source_dep_depth = 0` with `[fetch] source = true`, **When** the administrator adds a package, **Then** no sources or build tools are fetched, a warning is shown that depth `0` fetches no sources (suggesting `-1` or a positive value), and the command succeeds.
4. **Given** `max_source_dep_depth = 0` with `[fetch] source = false`, **When** the administrator adds a package, **Then** binaries only are resolved with no warning (the functionality is disabled by `source = false`).
5. **Given** a finite positive depth, **When** the administrator adds a package, **Then** every binary at any depth is still resolved (the runtime closure is never truncated — the package remains installable), while sources are fetched only within the configured depth.
6. **Given** a depth below `-1` (e.g. `-2`), a non-integer, or a boolean in the TOML, **When** the configuration is loaded, **Then** a clear configuration error is shown.

---

### User Story 2 - Three Showcase Profile Settings (Priority: P1)

Three profiles, each a plain project-setting TOML file the administrator drops into place — not code, not classes:

- **regular_user** — only binary packages (with their full runtime closure); no sources, no build tools. Maps to `[fetch] source = false` and `max_source_dep_depth = 0`.
- **developer** — sources and build tools for the requested set and its dependencies up to a finite depth; full runtime closure always.
- **graybeard** — everything recursively ("turtles all the way down"): full rebuild capability per the Constitution, the intended `ensure-base` configuration. `max_source_dep_depth = -1`.

**Why this priority**: The profiles are the deliverable requested — usable, documented settings presets. They reuse the depth cap from US1 and the existing `source` setting; no new machinery beyond the depth cap is required.

**Independent Test**: Take each profile file in turn as the project setting, `add` the same package, and confirm the cache shape matches the profile (regular_user: no sources/build tools; developer: sources + build tools within the depth, full runtime closure; graybeard: full recursion with sources + build tools).

**Acceptance Scenarios**:

1. **Given** the `regular_user` profile file as the project setting, **When** the administrator adds a package, **Then** only binary packages are resolved (full runtime closure) with no source records, no build-tool links, and no warning.
2. **Given** the `developer` profile file as the project setting, **When** the administrator adds a package, **Then** the full runtime closure is resolved, sources and build-tool dependencies are present for every package within the profile's source depth, and no source beyond it is fetched.
3. **Given** the `graybeard` profile file as the project setting, **When** the administrator adds a package or runs `ensure-base`, **Then** resolution is fully recursive — every binary in scope has its source and its build tools, satisfying rebuild capability (Constitution III).
4. **Given** any of the three profile files, **When** it is used as the project setting, **Then** it loads as a valid, complete configuration and the applied settings (including the new depth setting) are shown by the existing configuration inspection.
5. **Given** the profile files, **When** they are inspected, **Then** they are plain TOML data files under the docs folder with no accompanying code or class definitions.

---

### User Story 3 - Profile Shape Is Verifiable and Sizeable (Priority: P2)

The administrator can confirm that the chosen profile produced the intended cache and that the depth setting achieves its purpose: a smaller cache. The developer and graybeard caches remain source- and build-tool-complete within their scope, and the developer source set is a strict subset of the graybeard set for the same packages. The `max_build_dep_report_depth` convention (`-1` = all levels, `0` = none, positive = up to `N`) behaves consistently at both special values without regressions.

**Why this priority**: Trust in the profile system rests on being able to observe the difference it makes — otherwise an administrator cannot know whether a small cache is merely incomplete or correctly scoped.

**Independent Test**: Add the same small package under `developer` (finite depth) and `graybeard` (`-1`), then compare the resolved sets and the `verify --graph` report — the developer source/build-tool set is strictly smaller, the runtime closure is identical, and the in-scope set verifies COMPLETE; run `verify --graph` with `max_build_dep_report_depth = -1` and `0` and confirm no regressions versus prior behavior.

**Acceptance Scenarios**:

1. **Given** a developer-profile cache, **When** `verify --graph` runs, **Then** the runtime closure is complete, every fetched source has its build tools present (FR-013), out-of-scope binaries (beyond the depth) are not reported as missing-source defects (informational count only), and the cache reports COMPLETE.
2. **Given** the same package added under `regular_user`, `developer`, and `graybeard`, **When** the resolved sets are compared, **Then** regular_user resolves fewer packages than developer and graybeard, developer is a strict subset of graybeard, and the developer and graybeard sets carry sources and build tools while regular_user does not.
3. **Given** a finite source depth versus `-1` for the same package, **When** the resulting caches are compared, **Then** the finite-depth cache resolves strictly fewer sources and build tools (the purpose of the setting) while resolving the identical runtime closure.
4. **Given** `max_build_dep_report_depth = -1`, `0`, and a positive value, **When** `verify --graph` runs against the same cache, **Then** `-1` reports all levels (identical to the prior "report all" behavior), `0` reports no build-tool levels, and a positive value reports up to that level — with no regressions at either special value.

---

### Edge Cases

- Depth unset → `max_source_dep_depth` defaults to `0` (none) — the dataclass default matches the shipped `project.defaults.toml`, so unconfigured == regular_user (binary-only, no warning). `-1` (unlimited) must be set explicitly.
- Invalid depth (below `-1`, non-integer, boolean) → clear configuration error at load time, before any resolution runs — for both `max_source_dep_depth` and `max_build_dep_report_depth`.
- `max_source_dep_depth = 0` with `[fetch] source = true` → warning that no sources will be fetched (suggest `-1` for unlimited or a positive value); command still succeeds. With `source = false` no warning is required (the functionality is disabled by the other flag).
- `max_source_dep_depth = -1` → unlimited; identical to the pre-feature full-source behavior (regression-preserving).
- A source beyond the depth limit → not fetched, no error, no partial records; `verify` does not treat its absence as a defect (out of scope by design; informational only — FR-015).
- Verify with a finite depth → binaries without a `binary_to_source` link are informational only, never defects (FR-015); the runtime closure and the buildability of the fetched sources (FR-013) determine COMPLETE. In-scope missing-source detection is deliberately not performed at finite depths (schema-freeze trade-off). With `-1`, the strict unlinked-binaries check applies unchanged.
- The runtime binary closure is NEVER truncated, even at a finite depth — a requested package's transitive runtime dependencies are always fully resolved, so installability and version alignment (Constitution II) are preserved.
- Version drift: because the runtime tree is always complete, the cache cannot drift into a state where a requested package's runtime constraints are unsatisfiable; this is the documented reason the cap applies to sources/build tools, never to runtime binaries.
- Coherence: every source present in a finite-depth cache is buildable — its build tools are resolved with complete runtime closures. No source is fetched for a package whose build tools would be unavailable.
- Build-tool binaries in a finite-depth cache → resolved as installable binaries (with their runtime closures) but do not themselves get sources (they are tools, not rebuild targets); this is the documented, scoped Constitution III exception for finite depths.
- `max_build_dep_report_depth = -1` → report all levels (identical to the prior `0` = report-all behavior); `0` → no build-tool levels reported (only the always-on depth-0 runtime check); positive → up to `N` levels.
- Developer profile run against `ensure-base` → the base set resolves with a complete runtime closure and sources/build tools to the configured depth; the default (`-1`) preserves the full base.
- graybeard build toolchains with circular dependencies → handled by the existing circular-dependency handling; rebuild capability is the goal, not unlimited disk.
- `regular_user` used with `ensure-base` → resolves binaries only (no sources/build tools), consistent with `source = false`.
- Changing the cap between runs → previously-fetched sources/build tools are retained (never pruned); the cap bounds new resolution only, so a cache may legitimately hold sources beyond the current cap. The knob is documented for the grow-deeper direction only (regular_user → developer depth 1 → developer depth N → graybeard); no constraint enforces this.
- A profile file used as `project.defaults.toml` alongside an existing `project.local.toml` → the local overlay still applies on top of the profile's defaults (existing merge behavior).
- The illustrative graybeard scale (>13 700 binaries, >25 000 sources, >30 GB for a couple of packages) depends on the package set and archive; it motivates the feature but is not a hard acceptance number.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST support a `max_source_dep_depth` project setting (integer) that controls source/build-tool support depth for the requested packages.
- **FR-002**: The depth values MUST be: `-1` = unlimited (sources and build tools for the entire runtime closure); `0` = none (no sources or build tools fetched); positive `N` = sources and build tools for binaries within `N` levels of the requested set. `project.defaults.toml` MUST ship `0` (regular_user presets).
- **FR-003**: The runtime dependency closure of the requested packages MUST always be resolved completely, regardless of the depth setting — the depth MUST NOT truncate runtime binaries (installability and version alignment, Constitution II).
- **FR-004**: With a positive finite depth `N`, sources (and their build tools) MUST be provided only for binaries within `N` levels of the requested packages; binaries beyond `N` levels MUST still be resolved, but their sources MUST NOT be fetched and their build tools MUST NOT be resolved — with no errors.
- **FR-005**: A depth below `-1`, a non-integer, or a boolean in the TOML MUST produce a clear configuration error before any resolution runs — for both `max_source_dep_depth` and `max_build_dep_report_depth`.
- **FR-006**: The system MUST ship three complete, standalone project-setting TOML files — `regular_user`, `developer`, `graybeard` — under the docs folder, as data files only (no classes, no profile-specific code).
- **FR-007**: The `regular_user` profile MUST map to `[fetch] source = false` and `max_source_dep_depth = 0`: binary packages resolved with their full runtime closure, no source records and no build-tool dependencies.
- **FR-008**: The `developer` profile MUST enable source and build-tool fetching with a finite positive `max_source_dep_depth`: the full runtime closure is resolved, and sources and build tools are fetched for every package within the configured depth (FR-004/FR-013).
- **FR-009**: The `graybeard` profile MUST enable source and build-tool fetching with `max_source_dep_depth = -1` (unlimited): every binary in scope has its source and its build tools (full rebuild capability, Constitution III); it is the intended `ensure-base` configuration.
- **FR-010**: The applied settings — including both depth settings — MUST be visible through the existing configuration inspection command.
- **FR-011**: For the same requested packages, the set of sources and build tools resolved under a finite positive depth MUST be a strict subset of the set resolved under `-1`, while the runtime closure MUST be identical.
- **FR-012**: The feature MUST document the intended use of each profile, the effect of the depth settings, and the version-drift rationale for never truncating the runtime closure; step-by-step tutorials are explicitly deferred to a later feature.
- **FR-013**: Every source fetched under a finite depth MUST have its build tools resolved with complete runtime closures, so every fetched source is buildable and no source is fetched whose build tools would be unavailable.
- **FR-014**: The depth setting MUST bound new resolution only — sources and build tools resolved under a wider or unlimited cap MUST be retained (never deleted or unregistered) when a shallower depth is later configured; the intended grow-deeper usage direction MUST be documented, but no monotonicity constraint MUST be enforced.
- **FR-015**: With a finite `max_source_dep_depth` (`0` or positive `N`), the verification command MUST NOT treat binaries without a `binary_to_source` link as defects — out-of-scope binaries legitimately have no source; they are reported only as an informational count ("beyond source depth or missing"). COMPLETE is determined by the runtime dependency closure and the buildability of the fetched sources (FR-013). With `-1` (unlimited), the existing "unlinked binaries" check applies unchanged (missing sources are defects — regression-preserving). No schema change: the gate reads the configured depth.
- **FR-016**: When `[fetch] source = true` and `max_source_dep_depth = 0`, the system MUST warn that no sources will be fetched (suggesting `-1` for unlimited or a positive value) while still succeeding; when `[fetch] source = false` the depth is irrelevant and no warning is required.
- **FR-017**: `max_build_dep_report_depth` MUST use the same depth-value convention — `-1` = report all levels, `0` = no build-tool levels reported, positive `N` = report up to `N` levels — with its default remaining `2`; `-1` MUST behave identically to the prior "report all" behavior (no regression).

### Key Entities *(include if feature involves data)*

- **ProjectConfig (existing, extended)**: gains a `max_source_dep_depth` integer field (default `0` = none, matching the shipped regular_user presets) and changes `fetch_source`'s dataclass default to `False` (also matching the shipped presets), so an unconfigured key == regular_user; `-1` = unlimited is an explicit opt-in. Parsed from the `[resolution]`/`[fetch]` sections of the project-setting TOML; `max_build_dep_report_depth` keeps default `2` with the redefined value convention.
- **Profile files (new, documentation artifacts)**: three complete, standalone project-setting TOML files under the docs folder — `regular_user`, `developer`, `graybeard` — each a drop-in showcase of the settings for its profile. Not code, not classes, not enforced.
- **Source scope (existing, derived)**: which binaries get source records and `source_build_deps` links, bounded by `max_source_dep_depth`; the runtime closure (existing) is never bounded. The boundary is NOT persisted (no schema change) — `verify --graph` derives it from the configured `max_source_dep_depth` and weakens the unlinked check accordingly (FR-015).
- **Resolution graph (existing, derived)**: the runtime dependency traversal; always complete. Nothing new is stored and no schema changes (the spec 010/011 freeze holds).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With a finite positive source depth, `add` fetches sources exactly up to the configured depth — no source beyond it is registered (queryable), and the source/build-tool set is a strict subset of the `-1` set for the same packages.
- **SC-002**: Each of the three profile files is a valid, complete project setting; using it produces its intended cache shape (regular_user: no sources/build tools; developer: sources + build tools within depth with a complete runtime closure; graybeard: full rebuild capability).
- **SC-003**: `project.defaults.toml` ships the regular_user presets (`source = false`, `max_source_dep_depth = 0`), so a fresh, unmodified installation resolves binaries only and fetches no sources — novice users cannot pull all sources by mistake.
- **SC-004**: For the same package set, the developer-profile cache resolves strictly fewer sources and build tools than the graybeard profile (the depth setting's purpose), with an identical runtime closure, and the difference is observable through existing tooling.
- **SC-005**: A graybeard cache satisfies Constitution III: every binary in scope has its source, and build tools are present, as reported by the existing build-tool verification.
- **SC-006**: Both depth settings behave correctly at both special values `0` and `-1` with no regressions: `max_source_dep_depth = -1` equals the pre-feature full-source behavior and `0` fetches nothing (with the warning when sources are enabled); `max_build_dep_report_depth = -1` equals the prior "report all" behavior and `0` reports no build-tool levels. Coverage is proven by a dedicated test matrix.

## Assumptions

- **Showcase, not enforcement**: The profiles are plain TOML data files that the administrator copies into place as the project setting. There is no profile concept in code, no validation that a configuration "matches" a profile, and no behavior keyed to a profile name — only the underlying settings (`[fetch] source`, `max_source_dep_depth`, `max_build_dep_report_depth`) have meaning.
- **Location and shape**: The profile files live under `docs/` and are complete, standalone project settings — each is usable as a drop-in `project.defaults.toml`. The existing `project.local.toml` overlay mechanism still applies on top.
- **Depth-value convention**: both depth settings use `-1` = unlimited, `0` = none, positive = levels. This replaces the old confusing "`0` = unlimited" convention (the project is not yet in use, so the change is safe).
- **Defaults**: `project.defaults.toml` ships the regular_user presets (`[fetch] source = false`, `max_source_dep_depth = 0`, `max_build_dep_report_depth = 2`). The `ProjectConfig` dataclass defaults **match the shipped presets exactly** — `fetch_source = False`, `max_source_dep_depth = 0` — so an unconfigured key behaves identically to the shipped regular_user default and there is a single default story. `-1` (unlimited) is an explicit opt-in (it equals the pre-feature full-source behavior); `max_build_dep_report_depth` stays `2`.
- **Warning condition**: the depth-0 warning fires when `[fetch] source = true` and `max_source_dep_depth = 0` — i.e., source functionality is enabled but the depth disables it. When `[fetch] source = false` the depth is moot and no warning is needed (regular_user).
- **Version drift / why runtime binaries are never capped**: Debian packages declare precise versioned runtime constraints (e.g. `libc6 (>= 2.36)`). If the runtime dependency tree were truncated, the cache could hold a set of runtime packages whose versions do not satisfy what the requested packages require — and because the archive evolves, that mismatch would only worsen over time (the cached runtime set drifts relative to the constraints of packages added later). A truncated runtime tree would therefore make the requested packages uninstallable offline and break Dependency Alignment (Constitution II). Only the source/build-tool support is safely tradeable for cache size.
- **Source-dep depth semantics**: `max_source_dep_depth` counts levels of the dependency graph from the requested packages. Level 0 = the requested packages; level 1 = their direct runtime dependencies; and so on. `0` = none (the default, matching the shipped presets); `-1` = unlimited (explicit opt-in, equals pre-feature behavior); positive `N` = sources and build tools for binaries within `N` levels.
- **Coherence of the source set**: every source present in a finite-depth cache is buildable — its build tools are resolved with complete runtime closures (FR-013). No source is fetched for a package whose build tools would be unavailable.
- **Build-tool binaries get no sources at finite depth**: build tools are resolved as installable binaries (with their runtime closures) but their own sources are not fetched at a finite depth — they are tools, not rebuild targets. This is a documented, scoped Constitution III exception for finite depths; graybeard (`-1`) fetches everything, preserving full rebuild capability.
- **Report-depth semantics**: `max_build_dep_report_depth` — `-1` = report all levels (equals the prior "`0` = report all" behavior); `0` = report no build-tool levels (only the always-on depth-0 runtime check); positive `N` = report unresolved build deps up to `N` levels. Default `2`.
- **Uniform application**: The depth setting applies to both `add` and `ensure-base`; the graybeard profile (the `ensure-base` main use case) leaves it unlimited (`-1`), so the full base is the norm there.
- **regular_user meaning**: "only the requested binaries" means binary packages only (no source packages, no build tools) with their full runtime dependency closure — exactly the current `[fetch] source = false` behavior, not "no dependencies at all".
- **Retention, no pruning**: The depth cap bounds new resolution only. Already-fetched sources/build tools from a wider or unlimited run are never deleted or unregistered when a shallower depth is later configured. The knob is documented as intended to be turned in the grow-deeper direction only — regular_user → developer (depth 1) → developer (depth N) → graybeard — but no monotonicity constraint is enforced.
- **Illustrative scale**: The example (a couple of packages under graybeard → >13 700 binaries, >25 000 sources, >30 GB) motivates the depth setting but depends on the package set and archive; success is measured relatively (subset vs. superset), not by absolute numbers.
- **Tutorials deferred**: Step-by-step tutorials for the profiles are a later feature; this spec ships the profile files plus brief inline documentation of each profile's intended use.
- **Operator unaffected**: The profiles change what the administrator puts in the cache; the operator's tooling and handoff/diff flows (specs 012/013) are unchanged.
- **Verify gate (no schema change)**: the in-scope/out-of-scope boundary is not persisted. `verify --graph` reads the configured `max_source_dep_depth`: `-1` keeps the strict unlinked-binaries check (missing sources are defects, regression-preserving); a finite depth makes unlinked binaries informational-only (FR-015). In-scope missing-source detection is dropped at finite depths as an accepted trade-off for the schema freeze.
- **add summary unchanged**: under a finite depth, the `add`/`ensure-base` summary shows only the in-scope `sources`/`build-tool deps` counts — no "skipped" line. The depth's effect is observable via `package_mgmt config` (shows `max_source_dep_depth`) and `verify --graph` (informational unlinked count, FR-015).

## Clarifications

### Session

- Q: Runtime-depth cap vs build-dep depth vs source-dep depth → A: the runtime binary tree is NEVER capped (installability + version alignment, Constitution II). The setting is `max_source_dep_depth`: sources and build tools are provided only for binaries within `N` levels of the requested set; beyond `N`, binaries are still resolved but no source/build-tool support is fetched (FR-003/FR-004).
- Q: Why sources and build tools are capped together → A: it is incoherent to cap build tools while still fetching every source — sources beyond the cap could not be built. Capping the source depth means every fetched source has its build tools (FR-013) and no useless source is fetched.
- Q: Build tools under a finite depth → A: resolved as installable binaries with complete runtime closures, but their own sources are not fetched (they are tools, not rebuild targets) — a scoped Constitution III exception for finite depths; graybeard fetches everything.
- Q: Depth value convention → A: `-1` = unlimited, `0` = none, positive = levels — for both `max_source_dep_depth` and `max_build_dep_report_depth`. This replaces the confusing "`0` = unlimited" convention (project not yet in use). `max_source_dep_depth = 0` with `[fetch] source = true` warns that no sources will be fetched (FR-016); `max_build_dep_report_depth = 0` reports no build-tool levels (FR-017).
- Q: Defaults → A: `project.defaults.toml` ships the regular_user presets (`source = false`, `max_source_dep_depth = 0`) so novices do not pull all sources by mistake. The `ProjectConfig` dataclass defaults match those presets exactly (`fetch_source = False`, `max_source_dep_depth = 0`) — one default story, unconfigured == regular_user. `-1` (unlimited) is an explicit opt-in; the `max_build_dep_report_depth` default stays `2`.
- Q: Changing the cap between runs — retain or prune? → A: retain, never prune. The cap bounds new resolution only; previously-fetched sources/build tools are kept (and downloaded). The knob is documented as intended to be turned in the grow-deeper direction only (regular_user → developer depth 1 → developer depth N → graybeard); no monotonicity constraint is enforced (FR-014).
- Q: How does `verify --graph` know the in-scope boundary (schema is frozen; levels are in-memory per batch)? → A: no schema change — FR-015 is weakened. `verify --graph` reads the configured `max_source_dep_depth`: at `-1` (unlimited) the existing "unlinked binaries" check applies unchanged (missing sources are defects). At a finite depth (`0`/`N`), unlinked binaries are never defects — out-of-scope binaries have no source by design — so they are reported only as an informational count ("beyond source depth or missing"), and COMPLETE is decided by the runtime closure plus the buildability of the fetched sources (FR-013). In-scope missing-source detection is deliberately dropped at finite depths (accepted trade-off for the schema freeze).
- Q: Should the `add`/`ensure-base` summary report how many binaries were skipped as out-of-scope under a finite depth? → A: no — the summary stays unchanged (only the in-scope `sources` and `build-tool deps` counts appear). The depth's effect is observable via `package_mgmt config` (shows `max_source_dep_depth`) and `verify --graph` (informational unlinked count, FR-015). No summary-contract change.
