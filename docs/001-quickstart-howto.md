# OpenFromSource — Getting Started Guide

This guide walks through setting up OpenFromSource on a fresh Debian LTS system and
using it to resolve and register a package's dependency tree (using `vifm` as the
example). No package payloads are downloaded — this is metadata-only resolution
(database-first: metadata before payloads).

---

## Prerequisites

- **Debian Trixie LTS** (amd64) or compatible Debian-based system
- **Python 3.11+** (Trixie default is 3.13)
- **Git** (to clone the repository)
- **Network access** to a Debian archive mirror (default: `deb.debian.org`)

### Command prerequisites (external tools)

The core metadata path (`init`, `add`, `download`, `verify`, `list`, `show`,
`config`, `snapshot`, `sync`) needs **no external Debian packaging tooling**
(`dpkg-*`) — it is pure Python (the `debian` module parses indexes/`.deb`s).
The one external binary on that path is `gpgv`, required only when an archive
index fetch is verified against the signed `Release` (see the first row below).
Only the commands below shell out to system binaries; each names its required
tools and what happens when one is missing:

| Command | Required tools (apt package) | Failure mode when missing |
|---------|------------------------------|---------------------------|
| `init`, `add`, `sync` (index fetch/verify) | `gpgv` (`gnupg`) | `IndexVerificationError` — the archive's signed `Release` cannot be verified, so the index is **refused and never loaded** |
| `handoff` (payload signing) | `gpg` (`gnupg`) | `HandoffError` — *"gpg not found — the handoff requires gnupg on the admin system"* |
| `package-stack` (binary build) | `dpkg-deb`, `dpkg-shlibdeps` (`dpkg-dev`) · `readelf`, `strip` (`binutils`) | `DebGenError` — *"cannot run dpkg-deb: …"* / *"cannot run dpkg-shlibdeps: …"*; missing `readelf`/`strip` degrade (no soname, no strip), never abort |
| `package-stack` (source build) | `dpkg-source` (`dpkg-dev`) | `SrcGenError` — *"cannot run dpkg-source: …"* |
| `package-stack` (offline simulation) | `apt-get` (`apt`) | simulation fails with *"apt-get is not available on this host"* |
| `import-deb` | none (python-debian `ar`/control readers) | `ImportDebError` — *"Cannot read package file: …"* |

> `dpkg-parsechangelog`-compatible changelog dates are produced internally; the
> generators run `dpkg-shlibdeps`/`dpkg-deb`/`dpkg-source` themselves, so the
> `dpkg-dev` package (which provides all three) is the one install that covers
> `package-stack`.

---

## Step 1: Install system dependencies

```bash
sudo apt update
sudo apt install -y git python3 python3-pip gpgv
```

> The project's runtime dependencies are installed from PyPI by `pip install`
> below — no apt packages are needed: `python-debian` (the `debian` module's
> `debian.debian_support.Version` and the `deb822` parser used for reading
> Debian repository index files) and `pycdlib` (the ISO reader used by the
> `ensure-base` command chain). Because both are runtime dependencies, a plain
> `pip install .` (no extras) imports and runs every command.

---

## Step 2: Clone and install the project

```bash
git clone <repository-url> OpenFromSource
cd OpenFromSource
pip install -e .
```

This registers the `package_mgmt` command globally.

Two install modes are supported:

- **Editable (development)**: `pip install -e .` — the CLI runs straight from
  the working tree. The archive-signing keyring that `init`/`add` use to
  verify remote index signatures lives at `package_mgmt/data/` and is read
  from the source tree.
- **Standard (built artifact)**: `pip install .` — installs the built wheel.
  The wheel ships `package_mgmt/data/debian-archive-keyring.gpg` as package
  data, so remote index signature verification works from a plain installed
  copy as well — you are not limited to an editable install for real-archive
  fetches. No extras are required: the runtime dependencies are declared in
  `[project] dependencies`, so a plain install runs every command.

> **Development tip**: Instead of `pip install` after every code change, use
> `PYTHONPATH=src python3 -m package_mgmt` from the project root. This runs the
> CLI directly from your working tree — no reinstall needed:
> ```bash
> export PYTHONPATH="$PWD/src"
> python3 -m package_mgmt add vifm
> ```

Verify the installation:

```bash
package_mgmt --help
```

Expected output:
```
usage: package_mgmt [-h]
                    {add,ensure-base,import-deb,init,download,config,list,show,verify,systems,snapshot,handoff,sync} ...

OpenFromSource package management CLI

positional arguments:
  {add,ensure-base,import-deb,init,download,config,list,show,verify,systems,snapshot,handoff,sync}
    add                 Resolve and register a package and its dependencies
    ensure-base         Register offline DVD installer image metadata in the
                        cache
    import-deb          Import a locally built .deb into the local component
    init                Initialize cache directories and database
    download            Download and verify pending files into the cache pool
    config              Display configuration
    list                Display resolved dependency graph
    show                Display database records for a package
    verify              Verify dependency graph completeness
    systems             Manage sync system registry
    snapshot            Mark the current, verified cache state as delivered
                        for a system
    handoff             Prepare the cache for operator handoff (spec 012)
    sync                Prepare a minimal diff cache of new packages for a
                        system (spec 013)

options:
  -h, --help            show this help message and exit
```

---

## Step 3: Configure (optional)

The default configuration in `project.defaults.toml` targets Debian Trixie
with the `main` component from `https://deb.debian.org/debian`:

```toml
[paths]
base_dir = "/var/cache/package_mgmt"

[fetch]
# Regular_user preset: binaries only — no source packages.
source = false

[[archive.source]]
mirror = "https://deb.debian.org/debian"
release = "trixie"
components = ["main"]
```

If you need different settings (multiple releases, extra components, or a
different mirror), create `project.local.toml` in the project root with
`[[archive.source]]` entries. The local file replaces the default sources entirely:

```toml
[paths]
base_dir = "/var/cache/package_mgmt"

[fetch]
# Enable source fetching when you need build-tool/source support (developer,
# graybeard profiles). Regular_user keeps this false.
source = true

[[archive.source]]
mirror = "https://deb.debian.org/debian"
release = "trixie"
components = ["main", "contrib", "non-free"]

[[archive.source]]
mirror = "https://deb.debian.org/debian"
release = "trixie-backports"
components = ["main"]
```

The first source is the default for `add` commands; `add --source <release>`
overrides it. Components are validated so no `(release, component)` pair is
configured twice.

> **Why a local override file?** `project.defaults.toml` is version-controlled
> and shipped with the project. Your local settings stay in
> `project.local.toml`, which is git-ignored.

---

## Step 4: Initialize the cache

```bash
package_mgmt init
```

This creates:
- `{base_dir}/binary/pool/` and `{base_dir}/binary/dists/` — binary cache tree
- `{base_dir}/source/pool/` and `{base_dir}/source/dists/` — source cache tree
- `{base_dir}/package_mgmt.db` — SQLite database with the schema:
  `packages`, `binaries`, `sources`, `binary_to_source`, `source_build_deps`,
  `provides`, `virtual_resolutions`
- fetches `Packages` indexes for every configured source (and `Sources`
  indexes when `[fetch] source = true`; trying `.gz` first, falling back to
  `.xz`) and populates the `provides` table with the virtual-package
  mappings from all of them
- index fetches are secured: remote mirrors use HTTPS (any `http://` mirror is
  upgraded automatically) and each index is only trusted after the archive's
  `InRelease`/`Release` signature verifies against the pinned Debian archive
  keyring and the index bytes match the signed `Release` checksum — a crafted
  or tampered index is refused and never loaded (loopback mirrors such as a
  local test archive skip this, since the Debian keyring cannot apply there)

Expected output (with the multi-source local config above; counts vary with
the archive state):
```
Base directory:    /var/cache/package_mgmt
Binary cache:      /var/cache/package_mgmt/binary
Source cache:      /var/cache/package_mgmt/source
Database:          /var/cache/package_mgmt/package_mgmt.db
Schema version:    1
Default source:    trixie/main
Archive sources:
  [1] trixie/main — 68755 binaries, 37633 sources
  [2] trixie/contrib — 303 binaries, 159 sources
  [3] trixie/non-free — 741 binaries, 262 sources
  [4] trixie-backports/main — 3074 binaries, 849 sources
Provides entries:  69939
Status:            initialized
```

> **`init` requires connectivity**: `init`'s job is network work — it fetches
> the `Packages` (and, when enabled, `Sources`) indexes from at least one
> reachable configured mirror and builds the virtual-package (`provides`)
> snapshot. An `init` reporting "0 binaries cached" (offline mode) or a
> partial fetch is **not a usable cache**: the `provides` snapshot is built
> only by `init`/`ensure-base` and is never rebuilt on a later `add`, so a
> cache initialized without a reachable mirror fails every virtual dependency
> (e.g. `awk`, `editor`) with "no providers in the index". Resolve
> connectivity to a configured mirror and re-run `init` from a clean cache —
> the admin system is online by design; the offline-mode tolerance is only
> for a transient mirror outage, never a deliberate offline setup.

> **Idempotent**: Running `init` again is safe — it detects existing state,
> validates the schema, and repopulates the `provides` snapshot from the
> cached indexes. Resolution state (`packages`/`binaries`/`sources` and their
> history) is preserved.

---

## Step 5: Add a package

```bash
package_mgmt add vifm
```

This triggers the following workflow:

1. **Index acquisition** — `add` checks whether `Packages.gz` (or
   `Packages.xz`) and `Sources.gz` (or `Sources.xz`) are already cached
   locally. If not, it fetches them from the configured Debian archive mirror,
   trying `.gz` first then falling back to `.xz`.
2. **Index parsing** — The compressed index files are parsed using the `deb822`
   library, producing an in-memory index of all available binary and source
   packages.
3. **Dependency resolution** — The resolver looks up `vifm` in the binary
   index, finds its `Depends` field, creates a record in the `packages` table,
   then recurses into each dependency until the full transitive closure is
   resolved. If `--source <release>` is provided, the primary source is
   searched first, falling back to other configured sources. Dependency
   version constraints are honored during selection, and virtual packages
   (e.g. `awk`) are resolved to a provider deterministically.
4. **Source linking** — For every binary package in the tree, the resolver
   finds the corresponding source package via the `Source` field (or falls
   back to the package name itself). A record is created in the `sources`
   table and linked via `binary_to_source`.
5. **Build-tool resolution** — If `[fetch] source = true` (when enabled),
   each source's `Build-Depends` are parsed and their tools resolved into the
   `binaries` table, linked via `source_build_deps` with a depth marker.
   Unresolvable tools are reported as errors but don't stop the rest
   (best-effort).
6. **Reporting** — A per-package summary is printed.

Expected output (actual counts vary with the archive state):
```
Package vifm resolved from trixie/main (8533 dependencies, 16 virtual)
```

With `--source` flag:
```
package_mgmt add hyprland --source trixie-backports
Package hyprland resolved from trixie-backports/main (8819 dependencies, 8441 from fallback, 2 virtual)
```

All records are created with `verified = 0` — the package payloads have not
been downloaded yet. This is the database-first resolution pattern: metadata
first, payloads later.

---

## Step 6: Verify the results

Inspect the resolved dependency tree:

```bash
package_mgmt list --deps vifm
```

Expected output (tree showing transitive dependencies with annotations;
versions are current trixie values):
```text
vifm (pending)
  ├─ perl (= 5.40.1-6) [binary ✓, source ✓, verified: ✗]
  │ ├─ perl-base (= 5.40.1-6) [binary ✓, source ✓, verified: ✗]
  │ ├─ perl-modules-5.40 (= 5.40.1-6) [binary ✓, source ✓, verified: ✗]
  │ └─ libperl5.40 (= 5.40.1-6) [binary ✓, source ✓, verified: ✗]
  │   ├─ libbz2-1.0 (= 1.0.8-6) [binary ✓, source ✓, verified: ✗]
  │   │ └─ libc6 (= 2.41-12+deb13u3) [binary ✓, source ✓, verified: ✗]
  │   │   └─ libgcc-s1 (= 14.2.0-19) [binary ✓, source ✓, verified: ✗]
  ├─ libmagic1t64 (= 1:5.46-5) [binary ✓, source ✓, verified: ✗]
  ├─ libncursesw6 (= 6.5+20250216-2) [binary ✓, source ✓, verified: ✗]
  └─ vim (= 2:9.1.1230-2) [binary ✓, source ✓, verified: ✗]
    ├─ vim-common (= 2:9.1.1230-2) [binary ✓, source ✓, verified: ✗]
    ├─ vim-runtime (= 2:9.1.1230-2) [binary ✓, source ✓, verified: ✗]
    └─ libselinux1 (= 3.8.1-1) [binary ✓, source ✓, verified: ✗]
      └─ libpcre2-8-0 (= 10.46-1~deb13u1) [binary ✓, source ✓, verified: ✗]
```

> The tree is compressed by default (repeated subtrees shown once); use
> `--full` to show every repetition. `--include-build-deps` appends build
> tools. Use `--format json` for programmatic consumption.

Inspect the raw database records for a package:

```bash
package_mgmt show vifm
```

Shows the `packages`, `binaries`, `sources`, and `binary_to_source` rows for
`vifm` (plus `source_build_deps`, `provides`, and `virtual_resolutions` on
request via `--tables`). Add `--deps` to append the dependency tree; add
`--format json` for machine-readable output.

Check overall graph completeness:

```bash
package_mgmt verify
```

Expected output (format; counts vary):
```text
Dependency Graph Verification
─────────────────────────────
Total packages:         11420
Total binaries:         9375
Total sources:          6049
Unresolved deps:        0
Unlinked binaries:      0
Circular dependencies:  42
  - libgcc-s1 -> libc6 (circular)
  - dmsetup -> libdevmapper1.02.1 (circular)
  - ... (further entries omitted)
Runtime Status:         COMPLETE

Build-Tool Dependencies
───────────────────────
Total build-dep links:  52953
Total build tools:      6984
Unresolved build deps:  0
Missing build tools:    0
Build Status:           COMPLETE

Status:                 COMPLETE
```

> **Circular dependencies are informational.** Real Debian trees contain
> benign cycles (`libc6 <-> libgcc-s1`, `dmsetup <-> libdevmapper1.02.1`, …);
> the resolver records and annotates them, and `verify` lists them — but they
> do not affect the status. Only unresolved dependencies, unlinked binaries,
> and unresolved or missing build tools fail verification. The exit code is
> 0 for `COMPLETE`, non-zero for `INCOMPLETE`.

### Interpreting the verify counts

| Count | What it counts | Relationship |
|-------|----------------|--------------|
| **Total packages** | Rows in the `packages` table (unique *name*) — the registry of every name the resolver touched: requested packages, transitive runtime deps, build tools, and virtual package names | Cumulative name registry — not strictly nested with binaries (see below) |
| **Total binaries** | Rows in `binaries` with `status='current'` — the concrete build records that form the resolved tree (one per name+version+arch+release+component) | The operative size of the cache |
| **Total sources** | Rows in `sources` — recorded source packages (per name+version+release+component, no status) | Fewer than binaries: one source produces many binaries |

- **packages > binaries**: the `packages` table is keyed by the name *as
  requested*. Virtual names (like `awk`, `debhelper-compat`, `dh-sequence-*`)
  get their own `packages` row — thousands of them in a typical tree. Names
  whose binaries were superseded by a version change also remain here.
- **binaries > sources**: fan-out — one source builds many binaries (e.g.
  `qt6-declarative` produces 51 binaries in trixie).
- Known quirk: when a virtual resolves, the provider's record is registered
  under the virtual name, so a number of providers (e.g. `gawk` for `awk`,
  854 in a vifm-only cache) have no matching `packages` row of their own.
  This does not affect verification.

---

## Step 7: Add more packages (with version pins)

You can add multiple packages at once and pin specific versions:

```bash
package_mgmt add nano vim=2:9.1.1230-2
```

- Packages resolve independently per best-effort semantics: if `nano`
  resolves but `vim=2:9.1.1230-2` is not found, `nano`'s records remain.
- The `=version` suffix is optional. Omitting it selects the latest version
  available in the index.

### Add many packages from a file

For large manifests (a rebuild list, a base package set), pass a
package-list file instead of positional names — the two input forms are
mutually exclusive:

```bash
package_mgmt add --file /path/to/packages.txt
```

The file holds one package per line, with an optional `=version` pin in the
same notation as positional arguments:

```text
# rebuild manifest
vifm
lib1=2.0-1
```

Blank lines and lines starting with `#` are ignored; surrounding whitespace
is trimmed. All names are resolved in one batch and the run reports
batch-style (progress `Resolved X/Y` during resolution, then a summary with
errors capped at 50 on the console and the full list in the log):

```text
Packages requested:  1234
Packages resolved:   1220
Errors:              14
  - vim: Virtual package 'foo' has no providers in the index
```

File validation is all-or-nothing: a missing file, a malformed line (named
with its file and line number), or a file with no names aborts the run before
anything is processed. Re-running the same file is idempotent.

---

## Running the tests (development)

The test suite is `unittest`-based; it needs the `debian` module, which a
regular install provides. In a virtualenv:

```bash
cd OpenFromSource
python3 -m venv .venv && . .venv/bin/activate
pip install -e .        # installs the runtime deps (python-debian, pycdlib) + the CLI
python3 -m unittest discover -s tests -v
```

This runs the full suite (unit, integration, contract). Running from the
working tree without installing (`PYTHONPATH=src python3 -m unittest
discover -s tests`) requires the `debian` and `pycdlib` modules to be
importable (e.g. via the venv above).

For a production-style copy, a standard `pip install .` from the built
artifact is fully equivalent for signature-verifying archive fetches — the
wheel ships the keyring as package data (see Step 2). The editable install
above is the developer convenience path.

Optional convenience tooling (pytest runner + ruff linter) is declared as a
dev extra; the `pycdlib` module the fixture ISO builder needs is now a runtime
dependency, so it is present in any install:

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Troubleshooting

**"package_mgmt: command not found"**
Ensure the project is installed: `pip install -e .` from the project root.
If `pip` is not available, install it: `sudo apt install python3-pip`.

**"Default config not found"**
You must run `package_mgmt` commands from the project root directory (where
`project.defaults.toml` lives), or set up a symlink.

**"Index fetch failed"**
Check network connectivity to the configured mirror. You can override the
mirror URL in `project.local.toml` if you use a local or different mirror:

```toml
[[archive.source]]
mirror = "https://ftp.debian.org/debian"
release = "trixie"
components = ["main"]
```

**"Virtual package 'X' has no providers in the index"**
The name could not be resolved: either it is misspelled, does not exist in
the configured release, lives in a different component (e.g. `contrib` or
`non-free`), or it is a virtual package with no provider in any configured
source (e.g. a package only available in a newer release). Check the archive
component setting in your configuration.

**"Database error"**
Ensure you've run `package_mgmt init` at least once to create the database
schema. If the database becomes corrupted, delete it and re-run `init`.

---

## Full walkthrough

A complete session with the multi-source local config, using `PYTHONPATH` so
no install is needed. Log lines (`INFO:...`) go to stderr; the report goes to
stdout.

Init cache:

```bash
vit@bis OpenFromSource$ export PYTHONPATH="$PWD/src"
vit@bis OpenFromSource$ python3 -m package_mgmt init
INFO:package_mgmt.lib.service.initializer:Binary cache /var/cache/package_mgmt/binary (was created)
INFO:package_mgmt.lib.service.initializer:Source cache /var/cache/package_mgmt/source (was created)
INFO:package_mgmt.lib.service.db_init:Database created at /var/cache/package_mgmt/package_mgmt.db (schema v1)
Base directory:    /var/cache/package_mgmt
Binary cache:      /var/cache/package_mgmt/binary
Source cache:      /var/cache/package_mgmt/source
Database:          /var/cache/package_mgmt/package_mgmt.db
Schema version:    1
Default source:    trixie/main
Archive sources:
  [1] trixie/main — 68755 binaries, 37633 sources
  [2] trixie/contrib — 303 binaries, 159 sources
  [3] trixie/non-free — 741 binaries, 262 sources
  [4] trixie-backports/main — 3074 binaries, 849 sources
Provides entries:  69939
Status:            initialized
INFO:package_mgmt.cli.commands.init:Init completed (base_dir=/var/cache/package_mgmt, schema=v1, provides=69939, status=initialized)
```

Add package(s) metadata to database:

```bash
vit@bis OpenFromSource$ python3 -m package_mgmt add vifm
INFO:package_mgmt.lib.service.db_init:Database schema validated at /var/cache/package_mgmt/package_mgmt.db (v1)
INFO:package_mgmt.lib.service.resolver:Indexes loaded: 72873 binary packages, 38903 source packages (2 sources configured)
Package vifm resolved from trixie/main (8533 dependencies, 16 virtual)
  Error: Virtual package 'libsodium18' has no providers in the index
```

`libsodium18` is a virtual name with no provider in the configured sources
(its package lives in an older release) — the error is reported and resolution
continues (best-effort).

Inspect the resolved dependency tree (excerpt — the full tree has thousands
of nodes):

```bash
vit@bis OpenFromSource$ python3 -m package_mgmt list --deps vifm
vifm (pending)
  ├─ perl (= 5.40.1-6) [binary ✓, source ✓, verified: ✗]
  │ ├─ perl-base (= 5.40.1-6) [binary ✓, source ✓, verified: ✗]
  │ ├─ perl-modules-5.40 (= 5.40.1-6) [binary ✓, source ✓, verified: ✗]
  │ └─ libperl5.40 (= 5.40.1-6) [binary ✓, source ✓, verified: ✗]
  │   ├─ libbz2-1.0 (= 1.0.8-6) [binary ✓, source ✓, verified: ✗]
  │   │ └─ libc6 (= 2.41-12+deb13u3) [binary ✓, source ✓, verified: ✗]
  │   │   └─ libgcc-s1 (= 14.2.0-19) [binary ✓, source ✓, verified: ✗]
  │   ├─ libcrypt1 (= 1:4.4.38-1) [binary ✓, source ✓, verified: ✗]
  │   ├─ libdb5.3t64 (= 5.3.28+dfsg2-9) [binary ✓, source ✓, verified: ✗]
  │   ├─ libgdbm-compat4t64 (= 1.24-2) [binary ✓, source ✓, verified: ✗]
  │   │ └─ libgdbm6t64 (= 1.24-2) [binary ✓, source ✓, verified: ✗]
  │   ├─ zlib1g (= 1:1.3.dfsg+really1.3.1-1+b1) [binary ✓, source ✓, verified: ✗]
  ├─ libmagic1t64 (= 1:5.46-5) [binary ✓, source ✓, verified: ✗]
  │ ├─ liblzma5 (= 5.8.1-1+deb13u1) [binary ✓, source ✓, verified: ✗]
  │ └─ libmagic-mgc (= 1:5.46-5) [binary ✓, source ✓, verified: ✗]
  ├─ libncursesw6 (= 6.5+20250216-2) [binary ✓, source ✓, verified: ✗]
  │ └─ libtinfo6 (= 6.5+20250216-2) [binary ✓, source ✓, verified: ✗]
  └─ vim (= 2:9.1.1230-2) [binary ✓, source ✓, verified: ✗]
    ├─ vim-common (= 2:9.1.1230-2) [binary ✓, source ✓, verified: ✗]
    ├─ vim-runtime (= 2:9.1.1230-2) [binary ✓, source ✓, verified: ✗]
    ├─ libacl1 (= 2.3.2-2+b1) [binary ✓, source ✓, verified: ✗]
    ├─ libgpm2 (= 1.20.7-11+b2) [binary ✓, source ✓, verified: ✗]
    ├─ libselinux1 (= 3.8.1-1) [binary ✓, source ✓, verified: ✗]
    │ └─ libpcre2-8-0 (= 10.46-1~deb13u1) [binary ✓, source ✓, verified: ✗]
    └─ libsodium23 (= 1.0.18-1+deb13u1) [binary ✓, source ✓, verified: ✗]
```

Check graph completeness:

```bash
vit@bis OpenFromSource$ python3 -m package_mgmt verify
Dependency Graph Verification
─────────────────────────────
Total packages:         11420
Total binaries:         9375
Total sources:          6049
Unresolved deps:        0
Unlinked binaries:      0
Circular dependencies:  42        # informational — see above
  - libgcc-s1 -> libc6 (circular)
  - ...
Runtime Status:         COMPLETE

Build-Tool Dependencies
───────────────────────
Total build-dep links:  52953
Total build tools:      6984
Unresolved build deps:  0
Missing build tools:    0
Build Status:           COMPLETE

Status:                 COMPLETE
```

---

## Next steps

Once all required packages are resolved and their records populated:

1. **Download the payloads**: `package_mgmt download` fetches the actual
   `.deb` and source package files from the configured mirror, verifying each
   against its cached-index checksum before it becomes visible.
2. **Verify the cache**: `package_mgmt verify` validates the resolved tree —
   every runtime dependency linked to a matching version, sources and build
   tools present, no unlinked or circular defect — before shipping. (Payload
   integrity is enforced by `download`, and the handoff/diff exclude records
   whose pool file is missing.)
3. **Prepare the handoff**: `package_mgmt handoff --system <moniker>` derives
   the operator's apt source (filtered indexes, `Release`/`Release.gpg`,
   signing key, and `bootstrap.sh`) under `{binary_cache}/handoff/`, and
   records the delivery snapshot for the moniker.
4. **Transport offline**: copy the cache directory (pool + database + indexes,
   which now includes `handoff/`) onto portable media alongside a Debian LTS
   ISO. On the offline system the operator runs
   `{cache}/handoff/bootstrap.sh`, which configures apt to consume the cache
   via `file://` — no network and no `apt-offline` bundle transfer involved.

   > **Private key stays off operator media.** The handoff payload itself carries no secret key
> material — only the public `offline-cache.gpg`. The cache-local signing
> keyring lives at `{base_dir}/.gnupg-handoff/`. Because the handoff references
> the cache above it (its `pool`/`source` links point back into
> `{binary_cache}`), the transported unit is the whole cache root — so a plain
> copy of `{base_dir}` also carries the keyring. Exclude `.gnupg-handoff/` from
> operator media unless the operator is a trusted relay that must re-sign.

### Re-running the bootstrap and adopting new components

`bootstrap.sh` is **idempotent and re-runnable**: it atomically overwrites only
its own prior `/etc/apt/sources.list.d/openfromsource-cache.list` entry and
never touches other apt configuration. Re-run it whenever the handoff's
component set changes — for example when a source stack (`package-stack`) adds
the `local` component, or the handoff begins serving a new component such as
`non-free-firmware` — so `apt update` picks up the new component. To confirm,
the fetched sources line must list the component (e.g. `... main contrib
non-free non-free-firmware local`) and `apt search`/`apt-cache policy` must
resolve the expected packages.

`package_mgmt sync` and its operator-side `apply.sh` are **not** the way to
adopt a new component: they deliver incremental packages *within already
configured components* (copy pool files and replace the handoff's indexes) and
never rewrite the apt sources entry. If a component is missing from
`/etc/apt/sources.list.d/openfromsource-cache.list`, re-run `bootstrap.sh`.
