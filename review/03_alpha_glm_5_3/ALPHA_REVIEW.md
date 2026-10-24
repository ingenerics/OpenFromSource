# OpenFromSource — Pre-Alpha Release Review

**Scope**: Full technical + functional review of the codebase at commit `995cf3e` ("polish", clean working tree), the Spec Kit history (specs 001–014), the shipped docs, and the test suite.
**Method**: File inspection only — no project commands, workflows, or tests were executed, per the review instructions. All findings below are derived from reading source, specs, tests, and history.

---

## 1. Executive summary

The project is in good shape for an alpha: the architecture is coherent (database-first resolution, append-only binary history, derived handoff/diff artifacts), the security hardening from specs 011/012 is real (signed index verification, path-traversal guards, atomic writes), the config/profile plumbing of spec 014 is complete and well tested, and the test suite (~505 test functions across unit/integration/contract layers) is unusually thorough for this stage, including a regression matrix for the depth settings.

However, the review identified **two high-severity bugs** that directly contradict the feature contracts the alpha is supposed to showcase, plus a handful of medium robustness issues:

1. **The developer profile silently under-fetches sources** (diamond dependencies): the source-depth scope is decided by the *first* DFS encounter of a package, not its shallowest runtime level. Direct runtime dependencies of the requested packages routinely lose their source records — exactly what `max_source_dep_depth = N` promises to provide. The weakened finite-depth verify gate (FR-015) hides the gap, so the cache reports COMPLETE.
2. **Re-running `ensure-base` after `download` resets `verified` flags for the whole base set**, violating the command's own idempotency requirement (spec 008 FR / docs/003) and forcing a full re-download of thousands of packages; it also disturbs handoff/sync delivery state.

Profile verdicts (the review's primary acceptance bar):

| Profile | Packages resolve correctly? | Stable, fully installable cache? | Notes |
|---|---|---|---|
| `regular_user` | Yes (binary-only, full runtime closure) | Yes | Unnecessary Sources-index fetch (M-4); CLI robustness gaps (M-5) |
| `developer` | **Partially — source coverage is broken for diamond deps** (H-1) | Yes (installability unaffected; the *rebuild* promise is broken) | Runtime closure and buildability of what *is* fetched remain correct |
| `graybeard` | Yes (full recursion, `-1` unaffected by H-1) | Yes on a fresh cache | `ensure-base` re-run hazards H-2 / M-3 |

Recommended pre-alpha actions, in order: fix H-1 and H-2, add regression tests for both (the current suite cannot catch either — see §7), then the medium items (M-3 … M-7) and the documentation refresh (L-15).

---

## 2. System overview (as inspected)

- **CLI** (`src/package_mgmt/cli/`): `init`, `add` (positional + `--file`), `ensure-base`, `download`, `verify`, `list`, `show`, `config`, `systems`, `snapshot`, `handoff`, `sync`.
- **Engine** (`src/package_mgmt/lib/`): `PackageResolver.resolve_batch` (shared union-closure walk, constraint collection/supersede, virtual resolution, depth-limited source scope, wave-based build-dep pass), downloader (pooled keep-alive, retry/backoff, SHA-256-verified atomic writes), handoff/diff generators (signed `Release`/`Release.gpg`, filtered `Packages.gz`, `bootstrap.sh` / `apply.sh`), ISO reader (pycdlib), SQLite schema v1 (frozen by design).
- **Profiles** (spec 014): plain TOML data files in `docs/profiles/` (`regular_user`, `developer`, `graybeard`); `project.defaults.toml` ships regular_user presets (`source = false`, `max_source_dep_depth = 0`); `ProjectConfig` dataclass defaults match the shipped presets exactly; depth validation (`>= -1`, non-bool) is enforced at load; depth-0 warning implemented in `add`/`ensure-base`.

---

## 3. Bugs — high severity

### H-1. Finite source depth uses first-encounter DFS level, not the minimum level — in-scope binaries silently lose their sources

**Where**: `src/package_mgmt/lib/service/resolver.py:510-516` (early return ignores the new, shallower `level`), `resolver.py:742-771` (source registration gated on the *current call's* level), `resolver.py:793` (children recursed in `Depends` field order — depth-first).

**What happens**: `_resolve_one` returns immediately when a package is already `_visited` and its registered version satisfies the (absent) constraint — the `level` of the new encounter is never considered. Since the runtime walk is DFS in `Depends` order, a package that is reachable both *shallowly* (e.g. as a direct dependency of the requested package, level 1) and *deeper* (e.g. as a dependency of an earlier-listed sibling's subtree, level 3) is first encountered at the deeper level. At `max_source_dep_depth = 1`, the deeper encounter is out of scope, so **no source record and no `binary_to_source` link are created**; the later shallow encounter is skipped by the early return.

**Trace** (developer profile, depth 1, `fetch.source = true`):
```
app  Depends: aaa, libc6        # aaa listed before libc6
aaa  Depends: libc6
```
1. `app` resolved at level 0 → source registered. ✓
2. `aaa` resolved at level 1 → source registered. ✓
3. `aaa`'s dep `libc6` resolved at level 2 → `_source_in_scope(2)` is false → binary only, **no source**.
4. `app`'s dep `libc6` at level 1 → already visited + registered, no constraint → **early return, still no source**.

`libc6` is a *direct* runtime dependency of the requested package and per FR-004 / US1 acceptance scenario 1 ("the requested binary and its direct runtime dependencies have their sources and build-tool dependencies present") it must have a source. Debian dependency graphs are dense diamonds (everything reaches `libc6`, `libgcc-s1`, perl internals, …), so this will happen for typical packages — it is not an exotic corner case. The problem is deterministic (same walk order every run) and persistent (per-batch state is reset, but the same order recurs).

**Why verify doesn't catch it**: at a finite depth, FR-015 deliberately reports unlinked binaries as informational only ("Beyond source depth"), and in-scope missing-source detection is dropped (accepted schema-freeze trade-off). The result is a cache that reports `COMPLETE` while under-delivering the developer profile's core promise.

**Impact**: developer profile (any positive finite depth); `ensure-base` under the developer profile. `regular_user` (no sources at all) and `graybeard` (`-1`, always in scope) are unaffected.

**Suggested fix**: track the minimum observed level per name in the batch (e.g. `_batch_levels: dict[str, int]`, keep the min); when a package is re-encountered at a strictly shallower level that is in scope while it has no source yet, re-run just the source-registration block (extract `resolver.py:740-771` into a helper; the upserts are already idempotent). Alternatively, compute BFS levels for the batch closure in a post-pass before the build-dep pass and register missing in-scope sources then.

**Test gap**: all depth tests use linear chains or trees (`tests/unit/test_resolver_batch.py:490-540`, `tests/unit/test_resolver_build_deps.py:160-265`) — no test has a package reachable via two paths at different levels.

### H-2. `ensure-base` re-run resets `verified = 0` for every re-registered binary — idempotency violation with expensive consequences

**Where**: `src/package_mgmt/lib/service/resolver.py:54-73` (`upsert_binary`'s `ON CONFLICT … DO UPDATE SET … verified = 0, status = 'current'`) invoked unconditionally by `src/package_mgmt/lib/service/ensure_base.py:101-115` (`_register_binaries` registers every ISO index entry via direct `upsert_binary`).

**What happens**: on the first run this is correct (spec 008 FR-005: registration creates `verified = 0`). On any *re-run*, every ISO entry hits the `ON CONFLICT` branch and resets `verified` to 0 — including binaries already downloaded and verified. Consequences:

- `download` re-downloads everything: the pending set is `status = 'current' AND verified = 0`, and the downloader re-fetches unverified records unconditionally (see M-7 — no pool-presence check for unverified records). For a DVD base set (~4,300 binaries, several GB) this is a full forced re-download.
- `handoff` regeneration excludes the reset binaries (`_verified_records` requires `verified = 1`), silently shrinking the operator's apt source until the re-download completes.
- Delivery state degrades: `snapshot`/`sync` pending logic requires `verified = 1`, so reset binaries are neither pending nor delivered — they are in limbo.

**Contradicts**: spec 008 ("idempotent re-runs, safe interruption"; "the flag is only set when a file is actually verified/downloaded at a later stage — never at registration" — re-registration is not a download event) and `docs/003-ensure-base.md:88` ("Re-runs: the run is idempotent — re-running converges to the same state"). Note that plain `add` does **not** have this problem (same-version re-resolution takes the `binary_id = existing_id` path at `resolver.py:716-717` and never calls `upsert_binary`), so this is specific to ensure-base's bulk Phase A upserts.

**Suggested fix**: in `_register_binaries`, preserve the existing `verified` value on conflict (e.g. a dedicated registration statement with `verified = binaries.verified`, or `COALESCE`-style guard), keeping the resolver's `upsert_binary` semantics (reset on the supersede/restore path) untouched.

**Test gap**: `test_rerun_is_idempotent` (`tests/unit/test_ensure_base.py:263-273`) compares two runs in a database where nothing was ever verified; no test sets `verified = 1` (as `download` would) before the re-run.

---

## 4. Bugs — medium severity

### M-3. `ensure-base` Phase A can create duplicate `current` rows, breaking the one-current invariant

`_register_binaries` (`ensure_base.py:101-115`) inserts ISO binaries directly, bypassing the supersede logic that `_resolve_one_inner` applies (`resolver.py:713-733`). If the cache already holds a **different** current version of a package (e.g. resolved from the network archive after the DVD was built), Phase A inserts the ISO version as a second `current` row — the UNIQUE constraint only covers `(package_name, version, architecture, release, component)`. Spec 005's invariant "at most one current per (package_name, architecture)" is violated; `_find_current_binary` (`resolver.py:46-51`) uses `fetchone()` with no `ORDER BY`, so Phase B's supersede behavior is nondeterministic. In the likely outcome, the stale ISO row stays current forever: it enters the download pending set (redundant download), inflates verify counts, and is emitted as a second `Packages.gz` paragraph in the handoff. Realistic trigger: `add vim` (archive, newer) … `download` … later `ensure-base` with an older DVD.

### M-4. Sources indexes are fetched/required even when `[fetch] source = false` (regular_user)

`PackageResolver.ensure_indexes` (`resolver.py:232-246`) fetches and loads `Sources.gz|.xz` for every configured source **unconditionally** — no `fetch_source` check. `init` behaves oppositely (`cli/commands/init.py:70-73` skips the Sources fetch when `fetch_source = false`). Net effect for the regular_user profile: the first `add` downloads the full source indexes (tens of MB) that the profile is designed to avoid, and `add` hard-fails with `RuntimeError` if the source index is unreachable even though nothing but binaries is needed. Contradicts the profile's stated intent and creates an unnecessary network dependency.

### M-5. `verify`, `list`, `show` crash with a traceback on a missing/stale database

`handle_verify_graph` (`cli/commands/verify.py:18-20`), `handle_list_deps` (`cli/commands/list.py:14,31`), and `handle_show` (`cli/commands/show.py:107+`) connect and query without the schema gate that every other DB-touching command uses (`db_init.validate_schema_or_error`, used by `download`, `snapshot`, `handoff`, `sync`, `systems`). Running any of them before `init` (a likely first user action after `config`) raises an uncaught `sqlite3.OperationalError: no such table: packages` — and `sqlite3.connect` first **creates an empty DB file** as a side effect, which then confuses the next real command (`initialize_database` sees `user_version = 0` and builds a schema over the empty file — harmless but surprising). This should be the standard JSON error + exit 101 the other commands emit.

### M-6. The runtime verify check is version-blind for direct binary dependencies

`_dep_satisfied` (`verify.py:135-157`) returns True for any current binary matching the dependency *name* — the version constraint is only evaluated on the `provides` path. Yet the reported message says "no binary record for matching version", and the resolver's documented "sequential last-writer semantics" (`resolver.py:529-543`, mutually exclusive constraint fallback) can genuinely leave the cache with a current version that violates an earlier requester's constraint. In that state `verify --graph` reports COMPLETE, so the Constitution II gate (dependency alignment) has a hole exactly where the resolver's fallback can create the problem. Fix: check `satisfies_constraint(version, spec.version_constraint)` on the direct path (and for alternatives, per L-9, their own constraints).

### M-7. `download` re-fetches files that are already present and valid whenever `verified = 0`

Binary phase (`downloader.py:335-341`): the size+hash skip only applies to `verified = 1` records; unverified records are re-downloaded even when the pool file already exists and matches. Source phase (`downloader.py:454-483`): there is no existence check at all — an interrupted source download re-fetches every file of the pending sources. Both converge correctly, but combined with H-2 this turns an ensure-base re-run into a guaranteed full re-download. A cheap `exists + size + sha256` short-circuit for unverified records would make re-runs and interrupted runs cheap.

---

## 5. Bugs — low severity / polish

| ID | Finding | Where |
|---|---|---|
| L-8 | `verify --graph` flag is decorative — `main()` runs the graph verification regardless; bare `verify` behaves identically (contract says `verify --graph`). Also `verify --package X` bypasses the report-depth filter entirely (`if package:` branch has no depth clause). | `cli/main.py:120-123,208-215`; `verify.py:96-100` |
| L-9 | Version constraints on *alternatives* are dropped at parse (`foo (>= 1) \| bar (>= 2)` keeps only `bar`'s name); the resolver tries alternatives unconstrained and verify checks them unconstrained. | `model/dependency.py:63-86`; `resolver.py:807-810`; `verify.py:284-294` |
| L-10 | `Unresolved build deps` / `Missing build tools` lists are not filtered by `max_build_dep_report_depth` (only the runtime-dep check of build tools is). A strict reading of FR-017 ("`0` = no build-tool levels reported") is not met. | `verify.py:30,240-301` |
| L-11 | Unknown config keys are silently ignored — a typo like `max_source_deph` or `[fetch] sources = true` silently falls back to defaults (regular_user). Rejecting unknown keys in known sections would be friendlier for an alpha. | `config_loader.py:185-189` |
| L-12 | Build-profile annotations (`<pkg>`, `<!nocheck>`) are silently dropped by the dependency regex; `BuildDependencySpec.profiles` is never populated. Spec 004's data model said profiles are "stored as raw annotations". | `model/dependency.py:66-77,89-96` |
| L-13 | `packages.status` never transitions (always `pending`; nothing updates it), and `requested_version` stores the *resolved* version for every transitive dep, so `show`/`list` present misleading "requested" data. | `resolver.py:35-43,705` |
| L-14 | Spec/schema drift, accepted but undocumented: spec 001's data model requires `verified` columns on `packages` and `binary_to_source` (absent from `model/database.py`); spec 007 US8's *intended* `DELETE`-then-INSERT provides rebuild and the orphaned-`virtual_resolutions` sweep are not implemented (`populate_provides_table` is `INSERT OR IGNORE`; there is no `DELETE FROM` anywhere in `src/`), while `docs/001-quickstart-howto.md:176-179` still promises both. Practical risk is low (snapshot-in-time indexes), but either the code or the docs/specs should be reconciled. | `service/initializer.py:65-84`; `model/database.py` |
| L-15 | Documentation staleness (details in §8). | `docs/001`, `docs/002`, `docs/003`, `AGENTS.md`, `tests/REGRESSION.md` |
| L-16 | `validate_absolute`'s `ValueError` is dead code (`os.path.abspath` always returns an absolute path); a relative `base_dir` is silently reinterpreted relative to the CWD instead of being rejected. | `util/paths.py:8-12` |

---

## 6. Profile-by-profile functional assessment

### 6.1 regular_user (`docs/profiles/regular_user.toml`)

- **Config**: `[fetch] source = false`, `max_source_dep_depth = 0`; matches `project.defaults.toml` and the `ProjectConfig` dataclass defaults — one-default story holds (verified by `tests/unit/test_config.py:248-332`).
- **Resolution**: runtime closure only; build-dep pass skipped (`resolve_batch` gates on `fetch_source`); no warning (correct — FR-016 exempts `source = false`).
- **Download**: binaries only; source phase short-circuits.
- **Verify**: finite depth → unlinked binaries informational; COMPLETE decided by runtime closure — correct per FR-015.
- **Installability**: full runtime closure in the pool, handoff serves it — **PASS**.
- **Caveats**: M-4 (Sources index still fetched on first `add`), M-5 (CLI robustness), and the wasted-bandwidth concern is ironic for the "small cache" profile.

### 6.2 developer (`docs/profiles/developer.toml`, depth 1)

- **Config**: `source = true`, `max_source_dep_depth = 1`, warning/display/validation all correct.
- **Resolution**: runtime closure complete at every depth (regression matrix `test_resolver_batch.py:524-532` proves identity across depths); build tools placed at `depth + 1` so they resolve as binaries without their own sources (documented Constitution III exception, `resolver.py:407-419`); every fetched source's build tools are resolved (FR-013 holds for what is fetched).
- **Installability**: **PASS** — the runtime binary closure is never truncated.
- **Rebuild promise**: **BROKEN by H-1** — direct dependencies reached deeper-first lose their sources; the cache under-delivers silently (verify says COMPLETE). This is the profile's headline feature.

### 6.3 graybeard (`docs/profiles/graybeard.toml`, depth −1)

- **Config**: `source = true`, `max_source_dep_depth = -1` — identical to pre-014 behavior (regression-preserving; tested).
- **Resolution**: full recursion; build tools in scope at level 0 so the toolchain recursion runs to convergence (100-wave safety cap).
- **Installability**: **PASS** on a fresh cache (subject to the known real-archive resolution errors recorded in `specs/008-ensure-base-packages/validation.md` — virtual packages without providers, unsatisfiable constraints; documented as archive reality).
- **Caveats**: H-2 (ensure-base re-run after download resets the base set's verified flags) and M-3 (duplicate current rows when the ISO is older than already-resolved versions) both bite the primary graybeard workflow: `ensure-base` → `download` → `handoff`, then any later ensure-base re-run.

### 6.4 Cross-profile mechanics that are correct

- Depth convention (`-1`/`0`/`N`) uniformly validated and displayed (FR-001/002/005/010, FR-017); `config` shows both settings.
- The depth-0 warning (FR-016) fires in `add` (`cli/commands/add.py:105-107`) and `ensure-base` (`lib/service/ensure_base.py:199-201`), exit code unaffected.
- Retention (FR-014): shallower re-runs never prune (`test_rerun_shallower_retains_fetched_sources`).
- `project.local.toml` overlay still applies on top of any profile (the local file in the repo adds `contrib`/`non-free`/`trixie-backports` on top of the shipped main-only defaults — intended usage).
- Strict-subset property (FR-011) holds in the tested scenarios (finite ⊂ unlimited) — though with H-1 the finite set is smaller than it should be, the subset relation itself is preserved.

---

## 7. Test coverage assessment

**Strengths** (inspected, not executed):

- Config: full depth validation matrix, profile-file validity/distinguishing settings, override recording, mirror normalization (all in `test_config.py`).
- Resolver: batch-vs-sequential parity, constraint supersede/re-walk, last-writer fallback, interrupted-run convergence, link idempotency, depth matrix (Setting A), depth-equal runtime closure.
- Verify: current-only counting, superseded handling, build-tool sections, FR-017 report-depth behavior, FR-015 depth gate at 0/N/-1.
- Downloader: retry/backoff, politeness, checksum failures, per-phase summaries (per spec 011's Phase-7 audit fixes).
- Contract layer (`tests/contract/test_cli.py`, 58 tests) pins CLI output shapes.
- End-to-end bootstrap debugging was done against real apt (debug/01_bootstrap) with the Release-hash/signed-by fixes landed (`925c1c2`).

**Gaps relevant to the findings above**:

1. **No diamond/multi-path graph test for source depth** (H-1) — every depth fixture is a chain or tree.
2. **No test sets `verified = 1` before an `ensure-base` re-run** (H-2) — idempotency is only checked in an all-unverified state.
3. **No test for ensure-base over a cache holding a different current version** (M-3).
4. No test that `verify`/`list`/`show` fail gracefully on a missing DB (M-5).
5. No test that a *direct* dependency version-constraint violation is reported by verify (M-6) — only virtual/provider constraint cases are covered.
6. `tests/REGRESSION.md` still records the pre-014 baseline (458 passed); the debug session measured 470+14 subtests after the bootstrap fixes, and 014 added more. The baseline should be refreshed so the next feature has an accurate reference.

---

## 8. Documentation issues

| Doc | Problem |
|---|---|
| `docs/001-quickstart-howto.md:91-102,115,209` | Shows `[fetch] source = true` as the default configuration and calls source fetching "(default)" — the shipped default is now `source = false` (regular_user presets). A novice following the quickstart will believe sources are fetched. |
| `docs/001-quickstart-howto.md:555-559` | "Verify the cache: `package_mgmt verify` validates the resolved tree **and pool integrity**" — verify never touches the pool. |
| `docs/001-quickstart-howto.md:176-179` | Claims init "repopulates the provides snapshot … and sweeps orphaned virtual resolutions" — neither is implemented (see L-14). |
| `docs/002-build-dep-depth.md:25,123` | Still documents the OLD convention ("Set to 0 to report all levels"); spec 014 redefined `0` = none, `-1` = all. The code follows the new convention; the doc contradicts both the code and FR-017. |
| `docs/003-ensure-base.md:66` | "nothing sets `verified = 1` today" — stale since spec 011 (`download` sets it). |
| `AGENTS.md` | SpeCKIT block says "Run `/speckit.tasks` to generate the task breakdown" — all 40 tasks are already checked. |
| `tests/REGRESSION.md` | Stale baseline (see §7.6). |

The profile TOMLs themselves are accurate and self-documenting (intended use, drift rationale, grow-deeper direction, scale warning) — no issues found there.

---

## 9. Accepted trade-offs / known limitations (verified as intentional, no action needed)

These are documented decisions that the review confirms are implemented as specified — listed so they are not re-reported as bugs:

- In-scope missing-source detection deliberately dropped at finite depths (FR-015 schema-freeze trade-off) — *but note it is what makes H-1 invisible; consider revisiting after H-1 is fixed*.
- Build-tool binaries get no sources at finite depth (scoped Constitution III exception).
- Sequential last-writer semantics for mutually exclusive constraints (real-archive necessity) — *pairs with M-6*.
- Circular dependencies informational only (spec 007 T039 amendment).
- Single architecture (amd64 hardcoded in index paths), no index refresh/incremental updates (snapshot-in-time design), schema frozen at v1 with delete-and-rebuild as the dev migration path.
- Real-archive resolution errors on ensure-base (validation.md) — archive reality, reported per-name.
- Operator-side deep verification deferred (structural preflight + manifest hashes only); diff reverting, pruning, `deb-src`, HTTP-range resume all on the follow-up backlog (specs 012/013).

---

## 10. Performance observations (informational)

- Every `add` re-processes **all** sources with `build_depends` in the DB (`resolver.py:423-426`): on a large graybeard cache (~25k sources) each subsequent small `add` re-walks the full build-dep set (mostly early-returns + link upserts). Bounded and correct, but worth an eventual `processed`/epoch optimization.
- `list --deps` / `show --deps` build trees recursively with per-node queries — thousands of nodes on a big cache will be slow but functional.
- Merged in-memory indexes (~110k entries for trixie + backports) are fine.
- Per-segment stats double-count on constraint re-walks, and the build-dep pass's counters feed every segment — intentional (sequential parity, documented in `resolve_batch`'s docstring); only affects reported numbers, not state.

---

## 11. Recommended actions (prioritized)

1. **Fix H-1** (minimum-level source scoping) + add a diamond-graph regression test at depth 1 and 2.
2. **Fix H-2** (preserve `verified` on ensure-base re-registration) + test with pre-verified rows.
3. **Fix M-3** (route Phase A registration through supersede-aware logic or deduplicate currents) + test with a pre-existing different current version.
4. **Fix M-4** (skip Sources index fetch/load when `fetch_source = false`).
5. **Fix M-5** (schema gate + graceful errors for `verify`/`list`/`show`).
6. **Fix M-6** (version-aware direct satisfaction in verify) — this restores the Constitution II gate.
7. **Fix M-7** (skip-if-present-and-matching for unverified records) — cheap and high operational value.
8. Refresh documentation (`docs/001`, `002`, `003`), `AGENTS.md`, `tests/REGRESSION.md`; reconcile spec 007 US8 / spec 001 schema notes with reality (or land the intended provides rebuild + orphan sweep).
9. Address L-8 … L-16 as polish (unknown-key rejection and the alternatives-constraint drop are the most user-visible of them).

---

*Review produced by static inspection of commit `995cf3e`. No commands, workflows, or tests were executed.*
