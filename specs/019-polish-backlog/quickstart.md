# Quickstart Validation: Polish Backlog (Merged Low Findings)

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md) · **Contracts**: [contracts/cli.md](contracts/cli.md)
· **Data model**: [data-model.md](data-model.md)

This guide is the runnable acceptance path for feature 019. Each phase lists the unit/integration/contract test
commands (both runners) and the manual acceptance steps. It references, rather than duplicates, the contract
assertions.

## Prerequisites

- Repo root with the `.venv` active; both documented runners:
  - `python -m pytest -q`
  - `python -m unittest discover -s tests`
- Baseline (must be green before starting): `pytest` → 637 collected; `unittest` → 637 OK;
  `tests/contract/test_cli.py` → 68 passed + 15 subtests.
- Fixtures: the existing scratch-cache / index / pool builders in `tests/fixtures/` and the integration helpers
  from 015-018 (`test_handoff_flow.py`, `test_diff_flow.py`, `make_test_iso.py`).
- `bash`, `apt-get`, `gpg`, `gpgv` available for the operator-script and handoff tests.

---

## Phase 1 — Contract & Side-Effect Honesty (P-1, P-4, P-5)

**Run**:
```
python -m pytest tests/unit/test_add.py tests/unit/test_init.py tests/integration/test_ensure_base_flow.py -q
python -m pytest tests/contract/test_cli.py -q
```

**Manual acceptance**:
1. Stale-schema `add` → exit **101**, stale-schema message; valid DB unchanged.
2. `init` into a non-writable cache dir → exit **100**; `init` into a full disk (ENOSPC) → exit **1**.
3. `init` against unreachable mirrors → exit 0, summary line reads `Status: initialized (no mirror reachable —
   offline mode, 0 binaries cached)`.
4. Fresh cache + no image → `ensure-base` exits non-zero and **no `package_mgmt.db` file exists** afterward
   (directory listing unchanged).

**Expected**: FR-001/FR-002/FR-003 hold; exit codes match the contract and no stray DB is created on failure.

---

## Phase 2 — Flag & Argument Consistency (P-2, P-3, P-7)

**Run**:
```
python -m pytest tests/unit/test_verify.py tests/unit/test_systems.py tests/unit/test_add.py tests/unit/test_list.py -q
python -m pytest tests/contract/test_cli.py -q
```

**Manual acceptance**:
1. `verify --graph` → usage error (`--graph` removed); bare `verify` still runs the graph verification
   (regression: output/exit unchanged).
2. `verify --package X` under a finite report depth → build-dep lists respect the depth (same as full-cache).
3. `systems add ""`, `systems add "a b"`, `systems add $'a\nb'` → clean error, exit 1, no row; duplicate moniker
   still errors with no leaked connection.
4. `add "vim="` and a `--file` line `vim=` → both rejected (exit 1).
5. `list --deps X --status all` → clean usage error (mutually exclusive); `list --deps X` alone → tree;
   `list --status all` alone → status table.

**Expected**: FR-004/FR-005/FR-006 hold; the CLI surface is consistent and the decorative flag is gone.

---

## Phase 3 — Honest Sync & Verify Reports (P-6, P-18)

**Run**:
```
python -m pytest tests/unit/test_sync.py tests/unit/test_verify.py -q
python -m pytest tests/integration/test_sync_flow.py -q
```

**Manual acceptance**:
1. After a successful non-empty `sync`, re-run with nothing pending → the `diff/` directory is **empty**
   (regenerated, not stale), exit 0, snapshot unchanged.
2. `verify` with `max_build_dep_report_depth = 0` → no build-tool levels reported; `-1` → all; `N` → up to `N`;
   `verify --package X` matches the full-cache behavior at each depth.

**Expected**: FR-007/FR-008 hold; a zero-pending sync never ships stale content and the build-dep report is honest.

---

## Phase 4 — Deterministic, Spec-Compliant Resolution (P-9, P-10, P-11, P-12, P-16)

**Run**:
```
python -m pytest tests/unit/test_repository.py tests/unit/test_resolver.py -q
```

**Manual acceptance** (fixture-level):
1. Same version current in `main`/`contrib`/`non-free` → `main` wins, `main` pool path recorded.
2. Two current rows for the same package+arch → deterministic id on repeated calls.
3. Build-dep fallback with a mismatched-version candidate → not linked; with a valid candidate → linked, no
   residual error.
4. Alternative group satisfied only by superseded rows → does not suppress resolution; `current` provider resolves.
5. Index refresh upgrades a provider in the primary source → a cached virtual resolution picks up the upgrade.

**Expected**: FR-009/FR-010/FR-011/FR-012 hold; selection is deterministic and every link/probe is constraint-aware.

---

## Phase 5 — Accurate Database Lifecycle (P-13, P-14, P-15, P-17)

**Run**:
```
python -m pytest tests/unit/test_initializer.py tests/unit/test_resolver.py tests/unit/test_verify.py -q
```

**Manual acceptance** (fixture-level):
1. `ensure-base` Phase A is a single transaction — a forced mid-phase failure rolls back provides too.
2. `populate_provides_table` reports 0 inserts when all rows already exist (no phantom count); `init` still
   persists provides.
3. Remove a release from config + refresh index → its `provides` rows are swept; a stale provider is never selected.
4. Re-upsert a circular binary → flag reset; `verify` and `list --deps` agree (current-only).
5. After resolution, `packages.status` = `current` and a transitive dep shows no fabricated requested version.

**Expected**: FR-013/FR-014/FR-015/FR-016 hold; the stored model is truthful and transactional.

---

## Phase 6 — Path & Download Robustness (P-19, P-20, P-21)

**Run**:
```
python -m pytest tests/unit/test_paths.py tests/unit/test_downloader.py tests/unit/test_handoff.py -q
python -m pytest tests/contract/test_cli.py -q
```

**Manual acceptance**:
1. `[paths] base_dir = "relative/path"` → exit 102, error names `[paths] base_dir`.
2. A `Retry-After: 86400` response → retried after ≤ 30 s (cap); `Retry-After: 2` → 2 s.
3. Handoff from `/media/My USB/cache` → exit 1 with the actionable diagnostic, no `deb` line written; a space-free
   path behaves exactly as before.

**Expected**: FR-017/FR-018/FR-019 hold; no silent misconfiguration, no unbounded stall, no broken deb entry.

---

## Phase 7 — Doc/Test Hygiene (P-22, P-23)

**Run**:
```
python -m pytest tests/unit/test_downloader.py tests/unit/test_config.py -q
```

**Manual acceptance**:
1. The `_stat_size_matches` docstring and the renamed size-match test describe the conservative re-hash behavior.
2. The mirror-normalization tests are grouped under `TestMirrorNormalization`; total test count unchanged.

**Expected**: FR-020 holds; labels match behavior and coverage is preserved.

---

## Release Gate

- Both runners green across the whole suite (`pytest` and `python -m unittest discover -s tests`).
- `tests/contract/test_cli.py` green (contract-preserving; extended per phase; `verify --graph` references removed).
- 014 contract table + 018 quickstart updated for the `verify --graph` removal (docs inheritance, Constitution VIII).
- Manual acceptance recorded per phase above.
- `pytest tests/unit tests/integration tests/contract -q` → all pass.
