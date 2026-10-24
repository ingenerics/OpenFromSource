# CLI Contracts: Package Dependency Resolution

## `package_mgmt add`

Add one or more packages to the cache by resolving their full dependency tree and populating the database. No payloads are downloaded.

```text
package_mgmt add <package>[=version]...
```

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `<package>[=version]...` | str (one or more) | required | Package name(s) to add and resolve. Optional `=version` pins a specific version; omitted = latest per index |

**Exit codes**:
| Code | Meaning |
|------|---------|
| 0 | Success — all packages resolved, records created/updated |
| 1 | Resolution error — one or more packages could not be resolved |

**Stdout on success**:
```text
Added: vifm
  Binary: vifm (= 2.1-1+b1) [amd64] → source: vifm (2.1-1)
  Binary: perl (= 5.36.0-7) [amd64] → source: perl (5.36.0-7)
  Binary: perl-base (= 5.36.0-7) [amd64] → source: perl (5.36.0-7)
  ...
Packages resolved: 8
Binaries recorded: 12
Sources linked:    6
```

**Stderr on error** (JSON):
```json
{"error": "Package not found in index: nonexistent-pkg", "exit_code": 1}
```

---

## `package_mgmt list --deps`

Display the resolved dependency graph for a package.

```text
package_mgmt list --deps <package>
```

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--deps` | str | required | Package name to inspect |
| `--format` | `text` or `json` | `text` | Output format |

**Exit codes**:
| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Package not found in database |

**Stdout (text format)**:
```text
vifm (pending)
  ├─ perl (= 5.36.0-7) [binary ✓, source ✓, verified: ✗]
  │   ├─ perl-base (= 5.36.0-7) [binary ✓, source ✓, verified: ✗]
  │   └─ libc6 (>= 2.34) [binary ✓, source: missing, verified: ✗]
  └─ libc6 (>= 2.34) [binary ✓, source: missing, verified: ✗]
```

**Stdout (json format)**:
```json
{
  "package": "vifm",
  "status": "pending",
  "dependencies": [
    {
      "name": "perl",
      "version": "5.36.0-7",
      "binary_exists": true,
      "source_exists": true,
      "verified": false,
      "dependencies": [
        {"name": "perl-base", "version": "5.36.0-7", "binary_exists": true, "source_exists": true, "verified": false},
        {"name": "libc6", "version_constraint": ">= 2.34", "binary_exists": true, "source_exists": false, "verified": false}
      ]
    }
  ]
}
```

---

## `package_mgmt verify --graph`

Report completeness statistics for the resolved dependency graph.

```text
package_mgmt verify --graph [--package <package>]
```

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--graph` | flag | — | Report graph-level statistics |
| `--package` | str | all packages | Restrict to a single package's tree |

**Exit codes**:
| Code | Meaning |
|------|---------|
| 0 | Graph is complete — no unresolved dependencies |
| 1 | Graph has issues — missing binary/source records or unresolved deps |

**Stdout**:
```text
Dependency Graph Verification
─────────────────────────────
Total packages:         12
Total binaries:         18
Total sources:          10
Unresolved deps:        2
  - libc6 (>= 2.34) — no binary record for matching version
  - perl-base (= 5.36.0-7) — no binary record for matching version
Unlinked binaries:      0
Circular dependencies:  0
Status:                 INCOMPLETE
```
