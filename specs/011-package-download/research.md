# Research: Package Download

**Phase 0 output** — resolves the open questions from the Technical Context.
Most design decisions were settled in the spec-010 schema-stabilization session
and the planning kickoff; this document records each with its rationale and
rejected alternatives.

## Decision 1: Metadata resolution — the cached indexes are the per-file source of truth

- **Decision**: Every file's location, size, and SHA-256 are resolved at run time from the cached archive indexes (`{binary_cache}/dists/<release>/<component>/binary-amd64/Packages.gz` and the matching `Sources.gz`), keyed by the record's own (release, component). The database stores only state (`verified`, `sync_mask`, links); it gains no file-metadata columns.
- **Rationale**: The index files are part of the cache and ship with the handoff, so re-reading them is free and offline-safe. This is the design agreed in the spec-010 review: it keeps the schema minimal (no `filename`/`sha256`/`size` columns, no `source_files` table) so the schema can freeze after this feature. It also unifies the ISO path: `ensure-base` Phase B resolves through the regular `add` flow against the archive indexes (spec 008), so the cached indexes describe every current binary — downloading never touches the ISO.
- **Alternatives considered**: Persisting file metadata at registration — rejected (schema churn, redundant with cached indexes); reading the ISO's index at download time — rejected (unnecessary; records are archive-index records after Phase B).

## Decision 2: Mirror politeness — apt's own behavior is the calibration target

- **Decision**: The downloader keeps **one in-flight request per mirror** (sequential across files, no parallelism), reuses the connection (HTTP keep-alive via a shared opener), sends a **descriptive User-Agent** (`package_mgmt/<version> (OpenFromSource offline cache builder)`), and retries transient failures with **exponential backoff** (1 s base, doubling, 30 s cap, ±jitter, ≤5 attempts), **honoring a server-provided `Retry-After`** header. `404` (and any other permanent status) is a per-file failure, not a retry.
- **Rationale**: Debian mirrors and their rate-limiters are calibrated for apt's traffic pattern — one connection per mirror, sequential requests, keep-alive, a recognizable UA, retries. Matching that profile is the lowest-risk behavior for extended download phases (thousands of files); parallel fan-out is what gets clients throttled or blocked. Exponential backoff with jitter and `Retry-After` handling turns transient throttling into a pause instead of a hammer.
- **Alternatives considered**: Parallel downloads (2–4 workers) — rejected: higher throttling risk, more complex failure accounting, and the workload is transfer-bound (sequential keep-alive is adequate); a fixed artificial delay between requests — rejected: mirrors calibrate against apt's non-throttled behavior, and sleeps punish everyone for nothing; resumable partial downloads via HTTP Range — deferred: temp-file + re-download already converges idempotently, and Range adds server-behavior complexity for little gain at this stage.

## Decision 3: Progress UX — elapsed time above, progress bar beneath, per phase

- **Decision**: A `lib/util/progress.py` renderer shows, per phase (binary, source), an **elapsed-time line with a progress bar beneath it** (bar + `N/M files (X/Y MiB)`), refreshed in place with `\r` when stdout is a TTY; when stdout is not a TTY, it degrades to periodic progress lines (one per completed file, or a line every few seconds for very large files). Each phase ends with a **detailed summary block** (files, bytes, verified, failed, elapsed). Both phases run sequentially, each with its own progress.
- **Rationale**: Matches the user's explicit UX requirement and the existing `ensure-base` in-place pattern (`\rResolved X/Y`). Stdlib-only keeps the dependency footprint at zero; a hand-rolled renderer for two lines of output is trivial. Per-phase progress reflects the two-phase command structure and keeps output readable for thousands of files; the summary block carries the detail the bar cannot (per-file failures, capped like `ensure-base`'s 50-error console cap).
- **Alternatives considered**: A third-party progress library (tqdm) — rejected (new dependency, Constitution VI); single global bar across both phases — rejected (phases are distinct concerns with distinct summaries); no progress (only final summary) — rejected (explicit user requirement).

## Decision 4: Atomic writes and per-file commits — interrupted runs converge

- **Decision**: Each file is downloaded to a `*.part` temp file **in the destination directory**, verified against the index SHA-256 while still temp, then `os.replace`d into its final pool path. `verified` is committed **per file** (binary) and per source (after all its files succeed). Failed files leave no trace in the pool and never block other files.
- **Rationale**: A file is visible in the pool only when fully received and checksum-verified (FR-011); `os.replace` is atomic on the same filesystem, so a kill at any point leaves either nothing or the verified file. Per-file commits mirror the resolver's per-name commit pattern (US4: interrupted runs converge) — a re-run re-downloads only what failed. Source verification is all-or-nothing at the source level (FR-006/FR-007): partial source states are never recorded as success.
- **Alternatives considered**: Writing directly to the final path — rejected (kill mid-write leaves a corrupt file that looks present); staging elsewhere then copying — rejected (rename within the pool is atomic and cheaper); Range-resume of `*.part` files — deferred (Decision 2).

## Decision 5: Test strategy — a local HTTP server over the scratch archive

- **Decision**: Integration tests run a small threaded `http.server` fixture rooted at the scratch archive's pool, with the config mirror pointed at `http://127.0.0.1:<port>`. The fixture (`make_test_iso`) gains: `Size` in Packages entries, `Directory` + `Files` + `Checksums-Sha256` in Sources entries, and a pool-file writer that creates real file bytes at the index paths. A rate-limiting server variant (429s the first N requests per path, then succeeds) exercises the backoff/`Retry-After` path (SC-007); a corrupt-file variant exercises checksum mismatch (FR-004).
- **Rationale**: The downloader is HTTP-bound; only a real HTTP exchange exercises keep-alive, retries, and checksum flow end to end. The scratch archive is already the project's offline test substrate; serving its pool makes downloads real without touching the network. Development scope stays minimal (small trees like `vifm`, no build-dep downloads), per the user's constraint.
- **Alternatives considered**: `file://` URLs — rejected (urllib file handler bypasses the politeness/retry machinery under test); mocking the opener entirely — rejected for integration (used only in unit tests); pointing at a real mirror — rejected (offline-first dev, network nondeterminism).

## Decision 6: Download set definition — DB-derived, whole-cache

- **Decision**: The pending set is computed entirely from the database at run start: binaries with `status = 'current' AND verified = 0`; sources with `verified = 0` that are linked via `binary_to_source` to at least one current binary (when `fetch_source` is enabled). The command processes the whole set; per-package selection is a future refinement.
- **Rationale**: "Use the populated metadata to populate the cache" — the DB is the plan. Deriving the set at run start keeps it trivially correct after any registration flow (add, add --file, ensure-base) and after partial runs. Sources are gated on current-binary links so superseded history never triggers re-downloads (FR-009).
- **Alternatives considered**: Persisting the plan (a downloads table) — rejected: derivable state should not be stored (spec-010 principle); selection flags/filters — deferred (KISS; the whole cache is the actual use case).
