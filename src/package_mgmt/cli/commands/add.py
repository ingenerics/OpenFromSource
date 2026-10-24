import logging
import sqlite3

from package_mgmt.lib.model.resolution import PackageListResult, PackageRequest, ResolutionStats
from package_mgmt.lib.service.config_loader import load_config, source_depth_zero_warning
from package_mgmt.lib.service.db_init import initialize_database
from package_mgmt.lib.service.resolver import PackageResolver
from package_mgmt.lib.util.errors import (
    print_error,
    EXIT_SUCCESS,
    EXIT_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_SCHEMA_MISMATCH,
    ConfigError,
    DatabaseError,
)
from package_mgmt.lib.util.progress import make_resolved_progress

logger = logging.getLogger(__name__)

CONSOLE_ERROR_CAP = 50


def _parse_pkg_spec(arg: str) -> tuple[str, str | None]:
    if "=" in arg:
        parts = arg.split("=", 1)
        return parts[0], parts[1]
    return arg, None


def parse_package_list(path: str) -> PackageListResult:
    """Parse a package-list manifest (spec 009) into a typed result.

    One package per line: `name` or `name=version` (Debian notation, the same
    syntax positional `add` arguments use via `_parse_pkg_spec`). Lines are
    trimmed of surrounding whitespace; blank lines and lines starting with
    `#` are ignored. Validation is all-or-nothing: every malformed line is
    collected (naming the file and line number) and the caller aborts before
    any resolution when `errors` is non-empty.

    An unreadable file (missing, permission, or non-UTF-8) raises — the
    caller maps it to the contract's "Cannot read package list" error.
    """
    requests: list[PackageRequest] = []
    errors: list[str] = []
    with open(path, encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            name, version = _parse_pkg_spec(line)
            if not name or (version is not None and not version):
                errors.append(f"Malformed line {lineno} in {path}: '{line}'")
                continue
            requests.append(PackageRequest(name, version))
    return PackageListResult(requests=tuple(requests), errors=tuple(errors))


def _resolve_source(config, source_flag: str | None) -> str | None:
    if source_flag is None:
        return None
    for src in config.sources:
        if src.release == source_flag:
            return source_flag
    available = ", ".join(sorted(set(s.release for s in config.sources)))
    raise ConfigError(
        f"Source '{source_flag}' not found in configuration. "
        f"Available sources: {available}"
    )


def handle_add(
    packages: list[str], source: str | None = None, files: list[str] | None = None,
) -> int:
    if files and packages:
        print_error("Use either package names or --file, not both", EXIT_ERROR)
        return EXIT_ERROR

    if not packages and not files:
        print_error(
            "No packages specified; provide package name(s) or --file", EXIT_ERROR
        )
        return EXIT_ERROR

    for arg in packages:
        name, version = _parse_pkg_spec(arg)
        if not name or (version is not None and not version):
            print_error(
                f"Malformed package argument '{arg}': empty version pin", EXIT_ERROR
            )
            return EXIT_ERROR

    try:
        config, _ = load_config()
    except ConfigError as e:
        print_error(str(e), EXIT_CONFIG_ERROR)
        return EXIT_CONFIG_ERROR

    try:
        conn = sqlite3.connect(config.db_path)
        initialize_database(config.db_path)
    except DatabaseError as e:
        # A stale or unbuildable schema is a schema problem, not a generic
        # failure: report it with the schema-mismatch exit code like the
        # read commands do.
        conn.close()
        print_error(str(e), EXIT_SCHEMA_MISMATCH)
        return EXIT_SCHEMA_MISMATCH
    except Exception as e:
        print_error(f"Database error: {e}", EXIT_ERROR)
        return EXIT_ERROR

    try:
        primary_source = _resolve_source(config, source)
    except ConfigError as e:
        print_error(str(e), EXIT_CONFIG_ERROR)
        conn.close()
        return EXIT_CONFIG_ERROR

    resolver = PackageResolver(config, conn)
    try:
        resolver.ensure_indexes()
    except Exception as e:
        print_error(f"Index error: {e}", EXIT_ERROR)
        conn.close()
        return EXIT_ERROR

    warning = source_depth_zero_warning(config)
    if warning:
        print(f"Warning: {warning}")

    if files:
        return _handle_add_files(config, conn, resolver, files, primary_source)

    total = ResolutionStats()
    exit_code = EXIT_SUCCESS

    requests = [PackageRequest(*_parse_pkg_spec(arg)) for arg in packages]
    stats_list = resolver.resolve_batch(requests, source=primary_source)
    for arg, stats in zip(packages, stats_list):
        total = ResolutionStats(
            packages=total.packages + stats.packages,
            binaries=total.binaries + stats.binaries,
            sources=total.sources + stats.sources,
            build_tools=total.build_tools + stats.build_tools,
            build_dep_links=total.build_dep_links + stats.build_dep_links,
            virtual_count=total.virtual_count + stats.virtual_count,
            errors=total.errors + stats.errors,
        )

        resolved_from = stats.resolved_source or "unknown"
        parts = [f"{stats.packages} dependencies"]
        if stats.fallback_count:
            parts.append(f"{stats.fallback_count} from fallback")
        if stats.virtual_count:
            parts.append(f"{stats.virtual_count} virtual")
        print(f"Package {arg} resolved from {resolved_from} ({', '.join(parts)})")

    conn.close()

    for err in total.errors:
        print(f"  Error: {err}")

    if total.errors:
        return EXIT_ERROR
    return exit_code


def _handle_add_files(
    config,
    conn: sqlite3.Connection,
    resolver: PackageResolver,
    files: list[str],
    primary_source: str | None,
) -> int:
    """File-driven add: parse all manifests (all-or-nothing), resolve the
    batch, and report batch-style (progress + summary, errors capped at 50
    on the console, full list in the log) per contracts/cli.md."""
    requests: list[PackageRequest] = []
    parse_errors: list[str] = []
    for path in files:
        try:
            result = parse_package_list(path)
        except (OSError, UnicodeDecodeError) as e:
            print_error(f"Cannot read package list {path}: {e}", EXIT_ERROR)
            conn.close()
            return EXIT_ERROR
        requests.extend(result.requests)
        parse_errors.extend(result.errors)
    if parse_errors:
        print_error("\n".join(parse_errors), EXIT_ERROR)
        conn.close()
        return EXIT_ERROR
    if not requests:
        print_error(f"No package names found in {', '.join(files)}", EXIT_ERROR)
        conn.close()
        return EXIT_ERROR

    progress = make_resolved_progress()
    stats_list = resolver.resolve_batch(
        requests, source=primary_source, progress=progress
    )
    print()

    resolved = 0
    errors: list[str] = []
    for req, stats in zip(requests, stats_list):
        if stats.errors:
            errors.extend(f"{req.name}: {e}" for e in stats.errors)
        else:
            resolved += 1

    total = len(requests)
    print(f"Packages requested:  {total}")
    print(f"Packages resolved:   {resolved}")
    if errors:
        if len(errors) > CONSOLE_ERROR_CAP:
            print(
                f"Errors:              {len(errors)} (showing first {CONSOLE_ERROR_CAP}; "
                f"{len(errors) - CONSOLE_ERROR_CAP} more in the log)"
            )
        else:
            print(f"Errors:              {len(errors)}")
        for err in errors[:CONSOLE_ERROR_CAP]:
            print(f"  - {err}")
    else:
        print("Errors:              0")
    for err in errors:
        logger.warning("add --file resolution error: %s", err)
    logger.info(
        "add --file completed: %d requested, %d resolved, %d errors",
        total, resolved, len(errors),
    )
    conn.close()
    return EXIT_ERROR if errors else EXIT_SUCCESS
