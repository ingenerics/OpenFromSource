# Data Model: Resolver & Dependency Correctness

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

**Headline constraint: the SQLite schema is frozen (spec 010/011).** No phase changes any table. The substantive
model change is in the **serialized dependency JSON** (the `binaries.dependencies` and `sources.build_depends`
columns) — a backward-compatible internal shape change — plus additions to the in-memory `RepositoryIndex` and
`PackageResolver` batch state.

---

## Schema

**Unchanged.** Tables `packages`, `binaries`, `sources`, `binary_to_source`, `source_build_deps`, `provides`,
`virtual_resolutions`, `systems` keep their current shape. The phases below operate on serialized JSON, in-memory
index state, and batch state only.

---

## Phase 3+4 — Dependency model: per-alternative constraints, arch restrictions, profiles (F-11/F-12)

**New model type** (`lib/model/dependency.py`):

| Type | Fields | Meaning |
|------|--------|---------|
| `Alternative` (frozen dataclass) | `name: str`, `version_constraint: dict \| None`, `architecture: str \| None` | One member of an alternative group with its **own** constraint and `:arch` |

**Changed fields** on `DependencySpec` and `BuildDependencySpec`:

| Field | Old | New |
|-------|-----|-----|
| `alternatives` | `tuple[str, ...]` (names only) | `tuple[Alternative, ...]` (name + own constraint + arch) |
| `arch_restriction` | — | `tuple[str, ...] \| None` — raw `[arch]` tokens (each optionally `!`-negated) from the dependency line |
| `profiles` | — | `tuple[str, ...] \| None` — `<profile>` build-profile tokens |

**Serialized JSON shape** (`binaries.dependencies`, `sources.build_depends` — both already `TEXT` JSON columns):

- `alternatives` becomes a list of objects `{"name": ..., "version_constraint": {...}, "architecture": ...}`
  (only non-null keys emitted), instead of a list of bare name strings.
- **Backward compatibility**: `from_json` still reads legacy string-form alternatives (a string element wraps as
  `Alternative(name, None, None)`), so caches populated before this feature keep loading without a re-parse.

**Validation rules**:
- `parse_depends_line` preserves every alternative's own constraint and `:arch` (no loss on any but the first).
- A dependency whose `[arch]` restriction **excludes** the target architecture (`amd64`) is dropped; a restriction
  that **includes** it (or no restriction) is kept. Multi-profile tokens are kept (conservative/over-inclusive).
- Any token the parser cannot match produces a `WARNING` and the remainder of the line still parses — nothing is
  dropped silently.

**State transitions**: none to persisted rows; the resolver's alternative walk now satisfies a group only when some
member satisfies **its own** constraint.

---

## Phase 2 — Exact source linkage (F-9/F-10)

**In-memory index addition** (`lib/model/repository.py` `RepositoryIndex`):

| Method | Signature | Semantics |
|--------|-----------|-----------|
| `lookup_source_exact` | `(name, version, release=None, component=None) -> SourcePackage \| None` | Exact `(name, version)` match, optionally scoped to a release/component; no version ordering |

**Linkage rule** (in `resolver.py` `_register_source_for`):
1. Expected source version = `Source:` field's declared version, else `source_version_from_binary(binary_version)`
   (existing helper, already strips `+bN`).
2. Lookup order: exact in the binary's own `(release, component)` → exact in the primary release → exact anywhere →
   newest-in-release fallback.
3. A fallback taken because no exact version matched is **logged** (divergence is never silent).

**Entity**: `BinaryPackage` unchanged; `parse_source_field` (already returns `(name, version)`) is used, not changed.

---

## Phase 6 — Batch-scoped build-dep pass (F-15)

**In-memory batch state** on `PackageResolver` (not persisted, batch-scoped):

| Field | Type | Meaning |
|-------|------|---------|
| `_batch_inserted_sources` | `set[int]` | Source ids created (inserted) by the current batch; the only sources the build-dep pass processes |

**`upsert_source` return contract**: changes from `int` (id) to `(id: int, inserted: bool)` — `inserted` is True
when the row was newly created, False when an existing row was `ON CONFLICT`-updated. Single caller updated
(`_register_source_for`).

**Semantics introduced**:
- The build-dep pass queries only `_batch_inserted_sources` (bounded by the batch, not the whole table).
- Retained sources (pre-existing, updated in place) are never re-processed — a later shallower run does not extend
  their build-tool scope.
- Sources registered **during** the pass (a build tool's own source) join `_batch_inserted_sources` and are picked
  up by the existing loop, preserving transitive build-tool expansion.

---

## Phase 1 & 5 — Behavior semantics (no model change)

**Phase 1 (F-8)**: the alternative-group decision in `_resolve_one_inner` becomes "error only when the whole group
fails". No data change — a satisfied group leaves no error in `stats["errors"]`.

**Phase 5 (F-13)**: `verify`'s direct-name dependency check becomes constraint-aware (current binary's version
against `spec.version_constraint`); alternatives checked against their own constraints (Phase 3 model). Providers
keep their documented over-inclusive behavior.

---

## Cross-phase notes

- **No new tables/columns/indexes**: the schema freeze is absolute for this feature.
- **Entity additions**: one new frozen dataclass (`Alternative`); three new spec fields (`alternatives` re-typed,
  `arch_restriction`, `profiles`); one new index method (`lookup_source_exact`); one new resolver batch field
  (`_batch_inserted_sources`); `upsert_source` return widened.
- **Backward compatibility**: legacy string-form alternatives still deserialize; no stored data is rewritten.
- **Consumers updated**: `resolver.py` (runtime alternative walk, build-dep alt names, source linkage, build-dep
  pass), `verify.py` (`_dep_satisfied`, `_find_build_dep_issues`).
