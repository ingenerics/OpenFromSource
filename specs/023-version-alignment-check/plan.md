# Implementation Plan: Version Alignment Check

**Branch**: `023-version-alignment-check` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/023-version-alignment-check/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Feature 023 prevents version drift when building the Hyprland stack from source: it
determines the **newest Hyprland release "era"** whose declared build- and runtime-
dependency requirements are all satisfied by the **frozen trixie cache**, records that
choice as a **machine-checkable version manifest**, acquires the 12 essential pinned
sources into `repos/`, builds the stack bottom-up offline from the cache, and reaches a
functional in-place Hyprland setup (spec FR-001…FR-012, SC-001…SC-005).

Three settled scope decisions drive the design:

1. **The alignment check is productized as maintained in-project tooling (Q6/Q7/Q8)** —
   a new admin CLI command `package_mgmt align-repo` with two modes: the default mode
   scans the acquired repos (`--repo-dir DIR`), determines the **newest satisfying era**,
   and **produces the version manifest with success or failure**; the `--verify MANIFEST`
   mode **re-validates a given manifest** against the current cache/`repos/` without
   searching or writing (the explicit FR-013 re-validation path). Unit + contract + flow
   tests cover both modes. **Acquisition and build are documented procedures (FR-012),
   not productized test targets** — they are recorded in `docs/005-hyprland-source-stack.md`
   and driven by the manifest.
2. **Database role is read-only baseline (Q5)** — no schema change, no migrations, no
   new tables. The check reads the existing `binaries` table (trixie/main, `verified=1`)
   and the cached `Packages.gz` via `load_binary_index`; the source set lives only in the
   file-based manifest. DB records for the stack's binaries/sources arrive with the
   packaging follow-up (US5/FR-011).
3. **Scope is the 12 essential projects (Q4)** — Hyprland + aquamarine, hyprutils,
   hyprlang, hyprcursor, hyprgraphics, hyprwayland-scanner, hyprland-protocols,
   hyprpaper, hypridle, hyprlock, xdg-desktop-portal-hyprland. hyprpicker,
   hyprpolkitagent, hyprland-qtutils, hyprsunset are out entirely.

**Core technical finding (research.md §2).** The current CMake-based main-branch era does
**not** align with the frozen tree — it requires `glaze` (FetchContent fallback, not
packaged in Debian), `libinput>=1.29` (cache has 1.28.1), `wayland-protocols>=1.49`
(cache has 1.44) and `xkbcommon>=1.11.0` (cache has 1.7.0). The "newest satisfying era"
rule therefore walks back to an older, meson-based era, and the check must parse **both**
build-metadata formats (meson.build and CMakeLists.txt).

**No new runtime dependencies.** Version comparison reuses `debian.debian_support.Version`
(`lib/util/versions.py`); build-metadata parsing is line-based, no parser library.

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default); the `package_mgmt`
project at repo root. New code lives under the existing `src/package_mgmt/` package.

**Database**: SQLite 3.x — frozen spec 010/011 schema, read-only. The alignment baseline
is `binaries` (`package_name`, `version`, `release='trixie'`, `component='main'`,
`verified=1`) cross-checked against the cached `Packages.gz` via the existing
`load_binary_index` (`lib/service/index_loader.py`). No schema change, no population step
(Q5). Real cache at `/var/cache/package_mgmt/package_mgmt.db` holds 14,856 binaries /
8,193 sources and all key trixie versions the spec lists (wayland 1.23.1,
wayland-protocols 1.44, mesa 25.0.7, glslang 15.1.0, libinput 1.28.1, gcc-14 14.2.0,
Qt 6.8.2, …).

**Primary Dependencies**: python-debian (`debian.debian_support.Version` — already used by
`lib/util/versions.py`). No new runtime dependency. `load_binary_index` already loads the
cached archive index.

**Storage**:
- `manifests/hyprland-<era>.toml` — the git-tracked, machine-checkable version manifest
  (Q5; file artifact), **produced by `align-repo` on success (Q7)**. `*.manifest` is
  gitignored, so the manifest uses `.toml`.
- `repos/` (project root) — the 12 acquired source trees pinned to manifest commits
  (git-ignored; reproducible from the manifest by the documented acquisition procedure).
- `docs/005-hyprland-source-stack.md` — the documented acquisition + build + functional
  setup + validation procedure (FR-012, Q6).
- Baseline reads only the existing cache: `{base}/binary/dists/.../Packages.gz` + DB.

**Testing**: pytest **and** `python -m unittest discover -s tests` (both runners,
Constitution). New/changed:
- `tests/unit/test_alignment.py` (NEW): manifest load/validation, era enumeration from
  git tags, meson parser, cmake parser, pkg-config→Debian mapping, Debian
  version-comparison semantics (boundary `>=` exact match, backports exclusion), report
  aggregation, exit semantics.
- `tests/contract/test_cli.py` (EXTEND): `align-repo` help/arg validation (search and
  `--verify` modes), exit codes, JSON output shape.
- `tests/integration/test_align_repo_flow.py` (NEW): end-to-end against a fixture cache
  (existing `tests/fixtures/` deb builder) + small fixture repos with real meson.build /
  CMakeLists.txt files and a few tags — search success writes the manifest, search failure
  names the newest era's unmet requirements, and `--verify` PASS/FAIL against the current
  cache.

**Target Platform**: Debian Trixie LTS amd64 / x86_64. The check runs on the
Administrator's networked system; the build/functional setup run fully offline.

**Project Type**: CLI tool for offline Debian LTS package management — admin builds the
cache and (this feature) the version-aligned source set; operator consumes the offline
result.

**Performance Goals**: `align-repo` completes the era search (a bounded number of
candidate eras × ~40 declared requirements per era) in < 10s; per-era evaluation is a few
hundred comparisons. Build-time and resolution goals untouched.

**Constraints**:
- **Schema freeze** (spec 010/011): no new tables, columns, or migrations (Q5).
- **No new runtime dependencies** and no new network call sites in product code; network
  is used only by the documented acquisition procedure (git clone, admin alignment time).
- **Debian version-comparison semantics**: requirement `>= V` is satisfied by cached
  `V` exactly (spec edge case); comparisons use `debian.debian_support.Version`.
- **Backports/sid excluded by construction** (FR-003): baseline contains only
  trixie/main verified records; any requirement satisfiable only outside it fails loudly.
- **Manifest is produced by `align-repo` and is the source of truth** for acquisition and
  build (Q5/Q7); `align-repo --verify <manifest>` re-validates a given manifest against
  the current cache and repos, and the default search mode re-derives the era — both are
  re-runnable (FR-013).
- **Two-tier validation**: cache tier (requirements vs cached trixie versions) and
  ecosystem tier (companion-to-companion requirements vs the selected source versions,
  e.g. `hyprwayland-scanner>=0.3.10` for Hyprland, `hyprland-protocols>=0.6.0` for
  hypridle) — FR-001/FR-002.
- **TDD**: tests written first; both runners + contract suite green at every phase.

**Scale/Scope**: one new CLI command (`align-repo`) with two modes, two new service
modules (`alignment.py`, `buildmeta.py`), one new model module (`lib/model/alignment.py`),
one manifest file (produced by the command), one documentation page, three contract docs,
four test-file changes. 12 repos, ~40 declared requirements total per era.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / Gate | Verdict | Basis |
|------------------|---------|-------|
| I. Offline-First Availability | ✅ PASS | Network is used only by the documented acquisition procedure on the admin's networked system; the check, build and functional setup run fully offline from `repos/` + cache. |
| II. Dependency Alignment | ✅ PASS | This feature *is* the alignment mechanism — it mechanically validates every declared dependency of every selected ref against the frozen tree and rejects drift-by-construction eras. |
| III. Binary-Source Consistency | ✅ PASS | No binary↔source linkage change; the `binary_to_source` bookkeeping arrives with the packaging follow-up (US5/FR-011). |
| IV. Minimal Local Cache | ✅ PASS | The check prevents pulling backports/sid (which would bloat and destabilize the cache); only required build helpers are added (FR-014). |
| V. Debian Policy Compliance | ✅ PASS | No packaging in this feature (in-place install, Q1); lintian/DFSG vetting belongs to the packaging follow-up. |
| VI. Code Quality & Type Safety | ✅ PASS | Frozen dataclasses for `VersionManifest`/`ManifestRepo`/`DependencyRequirement`/`AlignmentReport`; `TypedDict` only at the JSON output boundary; typed `Version` comparisons. |
| VII. Database-First Resolution | ✅ PASS | Per Q5 the DB stays the read-only alignment baseline; the git source set is driven by the file manifest, not DB records — no new download/fetch gate introduced by this feature's tooling. |
| VIII. Documentation & Infrastructure Traceability | ✅ PASS | `docs/005` records manifest, acquisition, build order, functional setup, validation; build helpers recorded in the dependency graph (FR-014). |
| IX. Incremental Spec Scope | ✅ PASS | 5 user stories, 14 FRs — under the 10/20 decomposition limit. |
| Schema freeze (010/011) | ✅ PASS | No `database.py` changes (Q5). |
| No new runtime deps | ✅ PASS (gate) | Reuses python-debian (`Version`) and existing `load_binary_index`. |
| Both test runners green | ✅ PASS (gate) | Baseline 808; TDD at every phase. |
| Contract suite green | ✅ PASS (gate) | `tests/contract/test_cli.py` extended for `align-repo`; existing contracts unchanged. |

**Gates: no violations. Complexity Tracking table not required.**

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] Commands are assigned to the correct persona (admin-only vs operator-only vs shared)
- [x] No persona-dependent behavior leaks across the offline/online boundary
- [x] Cache handoff expectations are reflected in the architecture
- [x] If this feature introduces new personas or changes existing roles, [roles.md](../memory/roles.md) has been updated accordingly

`align-repo` is a **new admin-only command** (runs on the networked system against the
cache DB + `repos/` and produces the manifest); the Operator's surface is unchanged
(documented `docs/005` procedure consumes the in-place setup).
[roles.md](../memory/roles.md) gains the `align-repo` row in the Administrator command
table and an entry in the Impact-on-Design table. No new personas, no operator-side
tooling.

## Project Structure

### Documentation (this feature)

```text
specs/023-version-alignment-check/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   ├── cli.md                # CLI contract for this feature (align-repo command)
│   ├── align-repo.md         # align-repo behavior/acceptance contract
│   └── version-manifest.md   # machine-checkable manifest file-format contract
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
manifests/
└── hyprland-<era>.toml          # NEW: version manifest (git-tracked, machine-checkable)

repos/                           # NEW (git-ignored): 12 acquired source trees pinned to
├── Hyprland/                    #   manifest commits, submodules included; reproducible
├── hyprlang/                    #   from the manifest via the docs/005 acquisition procedure
├── … (12 repos)

src/package_mgmt/
├── cli/
│   ├── main.py                  # MODIFY: register `align-repo` subcommand
│   └── commands/
│       └── align_repo.py        # NEW: handle_align_repo (admin-only; search + --verify modes)
└── lib/
    ├── model/
    │   └── alignment.py         # NEW: VersionManifest, ManifestRepo, DependencyRequirement,
    │                            #      AlignmentReport, UnmetRequirement (frozen dataclasses)
    └── service/
        ├── alignment.py         # NEW: era enumeration from git tags, load_baseline (DB +
        │                        #      Packages.gz), extract+check, pkg-config→Debian
        │                        #      mapping, manifest writing, report
        └── buildmeta.py         # NEW: parse_meson_requirements, parse_cmake_requirements

docs/
└── 005-hyprland-source-stack.md # NEW: acquisition + bottom-up build order + functional
                                 #      setup + validation procedure (FR-012, Q6)

tests/
├── unit/
│   └── test_alignment.py        # NEW: era enumeration, parsers, manifest write/load,
│                                #      mapping, version semantics (boundary, backports),
│                                #      report aggregation
├── contract/
│   └── test_cli.py              # MODIFY: align-repo args, exit codes, JSON shape
└── integration/
    └── test_align_repo_flow.py  # NEW: end-to-end against fixture cache + fixture repos
```

**Structure Decision**: `align-repo` fits the existing single-package CLI layout — a new
admin command (`cli/commands/align_repo.py`) backed by two focused service modules
(`alignment.py` for era enumeration/orchestration/baseline, `buildmeta.py` for
build-metadata parsing) and a frozen-dataclass model module. The manifest (produced by
the command) and the documented procedure are new top-level artifacts (`manifests/`,
`docs/005`) because they are durable, git-tracked deliverables the feature is defined
around.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No Constitution violations; the Complexity Tracking table is not required.
