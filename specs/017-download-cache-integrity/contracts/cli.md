# CLI Contracts: Download & Cache Integrity

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md)

Extends (never contradicts) the existing contracts in `specs/014-base-profiles/contracts/cli.md`. This feature is
**contract-preserving** (spec Assumption): the exit-code scheme (0/1/101/102), the JSON error shapes, and the
output shapes of `ensure-base`, `download`, and `add` are **unchanged**. What changes is *which runs succeed and
which files are fetched* — cases that previously hard-failed now succeed, cases that previously re-downloaded now
skip, and a re-run of `ensure-base` on a populated cache is now refused. `tests/contract/test_cli.py` stays green
throughout.

---

## Phase 1 — `ensure-base` is fresh-cache-only (H-2/M-3 by constraint, FR-001)

**Contract.** `ensure-base` runs only against an empty cache. On a cache that already contains registered binary
records it refuses with a clear permanent error and exit code `1`, leaving the cache untouched. This resolves the
legacy H-2 (re-run resets `verified`) and M-3 (duplicate `current` rows) failure modes by construction: there is
never an existing record to reset or supersede. The successful-run summary output is unchanged.

```text
$ package_mgmt ensure-base   # fresh cache: succeeds (summary as today)
$ package_mgmt ensure-base   # populated cache: refuses, exit 1
{"error": "ensure-base is only for a fresh cache: ...", "exit_code": 1}
```

**Assertions**:
- Fresh cache → `ensure-base` succeeds with the existing summary shape.
- Populated cache (from a prior `ensure-base` or from `add`) → `ensure-base` exits 1, prints a JSON error on
  stderr, and `binaries`/`packages`/`provides` counts are unchanged.

---

## Phase 2 — `download` follows redirects (F-16, FR-002/FR-003)

**Contract.** Payload downloads follow `301/302/303/307/308` redirects to the final location (bounded at 5 hops)
before hashing; the final payload still passes the index checksum gate. A redirect with no `Location`, a
redirect loop, or a chain over the hop limit fails with a clear **permanent** error (not a bogus checksum
mismatch) and no leftover `*.part` file. Direct (non-redirecting) downloads behave unchanged.

**Assertions**:
- Mirror 301s `/pool/…` → download succeeds and the payload passes its checksum.
- Chain longer than the hop limit → permanent error naming the hop limit; no `.part` left behind.
- Redirect loop / no-`Location` redirect → clear permanent error; no `.part` left behind.
- Direct download → unchanged, payload passes checksum (regression).

---

## Phase 3 — `download` re-checks verified sources (F-17, FR-004/FR-005)

**Contract.** On every `download` run, verified source payloads are re-checked per index-listed file
(existence → size → checksum). A deleted or corrupt source file un-marks the source and re-fetches **exactly
that file**; intact files of the same source are not re-fetched; an all-intact source triggers no re-download.
The phase summary shape is unchanged ("Files downloaded / Bytes / Files verified / Failed").

**Assertions**:
- Delete one file of a verified source → next `download` re-fetches exactly that file and re-marks the source
  verified.
- Corrupt-but-size-matching file → un-marked and re-fetched (checksum step catches it).
- All-intact verified source → nothing re-downloaded for it.
- Multi-file source with one file missing → only the missing file is fetched.

---

## Phase 4 — `download` never re-fetches present-and-valid files (M-7, FR-006)

**Contract.** A payload file that already exists and matches its index `Size` and `SHA256` is **not**
re-downloaded, regardless of the record's `verified` state. Mismatched or absent files are fetched. The
short-circuit does not change a record's `verified` flag (an unverified-but-valid record stays unverified; it is
simply not re-fetched).

**Assertions**:
- Unverified record with a present+valid file → not re-downloaded.
- Unverified record with a present-but-mismatched file → re-fetched.
- Unverified record with an absent file → fetched.
- Mix of present-and-valid and missing records → only missing/mismatched fetched.

---

## Phase 5 — `add` respects source-fetching config (M-4, FR-007)

**Contract.** With source fetching disabled (`[fetch] source = false`), `add` succeeds using binaries alone: it
does **not** fetch or load Sources indexes, and it does **not** fail because a Sources index is unreachable.
With source fetching enabled, Sources indexes are fetched and loaded as before.

**Assertions**:
- `source = false`, reachable mirror → `add` succeeds; no Sources-index fetch performed.
- `source = false`, unreachable Sources index → `add` still succeeds (binaries alone).
- `source = true` → Sources indexes fetched/loaded (unchanged, regression).
- Cache built under `regular_user` (`source = false`, no Sources indexes ever fetched), then config switched to `developer`/`graybeard` (`source = true`) → the next `add` fetches and loads the previously-missing Sources indexes (profile switching must not permanently suppress source fetching).

---

## Phase 6 — index corruption self-heals (F-23, FR-008/FR-009)

**Contract.** Index files are updated atomically (a reader never observes a partially-written index); a failed
index download is cleaned up so a retry starts fresh; a cached index that cannot be read (e.g. truncated) is
treated as a cache miss and re-fetched — the next `add` succeeds instead of crash-looping. Exit codes/JSON
shapes are unchanged; the only observable difference is that a previously-poisoned cache now recovers.

**Assertions**:
- Connection dropped mid-index → retry `add` re-fetches cleanly (no half-written index in the way).
- Pre-seeded truncated cached index → treated as missing, re-fetched, run succeeds (no crash loop).
- Successful index download → persisted via atomic replace (no partial index ever becomes the cached index).
