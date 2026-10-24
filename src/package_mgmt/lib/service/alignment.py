"""Alignment baseline loading, era enumeration, and two-tier validation.

The service answers "which release era of the source set is the newest whose
declared build/runtime dependency requirements are all satisfied" against the
frozen cache, and re-validates a previously produced version manifest against
the current cache and source trees. The cache is used read-only: the baseline
comes from the `binaries` table (trixie/main, verified) cross-checked against
the cached `Packages.gz`, and no records are written.
"""

import os
import re
import sqlite3
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import date

from package_mgmt.lib.model.alignment import (
    BuildSystem,
    DependencyRequirement,
    ManifestRepo,
    UnmetRequirement,
    VersionManifest,
)
from package_mgmt.lib.service import index_loader
from package_mgmt.lib.util.versions import Version, compare_versions

BASELINE_LABEL = "trixie/main (verified, read-only)"


def baseline_label(release: str, component: str) -> str:
    """The human-readable baseline label reported by the alignment command,
    derived from the configured first archive source."""
    return f"{release}/{component} (verified, read-only)"

# The repo whose release version defines an era; companion refs for an era are
# resolved from the curated lockstep map below (the ecosystem does not share a
# single version numbering, so eras cannot be a common tag prefix).
ANCHOR_REPO = "Hyprland"

# Curated lockstep companion tags per Hyprland release era (era label = the
# anchor repo's major.minor). Each companion tag is the release current for
# that era, taken from the upstream release history at alignment time; it is
# the pair the era's declared minimums are validated against. Eras before 0.44
# are not curated (ancient wayland-era releases are outside the frozen-tree
# scope).
ERA_COMPANION_TAGS: dict[str, dict[str, str]] = {
    "0.44": {"hyprwayland-scanner": "v0.4.2", "hyprlang": "v0.5.3", "hyprutils": "v0.2.3", "hyprcursor": "v0.1.10", "hyprgraphics": "v0.1.0", "aquamarine": "v0.4.1", "hyprland-protocols": "v0.3.0", "hyprpaper": "v0.7.1", "hypridle": "v0.1.2", "hyprlock": "v0.4.1", "xdg-desktop-portal-hyprland": "v1.3.6"},
    "0.45": {"hyprwayland-scanner": "v0.4.2", "hyprlang": "v0.5.3", "hyprutils": "v0.2.4", "hyprcursor": "v0.1.10", "hyprgraphics": "v0.1.0", "aquamarine": "v0.4.4", "hyprland-protocols": "v0.4.0", "hyprpaper": "v0.7.1", "hypridle": "v0.1.5", "hyprlock": "v0.5.0", "xdg-desktop-portal-hyprland": "v1.3.8"},
    "0.46": {"hyprwayland-scanner": "v0.4.2", "hyprlang": "v0.6.0", "hyprutils": "v0.2.6", "hyprcursor": "v0.1.10", "hyprgraphics": "v0.1.1", "aquamarine": "v0.5.1", "hyprland-protocols": "v0.4.0", "hyprpaper": "v0.7.3", "hypridle": "v0.1.5", "hyprlock": "v0.5.0", "xdg-desktop-portal-hyprland": "v1.3.8"},
    "0.47": {"hyprwayland-scanner": "v0.4.4", "hyprlang": "v0.6.0", "hyprutils": "v0.5.0", "hyprcursor": "v0.1.11", "hyprgraphics": "v0.1.1", "aquamarine": "v0.7.1", "hyprland-protocols": "v0.6.1", "hyprpaper": "v0.7.4", "hypridle": "v0.1.5", "hyprlock": "v0.6.2", "xdg-desktop-portal-hyprland": "v1.3.9"},
    "0.48": {"hyprwayland-scanner": "v0.4.4", "hyprlang": "v0.6.0", "hyprutils": "v0.5.2", "hyprcursor": "v0.1.12", "hyprgraphics": "v0.1.2", "aquamarine": "v0.8.0", "hyprland-protocols": "v0.6.2", "hyprpaper": "v0.7.4", "hypridle": "v0.1.5", "hyprlock": "v0.7.0", "xdg-desktop-portal-hyprland": "v1.3.9"},
    "0.49": {"hyprwayland-scanner": "v0.4.4", "hyprlang": "v0.6.3", "hyprutils": "v0.7.1", "hyprcursor": "v0.1.12", "hyprgraphics": "v0.1.3", "aquamarine": "v0.8.0", "hyprland-protocols": "v0.6.4", "hyprpaper": "v0.7.5", "hypridle": "v0.1.6", "hyprlock": "v0.8.2", "xdg-desktop-portal-hyprland": "v1.3.9"},
    "0.50": {"hyprwayland-scanner": "v0.4.5", "hyprlang": "v0.6.3", "hyprutils": "v0.8.1", "hyprcursor": "v0.1.12", "hyprgraphics": "v0.1.5", "aquamarine": "v0.9.1", "hyprland-protocols": "v0.6.4", "hyprpaper": "v0.7.5", "hypridle": "v0.1.6", "hyprlock": "v0.8.2", "xdg-desktop-portal-hyprland": "v1.3.9"},
    "0.51": {"hyprwayland-scanner": "v0.4.5", "hyprlang": "v0.6.4", "hyprutils": "v0.8.4", "hyprcursor": "v0.1.13", "hyprgraphics": "v0.1.5", "aquamarine": "v0.9.4", "hyprland-protocols": "v0.6.4", "hyprpaper": "v0.7.5", "hypridle": "v0.1.7", "hyprlock": "v0.9.1", "xdg-desktop-portal-hyprland": "v1.3.10"},
    "0.52": {"hyprwayland-scanner": "v0.4.5", "hyprlang": "v0.6.4", "hyprutils": "v0.10.1", "hyprcursor": "v0.1.13", "hyprgraphics": "v0.3.0", "aquamarine": "v0.9.5", "hyprland-protocols": "v0.7.0", "hyprpaper": "v0.7.6", "hypridle": "v0.1.7", "hyprlock": "v0.9.2", "xdg-desktop-portal-hyprland": "v1.3.11"},
    "0.53": {"hyprwayland-scanner": "v0.4.5", "hyprlang": "v0.6.7", "hyprutils": "v0.11.0", "hyprcursor": "v0.1.13", "hyprgraphics": "v0.5.0", "aquamarine": "v0.10.0", "hyprland-protocols": "v0.7.0", "hyprpaper": "v0.8.0", "hypridle": "v0.1.7", "hyprlock": "v0.9.2", "xdg-desktop-portal-hyprland": "v1.3.11"},
    "0.54": {"hyprwayland-scanner": "v0.4.5", "hyprlang": "v0.6.8", "hyprutils": "v0.11.0", "hyprcursor": "v0.1.13", "hyprgraphics": "v0.5.0", "aquamarine": "v0.10.0", "hyprland-protocols": "v0.7.0", "hyprpaper": "v0.8.3", "hypridle": "v0.1.7", "hyprlock": "v0.9.2", "xdg-desktop-portal-hyprland": "v1.3.11"},
    "0.55": {"hyprwayland-scanner": "v0.4.6", "hyprlang": "v0.6.8", "hyprutils": "v0.13.1", "hyprcursor": "v0.1.13", "hyprgraphics": "v0.5.1", "aquamarine": "v0.11.0", "hyprland-protocols": "v0.7.0", "hyprpaper": "v0.8.4", "hypridle": "v0.1.7", "hyprlock": "v0.9.5", "xdg-desktop-portal-hyprland": "v1.3.12"},
    "0.56": {"hyprwayland-scanner": "v0.4.6", "hyprlang": "v0.6.8", "hyprutils": "v0.14.0", "hyprcursor": "v0.1.13", "hyprgraphics": "v0.5.1", "aquamarine": "v0.13.0", "hyprland-protocols": "v0.7.0", "hyprpaper": "v0.8.4", "hypridle": "v0.1.7", "hyprlock": "v0.9.6", "xdg-desktop-portal-hyprland": "v1.4.0"},
}


class AlignmentError(Exception):
    pass


class AlignmentLoadError(AlignmentError):
    """A load/configuration failure (missing repos, unreadable tree,
    malformed manifest) — a hard error, never a partial result."""


# Curated pkg-config module name -> owning Debian binary package (trixie).
PKG_CONFIG_MAPPING: dict[str, str] = {
    "wayland-server": "libwayland-dev",
    "wayland-client": "libwayland-dev",
    "wayland-egl": "libwayland-dev",
    "wayland-cursor": "libwayland-dev",
    "xkbcommon": "libxkbcommon-dev",
    "libinput": "libinput-dev",
    "gio-2.0": "libglib2.0-dev",
    "glib-2.0": "libglib2.0-dev",
    "gbm": "libgbm-dev",
    "pangocairo": "libpango1.0-dev",
    "pango": "libpango1.0-dev",
    "cairo": "libcairo2-dev",
    "pixman-1": "libpixman-1-dev",
    "xcursor": "libxcursor-dev",
    "libdrm": "libdrm-dev",
    "drm": "libdrm-dev",
    "uuid": "uuid-dev",
    "libeis-1.0": "libeis-dev",
    "lcms2": "liblcms2-dev",
    "libcanberra": "libcanberra-dev",
    "re2": "libre2-dev",
    "muparser": "libmuparser-dev",
    "lua5.5": "liblua5.5-dev",
    "wayland-protocols": "wayland-protocols",
    "xwayland": "xwayland",
    "libseat": "libseat-dev",
    "libdisplay-info": "libdisplay-info-dev",
    "libtomlplusplus": "libtomlplusplus-dev",
    "systemd": "libsystemd-dev",
    "hyprland-protocols": "hyprland-protocols",
    "spdlog": "libspdlog-dev",
    "fmt": "libfmt-dev",
    "nlohmann_json": "nlohmann-json3-dev",
    "glslang": "glslang-dev",
    "xcb": "libxcb1-dev",
    "xcb-render": "libxcb-render0-dev",
    "xcb-xfixes": "libxcb-xfixes0-dev",
    "xcb-icccm": "libxcb-icccm4-dev",
    "xcb-composite": "libxcb-composite0-dev",
    "xcb-res": "libxcb-res0-dev",
    "lua": "liblua5.5-dev",
    "libudev": "libudev-dev",
    "hwdata": "hwdata",
    "libzip": "libzip-dev",
    "librsvg-2.0": "librsvg2-dev",
    "tomlplusplus": "libtomlplusplus-dev",
    "libjpeg": "libjpeg-dev",
    "libwebp": "libwebp-dev",
    "libmagic": "libmagic-dev",
    "libjxl": "libjxl-dev",
    "libjxl_cms": "libjxl-dev",
    "libjxl_threads": "libjxl-dev",
    "sdbus-c++": "libsdbus-c++-dev",
    "pugixml": "libpugixml-dev",
    "egl": "libegl-dev",
    "opengl": "libopengl-dev",
    "libpng": "libpng-dev",
    "libpam": "libpam0g-dev",
    "pam": "libpam0g-dev",
}

_REASONS = {
    "unknown_module": "unknown_module",
    "not_in_trixie_main": "not_in_trixie_main",
    "version_too_low": "version_too_low",
}


# Source-built build/runtime dependencies that the frozen trixie tree does not
# carry at the required version. Most are absent from trixie/main and
# trixie-backports entirely (verified at alignment time); the exception is
# `wayland-protocols`, which trixie ships at 1.44 but the stack needs at >=1.45
# (Hyprland v0.52's protocols/meson.build) — it is a data-only package
# (Architecture: all, no ABI), so it is supplied from source at 1.45 rather
# than drifting the compiled system tree (docs/005, Part 0). All are built from
# source as part of the stack and validated in the ecosystem tier against the
# pinned version recorded here; the pin is part of the recorded dependency
# graph (build helpers and libraries are never taken silently from outside the
# cache).
SOURCE_BUILT_PROVIDED: dict[str, str] = {
    "sdbus-c++": "2.0.0",
    "wayland-protocols": "1.45",
    "spng": "0.7.4",
    "xcb-errors": "1.0.1",
    "hyprtoolkit": "0.4.1",
    "hyprwire": "0.1.0",
}


@dataclass(frozen=True)
class Baseline:
    """The frozen cache view every requirement is validated against."""

    versions: dict[str, str] = field(default_factory=dict)
    provides: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def cached_version(self, package_name: str) -> str | None:
        version = self.versions.get(package_name)
        if version is not None:
            return version
        for provider, provides_version in self.provides.get(package_name, ()):
            if provides_version:
                return provides_version
            return self.versions.get(provider)
        return None


def load_baseline(
    db_path: str, packages_gz_path: str, release: str, component: str
) -> Baseline:
    """Read the configured baseline's verified records from the DB, cross-checked
    against the cached index for virtual-package resolution."""
    versions: dict[str, str] = {}
    provides: dict[str, list[tuple[str, str]]] = {}
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT package_name, version FROM binaries"
            " WHERE release = ? AND component = ? AND verified = 1",
            (release, component),
        ).fetchall()
    finally:
        conn.close()
    for name, version in rows:
        versions[name] = version
    if os.path.exists(packages_gz_path):
        index = index_loader.load_binary_index(packages_gz_path, release, component)
        for packages in index.values():
            for entry in packages:
                for virtual, provided in index_loader.parse_provides_field(
                    entry.provides_raw
                ):
                    provides.setdefault(virtual, []).append(
                        (entry.package, provided)
                    )
    return Baseline(versions=versions, provides=provides)


def check_requirement(
    requirement: DependencyRequirement,
    baseline: Baseline,
    provided: dict[str, str],
) -> UnmetRequirement | None:
    """Validate one requirement; return an `UnmetRequirement` or None.

    The cache tier compares the requirement against the cached version of the
    mapped Debian package; the ecosystem tier compares it against the version
    the selected source provides. An empty constraint is satisfied by
    presence alone.
    """
    if requirement.tier == "ecosystem":
        provided_version = provided.get(requirement.module)
        if provided_version is None:
            return UnmetRequirement(
                requirement,
                cached_package=requirement.module,
                reason=_REASONS["unknown_module"],
            )
        if requirement.version and not compare_versions(
            provided_version, requirement.operator, requirement.version
        ):
            return UnmetRequirement(
                requirement,
                cached_package=requirement.module,
                cached_version=provided_version,
                reason=_REASONS["version_too_low"],
            )
        return None

    cached_package = PKG_CONFIG_MAPPING.get(requirement.module)
    if cached_package is None:
        return UnmetRequirement(
            requirement, reason=_REASONS["unknown_module"]
        )
    cached_version = baseline.cached_version(cached_package)
    if cached_version is None:
        return UnmetRequirement(
            requirement,
            cached_package=cached_package,
            reason=_REASONS["not_in_trixie_main"],
        )
    if requirement.version and not compare_versions(
        cached_version, requirement.operator, requirement.version
    ):
        return UnmetRequirement(
            requirement,
            cached_package=cached_package,
            cached_version=cached_version,
            reason=_REASONS["version_too_low"],
        )
    return None


def _git(repo_path: str, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, OSError) as e:
        raise AlignmentLoadError(
            f"cannot read git repo {repo_path}: {e}"
        ) from e
    return result.stdout.strip()


def _show(repo_path: str, commit: str, path: str) -> str | None:
    """File content at a commit, or None when the file does not exist there."""
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "show", f"{commit}:{path}"],
            capture_output=True,
            text=True,
        )
    except OSError as e:
        raise AlignmentLoadError(
            f"cannot read git repo {repo_path}: {e}"
        ) from e
    if result.returncode != 0:
        return None
    return result.stdout


def _remote_url(repo_path: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo_path, "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def list_repos(repo_dir: str) -> list[str]:
    """The in-scope repositories (subdirectories of `repo_dir`)."""
    if not os.path.isdir(repo_dir):
        raise AlignmentLoadError(f"repo directory not found: {repo_dir}")
    names = sorted(
        name
        for name in os.listdir(repo_dir)
        if os.path.isdir(os.path.join(repo_dir, name))
    )
    if not names:
        raise AlignmentLoadError(
            f"no source repositories found in {repo_dir}"
        )
    return names


def _era_from_tag(tag: str) -> str:
    label = tag[1:] if tag.startswith(("v", "V")) else tag
    parts = label.split(".")
    return ".".join(parts[:2])


def _git_tags(repo_path: str) -> list[str]:
    output = _git(repo_path, "tag")
    return [t for t in output.splitlines() if t]


def enumerate_eras(repo_dir: str) -> list[str]:
    """Candidate release eras, newest first, anchored on the anchor repo.

    Eras are the anchor repo's release labels that the curated era map
    covers; companion refs for each era come from the curated lockstep map
    (the ecosystem does not share a common version numbering).
    """
    names = list_repos(repo_dir)
    if ANCHOR_REPO not in names:
        raise AlignmentLoadError(
            f"anchor repository {ANCHOR_REPO} not found in {repo_dir}"
        )
    anchor_path = os.path.join(repo_dir, ANCHOR_REPO)
    labels = {_era_from_tag(t) for t in _git_tags(anchor_path)}
    eras = sorted(labels & set(ERA_COMPANION_TAGS), key=lambda e: Version(e))
    if not eras:
        raise AlignmentLoadError(
            f"no curated release era matches the {ANCHOR_REPO} tags in {repo_dir}"
        )
    return list(reversed(eras))


def _tag_for_era(repo_path: str, name: str, era: str) -> str | None:
    """The repo's tag for an era: its own era-labeled tag when it has one,
    else the curated lockstep companion tag for the era."""
    tags = _git_tags(repo_path)
    for tag in tags:
        if _era_from_tag(tag) == era:
            return tag
    mapped = ERA_COMPANION_TAGS.get(era, {}).get(name)
    if mapped is not None and mapped in tags:
        return mapped
    return None


def _provided_version(repo_path: str, commit: str, tag: str) -> str:
    content = _show(repo_path, commit, "VERSION")
    if content is not None and content.strip():
        return content.strip()
    return tag.strip()


def _requirements_at(
    repo_path: str, repo_name: str, commit: str
) -> list[DependencyRequirement]:
    from package_mgmt.lib.service.buildmeta import (
        parse_cmake_requirements,
        parse_meson_requirements,
    )

    meson = _show(repo_path, commit, "meson.build")
    if meson is not None:
        return parse_meson_requirements(repo_name, meson)
    cmake = _show(repo_path, commit, "CMakeLists.txt")
    if cmake is not None:
        return parse_cmake_requirements(repo_name, cmake)
    return []


def _classify(requirement: DependencyRequirement, repo_names: set[str]) -> DependencyRequirement:
    tier = (
        "ecosystem"
        if (
            requirement.module in repo_names
            and requirement.module != requirement.repo
        )
        or requirement.module in SOURCE_BUILT_PROVIDED
        else "cache"
    )
    if tier == requirement.tier:
        return requirement
    return DependencyRequirement(
        repo=requirement.repo,
        module=requirement.module,
        operator=requirement.operator,
        version=requirement.version,
        tier=tier,
        source_ref=requirement.source_ref,
        optional=requirement.optional,
    )


def evaluate_era(
    repo_dir: str,
    repo_names: list[str],
    era: str,
    baseline: Baseline,
) -> tuple[list[DependencyRequirement], list[UnmetRequirement]]:
    """Evaluate every repo's requirements for one era; return (all reqs, unmet)."""
    names = set(repo_names)
    provided: dict[str, str] = {}
    requirements: list[DependencyRequirement] = []
    for name in repo_names:
        repo_path = os.path.join(repo_dir, name)
        tag = _tag_for_era(repo_path, name, era)
        if tag is None:
            continue
        commit = _git(repo_path, "rev-parse", tag)
        provided[name] = _provided_version(repo_path, commit, tag)
        for requirement in _requirements_at(repo_path, name, commit):
            requirements.append(_classify(requirement, names))
    provided.update(SOURCE_BUILT_PROVIDED)
    unmet = [
        failure
        for requirement in requirements
        if not requirement.optional
        for failure in [check_requirement(requirement, baseline, provided)]
        if failure is not None
    ]
    return requirements, unmet


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def write_manifest(manifest: VersionManifest, manifest_dir: str) -> str:
    """Write the version manifest as deterministic TOML; return its path."""
    os.makedirs(manifest_dir, exist_ok=True)
    path = os.path.join(manifest_dir, f"hyprland-{manifest.era}.toml")
    lines = [
        f'era = "{manifest.era}"',
        f'chosen = "{manifest.chosen.isoformat()}"',
        f'reason = "{_toml_escape(manifest.reason)}"',
    ]
    for repo in manifest.repos:
        lines.extend(
            [
                "",
                "[[repo]]",
                f'name = "{repo.name}"',
                f'url = "{repo.url}"',
                f'tag = "{repo.tag}"',
                f'commit = "{repo.commit}"',
                f'build = "{repo.build}"',
                f'reason = "{_toml_escape(repo.reason)}"',
            ]
        )
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def load_manifest(path: str) -> VersionManifest:
    """Load and schema-validate a version manifest (raises AlignmentLoadError)."""
    if not os.path.exists(path):
        raise AlignmentLoadError(f"manifest not found: {path}")
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise AlignmentLoadError(f"cannot parse manifest {path}: {e}") from e
    try:
        era = data["era"]
        chosen = data["chosen"]
        reason = data["reason"]
        repos = data["repo"]
    except KeyError as e:
        raise AlignmentLoadError(
            f"invalid manifest {path}: missing required field {e}"
        ) from e
    if not isinstance(era, str) or not era:
        raise AlignmentLoadError(f"invalid manifest {path}: era must be a non-empty string")
    try:
        chosen_date = date.fromisoformat(chosen)
    except (TypeError, ValueError) as e:
        raise AlignmentLoadError(
            f"invalid manifest {path}: chosen is not an ISO date: {chosen!r}"
        ) from e
    if not isinstance(repos, list) or not repos:
        raise AlignmentLoadError(f"invalid manifest {path}: no [[repo]] entries")
    manifest_repos: list[ManifestRepo] = []
    for entry in repos:
        if not isinstance(entry, dict):
            raise AlignmentLoadError(f"invalid manifest {path}: repo entry is not a table")
        try:
            name = entry["name"]
            url = entry["url"]
            tag = entry["tag"]
            commit = entry["commit"]
            build = entry["build"]
            repo_reason = entry["reason"]
        except KeyError as e:
            raise AlignmentLoadError(
                f"invalid manifest {path}: repo missing required field {e}"
            ) from e
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise AlignmentLoadError(
                f"invalid manifest {path}: commit must be a full 40-hex SHA ({name})"
            )
        if build not in ("meson", "cmake"):
            raise AlignmentLoadError(
                f"invalid manifest {path}: build must be meson or cmake ({name})"
            )
        manifest_repos.append(
            ManifestRepo(
                name=name,
                url=url,
                tag=tag,
                commit=commit,
                build=build,
                reason=repo_reason,
            )
        )
    return VersionManifest(
        era=era,
        chosen=chosen_date,
        reason=reason,
        repos=tuple(manifest_repos),
    )


def build_manifest(
    repo_dir: str, repo_names: list[str], era: str, today: date
) -> VersionManifest:
    """Assemble the manifest for a selected era from the acquired repos."""
    repos: list[ManifestRepo] = []
    for name in repo_names:
        repo_path = os.path.join(repo_dir, name)
        tag = _tag_for_era(repo_path, name, era)
        if tag is None:
            continue
        commit = _git(repo_path, "rev-parse", tag)
        build: BuildSystem = (
            "meson" if _show(repo_path, commit, "meson.build") is not None else "cmake"
        )
        url = _remote_url(repo_path) or f"https://github.com/hyprwm/{name}"
        repos.append(
            ManifestRepo(
                name=name,
                url=url,
                tag=tag,
                commit=commit,
                build=build,
                reason=f"{tag} aligns with the frozen trixie tree",
            )
        )
    reason = (
        f"Newest satisfying era: every declared build/runtime dependency "
        f"requirement of era {era} is satisfied by the frozen trixie tree."
    )
    return VersionManifest(era=era, chosen=today, reason=reason, repos=tuple(repos))


def run_search(
    repo_dir: str, baseline: Baseline, today: date, label: str = BASELINE_LABEL
):
    """Search mode: newest satisfying era wins; writes the manifest on success."""
    from package_mgmt.lib.model.alignment import AlignmentReport

    names = list_repos(repo_dir)
    eras = enumerate_eras(repo_dir)
    candidates = sorted(eras, key=lambda era: Version(era))
    newest_unmet: list[UnmetRequirement] = []
    newest_checked = 0
    for era in eras:
        requirements, unmet = evaluate_era(repo_dir, names, era, baseline)
        checked = sum(1 for r in requirements if not r.optional)
        if not unmet:
            manifest = build_manifest(repo_dir, names, era, today)
            written = write_manifest(manifest, "manifests")
            return AlignmentReport(
                mode="search",
                repo_dir=repo_dir,
                baseline=label,
                candidates_evaluated=tuple(candidates),
                selected_era=era,
                manifest_written=written,
                requirements_checked=checked,
                satisfied=True,
            )
        # Eras are walked newest first; on total failure the newest era's
        # unmet requirements are the actionable ones (contract: name the
        # newest era's unmet requirements).
        if not newest_unmet:
            newest_unmet = unmet
            newest_checked = checked
    return AlignmentReport(
        mode="search",
        repo_dir=repo_dir,
        baseline=label,
        candidates_evaluated=tuple(candidates),
        selected_era=None,
        requirements_checked=newest_checked,
        satisfied=False,
        unmet=tuple(newest_unmet),
    )


def run_verify(
    repo_dir: str,
    manifest: VersionManifest,
    baseline: Baseline,
    manifest_path: str,
    label: str = BASELINE_LABEL,
):
    """Verify mode: re-validate a given manifest against the current state."""
    from package_mgmt.lib.model.alignment import AlignmentReport

    names = set(list_repos(repo_dir))
    provided: dict[str, str] = {}
    requirements: list[DependencyRequirement] = []
    for repo in manifest.repos:
        repo_path = os.path.join(repo_dir, repo.name)
        if not os.path.isdir(repo_path):
            raise AlignmentLoadError(
                f"repo {repo.name} from the manifest is missing in {repo_dir}"
            )
        head = _git(repo_path, "rev-parse", "HEAD")
        if head != repo.commit:
            raise AlignmentLoadError(
                f"repo {repo.name} HEAD ({head[:12]}) does not match manifest "
                f"commit ({repo.commit[:12]})"
            )
        provided[repo.name] = _provided_version(repo_path, repo.commit, repo.tag)
        for requirement in _requirements_at(repo_path, repo.name, repo.commit):
            requirements.append(_classify(requirement, names))
    provided.update(SOURCE_BUILT_PROVIDED)
    unmet = [
        failure
        for requirement in requirements
        if not requirement.optional
        for failure in [check_requirement(requirement, baseline, provided)]
        if failure is not None
    ]
    checked = sum(1 for r in requirements if not r.optional)
    return AlignmentReport(
        mode="verify",
        repo_dir=repo_dir,
        baseline=label,
        manifest=manifest_path,
        requirements_checked=checked,
        satisfied=not unmet,
        unmet=tuple(unmet),
    )
