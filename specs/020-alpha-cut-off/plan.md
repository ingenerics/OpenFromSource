# Implementation Plan: Alpha Cut-off

**Branch**: `020-alpha-cut-off` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/020-alpha-cut-off/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Feature 020 is the **final pre-alpha release spec**, converting the four high-priority findings of
`review/05_alpha_final_kimi_k3/ALPHA_REVIEW.md` (N-1…N-4) into their triaged resolutions. The review closed all
six prior blockers (B-1…B-6) and the high-priority follow-ups (F-8…F-23, H-2, M-3); the four remaining findings
were triaged in session and split into **two code fixes** and **two accepted-as-documented-limitation
resolutions**:

- **Phase 1 — Constraint-Aware Alternative Probes (N-3, code, P1)**: the two "already satisfied" alternative-group
  probes are constraint-blind — the runtime probe (`lib/service/resolver.py:1059-1069`) and its build-dep twin
  (`resolver.py:693-703`) treat **any** `current` DB row as satisfying, even when its version fails the member's
  own constraint. With `a 1.0` pre-registered and a dependency `a (>= 2.0) | b`, the primary fails, the probe sees
  the `a 1.0` row, `b` is never tried, the error is retracted, `add` exits 0 — yet `verify` reports INCOMPLETE and
  `apt` cannot install the result. The sibling build-dep link block (`resolver.py:716-744`) already evaluates each
  candidate's version against its own constraint; the probes must mirror it. This is the last
  `add`/`verify` contract contradiction.
- **Phase 2 — Distributable Trust Anchor (N-4, code, P1)**: `pyproject.toml` uses
  `setuptools.build_meta:__legacy__` with no package-data declaration, so a built wheel/sdist contains only `.py`
  files — the archive-signing keyring (`src/package_mgmt/data/debian-archive-keyring.gpg`) is absent, and a
  standard (non-editable) install cannot verify any remote index (`lib/service/index_loader.py:22-24` resolves it
  at runtime). **Verified empirically in research**: adding `[tool.setuptools.package-data]` →
  `package_mgmt = ["data/*.gpg"]` makes the wheel carry `package_mgmt/data/debian-archive-keyring.gpg` under the
  legacy backend.
- **Phase 3 — Honest `init` Guidance (N-1, docs, P2)**: `init`'s job is network work (fetch indexes + build the
  `provides` snapshot); the air-gapped path (`ensure-base`) self-bootstraps (`lib/service/ensure_base.py:207,218`).
  Per triage, **no resolver change** — the docs state that `init` requires connectivity and that an offline/partial
  `init` (0 binaries cached) is not a usable cache; recovery is to start again from scratch.
- **Phase 4 — Graybeard Standalone (N-2, docs, P2)**: graybeard is pulled out of the grow-deeper chain — it is a
  stand-alone, fresh-cache-only profile (matching `ensure-base`'s existing fresh-only refusal,
  `lib/service/ensure_base.py:172-186`); `regular_user → developer` (including deepening developer depth) remains
  the documented supported direction. **No resolver change** — the cached-virtual source-repair gap (N-2) can then
  only arise in an unsupported flow.

All four resolutions have a hard **SQLite schema freeze** (no tables, columns, or migrations). No new runtime
dependencies. The two code phases are small, test-first, independently verifiable; the two doc phases are
artifact-only with a test asserting the profile-doc contract. Baseline this cycle: **pytest 707 passed / both
runners** (per `tests/REGRESSION.md`, post-019).

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default); the real `package_mgmt` project at repo root.
Operator scripts are shipped bash embedded in `lib/service/handoff.py` (untouched by this feature).

**Database**: SQLite 3.x — frozen spec 010/011 schema (8 tables: `packages`, `binaries`, `sources`,
`binary_to_source`, `source_build_deps`, `provides`, `virtual_resolutions`, `systems`). No schema change;
Phase 1 only adds Python-side constraint evaluation on existing rows.

**Primary Dependencies**: stdlib only (`sqlite3`, `os`, `urllib`). No new runtime dependencies. Phase 2 adds a
build-metadata block to `pyproject.toml` only (setuptools is already the declared backend).

**Storage**: `{base}/binary/` + `{base}/source/` + `{base}/package_mgmt.db` + `project.defaults.toml` /
`project.local.toml`. No new storage artifacts.

**Testing**: pytest + `python -m unittest discover -s tests` (both runners mandatory, Constitution). Baseline
707 passed / both runners. Phase 1 tests are TDD in `tests/unit/test_resolver.py` (existing `_binary`/`_source`/
`_config` helpers and `TestRuntimeAlternativeStatusFilter`/`TestBuildDepFallbackConstraint` classes). Phase 2 adds
a packaging-contract unit test (reads `pyproject.toml`, asserts the keyring is declared and present) plus a
manual/CI build-and-inspect step. Phase 4 extends `tests/unit/test_config.py`'s profile-file-data test to assert
the new grow-deeper wording.

**Target Platform**: Debian Trixie LTS amd64 / x86_64.

**Project Type**: CLI tool for offline Debian LTS package management — admin builds cache on networked system,
operator consumes on offline system. Phases 1-2 are admin-side code fixes; Phases 3-4 are admin-facing docs.

**Performance Goals**: Phase 1 adds at most a bounded per-member SELECT + Python constraint check inside the
already-per-member alternative probe — O(group size) per group, no hot-path change; the 10k-index/5s resolution
goal is untouched. Phases 2-4 add no runtime work.

**Constraints**:
- **Schema freeze** (spec 010/011): no schema change, no migrations (spec Assumption).
- **Triage scope**: N-3 and N-4 are implemented; N-1 and N-2 are documentation-only. The review's low findings
  (L-1…L-13), process notes (§3, §6), and §7 items 5-6 (cheap lows, physical-media proofs) are **out of scope**.
- **No new network calls**: Phase 1 adds none; Phase 2 changes build metadata only.
- **Behavioral preservation**: FR-003/FR-004 explicitly preserve the provides-join over-inclusive semantics for
  virtual members; existing green tests stay green (only the profile-doc wording test and the new tests change).
- **TDD**: Phase 1 tests written first, must fail pre-fix; both runners green at every phase.

**Scale/Scope**: Phase 1 is a per-group probe change (group size is small — Debian alternatives rarely exceed a
handful of members); Phase 2 is build metadata; Phases 3-4 are documentation files.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / Gate | Verdict | Basis |
|------------------|---------|-------|
| I. Offline-First Availability | ✅ PASS | No new network call sites; Phase 1 changes in-memory/DB probe logic only; Phase 2 makes a standard install **more** able to build an offline cache (keyring ships). |
| II. Dependency Alignment | ✅ PASS | Phase 1 makes alternative-group resolution **more** correct (FR-001/FR-002) — the resolved closure is the one `apt` would actually install. |
| III. Binary-Source Consistency | ✅ PASS | No linkage changes; N-2 accepted as a documented limitation (graybeard fresh-only), no code impact. |
| IV. Minimal Local Cache | ✅ PASS | No caching-behavior changes. |
| V. Debian Policy Compliance | ✅ PASS | No packaging/policy surface changes. |
| VI. Code Quality & Type Safety | ✅ PASS | Phase 1 uses concrete typed helpers and the existing `satisfies_constraint`; no `dict[str, Any]` shapes added. |
| VII. Database-First Resolution | ✅ PASS | No schema change; Phase 1 reads existing `binaries`/`provides`/`virtual_resolutions` rows. |
| VIII. Documentation & Infrastructure Traceability | ✅ PASS | Phases 3-4 update the live docs in the same cycle; the plan/spec record the 014 grow-deeper supersession; the supported install modes are documented in Phase 2 (FR-006). |
| IX. Incremental Spec Scope | ✅ PASS | 4 user stories, 8 FRs — under the 10-story/20-FR decomposition limit. |
| Schema freeze (010/011) | ✅ PASS | Behavioral/build/doc changes only; no `database.py` edits. |
| Both test runners green | ✅ PASS (gate) | Baseline 707/707 held at every phase; TDD. |
| Contract suite green | ✅ PASS (gate) | `tests/contract/test_cli.py` stays green; the new add/verify alternative-resolution behavior is asserted in unit + contract tests. |

**Gates: no violations. Complexity Tracking table not required.**

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] Commands are assigned to the correct persona (admin-only vs operator-only vs shared)
- [x] No persona-dependent behavior leaks across the offline/online boundary
- [x] Cache handoff expectations are reflected in the architecture
- [x] If this feature introduces new personas or changes existing roles, [roles.md](../memory/roles.md) has been updated accordingly

All four resolutions are **admin-side** (`add`/`verify` resolver behavior, packaging, `init` docs, profile docs).
The operator is only *better served*: a cache handed off is never "successfully resolved" while uninstallable
(N-3), and the tool ships a working keyring for real-archive fetches (N-4). No new personas; `roles.md` unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/020-alpha-cut-off/
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
└── lib/service/
    └── resolver.py                      # Phase 1: constraint-aware probes (N-3)
    └── index_loader.py                  # (unchanged — runtime keyring path is already correct for installed wheels)
```

```text
pyproject.toml                           # Phase 2: [tool.setuptools.package-data] → package_mgmt = ["data/*.gpg"] (N-4)
src/package_mgmt/data/debian-archive-keyring.gpg   # Phase 2: the shipped trust anchor (already committed; now declared)
```

```text
docs/
├── 001-quickstart-howto.md              # Phase 2: supported install modes; Phase 3: init connectivity guidance (N-1)
└── profiles/
    ├── graybeard.toml                   # Phase 4: fresh-cache-only / stand-alone, out of the grow-deeper chain (N-2)
    ├── regular_user.toml                # Phase 4: grow-deeper direction ends at developer (N-2)
    └── developer.toml                   # Phase 4: verify wording (already stops at developer)
```

```text
tests/
├── unit/
│   ├── test_resolver.py                 # Phase 1: constraint-aware runtime + build-dep probe tests (N-3, TDD)
│   ├── test_packaging.py                # Phase 2 (new): keyring declared as package data + present in tree (N-4)
│   └── test_config.py                   # Phase 4: profile-file data test asserts the new grow-deeper wording (N-2)
└── contract/test_cli.py                 # stays green; + alternative-group resolution assertions (add exit-0 → verify COMPLETE)
```

**Structure Decision**: No new modules — each phase edits the existing file that owns the behavior/artifact:
`resolver.py` for the probes, `pyproject.toml` for the packaging declaration, `docs/001` + `docs/profiles/*` for
the documentation. A single new small test module (`test_packaging.py`) hosts the packaging-contract test.

## Phase Map (execution order for /speckit.tasks)

| Phase | US | Priority | Anchor (code) | Fix |
|-------|----|----------|---------------|-----|
| 1 | US1 (N-3) | P1 | `resolver.py:1059-1069` (runtime probe), `resolver.py:693-703` (build-dep twin), `resolver.py:716-744` (constraint-aware link block pattern) | Make both "already satisfied" probes constraint-aware, mirroring the link block: for each group member, check `status='current'` **and** `satisfies_constraint(version, member_vc)`; the runtime probe keeps the provides-join (virtual-member) path over-inclusive as documented (FR-003); only a constraint-wise-satisfying member retracts the failed-primary error (FR-001/FR-002); build-dep twin mirrors the link block so a resolvable alternative is linked with no "Build tool not resolved" error (FR-004). Consider extracting the link block's satisfaction loop into a shared helper to prevent drift |
| 2 | US2 (N-4) | P1 | `pyproject.toml`; `src/package_mgmt/data/debian-archive-keyring.gpg` | Add `[tool.setuptools.package-data]` → `package_mgmt = ["data/*.gpg"]` (empirically verified: legacy backend honors it). Build wheel + sdist and inspect that the keyring is present at `package_mgmt/data/debian-archive-keyring.gpg`; add a `test_packaging.py` contract test (pyproject declares the glob; the file exists in the tree); document the supported install modes in `docs/001` (editable dev flow + standard install working from the artifact). No `importlib.resources` migration (installed wheels are normal files; `os.path.join` is correct) |
| 3 | US3 (N-1) | P2 | `docs/001-quickstart-howto.md` step 4 (init) | Document that `init` requires access to at least one configured mirror, that its job is fetching indexes + building the virtual-package snapshot, and that an `init` reporting "0 binaries cached" (offline mode) or a partial fetch is not a usable cache — recover by resolving connectivity and re-running `init` from a clean cache. **No change to `init` behavior** |
| 4 | US4 (N-2) | P2 | `docs/profiles/graybeard.toml:20-22`; `docs/profiles/regular_user.toml:16`; `docs/profiles/developer.toml:17` | graybeard.toml: replace the grow-deeper chain with a stand-alone, fresh-cache-only statement (consistent with `ensure-base`'s fresh-only refusal; keep the FR-014 retention note). regular_user.toml: grow-deeper direction ends at developer (drop graybeard). developer.toml: confirm/align wording (already stops at developer). Extend `test_config.py`'s profile-file-data test to assert the new wording. 014's historical grow-deeper wording is superseded by this feature, not retro-edited |

**Dependencies**:
- Phases 1 and 2 are independent (resolver probes vs packaging metadata) and can proceed in either order; both are
  P1 and land before the doc phases.
- Phases 3 and 4 are independent documentation edits; they share no files.
- Phase 2's wheel build-and-inspect is a pre-tag verification step (spec FR-006) — it must run before the alpha
  tag even though the pytest contract test is in-cycle.
- The contract suite (`test_cli.py`) is extended once with the alternative-resolution assertions (add exit-0 →
  verify COMPLETE on a version-skewed group).
- The `AGENTS.md` SPECKIT block is refreshed to point at this feature (agent-context, after_plan).

## Complexity Tracking

> Not required — Constitution Check has no violations.
