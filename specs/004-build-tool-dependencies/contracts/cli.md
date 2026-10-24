# CLI Contracts: Build-Tool Dependencies

## `package_mgmt add` (extended)

Build-tool dependencies are resolved according to the TOML config — no per-command flags needed.

```text
package_mgmt add <package>[=version]...
```

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `<package>[=version]...` | str (one or more) | required | Package name(s) to add and resolve. Optional `=version` pins a specific version |

**Behaviour**:
- Source and build-tool fetching is controlled by the single `[fetch] source` TOML config setting (default `true`)
- When `source = true`: source packages and build-tool dependencies are resolved and cached
- When `source = false`: only binary packages are resolved and cached; no source or build-tool records are created
- The setting can be overridden in `project.local.toml` without modifying `project.defaults.toml`
- Build-tool resolution follows the same Database-First approach: dependency graph is computed before any fetch
- Build-tool packages are stored in the same binary cache directory as runtime packages
- Each source-to-build-tool link is recorded in the `source_build_deps` table

**Exit codes**:
| Code | Meaning |
|------|---------|
| 0 | Success — all packages and build-tool deps resolved |
| 1 | Resolution error — one or more packages or build tools could not be resolved |

**Stdout on success** (extended):
```text
Added: vifm
  Binary: vifm (= 2.1-1+b1) [amd64] → source: vifm (2.1-1)
  Binary: perl (= 5.36.0-7) [amd64] → source: perl (5.36.0-7)
  ...
Packages resolved:    8
Binaries recorded:    12
Sources linked:       6
Build tools cached:   18
Build-dep links:      27
```

**Stderr on error** (unchanged):
```json
{"error": "Package not found in index: nonexistent-pkg", "exit_code": 1}
```

---

---

## `[fetch]` Config Section

Controls whether source packages and their build-tool dependencies are resolved during `add`. Single setting under the `[fetch]` key in `project.defaults.toml`, overridable via `project.local.toml`.

```toml
[fetch]
source = true
```

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `source` | bool | `true` | When `true`, fetch source packages (.dsc, .orig.tar.*, .debian.tar.*) AND resolve build-tool dependencies. When `false`, fetch binary packages only |

**Behaviour**:
- When `source = true` (default): source packages are fetched, build-tool dependencies are resolved from `Build-Depends` fields, and the `source_build_deps` table is populated
- When `source = false`: only binary packages are fetched. Source metadata is still parsed into the `RepositoryIndex` in memory but NOT persisted to the database. No `sources` records, no `source_build_deps` links, no source payloads on disk
- The admin sets `source = false` in `project.local.toml` to minimize cache size when offline source rebuilds are not needed

---

## `package_mgmt verify --graph` (extended)

The verify output gains a build-tool cache section.

```text
package_mgmt verify --graph [--package <package>]
```

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--graph` | flag | — | Report graph-level statistics |
| `--package` | str | all packages | Restrict to a single package's tree |

**Exit codes**:
| Code | Meaning |
|------|---------|
| 0 | Graph is complete — no unresolved runtime or build dependencies |
| 1 | Graph has issues — missing records or unresolved deps of any type |

**Stdout**:
```text
Dependency Graph Verification
─────────────────────────────
Total packages:         12
Total binaries:         18
Total sources:          10
Unresolved deps:        2
  - libc6 (>= 2.34) — no binary record for matching version
  - perl-base (= 5.36.0-7) — no binary record for matching version
Unlinked binaries:      0
Circular dependencies:  0
Runtime Status:         INCOMPLETE

Build-Tool Dependencies
───────────────────────
Total build-dep links:  27
Total build tools:      18
Unresolved build deps:  0
Missing build tools:    0
Build Status:           COMPLETE

Status:                 INCOMPLETE
```

**Error output example** (when build deps are missing):
```text
Dependency Graph Verification
─────────────────────────────
Total packages:         12
Total binaries:         18
Total sources:          10
Unresolved deps:        0
Unlinked binaries:      0
Circular dependencies:  0
Runtime Status:         COMPLETE

Build-Tool Dependencies
───────────────────────
Total build-dep links:  27
Total build tools:      18
Unresolved build deps:  1
  - gcc (>= 4:12) — no binary record for matching version (required by: linux)
Missing build tools:    0
Build Status:           INCOMPLETE

Status:                 INCOMPLETE
```

---

## `package_mgmt list --deps` (extended)

The dependency tree optionally includes build-dependency information.

```text
package_mgmt list --deps <package> [--include-build-deps]
```

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--deps` | str | required | Package name to inspect |
| `--include-build-deps` | flag | — | Show build-tool dependencies in addition to runtime deps |
| `--format` | `text` or `json` | `text` | Output format |

**Stdout (with `--include-build-deps`, text format)**:
```text
vim (pending)
  ├─ libc6 (>= 2.34) [binary ✓, source ✓, verified: ✗]
  └─ [build] debhelper (>= 13) [binary ✓, verified: ✗]
```

**Stdout (json format with `--include-build-deps`)**:
```json
{
  "package": "vim",
  "status": "pending",
  "dependencies": [
    {"name": "libc6", "version_constraint": ">= 2.34", ...}
  ],
  "build_dependencies": [
    {"name": "debhelper", "version_constraint": ">= 13", "binary_exists": true}
  ]
}
```
