# OpenFromSource — Project Roles and Personas

## Administrator

**Environment**: Networked system (has access to the internet / Debian archives).

**Responsibilities**:
- Fetch packages and their dependencies into the local cache from an online system.
- Manage which packages go into the cache.
- Resolve dependency mismatches (e.g. find compatible versions per Constitution Principle II).
- Verify cache integrity before handing it off.
- Handle cross-machine transport of the cache using apt-offline when direct fetch is impractical.

**Commands the administrator runs**:
| Command | Purpose | Phase |
|---------|---------|-------|
| `package_mgmt add <pkg>` | Resolve and fetch a package plus its missing dependencies into the cache | Cache build-up |
| `package_mgmt ensure-base` | Ingest the offline DVD installer image from `iso_dir`: register every binary from the image index and resolve the base set like `add` | Cache build-up |
| `package_mgmt import-deb <file> [--source <dir>]` | Import a locally built or patched `.deb` into the `local` component, optionally alongside its complete rebuilt source set (`.dsc` + referenced files, checksum-verified) — distributed and installed through the ordinary handoff/sync channel (spec 021) | Cache build-up |
| `package_mgmt align-repo [--verify <manifest>] --repo-dir <dir>` | Determine/verify the version-aligned source set (spec 023). Default mode: scan the acquired repos in `<dir>`, find the newest Hyprland era whose declared build/runtime dependency requirements are all satisfied by the cached trixie tree (or by the selected companion source versions), and **produce the version manifest** with success or failure (exit 0 + manifest written, or exit 1 naming the newest era's unmet requirements). `--verify <manifest>`: re-validate a given manifest against the current cache and `repos/` (exit 0 PASS / exit 1 FAIL, never writes). Admin-only, run against the cache + `repos/` | Cache build-up / verification |
| `package_mgmt package-stack [--recipe <file>] [--format text\|json]` | Package and import a source-built stack into the cache `local` component (spec 024): consume a declarative recipe (`recipes/<stack>.toml`) + the staged install directories, build a deterministic `.deb` with derived `Depends` and a rebuildable `.dsc` source set per component, import the pair in dependency order (reusing `import-deb`'s machinery, `binary_to_source`-linked, verified), then run the whole-set `verify` + simulated offline `apt install`. Admin-only, run on the packaging host against `repos/` + the staged tree + the cache | Cache build-up / verification |
| `package_mgmt list` | See what's currently in the cache | Cache build-up / verification |
| `package_mgmt verify` | Validate cache integrity (SHA256 checks) | Cache build-up / handoff |
| `package_mgmt ingest` | Bulk-ingest an apt-offline bundle (`.deb`, `.dsc` files) into the cache | Alternative cache build-up |
| `package_mgmt handoff` | Generate the operator's apt source + `bootstrap.sh` under `{cache}/handoff/` from the verified pool (spec 012) | Handoff |
| `package_mgmt sync` | Produce a minimal diff cache under `{cache}/diff/` — only the new, verified packages since a system's last snapshot — and take the delivery snapshot (spec 013) | Incremental sync |

> **Note on `ingest` vs `add`**: `add` is the admin's primary tool for fetching packages from an archive. `ingest` is used when the admin prefers to gather a large or diverse bundle using `apt-offline` on another machine, then ingest the pre-downloaded bundle via `ingest`. It is not required — it is an alternative workflow for bulk or cross-machine collection. (`sync`, spec 013, is the *incremental delivery* command: it ships only what's new since a system's last snapshot, which the operator merges with `apply.sh`.)

**Cache handoff**: The administrator copies the cache directory (pool layout + `package_mgmt.db` + index files) onto portable media (USB) along with a Debian LTS ISO, then ships it to the operator.

> **Usage profiles**: The project ships three admin-side configuration presets
> under `docs/profiles/` — `regular_user`, `developer`, `graybeard` — as plain
> project-setting TOML files the administrator drops in as `project.defaults.toml`
> (or merges into `project.local.toml`). They are data only: there is no profile
> concept in the tool, no `--profile` flag, and no role or command assignments
> change. `project.defaults.toml` ships the `regular_user` presets (binaries
> only, no sources/build tools).

---

## Operator

**Environment**: Offline system (no network access; offline-first by design).

**Responsibilities**:
- Configure the system to use the admin-provided cache as an apt source.
- Install, remove, and manage packages from the local cache.
- Rebuild the full software stack from the cache on a fresh install.
- Integrity of the received cache is guaranteed by the admin's pre-handoff `verify` and the handoff's structural preflight; deep operator-side verification is deferred (the tool is not installed on a bare system).

**Commands the operator runs**:
| Command | Purpose | Phase |
|---------|---------|-------|
| `{cache}/handoff/bootstrap.sh` | Configure apt to install from the cache (spec 012; shipped inside the cache, no tool install needed); `--remove` unconfigures | Post-handoff setup |
| `{cache}/diff/apply.sh` | Merge a minimal diff cache into the existing cache (spec 013; shipped inside the diff, no tool install needed) | Incremental sync |
| `apt-cache` / `apt list` | Browse available packages in the cache (ordinary apt tooling — the project CLI is not installed on the bare system) | During/after install |
| `package_mgmt bootstrap` | Reinstall the entire cached package stack on a fresh OS (future feature) | Rebuild |

> **Note on integrity**: The operator does **not** run `package_mgmt verify`/`list` — the tool ships in the project repo, not in the handoff, and FR-007 forbids installing it on the bare system. Integrity rests on the admin's pre-handoff `verify`, the handoff's structural preflight (`bootstrap.sh`), and the physical handoff; deep operator-side verification is deferred.

**The operator does NOT run**:
- `sync` — the operator does not run the delta-export command; the shipped `{cache}/diff/apply.sh` merges the diff into the cache instead.
- `add` — package fetching is an admin-only concern; the operator only receives a finished cache.
- `ensure-base` — installer-image ingestion is admin-only (like `add`); the image ships with the cache handoff and the operator never ingests it.

---

## Two-Phase Workflow

```
┌───────────────────────────────────┐    ┌───────────────────────────────────┐
│          ADMINISTRATION           │    │           OPERATOR                │
│  (networked system)               │    │    (offline system)               │
│                                   │    │                                   │
│  1. `add neovim`                  │    │  ┌──────────────────────┐         │
│     → resolves deps, fetches      │    │  │ Receives:            │         │
│       missing pkgs into cache     │    │  │  - Debian LTS ISO    │         │
│                                   │    │  │  - Cache directory   │         │
│  2. (optional) `ingest` for       │    │  │  - apt-offline bundle│         │
│     apt-offline bundle ingest     │    │  │  - Diff cache (013)  │         │
│                                   │    │  └──────────────────────┘         │
│  3. `verify` (pre-handoff)        │    │                                   │
│     → ensure cache integrity      │    │  1. `bootstrap.sh` (in cache)     │
│  4. `handoff` (spec 012)          │    │     → configure apt to the cache  │
│     → generate operator source    │    │     (structural preflight only)   │
│  5. `sync` (spec 013) for new     │    │  2. `apply.sh` (in diff, 013)     │
│     packages → minimal diff       │    │     → merge new pkgs into cache   │
│  6. Copy cache + ISO to USB       │ ──→│  3. `apt install`                 │
│     (physical handoff)            │    │     → packages from local cache   │
│                                   │    │                                   │
│                                   │    │  4. `bootstrap` (future)          │
│                                   │    │     → full stack from cache       │
└───────────────────────────────────┘    └───────────────────────────────────┘
```

---

## Impact on Design Decisions

| Decision | Rationale |
|----------|-----------|
| `add` resolves and fetches on the connected host | The admin has network access — `add` should fetch missing deps from archive and place them in the cache. Flagging only applies when fetch is not possible. |
| `ingest` ingests pre-downloaded bundles (apt-offline) | Used for bulk or cross-machine transport scenarios where apt-offline is preferred over direct fetching. |
| `sync` exports the minimal diff (spec 013) | The admin ships only the new, verified packages since a system's last snapshot; the operator merges them with `apply.sh`. |
| `verify` is admin-only in practice | The admin verifies pre-handoff to ensure the cache shipped properly. The operator cannot run the project CLI on a bare install (FR-007); post-receipt integrity rests on the handoff's structural preflight, and deep operator-side verification is deferred. |
| `bootstrap` is operator-only | Only the operator needs to rebuild the full stack on a fresh offline system. |
| `handoff` is admin-only (spec 012) | The admin's networked machine is the only place the generator runs (DB + cached indexes + pool); the operator consumes the generated source with a plain script and base-install tools only. |
| `list` shared | Both personas benefit from visibility into what the cache contains. |
| `add` is admin-only, never operator | Package fetching and cache expansion happens exclusively on the admin's connected system. |
| `ensure-base` is admin-only, never operator | Ingests the offline DVD installer image on the admin's system (like `add`); the operator only receives the finished cache with the image alongside it. |
| `align-repo` is admin-only (spec 023) | Version alignment research and validation happen on the admin's networked system against the cache DB and the acquired `repos/` trees; the command produces the manifest. The operator never runs it — the offline build/setup consumes the validated manifest. |
| `package-stack` is admin-only (spec 024) | Packaging the source-built stack happens on the admin's packaging host, which has the stack and the cache's build deps installed (so `dpkg-shlibdeps` attribution works); the operator only consumes the imported `local` packages through the existing handoff/sync/apt channel (and `apt-get source` for the linked sources) — no operator-side tooling. |

---

## Updated Constitutional Context

- **Constitution Principle IV (Minimal Local Cache)** — the admin resolves and fetches only what's strictly required before handing the cache to the operator.
- **Constitution Principle II (Dependency Alignment)** — the admin's `add` command resolves dependency trees and selects compatible versions pre-handoff.

---

**Applies to**: Feature 001 (Base Cache Infrastructure) and all future features
