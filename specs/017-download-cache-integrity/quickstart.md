# Quickstart Validation: Download & Cache Integrity

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md) · **Contracts**: [contracts/cli.md](contracts/cli.md)

This guide is the runnable acceptance path for feature 017. Each phase lists the unit/integration test commands
(both runners) and the manual acceptance steps. It references, rather than duplicates, the contract assertions.

## Prerequisites

- Repo root with the `.venv` active; both documented runners:
  - `python -m pytest -q`
  - `python -m unittest discover -s tests`
- Baseline (must be green before starting): `pytest` → 580 passed + 41 subtests; `unittest` → 580 OK;
  `tests/contract/test_cli.py` → 58 passed + 11 subtests (or `python -m unittest tests.contract.test_cli`).
- Local mirror fixture: `tests/fixtures/make_test_iso.py` (`write_archive`, `serve_pool`, `serve_rate_limited`).
  Phase 2 adds a `serve_redirecting` handler; Phase 6 uses a mid-index-drop handler (both in `make_test_iso.py`).

---

## Phase 1 — Base Ingest Is Fresh-Cache-Only (H-2, M-3)

**Run**:
```
python -m pytest tests/unit/test_ensure_base.py -q
python -m pytest tests/integration/test_ensure_base_flow.py -q
python -m pytest tests/contract/test_cli.py -q
```

**Manual acceptance** (uses `make_test_iso.write_archive` + the real `ensure-base` flow):
1. `init` → `ensure-base` on the fixture image → succeeds (fresh cache).
2. Re-run `ensure-base` on the same image.
3. **Then** it refuses with a clear error (exit 1, JSON on stderr naming the fresh-cache rule), and
   `SELECT COUNT(*) FROM binaries` is unchanged.

**Expected**: FR-001 holds; no re-run can reset `verified` state (H-2) or create a duplicate `current` row
(M-3), because a populated cache is refused before any registration.

---

## Phase 2 — Downloads Follow Redirects (F-16)

**Run**:
```
python -m pytest tests/unit/test_downloader.py -q
```

**Manual acceptance** (HTTP fixture that redirects a pool request):
1. Serve a pool where `/pool/…` 301s (and 302/307/308 variants) to the real location.
2. `download` → the payload downloads and passes its checksum (final payload hashed, FR-003).
3. A redirect with no `Location` and a redirect loop → clear permanent error, no `.part` left behind.
4. A chain over the 5-hop limit → clear permanent error naming the limit.

**Expected**: FR-002/FR-003 hold; direct downloads unchanged.

---

## Phase 3 — Verified Sources Are Re-Checked Each Run (F-17)

**Run**:
```
python -m pytest tests/unit/test_downloader.py -q
python -m pytest tests/integration/test_download_flow.py -q
```

**Manual acceptance**:
1. `add` a package with a source, `download` (source verified).
2. Delete one file of the verified source → next `download` re-fetches exactly that file and re-marks the source
   verified (FR-004/FR-005).
3. Corrupt a file without changing its size → re-fetched (checksum step catches it).
4. Multi-file source with one file missing → only the missing file fetched.
5. All-intact source → nothing re-downloaded.

**Expected**: FR-004/FR-005 hold; `sources.verified` reflects the re-check.

---

## Phase 4 — Present Valid Files Are Not Re-Downloaded (M-7)

**Run**:
```
python -m pytest tests/unit/test_downloader.py -q
```

**Manual acceptance**:
1. With an **unverified** record whose pool file is already present and matches (size + sha256), `download` → the
   file is not re-fetched (FR-006).
2. Unverified + mismatched → re-fetched. Unverified + absent → fetched.
3. Mixed set → only missing/mismatched fetched.

**Expected**: FR-006 holds; verified flag is not changed by the short-circuit.

---

## Phase 5 — Source Indexes Respect the Configuration (M-4)

**Run**:
```
python -m pytest tests/unit/test_resolver.py -q
python -m pytest tests/contract/test_cli.py -q
```

**Manual acceptance**:
1. Config with `[fetch] source = false` and an **unreachable** Sources index → `add` succeeds using binaries
   alone (FR-007); no Sources-index fetch is attempted.
2. Same config with reachable sources → still no Sources fetch.
3. `source = true` → Sources indexes fetched/loaded as before.
4. Build the cache under `regular_user` (`source = false`), then switch the config to `developer`/`graybeard`
   (`source = true`) → the next `add` fetches and loads the previously-missing Sources indexes (the gate
   re-enables on cache miss; profile switching must not suppress source fetching).

**Expected**: FR-007 holds; binary-only profile behavior restored.

---

## Phase 6 — Index Corruption Self-Heals (F-23)

**Run**:
```
python -m pytest tests/unit/test_resolver.py -q
python -m pytest tests/unit/test_index_loader.py -q
```

**Manual acceptance**:
1. A fixture that drops the connection mid-index → retry `add` re-fetches cleanly (no half-written index in the
   way; FR-008).
2. Pre-seed a truncated cached `Packages.gz` → `add` treats it as a cache miss, re-fetches, and succeeds — no
   crash loop (FR-009).
3. Successful index download → persisted atomically (no partial index becomes the cached index).

**Expected**: FR-008/FR-009 hold; a poisoned cache self-heals on the next run.

---

## Release Gate

- Both runners green across the whole suite.
- `tests/contract/test_cli.py` green (contract-preserving).
- Manual acceptance recorded per phase above.
- `pytest tests/unit tests/integration tests/contract -q` → all pass.
