# Data Model: Local Patch Lane

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

**Schema freeze (spec 010/011).** No database schema changes, no migrations, no new
columns or tables. The feature uses the existing tables with `component = 'local'` as a
new *value* of the existing `component` column, and the existing `binary_to_source`
link table for imported-source provenance.

---

## 1. Imported binary (`binaries`, new rows with `component = 'local'`)

A locally built/patched package registered by `import-deb`.

| Field | Value | Source |
|-------|-------|--------|
| `package_name` | from `.deb` control `Package` | parsed `DebFile` |
| `version` | from control `Version` (Debian notation, validated) | parsed `DebFile` |
| `architecture` | from control `Architecture` | parsed `DebFile` |
| `release` | the default (primary) configured release | config |
| `component` | `'local'` (fixed) | constant |
| `dependencies` | parsed `Depends`/`Pre-Depends` (JSON via existing serialize) | parsed `DebFile` |
| `status` | `'current'` | constant |
| `verified` | `0` at registration, `1` after payload copy + SHA256 | import lifecycle |
| `circular` / `sync_mask` | `0` | constant |

**Uniqueness**: the existing `UNIQUE(package_name, version, architecture, release,
component)` permits a `local` row alongside the same-named upstream row — this is the
mechanism by which the patched package shadows the stock one at equal version
(research §2).

**Validation rules (FR-002/003/004/008/009)**:
- Every declared dependency MUST be satisfiable from the cache before registration; an
  unsatisfiable requirement aborts with zero changes.
- The payload MUST be copied to the pool and its SHA256 computed before `verified = 1`.
- Re-import of an identical `(name, version, arch)` is idempotent — `ON CONFLICT` update,
  no duplicate rows.

---

## 2. Imported source (`sources`, new rows with `component = 'local'`)

The optional rebuilt source registered by `import-deb --source <dir>`.

| Field | Value | Source |
|-------|-------|--------|
| `package_name` | source package name (from `dsc`/control) | parsed source artifact |
| `source_version` | source version | parsed source artifact |
| `release` | default release | config |
| `component` | `'local'` | constant |
| `verified` | `1` (artifact present at import) | constant |
| `build_depends` | parsed `Build-Depends` (may be empty) | parsed source artifact |

The `--source` argument is a **directory** containing the complete source set — the
`.dsc` plus every file it references (upstream tarball and Debian packaging tarball,
exactly what `dpkg-source -x <dsc>` needs present together). Every referenced file is
verified against the `.dsc`'s declared checksums before registration; a path that
cannot supply the complete, checksum-valid set is rejected (FR-005, spec Assumption
"`--source` form"). The files are stored in the source pool mirroring the binary pool
convention (`source/pool/local/…`).

---

## 3. Binary↔Source provenance (`binary_to_source`, new link)

When `--source` is given, a `binary_to_source` row links the imported binary to its
imported source (FR-005) — the same link table used for archive packages, so
deterministic-rebuild provenance is uniform. Without `--source` no link is created and
`verify` must not fail on the missing source (spec US3 AS2).

---

## 4. Component priority (`_COMPONENT_PRIORITY` in `repository.py`)

Not a DB entity — a resolver constant. `local` is added with the highest priority
(value `-1`, ahead of `main` at `0`). The ordering remains version-first with component
as tie-breaker (research §2), so at equal version a `local` package is preferred over
any upstream component's same-named package.

---

## 5. Resolver index injection (`RepositoryIndex`)

No new entity. `ensure_indexes()` appends current+verified `local` binaries and sources
(from DB) to the merged index, as `BinaryPackage`/`SourcePackage` instances carrying
`release=default`, `component='local'`, parsed dependencies and provides (research §3).

---

## 6. Pool payload (filesystem)

| Artifact | Path |
|----------|------|
| Imported binary | `{base}/binary/pool/local/<prefix>/<pkg>/<pkg>_<version>_<arch>.deb` |
| Imported source | `{base}/source/pool/local/<prefix>/<pkg>/…` (`.dsc` + tarballs) |

Identical layout to archive components (FR-004). `safe_pool_path` validates the paths.

---

## 7. Handoff/diff serving (filesystem + index)

No new entity. The `local` component is added to the default release's served component
list; its `Packages.gz` is built from the DB records via the existing `_paragraph`
builder (research §4), and it appears in the `deb` line (`bootstrap.sh`) and the
`Release` `Components:` line. Local records with a missing pool file are excluded with a
warning, like every other component.

---

## State transitions

```
[file provided] -> [validated .deb] -> [deps satisfiable] -> [binary registered,
verified=0] -> [payload copied + SHA256] -> [verified=1] -> [source linked (optional)]
        |                     |                    |
        | invalid .deb        | dep missing        | copy failure
        v                     v                    v
    error, zero changes   error, zero changes   rollback, zero changes
```

Any failure before the final commit leaves the database and pool untouched (FR-008).
