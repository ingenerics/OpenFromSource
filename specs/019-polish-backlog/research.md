# Research: Polish Backlog (Merged Low Findings)

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

Scope source: `review/04_alpha_summary/03_polish_backlog_docs.md` §1 — the merged low findings `P-1…P-23`,
deduplicated between the two alpha reviews. An audit of the current tree (this branch, after 015-018 landed)
confirmed **22 of 23 are still present**; P-8 (unknown config keys silently ignored) is already fixed by 018's
F-20 (`config_loader.py:220-229`) and is a regression-check only. No clarifications pending — the spec records
two plan-time decisions (the `verify --graph` removal and the status-lifecycle semantics); both are resolved
below. All decisions are verified against the current source.

---

## Decision 1 — `add` maps a stale schema to the contract's 101; `init` splits permission vs other failures and flags offline mode; `ensure-base` defers DB creation until after discovery (P-1, P-4, P-5)

**Decision**:
1. **P-1** — `handle_add` (`cli/commands/add.py:83-88`) wraps `sqlite3.connect` + `initialize_database` in a
   broad `except Exception → EXIT_ERROR (1)`. `initialize_database` raises `DatabaseError` on a schema mismatch;
   every sibling command routes that through `validate_schema_or_error` → **101**. Fix: catch `DatabaseError`
   separately and return `EXIT_SCHEMA_MISMATCH (101)`; keep the broad handler for real bugs (exit 1).
2. **P-4a** — `handle_init` (`cli/commands/init.py:109-112`) maps **every** `InitError` to `EXIT_PERMISSION
   (100)`. `_ensure_dir` (`lib/service/initializer.py:14-21`) raises `InitError` for both `PermissionError` and
   any other `OSError` (ENOSPC etc.). Fix: `InitError` gains a `permission: bool = False` attribute — set `True`
   only in the `except PermissionError` branch. `init.py` maps `e.permission → 100`, else → `EXIT_ERROR (1)`.
3. **P-4b** — the offline-mirror summary (`init.py:135-148`) hard-codes `status = "initialized"`. Fix: track
   whether any mirror was reachable — `_fetch_per_source` already reports per-source binary counts; when the
   merged total is 0 binaries (all mirrors unreachable; fetch failures are already `logger.warning`ed at
   `_fetch_with_fallback:41`), print `Status: initialized (no mirror reachable — offline mode, 0 binaries
   cached)` on the summary line. Exit stays 0 (offline tolerance is intended — the review only asks the summary
   to "say so").
4. **P-5** — `handle_ensure_base` (`cli/commands/ensure_base.py:64-68`) calls `initialize_database(config.db_path)`
   **before** `ensure_base(...)`, so a failed run (no image / corrupt index / populated cache) leaves a fresh DB
   file behind — violating spec 008 US1 AS2 ("exits non-zero without touching the database"). Also
   `lib/service/ensure_base.py:193` opens the connection before the failure-prone image work, which itself
   creates a 0-byte file. Fix: **move DB creation into the service, after discovery**: reorder `ensure_base` to
   1) `find_installer_image` (no DB), 2) `_cleanup_stale_temp_dirs` + `mkdtemp` (no DB), 3) open `IsoImage` and
   load/validate every component index (`_load_image_indexes`, no DB), 4) only then `if os.path.exists(db_path):
   _assert_fresh_cache(...)`; `initialize_database(db_path)`; `conn = sqlite3.connect(db_path)`; begin the
   transaction. The CLI drops its up-front `initialize_database` call and adds `except DatabaseError → 101`
   (schema init inside the service can still raise it). On a fresh cache with any discovery failure, no DB file
   is ever created.

**Rationale**: These three are the externally visible contract violations — automation branches on exit codes,
and a stray DB file poisons later diagnostics. P-5's reorder is the only placement that satisfies "no DB on
failure" while keeping the single-transaction guarantee (`ensure_base.py:200-205`): the transaction can only
begin once the DB is known to exist, and discovery is the part that fails on the documented fresh-cache paths
(no/multiple/corrupt image). The `InitError.permission` flag is the minimal typed signal — no exception-type
proliferation, and `init.py` keeps a single `except InitError` with an attribute branch.

**Alternatives considered**:
- *P-4: separate `PermissionInitError` subtype* — rejected: a boolean attribute on the existing `InitError` is
  less code and keeps the CLI catch surface unchanged.
- *P-4: let `PermissionError` propagate and catch it in `init.py`* — rejected: `_ensure_dir` already normalizes
  to `InitError`; re-raising the raw type would leak a stdlib type into the CLI contract.
- *P-5: pre-flight `find_installer_image` in the CLI before `initialize_database`* — rejected: the corrupt-index
  case is also a discovery failure and lives inside `ensure_base`; the CLI cannot cheaply replicate it. Owning
  DB creation inside the service after discovery is the robust single placement.
- *P-5: create the DB inside the `BEGIN` transaction only* — equivalent outcome; folding `initialize_database`
  before `BEGIN` keeps the existing "single transaction" comment accurate (schema DDL is not part of Phase A's
  rollback scope).

---

## Decision 2 — `verify --graph` is removed (bare `verify` is the single graph command); `--package` respects the depth filter; `systems add` validates monikers and closes the connection; empty pins and `--deps`/`--status` precedence are consistent (P-2, P-3, P-7)

**Decision**:
1. **P-2a** — `main.py:120-123` registers `--graph` and `main.py:208-215` ignores it (`handle_verify_graph`
   takes no graph arg). Per the spec/Clarifications decision, the flag is **removed** (registration + help text);
   bare `verify` is the single dependency-graph verification command — which is what it already does today. The
   depth gate (014 FR-015) becomes default `verify` behavior. Doc/contract references to `verify --graph` (014
   contract table, 018 quickstart, CLI `--help`) are updated in the same cycle (Constitution VIII);
   `tests/contract/test_cli.py` invocations of `verify --graph` are updated to `verify`.
2. **P-2b** — `_find_unresolved_deps` (`verify.py:97-137`) applies the `max_depth` clause only in the `else`
   branch (line 116-124); the `if package:` branch (101-105) selects without the depth clause. Fix: add the same
   `LEFT JOIN source_build_deps … COALESCE(sbd.min_depth, 0) <= ?` filter to the `if package:` branch, so
   `verify --package X` respects the report depth exactly like the full-cache path.
3. **P-3a** — `handle_systems_add` (`systems.py:26-52`) does no name validation. The spec says a moniker is "any
   word". Fix: reject an empty name, a name containing whitespace, or a name containing a newline/control char
   with a clean `_print_error` (exit 1), no INSERT. (`bit` assignment is untouched.)
4. **P-3b** — the `except sqlite3.IntegrityError` path (43-45) returns without `conn.close()` — the connection
   leaks. Fix: wrap the body in `try/finally` so `conn.close()` runs on every path (success, IntegrityError,
   generic `sqlite3.Error`). The same `try/finally` pattern applies to `handle_systems_add` (the finding's scope);
   rename/remove already close on their error paths but get the same hardening for consistency.
5. **P-7a** — `_parse_pkg_spec("vim=")` returns `("vim", "")` (`add.py:16-20`), and `add.py:115` builds
   `PackageRequest("vim", "")` — `""` is falsy in `_resolve_one_inner` (`resolver.py:835`), so it resolves
   unpinned, while the `--file` path rejects the same line at `add.py:43-46`. Fix: in the positional-argument
   path, treat `name=` (empty pin) as malformed exactly like `--file` — reject with a clean error listing the
   offending argument.
6. **P-7b** — `main.py:199-203` silently gives `--status` precedence (returns the status table, ignoring
   `--deps`) with no documented rule. Per the Clarifications session (spec Session), the resolved
   behavior is **mutual exclusion**: passing both `--deps X` and `--status all` is a usage error (clean error,
   exit 1); each flag alone works as before. Fix: add the both-given guard in `main.py` (before
   `handle_list_deps`) and document the mutual exclusion in the CLI contract (`contracts/cli.md`) + `--help`.
   This deliberately supersedes the current silent status-wins behavior.

**Rationale**: Removing a decorative flag is the cleanest fix and matches the user's decision; inventing a
bare-vs-`--graph` behavioral split would be a new, arbitrary contract. The remaining items are pure parity bugs
(positional vs `--file` parsing should agree; `--package` should obey the same filter), resource hygiene
(connection leak), and an explicit user decision (the `list` flags are mutually exclusive rather than silently
precedence-resolved).

**Alternatives considered**:
- *P-2a: make `--graph` gate the depth check (bare verify = basic checks only)* — rejected by the user: no added
  value in a second invocation; removal is simpler and honest.
- *P-3: shell-style `\w+` regex for the moniker* — rejected: "any word" per the spec is a word with no
  whitespace; reject empty/whitespace/newline/control explicitly rather than guess a character class.
- *P-7a: normalize `""` to `None` in both paths* — rejected: the `--file` path already treats it as malformed
  and the spec asks for consistent rejection, not silent leniency.

---

## Decision 3 — zero-pending `sync` regenerates an empty diff; `verify`'s build-dep report honors the depth (P-6, P-18)

**Decision**:
1. **P-6** — `handle_sync` (`sync.py:56-61`) early-returns on `pending_count(conn, bit) == 0` before
   `generate_diff`, leaving a previous non-empty `diff/` on disk (spec 013 AS2 expects an *empty diff*). Fix:
   on the zero-pending branch, still produce/refresh the diff artifacts as empty — regenerate `diff/` with no new
   packages (empty `Packages.gz`/`Release` + `apply.sh` with a `New files: 0` outcome), so the directory is never
   stale. This keeps the documented "idempotent empty diff" contract (013 FR-006) without changing delivery
   state (no `mark_delivered`, snapshot unchanged).
2. **P-18** — `_find_build_dep_issues(conn, package)` (`verify.py:254-322`) never receives a depth and never
   filters; only the runtime-dep check does (014 FR-017: `0` = no build-tool levels reported). Fix: thread
   `max_report_depth` through `handle_verify_graph → _find_build_dep_issues` and filter both the
   "Unresolved build deps" and "Missing build tools" sources by `min(build_dep_depth) <= max_report_depth`
   (mirroring `_find_unresolved_deps`'s `COALESCE(sbd.min_depth, 0) <= ?` pattern over `source_build_deps`), in
   both the full-cache and `--package` paths.

**Rationale**: Both are "report/output honesty" fixes. The zero-pending case is a well-specified contract
(013 AS2); regenerating an empty diff is the only way to guarantee a stale `diff/` cannot ship old packages. The
build-dep depth filter is a strict reading of a written FR (014 FR-017) that the code already implements for the
runtime side — the build-dep side simply forgot to take the parameter.

**Alternatives considered**:
- *P-6: delete `diff/` entirely on zero pending* — rejected: spec 013 expects an *empty diff* (artifacts present,
  zero packages), not an absent directory.
- *P-18: filter only the count lines, not the lists* — rejected: FR-017 is about what is *reported*; leaving
  items in the list while suppressing the count would be internally inconsistent.

---

## Decision 4 — component ties resolve to `main`; current-binary lookup is deterministic; build-dep fallback, runtime-alternative probe, and cached-virtual refresh honor constraints (P-9, P-10, P-11, P-12, P-16)

**Decision**:
1. **P-9** — `_best_entry` (`repository.py:85-88`) and `lookup_source`/`lookup_source_by_source` (57-58, 65-66)
   sort candidates by `(version, _component_order)` with `reverse=True`; `_COMPONENT_PRIORITY` maps `main=0,
   contrib=1, non-free=2`, so on an exact-version tie the **highest** order (non-free) wins — the inverse of spec
   006 FR-005. Fix: sort by `(version, -_component_order)` with `reverse=True` (equivalently `(version,
   _component_order)` ascending with a stable `main` first), so `main` wins on exact-version ties; the pool path
   then reflects `main`.
2. **P-16** — `_find_current_binary` (`resolver.py:103-108`) is `SELECT … LIMIT 1` with no ordering, so a package
   current in two releases/components picks an arbitrary row. Fix: add `ORDER BY id` (the insertion order) so the
   selection is deterministic; callers that need the latest release/component are unaffected (this is a
   determinism fix, not a semantics change).
3. **P-10** — the build-dep fallback (`resolver.py:706-722`) links any `status='current'` binary (or virtual
   resolution) by name without re-checking the entry's version constraint, and the error pushed by the failed
   `_resolve_one` (`resolver.py:780-782`) is never retracted. Fix: when a fallback candidate is found, verify
   `satisfies_constraint(candidate_version, entry.get("version_constraint"))` (and for a virtual, the provider's
   version) before linking; on a valid link, retract the residual error (mirroring the alternative-group
   retraction at 1043/1046) so the entry is not both "linked" and "unresolved".
4. **P-11** — the runtime-alternative "already satisfied" probe (`resolver.py:1023-1031`) uses
   `SELECT 1 FROM binaries WHERE package_name = ? LIMIT 1` (no status filter) and an un-filtered provides probe;
   the build-dep twin (684-694) has `AND status = 'current'`. Fix: add `AND status = 'current'` to both the
   binaries probe and the provides-join probe at 1023-1031, so superseded-only rows never suppress alternative
   resolution.
5. **P-12** — the cached-virtual path re-selects only when `cached_rel != primary_source` (`resolver.py:891-911`);
   the same-release branch (`912-915`) returns the cached id unconditionally, ignoring a refreshed-index provider
   upgrade in the primary source (spec 006 FR-005 partial). Fix: in the same-release `cached_usable` branch, still
   run `select_provider(query_provides(conn, name), …, primary_source, version_constraint, index)` and compare —
   if the fresh candidate is a higher version of the same provider (or a different higher-priority provider),
   re-resolve (clear `resolved_id`); else reuse the cache (with the existing source-repair call).

**Rationale**: These are correctness fixes for the resolver's *selection* and *linking* — the wrong component on
a tie means the wrong pool path, an arbitrary current binary means non-deterministic rebuilds, and a constraint-
blind fallback link or a stale cached virtual resolution puts the wrong entry (or a residual error) into the
cache. 006 FR-005 is the written authority for both the component tie-break and the virtual refresh.

**Alternatives considered**:
- *P-9: change `_COMPONENT_PRIORITY` values* — rejected: the map is also a human-readable ordering elsewhere;
  negating in the sort key is local and explicit.
- *P-12: always re-resolve cached virtuals on every run* — rejected: that defeats the cache; FR-005 only requires
  re-evaluation when the index has something to compare, so a fresh `select_provider` comparison (cheap, in-memory
  over the cached providers + index) is the right granularity.

---

## Decision 5 — provides population is transactional and truthful, stale providers are swept, the circular flag resets, and status/requested data tell the truth (P-13, P-14, P-15, P-17)

**Decision**:
1. **P-13** — `populate_provides_table` (`initializer.py:65-84`) calls `conn.commit()` at line 83 — inside
   `ensure_base`'s `BEGIN…commit` "single transaction" (200-205) — and `count += 1` at 82 for every
   `INSERT OR IGNORE` attempt, counting skips as inserted. Fix: remove the internal `commit()` (the caller owns
   the transaction); count real inserts via the cursor `rowcount` (0 for a skipped duplicate). `init.py:124-129`
   (the other caller) must add an explicit `conn.commit()` before close since it no longer inherits the internal
   commit.
2. **P-14** — `provides` rows from no-longer-configured releases/indexes are never swept (no `DELETE FROM` in
   `src/`), so a stale provider can be selected and later fail "not found in index". Fix: before re-populating
   the provides table from a fresh index, delete rows whose `(release, component)` is not in the currently
   configured set — `DELETE FROM provides WHERE (release, component) NOT IN (…)` scoped to the config's
   `(release, component)` pairs. Applied wherever `populate_provides_table` runs after an index refresh (init and
   the resolver's index ensure path).
3. **P-15** — the circular report (`verify.py:195-212`) counts `circular = 1` rows with no `status` filter, so
   superseded rows count; and `upsert_binary` (`resolver.py:111-130`) never resets `circular` on upsert, so a
   re-registered binary keeps a stale flag. Fix: (a) `_find_circular` adds `AND status = 'current'`; (b)
   `upsert_binary`'s `ON CONFLICT DO UPDATE` adds `circular = 0` (fresh inserts already default 0), so
   `verify` and `list --deps` agree.
4. **P-17** — `upsert_package` (`resolver.py:92-100`) always sets `status='pending'` and nothing ever transitions
   it; `_resolve_one_inner:956` passes `requested_version or binary_entry.version`, storing the **resolved**
   version as the "requested" version of every transitive dep. Fix: (a) define the lifecycle — `pending`
   (registered) → `current` (a current binary is registered) — and have the resolver set `packages.status =
   'current'` when it registers a current binary (`upsert_package` gains a `status='current'` call at the same
   site that upserts the binary); (b) `_resolve_one_inner:956` passes `requested_version` (None for transitive
   deps), so `requested_version` holds only what was actually requested; `upsert_package`'s existing
   `COALESCE(NULLIF(?, ''), requested_version)` keeps an already-recorded explicit request.

**Rationale**: All four are truthfulness/consistency of the stored model. The nested commit actively violates the
"single transaction" claim that spec 008 relies on (a mid-run failure would leave partial provides committed);
the stale-provides sweep removes a real "selected then not found" failure mode; the circular flag and
status/requested columns are the data behind `verify`/`list`/`show` and must not mislead.

**Alternatives considered**:
- *P-13: keep the commit but document it* — rejected: the review explicitly flags the contradiction with
  "ensure-base's single transaction claim"; the caller-owns-transaction fix is the correct ownership model.
- *P-17a: a `failed`/`superseded` status set* — deferred: the review only requires that status "transition" (not
  be stuck at `pending`); `pending → current` is the minimal truthful lifecycle. A richer lifecycle can come with
  a future status feature; the schema-freeze assumption prefers the smallest change.
- *P-14: sweep by re-INSERT from a full refresh only* — the `NOT IN (configured set)` delete before populate is
  the minimal correct sweep; it runs only when an index refresh actually re-populates.

---

## Decision 6 — a relative `base_dir` is rejected, `Retry-After` is capped, and space-containing cache paths produce a working entry or a clear diagnostic (P-19, P-20, P-21)

**Decision**:
1. **P-19** — `validate_absolute` (`paths.py:8-12`) resolves with `os.path.abspath` **before** the `isabs`
   check, so the `ValueError` is dead code and a relative `base_dir` is silently reinterpreted against the CWD.
   Fix: check `os.path.isabs(path)` on the expanded path **before** resolving:
   `expanded = os.path.expanduser(path); if not os.path.isabs(expanded): raise ValueError(f"Path is not absolute:
   {path}"); return os.path.abspath(expanded)`. `config_loader.py:246-247` already wraps the `ValueError` into
   `ConfigError` (exit 102, key named).
2. **P-20** — `_backoff_delay` (`downloader.py:204-211`) returns `max(0.0, retry_after)` uncapped, while the
   exponential fallback is capped at `BACKOFF_CAP = 30.0` (210). A server `Retry-After: 86400` stalls a run for a
   day. Fix: `return min(BACKOFF_CAP, max(0.0, retry_after))` — the override is capped at the same bound as the
   internal backoff. (Per-spec: "the wait is capped at the same bound as the internal backoff".)
3. **P-21** — `write_sources_entry` (`handoff.py:143-156`) emits
   `printf 'deb [signed-by=%s/offline-cache.gpg] file:%s %s %s\n'` with `$handoff_dir` unquoted inside the
   whitespace-delimited `deb` line; a path with spaces silently breaks it (plausible USB mount points). Fix:
   **guard + diagnostic**: `write_sources_entry` (and the generator that calls it) checks `$handoff_dir` for a
   space (or whitespace) and, when present, writes a clear, actionable message — the handoff refuses to emit a
   broken line and tells the operator/admin the path contains a space and the cache must be placed at a
   space-free path (e.g. mount point or `base_dir`). This is the deterministic, testable branch of FR-019
   ("working entry **or** clear, actionable diagnostic").

**Rationale**: P-19 restores the intended validation (the code *wanted* to reject relative paths but the order
made it impossible). P-20 is a one-line bound that prevents a pathological stall while preserving the server's
intent for sane values. P-21's guard-and-diagnostic is chosen over space-escaping (`\040`) because apt's handling
of escaped spaces inside the `[signed-by=…]` bracket option is not universally reliable, and a clear refusal is
safer than a subtly-broken line.

**Alternatives considered**:
- *P-21: escape spaces as `\040` in the URI and signed-by* — rejected as primary: reliable for the `file:` URI,
  uncertain for the bracket `signed-by` option across apt versions; a diagnostic is deterministic and testable.
  (Noted as a possible future enhancement if a working-entry variant is desired.)
- *P-21: quote the path in the deb line* — rejected: apt `sources.list` is whitespace-delimited; quoting is not a
  supported escape there.
- *P-20: honor Retry-After but never below 1 s* — rejected: not the finding; only the upper bound is wrong.

---

## Decision 7 — the downloader's size-match doc/test labels are corrected and the mirror-normalization tests get their own class (P-22, P-23)

**Decision**:
1. **P-22** — `_stat_size_matches`'s docstring (`downloader.py:341-344`) says "a matching size skips the O(file)
   re-hash for verified records", but the code re-hashes on a size match (`downloader.py:402-405`: `… and
   _stat_size_matches(dest, size) and _file_sha256(dest) == …`); the test name
   `test_verified_file_with_matching_size_is_not_rehashed_or_refetched` (`tests/unit/test_downloader.py:1115`)
   makes the same backwards claim. Docs backwards, code right. Fix: correct the docstring to state that a size
   match only short-circuits the *existence/size* check and the checksum is **always** re-verified, and rename the
   test to describe the conservative behavior (e.g. `test_verified_file_with_matching_size_is_rehashed_but_not_refetched`).
   No behavioral change.
2. **P-23** — the mirror-normalization tests (`tests/unit/test_config.py:580-628`, e.g.
   `test_remote_http_mirror_upgraded_to_https`) live inside `TestProfileFilesAreData` (declared at line 499) with
   no class header of their own. Fix: hoist them into a dedicated `TestMirrorNormalization` class (same bodies,
   same coverage), so the suite's structure matches the tests' subject.

**Rationale**: Both are label/structural hygiene — the docstring/test-name lie erodes confidence in the
(conservative, correct) checksum behavior, and the mis-grouped test class obscures what is being tested. Neither
changes behavior, so they are the lowest-risk items in the feature.

**Alternatives considered**: none — the code is correct in both cases; only the labels need fixing.

---

## Decisions consolidated against the spec FRs

| FR | Research decision | Phase |
|----|-------------------|-------|
| FR-001 (P-1) | `add` catches `DatabaseError` → 101 | 1 |
| FR-002 (P-4) | `InitError.permission` flag; offline-mirror summary note | 1 |
| FR-003 (P-5) | DB creation deferred past discovery in `ensure_base` | 1 |
| FR-004 (P-2) | `--graph` removed; `--package` depth clause | 2 |
| FR-005 (P-3) | moniker validation; `try/finally` conn close | 2 |
| FR-006 (P-7) | empty-pin parity; `--deps`/`--status` mutually exclusive | 2 |
| FR-007 (P-6) | zero-pending sync regenerates empty diff | 3 |
| FR-008 (P-18) | build-dep report depth threaded through | 3 |
| FR-009 (P-9/P-16) | `main` wins ties; `ORDER BY id` | 4 |
| FR-010 (P-10) | fallback constraint re-check + error retraction | 4 |
| FR-011 (P-11) | runtime-alternative probe `status='current'` | 4 |
| FR-012 (P-12) | same-release virtual refresh on provider upgrade | 4 |
| FR-013 (P-13) | no nested commit; real insert count; `init` commits | 5 |
| FR-014 (P-14) | stale-provides sweep on refresh | 5 |
| FR-015 (P-15) | circular current-only + reset on upsert | 5 |
| FR-016 (P-17) | status lifecycle; truthful `requested_version` | 5 |
| FR-017 (P-19) | relative `base_dir` rejected pre-abspath | 6 |
| FR-018 (P-20) | `Retry-After` capped at `BACKOFF_CAP` | 6 |
| FR-019 (P-21) | space-in-path guard + diagnostic | 6 |
| FR-020 (P-22/P-23) | docstring/test-name + test-class corrections | 7 |
