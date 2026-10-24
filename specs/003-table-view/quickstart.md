# Quickstart: Table View

## Prerequisites

- Debian Trixie LTS amd64
- A populated database at the configured `db_path` (run `package_mgmt add vifm` first)
- Project installed or `PYTHONPATH` set

## Validation Scenarios

### 1. Show all tables for a package

```bash
package_mgmt show vifm
```

**Expected**: Four formatted tables (packages, binaries, sources, binary_to_source) with rows matching `vifm`. Empty tables show headers only.

### 2. Filter by specific tables

```bash
package_mgmt show vifm --tables packages,binaries
```

**Expected**: Only the `packages` and `binaries` tables are displayed.

### 3. JSON output

```bash
package_mgmt show vifm --format json
```

**Expected**: Valid JSON with table names as keys and arrays of row objects as values.

### 4. Non-existent package

```bash
package_mgmt show nonexistent-pkg-xyz
```

**Expected**: All four tables displayed with zero rows. Exit code 0.

### 5. Invalid table name

```bash
package_mgmt show vifm --tables foobar
```

**Expected**: Error message listing valid table names. Exit code 1.

### 6. JSON output with NULL values

```bash
package_mgmt show vifm --format json | python3 -c "import sys,json; data=json.load(sys.stdin); print(json.dumps(data, indent=2))"
```

**Expected**: NULL database values appear as JSON `null`. No Python `None` strings leak into output.
