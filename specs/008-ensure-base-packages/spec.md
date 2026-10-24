# Feature Specification: Ensure Base Packages

**Feature Branch**: `008-ensure-base-packages`
**Status**: Draft

**Input**: User description: "Spec ensure base packages - To adhere to the foundation principles, we also need to be able to rebuild the base Debian packages. Those that were installed through the offline DVD installer ISO. This spec should allow the user to add the metadata for the packages for a given ISO to the cache through a single command. It's important not to reinvent the wheel. We have most of the functionality in place, which should be reused as much as possible. This spec is mostly about extracting the info from the ISO and converting it to the desired form. The ISO itself will be located at a directory in the cache. This way it will always be part of the handoff."

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Places the offline DVD installer image in the cache and ingests its package metadata with a single command |
| **Operator** | Offline system (no network) | Rebuilds the base Debian packages from the handoff (installer image + cache) |

**Phase 1 — Image Ingestion**: The single `ensure-base` command reads the offline DVD installer image file directly from the cache, extracts binary and provides metadata from its index files (the registration seed), registers it in the cache database with the same `verified` semantics as `add`, and cross-checks the index against the image contents (mismatches reported as warnings). Run by the Administrator.

**Phase 2 — Linking & Hardening**: Confirmation that the command behaves the same as `add` (dependencies, build-dependencies, and sources resolved as per app settings — no source DVD required), idempotent re-runs with safe failure, netinst/minimal image warning, and confirmation that existing commands (`list`, `show`, `verify`, `add`) operate on image records unchanged. Run by the Administrator.

---

### User Story 1 - One-Command Ingestion of the Offline DVD Installer Image (Priority: P1)

The administrator places the offline DVD installer image file (e.g. `debian-13.0.0-amd64-DVD-1.iso` — the first installer DVD of the release set, containing the base package set) into a directory inside the cache and runs a single command. The command reads the image file directly — no manual extraction or mounting step — and registers every binary package listed in the image's index in the cache database, reusing the existing index-parsing and database-population machinery rather than building anything new. The command prints a summary of what was ingested.

The supported artifact is the **offline DVD installer image**, not a netinst or minimal image: installation from those requires network access and they cannot support the offline-first principle. The command warns if the given image matches netinst/minimal characteristics.

**Why this priority**: This is the essence of the feature — one action turns the offline installer image into cache metadata, enabling later rebuild of the base packages.

**Independent Test**: Place an offline DVD installer image file under the cache's ISO directory and run `ensure-base`. The database then contains binary records for every package listed in the image's index, with no duplicates and no manual extraction step; dependency and build-dependency resolution for those packages follows the same behavior as `add` (as per app settings, including archive access on the networked system).

**Acceptance Scenarios**:

1. **Given** a cache containing an offline DVD installer image file with a complete Debian release tree (`dists/` + `pool/`), **When** the administrator runs `ensure-base`, **Then** every binary package listed in the image's index is registered in the database and a summary of counts is printed.
2. **Given** a cache with no installer image, with an image file that cannot be read, or with more than one image file in the ISO directory, **When** the administrator runs `ensure-base`, **Then** a clear error (naming the files when multiple are present) is shown and the command exits non-zero without touching the database.
3. **Given** an image whose index spans multiple components, **When** the administrator runs `ensure-base`, **Then** every record preserves its component provenance as declared in the image.
4. **Given** a netinst or minimal image (package set far smaller than the full DVD set), **When** the administrator runs `ensure-base`, **Then** the metadata present is still processed, but a warning states that such images cannot support offline installation.

---

### User Story 2 - Registration Integrity and Same-as-`add` Verified Flags (Priority: P1)

`ensure-base` registers image records with the same `verified` semantics as `add`: records are created **unverified** (`verified` flag unset), and the flag is only set when a file is actually verified/downloaded at a later stage — never at registration. This keeps one rule for the whole cache: registration alone never asserts verified. Separately, because the installer image is part of the handoff, the command cross-checks every file referenced by the image's index against the image's actual contents and reports mismatches as warnings — a damaged or corrupt image is thereby detected at ingestion time without affecting `verified` flags.

**Why this priority**: A single `verified` rule across `add` and `ensure-base` preserves the trust semantics of the database-first model; conflating "present on the installer media" with "verified" would create a second, weaker meaning of the flag.

**Independent Test**: Run `ensure-base` on an image; every registered record has the flag unset (`verified = 0`), exactly as after `add`. Run it on an image whose index references a file the image does not contain; the mismatch is reported as a warning and the flag semantics are unchanged.

**Acceptance Scenarios**:

1. **Given** any image, **When** `ensure-base` runs, **Then** every registered record is created with the `verified` flag unset — identical semantics to `add` — and the flag is never set by this command.
2. **Given** an image whose index references a file not present in the image, **When** `ensure-base` runs, **Then** the mismatch is reported as a warning naming the path, while registration and resolution continue normally.
3. **Given** a damaged or unreadable image, **When** `ensure-base` runs, **Then** a clear error is shown and the database is not modified.

---

### User Story 3 - Behaves the Same as `add` for the Base Set (Priority: P2)

The installer DVD carries binary packages only; their sources live on a separate source DVD that is **not** supplied and is not part of the handoff. From the user's point of view, `ensure-base` behaves the same as `add`: for each base binary package registered from the image, the command runs the existing resolution flow — dependencies and build-dependencies are resolved as per app settings (against the configured archives, which on the administrator's networked system may require network access), sources are obtained, and `binary_to_source` links are created. The image supplies the starting metadata (the base set); everything downstream reuses the existing flow unchanged. No source DVD is required, under the assumption that sources obtained this way match the sources the source DVD would carry. Behind the scenes the implementation may do things differently (e.g. batch the ISO extraction), but the user-visible behavior and results are those of `add` — with batch progress and capped error reporting per FR-013/014.

**Why this priority**: Constitution principles II (dependency alignment) and III (binary–source consistency) make full resolution and rebuildability core requirements; the existing flow already delivers them, so this story proves the image feeds into that flow with zero new mechanics.

**Independent Test**: Run `ensure-base` on a binaries-only installer image — it succeeds without a source index, and every base binary's dependencies, build-dependencies, and source are resolved with the same results and reporting as running `add` on the same package names.

**Acceptance Scenarios**:

1. **Given** a binaries-only installer image (the expected artifact), **When** `ensure-base` runs, **Then** the image's binaries and provides are ingested; the absence of a source index **on the image** is the expected state for an installer DVD and is not an error — sources are resolved from the configured archives through the existing flow, exactly as `add` does, and each base package's dependencies and build-dependencies are resolved following the same behavior as `add` (as per app settings).
2. **Given** a base binary registered from the image, **When** its resolution runs, **Then** its source is obtained and a `binary_to_source` link is created through the existing flow — with no changes to that flow.
3. **Given** a resolution failure (e.g. a dependency not satisfiable from the configured sources), **When** `ensure-base` runs, **Then** the same error reporting and best-effort continuation semantics apply as with `add`.
4. **Given** flow-resolved source versions, **When** they are compared with the versions the source DVD would carry, **Then** they are assumed consistent (documented assumption); any divergence surfaces through the existing verification, not through new ISO machinery.

---

### User Story 4 - Idempotent Re-runs and Safe Failure (Priority: P2)

The administrator runs `ensure-base` more than once (e.g. after re-adding the image or after resolving additional packages with `add`). Re-running must never duplicate records, and a failed or interrupted run must not corrupt existing data.

**Why this priority**: The cache is the handoff artifact; repeated or interrupted runs must be safe, or the database's integrity guarantee weakens.

**Independent Test**: Run `ensure-base` twice on the same cache — the database is identical after both runs. Interrupt a run part-way; the next run completes cleanly with no duplicates.

**Acceptance Scenarios**:

1. **Given** a cache whose image metadata was already ingested, **When** the administrator runs `ensure-base` again, **Then** the database state is unchanged (no duplicate records, no orphaned links).
2. **Given** a run interrupted part-way, **When** `ensure-base` is re-run, **Then** it completes without errors or duplicates.
3. **Given** an image file removed between runs, **When** `ensure-base` runs, **Then** a clear error is shown and the database is not modified.

---

### User Story 5 - Image Records Work with Existing Tooling (Priority: P2)

The administrator inspects and verifies image-derived records exactly like any other cache record. `list`, `show`, `verify --graph`, and dependency resolution by `add` treat image packages like previously fetched ones — because they live in the same tables in the same shape.

**Why this priority**: "Do not reinvent the wheel" — ingestion's value is that all downstream behavior already exists; this story proves it by reusing rather than rebuilding.

**Independent Test**: After `ensure-base`, run `list` and `show` on an image package, run `verify --graph` over the image records, and `add` a package that depends on an image package — all behave exactly as for add-fetched records.

**Acceptance Scenarios**:

1. **Given** image records in the database, **When** the administrator runs `list` or `show`, **Then** image packages appear with the same fields and formats as packages added via `add`.
2. **Given** image records in the database, **When** `verify --graph` runs, **Then** image records are counted and checked with the same rules as any other record.
3. **Given** an `add` request whose dependency resolves to an image record, **When** `add` runs, **Then** the image record satisfies the dependency without refetching or duplication.

---

### Edge Cases

- What if the file in the ISO directory is not a readable image (not ISO9660, truncated, corrupt media)? → Clear error; database untouched.
- What if `iso_dir` contains more than one image file? → Clear error naming all files found; no selection is made; the administrator keeps exactly one image and re-runs.
- What if the image contains no `dists/` or no `pool/`? → Clear error; database untouched.
- What if the image is a netinst or minimal installer (package set far smaller than the full DVD set)? → Metadata is processed but a warning states such images cannot support offline installation (netinst/minimal images are not the supported artifact).
- What if an indexed file is missing from the image's pool? → Mismatch warning naming the path; the record is registered normally with the flag unset (same as `add`); run succeeds.
- What if the image ships binaries but no sources (the expected state)? → Normal; binaries and provides ingested; sources come through the existing resolution flow (US3).
- What if a source is on the image but its binary counterpart is not? → Source registered; no link; missing binaries simply absent.
- What if the image's release codename differs from the configured archive releases? → Records keep their image provenance; the existing multi-release handling applies unchanged.
- What if an image index file is corrupt? → Loud failure with a clear message; no partial writes.
- What if the cache is handed off and the operator re-runs `ensure-base`? → The command is admin-only, same as `add`; on the offline system, resolution would fail exactly as it would for `add`. The image extraction and registration part is idempotent (US4).
- What if the operator verifies a handoff whose image binaries' sources were never resolved via the existing flow? → Existing `verify` semantics report unlinked binaries unchanged; the administrator resolves the base set's sources before handoff using the existing flow.
- What if the image contains a package also fetched earlier via `add`? → Existing uniqueness and superseding semantics apply; no duplicates (US4).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A single command, named `ensure-base`, MUST register the metadata of every binary package listed in the offline DVD installer image's index into the existing cache database, reusing the established index-parsing and database-population components.
- **FR-002**: The artifact MUST be the offline DVD installer image **file** (e.g. `debian-<release>-<arch>-DVD-1.iso`) located in a configurable directory inside the cache, defaulting to `<cache root>/iso`.
- **FR-003**: The command MUST read the image file directly — no manual extraction or mounting step by the user.
- **FR-004**: Every registered record MUST carry the release, component, and architecture provenance read from the image itself.
- **FR-005**: Every record registered by the command MUST carry the `verified` flag unset, with the same semantics as the `add` command — the flag is only set when a file is actually verified/downloaded at a later stage, and this command MUST NOT set it. The command MUST additionally cross-check each file referenced by the image's index against the image's actual contents and report mismatches as warnings (damaged-image detection), independent of `verified` flags.
- **FR-006**: The image extraction, registration, and cross-check part of the command MUST operate on local cache contents without network access.
- **FR-007**: The image MUST be expected to be binaries-only: the command MUST succeed without any source index on the image; sources are resolved from the configured archives per FR-008.
- **FR-008**: No new resolution or source-acquisition machinery may be introduced by this feature — dependencies, build-dependencies, and sources for image binaries MUST be handled by the existing flow used by `add`; from the user's point of view the command MUST behave the same as `add`, including archive access per app settings.
- **FR-009**: Re-running `ensure-base` MUST produce an identical database state — no duplicate records, no orphaned links.
- **FR-010**: A missing, unreadable, or ambiguous image (more than one image file in the ISO directory) MUST produce a clear error — naming the files when multiple are present — and a non-zero exit code without modifying the database.
- **FR-011**: A netinst or minimal image (package set below a documented minimum consistent with the full offline DVD set) MUST produce a warning that such images cannot support offline installation, while still processing the metadata present.
- **FR-012**: The provides table MUST be populated from image binaries using the same rules as any other index.
- **FR-013**: The command MUST print a summary covering counts of binaries registered, provides entries, index/image mismatches (if any), resolution results, and warnings (if any).
- **FR-014**: During the resolution batch the command MUST display progress (e.g. `Resolved X/Y`); errors MUST be listed at the end, capped at 50 on the console with the full list written to the log.

### Key Entities *(include if feature involves data)*

- **ISOCache (new config entity)**: The offline DVD installer image file located in a directory inside the cache (default `<cache root>/iso`). A read-only artifact that ships with the handoff; read directly by the command.
- **RepositoryIndex (existing)**: Reused to parse the image's binary index; no new parsing machinery.
- **Binaries / provides / binary_to_source (existing DB tables)**: Populated with image-derived records carrying image provenance; `verified` flags follow `add` semantics (unset at registration, per FR-005); `binary_to_source` links created by the existing resolution flow per FR-008.
- **Database as Metadata Source of Truth**: The image's index (`Packages.gz`) is used once, at registration, to seed the initial records; all subsequent metadata retrieval (`list`, `show`, `verify`, resolution) reads the database — the image is never consulted again.
- **Config**: Gains an `iso_dir` setting (default `<cache root>/iso`).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A single command run ingests 100% of the binary metadata listed in the offline DVD installer image's index into the cache database, reading the raw image file with no manual extraction step.
- **SC-002**: Every record registered from the image carries the `verified` flag unset — identical to `add` — and an image whose index references a missing file produces the documented mismatch warning naming the path.
- **SC-003**: A binaries-only installer image ingests successfully, and every base binary's dependencies, build-dependencies, and source resolve with the same results as `add` on the same package names, with zero changes to that flow — no source DVD required.
- **SC-008**: A resolution batch of 4,000+ packages displays progress, lists errors at the end, and caps console errors at 50 with the full list in the log.
- **SC-004**: Running `ensure-base` twice leaves all affected tables unchanged: identical record counts and contents after the second run.
- **SC-005**: `list`, `show`, and `verify --graph` produce correct output over image records with no changes to those commands.
- **SC-006**: The image extraction, registration, and cross-check part of `ensure-base` completes with zero network access and never modifies the image file contents; subsequent resolution behaves exactly as configured for `add`.
- **SC-007**: A netinst or minimal image produces the documented warning; a full DVD image produces none.

## Assumptions

- **Offline DVD Installer Image**: The supported artifact is the full offline DVD installer image (the first installer DVD of the release set, e.g. `debian-13.0.0-amd64-DVD-1.iso`), which contains the base package set locally. Netinst and minimal images are **not** supported: their installation path requires network access, violating the offline-first principle.
- **Raw File Form**: The image file is stored as-is in `<cache root>/iso`; the command mounts or extracts it internally as needed to read the index and verify file presence. The user does not pre-extract the image.
- **Single Image File**: Each cache holds exactly one installer image in `iso_dir`; multi-DVD installer sets are out of scope. Multiple files in `iso_dir` produce an error (FR-010), never a silent selection.
- **Ingestion Scope**: All binary packages listed in the image's index are ingested. The installer's actual per-machine base selection is made at install time and is not part of the image metadata, so it cannot be derived from the image.
- **No Source DVD**: The companion source DVD is neither supplied nor required. Sources for rebuild are obtained through the existing resolution flow (the same flow `add` uses), starting from the base binary package name. It is assumed the flow-resolved sources do not diverge from the sources the source DVD would carry; any divergence surfaces through existing verification.
- **Same Behavior as `add`**: From the user's point of view, `ensure-base` behaves like `add`: for each base binary from the image, dependencies and build-dependencies are resolved as per app settings (which on the administrator's networked system may access the configured archives), sources are obtained, and the usual reporting and error semantics apply. The implementation may batch or structure work differently behind the scenes, but must not alter user-visible behavior or results.
- **In-Place Referencing**: No package files are copied out of the image — records reference file locations inside the image, consistent with the minimal-local-cache principle (no duplication of packages that already ship with the OS).
- **Verified Semantics**: The `verified` flag follows the `add` rule for every record in the cache: registration creates `verified = 0`; the flag is set only when a file is actually verified/downloaded at a later stage. Image presence never sets the flag (FR-005).
- **No New Parsing**: The image's index files are the same Debian metadata format already supported; existing parsing is reused, not rewritten.
- **Command Name**: The command is named `ensure-base`, matching the feature title; renaming is presentation only and does not change the functional contract.
- **GPG Scope**: Signature verification of the image's Release files remains with the existing verification flow; `ensure-base` confirms file presence, not signatures.

## Clarifications

### Session

- Q: How is the offline DVD installer ISO provided in the cache? → A: As the raw image file (e.g. `debian-13.0.0-amd64-DVD-1.iso`) located in a directory inside the cache; the command reads it directly (mounting/extraction is internal to the command). No manual extraction by the user.
- Q: Are sources from the companion source DVD in scope? → A: No. The source DVD is not supplied. Rebuild sourcing reuses the existing `add` resolution flow starting from the base binary package name; it is assumed the flow-resolved sources do not diverge from the source DVD set. If that holds, the binaries-only installer image suffices (US3, FR-008).
- Q: Does `ensure-base` run fully offline (no network activity)? → A: No. From the user's point of view `ensure-base` behaves the same as `add`: for each base binary from the image, dependencies and build-dependencies are resolved as per app settings (which may access the configured archives on the networked system). Only the image extraction, registration, and cross-check part is purely local. The Independent Test and SC-006 were corrected accordingly (US3, FR-006/007/008, Assumptions).
- Q: Does the ISO `Packages.gz` become the metadata source after ingestion? → A: No. The image's index is used once at registration to seed the initial records; the database is the source of truth for all subsequent metadata retrieval. The image index is a subset of the records the database ultimately holds after resolution.
- Q: Does `ensure-base` set `verified` flags for records whose files are present in the image? → A: No. Verified semantics are identical to `add`: records are registered `verified = 0`, and the flag is only set when a file is actually verified/downloaded at a later stage. The index↔image cross-check remains purely a damaged-image diagnostic (warning), never a flag setter (US2, FR-005, SC-002 amended).
- Q: How are netinst/minimal images handled? → A: They are not the supported artifact (their install path requires network). The command processes whatever metadata is present but warns when the image's package set is below a documented minimum consistent with the full offline DVD set (FR-011, US1 AS4).
- Q: What if `iso_dir` contains more than one image file? → A: The command MUST NOT select one silently — it errors, naming all files found; the administrator keeps exactly one image in `iso_dir` and re-runs (US1 AS2, FR-010, Edge Cases).
- Q: What output should the resolution batch produce? → A: Progressbar-style progress during resolution (e.g. `Resolved 1200/4233`), errors listed at the end, capped at 50 on the console (the full list is written to the log) to avoid overwhelming output during debugging, followed by the summary block (US3, FR-013/014, SC-003/008, contract).
- Q: "Absence of a source index is normal (not an error)" — sources still resolve, right? → A: Yes. The phrase refers only to the image's own index: installer DVDs are binaries-only, so `ensure-base` must not treat a missing source index on the image as an error. Sources resolve exactly like `add` — from the configured archives via the existing flow (creating `sources` records and `binary_to_source` links); the image's source index is never used (US3 AS1 reworded).
