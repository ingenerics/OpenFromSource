# Alpha Review — Detailed Findings (F-8 … F-23)

Companion to [`ALPHA-REVIEW.md`](ALPHA-REVIEW.md). This file expands each high/medium finding with enough context to understand and fix it without re-doing the investigation. The seven P0 blockers are already described in depth in `ALPHA-REVIEW.md` §4 and are not repeated here.

**How to read each entry**

- **What** — the behavior and the code involved.
- **Scenario** — the smallest concrete input that produces the wrong outcome.
- **Root cause** — why the code does this.
- **Fix** — concrete change(s). Line numbers refer to the state at review time (branch `main`, commit `995cf3e`).
- **Tests** — what to add/extend so the fix stays fixed.

**Status legend**: *confirmed* = reproduced by runtime repro or traced this review; *reported* = single-reviewer, code-evidenced.

| ID | Sev | Area | One-liner |
|---|---|---|---|
| F-8 | high | resolver | satisfied alternative leaves an error → `add` exits 1 |
| F-9 | medium | resolver | explicit `Source:` version discarded → wrong source linked under skew |
| F-10 | medium | resolver | source looked up in primary release, not the binary's own release |
| F-11 | medium | parser/resolver | version constraints on `\|` alternatives dropped |
| F-12 | medium | parser | `[arch]`-restricted and multi-profile deps silently dropped |
| F-13 | medium | verify | `verify` ignores version constraints → broken cache says COMPLETE |
| F-14 | high | resolver | batch rollback leaves stale in-memory registrations → silent holes |
| F-15 | medium | resolver | build-dep pass re-walks all historical sources on every `add` |
| F-16 | medium | downloader | HTTP redirects become bogus "checksum mismatch" failures |
| F-17 | medium | downloader | verified source files never rechecked / re-fetched |
| F-18 | medium | handoff | records outside configured components dropped but marked delivered |
| F-19 | medium | handoff | `apply.sh` captures apt output → noise + bash error on every success |
| F-20 | medium | config | mistyped non-depth values crash with raw tracebacks (exit 1) |
| F-21 | medium | config | overlay silently drops depth keys when defaults lack the section |
| F-22 | medium | cli/db | `show`/`list`/`verify` create a stray 0-byte DB and traceback |
| F-23 | low | index | truncated index file poisons the cache permanently |

---

## Resolver & dependency model

### F-8 — A satisfied alternative still leaves the failed primary's error behind; `add` exits 1 · *confirmed*

**What.** When the resolver fails to resolve the primary of an alternative group `a | b`, it appends an error to `stats["errors"]` **before** trying the alternatives. When a later alternative succeeds, nothing removes the stale error. `handle_add` maps any non-empty `stats["errors"]` to `EXIT_ERROR` (`cli/commands/add.py:141-143, 185-188`), so the command reports failure for a fully satisfied dependency.

**Scenario.** `app` has `Depends: ghost | realpkg`, only `realpkg` exists in the index. `ghost` fails → `resolver.py:635` appends `"Virtual package 'ghost' has no providers in the index"`; the fallback at `resolver.py:794-810` then resolves `realpkg`. Result: complete closure on disk, but `add` exits 1 and prints the error; in `--file` mode the package is counted among the failures. Fires routinely in real archives, where the first alternative often lives in an unconfigured component (e.g. non-free) or is versioned away.

**Root cause.** Error emission happens inside `_resolve_one_inner` at first failure (`resolver.py:626-636, 675-681`); the alternative-retry wrapper (`resolver.py:794-810`) has no way to know which errors belong to the attempt it just superseded.

**Fix.** Defer error commitment for alternatives: resolve the group as a unit (try primary, then alternatives, `break` on success) and append an error only when the *whole group* fails. Minimal version: have `_resolve_one` return a failure descriptor instead of appending when called from the alternative loop, or snapshot `len(stats["errors"])` before the group and truncate back on success. Keep per-segment error attribution (`_segment_failed`) semantics intact.

**Tests.** Unit: `a | b` with only `b` present → closure contains `b`, `stats["errors"] == []`, `handle_add` returns 0. Integration: `add` a fixture package whose first alternative is absent → exit 0; batch `--file` summary shows zero errors.

### F-9 — The explicit version in the `Source:` field is discarded → binaries link to the wrong source under archive skew · *confirmed*

**What.** Debian binaries may name their source explicitly, including a version: `Source: foo (1.0-1)`. The resolver parses this field but throws the version away — `src_name, _ = parse_source_field(...)` (`resolver.py:740`) — and then picks the source as the **highest version present in the index** (`lookup_source_by_source` / `lookup_source`, `resolver.py:743-748`).

**Scenario.** During an archive transition the source `foo 2.0-1` has been uploaded but the amd64 binaries are still `foo-bin 1.0-1+b1` with `Source: foo (1.0-1)`. Resolution links `foo-bin 1.0-1+b1 → foo 2.0-1`: the cache fetches a source that does not correspond to the binary — a direct violation of Binary-Source Consistency (Constitution III). `download` happily fetches it, `verify` accepts it, and the graybeard "rebuild what you run" promise quietly breaks. binNMUs (`+b1` suffixes) make this a steady-state occurrence in a real archive, not an edge case.

**Root cause.** The source-selection path is "name → newest in release" everywhere; the parsed explicit version never participates.

**Fix.** Prefer exactness: (1) if the `Source:` field carries a version, look up that exact `(name, version)` first; (2) else derive the expected source version from the binary version via `source_version_from_binary` (already correct, incl. `+bN` stripping) and try that exact version; (3) only then fall back to "newest in release" — and log when the fallback diverges from the expected version. The lookup helpers in `repository.py`/`index_loader.py` need a version-exact variant (the index keeps all versions, so this is a filter, not a data problem).

**Tests.** Unit: index with source versions {1.0-1, 2.0-1} and binary `foo-bin 1.0-1+b1` (`Source: foo (1.0-1)`) → links to `foo 1.0-1`. Also cover the no-`Source:`-field case (name == package, version derived from binary, `+bN` strip).

### F-10 — Source lookup is keyed to the primary release, not the binary's actual release · *confirmed*

**What.** When a binary was resolved from a **fallback** release (e.g. `trixie-backports` via the `--source`/fallback machinery), its source is still searched first in the **primary** release: `lookup_source_by_source(src_name, primary_source)` (`resolver.py:743-748`). Only on miss does it fall back to a global lookup.

**Scenario.** `libx 9.0-1~bpo13+1` is resolved from `trixie-backports`; the index also has source `libx 8.0-1` in `trixie`. Primary-source lookup succeeds (name match), so the backports binary is linked to the **trixie 8.0** source — a version-mismatched pair, silently. Combined with F-9, every constrained/fallback resolution risks a wrong binary↔source link.

**Fix.** Look the source up in `binary_entry.release`/`component` first (the binary's own origin), then fall back to other configured sources; combine with the F-9 version-exact lookup (release + version together). The data is already on `binary_entry`.

**Tests.** Two-source fixture: `libx 8.0` in `trixie`, `libx 9.0` in `trixie-backports`; resolve the backports binary → source must be the backports `9.0`.

### F-11 — Version constraints on alternatives (`a | b`) are silently dropped · *confirmed*

**What.** `DependencyGraph.parse_depends_line` (`dependency.py:56-86`) keeps the version constraint only on the *first* alternative; for later alternatives it computes the constraint and then discards it (`vc` at line 72 is unused for alts — only the bare name goes into `alt_names`, line 77). Resolution then asks for the alternatives **without** any constraint: `resolver.py:460` passes `None` (build-dep path) and `resolver.py:808` passes nothing (runtime path).

**Scenario.** `Build-Depends: debhelper-compat (= 13) | debhelper (>= 13)` with only `debhelper 10` in the index → `debhelper 10` is registered and linked as the build tool, **no error**. The source's declared requirement is violated and "every fetched source is buildable" (FR-013) no longer holds. Same shape for runtime deps: `foo (= 9.9) | bar (= 2.0)` with only `bar 1.0` → `bar 1.0` accepted silently.

**Root cause.** `DependencySpec.alternatives` is a tuple of *names only* — the data model cannot represent per-alternative constraints, so the parser's loss is baked into the model.

**Fix.** Two steps: (1) extend the model — represent alternatives as name+optional constraint (e.g. `alternatives: tuple[dict | str, …]` or a small `Alternative` dataclass), update `serialize`/`deserialize` (JSON shape is internal, no DB schema change — the field is opaque JSON); (2) pass each alternative's own constraint at both call sites (`resolver.py:460, 808`) and in `_dep_satisfied` (`verify.py:139`) when walking alternatives.

**Tests.** Parser round-trip preserving per-alternative constraints; resolver: only-unsatisfying-version-alternative present → error recorded, no silent link; satisfying version → resolved.

### F-12 — Arch-restricted dependencies (`[amd64]`) and multi-token build profiles are silently dropped by the parser · *confirmed*

**What.** The parse regex (`dependency.py:66`) matches `name[:qualifier] [(constraint)] [<\!profile>]` and nothing else. Two common real-world constructs fail to match the whole token and are then **silently skipped** (`if m:` guard, no error): architecture-restriction lists (`foo [amd64]`, `gperf [amd64 i386]`) and multi-token build profiles (`<!nocheck !cross>`, `<stage1 stage2>`).

**Scenario.** `Build-Depends: debhelper-compat (= 13) <!nocheck !cross>, gperf [amd64 i386], realtool` parses to just `[realtool]` — required build tools vanish without a trace, undermining FR-013 ("every fetched source is buildable"). The same entries also disappear from the stored `dependencies` JSON, which additionally violates spec 002 FR-009 ("store as-is").

**Root cause.** The regex has no group for `[…]` and only one optional `<…>` group; and the parser treats "regex did not match" as "skip" with zero diagnostics, so the loss is invisible in tests and logs.

**Fix.** (1) Extend the token grammar: optional arch list after the constraint/profile parts; capture and evaluate it — drop the entry only when the restriction *excludes* the target arch (`amd64`; handle `!arch` negations), keep it otherwise. (2) Accept multiple profile tokens and evaluate conservatively (current code ignores the single token's value — over-inclusive, which is the safe direction for a cache; keep that). (3) Never drop silently: log at WARNING any dep token that doesn't parse, so the next grammar gap is visible. Notes: single-token profiles like `<!nocheck>` already parse; arch *qualifiers* (`pkg:any`, `pkg:native`) are already carried in `architecture`.

**Tests.** Parser: `foo [amd64]` kept (arch=amd64 matches target), `foo [i386]` dropped, `foo [!amd64]` dropped; multi-profile token kept with constraint intact; unparseable token → warning emitted (assertLogs) and the rest of the line still parses.

### F-13 — `verify` reports version-constrained deps satisfied without checking the version · *confirmed*

**What.** `_dep_satisfied` (`verify.py:135-157`) has two checks per candidate name. The first — a direct-name probe — is:

```python
if conn.execute("SELECT 1 FROM binaries WHERE package_name = ? AND status='current' LIMIT 1", (n,)).fetchone():
    return True
```

`spec.version_constraint` is never examined on this path. (The second path — provides — *does* check, via `satisfies_constraint(pv or bv, ...)`.)

**Scenario.** `app` Depends `libfoo (>= 2.0)`; the cache contains only `libfoo 1.5-1` (resolution registered the wrong version — exactly the class of bug verify exists to catch, cf. F-11). `verify --graph` reports `Unresolved deps: 0 / Status: COMPLETE`, exit 0 — and the operator's apt refuses the install over the version constraint. The project's headline completeness gate is weaker than the gate it fronts for.

**Fix.** On the direct-name path, select the current binary's **version** and return `satisfies_constraint(version, spec.version_constraint)` (constraint `None` ⇒ satisfied). Walk alternatives with their own constraints (ties into the F-11 model change). Providers: keep the existing `pv or bv` behavior (versioned provides checked against `provides_version`, else provider version) — documented, deliberately over-inclusive.

**Tests.** Scratch DB / fixture: dep with `>=` constraint and only-too-old binary → unresolved + INCOMPLETE + non-zero exit; exact-`=` match → clean; alternative with constraint only satisfied by the *second* alternative → clean.

### F-14 — Mid-batch exception: DB rolled back, in-memory registrations not → later segments silently resolve incomplete closures · *confirmed*

**What.** `resolve_batch` wraps each request's resolution in `try: _resolve_one(...); conn.commit() / except: conn.rollback()` (`resolver.py:314-319`). Rollback discards the segment's **database** writes, but the in-memory batch state — `_visited` and `_batch_registered` — is **not** rolled back. Later segments of the same batch hit the early-return at `resolver.py:510-516` ("already visited and registered") for names that were in fact never committed.

**Scenario.** Batch `add bad good`: resolving `bad` registers `lib` (inserted, uncommitted), then an unexpected exception fires (disk error, `sqlite3.OperationalError`, a bug) → rollback removes `lib` from the DB but not from `_visited`/`_batch_registered`. Segment `good` (Depends: `lib`) then treats `lib` as done: `_resolve_one` returns early with **no error recorded**. `good` completes with `errors=[]`, `add` exits 0, and the cache has a closure hole that only `verify --graph` would catch — if the hole is name-satisfiable-verify-loose (cf. F-13), possibly not even then.

**Root cause.** Two transaction domains (DB and in-memory walk state) with rollback implemented for only one. The per-segment `try` exists for best-effort batch semantics ("one failure must not kill the batch"), which is correct — the missing half is state healing after a rollback.

**Fix.** After a segment rollback, make the batch state trustworthy again. Simplest robust option: clear `_visited`, `_batch_registered`, `_batch_constraints` and re-seed `_batch_registered` from the DB (registered versions of current binaries for names requested so far) — the walk is idempotent per name (upserts), so a re-walk is cheap and correct. Also mark the failed segment's error clearly (already appended as `str(e)`). Narrow trigger (needs an *unexpected* exception; normal resolution failures don't raise) — but the failure mode is invisible, so fix rather than document.

**Tests.** Force an exception mid-segment after a successful registration (e.g. monkeypatch `upsert_binary` to raise on the second package), batch of two requests sharing a dep → second segment must re-resolve the dep and end with it present in the DB, or report an error; never silently skip.

### F-15 — The build-dep pass re-walks **all** historical sources on every `add` (perf + scope leak past the configured depth) · *confirmed*

**What.** `_resolve_build_deps` (`resolver.py:407-490`) opens each pass with `SELECT id, build_depends FROM sources WHERE build_depends IS NOT NULL` and filters with a per-call `processed_ids` set — i.e. only within one call. Every `add` therefore re-iterates **every source ever registered** in this cache: runtime re-registration calls hit `_visited` and no-op out, links re-MIN-merge, but the full scan and its per-source work still happen, and `stats` are inflated by re-processed tools. Two consequences: (1) on a mature graybeard cache (tens of thousands of sources) each `add` gets steadily slower; (2) after an unlimited run, a later run at a *shallower* depth re-resolves build tools for retained-but-out-of-scope sources — retention (FR-014) means *keep what exists*, not *keep extending it*; the depth cap is supposed to bound new work (FR-004).

**Fix.** Process only sources created in the current batch: have `upsert_source` report insert-vs-existing, collect the new `src_id`s during the runtime pass, and seed `processed_ids` with everything already in the DB (or iterate only the batch's list). Ratification detail: when the runtime pass *links an existing source to a new binary*, that source's build tools are already resolved (MIN-merged) — no need to redo it.

**Tests.** Two sequential `add` runs (distinct packages): second run's `build_tools`/`build_dep_links` counters count only the second package's sources; a shallow-after-deep rerun resolves **zero** new build tools for retained sources (mirrors `test_rerun_shallower_retains_fetched_sources` but asserts counters, not just presence).

## Download / verify / operator track

### F-16 — HTTP redirects are treated as successful responses → permanent per-file failure with a misleading diagnosis · *confirmed*

**What.** The downloader uses a hand-rolled keep-alive opener (`_PooledOpener`, `downloader.py:95-115`) over raw `http.client`. It raises `HTTPError` only for `status >= 400` (line 109); a `301/302/307/308` response is returned to `_fetch_to_part` as if it were the payload. Its body (an empty body or a small HTML page) is then hashed, fails the index checksum, and surfaces as **"checksum mismatch"** — a *permanent*, non-retried error, so the affected files never download.

**Scenario.** Any mirror setup that redirects: `http://` → `https://` canonicalization at the pool path, a CDN that 302s to a mirror node, a moved tree (`/debian/` → `/archive/debian/`). `urllib.request.urlopen` would follow these; `_PooledOpener` does not (verified side-by-side by the reviewer: urlopen → 200+bytes, pooled opener → 301+empty body). Integrity was never at risk (the hash gate stops bad bytes) — availability and diagnosability are.

**Fix.** Follow redirects in `_PooledOpener.__call__`: on 3xx with a `Location` header, resolve it (`urllib.parse.urljoin`), re-issue the GET (same-connection when same origin; close+reconnect otherwise), cap at a small hop count (e.g. 5) and reject redirect loops; only then apply the `>= 400` raise. Keep the existing per-mirror connection reuse. Alternative: note that mirror URLs are config-controlled and *fail fast with a clear message* (`redirect to <url> — update mirror config`) — much less code, but breaks on transient CDN redirects; following is the standard apt-like behavior.

**Tests.** Local HTTP fixture that 301s `/pool/...` to `/moved/pool/...`: download succeeds and hashes. Redirect without `Location`, and a loop → clear permanent error, `.part` cleaned up.

### F-17 — A verified source whose pool file is deleted or corrupted is silently ignored forever · *confirmed*

**What.** The binary phase is crash-convergent: every run, each *verified* record's file is re-checked (size, then SHA-256) and re-fetched / un-marked if bad (`downloader.py:335-341`). The source phase has **no equivalent**: `pending_sources` (`downloader.py:402-412`) selects only `verified = 0`, and nothing else ever looks at source payload files (`verify` inspects DB only). Related deviation: when a source is left unverified because one file failed, the next run re-downloads **all** its files — there is no per-file skip, contrary to the "re-run downloads only what is still missing" intent.

**Scenario.** Graybeard cache; a source tarball is lost to media corruption or manual cleanup. `download` reports nothing to do; `verify` says COMPLETE; the operator's tarball is gone and no command will restore it — `sources.verified = 1` no longer means "all files present and verified" (spec 011).

**Fix.** Mirror the binary phase for sources: each run, for every `verified = 1` source, check (size → SHA-256) each of its index-listed files; on failure, set `verified = 0` so the normal pending path re-fetches only the missing files (add a per-file existence check in the source loop to get the skip behavior and the F-17-deviation fix in one go).

**Tests.** Delete one file of a verified source → next `download` re-fetches exactly that file and re-marks verified; corrupt (size-matching but hash-wrong) file → un-marked and re-fetched; nothing re-downloaded when all files intact.

### F-18 — `generate_handoff` silently drops verified records outside the configured components — and marks them delivered · *confirmed*

**What.** `generate_handoff` groups **all verified DB records** by their own `(release, component)` (`handoff.py:1142-1145`), then processes every such pair (loop at 1162) — but the index-write loop (1201-1212) iterates only over `bases`, the **configured** pairs. A record whose `(release, component)` is no longer configured (e.g. `contrib` removed from config after registration) passes every per-record check, gets its paragraph appended (1198) and its id appended to `delivered_ids` (1199) — and its stanza is then never written anywhere. No exclusion list, no warning: `excluded_pool`/`unconfirmable` stay empty, the CLI marks the record **delivered**. The operator never sees the package (spec 012 FR-003: every verified package MUST be offered), and the false delivery record later surfaces in `sync` as a fatal "excluded delivered … defect state".

**Fix.** Before the record loop, compute `base_set = set(bases)` and exclude any `(release, component)` not in it — with a warning and by refusing to mark those ids delivered (exactly the check `generate_diff` already has at `handoff.py:1009-1011`). Decide explicitly whether this is a soft exclusion (warn, skip) or a hard config error; either way it must be *loud*.

**Tests.** Register+verify a record under `trixie/contrib`, re-run with config reduced to `main` → handoff output omits it **and** reports it (in exclusions and/or as non-zero exit), and `delivered_ids` excludes it (delivery marker untouched).

### F-19 — `apply.sh` captures `apt update` output into `$new_files` → bash error + garbage summary on every successful apply · *confirmed (reviewer-reproduced)*

**What.** In the generated `apply.sh`, `apply_main` line ~534 does `new_files="$(apply_configure …)"` — capturing the function's *entire stdout*. But `apply_configure` (~465-498) runs `run_apt_update` in between, and apt prints its progress to **stdout** (`Ign:1 …`, `Get:2 …`, `Reading package lists…`). Only the final `echo "$new_files"` was meant to be captured.

**Scenario.** Every successful operator apply prints `bash: [: Ign:1 …: integer expression expected` (the `[ "$new_files" -eq 0 ]` test), the "(already applied)" branch is never taken, and the summary line shows raw apt output where a number should be. State and exit code are correct — but the operator-facing tool emits an error-looking message on its happy path, right where trust matters most.

**Root cause.** Using a command's stdout as its return channel while a chatty child (apt) writes to the same stream. `bootstrap.sh`'s configure step doesn't have the bug (not inside `$()`).

**Fix.** Make the channel explicit: have `copy_new_files` write the count to a dedicated file/FD (or print apt output to stderr — `run_apt_update ... >&2`), and keep `apply_configure`'s stdout reserved for the count. Check `[ "$new_files" -eq 0 ]` still parses (quote defensively).

**Tests.** A shell-level test that runs the generated `apply.sh` against the fixture diff in a scratch `state_root` and asserts stderr is empty and the summary line matches `New files: <number>`.

## Config / CLI / database

### F-20 — Mistyped non-depth config values crash with raw tracebacks (exit 1), not structured config errors · *confirmed (this session)*

**What.** Only the two depth keys get type validation. Anything else mistyped escapes as an unhandled exception from the loader — before `main()`'s `ConfigError` handler can make it a clean exit-102:

- `base_dir = 123` → `TypeError` from `os.path.expanduser` (`paths.py:9`), full traceback — reproduced live.
- `archive = "x"` (non-table) → `AttributeError: 'str' object has no attribute 'get'` at `config_loader.py:192`.
- `binary_cache = 5` → `AttributeError: 'int' object has no attribute 'replace'` in `expand_path`.
- `fetch.source = "yes"` → silently accepted; JSON output then emits `"source": "yes"` (string) where the contract type is boolean.

**Fix.** Add a small declarative type map next to `_KEY_MAP` (field → expected type), validate in the `merged` loop, and raise `ConfigError("<key> must be <type>, got: <value>")`. Booleans: accept only real TOML booleans for `fetch.source`. All config failures then flow through the existing ConfigError → exit 102 (+ JSON payload in `--format json`) path the 014 contract promises.

**Tests.** Parametrized over the mistyped cases above: each → exit 102, error names the key, `config --format json` emits the JSON payload. Assert the boolean-only case rejects `"yes"`.

### F-21 — Local-overlay depth values are silently dropped when the defaults file lacks the section · *confirmed*

**What.** Overrides are adopted per key only when the key exists in defaults: `for tup, value in local_flat.items(): if tup in defaults_flat: overrides_raw[tup] = value` (`config_loader.py:175-177`). So with a `project.defaults.toml` that has no `[resolution]` section, a `project.local.toml` saying `max_source_dep_depth = -2` is **silently ignored** — loads as the dataclass default 0, no error, `overridden` empty — directly violating the documented contract that an invalid depth "in either project TOML" fails at load. Valid overrides in absent sections are dropped the same way, contradicting the documented overlay story ("local applies on top of the profile's defaults").

**Why it matters for the profiles.** The shipped defaults carry every section, so the boxed layout is safe; but profiles are advertised as drop-in defaults users may hand-trim — and someone trimming to just `[fetch]` loses both validation and overrides.

**Fix.** Adopt local overrides for **known** keys regardless of presence in defaults (`if tup in _KEY_MAP` / allowlist), validate the merged result — unknown keys remain ignored (tested, intended). Keep semantic differences only for `[[archive.source]]` replacement.

**Tests.** Defaults without `[resolution]` + local `-2` → ConfigError naming the key, exit 102; same with local `2` → applied and flagged as override in `config` output.

### F-22 — `show` / `list` / `verify` on an uninitialized cache create a stray 0-byte DB and die with a raw traceback · *confirmed (this session)*

**What.** Every other DB-touching command gates on the database via `validate_schema_or_error` and emits the clean `{"error": "Database not found … run 'init' first"}` + exit 101 when it's missing. The read-only trio doesn't: `show.py:133,138-164`, `list.py:14`, `verify.py:18-20` open `sqlite3.connect(db_path)` directly — which **creates** a 0-byte file when absent — then fire `SELECT`s that die with `sqlite3.OperationalError: no such table: packages`, escaping as an unhandled traceback (exit 1, no JSON error payload).

**Scenario (reproduced live).** Fresh machine, run `package_mgmt show vifm` before `init` → full traceback; `package_mgmt.db` now exists but is empty. Worse, the *next* gated command (e.g. `download`) sees the stray file and reports it as "**stale** … delete and rebuild" (exit 101): misleading repair advice for a file the tool itself just created.

**Fix.** Route the three commands through the same existence+schema gate as their siblings (missing → JSON/text error + 101; stale → rebuild message + 101). No new machinery needed.

**Tests.** `show`/`list`/`verify` with no DB → exit 101, clean message, **and no file created**; on a schema-mismatched DB → the shared stale-schema path.

### F-23 — A truncated index file poisons the cache permanently (loopback path; non-atomic write on the verified path) · *confirmed (partial-file behavior reproduced)*

**What.** Two soft spots around index writes: (1) on the loopback/test path (`index_loader.py:261-263`), `urlretrieve` writes directly to the final `Packages.gz`; the retry loop in `PackageResolver.ensure_indexes` (`resolver.py:217-226`, `except Exception: continue`) does **not** delete a failed destination, so a truncated file persists—and since `resolve_index_path` prefers an existing `.gz`, every later `add` dies with `BadGzipFile` until manual repair. (2) On the verified remote path, the signature+checksum gate runs *before* writing (good), but the final write itself (`index_loader.py:291-293`) is not atomic — kill the process mid-write and the same poisoned-file state results (next consumer only checks existence).

**Fix.** Write indexes like payloads are already written: `dest + ".part"` (or temp file) → fsync/verify → `os.replace`; and in `ensure_indexes`' retry loop, remove a failed destination before `continue`. Self-heal option: treat a corrupt cached index as a cache miss and re-fetch.

**Tests.** HTTP fixture that drops the connection mid-index → next `add` re-fetches cleanly instead of erroring; pre-seeded truncated `.gz` at the index path → recovered by re-fetch, not a crash loop.

---

## Cross-cutting notes for whoever picks these up

- **Cluster the resolver work.** F-8/F-11/F-12 (alternatives & parser), F-9/F-10 (source linkage) and the P0-1/P0-2/F-14/F-15 depth-batch changes all touch the same ~300 lines of `resolver.py` + `dependency.py`. Fixing them in one branch with one fixture pack (diamond chains, alternative groups with constraints, arch-restricted deps, version-skewed source) is much cheaper than six separate passes, and the tests share scaffolding.
- **Mind the contract tests when changing error paths.** The exit-code/JSON contracts live in `specs/014-base-profiles/contracts/cli.md` and are asserted in `tests/contract/test_cli.py`; F-20/F-21/F-22 fixes should extend that table, not diverge from it.
- **Runner independence.** After any of these fixes, run *both* `pytest` and `python -m unittest discover -s tests` (see P0-6 in `ALPHA-REVIEW.md`); a green pytest alone currently proves less than it should.
- **Docs inheritance.** Where specs/docs promised behavior the code must now gain (F-17's re-check semantics, F-18's exclusion reporting, F-16's redirects), update the relevant spec clarifications/docs alongside — per the project's own AGENTS.md rule to keep SDD artifacts truthful.






