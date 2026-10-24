# Regression Snapshot — Alpha Release (post-022)

Baseline recorded after feature `022-apt-version-hash` (specs/022-apt-version-hash/)
on `main`. This is the regression reference for the alpha release: the full
suite must stay green under **both** documented runners.

- **Branch**: `main`
- **Command**: `.venv/bin/python -m pytest` and `.venv/bin/python -m unittest discover -s tests`
- **Result**: `pytest` → `808 passed, 59 subtests passed`; `unittest` → `Ran 808 tests ... OK`
- **Contract suite**: `pytest tests/contract -q` → `96 passed + 15 subtests`
- **Lint**: `ruff check src/ tests/` → clean
- **Failures**: 0

Prior snapshots: post-021 → 782. The apt-version-hash feature added
25 tests across unit, contract, and integration suites, plus one regression
test for the graybeard cached-virtual source repair (`test_virtual_resolution.py`).

## Snapshot — post-024 (source-stack packaging)

Baseline recorded after feature `024-source-stack-packaging`
(specs/024-source-stack-packaging/) on `main`.

- **Branch**: `main`
- **Command**: `.venv/bin/python -m pytest` and `.venv/bin/python -m unittest discover -s tests`
- **Result**: `pytest` → `914 passed, 60 subtests passed`; `unittest` → `Ran 914 tests ... OK`
- **Lint**: `ruff check src/ tests/` → clean
- **Failures**: 0

The feature added 15 tests covering the policy-clean generator output
(extended descriptions, doc files, `triggers`/`shlibs`/`conffiles`/lintian
overrides, man pages, dev-symlink dropping, maintainer defaults) and the
`admin_email` project setting. The generated artifact gate (lintian 2.122.0,
zero `E:`/`W:` over the full 13-component set) is tracked under
`specs/024-source-stack-packaging/tasks.md` T030.

## Snapshot — post-025 (alpha-release preparation)

Baseline recorded after feature `025-alpha-release-preparation`
(specs/025-alpha-release-preparation/) on `main`.

- **Branch**: `main`
- **Command**: `.venv/bin/python -m pytest` and `.venv/bin/python -m unittest discover -s tests`
- **Result**: `pytest` → `968 passed, 60 subtests passed`; `unittest` → `Ran 968 tests ... OK`
- **Contract suite**: `pytest tests/contract -q` → `118 passed + 15 subtests`
- **Lint**: `ruff check src/ tests/` → clean
- **Failures**: 0

The feature added 54 tests over the post-024 snapshot: the packaging
install contract, download-after-import regression, cross-version and
same-version supersede, config-driven align-repo baseline, keyring relocation,
the graybeard `ensure-base` warning, the consolidation sweep, and the
review-07 remediations (same-version archive collision, diff local
source channel). `pytest` reports 968 pass (60 subtests); `unittest` reports
the same 968-test OK.

## Snapshot — post-025 polish (Phase 13)

Final snapshot for feature `025-alpha-release-preparation` (Phase 13,
`specs/025-alpha-release-preparation/`) on `main`.

- **Branch**: `main`
- **Command**: `.venv/bin/python -m pytest` and `.venv/bin/python -m unittest discover -s tests`
- **Result**: `pytest` → `976 passed, 6 skipped, 60 subtests passed`; `unittest` → `Ran 976 tests ... OK`
- **Contract suite**: `pytest tests/contract -q` → `118 passed + 15 subtests`
- **Lint**: `ruff check src/ tests/` → clean
- **Failures**: 0

The polish phase added the shared fs/hash helper tests
(`tests/unit/test_fs_util.py`, F1 — 14 tests), closing the gap the
quickstart's Phase 6 referenced. The 6 skips are the `TestHyprlandRecipe`
real-stack recipe tests, which now **skip gracefully** when the pinned
Hyprland source tree (`repos/`, gitignored and populated locally via
`scripts/build-hyprland.sh` Part 1) is absent, instead of erroring. With the
tree present the full 982-test suite runs (no skips).

Any change must keep the full suite green at or above this baseline with no
regressions, under both `pytest` and `python -m unittest discover -s tests`.
