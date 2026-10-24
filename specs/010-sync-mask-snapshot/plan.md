# Implementation Plan: Sync Mask & Snapshot

**Branch**: `010-sync-mask-snapshot` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/010-sync-mask-snapshot/spec.md`

## Summary

The cache gains the plumbing to record which cache state has been delivered to which sync target ("system"). A `systems` registry (moniker → opaque identifier, auto-assigned, never reused) and a per-binary delivery record (`binaries.sync_mask`) are added to the database at schema v1 (no version bump — delete-and-rebuild is the documented development-time migration path). The `snapshot` command marks exactly the current, verified binaries as delivered for a named system — a pure, idempotent assertion with a read-only `--dry-run` preview; the pending set between two snapshots is the delta for the next handover. The pending set is derived by a single mask-based query gated on `verified = 1` (only files that actually exist locally can have been delivered) and is the contract every future sync flow (delta export, bootstrap, staged syncs) composes: *copy the pending set, then snapshot*. A small supersede fix lands in the same cycle because snapshot marking depends on exactly one current binary per package. Metadata-only: no file operations, no `verified`-flag writes, no timestamps, no snapshots table — the mask is the record.

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default)

**Database**: SQLite 3.x — schema version **stays 1** (FR-013). `EXPECTED_SCHEMA_SQL` gains the `systems` table and the `binaries.sync_mask` column in place; `db_init.initialize_database` additionally verifies the new objects exist when the version matches, failing with a rebuild instruction on stale development databases (FR-012). 64-bit `INTEGER` masks: ceiling 62 systems.

**Primary Dependencies**: sqlite3 (stdlib). **No new dependencies.**

**Existing Architecture**:
- `cli/main.py`: flat subparsers (`add`, `ensure-base`, `init`, `config`, `list`, `show`, `verify`) — **extended**: new `systems` subparser (nested `add`/`list`/`rename`/`remove`) and `snapshot` subparser (`--system`, `--dry-run`)
- `lib/model/database.py`: `SCHEMA_VERSION`, `EXPECTED_SCHEMA_SQL` — **extended** in place
- `lib/service/db_init.py`: `initialize_database` — **extended**: stale-object check + clear rebuild error
- `lib/service/resolver.py`: `upsert_binary` conflict-update (line ~57) — **fixed** (FR-011): re-registration of a previously superseded version must restore `status = 'current'`
- `lib/util/errors.py`: `_print_error` (JSON to stderr), `EXIT_SUCCESS/EXIT_ERROR/EXIT_CONFIG_ERROR` — **reused**; `EXIT_SCHEMA_MISMATCH = 101` exists for the stale-schema path
- `cli/commands/ensure_base.py` + `cli/commands/list.py`: reporting/table-print patterns — **patterns reused** for registry and snapshot output

**What needs to change**:
1. `lib/model/database.py`: `systems` table + `uq_systems_active_name` partial unique index + `binaries.sync_mask`; version stays 1
2. `lib/service/db_init.py`: stale-object detection (systems table + `binaries.sync_mask` column present) with a rebuild-instructions error; exposed as a shared validate helper invoked by the new `systems`/`snapshot` command entry points (mapped to `EXIT_SCHEMA_MISMATCH` 101, the same mapping `init` already applies to `DatabaseError`)
3. `lib/service/resolver.py`: supersede fix in `upsert_binary` conflict-update (status restored to `current`)
4. New `cli/commands/systems.py`: registry commands — auto-assigned monotonic bits (next power of two above the max ever issued; never reused thanks to soft-delete `retired` flag), duplicate-name rejection, rename preserving marks, soft-remove, list with delivered counts
5. New `cli/commands/snapshot.py`: pending count (dry-run), mark operation (`UPDATE binaries SET sync_mask = sync_mask | ? WHERE status = 'current' AND verified = 1`, single atomic statement), unknown-system error
6. `cli/main.py`: register `systems` and `snapshot` subparsers, dispatch
7. `tests/`: unit tests (registry lifecycle incl. bit-never-reuse, snapshot semantics, db_init stale detection, resolver supersede fix), integration test (two-points-in-time flow), contract tests (systems/snapshot output formats)

**Performance Goals**: No new global targets. The snapshot mark is one indexed-agnostic `UPDATE` over at most tens of thousands of rows — SQLite executes it in well under a second; the dry-run count is a single aggregate query. Registry operations are single-row.

**Constraints**: Metadata-only — no downloads, no file copying, no `verified`-flag writes; `verified` is read as the delivery gate (only current **and verified** binaries qualify for marking — an unverified file does not exist locally, so it cannot have been delivered). The mask is written by exactly one operation (snapshot) and never auto-reset. Bits are opaque (never shown), monotonic, never reused (FR-002) — enforced by soft-delete + `MAX(bit)`-based assignment. No version bump (FR-013); stale databases fail with a clear rebuild message (FR-012). The future sync/export command (another spec) enforces copy → verify → snapshot ordering; this spec only provides the assertion and the pending-set query contract. Since no command sets `verified = 1` yet (download work is future), validation and integration tests simulate downloaded files by setting the flag directly.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Notes |
|-----------|-------|-------|
| I. Offline-First | ✅ Compliant | Pure local metadata state; no network, no new fetches. |
| II. Dependency Alignment | ✅ Compliant | No resolution changes except the supersede fix, which preserves existing last-writer-wins semantics (only restores `current` for re-requested versions). |
| III. Binary-Source Consistency | ✅ Compliant | No changes to sources, links, or version mapping. |
| IV. Minimal Local Cache | ✅ Compliant | Adds one integer column + one small registry table; no cache footprint change. |
| V. Debian Policy Compliance | ✅ Compliant | No policy-surface changes. |
| VI. Code Quality & Type Safety | ✅ Compliant | Registry logic in a new command module with typed dataclass-free helpers (SQL-level), following existing command conventions; no new dict shapes. |
| VII. Database-First Resolution | ✅ Compliant | The delivery record is database state derived from existing current-status + `verified` semantics; `verified` is read as a gate, never written. |
| VIII. Documentation & Infrastructure | ✅ Compliant | contracts/, quickstart.md, data-model.md updated in this cycle; the delete-and-rebuild migration policy is documented here (data-model.md) — the spec's sanctioned development-time path. |
| IX. Incremental Spec Scope | ✅ Compliant | 3 stories / 13 FRs — under the caps; one demonstrable outcome (two snapshots yield a verifiable pending delta). |

**Gate**: PASS — no violations that require justification.

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] Commands are assigned to the correct persona (admin-only vs operator-only vs shared) — `systems` and `snapshot` are admin-only cache-building commands, exactly like `add`/`ensure-base`
- [x] No persona-dependent behavior leaks across the offline/online boundary — metadata-only state, nothing ships differently; the operator flow is unchanged
- [x] Cache handoff expectations are reflected in the architecture — the delivery record documents which state was handed over; the delta query is the contract for future handoff tooling
- [x] If this feature introduces new personas or changes existing roles, [roles.md](../memory/roles.md) has been updated accordingly — no changes needed (no new persona; admin-only additions)

## Project Structure

### Documentation (this feature)

```text
specs/010-sync-mask-snapshot/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: design decisions consolidated (brainstorming outcomes)
├── data-model.md        # Phase 1: schema + semantics + rebuild policy
├── quickstart.md        # Phase 1: validation guide
├── contracts/           # Phase 1: CLI contract definitions
│   └── cli.md           # systems + snapshot contract (usage, output, exit codes)
├── checklists/
│   └── requirements.md  # Specification quality checklist
└── tasks.md             # Phase 2 output (/speckit.tasks - NOT created here)
```

### Source Code (files to change)

```text
src/package_mgmt/
├── cli/
│   ├── main.py                      # +systems, +snapshot subparsers and dispatch
│   └── commands/
│       ├── systems.py               # NEW: add/list/rename/remove registry commands
│       └── snapshot.py              # NEW: pending dry-run + mark assertion
└── lib/
    ├── model/
    │   └── database.py              # +systems table, +binaries.sync_mask (v1, in place)
    └── service/
        ├── db_init.py               # +stale-object detection with rebuild error
        └── resolver.py              # supersede fix: upsert_binary conflict restores status='current'
tests/
├── unit/
│   ├── test_systems.py              # NEW: registry lifecycle, bit assignment/never-reuse
│   ├── test_snapshot.py             # NEW: pending counts, marking, idempotency, errors
│   ├── test_database.py             # +stale-schema detection cases
│   └── test_resolver_batch.py       # +superseded-version re-request cases
├── integration/
│   └── test_sync_snapshot_flow.py   # NEW: end-to-end two-points-in-time delta flow
└── contract/
    └── test_cli.py                  # +systems/snapshot output-format contract tests
```

**Structure Decision**: Follows the existing single-package layout — one command module per subcommand (`systems.py`, `snapshot.py`), schema in `model/database.py`, init logic in `service/db_init.py`. Registry and snapshot logic live at the command layer as small SQL-driven helpers (unit-testable), matching how `list.py`/`show.py` operate directly on the connection; no repository layer is justified at this scale (KISS, Constitution VI reuse mandate).

## Complexity Tracking

> No constitution violations — this section is intentionally left blank.
