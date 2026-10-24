import json
import logging
import sqlite3

from package_mgmt.lib.model.dependency import deserialize_depends
from package_mgmt.lib.service.db_init import validate_schema_or_error
from package_mgmt.lib.util.errors import print_error, EXIT_SUCCESS, EXIT_ERROR

logger = logging.getLogger(__name__)


def handle_list_deps(db_path: str, package: str | None = None, fmt: str = "text",
                     full: bool = False, include_build_deps: bool = False,
                     status: str | None = None) -> int:
    error = validate_schema_or_error(db_path)
    if error is not None:
        return error

    conn = sqlite3.connect(db_path)

    if status:
        result = _list_by_status(conn, status)
        conn.close()
        if fmt == "json":
            print(json.dumps(result, indent=2))
        else:
            _print_status_table(result)
        return EXIT_SUCCESS

    if not package:
        print_error("Either --deps or --status is required", EXIT_ERROR)
        conn.close()
        return EXIT_ERROR

    cursor = conn.cursor()
    cursor.execute("SELECT name, status FROM packages WHERE name = ?", (package,))
    pkg_row = cursor.fetchone()
    if not pkg_row:
        print_error(f"Package not found: {package}", EXIT_ERROR)
        conn.close()
        return EXIT_ERROR

    pkg_name, pkg_status = pkg_row

    tree = _build_tree(conn, package, full=full, include_build_deps=include_build_deps)

    if fmt == "json":
        print(json.dumps(tree, indent=2))
    else:
        _print_text_tree(tree, package, pkg_status, full=full)

    conn.close()
    return EXIT_SUCCESS


def _list_by_status(conn: sqlite3.Connection, status: str) -> list[dict]:
    cursor = conn.cursor()
    if status == "all":
        cursor.execute(
            "SELECT package_name, version, release, component, status FROM binaries ORDER BY package_name, id"
        )
    else:
        cursor.execute(
            "SELECT package_name, version, release, component, status FROM binaries WHERE status = ? ORDER BY package_name, id",
            (status,),
        )
    rows = cursor.fetchall()
    return [
        {"package": r[0], "version": r[1], "release": r[2], "component": r[3], "status": r[4]}
        for r in rows
    ]


def _print_status_table(entries: list[dict]) -> None:
    if not entries:
        print("No binaries found.")
        return
    header = f"{'Package':<20} {'Version':<20} {'Status':<15} {'Release':<20} {'Component':<15}"
    print(header)
    print("-" * len(header))
    for e in entries:
        print(f"{e['package']:<20} {e['version']:<20} {e['status']:<15} {e['release']:<20} {e['component']:<15}")


def _build_tree(conn: sqlite3.Connection, package: str, full: bool = False,
                ancestry: set | None = None, displayed: set | None = None,
                include_build_deps: bool = False) -> dict:
    if ancestry is None:
        ancestry = set()
    if displayed is None:
        displayed = set()

    if not full and package in displayed:
        return {"name": package, "suppressed": True, "dependencies": []}

    if full and package in ancestry:
        return {"name": package, "circular": True, "binary_exists": True,
                "source_exists": None, "verified": None, "dependencies": []}

    displayed.add(package)
    ancestry.add(package)

    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, version, architecture, dependencies, verified FROM binaries WHERE package_name = ? AND status = 'current' ORDER BY id LIMIT 1",
        (package,),
    )
    bin_row = cursor.fetchone()
    if not bin_row:
        ancestry.discard(package)
        return {"name": package, "binary_exists": False, "source_exists": False,
                "verified": False, "dependencies": []}

    bin_id, version, _arch, deps_json, verified = bin_row

    cursor.execute(
        """SELECT s.package_name FROM sources s
           JOIN binary_to_source bts ON s.id = bts.source_id
           JOIN binaries b ON b.id = bts.binary_id
           WHERE b.package_name = ? AND b.version = ? LIMIT 1""",
        (package, version),
    )
    source_exists = cursor.fetchone() is not None

    children = []
    if deps_json:
        dep_specs = deserialize_depends(deps_json)
        for spec in dep_specs:
            dep_name = spec.name.split(":")[0]
            child = _build_tree(conn, dep_name, full, ancestry, displayed)
            child["version_constraint"] = spec.version_constraint["version"] if spec.version_constraint else None
            children.append(child)

    if include_build_deps and len(ancestry) == 1:
        # Build tools of the source that produces this binary, attached at the
        # root node (per the `list --deps <pkg> --include-build-deps` contract:
        # the queried package's build-tool tree). Follow the binary -> source
        # link (binary_to_source) instead of matching by name, so packages
        # whose binary name differs from their source name (e.g.
        # libxkbcommon-x11-0 -> libxkbcommon) still surface their build
        # dependencies.
        cursor.execute(
            """SELECT DISTINCT b.package_name, b.version, b.verified
               FROM source_build_deps sbd
               JOIN binary_to_source bts ON bts.source_id = sbd.source_id
               JOIN binaries b ON b.id = sbd.binary_id
               WHERE bts.binary_id = ?""",
            (bin_id,),
        )
        for bt_name, bt_version, bt_verified in cursor.fetchall():
            children.append({
                "name": bt_name,
                "version": bt_version,
                "binary_exists": True,
                "source_exists": None,
                "verified": bool(bt_verified),
                "build_dep": True,
                "version_constraint": None,
                "dependencies": [],
            })

    ancestry.discard(package)

    return {
        "name": package,
        "version": version,
        "binary_exists": True,
        "source_exists": source_exists,
        "verified": bool(verified),
        "dependencies": children,
    }


def _print_text_tree(node: dict, root_name: str, status: str, prefix: str = "",
                     is_last: bool = True, is_root: bool = True, full: bool = False) -> None:
    if is_root:
        print(f"{root_name} ({status})")
    else:
        if node.get("suppressed"):
            return
        if node.get("circular"):
            connector = "└─ " if is_last else "├─ "
            print(f"{prefix}{connector}{node.get('name', '?')} (circular — already shown above)")
            return

        if node.get("build_dep"):
            connector = "└─ " if is_last else "├─ "
            version_info = f" (= {node['version']})" if node.get("version") else ""
            ver_status = "✓" if node.get("verified") else "✗"
            print(f"{prefix}{connector}[build] {node.get('name', '?')}{version_info} [binary ✓, verified: {ver_status}]")
            children = node.get("dependencies", [])
            for i, child in enumerate(children):
                child_prefix = prefix + ("  " if is_last else "│ ")
                _print_text_tree(child, "", "", child_prefix, i == len(children) - 1, is_root=False, full=full)
            return

        bin_status = "✓" if node.get("binary_exists") else "✗"
        src_exists = node.get("source_exists")
        ver_status = "✓" if node.get("verified") else "✗"
        version_info = f" (= {node['version']})" if node.get("version") else ""
        if src_exists is None:
            source_label = "source: unknown"
        else:
            source_label = "source ✓" if src_exists else "source: missing"
        connector = "└─ " if is_last else "├─ "
        print(f"{prefix}{connector}{node.get('name', '?')}{version_info} [binary {bin_status}, {source_label}, verified: {ver_status}]")

    children = node.get("dependencies", [])
    for i, child in enumerate(children):
        child_prefix = prefix + ("  " if is_last else "│ ")
        _print_text_tree(child, "", "", child_prefix, i == len(children) - 1, is_root=False, full=full)
