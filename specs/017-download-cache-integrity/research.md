# Research: Download & Cache Integrity

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

Scope source: `review/04_alpha_summary/02_high_priority_followups.md` entries H-2, M-3, F-16, F-17, M-7, M-4,
F-23 (the "ensure-base" and "Download / verify / operator track" clusters). H-2 and M-3 are resolved **by
constraint** (Decision 1) rather than by registration rework; F-16/M-4/F-17/M-7/F-23 are resolved behaviorally.
No clarifications pending (spec "Clarifications" section).

---

## Decision 1 — `ensure-base` is fresh-cache-only; a guard resolves H-2 and M-3 by constraint (H-2, M-3)

**Decision**: `ensure_base` refuses to run on a non-empty cache. Before any registration, a single
`SELECT 1 FROM binaries LIMIT 1` check raises a clear `CacheNotEmptyError` (exit 1) telling the administrator to
start from an empty cache (delete the database and re-run `init`) to ingest an installer image. The intended
workflow — the graybeard profile, the documented `ensure-base` configuration — ingests the image exactly once
into an empty cache (`init` → `ensure-base` → `verify` → `handoff`), so the guard never fires in the supported
flow and costs a single LIMIT-1 query.

**Rationale**: Both review items are *re-run* failure modes that only exist when `ensure-base` runs over a cache
that already holds records:
- **H-2** (`resolver.py:74` `ON CONFLICT ... SET verified = 0`, hit by `ensure_base.py:101-115`): a re-run
  resets the whole base set to unverified → forced full re-download, shrinking handoff. With a fresh cache there
  is no existing `verified` state to reset.
- **M-3** (Phase A bypasses the add-time supersede): a re-run over a cache holding a different current version
  creates a second `current` row. With a fresh cache there is no pre-existing current version to conflict with.

Constraining *when the command is called* is strictly simpler than adding `preserve_verified` flag plumbing and
supersede-aware registration (`versions.version_compare` + `python-debian` dependency), and it matches how the
graybeard persona actually uses the tool. The resolver's add-time reset-on-supersede semantics (load-bearing for
restore) are untouched.

**Alternatives considered**:
- *`preserve_verified` flag on `upsert_binary` (H-2) + supersede-aware Phase A (M-3)* — rejected: two behavioral
  changes, a new `python-debian` dependency, and multiple new code paths to preserve the exact scenario the
  guard eliminates. Over-engineering for a routine path that is documented to run once.
- *Docs-only constraint (no guard)* — rejected: a silent re-run on a populated cache would keep the old,
  damaging H-2/M-3 behavior with no diagnostic. The guard makes the constraint real and self-explaining.

---

## Decision 2 — Redirects followed inside `_PooledOpener`, bounded at 5 hops, final response returned (F-16)

**Decision**: In `downloader._PooledOpener.__call__`, when the response status is `301/302/303/307/308`, read the
`Location` header, resolve via `urllib.parse.urljoin(url, location)`, and re-issue the GET against the new URL —
tracking visited URLs to reject loops and capping the chain at `MAX_REDIRECTS = 5`. A `Location`-less redirect or
an over-limit chain raises a clear `urllib.error.HTTPError`/`URLError` (permanent — the retry layer does not
back off). Only the final non-3xx response is returned, so `_fetch_to_part` hashes the final payload and the
integrity gate is unchanged.

**Rationale**: The bug is `_PooledOpener` treating a 3xx as a 2xx body (raises only for `status >= 400`), so a
redirect body is hashed and fails as a permanent, non-retried checksum mismatch. Fixing it at the opener keeps
one place that knows the keep-alive pool and lets every payload request (binary + source) inherit redirect
support. Per-origin pooling is naturally handled by the existing `(scheme, host, port)` key — a cross-host
redirect opens a fresh pooled connection. The spec's hop bound (~5, "industry-standard") matches the review's
"cap ~5 hops".

**Alternatives considered**:
- *`urllib`'s built-in `HTTPRedirectHandler`* — rejected: the codebase deliberately replaces `urlopen` with a
  keep-alive `_PooledOpener` (apt-calibrated politeness); the built-in handler would bypass pooling and its
  loop/hoop diagnostics don't map to the desired clear permanent errors.
- *Redirect handling in `_fetch_to_part`* — rejected: it doesn't own the pooled connection and would have to
  re-open per hop, losing keep-alive across same-origin hops.

---

## Decision 3 — Source re-check mirrors the binary phase exactly (F-17)

**Decision**: Restructure `download_pending_sources` to run a per-run re-check over **verified** sources: for each
index-listed file, `existence → stat size → sha256`; on any failure, un-mark the source (`verified = 0`) and add
exactly the missing/mismatched files to the re-fetch work set. Intact files of that source are not re-fetched
(per-file skip). Reuse `_stat_size_matches` + `_file_sha256` (the binary phase's helpers).

**Rationale**: The binary phase already does this (`downloader.py:339-344` re-check path); the source phase's
`pending_sources` selects only `verified = 0` and nothing ever re-examines verified source payloads — a
deleted/corrupt tarball is silently ignored. Mirroring the binary phase reuses tested helpers, closes the
silent hole, and the per-file skip satisfies FR-005 (a multi-file source with one missing file re-fetches only
that file). A source is marked verified only when ALL of its files are present and checksum-verified (existing
invariant, retained).

**Alternatives considered**:
- *Re-check only unverified sources more aggressively* — rejected: that leaves the verified-source hole open
  (the whole point of F-17).
- *Whole-source re-verify (all files re-hashed every run)* — rejected: acceptable for correctness but the
  `stat`-size short-circuit (skip the O(file) re-hash when size matches) is the existing economy pattern and is
  kept for verified sources.

---

## Decision 4 — Short-circuit applies to unverified records too; verified flag untouched (M-7)

**Decision**: In both the binary and source phases, a record whose pool file already exists, whose stat size
matches the index `Size`, and whose `sha256` matches the index checksum is **skipped** — no download — regardless
of the record's `verified` value. The short-circuit does **not** flip an unverified record to verified.

**Rationale**: M-7 is framed purely as economy ("cheap exists + size + sha256 short-circuit for unverified
records"); the integrity gate is intact (a file that matches the index checksum is the correct content). Leaving
the flag unchanged keeps scope minimal and avoids conflating "present and correct" with "downloaded and confirmed
this run" — the `verified` flag also gates handoff. Because `ensure-base` is now fresh-cache-only (Decision 1),
the H-2 re-run reset that used to *force* a full re-download is gone; the short-circuit independently makes
interrupted runs cheap.

**Alternatives considered**:
- *Mark unverified-but-matching records verified* — rejected: changes verify-count semantics and the handoff
  gate beyond the review's intent; the spec (FR-006/SC-004) requires only that the file is not re-fetched.

---

## Decision 5 — Gate Sources index fetch/load on `config.fetch_source` in `ensure_indexes` (M-4)

**Decision**: In `resolver.ensure_indexes`, wrap the source-index fetch + `load_source_index` in
`if self.config.fetch_source:`. When disabled, `merged_source` stays empty and no Sources URL is fetched — matching
`init` (`cli/commands/init.py`), which already gates on `fetch_source`.

**Rationale**: `ensure_indexes` (resolver.py:251-265) fetches/loads Sources indexes for every configured source
unconditionally, while `init` and the source-link walk (`_register_source_for` checks `config.fetch_source`) are
already gated. This restores the binary-only profile's actual behavior: no wasted source-index downloads, no
spurious hard-fail when a Sources index is unreachable (FR-007). The gate is evaluated **per run against the live
config**, and the fetch triggers on cache miss — so a cache built under `regular_user` (`source = false`, Sources
indexes never fetched) re-fetches those indexes on the first run after switching to `developer`/`graybeard`
(`source = true`); the gate suppresses fetching only while source fetching is off, never permanently. Purely a
parity fix.

**Alternatives considered**:
- *Fetch but don't fail on unreachable* — rejected: still wastes the download the profile was designed to avoid;
  gating (like `init`) is simpler and consistent.

---

## Decision 6 — Index writes go atomic (`*.part` → `os.replace`) and corrupt cached indexes self-heal (F-23)

**Decision**: Two-part fix in the index path:
1. `index_loader.fetch_index` writes to `dest_path + ".part"`, then `os.replace(part, dest_path)` on success and
   removes the part on any failure — the same discipline payload downloads already use.
2. `resolver.ensure_indexes`: in the fetch fallback loop, delete a failed destination before `continue` (a
   partial file must never be preferred by `resolve_index_path`); and treat a cached index that
   `load_binary_index`/`load_source_index` cannot read (e.g. truncated `.gz`) as a **cache miss** — remove it and
   re-fetch, instead of crashing (FR-008, "optional self-heal" in the review is required by FR-008).

**Rationale**: A truncated/half-written index persists and is preferred forever today, poisoning every later run
(the review's code anchors: `index_loader` final writes non-atomic; `ensure_indexes` retry loop never deletes a
failed destination). Atomic writes guarantee a reader never observes a partially-written index (FR-008);
treating an unreadable cached index as a miss makes the next run self-heal. The verified-remote path already
buffers + verifies before writing; the part+replace step closes the crash-window gap.

**Alternatives considered**:
- *fsync + rename inside `fetch_index` only* — rejected: without the ensure_indexes self-heal, a corrupt file
  already on disk (or from a pre-017 crash) still bricks the cache; both halves are required.
- *Verify-then-write to the final path (current behavior)* — rejected: a crash between write and completion can
  still leave a truncated file that the `os.path.exists` check trusts.

---

## Consolidation

The two ensure-base items are resolved by a fresh-cache guard (Decision 1); the five download/index items are
anchored to the code-verified findings in `review/04_alpha_summary/02_high_priority_followups.md`; no unresolved
clarifications. Cross-cutting:
- **Schema freeze** holds — every decision is behavioral; the `verified` flag's semantics are refined, never
  its column.
- **No new runtime dependencies** — dropping supersede-aware selection removes the earlier `python-debian`
  need; everything else is stdlib.
- **No new network call sites** — redirects re-issue an existing request; index self-heal re-fetches an existing
  URL.
- **Docs inheritance** — data-model/contracts/quickstart updated in this feature and `docs/003-ensure-base.md`
  documents the fresh-cache constraint, per the review's "docs inheritance" note.
