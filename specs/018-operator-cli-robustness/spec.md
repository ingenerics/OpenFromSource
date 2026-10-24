# Feature Specification: Operator Track & CLI/Config Robustness

**Feature Branch**: `018-operator-cli-robustness`
**Status**: Draft

**Input**: User description: "operator track and CLI/config robustness - As discussed in current session."

**Design context**: The alpha reviews (`review/02_alpha_kimi_k3/`, `review/03_alpha_glm_5_3/`) surfaced, alongside the blockers already tackled in spec 015, a batch of high-priority robustness items grouped by area in `review/04_alpha_summary/02_high_priority_followups.md`. Per the agreed three-spec split of that file, this is the third follow-up feature — the **operator-track** handoff/apply fixes (F-18, F-19) together with the **Config / CLI / database** robustness cluster (F-20, F-21, F-22). It is sequenced after 016 (Resolver & dependency correctness) and 017 (Download & cache integrity), all in the same alpha release. The theme: what the operator receives and runs must be honest and clean, and the CLI/config surface must fail structurally rather than crash.

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Generates handoffs/diffs and configures the project; this feature changes what `handoff` includes and reports, and how `config`/`show`/`list`/`verify` behave on bad or uninitialized state |
| **Operator** | Offline system (no network) | Merges shipped diffs with the apply script; this feature makes the apply output a clean, parseable count instead of raw apt noise |

**Phase 1 — Loud Handoff Exclusion (F-18)**: a handoff excludes — loudly — any verified record whose release/component is no longer configured, and never marks such a record delivered.
**Phase 2 — Clean Apply Output (F-19)**: the operator's apply script keeps its count capture clean — apt/progress output never leaks into the summary, and the already-applied path works.
**Phase 3 — Structured Config Errors (F-20)**: every mistyped configuration value fails structurally (naming the key and expected type) instead of crashing with a raw traceback.
**Phase 4 — Local Overrides Always Apply (F-21)**: a local override for a known key applies even when the defaults file lacks the section, and the merged result is validated at load.
**Phase 5 — DB-Gated Read Commands (F-22)**: the read commands gate on cache initialization/schema validity like their siblings — clean error, no stray database file created.

---

### User Story 1 - Handoff Reports Out-of-Config Records Loudly (Priority: P2)

The administrator narrows the configured components after packages were already verified (e.g. the configuration drops `contrib` so only `main` remains). Today a verified record whose `(release, component)` is no longer configured passes every handoff check, gets its stanza appended and its id marked delivered — yet is never written anywhere, with no warning. The operator never sees the package, and the false delivery record later breaks `sync` ("excluded delivered"). This phase makes the handoff exclude any record outside the currently configured components — loudly, and refusing to mark those ids delivered, mirroring how diff generation already excludes such records.

**Why this priority**: Silent omission plus a false delivery record both hides packages from the operator and poisons later sync runs — a correctness failure in the core handoff path.

**Independent Test**: Register and verify a record under `trixie/contrib`, then regenerate the handoff with the configuration reduced to `main` — the record is omitted, reported loudly, and its delivery id is not advanced.

**Acceptance Scenarios**:

1. **Given** a verified record whose `(release, component)` is no longer in the configured set, **When** the administrator generates a handoff, **Then** the record is excluded from the output and a clear report notes the exclusion.
2. **Given** the same handoff run, **When** delivery state is inspected, **Then** the excluded record's delivery id has not advanced.
3. **Given** a mix of in-config and out-of-config verified records, **When** the handoff is generated, **Then** only the in-config records appear in the output and only they are marked delivered.
4. **Given** the configuration later restored to include the previously excluded component, **When** the handoff is regenerated, **Then** the record is included again (exclusion is not permanent).

---

### User Story 2 - Apply Output Is a Clean Count (Priority: P2)

The operator runs the apply script to merge a shipped diff into the cache. Today the apt update output leaks into the count capture, so every successful apply prints a shell integer-comparison error (`[: Ign:1 …: integer expression expected`), the "(already applied)" branch is unreachable, and the summary shows raw apt output instead of a file count. This phase keeps the apply script's count capture clean — apt/progress output is routed away from it — so a successful apply prints no shell error, the already-applied path works, the count line is exactly `New files: <number>`, and the already-applied state is reported on a separate line.

**Why this priority**: The operator's merge step is the primary incremental transport; a summary that is raw apt noise and a shell error on every run makes the shipped script look broken and defeats automation.

**Independent Test**: Run the generated apply script against a fixture diff — stderr is empty and the summary matches `New files: <number>`; re-running reports the already-applied path cleanly.

**Acceptance Scenarios**:

1. **Given** a diff to merge, **When** the operator runs the apply script, **Then** the summary is exactly `New files: <number>` with no raw apt output mixed in.
2. **Given** the same run, **When** the output is inspected, **Then** no shell error (e.g. integer-comparison) is emitted and stderr is clean.
3. **Given** an already-applied diff, **When** the operator runs the apply script again, **Then** the already-applied branch is reached and reported on a separate line (the count line stays exactly `New files: 0`), without an error.
4. **Given** a diff with zero files, **When** the apply script runs, **Then** it completes cleanly with a `0` count and no error.

---

### User Story 3 - Mistyped Config Values Fail Structurally (Priority: P2)

The administrator mistypes a configuration value — `base_dir = 123`, a non-table `archive`, `binary_cache = 5`, or `fetch.source = "yes"` — or mistypes a key name (`binry_cache`, `max_source_dep_th`). Today only the two depth settings are type-validated; every other mistyped key crashes with a raw traceback and exit 1, a string where a boolean is expected is silently accepted and echoed as a string, and an unknown key name is silently ignored. The established contract promises structured config errors (a specific exit code with a machine-readable payload) for configuration failures generally. This phase type-validates every configuration value, rejects unknown key names, and routes all config failures through that structured path, naming the offending key (and, for a mistyped value, the expected type) — never a raw traceback.

**Why this priority**: Raw tracebacks and silent acceptance of wrong types make the configuration surface hostile and unscriptable, contradicting the promised structured-error contract.

**Independent Test**: A parametrized set of mistyped values (wrong-type scalar, non-table table, wrong-type path, string as boolean) and unknown key names — each yields the structured config error with the key named and the JSON payload emitted; a `"yes"` string for a boolean setting is rejected.

**Acceptance Scenarios**:

1. **Given** any mistyped configuration value, **When** the configuration is loaded, **Then** a structured config error is raised that names the offending key and the expected type — no raw traceback.
2. **Given** the same failure in machine-readable mode, **When** the command output is inspected, **Then** the machine-readable error payload is emitted (per the config contract).
3. **Given** a boolean setting given a string like `"yes"`, **When** the configuration is loaded, **Then** it is rejected (only real boolean values are accepted).
4. **Given** correctly-typed configuration values, **When** the configuration is loaded, **Then** loading succeeds unchanged (no regression for valid config).
5. **Given** a configuration containing an unknown or mistyped key name, **When** the configuration is loaded, **Then** it is rejected with a structured config error naming the key (not silently ignored).

---

### User Story 4 - Local Overrides Always Apply and Validate (Priority: P2)

The administrator sets a depth override in the local project file while the defaults file lacks the `[resolution]` section (trimmed or profile drop-in defaults). Today the override is silently dropped because overrides apply only to keys that exist in defaults — so a valid override never takes effect, and a bad depth value in a local override is never validated. This phase applies local overrides for **known** keys regardless of whether the defaults file carries the section, then validates the merged result — a bad depth fails at load naming the key, and a valid override is applied and flagged as an override.

**Why this priority**: Silent dropping of overrides and skipped validation undermine the hand-written/profile drop-in workflow and the promise that a bad depth in either project file fails at load.

**Independent Test**: Defaults without the `[resolution]` section and a local `-2` depth — loading fails with a structured error naming the key; with a local `2`, the override is applied and flagged as an override.

**Acceptance Scenarios**:

1. **Given** defaults that lack a section and a local override for a known key in that section, **When** the configuration is loaded, **Then** the override is applied (not silently dropped).
2. **Given** the same setup with an invalid override value (e.g. a bad depth), **When** the configuration is loaded, **Then** loading fails with a structured error naming the key.
3. **Given** an applied local override, **When** the merged configuration is inspected, **Then** the override is flagged as such.
4. **Given** the archive-source replacement semantics, **When** the configuration is loaded, **Then** their existing replacement behavior is preserved (unchanged by this fix).

---

### User Story 5 - Read Commands Gate on Cache Initialization (Priority: P2)

The administrator runs `show`, `list`, or `verify` before the cache has been initialized. Today these commands open the database directly — creating a stray 0-byte database file when none exists — and crash with a raw traceback; the leftover file then makes properly gated commands misleadingly report the database as stale. This phase routes these three commands through the same existence+schema gate their siblings use: on an uninitialized cache they return a clean error with the agreed exit code and create no file; on a schema-mismatched cache they take the shared stale-schema path.

**Why this priority**: A raw traceback plus a poisoned leftover file is a poor first-run experience and corrupts later diagnostics — the read commands must behave exactly like their gated siblings.

**Independent Test**: Run `show`/`list`/`verify` with no database present — each exits with the agreed code, prints a clean message, and leaves no database file behind; a schema-mismatched database takes the shared stale-schema path.

**Acceptance Scenarios**:

1. **Given** an uninitialized cache (no database), **When** the administrator runs `show`, `list`, or `verify`, **Then** a clean error is returned with the agreed exit code and **no database file is created**.
2. **Given** a database whose schema does not match, **When** the administrator runs one of the three commands, **Then** the shared stale-schema error path is taken (same message/behavior as sibling commands).
3. **Given** a valid, initialized cache, **When** the administrator runs one of the three commands, **Then** behavior is unchanged (no regression).

---

### Edge Cases

- **Handoff exclusion (Phase 1)**: configuration narrowed vs broadened between runs; records in a removed component; a mix of in- and out-of-config records; re-running after the configuration is restored; interplay with delivery ids that were advanced before the exclusion.
- **Apply output (Phase 2)**: a fresh apply vs an already-applied diff; a diff with zero files; apt emitting warnings/progress during the run; an apt failure mid-apply; paths containing spaces.
- **Config validation (Phase 3)**: a scalar where a table is expected and vice versa; wrong-type path values; wrong-type numeric values; string vs boolean; nested/known keys in absent sections; unknown or mistyped key names rejected (no silent ignore); valid config remaining valid.
- **Local overrides (Phase 4)**: defaults lacking the section with valid and invalid local overrides; section present in defaults (regression guard); multiple overridden keys; archive-source replacement semantics.
- **Read-command gating (Phase 5)**: no database at all; schema-mismatched database; valid database; running after a prior gated command created/validated the database; a database present but empty.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When generating a handoff, a verified record whose `(release, component)` is not in the currently configured set MUST be excluded from the output and reported loudly (a visible exclusion notice, not silence).
- **FR-002**: An excluded record MUST NOT have its delivery id advanced, and MUST NOT be marked delivered by the handoff run.
- **FR-003**: The operator apply summary MUST contain only the count of applied files (e.g. `New files: <number>`); apt/progress output MUST NOT be mixed into the count capture or the summary.
- **FR-004**: A successful apply MUST NOT emit a shell error from the count comparison, and the already-applied branch MUST be reachable and reported cleanly on a separate line; the summary count line MUST read exactly `New files: <number>`.
- **FR-005**: Every configuration value MUST be type-validated (not only the depth settings) and every configuration key MUST be a recognized key; a mistyped value MUST produce a structured config error naming the offending key and the expected type — never a raw traceback. An unknown or mistyped key name MUST likewise be rejected with a structured config error naming the key (unknown keys are not silently ignored).
- **FR-006**: Boolean configuration settings MUST accept only real boolean values; string forms such as `"yes"` MUST be rejected.
- **FR-007**: All configuration failures MUST flow through the established structured error path (the specific config exit code plus a machine-readable payload).
- **FR-008**: A local-config override for a known key MUST apply even when the defaults file lacks the section containing that key; it MUST be flagged as an override when applied.
- **FR-009**: The merged configuration MUST be validated after local overrides are applied; a bad value in a local override MUST fail at load naming the key, even when defaults lack the section.
- **FR-010**: The `show`, `list`, and `verify` commands MUST gate on cache initialization and schema validity like their sibling commands: on an uninitialized cache they MUST return the agreed clean error and MUST NOT create a database file.
- **FR-011**: On a schema-mismatched cache, the `show`, `list`, and `verify` commands MUST take the same stale-schema error path as their sibling commands.

### Key Entities *(include if feature involves data)*

- **Handoff exclusion set**: the configured `(release, component)` membership check applied to handoff records; out-of-set records are omitted and reported.
- **Delivery record**: advanced only for records actually included in a handoff; out-of-config records never advance.
- **Operator apply summary**: the clean file-count output of the operator's merge script, free of apt/progress noise — count line exactly `New files: <number>`, with the already-applied state reported on a separate line.
- **Configuration type contract**: the expected type of every configuration key, validated at load; violations (mistyped values or unknown key names) become structured errors naming the key and expected type.
- **Local-override merge**: known-key overrides applied regardless of defaults section presence, then validated as part of the merged result.
- **Cache initialization gate**: the existence+schema check that the read commands share with their siblings; prevents stray database files on uninitialized caches.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After the configured components shrink, handoff omits and loudly reports every verified record outside them, and no delivery id for an excluded record advances.
- **SC-002**: Running the generated apply script against a fixture diff produces an empty stderr and a summary matching `New files: <number>`, with no shell error and a reachable already-applied branch.
- **SC-003**: Every mistyped configuration value (including a string where a boolean is expected) and every unknown key name yields a structured config error naming the key and expected type — the specific config exit code with a machine-readable payload — and never a raw traceback.
- **SC-004**: A local depth override applies and is validated even when defaults lack the section; an invalid value fails at load naming the key.
- **SC-005**: `show`/`list`/`verify` on an uninitialized cache return the agreed clean error without creating any database file, and a schema-mismatched cache takes the shared stale-schema path.

## Assumptions

- **Scope source**: The five entries F-18, F-19, F-20, F-21, F-22 in `review/04_alpha_summary/02_high_priority_followups.md` (the "Download / verify / operator track" handoff/apply items and the "Config / CLI / database" cluster) are the authoritative scope of this feature. Per the agreed split, 016 (Resolver & dependency correctness) and 017 (Download & cache integrity) are separate features; all resolver/downloader entries are out of scope here.
- **Contract-preserving**: Per the source doc's cross-cutting note, the exit-code/JSON contract table (`specs/014-base-profiles/contracts/cli.md`) is **extended**, not diverged from, for F-20/F-21/F-22; `tests/contract/test_cli.py` must stay green.
- **Handoff parity**: The handoff exclusion mirrors the existing diff-generation exclusion behavior (same membership check and reporting approach).
- **Schema freeze**: The spec 010/011 schema freeze holds — no database schema changes; F-22 is behavioral gating only.
- **Alpha sequencing**: This feature ships in the same alpha release, after 016 and 017; it is complete when all five phases are implemented and both documented test runners are green.
- **Regression-preserving**: Behavior not covered by one of the five entries is unchanged. Note: F-20 now also rejects unknown/mistyped key names — a deliberate, documented change from the previous silent-ignore behavior (confirmed in the Clarifications session), and the only intentional behavioral supersession of prior config-loading behavior in this feature.

## Clarifications

### Session

- Q: Unknown/mistyped configuration key names (F-20) → A: rejected as structured config errors (exit 102, JSON, naming the key); unknown keys are no longer silently ignored.
- Q: How is the already-applied state reported when the apply count line must be exactly `New files: <number>` (F-19) → A: on a separate line (e.g. `Already applied: all new files already present in the pool`); the count line stays exactly `New files: <number>`.
