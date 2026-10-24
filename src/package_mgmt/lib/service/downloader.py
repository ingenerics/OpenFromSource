"""Download engine: fetch pending files from the configured mirrors into the
cache pool, verifying each against the cached archive index checksum before
it becomes visible, and mark records verified (spec 011).

Metadata comes strictly from the cached archive indexes (index-read design,
research Decision 1); the database only drives the pending set and records
completion. Downloads are sequential (one in-flight request per mirror),
files land via `*.part` -> verify -> `os.replace`, and `verified` is committed
per file so interrupted runs converge on re-run (research Decision 4).
"""

import hashlib
import http.client
import logging
import os
import random
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version as _dist_version

from package_mgmt.lib.service.index_loader import load_binary_index, load_source_index
from package_mgmt.lib.util.fs import file_sha256
from package_mgmt.lib.util.paths import binary_index_path, resolve_index_path, safe_pool_path, source_index_path

logger = logging.getLogger(__name__)

CHUNK_SIZE = 65536
MAX_RETRY_ATTEMPTS = 5
BACKOFF_BASE = 1.0
BACKOFF_CAP = 30.0
TRANSIENT_STATUSES = (429, 503)
# Redirects: follow up to MAX_REDIRECTS hops (301/302/303/307/308) before the
# final payload is hashed; a no-Location redirect, a loop, or an over-limit
# chain is a clear permanent error, never retried and never a bogus checksum.
MAX_REDIRECTS = 5
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
# Socket idle timeout for pooled mirror connections: a server that accepts a
# request but never delivers data must abort instead of blocking the run
# forever. Slow-but-streaming downloads are unaffected (idle timeout only).
SOCKET_TIMEOUT = 10.0
# Live progress granularity: emit an `on_progress` report at most roughly
# once per MiB so the in-place bar tracks a large payload without a per-chunk
# render cost. Only ever used on a TTY (see `_live_progress`); the per-file
# committed update is unaffected.
PROGRESS_LIVE_BYTES = 1024 * 1024

try:
    _PACKAGE_VERSION = _dist_version("package_mgmt")
except PackageNotFoundError:
    _PACKAGE_VERSION = "0.0.0"

USER_AGENT = f"package_mgmt/{_PACKAGE_VERSION} (OpenFromSource offline cache builder)"


class _PooledResponse:
    """Keep-alive response wrapper: drops the pooled connection on read errors.

    A connection broken mid-read must not be reused; the wrapper evicts it
    from the opener's pool so retries always start on a fresh socket.
    """

    def __init__(self, opener: "_PooledOpener", key: tuple, response: http.client.HTTPResponse):
        self._opener = opener
        self._key = key
        self._response = response

    def read(self, size: int = -1) -> bytes:
        try:
            return self._response.read(size)
        except Exception:
            # A broken mid-read transfer is a network error, not an HTTP
            # status — counted separately so the run's histogram shows how
            # often the network, rather than the mirror, was the problem.
            self._opener.network_errors += 1
            self._opener._evict(self._key)
            raise

    def close(self) -> None:
        self._response.close()


class _RedirectError(urllib.error.HTTPError):
    """A redirect that cannot be followed (missing Location, loop, or over
    the hop limit). A permanent error — the retry layer must not back off."""

    def __init__(self, url: str, code: int, reason: str):
        super().__init__(url, code, reason, {}, None)


class _PooledOpener:
    """Keep-alive opener: one pooled HTTP(S) connection per mirror.

    `urllib.request.urlopen` opens a fresh connection per request; this
    opener reuses a single `http.client` connection per origin so long
    download phases keep one socket open per mirror (the apt-calibrated
    politeness profile). A connection is dropped from the pool on any
    request- or read-side error, so retries always start fresh. Raised
    exceptions mirror urllib's (`HTTPError`/`URLError`) so the retry layer
    is unchanged.

    Redirects (301/302/303/307/308) are followed inside the opener — bounded
    at MAX_REDIRECTS hops, loops rejected, and only the final non-redirect
    response returned — so the payload hashed downstream is always the final
    payload, never a redirect body.
    """

    def __init__(self):
        self._conns: dict[tuple, http.client.HTTPConnection] = {}
        # Per-run HTTP histogram (see `_get`): every response status the
        # opener observes (200s, followed redirects, 4xx/5xx — including each
        # retried attempt), plus requests that failed without an HTTP status.
        self.status_counts: dict[int, int] = {}
        self.network_errors: int = 0

    def _evict(self, key: tuple) -> None:
        conn = self._conns.pop(key, None)
        if conn is not None:
            conn.close()

    def _get(self, url: str) -> tuple[tuple, http.client.HTTPConnection, http.client.HTTPResponse]:
        """Issue one GET, returning (pool_key, conn, raw_response).

        The connection is popped from the pool (never reused by this call);
        the caller re-pools it on success or closes it on error. Redirect
        responses are returned as-is so the caller can read the Location
        header.
        """
        parsed = urllib.parse.urlsplit(url)
        scheme = parsed.scheme
        host = parsed.hostname or ""
        port = parsed.port or (443 if scheme == "https" else 80)
        key = (scheme, host, port)
        conn = self._conns.pop(key, None)
        if conn is None:
            cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
            conn = cls(host, port, timeout=SOCKET_TIMEOUT)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        try:
            conn.request(
                "GET", path,
                headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
            )
            response = conn.getresponse()
        except Exception as e:
            conn.close()
            self.network_errors += 1
            raise urllib.error.URLError(reason=e) from e
        self.status_counts[response.status] = self.status_counts.get(response.status, 0) + 1
        if response.status >= 400:
            conn.close()
            raise urllib.error.HTTPError(
                url, response.status, response.reason, response.headers, response
            )
        return key, conn, response

    def __call__(self, url: str) -> http.client.HTTPResponse:
        current = url
        visited: list[str] = []
        while True:
            if current in visited:
                raise _RedirectError(
                    current, 302, f"redirect loop detected ({current})"
                )
            visited.append(current)
            if len(visited) > MAX_REDIRECTS + 1:
                raise _RedirectError(
                    current, 302,
                    f"redirect limit exceeded (more than {MAX_REDIRECTS} redirects)",
                )
            key, conn, response = self._get(current)
            if response.status not in REDIRECT_STATUSES:
                self._conns[key] = conn
                return _PooledResponse(self, key, response)
            code = response.status
            location = response.headers.get("Location")
            if not location:
                conn.close()
                raise _RedirectError(
                    current, code, f"redirect without Location (HTTP {code})"
                )
            current = urllib.parse.urljoin(current, location)
            try:
                response.read()
            except Exception:
                conn.close()
            else:
                if response.will_close:
                    conn.close()
                else:
                    self._conns[key] = conn

    def close(self) -> None:
        for conn in self._conns.values():
            conn.close()
        self._conns.clear()


def _default_opener():
    """Shared urlopen-like callable with a descriptive User-Agent and
    per-mirror keep-alive connection reuse."""
    return _PooledOpener()


def _retry_after(exception) -> float | None:
    headers = getattr(exception, "headers", None)
    value = headers.get("Retry-After") if headers is not None else None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _backoff_delay(attempt: int, retry_after: float | None = None,
                   jitter_fn=lambda: random.uniform(0, 0.5)) -> float:
    """Delay before retry `attempt` (1-based): exponential backoff (1 s base,
    doubling, 30 s cap) with jitter, overridden by a server Retry-After capped
    at the same internal bound."""
    if retry_after is not None:
        return min(BACKOFF_CAP, max(0.0, retry_after))
    delay = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** (attempt - 1)))
    return delay + jitter_fn()


def _fetch_with_retries(opener, url: str, dest: str, sleep=time.sleep,
                        on_progress=None):
    """Fetch url into dest + '.part' with bounded retries.

    Transient failures (429/503, connection errors) are retried with
    exponential backoff honoring Retry-After; permanent failures and
    write errors are not. Returns (digest, total, error_message) where
    error_message is None on success and the part file is removed on any
    failure path. `on_progress`, when given, is forwarded to each download
    attempt so the progress bar can move while a large payload streams in.
    """
    attempts = 0
    while True:
        attempts += 1
        try:
            digest, total = _fetch_to_part(opener, url, dest, on_progress)
            return digest, total, None
        except _RedirectError as e:
            return None, 0, e.reason
        except urllib.error.HTTPError as e:
            if e.code in TRANSIENT_STATUSES and attempts < MAX_RETRY_ATTEMPTS:
                sleep(_backoff_delay(attempts, _retry_after(e)))
                continue
            if attempts > 1:
                return None, 0, f"{attempts} retries exhausted (HTTP {e.code})"
            return None, 0, f"HTTP {e.code}"
        except urllib.error.URLError as e:
            if attempts < MAX_RETRY_ATTEMPTS:
                sleep(_backoff_delay(attempts))
                continue
            if attempts > 1:
                return None, 0, f"{attempts} retries exhausted ({e.reason})"
            return None, 0, f"network error ({e.reason})"
        except OSError as e:
            return None, 0, f"write error ({e.strerror or e})"


@dataclass
class DownloadStats:
    planned_files: int = 0
    files_downloaded: int = 0
    bytes_downloaded: int = 0
    files_verified: int = 0
    failures: list[str] = field(default_factory=list)
    command_errors: list[str] = field(default_factory=list)
    # Per-phase HTTP histogram: every response status the opener observed
    # (200s, redirects, 4xx/5xx incl. each retried attempt) and requests that
    # failed without an HTTP status (connection/DNS/timeout/mid-read drops).
    # Populated from the opener after the loop; empty for injected openers
    # that don't track them.
    status_counts: dict[int, int] = field(default_factory=dict)
    network_errors: int = 0


def pending_binaries(conn: sqlite3.Connection) -> list[tuple[int, str, str, str, str]]:
    """Pending set: current binaries with verified = 0."""
    rows = conn.execute(
        "SELECT id, package_name, version, release, component FROM binaries"
        " WHERE status = 'current' AND verified = 0"
        " ORDER BY package_name, version"
    ).fetchall()
    return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]


def _mirror_for_release(config, release: str) -> str | None:
    for source in config.sources:
        if source.release == release:
            return source.mirror
    return None


def _remove_part(part_path: str) -> None:
    try:
        os.remove(part_path)
    except OSError:
        pass


def _fetch_to_part(opener, url: str, dest: str, on_progress=None) -> tuple[str, int]:
    """Download url into dest + '.part', returning (sha256_hex, bytes).

    The caller removes the part file on any failure path. Raises on network
    and write errors. `on_progress`, when given, is called with the running
    byte count of the current attempt, throttled to roughly once per
    `PROGRESS_LIVE_BYTES`; the count is per-attempt, so a retry reports from
    zero again (the engine adds the already-committed baseline on top).
    """
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    last_report = 0
    response = None
    try:
        response = opener(url)
        with open(part, "wb") as f:
            while True:
                try:
                    chunk = response.read(CHUNK_SIZE)
                except (ConnectionError, TimeoutError, http.client.HTTPException) as e:
                    # Mid-read network failures (connection reset, dropped
                    # connection, truncated body) are transient: normalize to
                    # URLError so the retry layer backs off. File
                    # write errors stay OSError and are not retried.
                    raise urllib.error.URLError(reason=e) from e
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
                f.write(chunk)
                if on_progress is not None and total - last_report >= PROGRESS_LIVE_BYTES:
                    last_report = total
                    on_progress(total)
    except BaseException:
        _remove_part(part)
        raise
    finally:
        if response is not None:
            response.close()
    return digest.hexdigest(), total


def _file_sha256(path: str) -> str | None:
    """SHA-256 of a file's contents, or `None` when it cannot be read.

    Delegates to the shared helper; an unreadable file is treated as absent
    (the caller falls through to a re-fetch) rather than aborting the run.
    """
    return file_sha256(path, errors="none")


def _entry_size(entry) -> int | None:
    """Index `Size` as an int, or None when absent/unparsable."""
    if entry.size and str(entry.size).isdigit():
        return int(entry.size)
    return None


def _stat_size_matches(path: str, expected_size: int | None) -> bool:
    """Cheap short-circuit: true when the file's stat size equals the index
    `Size`. A size mismatch (or unstatable file) means a full re-check. A
    matching size skips only this existence/size check — the checksum is
    always re-verified, never skipped for a size match."""
    if expected_size is None:
        return False
    try:
        return os.stat(path).st_size == expected_size
    except OSError:
        return False


def _live_progress(progress, done_files: int, done_bytes: int):
    """Live byte hook for the progress bar, or None when not applicable.

    Live in-flight bytes are only streamed to a TTY progress object (a piped
    or logged run keeps its one-line-per-file output — per-chunk lines would
    spam the log). The hook reports the committed baseline plus the current
    attempt's bytes, so a retry visibly restarts at the baseline instead of
    double counting.
    """
    if progress is None or not getattr(progress, "is_tty", False):
        return None

    def _live(n: int, _files=done_files, _bytes=done_bytes) -> None:
        progress.update(_files, _bytes + n)

    return _live


def download_pending_binaries(config, conn, progress=None, opener=None, sleep=time.sleep) -> DownloadStats:
    """Run the binary phase: download + verify every pending current binary.

    A current binary's file is pending when the record is unverified, or when
    the pool file is missing or does not match the index checksum (presence is
    never treated as verified; deleted or corrupt files are re-fetched).

    `progress` (optional) must expose `set_totals(files, bytes)` and
    `update(done_files, done_bytes)`. `opener` (optional) is a urlopen-like
    callable for tests; `sleep` (optional) is the backoff sleeper; both
    default to the production behavior (shared UA opener, real sleeps).
    """
    opener = opener if opener is not None else _default_opener()
    stats = DownloadStats()

    work: dict[str, dict] = {}
    recheck: dict[str, list[int]] = {}
    reverified = 0
    for release, component, records in _group_current(conn):
        mirror = _mirror_for_release(config, release)
        if mirror is None:
            stats.command_errors.append(
                f"No configured archive source for release {release}"
            )
            continue
        index_path = resolve_index_path(binary_index_path(config.binary_cache, release, component))
        if not os.path.exists(index_path):
            stats.command_errors.append(
                f"Cached index missing for {release}/{component}; the index is "
                "part of the cache — restore or re-fetch it"
            )
            continue
        binary_index = load_binary_index(index_path, release, component)
        for rid, name, version, architecture, verified in records:
            entry = lookup_entry(binary_index, name, version, architecture)
            if entry is None or not entry.filename or not entry.sha256:
                stats.command_errors.append(
                    f"No index entry for {release}/{component} {name} {version}; "
                    "restore or re-fetch the cached index"
                )
                continue
            dest = safe_pool_path(config.binary_cache, entry.filename)
            if dest is None:
                stats.command_errors.append(
                    f"Unsafe Filename in cached index for {release}/{component} "
                    f"{name} {version}: {entry.filename!r} (refusing to write "
                    "outside the cache pool)"
                )
                continue
            if (
                _stat_size_matches(dest, _entry_size(entry))
                and _file_sha256(dest) == entry.sha256.lower()
            ):
                # Already present and valid: never re-download, regardless of
                # the record's verified state. The re-check confirms
                # integrity, so an unverified record is re-verified here
                # without a network round-trip.
                if not verified:
                    conn.execute("UPDATE binaries SET verified = 1 WHERE id = ?", (rid,))
                    reverified += 1
                continue
            if verified:
                recheck.setdefault(dest, []).append(rid)
            item = work.get(dest)
            if item is None:
                work[dest] = {
                    "filename": entry.filename,
                    "url": f"{mirror.rstrip('/')}/{entry.filename}",
                    "sha256": entry.sha256,
                    "size": _entry_size(entry) or 0,
                    "record_ids": [],
                }
            work[dest]["record_ids"].append(rid)
    if reverified:
        conn.commit()

    stats.planned_files = len(work)
    if progress is not None:
        progress.set_totals(len(work), sum(item["size"] for item in work.values()))

    done_files = 0
    done_bytes = 0
    for dest, item in work.items():
        part = dest + ".part"
        filename = item["filename"]
        failed = False
        digest, total, error_message = _fetch_with_retries(
            opener, item["url"], dest, sleep,
            on_progress=_live_progress(progress, done_files, done_bytes),
        )
        if error_message is not None:
            _remove_part(part)
            stats.failures.append(f"{filename}: {error_message}")
            failed = True
        else:
            expected = item["sha256"].lower()
            if digest != expected:
                _remove_part(part)
                stats.failures.append(
                    f"{filename}: checksum mismatch (expected {expected[:4]}…, "
                    f"got {digest[:4]}…)"
                )
                failed = True
            else:
                os.replace(part, dest)
                for rid in item["record_ids"]:
                    conn.execute("UPDATE binaries SET verified = 1 WHERE id = ?", (rid,))
                conn.commit()
                done_files += 1
                done_bytes += total
                stats.files_downloaded += 1
                stats.bytes_downloaded += total
                stats.files_verified += len(item["record_ids"])
        if failed:
            previously_verified = recheck.get(dest, [])
            if previously_verified:
                for rid in previously_verified:
                    conn.execute("UPDATE binaries SET verified = 0 WHERE id = ?", (rid,))
                conn.commit()
        if progress is not None:
            progress.update(done_files, done_bytes)

    close = getattr(opener, "close", None)
    if close is not None:
        close()
    stats.status_counts = dict(getattr(opener, "status_counts", {}))
    stats.network_errors = getattr(opener, "network_errors", 0)
    return stats


def pending_sources(conn: sqlite3.Connection) -> list[tuple[int, str, str, str, str]]:
    """Pending sources: unverified sources linked to at
    least one current binary."""
    rows = conn.execute(
        "SELECT s.id, s.package_name, s.source_version, s.release, s.component"
        " FROM sources s WHERE s.verified = 0 AND EXISTS ("
        "  SELECT 1 FROM binary_to_source bts JOIN binaries b ON b.id = bts.binary_id"
        "  WHERE bts.source_id = s.id AND b.status = 'current')"
        " ORDER BY s.package_name, s.source_version"
    ).fetchall()
    return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]


def download_pending_sources(config, conn, progress=None, opener=None, sleep=time.sleep) -> DownloadStats:
    """Run the source phase: download + verify pending source files and
    re-check verified sources each run.

    Every source linked to a current binary is examined on every run: each
    index-listed file (.dsc + tarballs) is checked existence -> stat size ->
    sha256 (reusing the binary re-check helpers). Files already present and
    valid are never re-downloaded (verified or not). A verified source with a
    missing or corrupt file is un-marked and exactly those files are
    re-fetched; intact files of the same source stay untouched. A source is
    marked verified only when ALL of its index-listed files are present and
    checksum-verified. Skipped entirely when source fetching is disabled.
    """
    opener = opener if opener is not None else _default_opener()
    stats = DownloadStats()
    if not config.fetch_source:
        return stats

    work: dict[str, dict] = {}
    source_all: dict[int, set[str]] = {}
    source_ok: dict[int, set[str]] = {}
    source_present: dict[int, set[str]] = {}
    blocked: set[int] = set()
    unmarked = 0
    for release, component, records in _group_sources_to_check(conn):
        mirror = _mirror_for_release(config, release)
        if mirror is None:
            stats.command_errors.append(
                f"No configured archive source for release {release}"
            )
            continue
        index_path = resolve_index_path(source_index_path(config.source_cache, release, component))
        if not os.path.exists(index_path):
            stats.command_errors.append(
                f"Cached index missing for {release}/{component}; the index is "
                "part of the cache — restore or re-fetch it"
            )
            continue
        source_index = load_source_index(index_path, release, component)
        for sid, name, version, verified in records:
            entry = lookup_entry(source_index, name, version)
            if entry is None or not entry.directory or not entry.parsed_files:
                stats.command_errors.append(
                    f"No index entry for {release}/{component} {name} {version}; "
                    "restore or re-fetch the cached index"
                )
                continue
            all_dests: set[str] = set()
            for source_file in entry.parsed_files:
                if not source_file.name or not source_file.sha256:
                    stats.failures.append(
                        f"{entry.directory}/{source_file.name}: no checksum in cached index"
                    )
                    blocked.add(sid)
                    continue
                rel_path = f"{entry.directory}/{source_file.name}"
                dest = safe_pool_path(config.source_cache, rel_path)
                if dest is None:
                    stats.failures.append(
                        f"{rel_path}: unsafe path in cached index; refusing to "
                        "write outside the source cache"
                    )
                    blocked.add(sid)
                    continue
                all_dests.add(dest)
                if (
                    os.path.exists(dest)
                    and _stat_size_matches(dest, source_file.size)
                    and _file_sha256(dest) == source_file.sha256.lower()
                ):
                    source_present.setdefault(sid, set()).add(dest)
                    continue
                item = work.get(dest)
                if item is None:
                    work[dest] = {
                        "rel_path": rel_path,
                        "url": f"{mirror.rstrip('/')}/{rel_path}",
                        "sha256": source_file.sha256,
                        "size": source_file.size or 0,
                        "source_ids": set(),
                    }
                    item = work[dest]
                item["source_ids"].add(sid)
            source_all[sid] = all_dests
            source_ok[sid] = set()
            if verified and len(source_present.get(sid, set())) < len(all_dests):
                conn.execute("UPDATE sources SET verified = 0 WHERE id = ?", (sid,))
                unmarked += 1
    if unmarked:
        conn.commit()

    stats.planned_files = len(work)
    if progress is not None:
        progress.set_totals(len(work), sum(item["size"] for item in work.values()))

    done_files = 0
    done_bytes = 0
    for dest, item in work.items():
        part = dest + ".part"
        rel_path = item["rel_path"]
        digest, total, error_message = _fetch_with_retries(
            opener, item["url"], dest, sleep,
            on_progress=_live_progress(progress, done_files, done_bytes),
        )
        if error_message is not None:
            _remove_part(part)
            stats.failures.append(f"{rel_path}: {error_message}")
        else:
            expected = item["sha256"].lower()
            if digest != expected:
                _remove_part(part)
                stats.failures.append(
                    f"{rel_path}: checksum mismatch (expected {expected[:4]}…, "
                    f"got {digest[:4]}…)"
                )
            else:
                os.replace(part, dest)
                done_files += 1
                done_bytes += total
                stats.files_downloaded += 1
                stats.bytes_downloaded += total
                for sid in item["source_ids"]:
                    source_ok[sid].add(dest)
        if progress is not None:
            progress.update(done_files, done_bytes)

    for sid, all_dests in source_all.items():
        if sid in blocked:
            continue
        ok = source_present.get(sid, set()) | source_ok.get(sid, set())
        if ok == all_dests:
            conn.execute("UPDATE sources SET verified = 1 WHERE id = ?", (sid,))
            stats.files_verified += len(source_ok.get(sid, set()))
    if stats.files_verified:
        conn.commit()
    close = getattr(opener, "close", None)
    if close is not None:
        close()
    stats.status_counts = dict(getattr(opener, "status_counts", {}))
    stats.network_errors = getattr(opener, "network_errors", 0)
    return stats


def _group_sources_to_check(conn: sqlite3.Connection) -> list[tuple[str, str, list[tuple[int, str, str, int]]]]:
    """Sources linked to at least one current binary, grouped by
    (release, component), including verified ones for the per-run re-check.

    `local` sources are excluded: they are verified at import time from the
    supplied directory (there is no archive index to confirm against and
    nothing to fetch), so the archive source phase must not report them as
    missing-index errors."""
    groups: dict[tuple[str, str], list[tuple[int, str, str, int]]] = {}
    rows = conn.execute(
        "SELECT s.id, s.package_name, s.source_version, s.release, s.component, s.verified"
        " FROM sources s WHERE EXISTS ("
        "  SELECT 1 FROM binary_to_source bts JOIN binaries b ON b.id = bts.binary_id"
        "  WHERE bts.source_id = s.id AND b.status = 'current')"
        " AND s.component != 'local'"
        " ORDER BY s.package_name, s.source_version"
    ).fetchall()
    for sid, name, version, release, component, verified in rows:
        groups.setdefault((release, component), []).append((sid, name, version, verified))
    return [(release, component, records) for (release, component), records in groups.items()]


def _group_current(conn: sqlite3.Connection) -> list[tuple[str, str, list[tuple[int, str, str, str, int]]]]:
    """Current binaries grouped by (release, component).

    `local` binaries are excluded: they are verified at import time from the
    supplied payload (there is no archive index to confirm against and nothing
    to fetch), so the archive binary phase must not report them as
    missing-index errors. Handled payloads (e.g. deleted local payloads) are
    covered by the handoff/diff `excluded_pool` logic."""
    groups: dict[tuple[str, str], list[tuple[int, str, str, str, int]]] = {}
    rows = conn.execute(
        "SELECT id, package_name, version, release, component, architecture, verified"
        " FROM binaries WHERE status = 'current' AND component != 'local'"
        " ORDER BY package_name, version"
    ).fetchall()
    for rid, name, version, release, component, architecture, verified in rows:
        groups.setdefault((release, component), []).append(
            (rid, name, version, architecture, verified)
        )
    return [(release, component, records) for (release, component), records in groups.items()]


def lookup_entry(binary_index: dict, name: str, version: str, architecture: str = ""):
    """Resolve an index entry by name + version, preferring the record's
    architecture so a multi-arch index (same version under `amd64` and `all`,
    say) resolves to the record's own `Filename`. Falls back to a plain
    version match so records without an architecture still resolve."""
    fallback = None
    for entry in binary_index.get(name, []):
        if entry.version != version:
            continue
        # Source entries carry no architecture; an empty record arch matches
        # any entry, a non-empty one must equal the entry's own arch.
        if getattr(entry, "architecture", "") == architecture:
            return entry
        if fallback is None:
            fallback = entry
    return fallback
