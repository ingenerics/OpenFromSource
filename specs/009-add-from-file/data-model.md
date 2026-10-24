# Data Model: Add from File

## Entity List

**No schema changes** — `SCHEMA_VERSION` stays `1`; no new tables, columns, or constraints. The feature introduces one input artifact and reuses the existing batch boundary type.

### PackageListFile (input artifact, new)

A plain-text manifest the administrator passes to `add --file <path>`:

| Aspect | Rule |
|--------|------|
| Content | One package per line; optional version pin in Debian notation: `name` or `name=version` |
| Whitespace | Surrounding whitespace trimmed per line |
| Blank lines | Ignored |
| Comment lines | Lines starting with `#` ignored |
| Multiple files | `--file` may be given multiple times; read in CLI order |
| Encoding | UTF-8 |
| Scope | Package names and pins only — no file paths, URLs, or apt source entries |
| Lifetime | Read once at the start of the invocation, then discarded — never stored, never part of the cache or handoff |

Validation rules (all-or-nothing, US2): the whole file set is parsed before any resolution; a malformed line (empty package name, malformed pin) is an error naming the file and line number; an unreadable file is an error; an invocation yielding zero names is an error. Any error → nothing processed, non-zero exit.

### PackageRequest (existing, reused)

The batch boundary type from `model/resolution.py` (`name: str`, `requested_version: str | None = None`). Each validated manifest line becomes exactly one `PackageRequest` — the same type positional `add` arguments produce via `_parse_pkg_spec`. No changes.

### Parse result shape

The file parser returns a typed result so the CLI can distinguish *valid requests* from *line errors*:

| Field | Type | Description |
|-------|------|-------------|
| `requests` | `tuple[PackageRequest, ...]` | Validated requests, in file order |
| `errors` | `tuple[str, ...]` | One message per malformed line, each naming the file and line number |

If `errors` is non-empty, the invocation aborts before any resolution (FR-006).

## Database Semantics (unchanged — same as `add`)

All records produced by a file-driven invocation are created through the existing resolution flow with the `add` rules:

- `packages` / `binaries`: upserted with `verified = 0` (registration never sets the flag; FR-003).
- `sources` / `binary_to_source` / `source_build_deps`: produced by the existing resolver, unchanged.
- Idempotency: re-running the same manifest converges to an identical state (FR-010) — the batch engine's upserts and the `binary_to_source` UNIQUE constraint make re-runs no-ops.

## State Transitions

None new. `binaries.status` (`current` → `superseded`) follows the existing superseding semantics when a requested pin or archive drift differs from a stored version — identical to positional `add name=version`.
