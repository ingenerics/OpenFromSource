# Quickstart: Base Profiles

**Feature**: [spec.md](spec.md) | **Validation guide** — prove the three profiles and the `max_source_dep_depth` setting work end to end. Contracts: [contracts/cli.md](contracts/cli.md); state model: [data-model.md](data-model.md).

## Prerequisites

- A networked admin machine with the project tooling installed and a hydrated archive cache (indexes cached, as built by `init`/`add`).
- A local test archive mirror for repeatable, offline-safe validation (the integration suite's `http://127.0.0.1:<port>` fixture pattern — loopback mirrors keep `http://`, remote mirrors are upgraded to `https://`).
- The three profile presets under `docs/profiles/` (shipped by this feature).

## Scenario 1 — Each profile file loads and shapes the cache (US2 / FR-006–FR-009)

For each profile, copy it over `project.defaults.toml` (scratch copy of the repo or cache dir), then `add` the same small package and `download`:

```bash
cp docs/profiles/regular_user.toml project.defaults.toml
package_mgmt add vifm
package_mgmt download
```

**Expected (regular_user)**: the summary shows binaries resolved, **0 sources**, **0 build-tool deps**; `package_mgmt show vifm --tables sources source_build_deps` returns **0 records** in both; `download` reports `Source phase: nothing to download.` and exits 0 (source phase skipped). The requested binary's full runtime closure is present (the package is installable).

Repeat for the other two:

```bash
cp docs/profiles/developer.toml project.defaults.toml
package_mgmt add vifm && package_mgmt download
cp docs/profiles/graybeard.toml project.defaults.toml
package_mgmt add vifm && package_mgmt download
```

**Expected (developer)**: the full runtime closure is resolved (identical `binaries` count to graybeard), and sources + build tools are present for the requested set and its dependencies up to the profile's source depth — strictly fewer sources than graybeard.

**Expected (graybeard)**: full recursion — every resolved binary has its source (`binary_to_source`) and its build tools (`source_build_deps`); `package_mgmt verify --graph` reports zero unresolved build deps and a COMPLETE build-tool section. This is the largest of the three sets.

## Scenario 2 — The depth setting bounds source/build-tool support, never the runtime closure (US1 / FR-001–FR-004, FR-011, FR-013)

Set `[resolution] max_source_dep_depth = 1` with `[fetch] source = true`, then `add` a small package:

```bash
# project.defaults.toml: [fetch] source = true ; [resolution] max_source_dep_depth = 1
package_mgmt add vifm
package_mgmt list
```

**Expected**: the requested binary and its **complete** runtime closure are registered as binaries (identical to an unlimited run); the requested binary and its **direct** runtime dependencies have sources and build tools; no source beyond depth 1 is fetched. Every fetched source is buildable (its build tools are present — check `show <pkg> --tables source_build_deps`). `add` exits 0 (skipping out-of-scope sources is not an error).

**Coherence check**: a transitive runtime dependency beyond depth 1 is present as a binary (installable) but has no source — `show <that-dep> --tables sources` returns 0 records, and `verify --graph` does NOT treat it as a defect (informational only — FR-015, no schema change).

**Backward compatibility**: pre-feature full-source behavior is reproduced by explicitly setting `[fetch] source = true` **and** `max_source_dep_depth = -1`. Omitting the depth key is **not** enough — it now defaults to `0` (regular_user). Re-add the same package in a fresh cache → sources and build tools are fetched for the entire closure, identical to pre-feature behavior.

## Scenario 3 — Config reflects the setting (FR-010) and validates it (FR-005)

```bash
package_mgmt config                 # text: shows max_source_dep_depth: 1 (default/override)
package_mgmt config --format json   # JSON: resolution.max_source_dep_depth + _override
```

**Expected**: the setting appears in both formats; an override from `project.local.toml` is annotated `(override: project.local.toml)` / `max_source_dep_depth_override: true`.

**Invalid depth** (below `-1` is invalid; `-1` itself is valid — unlimited):

```toml
# project.local.toml
[resolution]
max_source_dep_depth = -2
```

```bash
package_mgmt config
```

**Expected**: `Config error: [resolution] max_source_dep_depth must be an integer >= -1, got: -2`, exit 102 (JSON: `{"error": "...", "exit_code": 102}`). The same rejection applies to `1.5` and `true` — and to `max_build_dep_report_depth` — and to every command that loads config.

## Scenario 3b — Special values `0` and `-1` for both depth settings (SC-006, regression matrix)

Exercise both special values for both settings on the same package and cache; this is the regression proof that `0` no longer means "unlimited"/"report all" and that `-1` preserves pre-feature behavior.

**Setting A — `max_source_dep_depth`** (note: unconfigured `[fetch] source` now defaults to `false`; set `source = true` explicitly to exercise depth behavior):

| Setting | Expected |
|---------|----------|
| absent | dataclass default `0` applies — **no** sources/build tools, no warning (identical to the shipped regular_user default; the depth-0 warning cannot fire because `fetch_source` also defaults to `false`) |
| `-1` | sources + build tools for the **entire** runtime closure — identical to pre-feature behavior |
| `0` (with `source = true`) | **no** sources/build tools fetched, a warning is shown ("depth `0` fetches no sources — use `-1` for unlimited or a positive value"), command exits 0 |
| `1` (with `source = true`) | sources/build tools for the requested set and its direct runtime deps; full runtime closure |

Then set `[fetch] source = false` with `max_source_dep_depth = 0` (the regular_user case): binaries only, **no warning** — the functionality is disabled by `source = false`.

**Setting B — `max_build_dep_report_depth`** (`verify --graph` against the same cache):

| Setting | Expected |
|---------|----------|
| `-1` | reports **all** build-tool levels — identical to the prior "`0` = report all" behavior |
| `0` | reports **no** build-tool levels (only the always-on depth-0 runtime check) |
| `2` (default) | unresolved build deps reported up to 2 levels |

**Runtime-closure invariant**: the resolved `binaries` set is identical across `-1`, `0`, and `1` — the depth never truncates runtime binaries (FR-003).

## Scenario 4 — Developer cache is measurably smaller than graybeard (US3 / SC-004)

Add the same package set under `developer` and `graybeard` in two scratch caches, then compare:

```bash
package_mgmt verify --graph   # under each profile: identical runtime closure, dev sources ⊂ graybeard
du -sh <cache>/binary <cache>/source   # under each profile
```

**Expected**: the graybeard cache resolves strictly more sources and build tools and is strictly larger on disk; the binary (`binaries`) closure is identical in both (the purpose of the setting: bound only source/build-tool support). The spec's illustrative graybeard scale is >13 700 binaries / >25 000 sources / >30 GB for a couple of packages on a real archive. Both caches report a COMPLETE in-scope build-tool section.

## Scenario 5 — ensure-base respects the profile (FR-004, graybeard = main use case)

```bash
cp docs/profiles/graybeard.toml project.defaults.toml
package_mgmt ensure-base       # against a full DVD-1 image in iso_dir
package_mgmt verify --graph
```

**Expected**: the base set resolves fully recursively (every registered binary has its source and build tools) — the graybeard configuration. With a finite `max_source_dep_depth`, the base set's sources/build tools resolve to that depth instead (developer/regular_user are "not likely to use ensure-base" per the spec, so this is the documented fallback, not a primary flow).
