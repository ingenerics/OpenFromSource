# CLI Contracts: Virtual Package Resolution

## `package_mgmt init`

Extended with provides population output:

```text
$ package_mgmt init
Base directory:    /var/cache/package_mgmt
Binary cache:      /var/cache/package_mgmt/binary
Source cache:      /var/cache/package_mgmt/source
Database:          /var/cache/package_mgmt/package_mgmt.db
Schema version:    1
Default source:    trixie/main
Archive sources:
  [1] trixie/main — 68750 binaries, 12000 provides entries
Status:            initialized
```

## `package_mgmt add`

Resolution output includes virtual resolution information:

```text
# Without virtual packages
$ package_mgmt add vifm
Package vifm resolved from trixie/main (8 dependencies)

# With virtual packages resolved
$ package_mgmt add vifm
Package vifm resolved from trixie/main (8 dependencies, 2 virtual)
```

Error cases:
- Virtual package cannot be resolved → error in output: "Virtual package 'foo' has no providers in the index"
```

## `package_mgmt list`

No contract changes for this spec.

## `package_mgmt show`

Support `--tables provides` and `--tables virtual_resolutions` to inspect virtual package data:

```text
$ package_mgmt show gawk --tables provides
Table: provides
 package_name | virtual_name | provides_version
--------------|--------------|-----------------
 gawk         | awk          | (null)
```

```text
$ package_mgmt show --tables virtual_resolutions
Table: virtual_resolutions
 virtual_name | resolved_package | version  | release
--------------|------------------|----------|--------
 awk          | gawk             | 5.2.1-2  | trixie
```
