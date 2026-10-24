# Feature Specification: Polish Backlog (Merged Low Findings)

**Feature Branch**: `019-polish-backlog`
**Status**: Draft

**Input**: User description: "Spec backlog polish - As a final clean up spec, pick up the action points listed in 03_polish_backlog_docs.md under point 1. Polish backlog (merged low findings). Do not take the other points into account for this spec."

**Design context**: The two pre-release reviews (`review/02_alpha_kimi_k3/`, `review/03_alpha_glm_5_3/`) deferred their low-severity findings to `review/04_alpha_summary/03_polish_backlog_docs.md`, §1 "Polish backlog (merged low findings)" — the deduplicated set `P-1…P-23`. This is the final clean-up feature of the alpha cycle, sequenced after 015 (blockers), 016 (resolver), 017 (download/cache), and 018 (operator track & CLI/config). It collects the remaining low findings that are **not** already handled by those four specs, grouped by area: the CLI/exit-code/UX consistency cluster, the resolver/data-model correctness cluster, the downloader/operator robustness cluster, and the cosmetic test issue. One finding — P-8 (unknown config keys silently ignored) — was already resolved by spec 018's F-20 and is excluded here (regression check only). Everything in this feature is a small, independently verifiable correction; nothing is a release blocker. Per the user's instruction, §2 (docs refresh), §3 (spec/doc drift), §4 (test-coverage gaps), and §5 (process reminders) of the source document are **out of scope** for this feature.

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Runs `init`, `add`, `ensure-base`, `verify`, `list`, `show`, `systems`, `sync`; this feature makes the CLI honor its exit-code contract, makes command flags/arguments behave as documented, makes `verify`/`sync` reports honest, and makes resolver/database state accurate |
| **Operator** | Offline system (no network) | Merges shipped diffs (`apply.sh`) and installs from the cache; indirectly protected — a cache path containing spaces is refused with a clear diagnostic (never a silently-broken `deb` entry), and the shipped cache resolves deterministically |

**Phase 1 — Contract & Side-Effect Honesty**: command failures map to the documented exit codes and never leave stray database files or misleading success summaries. *(P-1, P-4, P-5)*
**Phase 2 — Flag & Argument Consistency**: command flags do what they claim and argument validation is consistent across argument and `--file` paths. *(P-2, P-3, P-7)*
**Phase 3 — Honest Sync & Verify Reports**: a zero-pending sync leaves an empty diff, and `verify`'s build-dep report respects the configured depth. *(P-6, P-18)*
**Phase 4 — Deterministic, Spec-Compliant Resolution**: exact-version/component ties resolve to the configured priority, current-binary selection is deterministic, and build-dep/alternative/virtual probes respect constraints and refreshed indexes. *(P-9, P-10, P-11, P-12, P-16)*
**Phase 5 — Accurate Database Lifecycle**: the provides table is rebuilt without nested commits or phantom inserts, stale providers are swept, the circular flag is reset on upsert, and `packages.status`/`requested_version` tell the truth. *(P-13, P-14, P-15, P-17)*
**Phase 6 — Path & Download Robustness**: a relative `base_dir` is rejected, server `Retry-After` is capped, and cache paths with spaces hand off correctly or are diagnosed. *(P-19, P-20, P-21)*
**Phase 7 — Doc/Test Hygiene**: artifacts that contradict or mislabel behavior are corrected. *(P-22, P-23)*

---

### User Story 1 - Command Failures Honor the Exit-Code Contract and Leave No Artifacts (Priority: P1)

The administrator runs `add` against a stale-schema cache, `init` on a disk with no space, or `ensure-base` with no usable image. Today `add` maps a stale-schema database to the generic error code (1) while every sibling command uses the contract's stale/missing-database code (101); `init` maps **every** failure — including "no space left on device" — to the permission code (100), and a successful `init` that never reached a mirror reports a plain "initialized" with 0 binaries and no explanation; and a failed `ensure-base` on a fresh cache creates the database file first, violating spec 008's promise to "exit non-zero without touching the database". This phase makes every command honor the documented exit-code table, distinguish real permission failures from other failures, state when initialization ran without a reachable mirror, and never create the database before a command that is about to fail.

**Why this priority**: Exit codes and failure side-effects are the automation contract — scripts branch on them and a stray database file poisons later diagnostics. These are the most externally visible of the low findings, and one (P-5) directly contradicts a written spec promise (008 US1 AS2).

**Independent Test**: A stale-schema `add` exits 101 (not 1); an ENOSPC-style `init` exits 1 (not 100) and a mirror-unreachable `init` exits 0 with an offline note in the summary; a failed `ensure-base` on a fresh cache leaves no database file.

**Acceptance Scenarios**:

1. **Given** a database whose schema does not match the expected version, **When** the administrator runs `add`, **Then** the command exits with the same stale/missing-database code (101) its sibling commands use, and never with the generic error code (1).
2. **Given** an initialization that fails for a non-permission reason (e.g. insufficient disk space), **When** the administrator runs `init`, **Then** the command exits with the generic error code (1), reserving the permission code (100) for actual permission problems.
3. **Given** an initialization that completes without reaching any mirror (0 binaries cached), **When** the administrator runs `init`, **Then** the summary states that the cache was initialized with no mirror reachable (offline mode), rather than presenting a bare success.
4. **Given** a fresh cache with no database and an `ensure-base` that fails (e.g. no readable image), **When** the administrator runs `ensure-base`, **Then** the command exits non-zero and **no database file is created** (the database is created only after ingestion succeeds).

---

### User Story 2 - Command Flags and Arguments Behave as Documented and Consistently (Priority: P2)

The administrator runs `verify`, `verify --package X`, `systems add ""`, `add "vim="`, and `list --deps X --status all`. Today `verify --graph` is a decorative flag — `main()` runs the graph verification regardless of it, so bare `verify` already does the graph verification — and `verify --package X` bypasses the report-depth filter entirely; `systems add ""` and newline-containing names are accepted even though the spec says "any word", and the duplicate-name error path leaks the database connection; `add "vim="` (an empty pin) is silently unpinned while the identical line in a `--file` is rejected as malformed; and passing `--deps X --status all` together is silently resolved (status wins) with no documented rule. This phase removes the decorative `--graph` flag — bare `verify` is the single graph-verification command — makes argument validation consistent across paths, closes the connection on failure, and makes `--deps` and `--status` mutually exclusive (a clean usage error when both are given).

**Why this priority**: Silent inconsistencies between flag/argument paths and a decorative no-op flag are confusing and untestable; the connection leak is a resource bug on a repeatable error path.

**Independent Test**: A parametrized matrix — `verify` (the single graph-verification command; the `--graph` flag is gone), `verify --package X` under a finite report depth (respects the filter), `systems add ""`/`systems add $'a\nb'` (rejected cleanly, no leak), `add "vim="` and the same line in `--file` (both rejected), and `list --deps X --status all` (both flags together → clean usage error).

**Acceptance Scenarios**:

1. **Given** the `verify` command, **When** the administrator runs `verify` (the `--graph` flag no longer exists — it is removed from the CLI), **Then** bare `verify` runs the dependency-graph verification; there is no second, decorative invocation to confuse.
2. **Given** a `verify --package X` run under a finite report depth, **When** the report is generated, **Then** the report-depth filter is applied to the per-package output exactly as it is in the full-cache path.
3. **Given** an empty moniker or a moniker containing whitespace/newlines, **When** the administrator runs `systems add`, **Then** the command rejects it with a clean error, registers no record, and releases the database connection (no leak on the failure path).
4. **Given** an empty version pin (`name=`) in either the positional-argument path or a `--file` line, **When** the administrator runs `add`, **Then** both paths reject it consistently (the empty pin is not silently unpinned in one path and rejected in the other).
5. **Given** both `--deps X` and `--status all` on `list`, **When** the command runs, **Then** it refuses with a clean usage error (exit 1) — the two flags are mutually exclusive; each alone works as before.

---

### User Story 3 - Sync and Verify Reports Are Honest (Priority: P2)

The administrator re-runs `sync` for a system that has nothing pending, and runs `verify` under `max_build_dep_report_depth = 0`. Today a zero-pending `sync` early-returns without regenerating, leaving a previous non-empty `diff/` on disk — violating spec 013's expectation of an empty diff; and `verify`'s "Unresolved build deps" and "Missing build tools" lists are never filtered by `max_build_dep_report_depth`, so a strict reading of spec 014 FR-017 (`0` = no build-tool levels reported) is not met. This phase makes a zero-pending `sync` leave an empty diff and makes `verify`'s build-dep report respect the depth convention in every path.

**Why this priority**: A stale non-empty diff silently ships old packages to the operator, and an un-filtered build-dep report misrepresents a correctly depth-scoped cache as broken.

**Independent Test**: After a successful `sync`, re-run `sync` with nothing pending — the diff directory is empty (previous content cleared); `verify` under `max_build_dep_report_depth = 0` reports no build-tool levels, and under `-1`/`N` reports the full/limited set respectively, for both the full cache and `--package X`.

**Acceptance Scenarios**:

1. **Given** a system with a previously generated non-empty diff and no new pending packages, **When** the administrator re-runs `sync`, **Then** the diff directory is regenerated as empty (spec 013) — it does not retain stale content from the previous run.
2. **Given** `max_build_dep_report_depth = 0`, **When** `verify` runs, **Then** the "Unresolved build deps" and "Missing build tools" lists are empty (no build-tool levels reported, spec 014 FR-017).
3. **Given** `max_build_dep_report_depth = -1` and a positive `N`, **When** `verify` runs, **Then** `-1` reports all build-dep levels and `N` reports up to `N` levels, with no regression versus current behavior.
4. **Given** a `verify --package X` run, **When** the report is generated, **Then** the same depth filtering applies to the per-package build-dep output as to the full-cache path.

---

### User Story 4 - The Resolver Selects and Links Correct, Deterministic Entries (Priority: P1)

The administrator resolves packages where several candidates tie, where a build-dep fallback is needed, or where a provider was upgraded by an index refresh. Today an exact-version tie across components picks the wrong component (non-free over main — the inverse of spec 006 FR-005), producing the wrong pool path; a package current in two releases/components resolves arbitrarily (the current-binary lookup takes the first row with no defined ordering); the build-dep fallback links any current binary by name without re-checking the entry's version constraint (wrong link *and* a residual error for the same dep); the runtime-alternative "already satisfied" probe omits `status='current'` (the build-dep twin has it), so superseded-only rows can suppress alternative resolution; and a cached virtual resolution is not re-evaluated against same-release provider upgrades after an index refresh (spec 006 FR-005 partial). This phase makes selection deterministic and spec-compliant, and makes every link and probe constraint-aware.

**Why this priority**: Wrong-component selection and wrong/arbitrary links put the wrong package or a broken dependency into the cache — a correctness failure that surfaces later at the operator, and the direct subject of a written spec requirement (006 FR-005).

**Independent Test**: A fixture where the same package/version is current in `main`, `contrib`, and `non-free` — the resolver selects `main` and records the `main` pool path; a build-dep entry with a mismatched-version candidate is not linked and is reported unresolved (no residual error once a valid link exists); an alternative group satisfied only by a `current` provider resolves, while superseded-only rows do not suppress it; after an index refresh upgrades a provider in the primary source, a cached virtual resolution picks up the upgrade.

**Acceptance Scenarios**:

1. **Given** an exact-version tie across components, **When** the resolver selects a binary, **Then** the highest-priority configured component (`main`) wins (spec 006 FR-005) and the pool path reflects that component.
2. **Given** multiple current binaries for the same package+architecture, **When** the resolver looks up the current binary, **Then** the selection is deterministic (a defined ordering is used), never an arbitrary pick.
3. **Given** a build-dep entry whose fallback candidate does not satisfy the entry's version constraint, **When** the resolver links it, **Then** it does not link the mismatched entry, and once a valid constraint-satisfying link exists no residual "unresolved" error remains for that entry.
4. **Given** a runtime-alternative group whose only matching rows are superseded, **When** the resolver checks whether the group is already satisfied, **Then** the probe considers only `current` rows — superseded-only rows do not suppress alternative resolution (mirroring the build-dep probe).
5. **Given** an index refresh that upgrades a provider version within the primary source, **When** a previously cached virtual resolution is re-resolved, **Then** the upgrade is picked up (the cached resolution is not blindly reused for the same release, spec 006 FR-005).

---

### User Story 5 - The Database Lifecycle Is Consistent and Accurate (Priority: P2)

The administrator inspects `show`/`list`/`verify` and refreshes indexes over time. Today the provides-table population commits inside the caller's transaction (breaking `ensure-base`'s single-transaction claim) and counts already-present rows as newly inserted; `provides` rows from no-longer-configured releases/indexes are never swept, so a stale provider can be selected and then fail with "not found in index"; the circular report counts superseded rows and the `circular` flag is never reset on upsert, so `verify` and `list --deps` can disagree; and `packages.status` never transitions (always `pending`) while `requested_version` stores the *resolved* version for every transitive dependency, so `show`/`list` present misleading "requested" data. This phase makes the provides lifecycle atomic and swept, the circular state current-only and reset on upsert, and the status/requested-version data truthful.

**Why this priority**: Misleading database state undermines every read command and can drive incorrect operator-facing output; the provides sweep prevents hard "not found in index" failures on stale selections.

**Independent Test**: Refresh the provides table while a release is removed from configuration — the stale rows are gone and counts reflect only real inserts; a circular binary that is re-registered loses its `circular` flag and `verify`/`list --deps` agree; after resolution, `packages.status` reflects the lifecycle and a transitive dep with no explicit request shows no fabricated requested version.

**Acceptance Scenarios**:

1. **Given** a call to populate the provides table within the caller's transaction, **When** the table is refreshed, **Then** no nested commit is issued (the caller's single-transaction claim holds) and the reported inserted count reflects only rows actually inserted (rows already present are not counted as newly inserted).
2. **Given** provides rows originating from a release/index no longer in the configuration, **When** the provides table is refreshed, **Then** those stale rows are swept — they are never selected as providers.
3. **Given** a binary previously flagged circular that is re-registered (upserted), **When** `verify` and `list --deps` run, **Then** the `circular` flag is reset on the upsert and the circular report counts only `current` rows — the two commands agree.
4. **Given** a resolved package set, **When** `show`/`list` report status and requested versions, **Then** `packages.status` is `current` for packages with a current binary (transitioned from `pending`, not stuck), and `requested_version` holds the version actually requested — a transitive dependency that was not explicitly requested does not show the resolved version as if it were requested.

---

### User Story 6 - Path and Download Robustness (Priority: P2)

The administrator configures a relative `base_dir`, hits a server that returns a long `Retry-After`, and hands off from a cache path containing spaces (e.g. a USB mount). Today a relative `base_dir` is silently reinterpreted relative to the working directory (the path validation that is supposed to reject it can never fire); a server `Retry-After` of 86400 seconds stalls the run for a day while the internal backoff is capped at 30 seconds; and a cache path with spaces silently breaks the whitespace-delimited deb entry in the handoff, with no guard or diagnostic. This phase rejects the relative path cleanly, caps the server delay, and refuses a space-containing cache path with a clear, actionable diagnostic.

**Why this priority**: These are the operator-visible edge cases — an unbounded stall, a silently misconfigured path, and a broken handoff line on plausible media paths.

**Independent Test**: Load a config with a relative `base_dir` — clean config error (exit 102, key named); a download response with `Retry-After: 86400` is retried after at most the cap; generating a handoff from a cache under a path with spaces fails with a clear, actionable diagnostic and no `deb` line written.

**Acceptance Scenarios**:

1. **Given** a configuration with a relative `base_dir`, **When** the configuration is loaded, **Then** it is rejected with a clean configuration error naming the key (never silently reinterpreted relative to the working directory).
2. **Given** a download response whose `Retry-After` exceeds the internal backoff cap, **When** the downloader retries, **Then** the wait is capped at the same bound as the internal backoff — a run cannot stall for unbounded time on one response.
3. **Given** a cache whose path contains spaces, **When** the administrator generates a handoff, **Then** the handoff refuses with a clear, actionable diagnostic naming the path and the remedy (a space-free path or mount point), exits non-zero, and writes no broken `deb`/sources entry.

---

### User Story 7 - Doc and Test Artifacts Match Behavior (Priority: P3)

A maintainer reads the downloader's docstring/test names and the config test suite. Today the downloader docstring and a test name claim the opposite of the (correct, conservative) size-match re-hash behavior — docs backwards, code right — and the mirror-normalization tests live inside `TestProfileFilesAreData` without their own class header (they run, but are mislabeled). This phase aligns the doc/test labels with actual behavior.

**Why this priority**: Lowest impact — cosmetic, but misleading labels erode confidence in the test suite and the documented behavior.

**Independent Test**: The downloader docstring and the size-match test name describe the conservative re-hash behavior; the mirror-normalization tests are grouped in a dedicated `TestMirrorNormalization` class.

**Acceptance Scenarios**:

1. **Given** the downloader module, **When** the docstring and the size-match test name are read, **Then** they describe the actual conservative behavior (a size match does not skip the re-hash), not its opposite.
2. **Given** the config test module, **When** the mirror-normalization tests run, **Then** they are declared under a dedicated `TestMirrorNormalization` test class (the misleading placement under `TestProfileFilesAreData` is gone).

---

### Edge Cases

- **Exit-code contract (Phase 1)**: stale-schema vs missing database; ENOSPC vs permission; mirror-unreachable `init` (offline success); `ensure-base` failing before vs after any database creation; a pre-existing valid database on a failed `ensure-base` re-run.
- **Flags & arguments (Phase 2)**: bare `verify` as the single graph-verification command (`--graph` removed); `--package X` with and without a finite report depth; empty and whitespace/newline monikers; duplicate moniker (connection leak path); empty pins in positional vs `--file` paths; `list` with both `--deps X` and `--status all` (mutually exclusive → usage error) and with each flag alone.
- **Honest reports (Phase 3)**: zero-pending `sync` after a non-empty diff; `verify` build-dep report at `-1`, `0`, and positive `N`; per-package build-dep depth.
- **Resolution (Phase 4)**: exact-version ties across components; same package+arch current in two releases/components; build-dep fallback with and without a constraint-satisfying candidate; superseded-only provider rows; same-release provider upgrade after an index refresh.
- **Database lifecycle (Phase 5)**: provides refresh with removed releases/indexes; already-present rows skipped vs newly inserted; re-upserting a circular binary; status transitions across add/snapshot/supersede; transitive deps with no explicit version request.
- **Path & download (Phase 6)**: relative `base_dir` in defaults vs local overrides; `Retry-After` absent, zero, small, and huge; cache paths with spaces in mount points and nested directories (refused with a clear diagnostic).
- **Hygiene (Phase 7)**: the size-match re-hash docstring and test name; the mirror-normalization test class placement.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `add` on a stale-schema database MUST exit with the same stale/missing-database code (101) as its sibling commands, never the generic error code (1).
- **FR-002**: `init` MUST map only genuine permission failures to the permission code (100); other initialization failures (e.g. insufficient disk space) MUST use the generic error code (1), and an `init` that completes without reaching any mirror (0 binaries cached) MUST state in its summary that no mirror was reachable, rather than presenting a bare success.
- **FR-003**: A failed `ensure-base` on a cache with no database MUST NOT create the database file; the database MUST be created only after ingestion succeeds (spec 008 US1 AS2).
- **FR-004**: The decorative `verify --graph` flag MUST be removed — bare `verify` is the single dependency-graph verification command — and a `verify --package X` invocation MUST apply the report-depth filter to its output exactly as the full-cache path does.
- **FR-005**: `systems add` MUST reject an empty moniker and any moniker containing whitespace or newlines with a clean error and no partial record, and a failure (e.g. duplicate moniker) MUST release the database connection.
- **FR-006**: An empty version pin (`name=`) MUST be rejected consistently in both the positional-argument and `--file` paths of `add`, and `list` MUST refuse to run with **both** `--deps X` and `--status all` given (clean usage error, exit 1); each flag alone works as before.
- **FR-007**: A `sync` run with zero pending packages MUST leave an empty diff for the system (spec 013 AS2) — the diff directory is regenerated as empty, not left holding stale content from a previous run.
- **FR-008**: The `verify` "Unresolved build deps" and "Missing build tools" lists MUST respect `max_build_dep_report_depth` (`0` = no build-tool levels reported, `-1` = all, `N` = up to `N` levels — spec 014 FR-017), in both the full-cache and `--package` paths.
- **FR-009**: When multiple current binaries match the same package/architecture, selection MUST be deterministic, and on an exact-version tie across components the highest-priority configured component (`main`) MUST win (spec 006 FR-005) — the selection MUST NOT be arbitrary.
- **FR-010**: The build-dependency fallback MUST re-check the linked entry's version constraint before linking, MUST link only a constraint-satisfying entry, and MUST NOT leave a residual "unresolved" error when a valid link is made.
- **FR-011**: The runtime-alternative "already satisfied" probe MUST consider only `status='current'` rows (mirroring the build-dependency probe); superseded-only rows MUST NOT suppress alternative resolution.
- **FR-012**: A cached virtual resolution MUST be re-evaluated against refreshed-index provider candidates even when the cached provider is from the primary source — a same-release provider upgrade after an index refresh MUST be picked up (spec 006 FR-005).
- **FR-013**: Provides-table population MUST NOT commit inside the caller's transaction, and MUST report only rows actually inserted (rows skipped because they already existed MUST NOT be counted as inserted).
- **FR-014**: `provides` rows originating from releases/indexes no longer in the configuration MUST be swept when the provides table is refreshed, so stale providers are never selected.
- **FR-015**: The circular-dependency report MUST count only `status='current'` rows, and the `circular` flag MUST be reset when a binary is re-registered (upserted), so `verify` and `list --deps` agree.
- **FR-016**: `packages.status` MUST transition through the defined lifecycle — `pending` (registered) → `current` (a current binary is registered), never stuck at `pending` — and `requested_version` MUST hold the version actually requested — a transitive dependency not explicitly requested MUST NOT show the resolved version as its requested version.
- **FR-017**: A relative `base_dir` MUST be rejected with a clean configuration error (the config exit code, naming the key) instead of being silently reinterpreted relative to the working directory; the ineffective absolute-path validation that can never fail MUST be corrected.
- **FR-018**: A server-provided `Retry-After` delay MUST be capped at the same bound as the internal backoff, so a download run cannot stall for unbounded time on a single response.
- **FR-019**: A cache path containing spaces MUST be refused by the handoff with a clear, actionable diagnostic naming the path and the remedy (a space-free path or mount point), with a non-zero exit and **no `deb`/sources entry written** — never a silently-broken entry.
- **FR-020**: Docstring and test artifacts that contradict or mislabel actual behavior MUST be corrected — the downloader's size-match re-hash docstring and test name (conservative re-hash behavior) and the mirror-normalization tests' missing dedicated test class.

### Key Entities *(include if feature involves data)*

- **Exit-code contract**: the documented mapping of command failures to exit codes (0 success, 1 generic, 100 permission, 101 stale/missing database, 102 config error); `add` (P-1), `init` (P-4), and `ensure-base` (P-5) must conform to it.
- **Database file lifecycle**: whether a command creates the database at all; a failed `ensure-base` must not create it (P-5), and `init` must not leave an un-explained state (P-4).
- **Current-binary selection**: the deterministic, spec-compliant rule for choosing among multiple current binaries on version/component/architecture ties (P-9, P-16).
- **Link/probe constraint state**: build-dep fallback links (P-10), runtime-alternative "already satisfied" probes (P-11), and cached virtual resolutions (P-12) all carry version/status constraints that must be honored.
- **Provides lifecycle**: how `provides` rows are inserted, counted, committed, and swept relative to the configured releases/indexes (P-13, P-14).
- **Circular state**: the `circular` flag on binaries — current-only reporting and reset-on-upsert (P-15).
- **Status/requested state**: `packages.status` transitions `pending → current` (FR-016) and `requested_version` accuracy on `packages` (P-17).
- **Config path validation**: `base_dir` must be absolute or rejected (P-19).
- **Download backoff**: the capped server `Retry-After` (P-20).
- **Handoff deb/sources entry**: the operator apt entry must never be silently broken — a space-containing cache path is refused with a clear, actionable diagnostic (P-21).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every command failure covered by this feature maps to the documented contract exit code (stale/missing database 101, permission 100 only for permission problems, generic 1 otherwise), and no covered failure leaves a stray database file or an un-flagged offline state.
- **SC-002**: `verify`'s flags and argument handling behave as documented — the decorative `--graph` flag is removed (bare `verify` is the single graph-verification command), `--package X` respects the report-depth filter, empty pins and empty/whitespace monikers are rejected consistently across paths, and `list` refuses to run with both `--deps X` and `--status all` (mutually exclusive, clean usage error).
- **SC-003**: A zero-pending `sync` leaves an empty diff, and `verify`'s build-dep lists honor `max_build_dep_report_depth` (`0` reports none, `-1` all, `N` up to `N`) in both the full-cache and per-package paths.
- **SC-004**: On exact-version/component ties the configured priority (`main`) wins and current-binary selection is deterministic; build-dep fallbacks link only constraint-satisfying entries with no residual errors; runtime-alternative probes consider only current rows; and cached virtual resolutions pick up same-release provider upgrades.
- **SC-005**: The provides table is refreshed without nested commits or phantom insert counts, stale providers are swept, circular reports count only current rows with the flag reset on upsert, and `packages.status`/`requested_version` reflect the actual lifecycle and requested versions.
- **SC-006**: A relative `base_dir` fails cleanly at load; downloads never stall beyond the capped backoff; and a space-containing cache path is refused by the handoff with a clear, actionable diagnostic and no broken `deb` entry.
- **SC-007**: All corrected doc/test artifacts (downloader docstring/test name, mirror-normalization test class) match actual behavior, and the documented test suite (both pytest and `python -m unittest`) is green.

## Assumptions

- **Scope source**: The authoritative scope is §1 "Polish backlog" of `review/04_alpha_summary/03_polish_backlog_docs.md` — findings P-1…P-23. Per the user's instruction, §2 (documentation refresh), §3 (spec/doc drift reconciliation), §4 (test-coverage gaps), and §5 (process reminders) of that document are **out of scope** for this feature.
- **P-8 already fixed**: The unknown/mistyped-config-key finding (P-8) was resolved by spec 018's F-20 (unknown keys are now rejected with a structured config error). It is excluded from this feature except as a regression check — the behavior must remain.
- **Contract-preserving**: The exit-code/JSON contract table (`specs/014-base-profiles/contracts/cli.md`) is extended, not diverged from; `tests/contract/test_cli.py` must stay green.
- **Schema freeze**: The spec 010/011 schema freeze holds — no database schema changes. The status lifecycle (FR-016: `pending → current`) and circular reset (FR-015) are implemented within existing columns; `requested_version` for a transitive dep with no explicit request is set to "none requested" (e.g. empty/NULL), not a new column.
- **`verify --graph` removed**: The decorative `--graph` flag is dropped — bare `verify` is the single dependency-graph verification command, matching its actual behavior today (the flag currently has no effect). The `verify --package X` report-depth filter fix remains in scope. This deliberately supersedes the current decorative flag and any doc/contract references to `verify --graph` are updated in the same feature cycle (Constitution VIII). This is the only intentional behavioral change to `verify` beyond the depth-filter fix.
- **Handoff space-path refusal (P-21)**: Per the Clarifications session, a cache path containing spaces is **refused** with a clear, actionable diagnostic (no space-escaping of the `deb` line). The FR-019 "working entry" branch is intentionally dropped; the diagnostic names the path and the remedy (a space-free path or mount point).
- **`list` flags mutually exclusive (P-7b)**: Per the Clarifications session, passing both `--deps X` and `--status all` is a **usage error** (exit 1) — a deliberate change from the current silent status-wins behavior; each flag alone works as before.
- **Determinism preserved**: Behavior not covered by one of the P-items is unchanged; existing green tests stay green (the cosmetic test refactor in P-23 must not alter test coverage).
- **Alpha sequencing**: This is the final clean-up feature of the alpha cycle, after 015, 016, 017, and 018; it is complete when all seven phases are implemented and both documented test runners are green.

## Clarifications

No [NEEDS CLARIFICATION] markers remain in the spec; decisions are recorded in the sessions below.

### Session

- Q: For cache paths containing spaces (FR-019 / P-21), should the handoff produce a working deb entry (space-escaping) or refuse with a diagnostic? → A: Option A — refuse with a clear, actionable diagnostic (exit 1, no broken deb line); the admin/operator is told to place the cache at a space-free path or mount point. The "working entry" branch is dropped.
- Q: What lifecycle should `packages.status` transition through (FR-016)? → A: Option A — minimal `pending → current`: `pending` when registered, `current` when the resolver registers a current binary. No `superseded`/`failed` states in this feature (schema-freeze compatible).
- Q: What precedence should `list --deps X --status all` follow (FR-006 / P-7b)? → A: Option C — passing **both** `--deps X` and `--status all` is a usage error (clean error, exit 1); each flag alone works as before. This deliberately changes the current silent status-wins behavior.
