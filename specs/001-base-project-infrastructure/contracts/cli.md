# CLI Contracts: Base Project Infrastructure

## `package_mgmt init`

Initialise the cache directories and database.

```text
package_mgmt init [--base-dir PATH] [--force]
```

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--base-dir` | `str` | from config | Override base path for this invocation |
| `--force` | flag | `false` | Re-create database if schema mismatched (destructive) |

**Exit codes**:
| Code | Meaning |
|------|---------|
| 0 | Success — cache dirs and DB ready |
| 100 | Permission error — cannot create directory |
| 101 | Schema mismatch — existing DB has unexpected version |
| 102 | Unparseable config — syntax error in TOML file |

**Stdout on success**:
```text
Base directory:    /var/cache/package_mgmt
Binary cache:      /var/cache/package_mgmt/binary
Source cache:      /var/cache/package_mgmt/source
Database:          /var/cache/package_mgmt/package_mgmt.db
Schema version:    1
Status:            initialized
```

**Stderr on error** (JSON):
```json
{"error": "Permission denied: /var/cache/package_mgmt/binary", "exit_code": 100}
```

---

## `package_mgmt config`

Display the effective configuration.

```text
package_mgmt config [--format {text,json}]
```

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--format` | `text` or `json` | `text` | Output format |

**Exit codes**:
| Code | Meaning |
|------|---------|
| 0 | Success |
| 102 | Unparseable config |

**Stdout (text format)**:
```text
paths.base_dir        /var/cache/package_mgmt          (default)
paths.base_dir        /custom/cache                     (override: project.local.toml)
paths.binary_cache    /var/cache/package_mgmt/binary    (default)
paths.source_cache    /var/cache/package_mgmt/source    (default)
database.path         /var/cache/package_mgmt/package_mgmt.db  (default)
log.level             INFO                              (default)
```

**Stdout (json format)**:
```json
{
  "paths": {
    "base_dir": "/var/cache/package_mgmt",
    "binary_cache": "/var/cache/package_mgmt/binary",
    "source_cache": "/var/cache/package_mgmt/source"
  },
  "database": {"path": "/var/cache/package_mgmt/package_mgmt.db", "source": "default"},
  "logging": {"level": "INFO", "source": "default"}
}
```
