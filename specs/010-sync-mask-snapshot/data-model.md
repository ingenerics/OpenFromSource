# Data Model: Sync Mask & Snapshot

**Schema version stays 1** — no `SCHEMA_VERSION` bump (development policy, see [Migration Policy](#migration-policy-delete-and-rebuild)). The objects below land in `EXPECTED_SCHEMA_SQL` in place.

## Entity List

### Systems (new table)

The registry of sync targets. An operator may own several systems; the table is persona-agnostic.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` | Internal identity |
| `name` | `TEXT` | `NOT NULL` | Moniker — any word the administrator chooses; unique among **active** systems (`uq_systems_active_name` partial unique index on `name WHERE retired = 0`) |
| `bit` | `INTEGER` | `NOT NULL UNIQUE` | Opaque mask bit — auto-assigned (next power of two above the highest ever issued; first system gets 2); never shown in any output; never reused |
| `note` | `TEXT` | nullable | Optional free text (e.g. which operator owns the system) |
| `retired` | `INTEGER` | `NOT NULL DEFAULT 0` | Soft-delete flag — `1` means removed: invisible to `systems list` and `snapshot`, deliveries kept untouched, bit never reissued |

```sql
CREATE TABLE IF NOT EXISTS systems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    bit INTEGER NOT NULL UNIQUE,
    note TEXT,
    retired INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_systems_active_name
    ON systems(name) WHERE retired = 0;
```

**Bit assignment rule** (FR-002): `new_bit = COALESCE(MAX(bit), 1) * 2` over **all** rows, retired included. Retired rows keep their bit in `MAX(bit)`, so the next assignment is always a fresh power of two. Re-adding a retired moniker creates a new row with a fresh bit and behaves as never-synced. Ceiling: 62 systems (`2^63` and beyond are rejected with a clear error; `MAX(bit)` must never exceed `2^62`).

**Registry operations** (all single-row, single-statement):

| Operation | SQL shape | Notes |
|-----------|-----------|-------|
| add | `INSERT (name, bit, note) VALUES (?, ?, ?)` | bit computed as above; duplicate active name → error |
| rename | `UPDATE systems SET name = ? WHERE id = ? AND retired = 0` | active-only; duplicate target name → error; bit and marks unchanged |
| remove | `UPDATE systems SET retired = 1 WHERE name = ? AND retired = 0` | soft-delete; unknown/retired name → error |
| list | `SELECT name, note FROM systems WHERE retired = 0 ORDER BY id` | joined with the delivered count per system |

### Binaries (existing table — extended in place)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `sync_mask` | `INTEGER` | `NOT NULL DEFAULT 0` | Delivery record — bit `b` set ⇔ the binary's file has been delivered to the system owning bit `b` |

**Invariant (FR-006)**: `sync_mask` is written by exactly one operation — the snapshot mark (`UPDATE binaries SET sync_mask = sync_mask | ? WHERE status = 'current' AND verified = 1`). Nothing else ever writes it: not `upsert_binary`, not supersede, not registration. New rows default to 0 — undelivered.

### SyncState (derived — no new table)

The set "current, verified binaries not yet delivered to system X" — the **pending set** between two points in time:

```sql
SELECT b.id, b.package_name, b.version
FROM binaries b
WHERE b.status = 'current' AND b.verified = 1 AND (b.sync_mask & ?) = 0;
```

`?` is the system's bit. This single query is the contract every future sync flow (delta export, bootstrap, staged syncs) composes: *copy the pending set, then snapshot*. It is deliberately not stored — it is always derivable from the current mask. Unverified binaries never qualify: their files do not exist locally, so they are a pending-*download* concern of the future download spec, not a pending-delivery one.

## Semantics

### Snapshot (pure assertion — FR-007)

1. Resolve the system by moniker; active rows only. Unknown or retired moniker → error, exit non-zero, nothing modified (FR-010).
2. `--dry-run`: count `current` and verified binaries with the system's bit unset; print the count; exit 0 (FR-008).
3. Real run: `UPDATE binaries SET sync_mask = sync_mask | ? WHERE status = 'current' AND verified = 1` — one atomic statement, OR semantics (marks accumulate across snapshots), idempotent (FR-009). Print the marked count.

Superseded and unverified binaries are never marked — a snapshot records exactly the delivered current state at its moment of execution. Mark-after-copy ordering is the administrator's flow: marking happens only after a successful delivery (spec Assumptions; the future export command enforces copy → verify → snapshot).

### Verified-flag interaction (read-only gate)

This feature never writes `verified`; it reads it as the delivery gate. A binary qualifies for marking only when `status = 'current' AND verified = 1` — the file must actually exist locally and have been integrity-confirmed before the record may claim it was delivered. Current-but-unverified binaries are pending download, not pending delivery; the future download spec sets the flag, and the future export spec's pending query (above) inherits the same gate. A later re-registration may reset `verified = 0` (existing upsert semantics) — `sync_mask` is unaffected: the file identity (version) is unchanged and the delivery record stands. Future download/verification features do not read or write `sync_mask`.

## Migration Policy (delete-and-rebuild)

**Documented decision (FR-012/FR-013)**: During development, schema changes land in place at version 1 and stale databases are rebuilt — no migrations, no version bumps.

- The whole cache is derivable: `init` (directories + empty DB) → `ensure-base` (DVD image index) → `add --file <manifest>` (archive metadata). A rebuild is a re-run of the administrator's own manifests.
- `initialize_database` verifies the expected objects exist even when `PRAGMA user_version` matches: if the `systems` table or the `binaries.sync_mask` column is absent, it fails with `EXIT_SCHEMA_MISMATCH` (101) and a message describing the rebuild procedure, instead of failing obscurely at query time.
- The version bump is reserved for stable releases / databases that must survive in the field; that decision is revisited when a release is cut.

## State Transitions

### Binaries.status — supersede fix (FR-011)

The current resolver has a hole: re-requesting a version whose row exists as `superseded` leaves **zero** current binaries (the existing current row is superseded, then the re-insert hits `ON CONFLICT` and the update never restores status).

**Fix**: `upsert_binary`'s `ON CONFLICT ... DO UPDATE` also sets `status = 'current'`. Re-requesting a superseded version restores it as the single current binary; the previously current version becomes superseded. Last-writer-wins, consistent with every other resolution path. `sync_mask` is untouched (invariant above).

```
requested v2.0 (current) ── re-request v1.0 ──> v1.0 current, v2.0 superseded
```

### sync_mask

```
binary registered ──────────> sync_mask = 0            (never auto-marked)
snapshot for system X ───────> sync_mask |= bit(X)     (current AND verified only)
new version supersedes ──────> new row, sync_mask = 0  (pending again)
```

## Verified-Flag Policy (schema decision)

**`verified` exists on exactly two tables: `binaries` and `sources`.** It was
removed from `packages`, `binary_to_source`, and `source_build_deps`; nothing
else changed.

| Table | `verified` | Meaning / rationale |
|-------|-----------|---------------------|
| `binaries` | kept | File present locally + checksum matches the current index entry (download gate; snapshot marks `current AND verified` only) |
| `sources` | kept | All of the source's files present (set-points defined by the download spec) |
| `packages` | **removed** | Was reset to 0 on every re-`add` (`upsert_package` conflict) and unreadable as a trust signal; "all current files verified" is derived from `binaries` and cannot drift |
| `binary_to_source` | **removed** | Never written by any operation; link existence is the fact |
| `source_build_deps` | **removed** | Never written by any operation; link existence is the fact |

The download-time index-read design (DB = state + graph; cached `Packages.gz`
/ `Sources.gz` = per-file metadata: filename, size, sha256) means the file
records need no checksum columns; `verified` remains purely a state flag on
the two record tables. Rationale and rejected alternatives: research.md
Decision 9. This policy is part of the schema freeze.
