import os
import tomllib
from pathlib import Path

from package_mgmt.lib.model.config import ArchiveSource, ProjectConfig
from package_mgmt.lib.util.errors import ConfigError
from package_mgmt.lib.util.paths import expand_path, validate_absolute


_KEY_MAP = {
    ("paths", "base_dir"): "base_dir",
    ("paths", "binary_cache"): "binary_cache",
    ("paths", "source_cache"): "source_cache",
    ("paths", "iso_dir"): "iso_dir",
    ("database", "path"): "db_path",
    ("logging", "level"): "log_level",
    ("logging", "directory"): "log_dir",
    ("fetch", "source"): "fetch_source",
    ("resolution", "max_build_dep_report_depth"): "max_build_dep_report_depth",
    ("resolution", "max_source_dep_depth"): "max_source_dep_depth",
    ("metadata", "admin_email"): "admin_email",
}

# Expected Python type for every known key (validated in the merged loop).
_TYPE_MAP: dict[tuple[str, str], type] = {
    ("paths", "base_dir"): str,
    ("paths", "binary_cache"): str,
    ("paths", "source_cache"): str,
    ("paths", "iso_dir"): str,
    ("database", "path"): str,
    ("logging", "level"): str,
    ("logging", "directory"): str,
    ("fetch", "source"): bool,
    ("resolution", "max_build_dep_report_depth"): int,
    ("resolution", "max_source_dep_depth"): int,
    ("metadata", "admin_email"): str,
}

_TYPE_LABELS: dict[type, str] = {
    str: "a string",
    bool: "a boolean",
    int: "an integer",
}

_DEPTH_KEYS = (
    ("resolution", "max_build_dep_report_depth"),
    ("resolution", "max_source_dep_depth"),
)

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Path-valued keys that must be absolute after `expand_path` (R-10): internal
# field -> the `(section, key)` tuple used in error messages. `base_dir` is
# handled separately (already validated absolute before expansion).
_PATH_FIELDS = {
    "binary_cache": ("paths", "binary_cache"),
    "source_cache": ("paths", "source_cache"),
    "iso_dir": ("paths", "iso_dir"),
    "db_path": ("database", "path"),
    "log_dir": ("logging", "directory"),
}


def _format_setting(tup: tuple) -> str:
    """`[<section>] <key>` for a flattened `(section, key)` tuple, or
    `[<section>]` for a scalar top-level entry `(section,)`."""
    if len(tup) == 2:
        return f"[{tup[0]}] {tup[1]}"
    return f"[{tup[0]}]"


def _expected_label(tup: tuple) -> str:
    """Human-readable expected type for the error message. The depth keys keep
    their range-rule wording (`integer >= -1`) so a wrong-typed depth reads as
    the same contract the `>= -1` check documents."""
    if tup in _DEPTH_KEYS:
        return "an integer >= -1"
    return _TYPE_LABELS[_TYPE_MAP[tup]]


def source_depth_zero_warning(config) -> str | None:
    """Warning text when source fetching is enabled but
    `max_source_dep_depth = 0` (no sources will be fetched).

    Returns None when no warning applies: source fetching disabled (`source =
    false` — regular_user, where the depth is moot), or a depth that actually
    fetches something (`-1` unlimited, or a positive value)."""
    if config.fetch_source and getattr(config, "max_source_dep_depth", 0) == 0:
        return (
            "max_source_dep_depth = 0 fetches no sources — "
            "use -1 for unlimited or a positive value"
        )
    return None


_GRAYBEARD_CANONICAL_COMPONENTS = ("main", "contrib", "non-free", "non-free-firmware")


def graybeard_missing_components_warning(config) -> str | None:
    """Advisory warning when a graybeard configuration is missing canonical
    Trixie components.

    A graybeard configuration (`source = true` AND
    `max_source_dep_depth = -1`) promises full source/build-tool coverage, so
    an incomplete component set means that promise cannot hold for packages in
    the missing components. The check is config-driven: the union of the
    config's trixie-source components against the four canonical components.

    Returns None when the configuration is not graybeard, or when the union of
    the config's trixie-source components already covers `main`, `contrib`,
    `non-free`, and `non-free-firmware`."""
    if not config.fetch_source or getattr(config, "max_source_dep_depth", None) != -1:
        return None
    present: set[str] = set()
    for source in getattr(config, "sources", ()):
        if source.release == "trixie":
            present.update(source.components)
    missing = [c for c in _GRAYBEARD_CANONICAL_COMPONENTS if c not in present]
    if not missing:
        return None
    return (
        "graybeard configuration (source = true, max_source_dep_depth = -1) is set\n"
        f"but the trixie sources are missing component(s): {', '.join(missing)}.\n"
        "The full-rebuild promise cannot hold for packages in those components — add them to\n"
        "[[archive.source]] to restore complete source/build-tool coverage."
    )


def _normalize_mirror(mirror: str) -> str:
    """Force HTTPS for remote mirrors (spec 012 security hardening): any
    `http://` mirror is upgraded to `https://` so index and payload fetches are
    authenticated by TLS. Loopback hosts keep `http://` — they are the admin's
    own machine / local test archives, where there is no on-path attacker and
    (for tests) the local server may only speak HTTP."""
    if not mirror.startswith("http://"):
        return mirror
    rest = mirror[len("http://"):]
    host = rest.split("/", 1)[0].split(":", 1)[0]
    if host in _LOOPBACK_HOSTS:
        return mirror
    return "https://" + rest


def _flatten(toml_data: dict) -> dict:
    result = {}
    for section, keys in toml_data.items():
        if section == "archive":
            continue
        if isinstance(keys, dict):
            for key, value in keys.items():
                result[(section, key)] = value
        else:
            result[(section,)] = keys
    return result


def _validate_depth_settings(kwargs: dict) -> None:
    """Validate both depth settings as integers >= -1.

    `-1` = unlimited, `0` = none, positive `N` = up to `N` levels. A value
    below `-1`, a non-integer (`1.5`), or a boolean (`true`) is a config
    error that names the key (TOML `true` parses as a bool, which is not a
    valid depth even though `bool` is an `int` subclass in Python)."""
    for section, key in _DEPTH_KEYS:
        field = _KEY_MAP[(section, key)]
        if field not in kwargs:
            continue
        value = kwargs[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < -1:
            raise ConfigError(
                f"[{section}] {key} must be an integer >= -1, got: {value}"
            )


def _parse_sources(archive_data: dict | None) -> tuple[ArchiveSource, ...]:
    sources: list[ArchiveSource] = []
    seen: set[tuple[str, str]] = set()

    raw_sources = archive_data.get("source") if archive_data else None

    if raw_sources is None:
        return ProjectConfig.__dataclass_fields__["sources"].default

    if not isinstance(raw_sources, list) or not raw_sources:
        raise ConfigError(
            "No [[archive.source]] entries found. At least one archive source is required."
        )

    for entry in raw_sources:
        if not isinstance(entry, dict):
            raise ConfigError("Each [[archive.source]] entry must be a table")

        mirror = entry.get("mirror")
        release = entry.get("release")
        components = entry.get("components")

        if not mirror or not isinstance(mirror, str):
            raise ConfigError("Each [[archive.source]] entry requires a non-empty 'mirror' string")
        if not release or not isinstance(release, str):
            raise ConfigError("Each [[archive.source]] entry requires a non-empty 'release' string")
        if not components or not isinstance(components, list) or not components:
            raise ConfigError(
                "Each [[archive.source]] entry requires a non-empty 'components' list"
            )
        for comp in components:
            if not isinstance(comp, str) or not comp:
                raise ConfigError(
                    f"Each component in [[archive.source]] entries must be a non-empty string, got: {comp!r}"
                )

        for comp in components:
            pair = (release, comp)
            if pair in seen:
                raise ConfigError(
                    f"Duplicate (release, component) pair: ({release}, {comp}). "
                    "Each (release, component) combination must be unique."
                )
            seen.add(pair)

        sources.append(
            ArchiveSource(
                mirror=_normalize_mirror(mirror),
                release=release,
                components=tuple(components),
            )
        )

    return tuple(sources)


def load_config(config_dir: str | None = None) -> tuple[ProjectConfig, set[str]]:
    base = Path(config_dir or ".")
    defaults_path = base / "project.defaults.toml"
    local_path = base / "project.local.toml"

    if not defaults_path.exists():
        raise ConfigError(f"Default config not found: {defaults_path}")

    try:
        with open(defaults_path, "rb") as f:
            defaults_raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML in project.defaults.toml: {e}")

    overrides_raw: dict = {}
    overridden_keys: set[str] = set()
    local_data: dict | None = None

    if local_path.exists():
        try:
            with open(local_path, "rb") as f:
                local_data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"Invalid TOML in project.local.toml: {e}")

        local_flat = _flatten(local_data)
        for tup, value in local_flat.items():
            if tup in _KEY_MAP:
                overrides_raw[tup] = value
                overridden_keys.add(_KEY_MAP[tup])

    defaults_flat = _flatten(defaults_raw)
    merged = {**defaults_flat, **overrides_raw}

    kwargs = {f.name: f.default for f in ProjectConfig.__dataclass_fields__.values()}
    for tup, value in merged.items():
        field = _KEY_MAP.get(tup)
        if field is None:
            raise ConfigError(f"Unknown configuration key: {_format_setting(tup)}")
        expected = _TYPE_MAP[tup]
        if type(value) is not expected:
            raise ConfigError(
                f"{_format_setting(tup)} must be {_expected_label(tup)}, got: {value!r}"
            )
        kwargs[field] = value

    archive_data = defaults_raw.get("archive")
    if archive_data is not None and not isinstance(archive_data, dict):
        raise ConfigError(f"[archive] must be a table, got: {archive_data!r}")
    if local_data and "archive" in local_data:
        local_archive = local_data["archive"]
        if not isinstance(local_archive, dict):
            raise ConfigError(f"[archive] must be a table, got: {local_archive!r}")
        if local_archive.get("source"):
            archive_data = local_archive
            overridden_keys.add("sources")
    sources = _parse_sources(archive_data)
    kwargs["sources"] = sources

    try:
        kwargs["base_dir"] = validate_absolute(kwargs["base_dir"])
    except ValueError as e:
        raise ConfigError(f"[paths] base_dir {e}")

    for field, tup in _PATH_FIELDS.items():
        expanded = expand_path(kwargs[field], kwargs["base_dir"])
        if not os.path.isabs(expanded):
            raise ConfigError(
                f"{_format_setting(tup)} must be an absolute path after expansion, "
                f"got: {expanded}"
            )
        kwargs[field] = expanded

    _validate_depth_settings(kwargs)

    return ProjectConfig(**kwargs), overridden_keys
