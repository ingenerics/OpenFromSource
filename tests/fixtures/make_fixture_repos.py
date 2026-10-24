#!/usr/bin/env python3
"""Build small, real git repos for the alignment-check test suites.

The alignment command reads build metadata from checked-out git trees, so the
fixtures must be actual git repositories: several tags per repo (a common era
prefix plus one newer, non-aligning era), a `meson.build` or `CMakeLists.txt`,
and a `VERSION` file. They are assembled here without external tooling beyond
`git`; the caller owns the target directory.

Repo layout produced (one git repo each):
  Hyprland              anchor repo; meson in the two older eras, CMake in the
                        newest era which declares unsatisfiable requirements
  hyprutils             companion (meson), VERSION 0.14.0
  hyprwayland-scanner   companion (meson), VERSION 0.4.4
  hyprland-protocols    companion (meson), VERSION 0.6.0

All four repos carry the tags v0.44.0, v0.45.0 and v0.46.0, so candidate eras
are enumerated newest-first as 0.46, 0.45, 0.44. Only v0.45.0 (and older)
satisfy the fixture baseline; v0.46.0 declares cache-tier requirements no
fixture cache satisfies.
"""

import os
import subprocess

REPO_NAMES = ("Hyprland", "hyprutils", "hyprwayland-scanner", "hyprland-protocols")
ERAS = ("0.44", "0.45", "0.46")

# The era the fixture set is expected to select (the newest satisfying one).
SELECTED_ERA = "0.45"

# Expected requirement counts for the selected era (for assertions).
CACHE_TIER_COUNT = 3
ECOSYSTEM_TIER_COUNT = 3

# Companion provided versions (from each repo's VERSION file at the pinned tag).
COMPANION_VERSIONS = {
    "hyprutils": "0.14.0",
    "hyprwayland-scanner": "0.4.4",
    "hyprland-protocols": "0.6.0",
}

# The modules Hyprland's satisfying eras declare as ecosystem-tier requirements.
ECOSYSTEM_MODULES = ("hyprutils", "hyprwayland-scanner", "hyprland-protocols")


def _run(cwd: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def _commit(cwd: str, message: str) -> None:
    _run(cwd, "add", "-A")
    _run(cwd, "commit", "-m", message)


def _init_repo(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    _run(path, "init", "-q")
    _run(path, "checkout", "-q", "-b", "main")
    _run(path, "config", "user.name", "Fixture")
    _run(path, "config", "user.email", "fixture@example.invalid")


def _write(path: str, name: str, content: str) -> None:
    with open(os.path.join(path, name), "w", encoding="utf-8") as f:
        f.write(content)


MESON_DEP_LINES = (
    "wl_server = dependency('wayland-server', version: '>=1.22')\n"
    "libinput_dep = dependency('libinput', version: '>=1.28')\n"
    "xkbcommon_dep = dependency('xkbcommon', version: '>=1.7')\n"
    "hyprutils_dep = dependency('hyprutils', version: '>=0.14.0')\n"
    "hwscanner_dep = dependency('hyprwayland-scanner', version: '>=0.3.10')\n"
    "protocols_dep = dependency('hyprland-protocols', version: '>=0.6.0')\n"
)

CMAKE_REQUIREMENTS = (
    "set(LIBINPUT_MINIMUM_VERSION 1.29)\n"
    "find_package(hyprwayland-scanner 0.4.0 REQUIRED)\n"
    "pkg_check_modules(deps REQUIRED IMPORTED_TARGET GLOBAL\n"
    "  libinput>=${LIBINPUT_MINIMUM_VERSION}\n"
    "  wayland-protocols>=1.49\n"
    "  xkbcommon>=1.11.0\n"
    "  hyprutils>=0.14.0)\n"
    "find_package(glaze CONFIG REQUIRED)\n"
)


def _hyprland_meson(version: str) -> str:
    return f"project('Hyprland', version: '{version}')\n" + MESON_DEP_LINES


def _hyprland_cmake() -> str:
    return (
        "cmake_minimum_required(VERSION 3.25)\n"
        "project(Hyprland VERSION 0.46.0)\n"
        + CMAKE_REQUIREMENTS
    )


def _companion_meson(name: str, version: str) -> str:
    return f"project('{name}', version: '{version}')\n"


def _build_hyprland(path: str) -> None:
    _init_repo(path)
    _write(path, "VERSION", "0.44.0\n")
    _write(path, "meson.build", _hyprland_meson("0.44.0"))
    _commit(path, "era 0.44")
    _run(path, "tag", "v0.44.0")

    _write(path, "VERSION", "0.45.0\n")
    _write(path, "meson.build", _hyprland_meson("0.45.0"))
    _commit(path, "era 0.45")
    _run(path, "tag", "v0.45.0")

    _write(path, "VERSION", "0.46.0\n")
    _write(path, "CMakeLists.txt", _hyprland_cmake())
    os.remove(os.path.join(path, "meson.build"))
    _commit(path, "era 0.46")
    _run(path, "tag", "v0.46.0")


def _build_repo(name: str, root: str) -> None:
    path = os.path.join(root, name)
    if name == "Hyprland":
        _build_hyprland(path)
        return
    version = COMPANION_VERSIONS[name]
    _init_repo(path)
    _write(path, "VERSION", f"{version}\n")
    _write(path, "meson.build", _companion_meson(name, version))
    _commit(path, "initial")
    for era in ERAS:
        _run(path, "tag", f"v{era}.0")


def build_fixture_repos(root: str) -> dict[str, str]:
    """Create the fixture git repos under `root` and return name -> repo path."""
    created: dict[str, str] = {}
    for name in REPO_NAMES:
        _build_repo(name, root)
        created[name] = os.path.join(root, name)
    return created


def build_failing_only_repo(root: str) -> str:
    """A single-repo source set whose only era cannot align (the CMake era).

    Used for the search-failure path: the sole candidate era declares
    cache-tier requirements no fixture cache satisfies, so the search must
    fail and name them.
    """
    path = os.path.join(root, "Hyprland")
    _init_repo(path)
    _write(path, "VERSION", "0.46.0\n")
    _write(path, "CMakeLists.txt", _hyprland_cmake())
    _commit(path, "era 0.46")
    _run(path, "tag", "v0.46.0")
    return path


def build_era_anchor_set(root: str) -> dict[str, str]:
    """A set whose companion repos use independent version numbering.

    The companion carries its own release tags (no common prefix with the
    anchor repo), so era resolution must fall back to the curated lockstep
    map. Exercises the era-anchored enumeration path.
    """
    anchor_lines = (
        "wl_server = dependency('wayland-server', version: '>=1.22')\n"
        "hwscanner_dep = dependency('hyprwayland-scanner', version: '>=0.3.10')\n"
    )
    created: dict[str, str] = {}
    hypr = os.path.join(root, "Hyprland")
    _init_repo(hypr)
    _write(hypr, "VERSION", "0.45.0\n")
    _write(hypr, "meson.build", anchor_lines)
    _commit(hypr, "era 0.45")
    _run(hypr, "tag", "v0.45.0")
    created["Hyprland"] = hypr
    scanner = os.path.join(root, "hyprwayland-scanner")
    _init_repo(scanner)
    _write(scanner, "VERSION", "0.4.2\n")
    _write(scanner, "meson.build", _companion_meson("hyprwayland-scanner", "0.4.2"))
    _commit(scanner, "initial")
    _run(scanner, "tag", "v0.4.2")
    created["hyprwayland-scanner"] = scanner
    return created


def build_source_built_set(root: str) -> dict[str, str]:
    """A set whose companion requires a dependency absent from trixie.

    The dependency is resolved as a source-built ecosystem member against the
    recorded pin, not against the cache (it is not in the fixture baseline).
    """
    created: dict[str, str] = {}
    hypr = os.path.join(root, "Hyprland")
    _init_repo(hypr)
    _write(hypr, "VERSION", "0.45.0\n")
    _write(
        hypr,
        "meson.build",
        "wl_server = dependency('wayland-server', version: '>=1.22')\n",
    )
    _commit(hypr, "era 0.45")
    _run(hypr, "tag", "v0.45.0")
    created["Hyprland"] = hypr
    idle = os.path.join(root, "hypridle")
    _init_repo(idle)
    _write(idle, "VERSION", "0.1.5\n")
    _write(
        idle,
        "meson.build",
        "sdbus_dep = dependency('sdbus-c++', version: '>=2.0.0')\n",
    )
    _commit(idle, "initial")
    _run(idle, "tag", "v0.45.0")
    created["hypridle"] = idle
    return created
