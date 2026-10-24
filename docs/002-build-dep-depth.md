# Build-Dependency Depth Reporting

## The Problem

When `add` resolves a package's dependency tree, it also resolves build dependencies
(`Build-Depends` from Sources.gz). Build tools like `debhelper`, `cmake`, and `gcc`
bring their own transitive dependencies, which can be hundreds or thousands of packages
deep. Publishing all of them as "unresolved" in `verify` creates noise that
obscures the real issues.

## The Solution: `max_build_dep_report_depth`

Build dependencies are resolved fully (the pool is complete), but the `verify`
command only reports unresolved transitive deps for packages within a
configured depth. Packages deeper than the limit are still fetched and stored — they just don't
appear in the error output.

### Configuration

```toml
[resolution]
max_build_dep_report_depth = 2
```

Default is 2. Depth convention (both depth settings): `-1` = unlimited
(report all levels), `0` = none (no build-tool levels reported), positive
`N` = up to `N` levels.

### Semantics

- **Depth 0**: Runtime packages (the packages the user asked for and their runtime
  deps). These are always checked regardless of the setting.
- **Depth -1**: All build-tool levels are reported (no filtering).
- **Depth N (positive)**: Build dependencies at depth ≤ N are reported —
  depth 1 is direct build deps of depth-0 packages (like `debhelper`,
  `libmagic-dev`), depth 2 is build deps of depth-1 packages (e.g. build deps
  of `debhelper`), and so on. Build tools deeper than `N` are still resolved
  and fetched, but excluded from the "unresolved" count.

## Implementation

### Where depth is stored: `source_build_deps.build_dep_depth`

Depth is stored on the **link** between a source and its build dependency, not on
the binary itself. A binary can be at depth 1 for one source (shallow build dep)
and depth 6 for another (deep transitive build dep). The `MIN()` across all links
determines the effective depth for reporting.

Schema (`database.py`):

```sql
CREATE TABLE IF NOT EXISTS source_build_deps (
    source_id INTEGER NOT NULL REFERENCES sources(id),
    binary_id INTEGER NOT NULL REFERENCES binaries(id),
    build_dep_depth INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source_id, binary_id)
);
```

### How depth flows through the resolver

1. **Runtime resolution** (`_resolve_one` → `upsert_binary`): resolves the
   initial package and all its transitive runtime dependencies. No
   `source_build_deps` links are created — depth tracking starts at the
   build-dep level.

2. **Build-dep resolution** (`_resolve_build_deps`): iterates over sources
   with `build_depends` in waves until no new sources remain (spec
   007-consolidation-fixes, US1). For each build dependency:
   - Calls `_resolve_one` to resolve the build tool and its runtime deps
   - Calls `link_source_build_dep(source_id, binary_id, build_dep_depth=N)`
     where `N` is the wave number (1 for direct build deps, 2 for build deps
     of build tools, and so on)

   Resolution is NOT truncated by `max_build_dep_report_depth` — that setting
   only filters `verify` output. A safety cap of 100 waves bounds the
   loop (logged warning at the cap).

3. **Alternatives**: build-dep entries with pipe alternatives
   (`liblua5.1-dev | liblua5.4-dev`) keep their alternative list; if the
   primary fails to resolve, the alternatives are tried in order (mirroring
   runtime dependency handling).

4. **Satisfied-tool fallback**: if `_resolve_one` returns no record (e.g. the
   tool was already resolved in an earlier wave, or is a virtual package whose
   provider is already in the tree), the resolver links the existing record
   instead of reporting an error: a `status='current'` binary by that name, a
   `virtual_resolutions` entry for it, or a resolved alternative. Links are
   upserted with `ON CONFLICT ... DO UPDATE SET
   build_dep_depth = MIN(build_dep_depth, excluded.build_dep_depth)` — the
   shallowest depth wins.

### How verify uses depth

The `_find_unresolved_deps` function in `verify.py` queries:

```sql
SELECT b.package_name, b.version, b.dependencies
FROM binaries b
LEFT JOIN (
    SELECT binary_id, MIN(build_dep_depth) AS min_depth
    FROM source_build_deps GROUP BY binary_id
) sbd ON b.id = sbd.binary_id
WHERE b.status = 'current' AND COALESCE(sbd.min_depth, 0) <= ?
```

- Only `status='current'` binaries are checked (superseded rows are history).
- Binaries with no `source_build_deps` links get `COALESCE(..., 0)` — they are
  runtime packages, always reported.
- Binaries with links get the minimum depth across all links. If the minimum
  is within the configured limit, their unresolved deps are reported.

Dependency satisfaction itself is provider-aware: a dependency (or one of its
alternatives) is satisfied by a current binary of that name or by any current
binary providing it, with versioned provides matched against the dependency
constraint — so virtual build tools never produce false "no record in cache"
reports.

### Files

| File | Role |
|------|------|
| `database.py` | `build_dep_depth` column on `source_build_deps` |
| `config.py` / `config_loader.py` | `max_build_dep_report_depth` setting |
| `project.defaults.toml` | Default value with comment (-1 = report all levels, 0 = none, N = up to N) |
| `resolver.py` | `link_source_build_dep(build_dep_depth=N)`; `_resolve_build_deps` passes the wave number as depth (1 = direct build deps, 2 = build deps of build tools, …); alternatives + satisfied-tool fallback |
| `verify.py` | `_find_unresolved_deps` filters by `MIN(sbd.build_dep_depth)` on current rows; provider-aware satisfaction |
| `main.py` | Passes config value to `handle_verify_graph` |
| `config.py` (CLI) | Shows setting in `package_mgmt config` output |