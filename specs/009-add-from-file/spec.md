# Feature Specification: Add from File

**Feature Branch**: `009-add-from-file`
**Status**: Draft

**Input**: User description: "spec add file - Provide a way for the user to pass a file to the `add` command to resolve all package names listed in the file. Like add and ensure-base this resolves metadata only and just hydrates the database. The file format reserves one line per package, with the optional version pin (default debian notation)."

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Hydrates cache metadata for many packages at once by passing a package-list file to `add` (e.g. a rebuild manifest or a base package list) |
| **Operator** | Offline system (no network) | Unchanged — consumes the finished cache; never runs `add` |

**Phase 1 — File Input & Resolution**: The `add` command accepts one or more package-list files instead of positional package names (the two input forms are mutually exclusive). Every listed package — each with an optional version pin — is resolved in a single invocation with exactly the same semantics as `add`: metadata only, database hydration with `verified` flags unset, dependencies/build-dependencies/sources resolved per app settings through the existing flow. Run by the Administrator.

**Phase 2 — File Quality & Robustness**: Missing, malformed, or empty inputs produce clear errors (naming the file and line) with a non-zero exit and no partial processing; version pins behave exactly like positional `name=version` arguments; re-runs are idempotent. Run by the Administrator.

---

### User Story 1 - One File, One Run: Metadata Hydration from a Package List (Priority: P1)

The administrator has a text file listing the packages they want in the cache — one package per line, optionally pinned to a version in Debian notation (e.g. `vim` or `vim=2.1-1`). They run `add --file <path>`. All packages listed in the file are resolved in one invocation — the database is hydrated with the same records, the same `verified`-flag semantics, and the same per-app-settings resolution behavior as running `add` with those names as arguments. Reusing this flow means nothing new is learned or invented: the file is purely an input convenience for the existing `add` behavior.

**Why this priority**: This is the essence of the feature — one command turns a package manifest into cache metadata. Everything else (resolution, sources, links, flags) already exists.

**Independent Test**: Create a file listing N package names (some with `=version` pins). Run `add --file <file>`. The database state is identical to running `add` with those same N names as positional arguments; every record carries the `verified` flag unset; the exit code follows the same rules as `add`.

**Acceptance Scenarios**:

1. **Given** a file with one package per line (pins allowed), **When** the administrator runs `add --file <file>`, **Then** every listed package is resolved and registered exactly as if the names had been passed as arguments, with `verified` flags unset and the usual per-app-settings resolution behavior.
2. **Given** both a file and positional package names, **When** `add --file <file> <pkg>` runs, **Then** a clear error is shown, the command exits non-zero, and nothing is processed — the two input forms are mutually exclusive.
3. **Given** a name in the file that cannot be resolved, **When** the run finishes, **Then** the failure is listed in the final summary (capped at 50 on the console, full list in the log), the remaining names still process (best-effort, same as `add`), and the exit code is non-zero.
4. **Given** a file listing a package with a version pin, **When** the run processes it, **Then** the pin behaves exactly like a positional `name=version` argument.

---

### User Story 2 - File Quality: Clear Errors, No Partial Runs (Priority: P2)

The administrator makes a mistake in the input — the file is missing, a line is malformed, or the file yields no names at all. The command reports the problem clearly — naming the file and, for line-level problems, the line number — exits non-zero, and processes nothing. Fixing the file and re-running is a safe, idempotent loop.

**Why this priority**: The file is a declarative artifact; silently skipping or guessing at bad lines would hide errors in a manifest that may list hundreds of packages. Clear, atomic failure keeps the file trustworthy.

**Independent Test**: Run `add --file` with a missing file, with a file containing a malformed line, and with an empty file — each produces an error naming the file (and line where applicable), a non-zero exit, and zero database changes.

**Acceptance Scenarios**:

1. **Given** a file path that does not exist or cannot be read, **When** `add --file <path>` runs, **Then** a clear error is shown, the command exits non-zero, and the database is not modified.
2. **Given** a file containing a malformed line (e.g. an empty package name, or a malformed version pin), **When** the run starts, **Then** an error naming the file and the line number is shown, the command exits non-zero, and nothing is processed — including lines that would have been valid.
3. **Given** a file that contains no package names (empty, or only blank/comment lines), **When** `add --file <file>` runs, **Then** a clear error is shown and the command exits non-zero.
4. **Given** a file that was fixed after a failed run, **When** `add --file <file>` runs again, **Then** it succeeds and produces no duplicate records.

---

### Edge Cases

- What if the file contains duplicate names? → The invocation behaves like `add vim vim`: names are resolved once per batch semantics; no duplicate records in the database.
- What if the administrator passes both a file and positional package names? → Clear error; the two input forms are mutually exclusive, nothing is processed (US1 AS2, FR-001).
- What if a line's pinned version does not exist in the configured sources? → Reported as a per-name error exactly like a positional `name=version` argument; other names continue.
- What if the file uses Windows line endings or trailing whitespace? → Lines are trimmed; blank lines are ignored.
- What if the file contains comment lines? → Lines starting with `#` are ignored (documented assumption).
- What if the file contains only comments and blank lines? → Treated as an empty file (US2 AS3).
- What if the file is very large (thousands of names)? → All names resolve in one batch invocation; reporting is batch-style (in-place `Resolved X/Y` progress plus a final summary with console errors capped at 50 and the full list in the log), so the output stays readable at any scale.
- What if a name appears in the file that is not in any configured source? → Best-effort per-name error, same as `add` on an unknown name; other names continue (US1 AS3).
- What if the file contains non-UTF-8 bytes? → The file must be readable as text; an unreadable file is reported like a missing file (documented assumption: UTF-8).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The `add` command MUST accept one or more package-list files (e.g. via a `--file` option); the file input form and positional package names MUST be mutually exclusive (mixing them is an error), and all names from the given file(s) MUST be resolved in a single invocation.
- **FR-002**: The file format MUST reserve exactly one package per line, with an optional version pin in Debian notation (`name=version`); blank lines and lines starting with `#` MUST be ignored; surrounding whitespace MUST be trimmed.
- **FR-003**: The command MUST behave like `add` in every user-visible way: metadata-only database hydration, `verified` flags unset at registration, dependencies/build-dependencies/sources resolved per app settings through the existing flow — no new resolution machinery.
- **FR-004**: A version pin in the file MUST behave exactly like a positional `name=version` argument to `add`.
- **FR-005**: A missing or unreadable file MUST produce a clear error and a non-zero exit code without processing anything.
- **FR-006**: A malformed line MUST produce a clear error naming the file and the line number and a non-zero exit code without processing anything (the file is validated before any resolution starts).
- **FR-007**: An invocation that yields no package names (an empty file) MUST produce a clear error and a non-zero exit code.
- **FR-008**: Per-name resolution failures MUST be handled best-effort with the same semantics as `add`: other names continue, failures are reported, and the exit code is non-zero when any name failed.
- **FR-009**: When an invocation includes file-provided names, the command MUST use batch-style reporting (Clarification Q1, option B): an in-place progress line (`Resolved X/Y`) during resolution, a final summary block (counts of resolved/failed names), errors listed at the end capped at 50 on the console with the full list written to the log.
- **FR-010**: Re-running the same file MUST produce an identical database state — no duplicate records (idempotent, same as `add`).

### Key Entities *(include if feature involves data)*

- **PackageListFile (new input artifact)**: A plain-text manifest, one package per line with an optional `=version` pin. An input convenience only — it is read once at the start of the invocation; everything downstream is the existing `add` behavior.
- **Packages / binaries / sources / binary_to_source / provides (existing DB tables)**: Populated with the same records and the same `verified`-flag semantics as positional `add`; no schema or table changes.
- **No new entities**: The feature introduces no new data model — the file is consumed and discarded.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A file listing N packages hydrates the database with exactly the same records as running `add` with those N names as positional arguments (identical table contents, `verified` flags unset), in a single invocation.
- **SC-002**: 100% of valid lines in a correctly formatted file are processed, and every version pin behaves identically to a positional `name=version` argument.
- **SC-003**: Missing, malformed, or empty file input fails with a clear error — naming the file and, for malformed lines, the line number — a non-zero exit, and zero database changes.
- **SC-004**: Re-running the same file leaves the database unchanged.
- **SC-005**: A file with hundreds of names completes in a single invocation with batch-style reporting — an in-place progress line during resolution and a final summary with console errors capped at 50 and the full list in the log — keeping the output readable at any file size.

## Assumptions

- **Pin Syntax**: The version pin in the file uses the same notation as the existing `add` command — `name=version` (e.g. `vim=2.1-1`) — with the version itself in Debian version notation.
- **Comments and Blank Lines**: Lines starting with `#` and blank lines are ignored; this is a convenience on top of the one-line-per-package format.
- **Multiple Files**: The `--file` option may be given multiple times; files are read in the order given and names resolve in that deterministic order. The file input form is mutually exclusive with positional package names (KISS — one input form per invocation).
- **No Standard Input**: Reading from standard input (`-`) is out of scope for this feature; the input is a file path.
- **Encoding**: Files are UTF-8 text.
- **Exit Codes**: The same exit-code rules as `add` (0 success, non-zero when any name failed, configuration errors per existing convention).
- **Persona**: File-based `add` is administrator-only, exactly like `add`.
- **Manifest Scope**: The file lists package names and optional version pins only — no file paths, URLs, or apt source entries.
- **Idempotency**: Re-runs are safe and converge, matching `add`'s behavior on already-resolved names.

## Clarifications

### Session

- Q1: How should the command report progress and results when an invocation includes file-provided names? → A: Batch-style reporting (option B) — when a file is part of the invocation, the whole run reports like `ensure-base`: an in-place progress line (`Resolved X/Y`) during resolution, a final summary block, and errors listed at the end capped at 50 on the console with the full list written to the log (FR-009, US1 AS3, SC-005). Invocations with only positional names keep the existing per-name output unchanged.
