# Research: Local Patch Lane

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

**Method.** The feature description was traced against the current source at HEAD and
against the existing component/distribution machinery. Each design decision below records
the decision, the code evidence, and the alternatives considered. There were no open
`[NEEDS CLARIFICATION]` markers in the spec — the spec's Assumptions already fixed the
component scope, command form, `--source` shape (a directory holding the complete
source set — see §5a), duplicate-import behavior, and persona.
This file documents the technical resolutions those assumptions require.

---

## 1. Component scope: `local` is a component within the default release

**Decision**: `local` is a value of the existing `component` column, stored under the
default (primary) release — the same release the operator's apt sources already use.

**Evidence**:
- `binaries.component` / `sources.component` exist (`database.py:16,41`) with
  `UNIQUE(package_name, version, architecture, release, component)` — a local row can
  coexist with the upstream row at the same name/version/arch without a schema change.
- `handoff.py:665-670` already refuses a `deb file:` line whose release/components are
  unserved; the per-release multi-component `deb` line already exists
  (`bootstrap.sh` `printf 'deb [signed-by=...] file:%s %s %s\n' … "$rel" "$comps"`,
  `handoff.py:161`). Adding `local` to the served components of the default release is
  exactly the "per-release multi-component logic already exists" the case cites.

**Alternatives considered**:
- *A separate pseudo-release (e.g. `local` as its own suite)*: would add a second
  `deb` line and a second `Release`/signature — more machinery, no benefit; the
  operator already trusts the default release. Rejected.
- *A release named after the patch (e.g. `local/patched`)*: the `component.split('/',
  1)[1]` handling in handoff summaries suggests slash components were contemplated, but
  a single `local` component is simpler and matches the description. Rejected.

---

## 2. Priority: `local` slots into the existing version-then-component tie-break

**Decision**: add `"local": -1` to `_COMPONENT_PRIORITY` (`repository.py:8-18`) so it
sorts ahead of `main` (0). The existing ordering is version-first, component as
tie-breaker (`_best_entry`, `repository.py:98-104`; same in `lookup_source`). `local`
wins exactly when versions are equal — which is the primary use case (a patched rebuild
at the same version, e.g. the tutorial's `libxkbcommon-x11-0 1.7.0-2`). A strictly
newer upstream version still wins, matching Debian's normal upgrade semantics.

**Evidence**: `_best_entry` builds `[(parse_version(version), -_component_order(comp),
entry)]`, sorts descending, returns the head. The user's "we already have an (implicit)
priority between components" refers to exactly this mechanism; `local` becomes its top
entry, not a new mechanism.

**Rationale**: changing to "local always wins regardless of version" would break
upgrade semantics (a newer upstream fix would never be preferred) and would deviate from
how `main`/`contrib`/`non-free` already interrelate. The spec's US4 AS1 ("the local
version is chosen over every upstream component") is satisfied for the equal-version
patch case; the tie-break nuance is recorded here so planning/tasks implement the
consistent behavior.

**Alternatives considered**:
- *Local always wins*: breaks version upgrades; rejected.
- *Local as a separate resolution layer*: over-engineered; rejected.

---

## 3. Resolver visibility: inject `local` records into the merged index

**Decision**: `PackageResolver.ensure_indexes()` (`resolver.py:284-317`) appends
current+verified `local` binaries and sources from the DB to the merged
`RepositoryIndex` after the configured archive sources are merged. `local` has no
archive index, so without injection the resolver's `lookup_binary*`/`lookup_source`
(`repository.py:22-95`) would never see it and FR-006 could not hold.

**Evidence**: `ensure_indexes()` merges `load_binary_index(...)` per configured
release/component into `merged_binary` and builds a `RepositoryIndex`. `local` is not in
any configured source's `components`, so it is absent today. The DB already holds the
authoritative local metadata (`binaries`/`sources` rows with `component='local'`),
including `verified`.

**Design points**:
- Only `status='current' AND verified=1` local rows are injected, so the resolver never
  considers a half-imported local package.
- Injected entries are `BinaryPackage`/`SourcePackage` instances (the same models
  `load_binary_index`/`load_source_index` produce), carrying `release=default`,
  `component='local'`, parsed `dependencies`, and `provides` (from the parsed control).
- The injection is DB-read only — no index file, no fetch (Constitution I, VII).

**Alternatives considered**:
- *Generate a `local` `Packages.gz` in the tool's own `dists/`*: adds an index file the
  tool never needs (the tool's `dists/` mirrors archives); handoff builds its own index
  from the DB anyway (see §4). Rejected — DB injection is simpler and keeps the
  database-first rule.
- *Resolve local through the DB probe path only*: the probes only cover "already
  satisfied" groups; a `local` package as a *dependency* of an added package would not
  be selectable. Rejected.

---

## 4. Handoff/diff: serve `local` from the DB, not an archive index

**Decision**: extend the served base set and the per-component index generation in
`handoff.py` so `local` is served like any other component — but with its `Packages.gz`
built from the DB records (there is no archive `Packages.gz` to confirm against).

**Evidence**:
- `_component_bases(config)` (`handoff.py:712-723`) returns `(release, component)`
  pairs only for configured components whose cached archive index exists. `local` must
  be added to the base set when local records exist (else
  `generate_handoff`/`generate_diff` routes them to `excluded_config`,
  `handoff.py:1224-1231`).
- The per-component generation (`handoff.py:1246-1260`) currently loads the archive
  index (`load_binary_index`) and `lookup_entry(...)` to confirm checksum/filename for
  each record. For `local`, the record's own metadata is authoritative: filename is
  derived from the layout convention, size/SHA256 from the copied payload. The existing
  `_paragraph` builder (`handoff.py:726-756`) already produces a Packages paragraph from
  the same fields — it can be fed directly for local records.
- The `deb` line (`bootstrap.sh:161`) and the `Release` `Components:` line
  (`handoff.py:805-814`) are both driven by the served component list; adding `local`
  to the default release's component list covers both.

**Design points**:
- Local component is served **only** when at least one current+verified local record
  exists (an empty cache must not gain a bogus `local` component). When present, it is
  appended to the default release's component list.
- Local records with a missing pool file are excluded like any other component (with a
  warning) — the same integrity posture.
- Diff (`generate_diff`) mirrors the handoff logic so `sync` carries local packages
  (FR-007).

**Alternatives considered**:
- *Require a cached local index like archives*: `import-deb` would have to generate and
  maintain one; the DB is already the source of truth. Rejected.
- *Ship local via a separate deb line*: unnecessary; per-release multi-component exists.
  Rejected.

---

## 5. `.deb` parsing: python-debian `DebFile`

**Decision**: parse the imported `.deb` with `debian.debfile.DebFile` (control fields)
and `debian.debian_support.Version` (version validation), both already available.

**Evidence**: the project already imports `debian.deb822` (`index_loader.py:13`) and
`debian.debian_support.Version` (`versions.py:1`); `debian.debfile` is importable in the
runtime environment. `DebFile.control` gives `Package`, `Version`, `Architecture`,
`Depends`, `Pre-Depends`, `Provides`, `Multi-Arch` — everything the DB row, the
dependency re-resolution, and the handoff `_paragraph` need.

**Design points**:
- Dependency re-resolution (FR-003) reuses the existing `parse_build_depends_field` /
  `DependencyGraph` machinery (`lib/model/dependency.py`) to parse the declared
  `Depends`/`Pre-Depends`, then checks each requirement against the repository index
  (`lookup_binary_satisfying`, `repository.py:44-60`) and the DB's current binaries —
  exactly the check `verify` already performs (`verify.py:182-188`). An unsatisfiable
  requirement aborts before any registration.
- `Provides`/`Multi-Arch` are parsed for the record and for the handoff paragraph
  (the handoff preserves `Multi-Arch` for `name:any` resolution, `handoff.py:736-741`).

**Alternatives considered**:
- *Shell out to `dpkg-deb -f`*: works but forks per file and bypasses the typed
  parsing the project already uses; rejected.
- *Reuse apt's python bindings*: not a declared dependency; rejected.

---

## 5a. `--source` is a directory holding the complete source set

**Decision**: `--source <dir>` takes a **directory** containing the complete rebuilt
source package — the `.dsc` plus every file its `Files:`/`Checksums-*:` sections
reference (the upstream `.orig.tar.*` and Debian `.debian.tar.*` tarballs). Every
referenced file must be present in the directory and match the `.dsc`'s declared
checksums; the whole set is then registered and copied to the source pool.

**Rationale**: a single `.dsc` delivers nothing on its own — `dpkg-source -x
<dsc>.dsc` resolves and verifies the companion tarballs from the same directory, and
fails if they are absent. A `--source <file>` that only "works" when the companions
happen to sit beside it is a silent trap. Requiring a directory makes the complete-set
requirement explicit and self-validating: the import fails with a clear error naming
the missing/mismatched file instead of registering a source that cannot be unpacked
(FR-005, US3 AS3).

**Design points**:
- The `.dsc` is parsed with `debian.deb822` (already imported, `index_loader.py:13`);
  the `Files`/`Checksums-Sha256` paragraphs give the file list and expected hashes.
- Every listed file must exist in the directory; each is SHA256-verified against the
  `.dsc` before anything is registered (integrity posture consistent with the
  `verified` concept — the source is only ever marked verified when the set it names is
  present and intact).
- A path that is a file, a missing/unreadable directory, or a directory failing the
  presence/checksum check aborts with zero changes (FR-008).
- Storage mirrors the binary pool convention: `source/pool/local/<prefix>/<pkg>/…`
  holds the `.dsc` + tarballs, same layout as archive source components (spec
  Assumption "`--source` form").

**Alternatives considered**:
- *`--source <dsc>` with companions auto-discovered in the same directory*: the
  original assumption; "works" only when companions are co-located and silently
  registers nothing usable otherwise. Rejected per the user's feedback.
- *Accept a single tarball (`.orig` or `.debian`)*: insufficient for a rebuild; a
  rebuildable source requires the whole set. Rejected.

---

## 6. Payload placement: identical pool layout

**Decision**: copy the payload to `pool/local/<prefix>/<pkg>/<pkg>_<version>_<arch>.deb`
using the exact layout convention as archive components.

**Evidence**: existing pool is `pool/main/libn/libnotify/…` — `pool/<component>/
<prefix>/<pkg>/<file>`. `safe_pool_path` (`paths.py:110+`) validates pool-relative
paths. The prefix rule matches Debian's pool convention (first letter, `lib*` → `lib<first>`).

**Design point**: the destination filename is derived from the parsed control
(`<package>_<version>_<arch>.deb`), so the same payload always lands at the same path —
making re-import idempotent (FR-009) and handoff lookups deterministic.

**Alternatives considered**: none — layout parity is a spec requirement (FR-004,
Assumption "Identical layout").

---

## 7. Import atomicity and DB-first ordering

**Decision**: follow Constitution VII strictly. Order per file: (a) parse and validate
the `.deb` control; (b) re-resolve declared deps (fail → abort with zero changes);
(c) register the binary row (`verified=0`); (d) copy the payload + compute SHA256; (e)
flip `verified=1`; (f) optional source registration + `binary_to_source` link — all in
one transaction. A failure anywhere rolls back to zero DB and pool changes (FR-008).

**Evidence**: `upsert_binary` (`resolver.py:124-139`) already does insert-or-update with
`verified=0` and `RETURNING id`; `upsert_source` and the `binary_to_source` link
(`resolver.py:146-167`, and the `_register_source_for` pattern) already exist and are
reused. Pool writes follow the `*.part` → verify → `os.replace` pattern used by the
downloader (`downloader.py:8`).

**Design points**:
- Idempotency (FR-009): re-importing an identical binary hits the `ON CONFLICT` update
  path and re-verifies the payload — no duplicate rows.
- `download` skips local records automatically: its pending set is
  `status='current' AND verified=0` (`downloader.py:261-264`), and local rows are
  verified at import.

---

## 8. Non-goals confirmed

- No `.deb` **building** (spec Assumption "Scope boundary") — `import-deb` only ingests
  finished artifacts.
- No operator-side tooling change — the operator uses ordinary apt against the handoff
  `deb` line.
- No changes to how upstream packages are fetched or resolved.
- No schema migration.

---

## Consolidated decisions

| Decision | Choice | Artifact |
|----------|--------|----------|
| Component scope | `local` as a component under the default release | `import_local.py`, `handoff.py` |
| Priority | `_COMPONENT_PRIORITY["local"] = -1`, version-then-component tie-break | `repository.py` |
| Resolver visibility | DB injection into merged `RepositoryIndex` | `resolver.py` |
| Distribution | `local` served in handoff + diff; index built from DB; in `deb` line | `handoff.py` |
| `.deb` parsing | python-debian `DebFile` | `import_local.py` |
| `--source` form | directory with complete, `.dsc`-verified source set | `import_local.py` |
| Payload layout | identical pool layout under `pool/local/…` | `import_local.py` |
| Atomicity | DB-first, single transaction, zero partial changes | `import_local.py` |
