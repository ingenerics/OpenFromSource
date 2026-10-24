# CLI Contracts: Ensure Base Packages

## `package_mgmt ensure-base`

Ingests the offline DVD installer image from `iso_dir` (default `<cache root>/iso`) and resolves the base set like `add`.

```text
$ package_mgmt ensure-base
ISO image:        debian-13.0.0-amd64-DVD-1.iso (trixie: main, contrib, non-free-firmware)
Binary packages:  4233
Provides entries: 1142
Index/image mismatches: 0
Base packages resolved: 4219/4233   # deps + build deps resolved like `add`
Warnings:         none

Errors:           none
```

All registered records carry `verified = 0`, exactly like `add`; the flag is set only
when a file is actually verified/downloaded at a later stage — never by this command.

With a netinst/minimal image (below the documented 1,000-package minimum):

```text
$ package_mgmt ensure-base
ISO image:        debian-13.0.0-amd64-netinst.iso (trixie: main, contrib, non-free-firmware)
Binary packages:  494
Provides entries: 120
Index/image mismatches: 0
Base packages resolved: 494/494
Warnings:
  - Image contains only 494 binary packages — this looks like a netinst or minimal
    image, which cannot support fully offline installation. The full offline DVD
    installer image (DVD-1) is the supported artifact.
```

With an index/image mismatch (index lists a file the image does not contain — damaged
image diagnostic; the flag is not affected):

```text
Index/image mismatches: 1
  - pool/main/v/vifm/vifm_1.0-2_amd64.deb (in index, not found in image)
```

During the resolution batch a progress line is shown (single line, updated in place):

```text
Resolved 1200/4233
```

Resolution errors (best-effort, same semantics as `add`) are listed at the end,
capped at 50 on the console; the full list goes to the log:

```text
Resolved 4233/4233
...
Errors:           61 (showing first 50; 11 more in the log)
  - Build tool not resolved: debhelper-extra
  - Virtual package 'foo' has no providers in the index
  - ...
```

When all errors fit in the cap:

```text
Errors:           2
  - Build tool not resolved: debhelper-extra
  - Virtual package 'foo' has no providers in the index
```

Exit codes:
- `0` — ingestion and resolution completed (errors may be listed; matches `add`'s convention: `add` exits non-zero when errors exist, see below)
- `1` (`EXIT_ERROR`) — resolution errors exist (same rule as `add`), or image read/registration failure
- `102` (`EXIT_CONFIG_ERROR`) — invalid configuration (including an `iso_dir` that cannot be resolved)

Error cases (no database modification, exit non-zero):
- No image file present in `iso_dir` → `{"error": "No installer image found in <iso_dir>"}`
- File present but not a readable ISO image → `{"error": "Failed to read ISO image <path>: ..."}`
- Image with no `binary-amd64` indexes (e.g. a source DVD) → `{"error": "No binary-amd64 package indexes found in <path> — not an installer image"}`

Metadata source: the image's `Packages.gz` files are the registration seed only;
`list`, `show`, `verify`, and resolution read the database. The image is never
consulted after registration.

The summary block is also written to the log (Constitution VIII traceability).

## `package_mgmt config`

Gains `iso_dir` in both formats (text format is the existing no-colon layout):

```text
$ package_mgmt config
Configuration:
  base_dir           /var/cache/package_mgmt             (default)
  binary_cache       /var/cache/package_mgmt/binary      (default)
  source_cache       /var/cache/package_mgmt/source      (default)
  iso_dir            /var/cache/package_mgmt/iso         (default)
  db_path            /var/cache/package_mgmt/package_mgmt.db (default)
  log_level          INFO                                (default)
  log_dir            /var/cache/package_mgmt/logs        (default)
  fetch_source       False                               (default)
  max_build_dep_report_depth 2                           (default)
  default source: trixie/main
  ...
```

```json
{
  "paths": {
    "base_dir": "...",
    "binary_cache": "...",
    "source_cache": "...",
    "iso_dir": "..."
  },
  ...
}
```

## `package_mgmt init`

Creates the (empty) `iso_dir` alongside the binary/source cache directories:

```text
$ package_mgmt init
...
Status:            initialized
```

(Output fields unchanged; `iso_dir` creation is the only behavioral addition.)

## Error message scheme (shared)

All failures use the existing `_print_error` JSON-to-stderr convention with the exit codes above. New exception: `IsoError` (image read/parse failures).
