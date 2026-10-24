# debug_bootstrap.md — Operator Bootstrap / apt / vifm Debugging

Scratchpad for fixing the release-blocking bugs in the operator bootstrap flow
(spec 012/013). We are on the **operator** system; the admin's first handoff is
at `/var/cache/package_mgmt`. The project source is at the repo root
(`/home/op/code/OpenFromSource`). Passwordless sudo available.

**Constraint**: the real `bootstrap.sh` + `apt update` + `apt install vifm`
run is effectively one-shot — validate everything in isolation first, then do a
single clean end-to-end run.

## Environment state (verified read-only)

### Operator system
- Debian trixie (codename matches handoff releases), amd64, apt 3.0.3.
- `sudo` passwordless. `gpg` is **not** installed.
- `/etc/apt/sources.list.d/` empty (pre-bootstrap clean state).
- `/etc/apt/sources.list` still has **network archives** active
  (deb.debian.org trixie / security trixie-security / trixie-updates, incl.
  `deb-src`). Network to deb.debian.org:80/443 is reachable in this lab.
  → Decision: treat the operator as strictly offline: **comment out the
  network archives** (T1b) so apt uses only the cache `file:` source.
- Base deps already installed at **exactly the cache's versions**: perl
  5.40.1-6, libc6 2.41-12+deb13u3, libmagic1t64 1:5.46-5, libncursesw6 /
  libtinfo6 6.5+20250216-2, libselinux1 3.8.1-1, libacl1 2.3.2-2+b1, zlib1g,
  perl-base / libperl5.40 / perl-modules-5.40 5.40.1-6.
- Not installed: `vifm`, `vim`, `libsodium23`, `libgpm2` (all present in cache).

### Handoff (`/var/cache/package_mgmt/binary/handoff/`)
- Contains: `bootstrap.sh`, `offline-cache.gpg`, `.gnupg` (secret key),
  `pool -> ../pool` symlink, `dists/{trixie,trixie-backports}` each with
  `Release` + `Release.gpg` + per-component `Packages.gz`.
- `trixie/main` `Packages.gz`: 151 packages. All 151 `Filename` entries exist
  in the pool; all 151 SHA256+Size match the index; `Release` checksums match
  the actual `.gz` bytes. `contrib`/`non-free`/`trixie-backports/main`
  indexes are valid empty gzips (20 bytes).
- `bootstrap.sh` matches the `BOOTSTRAP_SCRIPT` template in
  `src/package_mgmt/lib/service/handoff.py` exactly (diff = shebang only).
- vifm stanza (from generated Packages.gz):
  `Depends: perl:any, libc6 (>= 2.38), libmagic1t64 (>= 5.12),
  libncursesw6 (>= 6.1), libtinfo6 (>= 6),
  vim (>= 2:8.1.0693-2~) | neovim (>= 0.2.2-1~)` — full 27-pkg closure present
  in the cache.

### Cache / DB (`/var/cache/package_mgmt/package_mgmt.db`)
- Schema v1; tables incl. `systems`, `binaries` (status/verified/circular/
  sync_mask), `packages`, `sources`, `binary_to_source`, `source_build_deps`,
  `provides`, `virtual_resolutions`.
- `systems`: `mini-pc` bit 2 (active).
- All 151 current binaries: `verified=1`, `sync_mask=2` (admin's handoff
  snapshot already taken).
- No `diff/` dir → this is the first handoff; sync (013) not yet exercised.
- `logs/package_mgmt.log` shows: init (4 indexes; backports fell back to .xz),
  add --file (8 requested, 8 resolved), download (151 verified), systems add
  mini-pc, handoff generated (151 packages across 2 releases).

### Project source (`/home/op/code/OpenFromSource`)
- Git HEAD `3af9efa` (main). Only uncommitted changes are 014 base-profiles
  files (`config.py`, `config_loader.py`, `config` cmd, tests, tasks.md) —
  **012/013 code is committed unchanged**, so bugs are in the committed
  generator/script/artifacts, not injected locally.
- Key files:
  - `src/package_mgmt/lib/service/handoff.py` — `BOOTSTRAP_SCRIPT` (bash
    template), `APPLY_SCRIPT`, `generate_handoff`, `generate_diff`.
  - `src/package_mgmt/cli/commands/handoff.py`, `snapshot.py`, `sync.py`,
    `systems.py`; `src/package_mgmt/lib/service/snapshot.py`.
  - Tests: `tests/unit/test_bootstrap_script.py`, `test_apply_script.py`,
    `test_handoff.py`, `test_diff_generator.py`;
    `tests/integration/test_handoff_flow.py` (real apt-get, hermetic scratch
    apt dirs), `test_diff_flow.py`.
- Config: `project.defaults.toml` (trixie/main only) + `project.local.toml`
  (trixie main/contrib/non-free + trixie-backports main).

## Findings

- The handoff artifacts are **internally consistent**; static inspection found
  no defect (index/checksum/pool/signature layout all correct, script matches
  template, closure complete, base versions align).
- Therefore the errors almost certainly only surface when **apt actually
  processes the handoff** (Release/checksum/trust subtleties) and when
  **resolving vifm against the real installed base**, especially with the
  network archives still active (offline operator).
- The bootstrap only *adds* the cache source; it never disables the network
  sources in `/etc/apt/sources.list` — on a truly offline operator that is a
  contributing failure (and why T1b comments them out).

## T1 — Isolated reproduction results (hermetic apt harness, real handoff)

- **`apt update` against the real handoff as `file:` source: exit 0, works.**
  All packages fetched (trixie main 13.5 kB, contrib/non-free/backports 20 B
  empty). BUT two warnings every run:
  - `W: OpenPGP signature verification failed: ... Missing key
    9D4A5CB20EC28A8CFAC97F72D1A78EEEB03DDE7A, which is needed to verify
    signature.` — apt is still attempting to verify `Release.gpg` even with
    `[trusted=yes]`, and the operator has no trusted key for the handoff's
    keyring.
  - `W: No Hash entry in Release file ...` — follows the failed verification
    (trust cannot be established, so apt falls back to no hash confirmation);
    packages still download because `[trusted=yes]`.
  → Both trace to the same root cause: **the `file://` trust model only
  silences failures via `[trusted=yes]`; the signature/key is never actually
  trusted on the operator** (spec 012 research.md "Decision 2"). These W:
  messages are the "errors" the release must clean up.
- **`apt-get install --download-only -y vifm` against the REAL dpkg status
  (`/var/lib/dpkg/status`): exit 0.** Resolves exactly the expected set from
  the handoff: vifm 0.14-3, vim 2:9.1.1230-2, vim-runtime, libsodium23
  1.0.18-1+deb13u1, libgpm2 1.20.7-11+b2 — 0 B / 9,983 kB from the cache.
  (Hermetic empty-status fails on `perl:any`, which is expected — perl is a
  base-system package not shipped in the handoff.)
- `sudo apt install vifm` on the real system is therefore expected to succeed
  (real dpkg status has perl + all base deps at cache-matching versions).

## T2 — Diagnosis (root cause found)

The two `apt update` warnings ("Missing key" + "No Hash entry") are **not**
inherent to `file://` as the spec research originally concluded. They have two
concrete, fixable root causes in the generator:

1. **`src/package_mgmt/lib/service/handoff.py` — `_release_text()` emitted a
   blank line between `Valid-Until` and the `MD5Sum:` hash blocks.** In Debian
   control format a blank line terminates the section, so apt's
   `pkgTagSection::Step` stopped there and never parsed `MD5Sum`/`SHA1`/
   `SHA256`/`SHA512` → `FoundHashSum == false` → **"No Hash entry in Release
   file"** + the repo was treated as weak/unsigned. (Confirmed against apt
   source `debmetaindex.cc:454` + a `pkgTagSection` probe: with the blank line,
   `FindS("MD5Sum")` returns empty; after removing it, the hash blocks parse.)
2. **The `Release` hash list only covered `Packages.gz`.** Once the blank line
   is removed apt parses the hashes and then fetches the *uncompressed*
   `Packages` target — which had no hash entry, so the index was dropped
   ("Unable to locate package"). Fix: list hashes for BOTH
   `{comp}/binary-amd64/Packages` and `.../Packages.gz` (the `.gz` is still the
   only file physically shipped; apt decompresses and verifies against the
   `Packages` entry). Real Debian releases do exactly this.
3. **`BOOTSTRAP_SCRIPT` used `deb [trusted=yes] file:...`.** This produced the
   "Missing key 9D4A… needed to verify signature" warning (apt 3.0.3 still
   attempts signature verification even under `[trusted=yes]`, and the operator
   has no trusted key). Fix: `deb [signed-by={handoff}/offline-cache.gpg]` —
   the research's intended Design 2/4 ("trusts exactly this key via signed-by")
   — which eliminates the warning AND actually enforces the signature.

**Verified end-to-end in `/tmp/opencode/`** (re-signed copies of the real
handoff, apt 3.0.3, scratch apt dirs):

- `[trusted=yes]` + fixed Release → still "Missing key" warning.
- `[signed-by=.../offline-cache.gpg]` + fixed Release → **`apt update` exit 0,
  ZERO warnings, index fetched**, `vifm` resolves and downloads.
- Tampering with `Release` after signing → `E: The repository ... is not
  signed` (signature enforced).
- The old research conclusions ("No Hash entry is unavoidable for `file://`",
  "signed-only file:// is broken", "two harmless warnings are expected") were
  all artifacts of root cause #1 (the blank line) and are now corrected in
  `specs/012-bootstrap-operator-system/research.md` and related docs.

## T3 — Fixes applied (source + tests + docs)

- `handoff.py`: removed the blank line in `_release_text()`; added
  `_index_hash_entries()` listing `Packages` + `Packages.gz` hashes (used by
  both `generate_diff` and `generate_handoff`); `write_sources_entry()` now
  writes `deb [signed-by=$handoff_dir/offline-cache.gpg] file:...`.
- Tests updated to the new entry format: `test_bootstrap_script.py`,
  `test_apply_script.py`, `test_handoff_flow.py`, `test_diff_flow.py`.
- Full suite: **470 passed, 14 subtests passed** (`PYTHONPATH=... pytest -q`).
- Docs corrected: spec 012 research/data-model/plan/cli/quickstart/tasks + spec
  013 research/quickstart/tasks (no more "harmless warnings"/`trusted=yes`).

## T4/T5/T6 — Regenerated handoff + real end-to-end (clean)

- `handoff --system mini-pc` regenerated `/var/cache/package_mgmt/binary/handoff`
  (151 pkgs, 2 releases). New `Release` has 0 blank lines and both hash entries;
  `bootstrap.sh` uses `[signed-by=.../offline-cache.gpg]`.
- T1b re-applied (network archives commented out), old entry + lists cleared.
- `sudo bash bootstrap.sh` → exit 0, "apt update: OK", **no warnings**.
- `sudo apt update` → exit 0, **no warnings**.
- `sudo apt-get install --reinstall --download-only vifm` → exit 0,
  `Get:1 file:... vifm 0.14-3 [1,007 kB]` from cache, 0 B network.

## Task plan

1. **T1** — Isolated, non-mutating reproduction in `/tmp`: replicate the
   project's hermetic harness (`tests/integration/test_handoff_flow.py`
   pattern): scratch apt dirs + real handoff as `file:` source + real pool +
   real dpkg status. Run `apt-get update`, then
   `apt-get install --download-only -y vifm`. Also `source bootstrap.sh` and
   call its functions with scratch paths. Capture every error; no system
   changes; does not consume the one-shot bootstrap.
2. **T1b** — Comment out the network archives in `/etc/apt/sources.list`
   (deb.debian.org / security / trixie-updates / deb-src) so apt uses only the
   cache `file:` source (strictly offline operator).
3. **T2** — Diagnose each error from T1 and map to a root cause in the project
   source (bootstrap.sh template / handoff.py generator / Release+checksums /
   index / pool / apt source wiring).
4. **T3** — Fix the source; add/adjust unit + integration tests
   (`test_bootstrap_script.py`, `test_handoff_flow.py` patterns); run the
   project suite (`PYTHONPATH=src pytest tests/`) until green.
5. **T4** — Regenerate the handoff from the existing cache+DB with the fixed
   code (install `gnupg` if needed; `handoff --system mini-pc` is idempotent;
   uses cached indexes + pool, no network for payloads).
6. **T5** — Re-run the isolated apt harness against the regenerated handoff;
   confirm `apt update` + vifm resolve/install cleanly offline.
7. **T6** — The single real end-to-end run: `sudo bash bootstrap.sh` →
   `sudo apt update` → `sudo apt install vifm`; confirm vifm installs from the
   cache.
8. **T7** — Update `tests/REGRESSION.md` baseline if the suite changed; report
   results (commit only if requested).

## Status

- [x] Read-only investigation (summary above)
- [x] T1  — isolated reproduction
- [x] T1b — network archives commented out
- [x] T2  — diagnosis (root cause: blank line + Packages-only hashes + trusted=yes)
- [x] T3  — fixes + tests green (470 passed)
- [x] T4  — handoff regenerated
- [x] T5  — isolated re-verify (zero warnings, index fetched, signature enforced)
- [x] T6  — real end-to-end run (bootstrap/apt update/install vifm all clean)
- [ ] T7  — regression baseline / report
