import logging
import sqlite3

from package_mgmt.lib.service.db_init import validate_schema_or_error
from package_mgmt.lib.service.snapshot import pending_count, system_bit, take_snapshot
from package_mgmt.lib.util.errors import (
    print_error,
    EXIT_SUCCESS,
    EXIT_ERROR,
)

logger = logging.getLogger(__name__)


def handle_snapshot(db_path: str, system: str | None, dry_run: bool = False) -> int:
    if not system:
        print_error("snapshot requires --system <name>", EXIT_ERROR)
        return EXIT_ERROR

    error = validate_schema_or_error(db_path)
    if error is not None:
        return error

    try:
        conn = sqlite3.connect(db_path)
        try:
            if dry_run:
                pending = pending_count(conn, system_bit(conn, system))
                print(f"{pending} pending for '{system}'")
                return EXIT_SUCCESS
            pending = take_snapshot(conn, system)
            print(f"Marked {pending} binaries for '{system}'")
            return EXIT_SUCCESS
        except ValueError as e:
            print_error(str(e), EXIT_ERROR)
            return EXIT_ERROR
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.error("snapshot failed: %s", e)
        print_error(f"Database error: {e}", EXIT_ERROR)
        return EXIT_ERROR
