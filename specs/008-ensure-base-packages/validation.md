# Validation Record: Ensure Base Packages

Results of the quickstart.md scenarios, run against the real CLI
(`PYTHONPATH=src python3 -m package_mgmt ...`) on a scratch cache.

Environment: Debian trixie, python3-pycdlib 1.12.0, fixture images from
`tests/fixtures/make_test_iso.py`, real archive `http://deb.debian.org/debian`.

## Scenarios 1–7 (fixture images)

| # | Scenario | Result | Notes |
|---|----------|--------|-------|
| 1 | One-command ingestion | PASS | 6 binaries, 1 provides, `verified = 0`, `Index/image mismatches: 0`, 6/6 resolved against scratch archive |
| 2 | Mismatch diagnostics | PASS | `--omit` fixture → `Index/image mismatches: 1` naming `pool/main/v/vifm/vifm_1.0-2_amd64.deb (in index, not found in image)`; record registered, `verified = 0` |
| 3 | Netinst/minimal warning | PASS | 4-package image → warning "...looks like a netinst or minimal image..."; run completes |
| 4 | Idempotency | PASS | Two runs → identical counts (6 binaries / 6 packages / 1 provides) |
| 5 | Behaves like `add` | PASS | `verify --graph` COMPLETE (6 binaries, 0 unresolved, 0 unlinked, exit 0); `add app` satisfied by the image record — no new binaries/sources/packages; list/show output identical to add-fetched records (T033/T034) |
| 6 | Error handling | PASS | No image → `{"error": "No installer image found in <iso_dir>", "exit_code": 1}`; fake ISO → `{"error": "Failed to read ISO image <path>: Failed to read entire volume descriptor", "exit_code": 1}`; DB untouched in both cases |
| 7 | Config surface | PASS | `iso_dir` in text + JSON output; `[paths] iso_dir = "{base_dir}/iso-alt"` in `project.local.toml` → `(override: project.local.toml)` shown; `init` creates `iso-alt` |

## Scenario 8 — real DVD image

Image: `/var/cache/package_mgmt/iso/debian-13.6.0-amd64-DVD-1.iso`
(debian-13.6.0 trixie, 3.99 GB; SHA256 verified against `sha256sum`).

Archive: `http://deb.debian.org/debian`, trixie, components
`main` + `contrib` + `non-free-firmware` (real network).

Final summary after the Phase 9 batch refactor (run with `[fetch] source = true`,
the default — both runs below are the same 4,447-name batch):

```text
ISO image:        debian-13.6.0-amd64-DVD-1.iso (trixie: contrib, main, non-free-firmware)
Binary packages:  4447
Provides entries: 1268
Index/image mismatches: 0
Base packages resolved: 4441/4447
Warnings:         none
Errors:           12   (fresh run; 11 after a re-run — see note below)
  - 7zip: Virtual package 'librust-hashbrown-0.15+default-dev' has no providers in the index
  - 7zip: No version of openbox-dev satisfies constraint (<< 3.6.0-1)
  - 7zip: Build tool not resolved: openbox-dev | openbox-dev
  - ... (all real-archive index quirks; well under the 50 console cap)
```

Checkpoint expectations — all met:

- ~4,400+ binary packages: **4,447** (main 4,412, contrib 3, non-free-firmware 31,
  from the image; plus ~9,200 archive-only records brought in by resolution)
- no netinst warning (4,447 ≥ 1,000 threshold)
- `Resolved X/Y` progress line through the whole batch (4,447 steps)
- errors listed at the end, console cap of 50 (12 here; the 50-cap path is
  covered by unit test T027 with 61 errors → 50 shown + log note)
- `verified = 1` count after the run: **0** (add-rule: registration never sets it)
- `Index/image mismatches: 0` — the real DVD's index matches its pool exactly
- re-run converges: identical row counts (13,664 binaries / 16,214 packages /
  7,672 sources / 13,664 links / 70,973 build-dep links), zero duplicate
  `binary_to_source` rows (the table's inline UNIQUE constraint)

Exit code: 1 (errors exist — same rule as `add`).

## Performance note (Phase 9 result)

The per-name resolution design measured ~1.5 s/name with `fetch.source = true`
(~2 h projected for the full batch) and accumulated duplicate
`binary_to_source` rows (no UNIQUE constraint). Phase 9 replaced it with a
first-class batch resolver (`resolve_batch`): the union closure is walked
once, the build-dep pass runs once, per-name commits checkpoint progress, and
the schema-level `UNIQUE(binary_id, source_id)` constraint (KISS: changed
directly in the DDL, dev DBs rebuilt) makes re-linking a no-op. **Measured:
full 4,447-name batch in ~13 s (fresh run 13.0 s, re-run 12.4 s)** — the
"≤ 15 min" target from tasks.md T046 is met by two orders of magnitude. A
runtime-only run (`fetch.source = false`) completes in ~3–4 min (index
download/parse bound, unrelated to resolution).

Error-count note: a fresh run reports 12 unique errors; a re-run reports 11.
The drop is sequential-parity behavior, not a bug — cached
`virtual_resolutions` rows from the first run short-circuit the walk on
re-runs (identical to sequential `add` on a converged cache); DB state
converges to identical counts. The two `openbox-dev` build-tool entries are a
real Debian index quirk (a build-dep listing the same name as its own
alternative); the debhelper/openbox-dev/lib-sodium18/hashbrown-0.15 errors are
genuine unsatisfiable-constraint and absent-provider cases in trixie.

## Suite

Full suite green at validation time: `PYTHONPATH=src python3 -m unittest
discover -s tests` → 155 tests OK (unit + integration + contract).
