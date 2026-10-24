# Code Review — Structure, Patterns & Re-use

**Scope**: code-structure review of `src/package_mgmt/` (≈ 11 660 lines, 56 modules)
and `tests/` (≈ 21 490 lines). Questions answered, per request: (1) is the code
structured following best practices and common patterns, with shared logic refactored
for re-use; (2) what low-hanging fruit is left; (3) how is logic shared between the
`import-deb` flow and `package-stack` — is the common code cleanly refactored.

**Method**: static file inspection only — no project commands, tests, or builds were
executed. Every claim below cites `<file>:<line>` against the current tree and was
verified by reading both sides of each alleged duplication. Complements
`ALPHA_REVIEW.md` (functional/technical review from the same session); findings R-1
… R-5 from that review are referenced here, not re-argued.

---

## 1. Verdict

**The codebase is in genuinely good structural shape — unusually so for a fully
generated (SDD) project.** It has a coherent layering (`cli → service → model → util`),
concrete frozen-`dataclass` domain types with typed JSON boundaries, one shared error
taxonomy with machine-readable output, consistent atomic-write posture, and
documentation-in-comments that encodes *why* each decision exists. The spec-driven
process visibly worked: the big consistency fixes (pre-Depends parsing, alternative
groups, VersionHash) were centralized once and reused everywhere instead of patched
per call site.

That said, there **is** low-hanging fruit, concentrated in two bands:

1. **Helper-level duplication across the 021–024 feature set** — small filesystem/hash
   helpers (`_file_sha256` ×3, atomic write/copy ×3, `_changelog_date` + maintainer
   fallback ×2, index-hash computation ×3) re-implemented per module instead of living
   in `lib/util/` or a single shared service module. Two of these duplications are
   already implicated in functional defects (`ALPHA_REVIEW.md` R-2: the `local`
   exclusion exists in one grouping helper but not its twin).
2. **The handoff emission loop for `local` records exists three times** (handoff,
   diff, package-stack scratch repo) around the same two primitives, and the
   cross-module re-use that *does* happen goes through **underscore-private names**
   (`srcindex` imports `handoff._atomic_write`, `package_stack` imports
   `handoff._local_binary_entry`) — the consumers know the code should be shared, the
   naming just hasn't caught up.

**On the specific question (`import-deb` vs `package-stack`): the common core IS cleanly
re-used — `package-stack` deposits every component through the single public entry point
`import_local_deb()`, so there is one import path, one atomicity story, one dependency
gate. The *non*-common parts (stanza emission, set-wide verification, index-hash
bookkeeping) are the parts that drifted into copies.** Details in §5.

## 2. Where the structure is strong (worth protecting)

These are the patterns the refactor should extend, not disturb:

- **Layering discipline.** No model imports from service, no util imports from
  anything; `cli/commands/*` are thin argument/error-mapping shells over services.
  The one soft spot is service→service imports, which are needed by design (see §5)
  but done through private names.
- **Domain types at every boundary.** `BinaryPackage`/`SourcePackage`/
  `DependencySpec`/`Alternative`/`PackResult`/`AlignmentReport` are frozen
  dataclasses; `TypedDict` appears only at JSON output edges
  (`package_stack.py:339-357`, `align_repo.py:32-54`). Serialization round-trips are
  backward-compatible (legacy string-form alternatives accepted,
  `dependency.py:117-133`).
- **One error contract.** `_print_error` JSON payload + dedicated exit codes (100/101/
  102) shared by every command; command-level exception types (`HandoffError`,
  `DebGenError`, `SrcGenError`, `ImportError`, `PackagingError`, `RecipeError`)
  mapped 1:1 to exit codes in the CLI layer. The contract suite mechanically
  enforces the single-JSON-stderr rule (`tests/contract/test_cli.py`).
- **Atomicity as a single posture, applied everywhere.** `*.part → verify →
  os.replace` for downloads (`downloader.py:309-350`), imports
  (`import_local.py:84-103`), handoff artifacts (`handoff.py:705-726`), index fetches
  (`index_loader.py:242-257`), deb/source copies (`handoff.py:1178-1196`).
- **Config centralization.** All path templates in `model/config.py`, expansion via
  one `_KEY_MAP`, unknown-key rejection, strict type validation (bool ≠ int)
  (`config_loader.py`).
- **Determinism.** `gzip mtime=0`, `SOURCE_DATE_EPOCH` everywhere artifacts are
  built, sorted serialization, stable paragraph ordering.
- **Security defaults.** `safe_pool_path` guards every index-derived path
  (`util/paths.py:119-140`); single HTTPS-upgrade point (`config_loader.py:85-97`);
  fail-closed index verification (`index_loader.py:337-385`).

## 3. Duplication inventory (verified, both sides read)

Legend: **D#** = duplication cluster; *Fix* = suggested consolidation point.

### D1 — File hashing re-implemented 3× — *fix: `lib/util/` (or a `lib/util/fs.py`)*

| Location | Signature |
|---|---|
| `downloader.py:353-364` | `_file_sha256(path) -> str \| None` (returns None on OSError) |
| `import_local.py:76-81` | `_file_sha256(path) -> str` (raises) |
| `handoff.py:1170-1175` | `_file_sha256(path) -> str` (raises) |

Plus a fourth partial twin: `srcindex.py:50-58` `_file_meta` hashes the same file in
one pass for md5+sha256. All read the same 64 KiB chunks. One helper with an
`errors="raise"|"none"` knob replaces all of them; the `srcindex` variant shows the
"hash a file once, many digests" shape the shared helper should grow into.

### D2 — Atomic write/copy exists in 3 flavors — *fix: same util home as D1*

| Location | Shape |
|---|---|
| `handoff.py:705-726` | `_atomic_write` — bytes → temp in dest dir → `os.replace` |
| `import_local.py:84-103` | `_copy_atomic` — file `src` → `dest.part` → replace |
| `handoff.py:1178-1196` | `_copy_verified` — copy + post-copy hash/size verify → replace |
| `index_loader.py:242-257` | `_write_index_atomic` — bytes → `dest.part` → replace |

Four implementations of "make a file appear atomically, clean the temp on failure".
`_copy_verified`'s verify-after-copy is a legitimately distinct feature; the other
three collapse into one `atomic_write_bytes(path, data, mode=...)` /
`atomic_copy(src, dst, verify=None)` pair. (The `*.part` convention also varies —
`handoff` uses `.tmp-*`+`.part`; unifying the temp naming is free once the helpers
merge.)

### D3 — Changelog/maintainer helpers duplicated verbatim in debgen/srcgen — *fix: small `lib/service/packaging_util.py` (or into `model/packaging.py`)*

- `debgen.py:44-54` and `srcgen.py:29-37`: identical `_FALLBACK_MAINTAINER` constant
  and identical `_changelog_date(source_epoch)` function.
- `debgen.py:115-116` and `srcgen.py:279`: identical maintainer-resolution expression
  (`component.maintainer or default_maintainer or _FALLBACK_MAINTAINER`).

Byte-identical copies; the 021/022 features were built in sequence and the second
module cloned the first's helpers.

### D4 — Release-hash entry computation 3× — *fix: one "hash an index file pair" helper*

| Location | Purpose |
|---|---|
| `handoff.py:1000-1020` | `_index_hash_entries` — binary Packages/Packages.gz |
| `srcindex.py:112-135` | `source_index_hash_entries` — Sources/Sources.gz |
| `package_stack.py:558-596` | `_index_hashes_for_existing` — reads *existing* files, synthesizes the missing plain hash by decompressing the `.gz` |

All three emit the 4-hash + size dict per relative path. The third has a real
algorithmic difference (decompress-on-demand), so the clean factoring is:
`hash_entries_for_pair(component, plain_rel, plain_data, gz_rel, gz_data)` in a shared
spot, with the decompress fallback as an option.

### D5 — The "emit every verified `local` record's paragraph" loop exists 3× — *fix: pull a `local_entries(conn, config, release)` generator into one module*

1. `handoff.py:1521-1551` (generate_handoff's `local` branch)
2. `handoff.py:1311-1358` (generate_diff's `local` branch)
3. `package_stack.py:538-555` (`_local_packages_text` for the scratch repo)

All three: iterate verified current `local` rows → `_local_binary_entry` →
`_paragraph`, with exclusion bookkeeping that differs only in *what it collects*
(handoff: `excluded_pool`/`degraded_local`/`delivered_ids`; diff: `excluded_pending/
delivered`; package-stack: skips silently). One generator yielding
`(rid, name, version, entry_or_none, used_fallback)` per row makes the three call sites
differ only in their accounting — and makes #3 stop silently dropping unreadable
payloads.

### D6 — Test scaffolding duplicated across ~8 modules — *fix: `tests/fixtures/db.py`*

- `_insert_binary`: `test_sync.py:44`, `test_diff_generator.py:49`, `test_handoff.py:96`,
  `test_srcindex.py:59`, `test_downloader.py:119`, `test_import_deb.py:45` (+ method
  variants in `test_systems.py:58`, `test_snapshot.py:32`).
- `_config(base_dir, ...)` factories: `test_sync.py:25`, `test_diff_generator.py:30`,
  `test_handoff.py:44`, `test_downloader.py:36`, `test_import_deb.py:26`,
  `test_srcindex.py:40`, `test_apply_script.py:23`, `test_bootstrap_script.py:18`,
  `test_resolver_build_deps.py:158`.
- `tests/fixtures/make_test_deb.py:177` has its own `_pool_prefix` copy — a third
  consumer of the same convention instead of importing it from
  `import_local._pool_prefix`.

Moving `_insert_binary`/`_config` into `tests/fixtures/` (which already hosts the
builders) kills ~15 near-identical definitions and, more importantly, gives fixture
schema changes a single update point.

### D7 — Near-duplicate CLI handler shells — *fix: table-driven dispatch*

`cli/commands/handoff.py` and `cli/commands/sync.py` share the shape "validate schema →
validate moniker → open conn → generate → mark → print summary → map exceptions";
`main.py:244-315` is a 16-branch `if/elif` chain with per-command `getattr(args, ...)`
reads. A dataclass-free command registry (name → handler + arg-pulling lambda) would
shrink `main()` to ~40 lines. Cosmetic; do it only alongside other `main.py` work.

### D8 — Deprecation / dead craft items (zero-risk sweeps)

- `config_loader.py:3`: `from typing import Set` + `Set[str]` — the PEP 585 built-in
  `set[str]` has been canonical since 3.9; project pins 3.11+ (`pyproject.toml:9`).
- `import_local.py:373`: discarded `_file_sha256(dest)` call — dead work + misleading
  comment (also ALPHA_REVIEW R-8).
- `downloader.py:208`/`203-206`: `_default_opener` docstring/dupe; and
  `errors.py:_print_error` is private by name but imported by ~10 command modules —
  promote it (public name) or accept the convention; the underscore signals "don't
  reuse" while everything reuses it.

## 4. The `import-deb` ↔ `package-stack` axis — how the common logic is maintained

This was the explicit question, so here is the full trace.

### 4.1 What the two flows actually do

```
import-deb (single, manual)                package-stack (set, recipe-driven)
─────────────────────────────              ─────────────────────────────────────
cli/commands/import_deb.py                 cli/commands/package_stack.py
  └─ import_local.import_local_deb()  ◄────  package_stack.run_package_stack()
       = THE one import path                    load_recipe + resolve_component_order
                                                ├─ debgen.build_binary_package()
                                                │    stage dir → derived Depends → .deb
                                                ├─ srcgen.build_source_package()
                                                │    pinned tree → .dsc + referenced set
                                                ├─ import_local_deb(deb, src_dir)  ←──┘
                                                ├─ whole_set_verify()
                                                └─ scratch-repo apt-get install --simulate
```

**The load-bearing fact: `package-stack` does not re-implement import — it calls the
public `import_local_deb()` (`package_stack.py:44, 758`) for every component.** There is
exactly one code path that registers a local binary+source pair: control parsing,
atomic DB+payload registration, dependency re-resolution, `--source` set validation,
`binary_to_source` linking, verified marking, rollback — all of it lives once, in
`import_local.py:296-402`. That is the correct design and it is correctly executed.

### 4.2 Shared pieces that ARE properly factored

| Concern | Single home | Consumers |
|---|---|---|
| Import/registration | `import_local.import_local_deb` | `import-deb` cmd, `package_stack` |
| Dependency parsing (runtime/build, arch/profiles) | `model/dependency.py` | resolver, import, debgen-merge, verify, handoff |
| Dep-satisfaction semantics | `import_local._dep_satisfied` mirrors `verify._dep_satisfied` | (parallel, see below) |
| Pool layout convention | `import_local.local_pool_rel_path` + `_pool_prefix` | handoff (`handoff.py:43, 853`), srcindex (`srcindex.py:20, 32`) |
| Local-paragraph construction | `handoff._local_binary_entry` + `handoff._paragraph` | package_stack (`package_stack.py:39-41, 549-554`) |
| VersionHash field storage | `model/dependency.serialize_depends_with_version_hash` | import writes; handoff reads on degraded fallback |
| Index upsert primitives | `resolver.upsert_binary/upsert_source/link_*` | import (`import_local.py:35-40`), ensure_base |

Note the satisfaction-check row: `import_local.py:114-131` and `verify.py:157-188`
implement the *same semantics* (direct current-row constraint check, then versioned-
provides fallback) in two private copies. They were written to mirror each other (the
comments say so), twice in the same feature family — a textbook candidate for one
shared `deps_satisfied(conn, name, spec) -> bool` in `model/dependency.py` or a tiny
service util. The resolver's `select_provider` (`resolver.py:234-264`) is a third
cousin (provides_version-vs-provider-version posture) that should consult the same
rule. This is the one place where "same semantics, three implementations" could
actually drift — §6 ranks it accordingly.

### 4.3 Where the axis drifted into copies (the real answer to "cleanly refactored?")

1. **Stanza emission for `local` rows — 3 loops (D5).** The handoff's local branch,
   the diff's local branch, and package-stack's `_local_packages_text` all loop
   `_local_binary_entry` → `_paragraph`. The primitive is shared; the *loop* is not,
   and the three copies already differ in failure accounting (package-stack silently
   skips, handoff reports, diff defects). Fix = one generator (D5 fix).
2. **Recorded dependency parsing.** `import_local.py:336-338` parses `Depends` +
   `Pre-Depends` separately (correctly, for VersionHash capture), while debgen's
   output flows through the same `DependencyGraph.parse_depends_line`. Good.
   But `_dsc_files` (`import_local.py:184-197`) and `srcgen._dsc_file_count`
   (`srcgen.py:246-261`) are two partial `.dsc` readers — one validates, the other
   counts. Different purposes, same parse; a small `read_dsc_files(dsc)` helper would
   serve both.
3. **Whole-set verify vs the `verify` command.** `whole_set_verify`
   (`package_stack.py:503-535`) re-asks the DB "binary verified / linked / source
   verified" per set member with an unordered `fetchone()`; `verify.py` asks the same
   universe of questions cache-wide. They target different scopes (set vs cache), so
   sharing is not free-form — but the *row-level* predicate ("this binary row is
   verified and linked to a verified source") is the same two-line SQL join and could
   be one function. This also fixes the unordered-`fetchone` nit
   (flagged in ALPHA_REVIEW R-3 collateral).
4. **Private-name cross-imports along the axis.** `srcindex.py:19-20` imports
   `_atomic_write`, `_write_gzip`, `_pool_prefix`; `package_stack.py:39-41` imports
   `_local_binary_entry`, `_paragraph`, `_release_text`, `_write_gzip`. Every one of
   these pairs is *correct* re-use — the code IS shared, exactly once — but the
   underscore names advertise the opposite. Mechanical fix: drop the leading
   underscore on the handful of helpers that already have external consumers
   (`_pool_prefix`, `_local_binary_entry`, `_paragraph`, `_write_gzip`,
   `_atomic_write`) or move them to `util/`. Zero behavior change, kills the smell.

### 4.4 Divergences that are deliberate (keep)

- **Where `Depends` comes from**: debgen *derives* (`dpkg-shlibdeps` over the staged
  tree + recipe `explicit_depends`, with unattributed-library refusal,
  `debgen.py:135-171`); import *trusts the control file* but re-resolves it against
  the cache (`import_local.py:331-340`). Different trust bases — right call.
- **Dependency gate at import**: single-deb import checks against the cache so the
  operator channel stays closed (`import_local.py:140-158`); package-stack instead
  relies on topological order + the final simulated install. Both gates exist; neither
  duplicates the other.
- **MAINTAINER/changelog generation**: needed by artifact *builders* only, so it lives
  next to them (debgen/srcgen) — but as twins, not a shared module (D3).

### 4.5 Verdict on the axis

The common core is clean: **one import path, one pool layout, one paragraph builder,
one version-hash storage format.** The drift is at the *edges*: the loop around the
paragraph builder (D5), the satisfaction predicate (two private copies), the `.dsc`
reader split, and a naming convention that lies about sharedness (§4.3.4). None of
this is structural — it is one tidy "consolidate the 021–024 helpers" pass away from
being uniformly clean.

## 5. Craft observations (minor, no behavior risk)

- **`ImportError` shadows the builtin** (`import_local.py:53`). Deliberate command
  taxonomy and it never escapes the import lane, but a name like `ImportDebError`
  avoids the moment of confusion for every new reader (and for `pytest` tracebacks).
- **Private-by-name, public-by-use.** `_print_error` (`errors.py:44`) is imported by
  ~12 modules; promote or accept — pick one convention and write it in AGENTS.md.
- **`getattr(args, ...)` double defaults.** `main.py` reads `getattr(args, "format",
  "text")` where argparse already assigns defaults — harmless belt-and-braces, also a
  smell that the registry refactor (D7) would remove for free.
- **Resolver internals use an untyped `stats: dict`** with ten well-known keys
  (`resolver.py:447-452`), while the public `ResolutionStats` dataclass exists and the
  final mapping is hand-written field-by-field (`resolver.py:499-512`). Making the
  internal accumulator the dataclass kills a whole class of key-typos and shrinks the
  mapping to a constructor call. (Care: two sites use error-list *retraction* via
  index marks, so the list stay needs to remain mutable — fine for a non-frozen
  internal class.)
- **Test `sys.path` injection + `# noqa: E402`** at the top of nearly every test file —
  worked-around rather than solved (a `tests/fixtures/__init__` already exists; a
  `conftest.py` or a one-line path shim in `tests/__init__.py` would centralize it).
  Both runners pass today; this is friction, not failure.
- **`E501` globally ignored** (`pyproject.toml:31-32`) — documented rationale (SQL +
  config literals), acceptable; exceptions are visually rare and the line-length
  discipline otherwise holds.

## 6. Low-hanging fruit (ranked by risk-reduction per line changed)

| # | Item | Effort | Why now |
|---|---|---|---|
| F1 | **Shared hash/atomic-write helpers** (D1+D2 → `lib/util/fs.py`): one `file_sha256(path, errors=…)`, one `atomic_write_bytes`, one `atomic_copy(src, dst, verify=…)`; re-point the 7 call sites | ~½ day | Prevents the next divergent-copy defect (R-2 class); touches many files but each change is mechanical and test-covered |
| F2 | **One dep-satisfaction predicate** (`model/dependency.py` or `lib/service/depgraph.py`): used by `import_local._dep_satisfied`, `verify._dep_satisfied`, and consulted by `resolver.select_provider` | ~½ day | Same semantics in three private copies is the highest-drift-risk duplication in the tree; the alpha's alternative-group bugs (N-3 class) lived exactly here |
| F3 | **Public re-export pass** (§4.3.4): un-underscore `_pool_prefix`, `_local_binary_entry`, `_paragraph`, `_write_gzip`, `_atomic_write`, `_print_error` | ~1 h | Zero behavior change; ends the "imports private names" lint across `srcindex`, `package_stack`, and the command layer |
| F4 | **`local_entries()` generator** (D5 fix): one loop, three accounting skins; makes package-stack stop silently dropping unreadable payloads | ~½ day | Kills the third copy of a loop that already diverged once |
| F5 | **Downloader grouping symmetry** (ALPHA_REVIEW R-2): `local` exclusion in `_group_current` mirroring `_group_sources_to_check` + engine test | ~1 h | A real exit-code bug; listed here because it *is* a shared-code-divergence fix, done in the same pass as F1/F4 |
| F6 | **Packaging helpers merge** (D3): `_changelog_date`, `_FALLBACK_MAINTAINER`, maintainer resolution into one module; `read_dsc` helper shared by srcgen/import | ~1 h | Byte-identical twins |
| F7 | **Release-hash one-pair helper** (D4 fix) with decompress fallback | ~½ day | Three copies, one real variant |
| F8 | **Cross-version import supersede** (ALPHA_REVIEW R-3) via the existing `_find_current_binary` supersede dance, reused instead of re-typed | ~½ day | Reuses the resolver's exact pattern — more evidence for extracting the supersede primitive instead of cloning it |
| F9 | **Test scaffolding** (D6): `_insert_binary`, `_config`, fixture `_pool_prefix` into `tests/fixtures/db.py` | ~½ day | ~15 definitions → 1; single update point for schema fixtures |
| F10 | **Craft sweeps** (D8 + §5): `Set`→`set`, dead `_file_sha256` call, `ImportError` rename | ~1 h | Zero-risk; do while nearby |

## 7. Recommended sequence

1. **F5 first** — it's a user-visible defect riding the same lines this pass touches.
2. **F1 + F4 + F6 + F7 in one "helper consolidation" commit set** — all mechanical,
   all covered by the existing 914-test net; expectations: no output changes except
   the now-loud package-stack payload skip (F4), which *should* change (a warning).
3. **F2** — after the helpers settle; re-pin the three call sites to one predicate and
   watch the contract suite stay green.
4. **F3 + F10** — pure naming/craft commit, trivially reviewable.
5. **F8** — supersede primitive + cross-version test (behavior change, isolated).
6. **F9 (+ optionally D7 registry)** — test-side hygiene; can trail the release.

Everything above fits inside the current architecture — no module moves, no schema
changes, no new dependencies — and the recorded dual-runner baseline + ruff + contract
suite is exactly the harness needed to land it safely.

---

## Appendix — Evidence trail

- Duplication points were verified by reading **both** sides of each pair (table §3
  cites the location of every copy).
- The import-axis trace (§4.1) was confirmed at `package_stack.py:728-796` (the
  orchestration loop) and `import_local.py:296-402` (the single registration path).
- No commands, tests, builds, or network actions were executed for this review
  (per instruction); the 914-test baseline is cited from `tests/REGRESSION.md`.
- Complements, and intentionally does not repeat, the functional findings of
  `ALPHA_REVIEW.md` (R-1 … R-11); where a structural fix here overlaps one of those
  findings (F5 ↔ R-2, F8 ↔ R-3), the cross-reference is explicit.
