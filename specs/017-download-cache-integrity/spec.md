# Feature Specification: Download & Cache Integrity

**Feature Branch**: `017-download-cache-integrity`
**Status**: Draft

**Input**: User description: "download and cache integrity - As discussed in current session."

**Design context**: The alpha reviews (`review/02_alpha_kimi_k3/`, `review/03_alpha_glm_5_3/`) surfaced, alongside the blockers already tackled in spec 015, a batch of high-priority robustness items grouped by area in `review/04_alpha_summary/02_high_priority_followups.md`. Per the agreed three-spec split of that file, this is the second follow-up feature — the **Download / verify / operator track** and **ensure-base** clusters that concern the integrity and economy of the local payload cache (H-2, M-3, M-4, F-16, F-17, M-7, F-23). It is sequenced after 016 (Resolver & dependency correctness) and before 018 (Operator track & CLI/config robustness), all in the same alpha release.

The **ensure-base** items (H-2 re-run resets verified state; M-3 duplicate current rows) are **resolved by constraint, not by registration rework**: `ensure-base` is defined to run on a **fresh cache only** — the intended (graybeard) workflow ingests the installer image exactly once into an empty cache (`init` → `ensure-base` → `verify` → `handoff`). The command refuses a cache that already holds packages, so there is never an existing record to reset or supersede. The remaining theme: downloads must survive real-world network behavior, and the cache must never be poisoned by a truncated or half-written file.

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Builds and maintains the cache; this feature changes what `ensure-base`, `download`, and `add` fetch, re-fetch, skip, and report |
| **Operator** | Offline system (no network) | Installs from the shipped cache; indirectly protected — a complete, un-poisoned, correctly-verified cache is what makes offline install possible |

**Phase 1 — Base Ingest Is Fresh-Cache-Only (H-2, M-3)**: `ensure-base` runs only on a cache with no registered packages yet; a populated cache is refused with a clear error. This removes the two re-run failure modes (verified-state reset, duplicate current rows) by construction — the graybeard flow ingests the image once into an empty cache.
**Phase 2 — Redirect-Aware Downloads (F-16)**: downloads follow server redirects (bounded hops, loops rejected) instead of failing as bogus integrity mismatches.
**Phase 3 — Verified Sources Re-Checked (F-17)**: verified source payloads are re-checked on every run (existence → size → checksum) and repaired per-file, like binary payloads.
**Phase 4 — Present-File Short-Circuit (M-7)**: an already-present, matching payload is not re-downloaded even when its record is unverified.
**Phase 5 — Source-Index Gating (M-4)**: Sources indexes are fetched/loaded only when source fetching is enabled; binary-only caches never touch them or fail on them.
**Phase 6 — Poison-Proof Index Updates (F-23)**: index files are written atomically, failed downloads are cleaned up, and a corrupt cached index is treated as a cache miss.

---

### User Story 1 - Base Ingest Is Fresh-Cache-Only (Priority: P1)

The administrator ingests the offline DVD installer image with `ensure-base` — the graybeard workflow runs it exactly once on an empty cache. Today, if the command is (re-)run against a cache that already holds packages, the whole base set is reset to unverified on every re-run (forcing a full re-download after `download`) and a second `current` row can appear when the image version differs from a cached one — silently shrinking the handoff and duplicating handoff entries. This phase makes `ensure-base` refuse to run on a non-empty cache: the fresh-cache-only rule makes both failure modes impossible, because there is never an existing record to reset or supersede.

**Why this priority**: One routine re-run can throw away gigabytes of verified work and corrupt delivery bookkeeping; constraining *when the command runs* is strictly simpler than adding per-record preservation and supersede logic — and it matches how the graybeard profile actually uses the tool.

**Independent Test**: Register the base set on a fresh cache (succeeds), then invoke `ensure-base` again — it refuses with a clear error (exit 1) and leaves the cache byte-for-byte unchanged.

**Acceptance Scenarios**:

1. **Given** a fresh cache (initialized only, no packages), **When** the administrator runs base registration, **Then** it succeeds and registers the image's binaries.
2. **Given** a cache that already holds registered packages (from a prior `ensure-base` or from `add`), **When** the administrator runs base registration, **Then** it refuses with a clear error (exit 1) and does not modify the cache.
3. **Given** the refusal, **When** the administrator starts from an empty cache (delete the database and re-run `init`), **Then** base registration proceeds normally.
4. **Given** a run interrupted mid-ingest that left partial records, **When** the administrator re-runs base registration, **Then** it refuses (the cache is no longer empty); recovery is a fresh start.

---

### User Story 2 - Downloads Follow Redirects (Priority: P2)

The administrator downloads from Debian mirrors, which routinely redirect (http→https canonicalization, CDN moves, relocated trees). Today a 301/302/307/308 response body is hashed as if it were the payload and fails as a **permanent, non-retried "checksum mismatch"** — so any redirecting mirror breaks the download permanently. This phase follows redirects to the final location before hashing, with a bounded hop count and loop rejection, keeping the integrity gate intact.

**Why this priority**: A single redirecting mirror bricks the entire download path with no retry; integrity is already safe (the checksum gate still applies to the final payload), only availability is broken.

**Independent Test**: A local HTTP fixture that redirects a pool request — the download succeeds and the payload passes the checksum; a redirect without a target and a redirect loop each fail with a clear permanent error and no leftover partial file.

**Acceptance Scenarios**:

1. **Given** a mirror that redirects a payload request, **When** the administrator downloads, **Then** the redirect is followed and the payload downloads and passes its integrity check.
2. **Given** a chain of redirects longer than the allowed hop limit, **When** the download is attempted, **Then** it fails with a clear permanent error (not a bogus integrity failure).
3. **Given** a redirect loop or a redirect with no target location, **When** the download is attempted, **Then** it fails with a clear permanent error and any partial file is cleaned up.
4. **Given** a direct (non-redirecting) download, **When** the administrator downloads, **Then** behavior is unchanged and the payload passes its integrity check.

---

### User Story 3 - Verified Sources Are Re-Checked Each Run (Priority: P2)

The administrator relies on `download` to keep the cache's verified state honest. Today binary payloads are re-checked on every run (size → checksum, re-fetch + un-verify on failure), but **source payloads are never re-examined** — a deleted or corrupt source tarball is silently ignored by every command, and a source left unverified by one failed file re-downloads **all** of its files next run. This phase gives source payloads the same per-run re-check as binaries (existence → size → checksum per index-listed file, un-verify + re-fetch on failure, per-file skip for the rest).

**Why this priority**: A silent hole in the verified set means the shipped cache can claim COMPLETE while its sources are unusable — undercutting the deterministic-rebuild promise; the all-or-nothing re-download of a multi-file source wastes bandwidth.

**Independent Test**: Delete one file of a verified source — the next `download` re-fetches exactly that file and re-verifies it; a corrupt-but-size-matching file is un-marked and re-fetched; an all-intact source triggers no re-download.

**Acceptance Scenarios**:

1. **Given** a verified source whose payload file has been deleted, **When** `download` runs, **Then** exactly that file is re-fetched and the source is re-marked verified.
2. **Given** a verified source whose payload file is corrupt but matches its recorded size, **When** `download` runs, **Then** the file is un-marked and re-fetched (the checksum step catches it).
3. **Given** a verified source whose files are all present and intact, **When** `download` runs, **Then** nothing is re-downloaded for that source.
4. **Given** a source with multiple files where only some are missing, **When** `download` runs, **Then** only the missing files are re-fetched (intact files are skipped).

---

### User Story 4 - Present Valid Files Are Not Re-Downloaded (Priority: P2)

The administrator's download runs must be economical, especially after an interrupted run. Today the size+checksum skip applies only to verified records — an **unverified** record is re-downloaded even when its pool file is already present and matches. (Phase 1 removes the base re-run that used to reset every record to unverified, so this no longer compounds into a guaranteed full re-download; independently it still makes every interrupted run expensive.) This phase short-circuits any payload whose file is already present and matches its expected size and checksum, regardless of verified state.

**Why this priority**: This is the economy half of the download story — without it, recovery from any interruption or state reset means re-fetching gigabytes that are already on disk.

**Independent Test**: An unverified record whose pool file already matches is not re-downloaded; one whose file is mismatched or absent is re-fetched.

**Acceptance Scenarios**:

1. **Given** an unverified record whose payload file is already present and matches (size + checksum), **When** `download` runs, **Then** the file is not re-downloaded.
2. **Given** an unverified record whose payload file is present but does not match, **When** `download` runs, **Then** the file is re-fetched.
3. **Given** an unverified record whose payload file is absent, **When** `download` runs, **Then** the file is fetched.
4. **Given** a mix of present-and-valid and missing records, **When** `download` runs, **Then** only the missing/mismatched ones are fetched.

---

### User Story 5 - Source Indexes Respect the Configuration (Priority: P2)

The `regular_user` profile is designed to fetch binaries only (source fetching disabled), yet its first `add` still downloads the Sources indexes it was designed to avoid — and hard-fails if they are unreachable even though only binaries are needed. This phase gates the fetching and loading of Sources indexes on the source-fetching setting, matching how initialization already behaves.

**Why this priority**: It restores the binary-only profile's actual behavior — no wasted source-index downloads and no spurious failure when a source index is unreachable.

**Independent Test**: With source fetching disabled and an unreachable Sources index, `add` succeeds using binaries alone, and no Sources-index fetch work is performed.

**Acceptance Scenarios**:

1. **Given** a configuration with source fetching disabled, **When** the administrator adds a package, **Then** the run succeeds using binaries alone and does not fetch or load Sources indexes.
2. **Given** the same configuration and an unreachable Sources index, **When** the administrator adds a package, **Then** the run still succeeds (it does not depend on the unreachable index).
3. **Given** a configuration with source fetching enabled, **When** the administrator adds a package, **Then** Sources indexes continue to be fetched and loaded (unchanged).
4. **Given** a cache built under a source-disabled profile (`regular_user`, so its Sources indexes were never fetched) and the configuration then switched to a source-enabled profile (`developer`/`graybeard`), **When** the administrator adds a package, **Then** the previously-missing Sources indexes are fetched and loaded — switching profiles must not permanently suppress source fetching.

---

### User Story 6 - Index Corruption Self-Heals (Priority: P3)

A truncated or half-written index file must never permanently break the cache. Today a truncated index file (e.g. a connection dropped mid-download) persists on disk and is preferred forever, so every later `add` dies on the same corrupt file; index files are also written non-atomically. This phase updates index files atomically (write, verify, then replace), cleans up a failed download so a retry starts fresh, and treats a corrupt cached index as a cache miss so it is re-fetched.

**Why this priority**: One interrupted index download makes the cache unusable until manually repaired — a poison that should self-heal on the next run.

**Independent Test**: A fixture that drops the connection mid-index — the next `add` re-fetches cleanly; a pre-seeded truncated index file is recovered (treated as missing and re-fetched), not a crash loop.

**Acceptance Scenarios**:

1. **Given** a connection that drops while an index is being downloaded, **When** the administrator retries `add`, **Then** the download starts fresh (no half-written index remains in the way) and succeeds.
2. **Given** a pre-existing truncated/corrupt cached index file, **When** the administrator runs `add`, **Then** the corrupt file is treated as a cache miss, re-fetched, and the run succeeds — no crash loop.
3. **Given** a successful index download, **When** it is persisted, **Then** it is written in a way that a reader never observes a partially-written index (only complete indexes become the cached index).

---

### Edge Cases

- **Base ingest guard (Phase 1)**: fresh cache (succeeds); cache populated by a prior `ensure-base` (refused); cache populated by `add` (refused); cache with only partially-registered records after an interrupt (refused — fresh start required); initialized-but-empty database (allowed).
- **Redirects (Phase 2)**: 301/302/303/307/308 responses; a redirect with no target location; a redirect loop; a chain over the hop limit; a redirect to a different scheme; redirects across pooled connections.
- **Source re-check (Phase 3)**: a deleted source file; a corrupt-but-size-matching file; a multi-file source with one file missing; an all-intact source; a source never downloaded.
- **Short-circuit (Phase 4)**: unverified with present+valid, present+mismatched, and absent files; interplay with an interrupted run's leftovers; binary vs source payloads.
- **Index gating (Phase 5)**: source disabled with unreachable and reachable Sources indexes; source enabled unchanged; a cache initialized before the setting existed; a cache built under a source-disabled profile then run under a source-enabled profile (the never-fetched Sources indexes are fetched on the first source-enabled run).
- **Index integrity (Phase 6)**: a mid-index connection drop; a pre-seeded truncated index; a retry after failure; a corrupt index across repeated runs (must recover, not loop).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `ensure-base` MUST refuse to run (clear error, exit 1, cache unmodified) on a cache that already contains registered binary records; it MUST succeed on a fresh cache.
- **FR-002**: When a server responds to a payload request with a redirect, the system MUST follow the redirect and download from the final location; the number of hops MUST be bounded and a redirect loop or missing target MUST fail with a clear permanent error.
- **FR-003**: The integrity of a downloaded payload MUST still be verified against its checksum after any redirects are followed.
- **FR-004**: Verified source payloads MUST be re-checked on every download run (existence, then size, then checksum) just as binary payloads are; a deleted or corrupt source payload MUST be re-fetched and its record un-marked as verified.
- **FR-005**: When a source's payload is re-fetched, only the missing or corrupt files MUST be downloaded — intact files of the same source MUST NOT be re-fetched.
- **FR-006**: A download run MUST NOT re-fetch a payload file that is already present and matches its expected size and checksum, even when its record is currently unverified.
- **FR-007**: Sources indexes MUST be fetched and loaded only when source fetching is enabled; with it disabled, `add` MUST succeed using binaries alone and MUST NOT fail because a Sources index is unreachable. Enabling source fetching after a source-disabled run MUST still fetch the then-missing Sources indexes (the gate re-enables on cache miss).
- **FR-008**: Index files MUST be updated so that a partially-written index can never become the cached index; a failed index download MUST be cleaned up so a retry starts fresh.
- **FR-009**: A cached index that cannot be read (e.g. truncated) MUST be treated as a cache miss and re-fetched, so a corrupt index never permanently breaks subsequent runs.

### Key Entities *(include if feature involves data)*

- **Verified state**: the per-record `verified` flag; un-marked when a payload fails re-check, and the gate for what ships in the handoff. Because `ensure-base` is fresh-cache-only, no base registration ever resets it.
- **Fresh-cache constraint**: `ensure-base` runs only against an empty cache (no registered binary records); this is the mechanism that resolves the H-2/M-3 re-run failure modes by construction.
- **Payload presence**: whether a pool file exists and matches its expected size and checksum; drives the download short-circuit for any record regardless of verified state.
- **Source payload check**: the per-run existence/size/checksum verification of verified source payload files and the per-file re-fetch that repairs them.
- **Redirect handling**: following HTTP redirects to the canonical payload location before hashing, with a bounded hop count and loop rejection.
- **Source-index gating**: whether Sources indexes are fetched and loaded, driven by the source-fetching configuration setting.
- **Index integrity**: atomic index updates (a reader never sees a partially-written index) and self-healing of a corrupt cached index as a cache miss.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `ensure-base` succeeds on a fresh cache and refuses a populated cache with a clear error (exit 1) and no cache modification — so no re-run can reset verified state or create a duplicate current row.
- **SC-002**: Downloads through a redirecting mirror succeed and the payload passes its integrity check; redirect loops, missing targets, and over-limit chains fail with a clear permanent error and no leftover partial file.
- **SC-003**: A download run after deleting or corrupting one file of a verified source re-fetches exactly that file, re-verifies, and re-marks it; all-intact sources trigger no re-download.
- **SC-004**: A download run never re-fetches a payload file that is already present and matches (size + checksum), even for unverified records.
- **SC-005**: With source fetching disabled, `add` completes using binaries alone and never fails because a Sources index is unreachable.
- **SC-006**: A truncated or half-written cached index is recovered on the next run (treated as missing and re-fetched), and no partially-written index is ever used as the cached index.

## Assumptions

- **Scope source**: The seven entries H-2, M-3, M-4, F-16, F-17, M-7, F-23 in `review/04_alpha_summary/02_high_priority_followups.md` (the "ensure-base" and "Download / verify / operator track" clusters) are the authoritative scope of this feature. H-2 and M-3 are resolved by the fresh-cache-only constraint (Phase 1 / FR-001) rather than by registration rework. Per the agreed split, 016 (Resolver & dependency correctness) and 018 (Operator track & CLI/config robustness) are separate features; F-18/F-19 (operator diff/apply) and F-20/F-21/F-22 (config/CLI) are out of scope here.
- **Schema freeze**: The spec 010/011 schema freeze holds — no database schema changes; all fixes are behavioral or registration-semantics changes.
- **Fresh-cache only**: `ensure-base` is defined to run on an empty cache; the resolver's add-time reset-on-supersede semantics for `add`-time registration are intentionally left untouched.
- **Redirect bounds**: A finite hop limit (industry-standard ~5) is enforced; redirects are followed for payload downloads while the final payload still passes the integrity gate.
- **Index self-heal**: A corrupt cached index is treated as a cache miss and re-fetched; index files are updated atomically.
- **Contract-preserving**: Existing exit-code/JSON contracts (spec 014 `contracts/cli.md`) continue to hold; `tests/contract/test_cli.py` must stay green.
- **Alpha sequencing**: This feature ships in the same alpha release, after the 016 resolver cluster and before 018; it is complete when all six phases are implemented and both documented test runners are green.
- **Regression-preserving**: Behavior not covered by one of the six entries is unchanged.

## Clarifications

*None pending — the seven entries carry concrete fix and test guidance, the fresh-cache-only resolution of H-2/M-3 was confirmed with the stakeholders, and the split/sequencing was confirmed before writing this specification.*
