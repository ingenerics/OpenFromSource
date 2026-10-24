# Feature Specification: Apt VersionHash Fidelity

**Feature Branch**: `022-apt-version-hash`
**Status**: Draft

**Input**: User description: "Spec apt version hash - To ensure the packages from the local cache only appear as upgradeable when there is actually an upgrade, apt's VersionHash needs to be able to work properly."

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Builds the cache and generates the handoff/diff indexes the operator consumes; must ship indexes whose metadata matches the actual `.deb` payloads |
| **Operator** | Offline system (no network) | Installs and upgrades packages from the pre-built cache through ordinary apt tooling; must see a package as upgradable only when a newer version is genuinely available |

**Phase 1 — Index Fidelity**: The administrator generates the handoff/diff `Packages` indexes (specs 012/013) whose per-package stanzas carry apt's VersionHash-relevant control fields (`Installed-Size`, `Depends`, `Pre-Depends`, `Conflicts`, `Breaks`, `Replaces`) verbatim from the actual package control — the same fields dpkg records when the shipped `.deb` is installed. Run by the Administrator.

**Phase 2 — Upgrade Accuracy**: The operator configures the cache as an apt source (spec 012) and runs ordinary `apt update` / `apt list --upgradable`. apt's VersionHash for each repo version matches the installed dpkg-status record's, so apt merges them and reports a package as upgradable **only** when a strictly newer version is available. Run by the Operator.

---

### User Story 1 - An Installed Package Is Not Reported as Upgradable to Its Own Version (Priority: P1)

The operator has already installed a package from the cache (or from upstream) at a specific version. The cache's handoff repo also carries that exact version. When the operator runs `sudo apt update` and `apt list --upgradable`, the package must **not** appear as upgradable "from `<version>` to `<version>`" — the identical installed version must be recognised as already current, because the repo's metadata and the installed record hash identically in apt's view.

**Why this priority**: This is the whole point of the feature. False "upgradable to the same version" reports are confusing, pollute upgrade output, and erode trust in the cache; the reported regression (debianutils, libexpat1, libpam-modules, mount, perl-base, sed, tar) is exactly this failure.

**Independent Test**: Install a package at the version the cache holds, run `apt update` against the handoff source, and confirm `apt list --upgradable` does not list it — while a package for which the cache genuinely holds a newer version still does.

**Acceptance Scenarios**:

1. **Given** an operator system where package `P` is installed at version `V` and the cache's handoff repo also serves `P V`, **When** the operator runs `apt update` and `apt list --upgradable`, **Then** `P` is not listed as upgradable (the repo version `V` is merged with the installed record `V`).
2. **Given** the same setup but the handoff repo serves only `P V` while the installed version is `V'` (strictly older), **When** the operator runs `apt list --upgradable`, **Then** `P` IS listed as upgradable from `V'` to `V` and installs cleanly.
3. **Given** a cache where every package's repo stanza reproduces the VersionHash of the installed record, **When** the operator runs `apt update`, **Then** apt produces no upgrade list containing an "X upgradable from X" self-upgrade for any package.

---

### User Story 2 - Repo Stanzas Carry the VersionHash Fields Verbatim, Including Their Absence (Priority: P1)

The handoff and diff `Packages` stanzas reproduce the VersionHash-relevant control fields (`Installed-Size`, `Depends`, `Pre-Depends`, `Conflicts`, `Breaks`, `Replaces`) exactly as they appear in the shipped `.deb`'s control — the same record dpkg writes on install. Critically, a package whose control has **no** `Depends` field (only, say, a `Pre-Depends`) must not gain an invented `Depends` in the repo stanza; any divergence changes apt's VersionHash and re-opens the false-upgradable regression.

**Why this priority**: This is the mechanism behind US1. The historical bug was emitting the resolver's merged `Depends`+`Pre-Depends` view as `Depends` when the original field was absent, which changed the hash for packages such as `debianutils`, `sed`, `tar`, `perl-base`, `mount`, `libexpat1`, and `libpam-modules`.

**Independent Test**: For a package whose `.deb` control has `Pre-Depends` but no `Depends`, generate the handoff and confirm the emitted stanza contains `Pre-Depends` but no `Depends` line; confirm the stanza's VersionHash equals the `.deb` control's VersionHash.

**Acceptance Scenarios**:

1. **Given** a `.deb` whose control declares `Pre-Depends: libc6 (>= 2.38)` and no `Depends`, **When** the handoff index is generated for it, **Then** the emitted stanza carries `Pre-Depends` verbatim and contains no `Depends` field.
2. **Given** a `.deb` whose control declares both `Depends` and `Pre-Depends`, **When** the handoff index is generated, **Then** each field is emitted with its original text, unmerged and undeduplicated against the other.
3. **Given** any package in the cache, **When** its handoff stanza's VersionHash is computed over `{Installed-Size, Depends, Pre-Depends, Conflicts, Breaks, Replaces}` and compared with the same computation over the `.deb`'s control, **Then** the two hashes are identical.
4. **Given** a package whose control is not a readable `.deb` (local component), **When** the handoff is generated, **Then** generation does not fail: the stanza falls back to the database-only reconstruction rather than aborting the whole index, the fallback stanza still reproduces the VersionHash fields from the stored values, and a warning flags the affected package.

---

### User Story 3 - The VersionHash Contract Is Documented in the Project (Priority: P2)

The knowledge that enables this correctness — apt's VersionHash algorithm, the exact field set and order it covers, the hashing rules, and the reference implementation location — lives with the project (spec, plan, and research artifacts), so future work does not have to look it up externally and does not silently regress it.

**Why this priority**: Correctness currently rests on a subtle, external, undocumented algorithm. Recording it makes the behavior maintainable and testable, and is the stated intent behind this feature ("the information should be included with the project").

**Independent Test**: A reader of the spec/plan can determine, without any external lookup, which fields contribute to apt's VersionHash, in what order, and with what normalisation rules, and can find where the reference implementation lives.

**Acceptance Scenarios**:

1. **Given** the feature's planning/research artifacts, **When** a developer needs to know which control fields feed apt's VersionHash, **Then** the exact set (`Installed-Size`, `Depends`, `Pre-Depends`, `Conflicts`, `Breaks`, `Replaces`), their order, and the normalisation rules (whitespace and `=` stripped, `0:` epochs removed, case folded) are stated in the project.
2. **Given** the documented algorithm, **When** a package stanza is changed, **Then** the change can be checked against the documented contract to confirm it preserves VersionHash parity.

---

### User Story 4 - Genuine Upgrades Still Work Across All Components and Channels (Priority: P2)

The fix must not blunt upgrades: a strictly newer version in the cache still presents as upgradable and installs. The behavior holds for every component the cache serves — upstream components (`main`, `contrib`, `non-free`, backports) and the `local` component — and for both delivery channels (the baseline `handoff` and the incremental `diff`/`sync`).

**Why this priority**: Correctness of "not upgradable when equal" is only meaningful if "upgradable when newer" still functions, everywhere packages are served.

**Independent Test**: With a package at an older installed version and a newer version in the cache, confirm it appears upgradable and installs; repeat across `main`, `local`, the handoff index, and the diff index.

**Acceptance Scenarios**:

1. **Given** an installed package at version `V` and a cache holding `V+1`, **When** the operator runs `apt upgrade`, **Then** the package upgrades to `V+1` from the cache with no network access.
2. **Given** a cache with packages in `main` and `local` components, **When** the handoff and diff indexes are generated, **Then** every emitted stanza for both components satisfies the VersionHash-parity contract of US2.
3. **Given** a `local`-component package whose payload control is readable, **When** its handoff and diff stanzas are generated, **Then** both reproduce the control's VersionHash fields verbatim.

---

### Edge Cases

- Package whose control has only `Pre-Depends` and no `Depends` → the stanza keeps the field absent; no invented `Depends` is emitted (the root cause of the regression).
- Package with none of the VersionHash dependency fields → no dependency fields are emitted; the hash contribution is from the fields that do exist.
- Package with `Installed-Size` only → emitted verbatim; presence/absence of each field is what matters, not a fixed set.
- Whitespace/operator formatting differences between the index and the installed record → hash-insensitive: apt strips whitespace and `=` and folds case, so only the semantic field content matters.
- Versions with a `0:` epoch prefix → hash-insensitive: apt strips `0:` epochs before hashing.
- `local` payload that is missing or not a readable `.deb` → generation falls back to the database-only reconstruction and does not fail the whole index; the fallback still emits the VersionHash fields from the stored values, so the stanza remains parity-correct (degraded in payload source only, not in hash fidelity), and a per-package warning flags the affected package so the Administrator knows the payload is missing/corrupt.
- Multi-arch packages (`Multi-Arch: foreign` / `same`) → unaffected by VersionHash, but the `Multi-Arch` field is preserved so `name:any` resolution keeps working.
- A genuine new upstream version vs. a same-version repo entry → version ordering decides; equal versions merge (not upgradable), strictly newer versions upgrade (US4).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The generated `Packages` indexes (handoff and diff, all components) MUST carry apt's VersionHash-relevant control fields — `Installed-Size`, `Depends`, `Pre-Depends`, `Conflicts`, `Breaks`, `Replaces` — verbatim from the package's actual control record (the archive index for upstream components, the shipped `.deb` control for the `local` component).
- **FR-002**: The indexes MUST NOT emit a `Depends` field for a package whose control has none — the resolver's merged `Depends`+`Pre-Depends` view MUST never appear as a `Depends` line in a generated stanza.
- **FR-003**: For every emitted stanza, the VersionHash computed over `{Installed-Size, Depends, Pre-Depends, Conflicts, Breaks, Replaces}` MUST equal the VersionHash of the dpkg-status record that installing the shipped `.deb` produces, so apt merges the repo version with the installed version at equal version strings.
- **FR-004**: Upgrade semantics MUST be preserved: a strictly newer cached version MUST still be reported as upgradable and install from the cache.
- **FR-005**: The VersionHash-fidelity contract MUST hold for every component the cache serves (upstream `main`/`contrib`/`non-free`/backports and `local`) and for both delivery channels (baseline `handoff` and incremental `diff`/`sync`).
- **FR-006**: A `local` payload that is missing or not a readable `.deb` MUST fall back to the database-only reconstruction instead of failing index generation, and that reconstruction MUST still emit the VersionHash fields from the values captured and stored at load time so the fallback stanza remains parity-correct (parity is not forfeited on the degraded path). The fallback MUST also emit a per-package warning flagging the affected package, so the Administrator is aware the payload is missing/corrupt.
- **FR-007**: The project (spec + planning artifacts) MUST document apt's VersionHash contract — the field set, their order, the normalisation rules (whitespace and `=` stripped, `0:` epochs removed, case folded), the merge semantics (equal version + equal hash merge; equal version + different hash stay separate and report upgradable), and the location of apt's reference implementation — so the behavior is maintainable without external lookup. The contract is treated as stable across the apt 3.x series (the Trixie target), with apt 3.0.3 as the reference snapshot.

### Key Entities *(include if feature involves data)*

- **Packages stanza (handoff/diff index)**: The per-package paragraph emitted into `Packages.gz`; its VersionHash-relevant fields must reproduce the shipped `.deb` control exactly.
- **dpkg-status record**: The control fields dpkg writes when the `.deb` is installed on the operator; this is the record apt hashes when reading the installed state, and the parity target for repo stanzas.
- **VersionHash**: apt's hash over `{Installed-Size, Depends, Pre-Depends, Conflicts, Breaks, Replaces}` (in that order) with whitespace/`=` stripping, `0:` epoch removal, and case folding; equal version strings with different hashes are kept as separate version objects. (The precise location of apt's reference implementation is recorded in the planning artifacts; the contract is treated as stable across the apt 3.x series, apt 3.0.3 being the reference snapshot.)
- **`.deb` control**: The authoritative source of the VersionHash fields for the `local` component (read from the pool payload) and the provenance of the archive index for upstream components.
- **Pool payload**: The shipped `.deb`; the exact bytes the operator installs, hence the record dpkg will write.
- **Merged dependency view (`depends_raw`)**: The resolver's internal Depends+Pre-Depends union used for closure walking — never to be emitted into a generated stanza.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On an operator with the identical version installed, `apt list --upgradable` reports zero self-upgrades ("X upgradable from X") for any package served by the cache.
- **SC-002**: 100% of generated stanzas — including degraded-fallback stanzas — produce a VersionHash identical to the installed dpkg-status record's VersionHash for the same package and version (verified over the full cache contents, all components).
- **SC-003**: A strictly newer cached version is reported as upgradable and installs offline 100% of the time.
- **SC-004**: VersionHash parity holds for every component and both channels — no component or channel ships a stanza that deviates from the contract.
- **SC-005**: A developer can determine apt's VersionHash field set, order, and normalisation rules from the project artifacts alone, without external reference.

## Assumptions

- **Equal version, equal hash → merged**: apt merges a repo version with an installed version of the same version string when their VersionHashes match, which is what makes "identical version not upgradable" hold.
- **Equal version, different hash → separate**: a differing VersionHash keeps the repo version distinct from the installed record, so apt reports the identical version as upgradable — this is the failure mode the feature eliminates.
- **Archive index fidelity**: For upstream components, the cached archive index's dependency fields are generated from the same `.deb` control the operator installs, so carrying them verbatim achieves parity; field text spacing/case is hash-insensitive anyway.
- **Field order matters**: the hash folds the fields in the fixed order `Installed-Size`, `Depends`, `Pre-Depends`, `Conflicts`, `Breaks`, `Replaces`; a field's absence contributes nothing.
- **Degraded fallback parity-preserving**: a `local` payload that is unreadable falls back to the database reconstruction (rather than failing the whole generation); because the project owns the reconstruction code, the fallback emits the VersionHash fields from the values captured and stored at load time, so hash parity is preserved on the degraded path rather than forfeited.
- **Scope boundary**: This feature is about the *metadata* the cache emits. It does not change how apt is invoked, does not modify packages, and does not alter resolution or download behavior. The operator continues to use ordinary apt tooling.
- **Implementation status**: The corrective work described here has already been implemented and verified; this spec captures the required behavior and the VersionHash contract as project knowledge so it is referenceable and regression-safe.

## Clarifications

### Session

- Q: Is this a forward-looking feature request or documentation of already-completed work? → A: Documentation of already-completed corrective work. The VersionHash divergence bug (packages shown as "upgradable from X to X") was diagnosed against the real cache and fixed; this spec records the required behavior and the apt VersionHash contract so the fix is maintainable, testable, and regression-safe.
- Q: Where does the VersionHash contract live once this feature is done? → A: In the feature's planning artifacts (plan.md/research) plus this spec — the field set, order, normalisation rules, merge semantics, and the location of apt's reference implementation are recorded in-project so no external lookup is needed (FR-007, SC-005).
- Q: Does the fix apply to the `local` component's unreadable-payload fallback too? → A: Yes, but only as a degraded path: a readable control is authoritative (missing `Depends` stays missing), and an unreadable payload falls back to the DB reconstruction without failing generation (FR-006).
- Q: Is VersionHash parity unachievable for the degraded DB-only fallback (unreadable local payload), which would break SC-002's 100% claim? → A: No — the project owns the reconstruction code, so the DB-only fallback is intercepted at that point and emits the VersionHash fields from the values captured and stored at load time; the fallback remains parity-correct, so SC-002's 100% claim holds across all stanzas without exclusion (FR-006, SC-002).
- Q: When a `local` payload is unreadable at handoff/diff generation, should the Administrator be warned? → A: Yes — generation emits a per-package warning flagging the affected package while the index still builds. A well-formed `.deb` always carries its control member, so this is a defensive path for abnormal states (missing/corrupt/non-`.deb` payload); the warning surfaces a payload that may also fail at actual install (FR-006).
- Q: How is the documented VersionHash contract anchored to an apt version? → A: It is treated as stable across the apt 3.x series (the Trixie target); apt 3.0.3 is the reference snapshot recorded in the planning artifacts, with no re-verification note required for apt upgrades within the series (FR-007, SC-005).
