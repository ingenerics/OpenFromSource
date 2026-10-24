# Data Model: Sync New Packages

**Feature**: [spec.md](spec.md) | **Phase 1** — entities, validation rules, and state transitions.

**Schema stays version 1 — no schema changes** (spec 011 freeze). Everything in this feature is *derived*: the diff generator reads the database (verified records + the system's `sync_mask` bit), the cached archive indexes, and the pool, then writes the spec 010 delivery snapshot; the operator side reads the diff directory and merges it into the operator's existing cache.

## Entities

### Diff cache (derived, generated)

`{binary_cache}/diff/` — produced by the admin-only `package_mgmt sync --system <moniker>`; the minimal incremental payload shipped to the operator.

| Artifact | Content | Producer |
|----------|---------|----------|
| `dists/{release}/{component}/binary-amd64/Packages.gz` | deb822 paragraphs for **all** current binaries with `verified = 1` whose pool file exists — the delivered *and* the pending (new) set — per (release, component) present in the cache; the same complete index set a fresh full `handoff` would emit | `generate_diff` (reuses the handoff generator internals) |
| `dists/{release}/Release` | Same format as the handoff `Release` (`Origin`, `Label`, `Suite`/`Codename`, `Architectures`, `Components`, `Date`, `Valid-Until` +1y, `MD5Sum`/`SHA1`/`SHA256`/`SHA512` of the complete indexes) — **one per release** | `generate_diff` |
| `dists/{release}/Release.gpg` | Armored detached signature (the same cache-local key as the baseline handoff, from `{cache}/handoff/.gnupg`) — **one per release** | `generate_diff` |
| `offline-cache.gpg` | Armored public key of that key (identical to the handoff's) | `generate_diff` |
| `manifest.txt` | One line per indexed file: `new|delivered <sha256> <size> <pool-relative-path>` — `new` = pending binary shipped in `diff/pool/`; `delivered` = already-marked binary whose file must already be in the operator's pool | `generate_diff` |
| `pool/` | **Only the pending binaries' files** (real copies of `{binary_cache}/pool/<path>` — they must physically ship; no symlink) | `generate_diff` |
| `apply.sh` | Operator script: `apply.sh [<cache-dir>]` (optional path, used as-is; default = the diff's parent — the cache the script ships in; no settings parsing) | `generate_diff` (embedded template) |

**Validation rules (FR-001/FR-003/FR-010)**: the pending set is `status = 'current' AND verified = 1 AND (sync_mask & bit) = 0` (spec 010 `pending_count` semantics). A pending binary is included only when its index entry resolves (name + version + architecture, spec 011 lookup) and its pool file exists; otherwise it is reported and excluded and **never marked delivered** (it remains pending for a future diff). The complete index set includes delivered + included-pending binaries whose files exist; an excluded *delivered* binary is a defect state reported like the handoff's unconfirmable handling. The snapshot is written with `mark_delivered(conn, system, shipped_ids)` — exactly the included pending ids (spec 010 semantics; idempotent bitwise OR).

**State transitions**: `sync` is idempotent at the *record* level — a second run after a successful first sees zero pending, produces no new diff, and reports 0 (FR-006). When there is anything pending, the diff directory is (re)generated from the current verified state, so re-running before shipping rebuilds it consistently; the snapshot is taken after generation, marking exactly the shipped set (spec 012 Decision 8 ordering).

### Operator-side applied state (derived, no new files beyond the cache itself)

The spec's "applied-state marker" (FR-011) is realized by the **manifest base set**, not a stored marker:

| Concept | Meaning |
|---------|---------|
| Base set | The `delivered` lines of the incoming `manifest.txt` — current+verified binaries the admin has already marked delivered to the system. The apply checks each listed path exists under the operator's pool before changing anything. |
| Applied state | "The operator's pool contains a file for every binary the admin's snapshot marked delivered." A `delivered` path missing from the pool means an intermediate diff was missed (or the baseline never applied) → the diff is refused unchanged. |
| Advanced state | After a successful apply, the new files are in the pool and the handoff `dists/` present the complete set — the operator is at the new snapshot level, ready for the next diff. |

Transitions: baseline (from spec 012 bootstrap, handoff `dists/` + full pool) → (apply diff N) → pool + complete handoff `dists/` for diff N → … Each apply is idempotent (a `new` file already present with a matching SHA256 is skipped); out-of-order diffs (missing `delivered` file) are refused before any change.

### System cache (existing, operator side, merged by the apply)

- `pool/` — the operator's binary pool; gains the diff's new files (copy → verify → atomic rename). Existing files are never modified or removed.
- `handoff/` — the spec 012 apt source the operator's sources entry points at; the apply replaces `dists/{release}/Release`, `Release.gpg`, and every per-component `Packages.gz` with the diff's complete ones (per-file atomic). `bootstrap.sh`, `offline-cache.gpg`, `pool -> ../pool`, and `.gnupg` are left in place.
- The apt sources entry (`/etc/apt/sources.list.d/openfromsource-cache.list`) is **not** touched by the apply — it keeps pointing at `file:{cache}/handoff`.

### Delivery record (existing, spec 010, written by `sync`)

`systems` monikers + `binaries.sync_mask` bits; the diff operation calls `mark_delivered` with the shipped pending ids, so the record equals the payload (FR-005). No new tables or columns.

## Data Flow

```
binaries (current, verified=1, bit unset) ──┐
cached archive index (Filename, Size, SHA256, ─┤─> generate_diff ─> diff/ (complete Packages.gz,
  Depends, Provides, architecture)           │                     Release, Release.gpg, offline-cache.gpg,
binaries (current, verified=1, bit set) ─────┘                     manifest.txt, base set, pool/{new files},
pool/ files (existence check) ──────────────┘                      apply.sh)
                                                                   └─> mark_delivered(system, shipped_ids)

diff/ ─> apply.sh ─> (base-set check ─> copy+verify new files ─> replace handoff dists ─> apt update)
        ─> operator pool + handoff complete; apt presents prior + new; install works offline
```

No writes into the admin cache beyond the `diff/` output and the snapshot; no schema changes; no new tables.
