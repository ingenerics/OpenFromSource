# CLI Contracts: Version Alignment Check

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md) · **Data model**: [data-model.md](../data-model.md)

**Contract policy.** This feature's CLI surface is exactly one new admin-only command:
`package_mgmt align-repo`. The exit-code/JSON contract table in
`specs/014-base-profiles/contracts/cli.md` is **extended, not diverged from**;
`tests/contract/test_cli.py` stays green throughout. Exit codes follow the shared
convention: 0 success, 1 (`EXIT_ERROR`), 101 (`EXIT_SCHEMA_MISMATCH`) stale/missing
database, 102 (`EXIT_CONFIG_ERROR`) config/argument/manifest/repo-dir errors. No
operator-facing surface is added.

---

## `package_mgmt align-repo` (admin-only)

The productized component of the feature (spec Q6/Q7/Q8). Two modes:

```text
package_mgmt align-repo [--verify MANIFEST] [--repo-dir DIR] [--format text|json]
```

- **Default (search) mode** — the era search: scans the acquired source repos in `DIR`
  (default `repos/` at the project root), finds the **newest satisfying era** (every
  ref's declared dependency requirements vs the cached trixie/main baseline, read-only,
  plus companion-to-companion requirements vs the selected source versions), and **writes
  the version manifest** (`manifests/hyprland-<era>.toml`, contract:
  [version-manifest.md](version-manifest.md)) on success.
- **`--verify MANIFEST` mode** — re-validates the given manifest against the current
  cache and `repos/` without searching or writing (FR-013, Q8).
- `--format text` (default) or `--format json` (stable JSON report carrying `mode`).

```text
# search — success
$ package_mgmt align-repo --repo-dir repos/
  candidates evaluated: 0.44 → 0.45 → 0.46 → 0.47 (newest fails, 0.45 selected)
  manifest written: manifests/hyprland-0.45.toml
  result: PASS
# exit 0

# search — no era satisfies
$ package_mgmt align-repo --repo-dir repos/
  newest era 0.48 — 2 unmet requirement(s)
  FAIL Hyprland -> libinput>=1.29   (cached libinput-dev 1.28.1-1+deb13u1: version_too_low)
  result: FAIL
# exit 1

# verify — PASS
$ package_mgmt align-repo --verify manifests/hyprland-0.45.toml --repo-dir repos/
  result: PASS
# exit 0

# verify — alignment broke after a cache change
$ package_mgmt align-repo --verify manifests/hyprland-0.45.toml --repo-dir repos/
  FAIL Hyprland -> libinput>=1.29   (cached libinput-dev 1.28.1-1+deb13u1: version_too_low)
  result: FAIL
# exit 1
```

**Assertions**:
- Search success exits 0 and writes a valid manifest; search failure exits 1 naming the
  newest era's unmet requirements (FR-004/FR-005).
- Verify PASS exits 0; verify FAIL exits 1 naming the unmet requirements; verify never
  writes a manifest and never searches other eras (FR-013, Q8).
- A malformed/unknown `MANIFEST`, invalid `--format`, missing/unreadable `--repo-dir`
  tree, or malformed tags → exit 102 (config/argument error) via the shared `_print_error`
  convention; a stale/missing database → exit 101.
- JSON (`--format json`) output is stable: `mode`, `repo_dir`, `baseline`,
  `requirements_checked`, `result`, `unmet[]`; search mode adds `candidates_evaluated`,
  `selected_era`, `manifest_written`; verify mode adds `manifest`.
- No other command, flag, or operator artifact changes; the existing commands' contracts
  in `specs/014-base-profiles/contracts/cli.md` are untouched.
