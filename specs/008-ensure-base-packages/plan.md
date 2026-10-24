# Implementation Plan: Ensure Base Packages

**Branch**: `008-ensure-base-packages` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/008-ensure-base-packages/spec.md`

## Summary

Add a single command, `package_mgmt ensure-base`, that turns the offline DVD installer image (raw `.iso` file at `<cache root>/iso`, read directly — no manual extraction) into cache metadata: the image's index (`Packages.gz`) is read once as the registration seed — every binary package it lists is registered in the existing database with `verified = 0`, exactly like `add` (the flag is only set when a file is actually verified/downloaded at a later stage; image presence never sets it). An index↔image cross-check reports mismatches as warnings (damaged-image diagnostics). Then, behaving exactly like `add`, each base package's dependencies and build-dependencies are resolved per app settings (network on the admin machine as configured), sources and `binary_to_source` links arriving through the existing resolver flow — all later metadata retrieval reads the database, never the image. No source DVD is required. New dependency: system package `python3-pycdlib` for Rock Ridge ISO reading (no root, no pip). No schema changes — everything reuses the existing tables, index parsing, provides population, and resolver.

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default)

**Database**: SQLite 3.x — schema version 1, **no changes**. All records live in the existing `packages`, `binaries`, `provides`, `sources`, `binary_to_source`, `source_build_deps` tables.

**Primary Dependencies**: python-debian (deb822, Version), sqlite3 (stdlib), **python3-pycdlib** (new system apt package — ISO9660/Rock Ridge reading)

**Existing Architecture**:
- `resolver.py`: `PackageResolver.ensure_indexes()` (fetch/load archive `Packages.gz`/`Sources.gz`), `resolve(name)` per-package DB-first resolution (upserts, superseding, virtual/provides handling, `fetch_source`-gated build-deps + source linking, best-effort error aggregation) — **reused unchanged**
- `index_loader.py`: `load_binary_index(path, release, component)` (deb822 Packages.gz → `BinaryPackage`), `parse_provides_field` — **reused**
- `initializer.py`: `initialize_caches` (cache dir creation), `populate_provides_table` (insert-only, idempotent) — **reused**
- `db_init.py`: `initialize_database` (schema ensure) — **reused**
- `config.py` model + `config_loader.py` (`_KEY_MAP`, `expand_path`) — extended with `iso_dir`
- `cli/main.py` argparse wiring; `cli/commands/config.py` text/JSON output — extended
- `verify.py`/`list.py`/`show.py`: consume records with zero changes (US5)

**What needs to change**:
1. `lib/service/iso_reader.py` (**new**): `IsoImage` (pycdlib-backed) — open, walk file list (Rock Ridge names), read file bytes, member existence; `IsoError` exception
2. `lib/service/ensure_base.py` (**new**): three-phase workflow — Phase A (local): discover release/components from `dists/`, extract each `binary-amd64/Packages.gz` to a temp file, `load_binary_index`, register binaries/packages/provides with the existing upserts (all `verified = 0`, same as `add`), walk the image once to cross-check index `Filename`s against image contents (mismatches → warnings; no flag mutations); Phase B: per-name `PackageResolver.resolve(name)` loop (behaves like `add`) with progress display (`Resolved X/Y`); Phase C: finalize — summary, temp-dir cleanup; netinst warning (index count < 1,000); typed `EnsureBaseStats` summary; console errors capped at 50 (full list to log, FR-014)
3. `cli/commands/ensure_base.py` (**new**): CLI handler — config load, image discovery (`iso_dir`), db init, workflow call, summary printing, exit codes
4. `lib/model/config.py`: +`iso_dir: str = "{base_dir}/iso"`
5. `lib/service/config_loader.py`: `_KEY_MAP` + `(("paths", "iso_dir"))`; `expand_path` list + `iso_dir`
6. `lib/service/initializer.py`: `initialize_caches` also creates the empty `iso_dir`
7. `cli/main.py`: register `ensure-base` subcommand
8. `cli/commands/config.py`: `iso_dir` in text and JSON output
9. `project.defaults.toml`: `[paths] iso_dir = "{base_dir}/iso"`
10. `docs/003-ensure-base.md` (**new**) + `roles.md` command table (+`ensure-base`, admin-only) — Constitution VIII docs-in-cycle
11. `tests/fixtures/make_test_iso.py` (**new**): synthetic ISO builder using pycdlib's write support (Rock Ridge facade) — feeds unit/integration/quickstart validation without root or real images
12. `tests/` (**new**): unit tests for `iso_reader` (synthetic image), `ensure_base` phases, netinst warning, idempotency; integration test for the full command against a synthetic image

**Performance Goals**: No new global targets. Batch resolution of ~4,200 names (DVD-1) / ~500 (netinst) completes in minutes at most; image walk of ~4,400 files is a single pass.

**Amendment**: Real-DVD validation (T039) showed the per-name loop does NOT meet this goal with `fetch.source = true` (~1.5 s/name, ~2 h projected for 4,447 names; `source = false` was ~3–4 min). Phase 9 (tasks.md) refactors resolution to a first-class batch shape: `resolve_batch()` walks the union closure once with shared `_visited`, collects per-package constraints (single version satisfying all), stages the runtime pass before the build-dep waves (once per batch), and keeps per-name commits for US4 safety; `resolve()` delegates to `resolve_batch([name])` and `add` multi-name shares the same path. Target: real-DVD `source=true` batch ≤ 15 min. No user-visible behavior change; batch ≠ parallel (single-writer SQLite). Design details and rationale in research.md §6 amendment.

**Constraints**: No schema version bump; no migration; no file copying out of the image (in-place referencing, Constitution IV); no new resolution machinery (Constitution VI reuse mandate, spec FR-008); `verified` flags follow the `add` rule — registration leaves them unset, only later-stage verification/download sets them, and this command never mutates them (FR-005); the image index is a registration seed only — the database is the metadata source of truth; command is admin-only (roles.md).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Notes |
|-----------|-------|-------|
| I. Offline-First | ✅ Compliant | Image extraction/registration is purely local; the operator side never needs network. Resolution is admin-side only and behaves like `add` (spec 008 amendment). |
| II. Dependency Alignment | ✅ Compliant | Dependencies/build-deps resolved by the existing constraint-aware resolver. |
| III. Binary-Source Consistency | ✅ Compliant | Binaries-only image is the expected artifact; sources and `binary_to_source` links come from the existing flow (no source DVD). |
| IV. Minimal Local Cache | ✅ Compliant | Nothing is copied out of the image; records reference image contents in place. |
| V. Debian Policy Compliance | ✅ Compliant | No policy-surface changes; `non-free-firmware` metadata ingested like any component (licensing vetting remains an existing concern). |
| VI. Code Quality & Type Safety | ✅ Compliant | New typed structures (`IsoImage`, `EnsureBaseStats`); no bare dicts/tuples across service boundaries. |
| VII. Database-First Resolution | ✅ Compliant | Records populated (Phase A) before any network operation (Phase B); `verified` flags follow the `add` rule (set only at later verification/download). |
| VIII. Documentation & Infrastructure | ✅ Compliant | `python3-pycdlib` tracked as a dependency; docs/003 + roles.md + contracts updated in the same cycle. |
| IX. Incremental Spec Scope | ✅ Compliant | 5 stories / 14 FRs — well under the caps; one demonstrable outcome (single command). |

**Gate**: PASS — no violations that require justification.

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] Commands are assigned to the correct persona (admin-only vs operator-only vs shared) — `ensure-base` is **admin-only** (like `add`); roles.md command table updated in this cycle
- [x] No persona-dependent behavior leaks across the offline/online boundary — the command's only networked part is the `add`-like resolution on the admin's system
- [x] Cache handoff expectations are reflected in the architecture — the raw image file lives inside the cache (`iso_dir`) and ships with the handoff
- [x] roles.md updated for the new command (same personas, no new roles)

## Project Structure

### Documentation (this feature)

```text
specs/008-ensure-base-packages/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # ISO reading / image facts / design decisions
├── data-model.md        # Entities and verified-flag semantics (no schema change)
├── quickstart.md        # Validation guide
├── contracts/           # CLI contract definitions
│   └── cli.md           # ensure-base contract + config iso_dir
└── tasks.md             # Generated per story (/speckit.tasks)
```

### Source Code (files to change)

```text
src/package_mgmt/
├── lib/
│   ├── model/
│   │   └── config.py               # +iso_dir
│   └── service/
│       ├── iso_reader.py           # NEW: IsoImage (pycdlib), IsoError
│       ├── ensure_base.py          # NEW: 3-phase workflow, EnsureBaseStats
│       ├── config_loader.py        # +iso_dir key/expansion
│       └── initializer.py          # init creates iso_dir
├── cli/
│   ├── main.py                     # +ensure-base subcommand
│   └── commands/
│       ├── ensure_base.py          # NEW: CLI handler
│       └── config.py               # +iso_dir output (text + JSON)
project.defaults.toml                # +[paths] iso_dir
docs/
├── 003-ensure-base.md              # NEW: howto
└── (roles.md update via .specify/memory/)
tests/
├── fixtures/
│   └── make_test_iso.py            # NEW: pycdlib synthetic-image builder
├── unit/
│   ├── test_iso_reader.py          # NEW
│   └── test_ensure_base.py         # NEW
└── integration/
    └── test_ensure_base_flow.py    # NEW
```

**Structure Decision**: Follows the existing single-package layout (`cli/commands/*` handlers, `lib/service/*` workflows, `lib/model/*` config) — the new workflow is a service like `resolver.py`, not a CLI-only feature.

## Complexity Tracking

> No constitution violations — this section is intentionally left blank.
