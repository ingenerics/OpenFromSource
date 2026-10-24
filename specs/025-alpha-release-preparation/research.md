# Research: Alpha-Release Preparation

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

This research resolves the design questions the spec's review-driven findings leave open,
grounded in the current source (every fix site verified below) and the two review
documents (`review/06_alpha_finally_kimi_k3/ALPHA_REVIEW.md`, `CODE_REVIEW.md`). Per the
user's decisions (spec Clarifications Q1–Q3): R-5 relocates the keyring with no
migration and documented posture; scope = everything from both reviews; the graybeard
warning is advisory and first in the output.

---

## §1 — R-1: `pycdlib` must be a runtime dependency

**Decision**: Move `pycdlib` from `[project.optional-dependencies] dev` into
`[project] dependencies` (`pyproject.toml:10`), and add a declarative contract check that
`dependencies` carries it. The no-extras fresh-venv install + built-wheel inspection are
recorded as release checks in [quickstart.md](quickstart.md) (closes review 020/N-4).

**Rationale**: `lib/service/iso_reader.py:12` (`from pycdlib import PyCdlib`) is a
module-top import reached from `cli/main.py:10 → cli/commands/ensure_base.py:6 →
lib/service/ensure_base.py:27 → iso_reader.py:12`, so *every* command (including
`--help`) requires `pycdlib` at import time. A documented standard install
(`pip install .`, docs/001 Step 2) provides only `python-debian` and dies on
`ModuleNotFoundError`. `pycdlib` is a small pure-Python package; making it a runtime
dependency is the review's "one line" fix and matches the reality that the CLI cannot
start without it. CI missed this because it installs `.[dev]`
(`.github/workflows/ci.yml`).

**Alternatives considered**:
- *Lazy-import inside `IsoImage.__init__` + clean `IsoError`* (the review's alternative):
  rejected — it narrows the fix to ISO commands only and leaves the "documented standard
  install is broken for every command" claim un-closed in spirit; a future top-level
  import would re-trip it. Making the dependency real is simpler and testable.
- *Documenting `sudo apt install python3-pycdlib` as a prerequisite* (docs/003 already
  lists it): rejected — the venv/pip install paths do not see `/usr/lib/python3/dist-packages`,
  so it does not rescue the documented pip flow.

---

## §2 — R-2/F5: the downloader must exclude `local` rows in its binary re-check

**Decision**: Mirror the source phase: add `AND component != 'local'` to the binary
grouping query in `_group_current` (`lib/service/downloader.py:713-723`), which currently
lacks the exclusion that `_group_sources_to_check` (`downloader.py:691-710`) already has
("verified at import time from the supplied directory"). Add the engine-level regression
test the review identifies as the coverage hole: import a `local` deb, then
`download_pending_binaries` → zero command errors, record stays verified.

**Rationale**: The binary phase re-checks every current row per run (the F-17 posture —
present payloads re-verified from disk). A `local` row is grouped under
`(<default-release>, local)`; no archive index exists at
`binary/dists/<release>/local/binary-amd64/Packages.gz` (nothing ever writes one), so the
run appends a command error per the missing-index branch and `handle_download` exits 1 —
even when nothing is pending and every payload is intact. The message ("the index is part
of the cache — restore or re-fetch it") misleads the admin toward index repair. The
existing `excluded_pool` path (handoff) already handles deleted local payloads, so no
other change is needed.

**Coverage hole**: the only local-vs-download test
(`test_download_pending_set_excludes_local_verified_row`, `tests/unit/test_import_deb.py:339-347`)
exercises the SQL helper `pending_binaries()` (filters `verified = 0`), not the engine's
own `_group_current` re-check path, and no integration flow runs `handle_download` after
an import. Both are closed here.

---

## §3 — R-3/F8: import must supersede older `local` `current` rows

**Decision**: Extract the resolver's existing supersede dance
(`resolver.py:1118-1138`: `_find_current_binary` → if same version reuse, else mark old
`status='superseded'` + upsert new) into a reusable helper
`supersede_current_binary(conn, package_name, architecture, version, release, component,
deps_json)`, and call it from `import_local_deb` (`import_local.py:296-403`) before the
upsert — scoped to `component='local'`. Stale payloads are **retained** (content-addressed
pool, still checksummed on sync); only the index `current` row is superseded.

**Rationale**: `upsert_binary`'s conflict key includes the version
(`resolver.py:120-140`), so importing `patch-tool 1.3-0` after `1.2-3` inserts a second
`current` row. Consequences (review R-3): the handoff/diff emit both stanzas (the index
grows with dead revisions), `whole_set_verify`'s un-ordered `fetchone()`
(`package_stack.py:514-518`) and `_find_current_binary`'s `ORDER BY id`
(`resolver.py:111-117`) become nondeterministic, and a name can end up with three
concurrent `current` rows (old local, new local, archive). Reusing the archive path's
exact pattern (instead of cloning it) is the code-review F8 recommendation and keeps one
supersede semantics. Retaining the stale payload is the review's explicit call: delivered
systems may legitimately lag and their snapshot references the old payload; the pool is
content-addressed by name/version and still checksummed on sync.

**Note**: `upsert_binary`'s `ON CONFLICT` re-import of the *same* version stays
idempotent (existing behavior preserved).

---

## §4 — R-4: `align-repo` baseline from the configured sources

**Decision**: Derive the baseline release/component from `config.sources[0]` and thread it
through `load_baseline(db_path, packages_gz_path, release, component)`
(`alignment.py:177-202`), `_packages_gz_path` (`cli/commands/align_repo.py:149-150`), and
`BASELINE_LABEL` (`alignment.py:29`). align-repo already runs under a loaded config
(`cli/main.py`), so no new CLI plumbing is needed beyond passing `config.sources[0]`.

**Rationale**: The baseline the era gate validates against is, by definition, the frozen
cache of the configured first source. Hard-coding `trixie/main` reports the wrong
baseline's verdict on any non-trixie config (or a trixie config whose first component is
not `main`) and degrades to "everything unmet" when no `trixie/main` index is cached. The
ecosystem-specific era tables (`ERA_COMPANION_TAGS`, `alignment.py:42-56`) being
Hyprland-shaped is per spec 023; the *release* coupling is not. The CWD-relative
`manifests/` write (`alignment.py:588`) and `--repo-dir` default are documented
run-from-project-root requirements (docs, not defects).

---

## §5 — R-5: private signing key stays off operator media by default

**Decision** (user Q1): Relocate the persistent keyring from `{binary_cache}/handoff/.gnupg`
to `{base_dir}/.gnupg-handoff/` (`util/paths.handoff_gnupg_dir`, `paths.py:55-59`), with
**no migration** of legacy in-tree keyrings. Update `_ensure_key`
(`handoff.py:1086-1095`) and the key/signature writers to use the new home; the key is
still generated once and reused (stable trust anchor). The handoff payload then contains
only the public `offline-cache.gpg`. **Document the decision** (README + docs/001): a
plain copy of `{base_dir}` carries the keyring, so admins SHOULD exclude `.gnupg-handoff/`
from operator media unless the operator is a trusted relay that must re-sign.

**Rationale**: The operator only needs the public key to verify the signed Release files;
the private key is only needed to re-sign. As shipped, every copy of the transport media
carried the private key — any operator machine (or anyone reading the stick) could re-sign
arbitrary index content that every other machine bootstrapped from the same cache trusts,
and on FAT media the 0700 permissions are meaningless. The persistent-GNUPGHOME design
itself (stable trust anchor) is right; only its placement leaks the secret. Pre-alpha, no
cache has shipped, so no migration is required — a fresh key is generated at the new
location and any legacy in-tree keyring is unused. The posture is a recorded decision
(the review's "decision to record" demand), now with a secure default.

**Alternatives considered**:
- *Document-only (keep shipping + record the posture)*: rejected — the user's direction
  is "do we even need to ship it… by default?" → no. Relocation makes the safe outcome the
  default with zero operator impact.
- *A runtime flag to include/exclude the keyring in the payload*: rejected as unnecessary
  machinery — the keyring is outside the payload tree by construction; whether the private
  key travels is a **copy-time** decision the docs make explicit (exclude the dir, or
  deliberately include it for a trusted relay).

---

## §6 — Graybeard `ensure-base` warning (new, Q3)

**Decision**: Add a `config_loader` helper (alongside `source_depth_zero_warning`,
`config_loader.py:70-82`) that returns a warning string when
`config.fetch_source and config.max_source_dep_depth == -1` (graybeard) AND the union of
the config's trixie-source components is missing any of `main`, `contrib`, `non-free`,
`non-free-firmware`; returns `None` otherwise (including all non-graybeard configs and
complete graybeard configs). `ensure_base()` appends it to `EnsureBaseStats.warnings` in
the first position; the CLI (`cli/commands/ensure_base.py`) emits it before the summary,
so it is the first stdout lines (advisory, non-blocking) and present in the run's
structured warnings.

**Rationale**: Graybeard promises full rebuild capability; if the config only carries
`main`, the source/build-tool promise cannot hold for the other components, and silently
proceeding produces a cache that looks complete but cannot rebuild parts of the stack.
The four canonical components are the graybeard preset set
(`docs/profiles/graybeard.toml`: `main, contrib, non-free, non-free-firmware`); "graybeard"
is detected by config values (`source = true`, depth `-1`) — there is no profile concept
in the tool (roles.md). The existing `source_depth_zero_warning` handles the `depth = 0`
case; this handles the `-1`/missing-components case. The warning is config-driven (the
ISO's actual contents are irrelevant to it).

---

## §7 — F1: shared filesystem/hash helpers

**Decision**: Create `lib/util/fs.py` with a uniform API — `file_sha256(path,
errors="raise"|"none")`, `atomic_write_bytes(path, data, mode=0o644)`,
`atomic_copy(src, dst, verify=None)` — and re-point the seven existing implementations:
`downloader._file_sha256` (`downloader.py:353-364`), `import_local._file_sha256`
(`import_local.py:76-81`) + `_copy_atomic` (`import_local.py:84-103`), `handoff._file_sha256`
(`handoff.py:1170-1175`) + `_atomic_write` (`handoff.py:705-726`) + `_copy_verified`
(`handoff.py:1178-1196`), `index_loader._write_index_atomic` (`index_loader.py:242-257`),
and `srcindex._file_meta` (`srcindex.py:50-58`). `_copy_verified`'s verify-after-copy
becomes `atomic_copy(..., verify=True)`.

**Rationale**: The code review (D1/D2) found four flavors of atomic write/copy and three
of file hashing, and one of these duplications is already implicated in a functional
defect (R-2: the `local` exclusion exists in one grouping helper but not its twin).
Consolidating into one typed home with the `errors=` knob and the verify-after-copy
option kills the divergence class; every change is mechanical and covered by the existing
test net.

---

## §8 — F2: one dependency-satisfaction predicate

**Decision**: Add a single shared predicate `deps_satisfied(conn, name, spec) -> bool` in
`lib/model/dependency.py` (or a tiny `lib/service/depgraph.py`), capturing the current
semantics (direct current-row constraint check, then versioned-`provides` fallback), and
use it from `import_local._dep_satisfied` (`import_local.py:114-131`),
`cli/commands/verify._dep_satisfied` (`verify.py:157-188`), and consulted by
`resolver.select_provider` (`resolver.py:234-264`).

**Rationale**: The same rule exists in three places (the two `_dep_satisfied` copies are
written to mirror each other; `select_provider` is a third cousin on the
provides-version-vs-provider-version posture). The code review ranks this the
highest-drift-risk duplication in the tree — the alpha's alternative-group bugs (N-3
class) lived exactly here. Unifying preserves semantics (the contract suite staying green
is the acceptance); it does not change resolution outcomes.

---

## §9 — F3/F4/F6/F7/F9/F10 + R-8/R-9/R-10/R-11 (consolidation & craft)

**F3 — public re-exports**: un-underscore `_pool_prefix`, `_local_binary_entry`,
`_paragraph`, `_write_gzip`, `_atomic_write`, `_print_error` — all already imported by
external consumers (`package_stack.py:38-43`, `srcindex.py:19-20`, ~12 command modules
for `_print_error`). Zero behavior change; drops the private-name cross-import smell.

**F4 — one `local_entries()` generator**: one generator yielding
`(rid, name, version, entry_or_none, used_fallback)` per verified current `local` row
(looping `_local_binary_entry` + `_paragraph`), used by the three call sites:
`handoff.generate_handoff`'s local branch (`handoff.py:1521-1551`), `generate_diff`'s
local branch (`handoff.py:1311-1358`), and `package_stack._local_packages_text`
(`package_stack.py:538-555`). The three differ only in accounting; the package-stack site
stops silently skipping unreadable payloads and **warns** instead (the review's F4
behavior change).

**F6 — packaging helper merge**: move `_changelog_date` + `_FALLBACK_MAINTAINER` +
maintainer-resolution into one module (`debgen.py:44-54,115-116` and
`srcgen.py:29-37,279` are byte-identical twins); one `read_dsc(dsc)` helper shared by
`import_local._dsc_files` (`import_local.py:184-197`) and `srcgen._dsc_file_count`
(`srcgen.py:246-261`).

**F7 — release-hash one-pair helper**: `hash_entries_for_pair(component, plain_rel,
plain_data, gz_rel, gz_data)` in a shared spot, with the decompress-on-demand variant
(present in `package_stack._index_hashes_for_existing`, `package_stack.py:558-596`)
preserved as an option; re-points `handoff._index_hash_entries` (`handoff.py:1000-1020`)
and `srcindex.source_index_hash_entries` (`srcindex.py:112-135`).

**F9 — test scaffolding**: consolidate `_insert_binary` and `_config` (duplicated across
~8 modules) and the fixture `_pool_prefix` (`make_test_deb.py:177`) into
`tests/fixtures/db.py` — one update point for schema fixtures; kills ~15 near-identical
definitions.

**F10/R-8 — craft sweeps**: `from typing import Set` → `set` (`config_loader.py:3`);
remove the dead `_file_sha256(dest)` call and its misleading comment
(`import_local.py:373`); rename `ImportError` → `ImportDebError` (`import_local.py:53`);
type the resolver's internal `stats: dict` accumulator as a mutable dataclass
(`resolver.py:447-512`) with the error-list-retraction sites (index marks) kept on a
mutable list.

**R-9 — fetch-hook consistency**: the loopback/unverified path of `fetch_index`
(`index_loader.py:355-358`) calls `_download(url)` without forwarding `fetch_fn`, unlike
the verified path (`index_loader.py:361,377`). Forward it; no production caller passes
`fetch_fn`, so impact is test/tooling reuse only.

**R-10 — path validation**: `config_loader.py:246-252` validates only `paths.base_dir`;
`binary_cache`, `source_cache`, `iso_dir`, `db_path`, `log_dir` pass through
`expand_path` and can be relative overrides anchored to the CWD. Reject relative values
post-expansion (or anchor to `base_dir`) with a clear `ConfigError`.

**R-11 — prerequisites matrix**: add a per-command external-tool prerequisites matrix to
`docs/001` (command → required tools/packages), covering `gpgv` (index verification),
`gpg` (handoff), `dpkg-deb`/`dpkg-shlibdeps`/`readelf`/`strip` (debgen),
`dpkg-source` (srcgen), `apt-get` (package-stack simulation), and the `dpkg`/`ar` readers
in `import-deb` — with their failure modes (clean errors vs tracebacks).

---

## §10 — R-6 release paper-trail & FR-020 verification

**Decision**: R-6 is a docs/manifest sweep — README figures (24 specs / 914 tests),
add a `LICENSE` file (text confirmed by the owner at planning), refresh the `AGENTS.md`
SPECKIT block, and set `manifests/hyprland-0.49.toml`'s `reason` to record the manual
build-verified 0.49 pin (matching docs/005). FR-020 is the release-verification gate:
re-run + re-record the baseline (`tests/REGRESSION.md`, both runners + ruff + contract
suite), inspect the built wheel in a fresh no-extras environment, and execute the two
physical proofs (FAT-USB bootstrap; graybeard DVD-1 offline install simulation),
appending results to the release notes.

**Rationale**: These are the standing checks from the 05/06 review checklists; they turn
"correct by inspection" into "verified by execution" for the alpha tag.

---

## §11 — Phase 3: tutorial track + version bump

**Decision**: The tutorial track is authored **collaboratively** in working sessions
between the Administrator and the agent (spec FR-021, US10). None of the content exists
yet; topics, scope, and a **new** tutorial style are agreed jointly during the phase
(distinct from the existing how-to documents `docs/001`–`docs/005` — per user
correction). Each tutorial stays consistent with the implemented behavior (Phases 1–2).
At the end of Phase 3, `pyproject.toml`'s version is set to `0.1.0-alpha` (from `0.1.0`)
and the release-facing references (README, quickstart) are made consistent with it.

**Rationale**: The version bump is the Phase-3 exit gate and the concrete alpha-release
marker. The tutorial content/style is deliberately not pre-specified in the plan — it is
agreed jointly as the phase proceeds (this is a collaborative authoring phase, not an
autonomous implementation).

---

## Consolidated decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | R-1 install | `pycdlib` → runtime `dependencies`; declarative check + no-extras install/wheel release checks (§1) |
| 2 | R-2/F5 download-after-import | exclude `component='local'` in `_group_current` + engine regression test (§2) |
| 3 | R-3/F8 import supersede | extract shared `supersede_current_binary`; retain stale payloads; cross-version test (§3) |
| 4 | R-4 align-repo baseline | derive from `config.sources[0]`; refresh `BASELINE_LABEL`; config-driven test (§4) |
| 5 | R-5 keyring | relocate to `{base_dir}/.gnupg-handoff/`, no migration; public-key-only payload; documented copy posture (§5) |
| 6 | Graybeard warning | config helper (source=true & depth=-1 & missing trixie component) → first stdout line + structured warning (§6) |
| 7 | F1 fs/hash helpers | new `lib/util/fs.py`; re-point 7 sites (§7) |
| 8 | F2 dep predicate | one shared `deps_satisfied`; 3 consumers (§8) |
| 9 | F3/F4/F6/F7 | public re-exports; `local_entries()` generator (+ package-stack warns); packaging helpers + `read_dsc`; hash-pair helper (§9) |
| 10 | F9/F10/R-8 | `tests/fixtures/db.py`; craft sweeps (§9) |
| 11 | R-9/R-10/R-11 | `fetch_fn` forwarding; absolute path validation; prerequisites matrix (§9) |
| 12 | R-6/FR-020 | paper-trail sweep; baseline re-record + wheel inspection + physical proofs (§10) |
| 13 | Phase 3 | collaborative tutorials (new style) + version → `0.1.0-alpha` (§11) |
