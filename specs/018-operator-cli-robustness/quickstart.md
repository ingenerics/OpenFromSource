# Quickstart Validation: Operator Track & CLI/Config Robustness

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md) · **Contracts**: [contracts/cli.md](contracts/cli.md)
· **Data model**: [data-model.md](data-model.md)

This guide is the runnable acceptance path for feature 018. Each phase lists the unit/integration/contract test
commands (both runners) and the manual acceptance steps. It references, rather than duplicates, the contract
assertions.

## Prerequisites

- Repo root with the `.venv` active; both documented runners:
  - `python -m pytest -q`
  - `python -m unittest discover -s tests`
- Baseline (must be green before starting): `pytest` → 608 collected; `unittest` → 608 OK;
  `tests/contract/test_cli.py` → 60 passed + 11 subtests (or `python -m unittest tests.contract.test_cli`).
- Fixtures: `tests/fixtures/make_test_iso.py` (`write_archive`, `write_pool_files`, pool servers) and the
  existing scratch cache/handoff/diff builders in `tests/unit/test_apply_script.py` /
  `tests/unit/test_handoff.py` / `tests/integration/test_handoff_flow.py`.
- `bash`, `apt-get`, `gpg`, `gpgv` available for the operator-script and handoff tests.

---

## Phase 1 — Handoff Excludes Out-of-Config Records Loudly (F-18)

**Run**:
```
python -m pytest tests/unit/test_handoff.py -q
python -m pytest tests/integration/test_handoff_flow.py -q
python -m pytest tests/contract/test_cli.py -q
```

**Manual acceptance** (config narrowed after verification):
1. Register + verify a record under `trixie/contrib`, then regenerate the handoff with the configuration reduced
   to `main` only.
2. **Then** the record is omitted from `handoff/dists/trixie/contrib/binary-amd64/Packages.gz` (component absent
   or empty), the summary prints the Warning line naming the exclusion, and
   `SELECT sync_mask FROM binaries WHERE package_name = '<pkg>'` is unchanged (delivery id not advanced).
3. Restore `contrib` to the config and regenerate → the record is included again.

**Expected**: FR-001/FR-002 hold; exclusion is loud and non-permanent.

---

## Phase 2 — Apply Output Is a Clean Count (F-19)

**Run**:
```
python -m pytest tests/unit/test_apply_script.py -q
python -m pytest tests/integration/test_diff_flow.py -q
```

**Manual acceptance** (generated `apply.sh` against a fixture diff):
1. Fresh apply → stderr empty, summary line is exactly `New files: <number>`, no `[: …: integer expression
   expected` anywhere.
2. Re-run the same diff → already-applied branch reached (`New files:       0` + the already-applied line),
   exit 0, stderr clean.
3. A zero-file diff → completes with `0` and no error.
4. Force an apt failure (unreadable index) → exit 1, stderr names `{cache}/diff/.apply-apt.log` and tails its
   excerpt; merged files and handoff indexes left in place.

**Expected**: FR-003/FR-004 hold; the operator's count capture is clean.

---

## Phase 3 — Mistyped Config Values Fail Structurally (F-20)

**Run**:
```
python -m pytest tests/unit/test_config.py -q
python -m pytest tests/contract/test_cli.py -q
```

**Manual acceptance** (each mistyped `project.defaults.toml`, then any command, e.g. `config`):
1. `[paths] base_dir = 123` → exit 102, error names the key and expected type; `config --format json` emits the
   JSON payload.
2. `archive = 5` (non-table) → exit 102, names the key and expected `table`.
3. `[paths] binary_cache = 5` → exit 102, names the key.
4. `[fetch] source = "yes"` → exit 102, rejected; TOML `true`/`false` still accepted.
5. Correctly-typed config → loads unchanged (regression).

**Expected**: FR-005/FR-006/FR-007 hold; no raw traceback, no silent acceptance.

---

## Phase 4 — Local Overrides Always Apply and Validate (F-21)

**Run**:
```
python -m pytest tests/unit/test_config.py -q
python -m pytest tests/contract/test_cli.py -q
```

**Manual acceptance** (defaults without `[resolution]`):
1. `project.local.toml` with `[resolution] max_source_dep_depth = 2` → override applied and flagged
   (`config --format json` shows `max_source_dep_depth_override: true`).
2. `project.local.toml` with `max_source_dep_depth = -2` → load fails, exit 102, error names the key.
3. `[[archive.source]]` replacement in the local file → behaves exactly as before (regression).

**Expected**: FR-008/FR-009 hold; no silently dropped override, no skipped validation.

---

## Phase 5 — Read Commands Gate on Cache Initialization (F-22)

**Run**:
```
python -m pytest tests/unit/test_list.py -q
python -m pytest tests/unit/test_verify.py -q
python -m pytest tests/contract/test_cli.py -q
```

**Manual acceptance** (fresh temp dir with config pointing at a non-existent cache):
1. `show vifm`, `list --status all`, `verify` with no database → each exits 101, prints the clean error,
   and **no `package_mgmt.db` file is created** (directory listing unchanged).
2. Seed a schema-mismatched database → the same rebuild message/exit 101 as `systems`/`snapshot`/`handoff`.
3. `init` then re-run → behavior unchanged (regression).

**Expected**: FR-010/FR-011 hold; read commands match their gated siblings and create no stray file.

---

## Release Gate

- Both runners green across the whole suite.
- `tests/contract/test_cli.py` green (contract-preserving; extended for F-20/F-21/F-22).
- 012/013/014 contract docs updated alongside the behavior (docs inheritance).
- Manual acceptance recorded per phase above.
- `pytest tests/unit tests/integration tests/contract -q` → all pass.
