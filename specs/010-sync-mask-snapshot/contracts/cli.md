# CLI Contracts: Sync Mask & Snapshot

## `package_mgmt systems`

Manages the registry of sync targets. The per-system opaque identifier is
**never shown** in any output — the moniker is the only handle (FR-001).

### Usage forms

```text
package_mgmt systems add <name> [--note <text>]
package_mgmt systems list
package_mgmt systems rename <name> <new-name>
package_mgmt systems remove <name>
```

### Output

`systems add` — success (new identifier auto-assigned, never printed):

```text
Added system 'bravo-main'
```

`systems list` — one row per active system, ordered by creation:

```text
NAME        NOTE            DELIVERED
bravo-main  main operator   1234
lab-box                      0
```

`DELIVERED` = count of current binaries with the system's bit set.

`systems rename` / `systems remove` — success:

```text
Renamed system 'bravo-main' to 'bravo-laptop'
Removed system 'bravo-laptop'
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 (`EXIT_ERROR`) | Registry error (below) |
| 101 (`EXIT_SCHEMA_MISMATCH`) | Stale/missing database — rebuild instructions on stderr (FR-012) |
| 102 (`EXIT_CONFIG_ERROR`) | Invalid configuration (existing behavior) |

### Error cases (JSON to stderr via the shared `_print_error` convention; nothing modified)

- Add with an existing active moniker →
  `{"error": "System 'bravo-main' already exists", "exit_code": 1}`
- Rename to an existing active moniker →
  `{"error": "System 'bravo-main' already exists", "exit_code": 1}`
- Rename/remove an unknown or retired moniker →
  `{"error": "Unknown system 'ghost'", "exit_code": 1}`
- Registry full (no fresh identifier available) →
  `{"error": "System registry is full (max 62 systems)", "exit_code": 1}`

## `package_mgmt snapshot`

Marks the current, verified state of the cache as delivered for a system — a
pure assertion (FR-007): no files are copied, moved, or verified. Mark-after-copy
is the administrator's flow; marking happens only after a successful delivery.
Binaries that are current but **unverified** (file never downloaded and
integrity-confirmed) never qualify — they cannot have been delivered.

### Usage forms

```text
package_mgmt snapshot --system <name>             # mark current binaries as delivered
package_mgmt snapshot --system <name> --dry-run   # preview only, read-only
```

### Output

`--dry-run`:

```text
1234 pending for 'bravo-main'
```

Real run (marked count; OR semantics, so re-runs mark 0):

```text
Marked 1234 binaries for 'bravo-main'
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success (including 0 marked — idempotent re-run) |
| 1 (`EXIT_ERROR`) | Unknown system / missing `--system` (below) |
| 101 (`EXIT_SCHEMA_MISMATCH`) | Stale/missing database — rebuild instructions on stderr (FR-012) |
| 102 (`EXIT_CONFIG_ERROR`) | Invalid configuration (existing behavior) |

### Error cases (JSON to stderr via the shared `_print_error` convention; nothing modified)

- Unknown or retired moniker →
  `{"error": "Unknown system 'ghost'", "exit_code": 1}`
- Missing `--system` →
  `{"error": "snapshot requires --system <name>", "exit_code": 1}`

## Pending-set query contract (for future sync/export specs)

The delta a future export command ships is defined by exactly one query
(spec Assumptions; data-model.md):

```sql
SELECT id, package_name, version FROM binaries
WHERE status = 'current' AND verified = 1 AND (sync_mask & <system bit>) = 0;
```

Any future flow composes: *copy the pending set, then snapshot*.
