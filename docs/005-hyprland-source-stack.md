# Build the Hyprland Stack from the Cache

How-to for the full source-stack workflow: **align** (choose the version set)
→ **acquire** (pin the sources) → **build** (compile offline from the cache) →
**functional setup** (a working Hyprland session) → **validate**. The workflow
is driven by the machine-checkable version manifest produced by
`package_mgmt align-repo`, and every step is reproducible from this document
and the cache alone.

Two people run these steps:

- **Administrator** — networked system: determines the aligned era, acquires
  the pinned sources into `repos/`, builds and validates the stack against the
  cache.
- **Operator** — offline system: consumes the cache (and, per the distribution
  model, the built artifacts) and reaches a functional Hyprland setup using
  only cached packages.

The current aligned era recorded for this document is **0.49**; the manifest is
`manifests/hyprland-0.49.toml`.

---

## Part 0 — The alignment gate and what it decided

`package_mgmt align-repo` (admin-only) scans the acquired repos, walks the
release eras newest-first, and selects the **newest era whose every declared
build- and runtime-dependency requirement is satisfied** — the cache tier
against the frozen trixie/main tree, the ecosystem tier against the versions
the selected sources provide. It writes the version manifest on success and
refuses (exit 1, naming the unmet requirements) when no era aligns.

For the current frozen tree the gate's verdict is:

- **Declared-dependency verdict (search mode): 0.52.** Every declared
  build/runtime dependency requirement of eras 0.44–0.52 is satisfied by the
  frozen trixie tree, so `align-repo`'s automated search selects **0.52**.
- **Build-verified era: 0.49** (Hyprland `v0.49.0`, meson). Eras 0.50–0.52
  **fail at compile time** with the frozen toolchain — a class of requirement
  the declared-dependency gate cannot see (see "Toolchain and library drift"
  below). The era is therefore **manually pinned at 0.49** in the manifest;
  the gate's search result is a *declared*-dependency verdict, not a
  *buildable* one.

- **Eras 0.53–0.56 are rejected** — the newest eras migrated Hyprland to CMake
  and raised system-library minimums the frozen tree does not carry:

  | Requirement (era 0.53–0.56) | Frozen trixie | Verdict |
  |---|---|---|
  | `xkbcommon>=1.11.0` | `libxkbcommon-dev 1.7.0-2` | drift |
  | `wayland-protocols>=1.45` / `>=1.49` | `wayland-protocols 1.44-1` | drift |
  | `libinput>=1.29` (era 0.55+) | `libinput-dev 1.28.1-1+deb13u1` | drift |
  | `lua>=5.5` (era 0.55+) | not packaged in trixie | drift |

  These are precisely the version-drift cases the alignment check exists to
  prevent: the stack is built against the frozen trixie tree, not by dragging
  newer system libraries (or backports) into it.

- **Toolchain and library drift (build-time; why 0.49 and not 0.50+).** The
  gate reads declared dependencies, so it cannot see requirements that live in
  the source itself. Building era 0.52 exposed several such requirements, all
  of which the frozen tree cannot satisfy without drift:

  | Requirement (eras 0.50–0.52) | Frozen tree | Where it surfaces | Verdict |
  |---|---|---|---|
  | gcc-15 `#embed` (C++26) | gcc-14 14.2.0 lacks it | `src/config/defaultConfig.hpp` (0.50+) | toolchain drift |
  | gcc-15 `std::string + std::string_view` (P2591) | gcc-14 14.2.0 lacks it | `hyprctl/main.cpp` (0.50+) | toolchain drift |
  | `libxkbcommon>=1.8` API | `libxkbcommon-dev 1.7.0-2` | `src/devices/IKeyboard.cpp` (`XKB_KEYMAP_FORMAT_TEXT_V2`, `xkb_keymap_new_from_names2`) — 0.52 only | library drift |
  | `?:` operands `CXCBConnection` vs `nullptr_t` | genuine type error, 0.50+ | `src/xwayland/XWM.hpp:217` | upstream bug |

  Hyprland v0.50.0 (2025-07-16) migrated the stack to C++26 features that need
  GCC 15 (released 2025-05, not in the frozen tree); Hyprland v0.52.0
  additionally calls libxkbcommon 1.8 APIs. Era **0.49** (2025-05-08) predates
  all of these — it targets `-std=c++26` but uses only the features gcc-14
  implements, and needs only the xkbcommon 1.7 API — so it is the newest era
  that builds on the frozen gcc-14 + xkbcommon-1.7 tree. See "Tooling
  follow-up" in Caveats for making the gate express this constraint.

- **Source-built dependencies** (the graph is not closed by trixie alone):

  - `sdbus-c++` is required by hypridle (`>=0.2.0`) and hyprlock (`>=2.0.0`) and
    is **not packaged in trixie/main or trixie-backports**. Per the dependency
    graph rule (FR-014) it is built from source as a recorded dependency at
    version **2.0.0** — it is not a blocker, it is part of the build. Its
    sources are captured offline by the acquisition step:
    `xdg-desktop-portal-hyprland` **v1.3.9** vendors the sdbus-c++ sources as
    the submodule `subprojects/sdbus-cpp` at tag **v2.0.0**.

  `wayland-protocols` is **not** a source-built dependency in this era:
  Hyprland **v0.49.0** requires `wayland-protocols>=1.43` (in its
  `protocols/meson.build`), which the frozen trixie tree's
  `wayland-protocols 1.44-1` satisfies unchanged — the apt package is used.

  **Known limitation of the alignment check — demonstrated.** The gate reads
  only *declared* dependency requirements; it cannot see requirements that
  live in the source itself (compiler features, library API usage). Era 0.52
  passed the declared-dependency gate but failed at compile time for exactly
  that reason (the toolchain/library drift table above). The build is therefore
  the final arbiter: a configure- or compile-time failure is a failed era, not
  a silent drift, and the era is pinned to the newest era the build verifies.
  Making the gate build-aware (a recorded follow-up, Caveats) would let it
  express this constraint itself.

`align-repo` records the choice in the manifest, and `align-repo --verify
<manifest>` re-validates it against the *current* cache and `repos/` whenever
the tree or the cache changes (see Part 4).

---

## Part 1 — Acquisition: pin the sources into `repos/` (FR-006)

The acquisition is a documented procedure driven by the version manifest. It
downloads each selected repository into `repos/` at the project root, checked
out at the manifest's commit SHA with submodules at their recorded commits, so
that **no network access is required to build thereafter**.

> Authenticity is SHA pinning only (trust on first use). The acquired sources
> are authenticated by verifying `HEAD` equals the manifest SHA and submodules
> sit at their recorded commits; **no GPG signature verification** of tags or
> commits is performed. The pin makes any post-acquisition tampering detectable
> at verify time.

### 1.1 Prerequisites

- The project installed (`pip install -e .`) and a populated cache
  (`package_mgmt init` / existing cache).
- A network connection (Administrator side only; this is the last step that
  needs one).
- The 12 in-scope repositories (Hyprland + the lockstep companions; the four
  niche tools — hyprpicker, hyprpolkitagent, hyprland-qtutils, hyprsunset —
  are out of scope and never acquired). The only source-built dependency is
  `sdbus-c++` at `2.0.0` (Part 0), whose sources ride the cache via the
  `xdg-desktop-portal-hyprland` submodule `subprojects/sdbus-cpp`.

### 1.2 Clone each repository into `repos/`

From the project root:

```bash
mkdir -p repos
for r in Hyprland hyprwayland-scanner hyprlang hyprutils hyprcursor \
         hyprgraphics aquamarine hyprland-protocols hyprpaper \
         hypridle hyprlock xdg-desktop-portal-hyprland; do
  git clone --quiet "https://github.com/hyprwm/$r" "repos/$r"
done
```

Full clones (with tags) are used so the era search can read the release tags;
`repos/` is git-ignored and reproducible from the manifest at any time.
`wayland-protocols` is **not** acquired: era 0.49 is satisfied by the frozen
tree's `wayland-protocols 1.44-1` apt package (Part 0).

### 1.3 Produce or refresh the version manifest

```bash
package_mgmt align-repo --repo-dir repos/          # search: newest satisfying era
package_mgmt align-repo --verify manifests/hyprland-0.49.toml --repo-dir repos/
```

The recorded manifest is `manifests/hyprland-0.49.toml` (git-tracked). Note
that the automated **search** mode still reports the *declared*-dependency
verdict (currently era 0.52 — every declared requirement of 0.50–0.52 is
satisfied), while the **build-verified** era is pinned at 0.49 (Part 0): the
gate cannot see the compile-time toolchain/library requirements that reject
0.50+, so the 0.49 manifest is the tracked, authoritative one. Do not let a
search run overwrite it — the build is the final arbiter of an era.
`--verify` re-validates the recorded 0.49 manifest against the current cache
and `repos/`: **Expected** `result: PASS`, exit 0. If the tree or cache
changed such that even era 0.49 no longer aligns, `--verify` reports `FAIL`
naming the unmet requirements — do not proceed to build until it passes.

### 1.4 Pin each repository to its manifest commit

```bash
manifest=$(ls manifests/hyprland-*.toml)
for d in repos/*/; do
  name=$(basename "$d")
  commit=$(python3 -c "import sys,tomllib;[print(r['commit']) for r in tomllib.load(open(sys.argv[1],'rb'))['repo'] if r['name']=='$name']" "$manifest")
  git -C "$d" checkout --detach "$commit"
done
```

or check out the manifest tags directly (`v0.49.0`, `v0.8.0`, …). Each tree is
now at the exact pinned commit.

### 1.5 Capture submodules at their recorded commits

Some pinned refs vendor submodules (Hyprland `v0.49.0` vendors
`hyprland-protocols`, `tracy` and `udis86`; `xdg-desktop-portal-hyprland`
`v1.3.9` vendors `hyprland-protocols` and `sdbus-cpp` — the latter at tag
`v2.0.0`, the source-built dependency):

```bash
for d in repos/*/; do git -C "$d" submodule update --init --recursive; done
```

A missing submodule is a **failed acquisition**, not a build-time surprise —
the tree must be complete and buildable offline as-is.

### 1.6 Verify integrity (quickstart Phase 3)

```bash
# every repos/<name> HEAD equals the manifest commit
for d in repos/*/; do echo "$(basename "$d"): $(git -C "$d" rev-parse HEAD)"; done
# submodules at their recorded commits
git -C repos/Hyprland submodule status
git -C repos/xdg-desktop-portal-hyprland submodule status
# the source-built dependency (sdbus-cpp) is at its recorded tag v2.0.0
git -C repos/xdg-desktop-portal-hyprland/subprojects/sdbus-cpp describe --tags --exact-match
# the gate agrees
package_mgmt align-repo --verify manifests/hyprland-0.49.toml --repo-dir repos/
```

**Expected**: each `HEAD` equals the manifest SHA; submodules present and
pinned; `repos/xdg-desktop-portal-hyprland/subprojects/sdbus-cpp` is at tag
`v2.0.0`; `--verify` reports `result: PASS`, exit 0.

**A mismatched tree is refused.** If any `repos/<name>` `HEAD` differs from the
manifest commit (a corrupt or incomplete acquisition), `align-repo --verify`
exits 102 and names the mismatch — it never validates a partial tree:

```text
{"error": "repo hyprutils HEAD (a21e87b878e7) does not match manifest commit (926689ddb9c0)", "exit_code": 102}
```

Re-run the acquisition (steps 1.2–1.5) to restore the pinned tree.

> `repos/` is git-ignored; the manifest (git-tracked) plus this procedure is
> the reproducible recipe for the pinned tree.

---

## Part 2 — Build the core stack bottom-up from the cache (FR-007/008/009/014)

The stack is compiled **bottom-up, fully offline, with the cache as the sole
package and source provider**, at the cached versions. Every component builds
against its already-built dependencies; nothing is fetched at build time.

### 2.1 Preconditions

- The acquired, pinned tree from Part 1.
- The network **disabled** on the build host.
- The build toolchain installed from the cache (all cached at the frozen
  versions): `meson` 1.7.0, `ninja` 1.12.1, `cmake` 3.31.6, `gcc`/`g++`
  (`gcc-14` 14.2.0) and `build-essential`, plus the development libraries the
   stack needs (wayland 1.23.1, libdrm 2.4.124, mesa/GBM 25.0.7, glslang
   15.1.0, libinput 1.28.1, libxkbcommon 1.7.0, and the rest of the frozen
   tree). The `developer` profile (`[fetch] source = true`) and
   `package_mgmt download` on the admin side ensure the build tools and source
   ride the same cache as everything else.

All of the listed toolchain and development packages in one command:

```bash
sudo apt install meson ninja-build cmake gcc g++ build-essential \
    libwayland-dev libdrm-dev libgbm-dev glslang-dev libinput-dev \
    libxkbcommon-dev libglib2.0-dev libpango1.0-dev libcairo2-dev \
    libpixman-1-dev libxcursor-dev uuid-dev libeis-dev liblcms2-dev \
    libcanberra-dev libre2-dev libmuparser-dev wayland-protocols xwayland \
    libseat-dev libdisplay-info-dev libtomlplusplus-dev libsystemd-dev \
    libspdlog-dev libfmt-dev nlohmann-json3-dev \
    libxcb1-dev libxcb-render0-dev libxcb-xfixes0-dev libxcb-icccm4-dev \
    libxcb-composite0-dev libxcb-res0-dev libxcb-errors-dev libudev-dev hwdata libzip-dev \
    librsvg2-dev libjpeg-dev libwebp-dev libmagic-dev libjxl-dev libspng-dev \
    libpugixml-dev libegl-dev libopengl-dev libgles-dev libpng-dev libpam0g-dev \
    qt6-base-dev libpipewire-0.3-dev libspa-0.2-dev
```

(`ninja-build` is named explicitly even though `meson` pulls it in; `gcc`/`g++`
are implied by `build-essential`. Every package here resolves from the cache at
the frozen trixie/main versions. Deliberately absent: `libsdbus-c++-dev`,
`liblua5.5-dev` and `hyprland-protocols` — the first two are not packaged in
trixie (sdbus-c++ is built from source as the recorded dependency, lua 5.5 is
not needed by era 0.49), and `hyprland-protocols` is built from source as
component 1 of the stack. `wayland-protocols` **is** used from apt at 1.44-1:
era 0.49 requires only `>=1.43` (Part 0). `libglaze-dev` is deliberately
absent: Hyprland's `hyprpm` requires `glaze`, which is not packaged in trixie,
so `hyprpm` is disabled at configure time (`-Dhyprpm=disabled`, §2.3) and no
glaze package is needed. Conversely `libxcb-errors-dev` is **required**:
Hyprland's XWayland path uses the xcb-errors API unconditionally even though meson
declares it `required: auto` — without it the configure passes but the XWM compile
fails. `qt6-base-dev` and `libpipewire-0.3-dev` are **required** by
`xdg-desktop-portal-hyprland`: its share-picker builds unconditionally (Qt Widgets)
and the portal links `libpipewire-0.3` unconditionally. `libspa-0.2-dev` (the
PipeWire SPA headers the portal's `libspa-0.2` check needs) is pulled in by
`libpipewire-0.3-dev`; it is listed explicitly so the apt command is
self-contained. `libspng-dev` is
**required** by hyprgraphics **v0.1.3** (it pkg-checks `spng` unconditionally;
era 0.52's hyprgraphics v0.3.0 dropped the requirement — which is why it never
appeared in the earlier build). `spng` **is** packaged in trixie (0.7.4), so it
is a cache dependency, not a source-built one: the alignment gate lists it
among its source-built providers (`SOURCE_BUILT_PROVIDED`), but like
`xcb-errors` it is actually installed from the cache — add `libspng-dev` and
`libspng0` to the cache if not already present.)

> **Missing build helper.** Per the dependency-graph rule, any build tool or
> helper the stack requires that is missing from the cache **must be added to
> the cache and recorded in the dependency graph before building** — never
> silently supplied from outside the cache. The source-built dependencies
> (`sdbus-c++` and `wayland-protocols`) and their versions are recorded this
> way (Part 0).

### 2.2 Build order (FR-008)

Each component is compiled in place and installed (in-place installation is the
delivery model for this feature; packaging into the cache `local` component is
the documented follow-up). The order is bottom-up: protocol/wayland tooling
first, then the companion libraries in dependency order, then Hyprland, then
the companion tools.

| Order | Component | Build system | Depends on | What it provides |
|---|---|---|---|---|
| 1 | `hyprland-protocols` | meson | — | protocol headers, `hyprland-protocols.pc` |
| 2 | `hyprwayland-scanner` | cmake | — | build-time codegen tool (scanner) |
| 3 | `hyprutils` | cmake | — | core utilities (depended on by most) |
| 4 | `hyprlang` | cmake | 3 (`hyprutils`) | config language |
| 5 | `hyprgraphics` | cmake | 3 (`hyprutils`) | image/shared rendering helpers |
| 6 | `aquamarine` | cmake | 2, 3 | rendering/backend abstraction |
| 7 | `hyprcursor` | cmake | 4 (`hyprlang`) | cursor themes |
| 8 | `Hyprland` | meson | 3–7, 2, 1 | the compositor |
| 9 | `sdbus-c++` (source-built dep) | cmake | — | D-Bus binding for the companions |
| 10 | `hypridle` | cmake | 9, 4, 3, 2, 1 | idle daemon |
| 11 | `hyprlock` | cmake | 9, 5, 4, 3, 2 | screen locker |
| 12 | `hyprpaper` | cmake | 5, 4, 3, 2 | wallpaper daemon |
| 13 | `xdg-desktop-portal-hyprland` | meson | 9, 5, 4, 3, 2, 1 | desktop portal |

The "Depends on" column lists the earlier rows (by order number) whose
installed artifacts each component requires at configure/build time — the
bottom-up order guarantees every prerequisite is installed before its
dependents configure. Hyprland (row 8) is configured against the apt
`wayland-protocols` 1.44 (its `protocols/meson.build` requires `>=1.43`);
`sdbus-c++` (row 9) must precede hypridle/hyprlock and the portal (Part 0).

### 2.3 Build and install commands

The whole stack builds in order with one command:

```bash
./scripts/build-hyprland.sh                      # full bottom-up build (order 2.2)
./scripts/build-hyprland.sh --from Hyprland      # resume from a component
./scripts/build-hyprland.sh --only hyprlang      # build just one component
```

`build-hyprland.sh` compiles every component in the exact order of section 2.2
(protocols → scanner → companion libraries → Hyprland → the source-built
`sdbus-c++` → companion tools) using the documented meson/cmake invocations
below, with `sudo` installs, and fails loudly on the first error. The commands
are shown per component for reference and manual runs:

Meson components:

```bash
name=hyprland-protocols        # or xdg-desktop-portal-hyprland
meson setup "repos/$name/build" "repos/$name" --prefix=/usr
ninja -C "repos/$name/build"
sudo ninja -C "repos/$name/build" install
```

`Hyprland` is configured the same way **with `-Dhyprpm=disabled`**: its `hyprpm`
subdirectory requires `glaze` (a header-only JSON library **not packaged in the cache**,
with no vendored fallback), and `hyprpm` — the plugin manager — is not needed for the
session or the smoke test. (This is the Part 0 alignment-check limitation in practice:
the `glaze` requirement lives in the *subdirectory* `hyprpm/src/meson.build`, which the
automated gate does not scan.)

```bash
name=Hyprland
meson setup "repos/$name/build" "repos/$name" --prefix=/usr -Dhyprpm=disabled
ninja -C "repos/$name/build"
sudo ninja -C "repos/$name/build" install
```

Hyprland itself needs **no source patch** in era 0.49: unlike 0.50+ it uses
only the C++26 features gcc-14 implements and only the xkbcommon 1.7 API
(Part 0), so it builds as-is with the frozen toolchain.

> **Recorded build deviation — `xdg-desktop-portal-hyprland` v1.3.9 meson
> build bugs.** The portal's **meson** path has two omissions its
> `CMakeLists.txt` does not (both fixed upstream only later, v1.4.0):
> (1) `XDPH_VERSION` is never defined, so `src/main.cpp:32` fails to compile;
> (2) the `hyprland-share-picker` target links only Qt, not `hyprutils`, so its
> `Hyprutils::OS::CProcess` usage fails to link (the CMake target links
> `PkgConfig::deps`). Both are backported so the pinned v1.3.9 tree builds with
> meson. Apply after each acquisition (Part 1) and before the portal build:

> ```bash
> # repos/ is git-ignored and reproducible — re-apply after every acquisition
> python3 - <<'EOF'
> p = "repos/xdg-desktop-portal-hyprland/meson.build"
> s = open(p).read()
> if "XDPH_VERSION" not in s:
>     s = s.replace(
>         "conf_data = configuration_data()",
>         'version = meson.project_version()\n\n'
>         'add_project_arguments(f\'-DXDPH_VERSION="{version}"\', language : \'cpp\')\n\n'
>         'conf_data = configuration_data()',
>     )
>     open(p, "w").write(s)
> p = "repos/xdg-desktop-portal-hyprland/hyprland-share-picker/meson.build"
> s = open(p).read()
> if "dependency('hyprutils')" not in s:
>     s = s.replace(
>         "  dependencies: qtdep,",
>         "  dependencies: [qtdep, dependency('hyprutils')],",
>     )
>     open(p, "w").write(s)
> EOF
> ```

> (Unlike upstream v1.4.0 — which hard-codes the literal `"@version@"` for
> `XDPH_VERSION` — this backport passes the real version via
> `meson.project_version()`, so `-V` prints `v1.3.9`.)

> `scripts/build-hyprland.sh` applies both backports automatically (idempotently)
> before configuring the portal, so `--rebuild` and fresh acquisitions build
> without the manual step below. The snippet remains for hand-rolled builds.

CMake components (all the rest, plus the source-built `sdbus-c++`):

```bash
name=hyprwayland-scanner       # or hyprgraphics / hyprutils / hyprlang / …
cmake -S "repos/$name" -B "repos/$name/build" -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/usr
cmake --build "repos/$name/build"
sudo cmake --install "repos/$name/build"
```

The source-built `sdbus-c++` (recorded dependency, version 2.0.0) is built
before hypridle/hyprlock from the vendored sources captured by acquisition
(`repos/xdg-desktop-portal-hyprland/subprojects/sdbus-cpp`):

```bash
cmake -S repos/xdg-desktop-portal-hyprland/subprojects/sdbus-cpp \
      -B repos/xdg-desktop-portal-hyprland/subprojects/sdbus-cpp/build \
      -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON -DCMAKE_INSTALL_PREFIX=/usr
cmake --build repos/xdg-desktop-portal-hyprland/subprojects/sdbus-cpp/build
sudo cmake --install repos/xdg-desktop-portal-hyprland/subprojects/sdbus-cpp/build
```

Each component's declared minimums are validated by `align-repo` before the
build; a requirement that cannot be satisfied by the already-built components
or the cache fails loudly at alignment time, not mid-build.

### 2.4 Reproducibility (FR-009)

Builds are reproducible because inputs and environment are recorded:

- **Pinned commits** — every source is the manifest SHA (Part 1).
- **Recorded build environment** — the exact command sequence in this section,
  the frozen cache versions, and the toolchain versions recorded in the cache.
- **Recorded dependency graph** — every build helper and the source-built
  dependency (`sdbus-c++`) is recorded (FR-014), so the complete toolchain is
  reproducible offline.

**Clean-state rebuild check.** To prove reproducibility, rebuild one component
from a clean state and compare artifacts:

```bash
name=hyprlang
rm -rf "repos/$name/build-verify"
cmake -S "repos/$name" -B "repos/$name/build-verify" \
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr
cmake --build "repos/$name/build-verify"
cmp "repos/$name/build/libhyprlang.so.0" \
    "repos/$name/build-verify/libhyprlang.so.0" && echo "identical artifacts"
```

Identical inputs (pinned commit + recorded environment) must produce identical
outputs. Repeat the full build from a clean state (fresh `repos/` acquired per
Part 1, clean build dirs) and compare the installed artifacts to confirm.

---

## Part 3 — Functional setup: a working Hyprland session (FR-010/011)

After the build, the stack is installed **in place on the target directly from
the cache** (this feature's delivery model; packaging the built binaries and
their sources into the cache `local` component for offline `apt` install is the
documented follow-up).

### 3.1 Start the session

From the target user's login shell (never as root), on the chosen VT:

```bash
exec Hyprland
```

or via the session entry (`/usr/share/wayland-sessions/hyprland.desktop`) from
a display manager. The session uses only cached packages at the cached
versions; no network is required.

### 3.2 Validate with the scripted smoke test (Q9)

`scripts/validate-hyprland-session.sh` starts (optionally), asserts the
compositor and the essential companions are running, and exits pass/fail:

```bash
./scripts/validate-hyprland-session.sh                  # validate the running session
./scripts/validate-hyprland-session.sh --start --lock   # launch, lock to exercise hyprlock, check, tear down
```

The checks are:

- the compositor answers (`hyprctl version`) and the session is live
  (`hyprctl activeworkspace`);
- the essential companions are running: `hyprpaper`, `hyprlock`, `hypridle`,
  `xdg-desktop-portal-hyprland` (hyprlock is exercised while the session is
  locked — use `--lock`);
- the portal answers on the session bus.

A sample run on a healthy session:

```text
PASS: compositor answers (hyprctl version)
PASS: session is live (hyprctl activeworkspace)
PASS: companion running: hyprpaper
PASS: companion running: hyprlock
PASS: companion running: hypridle
PASS: companion running: xdg-desktop-portal-hyprland
PASS: portal answers (dbus)
result: PASS
```

Any failing check is reported by name and the script exits 1.

### 3.3 Repeatability on a fresh target (FR-010/011)

The procedure is identical on a fresh trixie target: ensure the cache is
present, install the built stack in place from the cache, start the session,
and run the smoke test. The same inputs (cache + manifest + documented
procedure) yield the same functional session — it is a repeatable procedure,
not a one-off.

---

## Part 4 — Validation and re-runnability (FR-012/013)

Every step is re-runnable:

- **Re-align** — `package_mgmt align-repo --repo-dir repos/` re-derives the era
  from the *current* cache and `repos/`. A change to either can only move the
  outcome to a newer-era rejection (an older era, or `FAIL` naming the unmet
  requirements) — it never silently keeps a stale alignment.
- **Re-validate** — `package_mgmt align-repo --verify manifests/hyprland-0.49.toml
  --repo-dir repos/` checks the recorded alignment against the current state.
- **Re-acquire** — Part 1 reproduces `repos/` from the manifest at any time.
- **Re-build** — Part 2 reproduces identical artifacts from the pinned tree.
- **Re-validate the session** — Part 3.2 on any target.

The runnable validation guide is
`specs/023-version-alignment-check/quickstart.md` (Phases 1–5).

---

## Part 5 — Packaging the stack into the cache `local` component

The in-place delivery model of Parts 1–4 is the raw build. This part packages
the **same** built stack into the cache's `local` component so the Operator
installs it with ordinary `apt` — no `dpkg -i` — while every shipped binary
stays linked to an imported, verified, rebuildable source (`binary_to_source`).

The mechanism is deliberately split between a **generic tool** and
**per-stack data**:

- **Generic (automated by `package-stack`)**: recipe parsing, binary-package
  generation from a staged install directory, automatic `Depends` derivation
  (`dpkg-shlibdeps`), deterministic source-package generation (`dpkg-source`
  from a generated minimal `debian/` wrapper), dependency-order processing,
  import + linkage, and whole-set validation. The tool contains **no
  stack-specific knowledge** and never builds anything.
- **Per-stack (declarative)**: the recipe file `recipes/hyprland.toml` (the
  component list, metadata, and build-system choices) and the staged build
  (`--stage`, §5.2).

### 5.1 The recipe — `recipes/hyprland.toml`

The recipe is the entire stack-specific input. It declares the 13 build
components of §2.2 — the 12 hyprwm repositories pinned at the aligned era plus
the source-built `sdbus-c++` 2.0.0 vendored at
`xdg-desktop-portal-hyprland/subprojects/sdbus-cpp` — each with its binary
package name, version, Debian revision, build system (`meson`/`cmake`),
architecture (`all` for data-only components such as `hyprland-protocols`),
metadata, and the runtime dependencies on other source-built components
(`explicit_depends`).

```toml
[stack]
name = "hyprland"
repo_dir = "repos"
source_epoch = "2026-10-20"

[[component]]
name = "hyprland-protocols"
binary_package = "hyprland-protocols"
source_tree = "hyprland-protocols"
version = "0.6.4"
revision = 1
build_system = "meson"
architecture = "all"
description = "Hyprland Wayland protocol headers and XML"
section = "libdevel"
homepage = "https://github.com/hyprwm/hyprland-protocols"
```

The full contract for the file format is
`specs/024-source-stack-packaging/contracts/recipe.md`.

### 5.2 Staged build — `scripts/build-hyprland.sh --stage DIR`

The build script gains a `--stage DIR` mode that installs each component into
`DIR/<component>` — a DESTDIR-style root that becomes that component's `.deb`
data root — instead of `/usr`:

```bash
scripts/build-hyprland.sh --stage staging
# installs into staging/hyprland-protocols/, staging/Hyprland/, …, staging/sdbus-c++/
```

The in-place delivery of Parts 1–4 is unchanged; `--stage` is additive and
needs no root (meson `DESTDIR=… ninja -C … install` and
`cmake --install … --destdir …`). Each staged directory is exactly the payload
of one generated package, so file ownership follows the staged install (§2.3).

### 5.3 Package + import — `package_mgmt package-stack`

`package-stack` (admin-only) consumes the recipe and the staged tree, and for
each component in dependency order (dependencies before dependents, regardless
of recipe order):

1. **`debgen`** builds a deterministic `.deb` from the staged directory: a
   complete control record (with computed `Installed-Size`), `Depends`
   **derived** from the staged binaries' linked libraries via `dpkg-shlibdeps`
   and merged with the recipe's `explicit_depends`, and any library the tool
   cannot attribute reported by name (never silently dropped — the recipe must
   declare it). `SOURCE_DATE_EPOCH` is fixed, tar order/mtimes are fixed, and
   `dpkg-deb --root-owner-group` is used, so two runs over identical inputs
   produce byte-identical `.deb`s.
2. **`srcgen`** builds the complete, rebuildable Debian source package
   (`.dsc` + referenced files) from the pinned tree via a generated minimal
   `debian/` wrapper driving the component's own meson/cmake build — the same
   invocations as §2.3 — deterministically.
3. **`import_local_deb`** imports the pair into the cache `local` component:
   re-resolves every declared dependency against the cache (frozen trixie +
   already-imported `local`), checksums the payloads, registers the rows
   database-first, sets `verified=1` only after the copy + SHA256, records the
   `binary_to_source` link, and rolls back atomically on any failure.

After the set is imported, `package-stack` runs the whole-set checks — the
`verify` semantics (no unlinked binaries, no unverified records for the set)
and a simulated offline `apt install` of the whole set against a scratch mirror
of the cache — and both must pass before success.

```bash
package_mgmt package-stack --recipe recipes/hyprland.toml
# stack: hyprland
# order: hyprland-protocols → hyprutils → … → sdbus-c++ → … → xdg-desktop-portal-hyprland
# components imported: 13/13
# Depends derived for 13 binaries (explicit deps merged; 0 unattributed libraries)
# whole-set verify: PASS (no unlinked binaries, no unverified records)
# simulated offline apt install: PASS (0 unmet dependencies)
package_mgmt package-stack --recipe recipes/hyprland.toml --format json   # stable JSON report
```

The command is idempotent: re-running over identical inputs re-imports the
identical pair once (no duplicate rows), and re-packaging the same upstream
version at changed source/recipe bumps the Debian revision so `apt upgrade`
sees the new package.

The generated artifacts are policy-clean — lintian reports **zero errors and
zero warnings** over the full 26-artifact set (13 `.deb` + 13 `.dsc`): extended
descriptions, `usr/share/doc/<pkg>/copyright` + `changelog.Debian.gz`,
`Triggers: ldconfig` + `DEBIAN/shlibs` for library packages, stripped ELF
binaries, compressed + generated man pages, `DEBIAN/conffiles` for `/etc`
files, and documented lintian overrides for the two intentional deviations
(library packages named after the upstream project rather than the ABI soname,
and no Debian BTS bugs to close for a first internal upload). The `Maintainer`
defaults to `OpenFromSource <admin_email>` from the project settings
(`[metadata] admin_email` in `project.defaults.toml`, default
`noreply@example.com`) when the recipe omits `maintainer`.

### 5.4 Operator install and source retrieval

The imported `local` set flows through the ordinary handoff/sync channel
unchanged. The handoff additionally emits a `source/Sources` index for `local`
(hash-listed and signed in the same `Release`/`Release.gpg` as the binary
index) and the bootstrap writes a `deb-src` line for `local` to the operator's
apt config, so the Operator can:

```bash
apt install hyprland hypridle hyprlock hyprpaper xdg-desktop-portal-hyprland   # all from the cache
apt-get source hyprlang        # resolves offline from local/source/Sources, dpkg-source -x succeeds
```

The retrieved source is the imported, verified set — `dpkg-source -x` unpacks
offline and the recorded rebuild (§2.3 invocations, the generated `debian/rules`)
reproduces the shipped binary, keeping binary↔source consistency for every
shipped package. The runnable end-to-end validation guide is
`specs/024-source-stack-packaging/quickstart.md`.

---

## Caveats and follow-ups

- **Do not drift the system libraries.** The alignment gate rejects eras that
  need newer `xkbcommon`/`wayland-protocols`/`libinput`/`lua` than trixie
  carries. Extending the cache with newer *compiled* system libraries — or
  installing them from backports — is exactly the drift this workflow
  prevents; if a newer era is required, extend the frozen tree deliberately
  and record the change. This rule is what rejected era 0.52: it calls
  `libxkbcommon` 1.8 APIs the frozen 1.7.0 tree cannot provide (Part 0).
- **`sdbus-c++` is the only source-built dependency.** It is built from source
  at its recorded version (`2.0.0`) and recorded in the dependency graph
  (FR-014). It is not in trixie/main or trixie-backports; its sources ride the
  cache via the `xdg-desktop-portal-hyprland` **v1.3.9** submodule
  (`subprojects/sdbus-cpp`, tag `v2.0.0`). `wayland-protocols` is **not**
  source-built in era 0.49 — trixie's 1.44 satisfies Hyprland v0.49's
  `>=1.43` (Part 0).
- **Tooling follow-up — build-aware era selection.** `align-repo`'s search
  mode selects on *declared* dependency requirements only. Eras 0.50–0.52
  pass that gate yet fail at compile time on the frozen toolchain (gcc-15
  C++26 features, and xkbcommon 1.8 for 0.52), so the era is manually pinned
  at 0.49 in `manifests/hyprland-0.49.toml`. A recorded follow-up: make the
  gate express the build-time constraint (e.g. a `--max-era` override or a
  build-verified-era constraint) so the manual pin becomes machine-checkable
  and search mode cannot silently re-select a build-incompatible era.
- **Recorded build deviation — portal v1.3.9 meson build bugs.** The portal's
  meson build omits the `XDPH_VERSION` define (CMake-only upstream) and does
  not link `hyprutils` into the `hyprland-share-picker` target; §2.3 records
  the two backported one-line fixes to re-apply after every acquisition. They
  are upstream meson-build bugs (fixed only in v1.4.0), invisible to the
  declared-dependency gate — another case where the build is the final
  arbiter (see Alignment-check follow-up).
- **Alignment-check follow-up.** `align-repo` reads only each repo's top-level
  build file, so requirements in *subdirectories* (Hyprland's
  `protocols/meson.build` `wayland-protocols>=1.43`) are not machine-checked;
  they are recorded in Part 0 and validated by the build. Era 0.52
  demonstrated the deeper gap — requirements that live in the *source itself*
  (compiler features, library APIs) are invisible to any declared-dependency
  check; the build is the final arbiter (Part 0).
- **Packaging.** The built stack is packaged into the cache `local` component
  for offline `apt` install — see Part 5 (`package-stack`, the
  `recipes/hyprland.toml` recipe, and the staged-build `--stage` mode). Every
  shipped binary is linked via `binary_to_source` to an imported, verified,
  rebuildable source, so it can be rebuilt offline (`apt-get source` +
  `dpkg-source -x` + the recorded rebuild).

## Summary

1. `align-repo` selects the newest era on *declared* requirements (currently
   0.52), but the **build-verified** era is **0.49** — the newest era the
   frozen gcc-14 + xkbcommon-1.7 tree can compile (0.50+ need gcc-15 features,
   and 0.52 additionally needs libxkbcommon 1.8). The era is manually pinned
   in `manifests/hyprland-0.49.toml`; newer eras are rejected for drift
   (declared or build-time), and `sdbus-c++` is built from source as the one
   recorded dependency.
2. Acquisition pins all 12 repos and their submodules — including the
   source-built `sdbus-c++` via the portal's `sdbus-cpp` submodule — to their
   pinned commits, SHA-pinned (TOFU), after which the tree is fully offline.
3. The stack builds bottom-up from the cache alone, reproducibly.
4. A functional Hyprland session is validated by the scripted smoke test,
   repeatably on a fresh target.
5. The built stack is packaged into the cache `local` component by the generic
   `package-stack` command from the declarative recipe and the staged build,
   so the Operator installs it with ordinary `apt` — offline, no `dpkg -i` —
   and every shipped binary is linked to an imported, verified, rebuildable
   source (Part 5).
