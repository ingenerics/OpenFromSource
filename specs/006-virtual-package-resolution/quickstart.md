# Quickstart: Virtual Package Resolution

## Initialization

```bash
package_mgmt init
```

After init, the `provides` table contains all virtual package mappings from the index:

```bash
# Check total provides entries
sqlite3 /var/cache/package_mgmt/package_mgmt.db "SELECT COUNT(*) FROM provides"

# List providers for a specific virtual package
sqlite3 /var/cache/package_mgmt/package_mgmt.db \
  "SELECT package_name, version, release FROM provides WHERE virtual_name = 'awk'"
```

Expected output:
```
gawk|5.2.1-2|trixie
mawk|1.3.4|trixie
original-awk|2023-12-01|trixie
```

## Adding a Package with Virtual Dependencies

```bash
package_mgmt add vifm
```

The resolver:
1. Encounters `Depends: awk` — not a real package name
2. Checks `virtual_resolutions` — not found yet
3. Queries `provides` for `awk` → finds gawk, mawk, original-awk
4. Selects gawk (alphabetically first, or already-resolved)
5. Creates a `binaries` record for gawk with real dependencies
6. Records `virtual_resolutions(awk → gawk.binary_id)`

On re-run, step 2 hits the cache and reuses gawk immediately.

## Verifying Resolutions

```bash
# Show all virtual resolutions
sqlite3 /var/cache/package_mgmt/package_mgmt.db \
  "SELECT vr.virtual_name, b.package_name, b.version
   FROM virtual_resolutions vr
   JOIN binaries b ON b.id = vr.resolved_binary_id"

# Show which dependencies are virtual
package_mgmt show --tables virtual_resolutions
```
