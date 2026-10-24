# Feature Specification: Alpha Release Blockers

**Feature Branch**: `015-alpha-release-blockers`
**Status**: Draft

**Input**: User description: "Spec alpha release blockers - Before tagging alpha the blockers should be tackled. The current session already laid out the plan in a markdown file, each of the 6 entries should map cleanly to phases of the same spec. Nevertheless, use common sense and think hard."

**Design context**: Two pre-release reviews (`review/02_alpha_kimi_k3/`, `review/03_alpha_glm_5_3/`) identified alpha-blocking defects. The execution plan lives in `review/04_alpha_summary/01_alpha_blockers.md`; this spec turns its six blocker entries (B-1 … B-6) into six phases of one feature so the blockers are tackled before the alpha tag. Findings deferred to `review/04_alpha_summary/02_high_priority_followups.md` and `03_polish_backlog_docs.md` are **out of scope** for this feature.

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Builds the cache; the fixes change what `add`, `ensure-base`, `verify`, `config`, `sync`, `handoff` produce and report |
| **Operator** | Offline system (no network) | Installs from the shipped cache; the fixes change what `bootstrap.sh`/`apply.sh` and the shipped repository guarantee |

**Phase 1 — Complete Runtime Closures (Pre-Depends)**: every hard runtime requirement is included in the closure, so the shipped cache is installable. *(B-1)*
**Phase 2 — Order-Independent Source Coverage**: source/build-tool coverage for finite-depth profiles is independent of graph traversal order, and batch resolution is failure-safe. *(B-2)*
**Phase 3 — Safe Diff & Delivery Ordering**: an operator diff never references a missing payload, and delivery state never advances for a failed sync. *(B-3)*
**Phase 4 — Fail Hard on Linkless Media**: the operator bootstrap fails hard with a clear, actionable message when media cannot carry symbolic links — it never reports success with an unreachable pool. *(B-4)*
**Phase 5 — No-Expiry Repository & Generation-Time Dating**: the operator repository imposes no expiry deadline, and its `Date` reflects generation time (not a frozen cache timestamp); reproducible regeneration remains an explicit opt-in. *(B-5)*
**Phase 6 — Clean Machine-Readable Errors & Green Documented Runner**: machine-readable error output is pure, and every documented test command passes. *(B-6)*

---

### User Story 1 - Complete, Installable Runtime Closures (Priority: P1)

The cache must be genuinely installable offline. Today a package whose runtime requirement is declared as a Pre-Depends (a hard requirement used by `dpkg`, `bash`, `dash`, `coreutils`, `systemd`, …) and is **not** also duplicated in `Depends` is silently missing from the cache — the operator's install of that package fails. This phase makes the resolved runtime closure complete for every hard requirement, closing the hole in the "fully installable cache" promise for all three profiles.

**Why this priority**: This is the blocker that can make the cache genuinely **not installable**; it affects every profile, most acutely `regular_user` whose cache contains only the runtime closure.

**Independent Test**: Add a fixture package whose closure includes a Pre-Depends-only requirement, download, hand off, and run an offline `apt-get install` simulation — the install succeeds with zero missing packages, and the Pre-Depends target appears in the cache.

**Acceptance Scenarios**:

1. **Given** a package whose closure includes a Pre-Depends target that is **not** listed in `Depends`, **When** the administrator adds and downloads it, **Then** the Pre-Depends target is present in the cache and the package installs offline with zero missing packages.
2. **Given** the same package added under each of the three profiles (`regular_user`, `developer`, `graybeard`), **When** the cache is handed off and the operator installs the package offline, **Then** the install succeeds from the cache alone.
3. **Given** a Pre-Depends that is also duplicated in `Depends`, **When** the package is resolved, **Then** it appears exactly once in the closure (no duplicate work, no duplicate records).

---

### User Story 2 - Order-Independent Source Coverage (Priority: P1)

The developer profile's central promise — sources and build tools for the requested set and everything within the configured depth — must not depend on the order packages happen to be visited. Today a package reachable both shallowly and deeply is assigned the depth of its **first** visit, so direct dependencies routinely lose their sources under diamond-shaped dependency graphs (e.g. `libc6`), and the cache still reports COMPLETE. Two related resolution defects compound this: a source created out of scope for a virtually-provided binary is never repaired on later runs, and a mid-batch failure can leave later segments silently skipping packages that were never committed. This phase makes source coverage order-independent and batch resolution failure-safe.

**Why this priority**: The developer profile's headline feature silently under-delivers on real archives; the companion defects produce silent closure holes that no command currently reports.

**Independent Test**: Build a diamond fixture (`app` depends on `c1` and `x`; `c1` depends on `x`; depth 1) and assert `x` receives its source and build tools regardless of `Depends`-line ordering; force a mid-batch failure and assert no later segment silently skips a package.

**Acceptance Scenarios**:

1. **Given** a package reachable at multiple runtime depths and a finite source depth, **When** the administrator adds it, **Then** the package receives source/build-tool support if any of its paths is within the configured depth — the source set is identical regardless of dependency ordering.
2. **Given** a virtually-provided binary first resolved out of scope, **When** it is later reached in scope (same or later run), **Then** its source and build tools are created — missing sources are repaired, never silently ignored.
3. **Given** a batch of requests where one segment fails mid-way with an unexpected error, **When** the batch continues, **Then** later segments never treat uncommitted packages as resolved — each ends either fully committed or with an explicit error.

---

### User Story 3 - Safe Diff & Delivery Ordering (Priority: P1)

A failed pool-copy must never poison the operator's incremental delivery. Today a single failed copy leaves a diff that references a payload it does not contain (the operator's checksum gate then refuses the **entire** diff, forever), and the delivery record is committed before the failure is reported, so the rebuilt diff dead-ends. This phase orders the sync pipeline so a diff is only produced from successfully copied payloads and delivery state only advances on a clean result.

**Why this priority**: One failed copy makes the operator's `sync` workflow permanently unusable — a reliability failure in a core operator path.

**Independent Test**: Run a sync where one pool file is missing/unreadable — the command exits non-zero, the produced diff contains no stanza for the failed record, and no delivery record advances; a subsequent clean run works.

**Acceptance Scenarios**:

1. **Given** a pending package whose pool file cannot be copied, **When** the administrator runs sync, **Then** the command fails with a clear error and the produced diff contains no entry referencing the missing payload.
2. **Given** the same failed sync, **When** the administrator inspects the delivery state, **Then** no delivery record has advanced for the failed run.
3. **Given** the failure is repaired, **When** the administrator re-runs sync, **Then** the diff is produced cleanly and delivery advances exactly for the successfully shipped packages.

---

### User Story 4 - Fail Hard on Linkless Media (Priority: P2)

The primary offline transport is USB/FAT, which cannot carry symbolic links. Today the shipped bootstrap ignores the failed link creation, configures apt, reports **success** — and every subsequent install fails because the pool is unreachable. This phase makes the operator bootstrap **fail hard** (with a clear, actionable message and a non-zero exit) when the pool cannot be reached through the handoff — it never reports success — and makes its completion check actually verify the cache is reachable.

**Why this priority**: This is the primary transport scenario; silent success with an unusable cache is the worst possible operator outcome (the project's own research claimed the link survives such media — it does not).

**Independent Test**: Run the generated bootstrap on a FAT-formatted loop device (or an equivalent no-symlink scratch area) — it fails hard with a clear message and never reports success while the pool is unreachable.

**Acceptance Scenarios**:

1. **Given** media that cannot carry symbolic links, **When** the operator runs the bootstrap, **Then** the tool exits with a clear, actionable error and a non-zero exit code — it never configures a broken source and never reports success.
2. **Given** a cache whose pool or Release is missing/unreachable, **When** the operator runs the bootstrap, **Then** the completion check detects the problem and the bootstrap fails hard without reporting success.

---

### User Story 5 - No-Expiry Repository & Generation-Time Dating (Priority: P2)

The operator's repository carries a `Valid-Until` time-bomb anchored to the cache directory's creation time — every cache hard-expires about a year after creation, and **regeneration cannot refresh it**. This phase removes the imposed expiry entirely (the repository carries no enforced validity deadline, so apt never refuses it because of age) and anchors the repository's `Date` to **generation time** so the frozen-timestamp bug cannot recur. Reproducible (byte-identical) regeneration remains available as an explicit opt-in.

**Why this priority**: Without this, every cache silently dies within a year of creation regardless of maintenance, and reproducibility tooling can pin a repository to an instantly-dead date.

**Independent Test**: Generate a handoff, then regenerate it — the repository's `Date` advances to the regeneration time and the repository imposes no expiry (apt accepts it regardless of its age); the reproducibility path still produces a pinned, deterministic result when explicitly requested.

**Acceptance Scenarios**:

1. **Given** an existing handoff, **When** the administrator regenerates it, **Then** the repository's `Date` reflects the regeneration time and the repository carries no enforced validity deadline — apt does not refuse it solely because the cache directory or repository is old.
2. **Given** a repository generated long ago with no regeneration, **When** the operator configures it as an apt source, **Then** apt accepts it (no expiry is enforced), because the repository imposes no deadline.
3. **Given** a reproducibility tool that sets the deterministic-timestamp input, **When** the administrator regenerates, **Then** the repository's `Date` is pinned to the requested input (reproducible output), but only when that input is explicitly provided.

---

### User Story 6 - Clean Machine-Readable Errors & Green Documented Runner (Priority: P2)

Machine-readable error output must be usable by scripts, and the documented test commands must all pass. Today JSON error exits emit log text on the same stream ahead of the JSON payload (breaking any script that parses stderr), and the project's documented test command fails 8 tests while an alternate runner is green — masking the contract bug. This phase makes machine-readable error output pure and makes all documented test commands pass from a clean checkout.

**Why this priority**: It is both a release-hygiene blocker (the documented test command is red) and a real product defect (non-JSON noise on a machine-readable error stream).

**Independent Test**: Trigger a JSON-mode configuration error and assert the error stream contains exactly one JSON document; run every documented test command from a clean checkout and assert they all pass.

**Acceptance Scenarios**:

1. **Given** a command run in machine-readable mode that fails, **When** the error output is inspected, **Then** the output stream contains exactly the machine-readable payload and no log or diagnostic text.
2. **Given** a clean checkout, **When** each documented test command is run, **Then** all tests pass under every documented command (no runner is red while another is green for the same suite).

---

### Edge Cases

- **Pre-Depends (Phase 1)**: a Pre-Depends target also present in `Depends`; a Pre-Depends with a version constraint; a Pre-Depends satisfied by a virtual provider; a package whose entire closure is pre-dep-only; circular pre-dependencies (must not loop).
- **Resolver depth/batch (Phase 2)**: diamond graphs at depths `0`, `1`, `N`, and `-1`; a virtual provider reached first deep then shallow across separate runs; a mid-batch exception after some registrations; a failed segment followed by a segment sharing a dependency.
- **Diff/delivery (Phase 3)**: one failed copy among many successes; a mix of new and previously-delivered records; re-running after repair; a diff whose index entry is missing or whose pool file is absent.
- **Bootstrap (Phase 4)**: FAT media with no symlink support; a cache path containing spaces; a missing `Release` file; a pool directory present but empty; re-running the bootstrap after a partial setup.
- **Validity (Phase 5)**: a repository generated long ago (must still be accepted by apt — no expiry enforced); regeneration with the deterministic-timestamp input set; regeneration without it; a cache whose directory mtime is unchanged (the current frozen-`Date` failure mode).
- **Errors/runners (Phase 6)**: every JSON-mode error path (config, handoff, sync, and DB-gated commands); running the suite under each documented runner; the interplay between logging configuration and error output.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST account for `Pre-Depends` when building a package's runtime dependency closure; every Pre-Depends target MUST be included in the resolved set even when it is not also listed in `Depends`.
- **FR-002**: A cache whose requested packages' full runtime closure (including Pre-Depends) is present MUST support offline installation of those packages with zero missing dependencies.
- **FR-003**: With a finite source depth, a binary reachable at any path within the configured depth MUST receive its source and build-tool support — the in-scope source set MUST be identical regardless of dependency traversal order (minimum-level semantics).
- **FR-004**: A binary whose source was not created because it was first reached out of scope MUST gain its source and build tools when it is later reached in scope, including across separate runs and for virtually-provided binaries.
- **FR-005**: A failed segment of a batch MUST NOT leave stale resolution state that causes later segments to treat never-committed packages as resolved; such packages MUST be either committed or explicitly reported as errors.
- **FR-006**: The generated operator diff MUST reference only payloads that were actually copied successfully; a failed copy MUST NOT leave a diff entry referencing a missing payload.
- **FR-007**: Delivery records MUST advance only after a diff has been generated completely and successfully; a failed or partial sync MUST NOT advance delivery state.
- **FR-008**: When the operator bootstrap cannot reach the pool through the handoff directory (e.g. media that cannot carry symbolic links), it MUST fail hard — exiting non-zero with a clear, actionable message — and MUST NOT report success with an unreachable pool.
- **FR-009**: The bootstrap completion check MUST verify that the configured apt source resolves to a reachable pool and a valid repository before reporting success.
- **FR-010**: The operator repository MUST NOT impose an enforced validity deadline (no expiry field), and its `Date` MUST reflect the generation time — a regenerated repository is never refused by the operator's apt because of its age, and a stale cache is not silently treated as fresh due to a frozen cache timestamp.
- **FR-011**: Reproducible, byte-identical regeneration MUST remain possible as an explicit opt-in; it MUST NOT be the default behavior and MUST NOT silently pin the repository to an arbitrary date.
- **FR-012**: In machine-readable output mode, a failing command MUST emit exactly the machine-readable payload on the error stream and no log or diagnostic text mixed into it.
- **FR-013**: Every documented test command MUST pass all tests from a clean checkout; no documented command MAY be red while another documented command is green for the same suite.

### Key Entities *(include if feature involves data)*

- **Runtime dependency closure**: the set of binary packages required for a requested package to install; extended by this feature to include Pre-Depends targets.
- **Source scope**: the subset of the runtime closure that receives source/build-tool support, bounded by `max_source_dep_depth`; made order-independent by this feature.
- **Operator diff**: the minimal incremental repository handed to the operator (spec 013); must never reference missing payloads.
- **Delivery record**: the snapshot marker that records which packages were shipped to a system; must advance only on clean success.
- **Operator repository validity**: the operator repository imposes no expiry deadline and its `Date` reflects generation time (never a frozen cache timestamp); regenerating refreshes `Date`.
- **Machine-readable error contract**: the guarantee that machine-readable error output is a single clean payload (spec 014 contracts).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a validation package set covering Pre-Depends-only requirements, every package installs offline from the cache with zero missing dependencies across all three profiles.
- **SC-002**: Adding the same package set under the developer profile produces the same in-scope source/build-tool set regardless of dependency ordering (diamond cases included), and that set is complete for all in-scope binaries.
- **SC-003**: A sync run that fails on any payload copy never produces an operator diff referencing a missing payload and never advances delivery state; a subsequent clean run succeeds.
- **SC-004**: The operator bootstrap run on linkless media exits non-zero with a clear, actionable error and never reports success with an unreachable pool.
- **SC-005**: A cache handed off at any age is accepted by the operator's apt (the repository imposes no expiry deadline), the repository's `Date` reflects the most recent regeneration, and the reproducibility opt-in still yields a pinned, deterministic repository.
- **SC-006**: Every machine-readable error exit produces exactly one machine-readable payload on the error stream, and every documented test command passes 100% of the suite from a clean checkout.

## Assumptions

- **Scope source**: The six blocker entries (B-1 … B-6) in `review/04_alpha_summary/01_alpha_blockers.md` are the authoritative scope; the plan's completion checklist (live-mirror 3-profile runs, offline installability proof incl. FAT loop device, Pre-Depends census, both runners green) is the acceptance evidence for this feature.
- **Out of scope**: All findings deferred to `review/04_alpha_summary/02_high_priority_followups.md` (including H-2, the ensure-base verified-flag reset, and the P1 robustness items) and `03_polish_backlog_docs.md` are not part of this feature.
- **No schema changes**: The spec 010/011 schema freeze holds; all fixes are behavior/config changes that do not alter the database schema.
- **Regression-preserving**: Behavior not covered by a blocker is unchanged; where a blocker rewrites an error path or ordering, the existing exit-code/JSON contracts (spec 014 `contracts/cli.md`) continue to hold.
- **Order of phases**: Phase order is the plan's execution order; each phase is independently implementable and testable, and phases may land in the same release.
- **Alpha gate**: This feature is complete when all six phases are implemented and the completion checklist passes; the alpha tag is not created before that.

## Clarifications

*None pending — the execution plan in `review/04_alpha_summary/01_alpha_blockers.md` (and the prior session's discussion) settled scope, ordering, and boundaries before this specification was written.*
