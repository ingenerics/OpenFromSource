# Data Model: Alpha Cut-off

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

**Schema freeze (spec 010/011).** No database schema changes, no migrations, no new columns or tables. This
feature's data concerns are (a) the *interpretation* of existing rows by the resolver's alternative-group probes
(N-3), and (b) non-database artifacts — the packaging metadata and the profile-documentation contract (N-4, N-2).

---

## 1. Alternative-group probe state (N-3)

No new entities. The constraint-aware probes read existing rows; the only change is that a `current` row's
*version* is now evaluated against the member's constraint instead of merely its existence being checked.

### `binaries` (existing)

Fields read by the probe: `package_name`, `version`, `status`. A member is "constraint-wise satisfying" when a
row with `package_name = <member>` and `status = 'current'` exists **and**
`satisfies_constraint(version, <member's constraint>)`.

### `provides` (existing, runtime probe only)

The virtual-provider leg: `provides p JOIN binaries b ON b.package_name = p.package_name` where
`p.virtual_name = <member>` and `b.status = 'current'`. **Semantics unchanged (over-inclusive, FR-003):** any
`current` provider of the virtual counts as satisfying the group — this leg is deliberately *not* constraint-
checked, preserving the documented provider over-inclusiveness (F-13). A future narrowing is out of scope.

### `virtual_resolutions` (existing, build-dep twin only)

The build-dep probe (mirroring the link block at `resolver.py:716-744`) reads the recorded provider via
`virtual_resolutions vr JOIN binaries b ON b.id = vr.resolved_binary_id` and applies
`satisfies_constraint(b.version, <member's constraint>)` — the constraint-aware twin of the runtime leg.

### Validation rules (from FR-001…FR-004)

- FR-001: a member whose `current` row fails its own version constraint MUST NOT satisfy the group.
- FR-002: when no member constraint-wise satisfies, alternatives MUST be tried and a resolving alternative MUST
  enter the closure with the failed-primary error retracted.
- FR-003: a constraint-wise-satisfying member (or an over-inclusive virtual provider) satisfies the group; the
  failed-primary error is retracted; no regression.
- FR-004: the build-dep probe mirrors the runtime probe; a constraint-satisfying alternative is linked with no
  "Build tool not resolved" error.

---

## 2. Distribution artifact / trust anchor (N-4)

Non-DB artifact. The archive-signing keyring is committed at `src/package_mgmt/data/debian-archive-keyring.gpg`
(55 918 bytes). The packaging declaration (`[tool.setuptools.package-data]` → `package_mgmt = ["data/*.gpg"]`)
determines whether the file ships in a built wheel/sdist.

- **Runtime path**: `lib/service/index_loader.py:22-24` resolves `os.path.join(os.path.dirname(__file__), "..",
  "..", "data", "debian-archive-keyring.gpg")` → installed `package_mgmt/data/debian-archive-keyring.gpg`.
  Correct for wheels (installed as normal files); no `importlib.resources` migration.
- **Validation (FR-005)**: a built wheel/sdist MUST contain the keyring at that exact path.
- **Install modes (FR-006)**: editable install (dev flow, reads the source tree) and standard `pip install .`
  (reads the built artifact) both work; the docs state this.

---

## 3. Profile-documentation contract (N-2)

Non-DB, documentation entities. The three profile TOMLs under `docs/profiles/` are plain config data shipped as
presets (spec 014 FR-006). The contract text they carry:

- **graybeard.toml**: stand-alone, fresh-cache-only profile; run from the get-go, not reached by reusing a cache
  grown under a shallower profile; consistent with `ensure-base`'s fresh-only refusal; FR-014 retention note kept.
- **regular_user.toml**: grow-deeper direction ends at `developer` (graybeard removed from the chain).
- **developer.toml**: grow-deeper direction `regular_user → developer (depth 1) → developer (depth N)` (unchanged;
  wording confirmed).
- Spec 014's historical grow-deeper chain wording is superseded by this feature (not retro-edited).

`tests/unit/test_config.py` (`TestProfileFilesAreData`) asserts the contract text so the docs cannot drift back.

## 4. `init` guidance (N-1)

Documentation-only, no data artifact: `docs/001` step 4 states that `init` requires connectivity and that an
offline/partial `init` (0 binaries cached) is not a usable cache. No behavioral or schema impact.
