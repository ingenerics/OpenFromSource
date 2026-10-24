# Data Model: Source-Stack Packaging

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

**Schema freeze (spec 010/011).** No database schema changes, no migrations, no new
tables or columns. The feature imports through the existing spec 021 path, so the only
database rows created are `binaries` / `sources` / `binary_to_source` rows with
`component = 'local'` (a value that already exists from spec 021). The new data this
feature introduces lives **outside the DB**: the declarative recipe (a tracked file), the
generated `.deb`/`.dsc` artifacts (scratch files that become pool payloads), and the
handoff `source/Sources` index for `local` (a **file artifact generated from the DB
`sources` rows** — Q3/FR-008; schema-freeze holds). This mirrors spec 023 (file-based
manifest, DB read-only) and spec 021 (local component rows + file payloads).

---

## 1. Packaging recipe (`recipes/<stack>.toml`, tracked file)

The single declarative input of `package-stack` (FR-001/FR-011, "Key Entities"). See
[contracts/recipe.md](contracts/recipe.md) for the exact file contract; the entity here
is the in-memory model `StackRecipe`.

### 1.1 `StackRecipe`

| Field | Type | Source | Notes |
|-------|------|--------|-------|
| `stack` | `str` | TOML `[stack]` | e.g. `"hyprland"` |
| `repo_dir` | `str` | TOML | default `repos/`; where the pinned source trees live |
| `source_epoch` | `str` (ISO date) | TOML | `SOURCE_DATE_EPOCH` for deterministic artifacts (default: recipe date) |
| `components` | `tuple[ComponentRecipe, ...]` | TOML `[[component]]` | every source-built component of the set |

### 1.2 `ComponentRecipe`

| Field | Type | Source | Notes |
|-------|------|--------|-------|
| `name` | `str` | TOML | component name (also the recipe key / staged-dir name) |
| `binary_package` | `str` | TOML | binary package name |
| `source_tree` | `str` | TOML | path under `repo_dir` (for `sdbus-c++`: the vendored `…/subprojects/sdbus-cpp`) |
| `version` | `str` | TOML | upstream version (Debian notation) |
| `revision` | `int` | TOML (default 1) | Debian revision (`<version>-<revision>`); bumped on re-package (FR-010) |
| `build_system` | `str` | TOML | `"meson"` or `"cmake"` (drives the generated `debian/rules`) |
| `architecture` | `str` | TOML (default `amd64`) | `all` for data-only components (e.g. `wayland-protocols`) |
| `description` | `str` | TOML | control `Description` |
| `maintainer` | `str` | TOML (default empty → project `admin_email`) | control `Maintainer` (`OpenFromSource <admin_email>`; default `noreply@example.com`) |
| `section` | `str` | TOML (default `misc`) | control `Section` |
| `homepage` | `str` | TOML | control `Homepage` |
| `explicit_depends` | `list[str]` | TOML | raw Debian relation strings merged into the derived `Depends` (FR-003; companion libs not registered as packages on the host) |
| `build_depends` | `list[str]` | TOML | merged into the generated source control's `Build-Depends` |
| `source_format` | `str` | TOML (default `"3.0 (quilt)"`) | source package format for `dpkg-source` |

**Validation rules (FR-001/FR-003/FR-014)**:
- Component `name` is unique within the recipe; `binary_package` is unique within the
  set.
- `source_tree` must exist under `repo_dir` (missing → loud error naming the component).
- `version` must be a valid Debian version (the same check `import_local_deb` applies).
- An explicit/derived dependency on another component name **must** be present in the
  recipe, otherwise the set is unsatisfiable by construction and `package-stack` fails
  naming the gap (spec edge case "Missing source-built dependency in the recipe").
- A dependency cycle between components is a loud error (spec §5).

---

## 2. Generated binary package (`.deb`, scratch artifact)

The deterministic Debian binary produced from a staged install directory (FR-002/FR-003).
Not a persistent entity — it is built in a scratch work dir and then imported, at which
point the pool copy and DB rows below are the durable state.

| Control field | Value | Source |
|---------------|-------|--------|
| `Package` | `binary_package` | recipe |
| `Version` | `<version>-<revision>` | recipe (FR-010 revision bump) |
| `Architecture` | `architecture` | recipe |
| `Section` / `Priority` | `section` / `optional` | recipe / constant |
| `Maintainer` / `Homepage` / `Description` | recipe | recipe |
| `Installed-Size` | computed from staged tree | debgen |
| `Depends` | derived (`dpkg-shlibdeps`) merged with `explicit_depends` | debgen (§3) |

**Determinism** (FR-009): fixed `SOURCE_DATE_EPOCH`, fixed tar member mtimes/order,
`dpkg-deb --root-owner-group` — identical inputs → identical bytes (asserted in tests).

---

## 3. Generated source package (`.dsc` + files, scratch artifact directory)

The complete, rebuildable Debian source produced from the pinned tree (FR-004). A
directory holding exactly one `.dsc` plus every file it references (orig + Debian
tarballs) — the exact `--source <dir>` shape `import_local_deb` validates and imports.

| `.dsc` field | Value | Source |
|--------------|-------|--------|
| `Source` | `binary_package` (or recipe-declared source name) | recipe |
| `Version` | `<version>-<revision>` | recipe |
| `Format` | `source_format` (default `3.0 (quilt)`) | recipe |
| `Build-Depends` | from `build_depends` (may be empty) | recipe |
| `Files:` / `Checksums-Sha256:` | md5/sha256/size per referenced file | `dpkg-source` |

**Rebuildability**: `dpkg-source -x` of the generated set succeeds offline; the recorded
rebuild (`debian/rules` wrapper driving the component's own meson/cmake) reproduces the
shipped binary (FR-004/FR-008, US4) — validated in quickstart, not assumed.

---

## 4. Imported rows (existing schema, `component = 'local'`)

Identical to spec 021's data model — no new fields:

| Entity | Rows created | Verified lifecycle |
|--------|--------------|--------------------|
| `binaries` | one row per component: `(binary_package, <version>-<revision>, architecture, release, 'local')` | `0` at registration → `1` after payload copy + SHA256 (`import_local_deb`) |
| `sources` | one row per component: `(source_name, <version>-<revision>, release, 'local')` | `1` (artifact present at import) |
| `binary_to_source` | one link per component | `UNIQUE(binary_id, source_id)`, `INSERT OR IGNORE` |
| `packages` | `upsert_package(name, status='current')` | n/a |

**Uniqueness/atomicity** (from `import_local_deb`): `UNIQUE(package_name, version,
architecture, release, component)` and `UNIQUE(package_name, source_version, release,
component)` make re-import of an identical pair idempotent; any failure rolls back all
DB rows and removes freshly copied pool files — zero partial records (FR-014).

---

## 5. Command result (`PackResult`, JSON output boundary)

The `package-stack` report, emitted text or JSON (TypedDict at the output boundary only,
per Constitution VI):

| Field | Type | Meaning |
|-------|------|---------|
| `stack` | `str` | recipe name |
| `components_total` | `int` | recipe size |
| `components_imported` | `int` | count successfully imported |
| `order` | `list[str]` | resolved import order |
| `components` | `list[ComponentResult]` | per-component outcome |

### `ComponentResult`

| Field | Type | Meaning |
|-------|------|---------|
| `name` | `str` | component |
| `binary_package` / `version` / `architecture` | `str` | imported package identity |
| `derived_depends` | `str` | the derived `Depends` string |
| `explicit_depends` | `list[str]` | recipe deps merged in |
| `unattributed` | `list[str]` | libraries `dpkg-shlibdeps` could not attribute (must be declared) |
| `source_files` | `int` | source files imported + checksum-verified |
| `linked` | `bool` | `binary_to_source` recorded |
| `verified` | `bool` | DB `verified=1` for binary and source |

---

## 6. State transitions

Per component, the import lifecycle is exactly `import_local_deb`'s (database-first,
spec 021):

```text
recipe entry ──► debgen ──► .deb (scratch) ─┐
              └► srcgen ──► source dir ──────┼──► import_local_deb ──► binaries/sources
                                              │      (atomic, verified=1)      + binary_to_source
                                              └► _verify_dependencies (cache re-resolution)
```

- **Ordering**: components are topologically sorted (dependencies first) before any
  import; a cycle/gap aborts before any import (zero changes).
- **Failure**: any component's debgen/srcgen/import failure aborts the run atomically —
  no partial records or pool files remain (FR-014); the error names the component and
  the problem.

---

## 7. Handoff `source/Sources` index for `local` (Q3/FR-008, file artifact)

Generated by `srcindex` from the DB `sources` rows — **not a DB table**. It makes the
imported sources retrievable by the operator with standard `apt-get source`, offline,
over the same signed `Release` channel as the binary index (spec FR-008/US4/SC-004; the
handoff today emits only `binary-amd64/Packages[.gz]` — no deb-src anywhere, per
`handoff.py`).

| Aspect | Value | Source |
|--------|-------|--------|
| File | `dists/<release>/<component>/source/Sources` + `Sources.gz` (for `component='local'`) | `srcindex` |
| Input rows | `sources` where `verified=1` and `component='local'` | DB (existing schema) |
| Paragraph fields | `Package` (`source_name`), `Version`, `Directory` (relative `source/pool/...`), `Files:`/`Checksums-Sha256:`/`Size` taken from the imported `.dsc` | DB + `.dsc` |
| Auth/consistency | hash-listed in the handoff `Release` + signed `Release.gpg` alongside `Packages` | `handoff.py` |
| Operator config | handoff bootstrap writes a `deb-src` line for `local` to `/etc/apt/sources.list.d/openfromsource-cache.list` | `_write_bootstrap_script` |

**Determinism** (FR-009 spirit): index paragraphs are serialized in stable
(version, name) order with fixed formatting so two handoffs over identical DB state
produce byte-identical `Sources`/`Sources.gz`.

**Atomicity**: the index is written only from `verified` rows, alongside the same
`Release`/`Release.gpg` regeneration the handoff already performs; a failed generation
leaves the previous signed index in place (no partial channel state).
