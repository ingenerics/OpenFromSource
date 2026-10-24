# Quickstart: Build-Tool Dependencies

## Prerequisites

- Python 3.13+, `pip install python-debian`
- Write access to the cache directory
- Internet access (admin system only) for initial index fetch
- The `add` command must already work for runtime packages (spec 002)

## Setup

```bash
# Initialize cache (run once)
package_mgmt init

# Verify the cache directories exist
ls -la /var/cache/package_mgmt/binary/
ls -la /var/cache/package_mgmt/source/
```

## Validation Scenarios

### Scenario 1: Build-dependency metadata is persisted

With default `[fetch] source = true`, add a package and verify build-dependency metadata is stored.

```bash
package_mgmt add vifm

# Show the source record including the build_depends column
package_mgmt show vifm --tables sources
```

**Expected**: The `sources` table section includes a `build_depends` column containing a JSON array of parsed build-dependency entries (e.g., `perl`, `debhelper`). A package with no Build-Depends field shows NULL.

### Scenario 2: Build-tool records are created

Verify that build-tool binaries are recorded in the database and linked to their source.

```bash
# Show the source_build_deps link table for this source
package_mgmt show vifm --tables source_build_deps

# Show build-tool binaries alongside the source using --deps
package_mgmt show vifm --deps --tables binaries source_build_deps
```

**Expected**: The `source_build_deps` table shows entries linking vifm's source to its build-tool binaries. The `binaries` section includes build-tool packages like `debhelper`, `gcc`, etc.

### Scenario 3: Build-dependency resolution on a complex package

Add a package with a deep build-dependency tree, then verify completeness.

```bash
package_mgmt add perl

# Show build-tool binaries linked from perl
package_mgmt show perl --deps --tables source_build_deps binaries

# Verify no unresolved build deps
package_mgmt verify --graph
```

**Expected**: The `source_build_deps` section shows multiple entries. The `verify --graph` output includes a "Build-Tool Dependencies" section with zero unresolved build deps.

### Scenario 4: Source + build-deps skipped via config

Set `source = false`, add a package, and verify no source records or build-tool links are created.

```bash
# Create local override
cat >> project.local.toml << 'EOF'
[fetch]
source = false
EOF

package_mgmt add vifm

# Show that no source or build-dep tables have data for this package
package_mgmt show vifm --tables sources source_build_deps

# Restore default
rm project.local.toml
```

**Expected**: The `sources` and `source_build_deps` sections are empty for vifm. Binary records are still shown when querying `--tables binaries`.

### Scenario 5: Verify reports build-tool status

Add a package with build deps, then verify the output includes build-tool statistics.

```bash
package_mgmt add vifm
package_mgmt verify --graph
```

**Expected**: Output includes a "Build-Tool Dependencies" section with total build-dep links, build tools, unresolved count, and a COMPLETE/INCOMPLETE build status. A final combined "Status" line reflects both runtime and build-tool health.

### Scenario 6: Missing build tool triggers verify error

Simulate a missing build tool by removing a binary record, then verify.

```bash
# Assume vifm's build deps were resolved; remove one binary
sqlite3 /var/cache/package_mgmt/package_mgmt.db \
  "DELETE FROM binaries WHERE package_name = 'debhelper';"

package_mgmt verify --graph
```

**Expected**: Output shows an unresolved build dep referencing `debhelper` and the build status is INCOMPLETE.

### Scenario 7: Package is both runtime dep and build tool

Add a package whose runtime dependency graph overlaps with its build-tool graph.

```bash
package_mgmt add perl

# Show binaries that appear in both link tables via --deps
package_mgmt show perl --deps --tables binaries binary_to_source source_build_deps
```

**Expected**: The `binaries` section shows packages that also appear in both `binary_to_source` and `source_build_deps`. No duplicate records for the same package in the `binaries` table.

## Verification Matrix

| Scenario | Config | Command | Expected Result |
|----------|--------|---------|-----------------|
| Build-deps persisted | `source = true` | `add vifm` + `show vifm --tables sources` | `build_depends` column has JSON |
| Build-tool records created | `source = true` | `show vifm --tables source_build_deps` | >0 entries |
| Transitive build deps resolved | `source = true` | `add perl` + `verify --graph` | 0 unresolved |
| Source + build deps skipped | `source = false` | `add vifm` + `show vifm --tables sources source_build_deps` | 0 records in both |
| Verify reports build tools | `source = true` | `verify --graph` after `add` | Build-tool section present |
| Missing build tool detected | `source = true` | Remove binary + `verify --graph` | Unresolved dep, INCOMPLETE |
| Dual-role package no dupes | `source = true` | `show perl --deps --tables binaries binary_to_source source_build_deps` | Names in both link tables |

## Database Schema Changes (reference)

See [data-model.md](data-model.md) for full details. Key changes:

- `sources` table: new `build_depends` TEXT column
- New `source_build_deps(source_id, binary_id, verified)` link table

## Config Changes (reference)

See [contracts/cli.md](contracts/cli.md) for full details. Key changes:

- New `[fetch] source` config setting (default `true`) — controls whether source packages AND their build-tool dependencies are fetched
- `verify --graph`: new "Build-Tool Dependencies" section in output, separated runtime and build status lines
- `list --deps`: new `--include-build-deps` flag for build-tool tree display
- `show`: new `source_build_deps` table; `--deps` includes build-tool data
