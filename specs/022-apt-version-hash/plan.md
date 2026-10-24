# Implementation Plan: Apt VersionHash Fidelity

**Branch**: `022-apt-version-hash` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/022-apt-version-hash/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Feature 022 ensures packages served by the local cache (handoff/diff repos) are only
reported as upgradable by apt when a genuinely newer version exists. The mechanism is
**apt's VersionHash**: apt keeps a repo version and an installed dpkg-status version as
separate objects when their VersionHashes differ — even at the same version string — and
then reports the identical version as upgradable ("X upgradable from X").

The corrective work (already implemented and verified against the real cache) makes the
generated `Packages` stanzas reproduce apt's VersionHash inputs verbatim from the actual
package control, and records the VersionHash contract in-project so it is referenceable
and regression-safe (the stated intent of the feature).

Two design centers:

1. **VersionHash parity in emitted stanzas**. The handoff/diff `Packages` stanzas
   (`_paragraph`, `handoff.py`) carry apt's VersionHash inputs — `Installed-Size`,
   `Depends`, `Pre-Depends`, `Conflicts`, `Breaks`, `Replaces` — verbatim from the
   package's actual control record: the cached archive index for upstream components
   (`load_binary_index`, `index_loader.py`), and the shipped `.deb` control for the
   `local` component (`_read_deb_control` + `_local_binary_entry`, `handoff.py`).
   `Depends` is emitted **only** from the original control field text (`depends_field`);
   the resolver's merged `Depends`+`Pre-Depends` view (`depends_raw`) is never emitted,
   so a package whose control has no `Depends` (only a `Pre-Depends`) does not gain an
   invented `Depends` line — the root cause of the regression (FR-001/002/003).

2. **The VersionHash contract documented in-project**. The exact algorithm — field set
   and order, normalisation rules (whitespace and `=` stripped, `0:` epochs removed,
   case folded), the DJB-2-style hash, and the merge semantics — is recorded in
   `research.md`/this plan so future work does not need external lookup and cannot
   silently regress parity (FR-007, SC-005).

**Schema freeze (spec 010/011).** No tables, columns, or migrations. The `BinaryPackage`
model gains fields (`depends_field`, `installed_size`, `conflicts_raw`, `breaks_raw`,
`replaces_raw`) that are in-memory metadata only — no DB change.

**No new runtime dependencies.** Reading the `local` `.deb` control reuses
`debian.debfile` (python-debian, already a project dependency).

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default); the real `package_mgmt`
project at repo root. Operator-side behavior is ordinary apt (`apt update` /
`apt list --upgradable`) consuming the handoff's `Packages` metadata.

**Database**: SQLite 3.x — frozen spec 010/011 schema. No schema change. The corrective
work is entirely in the metadata emission path; the DB stores the same rows it always
has.

**Primary Dependencies**: python-debian (`debian.debfile.DebFile` to read a `local`
`.deb`'s control file, `debian.deb822` for index parsing). No new runtime dependencies.

**Storage**: `{base}/binary/dists/…/Packages.gz` (handoff) and `{base}/binary/diff/…`
(diff) — the emitted stanzas. `{base}/binary/pool/…` holds the `.deb` payloads whose
control files are the parity reference for `local` records. No new storage artifacts.

**Testing**: pytest + `python -m unittest discover -s tests` (both runners mandatory,
Constitution). New/changed:
- `tests/unit/test_handoff.py` (extend): regression tests asserting a pre-depends-only
  package emits `Pre-Depends` but **no** `Depends` in the handoff stanza — for archive
  (`test_handoff_no_invented_depends_for_pre_depends_only_archive_package`) and `local`
  (`test_handoff_local_pre_depends_only_no_invented_depends`) packages.
- `tests/unit/test_index_loader.py` (extend): `load_binary_index` captures
  `depends_field`/`installed_size`/`conflicts_raw`/`breaks_raw`/`replaces_raw`
  (`test_version_hash_fields_captured`).
- The existing `test_handoff_local_payload_control_fields_carried_verbatim` /
  `test_handoff_local_depends_keeps_original_control_text` /
  `test_handoff_local_unreadable_payload_falls_back_to_db_reconstruction` /
  `test_diff_local_payload_control_fields_carried_verbatim` cover the verbatim-carry and
  degraded-fallback behavior.
- **VersionHash parity scan** (verification tooling, `quickstart.md`): a script porting
  apt's `VersionHash` that compares every emitted stanza against the `.deb` control —
  used to prove 0/207 mismatches on the real cache after the fix.

**Target Platform**: Debian Trixie LTS amd64 / x86_64 (apt 3.0.x).

**Project Type**: CLI tool for offline Debian LTS package management — admin builds the
cache and emits the handoff/diff indexes; operator consumes them with ordinary apt.

**Performance Goals**: No hot-path change. The fix adds an O(1) `DebFile` read per
`local` record at handoff/diff generation and a small constant set of extra fields per
stanza. Resolution goals untouched.

**Constraints**:
- **Schema freeze** (spec 010/011): no new tables, columns, or migrations.
- **No new network calls**: the fix only reads already-cached index/payload data.
- **Verbatim reproduction**: emitted VersionHash fields must match the installed
  dpkg-status record exactly (field presence/absence included); the merged resolver view
  must never leak into a stanza.
- **Degraded fallback only for unreadable local payloads**: a readable control is
  authoritative (a missing `Depends` stays missing); an unreadable `.deb` falls back to
  the DB reconstruction without failing generation (FR-006).
- **Upgrade semantics preserved**: strictly newer versions still present as upgradable
  (FR-004).
- **TDD**: tests written first; both runners + contract suite green at every phase.

**Scale/Scope**: metadata emission only — one new model field set, one stanza builder
change, one local-entry builder change, one index-loader field set.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / Gate | Verdict | Basis |
|------------------|---------|-------|
| I. Offline-First Availability | ✅ PASS | No new network call sites; the fix only reads cached data and emits metadata the operator already consumes. |
| II. Dependency Alignment | ✅ PASS | No resolution change; `depends_raw` (the merged view) still feeds the closure walk — only its emission into stanzas is removed. |
| III. Binary-Source Consistency | ✅ PASS | No binary/source linkage change. |
| IV. Minimal Local Cache | ✅ PASS | No new cache contents; only metadata emission changes. |
| V. Debian Policy Compliance | ✅ PASS | No package or build change; correctness against apt's own metadata semantics. |
| VI. Code Quality & Type Safety | ✅ PASS | New `BinaryPackage` fields are typed (str | None, frozen dataclass); control parsing is typed via `debian.debfile`. |
| VII. Database-First Resolution | ✅ PASS | No DB change; emission reads the existing verified records and cached indexes. |
| VIII. Documentation & Infrastructure Traceability | ✅ PASS | The VersionHash contract is recorded in this feature's plan/research — the stated intent of the spec (FR-007). |
| IX. Incremental Spec Scope | ✅ PASS | 4 user stories, 7 FRs — under the 10/20 decomposition limit. |
| Schema freeze (010/011) | ✅ PASS | No `database.py` changes. |
| Both test runners green | ✅ PASS (gate) | Baseline 806 passed at every phase; TDD. |
| Contract suite green | ✅ PASS (gate) | `tests/contract/test_cli.py` stays green; no CLI contract change. |

**Gates: no violations. Complexity Tracking table not required.**

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] Commands are assigned to the correct persona (admin-only vs operator-only vs shared)
- [x] No persona-dependent behavior leaks across the offline/online boundary
- [x] Cache handoff expectations are reflected in the architecture
- [x] If this feature introduces new personas or changes existing roles, [roles.md](../memory/roles.md) has been updated accordingly

No new or changed commands, so no persona change: the **Administrator** generates the
handoff/diff indexes (the parity-correct stanzas), and the **Operator** consumes them
with ordinary apt. No operator-side tooling changes. `roles.md` needs no update — the
feature is a metadata-correctness fix inside the existing admin-side generation path.

## Project Structure

### Documentation (this feature)

```text
specs/022-apt-version-hash/
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
├── lib/
│   ├── model/
│   │   └── package.py                       # MODIFY (done): BinaryPackage gains depends_field,
│   │                                        #       installed_size, conflicts_raw, breaks_raw,
│   │                                        #       replaces_raw (VersionHash inputs)
│   └── service/
│       ├── index_loader.py                  # MODIFY (done): load_binary_index captures the
│       │                                    #       VersionHash fields verbatim
│       └── handoff.py                       # MODIFY (done): _read_deb_control reads local .deb
│                                            #       control; _local_binary_entry uses it;
│                                            #       _paragraph emits VersionHash fields
│                                            #       verbatim, Depends only from depends_field
tests/
├── unit/
│   ├── test_handoff.py                      # MODIFY (done): archive + local regression tests
│   │                                        #       for pre-depends-only packages
│   └── test_index_loader.py                 # MODIFY (done): VersionHash fields captured
└── contract/
    └── test_cli.py                          # unchanged (no CLI contract change)
```

**Structure Decision**: the existing single-package layout is kept. The corrective work
is surgical edits to existing modules — `_paragraph` and `_local_binary_entry` in
`handoff.py`, `load_binary_index` in `index_loader.py`, and the `BinaryPackage` model —
because the VersionHash contract applies uniformly to every emitted stanza, so there is
no new module or command.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No Constitution violations; the Complexity Tracking table is not required.

## Implementation Status

**Retrospective plan.** The corrective work described above is **already implemented and
verified** in the working tree (`src/package_mgmt/lib/service/handoff.py`,
`src/package_mgmt/lib/service/index_loader.py`, `src/package_mgmt/lib/model/package.py`,
`tests/unit/test_handoff.py`, `tests/unit/test_index_loader.py`); 806 tests pass and the
VersionHash parity scan reports 0/207 mismatches against the real cache. The tasks
breakdown (`/speckit.tasks`) will record the completed work as done.
