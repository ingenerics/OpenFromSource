# Research: Source-Stack Packaging

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

This research resolves the design questions the spec's "think hard / use common
sense" instruction leaves open (spec Clarifications Q1/Q2/Q3 and the Assumptions
section), grounded in the existing codebase (spec 021 `import-deb` machinery,
the docs/005 build procedure, the frozen DB schema) and the Debian packaging
toolset actually present on the packaging host (`dpkg-deb 1.22.22`,
`dpkg-shlibdeps`, `dpkg-source`, `fakeroot`).

---

## §1 — The import lane already exists: reuse `import_local_deb`

**Decision**: Every generated artifact (a `.deb` + a complete source-package
directory) is imported through the existing `import_local_deb(conn, config,
deb_path, source_dir=...)` (`src/package_mgmt/lib/service/import_local.py:293`)
— the exact path `import-deb` uses — with **no changes** to the import service.

**Rationale**: `import_local_deb` already provides every guarantee FR-005 asks
for, verified by the spec 021 suite:
- parses the `.deb` control with python-debian (`debian.debfile.DebFile`),
- re-resolves every declared `Depends`/`Pre-Depends` against the cache
  (`_verify_dependencies`, `import_local.py:140`) and aborts atomically naming
  the unmet requirement,
- registers the binary row first (database-first, `upsert_binary`), copies the
  payload to `pool/local/<prefix>/<pkg>/<pkg>_<ver>_<arch>.deb`, then sets
  `verified=1` (`import_local.py:361-386`),
- with `--source`, validates the complete source set against the `.dsc`
  checksums up front (`_validate_source_set`, `import_local.py:200`), copies it
  to `source/pool/local/...`, registers the source row (`upsert_source`), links
  via `binary_to_source` (`link_binary_to_source`), and marks it verified
  (`_import_source`, `import_local.py:249`),
- is idempotent on re-import (`ON CONFLICT` update path) and atomic on any
  failure (rollback + pool-file cleanup).

**Alternatives considered**:
- *New bespoke import code*: rejected — it would duplicate a battle-tested,
  contract-pinned path and risk diverging from the spec 021 guarantees for zero
  benefit.
- *Reusing `import-deb` as a subprocess per component*: rejected — `package-stack`
  must import in dependency order within one transaction-ish run and produce a
  single whole-set report; calling the CLI per component loses that cohesion and
  re-parses config per call. Calling the shared `import_local_deb` function
  directly (as `handle_import_deb` does) is the right seam.

**Consequence**: the generated `.deb`/source artifacts must satisfy the same
contract as hand-built ones — a parseable control with a valid Debian version,
and (for the source) a directory with exactly one `.dsc` plus every referenced
file present and checksum-matching. That becomes `debgen`/`srcgen`'s acceptance
bar (see §3/§4).

---

## §2 — The automation-vs-scripting split (spec Q1)

**Decision**: A generic `package-stack` command automates the reusable steps;
per-stack knowledge is data (a recipe) plus a staged build. Concretely:

- **Automated in the tool**: recipe parsing, per-component binary-package
  generation, `Depends` derivation, deterministic source-package generation,
  dependency ordering, import + linkage (reusing §1), whole-set verification,
  loud+atomic failure handling.
- **Scripted/declarative (per stack)**: the recipe file (`recipes/<stack>.toml`)
  and the staged build (`scripts/build-hyprland.sh --stage DIR`).

**Rationale**: Packaging, ordering, dependency derivation, and verification are
identical for every source-built stack and error-prone to hand-do; the component
list, metadata, and build steps differ per stack and belong in data, not code
(spec Assumptions, FR-001/FR-011). This matches how spec 023 productized
`align-repo` while keeping acquisition/build as documented procedures.

**Alternatives considered**:
- *Fully scripted (shell) packaging*: rejected — `Depends` derivation, ordering,
  and DB import would reimplement tool logic outside the tested Python
  codebase.
- *A full debhelper framework per component*: rejected (spec Assumption
  "no overengineering") — the stack already builds with meson/cmake and needs a
  minimal `debian/` wrapper driving the same build; a full debhelper/changelog
  chain adds maintenance for no benefit.
- *One-off "packaging" built into the existing build script*: rejected — the
  build script is per-stack (scripted side); packaging is the generic side and
  belongs in the tool.

---

## §3 — Binary `.deb` generation and `Depends` derivation

**Decision**: `debgen` builds a Debian binary package from the staged install
directory with standard tooling:

1. Treat the staged install directory (the DESTDIR-style root the build script
   `--stage` produces) as the package **data root**; the payload is exactly the
   staged files (spec Assumption "file ownership decided by the staged install").
2. Generate a complete `control` record (FR-002): `Package`, `Version`
   (`<upstream>-<revision>`), `Architecture` (from the recipe), `Section`,
   `Priority: optional`, `Maintainer`, `Homepage`, `Description`, `Installed-Size`
   (computed from the staged tree), and `Depends`.
3. Derive `Depends` automatically from the staged binaries' linked runtime
   libraries using `dpkg-shlibdeps` (the standard Debian tool): run it over the
   staged binaries (with `LD_LIBRARY_PATH`/`-l` pointing at the staged library
   tree and the system lib dirs) and use its `-O` output as the derived
   `Depends` string. It maps each linked library to the owning Debian package
   via the host's `shlibs`/`symbols` metadata (the packaging host has the stack
   and the cache's build deps installed — spec Assumption "Packaging host").
4. Merge the recipe's explicit `depends` into the derived set (append/override
   per recipe), and **report every library `dpkg-shlibdeps` cannot attribute by
   name** (spec edge case "Un-attributable runtime library") — the recipe must
   then declare it; it is never silently dropped.
5. Build the `.deb` deterministically with `dpkg-deb --build` using
   `--root-owner-group` and `SOURCE_DATE_EPOCH` + fixed mtimes (FR-009).

**Why `dpkg-shlibdeps` and not a hand-rolled scanner**: FR-003 explicitly wants
`Depends` derived from linked runtime libraries and attributed to Debian
packages; `dpkg-shlibdeps` is the tool the Debian toolchain itself uses for
exactly this and it is already in the project's documented toolset (spec
Assumption "no overengineering"). A hand-rolled `readelf -d` + `/var/lib/dpkg`
scan would re-implement it with worse edge-case behavior (symbol versioning,
`libc` splitting, `shlibs` overrides).

**Constraint reconciliation** (spec edge case "Emitted version constraint not
satisfiable"): when a derived/declared constraint (e.g. a companion-library
version) is not satisfied by the imported `local` version, `package-stack`
reconciles it for the imported set — the recipe may pin the constraint, or the
command relaxes a recorded companion constraint to the imported version — or
fails loudly naming the pair, so the whole-set install stays satisfiable.

**Alternatives considered**:
- *`dpkg-shlibdeps` on a temp-installed tree*: rejected as overkill — pointing
  it at the staged tree plus system dirs covers the stack (companion libraries
  the host has installed resolve from the host's dpkg database).
- *Version-pinning every dependency from the frozen tree*: rejected — `Depends`
  is written with the operators `dpkg-shlibdeps` derives (open lower bounds for
  system libs, exact for shlibs where needed); the whole-set install check
  (FR-007) is the safety net, not aggressive pinning.

---

## §4 — Deterministic source-package generation

**Decision**: `srcgen` produces a complete, rebuildable Debian source package
from the pinned source tree at the recorded version:

1. Copy the pinned source tree (`repos/<component>` at the manifest/recipe
   commit, with submodules) to a scratch dir and generate a minimal `debian/`
   wrapper in it: `control` (`Source`, `Version`, `Build-Depends` from the
   recipe), a `rules` file that drives the component's own build system
   (meson/cmake — exactly how the stack already builds), `changelog`,
   `compat`, `copyright` (or a minimal placeholder pointing at the tree's
   license files), and a `format`/`source` stamp.
2. Run `dpkg-source -b` on the prepared tree to produce the `.dsc` plus the
   referenced files (orig tarball + Debian tarball) — `Format: 3.0 (quilt)` or
   `native` depending on the recipe (the vendored `sdbus-c++` is an upstream
   tree, so `3.0 (quilt)` with a generated orig tarball).
3. Determinism (FR-009): fixed `SOURCE_DATE_EPOCH`, fixed tar member mtimes and
   ordering, sorted `debian/changelog`/control generation, so identical trees +
   recipe → identical `.dsc` + files.
4. The result is a **directory** holding exactly one `.dsc` + every referenced
   file — precisely the `--source <dir>` shape `import_local_deb` validates and
   imports (§1), so `apt-get source`/`dpkg-source -x` works offline and the
   recorded rebuild (the `rules` wrapper) reproduces the shipped binary (FR-004,
   US4).

**Rebuild validation**: US4 requires demonstrating that the recorded rebuild
reproduces the shipped binary. `srcgen` validates rebuildability, not assumes it
(spec edge case "Source package not rebuildable"): at minimum `dpkg-source -x`
of the generated set succeeds offline; the full "rebuild reproduces binary"
demonstration is a documented, runnable procedure (quickstart) using the
`debian/rules` wrapper — consistent with spec FR-004/FR-008 wording.

**Alternatives considered**:
- *`dpkg-buildpackage` full build*: rejected for the tool path — the spec's
  automation boundary is packaging already-staged artifacts and generating the
  rebuildable source; actually rebuilding is a validation/documentation concern
  (US4) and stays in the quickstart, not in `package-stack`.
- *Shipping the raw `repos/` tree as the "source package"*: rejected — a raw git
  tree is not a Debian source package; `import-deb --source` requires a `.dsc` +
  referenced files, and the operator must get them via the apt source channel
  (`apt-get source`), which requires the `.dsc`.

---

## §5 — Dependency ordering (FR-006)

**Decision**: `package-stack` topologically orders recipe entries so dependencies
import before dependents, regardless of recipe order. The ordering is derived
from each component's declared `depends` (explicit recipe deps + the source-built
dependencies named in the recipe) resolved against the component names in the
recipe; it mirrors the docs/005 build order (protocols → scanner → companion
libraries → Hyprland → `sdbus-c++` → companion tools). A
cycle or an unknown dependency (a component depending on another component not in
the recipe — spec edge case "Missing source-built dependency in the recipe") is a
loud failure naming the gap.

**Why this is needed**: each import re-resolves its `Depends` against the cache
at import time (§1); a dependent imported before its source-built dependency
would fail re-resolution with an unsatisfiable requirement. Ordering makes every
import satisfiable by construction (frozen trixie + already-imported `local`).

---

## §6 — Determinism and version/revision scheme (FR-009/FR-010)

**Decision**:
- Determinism is enforced by construction: `SOURCE_DATE_EPOCH` (fixed to a
  recipe/repo-chosen epoch, defaulting to the manifest era date), fixed tar
  member mtimes and lexicographic ordering, sorted control/changelog generation,
  `dpkg-deb --root-owner-group`. Two runs over identical inputs MUST produce
  identical `.deb`/`.dsc` — asserted in unit tests (byte-for-byte) and recorded
  in quickstart (FR-009, spec edge case "Reproducibility / determinism").
- Version scheme: `<upstream>-1` (Debian revision) by default (spec Assumption);
  re-packaging the same upstream version at a changed source/recipe bumps the
  revision (`<upstream>-2`, ...) so `apt upgrade` semantics hold on the operator
  (FR-010); re-importing an identical pair is idempotent (the existing
  `ON CONFLICT` path, §1). The recipe can override the revision.

---

## §7 — Staged build (FR-011)

**Decision**: `scripts/build-hyprland.sh` gains a `--stage DIR` mode that installs
each component into `DIR/<component>` (a DESTDIR-style root) instead of `/usr`
(e.g. `DESTDIR="$DIR/$name" ninja -C ... install` and
`cmake --install ... --prefix /usr --destdir "$DIR/$name"`), leaving the
in-place delivery model of docs/005 unchanged (additive). Each staged directory
becomes one component's `.deb` data root (§3). The source-built dependency
stages identically (`sdbus-c++`), and the recipe records each component's
staged-dir name.

**Rationale**: staging is per-stack (how each component installs) and belongs on
the scripted side; `package-stack` consumes the already-staged directories and
never builds (FR-011, spec Assumption "Staging").

---

## §8 — Recipe format

**Decision**: a TOML file `recipes/<stack>.toml` (tracked), with one table per
component. See [contracts/recipe.md](contracts/recipe.md) for the exact schema;
essentially each entry declares: component name, binary package name, upstream
version (+ optional Debian revision), source tree path (in `repos/`; for
`sdbus-c++` the vendored subproject path), build system (for the generated
`debian/rules`), staged-dir name, metadata (description, maintainer, section,
homepage), explicit `depends`/`build_depends`, and architecture. A data-only
component (protocol XML, e.g. upstream `wayland-protocols`) is `Architecture:
all`; the compiled components are `amd64`.

**Rationale**: TOML matches the project's existing config/manifest convention
(`project.defaults.toml`, `manifests/hyprland-*.toml`) and Python's stdlib
`tomllib` — no new dependency.

---

## §9 — Whole-set validation (FR-007)

**Decision**: after importing all components, `package-stack` runs the existing
integrity machinery: `verify` semantics (no unlinked binaries, no unverified
records for the set) plus a **simulated offline `apt install`** of the whole set
against the cache index — the same `apt-get -s`-style check the spec 021
integration tests use against the handoff — with no network access. Both must
pass before the command reports success (FR-007, SC-002/SC-003/SC-004).

---

## §10 — Testing strategy

**Decision**: TDD with both mandatory runners (pytest + unittest). Unit tests
cover the pure logic (recipe load/validate, topological ordering, control
generation, `Depends` merge + un-attributable reporting, determinism, revision
bump) using the existing `tests/fixtures/make_test_deb.py` builders; contract
tests extend `tests/contract/test_cli.py` for `package-stack` args/exit codes/JSON
shape; an integration test (`test_package_stack_flow.py`) builds two small fixture
components through the real `debgen`/`srcgen`/`import` path against a fixture
cache (`make_test_iso.py` archive + `make_fixture_repos.py`-style staged trees)
and validates ordering, linkage, verification, and offline handoff/apt install.
Determinism is asserted byte-for-byte.

**Why**: FR-009 determinism and FR-004 rebuildability are exactly the properties
that silently rot; unit byte-comparison and a runnable quickstart keep them
checked, mirroring how spec 023 asserts its manifest/version semantics.

---

## §11 — Operator source-retrieval channel: the handoff `source/Sources` index (spec Q3)

**Decision**: The operator retrieves imported sources offline with standard
`apt-get source` (FR-008/US4). That requires a **deb-src index** for the `local`
component in the handoff — today the handoff emits only
`dists/<release>/<component>/binary-amd64/Packages[.gz]`
(`handoff.py` writes no `Sources` anywhere; docs/004-version-drift-trap.md:252
explicitly notes the cache exposes no `deb-src` line). So:

1. A new `srcindex` step generates per-(release, component)
   `dists/<release>/<component>/source/Sources` + `Sources.gz` for `local`,
   from the **DB `sources` rows** that are `verified` with `component='local'`
   (each `.dsc`'s `Files:`/`Checksums-Sha256:` reference the imported
   `source/pool/local/...` payloads). No new DB schema — a file artifact
   generated from existing rows (Q3, schema-freeze).
2. The handoff `Release`/`Release.gpg` hash-lists the new `source/Sources` +
   `Sources.gz` entries alongside `Packages` (`_index_hash_entries`), so apt
   parses and authenticates the source channel exactly like the binary one.
3. The handoff bootstrap script's `/etc/apt/sources.list.d/openfromsource-cache.list`
   gains a `deb-src` line for `local` (`_write_bootstrap_script`) — config the
   handoff writes, not new operator tooling.

**Rationale**: `apt-get source` is the project's stated operator experience
(FR-008/US4/SC-004) and the only mechanism that keeps sources on the same
signed-Release trust path as binaries. The data already exists in the DB
(`sources` + `binary_to_source`); the index is a deterministic serialization of
it, so generating it is small, testable, and schema-freeze-compatible. Keeping it
additive (binary `Packages` flow unchanged, FR-012) matches the spec's
"existing channel unchanged" posture.

**Alternatives considered**:
- *Non-apt source channel* (operator fetches `source/pool/local/...` by documented
  path, `dpkg-source -x` directly): rejected — deviates from the explicit
  "`apt-get source`" wording of FR-008/US4, bypasses apt's checksum/Release
  verification, and forces a parallel retrieval procedure on the operator.
- *Deb-src index for all cache components* (main/contrib too): rejected as scope
  creep — this feature only ships source packages under `local`; upstream trixie
  sources are already retrievable from the archive itself. The `deb-src` line is
  added for `local` only.
- *Deferring the index to a later feature*: rejected — FR-008/US4/SC-004 would be
  unfulfillable without it; it is a small, well-understood addition now.

**Consequence**: `package-stack`'s whole-set validation extends to the source
channel — after import + handoff, a simulated offline `apt-get source` of an
imported component must resolve from the `local/source/Sources` index (unit +
integration coverage; quickstart Phase 5).

---

## Consolidated decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Import lane | Reuse `import_local_deb` unchanged (§1) |
| 2 | Automation split | Generic `package-stack` + declarative recipe + `--stage` build (§2) |
| 3 | Binary packaging | `dpkg-shlibdeps`-derived `Depends` + generated control + `dpkg-deb` (§3) |
| 4 | Source packaging | minimal `debian/` wrapper + `dpkg-source -b`, deterministic (§4) |
| 5 | Ordering | topological from recipe deps, loud on cycle/gap (§5) |
| 6 | Determinism/version | `SOURCE_DATE_EPOCH` + fixed ordering; `<upstream>-1`, revision bump (§6) |
| 7 | Staging | `build-hyprland.sh --stage DIR` (§7) |
| 8 | Recipe | TOML, `recipes/<stack>.toml` (§8) |
| 9 | Whole-set validation | existing `verify` + simulated offline apt install (§9) |
| 10 | Testing | TDD, both runners, byte-determinism assertions (§10) |
| 11 | Operator source channel (Q3) | handoff `source/Sources` index for `local` from DB rows + `deb-src` line, additive (§11) |
