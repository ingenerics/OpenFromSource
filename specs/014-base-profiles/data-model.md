# Data Model: Base Profiles

**Feature**: [spec.md](spec.md) | **Phase 1** — entities, validation rules, and state transitions.

**No database schema changes** (spec 010/011 freeze). Everything new in this feature is *configuration* (`ProjectConfig.max_source_dep_depth`, the three profile TOML files) and *derived in memory* (the per-binary source level used to bound source/build-tool support). The existing `binaries`, `sources`, `binary_to_source`, and `source_build_deps` tables are unchanged; a finite depth simply means fewer source records and build-tool links are written. The runtime binary closure is **always** complete (FR-003).

## Entities

### `ProjectConfig.max_source_dep_depth` (configuration, new field)

The source-dependency depth cap, read from the `[resolution]` section of the project-setting TOML.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `max_source_dep_depth` | `int` | `0` | Depth cap for source/build-tool support from the requested packages. `0` = none (the default — matches the shipped regular_user presets); `-1` = unlimited (explicit opt-in, equals pre-feature behavior); positive `N` = sources (and their build tools) are provided for binaries within `N` levels of the requested set (level 0 = requested, level 1 = direct runtime dependencies, …). |

**Validation rules (FR-002/FR-005)**: must be an integer `>= -1`. A value below `-1`, a non-integer (`1.5`), or a boolean in the TOML raises `ConfigError` at config-load time, before any resolution runs; the error names the key and the invalid value. Absent → default `0`. The same validation applies to `max_build_dep_report_depth` (FR-017). `ProjectConfig.fetch_source`'s dataclass default also flips to `False` to match the shipped presets (so an unconfigured config is regular_user, and the depth-0 warning cannot fire on defaults). The value is immutable once loaded (frozen dataclass).

**Source of truth**: `project.defaults.toml` (default) with `project.local.toml` overlay via the existing `_KEY_MAP` merge. The `overridden` set tracks whether `project.local.toml` overrides it, feeding the `config` command's `(default)/(override)` annotation.

### Profile files (documentation artifacts, new)

Three complete, standalone project-setting TOML files under `docs/profiles/`, each a drop-in `project.defaults.toml`.

| File | `[fetch] source` | `max_source_dep_depth` | Intended cache |
|------|------------------|------------------------|----------------|
| `docs/profiles/regular_user.toml` | `false` | `0` | Requested binaries + full runtime closure; no sources, no build tools (current `source = false`) |
| `docs/profiles/developer.toml` | `true` | `1` (illustrative) | Full runtime closure + sources and build tools for the requested set and its direct dependencies |
| `docs/profiles/graybeard.toml` | `true` | `-1` | Full recursion — every binary has its source + build tools; the `ensure-base` profile |

**Validation rules (FR-006)**: each file is a *complete* project setting (loads standalone as `project.defaults.toml`); each contains the distinguishing settings above plus paths/database/logging/archive entries; each is plain TOML with inline comments only — no code, no profile name keyed on by the tool. The tool never reads the `profiles/` directory at runtime; the files exist purely as user-facing presets.

### Source scope (derived, in memory per batch, bounded by the cap)

Which binaries get source records and build-tool links.

| Concept | Meaning |
|---------|---------|
| Source level | Runtime-graph distance from the requested set. Level 0 = the requested packages; level `k` = a runtime dependency `k` hops away. |
| In scope | Level ≤ `max_source_dep_depth` (or all, when `-1`). In-scope binaries get a source record (`sources` + `binary_to_source`) and, via the build-tool pass, their `source_build_deps` links. |
| Out of scope | Level > `N` when `N` is finite — the binary is still resolved (full runtime closure, installable) but has **no source record** and no build-tool links. |
| Boundary build tool | A build tool resolved for an in-scope source is placed one level deeper than that source; at a finite depth it is out of scope as a *source*, so it is resolved as a binary (with its full runtime closure) but gets no source record of its own. |

**Validation/consistency rules (FR-011/FR-013/FR-015)**: the source/build-tool set under a finite depth is a strict subset of the unlimited set; the runtime closure is identical across depths. Every in-scope source has its build tools resolved (each fetched source is buildable). Out-of-scope binaries intentionally have no `binary_to_source` link, so at a finite depth `verify --graph` must not treat them as defects — they are informational only (FR-015, no schema change: the gate reads the configured depth; `-1` keeps the strict unlinked check).

**State transitions**: unchanged record-level semantics — the resolver still upserts `binaries`/`sources`/`source_build_deps` idempotently; `_visited` still walks the closure once; per-name commits still checkpoint progress. The cap only changes *which* binaries get source records. Re-running with a deeper (or unlimited) cap converges by creating the additional source records; re-running with a shallower cap leaves the already-created source records in place (they are not deleted — FR-014, consistent with the existing never-delete resolution behavior).

### Resolution graph (existing, never bounded)

The runtime dependency traversal (binary → binary). **Always complete** — the depth setting never applies to runtime binaries (FR-003). This is the guarantee behind installability and version alignment (Constitution II).

## Data Flow

```
[resolution] max_source_dep_depth (defaults/local merge, validated)
        │
        ▼
PackageResolver.resolve_batch(requests)
  ├─ runtime pass: _resolve_one/_resolve_one_inner   # full runtime closure, every binary registered
  │     ├─ level accounting: requested = 0, runtime dep = parent + 1
  │     └─ source record created ONLY when level ≤ max_source_dep_depth (or -1 = unlimited)
  └─ build pass: _resolve_build_deps()               # processes only the created (in-scope) sources
        └─ each in-scope source's build tools resolved with complete runtime closures (FR-013);
           build-tool binaries get no source records of their own at a finite depth

download  # unchanged — follows the DB; fewer source records ⇒ fewer source downloads
verify    # + depth gate (FR-015, no schema change): finite depth → unlinked binaries informational-only; -1 keeps strict check
config    # unchanged shape + shows max_source_dep_depth (text + JSON)
```

## Contract Notes

- `package_mgmt config` (text): adds `max_source_dep_depth` line alongside `max_build_dep_report_depth` with the `(default)/(override)` annotation.
- `package_mgmt config` (JSON): adds `resolution.max_source_dep_depth` and `resolution.max_source_dep_depth_override` beside the existing report-depth entries.
- `verify --graph` (FR-015): no schema change — the gate reads the configured `max_source_dep_depth`. At a finite depth, binaries without a `binary_to_source` link are informational only (never defects) and a correctly-scoped cache reports COMPLETE (runtime closure complete + every fetched source buildable, FR-013); at `-1`, the strict unlinked-binaries check applies unchanged.
- Invalid depth → `ConfigError` → `{"error": "...", "exit_code": 102}` via the shared `_print_error` convention.
- No new commands, no new CLI flags, no new operator artifacts.
