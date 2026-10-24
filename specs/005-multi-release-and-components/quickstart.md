# Quickstart: Multi-Release and Multi-Component Support

## Configuration

```toml
[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie"
components = ["main", "contrib", "non-free"]

[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie-backports"
components = ["main"]
```

The first source is the default for `add` commands.

## Initialization

```bash
package_mgmt init
```

Creates the Debian-mirroring cache layout:

```
/var/cache/package_mgmt/
  binary/
    dists/
      trixie/
        main/binary-amd64/Packages.gz
        contrib/binary-amd64/Packages.gz
      trixie-backports/
        main/binary-amd64/Packages.xz    # .xz when the mirror uses xz compression
    pool/
      main/
        libd/libdrm2/libdrm2_2.4.110_amd64.deb
        libd/libdrm2/libdrm2_2.4.120_amd64.deb
  source/
    dists/
      trixie/main/source/Sources.gz
      trixie-backports/main/source/Sources.xz
    pool/
      main/
        libd/libdrm2/libdrm2_2.4.110.dsc
  package_mgmt.db
```

## Adding Packages

```bash
# Resolves from trixie/main (first configured source)
package_mgmt add vifm

# Resolves from trixie-backports/main, falls back to trixie for transitive deps
package_mgmt add hyprland --source trixie-backports
```

## Operator Bootstrap

The cache is a valid apt repository. The pool layout with component prefix matches what Debian tooling expects:

```bash
# Add the cache as an apt source on the offline machine
echo "deb [trusted=yes] file:/var/cache/package_mgmt/binary trixie main contrib non-free" | sudo tee /etc/apt/sources.list.d/cache.list
echo "deb [trusted=yes] file:/var/cache/package_mgmt/binary trixie-backports main" | sudo tee /etc/apt/sources.list.d/cache-backports.list
sudo apt update
```

## Querying Resolution History

```bash
# Current state of the resolved tree
sqlite3 /var/cache/package_mgmt/package_mgmt.db \
  "SELECT package_name, version, release, component FROM binaries WHERE status = 'current'"

# Full history for a specific package
sqlite3 /var/cache/package_mgmt/package_mgmt.db \
  "SELECT id, version, release, component, status FROM binaries WHERE package_name = 'libdrm2' ORDER BY id"
```
