# Implementation Plan: Operator Track & CLI/Config Robustness

**Branch**: `018-operator-cli-robustness` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/018-operator-cli-robustness/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Feature 018 completes the three-spec alpha split with the **operator-track** handoff/apply fixes and the
**Config / CLI / database** robustness cluster from `review/04_alpha_summary/02_high_priority_followups.md`
(F-18 … F-22), sequenced after 016 and 017:

- **F-18** — the handoff generator (`lib/service/handoff.py:generate_handoff`) has no `base_set` membership
  check (unlike `generate_diff`), so a verified record whose `(release, component)` is no longer configured is
  appended to the index and its id marked delivered, yet is never written anywhere. Phase 1 adds the same
  membership exclusion `generate_diff` already uses, reports the excluded records **loudly** (a new
  `HandoffStats.excluded_config` list + a summary Warning line), and refuses to advance their delivery ids.
- **F-19** — the operator `apply.sh` captures `apt update`'s stdout into `$new_files`
  (`apply_configure`/`run_apt_update`), so every successful apply prints a shell integer-comparison error and
  a raw-apt summary. Phase 2 routes apt output to a log file so `apply_configure`'s stdout is reserved for the
  count, the summary line is exactly `New files: <number>`, the already-applied branch is reachable, and the
  zero-files case reports `0` cleanly.
- **F-20** — only the two depth keys are type-validated in `config_loader.py`; every other mistyped value
  (`base_dir = 123`, a non-table `archive`, `binary_cache = 5`, `fetch.source = "yes"`) crashes with a raw
  traceback or is silently accepted, and an unknown key name is silently ignored. Phase 3 adds a declarative
  type map validated in the merged loop, routing every config failure through the existing `ConfigError` →
  exit 102 (+ JSON) path, naming the key and expected type; unknown key names are rejected (defaults only; the
  local overlay stays lenient); booleans accept only real TOML booleans.
- **F-21** — local overrides apply only when the key exists in defaults (`if tup in defaults_flat`), so a depth
  override is silently dropped when `project.defaults.toml` lacks `[resolution]`. Phase 4 adopts overrides for
  **known** keys regardless of defaults presence (`if tup in _KEY_MAP`) and validates the merged result, so a
  bad depth in a local override fails at load naming the key; `[[archive.source]]` replacement semantics are
  preserved.
- **F-22** — `show`, `list`, and `verify` open the database directly (`sqlite3.connect`), creating a stray
  0-byte file on an uninitialized cache and tracing back. Phase 5 routes all three through the shared
  `validate_schema_or_error` gate their siblings use: missing cache → clean JSON/text error + exit 101, no file
  created; schema-mismatched cache → the shared rebuild path.

All five phases are **behavioral with a hard SQLite schema freeze** — no tables, columns, or migrations. The
014 exit-code/JSON contract table is **extended** (never diverged) for F-20/F-21/F-22 per the review's
cross-cutting note; the 012 (handoff) and 013 (apply) contract docs are updated alongside for F-18/F-19
(docs inheritance). `tests/contract/test_cli.py` stays green throughout.

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default); codebase is the real `package_mgmt` project at
repo root (NOT the template tree). The operator `apply.sh` is a shipped bash script embedded in
`lib/service/handoff.py`.

**Database**: SQLite 3.x — frozen spec 010/011 schema (8 tables: `packages`, `binaries`, `sources`,
`binary_to_source`, `source_build_deps`, `provides`, `virtual_resolutions`, `systems`). No changes in this
feature; F-22 is behavioral gating only.

**Primary Dependencies**: stdlib only (`tomllib`, `sqlite3`, `os`, `pathlib`). No new runtime dependencies.
The operator scripts are pure bash.

**Storage**: `{base}/binary/` (pool + `dists/…` + `handoff/` + `diff/`), `{base}/source/`, `{base}/package_mgmt.db`,
`project.defaults.toml` / `project.local.toml`. Phase 2 adds a transient apt-update log file under the operator
cache during `apply.sh`.

**Testing**: pytest + `python -m unittest discover -s tests` (both runners mandatory, Constitution). Baseline
this cycle: **608 tests under both runners (608 OK / 608 collected)**, `tests/contract/test_cli.py` **60 passed
+ 11 subtests**. The operator scripts are unit-tested by sourcing the shipped template and calling plain bash
functions with scratch paths (spec 012/013 testability convention).

**Target Platform**: Debian Trixie LTS amd64 / x86_64.

**Project Type**: CLI tool for offline Debian LTS package management — admin builds cache on networked system,
operator consumes on offline system. F-18/F-20/F-21/F-22 are admin-side; F-19 is the operator's merge script.

**Performance Goals**: Config load is a single TOML parse + small dict walk (no measurable cost); the F-18
membership check is an O(1) set lookup per record; the F-22 gate is a pre-existing `os.path.exists` + schema
probe shared with siblings. No hot-path changes.

**Constraints**:
- **Schema freeze** (spec 010/011): no schema change, no migrations (spec Assumption).
- **Contract-preserving**: the 014 exit-code/JSON contract is **extended**, not diverged, for F-20/F-21/F-22;
  `tests/contract/test_cli.py` green. The 012/013 operator-script contracts are updated (not contradicted) for
  F-18/F-19 (docs inheritance).
- **No new network calls**: Phase 1/3/4/5 add none; Phase 2 merely redirects the existing `apt update` output.
- **Regression-preserving**: behavior outside the five entries is unchanged (unknown keys in the **local**
  overlay staying silently ignored, and archive-source replacement semantics). Unknown keys in the **defaults**
  file are rejected, not ignored — a clarification adopted during implementation (spec FR-005).
- **TDD**: tests written first, must fail before implementation; both runners green at every phase.

**Scale/Scope**: Cache with tens of thousands of packages; the F-18 exclusion can touch every verified record —
a set membership check, not a scan; the F-19 log file is bounded by a single apt run.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / Gate | Verdict | Basis |
|------------------|---------|-------|
| I. Offline-First Availability | ✅ PASS | No new network call sites; the F-19 fix only redirects the operator's existing `apt update` output. Offline runtime unchanged. |
| II. Dependency Alignment | ✅ PASS | No resolution/selection changes; F-18 filters the *handoff* view of already-verified records, never the dependency graph. |
| III. Binary-Source Consistency | ✅ PASS | No linkage changes. |
| IV. Minimal Local Cache | ✅ PASS | No caching behavior changes. |
| V. Debian Policy Compliance | ✅ PASS | No packaging/policy surface changes. |
| VI. Code Quality & Type Safety | ✅ PASS | The declarative type map is a concrete, typed structure; errors are the existing `ConfigError` type. No `dict[str, Any]` without a documented shape. |
| VII. Database-First Resolution | ✅ PASS | No schema change; F-22 is behavioral gating only (shared `validate_schema_or_error`). |
| VIII. Documentation & Infrastructure Traceability | ✅ PASS | Contracts/docs updated in this feature (012/013/014 + this spec's contract), per the review's "docs inheritance" note. |
| IX. Incremental Spec Scope | ✅ PASS | 5 user stories, 11 FRs — under the 10-story/20-FR decomposition limit. |
| Schema freeze (010/011) | ✅ PASS | Behavioral changes only; no `database.py` edits. |
| Both test runners green | ✅ PASS (gate) | Baseline 608 passed / 608 OK; held at every phase. |
| Contract suite green | ✅ PASS (gate) | `tests/contract/test_cli.py` (60 passed + 11 subtests) stays green; 014 table extended. |

**Gates: no violations. Complexity Tracking table not required.**

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] Commands are assigned to the correct persona (admin-only vs operator-only vs shared)
- [x] No persona-dependent behavior leaks across the offline/online boundary
- [x] Cache handoff expectations are reflected in the architecture
- [x] If this feature introduces new personas or changes existing roles, [roles.md](../memory/roles.md) has been updated accordingly

F-18/F-20/F-21/F-22 are **admin-side** (`handoff`, config loading, `show`/`list`/`verify`). F-19 is the
**operator's** shipped `apply.sh` — the fix is contained to the script's own functions; the script still never
parses project settings and runs on the bare offline system with no tool install (spec 013). The operator is
only *better served* (clean, parseable apply output). No new personas; `roles.md` unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/018-operator-cli-robustness/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/package_mgmt/
├── cli/
│   ├── main.py                          # entry point (unchanged)
│   └── commands/
│       ├── handoff.py                   # Phase 1: report excluded_config as a summary Warning (loud)
│       ├── show.py                      # Phase 5: validate_schema_or_error gate (no stray DB file)
│       ├── list.py                      # Phase 5: validate_schema_or_error gate
│       └── verify.py                    # Phase 5: validate_schema_or_error gate
└── lib/
    ├── model/
    │   └── config.py                    # ProjectConfig field types (unchanged; the source of truth for types)
    └── service/
        ├── handoff.py                   # Phase 1: base_set membership check + HandoffStats.excluded_config;
        │                                # Phase 2: APPLY_SCRIPT apt-output routing + clean summary + apt log
        └── config_loader.py             # Phase 3: _TYPE_MAP + merged-loop type validation, non-table archive
                                         #          guard; Phase 4: known-key override adoption (_KEY_MAP)
    └── util/
        └── paths.py                     # validate_absolute (unchanged; its ValueError wrapped into ConfigError
                                         #          in config_loader.py, Phase 3)
```

```text
tests/
├── contract/test_cli.py                 # must stay green (contract-preserving; + F-20/F-21 config-error and
│                                        #   F-22 show/list/verify gating assertions, 014 table extension)
├── integration/test_handoff_flow.py     # Phase 1 e2e: out-of-config record omitted + reported + not delivered
├── integration/test_diff_flow.py        # Phase 2 e2e: generated apply.sh clean summary (already-applied, zero)
├── unit/test_handoff.py                 # Phase 1: generate_handoff exclusion unit coverage
├── unit/test_apply_script.py            # Phase 2: apply.sh count capture, already-applied, zero-files, apt log
├── unit/test_config.py                  # Phases 3+4: type validation + absent-section overrides
├── unit/test_list.py / test_verify.py   # Phase 5: gating unit coverage (show is covered in contract tests)
└── fixtures/make_test_iso.py            # unchanged (reuses existing pool/handoff/diff fixtures)
```

**Structure Decision**: No new modules — each phase edits the existing function/command that owns the behavior
(matches the code-verified anchors in `review/04_alpha_summary/02_high_priority_followups.md`):
`lib/service/handoff.py`, `config_loader.py`, and the three CLI commands. The operator-script fix lives in the
shipped `APPLY_SCRIPT` template and is tested by sourcing it (existing convention).

## Phase Map (execution order for /speckit.tasks)

| Phase | US | Priority | Anchor (code) | Fix |
|-------|----|----------|---------------|-----|
| 1 | US1 (F-18) | P2 | `lib/service/handoff.py:generate_handoff` (no `base_set` check, unlike `generate_diff`) | Compute `base_set = set(bases)` and exclude any record whose `(release, component)` is not in it — loudly (new `HandoffStats.excluded_config`, summary Warning in `cli/commands/handoff.py`) — and never append those ids to `delivered_ids` (mirror `generate_diff`) |
| 2 | US2 (F-19) | P2 | `APPLY_SCRIPT` `apply_configure`/`run_apt_update` — `apt update` stdout leaks into `$new_files` | Route apt output to a log file so `apply_configure`'s stdout is only the count; summary line exactly `New files: <number>`; already-applied branch reachable, reported on a separate line (`Already applied: …`) so the count line stays exactly `New files: 0`; zero-files → `0`; apt-failure message names the log; 013 contract updated |
| 3 | US3 (F-20) | P2 | `config_loader.py` (only depth keys validated; non-table `archive` AttributeError; `fetch.source = "yes"` accepted) | Declarative `_TYPE_MAP` validated in the merged loop → `ConfigError("<key> must be <type>, got: …")`; unknown key names rejected (defaults only; the local overlay stays lenient); booleans accept only real TOML booleans; non-table `archive`/paths guarded; all config failures → exit 102 (+ JSON) |
| 4 | US4 (F-21) | P2 | `config_loader.py:175-181` (overrides apply only `if tup in defaults_flat`) | Adopt overrides for **known** keys regardless of defaults presence (`if tup in _KEY_MAP`); merged result validated (bad depth in local override fails naming the key); `[[archive.source]]` replacement semantics preserved |
| 5 | US5 (F-22) | P2 | `show.py:133`, `list.py:14`, `verify.py:18` direct `sqlite3.connect` | Route the three commands through the shared `validate_schema_or_error` gate: missing cache → clean error + 101, no file created; schema-mismatched → shared rebuild path |

**Dependencies**: Phases 3 and 4 both touch `config_loader.py` and are sequential (Phase 4's newly-adopted
overrides are validated by Phase 3's type map). Phases 1 and 2 touch different regions of
`lib/service/handoff.py` (`generate_handoff` vs the `APPLY_SCRIPT` template) and are independent; Phase 5 is
independent. The shared contract suite is extended once, after Phases 3–5, so the 014 table grows in one edit.

## Complexity Tracking

> Not required — Constitution Check has no violations.
