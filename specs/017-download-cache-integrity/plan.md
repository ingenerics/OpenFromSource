# Implementation Plan: Download & Cache Integrity

**Branch**: `017-download-cache-integrity` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/017-download-cache-integrity/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Feature 017 hardens the integrity and economy of the local payload cache: payload downloads follow bounded
redirects instead of failing as bogus checksum mismatches (F-16), verified source payloads are re-checked per
run like binaries (F-17), present-and-valid files are never re-downloaded even when unverified (M-7), Sources
indexes are fetched only when source fetching is enabled (M-4), and index files are written atomically with
self-healing against a corrupt cached index (F-23).

The two legacy **ensure-base** items (H-2 re-run resets `verified`; M-3 duplicate `current` rows) are **resolved
by constraint, not by new registration logic**: `ensure-base` now refuses to run on a non-empty cache
(`CacheNotEmptyError`, exit 1), because the intended (graybeard) workflow ingests the installer image into a
**fresh** cache — `init` → `ensure-base` → `verify` → `handoff`. With no pre-existing records there is nothing
to reset and nothing to supersede, so both failure modes cannot occur. This removes the preserve-verified
flag and supersede-aware registration from scope entirely.

All six phases are **behavioral / registration-semantics changes with a hard SQLite schema freeze** — no
tables, columns, or migrations. The `verified` flag's semantics are refined (un-marked on re-check failure) but
its meaning ("confirmed retrievable from the configured archives") is unchanged. Existing exit-code/JSON
contracts (spec 014 `contracts/cli.md`) continue to hold; `tests/contract/test_cli.py` stays green.

## Technical Context

**Language/Version**: Python 3.13.x (Debian Trixie LTS default); codebase is the real `package_mgmt` project at
repo root (NOT the template tree).

**Database**: SQLite 3.x — frozen spec 010/011 schema (8 tables: `packages`, `binaries`, `sources`,
`binary_to_source`, `source_build_deps`, `provides`, `virtual_resolutions`, `systems`). No changes in this feature.

**Primary Dependencies**: stdlib (`urllib`, `http.client`, `sqlite3`, `hashlib`, `gzip`). No new runtime
dependencies (the earlier supersede-aware selection would have needed `python-debian`; it is no longer in scope).

**Storage**: `{base}/binary/` (pool `.deb` + `dists/…/Packages.gz`), `{base}/source/` (pool source artifacts +
`dists/…/Sources.gz`), `{base}/package_mgmt.db`. Payloads land via `*.part` → verify → `os.replace` (existing
discipline, spec 011); indexes must adopt the same discipline (Phase 6).

**Testing**: pytest + `python -m unittest discover -s tests` (both runners mandatory, Constitution);
`tests/fixtures/make_test_iso.py` (pool/HTTP fixture servers: `serve_pool`, `serve_rate_limited`; Phase 2 adds a
redirecting handler); `tests/contract/test_cli.py` must stay green.

**Target Platform**: Debian Trixie LTS amd64 / x86_64.

**Project Type**: CLI tool for offline Debian LTS package management — admin builds cache on networked system,
operator consumes on offline system. All six stories are admin-side.

**Performance Goals**: The Phase 3/4 re-check + short-circuit must remain O(files present); a present-and-valid
file is skipped via `stat` size before any re-hash where possible (existing `_stat_size_matches` pattern).

**Constraints**:
- **Schema freeze** (spec 010/011): no schema change, no migrations (spec Assumption).
- **No new network calls**: redirect-following re-issues an existing request; it adds no new call sites.
- **Contract-preserving**: exit-code/JSON contracts hold; `tests/contract/test_cli.py` green.
- **Fresh-cache only (ensure-base)**: `ensure-base` refuses a non-empty cache with a clear error (H-2/M-3
  resolved by constraint); the resolver's add-time reset-on-supersede semantics are untouched.
- **Regression-preserving**: behavior outside the six entries and the ensure-base guard is unchanged.
- **TDD**: tests written first, must fail before implementation; both runners green at every phase.

**Scale/Scope**: Cache with tens of thousands of packages; base-set registration can touch thousands of binary
rows per run — the fresh-cache guard is a single `SELECT 1 … LIMIT 1` check before any registration.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / Gate | Verdict | Basis |
|------------------|---------|-------|
| I. Offline-First Availability | ✅ PASS | No new network call sites; redirect-following and index re-fetch stay within the admin's download path. Normal (offline) operation is untouched. |
| II. Dependency Alignment | ✅ PASS | No resolution/selection changes; the fresh-cache guard cannot create a second current row because the image always registers into an empty cache. |
| III. Binary-Source Consistency | ✅ PASS (strengthened) | F-17 re-checks verified source payloads each run, closing a silent hole in the deterministic-rebuild promise. |
| IV. Minimal Local Cache | ✅ PASS (strengthened) | M-7 short-circuits re-downloads of present-and-valid payloads; F-17 re-fetches only missing/corrupt source files. |
| V. Debian Policy Compliance | ✅ PASS | No packaging/policy surface changes. |
| VI. Code Quality & Type Safety | ✅ PASS | Follows existing frozen-dataclass/TypedDict discipline; no `dict[str, Any]` without a documented shape. |
| VII. Database-First Resolution | ✅ PASS | Records stay pre-populated; `verified` semantics refined behaviorally (un-mark on re-check failure) — no schema/column change. |
| VIII. Documentation & Infrastructure Traceability | ✅ PASS | Docs updated in this feature (plan/data-model/contracts/quickstart + `docs/003-ensure-base.md`), per the review's "docs inheritance" note. |
| IX. Incremental Spec Scope | ✅ PASS | 6 user stories, 9 FRs — under the 10-story/20-FR decomposition limit. |
| Schema freeze (010/011) | ✅ PASS | Behavioral changes only; no `database.py` edits. |
| Both test runners green | ✅ PASS (gate) | Baseline 580 passed + 41 subtests / 580 OK; held at every phase. |
| Contract suite green | ✅ PASS (gate) | `tests/contract/test_cli.py` (58 passed + 11 subtests) stays green. |

**Gates: no violations. Complexity Tracking table not required.**

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] Commands are assigned to the correct persona (admin-only vs operator-only vs shared)
- [x] No persona-dependent behavior leaks across the offline/online boundary
- [x] Cache handoff expectations are reflected in the architecture
- [x] If this feature introduces new personas or changes existing roles, [roles.md](../memory/roles.md) has been updated accordingly

All six stories are **admin-side** (`ensure-base`, `download`, `add`'s index fetching). The operator is only
*indirectly* protected — a complete, un-poisoned, correctly-verified cache is what makes offline install
possible. The fresh-cache guard matches the graybeard persona's natural flow (image ingested once into an empty
cache). No new personas; `roles.md` unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/017-download-cache-integrity/
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
│       ├── download.py                  # handle_download (unchanged; phases report through engines)
│       ├── ensure_base.py → lib/service/ensure_base.py   # cli wrapper (guard error handling)
│       ├── init.py                      # existing fetch_source gating — Phase 5 parity reference
│       └── add.py                       # unchanged (uses resolver.ensure_indexes)
└── lib/
    ├── model/
    │   ├── config.py                    # ArchiveSource; config.fetch_source (unchanged)
    │   └── repository.py                # index lookups (unchanged)
    ├── service/
    │   ├── downloader.py                # Phases 2/3/4: redirects, source re-check, short-circuit
    │   ├── ensure_base.py               # Phase 1: fresh-cache guard (CacheNotEmptyError) instead of registration rework
    │   ├── resolver.py                  # Phases 5/6: index gating, self-heal (unchanged upsert_binary)
    │   ├── index_loader.py              # Phase 6: atomic index writes (fetch_index)
    │   └── db_init.py / config_loader.py  # unchanged
    └── util/
        └── paths.py                     # index_path_xz, resolve_index_path (unchanged)
```

```text
tests/
├── contract/test_cli.py                 # must stay green (contract-preserving; + ensure-base guard JSON error)
├── integration/test_ensure_base_flow.py # Phase 1 guard e2e fixtures (interrupted-run refusal, CLI refusal)
├── integration/test_download_flow.py    # redirect/source-recheck/self-heal e2e fixtures
├── unit/test_downloader.py              # Phases 2/3/4 unit coverage
├── unit/test_ensure_base.py             # Phase 1 guard unit coverage
├── unit/test_resolver.py                # Phases 5/6 unit coverage (ensure_indexes gating/self-heal)
└── fixtures/make_test_iso.py            # + redirect/truncation HTTP handlers (Phase 2/6 fixtures)
```

**Structure Decision**: No new modules — each phase edits the existing service layer function that owns the
behavior (matches the code-verified anchors in `review/04_alpha_summary/02_high_priority_followups.md`):
`downloader.py`, `ensure_base.py`, `resolver.py`, `index_loader.py`. Test fixtures extend
`make_test_iso.py`'s existing HTTP-handler pattern.

## Phase Map (execution order for /speckit.tasks)

| Phase | US | Priority | Anchor (code) | Fix |
|-------|----|----------|---------------|-----|
| 1 | US1 (H-2/M-3 guard) | P1 | `ensure_base.py:ensure_base` + `cli/commands/ensure_base.py` | Refuse a non-empty cache: `_assert_fresh_cache` (`SELECT 1 FROM binaries LIMIT 1`) raises `CacheNotEmptyError` (exit 1, clear message) before any registration — resolves H-2/M-3 by constraint (fresh-cache-only), no `upsert_binary` change |
| 2 | US2 (F-16) | P2 | `downloader._PooledOpener.__call__` raises only for `status >= 400`; 3xx body hashed | Follow 301/302/303/307/308 (cap 5 hops, loop/missing-Location → clear permanent error) before the `>= 400` raise |
| 3 | US3 (F-17) | P2 | `download_pending_sources` selects only `verified = 0`; no re-check | Mirror the binary re-check: per-run existence → size → checksum for verified sources; un-mark + re-fetch missing/corrupt files only |
| 4 | US4 (M-7) | P2 | binary/source phases short-circuit only `verified = 1` records | Apply exists+size+sha256 short-circuit to unverified records too (no re-download; flag unchanged) |
| 5 | US5 (M-4) | P2 | `resolver.ensure_indexes` fetches/loads Sources indexes unconditionally | Gate source index fetch/load on `config.fetch_source` (match `init`); per-run live-config check + cache-miss fetch, so switching `regular_user` → `developer`/`graybeard` still fetches the never-downloaded Sources indexes |
| 6 | US6 (F-23) | P3 | `index_loader.fetch_index` non-atomic writes; `ensure_indexes` prefers a corrupt cached index | Atomic index writes (`*.part` → `os.replace`); failed-dest cleanup in the fetch loop; corrupt cached index → cache miss → re-fetch |

**Dependencies**: Phases are independent except Phase 4's source-side short-circuit is a prerequisite to
Phase 3's per-file re-fetch economy (both touch the source work-set construction). Phase 5 and Phase 6 touch the
same `ensure_indexes` loop (source gating + index self-heal) — sequential within that region. Phase 1 (the
guard) is independent and lands first so the ensure-base semantics are fixed before the download phases build
on them.

## Complexity Tracking

> Not required — Constitution Check has no violations.
