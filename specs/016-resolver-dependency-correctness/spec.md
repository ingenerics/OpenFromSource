# Feature Specification: Resolver & Dependency Correctness

**Feature Branch**: `016-resolver-dependency-correctness`
**Status**: Draft

**Input**: User description: "Spec high prio issues - The high prio issues are documented in the 02_high_prio_followups.md file. Use common sense and think hard on how to divide the work over the new spec. When in doubt ask, it is better to brainstorm to achieve the desired quality."

**Design context**: The alpha reviews (`review/02_alpha_kimi_k3/`, `review/03_alpha_glm_5_3/`) surfaced, alongside the blockers already tackled in spec 015, a batch of high-priority robustness/contract items. The findings are grouped by area in `review/04_alpha_summary/02_high_priority_followups.md`; its cross-cutting note explicitly instructs clustering the resolver work into one pass with one fixture pack. This spec takes the **Resolver & dependency model** cluster (F-8, F-9+F-10, F-11, F-12, F-13, F-15) as the first of three follow-up specs, per the agreed split: 016 (this spec), 017 (Download & cache integrity, incl. ensure-base H-2/M-3), 018 (Operator track & CLI/config robustness). All three land in the same alpha release, immediately after the 015 blockers; the resolver cluster ships first.

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Builds and verifies the cache; this feature changes what `add`, `verify`, and `--file` batch runs resolve, link, and report |
| **Operator** | Offline system (no network) | Installs from the shipped cache; indirectly protected — a correctly-linked, correctly-constrained cache is installable and rebuildable |

**Phase 1 — Alternative-Group Success (F-8)**: a request whose alternative group resolves (even when the first alternative is absent from the configured components) succeeds and is never reported as failed.
**Phase 2 — Exact Source Linkage (F-9+F-10)**: every binary links to its exact source version in its own release/component — never to a version/release-blind guess — with divergence logged when a fallback is used.
**Phase 3 — Per-Alternative Constraints (F-11)**: every alternative in an `a | b` group keeps its own version constraint; a group is satisfied only when a member satisfies its own constraint.
**Phase 4 — Arch Restrictions & Build Profiles (F-12)**: `[arch]` restrictions are honored, multiple `<profile>` tokens are evaluated conservatively, and no dependency token is ever dropped silently.
**Phase 5 — Constraint-Aware Verify (F-13)**: `verify` checks version constraints on direct-name dependencies — not mere presence — and walks alternatives with their own constraints.
**Phase 6 — Batch-Scoped Build-Dep Pass (F-15)**: build-tool resolution processes only sources created in the current batch; retained sources are never re-walked or re-extended.

---

### User Story 1 - Resolved Alternative Groups Exit Cleanly (Priority: P1)

The administrator adds a package whose dependency is an alternative group (`ghost | realpkg`) where the first alternative does not exist in the configured archive but a later one does. Today the closure resolves completely, yet the run exits with an error and a batch `--file` run counts the request as failed — because the absent first alternative leaves its error behind. This phase makes the group resolve as a unit: the request succeeds (exit 0, zero errors) whenever any member resolves, an error is recorded only when the **whole group** fails, and per-segment error attribution is preserved.

**Why this priority**: The first alternative being absent from the configured components is routine in real archives; the false failure makes `add` and batch runs report failures for requests that actually succeeded — blocking automated use.

**Independent Test**: Add a fixture `a | b` where only `b` exists — the closure contains `b`, `errors == []`, and `add` exits 0; a batch `--file` run over mixed entries shows zero errors for the resolved group.

**Acceptance Scenarios**:

1. **Given** a package whose dependency is `a | b` with only `b` present in the configured components, **When** the administrator adds it, **Then** the closure contains `b`, no error is attributed to the group, and the command exits 0.
2. **Given** the same package added through a batch `--file` run, **When** the summary is inspected, **Then** the request is not counted as failed.
3. **Given** a dependency group where **every** alternative is absent or unsatisfiable, **When** the administrator adds it, **Then** an error is recorded for the group and the request is reported as failed.
4. **Given** a batch with a mix of resolved groups and genuinely failing segments, **When** the summary is inspected, **Then** each error is attributed to the segment that actually failed.

---

### User Story 2 - Binaries Link to the Exact Source Version and Release (Priority: P1)

The developer/graybeard profiles promise deterministic rebuilds from linked source. Today source linkage is version- and release-blind: the explicit version in a `Source: foo (1.0-1)` field is discarded and the newest source in the index is picked instead; a binary resolved from a fallback release (e.g. `trixie-backports`) is searched first against the primary release's sources. Under archive skew, binNMU (`+bN`) rebuilds, and backports, binaries link to the wrong source version — a Binary-Source Consistency violation that `download` and `verify` both accept. This phase makes source selection exact: honor the version carried in the `Source:` field; otherwise derive the expected source version from the binary version; and look the source up in the binary's own release/component first. Only then fall back to the newest in the release, logging the divergence.

**Why this priority**: Every wrongly-linked binary silently violates the deterministic-rebuild promise; the cache is marked complete and handed off with sources that cannot rebuild it.

**Independent Test**: With source versions `{1.0-1, 2.0-1}` and a binary `foo-bin 1.0-1+b1` declaring `Source: foo (1.0-1)`, adding and verifying links `foo 1.0-1`; a backports fixture links its source from the backports release.

**Acceptance Scenarios**:

1. **Given** a binary whose `Source:` field carries an explicit version, **When** it is resolved and linked, **Then** the linked source is exactly that `(name, version)` — never the newest available.
2. **Given** a binary with no explicit `Source:` version, **When** it is linked, **Then** the expected source version is derived from the binary version (including stripping `+bN` binNMU suffixes) and that exact source is used.
3. **Given** a binary resolved from a fallback release (e.g. backports), **When** it is linked, **Then** the source is looked up in the binary's own release and component before any other release.
4. **Given** no exact source version exists in the binary's release/component, **When** a fallback to the newest source occurs, **Then** the divergence is logged (never silent), and a source is still linked.

---

### User Story 3 - Every Alternative Keeps Its Own Version Constraint (Priority: P2)

The administrator relies on version-constrained alternative dependencies (`foo (= 9.9) | bar (= 2.0)`). Today the constraint is kept only on the first alternative and dropped from the rest, so a group "satisfied" by `bar 1.0` — which fails `bar`'s own `(= 2.0)` — is registered silently with no error, and "every fetched source is buildable" no longer holds. This phase preserves each alternative's own constraint through parsing, storage, and resolution, and reports an explicit error when no member satisfies its own constraint — never a silent unconstrained link.

**Why this priority**: Silent constraint loss produces a cache that appears complete but cannot be rebuilt or installed as declared; correctness of the dependency model is a core product guarantee.

**Independent Test**: Round-trip a parser fixture `foo (= 9.9) | bar (= 2.0)` and assert every alternative keeps its constraint; resolve a group where only an unsatisfying-version alternative exists and assert an error with no silent link.

**Acceptance Scenarios**:

1. **Given** a dependency line with per-alternative constraints, **When** it is parsed and stored, **Then** every alternative retains its own constraint (no loss on any but the first).
2. **Given** an alternative group where a member exists but at a version that fails its own constraint, **When** the administrator adds the package, **Then** an explicit error is reported — no silent unconstrained registration.
3. **Given** an alternative group where at least one member satisfies its own constraint, **When** the package is added, **Then** that member is resolved against its own constraint and the group is satisfied.

---

### User Story 4 - Architecture Restrictions and Build Profiles Are Honored (Priority: P2)

The administrator builds caches for `amd64`; Debian dependency lines routinely carry architecture restrictions (`gperf [amd64 i386]`) and build-profile tokens (`debhelper-compat (= 13) <!nocheck !cross>`). Today `[arch]` groups are not parsed at all and only a single `<profile>` token is accepted, so both kinds of dependency are silently dropped — and are missing from the stored dependency data. This phase keeps a dependency whose restriction includes the target architecture, drops it only when the restriction excludes the target architecture, accepts multiple profile tokens and evaluates them conservatively (over-inclusive, the safe direction for a cache), and never drops any token silently — an unparseable token produces a warning while the rest of the line still parses.

**Why this priority**: Silent drops produce a cache missing dependencies the admin never learns about, violating the promise that stored dependencies reflect reality; over-inclusion is the safe failure mode and absence of diagnostics is not acceptable.

**Independent Test**: Fixtures `foo [amd64]` (kept), `foo [i386]` and `foo [!amd64]` (dropped), a multi-profile token kept with its constraint intact, and an unparseable token producing a warning with the remainder still parsed.

**Acceptance Scenarios**:

1. **Given** a dependency with an arch restriction that includes the target architecture, **When** the package is resolved, **Then** the dependency is kept.
2. **Given** a dependency with an arch restriction that excludes the target architecture, **When** the package is resolved, **Then** the dependency is dropped (only then).
3. **Given** a dependency with multiple build-profile tokens, **When** the package is resolved, **Then** the dependency is kept and evaluated conservatively, with its constraint intact.
4. **Given** a dependency line containing an unparseable token, **When** the package is resolved, **Then** a warning is emitted and the remainder of the line still parses — nothing is dropped silently.

---

### User Story 5 - Verify Checks Version Constraints, Not Just Presence (Priority: P2)

The completeness gate the alpha markets must be at least as strong as the operator's own install check. Today `verify` answers "satisfied" for a direct-name dependency on mere presence: `app` depending on `libfoo (>= 2.0)` with only `libfoo 1.5` in the cache reports COMPLETE and exits 0, while the operator's apt refuses the install. This phase makes `verify` evaluate the version constraint on the direct-name path (selecting the current binary's version and checking it against the constraint), and walk alternatives with their own constraints. Providers keep the existing documented over-inclusive behavior.

**Why this priority**: A completeness gate weaker than apt's own check lets an uninstallable cache ship with a green `verify` — undermining the release's central promise.

**Independent Test**: A `>=` constraint with only a too-old binary reports INCOMPLETE with a non-zero exit; an exact match is clean; an alternative group satisfied only by its second alternative is clean.

**Acceptance Scenarios**:

1. **Given** a package depending on a direct name with a version constraint and only a too-old/too-new current binary, **When** the administrator runs `verify`, **Then** the cache is reported INCOMPLETE with a non-zero exit.
2. **Given** a package depending on a direct name whose current binary version satisfies the constraint, **When** `verify` runs, **Then** the cache is reported COMPLETE.
3. **Given** an alternative group satisfied only by a later alternative (with its own constraint), **When** `verify` runs, **Then** the cache is reported COMPLETE.
4. **Given** a dependency satisfiable via a provider, **When** `verify` runs, **Then** the provider path retains its documented over-inclusive behavior (unchanged).

---

### User Story 6 - The Build-Dep Pass Stays Batch-Scoped (Priority: P2)

The build-tool pass must only do the work this `add` run asked for. Today every `add` re-iterates **every source ever registered**, so a mature graybeard cache (tens of thousands of sources) gets steadily slower, and after a deep run a later shallower-depth run re-resolves build tools for out-of-scope retained sources — retention means *keep*, not *keep extending*. This phase processes only sources created in the current batch, seeds the pass with everything already in the database, and leaves retained sources' build-tool scope untouched on later runs.

**Why this priority**: Unbounded re-walking degrades performance monotonically on the primary production profile and leaks scope beyond what the run requested.

**Independent Test**: Two sequential `add` runs — the second run's build-tool counters count only its own sources; a shallow-after-deep re-run resolves zero new build tools for retained sources.

**Acceptance Scenarios**:

1. **Given** an existing cache with many retained sources, **When** the administrator adds a new package, **Then** build-tool resolution processes only the sources created in that run — historical sources are not re-walked.
2. **Given** a deep run followed by a shallower-depth run over the same retained sources, **When** the shallower run completes, **Then** it resolves zero new build tools for the retained sources (retention does not extend scope).
3. **Given** a brand-new cache, **When** the first `add` runs, **Then** all sources created by that run receive their build-tool resolution exactly once.

---

### Edge Cases

- **Alternative groups (Phases 1 & 3)**: first alternative absent while a later one resolves; every alternative absent; all alternatives present; alternatives with constraints on the first, on later, or on every member; a member present but at an unsatisfying version; alternatives sharing a dependency with another segment.
- **Source linkage (Phase 2)**: `Source:` field with and without an explicit version; binary version with a `+bN` suffix; a source name differing from the binary name; a binary from a backports/fallback release; the same source present in multiple releases; no matching source at all (must still link the closest/logged fallback, never silently).
- **Parser robustness (Phase 4)**: single and multiple arch tokens (`[amd64]`, `[i386]`, `[!amd64]`, combined); single and multiple profile tokens; profile tokens combined with a version constraint; an unparseable token at the start, middle, or end of a line; an empty dependency field.
- **Verify (Phase 5)**: `>=`, `<=`, `=`, and versionless constraints; too-old and too-new current binaries; alternatives satisfied only by the second member; provider-only satisfaction.
- **Batch scope (Phase 6)**: first add on an empty cache; sequential adds over shared sources; shallow-after-deep re-runs; interaction with a mid-batch rollback (never re-resolve uncommitted sources as if retained).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When an alternative dependency group is requested and at least one member resolves within the configured components, the request MUST succeed (exit 0) with no error attributed to the group; an error MUST be recorded only when every member of the group fails.
- **FR-002**: In a batch request, a package whose alternative group resolved MUST NOT be counted as failed in the summary; per-segment error attribution MUST be preserved.
- **FR-003**: The source linked to a binary MUST match the binary's declared source version when the `Source:` field carries a version; otherwise the system MUST derive the expected source version from the binary version (including stripping `+bN` binNMU suffixes) and link that exact version.
- **FR-004**: Source lookup MUST prefer the binary's own release and component before searching elsewhere (a backports binary links to a backports source).
- **FR-005**: When no exact source version exists in the binary's release/component, the system MUST fall back to the newest available source and MUST record the divergence (log) — never link silently.
- **FR-006**: Every alternative in an alternative dependency group MUST retain its own version constraint through parsing, storage, and resolution; a group MUST be treated as satisfied only when at least one member satisfies its own constraint.
- **FR-007**: An alternative group where no member satisfies its own constraint MUST produce an explicit error — never a silent unconstrained registration.
- **FR-008**: An architecture-restricted dependency MUST be kept when the restriction includes the target architecture and dropped only when it excludes the target architecture; the restriction MUST NOT be silently discarded.
- **FR-009**: Multi-token build-profile dependencies MUST be accepted and evaluated conservatively (over-inclusive, the safe direction for a cache), with their constraints intact.
- **FR-010**: Any dependency token that cannot be parsed MUST NOT be silently dropped — it MUST produce a warning while the remainder of the line still parses.
- **FR-011**: The verify command MUST evaluate version constraints when checking a direct-name dependency (not mere presence); an unsatisfied constraint MUST be reported as INCOMPLETE with a non-zero exit. Alternatives MUST be walked with their own constraints; providers MAY retain their documented over-inclusive behavior.
- **FR-012**: The build-tool resolution pass MUST process only sources created in the current batch; sources already present in the database MUST NOT be re-walked on every add.
- **FR-013**: A later shallower-depth run over previously retained sources MUST NOT extend their build-tool scope (retention means keep, not keep extending).

### Key Entities *(include if feature involves data)*

- **Alternative dependency group**: a set of alternatives (`a | b`) in one dependency position; this feature gives each alternative its own name and its own version constraint.
- **Constraint-preserving dependency record**: the stored form of a parsed dependency that retains every alternative's constraint (internal shape; no database schema change).
- **Source linkage**: the mapping from a binary to its exact source `(name, version, release, component)` used for deterministic rebuilds; made version- and release-aware by this feature.
- **Source-version derivation**: the rule that derives the expected source version from a binary version (including `+bN` stripping) when no explicit `Source:` version exists.
- **Batch source scope**: the set of source packages created in the current `add` run, the only set eligible for build-tool resolution; retained sources are excluded.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Adding a package whose first alternative is absent but a later alternative resolves exits 0 with zero errors, and a batch `--file` run reports zero failed requests for such entries.
- **SC-002**: For a fixture set covering version-skewed sources and a backports/fallback release, every binary links to its exact source version in its own release/component, and no binary links to a mismatched source version without a logged divergence.
- **SC-003**: For fixtures with version-constrained alternatives, the stored dependency data preserves every alternative's constraint, and a group satisfied only by a member that fails its own constraint produces an explicit error — never a silent link.
- **SC-004**: For fixtures with architecture-restricted and multi-profile dependencies, target-arch dependencies are kept, excluded-arch dependencies are dropped, and no dependency token is silently dropped (unparseable tokens produce warnings).
- **SC-005**: `verify` reports a version-constrained direct-name dependency satisfied only when the current binary's version satisfies the constraint; otherwise INCOMPLETE with a non-zero exit — matching the operator's install check — with providers over-inclusive.
- **SC-006**: On a mature cache, a subsequent `add` run resolves build tools only for sources created in that run; no historical source is re-walked and no retained source's scope is extended by a shallower run.

## Assumptions

- **Scope source**: The six entries F-8, F-9+F-10, F-11, F-12, F-13, F-15 in `review/04_alpha_summary/02_high_priority_followups.md` (the "Resolver & dependency model" cluster) are the authoritative scope of this feature. Per the agreed split, the remaining clusters are separate features: 017 — Download & cache integrity (H-2, M-3, M-4, F-16, F-17, M-7, F-23) and 018 — Operator track & CLI/config robustness (F-18, F-19, F-20, F-21, F-22); they are out of scope here and land in the same alpha release after this feature.
- **Clustered implementation**: As the source doc's cross-cutting note directs, the resolver work ships in one branch with one fixture pack (alternative groups with constraints, arch-restricted deps, version-skewed source); each phase remains independently implementable and testable.
- **No schema changes**: The spec 010/011 schema freeze holds; the per-alternative constraint model is internal (JSON shape only), no database change.
- **Contract-preserving**: Existing exit-code/JSON contracts (spec 014 `contracts/cli.md`) continue to hold. Where an error path changes (F-8's false failure), `tests/contract/test_cli.py` must stay green and the contract table may need extension, not divergence.
- **Target architecture**: `amd64`, per the constitution's target platform; arch-restriction behavior is defined against it.
- **Providers over-inclusive**: Provider-based satisfaction keeps its documented over-inclusive behavior (F-13); only the direct-name path gains version checking.
- **Alpha sequencing**: This feature is complete when all six phases are implemented and the completion evidence (fixture packs + both documented runners green) passes; the alpha tag is not created before the resolver cluster lands.
- **Regression-preserving**: Behavior not covered by one of the six entries is unchanged.

## Clarifications

*None pending — the entries in `review/04_alpha_summary/02_high_priority_followups.md` carry concrete fix and test guidance, and the split/sequencing decision was confirmed with the stakeholders before writing this specification.*
