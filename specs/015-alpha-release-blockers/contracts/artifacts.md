# Operator Artifact Contracts: Alpha Release Blockers

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md)

Three phases change the **generated operator artifacts** (what the admin's `handoff`/`sync` produce and the
operator consumes). This file documents the resulting artifact contracts. Specs 012/013 govern the overall handoff
and diff; these contracts are additive.

---

## Phase 1 — `Packages.gz` carries a faithful `Pre-Depends:` stanza (FR-001/FR-002)

**Artifact**: handoff/diff `dists/<release>/<component>/binary-amd64/Packages.gz` paragraphs.

**Contract.** A binary index entry with a `Pre-Depends` field MUST be emitted with a `Pre-Depends:` stanza in the
generated `Packages.gz`, preserving the Debian control-field semantics (dpkg installs-and-configures these before
the package). The field is emitted **in addition to** the merged `Depends:` stanza.

Field order in a paragraph:

```text
Package: <name>
Version: <version>
Architecture: <arch>
Multi-Arch: <value>                 # only when present in the source index (Phase 9)
Depends: <depends + pre-depends, deduplicated>
Pre-Depends: <pre-depends>          # only when present in the source index
Provides: <provides>                # only when present
Filename: <pool path>
Size: <bytes>
SHA256: <hex>
```

**Assertions**:
- A package whose source index declares `Pre-Depends` appears with a `Pre-Depends:` stanza in the generated
  `Packages.gz` (both `handoff` and `sync`/`diff`).
- `apt update` against the generated repo succeeds and `apt-get -s install` of such a package resolves its
  Pre-Depends target from the cache.

---

## Phase 4 — `bootstrap.sh` fails hard when the pool is unreachable (FR-008/FR-009)

**Artifact**: `handoff/bootstrap.sh`.

**Contract.**
1. `ensure_pool_link` MUST detect symlink-creation failure and, when the pool cannot be reached through the
   handoff directory, **fail the bootstrap hard** — non-zero exit with a clear, actionable message, without
   configuring a broken apt source. There is no fallback.
2. `handoff_complete` MUST verify, in addition to the existing checks, that the `handoff/pool` link resolves to a
   directory **and** that each configured release has its `Release` + `Release.gpg` present, before reporting
   success.

**Assertions**:
- On media that cannot carry symlinks (FAT), running `bootstrap.sh` **fails hard** — non-zero exit, clear message,
  no apt source configured — and never prints success while the pool is unreachable.
- If any required artifact (link, `Release`, `Release.gpg`, per-component `Packages.gz`) is missing, the
  completion check reports non-zero.
- A valid, link-capable cache still bootstraps cleanly (regression).

---

## Phase 5 — `Release` carries no expiry; `Date` anchored to generation time (FR-010/FR-011)

**Artifact**: handoff/diff `dists/<release>/Release`.

**Contract.**

| Case | `Date` / expiry |
|------|-----------------|
| default | `Date` = generation time; **no `Valid-Until` field** — apt imposes no expiry when the field is absent |
| `SOURCE_DATE_EPOCH` set (valid) | `Date` pinned to that epoch (reproducible, byte-identical regeneration); still no `Valid-Until` |
| `SOURCE_DATE_EPOCH` invalid | warning + generation-time fallback |

**Assertions**:
- A generated `Release` contains a `Date:` field reflecting the regeneration time and **no** `Valid-Until:` field.
- Regenerating a handoff yields a later `Date` than the previous run (generation time, not the cache mtime).
- A handoff generated long ago is accepted by apt (`apt update` succeeds, no expiry error) — the repository imposes
  no deadline.
- With `SOURCE_DATE_EPOCH` set explicitly, regeneration is byte-identical across runs (opt-in reproducibility).
- Without `SOURCE_DATE_EPOCH`, regeneration is **not** byte-identical (the default is generation time) — this is
  the intended behavior change.

---

## Phase 9 — `Multi-Arch` preserved for `name:any` resolution (new)

**Artifact**: handoff/diff `dists/<release>/<component>/binary-amd64/Packages.gz` paragraphs.

**Contract.** When a binary index entry declares `Multi-Arch`, the generated `Packages.gz` paragraph MUST carry the
`Multi-Arch:` stanza (emitted between `Architecture:` and `Depends:`). This is an **additive extension** of the
Phase 1 field order above — no existing field is reordered or contradicted.

**Rationale (discovered during the Phase 9 release gate).** apt resolves a `name:any` dependency (e.g. `perl:any`,
used by `vifm` and thousands of packages) only when the providing entry advertises `Multi-Arch`. The pre-fix writer
dropped the field, so the operator's `apt-get -s install` of any such package failed against the offline handoff
index even though the provider was present in the cache. The data comes straight from the cached archive index at
`handoff`/`sync` time (no schema change).

**Assertions**:
- A package whose source index carries `Multi-Arch` appears with a `Multi-Arch:` stanza in the generated
  `Packages.gz` (both `handoff` and `sync`/`diff`).
- `apt-get -s install` of a package with a `name:any` dependency succeeds against the offline cache when the
  provider is present (verified live: `vifm` → `perl:any` → `perl` with `Multi-Arch`).
- A paragraph without `Multi-Arch` in the source index emits no such stanza (unchanged behavior).

---

## Invariants (unchanged, reasserted)

- The operator repo remains GPG-signed with the cache-local key and passes apt's checksum gate.
- `handoff`/`sync` continue to produce only verified, current records.
