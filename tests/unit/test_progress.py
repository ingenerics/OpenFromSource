import io
import time
import unittest
from unittest import mock

from package_mgmt.lib.util.progress import (
    PhaseProgress,
    format_bytes,
    format_elapsed,
    make_resolved_progress,
)


class TestFormatElapsed(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_elapsed(0), "00:00:00")

    def test_minutes_and_seconds(self):
        self.assertEqual(format_elapsed(222), "00:03:42")

    def test_hours(self):
        self.assertEqual(format_elapsed(3600 * 5 + 61), "05:01:01")

    def test_negative_clamped(self):
        self.assertEqual(format_elapsed(-5), "00:00:00")


class TestFormatBytes(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_bytes(0), "0 B")

    def test_bytes(self):
        self.assertEqual(format_bytes(3), "3.0 B")

    def test_kib(self):
        self.assertEqual(format_bytes(1024), "1.0 KiB")

    def test_mib(self):
        self.assertEqual(format_bytes(45.2 * 1024 * 1024), "45.2 MiB")

    def test_gib(self):
        self.assertEqual(format_bytes(1.2 * 1024 * 1024 * 1024), "1.2 GiB")


class TestPhaseProgressNonTty(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        self.progress = PhaseProgress(5, 1024, stream=self.stream, is_tty=False)

    def test_one_line_per_update_with_newline(self):
        self.progress.update(1, 200)
        self.progress.update(2, 400)
        lines = self.stream.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "downloaded 1/5 files (200.0 B/1.0 KiB)")
        self.assertEqual(lines[1], "downloaded 2/5 files (400.0 B/1.0 KiB)")

    def test_done_adds_nothing_when_not_a_tty(self):
        self.progress.update(5, 1024)
        self.progress.done()
        self.assertTrue(self.stream.getvalue().endswith("\n"))


class TestPhaseProgressTty(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        self.progress = PhaseProgress(4, 2048, stream=self.stream, is_tty=True)

    def test_bar_shape_and_counts(self):
        self.progress.update(2, 1024)
        rendered = self.stream.getvalue()
        self.assertIn("Elapsed: 00:00:", rendered)
        self.assertIn("2/4 files (1.0 KiB/2.0 KiB)", rendered)
        self.assertIn("█", rendered)
        self.assertIn("░", rendered)
        # In-place: the bar line is the last output, no trailing newline.
        self.assertFalse(rendered.endswith("\n"))
        self.progress.done()
        self.assertTrue(self.stream.getvalue().endswith("\n"))

    def test_in_place_redraw_moves_up_one_line(self):
        """Each redraw returns to column 0 and moves up one line so both the
        elapsed line and the bar line are overwritten in place."""
        self.progress.update(1, 512)
        self.progress.update(3, 1536)
        rendered = self.stream.getvalue()
        self.assertEqual(rendered.count("Elapsed:"), 2)
        self.assertEqual(rendered.count("3/4 files"), 1)
        self.assertIn("\r\x1b[1A", rendered)
        self.assertTrue(rendered.startswith("Elapsed:"))

    def test_fraction_fill_width(self):
        self.progress.update(4, 2048)
        self.progress.done()
        rendered = self.stream.getvalue()
        self.assertIn("█" * 40, rendered)
        self.assertNotIn("░" * 40, rendered)

    def test_elapsed_format_in_render(self):
        self.progress.update(0, 0)
        self.progress.done()
        self.assertRegex(self.stream.getvalue(), r"Elapsed: \d{2}:\d{2}:\d{2}")


class TestPhaseProgressZeroTotal(unittest.TestCase):
    def test_zero_total_no_division_error(self):
        stream = io.StringIO()
        progress = PhaseProgress(0, 0, stream=stream, is_tty=False)
        progress.update(0, 0)
        self.assertEqual(stream.getvalue().strip(), "downloaded 0/0 files (0 B/0 B)")
        progress.done()

    def test_zero_total_tty_bar_empty(self):
        stream = io.StringIO()
        progress = PhaseProgress(0, 0, stream=stream, is_tty=True)
        progress.update(0, 0)
        progress.done()
        self.assertIn("0/0 files", stream.getvalue())
        self.assertNotIn("█", stream.getvalue())

    def test_elapsed_monotonic(self):
        stream = io.StringIO()
        progress = PhaseProgress(1, 1, stream=stream, is_tty=True)
        time.sleep(1.1)
        progress.update(1, 1)
        self.assertIn("00:00:01", stream.getvalue())


class TestPhaseProgressBytesFraction(unittest.TestCase):
    """The bar fill is weighted by downloaded bytes (the last package is
    often the biggest), and the file count acts as the completion backstop
    (OR): a small size drift, a missing byte total, or over-accounted bytes
    must all still resolve to an honest, clamped bar."""

    def _bar(self, total_files, total_bytes, done_files, done_bytes) -> str:
        stream = io.StringIO()
        progress = PhaseProgress(total_files, total_bytes, stream=stream, is_tty=True)
        progress.update(done_files, done_bytes)
        progress.done()
        return stream.getvalue()

    def test_fraction_is_bytes_weighted(self):
        # 2 files / 100 B planned; 25 B done = 1/4 -> 10 of 40 filled, even
        # though the file count already reads 1/2.
        rendered = self._bar(2, 100, 1, 25)
        self.assertIn("█" * 10, rendered)
        self.assertIn("░" * 30, rendered)

    def test_big_last_file_bar_tracks_bytes_not_files(self):
        # 1000 files, 999 done but only ~5% of the bytes: the bar must sit at
        # ~5%, not at the (near-full) file-count share.
        rendered = self._bar(1000, 10_000, 999, 500)
        self.assertIn("█" * 2, rendered)
        self.assertNotIn("█" * 20, rendered)

    def test_file_count_backstop_completes_small_byte_shortfall(self):
        # All files done, bytes slightly under the planned total (stale index
        # Size): the OR condition still reads a full bar.
        rendered = self._bar(2, 100, 2, 90)
        self.assertIn("█" * 40, rendered)
        self.assertNotIn("░", rendered)

    def test_bytes_accounted_forces_full_bar_before_last_file(self):
        rendered = self._bar(3, 100, 1, 100)
        self.assertIn("█" * 40, rendered)

    def test_bytes_exceeding_total_are_clamped(self):
        # Actual bytes > planned total (index under-counted): no overflow past
        # the bar width.
        rendered = self._bar(2, 100, 1, 250)
        self.assertIn("█" * 40, rendered)
        self.assertNotIn("░", rendered)

    def test_falls_back_to_file_count_when_total_bytes_zero(self):
        # Index without any Size data: the file count drives the bar.
        rendered = self._bar(5, 0, 2, 0)
        self.assertIn("█" * 16, rendered)


class TestPhaseProgressAdaptiveBar(unittest.TestCase):
    """The bar must shrink to fit the terminal so the redrawn lines never
    wrap (a wrapped line breaks the in-place update on narrow windows)."""

    def _progress(self):
        import package_mgmt.lib.util.progress as progress_mod

        stream = io.StringIO()
        p = progress_mod.PhaseProgress(
            156, 74 * 1024 * 1024, stream=stream, is_tty=True
        )
        p.set_totals(156, 74 * 1024 * 1024)
        return progress_mod, p, stream

    def test_bar_shrinks_to_fit_narrow_terminal(self):
        progress_mod, p, stream = self._progress()
        with mock.patch.object(progress_mod, "_real_terminal_columns", return_value=63):
            p.update(23, 23 * 700 * 1024)
        body = stream.getvalue().lstrip("\r")
        widest = max(len(line) for line in body.split("\n"))
        self.assertLessEqual(widest, 63)
        self.assertNotIn("█" * 40, body)

    def test_bar_stays_full_width_on_wide_terminal(self):
        progress_mod, p, stream = self._progress()
        with mock.patch.object(progress_mod, "_real_terminal_columns", return_value=120):
            p.update(156, 74 * 1024 * 1024)
        self.assertIn("█" * 40, stream.getvalue())

    def test_very_narrow_terminal_still_has_a_bar(self):
        progress_mod, p, stream = self._progress()
        with mock.patch.object(progress_mod, "_real_terminal_columns", return_value=20):
            p.update(156, 74 * 1024 * 1024)
        self.assertIn("[█", stream.getvalue())


class TestResolvedProgress(unittest.TestCase):
    """`make_resolved_progress` (add/ensure-base): clean line-per-update on a
    non-TTY (no `\r` escape garbage in logs), in-place `\r` on a terminal."""

    def test_non_tty_emits_clean_lines_with_newlines(self):
        stream = io.StringIO()
        progress = make_resolved_progress(stream=stream, is_tty=False)
        progress(3, 6)
        progress(6, 6)
        lines = stream.getvalue().splitlines()
        self.assertEqual(lines, ["Resolved 3/6", "Resolved 6/6"])
        self.assertNotIn("\r", stream.getvalue())

    def test_tty_renders_in_place_without_newline(self):
        stream = io.StringIO()
        progress = make_resolved_progress(stream=stream, is_tty=True)
        progress(1, 6)
        progress(6, 6)
        self.assertIn("\rResolved 1/6", stream.getvalue())
        self.assertIn("\rResolved 6/6", stream.getvalue())
        self.assertFalse(stream.getvalue().endswith("\n"))

    def test_default_uses_stdout_isatty(self):
        progress = make_resolved_progress()
        self.assertTrue(callable(progress))


if __name__ == "__main__":
    unittest.main()
