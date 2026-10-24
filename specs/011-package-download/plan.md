# Implementation Plan: Package Download

**Branch**: `011-package-download` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/011-package-download/spec.md`

## Summary

The `download` command turns the database-first metadata into a real offline cache: it computes the pending set from the database (current binaries with `verified = 0`, plus sources linked to current binaries when source fetching is enabled), resolves every file's location/size/SHA-256 from the *cached archive indexes* (index-read design from spec 010 — the DB stores state, the indexes store file metadata; no schema changes, the schema freezes after this feature), downloads each file into the cache's pool with per-phase terminal progress (elapsed time + progress bar), verifies each file against the index checksum before writing it into place, and marks records verified. Downloading is mirror-polite by design (apt-calibrated): one in-flight request per mirror, connection reuse, descriptive User-Agent, bounded retries with exponential backoff honoring `Retry-After`. Failed or interrupted runs converge on re-run (temp files, per-file commits, no partial files). No ISO involvement: `ensure-base` records are archive-index records (spec 008 Phase B).

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default)

**Database**: SQLite 3.x — schema version 1, **no changes** (verified-flag policy from spec 010; this feature only *sets* `binaries.verified` / `sources.verified`).

**Primary Dependencies**: sqlite3 + urllib (stdlib only). **No new dependencies** — terminal progress is rendered by hand (the `ensure-base` `\r` in-place pattern is the precedent).

**Existing Architecture**:
- `lib/service/index_loader.py`: `load_binary_index` / `load_source_index` — **extended**: parse `Size` for binaries; parse `Directory` + `Checksums-Sha256` for sources (structured, not the raw `Files` list)
- `lib/util/paths.py`: `binary_index_path` / `source_index_path` / `resolve_index_path` — **reused** for per-(release, component) index loading
- `lib/service/resolver.py`: unchanged (writers already touch only `verified`)
- `lib/service/ensure_base.py`: unchanged; its Phase B records are archive-index records
- `cli/commands/` conventions: `_print_error` JSON, `EXIT_SUCCESS/ERROR/SCHEMA_MISMATCH/CONFIG_ERROR`, handlers take `db_path` (+ config where needed), `validate_schema` on entry (spec 010 FR-012)
- `tests/fixtures/make_test_iso.py`: **extended** — `Size` in Packages entries, `Directory` + `Files` + `Checksums-Sha256` in Sources entries, and a pool-file writer so tests can serve real bytes over a local HTTP server

**What needs to change**:
1. `lib/model/package.py`: `BinaryPackage` gains `size: str | None`; `SourcePackage.files` gains a structured parsed form (new dataclass `SourceFile(name, size, sha256)`)
2. `lib/service/index_loader.py`: parse the new fields
3. New `lib/util/progress.py`: phase progress renderer — elapsed-time line + progress bar (files/bytes), in-place `\r` updates on a TTY, periodic lines when not a TTY; `isatty` detection; no dependencies
4. New `lib/service/downloader.py`: download engine —
   - pending-set queries (FR-001)
   - per-(release, component) index loading + (name, version) → (filename, size, sha256) lookup maps; missing entry → per-record error (FR-002/FR-012)
   - mirror-polite HTTP: shared opener with descriptive User-Agent, sequential per-mirror requests, bounded retries (1s base ×2, max 30s, jitter, ≤5 attempts), honors `Retry-After`; `404` = permanent per-file failure (FR-015)
   - temp-file download (`*.part` in the destination dir) → sha256 verify → `os.replace` into place; mismatch → remove temp + report expected/actual (FR-003/FR-004/FR-011)
   - per-file `verified` commits (interrupted runs converge; FR-005/FR-011); dedupe by destination path (FR-010)
   - source phase: all files of a source must verify before `sources.verified = 1` (FR-006/FR-007); skipped when `fetch_source` is false (FR-008); only sources linked to current binaries (FR-009)
   - summary counts per phase (FR-013); failures never block other files
5. New `cli/commands/download.py`: `handle_download(db_path, config)` — validate schema on entry, run binary phase then source phase with progress, print per-phase summaries, exit 0 / 1 / 101 / 102
6. `cli/main.py`: register `download` subcommand (no arguments)
7. `tests/`: unit (progress renderer, downloader set/metadata/dedupe/retry logic with stubbed opener), integration (end-to-end against a local HTTP server fixture: happy path, sources, idempotency, delete-one-file, corrupt file, 404, interrupted-run convergence, fetch_source=false, rate-limited server for SC-007), contract (progress lines, summary block, exit codes, error JSONs)

**Performance Goals**: The downloader is transfer-bound; the plan is sequential-per-mirror (one in-flight request) to stay mirror-polite. Index loading happens once per (release, component) and is reused by both phases. No new global targets.

**Constraints**: Metadata resolution strictly from the cached indexes — never from guessed locations (FR-002/FR-012). Files visible in the pool only when fully received and checksum-verified (FR-011). No schema changes; schema freezes after this feature (spec 010 Decision 9). Stdlib-only progress (no new dependencies). Development testing keeps downloads minimal: scratch archive + local HTTP server, small trees (`vifm`), no build-dep downloads. Progress per phase (binary, source) with detailed summaries (FR-014). Mirror politeness per FR-015.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Notes |
|-----------|-------|-------|
| I. Offline-First | ✅ Compliant | Downloads happen only on the admin's networked system; the operator's flow is unchanged. |
| II. Dependency Alignment | ✅ Compliant | The download set is exactly the resolved current graph from the DB — no new resolution machinery. |
| III. Binary-Source Consistency | ✅ Compliant | Source artifacts are downloaded for every source linked to a current binary when `fetch_source` is enabled (US2), keeping rebuilds possible offline. |
| IV. Minimal Local Cache | ✅ Compliant | Only files for records in the DB are downloaded; the pending set is DB-derived. |
| V. Debian Policy Compliance | ✅ Compliant | No policy-surface changes; verification is per the archives' own SHA-256. |
| VI. Code Quality & Type Safety | ✅ Compliant | New typed dataclasses (`SourceFile`); downloader returns typed results; no bare dict shapes; stdlib-only. |
| VII. Database-First Resolution | ✅ Compliant | The DB is fully populated before any download; nothing is downloaded whose index entry cannot be confirmed (FR-002); `verified` set-points are defined by this feature (binaries: file present + checksum match; sources: all files present). |
| VIII. Documentation & Infrastructure | ✅ Compliant | contracts/, quickstart.md, data-model.md updated in this cycle; the index-read design and verified policy are already documented in spec 010. |
| IX. Incremental Spec Scope | ✅ Compliant | 3 stories / 15 FRs — under the caps; one demonstrable outcome (a small hydrated cache becomes a verified offline pool with live progress). |

**Gate**: PASS — no violations that require justification.

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] Commands are assigned to the correct persona (admin-only vs operator-only vs shared) — `download` is admin-only, like `add`/`ensure-base`
- [x] No persona-dependent behavior leaks across the offline/online boundary — the operator's flow is unchanged; the delivered cache now has a populated pool and `verified` records
- [x] Cache handoff expectations are reflected in the architecture — the pool is exactly what the handoff ships; snapshot marking (spec 010) now has verified files to gate on
- [x] If this feature introduces new personas or changes existing roles, [roles.md](../memory/roles.md) has been updated accordingly — no changes needed (admin-only addition)

## Project Structure

### Documentation (this feature)

```text
specs/011-package-download/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: download engine, mirror politeness, progress decisions
├── data-model.md        # Phase 1: download set, metadata resolution, verified transitions
├── quickstart.md        # Phase 1: validation guide
├── contracts/           # Phase 1: CLI contract definitions
│   └── cli.md           # download contract (progress, summaries, exit codes)
├── checklists/
│   └── requirements.md  # Specification quality checklist
└── tasks.md             # Phase 2 output (/speckit.tasks - NOT created here)
```

### Source Code (files to change)

```text
src/package_mgmt/
├── cli/
│   ├── main.py                      # +download subcommand and dispatch
│   └── commands/
│       └── download.py              # NEW: handle_download — phases, progress, summaries
└── lib/
    ├── model/
    │   └── package.py               # +BinaryPackage.size, +SourceFile dataclass
    ├── service/
    │   ├── index_loader.py          # +Size, +Directory/Checksums-Sha256 parsing
    │   └── downloader.py            # NEW: download engine (set, lookup, fetch, verify, retry)
    └── util/
        └── progress.py              # NEW: phase progress renderer (TTY + non-TTY)
tests/
├── unit/
│   ├── test_progress.py             # NEW: bar rendering, elapsed, non-TTY degradation
│   ├── test_downloader.py           # NEW: set enumeration, metadata lookup, dedupe, retry/backoff, mismatch, source all-or-nothing
│   └── test_index_loader.py         # +Size / Checksums-Sha256 parsing cases
├── integration/
│   └── test_download_flow.py        # NEW: local-HTTP-server end-to-end (happy, sources, idempotent, corrupt, 404, interrupted, fetch_source off, rate-limited server)
├── fixtures/
│   └── make_test_iso.py             # +Size in Packages, +Directory/Files/Checksums-Sha256 in Sources, +pool-file writer
└── contract/
    └── test_cli.py                  # +download progress/summary/exit-code contract tests
```

**Structure Decision**: Follows the single-package layout — a `lib/service/downloader.py` engine (unit-testable with a stubbed opener) driven by a thin `cli/commands/download.py` handler (phases + reporting, mirroring the `ensure_base` handler pattern); `lib/util/progress.py` is a standalone renderer reused by both phases. The local HTTP test server is a fixture module (not a dependency), serving the scratch archive's pool so checksum and rate-limit scenarios are fully exercised offline.

## Complexity Tracking

> No constitution violations — this section is intentionally left blank.
