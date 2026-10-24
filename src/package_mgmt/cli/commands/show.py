import json
import logging
import sqlite3

from package_mgmt.cli.commands.list import _build_tree, _print_text_tree
from package_mgmt.lib.service.db_init import validate_schema_or_error
from package_mgmt.lib.util.errors import print_error, EXIT_SUCCESS, EXIT_ERROR

logger = logging.getLogger(__name__)

KNOWN_TABLES = {
    "packages": "name",
    "binaries": "package_name",
    "sources": "package_name",
    "binary_to_source": None,
    "source_build_deps": None,
    "provides": "package_name",
    "virtual_resolutions": None,
}

_TABLE_ORDER = ["packages", "binaries", "sources", "binary_to_source", "source_build_deps", "provides", "virtual_resolutions"]

_VIRTUAL_RESOLUTION_COLS = ["virtual_name", "resolved_package", "version", "status", "release"]


def _get_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cursor = conn.execute("PRAGMA table_info({})".format(table))
    return [row[1] for row in cursor.fetchall()]


def _format_text_table(table_name: str, cols: list[str], rows: list[dict]) -> str:
    widths = []
    for c in cols:
        max_w = len(c)
        for row in rows:
            v = row.get(c)
            s = "NULL" if v is None else str(v)
            max_w = max(max_w, len(s))
        widths.append(max_w)

    lines = [table_name, "─" * len(table_name)]
    header = " │ ".join(c.ljust(w) for c, w in zip(cols, widths))
    lines.append(" " + header)
    sep = "─┼─".join("─" * w for w in widths)
    lines.append("─" + sep)
    for row in rows:
        vals = []
        for c, w in zip(cols, widths):
            v = row.get(c)
            s = "NULL" if v is None else str(v)
            vals.append(s.ljust(w))
        lines.append(" " + " │ ".join(vals))
    return "\n".join(lines)


def _format_json(tables: dict[str, list[dict]]) -> str:
    return json.dumps(tables, indent=2)


def _collect_dep_names(conn: sqlite3.Connection, package: str) -> list[str]:
    tree = _build_tree(conn, package)
    names = []
    def walk(node):
        names.append(node["name"])
        for dep in node.get("dependencies", []):
            walk(dep)
    walk(tree)
    names = list(dict.fromkeys(names))

    placeholders = ",".join("?" for _ in names)
    build_tool_names = conn.execute(
        """SELECT DISTINCT b.package_name FROM source_build_deps sbd
           JOIN sources s ON s.id = sbd.source_id
           JOIN binaries b ON b.id = sbd.binary_id
           WHERE s.package_name IN ({})""".format(placeholders),
        names,
    ).fetchall()
    for (bt_name,) in build_tool_names:
        if bt_name not in names:
            names.append(bt_name)
    return names


def _query_multi(conn: sqlite3.Connection, table: str, packages: list[str]) -> tuple[list[str], list[dict]]:
    if table == "virtual_resolutions":
        cols = _VIRTUAL_RESOLUTION_COLS
    else:
        cols = _get_columns(conn, table)
    placeholders = ",".join("?" for _ in packages)
    if table == "binary_to_source":
        rows = conn.execute(
            """SELECT DISTINCT bts.* FROM binary_to_source bts
               JOIN binaries b ON b.id = bts.binary_id
               WHERE b.package_name IN ({})""".format(placeholders),
            packages,
        ).fetchall()
    elif table == "source_build_deps":
        rows = conn.execute(
            """SELECT DISTINCT sbd.* FROM source_build_deps sbd
               JOIN sources s ON s.id = sbd.source_id
               WHERE s.package_name IN ({})""".format(placeholders),
            packages,
        ).fetchall()
    elif table == "virtual_resolutions":
        rows = conn.execute(
            """SELECT DISTINCT vr.virtual_name, b.package_name, b.version, b.status, b.release
               FROM virtual_resolutions vr
               JOIN binaries b ON b.id = vr.resolved_binary_id
               WHERE b.package_name IN ({})""".format(placeholders),
            packages,
        ).fetchall()
    else:
        where_col = KNOWN_TABLES[table]
        rows = conn.execute(
            "SELECT * FROM {} WHERE {} IN ({})".format(table, where_col, placeholders),
            packages,
        ).fetchall()
    return cols, [dict(zip(cols, row)) for row in rows]


def handle_show(db_path: str, package: str, tables: list[str] | None = None, fmt: str = "text", deps: bool = False) -> int:
    if tables is None:
        tables = _TABLE_ORDER

    for t in tables:
        if t not in KNOWN_TABLES:
            print_error(
                f"Invalid table name: '{t}'. Valid: {', '.join(KNOWN_TABLES.keys())}",
                EXIT_ERROR,
            )
            return EXIT_ERROR

    error = validate_schema_or_error(db_path)
    if error is not None:
        return error

    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as e:
        print_error(f"Database error: {e}", EXIT_ERROR)
        return EXIT_ERROR

    try:
        if conn.execute(
            """SELECT 1 WHERE
                 EXISTS (SELECT 1 FROM packages WHERE name = ?)
              OR EXISTS (SELECT 1 FROM binaries WHERE package_name = ?)
              OR EXISTS (SELECT 1 FROM sources WHERE package_name = ?)
              OR EXISTS (SELECT 1 FROM virtual_resolutions vr
                         JOIN binaries b ON b.id = vr.resolved_binary_id
                         WHERE b.package_name = ?)""",
            (package, package, package, package),
        ).fetchone() is None:
            print_error(f"Package not found: {package}", EXIT_ERROR)
            return EXIT_ERROR
        packages = _collect_dep_names(conn, package) if deps else [package]
        table_data = {}
        for t in tables:
            cols, rows = _query_multi(conn, t, packages)
            table_data[t] = {"cols": cols, "rows": rows}

        if fmt == "json":
            table_output = {t: data["rows"] for t, data in table_data.items()}
            if deps:
                output = {"tables": table_output, "dependency_tree": _build_tree(conn, package)}
            else:
                output = table_output
            print(_format_json(output))
        else:
            for t in tables:
                data = table_data[t]
                print(_format_text_table(t, data["cols"], data["rows"]))
                print()
            if deps:
                row = conn.execute("SELECT status FROM packages WHERE name = ?", (package,)).fetchone()
                pkg_status = row[0] if row else "unknown"
                print("Dependency Tree")
                print("───────────────")
                _print_text_tree(_build_tree(conn, package), package, pkg_status)
    finally:
        conn.close()

    return EXIT_SUCCESS
