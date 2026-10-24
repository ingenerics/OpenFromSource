# Data Model: Download & Cache Integrity

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

The spec 010/011 schema is **frozen** — this feature changes no tables, columns, or migrations. This document
records the **semantic refinements** to existing records and the entities this feature reasons about.

## Schema (frozen, unchanged)

Tables: `packages`, `binaries`, `sources`, `binary_to_source`, `source_build_deps`, `provides`,
`virtual_resolutions`, `systems`. Defined in `src/package_mgmt/lib/model/database.py` (`EXPECTED_SCHEMA_SQL`);
NOT modified by this feature.

## Semantic refinements

### `binaries` — fresh-cache-only registration (Phase 1)

| Column | Current meaning | Refined by this feature |
|--------|-----------------|-------------------------|
| `verified` | `1` = payload confirmed retrievable from the configured archive | **Never reset by base registration** — because `ensure-base` refuses a non-empty cache, no base re-run can set the whole set to 0. Still **reset to 0** on add-time supersede/restore (unchanged resolver semantics). |
| `status` | `'current'` / `'superseded'` | **Unchanged by this feature.** No supersede-aware registration is added: `ensure-base` ingests only into an empty cache, so it can never create a second `current` row for a `(package_name, architecture)`. |

Invariants:
- `download_pending_binaries` iterates `status = 'current'` only (unchanged).
- Handoff (`_verified_records`) and `snapshot`/`sync` pending logic require `verified = 1` — because base
  registration no longer resets verified state (Phase 1), a re-run cannot silently shrink the handoff.

### `sources` — per-run re-check (Phases 3 & 4)

| Column | Refinement |
|--------|------------|
| `verified` | **Un-marked (`0`)** when any index-listed payload file fails re-check (deleted, size mismatch, or checksum mismatch). **Marked (`1`)** only when ALL of a source's index-listed files are present and checksum-verified (existing invariant). |
| `verified` (short-circuit) | A source record stays `verified` unchanged when its files are present-and-valid (no re-download, no flag churn). |

Per-file economy: a re-checked source's **intact** files are never re-fetched; only missing/mismatched files are
added to the download work set (FR-005).

### Payload presence (Phases 2, 3, 4) — not a table

"Present and valid" is derived, not stored: `exists(pool_path) ∧ stat_size == index Size ∧ sha256(file) == index
SHA256`. This derived state drives:
- the **short-circuit** for any record regardless of `verified` (M-7, FR-006);
- the **re-check** of verified records (binaries today, sources after Phase 3);
- the integrity gate after **redirects** (F-16: the final payload is hashed against the same index checksum,
  FR-003).

### Index files (Phase 6) — filesystem-level, not a table

Cached indexes (`dists/…/Packages.gz`, `dists/…/Sources.gz`, `.xz` variants) are a file-level entity with a new
invariant: **a reader never observes a partially-written index**. Written `dest + ".part"` → `os.replace`; a
cached index that cannot be loaded is treated as absent (cache miss → re-fetch).

## State transitions

### Base ingest (Phase 1) — fresh-cache guard

```text
(ensure-base) ── binaries table non-empty ──► refuse (CacheNotEmptyError, exit 1; cache untouched)
             ── binaries table empty ──► register image (verified = 0) + resolve base set
```

Because registration always lands on an empty cache, no record is ever re-registered and no existing
`verified`/`status` state is touched. `verified` changes only via `download` (0 → 1 on successful verify; 1 → 0
on re-check failure).

### Source record, download run (Phase 3)

```text
verified = 1 ── re-check each index-listed file ── all present+valid ──► verified stays 1 (no fetch)
                                       │
                                       └─ any missing/mismatched ──► verified = 0; fetch only those files ──► all verified ──► verified = 1
```

### Index file, ensure_indexes (Phase 6)

```text
cached index unreadable (truncated/corrupt) ──► treated as absent ──► fetch (atomic .part + replace) ──► valid cached index
```

## Validation rules (from requirements)

- **FR-001**: `ensure-base` refuses a non-empty cache (exit 1, cache unmodified); succeeds on a fresh cache.
- **FR-002/003**: redirects followed (≤ 5 hops, loop/missing-target → clear permanent error); final payload still
  checksum-verified.
- **FR-004/005**: verified sources re-checked each run; only missing/corrupt files re-fetched.
- **FR-006**: present-and-valid payload never re-downloaded, regardless of `verified`.
- **FR-007**: Sources indexes fetched/loaded only when `config.fetch_source` is true — and the gate is per-run on
  the live config (cache-miss fetch), so a later switch to a source-enabled profile re-fetches the
  never-downloaded Sources indexes.
- **FR-008/009**: atomic index updates; corrupt cached index treated as a cache miss and re-fetched.

## Relationship to existing tables

Unchanged: `binary_to_source` (exact source linkage, spec 016), `source_build_deps`, `provides`,
`virtual_resolutions`. This feature adds no rows/columns to them.
