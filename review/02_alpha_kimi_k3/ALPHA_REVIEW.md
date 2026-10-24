# Alpha Readiness Review — OpenFromSource (`package_mgmt`)

**Scope**: full technical + functional review ahead of the alpha release · **Method**: static deep review of the entire codebase (~6 500 LOC under `src/`, ~30 test modules), full test-suite runs under both documented runners, lint, line-by-line comparison of shipped config defaults vs. the three profile presets vs. the `ProjectConfig` dataclass, and targeted runtime reproduction of every high-severity finding. Live-mirror end-to-end runs of the three profiles were **not** executed in this round (deferred per request); §9 states exactly what remains to be validated and how.

---

## 1. Executive summary

The project is in good shape overall: architecture is coherent, the spec-driven history (14 specs, `specs/`) documents the decision trail well, and the happy paths are well tested — **505 tests pass under `pytest` with a clean `ruff` run**. The core resolution model (database-first, runtime closure never truncated, sources retained never pruned, verified index supply chain, atomic download discipline) is sound and was verified correct.

However, the review found **7 alpha-blocking issues** and a tail of medium/low defects:

| # | Blocking issue | Track |
|---|---|---|
| P0-1 | Depth-level attribution is *first-visit-wins*, not minimum graph distance — the **developer** profile silently misses sources for genuinely in-scope packages depending on `Depends` ordering | resolver |
| P0-2 | Cached virtual-package resolutions bypass the source gate — missing sources are **never repaired**, even across separate `add` runs | resolver |
| P0-3 | `Pre-Depends` is never parsed — the binary-only **regular_user** cache can miss mandatory runtime packages; the "fully installable cache" promise has a hole | resolver/index |
| P0-4 | One failed pool-copy during `handoff`/`sync` produces a **permanently unappliable operator diff**, and the delivery record is committed before the failure is reported | operator |
| P0-5 | `bootstrap.sh` reports **success** when the `handoff/pool` symlink can't be created (FAT media — the primary transport scenario); every operator install then fails | operator |
| P0-6 | The repo's documented test command (`python -m unittest discover -s tests`) is **red — 8 errors** — while `pytest` is green; root cause is a real product bug: log lines pollute the "pure JSON on stderr" error contract | contract/CI |
| P0-7 | The operator repo's `Release` has a **`Valid-Until` time-bomb** anchored to a frozen directory mtime — the offline cache hard-expires ~1 year after creation and **regeneration cannot refresh it** | operator |

The most exposed profile is **developer**: three of the blockers (P0-1, P0-2, plus the build-dep parser gaps F-11/F-12) strike its central promise — "sources and build tools within N levels, every fetched source buildable". **regular_user** is affected by the `Pre-Depends` hole and by `verify`'s version-constraint blindness (F-13). **graybeard** is mostly spared by the depth bugs (`-1` = everything in scope) but inherits the binary↔source version-skew defect (F-9/F-10) and the operator-track issues. Recommended fix order is in §10.

---

## 2. What the project is, and what the history shows

`package_mgmt` is a Python 3.13 CLI tool (stdlib + `python-debian` only) that runs on a networked **admin** machine and produces a minimal, verified, fully-offline-consumable Debian package cache for an air-gapped **operator** system: it resolves dependency closures from signed Debian indexes into SQLite (database-first), downloads/hash-verifies payloads into a `pool/`+`dists/` tree, and hands the operator a filtered, GPG-signed apt repository plus `bootstrap.sh` (specs 001–013). Spec 014 ships the three alpha profiles as data-only TOML presets under `docs/profiles/`:

| Profile | `[fetch] source` | `max_source_dep_depth` | Intended cache |
|---|---|---|---|
| `regular_user` | `false` | `0` (moot) | requested binaries + full runtime closure; no sources |
| `developer` | `true` | `1` (finite) | + sources & build tools within N runtime levels |
| `graybeard` | `true` | `-1` (unlimited) | full recursion — every binary has source + build tools; the `ensure-base` profile |

**Spec-history assessment (positive).** The decision trail is unusually reconstructable: spec 014 went through three documented design iterations (`v1` runtime-depth → `v2` build-tool-depth → `v3` source-dep-depth) with the *reasons* preserved in `plan.md`/`research.md`, and the final model is coherent (cap sources+build tools together; the runtime closure is never capped; `-1/0/N` convention replaces the old confusing "`0` = unlimited"). The constitution gates are present in every plan. The dataclass defaults, `project.defaults.toml`, and `regular_user.toml` were compared line-by-line in this review: **no drift** (`fetch_source = false`, `max_source_dep_depth = 0`, `max_build_dep_report_depth = 2`). The three profile files are genuinely complete, standalone drop-in defaults, and differ only in the intended keys.

**Where the history also shows risk.** Two findings below (P0-6's stderr contract, and several "docs claim X, code does Y" items in §6–7) share a pattern: research/plan documents assert behaviors ("regeneration refreshes `Valid-Until`", "the pool symlink is re-created by the script — survives FAT USB") that the implementation does not deliver and that no test pins. The SDD artifacts are excellent at recording *decisions*; the alpha would benefit from the same rigor applied to *contract tests* for those decisions.

### Review method and evidence standard

- Three independent deep-review passes, each covering a disjoint area (resolver/dependency model; download/verify/handoff/cache; config/CLI/database/ensure-base), cross-checked against the specs and tests.
- Every high-severity finding was **independently reproduced** in this review session (by runtime repro or direct code trace); each finding below is tagged *confirmed* (reproduced/traced this session) or *reported* (single-reviewer, code-evidenced, not re-run).
- Test suites run in this session: `pytest` (whole suite), `python -m unittest discover -s tests` (whole suite), plus focused reruns. `ruff check src tests` clean. Environment: Python 3.13.5, project venv, network to `deb.debian.org` available (used only to confirm mirror reachability, no live resolution runs).

---

## 3. Current state snapshot

| Check | Result |
|---|---|
| `pytest` full suite | **505 passed** (+41 subtests), ~97 s |
| `python -m unittest discover -s tests` (the command documented in `docs/001-quickstart-howto.md`) | **FAILED — 8 errors in the same 505 tests** (see P0-6) |
| `ruff check src tests` | clean |
| Dataclass defaults ≡ `project.defaults.toml` ≡ `docs/profiles/regular_user.toml` | identical (line-by-line) |
| Profiles 014 functional validation (live mirror, 3 profiles) | **not run this round** — see §9 |

The 8 unittest errors: `contract.test_cli.TestConfigContract.test_invalid_source_depth_json_error`, all 6 subtests of `test_invalid_values_for_both_depth_settings_exit_102`, and `unit.test_handoff.TestHandoffGenerator.test_unconfirmable_record_is_command_error` — all the same root cause (P0-6).

---

## 4. Alpha-blocking findings (P0)

### P0-1 — Depth-level attribution is first-visit-wins → developer profile silently drops in-scope sources
**Confirmed** · `src/package_mgmt/lib/service/resolver.py:510-516, 742, 793`

The resolver records a binary's *level* (runtime-graph distance from the requested set) on its **first** DFS visit and never revisits it: `_resolve_one` early-returns for any name already in `_visited` (lines 510-516), so the `_source_in_scope(level)` gate at line 742 is evaluated only with the first-visit level. Levels propagate as `level + 1` down each recursion branch (line 793). If a package is first reached through a deep chain and only later via a shallow edge, the **minimum** level — which the spec means by "within N levels of the requested set" (FR-002, US1: "the requested binary and its direct runtime dependencies have their sources") — is never applied.

**Scenario** (reproduced): `app` Depends `c1, x`; `c1 → c2 → x`; `max_source_dep_depth = 1`. Minimum level of `x` is 1, but DFS visits `x` first at level 3 → `x` gets **no source**. Listing `x, c1` instead yields a source for `x`. The resolved source set at finite depth therefore depends on **Depends-line ordering and batch ordering**, and diamond patterns (ubiquitous in Debian, e.g. `libc6`) make this a real-world occurrence, not a corner case. Affects **developer** (finite depth) only; `regular_user` (no sources) and `graybeard` (`-1`, everything in scope) are unaffected by this bug.

**Fix direction**: on re-encountering a visited package at a strictly smaller level, re-run the in-scope gate (create the source + link if now in scope) — a level-upgrade path symmetric to the existing constraint-supersede path (lines 517-547).

### P0-2 — Cached virtual resolutions bypass the source gate → permanently missing sources
**Confirmed** · `resolver.py:638-668` (early return) vs. source block at `742-771`

When a `virtual_resolutions` row exists, `_resolve_one_inner` returns the cached `resolved_id` at lines 664/668 **before** the source-creation block — unlike the real-package path, which re-executes the source block on every fresh visit. A virtually-provided binary first resolved out-of-scope (deep level, finite depth) can therefore **never** gain a source when a later edge — or a later `add` — reaches the same virtual in scope. Persistent across runs because the cached-resolution fast path fires every time. Same minimum-level class as P0-1, but not repairable by re-running `add`.

**Fix direction**: move the cached-virtual return behind the in-scope evaluation (or store/refresh per-binary level and apply the same level-upgrade path as P0-1).

### P0-3 — `Pre-Depends` is never parsed → runtime closure can be incomplete
**Confirmed** · `src/package_mgmt/lib/service/index_loader.py:45-66` (only `Depends` is read); zero occurrences of `Pre-Depends` in `src/`

Pre-Depends are *hard* runtime requirements in Debian semantics (used by `dpkg`, `bash`, `dash`, `coreutils`, `systemd`, …). The index loader reads only the `Depends` field; nothing anywhere in the codebase accounts for `Pre-Depends`. Spec 002 FR-001 promises the full transitive dependency tree and spec 014 FR-003 requires that the (uncapped) runtime tree keep every requested package installable. Any pre-dependency **not also duplicated** in `Depends` (duplication is common but not universal) will be missing from the cache, and the operator's offline install of that package breaks. This affects **all three profiles** — most acutely **regular_user**, whose cache contains nothing but the runtime closure.

**Fix direction**: parse `Pre-Depends` in `load_binary_index` and merge into the stored dependency set (marking the field of origin is optional). Add a fixture package with a Pre-Depends-only requirement to the integration suite.

### P0-4 — One failed pool-copy bricks the operator diff; delivery record committed before failure is reported
**Confirmed** · `src/package_mgmt/lib/service/handoff.py:1033-1046` + `src/package_mgmt/cli/commands/sync.py:65-102`

In `generate_diff`, the `Packages` stanza (line 1033) and the manifest line (1035-1037) are appended **before** `_copy_verified` runs (1043); on copy failure the record is merely added to `excluded_pending` — the already-appended stanza/manifest entries are **not retracted**. The shipped `diff/` then references a payload it does not contain, and the operator side fails its checksum gate ("Refusing to apply") for the *entire* diff, forever. Compounding it, `handle_sync` calls `mark_delivered(conn, system, stats.shipped_ids)` (commits) **before** checking `excluded_pending` and returning `EXIT_ERROR`: the delivery record advances for a diff the operator can never apply, and the rebuilt diff (base set now includes those records) dead-ends with "Missing intermediate sync". Recovery requires a fresh full handoff.

**Fix direction**: append stanza/manifest only after a successful copy; validate → generate → and only then mark delivered (single ordering fix in both files).

### P0-5 — `bootstrap.sh` reports success with an unreachable pool (FAT media)
**Confirmed** (script logic read; end-to-end repro by reviewer) · `handoff.py` `BOOTSTRAP_SCRIPT`: `ensure_pool_link` (~lines 117-124), `handoff_complete` (~84-96)

The offline-transport scenario per spec 012 is a USB/FAT copy of the cache. On FAT, the `handoff/pool -> ../pool` symlink is both absent and uncreatable; `ln -s` failure is ignored (no status check, no `set -e`), and `handoff_complete` checks `$cache_dir/pool` but never the `handoff/pool` link or the `Release` file. Bootstrap writes the apt source, `apt update` succeeds (indexes don't touch the pool), reports **success** — and every subsequent install fails with "File not found". The research doc's claim that the link "is re-created by the script — survives media that can't carry symlinks" is false in exactly the scenario it invokes.

**Fix direction**: check `ln` result; if linking fails, either copy/point the source entry at the real pool path or hard-fail bootstrap with a clear message; extend `handoff_complete` to verify the link resolves and `Release` exists.

### P0-6 — Documented test runner is red (8 errors); root cause is a real contract bug: non-JSON noise on stderr in JSON error paths
**Confirmed (both runners executed)** · product code: `src/package_mgmt/cli/commands/config.py:14-17` (and same pattern in `handoff.py` CLI error paths); contract: `specs/014-base-profiles/contracts/cli.md` ("JSON on stderr")

Reproduced this session: `python -m unittest discover -s tests` → `Ran 505 tests … FAILED (errors=8)`; `pytest` → all green. The failing tests assert that `--format json` error exits emit **only** the JSON payload on stderr. But handlers call `logger.error("Config load failed: …")` / `logger.warning(…)` **before** printing the JSON; with unittest's default logging (`lastResort`) that text lands on stderr ahead of the payload. Under `pytest`, a root capture handler swallows it — which is why one runner is green and the documented one is red. The product impact is real, not just test noise: any script parsing stderr of a failed `config`/`handoff`/`sync --format json` call gets non-JSON. (Demonstrated live: `handle_config('json')` on an invalid depth emits two lines, `Config load failed: …` then `{“error”: …}`.)

**Fix direction**: in JSON mode, suppress/route console log output away from stderr (or emit logs only to the log file when `--format json`); then make the test suite runner-independent (configure logging isolation in the tests) so both documented commands are green.

### P0-7 — `Valid-Until` time-bomb in the operator repo, anchored to a frozen directory mtime
**Confirmed** · `handoff.py:63, 644-658, 734-739` (`VALID_UNTIL_DAYS = 365`, `_release_epoch`)

`Date` is derived from `os.stat(config.binary_cache).st_mtime` unless `SOURCE_DATE_EPOCH` is set. The `binary/` directory mtime changes only when direct children (`dists/`, `pool/`, `handoff/`, `diff/`) appear or disappear — i.e. essentially at `init`/first `handoff` — and then stays **frozen forever**. `Valid-Until = Date + 365 d` can therefore never be advanced by regeneration, contradicting both `research.md` ("re-running the bootstrap refreshes it") and `data-model.md`. Experimentally (reviewer): an expired `Release` is a hard apt failure (`E: Release file … is expired`). Additionally, an exported `SOURCE_DATE_EPOCH` (common on reproducible-build systems) silently pins the repo to an arbitrary old date — an *instantly* dead repo.

**Fix direction**: anchor `Date` to generation time (`time.time()`) by default; keep `SOURCE_DATE_EPOCH` honored only when explicitly set *for* reproducibility; keep byte-identical regeneration as opt-in, not default.

---

## 5. High/medium findings (fix before or shortly after alpha)

### Resolution & dependency model

- **F-8 · high · confirmed** — *Satisfied alternative still leaves the failed primary's error in `stats.errors`, so `add` exits 1.* `resolver.py:635, 794-810`; exit mapping `cli/commands/add.py:141-143, 185-188`. For `ghost | realpkg` where only `realpkg` exists, the closure is fully resolved yet the run returns EXIT_ERROR and `--file` counts the request as failed. Fires whenever the first alternative is absent from the configured components (routine in real archives). The error must be retracted when an alternative satisfies the dep.
- **F-9 · medium · confirmed** — *The explicit version in the `Source:` field is discarded.* `resolver.py:740, 743-753` — `src_name, _ = parse_source_field(...)`; the source is picked as the **highest version** in the release. Under source/binary skew (source 2.0 uploaded while binaries are still `1.0-1+b1` — routine during archive transitions), binaries link to the wrong source version, violating Binary-Source Consistency (Constitution III) and making `verify`/`download` agree on the wrong artifact.
- **F-10 · medium · confirmed** — *Source lookup is keyed to the primary release, not the binary's actual release.* `resolver.py:743-748`. A binary resolved from a fallback release (e.g. trixie-backports) is searched first against the *trixie* sources → wrong-version link. Combined with F-9: every constrained/fallback resolution risks a mismatched binary↔source pair.
- **F-11 · medium · confirmed** — *Version constraints on alternatives (`a | b`) are silently dropped.* `dependency.py:77` keeps the constraint only on the first alternative; `resolver.py:459-460, 807-808` resolve alternatives with `version_constraint=None`. `foo (= 9.9) | bar (= 2.0)` with only `bar 1.0` present registers `bar 1.0` with **no error**. Versioned alternatives are common in `Build-Depends` (`debhelper-compat (= 13) | debhelper (>= 13~)`).
- **F-12 · medium · confirmed** — *Arch-restricted deps and multi-token build profiles are silently dropped by the parser.* `dependency.py:56-86`: no `[amd64]` group; only a single `<!profile>` token; unmatched entries vanish with no error and are also missing from the stored `dependencies` JSON (violating spec 002 FR-009 "store as-is"). `foo [amd64], bar` → only `bar` survives; `debhelper-compat (= 13) <!nocheck !cross>, gperf [amd64 i386], realtool` → only `realtool` survives. Target-arch entries must be kept (other-arch correctly dropped); dropping them undercuts FR-013 "every fetched source buildable".
- **F-13 · medium · confirmed (this session)** — *`verify` reports deps satisfied without checking version constraints.* `verify.py:135-157` `_dep_satisfied`: any current binary with the name answers True; the constraint is never evaluated for direct matches. `app` Depends `libfoo (>= 2.0)` with only `libfoo 1.5` → `Unresolved deps: 0`, `Status: COMPLETE`, exit 0 — while the operator's apt will refuse. The completeness gate the alpha markets is weaker than apt's install check.
- **F-14 · medium · confirmed** — *Mid-batch exception leaves stale in-memory registrations → silent closure holes.* `resolver.py:298-318` rolls back the DB on a segment exception but not `_visited`/`_batch_registered`; later segments then early-return on names that were never committed, with **no error recorded**. Narrow trigger (needs an unexpected mid-segment exception, e.g. I/O error) but a very bad failure mode. Batch state should be rebuilt or marked suspect after a rollback.
- **F-15 · medium · confirmed** — *The build-dep pass re-processes all historical sources on every `add`.* `resolver.py:407-426` (`processed_ids` is per-call). Every batch re-walks build tools of every source in the DB — O(ever-growing) on a mature cache — and, after a graybeard run, a later shallow-depth run re-resolves build tools for out-of-scope retained sources: retention (FR-014) means *keep*, not *keep extending*.

### Download / verify / operator track

- **F-16 · medium · confirmed** — *HTTP redirects are treated as successful downloads.* `downloader.py:109-115`: `_PooledOpener` only raises for `status >= 400`. A 301/302/307/308 response body is hashed and fails as "checksum mismatch" — a permanent, non-retried, wrongly-diagnosed failure for any redirecting mirror (http→https canonicalization, CDN moves). Integrity is never compromised (hash gate), availability is. Mirror `urllib`'s redirect-following.
- **F-17 · medium · confirmed** — *Verified source payloads are never rechecked.* `downloader.py:402-412, 415-529`: the binary phase re-verifies each verified record's file every run (size, then SHA-256, re-fetch + un-mark on failure); the source phase has no equivalent. A deleted/corrupt source file is silently ignored by every command (`verify` inspects no files). Related deviation: a source left unverified by one failed file re-downloads *all* its files next run (no per-file skip).
- **F-18 · medium · confirmed** — *`generate_handoff` silently drops verified records outside the configured components yet marks them delivered.* `handoff.py:1162-1199` (no `base_set` membership check, unlike `generate_diff:1009`). The operator never sees the package (FR-003 "every verified package MUST be offered"), no warning is emitted, and the delivery lie later detonates in `sync` as an "excluded delivered" hard stop. Reproducer: remove a component from config after registration.
- **F-19 · medium · confirmed (reviewer-reproduced)** — *`apply.sh` captures `apt update` stdout into `$new_files`.* `handoff.py` `APPLY_SCRIPT`: `apply_main` line ~534 vs. `apply_configure` ~465-498. Every successful apply prints `bash: [: … integer expression expected` plus raw apt output in the summary ("already applied" branch unreachable). State stays correct, but the operator-facing tool emits errors on its happy path — bad look for alpha. Tests only exercise `apply_configure`'s failure branch.
- **F-20 · medium · confirmed (this session)** — *Config loader crashes with raw tracebacks (exit 1) for mistyped non-depth values.* Only the two depth keys are type-validated. Live repro: `base_dir = 123` → `TypeError: expected str, bytes or os.PathLike` with full traceback; non-table `archive` → `AttributeError` (`config_loader.py:192`); `fetch.source = "yes"` is accepted and echoed as a *string* in JSON where the contract says boolean. The 014 contract promises structured config errors (exit 102, JSON) for config failures generally.
- **F-21 · medium · confirmed** — *Local-overlay gate silently swallows depth values when defaults lack the section.* `config_loader.py:175-181`: overrides apply only `if tup in defaults_flat`. A `project.local.toml` with `[resolution] max_source_dep_depth = -2` is **silently ignored** (loads as 0, no error) when `project.defaults.toml` has no `[resolution]` — violating the contract that a bad depth "in either project TOML" fails at load; valid overrides in absent sections are dropped too. The shipped defaults carry every section, so this bites only hand-written/profile defaults missing a section — exactly the alpha's drop-in-profile story.
- **F-22 · medium · confirmed (this session)** — *Read-only commands on a missing DB create a stray 0-byte DB and traceback.* `show.py:133,138-164`, `list.py:14`, `verify.py:18-20` don't gate through `validate_schema_or_error`. Live repro: `show vifm` before `init` → `sqlite3.OperationalError: no such table: packages` (raw traceback, exit 1) **and leaves a 0-byte `package_mgmt.db`**, after which gated commands misleadingly report the DB as "stale". Every other DB-touching command emits the clean `{"error": … run 'init' first}` + exit 101.
- **F-23 · low-medium · confirmed (this session)** — *Truncated loopback index file permanently poisons the cache.* `index_loader.py:261-263`, `resolver.py:217-226`: `ensure_indexes`' retry loop does not clean a failed destination; a truncated `Packages.gz` persists, is preferred by `resolve_index_path` forever after, and every `add` dies on `BadGzipFile` until manual repair. The remote verified path's final index write (`index_loader.py:291-293`) is likewise non-atomic (contrast the careful `.part`+rename discipline for payloads).

---

## 6. Low findings (polish / robustness backlog)

| ID | Where | Issue |
|---|---|---|
| L-1 | `commands/add.py:83-88` | `add` maps stale-schema `DatabaseError` to exit 1; every sibling command uses 101 per the contract table. **Confirmed this session.** |
| L-2 | `repository.py:6-15, 53-73` | Component priority inverted on exact version ties: `reverse=True` on (version, order) prefers non-free > contrib > main; spec 006 FR-005 implies main should win. Wrong component → wrong pool path. **Confirmed.** |
| L-3 | `resolver.py:468-483` | Build-dep fallback links any current binary by name without re-checking the entry's version constraint — wrong link *and* residual error for one dep. |
| L-4 | `resolver.py:796-805` vs `447-455` | Runtime-alternative "already satisfied" probe lacks `status='current'` (build-dep twin has it); superseded-only rows can suppress alternative resolution. |
| L-5 | `resolver.py:650-668` | Cached virtual re-resolution ignores same-release provider upgrades after index refresh (spec 006 FR-005 partial). |
| L-6 | `initializer.py:83` | `populate_provides_table` commits inside the caller's transaction (breaks ensure-base's "single transaction" claim) and counts `INSERT OR IGNORE` skips as inserted. **Confirmed.** |
| L-7 | `initializer.py:65-85`, `resolver.py:134-146` | `provides` rows from no-longer-configured releases/indexes are never swept; stale providers can be selected, then fail with "not found in index". |
| L-8 | `verify.py:181-198`, `resolver.py:63-73` | Circular report counts superseded rows; `circular` flag never reset on upsert — `verify` and `list --deps` can disagree. |
| L-9 | `commands/systems.py:26-52, 85-107` | `systems add ""` and names containing newlines are accepted (spec says "any word"); empty moniker can never be snapshotted; `IntegrityError` paths leak the connection. **Confirmed.** |
| L-10 | `commands/init.py:109-112`, `initializer.py:14-21` | Any `InitError` maps to exit 100 ("permission") even for ENOSPC etc.; unreachable mirror → `init` exits 0 "initialized" with 0 binaries cached (offline tolerance is fine, but the summary should say so). |
| L-11 | `commands/ensure_base.py:63-67` | Failed `ensure-base` on a fresh cache still creates the DB — spec 008 US1 AS2 says "exits non-zero without touching the database". |
| L-12 | `downloader.py:140-147` | Server `Retry-After` honored uncapped (86400 s stalls a run for a day); internal backoff is capped at 30 s, the override path is not. |
| L-13 | `commands/sync.py:60-64` | Zero-pending sync early-returns without regenerating, leaving a previous non-empty `diff/` on disk (spec 013 AS2 expects an *empty diff* to be produced). |
| L-14 | `handoff.py` `BOOTSTRAP_SCRIPT` `write_sources_entry` | Cache paths with spaces silently break the one-line `deb` entry (whitespace-delimited format); no guard/diagnostic — mount points like `/media/…/My USB` are plausible on operator media. |
| L-15 | `commands/add.py:16-20` vs `43-47`; `list.py:16-28`; `verify --graph` | `add "vim="` (empty pin) silently unpinned here but malformed in `--file`; `--deps X --status all` precedence undocumented; bare `verify` ignores the missing `--graph` (prints graph anyway). |
| L-16 | `downloader.py:275-284`, `tests/unit/test_downloader.py:849` | Docstring/test-name claim the opposite of the (correct, conservative) behavior re: size-match re-hash. Docs backwards, code right. |
| L-17 | `tests/unit/test_config.py:400-475` | `TestMirrorNormalization` class header missing — tests live inside `TestProfileFilesAreData` (they run; cosmetic). |
| L-18 | `resolver.py:46-51` | `_find_current_binary` uses `LIMIT 1` without `ORDER BY` — arbitrary choice if the same package+arch is current in two releases/components. |

---

## 7. Verified correct (positive confirmations)

These were specifically exercised or traced and are **not** at risk:

- **Runtime closure is never truncated by depth (FR-003)** — the depth gate wraps only the source block (`resolver.py:742`); the dependency recursion is outside it. Regression-matrix behavior at `-1/0/1/N` is pinned by unit + integration tests (`TestSourceDepth`, `test_add_file_flow`, `test_download_flow`).
- **Depth config validation contract (FR-005/FR-017)** — the bool-is-int trap is handled explicitly; `-2`, `1.5`, `true` are rejected naming the key, for **both** depth settings, with exit 102 and the JSON error payload on every config-loading command. Dataclass defaults ≡ shipped defaults ≡ regular_user profile (verified line-by-line).
- **Retention / never-prune (FR-014)** and **idempotent re-add** — `upsert_source` COALESCE, no deletes; `binary_to_source` INSERT OR IGNORE, `source_build_deps` MIN-merge; rerun-deeper/shallower tests pass.
- **Verify depth gate (FR-015)** — finite depth: unlinked binaries informational-only; `-1`: strict defect (`verify.py:32-48`). No schema change, as designed.
- **Circular dependencies** — ancestry-stack detection flags only the cycle-closing edge, diamonds not flagged, recursion provably terminates (shared `_visited` + ancestry + 100-iteration build-tool cap); `test_deep_build_tool_chain_resolves_beyond_report_depth` covers deep chains.
- **Index supply-chain security** — InRelease/Release+Release.gpg via `gpgv` against the pinned Debian keyring, SHA-256+size checked against the signed Release **before** any index write, path confinement, refusal leaves nothing behind; loopback mirrors correctly exempted for tests.
- **Downloader integrity discipline** — SHA-256 (not MD5) against cached index; `.part` → verify → `os.replace`; part files cleaned on every failure path; corrupt bytes can never be marked verified; verified **binary** records re-hashed and self-healing (`downloader.py:335-341`); traversal guard `safe_pool_path` on all pool writes.
- **Handoff repo shape/trust model** — reviewer verified empirically with apt 3.0.3: Release fields + hash blocks correct, `Packages.gz` plaintext-hash verification passes, detached armored `Release.gpg` + `[signed-by=]` works over `file://` with zero warnings; omission of `InRelease` for `file://` is correct; cache-local key is a stable trust anchor across regenerations.
- **Transactions & SQL safety** — per-name commit/rollback in the resolver, per-file commits in the downloader, all bindings parameterized; the only interpolated identifiers are allow-listed names. No injection vector found.
- **Systems/snapshot mechanics** — monotonic power-of-two bitmask with no bit reuse, ceiling enforced, snapshot-after-verify gating, moniker validated **before** any artifact is generated.
- **`ensure-base`** — idempotent re-runs, advisory-only ISO mismatch flow, corrupt-index rollback of Phase A, integration-tested end to end (`test_ensure_base_flow`).

---

## 8. Functional assessment per profile (review-based; live runs pending — see §9)

**regular_user** (`source=false`, depth moot) — *nearest to ready.* The binaries-only path is the oldest and best-tested. Its "fully installable cache" guarantee rests on: (a) the runtime closure being complete — **weakened by P0-3** (`Pre-Depends` ignored) until proven otherwise per package set; (b) `verify` being a truthful installability gate — **weakened by F-13** (version constraints not checked) and F-11/F-12 (alternatives/arch-restricted deps dropped upstream, so verify sees a pruned view). Download and handoff mechanics are sound (§7), subject to the operator-track P0-4/P0-5/P0-7.

**developer** (`source=true`, depth 1) — *most exposed.* Its distinctive promise (sources + build tools within N levels; every fetched source buildable) is directly undercut by **P0-1** (level attribution order-dependent → in-scope binaries can lack sources), **P0-2** (virtual-provider sources never repaired), **F-12** (target-arch and versioned build-dep alternatives dropped → a "buildable" source may in fact miss build tools), and **F-15** (out-of-scope work leaks back in on mixed-depth histories). The cross-depth invariants that *are* tested (closure identity, subset, retention) pass; the untested diamond/ordering case is where the hole is.

**graybeard** (`source=true`, depth -1) — *depth bugs moot, skew bugs live.* With everything in scope, P0-1/P0-2 cannot withhold sources. Remaining risks: **F-9/F-10** (wrong source version linked under archive skew/backports — a real-archive certainty over time, precisely the binNMU case), **F-15** (full-source caches make every later `add` slower), plus the shared operator-track items. `ensure-base` behaves per spec in the fixture tests.

**All profiles** share the operator-track blockers: a diff/history corrupted by P0-4 makes `sync` unusable; P0-5 turns the FAT-USB transport story into silent failure; P0-7 caps every cache's service life at ~1 year from creation regardless of regeneration.

## 9. What remains unvalidated (and the exact plan)

Deferred per request, required before tagging alpha:

1. **Live-mirror runs of all three profiles** against `deb.debian.org` trixie with a small real package (e.g. `vifm`), following `specs/014-base-profiles/quickstart.md` scenarios 1–5: identical runtime-closure sets across profiles; developer source set ⊂ graybeard source set; `verify --graph` COMPLETE in scope for each.
2. **Operator-side installability proof (offline simulation)** per profile: `handoff --system`, then `apt-get` in a scratch root (`-o Dir::State=… -o Dir::Cache=…`) against the `file://` cache with the shipped keyring; `apt-get -s install <pkg>` must succeed; repeat through a **FAT-formatted loop device** to expose P0-5 and L-14.
3. **A Pre-Depends census** on the resolved closure of the alpha package set (script: which `Pre-Depends` targets are *not* also in `Depends`/`Provides` of the set) to size P0-3 empirically before deciding whether it gates the alpha or is documented as a known limitation.
4. **Consistency sweep after fixes**: rerun **both** `pytest` and `python -m unittest discover -s tests` — the project documents the latter, so both must be green (P0-6).

## 10. Recommended fix order

**P0 — gate the alpha on these (7 items):**

1. **F-14-adjacent batch hygiene + P0-1 + P0-2 (one coherent resolver change)**: per-binary minimum-level tracking with a re-visit/upgrade path at smaller levels; route the cached-virtual return through the same in-scope evaluation; make segment rollback rebuild or poison in-memory batch state. Add diamond-pattern fixtures at depths 0/1/N/-1 asserting the *minimum*-level source set (these become the SC-006 matrix's missing rows), plus a virtual-first-deep-then-shallow cross-batch test.
2. **P0-3**: parse `Pre-Depends` into the dependency set (with census per §9.3).
3. **P0-4**: order `generate_diff` (copy → then stanza/manifest) and `handle_sync` (validate → generate → mark delivered only on clean success).
4. **P0-5**: `bootstrap.sh` must detect symlink failure and either fall back (real path in the `deb` line) or fail loudly; strengthen `handoff_complete`.
5. **P0-6**: JSON-mode log silencing + runner-independent tests; both documented runners green.
6. **P0-7**: anchor `Date` to generation time; treat `SOURCE_DATE_EPOCH` as explicit opt-in.

**P1 — fix immediately after (same release if cheap):** F-8, F-9+F-10 (source-version-aware linking: honor the `Source:` version, key lookup to the binary's release), F-11+F-12 (parser completeness: alternative constraints, arch restrictions, multi-token profiles), F-13 (constraint-aware `_dep_satisfied`), F-16 (redirects), F-19 (apply.sh stdout), F-20/F-21/F-22/F-23 (config type validation, overlay gate, DB gates, atomic index writes), F-17 (source recheck), F-18 (component-drop warning + no silent delivery), L-1.

**P2 — backlog:** remaining §6 lows.

**Effort note:** items 1–3 touch the trickiest logic (resolver recursion semantics, diff generation); they are small in LOC but need the new tests to land with them — the good news is the existing depth-matrix test scaffolding (`test_resolver_batch`, `test_ensure_base_flow` depth rows) extends naturally to diamond fixtures.

---

## Appendix A — Reproduction evidence (this session)

| Item | Reproduction |
|---|---|
| P0-6 | `.venv/bin/pytest -q` → `505 passed`; `.venv/bin/python -m unittest discover -s tests` → `Ran 505 tests … FAILED (errors=8)` |
| P0-6 root cause | scratch `project.defaults.toml` with `max_source_dep_depth = -2`; `handle_config('json')` → stderr = `Config load failed: …` **then** `{"error": …, "exit_code": 102}`; exit 102 |
| F-20 | scratch defaults with `base_dir = 123` → `TypeError` traceback (config/add/…) |
| F-22 | `show vifm` before `init` → `sqlite3.OperationalError: no such table: packages` traceback **and** a leftover 0-byte `package_mgmt.db` |
| P0-3 | `grep -r "Pre-Depends" src/` → no matches |
| P0-1/P0-2/F-8… | code traces at the cited lines (resolver reads in full); reviewer-side minimal in-memory repros for each |

## Appendix B — Files changed by this review

None. This document (`ALPHA-REVIEW.md`) is the review's only artifact.









