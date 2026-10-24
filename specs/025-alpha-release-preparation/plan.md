# Implementation Plan: Alpha-Release Preparation

**Branch**: `025-alpha-release-preparation` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/025-alpha-release-preparation/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Feature 025 turns review `review/06_alpha_finally_kimi_k3/` (ALPHA_REVIEW + CODE_REVIEW)
into the final pre-alpha preparation, per the user's decisions (spec Clarifications
Q1–Q3): fix the alpha blockers, take the code review's low-hanging fruit along, add a
graybeard `ensure-base` warning, complete the release paper-trail, and (Phase 3)
collaboratively author the tutorial track and set the version to `0.1.0-alpha`. The
carried-over low-severity backlog (L-1…L-12 / R-7) is explicitly out of scope.

The settled design decisions (each verified against the current source; line refs =
current tree):

1. **R-1 — make a standard install work (packaging).** `pycdlib` is imported at module
   top (`lib/service/iso_reader.py:12`) but is only a *dev* extra (`pyproject.toml:15`),
   so a documented `pip install .` dies on `ModuleNotFoundError` for every command. Fix:
   move `pycdlib` into runtime `dependencies` (`pyproject.toml:10`), add a declarative
   contract check that `dependencies` carries it, and record the no-extras fresh-venv
   install + built-wheel inspection as release checks (quickstart FR-001/FR-002,
   closes review 020/N-4 artifact inspection).
2. **R-2 / F5 — `download` after a local import (downloader).** `_group_current`
   (`lib/service/downloader.py:713-723`) re-checks every current row per run but has
   **no** `local` exclusion, unlike `_group_sources_to_check` (`downloader.py:691-710`),
   so a `local` row is grouped under `(<release>, local)` and every run appends a
   bogus "cached index missing for <release>/local" command error (exit 1). Fix: mirror
   the source phase — exclude `component = 'local'` in `_group_current` (local records
   are verified at import from the supplied payload; the handoff already handles deleted
   local payloads via `excluded_pool`). Add the engine-level regression test the review
   flags as the coverage hole (import local deb → `download` → zero command errors,
   record stays verified).
3. **R-3 / F8 — import supersede (import lane).** `import_local_deb`
   (`lib/service/import_local.py:296-403`) registers via `upsert_binary`
   (`lib/service/resolver.py:120-140`) whose conflict key includes the version, so
   importing a newer `local` version inserts a **second** `current` row instead of
   superseding the old one. The resolver's archive path already implements the
   supersede dance (`resolver.py:1118-1138`: `_find_current_binary` → mark old
   `status='superseded'` → upsert new). Fix: **extract that supersede primitive into a
   reusable helper** (`supersede_current_binary(conn, package, arch, version, ...)`) and
   call it from `import_local_deb` before the upsert, so old `current` local rows are
   marked `superseded`. Stale payloads are **retained** (content-addressed pool, still
   checksummed on sync) so lagging operators keep installing; no delete. Add a
   cross-version unit test.
4. **R-4 — config-driven `align-repo` baseline.** `load_baseline`
   (`lib/service/alignment.py:177-202`) hard-codes `WHERE release='trixie' AND
   component='main'`, `BASELINE_LABEL = "trixie/main…"` (`alignment.py:29`), and
   `_packages_gz_path` pins `trixie/main` (`cli/commands/align_repo.py:149-150`). Fix:
   derive release/component from `config.sources[0]` (align-repo already runs under a
   loaded config, `cli/main.py`), thread it into `load_baseline`/`_packages_gz_path`, and
   refresh `BASELINE_LABEL` from the same source. Add a config-driven variant test
   (non-trixie / first-component-not-`main`). The CWD-relative `manifests/` write and
   `--repo-dir` default are documented as run-from-project-root requirements (docs, not
   defects).
5. **R-5 — the private signing key stays off operator media by default.** The persistent
   keyring lives inside the payload tree at `{binary_cache}/handoff/.gnupg`
   (`lib/util/paths.py:55-59`, `lib/service/handoff.py:1086-1095`), so copying the cache
   ships the private key to every operator. Fix (per Q1, no migration): relocate the
   canonical keyring to `{base_dir}/.gnupg-handoff/` (`handoff_gnupg_dir`), keep
   generating once + reusing (stable trust anchor), and update `_ensure_key`. The
   handoff payload then contains only the public `offline-cache.gpg`. **Document the
   decision** (README + docs/001): a plain copy of `{base_dir}` carries the keyring;
   admins should exclude `.gnupg-handoff/` from operator media unless the operator is a
   trusted relay that must re-sign. Pre-alpha: no migration, legacy in-tree keyrings are
   unused.
6. **Graybeard `ensure-base` warning (new, Q3).** Under graybeard config
   (`config.fetch_source` AND `config.max_source_dep_depth == -1`) with the config's
   trixie sources missing any of `main`/`contrib`/`non-free`/`non-free-firmware`,
   `ensure-base` must **start its output** with a descriptive, non-blocking warning
   naming the missing components. Fix: a `config_loader` helper alongside
   `source_depth_zero_warning` (`config_loader.py:70-82`); `ensure_base()` appends it to
   `EnsureBaseStats.warnings` (first position) and the CLI emits it before the summary
   (`cli/commands/ensure_base.py`), so it is the first stdout lines and present in the
   run's structured warnings.
7. **R-6 — release paper-trail.** README figures (24 specs / 914 tests), add a `LICENSE`
   file (text confirmed by owner at planning), refresh the `AGENTS.md` SPECKIT block,
   and fix `manifests/hyprland-0.49.toml`'s `reason` to record the manual build-verified
   0.49 pin (matching docs/005) so `align-repo --verify` is self-consistent.
8. **Code-review consolidation (F1/F3/F4/F6/F7).** One shared fs/hash home
   (`lib/util/fs.py`): `file_sha256(path, errors=…)`, `atomic_write_bytes`,
   `atomic_copy(src, dst, verify=…)`; re-point the 7 call sites (D1/D2). Public
   re-exports (F3): un-underscore `_pool_prefix`, `_local_binary_entry`, `_paragraph`,
   `_write_gzip`, `_atomic_write`, `_print_error`. One `local_entries()` generator (F4)
   for the three verified-`local` paragraph loops (handoff/diff/package-stack); the
   package-stack call site **warns** instead of silently skipping unreadable payloads.
   Packaging helpers merge (F6): `_changelog_date`/`_FALLBACK_MAINTAINER`/maintainer
   resolution into one module; one `read_dsc` helper for srcgen/import. Release-hash
   one-pair helper (F7) with the decompress-on-demand variant preserved.
9. **One dependency-satisfaction predicate (F2).** `_dep_satisfied` exists in two private
   copies — `lib/service/import_local.py:114-131` and
   `cli/commands/verify.py:157-188` — with the same semantics; `resolver.select_provider`
   (`resolver.py:234-264`) is a third cousin. Fix: one shared predicate
   (`deps_satisfied(conn, name, spec)` in `lib/model/dependency.py` or a tiny
   `lib/service/depgraph.py`), used by all three; contract suite stays green.
10. **Test scaffolding (F9) + craft sweeps (F10/R-8).** Move `_insert_binary`/`_config`/
    fixture `_pool_prefix` into `tests/fixtures/db.py`; apply `Set`→`set`
    (`config_loader.py:3`), drop the dead `_file_sha256(dest)` call
    (`import_local.py:373`), rename `ImportError` → `ImportDebError`
    (`import_local.py:53`), type the resolver's internal `stats: dict` accumulator as a
    mutable dataclass (`resolver.py:447-512`).
11. **R-9 / R-10 / R-11.** Forward `fetch_fn` on the loopback/unverified index-fetch path
    (`index_loader.py:355-358`); validate non-`base_dir` path overrides as absolute after
    expansion (`config_loader.py:246-252`); add the per-command external-tool
    prerequisites matrix to `docs/001`.
12. **FR-020 verification.** Re-run and re-record the test baseline (both runners + ruff +
    contract), inspect the built wheel, and execute the FAT-USB bootstrap and graybeard
    DVD-1 physical proofs, appending results to the release notes.
13. **Phase 3 — tutorials + version bump.** Collaboratively author the tutorial track in
    working sessions (topics/scope/style agreed jointly; **new** style, distinct from
    docs/001–005; none pre-existing), then set `pyproject.toml` version to `0.1.0-alpha`
    (from `0.1.0`) and update the release-facing references.

**Constraints that shape the work**: the DB schema is frozen (no tables/columns/
migrations — R-3 uses the existing `status` values; F9 consolidates test fixtures only);
product code stays offline; both test runners + ruff + the contract suite stay green;
determinism of generated artifacts is untouched; the only dependency change is adding
`pycdlib` to runtime `dependencies` (pure-Python, tiny — the R-1 fix; the alternative
lazy-import was rejected for not closing the "documented install is broken" claim as
directly). No new CLI commands.

## Technical Context

**Language/Version**: Python 3.11+ (project pins `>=3.11`; ruff target `py311`;
constitution names 3.13 as the Trixie default — the codebase targets both). All edits are
inside the existing `src/package_mgmt/` package and `tests/`; no new top-level modules
except the shared helper homes `lib/util/fs.py` and (optionally) `lib/service/depgraph.py`
for the F2 predicate.

**Database**: SQLite 3.x — frozen schema. The only data-semantic changes:
- `binaries.status` transitions `current` → `superseded` for the prior `local` row on a
  newer-version import (R-3), mirroring the existing archive path; the supersede
  primitive moves to a shared helper.
- No new tables/columns; no migrations. `verified` lifecycle untouched.

**Primary Dependencies**: `python-debian` (existing); **`pycdlib` moves from the dev
extra into runtime `dependencies`** (R-1). Standard Debian toolset invoked as
subprocesses, unchanged (`gpgv`, `gpg`, `dpkg-deb`, `dpkg-shlibdeps`, `dpkg-source`,
`apt-get`, `readelf`, `strip` — the R-11 matrix documents them). No other dependency
change.

**Storage**:
- **Signing keyring (R-5)**: canonical home moves to `{base_dir}/.gnupg-handoff/`
  (`util/paths.handoff_gnupg_dir`); `{binary_cache}/handoff/.gnupg` is no longer
  written/used. The handoff payload carries only `offline-cache.gpg` (public).
- **Config (R-10)**: `binary_cache`, `source_cache`, `iso_dir`, `db_path`, `log_dir` are
  validated absolute after expansion; relative overrides are rejected with a clear
  `ConfigError`.
- **Docs**: `README.md` (figures + LICENSE + key-handling posture), `docs/001` (R-11
  matrix + R-5 posture + install claim), `AGENTS.md` (SPECKIT block), the tutorial track
  (Phase 3, new files), release notes (FR-020).
- **Manifest**: `manifests/hyprland-0.49.toml` `reason` updated (R-6).

**Testing**: pytest **and** `python -m unittest discover -s tests` (both runners,
Constitution) + `ruff check` + the contract suite. New/changed tests (see Project
Structure for files):
- R-1: declarative contract check (pycdlib in `dependencies`); fresh-venv no-extras
  install + wheel inspection are release checks in quickstart.
- R-2: engine-level regression (import local deb → `download` → zero command errors,
  local stays verified) in `tests/unit/test_downloader.py`; integration
  `download`-after-import in `tests/integration/test_import_deb_flow.py`.
- R-3: cross-version supersede unit test in `tests/unit/test_import_deb.py` (+ shared
  primitive test); stale-payload retention assertion.
- R-4: config-driven baseline variant in `tests/unit/test_alignment.py` (non-trixie /
  non-`main`-first config).
- R-5: keyring path outside the payload tree (`tests/unit/test_paths.py`); handoff
  payload contains no private key (`tests/unit/test_handoff.py`).
- Graybeard warning: helper unit (`tests/unit/test_config.py`), ensure-base service
  (`tests/unit/test_ensure_base.py`), and first-line CLI output
  (`tests/contract/test_cli.py`).
- F1: `tests/unit/test_fs_util.py` for the shared helpers; existing call-site tests stay
  green. F2: existing import/verify/resolver tests + a focused predicate test. F4:
  package-stack warns on unreadable payload (`tests/unit/test_package_stack.py`). F9:
  fixtures consolidation, suite stays green. R-9: `tests/unit/test_index_loader.py`.
  R-10: `tests/unit/test_config.py` (relative override rejected).

**Target Platform**: Debian Trixie LTS amd64 / x86_64. All fixes are admin-side; the
operator's install path is unchanged (the R-5 change removes the private key from the
payload the operator receives — the public key they already verify with is unchanged).

**Project Type**: CLI tool for offline Debian LTS package management — admin builds the
cache; operator consumes it offline. This feature is a hardening/readiness pass over the
existing tool, not new surface.

**Performance Goals**: no meaningful change; the fixes are small. R-2 removes a per-run
spurious error (no perf cost); the consolidation (F1/F4) removes duplicate full-file
reads (dead `_file_sha256` call, R-8). No performance regression on the 914-test
baseline's measured paths.

**Constraints**:
- **Schema freeze (spec 010/011)**: no tables/columns/migrations; `status` values and
  `component='local'` already exist.
- **Runtime dependencies**: the only change is `pycdlib` into `dependencies` (R-1,
  justified — it is already an import-time requirement; see Complexity note).
- **No network in product code**; all fixes operate on local config/DB/files.
- **Both runners + ruff + contract suite green** at every phase (Constitution gates).
- **Output contracts**: the contract suite pins CLI text/JSON shapes and exit codes —
  the graybeard warning is an **additive** first-line output (text) + structured warning,
  and the `align-repo` verdict changes only for non-baseline configs; existing contract
  tests stay green (new assertions added where a shape legitimately changes).
- **TDD**: tests written first; the 914 baseline re-recorded at the end (FR-020).

**Scale/Scope**: one dependency-line change, ~15 surgical code edits across existing
modules, one new util module (`lib/util/fs.py`), one new test-fixtures module, ~6 new/
extended unit+integration test files, 4–5 doc files, one manifest edit, the Phase-3
tutorial files, and the version bump. No new CLI commands, no schema change, no new
runtime tooling.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / Gate | Verdict | Basis |
|------------------|---------|-------|
| I. Offline-First Availability | ✅ PASS | No network call sites added or removed; the graybeard warning is a local config check; R-5 keeps the operator fully offline with the public key it already trusts. |
| II. Dependency Alignment | ✅ PASS | R-3 supersede retains stale payloads so lagging operators still resolve; R-2 keeps `local` records verified without spurious re-checks; no dependency-selection semantics change. |
| III. Binary-Source Consistency | ✅ PASS | R-3 keeps the `binary_to_source` linkage intact (payload retained, only the index `current` row superseded); no change to linkage or source verification. |
| IV. Minimal Local Cache | ✅ PASS | No cache bloat: R-3 retains superseded payloads only because they are already content-addressed and still referenced by lagging snapshots — never re-fetched, never duplicated. |
| V. Debian Policy Compliance | ✅ PASS | No packaging/artifact changes beyond moving `pycdlib` to runtime deps (pure-Python); generated-artifact gates untouched. |
| VI. Code Quality & Type Safety | ✅ PASS | The feature *improves* typing: F2 unifies three private duplicates into one typed predicate, F1 gives the fs/hash helpers one typed home, F10 types the resolver's internal stats accumulator and drops the shadowing `ImportError` name. |
| VII. Database-First Resolution | ✅ PASS | R-3 reuses the existing database-first supersede pattern (mark old row, upsert new, then copy payload + set `verified`); the DB-first discipline is preserved, not bypassed. |
| VIII. Documentation & Infrastructure Traceability | ✅ PASS | R-6 paper-trail, R-11 prerequisites matrix, R-5 key-handling decision, the tutorial track, and the `0.1.0-alpha` version bump all land in the same cycle (FR-008/FR-010/FR-021). |
| IX. Incremental Spec Scope | ⚠️ PASS w/ note | 10 user stories / 21 FRs — at the story limit and one FR over the decomposition guidance. Justified: the user explicitly requested "everything from both reviews" plus Phase 3; the work decomposes cleanly into independent slices (the 13 clusters in the Summary), which `/speckit.tasks` will sequence. No architectural bloat. |
| Schema freeze (010/011) | ✅ PASS (gate) | No `database.py` changes; `status='superseded'` and `component='local'` already exist. |
| No new runtime deps (except pycdlib) | ✅ PASS (gate) | `pycdlib` is already an import-time requirement (R-1); adding it to `dependencies` is the fix, not a new surface. See Complexity note. |
| Both test runners green | ✅ PASS (gate) | Baseline 914; TDD at every phase; baseline re-recorded (FR-020). |
| Contract suite green | ✅ PASS (gate) | `tests/contract/test_cli.py` extended additively (graybeard warning first-line + structured); existing contracts unchanged. |
| Determinism preserved | ✅ PASS (gate) | Consolidation touches file I/O, not artifact serialization; determinism tests stay green. |

**Gates: no unjustified violations. Two deviations are recorded (see Complexity
Tracking): the `pycdlib` runtime-dep change (the R-1 fix) and the 21-FR scope (explicit
user request).**

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] Commands are assigned to the correct persona (admin-only vs operator-only vs shared)
- [x] No persona-dependent behavior leaks across the offline/online boundary
- [x] Cache handoff expectations are reflected in the architecture
- [x] If this feature introduces new personas or changes existing roles, [roles.md](../memory/roles.md) has been updated accordingly

All fixes are admin-side tool behavior, docs, and packaging. The one operator-visible
change is R-5: the operator now receives a payload containing **only the public key**
(`offline-cache.gpg`) — the same key they already verify with, so `bootstrap.sh`/`apt`
behavior is unchanged (roles.md already documents the operator consumes the handoff).
The Phase-3 tutorials introduce no new persona; they are user-facing documentation
authored jointly (Administrator + agent). `roles.md` gains a one-line note recording the
operator receives the public key only (R-5 posture) and, in Phase 3, no role change.

## Project Structure

### Documentation (this feature)

```text
specs/025-alpha-release-preparation/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   ├── cli.md                # ensure-base graybeard warning, align-repo baseline, download-after-import
│   ├── config.md             # path validation, four Trixie components, graybeard detection, keyring path
│   └── packaging.md          # runtime deps (pycdlib), no-extras install + wheel, handoff keyring contract
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
pyproject.toml                        # MODIFY: move pycdlib to runtime dependencies; version -> 0.1.0-alpha (Phase 3)
README.md                             # MODIFY: figures (24 specs/914 tests), LICENSE ref, key-handling posture
LICENSE                               # NEW: project license (text confirmed by owner at planning) — R-6
AGENTS.md                             # MODIFY: SPECKIT block (this feature) — R-6 + agent-context refresh

src/package_mgmt/
├── lib/
│   ├── model/
│   │   ├── config.py                 # (no change; defaults unchanged)
│   │   └── dependency.py             # MODIFY (F2): add shared deps_satisfied(conn, name, spec) — or new depgraph.py
│   └── util/
│       ├── fs.py                     # NEW (F1): file_sha256(path, errors=...), atomic_write_bytes, atomic_copy(src,dst,verify=...)
│       └── paths.py                  # MODIFY (R-5): handoff_gnupg_dir -> {base_dir}/.gnupg-handoff/
│   └── service/
│       ├── config_loader.py          # MODIFY (graybeard warning helper; F10 Set->set; R-10 path validation)
│       ├── downloader.py             # MODIFY (R-2/F5): _group_current excludes 'local'; _file_sha256 -> fs.py
│       ├── import_local.py           # MODIFY (R-3/F8 supersede; F1 _copy_atomic/_file_sha256 -> fs.py; R-8 dead call;
│       │                             #         F10 ImportError -> ImportDebError; F2 uses shared predicate)
│       ├── resolver.py               # MODIFY (F8: extract supersede_current_binary; F2 select_provider consults
│       │                             #         shared predicate; F10 typed stats accumulator)
│       ├── handoff.py                # MODIFY (R-5 _ensure_key at new path; F1 _atomic_write/_copy_verified/_file_sha256
│       │                             #         -> fs.py; F3 public re-exports; F4 local_entries generator used by
│       │                             #         generate_handoff + generate_diff)
│       ├── package_stack.py          # MODIFY (F3 imports public names; F4 _local_packages_text -> generator + warn
│       │                             #         on unreadable payload; F7 hash-pair helper)
│       ├── index_loader.py           # MODIFY (R-9 fetch_fn on unverified path; F1 _write_index_atomic -> fs.py)
│       ├── alignment.py              # MODIFY (R-4: baseline from config.sources[0]; BASELINE_LABEL)
│       ├── srcindex.py               # MODIFY (F3 imports public names; F1 _file_meta -> fs.py; F7 hash-pair helper)
│       ├── srcgen.py                 # MODIFY (F6: changelog/maintainer/read_dsc shared helpers)
│       ├── debgen.py                 # MODIFY (F6: changelog/maintainer shared helpers)
│       └── ensure_base.py            # MODIFY (graybeard warning: config check -> stats.warnings first)
└── cli/
    ├── main.py                       # (no change; align-repo config already injected)
    └── commands/
        ├── ensure_base.py            # MODIFY (graybeard warning printed first, before the summary)
        ├── verify.py                 # MODIFY (F2: use the shared dependency-satisfaction predicate)
        ├── align_repo.py             # MODIFY (R-4: _packages_gz_path from config.sources[0])
        └── (others)                  # MODIFY (F3: _print_error -> public name across ~12 commands)

manifests/hyprland-0.49.toml          # MODIFY (R-6): reason records the manual 0.49 pin

docs/
├── 001-quickstart-howto.md           # MODIFY (R-1 install claim; R-5 key-handling posture; R-11 prereqs matrix)
├── 002-build-dep-depth.md            # (residual N-2b line is out of scope)
├── (tutorial track)                  # NEW Phase 3: collaborative tutorial files (new style, none pre-existing)
└── release-notes / notes             # MODIFY (FR-020): physical-proof results, baseline, wheel inspection

tests/
├── fixtures/
│   ├── db.py                         # NEW (F9): _insert_binary, _config, _pool_prefix consolidated
│   └── make_test_deb.py              # MODIFY (F9): import fixture _pool_prefix from db.py
├── unit/
│   ├── test_fs_util.py               # NEW (F1): shared fs/hash helpers
│   ├── test_downloader.py            # MODIFY (R-2): engine regression — local import → download, zero errors
│   ├── test_import_deb.py            # MODIFY (R-3): cross-version supersede + stale-payload retention
│   ├── test_alignment.py             # MODIFY (R-4): config-driven baseline variant
│   ├── test_paths.py                 # MODIFY (R-5): keyring path outside payload tree
│   ├── test_handoff.py               # MODIFY (R-5): payload has no private key; (F4): shared generator
│   ├── test_config.py                # MODIFY (graybeard helper; R-10 relative-path rejection)
│   ├── test_ensure_base.py           # MODIFY (graybeard warning service)
│   ├── test_package_stack.py         # MODIFY (F4): unreadable payload → warning, not silent skip
│   ├── test_index_loader.py          # MODIFY (R-9): fetch_fn forwarded on unverified path
│   └── test_dependency.py            # MODIFY (F2): shared predicate
├── integration/
│   └── test_import_deb_flow.py       # MODIFY (R-2): download after import succeeds
└── contract/
    └── test_cli.py                   # MODIFY (graybeard warning first-line text; additive)
```

**Structure Decision**: The feature is a hardening pass over the existing single-package
CLI — no new commands or modules of substance. New code is confined to two small shared
homes (`lib/util/fs.py` for F1, and the F2 predicate in `lib/model/dependency.py` or a
tiny `lib/service/depgraph.py`) that the existing modules adopt; every finding is a
surgical edit at the exact site verified in research.md. The Phase-3 tutorial files land
under `docs/` following the new, jointly-agreed style (a new style, distinct from
`docs/001`–`docs/005`).

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `pycdlib` added to runtime `dependencies` (R-1) | It is already an import-time requirement of the CLI (`iso_reader.py:12`); the documented no-extras install must run every command. The review's alternative (lazy-import + `IsoError`) was rejected because it does not directly close the "documented standard install is broken" claim the alpha promise makes, and it leaves an import-time landmine for any future top-level use. |
| Spec at 10 stories / 21 FRs (over the IX guidance) | The user explicitly chose "everything from both reviews" plus Phase 3. The clusters are independently implementable and testable (13 in the Summary); `/speckit.tasks` sequences them. Not architectural complexity. |
