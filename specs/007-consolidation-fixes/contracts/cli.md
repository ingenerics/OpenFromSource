# CLI Contracts: Consolidation Fixes

## `package_mgmt add`

Output now reports virtual resolutions, matching the spec 006 contract:

```text
# With virtual packages resolved
$ package_mgmt add vifm
Package vifm resolved from trixie/main (8 dependencies, 2 virtual)
```

With fallback information preserved:

```text
$ package_mgmt add hyprland --source trixie-backports
Package hyprland resolved from trixie-backports/main (42 dependencies, 3 from fallback, 2 virtual)
```

Error cases:
- Unresolvable build tool → reported as an error in the summary; no `source_build_deps` link is created
- Virtual package cannot be resolved → "Virtual package 'foo' has no providers in the index" (no candidates) or "Virtual package 'foo' has no satisfiable providers" (constraint filtered all candidates); resolution of other packages continues

## `package_mgmt verify --graph`

Verification reflects the current tree only. Circular dependencies and missing build tools are computed, never hardcoded:

```text
$ package_mgmt verify --graph
Dependency Graph Verification
─────────────────────────────
Total packages:         10
Total binaries:         42        # status='current' rows only
Total sources:          12
Unresolved deps:        0
Unlinked binaries:      0
Circular dependencies:  1         # informational (amended T039 — no status/exit impact)
  - libgcc-s1 -> libc6 (circular)
Runtime Status:         COMPLETE

Build-Tool Dependencies
───────────────────────
Total build-dep links:  87
Total build tools:      23
Unresolved build deps:  0
Missing build tools:    1
  - debhelper-extra — required by vifm, no record in cache
Build Status:           INCOMPLETE

Status:                 INCOMPLETE
```

Exit codes: 0 = COMPLETE; non-zero when unresolved deps, unlinked binaries, unresolved or missing build tools exist. Circular dependencies are listed but informational (benign cycles are normal in Debian trees; amended per T039).

## `package_mgmt show`

`--deps` appends the dependency tree in text mode:

```text
$ package_mgmt show vifm --deps
Table: packages
...

Table: binaries
...

Dependency Tree
───────────────
vifm (pending)
└─ perl (= 5.38.2) [binary ✓, source ✓, verified: ✗]
   └─ perl-modules-5.38 (= 5.38.2-3) [binary ✓, source ✓, verified: ✗]
```

With `--format json`, both keys are present:

```json
{
  "tables": { "packages": [...], "binaries": [...], "sources": [...], "binary_to_source": [...] },
  "dependency_tree": { "name": "vifm", ... }
}
```

`--tables virtual_resolutions` now shows the resolved provider, not raw IDs:

```text
$ package_mgmt show awk --tables virtual_resolutions
Table: virtual_resolutions
 virtual_name | resolved_package | version   | status   | release
--------------|------------------|-----------|----------|--------
 awk          | gawk             | 5.2.1-2   | current  | trixie
```

## `package_mgmt config`

Text output gains `log_dir` (parity with the spec 005 contract); JSON gains `fetch_source`:

```text
$ package_mgmt config
Configuration:
  base_dir:                  /var/cache/package_mgmt (default)
  binary_cache:              /var/cache/package_mgmt/binary (default)
  source_cache:              /var/cache/package_mgmt/source (default)
  db_path:                   /var/cache/package_mgmt/package_mgmt.db (default)
  log_level:                 INFO (default)
  log_dir:                   /var/cache/package_mgmt/logs (default)
  fetch_source:              true (default)
  max_build_dep_report_depth: 2 (default)
  default source: trixie/main
...
```

```json
{
  "paths": { "base_dir": "...", "binary_cache": "...", "source_cache": "..." },
  "database": { "path": "...", "source": "default" },
  "logging": { "level": "INFO", "directory": "...", "source": "default" },
  "resolution": { "max_build_dep_report_depth": 2, "source": "default" },
  "fetch": { "source": true },
  "archive_sources": [...]
}
```

Error cases (unchanged):
- Duplicate `(release, component)` pair → error naming the pair, e.g. `Duplicate (release, component) pair: (trixie, main)`
- No sources configured → error requiring at least one `[[archive.source]]`
