# Implementation Plan: Local Patch Lane

**Branch**: `021-local-patch-lane` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/021-local-patch-lane/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Feature 021 makes the "curate and optionally modify" story concrete (case 4 of
`review/05_alpha_final_kimi_k3/high_value_alternative_use_cases.md`, now marked
IMPLEMENTED). A new admin command `import-deb <file> [--source <dir>]` registers a
locally built or patched `.deb` into a new pseudo-component `local`, which then flows
through the existing `handoff`/`sync` channel exactly like `main` — operators install
and upgrade patched packages with ordinary apt, never `dpkg -i`.

Two design centers:

1. **Import** (`import-deb`, admin-only). The `.deb` is parsed with python-debian
   (`debian.debfile.DebFile`, already a project dependency). The declared `Depends`/
   `Pre-Depends` are re-resolved against the cache: every declared dependency must be
   satisfiable from the cache, else the import fails atomically (no partial import,
   FR-002/003/008). On success the binary is registered in the existing `binaries`
   table with `component = 'local'` (release = default release), the payload is copied
   to `pool/local/<prefix>/<pkg>/<pkg>_<version>_<arch>.deb` (identical layout to every
   other component), its SHA256 computed, and the record marked `verified`. The optional
   `--source <dir>` registers the rebuilt source — a directory holding the complete
   source set (`.dsc` plus every file it references, each verified against the `.dsc`'s
   checksums, since a bare `.dsc` alone cannot be `dpkg-source -x`'d) — under
   `component = 'local'` and links
   it via `binary_to_source` (FR-005, provenance). There is **no** component selector
   (FR-001).

2. **Distribution**. `local` has no archive index, so two existing pieces must learn to
   serve it:
   - The resolver's merged `RepositoryIndex` (`resolver.ensure_indexes()`) injects
     current+verified `local` binaries and sources from the DB, and
     `_COMPONENT_PRIORITY` (`repository.py:8-18`) gains `"local"` as the **highest**
     priority entry — so implicit resolution prefers a `local` package over the
     same-named package in any upstream component (FR-006, the existing
     version-then-component tie-break, unchanged semantics).
   - `handoff`/`diff` (`_component_bases`, `handoff.py`) add the `local` component to
     the served base set when local records exist, build its `Packages.gz` from the DB
     records (no archive index to confirm against), and emit `local` in the per-release
     `deb` line (FR-007).

**Schema freeze (spec 010/011).** No tables, columns, or migrations. `local` is a value
of the existing `component` column; `binary_to_source` already links imported binary↔
source; the existing `UNIQUE(package_name, version, architecture, release, component)`
already permits a local row alongside the upstream row.

**No new runtime dependencies.** python-debian is already used (`debian.deb822`,
`debian.debian_support.Version`).

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default); the real `package_mgmt`
project at repo root. Operator-side scripts shipped in `handoff.py`'s embedded bash
(`bootstrap.sh`/`apply.sh`) — `deb` line handling is touched by this feature.

**Database**: SQLite 3.x — frozen spec 010/011 schema (`packages`, `binaries`,
`sources`, `binary_to_source`, `source_build_deps`, `provides`, `virtual_resolutions`,
`systems`). No schema change. `component` column (`database.py:16,41`) already exists in
`binaries`/`sources`; `binary_to_source` (`database.py:31-36`) already links binary↔
source; `verified` (`database.py:28`) is already per-record.

**Primary Dependencies**: python-debian (`debian.debfile.DebFile` for `.deb` control,
`debian.deb822` for source `dsc`/control parsing), stdlib (`sqlite3`, `os`, `hashlib`,
`shutil`). No new runtime dependencies.

**Storage**: `{base}/binary/pool/<component>/<prefix>/<pkg>/<file>` — existing layout
verified: `pool/main/libn/libnotify/…`. Imported payloads land in `pool/local/…` with the
identical layout (spec FR-004 + Assumption "Identical layout"). Source pool mirrors the
same convention. No new storage artifacts.

**Testing**: pytest + `python -m unittest discover -s tests` (both runners mandatory,
Constitution). Baseline **730 passed** (578 unit / 86 contract / 66 integration).
New/changed:
- `tests/unit/test_import_deb.py` (new): `.deb` control parse, dependency re-resolution,
  atomic-failure, idempotent re-import, `--source` linkage, priority ordering.
- `tests/unit/test_repository.py` (extend): `_component_order("local")` top priority;
  `_best_entry` prefers local at equal version.
- `tests/integration/test_import_deb_flow.py` (new): import → handoff → apply → install
  end-to-end; diff carries local; `local` present in the `deb` line.
- `tests/contract/test_cli.py` (extend): `import-deb` CLI contract (help, errors,
  exit codes, `--source`).

**Target Platform**: Debian Trixie LTS amd64 / x86_64.

**Project Type**: CLI tool for offline Debian LTS package management — admin builds cache
on networked system, operator consumes on offline system. `import-deb` is admin-side; the
operator consumes via ordinary apt (the tool is not installed on the bare system).

**Performance Goals**: import is a per-file O(1) op (parse control + copy + SHA256).
Dependency re-resolution reuses the already-loaded repository index. Resolution goal
(10k index / 5s) untouched; no hot-path change.

**Constraints**:
- **Schema freeze** (spec 010/011): no new tables, columns, or migrations.
- **No new network calls**: import never fetches; the payload is exactly the supplied
  `.deb` (spec Assumption "No archive fetch"). `download` must not attempt to fetch
  `local` records (they are `verified = 1` at import and out of the pending set
  `downloader.py:261-264`).
- **Only `local`**: the command exposes no component selector (FR-001).
- **Atomicity**: FR-003/FR-008 — a failed import leaves zero DB and pool changes.
- **Layout parity**: `local` uses exactly the same pool/index/handoff/diff layout as
  every other component (FR-004, spec Assumption).
- **Behavioral preservation**: handoff/diff multi-release and multi-component behavior is
  unchanged; upstream components are unaffected by the `local` entry in the priority map.
- **TDD**: tests written first; both runners + contract suite green at every phase.

**Scale/Scope**: import is per-file; handoff/diff gain at most one extra component
index. Component size is tiny (locally built packages are few).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / Gate | Verdict | Basis |
|------------------|---------|-------|
| I. Offline-First Availability | ✅ PASS | No new network call sites; imported payload ships through the same channel and installs with zero network access. |
| II. Dependency Alignment | ✅ PASS | Declared deps are re-resolved against the cache; an unsatisfiable import fails (FR-003) — the closure stays apt-installable. |
| III. Binary-Source Consistency | ✅ PASS | Optional `--source` links binary↔source via `binary_to_source` (FR-005); the local payload is itself the deterministic artifact. |
| IV. Minimal Local Cache | ✅ PASS | Only the imported payload is stored; nothing is fetched from an archive. |
| V. Debian Policy Compliance | ✅ PASS | The `.deb` must be a valid Debian package; import does not build (lintian applies to builds, not imports). |
| VI. Code Quality & Type Safety | ✅ PASS | Reuses existing typed models (`BinaryPackage`, dependency parse/serialize); `DebFile` control parsing is typed. |
| VII. Database-First Resolution | ✅ PASS | Binary registered before payload copy; `verified` set only after the payload is confirmed (FR-002/004). |
| VIII. Documentation & Infrastructure Traceability | ✅ PASS | `roles.md` gains `import-deb`; the case is marked IMPLEMENTED; tutorial/README reference the local lane in the same cycle. |
| IX. Incremental Spec Scope | ✅ PASS | 4 user stories, 10 FRs — under the 10/20 decomposition limit. |
| Schema freeze (010/011) | ✅ PASS | No `database.py` changes. |
| Both test runners green | ✅ PASS (gate) | Baseline 730/730 held at every phase; TDD. |
| Contract suite green | ✅ PASS (gate) | `tests/contract/test_cli.py` extended, stays green. |

**Gates: no violations. Complexity Tracking table not required.**

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] Commands are assigned to the correct persona (admin-only vs operator-only vs shared)
- [x] No persona-dependent behavior leaks across the offline/online boundary
- [x] Cache handoff expectations are reflected in the architecture
- [x] If this feature introduces new personas or changes existing roles, [roles.md](../memory/roles.md) has been updated accordingly

`import-deb` is **admin-only** (runs on the networked build box next to `add` /
`ensure-base`). The operator only ever consumes the result through apt (handoff `deb`
line + per-component `Packages.gz`); no operator-side tooling changes. `roles.md` gains
the `import-deb` row (Constitution VIII).

## Project Structure

### Documentation (this feature)

```text
specs/021-local-patch-lane/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/package_mgmt/
├── cli/
│   ├── main.py                            # MODIFY: register `import-deb` subcommand
│   └── commands/
│       └── import_deb.py                  # NEW: CLI handler (argparse, error mapping, exit codes)
├── lib/
│   ├── model/
│   │   └── repository.py                  # MODIFY: _COMPONENT_PRIORITY["local"] = -1 (top)
│   └── service/
│       ├── import_local.py                # NEW: import core (parse .deb, re-resolve deps,
│       │                                  #       register DB rows, copy payload, checksum)
│       ├── resolver.py                    # MODIFY: ensure_indexes() injects local
│       │                                  #       binaries/sources from the DB
│       └── handoff.py                     # MODIFY: serve `local` in handoff + diff;
│                                          #       _component_bases / base_set; local
│                                          #       Packages.gz from DB; `deb` line

tests/
├── unit/
│   ├── test_import_deb.py                 # NEW
│   └── test_repository.py                 # MODIFY: local priority ordering
├── integration/
│   └── test_import_deb_flow.py            # NEW: import → handoff → apply → install
└── contract/
    └── test_cli.py                        # MODIFY: import-deb CLI contract
```

**Structure Decision**: the existing single-package layout is kept. `import-deb` gets its
own command handler (`cli/commands/import_deb.py`) plus a small service module
(`lib/service/import_local.py`) mirroring the `add`/`ensure_base` split. Resolver and
handoff changes are surgical edits to existing functions, not new modules, because the
`local` component must behave identically to upstream components everywhere downstream.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No Constitution violations; the Complexity Tracking table is not required.
