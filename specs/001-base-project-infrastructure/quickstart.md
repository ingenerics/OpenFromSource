# Quickstart: Base Project Infrastructure

## Prerequisites

- Debian Trixie LTS amd64
- Python 3.11+ (Trixie default meets this)
- Write access to `/var/cache/` (or sudo)
- Project installed: `pip install -e .` (registers `package_mgmt` command)

## Setup

```bash
# Clone and install the project
git clone <repo-url> OpenFromSource
cd OpenFromSource
pip install -e .

# (Optional) Override cache base for testing
echo '[paths]
base_dir = "/tmp/package_mgmt_test"' > project.local.toml
```

## Validation Scenarios

### 1. Config Inspection

```bash
package_mgmt config
```

**Expected output**:
```
paths.base_dir        /var/cache/package_mgmt   (default)
paths.binary_cache    /var/cache/package_mgmt/binary   (default)
paths.source_cache    /var/cache/package_mgmt/source   (default)
database.path         /var/cache/package_mgmt/package_mgmt.db  (default)
log.level             INFO                      (default)
```

With `project.local.toml` present, overridden keys show `(override)`.

### 2. Full Initialisation

```bash
package_mgmt init --base-dir /tmp/package_mgmt_test
```

**Expected output**:
```
Base directory:    /tmp/package_mgmt_test
Binary cache:      /tmp/package_mgmt_test/binary
Source cache:      /tmp/package_mgmt_test/source
Database:          /tmp/package_mgmt_test/package_mgmt.db
Schema version:    1
Status:            initialized
```

**Verify**:
```bash
# Check binary cache Debian apt structure
ls /tmp/package_mgmt_test/binary/
# Should show: pool/  dists/

# Check source cache Debian apt structure
ls /tmp/package_mgmt_test/source/
# Should show: pool/  dists/

# Check database
sqlite3 /tmp/package_mgmt_test/package_mgmt.db ".tables"
# Should show: packages  binaries  sources  binary_to_source
```

### 3. Idempotent Re-init

```bash
package_mgmt init --base-dir /tmp/package_mgmt_test
```

**Expected**: Same success output, no errors, no data loss.

### 4. Schema Mismatch Detection

```bash
# Simulate a future schema version
sqlite3 /tmp/package_mgmt_test/package_mgmt.db "PRAGMA user_version = 99"
package_mgmt init --base-dir /tmp/package_mgmt_test
```

**Expected**: Error exit (code 101), message about schema mismatch.
