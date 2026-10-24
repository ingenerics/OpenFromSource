# Data Model: Ensure Base Packages

## Entity List

No schema changes — `SCHEMA_VERSION` stays `1`. All records use the existing tables.

### ISOCache (config entity, new)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `iso_dir` | `str` | `{base_dir}/iso` | Directory inside the cache holding the offline DVD installer image file (e.g. `debian-13.0.0-amd64-DVD-1.iso`). Created empty by `init`. |

Config plumbing: `project.defaults.toml` `[paths] iso_dir`, `ProjectConfig.iso_dir`, `_KEY_MAP (("paths", "iso_dir"))`, `expand_path` expansion, `config` command output (text + JSON).

### IsoImage (service entity, new)

Read-only view of a raw ISO9660 + Rock Ridge image backed by `python3-pycdlib`:

| Field / method | Type | Description |
|----------------|------|-------------|
| `path` | `str` | Image file path |
| `release` | `str` | Suite codename read from `dists/` + top-level `Release` `Codename` |
| `components` | `tuple[str, ...]` | Component dirs with a `binary-amd64` index (e.g. `main`, `contrib`, `non-free-firmware`) |
| `binary_package_count()` | `int` | Total binary entries across components (netinst-warning input) |
| `file_exists(path)` | `bool` | Rock Ridge tree membership check (index `Filename` vs image pool) |
| `read_file(path)` | `bytes` | Read an index file from the image (e.g. `Packages.gz` — temp-file extraction for `load_binary_index` reuse) |
| `walk()` | iterator | Single pass over all member paths (index↔image mismatch cross-check) |
| `close()` | `None` | Release resources; temp index files cleaned by the workflow |

Raises `IsoError` (new exception, extends `PackageMgmtError`) for unreadable/non-ISO files, missing `dists/`, or absent `binary-amd64` indexes.

### Binaries (existing table — verified-flag semantics)

Records registered from the image carry the image's own provenance (`release`, `component`, `architecture = amd64`). The `verified` flag follows the **`add` rule exactly**: registration creates `verified = 0`; the flag is only set when a file is actually verified/downloaded at a later stage (future download work — nothing sets it today). Presence of a file in the image never sets the flag (FR-005).

Lifecycle through the three phases:

1. **Phase A**: binary/packages/provides rows upserted with `verified = 0` (same upserts `add` uses). Index↔image cross-check produces warnings only (damaged-image diagnostic) — no flag mutations.
2. **Phase B** (`add`-like resolution): identical (package, version, architecture, release, component) rows are reused; different versions supersede per existing semantics; `upsert_binary` conflict-resets `verified = 0` where dependencies JSON changes — all unchanged from `add`.
3. **Phase C**: finalize — summary, temp-file cleanup. No flag mutations.

### Provides (existing table)

Populated from image binaries via the existing `populate_provides_table` (insert-only, idempotent — natural-key PK prevents duplicates on re-run). Archive provides coverage remains the `init` command's job (unchanged).

### packages / sources / binary_to_source / source_build_deps (existing tables)

- `packages`: one row per image binary name via `upsert_package` (requested_version = image version), `verified = 0` (same as `add`).
- `sources` / `binary_to_source` / `source_build_deps`: produced exclusively by the existing resolver in Phase B (binaries-only image expected — no source index on installer DVDs, per research §2).

### Database as Metadata Source of Truth

The image's `Packages.gz` files are the **registration seed only**: read once in Phase A, then never again. All metadata retrieval (`list`, `show`, `verify`, resolution) reads the database — which after resolution holds the image subset plus the archive-resolved records. The image index and the database index are never confused; the former is strictly smaller than the latter.

## State Transitions

### Binaries.status

Unchanged (`current` → `superseded`). An image row superseded by a drifted archive version remains visible as history; verification judges only `current` rows (spec 007 semantics).

### Verified flags (unchanged semantics — same as `add`)

```
registered (Phase A/B) ── verified = 0
verified = 1  only when a file is actually verified/downloaded at a later stage (future work; never by this command)
```

Re-runs are idempotent: upserts and `INSERT OR IGNORE` provides converge to identical state (US4).
