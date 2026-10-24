import json
import logging

from package_mgmt.lib.model.config import ProjectConfig
from package_mgmt.lib.service.config_loader import load_config
from package_mgmt.lib.util.errors import print_error, EXIT_SUCCESS, EXIT_CONFIG_ERROR, ConfigError

logger = logging.getLogger(__name__)


def handle_config(format: str = "text") -> int:
    try:
        config, overridden = load_config()
    except ConfigError as e:
        if format != "json":
            logger.error("Config load failed: %s", e)
        print_error(str(e), EXIT_CONFIG_ERROR)
        return EXIT_CONFIG_ERROR
    except Exception as e:
        if format != "json":
            logger.error("Unexpected config error: %s", e)
        print_error(f"Config error: {e}", EXIT_CONFIG_ERROR)
        return EXIT_CONFIG_ERROR

    logger.info("Config loaded (base_dir=%s, overrides=%d)", config.base_dir, len(overridden))

    if format == "json":
        _output_json(config, overridden)
    else:
        _output_text(config, overridden)

    return EXIT_SUCCESS


def _output_text(config: ProjectConfig, overridden: set) -> None:
    print("Configuration:")
    _print_field("  base_dir", config.base_dir, overridden, "base_dir")
    _print_field("  binary_cache", config.binary_cache, overridden, "binary_cache")
    _print_field("  source_cache", config.source_cache, overridden, "source_cache")
    _print_field("  iso_dir", config.iso_dir, overridden, "iso_dir")
    _print_field("  db_path", config.db_path, overridden, "db_path")
    _print_field("  log_level", config.log_level, overridden, "log_level")
    _print_field("  log_dir", config.log_dir, overridden, "log_dir")
    _print_field("  fetch_source", str(config.fetch_source).lower(), overridden, "fetch_source")
    _print_field("  max_build_dep_report_depth", str(config.max_build_dep_report_depth), overridden, "max_build_dep_report_depth")
    _print_field("  max_source_dep_depth", str(config.max_source_dep_depth), overridden, "max_source_dep_depth")
    _print_field("  admin_email", config.admin_email, overridden, "admin_email")

    if config.sources:
        default = config.sources[0]
        print(f"  default source: {default.release}/{default.components[0]}")
        print()
        print("Archive Sources:")
        for i, src in enumerate(config.sources, 1):
            components_str = ", ".join(src.components)
            print(f"  {i}. mirror: {src.mirror}")
            print(f"     release: {src.release}")
            print(f"     components: {components_str}")
    else:
        print()
        print("Archive Sources: (none configured)")


def _print_field(display_key: str, value: str, overridden: set, field: str) -> None:
    if field in overridden:
        annotation = "(override: project.local.toml)"
    else:
        annotation = "(default)"
    print(f"{display_key:<20} {value:<35} {annotation}")


def _output_json(config: ProjectConfig, overridden: set) -> None:
    data = {
        "paths": {
            "base_dir": config.base_dir,
            "binary_cache": config.binary_cache,
            "source_cache": config.source_cache,
            "iso_dir": config.iso_dir,
        },
        "database": {
            "path": config.db_path,
            "source": "override" if "db_path" in overridden else "default",
        },
        "logging": {
            "level": config.log_level,
            "directory": config.log_dir,
            "source": "override" if "log_level" in overridden else "default",
        },
        "fetch": {
            "source": config.fetch_source,
            "source_override": "fetch_source" in overridden,
        },
        "resolution": {
            "max_build_dep_report_depth": config.max_build_dep_report_depth,
            "max_source_dep_depth": config.max_source_dep_depth,
            "max_source_dep_depth_override": "max_source_dep_depth" in overridden,
            "source": "override" if "max_build_dep_report_depth" in overridden else "default",
        },
        "metadata": {
            "admin_email": config.admin_email,
            "admin_email_override": "admin_email" in overridden,
        },
        "archive_sources": [
            {
                "mirror": src.mirror,
                "release": src.release,
                "components": list(src.components),
            }
            for src in config.sources
        ],
    }
    print(json.dumps(data, indent=2))
