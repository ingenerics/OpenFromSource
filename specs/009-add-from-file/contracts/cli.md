# CLI Contracts: Add from File

## `package_mgmt add --file`

Reads package names from plain-text manifest file(s) and resolves them in one
batch, with the same semantics as positional `add` (metadata only, `verified`
flags unset, resolution per app settings).

```text
$ package_mgmt add --file /path/to/packages.txt
Resolved 1200/1234          # in-place progress line during the batch
Resolved 1234/1234
Packages requested:  1234
Packages resolved:   1220
Errors:              14
  - vim: Virtual package 'foo' has no providers in the index
  - ...
```

With more than 50 errors, the console is capped and the full list goes to the log:

```text
Errors:              61 (showing first 50; 11 more in the log)
```

`--file` may be given multiple times; files are read in CLI order. The
`--source <release>` override applies to the whole batch, as with positional
`add`.

### File format

- One package per line: `name` or `name=version` (Debian version notation,
  e.g. `vim=2.1-1`)
- Surrounding whitespace trimmed; blank lines ignored; lines starting with
  `#` ignored

### Usage forms

```text
package_mgmt add <pkg> [<pkg> ...] [--source <release>]      # unchanged
package_mgmt add --file <path> [--file <path> ...] [--source <release>]
```

The two input forms are mutually exclusive.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | All names resolved |
| 1 (`EXIT_ERROR`) | Resolution errors exist (same rule as `add`), or an input error (below) |
| 102 (`EXIT_CONFIG_ERROR`) | Invalid configuration (existing behavior) |

### Error cases (JSON to stderr via the shared `_print_error` convention; nothing processed)

- Both `--file` and positional names given →
  `{"error": "Use either package names or --file, not both", "exit_code": 1}`
- Unreadable file →
  `{"error": "Cannot read package list <path>: <reason>", "exit_code": 1}`
- Malformed line(s) — every bad line reported, nothing processed →
  `{"error": "Malformed line 3 in <path>: 'vim (>= 2.0)'", "exit_code": 1}`
  (multiple errors joined; each names its file and line number)
- No package names at all →
  `{"error": "No package names found in <path>", "exit_code": 1}`
- No arguments at all (no names, no `--file`) →
  `{"error": "No packages specified; provide package name(s) or --file", "exit_code": 1}`

### Reporting modes

- **File-driven invocations** (`--file` present): batch reporting as above —
  progress, summary, errors capped at 50 on the console, full list in the log.
- **Positional-only invocations**: output unchanged (per-name
  "Package X resolved from ..." lines, errors listed at the end).

The summary block is also written to the log (Constitution VIII traceability).
