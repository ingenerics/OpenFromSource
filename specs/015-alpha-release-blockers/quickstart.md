# Quickstart: Alpha Release Blockers

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md) · **Contracts**: [cli.md](contracts/cli.md), [artifacts.md](contracts/artifacts.md) · **Data model**: [data-model.md](data-model.md)

Validation guide for the six blocker phases. Each phase is validated independently (the phases share only that they
are all blockers). Prerequisites: repo checkout at base `995cf3e` (or the feature branch), project venv, and the
existing local mirror fixture (`tests/fixtures/make_test_iso.py`) for offline-safe runs.

**Global acceptance**: after all six phases, both `pytest` and `python -m unittest discover -s tests` are green,
and the release completion checklist in `review/04_alpha_summary/01_alpha_blockers.md` passes.

---

## Phase 1 — Complete Runtime Closures (Pre-Depends)

**Run**:
```bash
.venv/bin/pytest tests/unit/test_index_loader.py tests/unit/test_resolver_batch.py -q
.venv/bin/pytest tests/integration/test_download_flow.py -q   # fixture with Pre-Depends-only dep
```
**Also (manual, acceptance)**:
1. Add the Pre-Depends-only fixture package: `package_mgmt add <fixture-pkg> && package_mgmt download`.
2. `package_mgmt verify --graph` → COMPLETE.
3. `package_mgmt handoff --system <box>` → the generated `Packages.gz` contains a `Pre-Depends:` stanza for the
   fixture.
4. Offline install simulation (hermetic apt, per `tests/integration/test_handoff_flow.py`): `apt-get -s install
   <fixture-pkg>` resolves from the cache with zero missing packages.
5. **Census**: run the Pre-Depends census on the alpha package set; record which Pre-Depends targets are *not* also
   in `Depends`/`Provides` and confirm the closure now includes them.

**Expected**: closure includes the Pre-Depends-only target; handoff emits the field; install simulation succeeds.

---

## Phase 2 — Order-Independent Source Coverage

**Run**:
```bash
.venv/bin/pytest tests/unit/test_resolver_batch.py tests/unit/test_virtual_resolution.py tests/unit/test_resolver_build_deps.py -q
```
**Also (manual, acceptance)**:
1. Diamond fixture at depth 1 (`app` Depends `c1, x`; `c1` Depends `x`): resolve twice with the `Depends` lines in
   opposite orders → the in-scope source set is identical and includes `x`'s source.
2. Virtual provider reached deep, then shallow in a later `add` → the source appears (cross-run repair).
3. Forced mid-batch failure (monkeypatched upsert) → later segments re-resolve the shared dep or report an error;
   never silently skip.

**Expected**: source set is order-independent; virtual sources are repairable; no silent closure holes.

---

## Phase 3 — Safe Diff & Delivery Ordering

**Run**:
```bash
.venv/bin/pytest tests/unit/test_diff_generator.py tests/integration/test_sync_snapshot_flow.py -q
```
**Also (manual, acceptance)**: with one pool file missing/unreadable, run `package_mgmt sync --system <box>`:
- exits non-zero;
- `diff/` contains no stanza/manifest entry for the failed record;
- delivery state for the system is unchanged (re-run shows the failed record still pending);
- after repair, a clean sync advances exactly the shipped ids.

**Expected**: diff never references a missing payload; delivery never advances on a failed sync.

---

## Phase 4 — Fail Hard on Linkless Media

**Run**:
```bash
.venv/bin/pytest tests/unit/test_bootstrap_script.py -q
```
**Also (manual, acceptance)**:
1. FAT loop device: create a FAT filesystem, copy the handoff (symlink stripped), run `bootstrap.sh` → it **fails
   hard**: non-zero exit, clear message, no apt source configured; it never reports success.
2. Link-capable cache: `bootstrap.sh` still succeeds cleanly (regression).

**Expected**: never "success with unreachable pool"; hard, clear failure on linkless media.

---

## Phase 5 — No-Expiry Repository & Generation-Time Dating

**Run**:
```bash
.venv/bin/pytest tests/unit/test_handoff.py -q
```
**Also (manual, acceptance)**:
1. Generate a handoff; inspect `dists/<release>/Release` → it has a `Date:` field at generation time and **no**
   `Valid-Until:` field.
2. Regenerate → `Date` advances (generation time, not the cache mtime).
3. With `SOURCE_DATE_EPOCH` set: `Date` pinned; regeneration is byte-identical (opt-in reproducibility).
4. Simulate an old cache (or wait): `apt update` against the generated repo succeeds — no expiry error, regardless
   of the repository's age.

**Expected**: the repository imposes no expiry deadline; `Date` reflects generation time; reproducibility is an
explicit opt-in only.

---

## Phase 6 — Clean Machine-Readable Errors & Green Documented Runner

**Run** (both documented runners from a clean checkout):
```bash
.venv/bin/pytest -q
.venv/bin/python -m unittest discover -s tests
```
**Also (manual, acceptance)**: trigger a JSON-mode config error (invalid depth in TOML) and capture stderr:
`package_mgmt config --format json 2>err.txt` → `err.txt` contains exactly one JSON document (parses as JSON), no
`Config load failed:` text.

**Expected**: both runners green (the previously failing 8 tests pass); stderr is pure JSON on JSON error exits.
