# Contract: `package_mgmt align-repo`

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md) · **Data model**: [data-model.md](../data-model.md)
· **Manifest format**: [version-manifest.md](version-manifest.md)

**Contract policy.** `align-repo` is the **new admin-only command** and the only
productized component of this feature (Q6/Q7/Q8). It has two modes:
- **search mode** (default): the productized era search — scans the acquired repos, finds
  the newest era whose declared dependency requirements are all satisfied by the frozen
  trixie tree, and **produces the version manifest with success or failure**;
- **verify mode** (`--verify MANIFEST`): re-validates a **given manifest** against the
  current cache and `repos/` without searching or writing — the explicit FR-013
  re-validation path.

This feature's CLI surface is documented in [cli.md](cli.md), which **extends, not
diverges from**, the shared exit-code/JSON table in
`specs/014-base-profiles/contracts/cli.md`; the existing command contracts there are
untouched. The Operator's surface is unchanged.

---

## Synopsis

```text
package_mgmt align-repo [--verify MANIFEST] [--repo-dir DIR] [--format text|json]
```

| Argument | Meaning |
|----------|---------|
| `--verify MANIFEST` | **verify mode**: validate the given version manifest against the current cache and repos (never searches, never writes); absent → search mode |
| `--repo-dir DIR` | directory holding the 12 acquired source trees (default `repos/` at project root) |
| `--format text\|json` | output format; default `text` |

## Semantics

Both modes share the same requirement extraction and two-tier validation (research.md
§3–§6):

- **cache tier**: each declared dependency requirement vs the cached trixie/main version
  of the owning Debian package (read-only from the DB `binaries` + cached `Packages.gz`);
- **ecosystem tier**: a requirement that names another member of the set (`aquamarine`,
  `hyprwayland-scanner`, `hyprland-protocols`, …) vs the version that selected source
  provides.

**Search mode.** Walks the candidate release eras from **newest to oldest** (enumerated
from the git tags of the repos in `DIR`). The **newest era whose requirements are all
satisfied** is selected; on selection it **writes the version manifest**
(`manifests/hyprland-<era>.toml`, contract: [version-manifest.md](version-manifest.md))
with each repo's tag, pinned commit SHA, build format and rationale (FR-005), and reports
**success (exit 0)**. If **no** era satisfies the criterion, it reports **failure
(exit 1)** naming the newest era's unmet requirements (FR-004).

**Verify mode.** Loads and schema-validates `MANIFEST` (contract:
[version-manifest.md](version-manifest.md)); for each pinned ref extracts its declared
requirements from the build metadata in `DIR` at the pinned commit and runs the same
two-tier validation against the **current** cache. Reports **PASS (exit 0)** when every
requirement is satisfied, **FAIL (exit 1)** naming the unmet requirement(s) — it never
writes a manifest and never searches other eras (Q8, FR-013).

Common rules:
- `>=` includes equality — a cached version exactly equal to the requirement target is
  satisfied (boundary case).
- A requirement with no trixie/main record (backports/sid-only, or unpackaged with a
  FetchContent fallback such as `glaze`) is **unmet** and named (FR-003).
- An unknown pkg-config module (no curated mapping) is **unmet `unknown_module`** — never
  silently skipped (FR-004).
- Optional deps (`required: false` / `get_option()`-gated in meson; `QUIET`/optional
  finds in cmake) are excluded from pass/fail but may be listed in verbose detail.
- A repo missing from `DIR`, an unreadable tree, a malformed tag set, or (in verify mode)
  a malformed/invalid `MANIFEST` is a **load error** (below), never a partial result.

## Text output

**Search mode, success:**

```text
$ package_mgmt align-repo --repo-dir repos/
Alignment: scanning 12 repos for the newest satisfying era
  baseline: trixie/main (verified, read-only)
  candidates evaluated: 0.44 → 0.45 → 0.46 → 0.47 (newest fails, 0.45 selected)
  requirements checked: 41 (36 cache-tier, 5 ecosystem-tier)
  manifest written: manifests/hyprland-0.45.toml
  result: PASS
```

**Search mode, failure** — the newest era's unmet requirements with source refs:

```text
$ package_mgmt align-repo --repo-dir repos/
  candidates evaluated: 0.44 → 0.45 → 0.46 → 0.47 → 0.48 (all fail)
  newest era 0.48 — 2 unmet requirement(s)
  FAIL Hyprland -> libinput>=1.29   (cached libinput-dev 1.28.1-1+deb13u1: version_too_low)
       src: repos/Hyprland/CMakeLists.txt:263
  FAIL Hyprland -> glaze            (no trixie/main record: not_in_trixie_main)
       src: repos/Hyprland/CMakeLists.txt:131
  result: FAIL
```

**Verify mode:**

```text
$ package_mgmt align-repo --verify manifests/hyprland-0.45.toml --repo-dir repos/
Alignment (verify): manifests/hyprland-0.45.toml
  baseline: trixie/main (verified, read-only)
  requirements checked: 41 (36 cache-tier, 5 ecosystem-tier)
  result: PASS
```

On verify failure the unmet requirements are listed exactly as in search-mode failure.

## JSON output (`--format json`)

The report carries `mode` (`"search"` or `"verify"`); search-only fields
(`candidates_evaluated`, `selected_era`, `manifest_written`) are present only in search
mode, `manifest` only in verify mode.

```json
{ "mode": "search", "repo_dir": "repos/", "baseline": "trixie/main (verified)",
  "candidates_evaluated": ["0.44", "0.45", "0.46", "0.47"], "selected_era": "0.45",
  "manifest_written": "manifests/hyprland-0.45.toml", "requirements_checked": 41,
  "result": "PASS", "unmet": [] }

{ "mode": "verify", "manifest": "manifests/hyprland-0.45.toml",
  "repo_dir": "repos/", "baseline": "trixie/main (verified)",
  "requirements_checked": 41, "result": "PASS", "unmet": [] }
```

On failure: `result` `"FAIL"`, and `unmet` lists each failing requirement with stable
fields (`repo`, `module`, `operator`, `version`, `tier`, `cached_package`,
`cached_version`, `reason`, `source_ref`). The JSON shape is stable across runs.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | search: an era was found and the manifest written; verify: all requirements satisfied |
| 1 (`EXIT_ERROR`) | search: no era satisfies the criterion; verify: one or more unmet requirements — the newest era's (or manifest's) unmet requirements are named |
| 102 (`EXIT_CONFIG_ERROR`) | invalid `--format`, invalid `MANIFEST` (missing/unreadable/schema-violating), missing `--repo-dir`, missing/unreadable repo tree, malformed tags |
| 101 (`EXIT_SCHEMA_MISMATCH`) | stale/missing database (shared gate, matching the other DB-gated commands) |

A load error prints via the shared `_print_error` convention (stderr text; JSON
`{"error": "...", "exit_code": 102}` with `--format json`).

## Assertions (acceptance)

**Search mode:**
- With the real aligned era present in `repos/` and the real cache: exit 0, manifest
  written, `result: PASS` (US1 AS1, SC-001).
- With only non-aligning eras present (e.g. only the newest CMake era): exit 1, `FAIL`,
  naming the unmet dependency + version (US1 AS2, FR-003, edge case "Backports-only
  dependency").
- A companion pinned below another selected source's minimum (e.g. hypridle requiring
  `hyprland-protocols < 0.6.0`) rejects that era (FR-002, edge case "Companion drift").
- Boundary: requirement `>= V` with cached exactly `V` → satisfied (edge case "Boundary
  version").

**Verify mode (FR-013):**
- `--verify` on a manifest that aligns with the current cache → exit 0, `result: PASS`.
- `--verify` after a cache change that breaks alignment (e.g. a required version no longer
  cached) → exit 1, `FAIL`, naming the unmet requirement — never silently stale.
- `--verify` with a malformed manifest → exit 102 (schema/load error), never a partial
  result (contract: version-manifest.md).

**Both modes:**
- A repo tree whose `HEAD` does not match what the tool reads / a missing repo → load
  error 102 (US2, edge case "Corrupt/incomplete source acquisition").
- The manifest produced (search) / verified (verify) is valid per
  [version-manifest.md](version-manifest.md) and drives acquisition/build in docs/005
  (FR-006/008/012).
- No new operator-facing surface; existing CLI contracts unchanged.
