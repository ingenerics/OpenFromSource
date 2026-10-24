# CLI Contracts: Base Profiles

## `package_mgmt config`

Text output gains `max_source_dep_depth` (beside `max_build_dep_report_depth`); JSON gains it under `resolution` with the same `source: default|override` convention. The example below shows the shipped **regular_user presets** (`source = false`, `max_source_dep_depth = 0`), which are also the `ProjectConfig` dataclass defaults — an unconfigured key renders identically (one default story):

```text
$ package_mgmt config
Configuration:
  base_dir:                  /var/cache/package_mgmt (default)
  binary_cache:              /var/cache/package_mgmt/binary (default)
  source_cache:              /var/cache/package_mgmt/source (default)
  iso_dir:                   /var/cache/package_mgmt/iso (default)
  db_path:                   /var/cache/package_mgmt/package_mgmt.db (default)
  log_level:                 INFO (default)
  log_dir:                   /var/cache/package_mgmt/logs (default)
  fetch_source:              false (default)
  max_build_dep_report_depth: 2 (default)
  max_source_dep_depth:      0 (default)             # NEW: 0 = no sources/build tools (regular_user presets); -1 = unlimited
  default source: trixie/main
  ...
```

When `project.local.toml` overrides it, the line reads `(override: project.local.toml)` like every other overridable field.

JSON:

```json
{
  "paths": { "base_dir": "...", "binary_cache": "...", "source_cache": "...", "iso_dir": "..." },
  "database": { "path": "...", "source": "default" },
  "logging": { "level": "INFO", "directory": "...", "source": "default" },
  "fetch": { "source": false, "source_override": false },
  "resolution": {
    "max_build_dep_report_depth": 2,
    "max_source_dep_depth": 0,
    "max_source_dep_depth_override": false,
    "source": "default"
  },
  "archive_sources": [...]
}
```

### Config error (invalid depth)

A value below `-1`, a non-integer, or a boolean `max_source_dep_depth` in either project TOML fails at load time, before any command runs, via the shared `_print_error` convention (exit 102); the same validation applies to `max_build_dep_report_depth`:

```text
$ package_mgmt add vifm        # [resolution] max_source_dep_depth = -2
Config error: [resolution] max_source_dep_depth must be an integer >= -1, got: -2
```

stderr JSON (with `--format json`): `{"error": "[resolution] max_source_dep_depth must be an integer >= -1, got: -2", "exit_code": 102}`

The error applies to every command that loads config (including `add`, `ensure-base`, `download`, `verify`, `config`) — validation happens in the shared loader, so no command can run with an invalid depth.

## `package_mgmt add` / `ensure-base` (resolution under a finite depth)

The CLI surface is unchanged (no new flags, no new commands). The observable effect of a finite `max_source_dep_depth` is a smaller source/build-tool set — the runtime binary closure is identical to the unlimited case (FR-003):

```text
$ package_mgmt add vifm            # developer profile: max_source_dep_depth = 1, source = true
Resolved vifm (trixie/main) — 89 binaries, 12 sources, 9 build-tool deps, 0 virtual
```

The `binaries` count covers the full runtime closure; the `sources` count covers only the in-depth set; the `build-tool deps` count covers the build tools of those in-depth sources. With `max_source_dep_depth = -1` (unlimited), output and counts are identical to current behavior; with `0` (none) and `[fetch] source = true`, a warning is printed that no sources will be fetched but the command still exits 0.

Error/summary conventions (success, unresolved deps, JSON errors, exit codes) are unchanged from the existing `add`/`ensure-base` contracts — the depth only changes *which packages get source/build-tool support*, never the per-command output shape or exit semantics. A source beyond the depth limit is not an error: the binary is resolved, its source is simply not fetched, and the command exits 0.

## `package_mgmt verify` (depth gate, FR-015; bare `verify` — the former decorative `--graph` flag is removed)

A correctly-scoped finite-depth cache reports COMPLETE. No schema change — the gate reads the configured `max_source_dep_depth`:

- At a finite `max_source_dep_depth` (`0`/`N`), binaries without a `binary_to_source` link are NOT defects (out-of-scope by design) — they appear only as an informational count ("N binaries beyond source depth or missing source"), and COMPLETE is determined by the runtime closure being complete and every fetched source having its build tools present (FR-013).
- At `max_source_dep_depth = -1` (unlimited), the existing "unlinked binaries" check applies unchanged — missing sources are defects (regression-preserving).
- Every fetched source has its build tools present (FR-013), so the build-tool section is complete for the in-scope set; boundary build dependencies are not reported as defects.
- The runtime-dependency completeness check is unchanged (the runtime closure is always complete).

## Profile files (not a CLI command)

The three presets under `docs/profiles/` are **data files** — the tool has no `profile` command, no `--profile` flag, and no profile-keyed behavior. They are drop-in `project.defaults.toml` replacements: an administrator copies one over `project.defaults.toml` (or merges its `[fetch]`/`[resolution]` into the local overlay) and runs the standard commands. Nothing validates a config "matches" a profile.

| File | Effect |
|------|--------|
| `docs/profiles/regular_user.toml` | `add` resolves binaries only (full runtime closure); no source records, no `source_build_deps`; `download` skips the source phase (exit 0) |
| `docs/profiles/developer.toml` | `add` resolves the full runtime closure + sources and build tools for the requested set and its dependencies up to the configured depth; `download` fetches source files for the in-depth sources |
| `docs/profiles/graybeard.toml` | `add`/`ensure-base` resolve fully recursively (sources + build tools for every binary); the intended `ensure-base` profile |

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success (including a depth-limited resolution that intentionally skips out-of-scope sources) |
| 102 (`EXIT_CONFIG_ERROR`) | Invalid `max_source_dep_depth`/`max_build_dep_report_depth` (below `-1`, non-integer, boolean), a mistyped or unknown config key (declarative type map), a non-table `archive`, or any existing config error |
| 101 (`EXIT_SCHEMA_MISMATCH`) | Stale/missing database — now also for `show`/`list`/`verify` (spec 018 F-22), matching their gated siblings |
| 1 (`EXIT_ERROR`) | Other failures (unchanged) |

## Unchanged surface

No new commands, no new flags, no operator artifacts. For a valid initialized cache, `list`, `show`, and `verify`
output and exit codes are unchanged (spec 018 F-22 only adds the shared existence+schema gate, which passes
through valid caches). `handoff` (012) and `sync`/`apply.sh` (013) were updated by spec 018 (out-of-config
exclusion and the clean apply count respectively); `download` and `init` are unchanged. The runtime binary
closure is always complete regardless of the depth setting. A cache built under any profile hands off and is
consumed exactly like today.
