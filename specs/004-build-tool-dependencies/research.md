# Research: Build-Tool Dependencies

## Resolved Decisions

### Build-Depends Parsing

- **Decision**: Reuse `DependencyGraph.parse_depends_line()` for `Build-Depends`, `Build-Depends-Arch`, and `Build-Depends-Indep` fields from Sources.gz, since they use the same comma-separated dependency syntax as binary Depends.
- **Rationale**: Debian Policy Manual §4.2 specifies that Build-Depends use the same dependency field syntax as binary Depends (package names, version constraints, alternative deps via `|`, architecture qualifications). The existing parser handles all these cases. The only difference is the field name and the presence of build profiles (`<!profile>`), which are stored as raw annotations in the structured JSON but not actively filtered during resolution in this version.
- **Alternatives considered**: Writing a separate BuildDepends parser (unnecessary duplication — the grammar is identical). Using `deb822` field access only and deferring parsing to resolution time (wasteful — parsing at index-load time is cleaner and matches the existing pattern).

### Build-Depends Field Aggregation

- **Decision**: Store merged content of `Build-Depends`, `Build-Depends-Arch`, and `Build-Depends-Indep` as a single structured JSON array in `sources.build_depends`. A `build_context` field in each dependency spec entry distinguishes which field it came from (`'build'`, 'build-arch'`, or `'build-indep'`).
- **Rationale**: The three fields have different semantics (`Build-Depends-Arch` are needed only for architecture-specific builds, `Build-Depends-Indep` only for architecture-independent package builds), but at the cache-and-resolve level we need all of them available. The `build_context` discriminator preserves the origin for downstream build tooling without requiring three separate columns.
- **Alternatives considered**: Three separate columns on `sources` table (adds schema complexity for no query benefit). Single raw text column with all three concatenated (loses provenance information).

### SourceBuildDeps Link Table

- **Decision**: Create `source_build_deps(source_id, binary_id, verified)` — mirrors `binary_to_source` but links sources → build-tool binaries.
- **Rationale**: A source package may require multiple build tools, and a binary package may serve as a build tool for multiple sources. The many-to-many link table captures this naturally. All existing resolution, verification, and upsert patterns apply directly.
- **Alternatives considered**: Reusing `binary_to_source` with a `relationship` column (mixes two different relationships — "produces" vs "requires" — in one table, making queries ambiguous). Storing build-tool references as JSON in `sources.build_depends` (loses relational integrity, no FK enforcement, harder to query).

### Circular Build Dependency Handling

- **Decision**: Apply the same cycle-detection strategy used for runtime dependencies — the resolver's `_visited` set guards recursion, and detected cycles are annotated with a `circular:` prefix in the dependency name. The `verify --graph` command reports them.
- **Rationale**: Circular build dependencies are a known Debian reality (e.g., gcc ↔ g++). The existing cycle-avoidance mechanism (`_visited` set in `_resolve_one()`) prevents infinite recursion without special-casing build tools. Reporting them to the admin is sufficient for this phase — automated cycle-breaking (e.g., bootstrapping) is a future enhancement.
- **Alternatives considered**: Separate cycle detection for build tools (unnecessary — same recursion, same guard). Bootstrapping logic (out of scope — this spec covers caching, not building).

### Config-Driven Fetch Setting (single `[fetch] source`)

- **Decision**: Add a single `[fetch] source` boolean setting (default `true`) to the TOML config. When `true`, source packages AND their build-tool dependencies are resolved and cached. When `false`, only binary packages are fetched — source and build-tool resolution is skipped entirely. No per-command flags on `add` — the config is the source of truth.
- **Rationale**: Fetching source without build tools is pointless (you can't rebuild), and fetching build tools without source is nonsensical. A single boolean captures the only meaningful axis: does the admin need offline rebuild capability or not? TOML config with local overrides (`project.local.toml`) lets the admin set it once and forget it.
- **Alternatives considered**: Two separate settings (`source` + `build_deps`) — creates confusing matrix of states including nonsensical ones like `source=false, build_deps=true`. Per-command flags (repetitive, easy to forget). Always-on (wastes cache space for admins who don't need source rebuilds).

### Verification Extension

- **Decision**: Extend `_find_unresolved_deps` in `verify.py` to also check `source_build_deps` links. The existing "unresolved dep" check naturally generalizes: for each entry in `source_build_deps`, verify the referenced `binary_id` exists and its binary record is present. Build-tool statistics are reported as a separate section in the output.
- **Rationale**: The core check is the same — "does the referenced binary exist?" The only difference is the link table being queried. Adding a section to the output rather than mixing into the existing counts preserves clarity for the admin.
- **Alternatives considered**: Separate `verify --build-deps` subcommand (unnecessary fragmentation — `verify --graph` should report all graph issues in one run).

### Build-Depends Architecture Restrictions

- **Decision**: Architecture restrictions in Build-Depends (e.g., `libfoo [linux-any]`) are parsed and stored in the `architecture` field of the DependencySpec, matching how the existing parser handles arch-qualified binary dependencies. No active filtering — all build deps are resolved regardless of architecture restriction.
- **Rationale**: The target architecture is already fixed to `amd64` (from `target_arch` in config), and architecture restrictions in practice rarely exclude the primary arch. Storing the restriction without filtering keeps the data available for future profile-aware resolution without adding logic that would rarely trigger.
- **Alternatives considered**: Filtering by architecture restriction at resolution time (adds complexity for negligible benefit — virtually no build-dep in Debian excludes amd64).
