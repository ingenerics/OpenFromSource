# Feature Specification: Package Download

**Feature Branch**: `011-package-download`
**Status**: Draft

**Input**: User description: "spec package download - Following the database-first design, we can now use the populated metadata to populate the cache with packages. Depending on the project's config settings, the database tables will have been populated (or not) and we can move on to constructing the needed assets to trigger the downloads and verify the resulting packages. Note during development we want to keep the actual downloads to a minimum, so no build-deps for testing and only small dep trees such as for vifm."

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Runs `download` to fetch the actual package files (binary and source) for everything the database has registered, then verifies them against the archives' own checksums |
| **Operator** | Offline system (no network) | Unchanged — receives a cache whose pool is now fully populated; post-handoff integrity verification is a future spec |

**Phase 1 — Binary Download**: The `download` command computes the set of current binaries whose files are not yet verified, resolves each file's expected location and checksum from the *cached archive indexes* (the same indexes `add` and `ensure-base` already fetched — they are part of the cache), downloads each file into the cache's pool, verifies its checksum, and marks it verified. Records whose index entry cannot be confirmed are reported as errors, never guessed at. Run by the Administrator.

**Phase 2 — Source Download**: When the configuration enables source fetching, the same flow fetches every source package's artifacts (`.dsc` + tarballs) for sources linked to current binaries, verifying each file against the source index's checksums, and marks the source record verified only when all of its files are present and verified. Run by the Administrator.

**Phase 3 — Robustness & Convergence**: Failed, interrupted, or partial runs are safe: no half-written files are left in the pool, every failure is reported per file with a non-zero exit, re-running the command downloads only what is still missing, and caches built under any configuration (sources enabled or not, ISO-based or archive-based registration) download correctly. Run by the Administrator.

---

### User Story 1 - Fill the Pool: Download Every Unverified Binary (Priority: P1)

The administrator has hydrated the database (via `add`, `add --file`, or `ensure-base`) and runs `download`. The command walks the database's current binaries, finds every one whose file is not yet verified, looks up each file's exact location and checksum in the cached archive index for its release and component, downloads the files into the cache's pool at the very paths the index specifies, verifies each against the index checksum, and marks the binary verified. A re-run downloads nothing — the verified state is the record. Throughout the binary phase the terminal shows a live progress bar with an elapsed-time indication above it (files and bytes), so the administrator always knows how the run is going; completion ends with a detailed per-phase summary.

**Why this priority**: This is the essence of the application — turning populated metadata into an actual offline cache, verified against the archive's own checksums, without any guessing about file locations.

**Independent Test**: Hydrate a scratch cache with a small tree (`vifm` and its dependencies), run `download`, then confirm: every `.deb` the index lists for those binaries exists in the pool at the index's `Filename` path, every current binary is verified, and a second `download` reports nothing to do.

**Acceptance Scenarios**:

1. **Given** a cache whose database holds current binaries with no verified files, **When** the administrator runs `download`, **Then** every file listed in the archive index for those binaries is downloaded into the pool, checksum-verified, and the corresponding binary records are marked verified.
2. **Given** the same cache after a successful run, **When** `download` is run again, **Then** it reports zero files to download and changes nothing (idempotent).
3. **Given** a binary whose file is already verified, **When** `download` runs, **Then** that file is not re-downloaded.
4. **Given** a file whose downloaded content does not match the index checksum, **When** `download` finishes, **Then** the file is removed from the pool, the binary stays unverified, the failure is reported, and the command exits non-zero.
5. **Given** a binary whose entry cannot be found in the cached index for its release and component, **When** `download` runs, **Then** a clear error names the record, nothing is downloaded for it, and the command exits non-zero — no location or checksum is invented.

---

### User Story 2 - Fetch the Sources: Download Linked Source Artifacts (Priority: P2)

When the configuration enables source fetching, the cache is expected to contain, for every current binary, the source package it was built from and all of that source's files (`.dsc`, original tarball, Debian tarball). The `download` command fetches every file of every source that is linked to a current binary and not yet fully verified, verifying each file against the source index's checksums, and marks the source verified only when all of its files are present and verified. The source phase shows its own progress bar with the same elapsed-time indication, and ends with its own detailed summary.

**Why this priority**: Constitution III (binary-source consistency) makes sources part of the offline guarantee; without this story the cache has binaries but cannot rebuild anything.

**Independent Test**: Hydrate a scratch cache with source fetching enabled, run `download`, then confirm every file listed in the source index (directory + `Checksums-Sha256`) for each linked source exists in the source pool and the source records are verified; disabling source fetching changes nothing about the source pool.

**Acceptance Scenarios**:

1. **Given** a cache with source fetching enabled and sources linked to current binaries, **When** `download` runs, **Then** every file of every such source (`.dsc` and tarballs) is downloaded into the source pool, checksum-verified, and the source record is marked verified.
2. **Given** a source with one file failing its checksum, **When** `download` finishes, **Then** the bad file is removed, the source remains unverified (partial downloads are not recorded as success), the failure is reported, and the exit is non-zero.
3. **Given** a cache registered with source fetching disabled, **When** `download` runs, **Then** no source files are fetched and the run succeeds — the configuration's own choice is respected, not overridden.
4. **Given** sources that are not linked to any current binary, **When** `download` runs, **Then** their files are not downloaded (the download set is driven by the current binary graph).

---

### User Story 3 - Safe Runs: Interruptions, Failures, and Re-runs (Priority: P2)

The administrator's connection fails mid-run, or a file is missing upstream, or they stop the command. Nothing is left in a broken state: files are written only when fully received and checksum-verified, partial downloads are cleaned up, failures are reported per file with a non-zero exit, and the next run picks up exactly where the last run stopped. Because runs can be long, the command behaves politely toward the mirrors it downloads from: one request in flight at a time per mirror with connection reuse, a descriptive User-Agent identifying the tool, and bounded retries with exponential backoff (honoring a server-provided `Retry-After` where present) — so extended download phases don't get the cache blocked or throttled.

**Why this priority**: Downloads over flaky connections are the norm in the field; trust in the cache depends on the guarantee that a re-run converges without duplicates or half-files.

**Independent Test**: Interrupt a run mid-download (or simulate a failing file), then re-run `download` — only the missing/failed files are fetched, no partial files remain, and the final state matches a single clean run.

**Acceptance Scenarios**:

1. **Given** a run interrupted while writing a file, **When** `download` runs again, **Then** the partial file is not present in the pool and the file is fetched afresh.
2. **Given** a file that fails to fetch (network error, missing upstream), **When** `download` finishes, **Then** the failure is reported for that file, other files still download, the binary stays unverified, and the exit is non-zero.
3. **Given** a successful run followed by deleting one file from the pool, **When** `download` runs again, **Then** exactly that one file is re-downloaded and verified.
4. **Given** the same file reachable from several binaries (e.g. a shared dependency), **When** `download` runs, **Then** it is downloaded exactly once.

---

### Edge Cases

- Cache with zero pending files (everything verified) → reports zero files, exits successfully.
- Empty cache (nothing registered) → reports zero files, exits successfully.
- Index file missing for a configured release/component (deleted after registration) → clear error telling the administrator the index is part of the cache and must be restored or re-fetched; nothing is downloaded for that release/component.
- Index entry present but missing the checksum field → treated as unconfirmable, reported like a missing entry (no checksum-less downloads).
- File already present in the pool but unverified → re-downloaded and verified (presence alone is never treated as verified).
- Checksum mismatch → file removed; the failure names the file, the expected and actual checksums, and the binary record.
- Disk full / permission error while writing → reported per file, run continues, exit non-zero.
- Source records registered without a linked binary (orphaned) → skipped (US2 AS4).
- Source index lacking `Checksums-Sha256` for a file → that file is unconfirmable and reported, like a missing entry.
- Very large download sets (thousands of files) → reporting stays readable (progress bar + summary counts); no fixed-size limits.
- `ensure-base`-hydrated caches → covered by the same flow: the ISO's records are resolved through the regular `add` path against the archive indexes (documented in spec 008), so the cached archive indexes describe every current binary; the ISO itself is not involved in downloading.
- A server responds with `429`/`503` (or a connection reset) → the request is retried with exponential backoff, honoring `Retry-After` if the server sends it; repeated refusal is reported per file, the run continues with other files, and the exit is non-zero.
- Standard output is not a terminal (piped, logged) → the progress bar degrades to periodic progress lines (e.g. one line per file or per N seconds) instead of in-place escape sequences, keeping logs readable.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The `download` command MUST enumerate its work from the database alone: every binary with status `current` and `verified = 0` (the pending set), plus — when source fetching is enabled — every source linked (via `binary_to_source`) to a current binary whose `verified = 0`.
- **FR-002**: For every pending binary, the command MUST resolve the file's location, size, and SHA-256 checksum from the cached archive index for the binary's own release and component; a binary whose entry (or checksum) is absent from the index MUST be reported as an error and MUST NOT be downloaded from any guessed location.
- **FR-003**: Downloaded binary files MUST be written into the cache's pool at the exact path the index `Filename` field specifies, and MUST be checksum-verified against the index before the binary record's `verified` flag is set to 1.
- **FR-004**: A file whose checksum does not match MUST be removed from the pool, MUST leave its record unverified, MUST be reported with the expected and actual checksums, and the command MUST exit non-zero.
- **FR-005**: The command MUST be idempotent: verified records are never re-downloaded, and a re-run over a fully verified cache downloads zero files.
- **FR-006**: When source fetching is enabled, the command MUST download every file of every pending source (directory + file list from the source index, SHA-256 from its `Checksums-Sha256`), and MUST set the source record's `verified = 1` only when all of its files are present and verified.
- **FR-007**: A source with any missing or unverifiable file MUST remain unverified, its failures MUST be reported per file, and the command MUST exit non-zero.
- **FR-008**: When source fetching is disabled, the command MUST NOT fetch source files, and this MUST NOT be treated as an error.
- **FR-009**: Sources not linked to any current binary MUST NOT be downloaded.
- **FR-010**: A file shared by multiple records MUST be downloaded exactly once per run.
- **FR-011**: Interrupted or failed runs MUST leave no partial files in the pool: a file is visible in the pool only when fully received and checksum-verified; failed files MUST NOT block other files (best-effort per file).
- **FR-012**: A missing cached index for a configured release/component MUST produce a clear error instructing that the index is part of the cache and must be restored or re-fetched; no files are downloaded for that release/component.
- **FR-013**: The command MUST report a clear summary (files downloaded, verified, failed) and exit non-zero if any file failed, zero otherwise.
- **FR-014**: The command MUST show live progress during each phase (binary, source): an elapsed-time indication with a progress bar beneath it (files and bytes), refreshed in place on a terminal and degrading to periodic progress lines when the output is not a terminal; each phase ends with its own detailed summary (files, bytes, verified, failed).
- **FR-015**: The command MUST follow mirror-politeness practices during downloads: at most one in-flight request per mirror at a time with connection reuse, a descriptive User-Agent identifying the tool, and bounded retries with exponential backoff that honor a server-provided `Retry-After`.

### Key Entities *(include if feature involves data)*

- **DownloadSet (derived, no new table)**: Pending binaries + pending sources, computed from the database at run start (FR-001). Not stored; always derivable from `binaries.verified` / `sources.verified`.
- **IndexMetadata (derived, no new table)**: Per-file location, size, and SHA-256, read from the cached archive indexes at run time — the authoritative per-file metadata (schema decision from spec 010: the database stores state, the indexes store file metadata).
- **Pool layout (existing directories)**: `{binary_cache}/pool/...` and `{source_cache}/pool/...`, created by `init`; download writes files at the index paths under these roots.
- **Binaries / sources (existing tables)**: `verified` is the record of completion; no schema changes in this feature (spec 010 verified-flag policy; the schema freezes after this feature).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A cache hydrated with a small tree (e.g. `vifm` + dependencies) completes a `download` run with every current binary verified and every `.deb` present in the pool at the index's `Filename` path — 100% of pending files, zero failures.
- **SC-002**: 100% of downloaded files match the index checksum; any mismatch fails that file only, removes it, and is reported with both checksums.
- **SC-003**: With source fetching enabled, every source linked to a current binary has all of its files in the source pool and `sources.verified = 1`; with it disabled, no source file is fetched.
- **SC-004**: A re-run after a successful run downloads zero files and changes nothing; after deleting one pool file, a re-run downloads exactly that one file.
- **SC-005**: An interrupted or failing run leaves no partial files, and a subsequent run converges to the same state as a single clean run.
- **SC-006**: No file is ever downloaded from a guessed location or with a missing checksum — 100% of downloads are index-confirmed (missing entries are errors, not downloads).
- **SC-007**: During a run of 100+ files against a rate-limiting test server, the mirror never refuses the client (no `429`/`403`): one request in flight at a time, and transient refusals are retried with backoff rather than hammering.
- **SC-008**: Progress is visible at all times: on a terminal the elapsed time and progress bar update in place during each phase; piped output produces periodic progress lines; both phases end with detailed summaries.

## Assumptions

- **Persona**: `download` is administrator-only, like every cache-building command; the operator's flow is unchanged.
- **Command shape**: One command (`download`) processes the entire pending set from the database; per-package or per-tree selection is a future refinement and is out of scope.
- **Metadata source**: The cached archive indexes (under `{binary_cache}` / `{source_cache}`, fetched by `init`/`add` and part of the cache handoff) are the authoritative per-file metadata; the database stores only state. This is the documented spec-010 design decision (index-read download).
- **No ISO involvement**: Records hydrated via `ensure-base` are resolved through the regular `add` flow against the archive indexes (spec 008, Phase B), so the cached indexes describe every current binary. Downloading never reads the ISO image.
- **Verification**: Per-file integrity is SHA-256 against the index entry. Archive-level signature verification (Release file GPG) happens when indexes are fetched, as today; no per-file signing is re-verified here.
- **Failure handling**: Best-effort per file with a clear summary and non-zero exit when anything failed; no retry loops beyond the user re-running the command (idempotent convergence).
- **No schema changes**: This feature adds no tables or columns (spec 010 verified-flag policy). After this feature, the schema freezes.
- **Development scope**: Validation uses the scratch archive fixture with small dependency trees (e.g. `vifm`); no build-dep downloads are exercised in tests. The fixture's source index will need `Directory` and `Checksums-Sha256` fields added for source-download scenarios.
- **Network**: Downloads come from the configured `mirror` for each source's release; the admin is networked by definition of the persona.
- **Constitution gate**: The existing "no download until records are confirmed" rule is realized by FR-002/FR-012 — nothing is downloaded whose index entry cannot be confirmed.
- **Progress rendering**: Terminal progress uses the standard library only (no new dependencies); the established in-place `\r` pattern from `ensure-base` is the precedent. A progress bar is a compact single-line rendering (elapsed time above, bar + counts below), refreshed in place.
- **Politeness baseline**: The calibration target is apt's own behavior — one connection per mirror, sequential requests, connection reuse, descriptive User-Agent, retries. No artificial fixed rate limiting (sleeps) beyond backoff on failures; no unbounded parallelism against a mirror.

## Clarifications

### Session

Design decisions carried over from the schema-stabilization review (spec 010 session), no open items:

- Index-read download design: the database holds state + graph (`verified`, `sync_mask`, links); the cached archive indexes hold per-file metadata (`Filename`, `Size`, `SHA-256`), read at run time. No file-metadata columns were added to the schema.
- `verified` semantics: `binaries.verified` = file present locally + checksum matches the current index entry; `sources.verified` = all of the source's files present and verified. Set-points defined by this feature (FR-003, FR-006).
- `ensure-base` records come from the archive indexes via the regular `add` flow (Phase B), so the download flow never touches the ISO.
- Schema freeze: after this feature's schema usage is settled, the schema is stable; the verified-flag policy (spec 010 Decision 9) is part of that freeze.
