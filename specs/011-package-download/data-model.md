# Data Model: Package Download

**Schema version stays 1 — no schema changes** (spec 010 verified-flag policy;
the schema freezes after this feature). This feature only *reads* the existing
tables and *sets* two flags.

## Entity List

### DownloadSet (derived, no new table)

The work plan of a `download` run, computed from the database at run start
(spec FR-001, research Decision 6):

| Member | Query | Purpose |
|--------|-------|---------|
| Pending binaries | `SELECT id, package_name, version, release, component FROM binaries WHERE status = 'current' AND verified = 0` | Binary files to download |
| Pending sources | `SELECT s.id, s.package_name, s.source_version, s.release, s.component FROM sources s WHERE s.verified = 0 AND EXISTS (SELECT 1 FROM binary_to_source bts JOIN binaries b ON b.id = bts.binary_id WHERE bts.source_id = s.id AND b.status = 'current')` (only when `fetch_source` is enabled) | Source artifacts to download |

Deduplication (FR-010): distinct destination paths across the set collapse to
one download, even when several binaries map to the same file.

**Verified-record re-check (US3 AS3)**: a `verified = 1` record whose pool
file is missing or whose stat size no longer matches the index `Size` is
re-hashed; on a checksum mismatch it is treated as pending for that run — the
file is re-fetched and re-verified (or the record is reset to 0 if the
re-fetch fails). The size check short-circuits the O(file) re-hash for the
common case: a same-size file is trusted on re-run (presence alone is still
never treated as verified on first download). An untouched verified record is
never re-downloaded (FR-005).

### IndexMetadata (derived, no new table)

Per-file facts resolved from the **cached archive indexes** at run time
(research Decision 1):

| Source | Fields |
|--------|--------|
| `Packages.gz` (per release/component) | `Filename` (pool path), `Size`, `SHA256` — keyed by `(Package, Version)` |
| `Sources.gz` (per release/component) | `Directory` (pool dir), `Checksums-Sha256` (`sha256 size name` per file) — keyed by `Package` |

Lookup key for a binary: its own `(release, component, package_name, version)`
— the index of exactly the suite it was registered from. A missing entry or a
missing checksum makes the record **unconfirmable** → per-record error, never
a guessed download (FR-002, FR-012).

### Pool layout (existing directories, now written to)

| Content | Destination | URL |
|---------|-------------|-----|
| Binary files | `{binary_cache}/{Filename}` (e.g. `{base}/binary/pool/main/v/vifm/vifm_1.0-2_amd64.deb`) | `{mirror}/{Filename}` |
| Source files | `{source_cache}/{Directory}/{name}` (e.g. `{base}/source/pool/main/v/vifm/vifm_1.0-2.dsc`) | `{mirror}/{Directory}/{name}` |

Both trees are created by `init` (initializer.py); the downloader creates
intermediate directories as needed. Binary and source files live in the same
pool on the mirror (Debian layout), but in separate trees in the cache.

## State Transitions

### binaries.verified

```
pending (current, verified = 0)
   │  download file → *.part → sha256 vs index
   │  match: os.replace into pool, commit verified = 1
   │  mismatch: remove *.part, report expected/actual, stay 0
   ▼
verified = 1  (file present + checksum matches the current index entry)
```

- Written per file, committed per file (interrupted runs converge; FR-011).
- A later re-registration may reset `verified = 0` (existing upsert semantics);
  the file remains in the pool and a re-run re-verifies/re-downloads it.
- `sync_mask` (spec 010) is untouched by this feature; snapshot marking now has
  verified files to gate on.

### sources.verified

```
pending (verified = 0)
   │  all of the source's files downloaded + verified (from Checksums-Sha256)
   ▼
verified = 1  (all files present and verified)
```

- All-or-nothing at the source level (FR-006/FR-007): any missing or
  unverifiable file leaves the source at 0; failures are per file.
- Only sources linked to current binaries are in the set (FR-009); superseded
  history never triggers downloads.
- `fetch_source = false` → no source files are fetched and no error is raised
  (FR-008) — the configuration's own choice is respected.

### Download failure handling (no state)

- Transient HTTP failures (`429`, `503`, connection reset): retried with
  exponential backoff (1 s × 2, 30 s cap, jitter, ≤5 attempts), honoring
  `Retry-After`; repeated refusal → per-file failure.
- Permanent failures (`404` and other statuses): per-file failure, no retry.
- Failures never block other files; the run reports each with a non-zero exit
  (FR-013).

## Verification Contract

`verified = 1` means exactly: the file at the index-specified pool path exists
locally and its SHA-256 equals the index's `SHA256` / `Checksums-Sha256` entry
for that file (spec FR-003/FR-006). Archive-level GPG signature verification
remains at index-fetch time; no per-file signing is re-verified (Assumptions).

## Schema Freeze

This feature makes **no schema changes**. After its verified set-points are
implemented, the schema is stable: v1 is final for the foreseeable future, and
the delete-and-rebuild policy (spec 010, FR-012/FR-013) remains the sanctioned
development-time migration path if anything ever changes.
