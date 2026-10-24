import contextlib
import io
import os
import sqlite3
import tempfile
import types
import unittest

from package_mgmt.cli.commands.add import _handle_add_files, parse_package_list
from package_mgmt.lib.model.config import ArchiveSource
from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL
from package_mgmt.lib.model.package import BinaryPackage
from package_mgmt.lib.model.repository import RepositoryIndex
from package_mgmt.lib.model.resolution import PackageListResult, PackageRequest
from package_mgmt.lib.service.resolver import PackageResolver
from package_mgmt.lib.util.errors import EXIT_ERROR, EXIT_SUCCESS


class ParsePackageListTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, content: str) -> str:
        path = os.path.join(self.tmp.name, "pkgs.txt")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_valid_lines_become_requests_in_order(self):
        path = self._write("vim\nlib1=2.0-1\napp\n")
        result = parse_package_list(path)
        self.assertEqual(result.errors, ())
        self.assertEqual(
            result.requests,
            (
                PackageRequest("vim"),
                PackageRequest("lib1", "2.0-1"),
                PackageRequest("app"),
            ),
        )

    def test_trim_blank_and_comment_lines(self):
        path = self._write(
            "  vim  \n"
            "\n"
            "# rebuild manifest\n"
            "   # indented comment\n"
            "lib1=2.0-1\n"
            "\tapp\r\n"
        )
        result = parse_package_list(path)
        self.assertEqual(result.errors, ())
        self.assertEqual(
            result.requests,
            (
                PackageRequest("vim"),
                PackageRequest("lib1", "2.0-1"),
                PackageRequest("app"),
            ),
        )

    def test_all_malformed_lines_reported_with_file_and_line(self):
        path = self._write("vim\n=1.0\nlib1\nvim=\napp\n  = 2.0\n")
        result = parse_package_list(path)
        self.assertEqual(
            result.errors,
            (
                f"Malformed line 2 in {path}: '=1.0'",
                f"Malformed line 4 in {path}: 'vim='",
                f"Malformed line 6 in {path}: '= 2.0'",
            ),
        )
        # Valid lines still parse; the caller aborts when errors are present.
        self.assertEqual(
            result.requests,
            (PackageRequest("vim"), PackageRequest("lib1"), PackageRequest("app")),
        )

    def test_spaces_around_equals_kept_for_consistency_with_positional_args(self):
        """File lines behave like positional arguments — inner
        whitespace is not stripped (positional `add "vim = 2.0-1"` would also
        resolve the name 'vim ')."""
        path = self._write("vim = 2.0-1\n")
        result = parse_package_list(path)
        self.assertEqual(result.errors, ())
        self.assertEqual(result.requests, (PackageRequest("vim ", " 2.0-1"),))

    def test_empty_and_comment_only_files_yield_no_requests(self):
        for content in ("", "\n\n", "# nothing here\n\n# at all\n"):
            result = parse_package_list(self._write(content))
            self.assertEqual(result, PackageListResult())

    def test_crlf_line_endings(self):
        path = self._write("vim\r\nlib1=2.0-1\r\n")
        result = parse_package_list(path)
        self.assertEqual(result.errors, ())
        self.assertEqual(result.requests, (PackageRequest("vim"), PackageRequest("lib1", "2.0-1")))

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            parse_package_list(os.path.join(self.tmp.name, "nope.txt"))

    def test_non_utf8_file_raises(self):
        path = os.path.join(self.tmp.name, "binary.txt")
        with open(path, "wb") as f:
            f.write(b"vim\xff\xfe\n")
        with self.assertRaises(UnicodeDecodeError):
            parse_package_list(path)

    def test_pin_split_on_first_equals(self):
        path = self._write("vim=2.1-1+b1\n")
        result = parse_package_list(path)
        self.assertEqual(result.requests, (PackageRequest("vim", "2.1-1+b1"),))


class AddFileBatchFlowTest(unittest.TestCase):
    """The `--file` batch summary counts a request whose alternative group
    resolved as resolved with zero errors; a fully-failed group is counted
    failed with exactly one error (contracts/cli.md Phase 1)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = types.SimpleNamespace(
            sources=(
                ArchiveSource(mirror="http://example.invalid", release="trixie", components=("main",)),
            ),
            fetch_source=False,
            max_build_dep_report_depth=2,
            max_source_dep_depth=-1,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _binary(self, name, depends=None):
        return BinaryPackage(
            package=name, version="1.0", architecture="amd64",
            release="trixie", component="main",
            depends_raw=depends, provides_raw=None,
            source_field=name, filename=None, sha256=None,
        )

    def _conn(self):
        conn = sqlite3.connect(os.path.join(self.tmp.name, "test.db"))
        conn.executescript(EXPECTED_SCHEMA_SQL)
        conn.commit()
        return conn

    def _write_manifest(self, content: str) -> str:
        path = os.path.join(self.tmp.name, "manifest.txt")
        with open(path, "w") as f:
            f.write(content)
        return path

    def _run(self, depends: str | None) -> str:
        index = RepositoryIndex(
            binary_packages={
                "app": [self._binary("app", depends=depends)],
                "realpkg": [self._binary("realpkg")],
            },
        )
        conn = self._conn()
        resolver = PackageResolver(self.config, conn)
        resolver.index = index
        path = self._write_manifest("app\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = _handle_add_files(self.config, conn, resolver, [path], None)
        self._rc = rc
        return buf.getvalue()

    def test_satisfied_alternative_group_counts_as_resolved(self):
        output = self._run("ghost | realpkg")
        self.assertEqual(self._rc, EXIT_SUCCESS)
        self.assertIn("Packages requested:  1", output)
        self.assertIn("Packages resolved:   1", output)
        self.assertIn("Errors:              0", output)

    def test_fully_failed_group_counts_as_failed_with_one_error(self):
        output = self._run("a | b")
        self.assertEqual(self._rc, EXIT_ERROR)
        self.assertIn("Packages requested:  1", output)
        self.assertIn("Packages resolved:   0", output)
        self.assertIn("Errors:              1", output)


if __name__ == "__main__":
    unittest.main()
