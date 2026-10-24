import logging
import sqlite3
import time

from package_mgmt.lib.service.db_init import validate_schema_or_error
from package_mgmt.lib.service.downloader import download_pending_binaries, download_pending_sources
from package_mgmt.lib.util.errors import (
    print_error,
    EXIT_SUCCESS,
    EXIT_ERROR,
)
from package_mgmt.lib.util.progress import PhaseProgress, format_elapsed, format_bytes

logger = logging.getLogger(__name__)

CONSOLE_ERROR_CAP = 50


def _http_histogram(stats) -> str:
    """`200:3 404:2 429:1` — each status observed once per request attempt."""
    return " ".join(
        f"{code}:{count}"
        for code, count in sorted(stats.status_counts.items())
    )


def _phase_summary_lines(phase: str, stats, elapsed: float) -> list[str]:
    lines = [
        f"{phase} phase summary",
        "─" * 20,
        f"Files downloaded:  {stats.files_downloaded}",
        f"Bytes:             {format_bytes(stats.bytes_downloaded)}",
        f"Files verified:    {stats.files_verified}",
        f"HTTP statuses:     {_http_histogram(stats) or 'none'}",
        f"Network errors:    {stats.network_errors}",
    ]
    failures = stats.failures
    if failures:
        total = len(failures)
        if total > CONSOLE_ERROR_CAP:
            lines.append(
                f"Failed:            {total} (showing first {CONSOLE_ERROR_CAP}; "
                f"{total - CONSOLE_ERROR_CAP} more in the log)"
            )
        else:
            lines.append(f"Failed:            {total}")
        for failure in failures[:CONSOLE_ERROR_CAP]:
            lines.append(f"  - {failure}")
    else:
        lines.append("Failed:            0")
    lines.append(f"Elapsed:           {format_elapsed(elapsed)}")
    return lines


def _print_phase_summary(phase: str, stats, elapsed: float) -> None:
    lines = _phase_summary_lines(phase, stats, elapsed)
    for line in lines:
        print(line)
    # One concise log line (never the full block): the block is printed to
    # stdout exactly once, and re-logging it would duplicate every row on the
    # console (logging's StreamHandler echoes to stderr). The HTTP histogram
    # rides along so throttling (429/503) vs plain latency is visible in logs
    # without the stdout block. Per-failure rows are logged individually for
    # traceability (Constitution VIII).
    logger.info(
        "download %s phase: %d files downloaded, %d verified, %d failed "
        "(http: %s, net errors: %d)",
        phase.lower(), stats.files_downloaded, stats.files_verified,
        len(stats.failures), _http_histogram(stats) or "none", stats.network_errors,
    )
    for failure in stats.failures:
        logger.warning("download %s failure: %s", phase.lower(), failure)


def _run_phase(config, conn: sqlite3.Connection, engine, phase: str):
    """Run one phase through its engine; returns (stats, elapsed_seconds)."""
    progress = PhaseProgress(0, 0)
    start = time.monotonic()
    stats = engine(config, conn, progress=progress)
    elapsed = time.monotonic() - start
    progress.done()
    if stats.planned_files == 0:
        print(f"{phase} phase: nothing to download.")
        logger.info("download %s phase: nothing to download", phase.lower())
    else:
        print()
        _print_phase_summary(phase, stats, elapsed)
    return stats, elapsed


def handle_download(db_path: str, config) -> int:
    error = validate_schema_or_error(db_path)
    if error is not None:
        return error

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        binary_stats, binary_elapsed = _run_phase(
            config, conn, download_pending_binaries, "Binary"
        )
        source_stats, source_elapsed = _run_phase(
            config, conn, download_pending_sources, "Source"
        )
    except sqlite3.Error as e:
        logger.error("download failed: %s", e)
        print_error(f"Database error: {e}", EXIT_ERROR)
        return EXIT_ERROR
    except Exception as e:
        logger.error("download failed: %s", e)
        print_error(f"download failed: {e}", EXIT_ERROR)
        return EXIT_ERROR
    finally:
        if conn is not None:
            conn.close()

    for message in binary_stats.command_errors:
        print_error(message, EXIT_ERROR)
    for message in source_stats.command_errors:
        print_error(message, EXIT_ERROR)

    total_files = binary_stats.files_downloaded + source_stats.files_downloaded
    total_verified = binary_stats.files_verified + source_stats.files_verified
    total_failed = (
        len(binary_stats.failures) + len(binary_stats.command_errors)
        + len(source_stats.failures) + len(source_stats.command_errors)
    )
    total_elapsed = binary_elapsed + source_elapsed
    print(
        f"Total: {total_files} files downloaded, {total_verified} verified, "
        f"{total_failed} failed ({format_elapsed(total_elapsed)})"
    )

    failed = bool(
        binary_stats.failures or binary_stats.command_errors
        or source_stats.failures or source_stats.command_errors
    )
    logger.info(
        "download completed: %d files downloaded, %d verified, %d failed, %d command errors",
        total_files, total_verified,
        len(binary_stats.failures) + len(source_stats.failures),
        len(binary_stats.command_errors) + len(source_stats.command_errors),
    )
    return EXIT_ERROR if failed else EXIT_SUCCESS
