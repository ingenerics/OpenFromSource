# Implementation Plan: Polish Backlog (Merged Low Findings)

**Branch**: `019-polish-backlog` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/019-polish-backlog/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Feature 019 is the **final clean-up** of the alpha cycle: the merged low findings `P-1…P-23` from
`review/04_alpha_summary/03_polish_backlog_docs.md` §1 (P-8 already fixed by 018's F-20 → regression-check only).
An audit of the current tree confirmed **22 of 23 are still present**. The work splits into seven phases that map
1:1 to the spec's user stories, each a small, independently verifiable correction:

- **Phase 1 — Contract & Side-Effect Honesty (P-1, P-4, P-5)**: `add` maps a stale-schema `DatabaseError` to exit 1
  (`cli/commands/add.py:83-88`) instead of the contract's 101; `init` maps **every** `InitError` to exit 100
  (`cli/commands/init.py:109-112`) even for ENOSPC, and a mirror-unreachable `init` prints a bare "initialized" with
  0 binaries; `handle_ensure_base` calls `initialize_database` **before** the failure-prone work
  (`cli/commands/ensure_base.py:64-68`), creating the DB on a failed run (spec 008 US1 AS2).
- **Phase 2 — Flag & Argument Consistency (P-2, P-3, P-7)**: `verify --graph` is decorative (`main.py:120-123`
  registers it, `main.py:208-215` ignores it) and `verify --package X` skips the depth clause
  (`verify.py:101-105`) — per the spec/Clarifications decision, the **`--graph` flag is removed** and bare
  `verify` is the single graph-verification command; `systems add ""`/newline names are accepted
  (`cli/commands/systems.py:26-52`) and the `IntegrityError` path leaks the connection (43-45); `add "vim="`
  (empty pin) is silently unpinned in the positional path (`add.py:16-20`/`_parse_pkg_spec`) while `--file`
  rejects it (`add.py:43-46`), and passing `--deps X --status all` together is silently resolved (status wins) with no documented rule (`main.py:199-203`).
- **Phase 3 — Honest Sync & Verify Reports (P-6, P-18)**: zero-pending `sync` early-returns without regenerating
  (`cli/commands/sync.py:56-61`), leaving a stale non-empty `diff/` (spec 013 AS2); `verify`'s "Unresolved build
  deps"/"Missing build tools" lists are never depth-filtered (`verify.py:35,254-322` — FR-017).
- **Phase 4 — Deterministic, Spec-Compliant Resolution (P-9, P-10, P-11, P-12, P-16)**: `_best_entry`
  (`lib/model/repository.py:85-88`) and the two `lookup_source*` sorts use `reverse=True` on `(version,
  component_order)`, so `non-free` beats `main` on exact-version ties (spec 006 FR-005 inverted);
  `_find_current_binary` (`resolver.py:103-108`) is `LIMIT 1` without `ORDER BY`; the build-dep fallback links any
  current binary by name without re-checking the version constraint and leaves a residual error
  (`resolver.py:706-722`); the runtime-alternative "already satisfied" probe lacks `status='current'`
  (`resolver.py:1023-1031`, the build-dep twin at 684-694 has it); the cached-virtual same-release path
  (`resolver.py:912-915`) never re-compares refreshed-index candidates (FR-005 partial).
- **Phase 5 — Accurate Database Lifecycle (P-13, P-14, P-15, P-17)**: `populate_provides_table`
  (`lib/service/initializer.py:65-84`) commits inside the caller's transaction (breaking `ensure_base`'s
  single-transaction claim at `lib/service/ensure_base.py:200-205`) and counts `INSERT OR IGNORE` skips as
  inserted; `provides` rows from no-longer-configured releases/indexes are never swept (no `DELETE FROM` anywhere
  in `src/`); the circular report counts superseded rows (`verify.py:195-212`) and the `circular` flag is never
  reset on upsert (`resolver.py:111-130`); `packages.status` never transitions and `upsert_package` stores the
  **resolved** version as `requested_version` for every transitive dep (`resolver.py:956`).
- **Phase 6 — Path & Download Robustness (P-19, P-20, P-21)**: `validate_absolute` (`lib/util/paths.py:8-12`)
  runs `abspath` first so its `ValueError` is dead code — a relative `base_dir` is silently reinterpreted
  relative to CWD; `_backoff_delay` honors `Retry-After` uncapped (`downloader.py:204-211`, internal cap 30 s);
  `write_sources_entry` emits a whitespace-delimited `deb` line with an unquoted path
  (`lib/service/handoff.py:151`) that breaks on paths with spaces.
- **Phase 7 — Doc/Test Hygiene (P-22, P-23)**: the downloader `_stat_size_matches` docstring + test name claim
  the opposite of the conservative re-hash behavior (`downloader.py:341-344`,
  `tests/unit/test_downloader.py:1115`); the mirror-normalization tests live inside `TestProfileFilesAreData`
  (`tests/unit/test_config.py:580-628`).

All seven phases are **behavioral with a hard SQLite schema freeze** (no tables, columns, or migrations — the
status lifecycle and circular reset use existing columns). The 014 exit-code/JSON contract table is **extended**
(never diverged); `verify --graph` is **removed** and doc/contract references to it (014 contract, 018
quickstart, CLI help) are updated in the same cycle (Constitution VIII). `tests/contract/test_cli.py` stays green
throughout. Baseline this cycle: **pytest 637 collected / unittest 637 OK / contract 68 passed + 15 subtests**.

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default); codebase is the real `package_mgmt` project at
repo root (NOT the template tree). Operator scripts (`apply.sh`, `bootstrap.sh`) are shipped bash embedded in
`lib/service/handoff.py`.

**Database**: SQLite 3.x — frozen spec 010/011 schema (8 tables: `packages`, `binaries`, `sources`,
`binary_to_source`, `source_build_deps`, `provides`, `virtual_resolutions`, `systems`). No schema changes in this
feature; P-13 removes a nested commit (transaction ownership), P-15/P-17 use existing columns
(`binaries.circular`, `packages.status`, `packages.requested_version`).

**Primary Dependencies**: stdlib only (`sqlite3`, `os`, `pathlib`, `urllib`). No new runtime dependencies.

**Storage**: `{base}/binary/` (pool + `dists/…` + `handoff/` + `diff/`), `{base}/source/`, `{base}/package_mgmt.db`,
`project.defaults.toml` / `project.local.toml`. No new storage artifacts.

**Testing**: pytest + `python -m unittest discover -s tests` (both runners mandatory, Constitution). Baseline
this cycle: **637 collected / 637 OK** under both runners, `tests/contract/test_cli.py` **68 passed + 15
subtests**. Operator scripts are unit-tested by sourcing the shipped template and calling plain bash functions
with scratch paths (spec 012/013 testability convention).

**Target Platform**: Debian Trixie LTS amd64 / x86_64.

**Project Type**: CLI tool for offline Debian LTS package management — admin builds cache on networked system,
operator consumes on offline system. All fixes except P-21 (operator's handoff sources entry) are admin-side;
the operator is only *better served* (working `deb` entry or clear diagnostic on space-containing paths).

**Performance Goals**: Every fix is O(1) or a bounded scan — a `base_set` membership check, an `ORDER BY`
clause, a capped retry delay, a swept-provides delete. No hot-path changes; the 10k-index/5s resolution goal is
untouched.

**Constraints**:
- **Schema freeze** (spec 010/011): no schema change, no migrations (spec Assumption). P-13/P-15/P-17 stay
  within existing columns.
- **Contract-preserving**: the 014 exit-code/JSON contract is **extended**, not diverged; `tests/contract/test_cli.py`
  green. `verify --graph` removal updates all doc/contract references (014 contract table, 018 quickstart,
  CLI `--help`) in this cycle.
- **No new network calls**: Phases 1-5/7 add none; Phase 6 only caps an existing retry delay and adds a path
  guard.
- **Regression-preserving**: behavior outside the 22 items is unchanged (e.g. the handoff exclusion of 018, the
  F-22 read-command gating, the F-20 config type map). P-23's test refactor must not change coverage.
- **TDD**: tests written first, must fail before implementation; both runners green at every phase.

**Scale/Scope**: Cache with tens of thousands of packages; the provides sweep (P-14) and status transition
(P-17) are per-row updates on existing tables, bounded by the record count of the refresh/registration run.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / Gate | Verdict | Basis |
|------------------|---------|-------|
| I. Offline-First Availability | ✅ PASS | No new network call sites; P-20 only caps an existing retry delay; P-21 guards an operator file. Offline runtime unchanged. |
| II. Dependency Alignment | ✅ PASS | P-9/P-10/P-11/P-12/P-16 make selection/linking **more** spec-compliant (006 FR-005) — the resolved closure is unchanged, only the tie-break/link correctness. |
| III. Binary-Source Consistency | ✅ PASS | No linkage changes; P-15's circular fix is reporting/flag lifecycle only. |
| IV. Minimal Local Cache | ✅ PASS | No caching-behavior changes. |
| V. Debian Policy Compliance | ✅ PASS | No packaging/policy surface changes. |
| VI. Code Quality & Type Safety | ✅ PASS | All fixes are concrete typed code; the `InitError.permission` flag and provides-sweep delete are explicit. No `dict[str, Any]` shapes added. |
| VII. Database-First Resolution | ✅ PASS | No schema change; P-13 fixes the nested-commit violation of `ensure_base`'s single-transaction claim; P-17 makes `packages.status`/`requested_version` truthful. |
| VIII. Documentation & Infrastructure Traceability | ✅ PASS | Contracts/docs updated in this feature (014 contract table, 018 quickstart, this spec's contract) per Constitution VIII; P-22 fixes a backwards docstring. |
| IX. Incremental Spec Scope | ✅ PASS | 7 user stories, 20 FRs — under the 10-story/20-FR decomposition limit. |
| Schema freeze (010/011) | ✅ PASS | Behavioral changes only; no `database.py` edits. |
| Both test runners green | ✅ PASS (gate) | Baseline 637/637 held at every phase; TDD. |
| Contract suite green | ✅ PASS (gate) | `tests/contract/test_cli.py` (68 passed + 15 subtests) stays green; 014 table extended; `verify --graph` references updated. |

**Gates: no violations. Complexity Tracking table not required.**

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] Commands are assigned to the correct persona (admin-only vs operator-only vs shared)
- [x] No persona-dependent behavior leaks across the offline/online boundary
- [x] Cache handoff expectations are reflected in the architecture
- [x] If this feature introduces new personas or changes existing roles, [roles.md](../memory/roles.md) has been updated accordingly

Phases 1-5 and 7 are **admin-side** (`add`, `init`, `ensure-base`, `systems`, `list`, `sync`, `verify`,
resolver, initializer, downloader doc/tests). Phase 6 mixes admin (`config` path validation, downloader retry)
and operator (the handoff `deb` entry P-21) — the operator is only *better served*: a working entry or a clear
diagnostic, no tool install, no parsing of project settings. `verify` stays admin-only in practice (the tool
ships in the repo, not the handoff). No new personas; `roles.md` unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/019-polish-backlog/
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
│   ├── main.py                          # Phase 2: drop --graph registration; verify dispatch unchanged
│   └── commands/
│       ├── add.py                       # Phase 1: DatabaseError → 101; Phase 2: empty-pin rejection in positional path
│       ├── init.py                      # Phase 1: InitError.permission → 100 vs 1; offline-mirror summary note
│       ├── ensure_base.py               # Phase 1: defer initialize_database until after failure-prone work
│       ├── systems.py                   # Phase 2: moniker validation ("any word"); close conn on IntegrityError
│       ├── sync.py                      # Phase 3: zero-pending → regenerate empty diff
│       └── verify.py                    # Phase 2: depth clause on --package branch; Phase 3: build-dep report depth; Phase 5: circular counts current only
└── lib/
    ├── model/
    │   └── repository.py                # Phase 4: component tie-break → main wins (reverse sort fixed)
    └── service/
        ├── initializer.py               # Phase 5: provides no nested commit + real insert count; Phase 5: sweep stale provides
        ├── ensure_base.py               # Phase 1: own DB-creation ordering; Phase 5: (consumes fixed populate_provides_table)
        ├── resolver.py                  # Phase 4: build-dep fallback constraint, runtime-alternative status filter, same-release virtual refresh, _find_current_binary ORDER BY; Phase 5: circular reset, status/requested_version truth
        ├── downloader.py                # Phase 6: Retry-After cap; Phase 7: docstring corrected
        ├── config_loader.py             # (unchanged — P-8 already fixed by 018; P-19 fix is in util/paths.py)
        └── handoff.py                   # Phase 6: write_sources_entry space guard/diagnostic
    └── util/
        └── paths.py                     # Phase 6: validate_absolute checks absoluteness before abspath
```

```text
tests/
├── contract/test_cli.py                 # stays green; + add→101, empty-pin, verify --graph removal, systems moniker, sync empty diff, config base_dir errors
├── integration/
│   ├── test_sync_flow.py                # Phase 3 e2e: zero-pending sync leaves empty diff
│   └── test_ensure_base_flow.py         # Phase 1 e2e: failed ensure-base creates no DB
├── unit/
│   ├── test_init.py                     # Phase 1: InitError exit-code split + offline-mirror summary
│   ├── test_add.py                      # Phase 1: stale-schema → 101; Phase 2: empty-pin parity
│   ├── test_systems.py                  # Phase 2: moniker validation + connection cleanup
│   ├── test_list.py                     # Phase 2: --deps/--status mutual exclusion
│   ├── test_sync.py                     # Phase 3: empty-diff regeneration
│   ├── test_verify.py                   # Phase 2/3/5: --package depth, build-dep report depth, circular current-only
│   ├── test_repository.py               # Phase 4: component tie-break
│   ├── test_resolver.py                 # Phase 4/5: fallback constraint, status filter, virtual refresh, circular reset, status/requested_version
│   ├── test_initializer.py              # Phase 5: provides no nested commit, real count, stale sweep
│   ├── test_paths.py                    # Phase 6: relative base_dir rejected
│   ├── test_downloader.py               # Phase 6: Retry-After cap; Phase 7: renamed size-match test
│   ├── test_handoff.py                  # Phase 6: space-containing path → diagnostic
│   └── test_config.py                   # Phase 7: TestMirrorNormalization class header
└── fixtures/                            # existing scratch-cache / index / pool builders reused
```

**Structure Decision**: No new modules — each phase edits the existing function/command that owns the behavior,
matching the code-verified anchors in `review/04_alpha_summary/03_polish_backlog_docs.md` §1. The `verify --graph`
removal touches only `main.py` argument registration + help; the operator `deb`-entry guard lives in the shipped
`write_sources_entry` template and is tested by sourcing it (existing convention).

## Phase Map (execution order for /speckit.tasks)

| Phase | US | Priority | Anchor (code) | Fix |
|-------|----|----------|---------------|-----|
| 1 | US1 (P-1, P-4, P-5) | P1 | `add.py:83-88`; `init.py:109-112,135-148`; `ensure_base.py:64-68` + `lib/service/ensure_base.py:188-208` | P-1: catch `DatabaseError` in `add` → 101. P-4: `InitError` gains a `permission` flag (`initializer.py` sets it for `PermissionError`); `init` maps permission→100, other InitError→1; track reachable binaries and print `Status: initialized (no mirror reachable — offline mode)` when 0. P-5: defer DB creation — `ensure_base` does discovery (image, indexes) before `initialize_database` + connect; CLI drops the up-front call; DatabaseError → 101 preserved |
| 2 | US2 (P-2, P-3, P-7) | P2 | `main.py:120-123,199-203`; `verify.py:101-105`; `systems.py:26-52`; `add.py:16-20,43-47` | P-2a: remove the `--graph` flag (registration + help); bare `verify` is the single graph-verification command; update 014/018 contract refs. P-2b: add the depth clause to the `if package:` branch of `_find_unresolved_deps`. P-3: `systems add` rejects empty/whitespace/newline names with a clean error; wrap the whole handler so every path closes the connection (try/finally). P-7: `_parse_pkg_spec("name=")` → malformed (reject in the positional path identically to `--file`); reject `--deps X --status all` together (mutually exclusive — clean usage error, exit 1, in `main.py` + contract) |
| 3 | US3 (P-6, P-18) | P2 | `sync.py:56-61`; `verify.py:35,254-322` | P-6: on zero pending, still run the diff regeneration path (or an explicit empty-diff writer) so `diff/` is emptied/regenerated, not left stale. P-18: pass `max_report_depth` into `_find_build_dep_issues` and filter both lists by `min_depth` like `_find_unresolved_deps` |
| 4 | US4 (P-9, P-10, P-11, P-12, P-16) | P1 | `repository.py:57-58,65-66,85-88`; `resolver.py:103-108,706-722,1023-1031,912-915` | P-9/P-16: sort by `(version, -component_order)` so `main` wins on exact-version ties; `_find_current_binary` adds `ORDER BY id` (deterministic). P-10: build-dep fallback re-checks `satisfies_constraint` (or exact-`=` constraint) before linking and retracts the residual error on a valid link. P-11: add `AND status = 'current'` to the runtime-alternative probes. P-12: in the cached-virtual same-release branch, re-run `select_provider` against refreshed candidates and re-resolve when the provider upgraded |
| 5 | US5 (P-13, P-14, P-15, P-17) | P2 | `initializer.py:65-84`; `resolver.py:111-130,956,1006-1009`; `verify.py:195-212` | P-13: drop the internal `commit()` from `populate_provides_table` (caller owns the transaction — `init.py` adds an explicit commit); count via `cursor.rowcount` so skips are not counted. P-14: in the refresh path, `DELETE FROM provides WHERE (release, component) NOT IN (configured set)` before re-populating. P-15: `upsert_binary` ON CONFLICT resets `circular = 0`; `_find_circular` adds `AND status = 'current'`. P-17: `upsert_package` gets a `status='current'` transition when a current binary is registered; `_resolve_one_inner` passes `requested_version` (None for transitive) instead of `requested_version or binary_entry.version` |
| 6 | US6 (P-19, P-20, P-21) | P2 | `paths.py:8-12`; `downloader.py:204-211`; `handoff.py:141-156` | P-19: `validate_absolute` checks `os.path.isabs(path)` (post-expanduser, pre-abspath) so a relative path raises the existing `ValueError` (wrapped to `ConfigError` by `config_loader.py:246-247`). P-20: `_backoff_delay` caps the `Retry-After` override at `BACKOFF_CAP`. P-21: `write_sources_entry` (or its generator) detects a space in `$handoff_dir` and emits a clear, actionable diagnostic instead of a silently-broken `deb` line |
| 7 | US7 (P-22, P-23) | P3 | `downloader.py:341-344`; `tests/unit/test_downloader.py:1115`; `tests/unit/test_config.py:580-628` | P-22: correct the `_stat_size_matches` docstring and the size-match test name to the actual conservative re-hash behavior. P-23: move the mirror-normalization tests into a dedicated `TestMirrorNormalization` class |

**Dependencies**:
- Phases 4 and 5 both touch `resolver.py` and `verify.py` (P-15's circular flag lives in resolver + verify;
  P-11's status filter is resolver) — they are sequenced 4 → 5, but touch disjoint functions and could share a
  resolver fixture pack (mirroring the 016 convention).
- Phase 1 and Phase 5 both touch `lib/service/ensure_base.py`/`initializer.py` (P-5 defers DB creation; P-13
  changes provides-transaction ownership) — Phase 5's provides fix must account for Phase 1's reordered DB
  creation; sequence 1 → 5.
- Phase 2's `verify --graph` removal must land with the contract-suite update (014/018 refs) in the same phase.
- Phase 7's P-22 test rename is tied to Phase 6's downloader changes only by file adjacency; independent.
- The contract suite (`test_cli.py`) is extended once per phase as assertions land.

## Complexity Tracking

> Not required — Constitution Check has no violations.
