# Quickstart: Resolver & Dependency Correctness

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md) · **Contracts**: [cli.md](contracts/cli.md) · **Data model**: [data-model.md](data-model.md)

Validation guide for the six resolver/dependency phases. The shared parser/model phases (3+4) land first with one
fixture pack; each phase is validated independently. Prerequisites: repo checkout (post-015), project venv, and the
existing offline-safe fixtures (`tests/fixtures/make_test_iso.py`, including its backports variant).

**Global acceptance**: after all six phases, both documented runners are green — `pytest` and
`python -m unittest discover -s tests` — from a clean checkout.

---

## Phases 3+4 (foundation) — Per-alternative constraints & arch/profile handling

**Run**:
```bash
.venv/bin/pytest tests/unit/test_dependency.py -q
.venv/bin/pytest tests/unit/test_resolver_batch.py tests/unit/test_virtual_resolution.py -q   # regression
```
**Expected**:
- `parse_depends_line("foo (= 9.9) | bar (= 2.0)")` keeps `bar`'s `(= 2.0)`; round-trip preserves it; legacy
  string-form alternatives still deserialize (backward compat).
- `foo [amd64]` kept, `foo [i386]`/`foo [!amd64]` dropped, multi-profile token kept with constraint; an unparseable
  token logs a warning and the rest of the line still parses.

---

## Phase 1 — Resolved Alternative Groups Exit Cleanly (F-8)

**Run**:
```bash
.venv/bin/pytest tests/unit/test_resolver_batch.py -q        # alternative-group fixtures
.venv/bin/pytest tests/unit/test_add_file.py -q              # --file batch summary
```
**Also (manual, acceptance)**:
1. With a fixture where `pkg` Depends `ghost | realpkg` and only `realpkg` exists: `package_mgmt add pkg`.
2. Inspect the output and exit code → `Package pkg resolved ...` and `echo $?` → `0`, no error line.
3. `package_mgmt add --file manifest.txt` with that request → summary shows it resolved, `Errors: 0`.

**Expected**: a satisfied alternative group never reports failure; errors appear only when the whole group fails.

---

## Phase 2 — Binaries Link to the Exact Source Version and Release (F-9+F-10)

**Run**:
```bash
.venv/bin/pytest tests/unit/test_resolver_batch.py -q        # source-version/backports linkage fixtures
.venv/bin/pytest tests/unit/test_handoff.py -q               # backports variant
```
**Also (manual, acceptance)**:
1. Build a fixture index with sources `{foo 1.0-1, foo 2.0-1}` and a binary `foo-bin 1.0-1+b1` declaring
   `Source: foo (1.0-1)`; `package_mgmt add foo-bin`.
2. Query the DB (`SELECT package_name, source_version FROM sources`) → `foo 1.0-1` (not 2.0-1).
3. Backports fixture → the source record's release is the backports release.

**Expected**: exact `(name, version)` linkage; derived version when no `Source:` version; divergence logged (log
file) when a fallback is taken — never a wrong silent link.

---

## Phase 5 — Verify Checks Version Constraints (F-13)

**Run**:
```bash
.venv/bin/pytest tests/unit/test_verify.py -q
```
**Also (manual, acceptance)**: with `app` Depends `libfoo (>= 2.0)` and only `libfoo 1.5` current,
`package_mgmt verify --graph` → Runtime Status INCOMPLETE, `echo $?` → 1. With `libfoo (= 1.5)` → COMPLETE, exit 0.

**Expected**: `verify` matches the operator's install check on direct-name deps; providers stay over-inclusive.

---

## Phase 6 — The Build-Dep Pass Stays Batch-Scoped (F-15)

**Run**:
```bash
.venv/bin/pytest tests/unit/test_resolver_build_deps.py -q
```
**Also (manual, acceptance)**:
1. Run `add` twice on the same cache; compare the second run's build-tool counters → they count only its own
   sources (retained sources are not re-processed).
2. Run deep, then shallow, over the same retained sources → zero new build tools for retained sources.
3. A fresh batch still resolves its full transitive build-tool chain.

**Expected**: no per-`add` re-walk of historical sources; retention means keep, not keep extending.

---

## Global regression gate

```bash
.venv/bin/pytest -q
.venv/bin/python -m unittest discover -s tests
```
**Expected**: both runners 100% green from a clean checkout (no documented command red while another is green).
