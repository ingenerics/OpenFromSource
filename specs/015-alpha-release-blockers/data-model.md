# Data Model: Alpha Release Blockers

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

**Headline constraint: the SQLite schema is frozen (spec 010/011).** None of the six phases change the database
schema. This file documents the state/behavioral semantics each phase introduces over the existing model, plus the
one in-memory-only addition.

---

## Schema

**Unchanged.** Tables `packages`, `binaries`, `sources`, `binary_to_source`, `source_build_deps`, `provides`,
`virtual_resolutions`, `systems` keep their current shape. The phases below operate on existing columns and
in-memory batch state only.

---

## Phase 1 — Complete Runtime Closures (Pre-Depends)

**Entity**: `BinaryPackage` (`lib/model/package.py`) — gains an optional field:

| Field | Type | Meaning |
|-------|------|---------|
| `pre_depends_raw` | `str \| None` | The raw `Pre-Depends` field value (if any) as read from the binary index |

**Relationship**: no table change. `binaries.dependencies` continues to hold the serialized dependency set, now
**merged** from `Depends + Pre-Depends` (deduplicated). The merged set is what the closure walk resolves, so every
hard requirement lands in the cache.

**Generated artifacts**: the handoff/diff `Packages.gz` paragraphs may now carry a `Pre-Depends:` stanza
(synthesized from `pre_depends_raw`) in addition to the existing `Depends:` stanza. The stanza order follows the
Debian control-file convention (`Package`, `Version`, `Architecture`, `Depends`, `Pre-Depends`, `Provides`,
`Filename`, `Size`, `SHA256`).

**Validation rules**: a Pre-Depends target that is also present in `Depends` must appear exactly once in the
merged closure (deduplicate on parse); a Pre-Depends-only target must appear in the closure; version-constrained
Pre-Depends are honored by the existing constraint machinery.

**State transitions**: none (no status/verified changes).

---

## Phase 2 — Order-Independent Source Coverage (resolver cluster)

**In-memory batch state** (not persisted, batch-scoped on `PackageResolver`):

| Field | Type | Meaning |
|-------|------|---------|
| `_batch_levels` | `dict[str, int]` | Minimum runtime-graph level observed for each package name in the current batch |

**Semantics introduced**:
- A binary's source/build-tool support is decided by its **minimum** level (spec FR-003), not its first-visit
  level. Re-encountering a name at a strictly smaller level that is now in scope re-runs the source-registration
  block when the binary has no source yet.
- Cached-virtual resolutions (`virtual_resolutions` rows) no longer skip the source gate — a virtually provided
  binary can gain a source when reached in scope, including across separate runs (FR-004).
- After a segment rollback, `_visited` / `_batch_registered` / `_batch_constraints` are rebuilt from the DB, so
  never-committed names are re-resolvable (FR-005). `_batch_levels` is cleared/rebuilt alongside.

**Validation rules**: the in-scope source set is identical regardless of `Depends`-line ordering; at `-1` behavior
is byte-identical to today (regression-preserving); finite-depth sets stay strict subsets of the `-1` set.

**State transitions**: none to persisted rows (all upserts idempotent as today).

---

## Phase 3 — Safe Diff & Delivery Ordering

**Delivery record semantics** (unchanged schema):

| State | Meaning | Advanced when |
|-------|---------|---------------|
| pending | verified binary not yet shipped to a system | — (created at verify/snapshot) |
| delivered | shipped to a system | **only** on a clean, fully-generated diff (FR-007) |

**Introduced rule**: a `Packages` stanza / manifest entry exists in the generated diff **iff** its pool payload
was copied successfully (FR-006). A failed copy produces no entry and no delivery advance; the failed record
remains pending and the command reports it (`excluded_pending`, exit non-zero).

**State transitions**:
- Copy success → stanza + manifest entry written, id recorded in `shipped_ids`.
- Copy failure → no entry, record excluded (`excluded_pending`), not in `shipped_ids`, delivery not advanced.
- `mark_delivered` runs only after `generate_diff` returns clean (no `excluded_delivered`, no `excluded_pending`).

---

## Phase 4 — Fail Hard on Linkless Media

**Operator-side artifact** (no schema): the shipped `bootstrap.sh` template. Semantics introduced:

| Check | Current | Required |
|-------|---------|----------|
| `handoff/pool` symlink creation | ignored failure | failure ⇒ **fail hard** — non-zero exit, clear message, no broken apt source configured (FR-008) |
| `handoff_complete` | checks `$cache_dir/pool` only | also verifies `handoff/pool` resolves to a directory and per-release `Release`/`Release.gpg` exist (FR-009) |

**Validation rules**: bootstrap never reports success with an unreachable pool; on linkless media it **fails hard**
(non-zero exit, actionable message, no apt source configured) — it must never leave a half-configured apt source
marked successful.

---

## Phase 5 — No-Expiry Repository & Generation-Time Dating

**Generated artifact semantics** (no schema): the `Release` file's `Date` field and the absence of any expiry field.

| Input | Behavior |
|-------|----------|
| default | `Date` = generation time; **no `Valid-Until` field emitted** — apt imposes no expiry when the field is absent (FR-010) |
| `SOURCE_DATE_EPOCH` set (valid) | explicit opt-in: `Date` pinned to that epoch; byte-identical regeneration; still no expiry field (FR-011) |
| `SOURCE_DATE_EPOCH` invalid | warning + generation-time fallback (existing behavior) |

**Validation rules**: the repository never carries an enforced validity deadline; regenerating a handoff always
advances `Date` to the generation time unless `SOURCE_DATE_EPOCH` is explicitly set; a cache of any age is accepted
by apt, and a stale cache is not silently treated as fresh via a frozen cache timestamp.

---

## Phase 6 — Clean Machine-Readable Errors & Green Documented Runner

**Error contract semantics** (no schema): in `--format json` mode, the error stream (stderr) carries **exactly
one** JSON document on failure — no log/diagnostic text mixed in (FR-012). Logs still flow to the log file.

**Test-runner semantics**: the suite's behavior is runner-independent (FR-013) — no documented command is red while
another is green; logging isolation is a test-layer concern, not a product-behavior change in text mode.

---

## Cross-phase notes

- **No new tables/columns/indexes**: the schema freeze is absolute for this feature.
- **Entity additions**: exactly one new optional model field (`BinaryPackage.pre_depends_raw`) and one
  in-memory batch map (`_batch_levels`).
- **Generated-artifact changes**: `Packages.gz` (Pre-Depends stanza), `bootstrap.sh` (link/Release checks),
  `Release` (no enforced expiry; generation-time `Date`).
