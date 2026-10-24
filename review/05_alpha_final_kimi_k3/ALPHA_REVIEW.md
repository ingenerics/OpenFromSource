# Alpha Release Review

**Scope**: full technical + functional review of the `main` branch (HEAD `55cdf26`, post-spec-019) ahead of the
alpha tag. **Method**: static file inspection only — no project commands, tests, or builds were executed, per the
review request. Every finding below was traced through the current source; line numbers refer to HEAD.

**Inputs reviewed**: all of `src/package_mgmt/` (7 518 lines), `pyproject.toml`, `project.defaults.toml`,
`project.local.toml`, `docs/profiles/*.toml`, `docs/001–003`, `tests/` layout, `.github/workflows/ci.yml`,
the spec history `specs/001…019`, and the prior review corpus `review/01…04` (including the two pre-release
reviews and their consolidated backlog/fix ledger in `review/04_alpha_summary/01–03`).

---

## 1. Verdict

The project has converged: **all six prior alpha blockers (B-1…B-6) and all high-priority follow-ups
(F-8…F-23, H-2, M-3) are verifiably fixed in the current source**, and the 019 polish backlog (P-1…P-23) is
implemented. The fresh-cache flows for all three profiles (regular_user, developer, graybeard) resolve
correctly and produce a coherent, installable handoff — *by inspection* (see §4 for the evidence-by-design
arguments; execution evidence is recorded by the project itself in `specs/015…019` and `tests/REGRESSION.md`).

However, this review identified **new lingering defects** in code paths the earlier reviews did not reach —
concentrated in (a) the `provides`-snapshot vs. on-demand index fetch interplay, (b) profile-transition
source repair of cached virtuals, and (c) constraint-blindness in the alternative-group resolution machinery
added by the F-8 fix. Two of them touch the alpha's core "fully installable cache" promise; one is a
distribution-integrity hole. **Recommendation: fix N-1, N-2, N-3, N-4 (or accept them explicitly) before
tagging the alpha.** Everything found is listed in §5; the fresh-cache 3-profile functionality is otherwise
sound.

## 2. Fix-status verification of the prior review corpus

Re-verified against current source (all confirmed fixed):

| Prior finding | Current state (evidence) |
|---|---|
| B-1 `Pre-Depends` never parsed | **Fixed.** Parsed in `index_loader.py:120`, merged via `merge_depends_text` (`index_loader.py:84-106`); census artifact in `specs/015-alpha-release-blockers/census-pre-depends.md`. |
| B-2a first-visit depth | **Fixed.** `_batch_levels` + `_maybe_repair_min_level` + `_ensure_source` (`resolver.py:274, 586-603, 561-584`); re-seed sentinel after rollback (`resolver.py:43, 605-621`). |
| B-2b cached-virtual source gate | **Fixed** for positive depths via `_ensure_source` in all three cached-virtual return branches — *but incompletely gated for `-1`*; see **N-2**. |
| B-2c mid-batch rollback state | **Fixed.** `_re_seed_batch_state_from_db` (`resolver.py:375, 605-621`). |
| B-3 diff ordering / early delivery | **Fixed.** Copy precedes stanza/manifest (`handoff.py:1104-1122`); `mark_delivered` only after exclusion checks (`sync.py:66-92`). |
| B-4 FAT `bootstrap.sh` false success | **Fixed.** `ensure_pool_link` fails hard (`handoff.py:125-139`), `handoff_complete` checks the link (`handoff.py:97-99`). |
| B-5 frozen `Date`/`Valid-Until` time-bomb | **Fixed.** `_release_epoch` = generation time w/ `SOURCE_DATE_EPOCH` opt-in (`handoff.py:705-720`); no `Valid-Until` in `_release_text` (`handoff.py:804-821`). |
| B-6 non-JSON stderr in JSON mode | **Fixed.** e.g. `config.py:15-17` guards `logger.error` behind `format != "json"`; REGRESSION.md records both runners green. |
| F-8 alternative-group error residue | **Fixed** (group resolved as a unit, per-attempt truncation, `resolver.py:1048-1090`) — *but the "already satisfied" probe is constraint-blind*; see **N-3**. |
| F-9/F-10 version/release-blind source linkage | **Fixed.** `_register_source_for` exact-version scoping own release → primary → any, newest-in-release fallback warns (`resolver.py:485-511`). |
| F-11 per-alternative constraints | **Fixed.** `Alternative` model + serialization (`dependency.py:65-101`); passed at resolve call sites (`resolver.py:1071-1075, 705-708`). |
| F-12 arch-restricted / multi-profile deps | **Fixed.** `_ALT_PATTERN` + `_arch_allows` + profile accumulation (`dependency.py:13-62, 166-207`); unparseable tokens warn. |
| F-13 `verify` ignores direct-version constraints | **Fixed.** `_direct_name_satisfies` evaluates constraints (`verify.py:181-188`); providers over-inclusive by design. |
| F-15 build-dep pass re-walks all sources | **Fixed.** `_batch_inserted_sources` (`resolver.py:277, 533-536, 656-663`). |
| F-16 redirects hashed as mismatches | **Fixed.** `_PooledOpener` follows ≤5 redirects, loops/no-Location permanent (`downloader.py:35-39, 145-179`). |
| F-17 verified sources never rechecked | **Fixed.** Per-run existence→size→sha256 re-check of every index-listed file (`downloader.py:493-628`). |
| F-18 handoff ships out-of-config records | **Fixed.** `base_set` exclusion into `excluded_config`, not marked delivered (`handoff.py:1234-1242, 1278-1279`). |
| F-19 `apply.sh` garbage `$new_files` | **Fixed.** apt output routed to `.apply-apt.log` (`handoff.py:467-491`); summary line pinned (`handoff.py:534-548`). |
| F-20/F-21/P-8 config type/override handling | **Fixed.** `_TYPE_MAP` + unknown-key rejection (`config_loader.py:24-35, 220-229`), local overrides adopt known keys regardless (`config_loader.py:211-214`). |
| F-22/M-5 read commands create stray DB | **Fixed.** `validate_schema_or_error` gates `show`/`list`/`verify` (`show.py:133`, `list.py:15`, `verify.py:19`). |
| F-23 truncated index poisons cache | **Fixed** (resolve path). `_load_or_fetch_index` self-heals (`resolver.py:53-95`); `_write_index_atomic` (`index_loader.py:237-252`). *Caveat:* see L-11 (uncaught decode errors; and the downloader path doesn't self-heal, L-8). |
| H-2 ensure-base re-run resets verified | **Fixed by design change**: ensure-base is fresh-cache-only (`ensure_base.py:172-186`); docs updated (`docs/003-ensure-base.md:69-71`). |
| M-3 ensure-base duplicate current rows | **Fixed by the same fresh-cache refusal**; `_find_current_binary` additionally deterministic (`ORDER BY id`, `resolver.py:111-113`). |
| M-4 sources indexes fetched when disabled | **Fixed.** `ensure_indexes` gates on `fetch_source` (`resolver.py:301-308`). |
| M-7 re-download of present valid files | **Fixed.** Size+hash short-circuit for unverified records (binary `downloader.py:404-415`; source `downloader.py:558-564`). |
| P-1…P-23 (019 backlog) | **Fixed.** Spot-verified in source: P-1 (`add.py:99-108`), P-2 (`main.py:120-123`, `verify.py:113-122`), P-3 (`systems.py:26-29, 59-60`), P-4 (`init.py:109-115, 142-149`), P-5 (`ensure_base.py:198-207`), P-6 (`sync.py:57-65`), P-7 (`add.py:85-91`, `main.py:199-203`), P-9 (`repository.py:85-88`), P-10 (`resolver.py:716-745`), P-11 (`resolver.py:1061, 1065`), P-12 (`resolver.py:934-951`), P-13/P-14 (`initializer.py:65-85`), P-15 (`resolver.py:133-134`, `verify.py:217, 223`), P-16 (`resolver.py:111-113`), P-17 (`resolver.py:992`, `upsert_package` `resolver.py:98-106`), P-18 (`verify.py:294-323`), P-19 (`paths.py:8-12`), P-20 (`downloader.py:204-212`), P-21 (`handoff.py:664-678, 146-155`), P-22/P-23 (docstring, test class). |

## 3. Deviations and notes on the fix ledger itself

- **ensure-base re-runs are now refused** (`CacheNotEmptyError`) rather than made idempotent — a deliberate
  design change, documented (`docs/003`), but it creates the stuck-state edge L-9 below.
- `tests/REGRESSION.md` records the post-019 baseline **707 passed / both runners**, but its "Contract suite"
  line still says `68 passed + 15 subtests` while 019's T068 records **84 + 15** — regenerate REGRESSION.md
  before tagging (the baseline file contradicts the last milestone).
- `AGENTS.md`'s SPECKIT block still says "run `/speckit.tasks`" for 019 even though 019 is fully checked —
  refresh the block (it is the project's own agent-facing map).
- `.github/workflows/ci.yml` runs **pytest only**. The B-6 lesson ("a green pytest alone proves less") is not
  enforced in CI: add the `python -m unittest discover -s tests` leg, or CI can go green on a runner-specific
  regression again.

---

## 4. Functional assessment — the three profiles and the installable-cache promise

The alpha's bare-minimum requirement: *packages resolve correctly for all three profiles, and each setup
produces a stable, fully installable cache.* Assessed by tracing the complete chain for each profile:
config → index load → resolution → download → verify → handoff → operator `bootstrap.sh`/apt.

### 4.1 regular_user (`source = false`, `max_source_dep_depth = 0`) — **sound**

- The shipped `project.defaults.toml` and `docs/profiles/regular_user.toml` both carry these presets, and the
  `ProjectConfig` dataclass defaults match (`config.py:27-29`) — one default story, novice-safe (SC-003).
- Resolution: `_source_in_scope` never fires (`resolver.py:449-462`), `ensure_indexes` never fetches `Sources`
  (`resolver.py:301`), no source/build-dep pass runs (`resolver.py:388`). The runtime closure is uncapped
  (`resolver.py:1030-1090` recursion has no depth gate — FR-003).
- `Pre-Depends` merged into the closure (`index_loader.py:120-127`) — the B-1 census gap is closed.
- Download: source phase returns immediately (`downloader.py:508-509`); binary phase re-verifies shipped
  files per run — deleted/corrupt pool files self-repair or un-mark (`downloader.py:416-470`).
- verify: `max_source_depth = 0 ≠ -1` → unlinked binaries informational (`verify.py:44-47`) — correct for the
  profile; runtime completeness is the gate (`verify.py:55`).
- Handoff ships exactly `current + verified` records with present pool files (`handoff.py:723-729,
  1271-1279`); apt-installability evidence by construction in §4.4. **Verdict: fresh-cache flow is correct
  and installable.** Residual risks: N-1 (if `init` ran offline), N-3 (version-skewed alternative groups).

### 4.2 developer (`source = true`, `max_source_dep_depth = 1`) — **sound, with one transition caveat**

- Depth semantics: level 0 = requested, level N deps — `_source_in_scope` (`resolver.py:449-462`); build
  tools placed at `depth + 1`, i.e., deliberately out of source scope at finite depths (the documented
  Constitution-III exception, `resolver.py:652-653`).
- The B-2a/B-2b repair machinery (minimum-level first-visit fix, cached-virtual fix) is in place and is
  tested for the positive-depth cross-run case (`tests/unit/test_virtual_resolution.py:328-405`).
- FR-013 (every fetched source buildable): build-dep pass processes only current-batch sources
  (`resolver.py:641-759`), resolves tools' full runtime closures.
- verify: finite depth → unlinked informational; build-dep lists respect `max_build_dep_report_depth`
  (`verify.py:294-323`).
- **Verdict: fresh-cache flow is correct and installable.** Caveat: deepening a cache grown under
  regular_user/developer(depth-k) leaves cached-virtual *subtrees* under-supplied (N-2b) — a source-coverage
  gap (not installability) made silent by the informational-only unlinked check at finite depths.

### 4.3 graybeard (`source = true`, `max_source_dep_depth = -1`) — **sound on fresh caches; broken repair path on profile transitions**

- Fresh graybeard run: every binary is in scope at registration (`resolver.py:458-459`), build tools at level
  0 (`resolver.py:653`) → turtles all the way down, capped only by the generous
  `MAX_BUILD_DEP_ITERATIONS = 100` safety (`resolver.py:38`) with an explicit warning if ever hit.
- ensure-base (the graybeard home flow): fresh-cache-only refusal, single Phase-A transaction, no nested
  provides commit (`ensure_base.py:189-229`, `initializer.py:65-85`) — the prior data-corruption modes are
  gone. Residual UX edge: L-9.
- verify at `-1` enforces strict unlinked-binaries-are-defects (`verify.py:44-47, 191-209`).
- **Verdict: a fresh graybeard cache is correct, complete, and installable.** But the documented
  grow-deeper direction (regular_user → developer → graybeard, per the profile files' own headers and spec
  014 FR-014) hits the new defect **N-2**: cached-virtual providers first resolved under a source-less
  profile never gain their source at `-1`, and `verify` then reports INCOMPLETE with no way for `add` to
  repair it automatically.

### 4.4 The installable-cache chain (all profiles) — **coherent**

The operator-facing artifacts were traced end to end and are internally consistent:

1. **Pool integrity**: every payload lands via `*.part` → hash-verify → atomic rename; per-run re-checks
   un-mark/re-fetch damage (`downloader.py`). Redirects are followed before hashing; `Retry-After` is capped;
   unsafe `Filename`/`Directory` paths are refused (`paths.py:110-131`).
2. **Index trust**: remote indexes are accepted only after the archive's signed Release verifies against the
   pinned keyring *and* the index checksum matches (`index_loader.py:332-380`); loopback mirrors are the
   documented exception. Writes are atomic. Truncated caches self-heal on the resolve path.
3. **Handoff**: `Packages.gz` emitted per (release, component) for exactly the current+verified set with
   present pool files; `Multi-Arch` and `Provides` preserved (`handoff.py:746-769`) — necessary for
   `name:any` deps and virtual satisfaction operator-side; `Size`/`SHA256` come from the (verified) index;
   `Release` carries both `Packages` and `Packages.gz` hashes (the apt `file://` gotcha the research
   documented) and **no** `Valid-Until`; detached signature with the stable cache-local key; `bootstrap.sh`
   fails hard on unlinkable pools and never configures a broken source.
4. **Diff/sync**: payload copied and verified *before* its stanza is appended; delivered-set marking happens
   only after a clean pass; zero-pending runs regenerate an empty diff (no stale shipping).
5. **Operator apply**: manifest checksum gate, base-set ordering check, atomic copies, handoff dists replaced
   before `apt update`; nothing touches the system entry on failure paths before validation.

**Gaps found in this chain**: N-1 (stale `provides` snapshot after offline `init`), N-3 (installability of
version-skewed alternative groups), L-4 (`Size: None` stanza possible), L-6 (admin-side FAT crash on handoff
generation). None of these affects a cache built in the documented online-init flow with unskewed versions.

---

## 5. New findings (this review; static inspection, HEAD `55cdf26`)

Severity: **B** = blocks the alpha promise · **H** = high — fix or consciously accept before tag ·
**M** = medium — same-release-if-cheap · **L** = low/polish.

### N-1 — [H] After an offline `init`, `add` resolves every virtual package as "no providers" — with a misleading error

**Where**: `resolver.py:279-319` (`ensure_indexes`) populates no `provides` rows; `populate_provides_table` is
called **only** from `init` (`init.py:133`) and `ensure_base` (`ensure_base.py:218`) — never when indexes are
fetched on demand during `add`.

**What**: `ensure_indexes` fetches and merges archive indexes on first use, but virtual-package candidate
lookup (`query_provides`, `resolver.py:210-222`) reads **only the DB `provides` table**. Scenario:

1. `package_mgmt init` with no reachable mirror (deliberately tolerated; exits 0 with the honest offline
   note — P-4). No indexes cached → `provides` table empty.
2. Mirror becomes reachable; `package_mgmt add vim` → `ensure_indexes` now *fetches* the 68k-package index —
   but nothing repopulates `provides`.
3. Every virtual dependency in the closure (e.g. `awk`, `editor`, `mail-transport-agent`) fails:
   `"Virtual package 'awk' has no providers in the index"` (`resolver.py:897-898`) — **the index has
   providers; the DB snapshot is stale**. `add` exits 1 with errors that point the user at the archive, not
   at the real cause.

The same gap fires whenever an index first materializes outside `init`/`ensure_base`: partially-failed `init`
(some components cached, others not), or the F-23 self-heal re-fetch of a truncated index with changed
content (snapshot-in-time indexes make the last case benign in practice). The remedy — re-run `init` —
works but is nowhere suggested by the error or docs.

**Fix (small)**: after `ensure_indexes` (re)fetches any index, refresh `provides` from the merged index with
the configured-source sweep (reuse `populate_provides_table(conn, self.index, configured_sources)` inside
the batch's first commit), and/or hint "re-run init" in the no-providers error when the `provides` table is
empty. **Tests**: offline-init fixture → online `add` of a package with a virtual dep → succeeds and the
provider is registered; no-providers error names the stale snapshot.

---

### N-2 — [H] Cached-virtual source repair is depth-gated: graybeard profile transitions leave providers (and their whole subtree) without sources, irreparably

**Where**: the three cached-virtual return branches call `_ensure_source` only under
`_min_level_repair_enabled()` (`resolver.py:927-933, 949-951`), and that check is `depth > 0`
(`resolver.py:464-472`) — i.e. **never** at `-1` (graybeard).

**What (a)**: `_ensure_source` is already idempotent and scope-gated internally (`resolver.py:568-573`), so
the outer gate is redundant for correctness — but at `-1` it makes the repair unreachable. Scenario (the
profiles' own documented grow-deeper direction):

1. regular_user cache: `add mutt` resolves virtual `mail-transport-agent` → provider `exim4` is cached in
   `virtual_resolutions`; no sources exist anywhere (`fetch_source = false`).
2. Switch to graybeard.toml; `add something-else` whose closure hits `mail-transport-agent`:
   the cached provider is reused verbatim (`resolver.py:943-951`) — `_ensure_source` is skipped because
   `_min_level_repair_enabled()` is `False` at `-1` — and the provider's subtree is never re-walked through
   that edge (the early return replaces the whole inner resolution).
3. `verify` at `-1` → "Unlinked binaries: exim4, …" for the provider *and every transitive binary below the
   virtual edge* → **INCOMPLETE**, and no `add` re-run repairs it (the cache short-circuits every time).
   The only recovery is adding each affected provider by its real name — undiscoverable for a user.

All three cached-virtual return branches share the same `_min_level_repair_enabled()` gate
(`resolver.py:927-928, 931-932, 949-951`), so at `-1` none of them repair — only the fresh-provider
registration path (first-ever resolution of the provider) creates sources at `-1`, and that path is exactly
what a pre-existing `virtual_resolutions` row bypasses. Positive-depth transitions are the only covered case
today (and those only repair the provider, see (b)).

**What (b)**: even with the gate enabled (finite N), only the provider binary gains a source — its
transitive runtime deps at levels `edge+1 … N` are never re-walked through the virtual edge, so a
deepened developer cache silently under-covers virtual-adjacent subtrees (informational-only at finite
depths, so invisible — FR-004 says levels ≤ N must be covered).

**Fix**: (a) drop the `_min_level_repair_enabled()` wrapper around `_ensure_source` calls in the three
cached-virtual branches — `_ensure_source`'s own `fetch_source`/scope/idempotence gates are sufficient;
(b) when a repair actually registers a missing source, also re-walk the provider's stored dependency JSON at
`level+1` (idempotent re-resolution; mirrors the min-level intent), or mark the provider's real name for a
full `_resolve_one` re-entry in the same batch. **Tests**: transition fixtures — regular_user→graybeard with
a virtual in the closure → provider + subtree sources present, `verify` COMPLETE at `-1`;
developer(1)→developer(2) → level-2 subtree sources present. The existing
`TestVirtualSourceDepthRepair` (`tests/unit/test_virtual_resolution.py:328`) covers only fresh-developer.

---

### N-3 — [H] The "already satisfied" alternative-group probes are constraint-blind: version-skewed groups resolve to nothing, and `add` exit-0's a cache `verify` flags INCOMPLETE and apt cannot install

**Where**: runtime probe `resolver.py:1059-1069` (exists-by-name/status only); its build-dep twin
`resolver.py:693-703`.

**What**: after the F-8 rework, a failed primary in a `a (>= V) | b` group is excused whenever *any* group
member has a `current` DB row — regardless of whether that row satisfies the member's own version
constraint. Concretely:

1. `add before` (depends `a`) → `a 1.0` registered current. Index holds only `a 1.0`; `b 1.0` exists.
2. `add app` (depends `a (>= 2.0) | b`): primary fails — `"No version of a satisfies constraint (>= 2.0)"`
   is appended, then the probe at `resolver.py:1059-1061` finds the `a 1.0` current row →
   `del stats["errors"][error_mark:]` (`:1083`) → the group counts as resolved and **`b` is never tried**
   (the `if not any_satisfied` guard, `:1070`).
3. Result: `add` exits 0; the closure lacks `b` (which apt would install: a too-old primary does not win an
   alternative); `verify` does flag it — `_dep_satisfied` evaluates constraints (`verify.py:181-188`) —
   **INCOMPLETE**, whereupon the operator's `apt-get install app` also fails. So the cache is simultaneously
   "successfully resolved" and uninstallable — precisely the state F-8/F-13 were meant to eliminate, one
   probe short of the finish line.

The build-dep twin is less silent but still under-resolves: the blind probe skips alternatives, then the
constraint-aware link block (`resolver.py:716-745`) records "Build tool not resolved" even when an
alternative *could* have resolved — a buildable source rendered unbuildable in a version-skewed cache
(FR-013 "every fetched source is buildable" weakened).

Note the asymmetry with the *correct* pattern one screen away: the build-dep link block evaluates each
candidate's constraint against its own name's constraint (`resolver.py:719-740`). The two higher-up probes
should do the same (and the runtime one should try alternatives when the only DB hits are unsatisfying).

Probability check: requires a member already current at an unsatisfying version — prior version pins,
batch constraint intersections, archive skew after an index refresh. Not daily-driver, but exactly the
shape real multi-era caches take. Severity H because it directly punches the installable-cache promise and
contradicts the sibling `verify` gate.

**Fix**: make the probes mirror the link block — for each group member, check `status='current'` **and**
`satisfies_constraint(row_version, member_vc)` (with the provides-join path keeping its documented
over-inclusive semantics); only truncate the error when a member *constraint-wise* satisfies; otherwise try
alternatives. **Tests**: `a (>= 2) | b` with `a 1.0` pre-registered → `b` resolved into the closure, no
error, exit 0; build-dep twin with resolvable alternative → linked, no "not resolved" error.

---

### N-4 — [M] Wheel/sdist almost certainly ships without `debian-archive-keyring.gpg` — a standard (non-editable) install cannot fetch any verified index

**Where**: `pyproject.toml` declares `build-backend = "setuptools.build_meta:__legacy__"` and has **no**
`[tool.setuptools.package-data]` / `include-package-data` + no `MANIFEST.in`; the trust anchor lives at
`src/package_mgmt/data/debian-archive-keyring.gpg` and `data/` is **not** a Python package (no
`__init__.py`). `index_loader.py:22-24` resolves it relative to the installed module.

**What**: every remote-mirror `fetch_index` requires this file (InRelease/Release.gpg verification,
`index_loader.py:268-311, 355-356`). Editable installs (the documented dev flow, CI) read the source tree
and work; a wheel or regular `pip install .` very likely omits the file (legacy backend + no package-data
declaration), which makes **every** mirror fetch raise `IndexVerificationError` — the tool fails closed
(safe) but is useless: no `init`, no `add`, no download against any real archive. This is unverifiable by
file inspection alone (depends on the setuptools data-file rules actually applied) — so: build the wheel
and inspect it before tagging, and/or declare the data file. Either way the alpha's packaging story must be
pinned: if the alpha is "git clone + `pip install -e .` only", say so in the README/docs; otherwise add
`[tool.setuptools.package-data]` (e.g. `package_mgmt = ["data/*.gpg"]`) and confirm the artifact.

**Fix**: declare the keyring as package data (and move resolution to `importlib.resources` for zip-safety),
then verify the built wheel contains it; decide+document the supported install mode for alpha.

---

### Low-severity findings

- **L-1 — Zero-pending `sync` discards the defect check.** `sync.py:57-65` calls `generate_diff` and returns
  success without inspecting `stats.excluded_delivered` — the non-pending branch treats an unconfirmable
  delivered record (deleted pool file since `download`) as "nothing new, all good" while the generated diff
  silently drops that package. Largely self-healing (the next `download` re-fetches or un-marks), but the
  zero-pending branch should mirror the defect check two screens below it.

- **L-2 — Resolution stats miscount in three places.** (a) `stats["sources"] += 1` fires even when nothing
  was inserted (repaired/existing source re-linked, `resolver.py:537`); (b) `build_tools` counts a *failed*
  primary name and never counts a *resolved alternative* (`resolver.py:749-751`); (c) the downloader's
  `reverified` counter never feeds any stat (`downloader.py:412-429`) — a run that only re-marks valid
  files reports "nothing to download / 0 verified". Cosmetic, but these numbers are what reviewers and the
  `add`/`download` summaries quote.

- **L-3 — `merge_depends_text` dedup keeps the *first* constraint by name** (`index_loader.py:99-106`). When a
  package declares the same target in `Depends` and `Pre-Depends` with *different* strength, the
  `Pre-Depends` constraint is lost (`Depends` parses first). Usually harmless (Debian duplicates agree), but
  a stronger `Pre-Depends` would be silently weakened. Intersect constraints instead of first-wins.

- **L-4 — Handoff stanza can emit `Size: None`.** `_paragraph` (`handoff.py:764-768`) guards missing
  `filename`/`sha256` upstream but not missing `size`; a malformed index paragraph produces a signed,
  apt-unparseable stanza ("Size: None"). Real indexes always carry Size — treat as a defensive check.

- **L-5 — `_format_dep_spec` rendering drift for Pre-Depends packages.** The merged `Depends` text
  (`index_loader.py:60-106`) drops `:arch` qualifiers on every name and alternatives' arch restrictions —
  harmless on an amd64-only cache (the resolver strips qualifiers anyway), but the shipped index no longer
  byte-matches the archive's own metadata for those packages. Cosmetic at this scope.

- **L-6 — Admin-side FAT crash.** Python-side `_ensure_pool_link` (`handoff.py:907-912`) lets `os.symlink`
  raise raw `OSError` (FAT target) — the operator side fails cleanly (B-4), the admin side still tracebacks
  when writing a handoff directly onto FAT media. Wrap into `HandoffError` with the same actionable message.

- **L-7 — Pinned add misses an existing version in a non-primary release.** With `primary_source` always set
  (`resolver.py:351, 862-871`), `add vim=<version>` errors "Version … not found" even when that exact version
  exists in another configured release — the hint ("use `--source <release>`") is absent. UX.

- **L-8 — Downloader lacks the index self-heal the resolver has.** `download_pending_binaries`/
  `download_pending_sources` raise raw `BadGzipFile`/`LZMAError` from `load_binary_index` on a truncated
  cached index (`downloader.py:387, 531`) → `handle_download` → generic "download failed". The resolver-side
  self-heal (`resolver.py:53-95`) isn't reused; user must re-run `add` first. Acceptable, divergent.

- **L-9 — ensure-base Phase-B wedge.** If the archive mirror is unreachable mid-flight, `ensure_indexes`
  raises `RuntimeError` (`resolver.py:92-95`) after Phase A committed — the run dies, `_assert_fresh_cache`
  then refuses any re-run (`ensure_base.py:172-186`), and the cache is half-built: resolvable only via
  manual `add`. Either roll back Phase A on Phase-B transport failure or offer a resume path/flag.

- **L-10 — Repair-path source lookup can fall back to a *different* version's index entry.**
  `_lookup_binary_entry` (`resolver.py:548-559`) ends at `lookup_binary(name)` (any version) when exact
  (version, release) entries are gone (post-refresh), so `_ensure_source` can register a version-mismatched
  source for a retained binary. F-9 made a logged divergence for the main path; the repair path would
  benefit from the same exactness-first-and-log rule. Rare (repair path + rotated index).

- **L-11 — Index self-heal misses decode/parser errors.** `_load_or_fetch_index` only catches
  `INDEX_READ_ERRORS` (`resolver.py:89`); a decompressed-but-invalid UTF-8 or malformed-control index raised
  from the loader's `decode`/`deb822` path propagates as a traceback instead of a re-fetch. Loopback
  mirrors only (remote fetches are checksum-verified), i.e. test-adjacent.

- **L-12 — Batch-constraint fallback is silent.** When no version satisfies every accumulated constraint,
  the resolver switches to the later constraint alone ("sequential last-writer", `resolver.py:788-796`) with
  no log line — a deliberate semantic, but invisible when it fires; a `logger.info` would aid diagnosis of
  debhelper-style constraint wars.

- **L-13 — Doc/ops drift** (in addition to §3): `docs/001-quickstart-howto.md:60,65` `--help` listing omits
  `sync`; the `unittest` runner is documented but absent from CI (§3); `AGENTS.md` SPECKIT block stale (§3).

### Process / evidence notes

- **Internal evidence conflict (test counts).** Current `REGRESSION.md` says 707/707 both runners (dated) yet its contract-suite line shows the pre-019 figure; 019's own T068 records 84 contract tests.
  Re-record the baseline from a clean run at tag time.
- **FAT loop-device run never executed** (sandbox limits, recorded in 015's ledger) — the FAT failure mode
  is covered by simulation only; a physical USB run remains the honest last step of the alpha checklist.
- **Live-mirror 3-profile evidence exists for `vifm`** (015 T042: closures identical, COMPLETE everywhere)
  but there is no live evidence of a graybeard `ensure-base` DVD-1 end-to-end run recorded in the spec
  corpus; the ensure-base integration coverage is fixture-driven. For the graybeard alpha promise, one
  recorded live DVD-1 run + offline install simulation would settle it.
- **No execution verification in this review** (per instruction): everything above derives from reading the
  code that the project's own test corpus asserts; where evidence and reading conflict (test counts), I
  flagged it rather than re-executing.

---

## 6. Risk matrix

| Finding | Severity | Area | Alpha-promise impact |
|---|---|---|---|
| N-1 stale `provides` after offline `init` | H | resolver/init interplay | `add` fails on all virtuals in a supported flow; misleading error |
| N-2 cached-virtual source repair gap | H | resolver depth machinery | graybeard grow-deeper transitions permanently INCOMPLETE; developer subtrees silently under-covered |
| N-3 constraint-blind alternative probes | H | resolver alternatives | installability hole on version-skewed caches; `add`-0 vs `verify`-INCOMPLETE contradiction |
| N-4 keyring likely absent from wheel | M | packaging/trust | standard install non-functional vs real archives (fails closed) |
| L-1 zero-pending sync defect skip | L | operator track | silent drop instead of defect error |
| L-2 stat miscounts | L | reporting | cosmetic |
| L-3 pre-depends constraint dedup | L | index loader | weakened constraint in rare duplicates |
| L-4 `Size: None` stanza | L | handoff | malformed index → broken stanza |
| L-5 merged-Depends rendering drift | L | index loader | cosmetic |
| L-6 admin-side FAT crash | L | handoff | traceback instead of diagnostic |
| L-7 pinned-add release hint | L | CLI UX | confusing error |
| L-8 downloader no self-heal | L | download | divergent robustness |
| L-9 ensure-base Phase-B wedge | L | ensure-base | half-built cache, no resume |
| L-10 repair-path any-version fallback | L | resolver repair | version-mismatched source linkage (rare) |
| L-11 self-heal error coverage | L | resolver index load | traceback on garbage loopback index |
| L-12 silent constraint fallback | L | resolver diagnostics | invisible semantic switch |

## 7. Recommended actions before tagging alpha (in order)

1. **N-1** — repopulate/sweep `provides` when `ensure_indexes` (re)fetches, or fail with a "re-run init"
   hint; one transition test. *Small change, kills a flow-breaking landmine in the documented offline flow.*
2. **N-2** — ungate `_ensure_source` in the three cached-virtual branches; add subtree re-walk on repair;
   transition fixtures regular_user→graybeard and developer(k)→developer(k+n) with virtuals. *Directly
   restores the profiles' documented grow-deeper direction.*
3. **N-3** — constraint-aware already-satisfied probes in both runtime and build alternative paths, keeping
   the documented provider over-inclusiveness. *Closes the last add/verify contract contradiction.*
4. **N-4** — declare the keyring package data; build and inspect the wheel; pin the install mode in docs.
5. Cheap ones to fold into the same pass or the first point release: L-1, L-3, L-4, L-6, L-9, L-2; doc ops
   (L-13 + §3: REGRESSION.md baseline, AGENTS.md block, CI unittest leg, `sync` in the help listing).
6. Execute the two never-run physical proofs (FAT USB bootstrap; graybeard DVD-1 live run + offline install
   simulation) and append them to the release notes.

If 1–4 land, the alpha promise — *three profiles resolve correctly; each produces a stable, fully
installable cache* — holds by inspection for fresh caches **and** for the documented profile-transition
direction, with all prior blockers still verifiably closed.

---

## Appendix A — Method

- Static inspection of every file under `src/package_mgmt/` (complete read of the seven largest modules —
  `handoff.py`, `resolver.py`, `downloader.py`, `index_loader.py`, `verify.py`, `dependency.py`,
  `ensure_base.py` — and read of all remaining modules), both TOML configs, three profile presets, docs,
  CI config, and the full `specs/001…019` + `review/01…04` history for decision context.
- No project commands, tests, builds, or network actions were executed (per instruction); execution-evidence
  statements cite the project's own recorded artifacts (`tests/REGRESSION.md`, spec task ledgers).
- Line references use `<file>:<line>` against HEAD `55cdf26`.

## Appendix B — What was *not* re-examined (prior Art disclosure)

Operator physical-media runs, live-mirror behavior, and the 707-test corpus were trusted as recorded; the
`snapshot`/`systems` bookkeeping, progress rendering, and the `.specify`/`.opencode` tooling directories were
given only functional (not adversarial) reads. The pre-019 reviews' closed findings were re-verified by
inspection rather than re-derived.
