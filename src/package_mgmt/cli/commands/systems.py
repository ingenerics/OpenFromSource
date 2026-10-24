import logging
import sqlite3

from package_mgmt.lib.service.db_init import validate_schema_or_error
from package_mgmt.lib.util.errors import (
    print_error,
    EXIT_SUCCESS,
    EXIT_ERROR,
)

logger = logging.getLogger(__name__)

_BIT_CEILING = 2 ** 62


def _next_bit(conn: sqlite3.Connection) -> int:
    """Next power of two above the highest bit ever issued (retired included).

    Bits are assigned once and never reused: the MAX() over ALL rows (active
    and retired) guarantees a fresh bit even after removals.
    """
    row = conn.execute("SELECT COALESCE(MAX(bit), 1) * 2 FROM systems").fetchone()
    return row[0]


def _valid_moniker(name: str) -> bool:
    """A system moniker is a non-empty word — no whitespace/newlines (the
    name is used in CLI args and sync bookkeeping; spec 019 P-3)."""
    return bool(name) and not any(ch.isspace() for ch in name)


def handle_systems_add(db_path: str, name: str, note: str | None = None) -> int:
    error = validate_schema_or_error(db_path)
    if error is not None:
        return error
    if not _valid_moniker(name):
        print_error(
            "System name must be a non-empty word (no spaces, newlines)", EXIT_ERROR
        )
        return EXIT_ERROR
    conn = sqlite3.connect(db_path)
    try:
        bit = _next_bit(conn)
        if bit > _BIT_CEILING:
            print_error("System registry is full (max 62 systems)", EXIT_ERROR)
            return EXIT_ERROR
        conn.execute(
            "INSERT INTO systems (name, bit, note) VALUES (?, ?, ?)",
            (name, bit, note),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        print_error(f"System '{name}' already exists", EXIT_ERROR)
        return EXIT_ERROR
    except sqlite3.Error as e:
        logger.error("systems add failed: %s", e)
        print_error(f"Database error: {e}", EXIT_ERROR)
        return EXIT_ERROR
    finally:
        conn.close()
    print(f"Added system '{name}'")
    logger.info("System added: %s", name)
    return EXIT_SUCCESS


def handle_systems_list(db_path: str) -> int:
    error = validate_schema_or_error(db_path)
    if error is not None:
        return error
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            """SELECT s.name, s.note,
                      (SELECT COUNT(*) FROM binaries b
                       WHERE b.status = 'current' AND (b.sync_mask & s.bit) != 0)
               FROM systems s
               WHERE s.retired = 0
               ORDER BY s.id"""
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        logger.error("systems list failed: %s", e)
        print_error(f"Database error: {e}", EXIT_ERROR)
        return EXIT_ERROR
    if not rows:
        print("No systems registered.")
        return EXIT_SUCCESS
    header = f"{'NAME':<20} {'NOTE':<20} {'DELIVERED':<10}"
    print(header)
    print("-" * len(header))
    for name, note, delivered in rows:
        print(f"{name:<20} {note or '':<20} {delivered:<10}")
    return EXIT_SUCCESS


def handle_systems_rename(db_path: str, name: str, new_name: str) -> int:
    error = validate_schema_or_error(db_path)
    if error is not None:
        return error
    if not _valid_moniker(new_name):
        print_error(
            "System name must be a non-empty word (no spaces, newlines)", EXIT_ERROR
        )
        return EXIT_ERROR
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "UPDATE systems SET name = ? WHERE name = ? AND retired = 0",
            (new_name, name),
        )
        if cursor.rowcount == 0:
            print_error(f"Unknown system '{name}'", EXIT_ERROR)
            return EXIT_ERROR
        conn.commit()
    except sqlite3.IntegrityError:
        print_error(f"System '{new_name}' already exists", EXIT_ERROR)
        return EXIT_ERROR
    except sqlite3.Error as e:
        logger.error("systems rename failed: %s", e)
        print_error(f"Database error: {e}", EXIT_ERROR)
        return EXIT_ERROR
    finally:
        conn.close()
    print(f"Renamed system '{name}' to '{new_name}'")
    logger.info("System renamed: %s -> %s", name, new_name)
    return EXIT_SUCCESS


def handle_systems_remove(db_path: str, name: str) -> int:
    error = validate_schema_or_error(db_path)
    if error is not None:
        return error
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "UPDATE systems SET retired = 1 WHERE name = ? AND retired = 0",
            (name,),
        )
        if cursor.rowcount == 0:
            print_error(f"Unknown system '{name}'", EXIT_ERROR)
            return EXIT_ERROR
        conn.commit()
    except sqlite3.Error as e:
        logger.error("systems remove failed: %s", e)
        print_error(f"Database error: {e}", EXIT_ERROR)
        return EXIT_ERROR
    finally:
        conn.close()
    print(f"Removed system '{name}'")
    logger.info("System removed: %s", name)
    return EXIT_SUCCESS
