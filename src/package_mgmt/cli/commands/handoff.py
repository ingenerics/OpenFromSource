import sqlite3

from package_mgmt.lib.service.db_init import validate_schema_or_error
from package_mgmt.lib.service.handoff import HandoffError, generate_handoff
from package_mgmt.lib.service.snapshot import mark_delivered, marked_total, system_bit
from package_mgmt.lib.util.errors import (
    print_error,
    EXIT_SUCCESS,
    EXIT_ERROR,
)
from package_mgmt.lib.util.paths import handoff_dir, handoff_script_path


def _print_summary(config, system: str, snapshot_total: int, stats) -> None:
    if len(stats.releases) == 1:
        components = ", ".join(stats.components)
        per_component = ", ".join(
            f"{component.split('/', 1)[1]}: {count}"
            for component, count in stats.per_component.items()
        )
        release_str = stats.release
    else:
        components = "; ".join(
            f"{rel}: {', '.join(comps)}" for rel, comps in stats.release_components.items()
        )
        per_component = ", ".join(
            f"{component}: {count}" for component, count in stats.per_component.items()
        )
        release_str = ", ".join(stats.releases)
    print(f"Handoff ready: {handoff_dir(config.binary_cache)}")
    print(f"Release:        {release_str}")
    print(f"Components:     {components}")
    print(f"Packages:       {stats.packages} ({per_component})")
    print("Signed:         yes (key offline-cache.gpg)")
    print(f"Bootstrap:      {handoff_script_path(config.binary_cache)}")
    print(f"Snapshot:       {snapshot_total} binaries marked for '{system}'")
    if stats.packages == 0:
        print("Warning:        0 packages — nothing verified yet; run download first?")
    if stats.excluded_pool:
        print(
            f"Warning:        {len(stats.excluded_pool)} verified records excluded "
            "(missing pool file — run download)"
        )
    if stats.excluded_config:
        print(
            f"Warning:        {len(stats.excluded_config)} verified records excluded "
            "(release/component no longer configured)"
        )
    if stats.degraded_local:
        print(
            f"Warning:        {len(stats.degraded_local)} local record(s) served from stored "
            "metadata (unreadable pool payload)"
        )


def handle_handoff(db_path: str, config, system: str | None) -> int:
    if not system:
        print_error("handoff requires --system <moniker>", EXIT_ERROR)
        return EXIT_ERROR

    error = validate_schema_or_error(db_path)
    if error is not None:
        return error

    try:
        conn = sqlite3.connect(db_path)
        try:
            # Validate the moniker before generating anything: an unknown or
            # retired system must leave the cache untouched.
            system_bit(conn, system)
            stats = generate_handoff(config, conn)
            mark_delivered(conn, system, stats.delivered_ids)
            snapshot_total = marked_total(conn, system)
        finally:
            conn.close()
    except ValueError as e:
        print_error(str(e), EXIT_ERROR)
        return EXIT_ERROR
    except HandoffError as e:
        print_error(str(e), EXIT_ERROR)
        return EXIT_ERROR
    except sqlite3.Error as e:
        # Console log suppressed: the error is reported as a JSON document on
        # stderr (contracts/cli.md Phase 6), which must carry no other text.
        print_error(f"Database error: {e}", EXIT_ERROR)
        return EXIT_ERROR

    _print_summary(config, system, snapshot_total, stats)
    # The per-record warnings are redundant with _print_summary's Warning
    # lines and the JSON error below; emitting them to stderr would break the
    # "exactly one JSON document on error" contract, so only the JSON payload
    # (and the stdout summary) carry the exclusion detail.
    if stats.unconfirmable:
        print_error(
            f"handoff: {len(stats.unconfirmable)} verified records excluded "
            "(no index entry or pool file); run download or verify",
            EXIT_ERROR,
        )
        return EXIT_ERROR
    return EXIT_SUCCESS
