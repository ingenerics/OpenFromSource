# Implementation Plan: Bootstrap Operator System

**Branch**: `012-bootstrap-operator-system` | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/012-bootstrap-operator-system/spec.md`

## Summary

The operator's fresh offline system (installed from the Debian LTS DVD) is pointed at the admin-built cache so that ordinary `apt` commands install packages from it with zero network and zero manual configuration. The design is deliberately split by persona per the feature request ("don't over-engineer; propose a bash script if it's simpler"):

- **Admin side** (one `package_mgmt handoff` command, `handoff --system <moniker>`): takes the spec 010 delivery snapshot (marks the verified set delivered to the moniker — the delivery record, so `snapshot` needn't be run separately), then derives an apt-consumable source from the cache's own verified state — filtered per-component `Packages.gz` indexes (exactly the verified pool, FR-003), a `Release` file with checksums, a GPG-signed `InRelease` (Constitution gate), an armored public key, and the operator's `bootstrap.sh` script — all under `{binary_cache}/handoff/`. No schema changes; the cache's own `dists/` tree is untouched (the project's index-read design keeps working).
- **Operator side** (one bash script shipped in the cache, invoked as `bootstrap.sh [<cache-dir>]` — optional positional argument defaulting to the cache the script ships in, i.e. the handoff's parent): validates the handoff (structure, release match), installs a `sources.list.d` entry pointing apt at the cache (`file://` + `signed-by` the handoff's `offline-cache.gpg`), and runs `apt update`. Idempotent; `--remove` restores the pre-bootstrap state. The script never parses project settings — the settings merge (defaults + local) is authoritative only on the admin side, where `handoff` runs.

The bash-script-vs-command decision was resolved empirically in research: a single bash script (no signing tooling, no key management) is sufficient and the most robust option on a bare base install — `apt update` runs clean with the handoff's `Release` (no blank line before the hash blocks) + `Packages`/`Packages.gz` hash entries + `[signed-by=.../offline-cache.gpg]`, which also verifies the release signature (research.md). The admin side reuses the existing Python service layer (DB + index loader) because a bash reimplementation would need `sqlite3`/`python3` and duplicate index parsing — reusing `package_mgmt` is simpler.

## Technical Context

**Language/Version**: Python 3.13.x (admin-side generator, same stack as specs 010/011); POSIX bash for the operator-side script (only tools guaranteed on a DVD base install: `bash`, `apt`, standard coreutils — no `gnupg`/`python3`/`sqlite3` needed on the operator system).

**Database**: SQLite 3.x — **no changes** (schema freeze from spec 011). The handoff generator only READS `binaries` (verified records) plus the cached archive indexes and the pool.

**Primary Dependencies**: stdlib only (`hashlib`, `gzip`, `sqlite3`, `debian.deb822` for index parsing). Operator side: `apt` (with its hard dependency `gpgv`/`sqv` — always present). No new runtime dependencies.

**Storage**: Cache layout as built by specs 008-011: `{binary_cache}/dists/{release}/{component}/binary-amd64/Packages.gz` (archive indexes) + `{binary_cache}/pool/...` (verified files) + `{binary_cache}/package_mgmt.db`. New: `{binary_cache}/handoff/` — the derived apt source (filtered indexes, `Release`/`Release.gpg`, public key, `bootstrap.sh`, `pool` symlink).

**Testing**: pytest (unit: generator content/checksums/idempotency, Release format; integration: fixture cache → handoff → bootstrap script against a scratch root → real `apt-get` with scratch apt dirs — the exact mechanics verified in research; contract: CLI output shapes). No lintian/piuparts involvement (no package building in this feature).

**Target Platform**: Debian Trixie LTS amd64 — operator system freshly installed from the offline DVD ISO.

**Project Type**: CLI tool (admin) + shell script (operator) for offline Debian LTS package consumption.

**Performance Goals**: `handoff` completes in seconds for cache sizes up to ~10k verified records (single index parse + write); bootstrap script runs in seconds (no pool copies — the `pool` symlink serves the existing files in place).

**Constraints**: Fully offline operator runtime (FR-002/FR-007 — no network, no software installation on the operator system); offered set == verified pool exactly (FR-003); Release files GPG-signed (Constitution gate); read-only cache from the bootstrap's perspective; schema freeze; no manual trust workarounds (FR-009/SC-006).

**Scale/Scope**: Caches with thousands of verified packages; the handoff is a small fraction of the cache size (indexes + key + script).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- ✅ **I. Offline-First Availability** — the operator's flow (`bootstrap.sh` → `apt update` → `apt install`) performs zero network calls; everything ships in the handoff.
- ✅ **II. Dependency Alignment** — the offered set is the admin's verified pool (resolved pre-handoff by `add`); apt resolves within it plus the DVD base source.
- ✅ **III. Binary-Source Consistency** — unchanged; source pools remain as built by `download` (deb-src exposure is explicitly out of scope, documented in research).
- ✅ **IV. Minimal Local Cache** — no pool duplication: the operator source serves the existing pool in place via the handoff `pool` symlink.
- ✅ **V. Debian Policy Compliance** — no package modification/building; apt consumes the admin-verified content.
- ✅ **VI. Code Quality & Type Safety** — the generator follows the service-layer conventions (typed dataclasses, existing index loader).
- ✅ **VII. Database-First Resolution** — the handoff derives its indexes from verified DB state + cached indexes; nothing is guessed (spec 010/011 design carries through).
- ⚠️ **VIII. Documentation & Infrastructure Traceability** — roles.md gains the `handoff` command; quickstart + contracts updated in this feature (task included).
- ✅ **IX. Incremental Spec Scope** — 3 stories / 10 FRs; the "reinstall entire stack" capability is explicitly deferred (documented in spec assumptions).

**Gate result: PASS** — one caveat tracked in Complexity Tracking (file:// signature enforcement).

## Roles Check

*Verify implementation aligns with the personas defined in [roles.md](../memory/roles.md).*

- [x] `handoff` is **admin-only** (runs on the networked build machine, like `verify`); `bootstrap.sh` is **operator-only** (runs on the offline system)
- [x] No persona-dependent behavior leaks across the offline/online boundary — the operator path never writes to the cache and never fetches; the admin path is the only producer of handoff artifacts
- [x] Cache handoff expectations are reflected in the architecture — the handoff directory IS the handoff payload; the script locates the cache from its own position (`bootstrap.sh [<cache-dir>]`, default = handoff parent), so no settings parsing exists on the operator side
- [x] [roles.md](../memory/roles.md) will be updated with the `handoff` command (admin table) and the shipped `bootstrap.sh` (operator table) as part of this feature's documentation task

## Project Structure

### Documentation (this feature)

```text
specs/012-bootstrap-operator-system/
├── plan.md              # This file
├── research.md          # Phase 0: bash-vs-command decision, apt file:// mechanics (empirically verified)
├── data-model.md        # Phase 1: handoff artifact set, sources entry, bootstrap contract state
├── quickstart.md        # Phase 1: admin + operator validation scenarios
├── contracts/           # Phase 1: handoff CLI contract, bootstrap.sh contract
└── tasks.md             # Phase 2 output (/speckit.tasks - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/package_mgmt/
├── cli/
│   ├── main.py                  # register `handoff` subcommand (--system <moniker>)
│   └── commands/
│       └── handoff.py           # handle_handoff(db_path, config, system): validate moniker
│                                #   → snapshot (spec 010 semantics) → generate → summarize
├── lib/
│   ├── model/
│   │   └── package.py           # (unchanged; reused BinaryPackage fields)
│   ├── service/
│   │   ├── index_loader.py      # (unchanged; load_binary_index reused)
│   │   ├── downloader.py        # (unchanged; _lookup_entry pattern reused by handoff)
│   │   └── handoff.py           # handoff generator: filtered Packages.gz, Release/Release.gpg,
│   │                            #   key management, bootstrap.sh emission
│   └── util/
│       └── paths.py             # + handoff_dir(), handoff paths helpers
tests/
├── unit/test_handoff.py         # index content = verified set, Release checksums, idempotency
├── integration/test_handoff_flow.py  # fixture cache → handoff → bootstrap.sh in scratch root
│                                    #   → real apt-get update/install (scratch apt dirs)
├── integration/test_bootstrap_flow.py # operator-side: release mismatch, idempotency, --remove
└── contract/test_cli.py         # handoff output shapes, exit codes, JSON errors

{binary_cache}/
├── dists/...                    # archive indexes (UNTCHANGED - project index-read design)
├── pool/...                     # verified files (UNTCHANGED)
├── package_mgmt.db              # UNTCHANGED
└── handoff/                     # NEW: generated by `package_mgmt handoff`
    ├── bootstrap.sh             # operator script: bootstrap.sh [<cache-dir>] [--remove] (default = handoff parent)
    ├── offline-cache.gpg        # armored public key
    ├── pool -> ../pool          # symlink (re-created by the script if missing)
    └── dists/{release}/
        ├── Release              # checksums of the filtered indexes
        ├── Release.gpg          # armored detached signature (admin key)
        └── {component}/binary-amd64/Packages.gz   # filtered to the verified pool
```

**Structure Decision**: The handoff is a self-contained subdirectory of the binary cache so the project's own `dists/` indexes (read by `add`/`download`/`verify` per the spec 010/011 index-read design) are never overwritten; the `pool` symlink serves the verified files in place (Constitution IV — no duplication).

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `file://` apt source uses `[signed-by=<handoff>/offline-cache.gpg]` (Release signature *enforced* by apt) | The handoff's `Release` (no blank line before the hash blocks) + `Packages`/`Packages.gz` hash entries + `signed-by` the shipped armored public key makes apt verify the release signature: `apt update` runs clean (zero warnings) and the index is fetched; a tampered `Release` is refused ("repository ... is not signed"). The operator never manages keys — the pinned key ships in the handoff, so no `[trusted=yes]` workaround is needed. | (a) `[trusted=yes]` (no `signed-by`) — rejected: apt still warns "Missing key ... needed to verify signature" on every update and never enforces the signature. (b) Serve the cache over local HTTP to enable full signature enforcement — adds a daemon and lifecycle management, violating the "don't over-engineer" constraint and unnecessary since `file://` + `signed-by` already enforces it. |
