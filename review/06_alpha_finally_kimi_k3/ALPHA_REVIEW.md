# Pre-Alpha Release Review

**Scope**: full technical + functional review of `main` (post-spec-024, phases 1–7;
`src/package_mgmt/` ≈ 11 660 lines, `tests/` ≈ 21 490 lines, recorded baseline 914 tests).
**Method**: static file inspection only. No project commands, tests, builds, or network
actions were executed, per the review request (read-only file reads and code search were
used for navigation). Every finding below was traced through the current source; line
references use `<file>:<line>` against the current tree.

**Inputs reviewed**: all of `src/package_mgmt/` (complete read of every module, including
the six largest — `handoff.py`, `resolver.py`, `package_stack.py`, `downloader.py`,
`alignment.py`, `debgen.py`), `pyproject.toml`, `project.defaults.toml`,
`project.local.toml`, `docs/profiles/*.toml`, `recipes/hyprland.toml`, `scripts/`,
`manifests/`, `docs/001–005`, `.github/workflows/ci.yml`, `tests/` (suite layout plus
targeted reads of 15 test modules), the new spec corpus `specs/020…024`, and the prior
review corpus `review/01…05` (in particular the last full review,
`review/05_alpha_final_kimi_k3/ALPHA_REVIEW.md`, whose findings were re-verified one by
one— see §2). Skipped per instruction: the git-ignored `repos/`, `staging/` trees (huge,
no review value), `review/` regenerated artifacts. Tutorials in progress are out of scope.

---

## 1. Verdict

The project is materially closer than at the 05 review: **all three code-level findings
of the review-triage spec 020 (N-2a, N-3, N-4) are verifiably fixed**, the two
documentation resolutions (N-1, N-2b) are properly written down, and the 021–024 feature
set (local patch lane, apt VersionHash fidelity, version-alignment check, source-stack
packaging) is implemented with the same care as the rest of the codebase — deterministic
artifacts, atomic writes, constraint-aware resolution, gated operator scripts.

However, this review found **new defects introduced or exposed by the 021–024 work, plus
one packaging showstopper that has been present since ensure-base landed**:

- **R-1 (blocks standard installs)**: the CLI imports `pycdlib` at startup through the
  `ensure-base` command chain, but `pycdlib` is only a *dev* extra. A documented
  `pip install -e .` leaves the user with a CLI that dies on `ModuleNotFoundError` for
  *every* command, including `--help`.
- **R-2 (breaks the local-component flows)**: after any `import-deb`/`package-stack`,
  `download` fails (exit 1) with a misleading "cached index missing for <release>/local"
  command error, even when there is nothing to fetch. The binary-phase re-check grouping
  lacks the `local` exclusion the source phase already has.
- **R-3 / R-4 / R-5**: import supersede gap, `align-repo` hard-coded trixie baseline,
  signing key propagates inside the handoff payload.

**Recommendation: fix R-1 and R-2 before tagging (both are small, surgical changes);
decide deliberately on R-3…R-5; then re-run the recorded baselines and the two never-run
physical proofs (FAT USB bootstrap; graybeard DVD-1 live run).** The fresh-cache
3-profile promise and the operator installability chain remain sound by inspection.

## 2. Fix-status re-verification of the 05 review corpus

Every finding of `review/05_alpha_final_kimi_k3/ALPHA_REVIEW.md` (HEAD `55cdf26`,
post-019) was re-checked against the current source:

| 05 finding | Current state (evidence) |
|---|---|
| **N-1** stale `provides` after offline `init` | **Resolved as documented limitation** (spec 020). `docs/001-quickstart-howto.md:196-205` now states `init` requires connectivity and an offline/partial `init` is not a usable cache; resolver code intentionally unchanged (`populate_provides_table` still only called from `init.py:133` and `ensure_base.py:218`). Consistent. |
| **N-2a** cached-virtual source repair gated off at `-1` | **Fixed (code).** `_min_level_repair_enabled()` now returns `depth != 0`, i.e. enabled at `-1` (`resolver.py:548-559`), with the regression test `test_virtual_provider_source_repaired_at_unlimited_depth` (`tests/unit/test_virtual_resolution.py:~395+`). |
| **N-2b** repair does not re-walk provider subtree | **Resolved as documented limitation** (spec 020): graybeard declared stand-alone/fresh-cache-only (`docs/profiles/graybeard.toml` header; `docs/002`). Residual gap at developer(k)→developer(k+n) transitions remains (informational-only there) — see residual L-items below. |
| **N-3** constraint-blind "already satisfied" probes | **Fixed (code).** The shared `_group_satisfying_member` evaluates each member's version against its own constraint (`resolver.py:728-776`) and is used by the runtime probe, the build-dep twin, and the link block (`resolver.py:832-858, 1174-1205`). |
| **N-4** keyring absent from wheel | **Fixed (declaration).** `pyproject.toml:23-24` declares `[tool.setuptools.package-data] package_mgmt = ["data/*.gpg"]`. **Caveat**: the artifact itself still has to be built and inspected before tagging — and the dependency list has a worse hole (R-1). |
| **L-1** zero-pending `sync` skips delivered-defect check | **Still open.** `sync.py:62-70` regenerates the diff and returns without inspecting `stats.excluded_delivered`. |
| **L-2** stat miscounts (`sources`, `build_tools`, `reverified`) | **Still open** (cosmetic; `resolver.py:624, 867-869`; `downloader.py:422, 478-479`). |
| **L-3** `merge_depends_text` first-wins constraint dedup | **Still open** (`index_loader.py:99-106`): a stronger `Pre-Depends` constraint on the same name is dropped in the merged closure view. Rare. |
| **L-4** handoff stanza can emit `Size: None` | **Still open** (`handoff.py:982`): no guard when the index paragraph lacks `Size`. Defensive-only. |
| **L-6** admin-side FAT crash in `_ensure_pool_link` | **Still open** (`handoff.py:1126-1131`): raw `OSError` traceback instead of `HandoffError`. |
| **L-8** downloader has no index self-heal | **Still open** (`downloader.py:430-437, 579-586`); it fails *loudly* (exit 1) rather than silently, divergent from the resolver's `_load_or_fetch_index` self-heal (`resolver.py:55-97`). |
| **L-9** ensure-base Phase-B wedge | **Still open**: Phase A commits, a Phase-B transport failure leaves a half-built cache that `_assert_fresh_cache` (`ensure_base.py:172-186`) refuses to resume. |
| **L-10** `_lookup_binary_entry` any-version fallback | **Still open** (`resolver.py:627-646`): ends at `lookup_binary(name)` with no version match or divergence log on the repair path. |
| **L-11** self-heal misses decode/parser errors | **Still open**: `INDEX_READ_ERRORS` unchanged (`index_loader.py:57`); loopback mirrors only. |
| **L-12** silent batch-constraint fallback | **Still open** (`resolver.py:906-914`): no log line when the sequential last-writer semantic fires. |
| **L-13/§3** docs/CI/agent-map drift | **Partially fixed.** CI now enforces *both* runners (`.github/workflows/ci.yml` pytest + unittest legs) and `tests/REGRESSION.md` is re-recorded post-024 (914 tests). Still stale: `AGENTS.md` SPECKIT block and README figures — see R-6. |

---

## 3. New findings (this review)

Severity: **B** = blocks the alpha promise · **H** = high, fix or consciously accept before
tag · **M** = medium, same-release-if-cheap · **L** = low/polish.

### R-1 — [H/B] `pycdlib` is imported at CLI startup but is only a dev extra — every command of a standard install crashes

**Where**: `pyproject.toml:10` (`dependencies = ["python-debian"]`; `pycdlib` appears only
in `[project.optional-dependencies] dev`, line 15). Import chain at module top levels:
`cli/main.py:10` → `cli/commands/ensure_base.py:6` → `lib/service/ensure_base.py:27` →
`lib/service/iso_reader.py:12` (`from pycdlib import PyCdlib`).

**What**: building the CLI parser imports every command module, so `package_mgmt` — for
*any* subcommand, including `--help` — requires `pycdlib` at import time. A documented
standard install (`pip install -e .`, Step 2 of `docs/001`) provides only
`python-debian`; the very next step (`package_mgmt init`, Step 4) dies with
`ModuleNotFoundError: No module named 'pycdlib'`. This also undermines the 020/N-4
packaging claim in `docs/001:49-53` ("the wheel ships the keyring … standard (built
artifact)"): even with the keyring present, an installed wheel without dev extras fails
at startup. `docs/003`'s prerequisite (`sudo apt install python3-pycdlib`) does not
rescue the venv/pip paths, which do not see `/usr/lib/python3/dist-packages`. Hidden from
CI because the CI installs `.[dev]` (`.github/workflows/ci.yml`).

**Fix (one line)**: move `pycdlib` from the dev extra into `dependencies` (pure-Python,
tiny — there is no reason to make the runtime entry point import-fragile), **or**
lazy-import inside `IsoImage.__init__` and raise a clean `IsoError` naming the missing
dependency. Add a packaging contract test: fresh venv, `pip install .` (no extras),
`python -c "import package_mgmt.cli.main"` + `package_mgmt --help` must succeed; run the
same check against the built wheel before tagging (this also closes the N-4 "inspect the
artifact" follow-up properly).

---

### R-2 — [H] After any local import, `download` fails with a bogus "Cached index missing for <release>/local" error and exit 1

**Where**: `downloader.py:713-723` (`_group_current` has **no** `local` filter) vs
`downloader.py:691-710` (`_group_sources_to_check` explicitly excludes `local`, comment
"verified at import time from the supplied directory"). Command decision point:
`downloader.py:430-436` (missing index → `command_errors`), surfaced as exit 1 in
`cli/commands/download.py:117-118, 134-137`.

**What**: the binary phase re-checks *every* current row per run (the F-17 posture —
present payloads re-verified from disk). A `local` row is grouped under
`(<default-release>, local)`; no archive index exists at
`binary/dists/<release>/local/binary-amd64/Packages.gz` (nothing ever writes one —
the `local` index only exists under `handoff/` and scratch repos), so the run appends a
command error per the missing-index branch and `handle_download` exits 1 — **even when
there is nothing to download and every payload is intact**. The message ("the index is
part of the cache — restore or re-fetch it") misleads the admin toward index repair when
the real shape is normal. Reproducer shape: `init → add lib1 → download → import-deb
x.deb → download` — the second `download` exits 1. Every 021/024 user hits this on the
very next download after a stack import, and the error re-fires on *every* subsequent
download for the life of the cache.

**Coverage hole that let it through**: the only local-vs-download test,
`test_download_pending_set_excludes_local_verified_row` (`tests/unit/test_import_deb.py:339-347`),
exercises the SQL helper `pending_binaries()` (which filters `verified = 0`) — not the
engine's own `_group_current` re-check path, and no integration flow runs
`handle_download` *after* an import (`test_import_deb_flow.py` downloads only before the
import).

**Fix (trivial)**: mirror the source phase — add `AND component != 'local'` to the
engine's binary grouping (`local` records are verified at import from the supplied
payload; the handoff already handles deleted local payloads via `excluded_pool`), plus an
engine-level regression test: import a `local` deb, then run
`download_pending_binaries` → zero command errors, record stays verified.

---

### R-3 — [M] `import-deb` of a newer version leaves the older `local` version `current` — no supersede

**Where**: `import_local.py:365-367` registers via `upsert_binary` without the resolver's
`_find_current_binary` → supersede dance (`resolver.py:1118-1138`). `upsert_binary`'s
conflict key includes the version, so a second import of *another* version inserts a
second `current` row (`resolver.py:120-141`).

**What**: importing `patch-tool 1.3-0` after `patch-tool 1.2-3` leaves **both** rows
`current` + `verified`. Consequences:

- the handoff/diff emit both stanzas (`handoff.py:1521-1550`) — apt tolerates
  multi-version indexes and picks the newest, so the operator outcome is still correct,
  but the index grows with every dead revision and `sync` ships both payloads;
- `binaries`-unique readers become nondeterministic: `whole_set_verify`'s un-ordered
  `fetchone()` (`package_stack.py:514-518`), `_find_current_binary`'s `ORDER BY id`
  (oldest first — `resolver.py:111-117`), `list`'s tree for the name
  (`list.py:104-108`);
- resolver tie-breaking for names that later appear upstream picks the *oldest* current
  row for the equality check, which can yield three concurrent `current` rows for one
  name (old local, new local, archive).

`test_reimport_is_idempotent_no_duplicate_rows` (`tests/unit/test_import_deb.py:194-214`)
covers only the *same* version; nothing pins the cross-version rule.

**Fix**: at import, supersede prior `current` rows for the same `(package, architecture,
component='local')` before the upsert (mirroring the archive path), and decide what that
means for the stale payload (keep — pool files are content-addressed by name/version and
still checksummed on sync; do not delete, delivered systems may legitimately lag). Add a
cross-version unit test.

---

### R-4 — [M] `align-repo` validates against a hard-coded `trixie/main` baseline, ignoring the configured sources

**Where**: `alignment.py:184-188` (`WHERE release = 'trixie' AND component = 'main'`),
`alignment.py:29` (`BASELINE_LABEL`), `cli/commands/align_repo.py:149-150`
(`_packages_gz_path` pins `trixie/main`).

**What**: the baseline the era gate validates against is the frozen cache *by definition
of the configured first source* — yet the query and the cached-index cross-check are
hard-coded to `trixie/main`. On any non-trixie configuration (or a trixie config whose
first component is not `main`) the command reports the wrong baseline's verdict, or —
with no `trixie/main` cached index — degrades into "everything unmet". The
ecosystem-specific era tables (`ERA_COMPANION_TAGS`, `alignment.py:42-56`) being
Hyprland-shaped is per spec (023 is a Hyprland alignment tool); the *release* coupling is
not. Related CWD couplings (documentable, not defects): the manifest is written to
CWD-relative `manifests/` (`alignment.py:588`) and `--repo-dir` defaults to CWD-relative
`repos/` — both demand "run from the project root", which only the docs imply.

**Fix**: take `release`/`component` from `config.sources[0]` (align-repo already runs
under a loaded config in `main.py:301-309`) or add an explicit flag; refresh
`BASELINE_LABEL` from the same source. One config-driven unit test.

---

### R-5 — [M] The cache-local signing **private** key ships inside the handoff payload tree and travels to every operator machine

**Where**: `util/paths.py:55-59` (`handoff_gnupg_dir` = `{binary_cache}/handoff/.gnupg`),
`handoff.py:1086-1095` (persistent GNUPGHOME, key generated once).

**What**: the docs instruct the admin to copy the cache — "which now includes
`handoff/`" (`docs/001:603-607`) — onto operator media. The persistent secret keyring
lives *inside* that copied tree, so every offline machine receives the private signing
key for `offline-cache.gpg`. Two exposures follow: (a) any operator machine (or anyone
reading the stick afterwards) can re-sign *arbitrary* index content that every *other*
machine bootstrapped from the same cache trusts — the per-machine trust anchor is only
as strong as the weakest copy of the transport media; (b) on FAT media the 0700
permissions are meaningless. The persistent-GNUPGHOME design itself (regeneration keeps
a stable trust anchor) is right; only its *placement* leaks the secret into the payload.
The preceding reviews did not flag this, and it is a posture the original flow shipped on
purpose — so this is a *decision to record*, not necessarily a rewrite: the threat model
"the stick is the trust channel; operators are trusted" is defensible for the target use,
but it is currently undocumented, which makes it an accident rather than a decision.

**Fix (either)**: move the keyring out of the payload tree (e.g.
`{base_dir}/.gnupg-handoff/`, keeping `handoff/.gnupg` migration for existing caches, and
update `handoff_gnupg_dir`), **or** document the posture explicitly in README +
`docs/001` ("the private signing key travels with the cache; treat every copy of the
media as a copy of the signing key").

---

### R-6 — [L] Release paper-trail: README figures stale, `AGENTS.md` block stale, LICENSE missing

- `README.md:96-103`: "specs/ holds 22 feature specs" → **24** now; "Quality gates: **808
  tests**" → recorded baseline is **914** (`tests/REGRESSION.md`, post-024).
- `README.md:15`: "see `LICENSE` ToDo: Simlink" — there is **no `LICENSE` file** at the
  project root, and the sentence still carries the ToDo placeholder (and the "Simlink"
  typo). For an alpha tag this is release-managerial, not cosmetic.
- `AGENTS.md` SPECKIT block: still says spec 024 "planning is complete … proceed to
  `/speckit.tasks`" — 024 is implemented (all 32 tasks checked, phases 1–7 committed).
  Refresh the block (this is the project's agent-facing map; the 05 review flagged the
  same drift for 019).
- `manifests/hyprland-0.49.toml` `reason` string still claims the plain search verdict
  ("every declared … requirement of era 0.49 is satisfied") while `docs/005` correctly
  documents the *manual* pin (build-verified 0.49 vs search verdict 0.52). The manifest
  should say so itself, since `align-repo --verify` re-validates it.

### R-7 — [L] Carried-over low-severity items still open (re-verified)

Listed for the backlog ledger, no new analysis: **L-1** zero-pending `sync` skips the
delivered-defect check (`sync.py:62-70`); **L-2** stat miscounts; **L-3** Pre-Depends
constraint dedup first-wins; **L-4** possible `Size: None` stanza; **L-6** admin-side FAT
traceback; **L-8** downloader lacks resolver-style index self-heal (fails loudly, not
silently); **L-9** ensure-base mid-flight wedge with no resume path; **L-10** repair-path
any-version source fallback without divergence log; **L-11** self-heal misses
decode/parser errors (loopback only); **L-12** silent constraint-fallback switch.
Residual **N-2b**: developer(k)→developer(k+n) transitions cover only the provider, not
its subtree, at finite depths (informational-only there, no longer reachable at `-1`
after the grow-deeper re-scope — acceptable but worth one line in `docs/002`'s
transition section).

### R-8 — [L] Dead `_file_sha256` call in `import_local.py:373`

The docstring/inline comment says the payload's "SHA256 computed" as part of the verified
record — the call's return value is discarded and nothing compares it (the handoff
re-hashes the payload at generation time, so nothing is actually at risk). Drop the call
or store/compare it; as written it only costs a full-file read per import and misleads
a reader about what "verified" means.

### R-9 — [L] `fetch_index` unverified branch drops the `fetch_fn` injection hook

`index_loader.py:355-358`: the loopback/unverified path calls `_download(url)` without
forwarding `fetch_fn`, unlike the verified path (`:361, 377`). No production caller
passes `fetch_fn`, so impact is confined to test/tooling reuse — flag as an
inconsistency, not a user-facing defect.

### R-10 — [L] Path overrides other than `base_dir` are not validated as absolute

`config_loader.py:246-252` validates only `paths.base_dir`; `binary_cache`,
`source_cache`, `iso_dir`, `database.path`, `logging.directory` pass through
`expand_path` and can be relative overrides anchored to the CWD — silently
CWD-dependent cache locations. Reject relative values post-expansion (or anchor them
to `base_dir`) with a clear config error.

### R-11 — [L] External-tool prerequisites are not consolidated (per-command matrix missing)

Runtime shells out to: `gpgv` (index verification — a *missing* binary surfaces as
`FileNotFoundError`, caught only by the resolver's generic fetch retry and reported as a
generic fetch failure), `gpg` (handoff — pre-checked cleanly via `_check_gpg`,
`handoff.py:681-685`), `dpkg-deb`/`dpkg-shlibdeps`/`readelf`/`strip` (debgen —
`OSError`-wrapped, clean), `dpkg-source` (srcgen — clean), `apt-get` (package-stack
simulation — clean capability check, `package_stack.py:659-660`), `dpkg*`/`ar` readers in
`import-deb` (`debfile` needs system `ar` semantics). `docs/001:19-23` lists only
git/python/network. A compact per-command prerequisites matrix (command → required
tools/packages) belongs in the quickstart before the alpha, since the failure modes
range from clean errors to tracebacks depending on which binary is missing.

---

## 4. Functional assessment — profiles, operator chain, and the new lanes

Same bar as the previous reviews: *the 3 profiles resolve correctly and each setup
yields a stable, fully installable cache.* Evidence by construction, traced end to end.

### 4.1 regular_user (`source = false`, depth 0) — **sound**

Defaults agree across `project.defaults.toml`, `docs/profiles/regular_user.toml`, and the
`ProjectConfig` dataclass. The source phase is fully gated (`resolver.py:303-310,
533-546`; `downloader.py:563-564`); the runtime closure is uncapped; `Pre-Depends` is
merged into the closure with rendering preserved in the *index-entry* view
(`depends_field`) for VersionHash fidelity. verify's runtime gate is the only hard gate;
unlinked-binaries is informational at finite depths by design (`verify.py:39-47`).
Handoff ships exactly `current+verified` with present payloads. **Residual risk: none
new.** (N-1 offline-init caveat is the accepted documented limitation.)

### 4.2 developer (`source = true`, depth N) — **sound**, with the documented transition edge

Depth scoping, build-tool out-of-scope placement at `depth+1`, min-level repair, and the
constraint-aware probes all verified in source. The developer(k)→developer(k+n) subtree
under-coverage (residual N-2b) is invisible at finite depths because the unlinked check
is informational there — a *source-coverage* gap only, never installability.

### 4.3 graybeard (`source = true`, depth -1) — **sound on fresh caches**

Fresh-cache-only by contract (ensure-base refusal); unlimited scope at registration;
build tools at level 0; `MAX_BUILD_DEP_ITERATIONS = 100` with an explicit warning;
`-1` strictness in verify; the N-2a repair path and its regression test now close the
last cached-virtual hole. The ensure-base wedge (L-9) is the standing operational edge:
an interrupted Phase B leaves a cache that refuses re-ingestion — manual recovery is
documented (delete DB, re-run) — acceptable for alpha, call it out in release notes.

### 4.4 The operator chain (handoff → bootstrap / sync → apply) — **coherent and hardened**

Traced fully; consistent with the 05 chain analysis, now strengthened:

- **Index fidelity (spec 022)**: handoff/diff stanzas emit `Depends` verbatim from the
  index and keep `Pre-Depends`/`Conflicts`/`Breaks`/`Replaces`/`Installed-Size` — apt's
  VersionHash merges repo and dpkg-status records either way; the `local` lane reads the
  same fields from the payload's own control file with a stored-fallback for unreadable
  payloads (`handoff.py:826-926`), and fallback cases are surfaced as `degraded_local`
  warnings in CLI output.
- **Atomicity**: every artifact (indexes, Release, signatures, manifest, scripts, pool
  payloads) lands via temp-file + `os.replace`; interrupted runs converge; the diff pool
  is rebuilt from scratch per run (no stale drift).
- **Operator scripts** (`bootstrap.sh`, `apply.sh`): `set -u`, function-factored and
  unit-tested by sourcing in Python harnesses; hard failure on unlinkable pools (FAT),
  release mismatch, missing intermediates (base-set check), checksum failures; apt-update
  output routed to logs so stdout stays machine-clean; `--remove` path on bootstrap.
- **Signing**: stable cache-local key, detached armored `Release.gpg` per release,
  `Release` covers both compressed and uncompressed index forms, no `Valid-Until` time
  bomb. **One trust-scope decision to record: R-5 (private key inside the payload
  tree).**

### 4.5 Spec 021 local patch lane — **sound minus R-2/R-3**

Dependency re-resolution at import, atomic zero-change failures, verbatim VersionHash
field storage, source-set validation against the `.dsc`, idempotent re-import, provides
registration, and component priority (`local` > archive at equal version via
`_component_order`, `repository.py:6-16, 86-89`) are all in place. The lane plugs into
handoff/sync/srcindex correctly. The two gaps are exactly R-2 (download after import)
and R-3 (no supersede of older `local` revisions).

### 4.6 Spec 024 source-stack packaging + Spec 023 alignment — **sound, ecosystem-aware**

- `debgen`: `dpkg-shlibdeps` derivation with unattributed-library refusal, lintian-baseline
  documentation (copyright/changelog/triggers/shlibs/man pages), `SOURCE_DATE_EPOCH`
  determinism, staged tree never mutated.
- `srcgen`: deterministic orig tarball (fixed order/mtime/uid), minimal `debian/`
  wrapper, `dpkg-source -b`; note the copy filter silently drops any directory named
  `build` anywhere in a pinned tree (`srcgen.py:122-131`) — for the Hyprland set this is
  build-output pruning and correct; for an arbitrary future recipe whose upstream ships a
  legitimate `build/` directory it would silently exclude source. Acceptable for alpha;
  document in `docs/005` or the recipe contract.
- `package_stack.py`: recipe schema/typo rejection, topological import order,
  per-component atomic import, whole-set verify + scratch-repo `apt-get install
  --simulate` end-to-end proof (symlinked archive indexes, local index generated — clean
  design).
- `alignment.py`: era enumeration + curated lockstep companions + two-tier requirement
  validation with evidence lines; line-based meson/CMake parsers are deliberately bounded
  and documented as such. The one real coupling defect is R-4 (hard-coded baseline).

---

## 5. Technical / architecture assessment

**Layering.** `cli → service → model → util` discipline holds throughout; domain types
are frozen dataclasses with typed JSON boundaries (`TypedDict` only at serialization
edges). Two coupling warts, both minor: private cross-imports
(`srcindex.py:19-20` reaches into `handoff._atomic_write/_write_gzip` and
`import_local._pool_prefix`; `package_stack.py:38-43` reuses `handoff._local_binary_entry/
_paragraph/_release_text/_write_gzip`) — intentional reuse, but the underscore names make
the brittleness explicit; consider promoting those helpers. `cli/main.py` is a 315-line
if/elif dispatcher with a module-global `_config_error` and `getattr(args, ...)`
defensive reads; some commands (`add`, `init`, `config`) load config inside their
handlers while others get it injected — a small consistence pass (subcommand registry +
uniform config injection) would shrink the surface, but it is maintainable as is.

**Data layer.** All SQL is parameterized; the only interpolated identifiers come from
fixed internal sets (table names validated against `KNOWN_TABLES` first, `show.py:11-19,
125-131`). Schema is self-describing (`EXPECTED_SCHEMA_SQL`) with a stale-DB rebuild
message and dedicated exit code (101). Transaction design is convergent by construction:
per-name commits in the resolver (interrupted batches keep prefix progress), single-txn
Phase A in ensure-base, per-file commits in the downloader, all-or-nothing import with
payload rollback. SQLite is used single-process; the concurrent-run posture is
documented ("single-user CLI" in `ensure_base.py:135-138`).

**Resolver** (most complex module, 1 207 lines): batch-shared `_visited`/
`_batch_registered`/`_batch_levels`/`_batch_inserted_sources` state, rollback-aware
re-seeding with a sentinel level, per-name constraint accumulation with supersede+re-walk
and a documented sequential last-writer fallback, constraint-aware alternative-group
machinery shared across runtime and build-dep paths, cached-virtual branches that now
re-check constraints and re-select on drift. This is dense but heavily documented
in-place, and the current test corpus (resolver, resolver_batch, resolver_build_deps,
constraints, virtual_resolution, circular, source_linkage ≈ 3 100 lines) exercises the
branches. One readability note: `_resolve_one`'s four-way entry ladder (visited&registered
/ visited-only / virgin) plus the in-place `version_constraint` rewrite would benefit
from a state comment block, but behavior is pinned by tests.

**Downloader**: single per-mirror keep-alive socket, bounded redirect following,
Retry-After + bounded exponential backoff with jitter, `*.part → hash-verify → replace`,
per-run re-verification of shipped payloads, per-run histogram. The asymmetric R-2 hole
(`local` in the binary phase) is the single functional defect in it.

**External-surface hardening**: pinned Debian archive keyring + InRelease verification +
per-index checksum enforcement (fail-closed), HTTPS upgrade for non-loopback HTTP
mirrors, loopback exception documented, `safe_pool_path` traversal guard on every
index-derived write, space-free-path gate for `deb file:` lines, loop-safety in redirect
following. The security posture is genuinely good; R-5 is the remaining trust-scope
decision.

**Determinism**: `gzip mtime=0` everywhere, `SOURCE_DATE_EPOCH` honored for
deb/dsc/tar/changelog, sorted serialization for recipe manifests and the source index,
byte-identical regeneration claimed and testable. Release `Date` uses generation time by
default (freshness) with SDE opt-in — the right split.

**Error contract**: consistent `{error, exit_code}` JSON on stderr, capped console error
lists with full lists in the log, dedicated exit codes (100 permission, 101 schema, 102
config). The contract suite (2 445 lines) pins single-JSON-stderr in JSON mode and
output shapes — exactly the sort of thing LLM-generated codebases usually drift on, and
it is enforced mechanically here.

## 6. Documentation assessment

- `README.md`: accurate architecture summary, persona table, profiles table; figures
  stale (R-6); the LICENSE note is a dangling placeholder (R-6).
- `docs/001`: the quickstart is honestly excellent — expected outputs, the offline-init
  caveat (the N-1 resolution), idempotency notes, the "re-run bootstrap to adopt new
  components" rule (which correctly discloses that `sync` never rewrites the sources
  entry). Prereqs section needs the external-tool matrix (R-11) and the pycdlib reality
  (R-1).
- `docs/002`/`docs/003`: depth semantics and the fresh-cache-only ensure-base flow are
  stated plainly, including the wedge recovery (delete DB, re-run).
- `docs/004`/`docs/005`: the tutorial track (version-drift trap; Hyprland stack with the
  honest "search verdict 0.52 vs build-verified 0.49" toolchain-drift analysis). 005's
  tables documenting *why* newer eras fail are exactly the evidence an alpha reviewer
  wants. (User noted further tutorials are in progress — not reviewed here.)
- `docs/profiles/*.toml`: headers carry the grow-deeper direction and the
  graybeard-stand-alone rule (the N-2 resolution). Consistent.
- `AGENTS.md`: SPECKIT block stale (R-6).

## 7. Test / QA assessment

**Recorded baseline** (`tests/REGRESSION.md`): 914 tests, green under both
runners, ruff clean, contract suite green. CI enforces both runners + ruff. Prior
reviews' runner-drift lesson (B-6) is now structurally prevented.

**Suite shape**: unit (53 files) with tmp-dir hermetic fixtures; integration (13 files)
with a local signed fixture archive + fixture ISO + fixture pool server; contract (1
file, ~96 tests + subtests) pinning CLI text/JSON shapes and exit codes; fixture builders
for debs, ISOs, and signed archives. Cross-checking suite-vs-code coverage during this
review surfaced three concrete holes, each worth a pointed test (§8 actions): (a)
download-engine × `local` rows (R-2); (b) cross-version import supersede (R-3);
(c) install-without-extras import smoke (R-1). The config-driven align-repo baseline
variant (R-4) is the fourth.

**Not covered anywhere** (accepted, recorded here rather than sprung at release):
physical FAT-USB bootstrap run; live DVD-1 graybeard end-to-end; wheel/sdist artifact
inspection. These are the standing physical proofs from the 05 review's checklist.

---

## 8. Risk matrix

| Finding | Severity | Area | Alpha-promise impact |
|---|---|---|---|
| R-1 pycdlib import at startup, dev-only dep | H/B | packaging | documented standard install is broken for *every* command; wheel inspection must catch it |
| R-2 download fails after any local import | H | downloader × 021/024 | every local-lane user's next `download` exits 1 with a misleading index-repair message; recurring |
| R-3 import newer version → no supersede | M | import lane | stale `local` revisions pile into handoff/diff; nondeterministic fetchone reads; resolver oldest-first lookup |
| R-4 `align-repo` baseline hard-coded to trixie/main | M | alignment | wrong baseline on any non-trixie config; degrades to "everything unmet" without trixie/main index |
| R-5 private signing key inside handoff payload | M | trust model | every operator copy of the media carries the signing key; undocumented decision |
| R-6 release paper-trail (README, AGENTS.md, LICENSE, manifest reason) | L | release mgmt | tag-readiness hygiene |
| R-7 carried-over low items (L-1…L-12 subset) | L | backlog | individually small; collectively the next polish spec |
| R-8 discarded `_file_sha256` at import | L | import lane | dead work + misleading comment |
| R-9 loopback fetch drops `fetch_fn` | L | test tooling | inconsistency |
| R-10 non-`base_dir` path overrides unvalidated | L | config | CWD-dependent cache paths possible |
| R-11 no external-tool prerequisites matrix | L | docs | failure modes vary from clean errors to tracebacks depending on the missing binary |
| Residual N-2b developer-depth subtree coverage | L (residual) | resolver | source-coverage only, informational at finite depth |

## 9. Recommended actions before tagging alpha (in order)

1. **R-1** — move `pycdlib` into runtime `dependencies` (or lazy-import under
   `IsoImage` with a clean `IsoError`); add the no-extras install smoke test; build and
   inspect the wheel (this simultaneously closes the N-4 artifact-inspection follow-up).
2. **R-2** — exclude `local` in the downloader's binary-phase grouping mirror-style, add
   the engine-level regression test, then re-record REGRESSION.md.
3. **R-3** — supersede prior `current local` rows on import + cross-version test.
4. **R-4** — derive the align-repo baseline from `config.sources[0]`; add a
   config-driven variant test.
5. **R-5** — decide: relocate the GNUPGHOME outside the payload tree *or* document the
   posture (README + docs/001). Either closes the finding; silence does not.
6. **R-6 sweep** — README figures (24 specs/914 tests), add the LICENSE file, refresh
   the AGENTS.md block, fix the manifest `reason` field to record the manual 0.49 pin.
7. **Cheap polish batch (same pass or first point release)**: L-1 sync defect check,
   L-4 `Size` guard, L-6 FAT `HandoffError`, L-8 downloader self-heal reuse, L-9
   ensure-base resume-or-rollback, R-8 dead call, R-10 relative-path rejection, L-12
   log line, L-2 counters, R-11 prerequisites matrix.
8. **Physical proofs (execute, then append to release notes)**: FAT-USB bootstrap; live
   DVD-1 graybeard ensure-base + offline install simulation; both were already on the
   05 checklist.

With 1–4 landed, the alpha promise — *three profiles resolve correctly and each yields a
stable, fully installable cache, now including the local patch lane and the packaged
source stack* — holds by inspection for fresh caches, for the supported profile
transitions, and for the operator's bootstrap/apply consumption, with every prior
review finding either fixed, tested, or explicitly accepted as a documented limitation.

---

## Appendix A — Method

- Complete read of every `src/package_mgmt/*.py` module; targeted reads of
  `tests/unit/test_resolver*.py`, `test_virtual_resolution.py`, `test_import_deb.py`,
  `test_downloader.py`, `test_package_stack.py`, `tests/integration/test_import_deb_
  flow.py`, `test_package_stack_flow.py`, `tests/contract/test_cli.py` (header +
  structure), and the fixture builders' listings.
- Full re-verification of the 05 review's N-1…N-4 and L-1…L-13 against current source;
  spot verification of the 020 spec's documentation resolutions; traversal of
  `specs/020…024` spec/plan/tasks for intent-vs-implementation drift.
- No project commands, tests, builds, downloads, or network actions were executed
  (per instruction); git-ignored bulk trees (`repos/`, `staging/`) were not read.
- Line references use `<file>:<line>` against the current working tree; recorded
  baselines are cited from `tests/REGRESSION.md` rather than re-derived.

## Appendix B — What was *not* re-examined

Operator physical-media runs, live-mirror behavior, the 914-test corpus execution, the
`repos/` pinned source trees' content correctness (the manifest pins are validated by
the project's own `align-repo --verify` machinery), the `.specify`/`.opencode` tooling
directories, and the two in-progress tutorials. The pre-019 closed findings (B-1…B-6,
F-8…F-23) were not re-derived here — the 05 review re-verified them and nothing in the
021–024 diffs touches their code paths.
