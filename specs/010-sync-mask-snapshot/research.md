# Research: Sync Mask & Snapshot

**Phase 0 output** — consolidates the design decisions from the specification brainstorming session. No open unknowns: every decision below was made deliberately during design, and each is recorded with its rationale and rejected alternatives so the reasoning survives into implementation.

## Decision 1: Delivery record granularity — `binaries`, not `packages`

- **Decision**: The delivery record (`sync_mask`) lives on the `binaries` table, one column covering all systems at once via bit-coded integer.
- **Rationale**: The unit actually shipped to a system is a `.deb` file — exactly one `binaries` row. `packages.name` is stable while its current binary changes on supersede; a name-level record would silently claim "delivered" after a version bump unless a hidden reset rule existed. On `binaries`, every new row naturally starts at 0 (undelivered), superseded rows keep their history (the system really did receive v1), and no reset hook is needed anywhere.
- **Alternatives considered**: Name-level mask on `packages` (original proposal) — rejected: requires a "clear on supersede" hook coupled to the resolver, and loses per-version history.

## Decision 2: Bitmask vs normalized join table

- **Decision**: A single bitmask column; each system owns one bit.
- **Rationale**: Compact (one column), atomic to update (`UPDATE ... SET sync_mask = sync_mask | ?`), and trivially queryable for the diff (`(sync_mask & ?) = 0`). Three systems are the current need; the ceiling (62 systems on a signed 64-bit `INTEGER`) is a clear error, far beyond realistic use.
- **Alternatives considered**: `binary_sync(binary_id, system_id)` join table — rejected: more rows, multi-statement snapshots, and no benefit at this scale; it remains the documented upgrade path if the ceiling is ever hit, with an identical diff contract.

## Decision 3: Registry shape — `systems` table with soft-delete

- **Decision**: A `systems` table: `name` (moniker, unique among active systems via a partial unique index), `bit` (unique, auto-assigned), `note`, `retired` flag. Removal is a soft-delete (`retired = 1`).
- **Rationale**: Bits are assigned once and never reused (spec FR-002). A hard DELETE would break monotonic assignment (`MAX(bit)` would forget retired bits) and a re-added moniker could silently inherit a retired system's deliveries. Soft-delete keeps retired rows in `MAX(bit)` — the next assignment is always a fresh power of two — while `snapshot`/`systems list` only consider active rows. Re-adding a retired moniker creates a new row with a fresh bit: conservatively appears never-synced (spec US1 AS4).
- **Alternatives considered**: Hard delete + a `meta` table tracking the max-ever-issued bit — rejected: two moving parts where one flag suffices; config-file registry — rejected: no FK/identity, and the registry must survive in the DB that ships with the cache.

## Decision 4: Bit assignment — auto-assigned, opaque, monotonic

- **Decision**: The CLI assigns the next power of two above the highest bit ever issued (first system gets 2); the value is never shown in any output. The moniker is the only handle.
- **Rationale**: Satisfies the original requirement ("this hash won't be exposed, a human readable moniker should be used instead") by construction, and removes the class of admin errors (duplicate, reordered, or reused bits) that a manual assignment UI would invite.
- **Alternatives considered**: Manual `--bit` flag — rejected: exposes and invites the reuse hazard; the spec's example numbering (2/4/8) is exactly what auto-assignment produces.

## Decision 5: Snapshot semantics — pure assertion, mark-after-copy, verified gate

- **Decision**: `snapshot` marks exactly the binaries that are `status = 'current'` **and verified** for the named system, in one atomic `UPDATE` with OR semantics. It never copies, moves, or verifies files. Dry-run reports the pending count read-only. Idempotent by construction (OR).
- **Rationale**: The mask is a monotonic membership record; OR preserves marks from earlier snapshots while adding the new one. Mark-after-copy is the only safe ordering — marking before delivery would lose the delta on a failed copy, and the delta cannot be reconstructed (no timestamps, no snapshot history — Decision 6). The verified gate (resolved post-planning review): the record may only claim delivery of files that actually exist locally — an unverified binary was never downloaded, so marking it "delivered" would poison the record; unverified binaries are a pending-*download* concern of the future download spec, not a pending-delivery one. The feature reads `verified` and never writes it.
- **Alternatives considered**: Snapshot as a stored object with `created_at` — rejected (Decision 6); snapshot-before-copy as the blessed flow — rejected: unsafe, delta-losing; marking all current binaries regardless of `verified` — rejected: would mark files that do not exist locally.

## Decision 6: No snapshots table, no timestamps

- **Decision**: The mask is the record. There is no `snapshots` history table and no timestamp column anywhere.
- **Rationale**: A snapshot row with `(name, operator, created_at, note)` would contain none of the covered set — the mask grows monotonically, so the set at any past time is not reconstructible from a date. Storing the actual set (`snapshot_binaries` join table) duplicates the mask exactly. Delivery manifests (the concrete list of shipped files) are a future export-command artifact written into the destination folder — files, not DB rows. Tinkerers who want `created_at` add a column in five minutes; locking the schema for a decorative date is a worse trade.
- **Alternatives considered**: `snapshots` + `snapshot_binaries` — rejected: redundant representation of state the mask already holds.

## Decision 7: Schema versioning — stay at v1, delete-and-rebuild

- **Decision**: `SCHEMA_VERSION` stays 1 during development. New objects land in `EXPECTED_SCHEMA_SQL` in place. `db_init` gains a stale-object check: when the version matches but the new objects are absent (a pre-feature development database), fail with a clear rebuild instruction instead of an obscure query-time error.
- **Rationale**: The whole cache is derivable from indexes + manifests (`ensure-base` + `add --file`), so rebuild is cheap and migrations are dead weight during development. The version bump is reserved for stable releases / shipped databases; the policy is documented in data-model.md and revisitable before implementation.
- **Alternatives considered**: `SCHEMA_VERSION = 2` + migration runner — rejected: machinery with no callers at this stage; silent acceptance of stale DBs — rejected: fails obscurely mid-command.

## Decision 8: Supersede fix — `upsert_binary` restores `current`

- **Decision**: In `upsert_binary`'s `ON CONFLICT` update, `status` is restored to `'current'`. Re-requesting a version whose row exists as `superseded` (the current code path leaves *zero* current binaries) now leaves exactly one.
- **Rationale**: Snapshot marks `WHERE status = 'current' AND verified = 1`; a package in the broken state would silently never be marked and would be missing from every delta. The fix preserves last-writer-wins semantics — the re-requested version wins, exactly as every other resolution path behaves.
- **Alternatives considered**: Guarding the snapshot query differently (e.g. marking "the latest binary per package") — rejected: papers over the state-machine bug instead of fixing it, and changes the diff contract.

## Decision 9: Verified-flag policy — only `binaries` and `sources` carry it

- **Decision**: `verified` is dropped from `packages`, `binary_to_source`, and `source_build_deps`; it remains only on `binaries` and `sources`. Everything else stays as-is.
- **Rationale** (schema-stabilization review): `packages.verified` was reset to 0 on every re-`add` of a name (`upsert_package` conflict path) and carried no meaning a reader could trust — "all current files verified" is derivable from `binaries` and can't drift. The link-table flags (`binary_to_source.verified`, `source_build_deps.verified`) were never written by any operation; link existence is the fact that matters, and per-file verification state lives on the record tables. Keeping `verified` on `binaries` and `sources` preserves the download gate the constitution requires (a file record must be confirmed retrievable before download proceeds).
- **Consequence**: The record-level verification gate for downloads is defined by `binaries.verified` (binary file present + checksum matches the current index entry) and `sources.verified` (all of the source's files present, per the download spec). This is the decision record for the schema freeze.
- **Alternatives considered**: Defining set-points for the link-table flags (e.g. `binary_to_source.verified` = "the linked source is fully fetched") — rejected: redundant with `sources.verified`, and the download spec would have to maintain two sources of truth; keeping `packages.verified` with redefined semantics — rejected: derived-from-`binaries` state is strictly more reliable.
