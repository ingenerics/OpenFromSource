# Research: Apt VersionHash Fidelity

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

**Method.** The feature description ("packages from the local cache only appear as
upgradeable when there is actually an upgrade") was traced against the current source at
HEAD and against apt's own metadata semantics. The root cause was diagnosed by porting
apt's VersionHash and comparing every emitted handoff stanza against the actual `.deb`
control of the same package on the real cache; the fix was then implemented and
re-verified (0/207 mismatches). There were no open `[NEEDS CLARIFICATION]` markers in the
spec — the spec's Assumptions and Clarifications record the settled decisions. This file
documents the technical contract and the resolutions the fix relies on.

---

## 1. The apt VersionHash contract (the core external knowledge this feature preserves)

**Decision**: capture, in-project, apt's VersionHash algorithm exactly as implemented, so
index emission can be held to it and future work cannot silently regress it.

**The algorithm** (apt 3.0.3, `apt-pkg/deb/deblistparser.cc`,
`debListParser::VersionHash`, line 335):

- The hash is computed over these control fields, **in this order**:
  `Installed-Size`, `Depends`, `Pre-Depends`, `Conflicts`, `Breaks`, `Replaces`.
  A field that is absent contributes nothing.
- Hash function: a DJB-2-style rolling hash starting at `5381`; for every significant
  character `c`: `hash = 33 * hash + tolower(c)`.
- Normalisation before hashing:
  - whitespace (space, tab, …) is **skipped** (this undoes dpkg's field
    reformatting/spacing differences);
  - the `=` character is **skipped** (this undoes dpkg's reformatting of `<=`/`>=`
    operators to `<`/`>` semantics — the relation is compared without the equality
    half);
  - a leading `0:` epoch is **stripped** (when a `0` is immediately followed by `:`,
    both are skipped);
  - all other characters are lowercased and folded into the hash.

**Merge semantics** (apt 3.0.3, `apt-pkg/pkgcachegen.cc`,
`pkgCacheGenerator::MergeListVersion`): when apt parses a repo `Packages` record and an
installed dpkg-status record for the same package:
- equal version string **and** equal VersionHash → the repo version is merged into the
  existing version object (a second file index is attached) → the installed version is
  current, **not upgradable**;
- equal version string **but** different VersionHash → a **separate version object** is
  created for the repo copy → apt sees an installed version and a distinct candidate at
  the same version string → reports **"X upgradable from X"**.

**Rationale**: this is the authoritative definition of "an upgrade actually exists" from
apt's point of view, and it is the only place the behaviour is defined. Recording it
makes the emission path provably correct rather than accidentally correct (spec FR-007,
SC-005).

**Alternatives considered**: none — this is a reference capture of an external contract,
not a design choice.

---

## 2. Root cause: an invented `Depends` field changed the VersionHash

**Decision**: the regression was caused by the handoff stanza emitting a `Depends` field
for packages whose control has **no** `Depends` field, derived from the resolver's merged
`Depends`+`Pre-Depends` view.

**Evidence** (real cache, `/var/cache/package_mgmt/binary`, handoff `trixie/main`):
the 7 reported packages — `debianutils`, `libexpat1`, `libpam-modules`, `mount`,
`perl-base`, `sed`, `tar` — all ship a `.deb` whose control declares only
`Pre-Depends` (no `Depends`). dpkg therefore records no `Depends` in the operator's
dpkg-status file. The old emission logic

```python
depends = getattr(entry, "depends_field", None) or entry.depends_raw
```

fell back to `depends_raw` — the **merged** `Depends`+`Pre-Depends` view built for the
resolver's closure walk (`merge_depends_text`, `index_loader.py`) — so the repo stanza
gained `Depends: <pre-depends targets>`. The VersionHash of the repo record then differed
from the installed record's, splitting them and reporting the identical version as
upgradable.

A parity scan porting the §1 algorithm over the real handoff index found exactly these
**7/207** mismatches before the fix, all with a single differing field (`Depends`).

**Alternatives considered**:
- *Emit the merged view only when the control truly has a `Depends` field*: subtle and
  fragile — the merged text still rewrites the original field's text and ordering
  (`_format_dep_spec` re-renders it), which the hash is partly insensitive to (spaces,
  `=`, case) but partly sensitive to (ordering of targets, `0:` epochs in some cases).
  Rejected — verbatim reproduction is simpler and provably correct.
- *Compute the VersionHash and ship a canonical form*: over-engineered; apt's normalisation
  already tolerates formatting differences, so carrying the field text verbatim is
  sufficient. Rejected.

---

## 3. Data flow: where each stanza's VersionHash fields come from

**Decision**: the emitted VersionHash fields are read from the package's actual control
record — the cached archive index for upstream components, the shipped `.deb` control for
the `local` component.

**Evidence**:
- Upstream (`main`/`contrib`/`non-free`/backports): `generate_handoff`/`generate_diff`
  load the cached archive index with `load_binary_index` and emit
  `_paragraph(entry)`. The Debian archive `Packages` file is generated from the same
  `.deb` control the operator installs, so carrying its `Depends`/`Pre-Depends`/
  `Installed-Size`/`Conflicts`/`Breaks`/`Replaces` verbatim reproduces the installed
  record — and since apt strips whitespace/`=`/`0:`/case (§1), residual formatting
  differences cannot change the hash.
- `local`: there is no archive index; the payload is the supplied `.deb`.
  `_read_deb_control` (`handoff.py`) reads the control via `debian.debfile.DebFile`
  (python-debian, already a dependency) and extracts exactly the VersionHash fields. A
  readable control is authoritative (a missing `Depends` stays missing); an **unreadable**
  payload returns `{}` and the entry falls back to the DB-only reconstruction rather
  than failing generation (FR-006).

**Design points**:
- `BinaryPackage` gains `depends_field` (the original `Depends` text, kept separate from
  `depends_raw` which folds `Pre-Depends` in for the resolver), plus `installed_size`,
  `conflicts_raw`, `breaks_raw`, `replaces_raw` (the remaining VersionHash inputs).
- `load_binary_index` populates these from the archive index (`para.get(...)`);
  `_local_binary_entry` populates them from `_read_deb_control`.
- `_paragraph` emits, in order: `Installed-Size`, `Depends` (from `depends_field` **only**),
  `Pre-Depends`, `Conflicts`, `Breaks`, `Replaces`. `Multi-Arch`/`Provides` are still
  carried for `name:any` resolution and virtual-package installs (unchanged behaviour).

**Alternatives considered**:
- *Reconstruct all VersionHash fields from the DB*: the DB does not store
  `Conflicts`/`Breaks`/`Replaces`/`Installed-Size` for archive packages, and its
  dependency text is the merged view. Rejected — the archive index / `.deb` control is
  the authoritative source and is already loaded.

---

## 4. Upgrade semantics preserved

**Decision**: the fix only affects same-version merging. A strictly newer cached version
still presents as upgradable and installs (spec FR-004, US1 AS2, US4).

**Evidence**: apt's version comparison (`DoCmpVersion`) runs before the hash check in
`MergeListVersion`; a strictly greater version is always a new candidate regardless of
hash. The parity scan confirms equal-version packages now merge; the existing
`test_generates_filtered_index_for_verified_set` and upgrade tests confirm newer versions
still surface.

---

## 5. Verification methodology: the VersionHash parity scan

**Decision**: prove correctness by porting §1's algorithm and comparing every emitted
stanza against the `.deb` control of the same `(package, version, arch)`.

**Evidence**: a Python port of `debListParser::VersionHash` (field order, DJB-2 hash,
whitespace/`=`/`0:`/case normalisation) was run over:
- the **before** handoff index (real cache): **7/207** mismatches — exactly the 7
  reported packages, all with a spurious `Depends`;
- the **after** (fixed) emission: **0/207** mismatches.

The scan is repeatable from `quickstart.md` and is the regression proof for SC-002/SC-004.

**Alternatives considered**: running apt itself on the operator is the ultimate end-to-end
check (quickstart Phase 3) but cannot be automated in unit tests; the hash port gives a
fast, deterministic unit-level oracle. Both are retained.

---

## 6. Non-goals confirmed

- No schema migration, no new tables/columns (spec Assumption "Implementation status",
  plan Technical Context "Schema freeze").
- No new runtime dependency — `debian.debfile` is python-debian, already used.
- No operator-side tooling change — the operator keeps using ordinary apt.
- No change to how upstream packages are fetched, resolved, or downloaded.
- No change to the `depends_raw` merged view's role in the resolver's closure walk — it
  is only no longer emitted into stanzas.

---

## Consolidated decisions

| Decision | Choice | Artifact |
|----------|--------|----------|
| VersionHash reference | apt `debListParser::VersionHash` (field set, order, normalisation, DJB-2 hash); recorded in-project | `research.md` §1 |
| Root cause | invented `Depends` from merged `depends_raw` for pre-depends-only packages | `handoff.py` `_paragraph` |
| Emission source | archive index (upstream) / `.deb` control via `DebFile` (`local`) | `index_loader.py`, `handoff.py` |
| `Depends` emission | `depends_field` only; never `depends_raw` | `handoff.py` `_paragraph` |
| Degraded fallback | unreadable `local` payload → DB reconstruction, generation does not fail | `handoff.py` `_local_binary_entry` |
| Verification | VersionHash parity scan (0/207 after fix) + operator apt scenarios | `quickstart.md` |
