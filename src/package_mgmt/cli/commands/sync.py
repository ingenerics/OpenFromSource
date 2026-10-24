import sqlite3

from package_mgmt.lib.service.db_init import validate_schema_or_error
from package_mgmt.lib.service.handoff import HandoffError, generate_diff
from package_mgmt.lib.service.snapshot import mark_delivered, marked_total, pending_count, system_bit
from package_mgmt.lib.util.errors import (
    print_error,
    EXIT_SUCCESS,
    EXIT_ERROR,
)
from package_mgmt.lib.util.paths import diff_dir, diff_script_path


def _print_summary(config, system: str, stats, snapshot_total: int) -> None:
    new = stats.new_count
    prior = snapshot_total - new
    if len(stats.releases) == 1:
        components = ", ".join(stats.components)
        per_component = ", ".join(
            f"{component.split('/', 1)[1]}: {count}"
            for component, count in stats.per_component_new.items()
        )
        release_str = stats.release
    else:
        components = "; ".join(
            f"{rel}: {', '.join(comps)}" for rel, comps in stats.release_components.items()
        )
        per_component = ", ".join(
            f"{component}: {count}" for component, count in stats.per_component_new.items()
        )
        release_str = ", ".join(stats.releases)
    print(f"Sync ready:      {diff_dir(config.binary_cache)}")
    print(f"Release:         {release_str}")
    print(f"Components:      {components}")
    print(f"New packages:    {new} ({per_component})")
    print(f"Total offered:   {snapshot_total} (prior {prior} + new {new})")
    print("Signed:          yes (key offline-cache.gpg, same as handoff)")
    print(f"Apply:           {diff_script_path(config.binary_cache)}")
    print(f"Snapshot:        {new} binaries marked for '{system}'")
    if stats.degraded_local:
        print(
            f"Warning:        {len(stats.degraded_local)} local record(s) served from stored "
            "metadata (unreadable pool payload)"
        )


def handle_sync(db_path: str, config, system: str | None) -> int:
    if not system:
        print_error("sync requires --system <moniker>", EXIT_ERROR)
        return EXIT_ERROR

    error = validate_schema_or_error(db_path)
    if error is not None:
        return error

    try:
        conn = sqlite3.connect(db_path)
        try:
            # Validate the moniker before generating anything: an unknown or
            # retired system must leave the cache untouched.
            bit = system_bit(conn, system)
            if pending_count(conn, bit) == 0:
                # A zero-pending sync still regenerates the diff as empty:
                # the previous run's content is never left stale. No new
                # packages ship, so nothing is marked delivered.
                generate_diff(config, conn, bit)
                print(f"Sync ready:      {diff_dir(config.binary_cache)}")
                print(f"New packages:    0 — nothing new since the last sync for '{system}'")
                print("Snapshot:        no change")
                return EXIT_SUCCESS
            stats = generate_diff(config, conn, bit)
            # An excluded delivered record is a defect state: the delivery
            # record must keep matching the operator's payload, so nothing is
            # marked (contracts/cli.md).
            if stats.excluded_delivered:
                # Console logs suppressed: the error is reported as a JSON
                # document on stderr (contracts/cli.md Phase 6), which must
                # carry no other text; the payload names the counts.
                print_error(
                    f"sync: {len(stats.excluded_delivered)} delivered records no longer "
                    "confirmable (missing index entry or pool file); the delivery "
                    "record must keep matching the payload",
                    EXIT_ERROR,
                )
                return EXIT_ERROR
            # Delivery advances only on a clean result: any excluded pending
            # record leaves the delivery record untouched, so the
            # failed record stays pending and the next run can retry it.
            if stats.excluded_pending:
                print_error(
                    f"sync: {len(stats.excluded_pending)} pending records excluded "
                    "(no index entry or pool file); run download or verify",
                    EXIT_ERROR,
                )
                return EXIT_ERROR
            mark_delivered(conn, system, stats.shipped_ids)
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

    _print_summary(config, system, stats, snapshot_total)
    return EXIT_SUCCESS
