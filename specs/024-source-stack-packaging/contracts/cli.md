# CLI Contracts: Source-Stack Packaging

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md) · **Data model**: [data-model.md](../data-model.md)

**Contract policy.** This feature's CLI surface is exactly one new admin-only command:
`package_mgmt package-stack`. The exit-code/JSON contract table in
`specs/014-base-profiles/contracts/cli.md` is **extended, not diverged from**;
`tests/contract/test_cli.py` stays green throughout. Exit codes follow the shared
convention: 0 success, 1 (`EXIT_ERROR`), 101 (`EXIT_SCHEMA_MISMATCH`) stale/missing
database, 102 (`EXIT_CONFIG_ERROR`) config/argument/recipe errors. No operator-facing
surface is added — the imported `local` packages flow through the existing handoff/sync
channel (spec 021) unchanged, and the handoff is extended **additively** with a
`source/Sources` (deb-src) index for `local` plus a `deb-src` source line for `local`
in the operator's sources config (Q3/FR-008/FR-012 — config/index work, no new operator
tooling).

---

## `package_mgmt package-stack` (admin-only)

The generic packaging command (spec FR-001…FR-014). It consumes a declarative recipe and
packages + imports every component it describes into the cache's `local` component in
dependency order, then validates the completed set.

```text
package_mgmt package-stack [--recipe PATH] [--format text|json]
```

- `--recipe PATH` — recipe file (default `recipes/hyprland.toml`).
- `--format text` (default) or `--format json` (stable JSON report).

```text
# success — full set packaged and imported, whole-set check passes
$ package_mgmt package-stack --recipe recipes/hyprland.toml
  stack: hyprland
  order: hyprland-protocols → hyprwayland-scanner → … → sdbus-c++ → … → xdg-desktop-portal-hyprland
  components imported: 14/14
  Depends derived for 14 binaries (explicit deps merged; 0 unattributed libraries)
  whole-set verify: PASS (no unlinked binaries, no unverified records)
  simulated offline apt install: PASS (0 unmet dependencies)
# exit 0

# failure — a component's dependency is missing from the recipe/cache
$ package_mgmt package-stack --recipe recipes/hyprland.toml
  Error: component 'hypridle' depends on 'sdbus-c++' which is not in the recipe — add it or the import cannot resolve
# exit 1, zero records/pool changes

# failure — generated source set not rebuildable/checksum-invalid
$ package_mgmt package-stack --recipe recipes/hyprland.toml
  Error: hyprlang source package not rebuildable (dpkg-source -x failed): …
# exit 1, zero changes for that component (prior components remain imported)

# config/argument error
$ package_mgmt package-stack --recipe missing.toml
  Error: cannot read recipe missing.toml
# exit 102
```

**Assertions**:
- A valid recipe over staged components imports every component into `local`
  (`component='local'`, binary+source `verified=1`), records `binary_to_source` per
  component, lists the set in dependency order, passes whole-set `verify` + simulated
  offline install, exit 0.
- Recipe entries listed in **arbitrary order** still import in dependency order
  (dependencies before dependents).
- A component whose declared/derived dependency is absent from the recipe or unsatisfiable
  from the cache fails loudly naming it, exit 1, with zero DB/pool changes for that run's
  incomplete component and a clear message.
- Re-running over an identical recipe + staged inputs is idempotent (re-import via the
  existing `ON CONFLICT` path, no duplicate rows), exit 0.
- An unreadable/malformed recipe, or an unknown `--format`, is exit 102; a missing/stale
  database is exit 101; any packaging/import failure is exit 1.
- `--format json` emits a stable JSON report (the `PackResult` shape in
  [data-model.md](../data-model.md)) with `stack`, `components_total`,
  `components_imported`, `order`, and per-component `components`.

---

## Whole-set validation (FR-007/FR-008)

`package-stack` runs the existing integrity machinery after importing: the `verify`
semantics (no unlinked binaries, no unverified records for the set) and a simulated
offline `apt install` of the whole set against the cache index. Both must pass before the
command reports success; any failure is reported by name and the command exits 1.

The operator's **source channel** is validated as part of the same guarantee (Q3/FR-008):
the handoff must emit `dists/<release>/local/source/Sources[.gz]` (generated from the DB
`sources` rows, hash-listed + signed in the handoff `Release`), the handoff bootstrap's
sources config must carry a `deb-src` line for `local`, and a simulated offline
`apt-get source` of an imported component must resolve from that index — asserted in the
integration suite and quickstart Phase 5.
