# 02 — High-Priority Follow-Ups (same release if cheap)

**Source reviews**: `review/02_alpha_kimi_k3/ALPHA_REVIEW.md` (+ `ALPHA_REVIEW_FINDINGS.md`) · `review/03_alpha_glm_5_3/ALPHA_REVIEW.md`
**Base commit**: `995cf3e`. All line numbers refer to this state.
**Status**: verified against source at writing time; none fixed yet.

Deferred from `01_alpha_blockers.md` per decision (robustness/contract/perf items that should land in the
same release when cheap, or immediately after). Items are grouped by area. Entries with an `L-` cross-reference
were rated low by their review but are included here when they pair with a fix in this file.

---

## Resolver & dependency model

### F-8 — Satisfied alternative leaves the failed primary's error behind; `add` exits 1 · *high, confirmed*
`resolver.py:635, 794-810`; exit mapping `cli/commands/add.py:141-143, 185-188`.
For `ghost | realpkg` where only `realpkg` exists, the closure is fully resolved yet the run returns
`EXIT_ERROR` and `--file` counts the request as failed. Fires whenever the first alternative is absent from
the configured components (routine in real archives).
**Fix**: resolve the alternative group as a unit and append an error only when the *whole group* fails
(truncate `stats["errors"]` back on success). Keep per-segment error attribution.
**Tests**: `a | b` with only `b` → closure contains `b`, `errors == []`, exit 0; batch `--file` summary shows zero errors.

### F-9 + F-10 — Source linkage is version- and release-blind · *medium, confirmed*
`resolver.py:740-753`. The explicit version in `Source: foo (1.0-1)` is discarded (`src_name, _ = parse_source_field`),
and the source is picked as the **highest version** in the index (F-9). Separately, a binary resolved from a
fallback release (e.g. `trixie-backports`) is searched first against the **primary** release's sources (F-10).
Under archive skew / binNMU / backports, binaries link to the wrong source version — a Binary-Source
Consistency violation (Constitution III) that `download`/`verify` both accept.
**Fix**: (1) exact `(name, version)` lookup when the `Source:` field carries a version; (2) else derive the
expected source version from the binary version (`source_version_from_binary`, incl. `+bN` stripping); (3) only
then fall back to "newest in release", logging divergence. Look the source up in `binary_entry.release`/`component`
first. The index keeps all versions — this is a filter, not a data problem.
**Tests**: source versions `{1.0-1, 2.0-1}` + binary `foo-bin 1.0-1+b1` (`Source: foo (1.0-1)`) → links `foo 1.0-1`;
backports fixture → source from the backports release.

### F-11 / GLM L-9 — Version constraints on alternatives (`a | b`) are dropped · *medium, confirmed*
`dependency.py:72-77` keeps the constraint only on the first alternative; `resolver.py:460, 808` resolve
alternatives with no constraint. `foo (= 9.9) | bar (= 2.0)` with only `bar 1.0` registers `bar 1.0` with **no
error** — "every fetched source is buildable" (FR-013) no longer holds.
**Fix**: extend the model to carry a per-alternative constraint (e.g. `Alternative` dataclass; JSON shape is
internal, no DB change); pass each alternative's own constraint at `resolver.py:460/808` and in `verify`'s
alternative walk.
**Tests**: parser round-trip preserving per-alternative constraints; only-unsatisfying-version alternative present
→ error, no silent link.

### F-12 / GLM L-12 — Arch-restricted deps and multi-token build profiles silently dropped · *medium, confirmed*
`dependency.py:56-86`: no `[arch]` group, only a single `<profile>` token; unmatched tokens vanish with zero
diagnostics (and are missing from the stored `dependencies` JSON, violating spec 002 FR-009). `gperf [amd64 i386]`,
`debhelper-compat (= 13) <!nocheck !cross>` → silently dropped.
**Fix**: (1) optional arch list after constraint/profile parts — drop only when the restriction excludes the target
arch; (2) accept multiple profile tokens, evaluate conservatively (over-inclusive is the safe direction for a
cache); (3) never drop silently — WARNING on any unparseable token.
**Tests**: `foo [amd64]` kept, `foo [i386]`/`foo [!amd64]` dropped, multi-profile token kept with constraint intact,
unparseable token → warning + rest of line still parses.

### F-13 / GLM M-6 — `verify` reports version-constrained deps satisfied without checking the version · *medium, confirmed*
`verify.py:135-157` `_dep_satisfied`: any current binary with the name answers True; the constraint is never
evaluated on the direct-name path. `app` Depends `libfoo (>= 2.0)` with only `libfoo 1.5` → COMPLETE, exit 0,
while the operator's apt refuses. The completeness gate the alpha markets is weaker than apt's install check
(Constitution II gate has a hole).
**Fix**: on the direct-name path, select the current binary's version and return
`satisfies_constraint(version, spec.version_constraint)`; walk alternatives with their own constraints (ties into
F-11). Providers keep the existing documented over-inclusive behavior.
**Tests**: `>=` constraint with only-too-old binary → INCOMPLETE + non-zero exit; exact match → clean;
alternative satisfied only by the second alternative → clean.

### F-15 — Build-dep pass re-walks all historical sources on every `add` · *medium, confirmed (perf + scope leak)*
`resolver.py:407-490` (`processed_ids` is per-call). Every `add` re-iterates **every source ever registered** —
steadily slower on a mature graybeard cache (~25k sources), and after a graybeard run a later shallower-depth run
re-resolves build tools for out-of-scope retained sources (retention means *keep*, not *keep extending*).
**Fix**: process only sources created in the current batch; have `upsert_source` report insert-vs-existing and seed
`processed_ids` with everything already in the DB.
**Tests**: two sequential `add` runs — second run's build-tool counters count only its own sources; shallow-after-deep
rerun resolves zero new build tools for retained sources.

---

## ensure-base (deferred edge cases)

### H-2 — `ensure-base` re-run resets `verified = 0` for the whole base set · *high, code-verified (deferred here per decision)*
`resolver.py:66-69` (`upsert_binary` `ON CONFLICT ... SET verified = 0`) + `ensure_base.py:101-115`
(`_register_binaries` registers every ISO entry via `upsert_binary`). On any re-run after `download`, the entire
base set is reset to unverified → forced full re-download (several GB on DVD-1), the handoff silently shrinks
(`_verified_records` requires `verified = 1`), and delivery state limbo (`snapshot`/`sync` pending logic requires
`verified = 1`). Contradicts spec 008 idempotency and `docs/003-ensure-base.md:88`. Note `add` does **not** have
this problem — it is specific to ensure-base's bulk Phase A upserts.
**Fix**: in `_register_binaries`, preserve the existing `verified` value on conflict (dedicated registration
statement or `COALESCE`-style guard); keep the resolver's `upsert_binary` reset semantics for the
supersede/restore path untouched.
**Tests**: `test_rerun_is_idempotent` variant with `verified = 1` rows present before the re-run (current test only
covers the all-unverified state — GLM §7.2).

### M-3 — ensure-base Phase A can create duplicate `current` rows · *medium, code-verified*
`ensure_base.py:101-115` bypasses the supersede logic that `_resolve_one_inner` applies (`resolver.py:713-733`);
the UNIQUE constraint covers only `(package_name, version, architecture, release, component)`. If the cache holds a
different current version (e.g. `add vim` from the archive, then `ensure-base` with an older DVD), a second
`current` row appears; `_find_current_binary` (`resolver.py:46-51`) uses `fetchone()` with no `ORDER BY` →
nondeterministic supersede; the stale ISO row can stay current forever (redundant download, inflated verify
counts, duplicate handoff paragraph).
**Fix**: route Phase A registration through supersede-aware logic (or deduplicate to one current per
name+arch before/during registration).
**Tests**: cache with a different current version, then ensure-base with an older ISO → one current row, no
duplicate paragraph in the handoff.

---

## Download / verify / operator track

### F-16 — HTTP redirects become bogus "checksum mismatch" failures · *medium, confirmed*
`downloader.py:95-115` `_PooledOpener` raises only for `status >= 400`; a 301/302/307/308 response body is hashed
and fails as a **permanent, non-retried** checksum mismatch. Any redirecting mirror (http→https canonicalization,
CDN moves, relocated trees) breaks permanently. Integrity is safe (hash gate); availability is not.
**Fix**: follow redirects (`urllib.parse.urljoin` + re-issue, cap ~5 hops, reject loops) before the `>= 400` raise;
keep per-mirror connection reuse.
**Tests**: local HTTP fixture that 301s `/pool/...` → download succeeds and hashes; redirect without `Location` and a
loop → clear permanent error, `.part` cleaned up.

### F-17 — Verified source payloads are never rechecked / re-fetched · *medium, confirmed*
`downloader.py:402-529`: the binary phase re-checks each verified record's file every run (size → SHA-256, re-fetch
+ un-mark on failure); the source phase has **no equivalent** — `pending_sources` selects only `verified = 0` and
nothing ever re-examines source payload files. A deleted/corrupt source tarball is silently ignored by every command.
Related deviation: a source left unverified by one failed file re-downloads **all** its files next run (no per-file
skip).
**Fix**: mirror the binary phase for sources (each run, check size → SHA-256 of each index-listed file for
`verified = 1` sources; un-mark on failure) and add a per-file existence check in the source loop.
**Tests**: delete one file of a verified source → next `download` re-fetches exactly that file and re-marks verified;
corrupt-but-size-matching file → un-marked and re-fetched; nothing re-downloaded when all intact.

### M-7 — `download` re-fetches files that are already present and valid whenever `verified = 0` · *medium*
`downloader.py:335-341, 454-483`: the size+hash skip applies only to `verified = 1` records; unverified records are
re-downloaded even when the pool file already exists and matches (sources: no existence check at all). Combined with
H-2 this turns an ensure-base re-run into a guaranteed full re-download; independently it makes interrupted runs
expensive.
**Fix**: cheap `exists + size + sha256` short-circuit for unverified records.
**Tests**: unverified record whose pool file already matches → not re-downloaded; mismatched → re-fetched.

### F-18 — Handoff silently drops records outside the configured components — and marks them delivered · *medium, confirmed*
`handoff.py:1162-1199` (no `base_set` membership check, unlike `generate_diff:1009`). A verified record whose
`(release, component)` is no longer configured passes every check, gets its stanza appended and its id marked
delivered — and is then never written anywhere; no warning. The operator never sees the package (FR-003) and the
false delivery record later detonates in `sync` as "excluded delivered".
**Fix**: compute `base_set = set(bases)` and exclude any `(release, component)` not in it — loudly, and refusing to
mark those ids delivered (mirror `generate_diff:1009`).
**Tests**: register+verify under `trixie/contrib`, re-run with config reduced to `main` → omitted **and** reported;
`delivered_ids` unchanged.

### F-19 — `apply.sh` captures `apt update` stdout into `$new_files` → bash error + garbage summary · *medium, reviewer-reproduced*
`handoff.py` `APPLY_SCRIPT`: `apply_main` (~534) does `new_files="$(apply_configure …)"`, but `apply_configure`
(~465-498) runs `run_apt_update` whose output goes to stdout. Every successful apply prints
`bash: [: Ign:1 …: integer expression expected`, the "(already applied)" branch is unreachable, and the summary shows
raw apt output.
**Fix**: keep `apply_configure`'s stdout reserved for the count (route apt output to stderr or a file/FD).
**Tests**: shell-level test running the generated `apply.sh` against the fixture diff → stderr empty, summary matches
`New files: <number>`.

### M-4 — Sources indexes are fetched/required even when `[fetch] source = false` (regular_user) · *medium*
`resolver.py:232-246` fetches/loads `Sources` indexes for every configured source unconditionally (no `fetch_source`
check), while `init` (`cli/commands/init.py:70-73`) skips them when `source = false`. First `add` for regular_user
downloads the source indexes it was designed to avoid, and hard-fails if they're unreachable even though only
binaries are needed.
**Fix**: gate the Sources index fetch/load on `config.fetch_source` in `ensure_indexes` (match `init`).
**Tests**: `source = false` + unreachable source index → `add` succeeds; index fetch counters show no Sources work.

### F-23 — A truncated index file poisons the cache permanently · *low-medium, confirmed*
`index_loader.py:261-263` (loopback: `urlretrieve` to the final `Packages.gz`) + `resolver.py:217-226`
(`ensure_indexes` retry loop never deletes a failed destination) → a truncated `.gz` persists and is preferred by
`resolve_index_path` forever; every later `add` dies on `BadGzipFile`. The verified remote path's final write
(`index_loader.py:291-293`) is likewise non-atomic (contrast the payload `.part`+rename discipline).
**Fix**: write indexes like payloads (`dest + ".part"` → fsync/verify → `os.replace`); remove a failed destination
in the retry loop before `continue`. Optional self-heal: treat a corrupt cached index as a cache miss.
**Tests**: HTTP fixture that drops the connection mid-index → next `add` re-fetches cleanly; pre-seeded truncated
`.gz` → recovered, not a crash loop.

---

## Config / CLI / database

### F-20 — Mistyped non-depth config values crash with raw tracebacks (exit 1) · *medium, confirmed*
Only the two depth keys are type-validated. `base_dir = 123` → `TypeError` (`paths.py:9`); non-table `archive` →
`AttributeError` (`config_loader.py:192`); `binary_cache = 5` → `AttributeError` in `expand_path`;
`fetch.source = "yes"` is accepted and echoed as a *string* where the contract says boolean. The 014 contract
promises structured config errors (exit 102, JSON) for config failures generally.
**Fix**: declarative type map next to `_KEY_MAP`; validate in the `merged` loop; raise `ConfigError("<key> must be
<type>, got: …")`; booleans accept only real TOML booleans. All config failures flow through the existing
ConfigError → exit 102 (+ JSON) path.
**Tests**: parametrized mistyped cases → exit 102, error names the key, `config --format json` emits the JSON payload;
`"yes"` for `fetch.source` rejected.

### F-21 — Local-overlay depth values silently dropped when defaults lack the section · *medium, confirmed*
`config_loader.py:175-181`: overrides apply only `if tup in defaults_flat`. A `project.local.toml` depth override is
silently ignored when `project.defaults.toml` lacks `[resolution]` — violating the contract that a bad depth "in
either project TOML" fails at load, and dropping valid overrides in absent sections (bites the hand-written/profile
drop-in story). Shipped defaults carry every section, so the boxed layout is safe — but trimmed profile defaults
lose validation and overrides.
**Fix**: adopt local overrides for **known** keys regardless of presence in defaults (`if tup in _KEY_MAP`), validate
the merged result; keep semantic differences only for `[[archive.source]]` replacement.
**Tests**: defaults without `[resolution]` + local `-2` → ConfigError naming the key, exit 102; local `2` → applied and
flagged as override.

### F-22 / GLM M-5 — `show` / `list` / `verify` create a stray 0-byte DB and traceback on an uninitialized cache · *medium, confirmed*
`show.py:133,138-164`, `list.py:14`, `verify.py:18-20` open `sqlite3.connect` directly (creating a 0-byte file when
absent) instead of gating through `validate_schema_or_error`. `show vifm` before `init` → raw traceback, exit 1,
**and a leftover 0-byte `package_mgmt.db`** that later makes gated commands misleadingly report the DB as "stale".
**Fix**: route the three commands through the same existence+schema gate as their siblings (missing → JSON/text error
+ 101; stale → rebuild message + 101).
**Tests**: `show`/`list`/`verify` with no DB → exit 101, clean message, **no file created**; schema-mismatched DB →
shared stale-schema path.

---

## Cross-cutting notes

- **Cluster the resolver work.** F-8/F-11/F-12 (alternatives & parser) and the source-linkage F-9/F-10 all touch
  `resolver.py` + `dependency.py`; fixing them together with one fixture pack (alternative groups with constraints,
  arch-restricted deps, version-skewed source) is much cheaper than separate passes.
- **Mind the contract tests when changing error paths.** The exit-code/JSON contracts live in
  `specs/014-base-profiles/contracts/cli.md` and are asserted in `tests/contract/test_cli.py`; F-20/F-21/F-22 fixes
  should extend that table, not diverge from it.
- **Runner independence.** After any fix, run *both* `pytest` and `python -m unittest discover -s tests` (B-6);
  a green pytest alone currently proves less than it should.
- **Docs inheritance.** Where specs/docs promised behavior the code must now gain (F-17's re-check semantics,
  F-18's exclusion reporting, F-16's redirects), update the relevant spec clarifications/docs alongside.
