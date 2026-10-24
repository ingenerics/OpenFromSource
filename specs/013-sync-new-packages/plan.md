# Implementation Plan: Sync New Packages

**Branch**: `013-sync-new-packages` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/013-sync-new-packages/spec.md`

## Summary

The baseline flow (spec 012) hands a full cache to the operator once; growing the cache afterwards would require re-shipping everything. This feature adds the incremental "diff" path between the two personas on top of the spec 010 snapshot plumbing:

- **Admin side** (one `package_mgmt sync --system <moniker>` invocation): computes the spec 010 pending set for the system (current + verified + not yet delivered), takes the spec 010 delivery snapshot for exactly the binaries it ships, then generates a **minimal diff cache** at `{binary_cache}/diff/` carrying only the new pool files (copied — they must physically ship), the **complete** per-release apt indexes (prior + new, so apt presents the union after apply), `Release` + `Release.gpg` signed with the same cache-local key as the baseline handoff, a plain-text **manifest** (per-file SHA256 + a "delivered" base set), and the operator's `apply.sh`. Re-running reports zero new packages and produces nothing (the snapshot already advanced).
- **Operator side** (one `apply.sh [<cache-dir>]` script shipped in the diff, default cache-dir = the diff's parent, mirroring `bootstrap.sh`): preflights the diff (structure, release match, root), verifies the **base set** (every previously-delivered file listed in the manifest already exists in the operator's pool — a missed intermediate diff is refused here, so apt never sees a package whose file is missing), checksum-copies the new files into the pool atomically, replaces the handoff's `dists/` indexes/`Release`/`Release.gpg` with the diff's complete ones (per-file atomic), and runs `apt update`. Standard `apt` commands keep working offline; previously installed packages are untouched.

The design reuses the spec 012 handoff generator wholesale (index loading, paragraph emission, Release/signing, keyring) — the diff IS a partial handoff (new files + complete metadata), so no new download/parse/sign machinery is introduced.

## Technical Context

**Language/Version**: Python 3.13.x (admin-side generator, same stack as specs 010-012); POSIX bash for the operator-side `apply.sh` (only tools guaranteed on a base install: `bash`, `apt`, standard coreutils — same constraint set as spec 012's `bootstrap.sh`).

**Database**: SQLite 3.x — **no schema changes** (spec 011 freeze). The diff generator only READS `binaries` (current/verified records + the system's `sync_mask` bit via the spec 010 snapshot service) plus the cached archive indexes and the pool, then writes the snapshot via the existing `mark_delivered`.

**Primary Dependencies**: stdlib only (`hashlib`, `gzip`, `sqlite3`, `debian.deb822` for index parsing); `gpg` (already required by the handoff) for signing on the admin side. Operator side: `apt` (+ `gpgv`/`sqv`, always present). No new runtime dependencies.

**Storage**: Cache layout as built by specs 008-012. New: `{binary_cache}/diff/` — the minimal diff payload (new pool files, complete per-release indexes, `Release`/`Release.gpg`, `offline-cache.gpg`, `manifest.txt` (new + delivered lines; the `delivered` lines are the base set), `apply.sh`). The baseline `{binary_cache}/handoff/` is READ (its persistent `.gnupg` keyring signs the diff) and never rewritten by `sync`; the operator's apply overwrites the *operator-side* handoff `dists/` with the diff's complete ones.

**Testing**: pytest (unit: diff generator content/manifest/base-set/snapshot/idempotency; apply script functions sourced with scratch paths — base-set mismatch, checksum verify, idempotent re-apply, release mismatch, atomic replacement; integration: baseline handoff → grow → diff → apply into a scratch cache → real `apt-get` with scratch apt dirs confirming prior + new are offered and installable; contract: CLI output shapes). No lintian/piuparts (no package building in this feature).

**Target Platform**: Debian Trixie LTS amd64 — operator system already bootstrapped from the spec 012 handoff.

**Project Type**: CLI tool (admin, extension of `handoff`) + shell script (operator, `apply.sh`) for incremental offline Debian LTS package delivery.

**Performance Goals**: Diff generation completes in seconds for caches up to ~10k verified records (single index parse + write of the complete index set; only pending files are copied); apply runs in seconds for typical diffs (base-set check is one `stat` per delivered file; new-file copies are bounded by the diff size); no pool re-verification of the whole cache on apply.

**Constraints**: Fully offline operator runtime (FR-008 — no network, no additional software on the operator system); payload minimality (FR-003 — only new files ship, Constitution IV); ordering safety (FR-011 — a missed intermediate diff is detected and refused, so apt never sees a package whose file is missing); idempotency (FR-006/FR-012); release match (FR-010); schema freeze; the offered set stays exactly the deliverable set (FR-002/FR-009).

**Scale/Scope**: Incremental diffs over a multi-thousand-package cache; each diff is a small fraction of the cache size (new files + metadata). The cycle repeats without bound (FR-014).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- ✅ **I. Offline-First Availability** — the operator flow (`apply.sh` → copy → `apt update` → `apt install`) performs zero network calls; everything ships in the diff.
- ✅ **II. Dependency Alignment** — the diff carries the admin's verified pending set (resolved pre-handoff by `add`); apt resolves within the merged cache plus the DVD base source.
- ✅ **III. Binary-Source Consistency** — unchanged; the diff ships binary packages like the handoff (spec 012 Decision 7), source pools remain as built by `download`.
- ✅ **IV. Minimal Local Cache** — the diff payload is minimal by construction (only new files + small metadata); the operator's cache grows by exactly the new files; no re-shipping of delivered content (FR-003).
- ✅ **V. Debian Policy Compliance** — no package modification/building; apt consumes the admin-verified content.
- ✅ **VI. Code Quality & Type Safety** — the generator follows the service-layer conventions (typed dataclasses, the existing handoff machinery); `apply.sh` mirrors `bootstrap.sh`'s testable-function structure.
- ✅ **VII. Database-First Resolution** — the diff derives its content from verified DB state + cached indexes + pool existence; the snapshot is written via the existing delivery-record service; nothing is guessed.
- ✅ **VIII. Documentation & Infrastructure Traceability** — roles.md gains `sync` (admin) and `apply.sh` (operator), and the documented-but-unimplemented bulk-ingest `sync` is renamed `ingest`; quickstart + contracts updated in this feature (task included).
- ✅ **IX. Incremental Spec Scope** — 3 stories / 14 FRs; reverting an applied diff and operator-side pruning are explicitly deferred (spec assumptions).

**Gate result: PASS** — no violations requiring Complexity Tracking.

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] `sync` is **admin-only** (runs on the networked build machine, like `handoff`/`snapshot`); `apply.sh` is **operator-only** (runs on the offline system against the existing cache)
- [x] No persona-dependent behavior leaks across the offline/online boundary — the operator path never runs the tool, never writes the database, and never touches the admin's cache; the admin path never runs `apply.sh`
- [x] Cache handoff expectations are reflected in the architecture — the diff directory IS the incremental payload; `apply.sh` locates the cache from its own position (`apply.sh [<cache-dir>]`, default = diff parent), so no settings parsing exists on the operator side
- [x] [roles.md](../memory/roles.md) will be updated with the `sync` command (admin table), the shipped `apply.sh` (operator table), and the bulk-ingest `sync` → `ingest` rename as part of this feature's documentation task

## Project Structure

### Documentation (this feature)

```text
specs/013-sync-new-packages/
├── plan.md              # This file
├── research.md          # Phase 0: command shape, diff payload design, ordering safety, integrity model
├── data-model.md        # Phase 1: diff artifact set, manifest/base-set, applied-state model
├── quickstart.md        # Phase 1: admin + operator validation scenarios
├── contracts/           # Phase 1: sync CLI contract, apply.sh contract
└── tasks.md             # Phase 2 output (/speckit.tasks - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/package_mgmt/
├── cli/
│   ├── main.py                  # register `sync` subcommand (--system <moniker>)
│   └── commands/
│       ├── sync.py              # handle_sync(db_path, config, system): validate moniker
│       │                        #   → generate_diff (spec 013) → mark_delivered → summarize
│       └── handoff.py           # (unchanged — spec 012 full handoff)
├── lib/
│   ├── service/
│   │   ├── handoff.py           # + generate_diff(...): pending set → minimal diff cache;
│   │   │                        #   reuses _verified_records, index loader, _paragraph,
│   │   │                        #   _write_gzip, _release_text, _write_key_and_signatures
│   │   ├── snapshot.py          # (unchanged) pending_count, system_bit, mark_delivered
│   │   └── downloader.py        # (unchanged) lookup_entry reused for per-record metadata
│   └── util/
│       └── paths.py             # + diff_dir(), diff_index_path(), diff_release_dir(),
│                                #   diff_manifest_path(), diff_script_path(), diff_pool_path()
tests/
├── unit/test_diff_generator.py      # index = complete current verified set; manifest/base-set
│                                    #   content; new-only pool; snapshot marking; idempotency;
│                                    #   excluded/unconfirmable handling
├── unit/test_apply_script.py        # source apply.sh functions with scratch paths: base-set
│                                    #   mismatch, checksum verify, idempotent re-apply,
│                                    #   release mismatch, atomic replacement, apt-update failure
├── integration/test_diff_flow.py    # baseline handoff + snapshot → add/download new pkgs →
│                                    #   sync → apply into scratch cache →
│                                    #   real apt-get (scratch apt dirs) shows prior+new, install works
└── contract/test_cli.py             # sync output shapes, exit codes, JSON errors
```

```text
{binary_cache}/
├── dists/...                    # archive indexes (UNCHANGED - project index-read design)
├── pool/...                     # verified files (UNCHANGED)
├── package_mgmt.db              # UNCHANGED (schema freeze); sync_mask advanced by the diff
├── handoff/                     # baseline spec 012 artifacts (READ by the diff generator for
│   ├── .gnupg                   #   the keyring; never rewritten by sync)
│   └── ...
└── diff/                        # NEW: produced by `package_mgmt sync --system <m>`
    ├── apply.sh                 # operator script: apply.sh [<cache-dir>] (default = diff parent)
    ├── manifest.txt             # one line per indexed file: `new|delivered <sha256> <size> <rel-path>`
    ├── offline-cache.gpg        # armored public key (same key as the handoff)
    ├── pool/                    # ONLY the new .deb files (real files, copied - they must ship)
    └── dists/{release}/
        ├── Release              # checksums of the COMPLETE (prior + new) filtered indexes
        ├── Release.gpg          # armored detached signature (same admin cache-local key)
        └── {component}/binary-amd64/Packages.gz   # COMPLETE current verified set (prior + new)
```

**Structure Decision**: The diff is a self-contained sibling of the handoff. It carries the **complete** per-release indexes (so the operator-side apply replaces the handoff `dists/` wholesale — no bash index merging, and the handoff's `Release`/`Valid-Until` is refreshed in the process) while the **pool payload is strictly the pending files** (Constitution IV — minimality applies to the shipped bytes). The base-set lines in the manifest (delivered files already on the operator) make the ordering check self-contained: no snapshot sequence number, no schema change, and a missed intermediate diff is detected by file presence before anything is modified.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations — the diff reuses the established handoff/`file://` trust model (spec 012 research Decisions 2, 4, 8) unchanged, so no new complexity is introduced beyond the minimal payload itself.
