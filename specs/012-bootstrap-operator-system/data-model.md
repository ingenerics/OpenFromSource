# Data Model: Bootstrap Operator System

**Feature**: [spec.md](spec.md) | **Phase 1** — entities, validation rules, and state transitions.

**Schema stays version 1 — no schema changes** (spec 011 freeze). Everything in this feature is *derived*: the handoff generator reads the database and the cached archive indexes; the operator side only reads the handoff directory and writes apt configuration on the operator system.

## Entities

### Handoff artifact set (derived, generated)

`{binary_cache}/handoff/` — produced by the admin-only `handoff` command; the handoff payload shipped to the operator alongside the ISO.

| Artifact | Content | Producer |
|----------|---------|----------|
| `dists/{release}/{component}/binary-amd64/Packages.gz` | deb822 paragraphs for exactly the current binaries with `verified = 1` whose pool file exists, per (release, component) present in the cache — one index set per release | `handoff` generator (filtered from the cached archive index via the DB) |
| `dists/{release}/Release` | `Origin`, `Label`, `Suite`/`Codename` (= release), `Architectures: amd64`, `Components`, `Date`, `Valid-Until` (+1 year), `MD5Sum`/`SHA1`/`SHA256`/`SHA512` of the filtered indexes (paths relative to `dists/{release}/`) — **one per release**, so multi-release caches are served fully | `handoff` generator |
| `dists/{release}/Release.gpg` | Armored detached signature of that release's `Release` (cache-local key; one signature per release) | `handoff` generator |
| `offline-cache.gpg` | Armored public key of the cache-local signing key (one key, signs every release) | `handoff` generator |
| `bootstrap.sh` | Operator script: `bootstrap.sh [<cache-dir>] [--remove]` (optional path argument, used as-is when given; default = the cache the script ships in — the handoff's parent; no settings parsing). Writes **one `deb` line per release** | `handoff` generator (emitted from an embedded template) |
| `pool` | Symlink to `../pool` (the verified binary pool) | `handoff` generator; re-created by the script if missing |

**Validation rules (FR-004)**: the generator only includes records that are `status = 'current'` AND `verified = 1` AND whose index entry resolves (name + version + architecture, spec 011 lookup) AND whose pool file exists. Records failing any check are reported and excluded; if the resulting index for a (release, component) would be empty, no artifacts for that component are generated.

**State transitions**: `handoff` is idempotent — re-running regenerates the whole handoff directory (new key, new signatures, refreshed `Valid-Until`). The handoff has no incremental state; it is always a snapshot of the cache's current verified state.

### Sources entry (operator system, derived)

`/etc/apt/sources.list.d/openfromsource-cache.list` — **one `deb` line per
release** the handoff serves:

```
deb [signed-by={cache}/handoff/offline-cache.gpg] file:{cache}/handoff {release} {present components}
deb [signed-by={cache}/handoff/offline-cache.gpg] file:{cache}/handoff {other-release} {present components}
```

- `{cache}` is the binary cache directory: by default the handoff's parent directory (where the script ships), overridable with an optional path argument to `bootstrap.sh` (used as-is when given — no settings parsing).
- Written atomically, only after validation succeeds (FR-006); overwrites only its own prior content (FR-005); deleted entirely by `--remove` (FR-008).
- Existing sources (installer DVD) are never touched (spec assumption: cache source complements them).

### Operator-side bootstrap script contract state

| State | Meaning |
|-------|---------|
| Unconfigured | No entry in `sources.list.d`; system apt as the installer left it |
| Configured | Entry present; `apt update` succeeded at bootstrap time; `apt install` resolves from cache + DVD |
| Removed | Entry deleted; system apt as before bootstrap |

Transitions: unconfigured → (run script) → configured; configured → (re-run script) → configured (idempotent); configured → (`bootstrap.sh [<cache-dir>] --remove`) → unconfigured. Failures before the entry is written leave no trace; an `apt update` failure after the write leaves the completed entry in place for inspection (FR-006); runs never leave a partially written entry.

## Data Flow

```
binaries (verified=1, current) ─┐
cached archive index (Filename,  ├─> handoff generator ─> handoff/ (Packages.gz, Release, Release.gpg,
  Size, SHA256, Depends, Provides,                        offline-cache.gpg, bootstrap.sh, pool->../pool)
  architecture) ────────────────┘
pool/ files (existence check) ───┘

handoff/ ─> bootstrap.sh ─> /etc/apt/sources.list.d/openfromsource-cache.list ─> apt update/install
```

No writes into the cache from the operator side; no schema changes; no new tables.

**Delivery record (spec 010, written by `handoff`)**: `handoff --system <moniker>`
validates the moniker, generates the handoff artifacts, then sets the moniker's
bit in `binaries.sync_mask` for **exactly the binaries the handoff actually
delivers** (across every release served). Binaries excluded from the offered
set — missing pool file, unconfirmable index entry — are never marked, so the
delivery record equals the physical payload (FR-003). The generated handoff is
the snapshot's physical payload; the DB record documents which state was
delivered to which system.
