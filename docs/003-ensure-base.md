# Ensure Base Packages (`ensure-base`)

How-to for `package_mgmt ensure-base` — ingesting the offline DVD installer
image into the cache. The image's index is the registration seed; the database
is the metadata source of truth after the run.

## Prerequisites

- System package `python3-pycdlib` (ISO9660/Rock Ridge reading; Debian Trixie
  package, pure Python, no root required):

  ```bash
  sudo apt install python3-pycdlib
  ```

- The offline DVD installer image (first installer DVD of the release set,
  e.g. `debian-13.6.0-amd64-DVD-1.iso`) placed in the cache's ISO directory
  (default `<cache root>/iso`, configurable via `[paths] iso_dir`). Exactly one
  image file must be present.
- An initialized cache database (`package_mgmt init`).

> **Admin-only**: `ensure-base` is an administrator command, like `add`. The
> operator never runs it — it ingests installer images on the networked
> admin system, and the image itself ships with the cache handoff.

> **Fresh cache only**: `ensure-base` ingests the installer image into an
> **empty** cache — the graybeard profile runs it exactly once
> (`init` → `ensure-base` → `verify` → `handoff`). It refuses a cache that
> already contains registered packages (clear error, exit 1, database
> untouched). To re-ingest, start from a fresh cache (delete the database and
> re-run `init`); an interrupted run is recovered the same way.

## Workflow

```bash
package_mgmt init          # once: creates cache dirs (incl. iso/), database
package_mgmt ensure-base   # ingest the image and resolve the base set (fresh cache only)
package_mgmt verify        # confirm the graph is complete before handoff
```

A run has three phases, mirroring `add` semantics:

1. **Phase A (local)**: the image's `dists/<release>/<component>/binary-amd64/
   Packages.gz` files are read once and every binary they list is registered in
   the database (`verified = 0`), along with `packages` rows and `provides`
   entries. The index↔image cross-check reports files the index lists but the
   image does not contain as warnings (damaged-image diagnostic).
2. **Phase B (behaves like `add`)**: each base package name is resolved with
   the same resolver `add` uses — dependencies and build-dependencies per app
   settings (archive access on the networked system as configured). Progress
   is shown as a single in-place line (`Resolved X/Y`); resolution errors are
   listed at the end, capped at 50 on the console with the full list in the
   log.
3. **Phase C**: summary printed (and logged) and temporary index files cleaned
   up. Nothing is copied out of the image.

Example summary on a full-size image:

```text
ISO image:        debian-13.6.0-amd64-DVD-1.iso (trixie: main, contrib, non-free-firmware)
Binary packages:  4233
Provides entries: 1142
Index/image mismatches: 0
Base packages resolved: 4219/4233
Warnings:         none
Errors:           none
```

## Verified-flag semantics (same as `add`)

Registration creates every record with `verified = 0`. The flag is set only
when a file is actually verified/downloaded at a later stage (`download` sets
`verified = 1`); `ensure-base` never mutates the flag. Presence of a file in
the installer image is not a verification criterion. Because `ensure-base` is
fresh-cache-only, no re-run can reset the flag after a download. The download
phase's short-circuit leaves the flag untouched too: when a record's pool file
is already present and matches (size + sha256), it is not re-fetched and
`verified` is not changed — only an actual fetch/verify sets the flag. After a
run:

```sql
SELECT count(*) FROM binaries WHERE verified = 1;  -- 0
```

## Notes

- **Batch resolution**: dependencies are resolved once per invocation — the
  whole base set is walked in one batch pass (shared dependency walk), with
  per-name progress (`Resolved X/Y`), per-name commits as safety checkpoints,
  and the build-dependency pass running once for the batch. A full DVD-1 run
  (4,447 names) against the live archive completes in ~13 s. `add` uses the
  same batch engine (a single name is a batch of one).
- **Netinst / minimal images**: below the documented minimum of 1,000 binary
  packages the run prints a warning (`...looks like a netinst or minimal
  image...`) and continues. The full offline DVD installer image (DVD-1) is
  the supported artifact.
- **Damaged images**: an image whose index lists files that are missing from
  its pool produces `Index/image mismatches: N` warnings naming each path; the
  run continues and flags are unaffected.
- **Re-runs / fresh cache**: `ensure-base` is fresh-cache-only — it refuses a
  cache that already contains packages (clear error, exit 1, database
  untouched). Re-ingest by starting from an empty cache (delete the database
  and re-run `init`); an interrupted run is recovered the same way (Phase A
  may have committed partial records, so re-running is refused).
- **Error cases** (exit `1`, database untouched): no `.iso` in `iso_dir`, a
  file that is not a readable Rock Ridge image, no `binary-amd64` indexes, or
  a corrupt `Packages.gz`. Invalid configuration (including an unresolvable
  `iso_dir`) exits `102`.
- **Sources and `binary_to_source` links** arrive through the existing `add`
  resolution flow in Phase B (installer DVDs carry binaries only — no source
  index). The source DVD is not required.
- **Dependency**: `python3-pycdlib` is a system package tracked as part of the
  project's dependency graph (Constitution VIII) so the full stack is
  reproducible offline.
