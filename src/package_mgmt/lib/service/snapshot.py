"""Delivery snapshot service (spec 010): marks current+verified binaries as
delivered to a system moniker.

Shared by the `snapshot` command and the `handoff` command (spec 012,
research Decision 8): `handoff --system <moniker>` takes the snapshot at
generation time, so the delivery record and the physical handoff always
coincide. The marking is idempotent — re-running ORs the bit again, never
double-counting.
"""

import sqlite3


def system_bit(conn: sqlite3.Connection, system_name: str) -> int:
    """The moniker's `systems.bit`, raising ValueError if unknown/retired."""
    row = conn.execute(
        "SELECT bit FROM systems WHERE name = ? AND retired = 0", (system_name,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown system '{system_name}'")
    return row[0]


def pending_count(conn: sqlite3.Connection, bit: int) -> int:
    """Current+verified binaries not yet marked with `bit`."""
    return conn.execute(
        "SELECT COUNT(*) FROM binaries"
        " WHERE status = 'current' AND verified = 1 AND (sync_mask & ?) = 0",
        (bit,),
    ).fetchone()[0]


def marked_total(conn: sqlite3.Connection, system_name: str) -> int:
    """Current+verified binaries marked for the moniker (after any snapshot)."""
    bit = system_bit(conn, system_name)
    return conn.execute(
        "SELECT COUNT(*) FROM binaries"
        " WHERE status = 'current' AND verified = 1 AND (sync_mask & ?) != 0",
        (bit,),
    ).fetchone()[0]


def take_snapshot(conn: sqlite3.Connection, system_name: str) -> int:
    """Mark every current+verified binary as delivered to `system_name`.

    Returns the number of binaries newly marked (the pending count).
    Raises ValueError for an unknown or retired system. Commits the marking.
    """
    bit = system_bit(conn, system_name)
    pending = pending_count(conn, bit)
    conn.execute(
        "UPDATE binaries SET sync_mask = sync_mask | ?"
        " WHERE status = 'current' AND verified = 1",
        (bit,),
    )
    conn.commit()
    return pending


def mark_delivered(conn: sqlite3.Connection, system_name: str, binary_ids: list[int]) -> int:
    """Mark exactly the given binary ids as delivered to `system_name`.

    Unlike `take_snapshot` (which marks every current+verified binary), this
    marks only the ids the caller supplies — the handoff's actual delivery set,
    so binaries the handoff could not include (missing pool file, unconfirmable
    index entry) are never recorded as delivered. Only current+verified rows
    are eligible. Returns the number newly marked (rows whose bit actually
    changed); the marking is idempotent (bitwise OR, no double-counting).
    """
    bit = system_bit(conn, system_name)
    if not binary_ids:
        return 0
    placeholders = ",".join("?" for _ in binary_ids)
    newly = conn.execute(
        f"SELECT COUNT(*) FROM binaries"
        f" WHERE id IN ({placeholders}) AND status = 'current' AND verified = 1"
        f" AND (sync_mask & ?) = 0",
        [*binary_ids, bit],
    ).fetchone()[0]
    conn.execute(
        f"UPDATE binaries SET sync_mask = sync_mask | ?"
        f" WHERE id IN ({placeholders}) AND status = 'current' AND verified = 1",
        [bit, *binary_ids],
    )
    conn.commit()
    return newly
