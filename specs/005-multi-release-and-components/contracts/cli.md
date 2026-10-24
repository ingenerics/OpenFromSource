# CLI Contracts: Multi-Release and Multi-Component Support

## `package_mgmt config`

Prints all configured archive sources. Each source shows mirror, release, and components.

```text
$ package_mgmt config
Configuration:
  base_dir:      /var/cache/package_mgmt
  binary_cache:  /var/cache/package_mgmt/binary
  source_cache:  /var/cache/package_mgmt/source
  db_path:       /var/cache/package_mgmt/package_mgmt.db
  log_level:     INFO
  fetch_source:  true
  default source: trixie/main

Archive Sources:
  1. mirror: http://deb.debian.org/debian
     release: trixie
     components: main, contrib, non-free
  2. mirror: http://deb.debian.org/debian
     release: trixie-backports
     components: main
```

Error cases:
- Duplicate `(release, component)` across sources → error naming the duplicate pair
- No sources configured → error requiring at least one `[[archive.source]]`

## `package_mgmt init`

Creates Debian-mirroring cache directories, initializes database, populates tables.

```text
$ package_mgmt init
Base directory:    /var/cache/package_mgmt
Binary cache:      /var/cache/package_mgmt/binary
Source cache:      /var/cache/package_mgmt/source
Database:          /var/cache/package_mgmt/package_mgmt.db
Schema version:    1
Default source:    trixie/main
Archive sources:
  [1] trixie/main — 68750 binaries
  [2] trixie/contrib — 500 binaries
  [3] trixie-backports/main — 500 binaries
Status:            initialized
```

## `package_mgmt add`

New `--source` flag to override the default resolution source:

```text
# Uses default (first configured source)
$ package_mgmt add vifm
Package vifm resolved from trixie/main (8 dependencies)

# Uses specified source
$ package_mgmt add hyprland --source trixie-backports
Package hyprland resolved from trixie-backports/main (42 dependencies, 3 from trixie fallback)
```

Error cases:
- `--source` value does not match any configured release → error listing available releases
- No binary found for the package in any source → existing "Package not found" error

## `package_mgmt list`

Optional `--status` filter (`current`, `superseded`, `all`):

```text
$ package_mgmt list --status current
Package         Version         Status    Release            Component
libdrm2         2.4.120-1       current   trixie-backports   main
gawk            5.2.1-2         current   trixie             main
```

```text
$ package_mgmt list --status all
Package         Version         Status    Release            Component
libdrm2         2.4.110-1       superseded trixie           main
libdrm2         2.4.120-1       current   trixie-backports   main
gawk            5.2.1-2         current   trixie             main
```
