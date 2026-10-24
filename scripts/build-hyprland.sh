#!/usr/bin/env bash
#
# Build the Hyprland stack bottom-up from the pinned repos/ tree (Part 2).
#
# Compiles every component of docs/005-hyprland-source-stack.md in the exact
# dependency order of section 2.2, using the documented meson/cmake invocations
# of section 2.3, fully offline from the cache. One dependency is built from
# source (recorded in the dependency graph, Part 0): sdbus-c++ 2.0.0 (from the
# vendored subproject sources repos/xdg-desktop-portal-hyprland/subprojects/
# sdbus-cpp, before hypridle and hyprlock which require it, and before the
# portal so its meson subproject resolves against the installed system
# library).
#
# Usage:
#   build-hyprland.sh [--jobs N] [--prefix DIR] [--only NAME] [--from NAME]
#                     [--stage DIR]
#
# Options:
#   -j, --jobs N       parallel build jobs (default: nproc)
#   --prefix DIR       install prefix (default: /usr)
#   --only NAME        build just this one component
#   --from NAME        start at this component and build the rest in order
#   --stage DIR        DESTDIR-style staging mode: install each component into
#                      DIR/<component> instead of the live system, so the
#                      staged trees can be packaged into the cache `local`
#                      component by `package-stack`
#   --rebuild          wipe each component's build dir before configuring, so
#                      meson/cmake start from scratch (full clean rebuild)
#   -h, --help         show this help
#
# Preconditions (Part 1): the pinned tree in repos/ with submodules captured at
# their recorded commits. Without --stage, installs with sudo unless already
# root; with --stage, installs into DIR (no root needed). Performs no network
# access (the build host must have the network disabled).
#
# Environment:
#   REPO_DIR   directory holding the pinned sources (default: repos/)

set -euo pipefail

REPO_DIR="${REPO_DIR:-repos}"
PREFIX=/usr
JOBS="$(nproc 2>/dev/null || echo 2)"
ONLY=""
FROM=""
STAGE=""
REBUILD=0
NEXT=0

# Command prefix for privileged installs. Kept as an array so that the root
# case expands to nothing instead of an empty string that bash tries to run
# as a command name (`"" ninja ...` -> `: command not found`).
if [ "$(id -u)" -eq 0 ]; then
    SUDO=()
else
    SUDO=(sudo)
fi

usage() {
    sed -n '2,30p' "$0"
    exit 2
}

die() {
    echo "FAIL: $1" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        -j | --jobs) JOBS="$2"; shift ;;
        --prefix) PREFIX="$2"; shift ;;
        --only) ONLY="$2"; shift ;;
        --from) FROM="$2"; shift ;;
        --stage) STAGE="$2"; shift ;;
        --rebuild) REBUILD=1 ;;
        -h | --help) usage ;;
        *) echo "unknown option: $1" >&2; usage ;;
    esac
    shift
done

# Exact bottom-up build order (docs/005-hyprland-source-stack.md, section 2.2):
# protocol/wayland tooling, then companion libraries in dependency order, then
# Hyprland, then the source-built sdbus-c++, then the companion tools.
ORDER=(
    hyprland-protocols
    hyprwayland-scanner
    hyprutils
    hyprlang
    hyprgraphics
    aquamarine
    hyprcursor
    Hyprland
    sdbus-c++
    hypridle
    hyprlock
    hyprpaper
    xdg-desktop-portal-hyprland
)

SDBUS_SRC="$REPO_DIR/xdg-desktop-portal-hyprland/subprojects/sdbus-cpp"

[ -d "$REPO_DIR" ] || die "no $REPO_DIR/ directory; run the Part 1 acquisition first"

# Make --stage an absolute path so DESTDIR resolves identically from every
# component's build dir (meson installs run with the build dir as CWD, so a
# relative DESTDIR would land inside repos/<name>/build/).
if [ -n "$STAGE" ]; then
    mkdir -p "$STAGE"
    STAGE="$(cd "$STAGE" && pwd)"
fi

# The per-component DESTDIR-style install root when --stage is used. Each
# component installs into "$STAGE/<name>" (e.g. STAGE/Hyprland/usr/bin/...),
# which becomes that component's .deb data root for `package-stack`.
stage_dir() {
    [ -n "$STAGE" ] || die "internal: stage_dir called without --stage"
    mkdir -p "$STAGE/$1"
    printf '%s' "$STAGE/$1"
}

# Remove a component's previous build dir so meson/cmake configure from a
# clean slate. Only active with --rebuild; otherwise stale build dirs are
# reused (meson then reconfigures incrementally as needed).
wipe_build() {
    local build="$1"
    if [ "$REBUILD" -eq 1 ]; then
        echo "==> wiping $build"
        rm -rf "$build"
    fi
}

run_meson() {
    local name="$1"
    shift
    wipe_build "$REPO_DIR/$name/build"
    echo "==> [$name] meson setup"
    meson setup "$REPO_DIR/$name/build" "$REPO_DIR/$name" --prefix="$PREFIX" "$@"
    echo "==> [$name] ninja build"
    ninja -C "$REPO_DIR/$name/build" -j "$JOBS"
    if [ -n "$STAGE" ]; then
        echo "==> [$name] install (staged to $STAGE/$name)"
        DESTDIR="$(stage_dir "$name")" ninja -C "$REPO_DIR/$name/build" install
    else
        echo "==> [$name] install"
        "${SUDO[@]}" ninja -C "$REPO_DIR/$name/build" install
    fi
}

run_cmake() {
    local name="$1"
    wipe_build "$REPO_DIR/$name/build"
    echo "==> [$name] cmake configure"
    cmake -S "$REPO_DIR/$name" -B "$REPO_DIR/$name/build" \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX"
    echo "==> [$name] cmake build"
    cmake --build "$REPO_DIR/$name/build" -j "$JOBS"
    if [ -n "$STAGE" ]; then
        echo "==> [$name] install (staged to $STAGE/$name)"
        DESTDIR="$(stage_dir "$name")" cmake --install "$REPO_DIR/$name/build" --prefix "$PREFIX"
    else
        echo "==> [$name] install"
        "${SUDO[@]}" cmake --install "$REPO_DIR/$name/build"
    fi
}

build_sdbus_cpp() {
    local src="$SDBUS_SRC"
    [ -d "$src" ] || die "sdbus-c++ sources not found at $src (run the Part 1 submodule capture)"
    wipe_build "$src/build"
    echo "==> [sdbus-c++] cmake configure (source-built dependency 2.0.0)"
    cmake -S "$src" -B "$src/build" -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=ON -DCMAKE_INSTALL_PREFIX="$PREFIX"
    echo "==> [sdbus-c++] cmake build"
    cmake --build "$src/build" -j "$JOBS"
    if [ -n "$STAGE" ]; then
        echo "==> [sdbus-c++] install (staged to $STAGE/sdbus-c++)"
        DESTDIR="$(stage_dir "sdbus-c++")" cmake --install "$src/build" --prefix "$PREFIX"
    else
        echo "==> [sdbus-c++] install"
        "${SUDO[@]}" cmake --install "$src/build"
    fi
}

for name in "${ORDER[@]}"; do
    if [ -n "$ONLY" ] && [ "$name" != "$ONLY" ]; then
        continue
    fi
    if [ -n "$FROM" ]; then
        if [ "$NEXT" -ne 1 ] && [ "$name" != "$FROM" ]; then
            continue
        fi
        NEXT=1
    fi
    [ -d "$REPO_DIR/$name" ] || [ "$name" = "sdbus-c++" ] \
        || die "source dir $REPO_DIR/$name is missing; run the Part 1 acquisition"
    case "$name" in
        hyprland-protocols | xdg-desktop-portal-hyprland) run_meson "$name" ;;
        # hyprpm (the plugin manager) requires `glaze`, which is not packaged in
        # the cache/trixie and has no vendored fallback; it is not needed for the
        # session or the smoke test, so it is disabled for the compositor build.
        Hyprland) run_meson "$name" -Dhyprpm=disabled ;;
        sdbus-c++) build_sdbus_cpp ;;
        *) run_cmake "$name" ;;
    esac
done

if [ -n "$STAGE" ]; then
    echo "build complete: the Hyprland stack is staged at $STAGE/<component>"
else
    echo "build complete: the Hyprland stack is installed at $PREFIX"
fi
