# Research: Add from File

**Source**: Spec 009 (user description + clarification Q1), codebase analysis of the Phase 9 batch engine and `ensure-base` reporting patterns.

## 1. File Format and Pin Syntax

**Problem**: The manifest file must be unambiguous: one package per line, with an optional version pin "in default Debian notation". The existing CLI already parses `name=version` (`_parse_pkg_spec` in `add.py`); the batch boundary type is `PackageRequest(name, requested_version)`.

**Decision**: Each line is `name` or `name=version` — exactly the syntax `_parse_pkg_spec` already understands — with the version string in Debian version notation (epochs, `~`, `+b` suffixes, etc.). Lines are trimmed of surrounding whitespace; blank lines and lines starting with `#` are ignored (convenience on top of the one-line-per-package format, per spec Assumptions). The parser reuses `_parse_pkg_spec` per line, producing `PackageRequest` objects directly — the same type `resolve_batch` consumes.

**Rationale**: One input syntax for both forms (positional args and file lines) keeps the command consistent and the implementation a thin loop over an existing parser. `name=version` is the established Debian pin notation (apt's `package=version`).

**Alternatives considered**:
- Debian source-style `name (>= 2.0)` / `name (2.1-1)` — rejected: version *constraints* are not what the file carries; a pin is a requested version, and the CLI already expresses it as `name=version`. Mixing two pin notations would be confusing.
- Space/comma-separated names on one line — rejected: the description explicitly reserves one line per package.
- Requiring no comments/blank lines (strictest reading of "one line per package") — rejected as unfriendly; blank/comment lines are harmless and conventional (documented assumption, easy to drop).

## 2. Validation Strategy — All-or-Nothing

**Problem**: What happens when a line is malformed (empty name, bad pin, garbage)? Two defensible policies: report-and-continue (skip bad lines, process the rest, exit non-zero) or validate-everything-first (report every bad line, process nothing).

**Decision**: **All-or-nothing.** The entire file set is parsed and validated before any resolution starts; every malformed line is reported with its file and line number; if any line is bad, the command errors and processes nothing. An unreadable file, or an invocation yielding zero names, is likewise a hard error with nothing processed.

**Rationale**: The file is a declarative manifest the administrator curates. Silent skipping (or partial processing) hides typos and makes the difference between "resolved everything" and "resolved everything except three lines" non-obvious — especially at hundreds of lines. Reporting *all* bad lines at once lets the administrator fix the file in one pass (the re-run loop is idempotent, US2). This matches the spec's framing: the format is reserved one-line-per-package, so a line violating it is a file error, not a resolution error.

**Alternatives considered**:
- Skip-and-continue with a warning list — rejected: hides data loss; error attribution across a large manifest becomes murky; violates KISS (two failure modes to reason about).
- Stop at the first bad line — rejected: forces repeated fix/re-run cycles for multi-line typos.

## 3. Input Exclusivity — `--file` vs Positional Names (KISS)

**Problem**: Should `add --file f.txt pkg1 pkg2` be allowed (union of both forms)?

**Decision**: **Mutually exclusive.** If both `--file` and positional names are given, the command errors (nothing processed). One input form per invocation.

**Rationale**: User directive (KISS). The union adds parsing order rules, dedup semantics across sources, and a second output decision for no benefit — the manifest file can simply contain everything.

**Alternatives considered**: Union with deterministic order (positionals first, then files) — rejected per user directive.

## 4. Output Mode — Batch Reporting for File-Driven Invocations

**Problem**: A manifest can hold hundreds or thousands of names; the existing per-name output ("Package X resolved from ...") would produce thousands of lines.

**Decision** (clarification Q1, option B): When the invocation includes file-provided names, report like `ensure-base`:
- in-place progress line `Resolved X/Y` during the batch (the `resolve_batch` progress callback),
- a final summary block: `Packages requested`, `Packages resolved`, then `Errors: N` with the error list,
- console errors capped at 50 (`CONSOLE_ERROR_CAP`), the full list written to the log via `logger.warning` per item.

Positional-only invocations keep their current per-name output byte-for-byte.

**Rationale**: Batch-appropriate output at any scale, reusing the two established patterns (progress callback + summary/error cap from `ensure_base.py`) without inventing a third reporting style. The summary numbers mirror `ensure-base`'s semantics: requested = validated lines; resolved = names whose resolution produced no errors.

**Alternatives considered**:
- Per-name lines (option A) — rejected in clarification: unreadable at thousands of lines.
- Per-name + opt-in `--quiet` (option C) — rejected in clarification: extra CLI surface and a second output path to test.

## 5. Reuse Strategy — Zero New Machinery

**Problem**: The feature must not reinvent resolution, reporting, or input parsing.

**Decision**: `resolve_batch` + `PackageRequest` are consumed unchanged (one call per invocation, progress callback wired for the file-mode reporting); `_parse_pkg_spec` is reused per line; `_print_error` and the exit-code constants are reused for all error paths; the summary/error-cap shape follows `ensure_base.py`'s handler. No resolver, model, schema, or dependency changes.

**Rationale**: Phase 9 built the batch engine exactly for this ("a future file-based add spec is a thin CLI change"); spec FR-003 forbids new machinery.

**Alternatives considered**: A new service module for manifest parsing — rejected: parsing is pure CLI-boundary input handling; module-level functions in `add.py` next to `_parse_pkg_spec` are unit-testable without new layers (KISS).
