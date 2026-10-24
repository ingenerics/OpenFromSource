import logging
import os
import sqlite3

from package_mgmt.lib.service.config_loader import load_config
from package_mgmt.lib.service.ensure_base import EnsureBaseStats, ensure_base
from package_mgmt.lib.util.errors import (
    print_error,
    EXIT_SUCCESS,
    EXIT_ERROR,
    EXIT_SCHEMA_MISMATCH,
    EXIT_CONFIG_ERROR,
    CacheNotEmptyError,
    ConfigError,
    DatabaseError,
    IsoError,
)
from package_mgmt.lib.util.progress import make_resolved_progress

logger = logging.getLogger(__name__)

CONSOLE_ERROR_CAP = 50


def _is_first_line_warning(warning: str) -> bool:
    """True for the graybeard missing-component warning, which the command
    emits ahead of the summary (so it is the first stdout lines) and which the
    summary block therefore does not re-list."""
    return warning.startswith("graybeard configuration")


def _print_summary(stats: EnsureBaseStats) -> None:
    components = ", ".join(stats.components)
    print(f"ISO image:        {os.path.basename(stats.image_path)} ({stats.release}: {components})")
    print(f"Binary packages:  {stats.binaries}")
    print(f"Provides entries: {stats.provides}")
    print(f"Index/image mismatches: {stats.mismatches}")
    for path in stats.mismatch_paths:
        print(f"  - {path} (in index, not found in image)")
    if stats.resolved is not None and stats.total is not None:
        print(f"Base packages resolved: {stats.resolved}/{stats.total}")
    shown_warnings = [w for w in stats.warnings if not _is_first_line_warning(w)]
    if shown_warnings:
        print("Warnings:")
        for warning in shown_warnings:
            print(f"  - {warning}")
    else:
        print("Warnings:         none")
    if stats.errors:
        total_errors = len(stats.errors)
        if total_errors > CONSOLE_ERROR_CAP:
            print(
                f"Errors:           {total_errors} (showing first {CONSOLE_ERROR_CAP}; "
                f"{total_errors - CONSOLE_ERROR_CAP} more in the log)"
            )
        else:
            print(f"Errors:           {total_errors}")
        for error in stats.errors[:CONSOLE_ERROR_CAP]:
            print(f"  - {error}")
    else:
        print("Errors:           none")


def handle_ensure_base() -> int:
    try:
        config, _ = load_config()
    except ConfigError as e:
        print_error(str(e), EXIT_CONFIG_ERROR)
        return EXIT_CONFIG_ERROR

    try:
        stats = ensure_base(config, progress=make_resolved_progress())
    except CacheNotEmptyError as e:
        print_error(str(e), EXIT_ERROR)
        return EXIT_ERROR
    except IsoError as e:
        print_error(str(e), EXIT_ERROR)
        return EXIT_ERROR
    except DatabaseError as e:
        print_error(str(e), EXIT_SCHEMA_MISMATCH)
        return EXIT_SCHEMA_MISMATCH
    except sqlite3.Error as e:
        print_error(f"Database error: {e}", EXIT_ERROR)
        return EXIT_ERROR
    except Exception as e:
        logger.error("ensure-base failed: %s", e)
        print_error(f"ensure-base failed: {e}", EXIT_ERROR)
        return EXIT_ERROR

    if stats.warnings and _is_first_line_warning(stats.warnings[0]):
        print(f"WARNING: {stats.warnings[0]}")
    if stats.resolved is not None:
        print()
    _print_summary(stats)
    for error in stats.errors:
        logger.warning("ensure-base resolution error: %s", error)
    logger.info("ensure-base completed: %s", stats)
    return EXIT_ERROR if stats.errors else EXIT_SUCCESS
