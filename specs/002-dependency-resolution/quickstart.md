# Quickstart: Package Dependency Resolution

## Prerequisites

- Debian Trixie LTS amd64
- Python 3.11+ with dependencies: `python3-debian`, `python3-apt` (or `python3-debian` alone for offline metadata parsing)
- Project installed: `pip install -e .` (registers `package_mgmt` command)
- A repository metadata directory with `Packages.gz` and `Sources.gz` files for the Debian Trixie archive (see Setup below)

## Setup

```bash
# Clone and install the project
cd OpenFromSource
pip install -e .

# Ensure target config exists
echo '[paths]
base_dir = "/var/cache/package_mgmt"' > project.local.toml

# Obtain repository metadata (choose one):
# Option A: Sync from a Debian mirror
mkdir -p /var/cache/package_mgmt/binary/dists/trixie/main/binary-amd64/
mkdir -p /var/cache/package_mgmt/source/dists/trixie/main/source/
wget http://deb.debian.org/debian/dists/trixie/main/binary-amd64/Packages.gz \
  -O /var/cache/package_mgmt/binary/dists/trixie/main/binary-amd64/Packages.gz
wget http://deb.debian.org/debian/dists/trixie/main/source/Sources.gz \
  -O /var/cache/package_mgmt/source/dists/trixie/main/source/Sources.gz

# Option B: Use cached metadata from a prior init (if available)
# (Metadata must be pre-placed for the offline workflow)
```

## Validation Scenarios

### 1. Add a Package with No Hard Dependencies

```bash
package_mgmt add nano
```

**Expected output**:
```
Added: nano
  Binary: nano (= 7.2-1) [amd64] → source: nano (7.2-1)
Packages resolved: 1
Binaries recorded: 1
Sources linked:    1
```

**Verify**:
```bash
sqlite3 /var/cache/package_mgmt/package_mgmt.db "SELECT name, status, verified FROM packages;"
# Should show: nano | pending | 0

sqlite3 /var/cache/package_mgmt/package_mgmt.db "SELECT package_name, version, verified FROM binaries;"
# Should show: nano | 7.2-1 | 0

sqlite3 /var/cache/package_mgmt/package_mgmt.db "SELECT package_name, source_version FROM sources;"
# Should show: nano | 7.2-1
```

### 2. Add a Package with Dependencies (Canonical Example)

```bash
package_mgmt add vifm
```

**Expected**: vifm and its dependency tree resolved. At minimum:
- `vifm` binary + source
- `perl` binary + source (vifm's primary dependency)
- Transitive dependencies of perl (perl-base, perl-modules-*, libc6, etc.)

**Verify dependency graph**:
```bash
package_mgmt list --deps vifm
```

**Expected output** (text format shows tree with binary/source/verified annotations).

**Verify idempotency**:
```bash
package_mgmt add vifm
# Run again — should produce identical database state with no errors
```

### 3. Inspect Dependency Graph

```bash
package_mgmt list --deps vifm --format json
```

**Expected**: JSON output showing the full dependency tree with `binary_exists`, `source_exists`, and `verified` flags.

### 4. Verify Graph Completeness

```bash
package_mgmt verify --graph
```

**Expected**: Summary statistics of total packages, binaries, sources, unresolved dependencies, and unlinked binaries. Since no downloads have occurred, all `verified` fields are 0.

### 5. Unresolvable Package

```bash
package_mgmt add nonexistent-pkg-xyz-12345
```

**Expected**: Error exit (code 1) with JSON error message. No records created in the database.

### 6. Idempotent Re-add

```bash
package_mgmt add nano
package_mgmt add nano
```

**Expected**: Second run updates existing records. No duplicate rows. `packages` table has exactly one row for `nano`.
