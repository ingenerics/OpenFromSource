# Feature Specification: Alpha Cut-off

**Feature Branch**: `020-alpha-cut-off`
**Status**: Draft

**Input**: User description: "Spec alpha cut off - With what we just discussed about the latest alpha review at `review/05_alpha_final_kimi_k3/ALPHA_REVIEW.md` turn the 4 final high prio issues into the final spec that will be implemented before alpha release."

**Design context**: The final alpha review (`review/05_alpha_final_kimi_k3/ALPHA_REVIEW.md`) closed all six prior blockers (B-1…B-6) and the high-priority follow-ups, but reported four new findings — N-1 (stale `provides` after an offline `init`), N-2 (cached-virtual source repair depth-gated at `-1`), N-3 (constraint-blind "already satisfied" alternative probes), and N-4 (the signing keyring absent from a standard wheel/sdist install). This is the last feature of the alpha cycle, sequenced after 019 (polish backlog); it is the review-triage outcome, converted into the final pre-alpha release spec.

The four findings were triaged in session before this spec was written, and split into **two code fixes** and **two accepted-as-documented-limitation resolutions**:

- **N-3 — implemented (code)**: the two alternative-group "already satisfied" probes (runtime and build-dep) are constraint-blind — they treat any `current` DB row as satisfying, even when its version fails the member's own constraint, so a version-skewed `a (>= V) | b` group resolves to *nothing*, `add` exits 0 while `verify` reports INCOMPLETE, and `apt` cannot install the result. The sibling build-dep link block already evaluates each candidate's version against its constraint; the probes must mirror it. This is the last `add`/`verify` contract contradiction.
- **N-4 — implemented (code)**: the archive-signature trust anchor (`debian-archive-keyring.gpg`) is not declared as package data, so a wheel/sdist built by the declared backend contains only `.py` files; a standard (non-editable) install then fails to verify any remote index and the tool is unusable against real archives (it fails closed). The keyring must ship in the built artifact and the artifact must be inspected before tagging.
- **N-1 — accepted as documented limitation (docs only)**: `init`'s job is network work (fetch indexes + build the `provides` snapshot); running it offline is not a supported flow, and the air-gapped path (`ensure-base`) self-bootstraps and populates `provides` from the image itself. Per triage, no resolver code change — the guidance "run `init` on a connected system; if it reports 0 binaries or a partial fetch, start again from scratch" is documented instead.
- **N-2 — accepted as documented limitation (docs only)**: the graybeard profile is pulled out of the grow-deeper chain — it stands on its own (fresh cache from the get-go, matching the existing fresh-cache-only `ensure-base` refusal), while `regular_user → developer` (and deepening developer depth) remains a supported transition. The cached-virtual source-repair gap that N-2 describes can then only arise in an unsupported flow; the documentation is updated to say so.

Scope: the two code fixes (N-3, N-4) with their tests, plus the two documentation resolutions (N-1, N-2). The low-severity findings (L-1…L-13) and the process/evidence notes (§3, §6, §7 items 5-6 of the review) are **out of scope** for this feature.

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Runs `init`, `add`, `ensure-base`, `verify`; this feature makes alternative groups resolve constraint-correctly (N-3), makes a standard install of the tool able to fetch verified indexes (N-4), and gives clear written guidance on when `init` requires connectivity (N-1) and that graybeard is a fresh-cache-only profile (N-2) |
| **Operator** | Offline system (no network) | Installs packages from the pre-built cache; indirectly protected — the cache handed off is never "successfully resolved" while being uninstallable (N-3), and the tool never ships broken against real archives (N-4) |

**Phase 1 — Constraint-Aware Alternative Probes**: the two alternative-group "already satisfied" probes evaluate each member's version against its own constraint, try the alternatives when no DB row constraint-wise satisfies, and only then retract the failed-primary error. *(N-3, code)*
**Phase 2 — Distributable Trust Anchor**: the archive-signing keyring is declared as package data so the built wheel/sdist contains it, and the artifact is inspected before tagging. *(N-4, code)*
**Phase 3 — Honest `init` Guidance**: the documentation states that `init` must run on a connected system and that an offline/partial `init` is not a usable state — start again from scratch. *(N-1, docs)*
**Phase 4 — Graybeard Standalone**: the profile documentation removes graybeard from the grow-deeper chain and states it is a fresh-cache-only profile; `regular_user → developer` remains the documented supported direction. *(N-2, docs)*

---

### User Story 1 - Version-Skewed Alternative Groups Resolve Correctly (Priority: P1)

The administrator manages a multi-era cache: a package was pinned to `a 1.0` in an earlier batch, and a later `add app` depends on `a (>= 2.0) | b` where the archive holds only `a 1.0` and `b`. Today the primary fails, but the "already satisfied" probe sees the `a 1.0` row and — without checking that `1.0` satisfies `>= 2.0` — declares the group satisfied, never tries `b`, retracts the error, and `add` exits 0. The closure then lacks `b` (which `apt` would install, because a too-old primary does not win an alternative); `verify` reports INCOMPLETE; and the operator's `apt-get install app` fails against a cache the administrator was told resolved successfully. The same blind probe in the build-dep path silently skips a resolvable alternative and records "Build tool not resolved", rendering a buildable source unbuildable. This phase makes both probes constraint-aware — mirroring the sibling link block that already evaluates each candidate's version against its own name's constraint.

**Why this priority**: This is a correctness hole in the installable-cache promise — a cache reported as successfully resolved that `verify` flags INCOMPLETE and `apt` cannot install. It is the exact state the earlier F-8/F-13 fixes were meant to eliminate, one probe short of the finish line.

**Independent Test**: Pre-register `a 1.0` as `current`; resolve a dependency `a (>= 2.0) | b` where the index holds `a 1.0` and `b 1.0` — `b` is resolved into the closure, no error remains, `add` exits 0, and `verify` reports COMPLETE. The build-dep twin with a resolvable alternative links it and reports no "Build tool not resolved" error.

**Acceptance Scenarios**:

1. **Given** a package `a 1.0` already `current` in the cache and a dependency `a (>= 2.0) | b` with `b` available in the index, **When** the closure is resolved, **Then** the too-old `a 1.0` does NOT satisfy the group, `b` is resolved into the closure, no group error remains, `add` exits 0, and `verify` reports COMPLETE (the cache is apt-installable).
2. **Given** the same version-skewed group in a build-dependency, **When** the build-dep pass resolves it, **Then** the resolvable alternative is linked (the build tool is recorded as resolved) and no "Build tool not resolved" error remains.
3. **Given** an alternative group whose member versions DO satisfy their constraints, **When** the closure is resolved, **Then** the group is treated as already satisfied as today — the provider over-inclusiveness of the provides-join probe is preserved and no behavior regresses.

---

### User Story 2 - A Standard Install Can Fetch Verified Indexes (Priority: P1)

A maintainer installs the tool the standard way — building a wheel from the source and installing it — rather than the editable install the dev flow uses. Today the wheel contains only Python modules: the archive-signing keyring (`debian-archive-keyring.gpg`) is never declared as package data, so the file is absent from the artifact. Every remote-mirror `init`/`add`/`download` then refuses the archive's signature and the tool is useless against any real mirror (it fails closed — safe, but non-functional). This phase declares the keyring as package data, rebuilds, inspects the artifact to confirm the file is present, and confirms the supported install modes in the documentation.

**Why this priority**: The alpha is about to be tagged; a distribution artifact that cannot talk to any real archive defeats the alpha promise for anyone who does not use the editable-install dev flow.

**Independent Test**: Build the wheel and sdist from the current source; list the archive contents — the keyring file is present; a standard (non-editable) install of the wheel in a clean environment successfully runs `init`/`add` against a loopback or real mirror that requires signature verification.

**Acceptance Scenarios**:

1. **Given** the current source tree, **When** a wheel (and sdist) is built, **Then** the built artifact contains the archive-signing keyring at the path the tool resolves at runtime.
2. **Given** a standard (non-editable) install of that wheel, **When** the administrator runs `init` or `add` against a mirror whose indexes require signature verification, **Then** the indexes verify successfully (no spurious "cannot verify signature" / missing-keyring failure).
3. **Given** the shipped documentation, **When** a reader looks up how to install the tool, **Then** the supported install mode(s) for the alpha are stated unambiguously (editable-install dev flow, plus standard install working from the built artifact).

---

### User Story 3 - `init` Guidance Is Honest About Connectivity (Priority: P2)

The administrator follows the quickstart on a machine that turns out to have no reachable mirror. Today `init` exits 0 with "initialized (offline mode, 0 binaries cached)" — the `provides` table stays empty — and a later `add` (once a mirror is reachable) fails on every virtual dependency with a misleading "Virtual package 'X' has no providers in the index" error that points at the archive, not at the real cause. Per triage, this is **not** a code fix: `init`'s purpose is network work, the air-gapped path (`ensure-base`) is self-sufficient, and an offline/partial `init` is simply not a usable state. This phase documents that `init` requires connectivity, that "0 binaries cached" means the run did not serve its purpose, and that the correct recovery is to start again from scratch — so the operator is never left guessing from a misleading error.

**Why this priority**: Documentation-only, but it removes the worst part of the finding — the misleading "no providers in the index" dead-end — by telling the user up front what `init` needs and what to do when it cannot do its job.

**Independent Test**: A reader of the quickstart `init` step can determine, without running anything, that `init` requires at least one reachable configured mirror and that an offline/partial result is not a usable cache (start again from scratch). No behavioral change to `init`.

**Acceptance Scenarios**:

1. **Given** the `init` documentation (quickstart and/or profile docs), **When** it is read, **Then** it states that `init` must run on a system with access to at least one configured mirror, because its job is to fetch the indexes and build the virtual-package snapshot.
2. **Given** an `init` run that reports "0 binaries cached" / offline mode, **When** the administrator reads the documentation, **Then** the docs state that this result is not a usable cache and the recovery is to resolve connectivity and re-run `init` from a clean cache — not to proceed to `add`.

---

### User Story 4 - Graybeard Is Documented as a Stand-Alone, Fresh-Cache-Only Profile (Priority: P2)

The administrator follows the profile files' "grow-deeper" guidance and reuses a `regular_user` cache under the `graybeard` profile, expecting it to deepen. Today the cached-virtual providers first resolved without sources never gain them at `-1`, and `verify` reports INCOMPLETE with no automatic repair. Per triage, graybeard is pulled out of the grow-deeper chain: it stands on its own, fresh from the get-go — consistent with its home flow `ensure-base`, which already refuses non-empty caches. `regular_user → developer` (including deepening developer depth) remains the documented supported direction. This phase updates the profile documentation and any spec references so the supported direction is unambiguous.

**Why this priority**: Documentation-only, but it makes the supported contract truthful: the only documented transitions are ones the machinery actually handles, and graybeard's fresh-only stance matches the code's own `ensure-base` enforcement.

**Independent Test**: A reader of the profile files can determine that graybeard is a fresh-cache-only profile (not part of the grow-deeper chain) and that the supported transition direction is `regular_user → developer`. No behavioral change to the resolver.

**Acceptance Scenarios**:

1. **Given** the `graybeard.toml` profile documentation, **When** it is read, **Then** it states that graybeard is a stand-alone, fresh-cache-only profile intended to be run from the get-go, not reached by reusing a cache grown under a shallower profile.
2. **Given** the `regular_user`/`developer` profile documentation, **When** the grow-deeper direction is read, **Then** the supported direction is stated as `regular_user → developer` (including deepening developer depth), without graybeard in the chain.
3. **Given** any spec or review document that previously named the grow-deeper chain including graybeard, **When** the documentation is refreshed in this feature cycle, **Then** the chain is restated without graybeard (Constitution VIII — docs stay current with the code's supported behavior).

---

### Edge Cases

- **Constraint-aware probes (N-3)**: a group member current at a version satisfying the constraint (treated as satisfied, as today); current at an unsatisfying version with a resolvable alternative (alternative tried); current at an unsatisfying version with no resolvable alternative (group error reported once); provides-joined (virtual) members keeping their documented over-inclusive semantics; the build-dep twin with and without a constraint-satisfying candidate; multi-alternative groups where the first tried alternative fails and a later one resolves.
- **Distributable keyring (N-4)**: wheel and sdist both inspected; the file present at the exact runtime-resolved path; a standard install in a clean environment; an editable install continuing to work (source-tree read); a mirror requiring signature verification vs a loopback mirror (unverified path unaffected).
- **`init` guidance (N-1, docs)**: offline `init` (0 binaries cached); partial `init` (some components cached, others not); the recovery message in each case; no change to `init`'s current exit-0 offline behavior.
- **Graybeard standalone (N-2, docs)**: `regular_user → developer` transitions (supported); developer deepening (supported, informational-only residual source-coverage gap under virtual edges remains an accepted, documented limitation); any transition into graybeard (unsupported — start fresh).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The runtime alternative-group "already satisfied" probe MUST evaluate each group member's version against that member's own version constraint — a member whose current row fails its own constraint MUST NOT be treated as satisfying the group. (N-3)
- **FR-002**: When the primary of an alternative group fails and no group member constraint-wise satisfies it, the alternatives MUST be tried, and a resolving alternative MUST be brought into the closure with the failed-primary error retracted. (N-3)
- **FR-003**: When a group member constraint-wise satisfies the group, the group MUST be treated as resolved as today (the error is retracted) — with the provides-join (virtual) probe preserving its documented over-inclusive semantics; provider over-inclusiveness MUST NOT regress. (N-3)
- **FR-004**: The build-dep "already satisfied" probe MUST mirror the runtime probe — constraint-aware, trying resolvable alternatives — and a constraint-satisfying alternative MUST be linked as a resolved build tool with no "Build tool not resolved" error. (N-3)
- **FR-005**: The built distribution artifacts (wheel and sdist) MUST contain the archive-signing keyring file at the path the tool resolves at runtime, so a standard (non-editable) install can verify remote indexes. (N-4)
- **FR-006**: The built wheel MUST be inspected before the alpha tag — the keyring present, a standard install able to run `init`/`add` against a signature-verifying mirror — and the supported install mode(s) MUST be stated in the documentation. (N-4)
- **FR-007**: The documentation MUST state that `init` requires access to at least one configured mirror and that an `init` reporting 0 binaries cached (offline mode) or a partial fetch is not a usable cache — the recovery is to resolve connectivity and start again from scratch. No behavioral change to `init`. (N-1)
- **FR-008**: The profile documentation MUST state that graybeard is a stand-alone, fresh-cache-only profile (not part of the grow-deeper chain), and MUST restate the supported grow-deeper direction as `regular_user → developer` (including deepening developer depth). No behavioral change to the resolver. (N-2)

### Key Entities *(include if feature involves data)*

- **Alternative-group probe state**: the "already satisfied" check in the runtime dependency walk and the build-dep pass — whether a `current` DB row satisfies the member's own version constraint before suppressing alternative resolution (N-3).
- **Provides-join provider set**: the over-inclusive virtual-provider probe whose semantics must be preserved by the constraint-aware rework (N-3).
- **Distribution artifact / trust anchor**: the built wheel and sdist and the archive-signing keyring file they must carry at the runtime-resolved path (N-4).
- **Profile documentation contract**: the stated install/run guidance for `init` connectivity (N-1) and the supported profile-transition direction (N-2) as reflected in `docs/001-quickstart-howto.md`, `docs/003-ensure-base.md`, and `docs/profiles/{regular_user,developer,graybeard}.toml`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With `a 1.0` pre-registered and a dependency `a (>= 2.0) | b` (index holds `a 1.0`, `b`), resolution brings `b` into the closure, `add` exits 0, `verify` reports COMPLETE, and the cache installs via `apt` — the N-3 scenario is fully closed.
- **SC-002**: The build-dep twin of the same scenario links the resolvable alternative and reports no "Build tool not resolved" error (FR-013 "every fetched source is buildable" holds on version-skewed caches).
- **SC-003**: The built wheel and sdist contain the archive-signing keyring; a standard (non-editable) install successfully runs `init`/`add` against a signature-verifying mirror (N-4 closed).
- **SC-004**: A reader of the quickstart can determine that `init` requires connectivity and that an offline/partial `init` (0 binaries cached) is not a usable cache — recover by starting again from scratch (N-1 documented).
- **SC-005**: A reader of the profile files can determine that graybeard is fresh-cache-only and stand-alone, with `regular_user → developer` as the supported grow-deeper direction (N-2 documented).
- **SC-006**: Both documented test runners (pytest and `python -m unittest`) are green on the post-019 baseline plus the new N-3/N-4 tests, with no regressions in the existing corpus.

## Assumptions

- **Triage scope**: This feature converts the four high-priority review findings into their agreed resolutions — N-3 and N-4 are implemented; N-1 and N-2 are accepted as documented limitations (documentation only, no resolver/`init` behavior change). The review's low-severity findings (L-1…L-13), process/evidence notes (§3, §6), and §7 items 5-6 (cheap low findings, physical-media proof runs) are out of scope.
- **N-3 design**: The constraint-aware probes mirror the existing build-dep link block pattern (`resolver.py:716-744`): each candidate's version is checked with `satisfies_constraint` against its own name's constraint; only a constraint-wise-satisfying member suppresses alternative resolution. The provides-join (virtual) probe keeps its documented over-inclusive semantics.
- **N-4 design**: The keyring is declared as package data (`package_mgmt` ships `data/*.gpg`); the runtime path resolution (`os.path.join` relative to the installed module) is unchanged — it is correct for installed wheels, which land as normal files, not zip-imports. `importlib.resources` migration is not required. The supported install modes are documented; the editable-install dev flow keeps working.
- **N-1 behavior unchanged**: `init`'s current behavior — exit 0 with an honest offline note when no mirror is reachable, per P-4 — is preserved. Only documentation changes.
- **N-2 behavior unchanged**: The resolver's depth machinery is untouched. The supported direction is `regular_user → developer` (including deepening developer depth); a mild, informational-only source-coverage gap under virtual edges at finite depths (the review's N-2b) is accepted and may be noted in the profile docs. Transitions into graybeard are unsupported; graybeard is run from a fresh cache.
- **Schema freeze**: No database schema changes (spec 010/011 freeze holds).
- **Regression-neutral**: Behavior not covered by FR-001…FR-008 is unchanged; existing green tests stay green, and the 019 baseline (707 passed / both runners) is re-recorded at tag time if REGRESSION.md needs refreshing.

## Clarifications

No [NEEDS CLARIFICATION] markers remain in the spec; the four findings were triaged in session before this spec was written and the decisions are recorded in the sessions below.

### Session (review triage)

- Q: N-1 (stale `provides` after offline `init`) — implement the resolver-side provides-repopulation fix, or accept as a documented limitation? → A: Accepted as a documented limitation (documentation only). `init`'s job is network work; running it offline is not a supported flow and the air-gapped path (`ensure-base`) is self-sufficient. Docs state `init` requires connectivity and an offline/partial `init` is not a usable cache — start again from scratch. No code change.
- Q: N-2 (cached-virtual source repair depth-gated at `-1`) — implement the repair fixes, or redefine the supported profile-transition contract? → A: Redefined the contract (documentation only). Graybeard is pulled out of the grow-deeper chain and documented as a stand-alone, fresh-cache-only profile (consistent with `ensure-base`'s existing fresh-only refusal); `regular_user → developer` (including deepening developer depth) remains supported. No resolver change; the review's subtree-re-walk machinery (N-2b) is intentionally not built.
- Q: N-3 (constraint-blind alternative probes) — fix in this feature? → A: Implemented in this feature (FR-001…FR-004): both probes become constraint-aware, mirroring the existing build-dep link block, with the provides-join over-inclusive semantics preserved.
- Q: N-4 (keyring absent from wheel/sdist) — fix in this feature? → A: Implemented in this feature (FR-005…FR-006): the keyring is declared as package data, the artifact is built and inspected before tagging, and the supported install modes are documented.
