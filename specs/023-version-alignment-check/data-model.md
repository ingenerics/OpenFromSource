# Data Model: Version Alignment Check

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

**Schema freeze (spec 010/011).** No database schema change, no migrations, no new
tables or columns (Q5). The alignment baseline is read-only from the existing `binaries`
table and cached `Packages.gz`. All entities below are **file artifacts or in-memory
frozen dataclasses** — never DB records.

---

## 1. Version manifest (file artifact, `manifests/hyprland-<era>.toml`)

The machine-checkable artifact (spec Key Entity "Version manifest"; FR-005/FR-013). TOML,
git-tracked, **produced by `align-repo` on success (Q7)**. Format contract:
[contracts/version-manifest.md](contracts/version-manifest.md).

| Field | Type | Meaning |
|-------|------|---------|
| `era` | str | release-era label, e.g. `0.45` |
| `chosen` | date | when the era was selected |
| `reason` | str | why this era was chosen (the selection rationale, FR-005) |
| `[[repo]]` | list | one entry per in-scope repo (12) |

**Per-repo entry (`ManifestRepo`):**

| Field | Type | Meaning |
|-------|------|---------|
| `name` | str | repo name, e.g. `Hyprland` |
| `url` | str | git remote |
| `tag` | str | selected tag, e.g. `v0.45.0` |
| `commit` | str | exact commit SHA (the pin) |
| `build` | enum `meson` \| `cmake` | build-metadata format of this ref (research §1) |
| `reason` | str | why this ref (era rationale per repo) |

**Validation rules (FR-001/002/005)**: every in-scope repo present exactly once; the 4
out-of-scope tools absent (Q4); `commit` must be a full SHA; `build` must be one of the
two known values.

**Key relationship**: the manifest is the *input* to acquisition (docs/005) and the
*subject* of the alignment check. `commit` is the reproducibility pin (FR-006/009) —
`repos/<name>` `HEAD` must equal it (US2).

---

## 2. `repos/` source tree (filesystem, git-ignored)

The acquired trees (spec Key Entity "`repos/` source tree"; FR-006). Layout:

```text
repos/<name>/            # one tree per ManifestRepo, checked out at manifest commit
repos/<name>/.git        # full clone (or shallow at commit) — NOT relied on at build
repos/<name>/<submodules># vendored submodules at their recorded commits
```

**Validation rules (US2, FR-006)**: `git rev-parse HEAD` == manifest `commit` for every
repo; submodules present at recorded commits; tree buildable offline (no lazy fetch).

**Relationship**: `repos/<name>/<build metadata>` (meson.build / CMakeLists.txt / VERSION)
is the source the check parses to extract `DependencyRequirement`s (research §1).

---

## 3. `DependencyRequirement` (in-memory, `lib/model/alignment.py`)

One declared dependency requirement extracted from a repo's build metadata.

| Field | Type | Meaning |
|-------|------|---------|
| `repo` | str | owning repo (manifest name) |
| `module` | str | declared name — pkg-config module (`wayland-server`, `libinput`) or companion (`aquamarine`, `hyprwayland-scanner`, `hyprland-protocols`) |
| `operator` | str | `>=`, `>`, `<=`, `<`, `=` (from `version:`/`pkg_check_modules(...)`/`find_package(<Name> <ver>)`) |
| `version` | str | constraint target |
| `tier` | enum `cache` \| `ecosystem` | validation tier (research §3) — `ecosystem` when `module` names another manifest repo or a built tool |
| `source_ref` | str | `repo:<relative-path>:<line>` for traceability |
| `optional` | bool | `required: false`/`get_option()`-gated in meson, or a `QUIET`/optional find → **excluded** from the pass/fail (research §1) |

**Validation rules**: `operator` in the supported set; `version` non-empty and
Debian-parseable; `tier` determined by whether `module` resolves to a manifest member.

**Relationships**: belongs to one repo; maps (via the pkg-config→Debian table, research
§5) to one baseline package for cache-tier checks; maps to one manifest repo's provided
version for ecosystem-tier checks.

---

## 4. Cached trixie baseline (derived, read-only)

The alignment baseline (spec Key Entity "Cached trixie tree"; FR-004). **Not stored** by
this feature — queried live:

```text
SELECT package_name, version FROM binaries
 WHERE release = 'trixie' AND component = 'main' AND verified = 1
```
cross-checked against cached `Packages.gz` via `load_binary_index` for
`Provides`/`:any`/multi-arch lookups.

| Attribute | Meaning |
|-----------|---------|
| `package_name` | Debian binary package |
| `version` | cached version (Debian version string) |
| `release` / `component` | `trixie` / `main` (baseline scope — backports/sid excluded by construction, FR-003) |
| `verified` | `1` (only verified records form the baseline) |

**Key relationship**: each cache-tier `DependencyRequirement.module` → one baseline
package (via the §5 mapping) → its `version`, compared with `(operator, version)` using
Debian semantics (research §6).

---

## 5. `AlignmentReport` / `UnmetRequirement` (in-memory, output entities)

The search/validation result of `align-repo` (contract:
[contracts/align-repo.md](contracts/align-repo.md)). The report carries a `mode`
(`search` or `verify`); search-only fields are present only in search mode, `manifest`
only in verify mode.

| `AlignmentReport` field | Type | Meaning |
|-------------------------|------|---------|
| `mode` | enum `search` \| `verify` | which mode produced the report |
| `repo_dir` | str | the `--repo-dir` scanned |
| `baseline` | str | description of the baseline used (trixie/main) |
| `manifest` | str \| None | (verify mode) the manifest path validated |
| `candidates_evaluated` | tuple[str, …] \| None | (search mode) eras evaluated, newest-first |
| `selected_era` | str \| None | (search mode) the newest satisfying era, or `None` on failure |
| `manifest_written` | str \| None | (search mode) manifest path written on success, else `None` |
| `requirements_checked` | int | count of non-optional requirements checked |
| `satisfied` | bool | PASS → exit 0 |
| `unmet` | tuple[UnmetRequirement, …] | the failures, ordered by repo |

| `UnmetRequirement` field | Type | Meaning |
|--------------------------|------|---------|
| `requirement` | DependencyRequirement | the failing requirement (incl. `source_ref`) |
| `cached_package` | str \| None | owning Debian package (cache tier) or manifest repo (ecosystem tier) |
| `cached_version` | str \| None | the version found, or `None` if nothing satisfies / module unmapped |
| `reason` | str | machine-readable failure reason (e.g. `not_in_trixie_main`, `unknown_module`, `version_too_low`) |

**Validation rule (FR-004)**: on failure the report names the unmet requirement(s) and the
offending ref — `align-repo` never fails silently.

---

## 6. Candidate era enumeration (derived)

The eras `align-repo` considers, newest-first, derived from the **git tags present in the
acquired repos** in `--repo-dir` (research §1/§2).

| Attribute | Meaning |
|-----------|---------|
| `era` | label taken from the common tag prefix across the set, e.g. `0.45` (Hyprland `v0.45.0`) |
| `repo_tags` | each repo's tag for that era (or a skip if the repo has no tag for the era) |
| `build` | meson or cmake for that ref (may vary by era) |

**Rule (US1, edge case "Era ambiguity")**: eras are evaluated newest→oldest; the first
(all-requirements-satisfied) era wins and is recorded with its rationale in the manifest.
If eras are not strictly ordered, the choice is surfaced for the Administrator.

**Relationship**: each candidate era maps to a set of `DependencyRequirement`s (§3) for
its 12 refs; `align-repo` evaluates those sets against the baseline (§4) and provided
versions (§7) until one passes or the list is exhausted (failure).

---

## 7. Provided versions for the ecosystem tier (derived)

The version each selected source provides, used for companion-to-companion checks
(FR-002; research §3). Resolved from `repos/<name>/VERSION` at the pinned commit, falling
back to the manifest `tag` when no `VERSION` file exists.

| Repo | Source of provided version |
|------|----------------------------|
| Hyprland, hyprlang, hyprutils, aquamarine, hyprpaper, hyprlock, hyprcursor, hyprgraphics, hyprwayland-scanner, hyprland-protocols, hypridle, xdg-desktop-portal-hyprland | `VERSION` file at pinned commit (fallback: tag) |

**Relationship**: an ecosystem-tier `DependencyRequirement` (`aquamarine>=0.15.0`,
`hyprwayland-scanner>=0.3.10`, `hyprland-protocols>=0.6.0`, …) compares the constraint
against the target repo's provided version.

---

## 8. Build order (derived, documented — FR-008)

The deterministic bottom-up sequence recorded in `docs/005-hyprland-source-stack.md`
(spec Key Entity "Build order"). Not executable product code (Q6):

```text
1. hyprland-protocols            # protocol headers, no hypr deps
2. hyprwayland-scanner           # build-time codegen tool (scanner)
3. hyprgraphics                  # image/shared rendering helpers
4. hyprutils                     # core utilities (depended on by most)
5. hyprlang                      # config language
6. aquamarine                    # rendering/backend abstraction (needs 3-5)
7. hyprcursor                    # cursor themes (needs 4, 6)
8. Hyprland                      # compositor (needs 3-7, scanner)
9. hypridle / hyprlock / hyprpaper / xdg-desktop-portal-hyprland   # companions (need 3-8)
```

**Constraint (FR-008)**: each component builds against its already-built dependencies;
the order is validated implicitly because companion `find_package`/`pkg_check_modules`
requirements are only satisfiable after their providers are built and installed in place.

**Key relationship**: the build order is the sequence in which `repos/` trees are
compiled; it is the bridge between the manifest (what to build) and the functional setup
(what must run).

---

## State transitions

`align-repo` governs the era-selection and manifest-readiness lifecycle; a requirement is
a per-requirement state:

```
[requirement unmet]  --align-repo (cache & ecosystem tiers)-->  [requirement met]
        │                                                                  │
        │  unmet + named failure (FR-004)                                  │
        └──────────────────────────────>  era rejected; next-older era tried,
                                          or FAIL if no era satisfies (FR-013)
```

The overall workflow state machine (documented, not a product state machine):

```
[sources acquired (repos/)]
   -> [align-repo selects newest satisfying era + writes manifest (PASS)]
   -> [bottom-up build] -> [functional setup] -> [validated]
[no satisfying era] -> FAIL (no manifest written; repos/cache must change, FR-013)
```

`align-repo` is the **gate** between acquisition and build: the manifest it writes on
success (search mode) is what acquisition-pinned repos were captured for and what the
build consumes. `--verify <manifest>` is the re-validation gate — a change to the cache or
repos can only move its outcome to FAIL (or, in search mode, select an older era); it can
never silently pass a stale alignment (FR-013, Q8).
