import json
import logging
import sqlite3

from package_mgmt.lib.model.dependency import deps_satisfied, deserialize_depends
from package_mgmt.lib.service.db_init import validate_schema_or_error
from package_mgmt.lib.util.errors import EXIT_SUCCESS, EXIT_ERROR
from package_mgmt.lib.util.versions import satisfies_constraint

logger = logging.getLogger(__name__)


def handle_verify_graph(
    db_path: str,
    package: str | None = None,
    max_report_depth: int = 0,
    max_source_depth: int = -1,
) -> int:
    error = validate_schema_or_error(db_path)
    if error is not None:
        return error

    conn = sqlite3.connect(db_path)

    total_packages = conn.execute("SELECT count(*) FROM packages").fetchone()[0]
    total_binaries = conn.execute(
        "SELECT count(*) FROM binaries WHERE status = 'current'"
    ).fetchone()[0]
    total_sources = conn.execute("SELECT count(*) FROM sources").fetchone()[0]

    unresolved = _find_unresolved_deps(conn, package, max_report_depth)
    unlinked = _find_unlinked_binaries(conn, package)
    circular = _find_circular(conn, package)

    build_dep_links, build_tools, unresolved_build, missing_build = _find_build_dep_issues(
        conn, package, max_report_depth
    )

    # Depth gate: at a finite max_source_dep_depth (0/positive N),
    # binaries without a binary_to_source link are out of scope by design —
    # informational only, never defects (COMPLETE is decided by the runtime
    # closure + the buildability of the fetched sources). At `-1`
    # (unlimited) unlinked binaries are defects.
    finite_source_depth = max_source_depth != -1
    beyond_source_depth = unlinked if finite_source_depth else []
    if finite_source_depth:
        unlinked = []

    has_issues = (
        len(unresolved) > 0
        or len(unlinked) > 0
        or len(unresolved_build) > 0
        or len(missing_build) > 0
    )
    runtime_status = "INCOMPLETE" if (len(unresolved) > 0 or len(unlinked) > 0) else "COMPLETE"
    build_status = "INCOMPLETE" if (len(unresolved_build) > 0 or len(missing_build) > 0) else "COMPLETE"

    conn.close()

    print("Dependency Graph Verification")
    print("─────────────────────────────")
    print(f"Total packages:         {total_packages}")
    print(f"Total binaries:         {total_binaries}")
    print(f"Total sources:          {total_sources}")
    print(f"Unresolved deps:        {len(unresolved)}")
    for dep in unresolved:
        print(f"  - {dep}")
    print(f"Unlinked binaries:      {len(unlinked)}")
    for item in unlinked:
        print(f"  - {item}")
    if finite_source_depth:
        print(f"Beyond source depth:    {len(beyond_source_depth)} (informational)")
        for item in beyond_source_depth:
            print(f"  - {item}")
    print(f"Circular dependencies:  {len(circular)}")
    for item in circular:
        print(f"  - {item}")
    print(f"Runtime Status:         {runtime_status}")

    print()
    print("Build-Tool Dependencies")
    print("───────────────────────")
    print(f"Total build-dep links:  {build_dep_links}")
    print(f"Total build tools:      {build_tools}")
    print(f"Unresolved build deps:  {len(unresolved_build)}")
    for item in unresolved_build:
        print(f"  - {item}")
    print(f"Missing build tools:    {len(missing_build)}")
    for item in missing_build:
        print(f"  - {item}")
    print(f"Build Status:           {build_status}")

    print()
    print(f"Status:                 {'INCOMPLETE' if has_issues else 'COMPLETE'}")

    return EXIT_SUCCESS if not has_issues else EXIT_ERROR


def _find_unresolved_deps(conn: sqlite3.Connection, package: str | None, max_depth: int = 0) -> list[str]:
    unresolved = []
    cursor = conn.cursor()

    if package:
        if max_depth < 0:
            # -1 = report all levels for the package.
            cursor.execute(
                "SELECT package_name, version, dependencies FROM binaries WHERE package_name = ? AND status = 'current'",
                (package,),
            )
        else:
            # The package's build-tool levels obey the same report-depth
            # filter as the full-cache path.
            cursor.execute(
                """SELECT b.package_name, b.version, b.dependencies FROM binaries b
                   LEFT JOIN (
                       SELECT binary_id, MIN(build_dep_depth) AS min_depth
                       FROM source_build_deps GROUP BY binary_id
                   ) sbd ON b.id = sbd.binary_id
                   WHERE b.package_name = ? AND b.status = 'current'
                     AND COALESCE(sbd.min_depth, 0) <= ?""",
                (package, max_depth),
            )
    elif max_depth < 0:
        # -1 = report all levels.
        cursor.execute(
            "SELECT package_name, version, dependencies FROM binaries WHERE status = 'current'"
        )
    else:
        # 0 = only the always-on depth-0 runtime check (no build-tool levels);
        # positive N = the depth-0 check plus build-tool levels up to N
        # Binaries that are not build tools (no source_build_deps
        # link) have min_depth NULL -> 0, so they are always in scope.
        cursor.execute(
            """SELECT b.package_name, b.version, b.dependencies FROM binaries b
               LEFT JOIN (
                   SELECT binary_id, MIN(build_dep_depth) AS min_depth
                   FROM source_build_deps GROUP BY binary_id
               ) sbd ON b.id = sbd.binary_id
               WHERE b.status = 'current' AND COALESCE(sbd.min_depth, 0) <= ?""",
            (max_depth,),
        )

    for pkg_name, version, deps_json in cursor.fetchall():
        if not deps_json:
            continue
        dep_specs = deserialize_depends(deps_json)
        for spec in dep_specs:
            dep_name = spec.name.split(":")[0]
            if deps_satisfied(conn, dep_name, spec):
                continue
            version_info = f" ({spec.version_constraint['operator']} {spec.version_constraint['version']})" if spec.version_constraint else ""
            unresolved.append(f"{dep_name}{version_info} — no binary record for matching version")

    return unresolved


def _find_unlinked_binaries(conn: sqlite3.Connection, package: str | None) -> list[str]:
    unlinked = []
    if package:
        rows = conn.execute(
            """SELECT b.package_name, b.version FROM binaries b
               LEFT JOIN binary_to_source bts ON b.id = bts.binary_id
               WHERE bts.binary_id IS NULL AND b.package_name = ? AND b.status = 'current'""",
            (package,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT b.package_name, b.version FROM binaries b
               LEFT JOIN binary_to_source bts ON b.id = bts.binary_id
               WHERE bts.binary_id IS NULL AND b.status = 'current'""",
        ).fetchall()
    for name, ver in rows:
        unlinked.append(f"{name} (= {ver})")

    return unlinked


def _find_circular(conn: sqlite3.Connection, package: str | None) -> list[str]:
    circular: list[str] = []
    if package:
        rows = conn.execute(
            "SELECT package_name, dependencies FROM binaries"
            " WHERE circular = 1 AND status = 'current' AND package_name = ?",
            (package,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT package_name, dependencies FROM binaries"
            " WHERE circular = 1 AND status = 'current'"
        ).fetchall()
    for pkg_name, deps_json in rows:
        if not deps_json:
            continue
        for spec in deserialize_depends(deps_json):
            if spec.circular:
                circular.append(f"{pkg_name} -> {spec.name} (circular)")
    return circular


def _linked_binary_satisfies(conn: sqlite3.Connection, dep_name: str, vc: dict | None, linked_rows) -> bool:
    """True if any linked (current) binary satisfies the build-dep entry —
    directly by name, or as a virtual provider (versioned provides checked
    against the constraint, unversioned provides checked by provider version)."""
    for bid, bname, bver, bstatus in linked_rows:
        if bstatus != "current":
            continue
        if bname == dep_name:
            if satisfies_constraint(bver, vc):
                return True
            continue
        provides_rows = conn.execute(
            "SELECT provides_version FROM provides WHERE virtual_name = ? AND package_name = ? AND version = ?",
            (dep_name, bname, bver),
        ).fetchall()
        for (provides_version,) in provides_rows:
            if satisfies_constraint(provides_version or bver, vc):
                return True
    return False


def _cache_has_usable_tool(conn: sqlite3.Connection, dep_name: str, vc: dict | None) -> bool:
    direct = conn.execute(
        "SELECT version FROM binaries WHERE package_name = ? AND status = 'current'",
        (dep_name,),
    ).fetchall()
    if any(satisfies_constraint(ver, vc) for (ver,) in direct):
        return True
    providers = conn.execute(
        """SELECT b.version, p.provides_version FROM provides p
           JOIN binaries b ON b.package_name = p.package_name
           WHERE p.virtual_name = ? AND b.status = 'current'""",
        (dep_name,),
    ).fetchall()
    return any(
        satisfies_constraint(pv or bv, vc) for bv, pv in providers
    )


def _find_build_dep_issues(
    conn: sqlite3.Connection,
    package: str | None,
    max_report_depth: int = -1,
) -> tuple[int, int, list[str], list[str]]:
    if package:
        cursor = conn.execute(
            """SELECT sbd.source_id, sbd.binary_id FROM source_build_deps sbd
               JOIN sources s ON s.id = sbd.source_id
               WHERE s.package_name = ?""",
            (package,),
        )
    else:
        cursor = conn.execute("SELECT source_id, binary_id FROM source_build_deps")

    all_links = cursor.fetchall()
    total_links = len(all_links)
    unique_binaries = len(set(bid for _, bid in all_links))

    unresolved: list[str] = []
    missing: list[str] = []
    # The report-depth gate mirrors _find_unresolved_deps: a source is in
    # scope when its shallowest build-dep level is within max_report_depth
    # (0 = no build-tool levels reported, -1 = all, N = up to N).
    depth_clause = (
        " AND COALESCE(sbd.min_depth, 0) <= ?" if max_report_depth >= 0 else ""
    )
    if package:
        src_cursor = conn.execute(
            """SELECT s.id, s.package_name, s.build_depends
               FROM sources s
               LEFT JOIN (
                   SELECT source_id, MIN(build_dep_depth) AS min_depth
                   FROM source_build_deps GROUP BY source_id
               ) sbd ON s.id = sbd.source_id
               WHERE s.package_name = ? AND s.build_depends IS NOT NULL"""
            + depth_clause,
            (package, max_report_depth) if max_report_depth >= 0 else (package,),
        )
    else:
        src_cursor = conn.execute(
            """SELECT s.id, s.package_name, s.build_depends
               FROM sources s
               LEFT JOIN (
                   SELECT source_id, MIN(build_dep_depth) AS min_depth
                   FROM source_build_deps GROUP BY source_id
               ) sbd ON s.id = sbd.source_id
               WHERE s.build_depends IS NOT NULL"""
            + depth_clause,
            (max_report_depth,) if max_report_depth >= 0 else (),
        )

    for src_id, src_name, build_depends_json in src_cursor.fetchall():
        try:
            bd_entries = json.loads(build_depends_json)
        except (json.JSONDecodeError, TypeError):
            continue
        linked_rows = conn.execute(
            """SELECT b.id, b.package_name, b.version, b.status
               FROM source_build_deps sbd
               JOIN binaries b ON b.id = sbd.binary_id
               WHERE sbd.source_id = ?""",
            (src_id,),
        ).fetchall()
        for entry in bd_entries:
            dep_names = [entry.get("name", "").split(":")[0]]
            name_vc = {dep_names[0]: entry.get("version_constraint")}
            for alt in entry.get("alternatives", ()):
                if isinstance(alt, dict):
                    alt_name = alt.get("name", "").split(":")[0]
                    alt_vc = alt.get("version_constraint")
                else:
                    alt_name = alt.split(":")[0]
                    alt_vc = None
                dep_names.append(alt_name)
                name_vc[alt_name] = alt_vc
            satisfied = False
            for n in dep_names:
                if _linked_binary_satisfies(conn, n, name_vc.get(n), linked_rows):
                    satisfied = True
                    break
            if satisfied:
                continue
            unsat = []
            for n in dep_names:
                if _cache_has_usable_tool(conn, n, name_vc.get(n)):
                    unsat.append(n)
            label = " | ".join(dep_names)
            if unsat:
                unresolved.append(f"{label} — usable record exists but not linked for matching version (required by: {src_name})")
            else:
                missing.append(f"{label} — required by {src_name}, no record in cache")

    return total_links, unique_binaries, unresolved, missing
