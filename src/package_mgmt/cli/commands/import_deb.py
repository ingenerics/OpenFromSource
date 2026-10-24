import logging
import sqlite3

from package_mgmt.lib.service.config_loader import load_config
from package_mgmt.lib.service.db_init import initialize_database
from package_mgmt.lib.service.import_local import ImportDebError, import_local_deb
from package_mgmt.lib.util.errors import (
    print_error,
    EXIT_SUCCESS,
    EXIT_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_SCHEMA_MISMATCH,
    ConfigError,
    DatabaseError,
)

logger = logging.getLogger(__name__)


def handle_import_deb(deb_path: str, source: str | None = None) -> int:
    """`import-deb <file> [--source <dir>]`: import exactly one locally built
    `.deb` into the `local` component (spec 021 contracts/cli.md)."""
    try:
        config, _ = load_config()
    except ConfigError as e:
        print_error(str(e), EXIT_CONFIG_ERROR)
        return EXIT_CONFIG_ERROR

    try:
        conn = sqlite3.connect(config.db_path)
        initialize_database(config.db_path)
    except DatabaseError as e:
        print_error(str(e), EXIT_SCHEMA_MISMATCH)
        return EXIT_SCHEMA_MISMATCH
    except sqlite3.Error as e:
        print_error(f"Database error: {e}", EXIT_ERROR)
        return EXIT_ERROR

    try:
        result = import_local_deb(conn, config, deb_path, source_dir=source)
    except ImportDebError as e:
        print_error(str(e), EXIT_ERROR)
        return EXIT_ERROR
    except sqlite3.Error as e:
        conn.rollback()
        print_error(f"Database error: {e}", EXIT_ERROR)
        return EXIT_ERROR
    finally:
        conn.close()

    print(
        f"Imported {result.package} {result.version} ({result.architecture}) "
        "into local component (verified)"
    )
    if result.source_summary:
        print(result.source_summary)
    return EXIT_SUCCESS
