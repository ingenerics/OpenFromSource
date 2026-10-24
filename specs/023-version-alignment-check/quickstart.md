# Quickstart / Validation: Version Alignment Check

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md) · **Data model**: [data-model.md](data-model.md)
· **Contracts**: [contracts/align-repo.md](contracts/align-repo.md),
[contracts/version-manifest.md](contracts/version-manifest.md)

This is the runnable validation guide. It proves the feature end-to-end: `align-repo` (the
productized tool) determines the newest satisfying era and produces the version manifest
with success or failure, and the documented acquisition + build + functional setup
procedure (docs/005) reaches a working in-place Hyprland setup offline.

Prerequisites: the project installed (`pip install -e .`), both test runners (`pytest`
and `python -m unittest discover -s tests`), a cache at `/var/cache/package_mgmt`, and
the acquired source trees in `repos/` (from the docs/005 acquisition procedure).

## Phase 1 — unit & contract regression (the align-repo tool)

```text
$ pytest tests/unit/test_alignment.py tests/integration/test_align_repo_flow.py
$ python -m unittest discover -s tests
```

**Expected**: green. The tests assert:
- era enumeration from git tags, newest-first, with the common era label;
- meson parser: `dependency('aquamarine', version: '>=0.4.2')` → `(aquamarine, >=, 0.4.2,
  cache/ecosystem tier)`; optional (`required: false`) deps excluded;
- cmake parser: `pkg_check_modules(... libinput>=1.29 ...)`, `find_package(Name 0.3.10
  REQUIRED)`, and variable-expanded versions (`${XKBCOMMON_MINIMUM_VERSION}`);
- pkg-config→Debian mapping for the trixie set; unknown module → `unknown_module` failure;
- Debian version semantics via `debian.debian_support.Version`: boundary `>= V` with
  cached `V` satisfied; `1.28.1` vs `1.29` → too low; epoch handling (`1:1.15.2+ds-2`);
- report aggregation + exit codes, the manifest written on search success, and
  `--verify` PASS/FAIL semantics (contract: align-repo.md).

## Phase 2 — align-repo on the real repos (US1, FR-001…FR-005, SC-001/SC-004)

With the acquired repos in `repos/` (docs/005 acquisition):

```text
$ package_mgmt align-repo --repo-dir repos/
$ package_mgmt align-repo --repo-dir repos/ --format json
```

**Expected**: `result: PASS`, exit 0, and `manifests/hyprland-<era>.toml` written with
every repo's tag, commit SHA, build format and rationale. JSON carries `result: "PASS"`,
`selected_era`, and `manifest_written`.

**Negative controls** (repo sets or caches that cannot align):
- A repo set whose only eras require a backports-only/unpackaged module (like `glaze`)
  → exit 1, `FAIL`, naming the newest era's unmet dependency + version (FR-003).
- A companion pinned below another selected source's minimum (e.g. hypridle with
  `hyprland-protocols < 0.6.0`) → that era rejected, older era selected or `FAIL` (FR-002).
- A repo missing from `--repo-dir`, or a tree whose `HEAD` is unreadable → exit 102
  load error (US2).

## Phase 2b — verify mode re-validation (FR-013, Q8)

With the produced manifest and the current cache:

```text
$ package_mgmt align-repo --verify manifests/hyprland-0.52.toml --repo-dir repos/
$ package_mgmt align-repo --verify manifests/hyprland-0.52.toml --repo-dir repos/ --format json
```

**Expected**: `result: PASS`, exit 0 (manifest still aligns with the current cache).

**Re-validation control**: change the cache so a requirement the manifest pins is no
longer satisfied (e.g. remove/alter a cached version of one baseline package), then:

```text
$ package_mgmt align-repo --verify manifests/hyprland-0.52.toml --repo-dir repos/
```

**Expected**: exit 1, `FAIL`, naming the now-unmet requirement and offending ref — the
check never silently passes a stale alignment. A malformed manifest → exit 102.

## Phase 3 — acquisition integrity (US2, FR-006)

```text
$ for d in repos/*/; do git -C "$d" rev-parse HEAD; done   # each == manifest commit
$ git -C repos/Hyprland submodule status                    # submodules at recorded commits
```

**Expected**: every repo `HEAD` equals the manifest SHA produced by `align-repo`;
submodules present and pinned; no network is required to build.

## Phase 4 — offline bottom-up build (US3, FR-007/008/009, SC-002)

```text
# network disabled; the cache is the sole package/source provider
# follow the documented build order in docs/005 (data-model §7):
#   hyprland-protocols → hyprwayland-scanner → hyprgraphics → hyprutils → hyprlang
#   → aquamarine → hyprcursor → Hyprland → hypridle/hyprlock/hyprpaper/xdg-desktop-portal-hyprland
$ ./docs/005-hyprland-source-stack.md   # (the documented procedure; run each step)
```

**Expected**: 100% of the 12 components build successfully with the network disabled,
using only cached packages at their cached versions; a repeat from a clean state yields
identical artifacts (FR-009).

## Phase 5 — functional setup (US4, FR-010/011, SC-003)

On the target (same trixie machine, in place, Q1):

```text
$ hyprctl version                  # compositor responds
$ hyprctl clients                  # session is live
$ pgrep -x hyprpaper; pgrep -x hyprlock; pgrep -x hypridle
$ dbus-send --session --print-reply --dest=org.freedesktop.impl.portal.Desktop \
    /org/freedesktop/portal/desktop org.freedesktop.DBus.Properties.Get \
    string:org.freedesktop.impl.portal.Background string:version
```

**Expected**: a usable Wayland session starts with no network access; hyprpaper,
hyprlock, hypridle and the portal are present and running; the same procedure on a fresh
target yields the same result (repeatable).

## Regression

After all scenarios: `pytest` and `python -m unittest discover -s tests` green (baseline
808 + new tests), `ruff check` clean, and `tests/contract/test_cli.py` passes with the
new `align-repo` cases (existing contract cases unchanged).
