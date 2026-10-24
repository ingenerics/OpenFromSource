"""Phase progress renderer: elapsed time + progress bar (files + bytes).

On a terminal it renders two lines in place — `Elapsed: HH:MM:SS` above and
`[bar] N/N files (X/X)` below — redrawn with a carriage return plus a one-line
cursor-up (`\\r\\x1b[1A`, the minimal cursor-addressing the two-line shape
needs). It degrades to one progress line per update when the stream is not a
terminal (piped/logged output). The bar width adapts to the terminal width so
the lines never wrap and the in-place redraw holds.

The bar's fill is weighted by downloaded **bytes** (the last package in a
dependency closure is often the biggest, so a file-count bar would sit near
full while most of the transfer is still ahead). The file count stays as the
completion backstop: once every file is done — or every planned byte is
accounted for — the bar reads 100% even when the two totals drift apart (e.g.
a stale index `Size` slightly off the served payload). Stdlib only; no
dependencies.
"""

import os
import sys
import time

BAR_WIDTH = 40
BAR_FILL = "█"
BAR_EMPTY = "░"


def _real_terminal_columns(stream) -> int | None:
    """Columns of a real terminal backing `stream`, or None if there is none.

    `os.get_terminal_size` only succeeds on an actual tty; StringIO/piped
    streams (tests, captured output) raise and fall through to None.
    """
    try:
        return os.get_terminal_size(stream.fileno()).columns
    except (OSError, ValueError):
        return None


def format_elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_bytes(num: int) -> str:
    if num <= 0:
        return "0 B"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{num} B"


class PhaseProgress:
    """Live per-phase progress: elapsed + bar across two redrawn lines.

    - On a TTY, each `update()` redraws the two lines in place with
      `\\r\\x1b[1A`; the bar is sized to fit the terminal width so neither
      line wraps (a wrapped line would break the redraw).
    - On a non-TTY stream, each `update()` emits one periodic progress line.
    - `done()` clears the in-place rendering so following output starts fresh.
    """

    def __init__(self, total_files: int, total_bytes: int, stream=None, is_tty=None):
        self.total_files = total_files
        self.total_bytes = total_bytes
        self.stream = stream if stream is not None else sys.stdout
        self.is_tty = self.stream.isatty() if is_tty is None else is_tty
        self.start_time = time.monotonic()
        self._last = None

    def set_totals(self, total_files: int, total_bytes: int) -> None:
        """Update the phase totals (known only once the work set is computed)."""
        self.total_files = total_files
        self.total_bytes = total_bytes

    def _elapsed(self) -> str:
        return format_elapsed(time.monotonic() - self.start_time)

    def _bar_width_for(self) -> int:
        """Bar width that fits the bar line (clamped to `BAR_WIDTH`).

        The bar line is `[bar] N/N files (X/X)`; on a real terminal the bar
        shrinks so that line stays under the column limit, on anything else
        (tests, pipes) the canonical width is kept. Computed per update so a
        live resize is respected.
        """
        columns = _real_terminal_columns(self.stream)
        if columns is None:
            return BAR_WIDTH
        suffix = len(
            f" {self.total_files}/{self.total_files} files "
            f"({format_bytes(self.total_bytes)}/{format_bytes(self.total_bytes)})"
        )
        available = columns - (3 + suffix)  # "[" + "]" + " "
        return max(1, min(BAR_WIDTH, available))

    def _fraction(self, done_files: int, done_bytes: int) -> float:
        """Overall completion in [0, 1]: bytes-weighted, files as backstop.

        The primary signal is downloaded bytes over the planned total, so a
        large trailing package moves the bar as it streams in instead of the
        bar sitting at ~100% for most of the download (the file-count
        fraction of the last of thousands of files is already ~1.0). The file
        count still decides completion (OR): once every file is done or every
        planned byte is accounted for, the fraction is 1.0 — covering a small
        size drift between the index `Size` and the served payload. A phase
        with no planned work stays at 0; a byte total of 0 (no size data in
        the index) falls back to the file count; any fraction past 1.0 (live
        bytes exceed the planned total) is clamped.
        """
        if not self.total_files and not self.total_bytes:
            return 0.0
        if self.total_bytes:
            fraction = done_bytes / self.total_bytes
        else:
            fraction = done_files / self.total_files
        if (self.total_files and done_files >= self.total_files) or (
            self.total_bytes and done_bytes >= self.total_bytes
        ):
            fraction = 1.0
        return min(1.0, fraction)

    def _bar_line(self, done_files: int, done_bytes: int) -> str:
        fraction = self._fraction(done_files, done_bytes)
        width = self._bar_width_for()
        filled = int(fraction * width)
        bar = BAR_FILL * filled + BAR_EMPTY * (width - filled)
        return (
            f"[{bar}] {done_files}/{self.total_files} files "
            f"({format_bytes(done_bytes)}/{format_bytes(self.total_bytes)})"
        )

    def _render(self, done_files: int, done_bytes: int) -> str:
        if self.is_tty:
            return (
                f"Elapsed: {self._elapsed()}\n"
                f"{self._bar_line(done_files, done_bytes)}"
            )
        return (
            f"downloaded {done_files}/{self.total_files} files "
            f"({format_bytes(done_bytes)}/{format_bytes(self.total_bytes)})"
        )

    def _pad_lines(self, rendered: str, previous: str) -> str:
        """Pad each line to at least the previous frame's line length so a
        shorter redraw erases stale trailing characters on both lines."""
        pairs = zip(rendered.split("\n"), previous.split("\n"))
        return "\n".join(line.ljust(len(prev)) for line, prev in pairs)

    def update(self, done_files: int, done_bytes: int) -> None:
        rendered = self._render(done_files, done_bytes)
        if self.is_tty:
            prefix = "" if self._last is None else "\r\x1b[1A"
            if self._last is not None:
                rendered = self._pad_lines(rendered, self._last)
            self._last = rendered
            print(f"{prefix}{rendered}", end="", file=self.stream, flush=True)
        else:
            print(rendered, file=self.stream, flush=True)

    def done(self) -> None:
        if self.is_tty and self._last is not None:
            print("\n", end="", file=self.stream, flush=True)
            self._last = None


def make_resolved_progress(stream=None, is_tty=None):
    """Resolution-loop progress callback (`Resolved X/Y`) for add/ensure-base.

    Renders in place with `\r` on a terminal and as one clean line per update
    when the output is piped or logged (no escape garbage in logs). Returns a
    `Callable[[int, int], None]` compatible with the resolver's `progress`
    hook.
    """
    stream = stream if stream is not None else sys.stdout
    is_tty = stream.isatty() if is_tty is None else is_tty

    def _progress(done: int, total: int) -> None:
        if is_tty:
            print(f"\rResolved {done}/{total}", end="", file=stream, flush=True)
        else:
            print(f"Resolved {done}/{total}", file=stream, flush=True)

    return _progress
