# Research: Ensure Base Packages

**Source**: Agent research on Debian installer image internals and ISO9660 reading options, plus codebase analysis of the existing `add`/resolver flow.

## 1. ISO9660 Reading Approach

**Problem**: The command must read a raw offline DVD installer image (ISO9660 + Rock Ridge) — list files to verify pool presence and extract `dists/<suite>/<component>/binary-amd64/Packages.gz` indexes on demand. The target machine has no ISO tools installed; the project is system-Python (no pip runtime deps) and offline-first (Constitution VIII: helper packages must be tracked in the dependency graph).

**Decision**: Use the **`python3-pycdlib` Debian package** (1.12.0 in Trixie) — pure Python, system-apt installable (no pip), mature and active upstream, Rock Ridge read (and write) support, no root required, first-class API (`walk`, `open_file_from_iso`, `has_rockridge`). It parses the Rock Ridge tree, which is mandatory: Debian images are built by xorriso `-r` (Rock Ridge) + `-J -joliet-long` (Joliet); plain ISO9660 names would mangle long pool/ paths. Reading ishybrid images (MBR appended) works — the ISO9660 structure remains valid from byte 0.

**Alternatives considered**:
- **Loop mount (`mount -o loop,ro`)** — zero new dependencies and kernel-proven, but requires root + `/dev/loop*` (absent in containers), needs careful `umount` on failure, and breaks the non-root runtime the tool should keep. Rejected as primary; retained as conceptual fallback only.
- **bsdtar (libarchive-tools)** — excellent no-root ISO9660/Rock Ridge reader with list + extract-to-stdout, but adds a system binary dependency and subprocess stdout/stderr parsing (glob member names, leading `./`, exit-status-based error detection) — clunkier and less type-safe than a Python API.
- **isoinfo (genisoimage)** — right capability, but cdrkit is dead upstream (last release ~2011); rejected as a foundation.
- **7z (`7zip` package)** — p7zip removed in Trixie; Rock Ridge long-name support historically incomplete; CVE record argues against parsing untrusted 4 GB images.
- **Hand-rolled stdlib ISO9660 reader** — stdlib has no ISO9660 support; SUSP/Rock Ridge continuation records and `rr_moved` directory relocation make a minimal reader error-prone. Last resort, rejected.
- **xorriso** — modern, but extraction targets disk paths only (no stdout), needing scratch dirs. Rejected.

## 2. Debian Installer Image Internals (verified facts)

- **Layout** (identical for netinst and DVD-1): `dists/<codename>/<component>/binary-amd64/Packages.gz` (gzipped only — no uncompressed `Packages`, no `.xz`), full `pool/<component>/<letter>/<source>/<file>.deb` tree, per-component `Release` files, top-level `Release` with `Codename`/`Components`, and a `dists/stable` symlink to the release.
- **Per-disc indexes**: `Packages.gz` on the image covers only that disc's pool subset, not the full archive.
- **Components** (trixie 13.6.0): `main` (4,199 debs on DVD-1), `contrib` (3), `non-free-firmware` (31); `non-free` absent. Note: the top-level `Release` `Components:` field omits `non-free-firmware` (added by a debian-cd step) — the index directories on disc are the reliable source.
- **Binaries only**: DVD-1 contains **no source packages** — zero `.dsc`, no `dists/*/source/` index (sources ship only on the separate `-source-*` image series). Confirms the spec's binaries-only expectation (US3).
- **Package counts (threshold evidence)**: netinst ~494–520 binary debs; DVD-1 ~4,233–4,270 (trixie/bookworm; DVD-1 is now capped at ~3.7–4.0 GB to fit a 4 GB USB stick). **Safe minimum for the netinst warning: 1,000 binary packages** (comfortably between the measured 494–520 and 4,233–4,270).
- **Base system**: DVD-1 is guaranteed by debian-cd to contain everything the installer needs (`.disk/base_installable` marker written only on success; required+important = 64 packages, +standard = 22 more). The netinst pool (~494) ≈ the offline-installable base+standard set; DVD-1's 4,233 = everything selectable offline from disc 1. Both contain the base set — the feature ingests the full index (spec assumption: all image packages), of which the base is a subset.

## 3. Release / Component Provenance from the Image

**Decision**: Enumerate `dists/<dir>/` entries; skip the `dists/stable` symlink; for each remaining suite directory read its `binary-amd64` (and only `binary-amd64` — the target architecture) index per component subdirectory. Provenance per record = `(release, component, arch)` read from the image (FR-004). If a top-level `Release` file exists, its `Codename` overrides the directory name for the release value (e.g. `dists/stable` → codename `trixie` when present).

**Scope of the image index**: The image's `Packages.gz` files are read **once**, at registration, to seed the initial database records. The image index is a subset of the metadata the database ultimately holds (resolution adds archive records); **all subsequent metadata retrieval (`list`, `show`, `verify`, resolution) reads the database** — the image is never consulted again. Wording in this research and the data-model avoids the ambiguous "the index": it is always the *image's index (the registration seed)* or the *database (the source of truth)*.

**Alternatives considered**: Deriving release from config — rejected (the image's own provenance is authoritative and independent of configured archive releases, per FR-004 and the multi-release handling in spec 005).

## 4. Verified Semantics and Registration Order

**Problem**: FR-008 requires resolution to behave exactly like `add`. The project's rule for `verified` is: registration creates rows `verified = 0`; the flag is only set when a file is actually verified/downloaded at a later stage (no file downloading exists anywhere in the project yet — the resolver registers metadata only, and nothing sets `verified = 1` today). Presence of a file in the installer image is **not** a verified criterion — setting the flag from mere media presence would create a second, weaker meaning of `verified`.

**Decision** — three phases in one command run:
1. **Phase A (local, one transaction)**: read the image, load each `Packages.gz` with the existing `load_binary_index` (temp-file extraction), register every binary via the existing `upsert_binary` (deps serialized with the existing `DependencyGraph`/`serialize_depends`), register `packages` rows via `upsert_package` (requested_version = image version), populate `provides` via the existing `populate_provides_table`. All records are created `verified = 0` — same as `add` (FR-005). Separately, cross-check every index `Filename` against the image's Rock Ridge tree (a single `walk`): mismatches are warnings (damaged-image diagnostic), independent of the flag.
2. **Phase B (behaves like `add`)** — for each unique binary package name from the image (sorted, deterministic): `PackageResolver(config, conn).resolve(name)` — dependencies and build-dependencies resolved as per app settings (network per config, `fetch_source` respected). Errors aggregated best-effort, exactly like `add`.
3. **Phase C (one transaction)**: finalize — print summary, clean temp index files. No verified-flag mutations anywhere in the workflow.

**Why no verified marking**: One rule for the whole cache (Constitution VII consistency). The later-stage download/verification feature (future work) will set the flag when files are actually retrieved — from the archive or, for image-present records, potentially from the image via a future copy-out step; that is outside this spec.

## 5. Version Selection — "Behaves the Same as `add`"

**Problem**: The archive's *current* version for a package may differ from the image's (point-release drift; the archive index only lists current versions — historical versions are not in `Packages`).

**Decision**: **No version pinning.** Each name resolves exactly as `add <name>` would: the resolver selects the archive's current version per app settings. Consequences, all consistent with existing semantics:
- Image version == archive version → the same row is reused; its `verified` flag stays `0` (flag semantics unchanged by reuse or superseding).
- Image version != archive version → archive row becomes `current` (`verified=0`, nothing fetched — same as any `add` today), image row becomes `superseded` (history visible via `list --status superseded`).
- Image release ≠ configured archive release (e.g. a Bookworm DVD with Trixie sources) → rows coexist across releases per spec 005 semantics.

Note: resolution reads the archive index and the database only; the image's `Packages.gz` is never a resolution input (§3).

**Alternatives considered**: Pinning to image versions — rejected: the archive index doesn't list historical versions, so pinned resolution would fail for drifted packages, and sources are fetched by the existing flow at archive versions anyway (the "rebuild at image versions" outcome is unattainable today regardless). Strictly re-resolving from the image index would require new index plumbing (against "reuse as much as possible").

## 6. Batch Resolution

**Decision (superseded)**: Sequential per-name `resolve()` loop over sorted unique names (~4,200 for DVD-1; ~500 for netinst). Each call commits like `add`; interrupted runs leave partial state that converges on re-run (idempotent upserts — US4). Shared dependency trees are re-walked per top-level name, bounded by the DB-first design (idempotent upserts); a cross-name shared `_visited` batch mode is a possible future optimization, not required for correctness.

**Amendment (user directive — batch-first)**: Real-DVD validation measured the per-name design at ~1.5 s/name with `fetch.source = true` — a ~2 h projected batch (T039). The per-name loop is superseded by a first-class `resolve_batch()` in the resolver (Phase 9 of tasks.md):

- **Walk once**: one runtime pass over the union closure of all names with a shared `_visited` set — each package is registered once per batch invocation (~13 k visits instead of ~44 M).
- **Constraint collection**: incoming version constraints are accumulated per package during the walk; a single version satisfying *all* is selected, superseding + re-walking the subtree when a later constraint invalidates an earlier selection. This replaces the rejected "shared `_visited` changes per-package semantics" concern: selection is constraint-aware rather than first-writer-wins.
- **Staged operations**: the runtime pass (registration + source linking, idempotent once per pair — `binary_to_source` now carries an inline `UNIQUE(binary_id, source_id)` constraint in the schema, KISS: no additive DDL, dev DBs are rebuilt from scratch) completes for the whole batch before the build-dep pass runs its wave logic once per batch (not once per name).
- **Per-name commits** stay as safety checkpoints (US4: interrupted runs converge and preserve progress).
- **Single code path**: `resolve()` delegates to `resolve_batch([name])`; `add` multi-name and `ensure-base` Phase B both call the batch entry point; per-name output lines, error attribution (capped at 50 on console), and exit codes are unchanged.
- Batch ≠ parallel: no threads/processes; SQLite remains single-writer. Performance target: real-DVD `source=true` batch ≤ 15 min (measured ~2 h before).

**Alternatives rejected**: Parallel resolution (same DB connection, ordering complexity), loading the image index as a resolver source (new machinery), and keeping the per-name loop with a memoized completeness skip (works, but encodes batch-awareness at the caller instead of making the batch the core shape).

## 7. Netinst / Minimal Image Warning

**Decision**: Count binary entries across all components of the image's index. Below **1,000** → warn (measured: 494–520 netinst vs 4,233–4,270 DVD-1). Warning is printed and the run continues (FR-011). Constant documented in code (`ISO_MIN_FULL_DVD_BINARIES = 1000`).

**Alternatives considered**: Rejecting the image — a false reject would block legitimate images; warn-and-continue matches spec FR-011.

## 8. Config Surface

**Decision**: New `[paths] iso_dir` setting (default `{base_dir}/iso`) — the directory inside the cache holding the offline DVD installer image file (FR-002). Plumbed through `ProjectConfig`, `_KEY_MAP`, `expand_path`; `init` creates the (empty) directory; `config` text and JSON output gain the field (contract updated).
