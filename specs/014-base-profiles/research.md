# Research: Base Profiles

**Feature**: [spec.md](spec.md) | **Phase 0** — decisions and constraints behind the plan.

## Overview

The feature ships three usage profiles as plain project-setting TOML files and adds one new engine setting, `max_source_dep_depth`, that caps how deep the dependency graph receives **source and build-tool support**. Earlier designs capped the *runtime* depth and then the *build-tool* depth; both were flawed. Research documents the final, coherent model: runtime binaries are always complete (installability + version alignment), while sources and build tools are capped together so every fetched source is buildable.

## Decision 1 — The runtime binary tree is never capped (version-drift rationale)

**Decision**: The runtime dependency closure of the requested packages is **always** resolved completely as binaries. The depth setting never applies to runtime binaries.

**Rationale**:
- **Installability**: the requested packages must remain installable offline. If a transitive runtime dependency were absent from the cache, `apt install` would fail against the cache.
- **Version drift / Dependency Alignment (Constitution II)**: Debian packages declare precise versioned runtime constraints (e.g. `libc6 (>= 2.36)`). If the runtime tree were truncated, the cache would hold a runtime set that may not satisfy those constraints. Because the archive evolves, the cached runtime set drifts further from the constraints of packages added later — the mismatch only worsens over time. The system's own resolver enforces these constraints (spec 008 constraint machinery), so a truncated tree would surface as resolution errors.
- **Constitutional consistency**: Principle II (Dependency Alignment) and the offline guarantee (I) both require a complete, aligned runtime set.

This is documented in the spec (FR-003 + Assumptions "Version drift / why runtime binaries are never capped").

## Decision 2 — Cap the source dependency depth, not the build-tool depth

**Decision**: The setting is `max_source_dep_depth`. It caps how many levels of the dependency graph receive **source and build-tool support**:
- Level 0 = the requested packages; level 1 = their direct runtime dependencies; and so on (runtime-graph distance from the requested set).
- `0` = **none** (the default — the dataclass default matches the shipped regular_user presets, so unconfigured == regular_user). `-1` = **unlimited** (explicit opt-in; backward compatible with the pre-feature full-source behavior). Positive `N` = sources (and their build tools) are provided for binaries within `N` levels; binaries beyond `N` are still resolved (installability) but no source/build-tool support is fetched.
- Build tools are resolved for every fetched source, with complete runtime closures, so **every source present is buildable** and no source is fetched whose build tools would be unavailable.
- Build-tool binaries themselves get no sources at a finite depth (they are tools, not rebuild targets) — a documented, scoped Constitution III exception; graybeard (`-1`, unlimited) fetches everything.

**Rationale**:
- The user identified the flaw in the intermediate design: "It doesn't make sense to cap the build tools and still fetch all sources, given we won't be able to build the latter." Under the build-dep-depth design, sources were fetched for the *entire* runtime closure (uncapped) while build tools were capped — yielding a cache full of sources that could not be built. Capping the **source depth** makes the source set coherent: sources and their build tools are provided together, exactly for the packages the developer cares about (the requested set and a shallow neighborhood).
- The graybeard explosion (>13 700 binaries, >25 000 sources, >30 GB) is driven by fetching sources and build tools for the entire recursive closure; bounding the *source depth* directly shrinks the source/build-tool portion while leaving the installable binary set intact.
- The original "runtime depth" idea was partly right (the acceptance scenario "sources and build-tool dependencies present, no [support] beyond depth 1") — the fix is to keep the runtime *binaries* complete while applying the depth to *sources*.

**Alternatives considered**:
- Capping the runtime tree (original) — breaks installability and causes version drift (Decision 1); rejected.
- Capping only the build-tool recursion (intermediate) — fetches all runtime-closure sources that cannot be built; incoherent; rejected.
- Capping both runtime binaries and sources — rejected (same installability/drift problem).

## Decision 3 — Enforcement

**Decision**: The cap is enforced where source records and build-tool links are created:

- The runtime pass (`_resolve_one` / `_resolve_one_inner`) is **unchanged in effect**: it always resolves the full runtime closure and registers every binary. It gains a per-package **level** (runtime-graph distance from the requested set; the requested packages are level 0, a runtime dependency is parent + 1).
- A source record (`upsert_source` + `binary_to_source` link) is created only when the binary's level ≤ `N` (or `N == -1`, unlimited). For a binary beyond `N`, the binary is registered but no source record is created.
- The build-tool pass (`_resolve_build_deps`) then naturally sees only the sources that were created (in-scope). It resolves each in-scope source's build tools with complete runtime closures (FR-013). Because out-of-scope binaries have no source records, no build tools are resolved for them.
- Build-tool binaries are resolved through the same `_resolve_one`, so their runtime closures are complete; their source records are not created (their level — placed one deeper than the source they serve — exceeds `N` at a finite depth), so their sources are not fetched.
- `ensure-base` uses the same `resolve_batch` path, so the cap applies uniformly (FR-004) with no extra code.
- **Verify gate (FR-015, no schema change)**: the scope boundary is not persisted (in-memory only). `verify --graph` reads the configured `max_source_dep_depth`: at `-1` (unlimited) the existing unlinked-binaries check applies unchanged (missing sources are defects). At a finite depth (`0`/`N`), unlinked binaries are never defects — out-of-scope binaries have no `binary_to_source` link by design — so they are reported only as an informational count ("beyond source depth or missing"), and COMPLETE is decided by the runtime closure plus the buildability of the fetched sources (FR-013). The build-dep check is naturally clean because every fetched source has its build tools. In-scope missing-source detection is deliberately dropped at finite depths (accepted trade-off for the schema freeze).

**Rationale**: Level accounting is a small addition to the existing recursive walk; gating source creation at the binary's level implements the depth with minimal machinery, and the build-tool pass needs no depth logic of its own (it processes only in-scope sources). The verify gate is required so a correctly-scoped developer cache reports COMPLETE.

**Alternatives considered**: A separate post-pass that prunes out-of-scope source records — would delete already-committed records and complicate the batch commits; rejected.

## Decision 4 — Config: field, default, validation, display

**Decision**:
- `ProjectConfig` gains `max_source_dep_depth: int = 0` (frozen dataclass) and changes `fetch_source`'s default to `False` — both matching the shipped regular_user presets, so an unconfigured key == regular_user (one default story).
- `project.defaults.toml` ships the **regular_user presets** — `[fetch] source = false` and `[resolution] max_source_dep_depth = 0` (with inline comments: `0` = none, `-1` = unlimited).
- `config_loader._KEY_MAP` gains `("resolution", "max_source_dep_depth") → "max_source_dep_depth"`, and a validation step raises `ConfigError` for a value below `-1`, a non-integer value (a `float` like `1.5`), or a boolean, with a clear message naming the key. `ConfigError` maps to `EXIT_CONFIG_ERROR` (102) via the existing `_print_error` convention. The same validation applies to `max_build_dep_report_depth` (FR-005/FR-017).
- `package_mgmt config` shows the field in both text (`max_source_dep_depth`) and JSON (under `resolution`, with the same `source: default|override` convention as `max_build_dep_report_depth`).
- When `[fetch] source = true` and `max_source_dep_depth = 0`, `add`/`ensure-base` emit a warning that no sources will be fetched (FR-016); with `source = false` no warning (regular_user — and, because `fetch_source` now defaults to `false`, the warning never fires on an unconfigured config).

**Rationale**: Validation at config-load time satisfies FR-005 and reuses the existing typed error path and exit code. Displaying it in `config` satisfies FR-010. Matching the dataclass defaults to the shipped presets (`fetch_source = False`, `max_source_dep_depth = 0`) gives a single default story: an unconfigured key behaves exactly like the shipped regular_user config, and `-1` (unlimited) is an explicit opt-in that reproduces the pre-feature full-source behavior.

**Alternatives considered**: Lazy validation at resolution time — lets an invalid config get further before failing; rejected.

## Decision 5 — Profile files are data-only presets

**Decision**: The three profiles ship as complete, standalone project-setting TOML files under `docs/profiles/` (`regular_user.toml`, `developer.toml`, `graybeard.toml`). Each is a drop-in `project.defaults.toml` (paths, database, logging, archive source, fetch, resolution) with inline comments describing the intended use. There is **no profile concept in code**: no classes, no name-keyed behavior, no detection or enforcement that a config "matches" a profile. Only the underlying settings have meaning.

**Rationale**: The user was explicit ("These profiles are not codified as classes in any way") and tutorials are deferred. Keeping profiles as pure data means zero runtime risk.

**Alternatives considered**: A `profile = "developer"` config key the tool interprets — adds a profile concept the user explicitly ruled out; rejected.

## Decision 6 — regular_user depth

**Decision**: `regular_user.toml` sets `max_source_dep_depth = 0` (none) alongside `[fetch] source = false`. When `[fetch] source = false`, the cap is effectively moot — no sources at all — and regular_user wants the full runtime closure so the requested packages remain installable. Setting the depth explicitly to `0` showcases the setting alongside `source = false`, and because `source = false` disables source fetching, the depth-0 warning (FR-016) does not fire.

**Rationale**: "only the requested binaries" (spec) means binary packages only with their full runtime closure — exactly current `source = false` behavior; the runtime closure is never capped (Decision 1), and there are no sources to bound.

## Decision 7 — Scope of change

**Decision**: The feature changes admin-side configuration and resolution only. No DB schema change, no operator artifact, no change to handoff (012) / diff (013) / download behavior other than the natural consequence of a smaller source/build-tool set (fewer source records → fewer downloads). Source/binary verification semantics are unchanged except the depth gate in `verify --graph` (FR-015).

**Rationale**: Constitution IX (incremental scope) and the spec's explicit "operator unaffected" assumption. The developer profile's cache, once handed off, is consumed exactly like any other cache.

## Consolidation

All decisions are resolved: version-drift / runtime-never-capped (Decision 1), source-dep-depth semantics (Decision 2), enforcement mechanics (Decision 3), config validation/display (Decision 4), profile-as-data design (Decision 5), regular_user depth (Decision 6), and scope boundary (Decision 7). No open items remain for planning.
