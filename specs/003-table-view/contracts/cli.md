# CLI Contracts: Table View

## `package_mgmt show`

Display database rows for a given package across all tables.

```text
package_mgmt show <package> [--tables TABLE,...] [--format {text,json}] [--deps]
```

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `<package>` | str | required | Package name to look up |
| `--tables` | str (comma-sep) | all | Table names to include: `packages,binaries,sources,binary_to_source` |
| `--format` | `text` or `json` | `text` | Output format |
| `--deps` | flag | — | Append dependency tree to output (Phase 2) |

**Exit codes**:
| Code | Meaning |
|------|---------|
| 0 | Success (even if no rows match) |
| 1 | Invalid table name in `--tables`, or database error |

**Stdout (text format)**:
```text
packages
────────
 name  | requested_version | status  | verified
───────┼───────────────────┼─────────┼─────────
 vifm  | NULL              | pending | 0

binaries
─────────
 id | package_name | version     | architecture | verified | dependencies
────┼──────────────┼─────────────┼──────────────┼─────────┼─────────────
 1  | vifm         | 2.1-1+b1    | amd64        | 0        | [{"name":"perl",...}]

sources
───────
 id | package_name | source_version | verified
────┼──────────────┼────────────────┼─────────
 1  | vifm         | 2.1-1          | 0

binary_to_source
─────────────────
 binary_id | source_id | verified
───────────┼───────────┼─────────
 1         | 1         | 0
```

**Stdout (json format)**:
```json
{
  "packages": [
    {"name": "vifm", "requested_version": null, "status": "pending", "verified": 0}
  ],
  "binaries": [
    {"id": 1, "package_name": "vifm", "version": "2.1-1+b1", "architecture": "amd64", "verified": 0, "dependencies": "[...]"}
  ],
  "sources": [
    {"id": 1, "package_name": "vifm", "source_version": "2.1-1", "verified": 0}
  ],
  "binary_to_source": [
    {"binary_id": 1, "source_id": 1, "verified": 0}
  ]
}
```

**Stderr on error** (JSON):
```json
{"error": "Invalid table name: 'foobar'. Valid: packages, binaries, sources, binary_to_source", "exit_code": 1}
```
