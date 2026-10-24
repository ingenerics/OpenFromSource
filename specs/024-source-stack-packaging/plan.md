# Implementation Plan: Source-Stack Packaging

**Branch**: `024-source-stack-packaging` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/024-source-stack-packaging/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Feature 024 packages the source-built stack (docs/005) into the cache's `local`
component so the Operator installs it with ordinary `apt`, offline, no `dpkg -i`,
with every binary linked to an imported, verified, rebuildable source. The 13
build components of `docs/005` — the 12 hyprwm repositories plus the source-built
D-Bus binding `sdbus-c++` (2.0.0) — are turned, generically, into a Debian binary
package (`.deb`) with an **auto-derived `Depends`** and a matching rebuildable
Debian source package (`.dsc` + files), imported into `local` and linked via
`binary_to_source`, and validated whole-set (spec FR-001…FR-014, SC-001…SC-005).

Five settled scope decisions drive the design:

1. **Generic tool + declarative recipe (the automation-vs-scripting split,
   Q1/Q2).** A new admin command `package-stack` consumes a declarative recipe
   (`recipes/<stack>.toml`) and automates everything that is identical for any
   source-built stack: recipe parsing, binary-package generation from the staged
   install directory, automatic `Depends` derivation (`dpkg-shlibdeps`), merging
   explicit recipe dependencies, deterministic source-package generation
   (`dpkg-source` from a generated minimal `debian/` wrapper), dependency-order
   processing, import + linkage (reusing spec 021's `import_local_deb`), and
   whole-set verification. The tool **contains no stack-specific knowledge** and
   **never builds components** — the per-stack knowledge is the recipe file and
   the staged build (FR-011).
2. **Import reuses the spec 021 machinery, unchanged (FR-005).** Each generated
   `.deb` + its generated `--source` directory are handed to the existing
   `import_local_deb(conn, config, deb_path, source_dir=...)` — the same
   atomic, database-first, checksum-verified, idempotent path `import-deb` uses.
   No new import code, no schema change (`component='local'` already exists from
   spec 021).
3. **Dependency ordering is the satisfiability guarantee (FR-006).** The recipe
   lists components in arbitrary order; `package-stack` topologically orders them
   (dependencies before dependents) so each import's `_verify_dependencies`
   re-resolution resolves against the frozen trixie tree **plus** the
   already-imported `local` set. The source-built dependency (`sdbus-c++`) is
   ordered before its dependents exactly as in the docs/005 build order.
4. **Determinism is a packaging requirement (FR-009).** Artifacts are built with
   `SOURCE_DATE_EPOCH` fixed, deterministic tar/control ordering, and
   `dpkg-deb --root-owner-group` so two runs over identical inputs produce
   byte-identical `.deb`/`.dsc`; re-packaging the same upstream version bumps the
   Debian revision (FR-010).
5. **The operator's source channel is extended additively (Q3).** `apt-get
   source` (FR-008/US4) requires a deb-src index, and today the handoff emits
   only `binary-amd64/Packages[.gz]` (docs/004-version-drift-trap.md:252 notes
   the cache exposes no deb-src line). The handoff gains a per-(release,
   component) `source/Sources` index for `local` — a file artifact generated from
   the DB `sources` rows (schema-freeze holds), hash-listed and signed in the
   same `Release`/`Release.gpg` as `Packages` — and the handoff bootstrap script
   writes the matching `deb-src` line for `local` into the operator's
   `/etc/apt/sources.list.d/openfromsource-cache.list`. Binary-package flow stays
   unchanged (FR-012); no new operator tooling.

**No new runtime dependencies and no network in product code.** Packaging uses
the standard Debian toolset already on the admin/packaging host (`dpkg-deb`,
`dpkg-shlibdeps`, `dpkg-source` — all present, dpkg 1.22.22) invoked as
subprocesses; parsing reuses `python-debian`. The command runs fully offline on
already-acquired (`repos/`) and already-staged inputs.

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default); the `package_mgmt`
project at repo root. New code lives under the existing `src/package_mgmt/` package,
mirroring the established `cli/commands/` + `lib/model/` + `lib/service/` layout.

**Database**: SQLite 3.x — frozen spec 010/011 schema, **no change** (spec 021 already
introduced `component = 'local'` as a value). All writes go through the existing
`upsert_binary` / `upsert_source` / `link_binary_to_source` / `upsert_package` helpers
(`lib/service/resolver.py`) and the `verified`-flag lifecycle inside
`import_local_deb` (`lib/service/import_local.py`): row registered before payload copy,
`verified=1` only after copy + SHA256, `ON CONFLICT` idempotence, rollback on any failure.

**Primary Dependencies**: python-debian (`debian.debfile.DebFile`, `debian.deb822.Dsc` —
already used by `import_local.py`). The standard Debian packaging toolset invoked as
subprocesses on the packaging host: `dpkg-deb` (1.22.22 present), `dpkg-shlibdeps`,
`dpkg-source`, `fakeroot`. No new Python runtime dependency.

**Storage**:
- `recipes/<stack>.toml` — the git-tracked, declarative per-stack recipe (FR-011,
  "Key Entities"); `recipes/hyprland.toml` is the shipped Hyprland recipe.
- Per-component **staged install directory** on the packaging host (the DESTDIR-style
  root that becomes the `.deb` data root; produced by the build script's staging mode).
- A scratch work directory per run (e.g. under `{tmp}` or `{cache}/staging`) holding the
  generated `.deb` and generated source-package directory before import; the import
  copies payloads into the existing `pool/local/…` (binary) and `source/pool/local/…`
  (source) layouts (`local_pool_rel_path`, `lib/service/import_local.py`).
- The handoff gains a per-(release, component) **`source/Sources` index for `local`**
  (Q3): `dists/<release>/<component>/source/Sources[.gz]`, generated from the DB
  `sources` rows (verified, `component='local'`), hash-listed + GPG-signed in the same
  `Release`/`Release.gpg` as `Packages` (`lib/service/handoff.py` hash entries), and a
  `deb-src` line for `local` in the bootstrap script's
  `/etc/apt/sources.list.d/openfromsource-cache.list` (`_write_bootstrap_script`).
- `docs/005-hyprland-source-stack.md` — gains the packaging path (Part 5) per FR-013.
- `scripts/build-hyprland.sh` — gains `--stage DIR` (FR-011), installing each component
  into `DIR/<component>` instead of `/usr`.

**Testing**: pytest **and** `python -m unittest discover -s tests` (both runners,
Constitution). New/changed:
- `tests/unit/test_packaging.py` (NEW): recipe load/validate, topological ordering
  (order-independent recipe input), control generation, `Depends` merge
  (derived + explicit + un-attributable-reporting), determinism of `.deb` and `.dsc`
  (two runs byte-identical), version/revision bump semantics.
- `tests/contract/test_cli.py` (EXTEND): `package-stack` args, exit codes (0/1/101/102),
  text and JSON output shapes.
- `tests/unit/test_srcindex.py` (NEW): `source/Sources` index generation from DB
  `sources` rows (correct `deb-src` paragraphs, `Files:`/checksums matching the pool,
  gzip + plain outputs, determinism, and that the generated index is hash-listed in the
  handoff `Release`), plus the bootstrap script emitting the `deb-src` line for `local`.
- `tests/integration/test_package_stack_flow.py` (NEW): against a fixture cache (existing
  `tests/fixtures/make_test_iso.py` archive + `make_test_deb.py` deb/dsc builders +
  `make_fixture_repos.py`-style small staged components), build two small components via
  the real `debgen`/`srcgen` paths, import them through `package-stack` in
  order-independent recipe order, and confirm: ordering, `binary_to_source` linkage,
  `verified`, whole-set `verify` clean, and handoff/apt offline install of the set — plus
  that `apt-get source` of an imported component resolves offline from the handoff's
  `local/source/Sources` (FR-008/Q3).

**Target Platform**: Debian Trixie LTS amd64 / x86_64. `package-stack` runs on the
Administrator's packaging host (which has the stack installed and the cache's build deps,
so library attribution works); the Operator consumes the imported `local` packages through
the existing handoff/sync/apt channel unchanged (FR-012).

**Project Type**: CLI tool for offline Debian LTS package management — admin builds the
cache and (this feature) packages the source-built stack into it; operator consumes the
offline result.

**Performance Goals**: `package-stack` over the 14-component Hyprland recipe completes in
well under a minute (per component: one `dpkg-shlibdeps` over a handful of binaries, one
`dpkg-deb`, one `dpkg-source`); dependency ordering is trivial (≤14 nodes).

**Constraints**:
- **Schema freeze** (spec 010/011): no new tables, columns, or migrations; reuse
  `component = 'local'` and `binary_to_source` (Q5 posture carries from spec 021/023).
- **No new runtime dependencies** and **no network call sites** in product code; every
  input (pinned `repos/`, staged dirs, cache) is local at packaging time.
- **Import parity**: every generated `.deb`/source set must satisfy the existing
  `import_local_deb` contract (parsable control, valid Debian version, complete
  checksum-valid source set) — the generated artifacts are validated by the same code
  that validates hand-built ones (FR-004/005).
- **Determinism gate (FR-009)**: two runs over identical inputs produce identical
  `.deb`/`.dsc` (fixed `SOURCE_DATE_EPOCH`, fixed tar member mtimes/order, sorted control,
  `dpkg-deb --root-owner-group`).
- **Version scheme (FR-010)**: `<upstream>-1` default; a re-package of the same upstream
  version bumps the Debian revision; re-importing an identical pair stays idempotent.
- **Loud + atomic (FR-014)**: any packaging or import failure names the component and the
  problem and leaves zero partial records/pool files (import_local's rollback + the
  command's own scratch cleanup).
- **Schema freeze (Q3)**: the operator's source channel is a **file-artifact** extension —
  `source/Sources` for `local` is generated from existing DB `sources` rows and added to
  the signed `Release`; no tables/columns/migrations are touched.
- **TDD**: tests written first; both runners + contract suite green at every phase.

**Scale/Scope**: one new CLI command (`package-stack`), one model module
(`lib/model/packaging.py`), three service modules (`lib/service/debgen.py`,
`lib/service/srcgen.py`, `lib/service/package_stack.py`), a handoff extension for the
`source/Sources` index of `local` (`lib/service/srcindex.py` + `handoff.py`/bootstrap
edits), one recipe (`recipes/hyprland.toml`), a `--stage` mode for
`scripts/build-hyprland.sh`, `docs/005` + `roles.md` updates, three contract docs,
three test-file changes. 13 components, one binary + one source package each.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / Gate | Verdict | Basis |
|------------------|---------|-------|
| I. Offline-First Availability | ✅ PASS | `package-stack` runs fully offline on local inputs (pinned `repos/`, staged dirs, cache); the operator consumes through the existing handoff/sync channel with no network and no `dpkg -i` (FR-012) and retrieves sources offline via the handoff's new `source/Sources` index for `local` (FR-008/Q3). |
| II. Dependency Alignment | ✅ PASS | Every imported binary's `Depends` is re-resolved against the cache (frozen trixie + already-imported `local`) by `import_local_deb`'s existing `_verify_dependencies`; dependency ordering makes each import satisfiable at import time (FR-006). |
| III. Binary-Source Consistency | ✅ PASS | Every imported binary is linked via `binary_to_source` to an imported, verified, rebuildable source (`.dsc` + files, checksum-verified); rebuild-reproducibility is demonstrated (US4/FR-008, SC-004) and the operator can `apt-get source` the linked source offline via the `local` `source/Sources` index (Q3). |
| IV. Minimal Local Cache | ✅ PASS | Only the 14 stack components are packaged/imported; upstream trixie packages are never re-packaged; the four niche hyprwm tools stay out of scope (Scope assumption). |
| V. Debian Policy Compliance | ✅ PASS | Artifacts are produced with standard Debian tooling (`dpkg-deb`/`dpkg-source`/`dpkg-shlibdeps`) and a complete control record (FR-002); generated artifacts pass lintian; the stack is free/DFSG software. |
| VI. Code Quality & Type Safety | ✅ PASS | Frozen dataclasses for the recipe/report models (`StackRecipe`, `ComponentRecipe`, `PackResult`); `TypedDict` only at the JSON output boundary; typed subprocess wrapper signatures. |
| VII. Database-First Resolution | ✅ PASS | Import reuses `import_local_deb` verbatim: rows registered before the payload copy, `verified=1` only after copy + SHA256 — the database-first discipline is preserved, not bypassed. |
| VIII. Documentation & Infrastructure Traceability | ✅ PASS | `docs/005` gains the packaging path, `roles.md` gains the command, the recipe is git-tracked, and the staged build + toolchain stay recorded (FR-013, Constitution VIII). |
| IX. Incremental Spec Scope | ✅ PASS | 4 user stories, 14 FRs — under the 10/20 decomposition limit. |
| Schema freeze (010/011) | ✅ PASS | No `database.py` changes; `component = 'local'` and `binary_to_source` already exist (spec 021). The Q3 source channel is a file-artifact extension (`source/Sources` generated from DB `sources` rows, hash-listed + signed in the handoff `Release`) — no tables/columns/migrations. |
| No new runtime deps / no network | ✅ PASS (gate) | Subprocess calls to the host Debian toolset; parsing reuses python-debian. |
| Both test runners green | ✅ PASS (gate) | Baseline 851; TDD at every phase. |
| Contract suite green | ✅ PASS (gate) | `tests/contract/test_cli.py` extended for `package-stack`; existing contracts unchanged. |

**Gates: no violations. Complexity Tracking table not required.**

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] Commands are assigned to the correct persona (admin-only vs operator-only vs shared)
- [x] No persona-dependent behavior leaks across the offline/online boundary
- [x] Cache handoff expectations are reflected in the architecture
- [x] If this feature introduces new personas or changes existing roles, [roles.md](../memory/roles.md) has been updated accordingly

`package-stack` is a **new admin-only command** (runs on the Administrator's networked/
packaging host against the staged build + `repos/` + the cache and imports into `local`);
the Operator's surface is unchanged — the imported `local` packages flow through the
existing handoff/sync/apt channel (FR-012).
[roles.md](../memory/roles.md) gains the `package-stack` row in the Administrator command
table and an entry in the Impact-on-Design table. No new personas, no operator-side
tooling.

## Project Structure

### Documentation (this feature)

```text
specs/024-source-stack-packaging/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   ├── cli.md                # CLI contract for this feature (package-stack command)
│   ├── recipe.md             # declarative recipe file-format contract
│   └── artifacts.md          # generated .deb/.dsc artifact contract (Depends, determinism)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
recipes/
└── hyprland.toml                # NEW: declarative recipe for the Hyprland stack (13 components)

src/package_mgmt/
├── cli/
│   ├── main.py                  # MODIFY: register `package-stack` subcommand
│   └── commands/
│       └── package_stack.py     # NEW: handle_package_stack (admin-only)
└── lib/
    ├── model/
    │   └── packaging.py         # NEW: StackRecipe, ComponentRecipe, PackResult,
    │                            #      ComponentResult (frozen dataclasses)
    └── service/
        ├── package_stack.py     # NEW: load/validate recipe, topological ordering,
        │                        #      orchestrate debgen+srcgen+import per component,
        │                        #      whole-set verify
        ├── debgen.py            # NEW: control generation + dpkg-shlibdeps Depends
        │                        #      derivation + dpkg-deb (deterministic .deb)
        ├── srcgen.py            # NEW: generated minimal debian/ wrapper + dpkg-source
        │                        #      (deterministic .dsc + files)
        └── srcindex.py          # NEW: generate the handoff `source/Sources` index for
                                 #      `local` from the DB `sources` rows (Q3/FR-008)
        # lib/service/handoff.py # MODIFY: hash-list + sign the local `source/Sources`
        #                        #         index in Release/Release.gpg; bootstrap script
        #                        #         emits a `deb-src` line for `local` (Q3/FR-012)

scripts/
└── build-hyprland.sh            # MODIFY: add --stage DIR (install each component to
                                 #         DIR/<component> instead of /usr) — FR-011

docs/
└── 005-hyprland-source-stack.md # MODIFY: add the packaging path (Part 5) — FR-013

tests/
├── unit/
│   └── test_packaging.py        # NEW: recipe, ordering, control, Depends merge,
│                                #      determinism, revision bump
├── contract/
│   └── test_cli.py              # MODIFY: package-stack args, exit codes, JSON shape
└── integration/
    └── test_package_stack_flow.py # NEW: end-to-end against fixture cache + staged
                                     components + fixture source trees
```

**Structure Decision**: `package-stack` fits the existing single-package CLI layout — a
new admin command (`cli/commands/package_stack.py`) backed by focused service modules
(`debgen.py` for binary generation + `Depends` derivation, `srcgen.py` for deterministic
source generation, `package_stack.py` for recipe/ordering/orchestration) and a
frozen-dataclass model module. The recipe (`recipes/hyprland.toml`) is a new tracked,
top-level artifact because it is the durable per-stack data the generic tool consumes
(FR-011); the staged build lives as a mode of the existing build script (FR-011).

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No Constitution violations; the Complexity Tracking table is not required.
