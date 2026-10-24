# Implementation Plan: Base Profiles

**Branch**: `014-base-profiles` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/014-base-profiles/spec.md`

## Summary

Three usage profiles (`regular_user`, `developer`, `graybeard`) are shipped as **plain project-setting TOML files** under `docs/profiles/` — data files only, no classes, no profile-aware code. The only new engine behavior is a `max_source_dep_depth` setting that caps how deep the dependency graph receives **source and build-tool support**, giving smaller caches for the developer profile while preserving the graybeard "turtles all the way down" full-rebuild cache and the regular_user binary-only cache. Both depth settings use the convention `-1` = unlimited, `0` = none, positive `N` = up to `N` levels — **not** the old confusing "`0` = unlimited". `project.defaults.toml` ships the regular_user presets (`[fetch] source = false`, `max_source_dep_depth = 0`) so novices do not pull all sources by mistake. The `ProjectConfig` dataclass defaults **match those presets exactly** (`fetch_source = False`, `max_source_dep_depth = 0`), so unconfigured == regular_user and `-1` (unlimited) is an explicit opt-in.

The runtime **binary** tree is **never** capped — it must always be complete so the requested packages stay installable and version-aligned (Constitution II). An earlier "runtime dependency depth" design (and an intermediate "build-tool depth" design) were withdrawn; the final coherent model caps *sources and build tools together* so every fetched source is buildable and no useless source is fetched. The version-drift rationale is documented in the spec (Assumptions) and research (Decision 1).

- **regular_user** → `[fetch] source = false` (existing behavior: full runtime closure, no sources, no build tools).
- **developer** → `[fetch] source = true` + a finite `max_source_dep_depth` (e.g. 1): full runtime closure, sources + build tools for the requested set and its in-depth dependencies.
- **graybeard** → `[fetch] source = true` + `max_source_dep_depth = -1` (unlimited): full recursion, the intended `ensure-base` configuration.

Implementation adds per-binary **level** accounting (runtime-graph distance from the requested set) to the runtime pass, creates source records only for in-level binaries, lets the existing build-tool pass process only those in-scope sources, and adds a depth gate to `verify --graph` (FR-015, no schema change: the gate reads the configured depth — a finite depth makes unlinked binaries informational-only, `-1` keeps the strict check). Config adds the setting to `ProjectConfig` + the `[resolution]` section, validates it (value below `-1`, non-integer, or boolean → `ConfigError`, exit 102), and surfaces it in `package_mgmt config` (text + JSON). When `[fetch] source = true` and `max_source_dep_depth = 0`, `add`/`ensure-base` emit a warning that no sources will be fetched (FR-016); with `source = false` the depth is moot and no warning is shown.

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default). No new runtime dependencies — stdlib `tomllib`/`sqlite3` only (already used by config loading and the resolver).

**Database**: SQLite 3.x — **no schema changes** (spec 010/011 freeze). `max_source_dep_depth` is configuration-only; the existing `sources`/`binary_to_source`/`source_build_deps` tables are unchanged. The resolver writes exactly the same record shapes as today, just fewer source records and build-tool links when a finite depth is configured.

**Primary Dependencies**: stdlib only. `tomllib` for config parsing (existing), `sqlite3` for the resolver (existing). No new packages.

**Storage**: No new cache layout. New files: `docs/profiles/{regular_user,developer,graybeard}.toml` (three complete, standalone project-setting presets — drop-in `project.defaults.toml` replacements); `project.defaults.toml` ships the regular_user presets — `[fetch] source = false` and `[resolution] max_source_dep_depth = 0`. No cache/pool/index changes.

**Testing**: pytest. **Regression test matrix below is mandatory** — both depth settings are exercised at both special values `0` and `-1` (plus positive values), so the redefined conventions cannot silently regress. Unit: config validation (dataclass defaults match the shipped presets — `fetch_source = false`, `max_source_dep_depth = 0`; value below `-1`, non-integer, boolean → `ConfigError`; both settings), resolver level accounting (source records only within depth; runtime closure identical across depths; boundary build tool resolved but no source of its own; strict subset; backward compatibility at `-1`), verify depth gate (finite depth → unlinked binaries informational-only, COMPLETE; `-1` → strict unlinked check unchanged, no regression), config display. Integration: `add` under each profile file against the local HTTP mirror fixture → verify the resolved binary/source/build-tool set matches the profile and the runtime closure is identical across depths; `ensure-base` respects the depth; the depth-0-with-source-enabled warning fires exactly once and the command still exits 0; `package_mgmt config` text/JSON show the setting. Contract: `package_mgmt config` output shapes and invalid-depth JSON error (`exit_code: 102`).

**Existing-test impact (dataclass defaults change)**: flipping the `ProjectConfig.fetch_source` dataclass default from `True` to `False` affects tests that previously relied on the `True` default. Concretely: `tests/contract/test_cli.py` `test_json_format_structure` asserts `fetch["source"] is True` on a project TOML that omits `[fetch] source` (must now write `[fetch] source = true` in the fixture, or assert `False`); `tests/unit/test_ensure_base.py:_scratch_config` constructs `ProjectConfig` without `fetch_source` (add `fetch_source=True` if its tests exercise source behavior). Any test TOML that omits `[fetch] source` yet expects source fetching must add the explicit override. Tests that already pass `fetch_source` explicitly (downloader, resolver_build_deps, handoff, apply, bootstrap, diff, constraints) are unaffected.

#### Mandatory regression test matrix

Each row is a required test; the matrix is the acceptance proof for SC-006 (no regressions at `0` and `-1` for both settings).

**Setting A — `max_source_dep_depth` (source/build-tool support depth)**

| `max_source_dep_depth` | `[fetch] source` | Expected | Regression guarantee |
|---|---|---|---|
| unset (absent key) | unset (absent `[fetch] source`) | dataclass defaults apply: `fetch_source=false`, `max_source_dep_depth=0` → binaries only, **no warning** | identical to the shipped regular_user presets (one default story) |
| `-1` (unlimited) | `true` | sources + build tools for the whole runtime closure | **equals pre-feature behavior** — no regression vs. configs without the key |
| `-1` (unlimited) | `false` | binaries only | same as today's `source = false` |
| `0` (none) | `true` | no sources/build tools fetched; **warning** shown; command exits 0 | new semantics — `0` must NOT mean "unlimited" (old bug) |
| `0` (none) | `false` | binaries only; **no warning** (functionality disabled by `source = false`) | regular_user presets; matches today's `source = false` |
| `1` (finite) | `true` | sources + build tools for requested set and its direct runtime deps; full runtime closure | depth bounds source support only, never the runtime closure (FR-003) |
| `N > 1` (finite) | `true` | sources/build tools within `N` levels; strict subset of the `-1` set | subset invariant (FR-011); every fetched source buildable (FR-013) |

Cross-depth invariants asserted on the same requested set: the **runtime binary closure is byte-identical across `-1`, `0`, `1`, `N`** (never truncated); the source/build-tool set at any finite depth is a strict subset of the `-1` set; re-running deeper→shallower retains previously-fetched sources (never pruned, FR-014); `verify --graph` reports the in-scope set COMPLETE and does not flag out-of-scope binaries (FR-015).

**Setting B — `max_build_dep_report_depth` (`verify --graph` report depth)**

| `max_build_dep_report_depth` | Expected | Regression guarantee |
|---|---|---|
| unset (absent key) | default `2` applies | unchanged default |
| `-1` (report all levels) | full build-tool report | **equals the prior "`0` = report all" behavior** — no regression |
| `0` (none) | no build-tool levels reported (only the always-on depth-0 runtime check) | new semantics — `0` must NOT mean "report all" (old bug) |
| `2` (default) | unresolved build deps reported up to 2 levels | unchanged |
| `N > 0` | unresolved build deps reported up to `N` levels | unchanged |

**Setting C — cross-setting combinations and validation**

| Case | Expected |
|---|---|
| `max_source_dep_depth = 0` + `max_build_dep_report_depth = -1` (regular_user + report-all) | `verify --graph` runs clean; no sources to report, so no missing-source defects; runtime check passes |
| `max_source_dep_depth = -1` + `max_build_dep_report_depth = 0` | full cache; report shows the runtime check only, no build-tool levels |
| `max_source_dep_depth = -2` / `1.5` / `true` | `ConfigError`, exit 102, names the key — for **both** settings |
| `max_build_dep_report_depth = -2` / `1.5` / `true` | `ConfigError`, exit 102, names the key |
| `max_source_dep_depth = 0` + `source = true` | warning printed, command still succeeds (exit 0), no sources fetched |

**Target Platform**: Debian Trixie LTS amd64 — admin-side only. The operator is unaffected (specs 012/013 unchanged); the operator never sees the profiles or the depth setting.

**Project Type**: CLI tool (admin, configuration + source/build-tool resolution extension).

**Performance Goals**: A finite depth strictly reduces source/build-tool resolution and download work (fewer source records, fewer build-tool links). The default `0` (matching the shipped regular_user presets) performs no source work by default — a deliberate, safe change from the pre-feature full-source behavior; an explicit `-1` restores the pre-feature behavior. The runtime closure is unchanged. Config load time is unaffected by the new setting.

**Constraints**: dataclass defaults match the shipped presets (`fetch_source = false`, `max_source_dep_depth = 0` — unconfigured == regular_user, one default story); `-1` = unlimited (explicit opt-in, equals the pre-feature full-source behavior); `0` = none (nothing fetched; warning when `source = true`); schema freeze; runtime binary closure never truncated (FR-003, Constitution II — installability + version alignment); source/build-tool support coherent (every fetched source buildable, FR-013); profiles are data-only (no profile concept in code, no name-keyed behavior); clear config error for a depth below `-1` or a non-integer/boolean value (FR-005); sources/build tools retained, never pruned (FR-014); verify depth gate (FR-015, no schema change — finite depth: unlinked informational-only; `-1`: strict); `max_build_dep_report_depth` uses the same `-1`/`0`/`N` convention with `-1` preserving the prior "report all" behavior (FR-017).

**Scale/Scope**: The developer profile targets caches far smaller than full recursion — the graybeard example (a couple of packages → >13 700 binaries, >25 000 sources, >30 GB) motivates the cap; success is measured by subset relationships on the source/build-tool set, not absolute numbers.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- ✅ **I. Offline-First Availability** — unchanged; the depth knob only changes how much the *admin* puts in the cache; the operator still consumes a complete (scoped) cache fully offline. The runtime closure stays complete, so installability offline is preserved.
- ✅ **II. Dependency Alignment** — the runtime binary tree is always complete, so every requested package resolves to a version whose full runtime dependency tree is satisfied by the cache — no version drift. Within the source/build-tool scope, resolution still selects compatible versions (shared `_visited` walk, constraints).
- ✅ **III. Binary-Source Consistency** — graybeard satisfies it fully (every binary has its source and build tools). The developer profile provides sources for the in-scope set (so its rebuild targets are rebuildable) and documents a scoped exception: build-tool binaries at a finite depth have no source of their own (they are tools, not rebuild targets). Out-of-scope binaries are installable but deliberately have no source — the developer profile's explicit trade.
- ✅ **IV. Minimal Local Cache** — this feature *is* the minimality knob: the depth bounds source/build-tool support, and the regular_user profile omits sources/build tools entirely (Constitution IV).
- ✅ **V. Debian Policy Compliance** — no package modification/building in this feature; existing verification gates unchanged (plus the depth gate, FR-015).
- ✅ **VI. Code Quality & Type Safety** — the cap is a typed `int` in the frozen `ProjectConfig` dataclass; level accounting is a single integer threaded through the recursive walk; validation raises the existing typed `ConfigError`.
- ✅ **VII. Database-First Resolution** — unchanged; resolution still populates the DB (binaries/sources/`source_build_deps`) before any download; the depth only bounds *which binaries get source records*. `download` follows the DB and therefore respects the scope automatically.
- ✅ **VIII. Documentation & Infrastructure Traceability** — the three profile files, their intended use, and the version-drift rationale ship in `docs/` in this feature (FR-012); `roles.md` gets a note that profiles are admin-side config presets; quickstart + contracts updated here.
- ✅ **IX. Incremental Spec Scope** — 3 stories / 15 FRs, within limits; tutorials are explicitly deferred (spec assumptions).

**Gate result: PASS** — no violations requiring Complexity Tracking.

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] The profiles and `max_source_dep_depth` are **admin-only** configuration presets; they change what `add`/`ensure-base`/`download`/`verify`/`config` do on the networked build machine. No new operator command and no operator-facing artifact is introduced.
- [x] No persona-dependent behavior leaks across the offline/online boundary — the depth setting and profile files exist only in the admin's project TOML; the operator's handoff (012) / diff (013) flows and tooling are untouched.
- [x] Cache handoff expectations are reflected — a scoped (developer/regular_user) cache hands off exactly like any other cache; the operator's apt source and `apply.sh` are unaffected.
- [x] [roles.md](../memory/roles.md) gains a short note that profiles are admin-side config presets (a documentation task in this feature); no role or command assignments change.

## Project Structure

### Documentation (this feature)

```text
specs/014-base-profiles/
├── plan.md              # This file
├── research.md          # Phase 0: version-drift rationale, source-dep-depth semantics, enforcement, config, profiles
├── data-model.md        # Phase 1: config field, profile files, source-scope model
├── quickstart.md        # Phase 1: per-profile validation scenarios
├── contracts/           # Phase 1: config command contract (text/JSON), config-error contract, verify gate
└── tasks.md             # Phase 2 output (/speckit.tasks - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/package_mgmt/
├── cli/
│   └── commands/
│       ├── config.py            # + max_source_dep_depth in text + JSON output
│       └── verify.py            # + depth gate (FR-015, no schema change): finite depth → unlinked binaries
│                                #   informational-only (never defects); -1 → strict unlinked check unchanged
├── lib/
│   ├── model/
│   │   └── config.py            # ProjectConfig + max_source_dep_depth: int = 0; fetch_source default False (match shipped presets)
│   ├── service/
│   │   ├── config_loader.py     # + _KEY_MAP entry + validation (below -1 / non-int / bool → ConfigError) + depth-0 warning
│   │   └── resolver.py          # + per-binary level accounting; source records only for in-level binaries; depth-0 warning
│   └── util/
│       └── errors.py            # (unchanged) ConfigError / EXIT_CONFIG_ERROR reused
docs/
├── profiles/                    # NEW: three showcase project-setting presets (FR-006)
│   ├── regular_user.toml        # [fetch] source = false, max_source_dep_depth = 0 (no sources; no warning needed)
│   ├── developer.toml           # [fetch] source = true, max_source_dep_depth = 1
│   └── graybeard.toml           # [fetch] source = true, max_source_dep_depth = -1 (unlimited)
project.defaults.toml            # regular_user presets: [fetch] source = false, [resolution] max_source_dep_depth = 0
tests/
├── unit/test_config.py              # defaults match shipped presets (fetch_source=false, depth=0); below -1 / non-integer / boolean → ConfigError (both depth settings)
├── unit/test_resolver_batch.py      # + level accounting: source records within depth only; runtime closure
│                                    #   identical across depths; boundary build tool resolved w/o own source
├── unit/test_resolver_build_deps.py # + build tools only for in-scope sources; strict subset; backward-compat at -1
├── unit/test_verify.py              # + depth gate: finite depth → unlinked informational-only + COMPLETE; -1 → strict check
├── integration/test_add_file_flow.py  # profile toml used as project.defaults.toml
├── integration/test_download_flow.py  # fetch_source=false + depth → binaries only, no source work
└── contract/test_cli.py            # `config` output shapes + invalid-depth JSON error (exit 102);
                                    #   fix test_json_format_structure: add [fetch] source = true (or assert False)
```

### Profile presets (implementation deliverable)

Each file is a **complete, standalone project setting** (a drop-in `project.defaults.toml`, with `project.local.toml` overlays still applying on top) — mirror, release, components, paths, database, logging, fetch, resolution — with the profile's distinguishing settings and inline comments describing the intended use:

| File | `[fetch] source` | `max_source_dep_depth` | Resulting cache |
|------|------------------|------------------------|-----------------|
| `docs/profiles/regular_user.toml` | `false` | `0` | Requested binaries + full runtime closure; no sources, no build tools |
| `docs/profiles/developer.toml` | `true` | `1` (illustrative finite value) | Full runtime closure + sources and build tools for the requested set and its direct dependencies |
| `docs/profiles/graybeard.toml` | `true` | `-1` (unlimited) | Full recursion — every binary has its source + build tools (Constitution III); the `ensure-base` profile |

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No constitution violations requiring Complexity Tracking — the developer profile's scoped source/build-tool support (and its documented Constitution III exception for build-tool binaries) is an explicit, documented trade in the spec, not an unapproved violation. The only added complexity (level accounting in the resolver + the verify depth gate) is minimal and typed, and is covered by unit tests for subset/boundary/backward-compatibility behavior.
