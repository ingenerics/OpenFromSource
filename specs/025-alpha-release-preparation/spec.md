# Feature Specification: Alpha-Release Readiness

**Feature Branch**: `025-alpha-release-preparation`
**Status**: Draft

**Input**: User description: "A thorough review has been done. Both an analysis and code review have been added to `reviews/06_...`. Lets turn these into a final spec before alpha release. Alpha review: Skip the remaining L-X tasks from previous review. For R-5, do we even need to ship the private key? It may make sense in some cases, but to do it by default? Additionally, for graybeard (source = true, depth -1) when ensure-base is called and not all 4 Trixie components are set in the config, I'd like the output to start with a descriptive warning message. Code review -> take along low hanging fruit. The spec, plan and tasks phases are likely to introduce questions. Ask, don't assume. Think hard."

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Runs the fixes below: a standard install that works, a download flow that stays clean after local imports, correct local-version supersede, a config-driven align-repo baseline, a handoff that no longer ships the private signing key by default, and a graybeard ensure-base that warns when the Trixie component set is incomplete |
| **Operator** | Offline system (no network) | Receives a cache whose handoff payload contains only the public key (no private signing key) by default, and continues to install from the cache unchanged |

**Phase 1 — Close the alpha blockers**: The administrator's core flows are made correct and installable: a standard no-extras install runs every command (R-1), `download` no longer fails after a local import (R-2), importing a newer `local` revision supersedes the older one (R-3), `align-repo` validates against the configured baseline (R-4), the handoff payload no longer carries the private signing key by default (R-5), and graybeard `ensure-base` opens with a descriptive warning when the config's Trixie components are incomplete (new). Run by the Administrator.

**Phase 2 — Release hygiene, consolidation, and verification**: The release paper-trail is completed (R-6), the code review's low-hanging fruit is taken along (shared helpers, one dependency-satisfaction rule, public re-exports, one `local`-entries generator, packaging/hash helper consolidation, test scaffolding, craft sweeps, config path validation, prerequisites matrix, fetch-hook consistency), and the release checks are executed (re-recorded test baseline, wheel inspection, FAT-USB bootstrap and graybeard DVD-1 physical proofs). Run by the Administrator, with results appended to the release notes.

**Phase 3 — Author the tutorial track, then mark the release**: The Administrator and the agent work together in collaborative authoring sessions to add new tutorials to the project — none of the planned tutorial content exists yet, and the tutorials introduce a **new** tutorial style agreed jointly as we go (distinct from the existing how-to documents such as `docs/001`–`docs/005`). When the tutorials are complete and agreed, the project version is set to `0.1.0-alpha` (from `0.1.0`) — the alpha-release marker. Run jointly by the Administrator and the agent.

---

### User Story 1 - Every Command Works From a Standard Install (Priority: P1)

A fresh administrator follows the documented quickstart on a clean machine: `pip install .` with **no extra packages**. Every `package_mgmt` command — including `--help` and `ensure-base`'s ISO-reading path — imports and runs without a `ModuleNotFoundError`. The built artifact ships everything the runtime needs (including the archive-signing keyring used to verify remote indexes) and runs from a plain installed wheel, not just from a source checkout. The current defect — the CLI importing a dev-only package at startup and dying on a documented standard install — is gone, and the packaging claim made in the quickstart is verified against the real artifact.

**Why this priority**: This blocks the alpha promise outright — a documented standard install currently fails on *every* command. Nothing downstream matters if the tool cannot be installed. It also closes the standing "inspect the artifact" follow-up (review 020 / N-4).

**Independent Test**: In a fresh venv, `pip install .` (no extras) then `package_mgmt --help` succeeds; the same check run against the built wheel (not an editable install) also succeeds; a fixture-ISO `ensure-base` run works from the plain install.

**Acceptance Scenarios**:

1. **Given** a fresh virtual environment and no dev extra packages, **When** `pip install .` runs and `package_mgmt --help` is invoked, **Then** it succeeds with no import errors, and every subcommand's parser builds.
2. **Given** the built wheel artifact installed without extras, **When** `package_mgmt` is imported and invoked, **Then** remote index verification and ISO ingestion paths are fully available (the signing keyring ships in the artifact).
3. **Given** the plain installed wheel, **When** `init` and `ensure-base` run against a fixture installer image, **Then** they complete successfully (the ISO-reading dependency is a runtime dependency, not a dev-only one).

---

### User Story 2 - `download` Stays Clean After Local Imports (Priority: P1)

After the administrator imports any local `.deb` or runs `package-stack`, the very next `download` — and every download afterwards for the life of the cache — exits 0 with zero command errors, even when there is nothing to fetch. The bogus "cached index missing for <release>/local" error (which currently fires on every download after a local import because the binary re-check treats `local` rows like archive rows) is eliminated. Local records stay `verified`; they are not spuriously re-checked against an archive index that legitimately does not exist.

**Why this priority**: Every `import-deb` / `package-stack` user hits this on the next download, and it re-fires on every subsequent run with a message that misleads the admin toward index repair. It breaks the local patch lane and the source-stack channel.

**Independent Test**: Import a `local` deb, then run `download` — it exits 0 with zero command errors and the local record remains verified; repeat several times with mixed archive and local packages and it stays clean.

**Acceptance Scenarios**:

1. **Given** a cache with an imported `local` deb and nothing pending, **When** `download` runs, **Then** it exits 0 with zero command errors and no "cached index missing for <release>/local" message.
2. **Given** a cache containing both archive and `local` packages, **When** `download` runs repeatedly, **Then** only archive rows are re-checked against archive indexes, the `local` rows stay `verified`, and the run never fails on the missing `local` index.
3. **Given** a `local` record whose payload is missing from the pool, **When** `download` runs, **Then** it is still handled by the existing excluded-payload path (the `local` index itself is never the error).

---

### User Story 3 - Importing a Newer Local Revision Supersedes the Older One (Priority: P1)

When the administrator imports a newer version of a `local` package (e.g. `patch-tool 1.3-0` after `1.2-3`), the older `current` local row is superseded: exactly one `current` row exists per package/architecture, the handoff and diff emit only the newest stanza, and no three-concurrent-`current` state can arise (old local, new local, archive). The stale payload file is **retained** in the pool — content-addressed and still checksummed on sync — so a lagging operator system that still references the old version continues to install it. Reads that were nondeterministic because of duplicate `current` rows become deterministic.

**Why this priority**: Without supersede, every dead `local` revision piles into the handoff/diff, index readers become nondeterministic, and a name can end up with three concurrent `current` rows. It is a correctness defect in the import lane (review R-3 / code-review F8).

**Independent Test**: Import `patch-tool 1.2-3`, then import `patch-tool 1.3-0`; confirm exactly one `current` local row, the index emits only `1.3-0`, the `1.2-3` payload remains in the pool, and a lagging operator can still install `1.2-3` from a snapshot that references it.

**Acceptance Scenarios**:

1. **Given** `patch-tool 1.2-3` is `current` and `verified`, **When** `1.3-0` is imported, **Then** exactly one `current` local row exists for the name, and the handoff/diff emit only the `1.3-0` stanza.
2. **Given** a superseded `1.2-3` payload, **When** `sync` runs, **Then** the old payload file is retained and still checksum-verified (never deleted), and a lagging operator holding `1.2-3` still installs.
3. **Given** an older local, a newer local, and an archive version of the same name, **When** any reader selects the current row, **Then** the selection is deterministic (never three concurrent `current` rows).
4. **Given** a re-import of the identical version, **When** it is imported again, **Then** it stays idempotent (no duplicate rows) — existing behavior preserved.

---

### User Story 4 - `align-repo` Validates Against the Configured Baseline (Priority: P1)

`align-repo` derives its baseline from the configured archive sources (the first configured source's release/component) instead of a hard-coded `trixie/main`. On any non-trixie configuration — or a trixie configuration whose first component is not `main` — the command reports the correct era verdict rather than the wrong baseline's verdict or a degraded "everything unmet" when no `trixie/main` index is cached. A standard trixie/main configuration behaves exactly as before.

**Why this priority**: The baseline is, by definition, the frozen cache of the configured first source; hard-coding `trixie/main` makes the tool wrong on every other configuration and collapses to "everything unmet" without a cached `trixie/main` index (review R-4).

**Independent Test**: Run `align-repo` under a config whose first source is not `trixie/main` (and with no `trixie/main` index cached) and confirm it validates against the actual configured baseline and reports a meaningful verdict, not blanket "unmet".

**Acceptance Scenarios**:

1. **Given** a config whose first archive source is not `trixie/main`, **When** `align-repo` runs, **Then** it uses the configured first source's release/component as the baseline and reports the correct verdict.
2. **Given** a config with no `trixie/main` cached index, **When** `align-repo` runs, **Then** it does not degrade to "everything unmet" — it validates against the actual configured baseline.
3. **Given** a standard trixie/main config, **When** `align-repo` runs, **Then** the verdict is unchanged from current behavior.

---

### User Story 5 - The Private Signing Key Stays Off Operator Media by Default (Priority: P1)

The cache-local signing keyring no longer lives inside the handoff payload tree. A generated handoff payload contains only the **public** key (`offline-cache.gpg`), which is all an operator needs to verify the signed Release files. The secret keyring is relocated to a base-dir-level location outside the payload, and no migration is performed for caches created under the old layout. The decision is recorded in the documentation: because an administrator commonly copies the whole cache directory (`{base_dir}`), the docs state plainly that the private key rides along in that copy and that it SHOULD be excluded from operator media unless the receiving machine is a trusted relay that must re-sign. This turns an accidental exposure into a deliberate, documented posture.

**Why this priority**: As shipped, every copy of the transport media carries the private signing key — any operator machine (or anyone reading the stick afterwards) can re-sign arbitrary index content that every other machine bootstrapped from the same cache trusts. On FAT media the 0700 permissions are meaningless. The stable trust-anchor design is sound; only its placement leaks the secret. The user's direction: do not ship the private key by default.

**Independent Test**: Generate a handoff, inspect the payload tree for private key material (none present, public key only), and confirm the documentation records the copy-time decision (exclude the keyring location unless the operator is a trusted relay).

**Acceptance Scenarios**:

1. **Given** a generated handoff payload, **When** it is inspected, **Then** it contains no private key material — only the public `offline-cache.gpg`; the secret keyring lives outside the payload tree.
2. **Given** the transport documentation, **When** an administrator follows it, **Then** the posture is explicit: copying the whole cache directory carries the keyring; exclude the keyring location from operator media unless the operator is a trusted relay that must re-sign.
3. **Given** a cache created under the old in-tree layout, **When** handoff regenerates, **Then** no migration is performed; a fresh key is generated at the new location and any legacy in-tree keyring is not used (acceptable pre-alpha, before any cache has shipped).

---

### User Story 6 - Graybeard `ensure-base` Warns When Trixie Components Are Incomplete (Priority: P1)

When `ensure-base` runs under the graybeard configuration (`[fetch] source = true` and `[resolution] max_source_dep_depth = -1`) and the config's trixie archive sources do not include all four canonical components (`main`, `contrib`, `non-free`, `non-free-firmware`), the command's output **starts with** a descriptive, non-blocking warning that names the missing components. The warning is the first thing the administrator sees. Under a complete graybeard config (all four components), or under any non-graybeard config, no such warning is emitted.

**Why this priority**: Graybeard promises "turtles all the way down" — every binary's source and build tools. If the config only includes `main`, the full-rebuild promise cannot hold for packages in the other components; silently proceeding produces a cache that looks complete but cannot rebuild parts of the stack. The warning makes the limitation visible at the moment it matters.

**Independent Test**: Under a graybeard config with only `main`, run `ensure-base` and confirm the output begins with the missing-component warning; add the other three components and confirm the warning disappears; run under a developer config (finite depth) and confirm no such warning.

**Acceptance Scenarios**:

1. **Given** a graybeard config (`source = true`, depth `-1`) whose trixie sources omit one or more of `main`, `contrib`, `non-free`, `non-free-firmware`, **When** `ensure-base` runs, **Then** its output starts with a descriptive, non-blocking warning naming the missing components.
2. **Given** a graybeard config with all four trixie components, **When** `ensure-base` runs, **Then** no such warning is emitted.
3. **Given** a non-graybeard config (e.g. `source = true` with a finite depth, or `source = false`), **When** `ensure-base` runs, **Then** the warning is not emitted.

---

### User Story 7 - The Release Paper-Trail Is Complete (Priority: P2)

Before the alpha tag, the release-facing records are correct and complete: the README's figures are current (24 feature specs; 914 recorded tests), the dangling `LICENSE` ToDo is resolved with a real GPL-3.0-or-later `LICENSE` file at the project root, the `AGENTS.md` SPECKIT block reflects the actually-implemented state (spec 024 complete; this feature current), and `manifests/hyprland-0.49.toml`'s `reason` records the manual build-verified 0.49 pin (matching `docs/005`) so `align-repo --verify` re-validation is self-consistent.

**Why this priority**: For an alpha tag this is release-managerial, not cosmetic — stale figures, a missing license, and a manifest that contradicts its own documentation undermine the artifact's credibility and its agent-facing map.

**Independent Test**: Review the README figures, confirm a `LICENSE` file exists and is referenced, confirm the `AGENTS.md` block is current, and confirm the Hyprland manifest's `reason` records the manual pin.

**Acceptance Scenarios**:

1. **Given** the README, **When** it is reviewed, **Then** the figures are current (24 feature specs, 914 recorded tests) and there are no dangling ToDo placeholders (the `LICENSE` note resolves to a real GPL-3.0-or-later file).
2. **Given** the `AGENTS.md` SPECKIT block, **When** it is updated, **Then** it reflects the implemented spec 024 status and this feature, so the agent-facing map is accurate.
3. **Given** `manifests/hyprland-0.49.toml`, **When** it is inspected, **Then** its `reason` records the manual build-verified 0.49 pin (matching `docs/005`), so `align-repo --verify` re-validation is self-consistent.

---

### User Story 8 - Code-Review Low-Hanging Fruit Is Taken Along (Priority: P2)

The code review's consolidation and craft items land with the alpha: duplicated filesystem/hash helpers become one shared set (preventing the next divergent-copy defect of the R-2 class); the three private copies of the dependency-satisfaction rule become one shared predicate; shared helpers that already have external consumers are exposed under public names; the verified-`local` paragraph emission becomes one generator used by handoff, diff, and package-stack (which starts warning instead of silently dropping unreadable payloads); duplicated packaging/changelog/maintainer and release-hash helpers are consolidated; near-duplicate test scaffolding moves into the shared fixtures; zero-risk craft sweeps are applied; config path overrides other than `base_dir` are validated as absolute; the quickstart gains a per-command external-tool prerequisites matrix; and the loopback index-fetch path forwards the same hook as the verified path. No behavior changes beyond the intended fixes; the existing contract suite stays green.

**Why this priority**: This is the user's explicit "take along low hanging fruit" instruction. The items are cheap and low-risk, several are directly implicated in the functional defects (F5 = R-2, F8 = R-3), and landing them now prevents the divergence class that produced R-2.

**Independent Test**: After the consolidation, the full recorded test suite (both runners, ruff, contract suite) is green, with no output-shape regressions except the intended new warning when package-stack encounters an unreadable payload.

**Acceptance Scenarios**:

1. **Given** the duplicated file-hash and atomic-write/copy helpers, **When** they are consolidated into one shared home with a uniform API, **Then** all call sites use the shared helpers and no divergent copies remain (the R-2-class defect is prevented).
2. **Given** the three private dependency-satisfaction implementations (import lane, verify, provider selection), **When** they are unified into one predicate, **Then** all three consumers share the same rule and the contract suite stays green.
3. **Given** the underscore-private helpers that already have external consumers (pool-prefix, local paragraph entry, paragraph builder, gzip writer, atomic write, error printer), **When** they are exposed under public names, **Then** the cross-module imports are clean with zero behavior change.
4. **Given** the three verified-`local` paragraph emission loops, **When** they are replaced by one shared generator, **Then** handoff, diff, and package-stack use it, and package-stack surfaces a warning instead of silently dropping unreadable payloads.
5. **Given** the duplicated changelog-date / fallback-maintainer / maintainer-resolution helpers and the duplicated release-hash entry computations, **When** they are consolidated into shared helpers, **Then** debgen/srcgen and the index-hash sites share them, with the decompress-on-demand variant preserved, and the duplicated `.dsc` reading shares one helper.
6. **Given** the repeated test scaffolding (`_insert_binary`, `_config`, fixture pool-prefix), **When** it is consolidated into the shared test fixtures, **Then** the near-identical definitions become a single update point.
7. **Given** the zero-risk craft items (legacy typing import, dead file-hash call with its misleading comment, shadowing exception name, typed resolver accumulator), **When** they are swept, **Then** they are fixed with no behavior change.
8. **Given** a config with a relative path override other than `base_dir`, **When** config loads, **Then** it is rejected with a clear config error — cache locations are never silently dependent on the working directory.
9. **Given** the quickstart, **When** it is extended, **Then** it includes a per-command external-tool prerequisites matrix (command → required tools/packages) covering the runtime's shelled-out tools and their failure modes.

---

### User Story 9 - Release Verification: Baseline, Artifact, Physical Proofs (Priority: P2)

With the fixes landed, the release checks are executed and recorded: the full test baseline re-runs green under both runners with ruff clean and the contract suite green, and the recorded baseline is re-recorded; the built wheel is inspected (it ships the keyring and runs without dev extras — closing the artifact-inspection follow-up); and the two never-run physical proofs — FAT-USB bootstrap and a graybeard DVD-1 offline install simulation — are executed with results appended to the release notes.

**Why this priority**: These are the standing physical proofs from the previous reviews' checklists. Executing them is what turns "correct by inspection" into "verified by execution" for the alpha tag.

**Independent Test**: Run the recorded baseline after the fixes, inspect the built wheel in a fresh no-extras environment, and execute the FAT-USB and DVD-1 physical proofs; all results are appended to the release notes.

**Acceptance Scenarios**:

1. **Given** the completed fixes, **When** the recorded test baseline runs, **Then** it is green under both runners with ruff clean and the contract suite green, and the baseline is re-recorded.
2. **Given** the built wheel, **When** it is inspected in a fresh no-extras environment, **Then** it ships the archive-signing keyring and runs every command (closing the artifact-inspection follow-up).
3. **Given** a FAT-USB media build and a graybeard DVD-1 cache, **When** the physical proofs are executed, **Then** FAT-USB bootstrap and the graybeard offline install simulation complete, and the results are appended to the release notes.

---

### User Story 10 - Tutorial Track, Authored Jointly, Then the Alpha Version Bump (Priority: P3)

The Administrator and the agent add new tutorials to the project in collaborative working sessions — none of this tutorial content exists yet. Topics, scope, and the tutorial style are decided together as the phase proceeds; the agent drafts, the Administrator reviews and steers. The tutorials introduce a **new** tutorial style agreed jointly during the phase — distinct from the existing how-to documents such as `docs/001`–`docs/005` — and each tutorial stays consistent with the implemented behavior from Phases 1–2, so following it never contradicts the shipped tool. At the end of Phase 3, once the tutorials are complete and agreed, the project version is set to `0.1.0-alpha`, and the release-facing references (README, quickstart) are consistent with that version.

**Why this priority**: This is the closing phase of the pre-alpha work — the tutorials turn the hardened tool into something a new user can follow, and the version bump is the concrete marker that the alpha release is cut. It is P3 because it depends on Phases 1–2 being done and is content-led rather than correctness-critical.

**Independent Test**: A tutorial added in a working session is complete, follows the agreed tutorial style, and its steps match the implemented behavior; after the tutorials are agreed, `pyproject.toml` reports `0.1.0-alpha` and the README/quickstart references are consistent.

**Acceptance Scenarios**:

1. **Given** a working authoring session, **When** a tutorial topic is agreed, **Then** the tutorial is drafted and added to the project in the agreed tutorial style — a new style defined during the phase, reproducible from the docs and cache alone — with the Administrator reviewing and steering.
2. **Given** the agreed tutorial set, **When** each tutorial is added, **Then** its commands and steps are consistent with the implemented behavior (Phases 1–2), so following it does not contradict the shipped tool.
3. **Given** the tutorials complete and agreed at the end of Phase 3, **When** the version is set, **Then** the project version is `0.1.0-alpha`, and the release-facing references (README, quickstart) are consistent with that version.

---

### Edge Cases

- **R-2 — cache with no `local` packages**: the downloader behaves exactly as before; the `local` exclusion never affects archive-only caches.
- **R-2 — `local` payload already deleted**: the existing excluded-payload path handles it; the `local` index itself is never the reported error.
- **R-3 — re-importing an older version**: the newest `current` row stays current; no downgrade and no duplicate row.
- **R-3 — re-importing the identical version**: stays idempotent (existing behavior preserved).
- **R-3 — stale payload retention**: superseded payloads are retained and checksummed so lagging operators still install; they are never deleted, only de-referenced in the current index.
- **R-4 — first source is trixie but its first component is not `main`**: the baseline follows the configured first component, not a hard-coded one.
- **R-4 — no cached index for the baseline at all**: the verdict reflects the actual configured baseline rather than blanket "unmet".
- **R-5 — pre-alpha cache created under the old layout**: no migration; a fresh key regenerates at the new location, and any legacy in-tree keyring is not used (acceptable before any cache has shipped).
- **R-5 — administrator copies only the payload tree**: the private key is already absent; nothing extra to do.
- **R-5 — trusted relay operator**: the documented posture tells the admin to include the keyring location in that copy deliberately.
- **Graybeard warning — developer config (`source = true`, finite depth)**: not graybeard; the warning does not fire.
- **Graybeard warning — config lists all four components but the image differs**: the check is config-driven (the four canonical components), independent of what the ISO actually contains.
- **F4 — unreadable `local` payload during `package-stack`**: a warning names the component instead of silently skipping it.
- **R-10 — absolute override**: accepted; **relative override**: rejected with a clear config error.
- **R-1 — offline install / no network**: the no-extras install smoke test and wheel inspection are local-only checks and do not require network.
- **Phase 3 — tutorial steps drift from behavior**: a tutorial that documents a changed behavior (e.g. after a Phase-1/2 fix) is corrected in the same working session, keeping tutorials aligned with the implemented tool.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A standard install with no extra packages MUST provide every runtime dependency the CLI needs, so every command — including `--help` and `ensure-base`'s ISO-reading path — works after `pip install .` (no `ModuleNotFoundError`).
- **FR-002**: The built artifact MUST ship the archive-signing keyring and run without dev extras; a no-extras fresh-environment smoke test and a built-artifact inspection MUST be part of the release checks (closing the review 020 / N-4 artifact-inspection follow-up).
- **FR-003**: The downloader MUST NOT re-verify `local`-component binary rows against a per-release archive index during its per-run re-check; after a local import, `download` MUST exit 0 with zero command errors even when nothing is pending, and `local` records MUST remain `verified`. An engine-level regression test (import `local` deb → `download`) MUST pin this.
- **FR-004**: Importing a local binary MUST supersede prior `current` local rows for the same (package, architecture, `local` component) so exactly one `current` row exists per name/architecture, reusing the resolver's existing supersede pattern rather than a new copy; a cross-version import unit test MUST pin the rule.
- **FR-005**: Superseded `local` payloads MUST be retained in the pool (content-addressed, still checksummed on sync) and MUST remain installable by lagging operators; row selection for a name with multiple `current`-eligible sources MUST be deterministic.
- **FR-006**: `align-repo` MUST derive its baseline release/component from the configured sources (the first configured archive source) instead of a hard-coded value and MUST report the correct era verdict on any configured source layout; a config-driven variant test MUST cover a non-trixie / first-component-not-`main` configuration.
- **FR-007**: The cache-local signing keyring MUST live outside the handoff payload tree (relocated to a base-dir-level location), so a generated payload contains only the public key; no automatic migration of legacy in-tree keyrings is performed.
- **FR-008**: The documentation MUST record the key-handling decision: the private key is not part of the handoff payload; an administrator copying the whole cache directory SHOULD exclude the keyring location from operator media unless the operator is a trusted relay that must re-sign.
- **FR-009**: Under the graybeard configuration (`[fetch] source = true` AND `[resolution] max_source_dep_depth = -1`), `ensure-base` MUST detect when the config's trixie sources omit any of `main`, `contrib`, `non-free`, `non-free-firmware` and MUST start its output with a descriptive, non-blocking warning naming the missing components (first lines of the text output; present in the run's warnings in machine-readable output); under complete or non-graybeard configs, no such warning is emitted.
- **FR-010**: The release paper-trail MUST be current: README figures accurate (24 feature specs, 914 recorded tests), a GPL-3.0-or-later `LICENSE` file present at the project root and referenced, the `AGENTS.md` SPECKIT block reflecting implemented status, and the Hyprland manifest's `reason` recording the manual build-verified 0.49 pin.
- **FR-011**: The duplicated filesystem/hash helpers (file SHA-256, atomic write, atomic copy) MUST be consolidated into one shared home with a uniform API, and all call sites MUST be re-pointed to it (eliminating the divergent-copy defect class).
- **FR-012**: The dependency-satisfaction rule MUST be a single shared predicate used by the import lane, the verify command, and consulted by the resolver's provider selection, so the private copies cannot drift.
- **FR-013**: Shared internal helpers that already have external consumers MUST be exposed under public names, and the zero-risk craft sweeps (modern typing syntax, removal of the dead file-hash call and its misleading comment, non-shadowing exception name, typed resolver accumulator) MUST be applied with no behavior change.
- **FR-014**: The verified-`local` paragraph emission MUST be one shared generator used by handoff, diff, and package-stack; package-stack MUST surface a warning instead of silently dropping unreadable payloads.
- **FR-015**: The duplicated packaging helpers (changelog date, fallback maintainer, maintainer resolution) and the release-hash entry computation MUST be consolidated into shared helpers (with the decompress-on-demand variant preserved), and the duplicated `.dsc` reading MUST share one helper.
- **FR-016**: The near-duplicate test scaffolding (`_insert_binary`, `_config`, fixture pool-prefix) MUST be consolidated into the shared test fixtures with a single update point.
- **FR-017**: Configuration path overrides other than `base_dir` MUST be validated as absolute after expansion and rejected with a clear config error when relative, so cache locations are never silently dependent on the working directory.
- **FR-018**: The quickstart MUST include a per-command external-tool prerequisites matrix (command → required tools/packages) covering the runtime's shelled-out tools and their failure modes.
- **FR-019**: The loopback/unverified index-fetch path MUST forward the same fetch hook as the verified path (consistency for test/tooling reuse).
- **FR-020**: After the fixes, the recorded test baseline MUST be re-run and re-recorded (both runners, ruff clean, contract suite green), the built artifact MUST be inspected, and the FAT-USB bootstrap and graybeard DVD-1 physical proofs MUST be executed with results appended to the release notes.
- **FR-021**: The project MUST gain new tutorials (topics, scope, and a new tutorial style agreed jointly between the Administrator and the agent during the phase; none pre-existing, and distinct from the existing how-to documents) that are consistent with the implemented behavior; at the end of the phase, the project version MUST be set to `0.1.0-alpha` and the release-facing references MUST be consistent with it.

### Key Entities *(include if feature involves data)*

- **Signing keyring (relocated)**: the persistent cache-local secret keyring, moved out of the handoff payload tree to a base-dir-level location; only the public `offline-cache.gpg` ships in the payload (FR-007/FR-008).
- **Handoff payload**: the derived operator apt source; now public-key-only by default (FR-007/FR-008).
- **`local` component rows (DB)**: binary records in the `local` component with `current`/`verified` semantics; superseded on newer-version import so exactly one `current` row exists per name/architecture (FR-004/FR-005).
- **Configured archive sources**: the config's `archive.source` entries; the source of the `align-repo` baseline (FR-006) and the check for graybeard component completeness (FR-009).
- **Test fixture scaffolding**: the shared `_insert_binary` / `_config` / pool-prefix fixtures consolidated into `tests/fixtures/` (FR-016).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a fresh no-extras install (source tree and built wheel), every command including `--help` runs with zero import errors, and a fixture-ISO `ensure-base` completes — measured by the no-extras smoke test and artifact inspection.
- **SC-002**: After any local import, every subsequent `download` exits 0 with zero command errors and `local` rows stay `verified` — measured by the engine-level regression test and repeated-run flow.
- **SC-003**: Re-importing a newer `local` version yields exactly one `current` row per name/architecture, the handoff/diff emit only the newest stanza, the stale payload is retained, and all readers are deterministic — measured by the cross-version unit test and the multi-source tie test.
- **SC-004**: `align-repo` reports the correct verdict on a non-trixie / first-component-not-`main` configuration with no false "everything unmet" — measured by the config-driven variant test.
- **SC-005**: A generated handoff payload contains no private key material (only the public key ships), and the copy-time decision is documented — measured by payload inspection and the documentation check.
- **SC-006**: Under graybeard config, `ensure-base` output starts with the missing-component warning when components are missing and has no such warning when all four are present — measured by the graybeard warning tests.
- **SC-007**: 100% of the review findings in scope (R-1…R-6, R-8…R-11, F1…F10) are closed with tests; the recorded baseline is re-recorded green (both runners, ruff, contract suite); the wheel is inspected; and the FAT-USB and DVD-1 physical proofs are executed and appended to the release notes.
- **SC-008**: Documentation is current and internally consistent: README figures, the GPL-3.0-or-later `LICENSE` file, `AGENTS.md` block, manifest `reason`, quickstart prerequisites matrix, and the key-handling decision all match the implemented behavior.
- **SC-009**: The agreed tutorial set is present, follows the agreed tutorial style (a new style defined during the phase), and its steps match the implemented behavior; the project version reads `0.1.0-alpha`, and the README/quickstart reference it consistently.

## Assumptions

- **Alpha framing**: This is the final spec before the alpha tag; all listed items land in this cycle, and the recorded test baseline is re-recorded as part of it.
- **Pre-alpha, no shipped caches**: the R-5 keyring relocation requires no migration; a cache created under the old layout regenerates a fresh key at the new location, and any legacy in-tree keyring is unused (accepted before any cache has shipped).
- **The four canonical Trixie components** are `main`, `contrib`, `non-free`, `non-free-firmware` (the graybeard preset set).
- **"Graybeard" is detected by config values** (`[fetch] source = true` AND `[resolution] max_source_dep_depth = -1`); there is no profile concept in the tool.
- **`align-repo` baseline** = the first configured archive source's release/component.
- **License**: the README's dangling `LICENSE` ToDo is resolved by adding a **GPL-3.0-or-later** `LICENSE` file at the project root (user decision, Clarifications Q4); the README references it and no ToDo placeholder remains.
- **Consolidation preserves semantics**: the unified dependency-satisfaction predicate and the shared helpers keep current behavior — the contract suite staying green is the acceptance (FR-011…FR-016).
- **Overlapping findings are single deliverables**: F5 = R-2, F8 = R-3, F10 ⊃ R-8 — no double work.
- **Out of scope (per user instruction)**: the carried-over low-severity backlog (L-1…L-12 / R-7) and the residual N-2b documentation note are deferred to a post-alpha polish spec.
- **Phase 3 is collaborative and content-led**: the tutorials are authored jointly between the Administrator and the agent in working sessions; their topics and scope are not fixed by this spec and are agreed as the phase proceeds. The version bump to `0.1.0-alpha` is the Phase-3 exit gate, applied once the tutorials are complete and agreed.

## Clarifications

### Session

- Q1 — R-5 private signing key default: → A: Relocate the persistent keyring outside the handoff payload tree (e.g. `{base_dir}/.gnupg-handoff/`), with **no migration** for existing caches. The decision is documented because in most cases the admin copies the whole `{base_dir}`; if security is of concern they exclude the private key from the copy. By default the payload ships only the public key (FR-007/FR-008).
- Q2 — Spec scope: → A: **Everything from both reviews** — R-1…R-6, R-8…R-11, F1…F10, the graybeard warning, and the release verification (baseline re-record, artifact inspection, physical proofs). The L-1…L-12 / R-7 backlog is skipped per the user's instruction. A Phase 3 was added after the initial session per user request: collaborative tutorial authoring plus the version bump to `0.1.0-alpha` (FR-021). (FR-001…FR-021.)
- Q3 — Graybeard warning behavior: → A: Advisory and non-blocking, printed as the first lines of the text output and present in the run's warnings in machine-readable output; fires only under graybeard config when any of the four Trixie components is missing from the config (FR-009).
- Q4 — Project license (R-6): → A: **GPL-3.0-or-later**. The `LICENSE` file at the project root uses GPL-3.0-or-later, the README references it, and the dangling `LICENSE` ToDo is resolved (FR-010, SC-008).
