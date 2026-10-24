# Research: Version Alignment Check

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

**Method.** The unknowns in Technical Context were resolved by inspecting the **real
frozen cache** (`/var/cache/package_mgmt/package_mgmt.db` — 14,856 binaries / 8,193
sources, all `trixie/main`, `verified=1`) and **shallow-cloning the 12 in-scope hyprwm
repos at their current main** plus the `v0.45.0` tag, then reading their build metadata.
The spec's Assumptions/Clarifications (Q1–Q6) record the settled scope decisions; this
file documents the technical contract the alignment check relies on and the era-reality
that shapes it.

---

## 1. Build metadata is dual-format: meson (older eras) and CMake (newer eras)

**Decision**: the alignment check must parse dependency requirements from **both**
`meson.build` (older eras; still used by `xdg-desktop-portal-hyprland`) and
`CMakeLists.txt` (newer eras). The manifest records each repo's `build` system
(`meson` | `cmake`) so the correct parser is selected per ref.

**Evidence** (shallow clones at main):
- 11 of 12 repos now ship a top-level `CMakeLists.txt` — Hyprland, hyprlang, hyprutils,
  aquamarine, hyprpaper, hyprlock, hyprcursor, hyprgraphics, hyprwayland-scanner,
  hyprland-protocols, hypridle.
- `xdg-desktop-portal-hyprland` still ships `meson.build`
  (`dependency('systemd', required: get_option('systemd'))`, …).
- Hyprland `v0.45.0` (an older, meson-era release) declares deps as
  `aquamarine = dependency('aquamarine', version: '>=0.4.2')`, `gio_dep =
  dependency('gio-2.0', required: true)`, xcb deps, etc.

**CMake patterns to parse** (Hyprland `CMakeLists.txt`, main):
```cmake
find_package(hyprwayland-scanner 0.3.10 REQUIRED)          # find_package(<Name> <ver> REQUIRED)
pkg_check_modules(deps REQUIRED IMPORTED_TARGET GLOBAL      # pkg_check_modules(... <mod>>=<ver> ...)
  xkbcommon>=${XKBCOMMON_MINIMUM_VERSION} …)
set(LIBINPUT_MINIMUM_VERSION 1.29)                          # version via variable
pkg_search_module(LUA REQUIRED … lua>=5.5 lua<5.6)
find_package(glslang CONFIG REQUIRED)                       # no version
find_package(OpenGL REQUIRED COMPONENTS GLES3)
```

**Meson patterns to parse** (Hyprland `v0.45.0` `meson.build`):
```meson
aquamarine = dependency('aquamarine', version: '>=0.4.2')
xcb_dep = dependency('xcb', required: get_option('xwayland'))
gio_dep = dependency('gio-2.0', required: true)
```
Requirements appear as `dependency('<module>', version: '<op><ver>')`; `required: false`
or a get_option gate marks optional deps.

**Rationale**: the era chosen by the "newest satisfying era" rule (§2) determines the
format; locking the check to one format would force the era choice and break the
re-runnability guarantee (FR-013) when the tree changes. Parsing both is bounded work
(§2 shows both are line-based).

**Alternatives considered**:
- *Run `meson introspect` / `pkg-config` on each tree*: requires the build toolchain
  installed and configured; heavier and not available offline pre-build. Rejected.
- *Hand-maintained requirement list per era*: not re-runnable against a changed tree
  (FR-013). Rejected.

---

## 2. Era reality: the newest CMake era does NOT align with the frozen trixie tree

**Decision**: the "newest satisfying era" search will land on an **older, meson-based
era**; the check must therefore be meson+cmake-capable regardless of the era the version
research ultimately selects (which is a plan/tasks deliverable, per the spec's
"Version determination" assumption).

**Evidence** — Hyprland `main` `CMakeLists.txt` declares requirements the frozen cache
cannot satisfy:

| Requirement (main) | Cached trixie (real DB) | Verdict |
|--------------------|-------------------------|---------|
| `glaze` via `find_package(glaze)` with **FetchContent fallback** (`GIT_TAG v7.2.0`) | not packaged in Debian (no `glaze`/`libglaze*` in the cache) | ✗ network-at-build → offline violation |
| `libinput>=1.29` (`LIBINPUT_MINIMUM_VERSION 1.29`) | `libinput-dev 1.28.1-1+deb13u1` | ✗ |
| `wayland-protocols>=1.49` | `wayland-protocols 1.44-1` | ✗ |
| `xkbcommon>=1.11.0` (`XKBCOMMON_MINIMUM_VERSION 1.11.0`) | `libxkbcommon-dev 1.7.0-2` (1.7.0 < 1.11.0) | ✗ |

(All other `pkg_check_modules` deps of main — `uuid`, `cairo`, `pango`, `pangocairo`,
`pixman-1`, `xcursor`, `libdrm`, `libeis-1.0`, `gbm`, `gio-2.0`, `re2`, `muparser`,
`lcms2`, `libcanberra` — are present in trixie: `libre2-dev 20240702-3+b1`,
`libmuparser-dev 2.3.4-1+b1`, `liblcms2-dev 2.16-2+deb13u2`, `libcanberra-dev 0.30-18`,
`libeis-dev 1.3.901-1`, `libxcursor-dev 1:1.2.3-1`.)

**Rationale**: this is the concrete, grounded justification for the spec's exclusion rule
(FR-003) and for the parser duality (§1). It also proves the check must treat a
**FetchContent/network fallback as an unmet requirement** (offline by construction).

**Alternatives considered**: none — this is a measured property of the current tree, not
a design choice. The era itself is selected during implementation and recorded with its
rationale in the manifest (FR-005).

---

## 3. Two validation tiers: the cache tier and the ecosystem tier

**Decision**: the check validates every declared requirement against **two** sources of
truth:
- **Cache tier** (FR-001/003/004): each declared dependency's version constraint is
  compared against the cached trixie version of the owning Debian package. Nothing from
  `trixie-backports`/sid exists in the baseline, so drift is excluded by construction and
  any requirement satisfied only outside the baseline fails loudly.
- **Ecosystem tier** (FR-002): requirements that name another member of the selected set
  (companions, `hyprwayland-scanner`, `hyprland-protocols`) are compared against the
  **version each selected source will provide** — resolved from the repo's `VERSION` file
  at the pinned commit (falling back to the tag), not against the cache.

**Evidence** (internal constraints observed at main and v0.45.0):
- Hyprland `CMakeLists.txt`: `pkg_check_modules(aquamarine>=0.15.0 …)`,
  `hyprlang>=0.6.7`, `hyprcursor>=0.1.7`, `hyprutils>=0.14.0`, `hyprgraphics>=0.5.1`;
  `find_package(hyprwayland-scanner 0.3.10 REQUIRED)`.
- hyprpaper: `find_package(hyprwayland-scanner 0.4.0 REQUIRED)`; hyprlock and hypridle:
  `0.4.4`; hypridle additionally `pkg_check_modules(hyprland_protocols_dep …
  hyprland-protocols>=0.6.0)`.
- Hyprland `v0.45.0`: `dependency('aquamarine', version: '>=0.4.2')`.

**Rationale**: the ecosystem releases in lockstep but the companions bump their own
minimums across the era (spec edge case "Companion drift within an era"); validating the
pairings against the *selected* source versions (not the cache) is what proves internal
consistency (FR-002), while the cache tier proves the trixie alignment (FR-001).

**Alternatives considered**:
- *Cache-only validation*: would pass companions that aren't packaged in Debian (none are
  in trixie) and would miss internal drift. Rejected.
- *Ecosystem-only validation*: misses trixie alignment. Rejected.

---

## 4. Alignment baseline: DB `binaries` (trixie/main, verified) + cached `Packages.gz`

**Decision**: the cache tier's baseline is the **DB `binaries` table** filtered to
`release='trixie'`, `component='main'`, `verified=1` as the primary source of exact
package versions, **cross-checked against the cached `Packages.gz`** (existing
`load_binary_index`) for `Provides`/virtual and multi-arch (`:any`) name resolution.

**Evidence**: the real DB at `/var/cache/package_mgmt/package_mgmt.db` carries
`(package_name, version, release, component, verified)` with `verified=1` for every
baseline package (e.g. `libwayland-dev 1.23.1-3`, `wayland-protocols 1.44-1`,
`libinput-dev 1.28.1-1+deb13u1`, `libseat-dev 0.9.1-1`, `meson 1.7.0-1`,
`qt6-base-dev 6.8.2+dfsg-9+deb13u2`, `xwayland 2:24.1.6-1`, `cmake 3.31.6-2`,
`gcc-14 14.2.0-19`).

**Rationale**: the DB is the fast, versioned, spec-named baseline (FR-004 names "the
database and cached indexes"); the index is the authoritative record for virtual
package/provides lookups. Reading both is read-only (Q5).

**Alternatives considered**: *index-only* — slower and loses the `verified`/component
semantics; *DB-only* — misses `Provides` resolution. Combined is required by FR-004.

---

## 5. pkg-config module name → Debian package mapping

**Decision**: ship a **curated static mapping table** from pkg-config module names (as
they appear in `dependency('...')`/`pkg_check_modules(...)`) to the Debian binary package
that owns the module's `.pc` file in trixie (e.g. `wayland-server` → `libwayland-dev`,
`xkbcommon` → `libxkbcommon-dev`, `libinput` → `libinput-dev`, `gio-2.0` →
`libglib2.0-dev`, `gbm` → `libgbm-dev`, `pangocairo` → `libpango1.0-dev`,
`pixman-1` → `libpixman-1-dev`, `xcursor` → `libxcursor-dev`, `libdrm` → `libdrm-dev`,
`uuid` → `uuid-dev`, `libeis-1.0` → `libeis-dev`, `lcms2` → `liblcms2-dev`,
`libcanberra` → `libcanberra-dev`, `re2` → `libre2-dev`, `muparser` → `libmuparser-dev`,
`lua5.5` → `liblua5.5-dev`). An unknown module is **reported as an unmet/unverifiable
requirement** (loud failure) rather than silently skipped.

**Rationale**: the ecosystem's dependency set is bounded and known (the spec's Alignment
baseline lists them); a deterministic, testable table is simpler and more predictable than
probing the cache for the `.pc`-owning package, and unknown modules failing loudly matches
FR-004's "fail, naming the unmet requirement".

**Alternatives considered**:
- *Derive at runtime via `dpkg -S <mod>.pc`*: requires the tool to shell out and the
  `.pc` files installed; not portable to the check's environment and not offline-clean.
  Rejected.

---

## 6. Version-comparison semantics (Debian)

**Decision**: reuse `debian.debian_support.Version` via the existing
`lib/util/versions.py` (`version_compare`, `compare_versions`). A requirement
`<op> <version>` parsed from build metadata is compared against the cached Debian version
of the owning package using Debian comparison rules. `>=` **includes equality** — a
requirement `>= 1.29` is satisfied by cached `1.29-1` (spec edge case "Boundary version").

**Rationale**: the spec mandates Debian version-comparison semantics (US1, edge case); the
project already implements them in `versions.py` with `python-debian`, so no new
dependency or duplicate logic.

**Note**: pkg-config module versions (from `.pc`) may differ textually from Debian package
versions (`1.23.1` vs `1.23.1-3`); the comparison is always requirement-vs-**cached Debian
version**, which is the contract FR-007 pins.

---

## 7. Backports / sid exclusion by construction (FR-003)

**Decision**: the baseline contains only `trixie/main` verified binaries; a requirement
that no trixie/main record satisfies is reported unmet with the offending dependency and
constraint named. Because backports/sid records are never in the baseline, "drift by
construction" cannot pass silently — the absence of a satisfying trixie/main record *is*
the failure (matches the spec edge case "Backports-only dependency").

**Rationale**: simplest and provably aligned with FR-003; no extra component filtering is
needed beyond the baseline query.

---

## 8. Re-runnability (FR-013)

**Decision**: `align-repo` re-derives the era from the *current* cache and the *current*
`repos/` trees on every run (Q7 — the search and its validation are the same re-runnable
tool); a change to either can only move the outcome to a **newer-era rejection** (select
an older era or FAIL), never silently keep a stale alignment. The manifest records the era
rationale so a future run explains why the choice was made.

**Rationale**: FR-013 requires the check and acquisition to be re-runnable and fail when
alignment no longer holds; `align-repo` being a single search+validate tool makes this
automatic — re-running is always a fresh evaluation.

---

## 9. Non-goals confirmed

- No DB schema change, no migration, no new tables (Q5).
- No new runtime dependency (reuses `python-debian` + `load_binary_index`).
- Acquisition and build are documented procedures (`docs/005`), not productized test
  targets (Q6) — `align-repo` is the only productized component.
- No packaging into the `local` component (US5/FR-011 follow-up).
- The actual era/tags/commits are produced by `align-repo` from the acquired repos and
  recorded in the manifest; this feature's design does not hard-code them.

---

## Consolidated decisions

| Decision | Choice | Artifact |
|----------|--------|----------|
| Build-metadata parsing | both meson.build and CMakeLists.txt; manifest records `build` per repo | `buildmeta.py` (§1) |
| Era reality | newest CMake era fails (glaze/libinput/protocols/xkbcommon) → older meson era; FetchContent = unmet | §2 |
| Validation tiers | cache tier (vs trixie/main) + ecosystem tier (vs selected source versions) | `alignment.py` (§3) |
| Baseline source | DB `binaries` (trixie/main, verified) + cached `Packages.gz` via `load_binary_index`, read-only | `alignment.py` (§4) |
| Module mapping | curated pkg-config→Debian table; unknown module = loud failure | `alignment.py` (§5) |
| Version semantics | `debian.debian_support.Version`; `>=` includes equality | reuse `versions.py` (§6) |
| Backports/sid | excluded by construction (baseline is trixie/main only) | `alignment.py` (§7) |
| Re-runnability | `align-repo` re-derives the era from current cache + repos each run | `align_repo.py` (§8) |
