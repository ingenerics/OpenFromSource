# Tutorial — The Backports Version-Drift Trap, and Rebuilding One Package to Keep Both Worlds

> Build it from source: when a backports upgrade collides with an exact version
> pin from `main`, OpenFromSource gets you the source, and a one-line change
> plus a small rebuild gets you *both* packages — still apt-managed.

> *DeepSeek's aforementioned sales pitch aside, it won't always be this easy. 
> Complexity grows exponentially with the number of dependencies, which can
> (quickly) end up with a broken system. The tutorials will expand on this.*

## Scenario

You are running an air-gapped fleet with OpenFromSource. The admin machine
builds a cache from `trixie/main` (the `regular_user` profile) and ships it to
the operator on a USB stick:

- the **admin** resolves and downloads `kitty`, `neovim`, `vifm`, hands off;
- the **operator** bootstraps and installs them — all from the cache;
- later the **admin** pulls `hyprland` from `trixie-backports`
  (`package_mgmt add hyprland --source trixie-backports`) and ships it with
  `package_mgmt sync`;
- the **operator** runs `apply.sh` and then:

```console
user@host ~$ sudo apt install hyprland hyprland-guiutils
...
Upgrading:
  libxkbcommon0

Installing:
  hyprland  hyprland-guiutils

REMOVING:
  kitty  libxkbcommon-x11-0

Continue? [Y/n]
```

`kitty` got removed, and `apt install kitty` now fails:

```console
Unsatisfied dependencies:
 libxkbcommon-x11-0 : Depends: libxkbcommon0 (= 1.7.0-2) but 1.13.1-1~bpo13+1 is to be installed
```

This is **the version-drift trap**. It has nothing to do with the handoff — the
handoff only ever contained `main` packages. The trap springs the moment a
second suite (`trixie-backports`) joins the operator's apt graph: a shared
library upgrades out of the range that a `main` package's exact pin allows, and
apt resolves the standoff by removing something.

## Why this specific conflict is structural

- `kitty` (main) depends on `libxkbcommon-x11-0`.
- The `main` `libxkbcommon-x11-0` carries an **exact** pin:
  `libxkbcommon0 (= 1.7.0-2)` — its X11 helper is kept in lockstep with the
  core library.
- The backports `libxkbcommon0` is `1.13.1-1~bpo13+1`. The backports
  `libxkbcommon` no longer ships a separate `libxkbcommon-x11-0` at all, so
  the *only* candidate for that package is `main`'s, which demands `1.7.0-2`.

`kitty` (main) and `hyprland` (backports) therefore **cannot coexist as stock
apt packages**: one of them must give up its dependency, or one of them must
change.

## Possible solutions

| Option | What you do | Cost |
|---|---|---|
| **Drop hyprland** | `apt remove` the backports packages, downgrade `libxkbcommon0` to `1.7.0-2`, reinstall `kitty` from main | Loses hyprland; future backports pulls will bite again |
| **Drop main kitty** | Install kitty's official portable bundle in `~/.local/kitty.app` | kitty is no longer apt-managed: no upgrades, no `dpkg` tracking, breaks the "everything in the cache" model |
| **Rebuild one package** *(this tutorial)* | Rebuild `libxkbcommon-x11-0` from the `main` source, relaxing its pin to `>= 1.7.0` so it accepts the installed `1.13.1` | One small, local rebuild; keeps both apt-managed |

We pick the third. `hyprland` keeps its backports `libxkbcommon0 1.13.1`,
`kitty` gets its `libxkbcommon-x11-0` (relaxed), nothing is removed.

> If you build the whole `libxkbcommon` source you will also produce a
> `libxkbcommon0_1.7.0-2` `.deb`. **Do not install that one** — it would
> downgrade the library and break hyprland. Install only the
> `libxkbcommon-x11-0` package.

**Why rebuild `libxkbcommon-x11-0` and not `kitty`?** kitty is a heavy build
(bundled C + Go components). `libxkbcommon` is a small Meson project that
builds in seconds, and the *only* thing standing between apt and kitty is that
one exact pin. The rebuilt X11 helper links `libxkbcommon.so.0`, whose soname
is stable across 1.7 → 1.13, so it runs correctly against the backports library
already on the system.

<details>
<summary>Deep dive</summary>

**libxkbcommon** is the core library for translating keyboard input into XKB keymaps — it's the engine that turns raw key events into what applications actually see as characters, keysyms, and modifiers.

Concretely it:

- **Compiles XKB keymaps** — takes the `xkb_keymap` descriptions (the `xkbcomp`-style data describing layouts, keycodes, symbols, and rules) and builds the in-memory keymap used at runtime.
- **Interprets events** — given a hardware keycode, it computes the resulting keysym (what key it is), the composed character, and the modifier/group state, using the layout, modifiers, and Compose rules.
- **Is the X11/Wayland-neutral keyboard layer** — it's used by both X servers, Wayland compositors (hyprland, GNOME/KDE compositors), and toolkits/terminals (kitty) so keyboard handling is identical across display servers.

So in our setup the two libraries at play are:

- **`libxkbcommon0`** — the core library (keymap compilation + interpretation). hyprland needs a recent version, hence the backports upgrade.
- **`libxkbcommon-x11-0`** — a thin helper that lets the core talk to X11's keyboard machinery (XCB + XKB). kitty links this for its X11 backend; **under Wayland it isn't strictly needed at runtime**, but kitty still *declares* it as a dependency — which is exactly why the exact-pin collision bites us.

That's also why rebuilding that one small X11 helper (rather than the core) is the sensible move: the core `libxkbcommon.so.0` soname is stable across 1.7 → 1.13, so the helper built against 1.7 runs fine against the backports 1.13.1.

</details>

---

## Step 1 — Admin: enable source fetching

The cache was built with the `regular_user` profile (`source = false`). To pull
source and build tools, switch to the `developer` settings in
`project.local.toml` (keep the `main`-only archive source):

```toml
[fetch]
source = true

[resolution]
max_source_dep_depth = 1
```

`max_source_dep_depth = 1` resolves the direct build dependencies of the
requested sources as installable binaries — enough to build them on the
operator. The runtime closure is always full, so those build tools' own
libraries come along for free.

## Step 2 — Admin: add the `libxkbcommon-x11-0` binary (source: `libxkbcommon`)

`libxkbcommon-x11-0` is a **binary** package. Its **source** package is named
`libxkbcommon`: Debian builds `libxkbcommon0`, `libxkbcommon-x11-0`,
`libxkbcommon-dev`, … all from the same `libxkbcommon` source tree. There is no
source named `libxkbcommon-x11-0` — the wrapper is changed by fetching and
modifying the `libxkbcommon` source. OpenFromSource's `add` resolves *binaries*
and links their source automatically, so ask for the binary, from `main` — not
backports:

```console
admin@host ~$ package_mgmt add libxkbcommon-x11-0
```

With `source = true` this links the binary to the `libxkbcommon` source
(1.7.0-2) and resolves the source's `Build-Depends` into installable binaries.
Inspect what the operator will need:

```console
admin@host ~$ package_mgmt list --deps libxkbcommon-x11-0 --include-build-deps
```

This tree is your authoritative list of build tools (Meson, Ninja, pkgconf,
the X11/XCB development libraries, and so on). Keep it — you'll hand it to the
operator.

> Note: `package_mgmt add libxkbcommon` would **not** work — there is no binary
> package by that name, so the resolver reports it as an unresolvable virtual
> package. The `libxkbcommon` source is pulled in only as a side effect of
> adding one of its binaries.

## Step 3 — Admin: download and verify

```console
admin@host ~$ package_mgmt download
admin@host ~$ package_mgmt verify
```

`download` fetches the build-tool binaries *and* the source files into the
separate source pool:

```
{source_cache}/pool/main/lib/libxkbcommon/libxkbcommon_1.7.0-2.dsc
{source_cache}/pool/main/lib/libxkbcommon/libxkbcommon_1.7.0.orig.tar.xz
{source_cache}/pool/main/lib/libxkbcommon/libxkbcommon_1.7.0-2.debian.tar.xz
```

## Step 4 — Admin: ship it

The `sync` diff carries **binaries only** — sources live in their own pool and
are not part of `diff/`. Ship both:

```console
admin@host ~$ package_mgmt sync --system mini-pc
```

Copy `diff/` to the USB stick, and copy the three `libxkbcommon_*` source files
from `{source_cache}/pool/main/lib/libxkbcommon/` onto the same stick (keep the
pool directory layout, e.g. under `sources/main/lib/libxkbcommon/`).

## Step 5 — Operator: apply the diff, install build deps

```console
user@host ~$ sudo apply.sh
user@host ~$ sudo apt update
```

Install the build tools from the admin's `--include-build-deps` tree — the
direct `Build-Depends` of the `libxkbcommon` source. All of them are served
from the cache:

```console
user@host ~$ sudo apt install build-essential bison debhelper dh-exec doxygen \
    flex graphviz libwayland-dev libxcb-xkb-dev libxml2-dev meson pkgconf \
    quilt wayland-protocols x11proto-dev
```


<details>
<summary>Deep dive</summary>

**`build-essential` is required even though it is not in the build-deps tree.**
`dpkg-checkbuilddeps` always demands it, for any source build, even when it is
not listed in `Build-Depends`: it is the implicit C/C++ toolchain
(`gcc`/`g++` 14, `make`, `libc6-dev`, `dpkg-dev`). That is also why it is
absent from the admin's `--include-build-deps` list — that list only covers the
package's own `Build-Depends`. `build-essential` is a per-host toolchain
install, and it is served from the cache like everything else. Skip it and the
first `dpkg-buildpackage` aborts with `unmet build dependencies:
build-essential:native`.

**Add `libwayland-dev` and `wayland-protocols`.** They are declared in
`debian/control` as `libwayland-dev [linux-any]` and
`wayland-protocols [linux-any]`. Wayland is enabled on Linux builds, and
libxkbcommon's `meson.build` errors out if `wayland-client`,
`wayland-protocols` and `wayland-scanner` are missing — so they must be
present even though they're test-adjacent tooling. All wayland libs are main
`1.23.1-3`, so they install cleanly next to hyprland's backports mesa.

**Drop `xvfb`** — and note why. `xvfb`, `x11-xkb-utils` and `xkb-data` are all
declared in `debian/control` with the `<!nocheck>` profile annotation: they
exist **only for the test suite**. `xvfb` in particular drags in the whole
GL/mesa stack (`xvfb` → `libgl1` → `libglx0` → `libglx-mesa0` →
`mesa-libgallium (= 25.0.7-2+deb13u1)`), which collides with hyprland's
backports `mesa-libgallium 26.1.2-1~bpo13+1` — the same version-drift trap
again, one level down. We don't need it: we'll build with the `nocheck`
profile (Step 8), which skips the tests and makes all three `<!nocheck>`
build-deps unrequired. `x11proto-dev` stays — it's a genuine build dep.

Two names you might expect are served indirectly:

- **`pkg-config`** — that's `pkgconf` in the list. Debian's `pkgconf` package
  *provides* the virtual `pkg-config`, so installing `pkgconf` gives you
  `pkg-config` on the operator.
- **`ninja-build`** — not a direct `Build-Depends` of `libxkbcommon`, but
  `meson` hard-depends on `ninja-build (>= 1.6)`, so `apt install meson`
  pulls it in automatically.

> If any of these is not yet `verified` in the cache (check the tree above —
> e.g. `debhelper` right after a fresh `add`), re-run `package_mgmt download`
> on the admin side before `sync`.

> `apt build-dep` needs a `deb-src` line, which the cache repo does not expose.
> That's fine — the developer resolution already turned every build dependency
> into an ordinary binary package, so you just install it by name.

</details>

## Step 6 — Operator: unpack the source

```console
user@host ~$ mkdir -p ~/src && cd ~/src
user@host ~/src$ cp /media/usb/sources/main/lib/libxkbcommon/* .
user@host ~/src$ dpkg-source -x libxkbcommon_1.7.0-2.dsc
user@host ~/src$ cd libxkbcommon-1.7.0
```

## Step 7 — Operator: relax the pin

Open `debian/control` and find the `libxkbcommon-x11-0` stanza. Its `Depends`
pins the core library to the exact version (the `${binary:Version}` expands to
`1.7.0-2` at build time — that's the `(= 1.7.0-2)` in the original apt error).
Change:

```
Depends:
 libxkbcommon0 (= ${binary:Version}),
 ${shlibs:Depends},
 ${misc:Depends}
```

to:

```
Depends:
 libxkbcommon0 (>= 1.7.0),
 ${shlibs:Depends},
 ${misc:Depends}
```

The `>= 1.7.0` constraint is satisfied by the installed backports `1.13.1`,
while keeping the guarantee that the helper and core come from the same API
generation. Because the pin lives in `debian/control`, this one-line source
edit is the whole fix — the built `.deb` already carries the relaxed
dependency.

## Step 8 — Operator: build and install only the X11 helper

```console
user@host ~/src/libxkbcommon-1.7.0$ DEB_BUILD_OPTIONS=nocheck DEB_BUILD_PROFILES=nocheck \
    dpkg-buildpackage -us -uc -b
user@host ~/src/libxkbcommon-1.7.0$ cd ..
user@host ~/src$ sudo apt install ./libxkbcommon-x11-0_1.7.0-2_amd64.deb
```

`-us -uc -b` skips signing and builds binary packages only. The `nocheck`
profile skips the test suite (which is what `xvfb` and the other `<!nocheck>`
build-deps were for), so the build works without the GL/mesa stack — no
downgrade of hyprland's backports `mesa-libgallium`. Because the helper's
dependency now allows `>= 1.7.0`, apt is happy with the installed `1.13.1` and
does **not** downgrade anything. Leave `libxkbcommon0_1.7.0-2_amd64.deb`
uninstalled — installing it would break hyprland.

## Step 9 — Operator: install kitty

```console
user@host ~/src$ sudo apt install kitty
```

kitty's `libxkbcommon-x11-0 (>= 0.5.0)` dependency is satisfied by the rebuilt
package, which in turn accepts the backports `libxkbcommon0`. kitty comes back,
hyprland stays, and both remain apt-managed.

## Step 10 — Verify

```console
user@host ~/src$ kitty --version
user@host ~/src$ dpkg -l | grep -E 'kitty|libxkbcommon'
user@host ~/src$ apt-cache policy libxkbcommon0 libxkbcommon-x11-0
```

Expect: `kitty` and `libxkbcommon-x11-0` installed; `libxkbcommon0` still at
`1.13.1-1~bpo13+1` (backports). Log in to the hyprland session and open kitty
under Wayland to confirm end to end.

## Caveats and follow-ups

- **Pin the local package.** The cache's own index still offers the stock,
  exact-pinned `libxkbcommon-x11-0`. If apt ever reinstalls it (e.g. after a
  broken state), kitty breaks again. Hold the local build so it isn't replaced:
  ```console
  user@host ~/src$ sudo apt-mark hold libxkbcommon-x11-0
  ```
- **Record the divergence.** The rebuilt helper is deliberately not
  byte-identical to Debian's. Keep the patched `debian/control` (or the `.deb`)
  alongside the cache so the divergence is reproducible and auditable — this
  is the "you don't have to trust what you can't rebuild" promise in action.
- **The trap is not about repo layout — it's about shared libraries.** All
  configured suites merge into one operator apt graph, and the goal is
  precisely to have backports and main coexist on that same graph, so "keep
  them in a separate repo" is not a fix — it only changes how they get
  delivered, not how apt resolves them together. The real precondition is a
  backports package that drags a *shared library* (here `libxkbcommon0`) to a
  newer version than a `main` package pins exactly (`libxkbcommon-x11-0`).
  App-level backports (leaves like hyprland) are usually harmless; a
  library-level upgrade is the one-way door — once the shared library moves,
  every `main` package that pins it exactly must follow, via a rebuild like
  this one.
- **Detect it before you ship it.** On the admin side, `add` resolves a
  backports package's closure with version constraints honored — inspect it for
  shared libraries that upgrade past exact pins already in the cache
  (`package_mgmt list --deps hyprland`, cross-check the `= ` pins in the
  existing tree). This is a natural place for an OpenFromSource guard: warn
  when a backports add would upgrade a library that an existing `main` package
  pins exactly.
- **Pinning won't save you.** `/etc/apt/preferences.d/` only picks *which*
  candidate wins among available versions; it cannot stop apt from installing
  the backports `libxkbcommon0`, because hyprland's `>= 1.13.1` dependency is
  satisfiable only by that version. A low backports priority prevents
  *unrequested* upgrades; it does not override hard dependencies.
- **`apt upgrade` may offer the stock helper later.** The `hold` above covers
  that; if you ever drop hyprland, remove the hold and the stock package
  restores the clean `main` state.

## Summary

1. The conflict is an exact version pin from `main` colliding with a shared
   library upgraded from `backports` — a structural property of mixing suites,
   not of the handoff.
2. The cheap exits (drop hyprland, or drop apt-managed kitty) each sacrifice
   something you want.
3. With OpenFromSource's `developer` profile, the source and build tools ride
   the same USB as the binaries; the operator unpacks `libxkbcommon`, relaxes
   one dependency in `debian/control`, builds the single X11 helper, and
   installs it.
4. Result: `kitty` and `hyprland` coexist, both apt-managed, one small and
   documented local rebuild.
