# Implementation Plan: Add from File

**Branch**: `009-add-from-file` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/009-add-from-file/spec.md`

## Summary

`add` gains a package-list file input: `add --file <path>` reads a plain-text manifest (one package per line, optional `=version` pin in Debian notation), validates it fully, and resolves every listed name in a single batch invocation — metadata only, identical semantics to positional `add` (records created with `verified` flags unset; dependencies/build-dependencies/sources resolved per app settings through the existing flow). File input and positional names are mutually exclusive (KISS — mixing them is an error, nothing processed). File-driven invocations report batch-style: in-place `Resolved X/Y` progress, a final summary block, and errors capped at 50 on the console with the full list in the log (clarified Q1). Everything reuses the Phase 9 batch engine (`resolve_batch` + `PackageRequest`) and the `ensure-base` reporting patterns — no new resolution machinery, no schema changes.

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default)

**Database**: SQLite 3.x — schema version 1, **no changes**. All records live in the existing tables; no new tables or columns.

**Primary Dependencies**: python-debian (deb822), sqlite3 (stdlib). **No new dependencies.**

**Existing Architecture**:
- `resolver.py`: `PackageResolver.resolve_batch(requests, source=None, progress=None)` — the Phase 9 batch engine: union closure walked once, per-name commits, per-segment stats, build-dep pass once per batch — **reused unchanged**
- `model/resolution.py`: frozen `PackageRequest(name, requested_version)` — the batch boundary type — **reused unchanged**
- `cli/commands/add.py`: `handle_add(packages, source)` — `_parse_pkg_spec` (name=version split), one `resolve_batch` call, per-name output lines — **extended**: file input + batch reporting mode
- `cli/commands/ensure_base.py`: progress callback (`\rResolved X/Y`), `_print_summary` block, `CONSOLE_ERROR_CAP = 50`, errors logged per item — **patterns reused** for the file-mode summary
- `util/errors.py`: `_print_error` (JSON to stderr), `EXIT_SUCCESS`/`EXIT_ERROR`/`EXIT_CONFIG_ERROR` — **reused**

**What needs to change**:
1. `cli/main.py`: `add` parser gains `--file <path>` (repeatable); the positional `package` argument becomes optional; mutual exclusivity enforced; dispatch passes the file list to `handle_add`
2. `cli/commands/add.py`: package-list parsing + validation (trim, blank/`#` lines, `name=version` per line), all-or-nothing on malformed lines (every bad line reported with file + line number, nothing processed); batch reporting mode for file-driven invocations (progress + summary + 50-cap + full list to log); input-error handling (missing file, empty result, mixed input) via `_print_error`
3. `tests/`: unit tests for the parser/validator; integration tests for file-vs-positional parity and error cases; contract tests for the file-mode summary format (per `contracts/cli.md`)

**Performance Goals**: No new global targets. The batch engine already resolves ~4,400 names in ~13 s (measured on the real DVD in spec 008 Phase 9); a manifest of any practical size inherits that.

**Constraints**: Metadata-only — no downloads beyond the existing `add` behavior (index fetch per app settings). `verified` flags are never set by this feature (same rule as `add`). No new resolution machinery (Constitution VI reuse mandate, spec FR-003). File input and positional names are mutually exclusive (spec FR-001, KISS). No schema changes. Positional-only invocations keep their current output byte-for-byte.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Notes |
|-----------|-------|-------|
| I. Offline-First | ✅ Compliant | File-driven `add` behaves exactly like `add` — admin-side, network per app settings; nothing new is fetched beyond the existing flow. |
| II. Dependency Alignment | ✅ Compliant | Dependencies/build-deps resolved by the existing constraint-aware batch resolver, unchanged. |
| III. Binary-Source Consistency | ✅ Compliant | Sources and `binary_to_source` links come from the existing flow, unchanged. |
| IV. Minimal Local Cache | ✅ Compliant | Metadata-only hydration; the manifest file is an input artifact, not cache content. |
| V. Debian Policy Compliance | ✅ Compliant | No policy-surface changes. |
| VI. Code Quality & Type Safety | ✅ Compliant | Reuses `PackageRequest`; the file parser returns a typed result (requests + line errors); no new bare dict shapes. |
| VII. Database-First Resolution | ✅ Compliant | Records populated before any network operation, exactly as `add`; `verified` flags follow the `add` rule (unset at registration). |
| VIII. Documentation & Infrastructure | ✅ Compliant | contracts/, quickstart.md, data-model.md updated in this cycle; no new dependencies to track. |
| IX. Incremental Spec Scope | ✅ Compliant | 2 stories / 10 FRs — well under the caps; one demonstrable outcome (one command turns a manifest into cache metadata). |

**Gate**: PASS — no violations that require justification.

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] Commands are assigned to the correct persona (admin-only vs operator-only vs shared) — `add` is already admin-only; the file input is the same command, same persona
- [x] No persona-dependent behavior leaks across the offline/online boundary — file-driven `add` behaves exactly like `add`
- [x] Cache handoff expectations are reflected in the architecture — nothing new ships in the handoff (the manifest is admin-side input, not cache content)
- [x] If this feature introduces new personas or changes existing roles, [roles.md](../memory/roles.md) has been updated accordingly — no changes needed (no new command, no persona change)

## Project Structure

### Documentation (this feature)

```text
specs/009-add-from-file/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # File format / validation / output decisions
├── data-model.md        # Input artifact + parse result shape (no schema change)
├── quickstart.md        # Validation guide
├── contracts/           # CLI contract definitions
│   └── cli.md           # add --file contract (usage, format, output, exit codes)
└── tasks.md             # Generated per story (/speckit.tasks)
```

### Source Code (files to change)

```text
src/package_mgmt/
├── cli/
│   ├── main.py                      # +--file option for add; exclusivity; dispatch
│   └── commands/
│       └── add.py                   # +package-list parse/validate, file-mode reporting
tests/
├── unit/
│   └── test_add_file.py             # NEW: parser/validator unit tests
├── integration/
│   └── test_add_file_flow.py        # NEW: end-to-end file-vs-positional parity + errors
└── contract/
    └── test_cli.py                  # +file-mode summary contract tests
```

**Structure Decision**: Follows the existing single-package layout — the file parser is CLI-boundary logic living in `cli/commands/add.py` (module-level, unit-testable, next to `_parse_pkg_spec`); the resolver and models are untouched. The batch reporting pattern mirrors `ensure_base.py`'s handler without extracting shared machinery (KISS — two similar summary printers are fine at this scale).

## Complexity Tracking

> No constitution violations — this section is intentionally left blank.
