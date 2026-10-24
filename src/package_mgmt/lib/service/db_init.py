import logging
import os
import sqlite3

from package_mgmt.lib.model.database import SCHEMA_VERSION, EXPECTED_SCHEMA_SQL
from package_mgmt.lib.util.errors import (
    DatabaseError,
    print_error,
    EXIT_SCHEMA_MISMATCH,
)

logger = logging.getLogger(__name__)


def _rebuild_message(db_path: str, missing: str) -> str:
    return (
        f"Database {db_path} is stale (missing {missing}). "
        "Rebuild it per the delete-and-rebuild policy: delete the database "
        "and re-run 'init', 'ensure-base', and your 'add --file' manifests "
        "— the cache is fully derivable from the indexes."
    )


def _check_schema_objects(conn: sqlite3.Connection, db_path: str) -> None:
    """Verify the current feature's schema objects exist.

    During development the schema version stays 1; a
    pre-feature database passes the version check but lacks the new objects
    and would fail obscurely at query time. Detect that here and direct the
    user to the documented rebuild procedure instead.
    """
    missing = []
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'systems'"
    ).fetchone()
    if not row:
        missing.append("'systems' table")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(binaries)").fetchall()}
    if "sync_mask" not in cols:
        missing.append("'binaries.sync_mask' column")
    if missing:
        raise DatabaseError(_rebuild_message(db_path, " and ".join(missing)))


def initialize_database(db_path: str) -> int:
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as e:
        raise DatabaseError(f"Cannot open database {db_path}: {e}")

    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version")
        row = cursor.fetchone()
        existing_version = row[0] if row else 0

        if existing_version == 0:
            cursor.executescript(EXPECTED_SCHEMA_SQL)
            cursor.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
            logger.info("Database created at %s (schema v%d)", db_path, SCHEMA_VERSION)
        elif existing_version == SCHEMA_VERSION:
            logger.info("Database schema validated at %s (v%d)", db_path, SCHEMA_VERSION)
        else:
            raise DatabaseError(
                f"Schema mismatch: DB is v{existing_version}, expected v{SCHEMA_VERSION}"
            )

        _check_schema_objects(conn, db_path)
        return SCHEMA_VERSION
    except DatabaseError:
        raise
    except sqlite3.Error as e:
        raise DatabaseError(f"Database error at {db_path}: {e}")
    finally:
        conn.close()


def validate_schema(db_path: str) -> None:
    """Validate an existing database's schema objects.

    Called by command entry points (systems, snapshot) before operating, so a
    stale development database fails with a clear rebuild message instead of
    an obscure query-time error. Raises DatabaseError on any problem.
    """
    if not os.path.exists(db_path):
        raise DatabaseError(
            f"Database not found at {db_path}; run 'init' first"
        )
    try:
        conn = sqlite3.connect(db_path)
        try:
            _check_schema_objects(conn, db_path)
        finally:
            conn.close()
    except sqlite3.Error as e:
        raise DatabaseError(f"Cannot open database {db_path}: {e}")


def validate_schema_or_error(db_path: str) -> int | None:
    """Command-facing schema gate: validate `db_path`, and on failure print the
    error and return the schema-mismatch exit code; return None when valid.

    Shared by every DB-touching command (spec 012 code-quality debt) so the
    stale-schema behavior — JSON error on stderr, exit 101 — lives in one
    place instead of four identical wrappers.
    """
    try:
        validate_schema(db_path)
    except DatabaseError as e:
        print_error(str(e), EXIT_SCHEMA_MISMATCH)
        return EXIT_SCHEMA_MISMATCH
    return None
