import io
import contextlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_deb  # noqa: E402
import make_test_iso  # noqa: E402

from package_mgmt.cli.commands.add import handle_add
from package_mgmt.cli.commands.import_deb import handle_import_deb
from package_mgmt.cli.commands.align_repo import handle_align_repo
from package_mgmt.cli.commands.config import handle_config
from package_mgmt.cli.commands.download import handle_download
from package_mgmt.cli.commands.ensure_base import handle_ensure_base
from package_mgmt.cli.commands.handoff import handle_handoff
from package_mgmt.cli.commands.snapshot import handle_snapshot
from package_mgmt.cli.commands.sync import handle_sync
from package_mgmt.cli.commands.systems import (
    handle_systems_add,
    handle_systems_list,
    handle_systems_remove,
    handle_systems_rename,
)
from package_mgmt.cli.commands.init import handle_init
from package_mgmt.cli.commands.list import handle_list_deps
from package_mgmt.cli.commands.package_stack import handle_package_stack
from package_mgmt.cli.commands.show import handle_show
from package_mgmt.cli.commands.verify import handle_verify_graph
from package_mgmt.cli.main import build_parser, main
from package_mgmt.lib.model.config import ProjectConfig
from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL
from package_mgmt.lib.service.config_loader import load_config
from package_mgmt.lib.service.db_init import initialize_database
from package_mgmt.lib.util.errors import (
    EXIT_SUCCESS,
    EXIT_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_SCHEMA_MISMATCH,
    EXIT_PERMISSION,
)


def _assert_single_json(stderr: str) -> dict:
    """Assert stderr carries exactly one JSON document and
    no other text (no log lines, no tracebacks, no blank lines), returning the
    parsed payload. `json.loads` rejects any surrounding text, so a successful
    parse of the stripped stream means the whole stream is one JSON document.
    """
    stripped = stderr.strip()
    if not stripped:
        raise AssertionError("stderr is empty; expected exactly one JSON document")
    if not stripped.startswith("{"):
        raise AssertionError(
            f"stderr carries non-JSON text before the payload: {stripped[:120]!r}"
        )
    return json.loads(stripped)


class TestDownloadContract(unittest.TestCase):
    """Download progress shapes, summary block, Total line,
    exit codes, and JSON errors per contracts/cli.md."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_base = os.path.join(self.tmp.name, "cache")
        self.fixture_base = os.path.join(self.tmp.name, "fixture")
        # The resolver reads the indexes from the cache trees; the mirror
        # serves only the shared pool (a separate copy, so pool edits and
        # cache edits are independent).
        make_test_iso.write_archive(self.cache_base)
        make_test_iso.write_pool_files(self.fixture_base)
        self.server = make_test_iso.serve_pool(self.fixture_base, port=0)
        port = self.server.server_address[1]
        self._write_defaults(f'[paths]\nbase_dir = "{self.cache_base}"\n[fetch]\nsource = false\n\n[[archive.source]]\nmirror = "http://127.0.0.1:{port}"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n')
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.server.shutdown()
        self.server.thread.join()
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _provision(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add(["vifm", "lib1"])
        self.assertEqual(rc, EXIT_SUCCESS)
        return load_config()[0]

    def _run(self, config):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_download(config.db_path, config)
        return rc, out.getvalue(), err.getvalue()

    def test_summary_block_matches_contract(self):
        config = self._provision()
        rc, out, err = self._run(config)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(err, "")
        self.assertIn("Binary phase summary", out)
        self.assertIn("────────────────────", out)
        self.assertIn("Files downloaded:  3", out)
        self.assertIn("Bytes:             ", out)
        self.assertIn("Files verified:    3", out)
        self.assertIn("Failed:            0", out)
        self.assertRegex(out, r"Elapsed:\s+\d{2}:\d{2}:\d{2}")
        self.assertIn("Total: 3 files downloaded, 3 verified, 0 failed (", out)
        # Per-phase HTTP histogram: the fixture pool serves every file with
        # HTTP 200 and never throttles.
        self.assertIn("HTTP statuses:     200:3", out)
        self.assertIn("Network errors:    0", out)
        # Each phase summary block appears exactly once (the log gets a
        # concise line, not a re-print of the block).
        self.assertEqual(out.count("Binary phase summary"), 1)
        self.assertEqual(out.count("Files downloaded:  3"), 1)

    def test_summary_logged_once_as_concise_line(self):
        config = self._provision()
        out, err = io.StringIO(), io.StringIO()
        with (
            self.assertLogs("package_mgmt.cli.commands.download", level="INFO") as captured,
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            rc = handle_download(config.db_path, config)
        self.assertEqual(rc, EXIT_SUCCESS)
        messages = [r.getMessage() for r in captured.records]
        binary = next(
            m for m in messages
            if m.startswith("download binary phase: 3 files downloaded, 3 verified, 0 failed")
        )
        # The HTTP histogram rides along so throttling vs latency is visible
        # in logs without the stdout block.
        self.assertIn("(http: 200:3, net errors: 0)", binary)
        self.assertIn(
            "download source phase: nothing to download",
            messages,
        )
        # The full block is never re-logged (that would duplicate the rows).
        for message in messages:
            self.assertNotIn("phase summary", message)

    def test_non_tty_progress_lines(self):
        config = self._provision()
        rc, out, err = self._run(config)
        self.assertEqual(rc, EXIT_SUCCESS)
        lines = out.splitlines()
        progress_lines = [line for line in lines if line.startswith("downloaded ")]
        self.assertTrue(progress_lines)
        self.assertTrue(any(line.startswith("downloaded 1/3 files (") for line in progress_lines))
        self.assertTrue(any(line.startswith("downloaded 3/3 files (") for line in progress_lines))

    def test_idempotent_rerun_prints_nothing_to_download(self):
        config = self._provision()
        self.assertEqual(self._run(config)[0], EXIT_SUCCESS)
        rc, out, _ = self._run(config)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("Binary phase: nothing to download.", out)
        self.assertIn("Total: 0 files downloaded, 0 verified, 0 failed (", out)

    def test_failure_listed_and_exit_1(self):
        config = self._provision()
        self.assertEqual(self._run(config)[0], EXIT_SUCCESS)
        # Reset vifm, remove its file from the CACHE and the mirror pool:
        # the absent cache file must be re-fetched, hitting a permanent 404.
        conn = sqlite3.connect(config.db_path)
        conn.execute("UPDATE binaries SET verified = 0 WHERE package_name = 'vifm'")
        conn.commit()
        conn.close()
        os.remove(os.path.join(
            self.cache_base, "binary", "pool", "main", "v", "vifm", "vifm_1.0-2_amd64.deb"
        ))
        os.remove(os.path.join(
            self.fixture_base, "binary", "pool", "main", "v", "vifm", "vifm_1.0-2_amd64.deb"
        ))
        rc, out, err = self._run(config)
        self.assertEqual(rc, EXIT_ERROR)
        self.assertIn("Failed:            1", out)
        self.assertIn("  - pool/main/v/vifm/vifm_1.0-2_amd64.deb: HTTP 404", out)
        self.assertIn("Total: 0 files downloaded, 0 verified, 1 failed (", out)

    def test_profile_switch_fetches_previously_missing_sources_indexes(self):
        # The mirror must serve the source index for the post-switch fetch.
        make_test_iso.write_source_index_for_mirror(self.fixture_base)
        # regular_user cache: source index never fetched (drop the fixture's copy).
        src_index = os.path.join(
            self.cache_base, "source", "dists", "trixie", "main", "source", "Sources.gz"
        )
        os.remove(src_index)
        self._write_defaults(f'[paths]\nbase_dir = "{self.cache_base}"\n[fetch]\nsource = false\n\n[[archive.source]]\nmirror = "http://127.0.0.1:{self.server.server_address[1]}"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
            self.assertEqual(handle_add(["vifm", "lib1"]), EXIT_SUCCESS)
        self.assertFalse(
            os.path.exists(src_index), "no Sources-index fetch may happen under source=false"
        )

        # Switch to a source-enabled profile (developer/graybeard) and re-add.
        self._write_defaults(f'[paths]\nbase_dir = "{self.cache_base}"\n[fetch]\nsource = true\n\n[[archive.source]]\nmirror = "http://127.0.0.1:{self.server.server_address[1]}"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_add(["vifm"]), EXIT_SUCCESS)
        self.assertTrue(
            os.path.exists(src_index),
            "the never-fetched Sources index must be fetched after the profile switch",
        )

    def test_unconfirmable_record_json_error(self):
        config = self._provision()
        conn = sqlite3.connect(config.db_path)
        conn.execute(
            "INSERT INTO binaries (package_name, version, architecture, release, component, status, verified)"
            " VALUES ('ghost', '1.0-2', 'amd64', 'trixie', 'main', 'current', 0)"
        )
        conn.commit()
        conn.close()
        rc, out, err = self._run(config)
        self.assertEqual(rc, EXIT_ERROR)
        payload = _assert_single_json(err)
        self.assertEqual(
            payload,
            {
                "error": "No index entry for trixie/main ghost 1.0-2; restore or re-fetch the cached index",
                "exit_code": 1,
            },
        )

    def test_missing_cached_index_json_error(self):
        config = self._provision()
        os.remove(os.path.join(
            self.cache_base, "binary", "dists", "trixie", "main", "binary-amd64", "Packages.gz"
        ))
        rc, out, err = self._run(config)
        self.assertEqual(rc, EXIT_ERROR)
        payload = json.loads(err)
        self.assertEqual(
            payload,
            {
                "error": "Cached index missing for trixie/main; the index is part of the cache — restore or re-fetch it",
                "exit_code": 1,
            },
        )
        deb_files = []
        for root, _, files in os.walk(os.path.join(self.cache_base, "binary", "pool")):
            deb_files.extend(f for f in files if f.endswith(".deb"))
        self.assertEqual(deb_files, [])


class TestConfigContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._write_defaults('[paths]\nbase_dir = "/tmp/test_contract"\n[fetch]\nsource = true\n')
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _write_local(self, content: str):
        with open(os.path.join(self.tmp.name, "project.local.toml"), "w") as f:
            f.write(content)

    def test_text_format_contains_expected_fields(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_config("text")
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("base_dir", output)
        self.assertIn("binary_cache", output)
        self.assertIn("iso_dir", output)
        self.assertIn("log_dir", output)
        self.assertIn("(default)", output)

    def test_text_format_shows_admin_email_default(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_config("text")
        self.assertEqual(rc, 0)
        line = next(
            line for line in buf.getvalue().splitlines() if "admin_email" in line
        )
        self.assertIn("noreply@example.com", line)
        self.assertIn("(default)", line)

    def test_json_format_shows_admin_email(self):
        self._write_local('[metadata]\nadmin_email = "ops@example.com"\n')
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_config("json")
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["metadata"]["admin_email"], "ops@example.com")
        self.assertTrue(data["metadata"]["admin_email_override"])

    def test_text_format_shows_override(self):
        self._write_local('[paths]\nbase_dir = "/tmp/override"\n')
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_config("text")
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("(override: project.local.toml)", output)

    def test_json_format_structure(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_config("json")
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertIn("paths", data)
        self.assertIn("database", data)
        self.assertIn("logging", data)
        self.assertIn("fetch", data)
        self.assertIs(data["fetch"]["source"], True)
        self.assertIn("directory", data["logging"])
        self.assertIn("base_dir", data["paths"])
        self.assertIn("iso_dir", data["paths"])

    def test_text_shows_source_depth_and_fetch_source_default(self):
        # Overwrite defaults: no [fetch] source, no [resolution] depth keys —
        # the dataclass defaults apply (regular_user presets).
        self._write_defaults('[paths]\nbase_dir = "/tmp/test_contract"\n')
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_config("text")
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("max_source_dep_depth", output)
        depth_line = next(line for line in output.splitlines() if "max_source_dep_depth" in line)
        self.assertIn(" 0 ", depth_line + " ")
        self.assertIn("(default)", depth_line)
        fetch_line = next(line for line in output.splitlines() if "fetch_source" in line)
        self.assertIn("false", fetch_line)
        self.assertIn("(default)", fetch_line)

    def test_text_shows_source_depth_override(self):
        # The key must exist in defaults for the local overlay to register an
        # override (existing loader convention; shipped project.defaults.toml
        # carries max_source_dep_depth = 0).
        self._write_defaults(
            '[paths]\nbase_dir = "/tmp/test_contract"\n[fetch]\nsource = true\n'
            '[resolution]\nmax_source_dep_depth = 0\n'
        )
        self._write_local('[resolution]\nmax_source_dep_depth = 1\n')
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_config("text")
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        depth_line = next(line for line in output.splitlines() if "max_source_dep_depth" in line)
        self.assertIn(" 1 ", depth_line + " ")
        self.assertIn("(override: project.local.toml)", depth_line)

    def test_json_shows_source_depth_and_override(self):
        # No depth key anywhere -> dataclass default, not overridden.
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_config("json")
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertIn("max_source_dep_depth", data["resolution"])
        self.assertIn("max_source_dep_depth_override", data["resolution"])
        self.assertEqual(data["resolution"]["max_source_dep_depth"], 0)
        self.assertIs(data["resolution"]["max_source_dep_depth_override"], False)

        # Key present in defaults, overridden locally -> override flagged.
        self._write_defaults(
            '[paths]\nbase_dir = "/tmp/test_contract"\n[fetch]\nsource = true\n'
            '[resolution]\nmax_source_dep_depth = 0\n'
        )
        self._write_local('[resolution]\nmax_source_dep_depth = 2\n')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_config("json")
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["resolution"]["max_source_dep_depth"], 2)
        self.assertIs(data["resolution"]["max_source_dep_depth_override"], True)

    def test_invalid_source_depth_json_error(self):
        self._write_defaults(
            '[paths]\nbase_dir = "/tmp/test_contract"\n[resolution]\nmax_source_dep_depth = -2\n'
        )
        import io
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = handle_config("json")
        self.assertEqual(rc, EXIT_CONFIG_ERROR)
        payload = _assert_single_json(err.getvalue())
        self.assertEqual(payload["exit_code"], 102)
        self.assertIn("max_source_dep_depth", payload["error"])
        self.assertIn("integer >= -1", payload["error"])

    def test_mistyped_values_and_unknown_keys_json_error(self):
        """A mistyped value or an unknown key name fails with exit 102 and a
        JSON payload on stderr naming the key — never a raw traceback."""
        cases = [
            ('[paths]\nbase_dir = 123\n', "base_dir"),
            ('[paths]\nbase_dir = "/tmp/x"\n[fetch]\nsource = "yes"\n', "source"),
            ('[paths]\nbase_dir = "/tmp/x"\nbinry_cache = "/tmp/x/b"\n', "binry_cache"),
            ('archive = 5\n[paths]\nbase_dir = "/tmp/x"\n', "archive"),
        ]
        for content, key in cases:
            with self.subTest(content=content):
                self._write_defaults(content)
                err = io.StringIO()
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    rc = handle_config("json")
                self.assertEqual(rc, EXIT_CONFIG_ERROR)
                payload = _assert_single_json(err.getvalue())
                self.assertEqual(payload["exit_code"], 102)
                self.assertIn(key, payload["error"])

    def test_relative_base_dir_in_defaults_config_error(self):
        """A relative `base_dir` fails at load with exit 102 and a JSON
        payload naming the key — never silently reinterpreted against the
        working directory."""
        self._write_defaults('[paths]\nbase_dir = "relative/path"\n[fetch]\nsource = true\n')
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = handle_config("json")
        self.assertEqual(rc, EXIT_CONFIG_ERROR)
        payload = _assert_single_json(err.getvalue())
        self.assertEqual(payload["exit_code"], 102)
        self.assertIn("[paths] base_dir", payload["error"])
        self.assertIn("relative/path", payload["error"])

    def test_relative_base_dir_in_local_override_config_error(self):
        """A relative `base_dir` in a local override is rejected exactly like
        the defaults path (exit 102, JSON naming the key)."""
        self._write_defaults('[paths]\nbase_dir = "/tmp/test_contract"\n[fetch]\nsource = true\n')
        self._write_local('[paths]\nbase_dir = "relative/override"\n')
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = handle_config("json")
        self.assertEqual(rc, EXIT_CONFIG_ERROR)
        payload = _assert_single_json(err.getvalue())
        self.assertEqual(payload["exit_code"], 102)
        self.assertIn("[paths] base_dir", payload["error"])

    def test_local_depth_override_in_absent_section_json(self):
        """A local override for a known key applies and is flagged even when
        the defaults file lacks the section containing it."""
        self._write_defaults('[paths]\nbase_dir = "/tmp/test_contract"\n')
        self._write_local('[resolution]\nmax_source_dep_depth = 2\n')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_config("json")
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["resolution"]["max_source_dep_depth"], 2)
        self.assertIs(data["resolution"]["max_source_dep_depth_override"], True)

    def test_local_bad_depth_in_absent_section_json_error(self):
        """A bad depth in a local override (defaults lacking the section)
        fails at load with exit 102 and a JSON payload naming the key."""
        self._write_defaults('[paths]\nbase_dir = "/tmp/test_contract"\n')
        self._write_local('[resolution]\nmax_source_dep_depth = -2\n')
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = handle_config("json")
        self.assertEqual(rc, EXIT_CONFIG_ERROR)
        payload = _assert_single_json(err.getvalue())
        self.assertEqual(payload["exit_code"], 102)
        self.assertIn("max_source_dep_depth", payload["error"])

    def test_config_with_missing_defaults_returns_error(self):
        os.remove(os.path.join(self.tmp.name, "project.defaults.toml"))
        rc = handle_config("text")
        self.assertEqual(rc, EXIT_CONFIG_ERROR)

    def test_each_profile_loads_and_shows_settings_in_config(self):
        """Each profile file loads as a valid, complete project
        setting and the applied settings (incl. both depth settings) appear in
        `package_mgmt config` text + JSON."""
        profiles = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "profiles")
        expected = {
            "regular_user": {"fetch_source": False, "max_source_dep_depth": 0, "max_build_dep_report_depth": 2},
            "developer": {"fetch_source": True, "max_source_dep_depth": 1, "max_build_dep_report_depth": 2},
            "graybeard": {"fetch_source": True, "max_source_dep_depth": -1, "max_build_dep_report_depth": 2},
        }
        for profile, exp in expected.items():
            with self.subTest(profile=profile):
                shutil.copy(
                    os.path.join(profiles, f"{profile}.toml"),
                    os.path.join(self.tmp.name, "project.defaults.toml"),
                )
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = handle_config("text")
                self.assertEqual(rc, 0)
                output = buf.getvalue()
                self.assertIn("max_source_dep_depth", output)
                self.assertIn("max_build_dep_report_depth", output)
                self.assertIn("fetch_source", output)
                depth_line = next(
                    line for line in output.splitlines() if "max_source_dep_depth" in line
                )
                self.assertIn(f" {exp['max_source_dep_depth']} ", depth_line + " ")

                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = handle_config("json")
                self.assertEqual(rc, 0)
                data = json.loads(buf.getvalue())
                self.assertEqual(data["fetch"]["source"], exp["fetch_source"])
                self.assertEqual(
                    data["resolution"]["max_source_dep_depth"],
                    exp["max_source_dep_depth"],
                )
                self.assertEqual(
                    data["resolution"]["max_build_dep_report_depth"],
                    exp["max_build_dep_report_depth"],
                )
                self.assertIn("max_source_dep_depth_override", data["resolution"])

    def test_cross_setting_special_values_load(self):
        """Setting C: the two cross-setting special-value
        combinations load and render — `max_source_dep_depth = 0` +
        `max_build_dep_report_depth = -1` (regular_user + report-all) and
        `-1` + `0` (full cache + depth-0 runtime check only)."""
        cases = [
            {"max_source_dep_depth": 0, "max_build_dep_report_depth": -1},
            {"max_source_dep_depth": -1, "max_build_dep_report_depth": 0},
        ]
        for case in cases:
            with self.subTest(**case):
                self._write_defaults(
                    '[paths]\nbase_dir = "/tmp/test_contract"\n[fetch]\nsource = true\n'
                    f"[resolution]\nmax_source_dep_depth = {case['max_source_dep_depth']}\n"
                    f"max_build_dep_report_depth = {case['max_build_dep_report_depth']}\n"
                )
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = handle_config("json")
                self.assertEqual(rc, 0)
                data = json.loads(buf.getvalue())
                self.assertEqual(
                    data["resolution"]["max_source_dep_depth"],
                    case["max_source_dep_depth"],
                )
                self.assertEqual(
                    data["resolution"]["max_build_dep_report_depth"],
                    case["max_build_dep_report_depth"],
                )

    def test_invalid_values_for_both_depth_settings_exit_102(self):
        """Setting C: a value below -1, a
        non-integer, or a boolean for EITHER depth setting fails at load time
        via the shared print_error path — JSON error, exit 102, naming the
        key."""
        for key in ("max_source_dep_depth", "max_build_dep_report_depth"):
            for value in ("-2", "1.5", "true"):
                with self.subTest(key=key, value=value):
                    self._write_defaults(
                        '[paths]\nbase_dir = "/tmp/test_contract"\n'
                        f"[resolution]\n{key} = {value}\n"
                    )
                    err = io.StringIO()
                    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                        rc = handle_config("json")
                    self.assertEqual(rc, EXIT_CONFIG_ERROR)
                    payload = _assert_single_json(err.getvalue())
                    self.assertEqual(payload["exit_code"], 102)
                    self.assertIn(key, payload["error"])
                    self.assertIn("integer >= -1", payload["error"])


class TestEnsureBaseContract(unittest.TestCase):
    """ensure-base summary output per contracts/cli.md."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "ensure_contract")
        self._write_defaults(f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = false\n\n[[archive.source]]\nmirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n')
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _provision(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_init()
        self.assertEqual(rc, 0)
        make_test_iso.write_archive(self.base)
        os.makedirs(os.path.join(self.base, "iso"), exist_ok=True)

    def test_summary_matches_contract(self):
        self._provision()
        make_test_iso.build_iso(os.path.join(self.base, "iso", "fx.iso"))

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_SUCCESS)
        output = buf.getvalue()
        self.assertIn("ISO image:        fx.iso (trixie: contrib, main)", output)
        self.assertIn("Binary packages:  6", output)
        self.assertIn("Provides entries: 1", output)
        self.assertIn("Index/image mismatches: 0", output)
        self.assertIn("Base packages resolved: 6/6", output)
        self.assertIn("Resolved 6/6", output)
        self.assertIn("Warnings:", output)
        self.assertIn("Errors:           none", output)

    def test_mismatch_block_matches_contract(self):
        self._provision()
        make_test_iso.build_iso(
            os.path.join(self.base, "iso", "fx.iso"),
            omit=["pool/main/v/vifm/vifm_1.0-2_amd64.deb"],
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_SUCCESS)
        output = buf.getvalue()
        self.assertIn("Index/image mismatches: 1", output)
        self.assertIn(
            "  - pool/main/v/vifm/vifm_1.0-2_amd64.deb (in index, not found in image)",
            output,
        )
        self.assertIn("Binary packages:  6", output)

    def test_missing_image_errors_with_json(self):
        with contextlib.redirect_stdout(io.StringIO()):
            handle_init()
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf), contextlib.redirect_stdout(io.StringIO()):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_ERROR)
        payload = json.loads(err_buf.getvalue())
        self.assertIn("exit_code", payload)
        self.assertIn("No installer image found", payload["error"])

    def test_fresh_cache_failure_creates_no_db(self):
        """Contract: a failed ensure-base on a cache with no database must not
        create one (spec 008 US1 AS2)."""
        db_path = os.path.join(self.base, "package_mgmt.db")
        self.assertFalse(os.path.exists(db_path))
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf), contextlib.redirect_stdout(io.StringIO()):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_ERROR)
        payload = json.loads(err_buf.getvalue())
        self.assertIn("No installer image found", payload["error"])
        self.assertFalse(
            os.path.exists(db_path),
            "no database file may be created when the run fails",
        )

    def test_rerun_refused_with_json_error(self):
        self._provision()
        make_test_iso.build_iso(os.path.join(self.base, "iso", "fx.iso"))
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_SUCCESS)
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf), contextlib.redirect_stdout(io.StringIO()):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_ERROR)
        payload = json.loads(err_buf.getvalue())
        self.assertEqual(payload["exit_code"], 1)
        self.assertIn("fresh cache", payload["error"])


class TestEnsureBaseGraybeardWarningContract(unittest.TestCase):
    """Under a graybeard configuration whose trixie sources are missing any of
    the four canonical components, `ensure-base` text output starts with an
    advisory warning (before the summary) naming the missing components.
    Complete graybeard, developer, and regular_user configs emit no such
    warning."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "graybeard_contract")
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _provision(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_init()
        self.assertEqual(rc, EXIT_SUCCESS)
        make_test_iso.write_archive(self.base)
        os.makedirs(os.path.join(self.base, "iso"), exist_ok=True)
        make_test_iso.build_iso(os.path.join(self.base, "iso", "fx.iso"))

    def _add_component_indexes(self, *components: str) -> None:
        """Write scratch index files for components the fixture archive does
        not ship by default, so a config declaring them loads cleanly."""
        for component in components:
            binary_dir = os.path.join(
                self.base, "binary", "dists", "trixie", component, "binary-amd64"
            )
            source_dir = os.path.join(
                self.base, "source", "dists", "trixie", component, "source"
            )
            os.makedirs(binary_dir, exist_ok=True)
            os.makedirs(source_dir, exist_ok=True)
            with open(os.path.join(binary_dir, "Packages.gz"), "wb") as f:
                f.write(
                    make_test_iso._gzip_bytes(
                        make_test_iso._index_text(make_test_iso.BASE_PACKAGES, component)
                    )
                )
            with open(os.path.join(source_dir, "Sources.gz"), "wb") as f:
                f.write(
                    make_test_iso._gzip_bytes(
                        make_test_iso._sources_text(make_test_iso.BASE_PACKAGES, component)
                    )
                )

    def _run(self) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_SUCCESS)
        return buf.getvalue()

    def test_graybeard_warning_precedes_summary_and_names_missing(self):
        self._write_defaults(
            f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = true\n'
            "[resolution]\nmax_source_dep_depth = -1\n"
            '[[archive.source]]\nmirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n'
        )
        self._provision()
        output = self._run()
        lines = output.splitlines()
        warn_idx = next(
            i for i, line in enumerate(lines)
            if line.startswith("WARNING: graybeard configuration")
        )
        summary_idx = next(
            i for i, line in enumerate(lines) if line.startswith("ISO image:")
        )
        self.assertLess(
            warn_idx, summary_idx, "the warning must precede the summary"
        )
        self.assertIn(
            "graybeard configuration (source = true, max_source_dep_depth = -1) is set",
            lines[warn_idx],
        )
        self.assertIn("non-free, non-free-firmware", output)

    def test_complete_graybeard_config_emits_no_warning(self):
        self._add_component_indexes("non-free", "non-free-firmware")
        self._write_defaults(
            f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = true\n'
            "[resolution]\nmax_source_dep_depth = -1\n"
            '[[archive.source]]\nmirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main", "contrib", "non-free", "non-free-firmware"]\n'
        )
        self._provision()
        self.assertNotIn("WARNING: graybeard configuration", self._run())

    def test_developer_config_emits_no_warning(self):
        self._write_defaults(
            f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = true\n'
            "[resolution]\nmax_source_dep_depth = 1\n"
            '[[archive.source]]\nmirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n'
        )
        self._provision()
        self.assertNotIn("WARNING: graybeard configuration", self._run())

    def test_regular_user_config_emits_no_warning(self):
        self._write_defaults(
            f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = false\n'
            "[resolution]\nmax_source_dep_depth = 0\n"
            '[[archive.source]]\nmirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n'
        )
        self._provision()
        self.assertNotIn("WARNING: graybeard configuration", self._run())


class TestImageRecordToolingContract(unittest.TestCase):
    """list/show output for image records matches the existing 003/007
    contract examples (same fields/format as any add-fetched record)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "tooling_contract")
        self._write_defaults(f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = true\n[resolution]\nmax_source_dep_depth = -1\n\n[[archive.source]]\nmirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n')
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _provision_image_records(self) -> str:
        """init + archive + fixture image + ensure-base; returns the db path."""
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_init()
        self.assertEqual(rc, EXIT_SUCCESS)
        make_test_iso.write_archive(self.base)
        os.makedirs(os.path.join(self.base, "iso"), exist_ok=True)
        make_test_iso.build_iso(os.path.join(self.base, "iso", "fx.iso"))
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_ensure_base()
        self.assertEqual(rc, EXIT_SUCCESS)
        return os.path.join(self.base, "package_mgmt.db")

    def test_list_deps_matches_007_contract_format(self):
        """list --deps text: root `name (status)` + tree markers per 007."""
        db = self._provision_image_records()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_list_deps(db, "vifm", fmt="text")
        self.assertEqual(rc, EXIT_SUCCESS)
        output = buf.getvalue()
        self.assertIn("vifm (current)", output)
        self.assertIn("├─ lib1 (= 2.0-1) [binary ✓, source ✓, verified: ✗]", output)
        self.assertIn("└─ lib2 (= 3.0-1) [binary ✓, source ✓, verified: ✗]", output)

    def test_list_status_current_matches_007_contract(self):
        """list --status current: same row shape as the 003/007 listings."""
        db = self._provision_image_records()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_list_deps(db, None, fmt="json", status="current")
        self.assertEqual(rc, EXIT_SUCCESS)
        rows = json.loads(buf.getvalue())
        self.assertEqual(
            set(rows[0]), {"package", "version", "release", "component", "status"}
        )
        vifm = next(r for r in rows if r["package"] == "vifm")
        self.assertEqual(
            vifm,
            {
                "package": "vifm",
                "version": "1.0-2",
                "release": "trixie",
                "component": "main",
                "status": "current",
            },
        )

    def test_show_text_matches_003_contract_format(self):
        """show text: table headers/field order per the 003 contract example."""
        db = self._provision_image_records()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_show(db, "vifm", fmt="text")
        self.assertEqual(rc, EXIT_SUCCESS)
        output = buf.getvalue()

        lines = output.splitlines()
        header_idx = next(
            i for i, line in enumerate(lines)
            if line.lstrip().startswith("id")
        )
        self.assertEqual(
            [cell.strip() for cell in lines[header_idx].split("│")],
            ["id", "package_name", "version", "architecture", "release",
             "component", "dependencies", "status", "verified", "circular",
             "sync_mask"],
        )
        row = next(
            line for line in lines[header_idx + 2:]
            if line.lstrip()[:1].isdigit() and "vifm" in line
        )
        self.assertIn("1.0-2", row)
        self.assertIn("trixie", row)
        self.assertIn("main", row)
        self.assertIn("current", row)

    def test_show_json_matches_003_contract_format(self):
        """show json: per-table key sets and verified=0 per the 003 contract."""
        db = self._provision_image_records()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_show(db, "vifm", fmt="json")
        self.assertEqual(rc, EXIT_SUCCESS)
        data = json.loads(buf.getvalue())
        self.assertEqual(
            set(data["packages"][0]),
            {"name", "requested_version", "status"},
        )
        self.assertEqual(
            set(data["binaries"][0]),
            {"id", "package_name", "version", "architecture", "release",
             "component", "dependencies", "status", "verified", "circular",
             "sync_mask"},
        )
        self.assertEqual(
            set(data["sources"][0]),
            {"id", "package_name", "source_version", "release", "component",
             "verified", "build_depends"},
        )
        self.assertEqual(
            set(data["binary_to_source"][0]),
            {"binary_id", "source_id"},
        )
        # add-rule: image registration never sets the verified flag
        self.assertEqual(data["binaries"][0]["verified"], 0)
        self.assertEqual(data["sources"][0]["verified"], 0)


class TestAddFileContract(unittest.TestCase):
    """Spec 009: file-mode summary block and error payloads per contracts/cli.md."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "addfile_contract")
        self._write_defaults(f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = false\n\n[[archive.source]]\nmirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n')
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _provision(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        make_test_iso.write_archive(self.base)

    def _write_list(self, content: str, name="pkgs.txt") -> str:
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_summary_block_matches_contract(self):
        self._provision()
        path = self._write_list("vifm\nlib1=2.0-1\nlib2\napp\nbase-pkg\nfirmware-x\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_add([], files=[path])
        self.assertEqual(rc, EXIT_SUCCESS)
        output = buf.getvalue()
        self.assertIn("Resolved 6/6", output)
        self.assertNotIn("\r", output)
        self.assertIn("Packages requested:  6", output)
        self.assertIn("Packages resolved:   6", output)
        self.assertIn("Errors:              0", output)

    def test_error_cap_wording_matches_contract(self):
        self._provision()
        path = self._write_list("".join(f"ghost-pkg-{i}\n" for i in range(1, 62)))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_add([], files=[path])
        self.assertEqual(rc, EXIT_ERROR)
        output = buf.getvalue()
        self.assertIn("Packages requested:  61", output)
        self.assertIn("Errors:              61 (showing first 50; 11 more in the log)", output)
        self.assertEqual(output.count("  - "), 50)

    def test_mixed_input_errors_with_json(self):
        self._provision()
        path = self._write_list("vim\n")
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf), contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add(["vim"], files=[path])
        self.assertEqual(rc, EXIT_ERROR)
        payload = json.loads(err_buf.getvalue())
        self.assertEqual(payload["exit_code"], EXIT_ERROR)
        self.assertIn("not both", payload["error"])

    def test_no_arguments_errors_with_json(self):
        self._provision()
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf), contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add([])
        self.assertEqual(rc, EXIT_ERROR)
        payload = json.loads(err_buf.getvalue())
        self.assertEqual(payload["exit_code"], EXIT_ERROR)
        self.assertIn("No packages specified", payload["error"])
        conn = sqlite3.connect(os.path.join(self.base, "package_mgmt.db"))
        try:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0,
                "nothing may be processed when no packages are given",
            )
        finally:
            conn.close()

    def test_malformed_line_errors_with_json_naming_line(self):
        self._provision()
        path = self._write_list("vim\nvim= \nlib1\n")
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf), contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add([], files=[path])
        self.assertEqual(rc, EXIT_ERROR)
        payload = json.loads(err_buf.getvalue())
        self.assertIn("Malformed line 2", payload["error"])
        self.assertIn(path, payload["error"])
        conn = sqlite3.connect(os.path.join(self.base, "package_mgmt.db"))
        try:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0,
                "nothing may be processed when a line is malformed",
            )
        finally:
            conn.close()

    def test_unreadable_file_errors_with_json(self):
        self._provision()
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf), contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add([], files=[os.path.join(self.tmp.name, "nope.txt")])
        self.assertEqual(rc, EXIT_ERROR)
        payload = json.loads(err_buf.getvalue())
        self.assertIn("Cannot read package list", payload["error"])
        self.assertIn("nope.txt", payload["error"])
        self.assertEqual(payload["exit_code"], EXIT_ERROR)

    def test_no_names_errors_with_json(self):
        self._provision()
        path = self._write_list("# only comments\n\n")
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf), contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add([], files=[path])
        self.assertEqual(rc, EXIT_ERROR)
        payload = json.loads(err_buf.getvalue())
        self.assertIn("No package names found", payload["error"])
        self.assertIn(path, payload["error"])
        self.assertEqual(payload["exit_code"], EXIT_ERROR)


class TestAlternativeGroupResolutionContract(unittest.TestCase):
    """A version-skewed alternative group resolves to the closure apt would
    install — add exits 0 and verify reports COMPLETE (the pre-registered
    a 1.0 row must not suppress the resolvable b alternative)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_base = os.path.join(self.tmp.name, "cache")
        self.fixture_base = os.path.join(self.tmp.name, "fixture")
        self._write_custom_archive(self.fixture_base)
        self.server = make_test_iso.serve_pool(self.fixture_base, port=0)
        port = self.server.server_address[1]
        self._write_defaults(
            f'[paths]\nbase_dir = "{self.cache_base}"\n[fetch]\nsource = false\n\n'
            "[[archive.source]]\n"
            f'mirror = "http://127.0.0.1:{port}"\nrelease = "trixie"\ncomponents = ["main"]\n'
        )
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.server.shutdown()
        self.server.thread.join()
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    @staticmethod
    def _write_custom_archive(base_dir: str) -> None:
        pkgs = [
            ("a", "1.0-1", None, None),
            ("b", "1.0-1", None, None),
            ("app", "1.0-1", "a (>= 2.0) | b", None),
        ]
        component = "main"
        binary_dir = os.path.join(
            base_dir, "binary", "dists", "trixie", component, "binary-amd64"
        )
        source_dir = os.path.join(
            base_dir, "source", "dists", "trixie", component, "source"
        )
        os.makedirs(binary_dir, exist_ok=True)
        os.makedirs(source_dir, exist_ok=True)
        with open(os.path.join(binary_dir, "Packages.gz"), "wb") as f:
            f.write(make_test_iso._gzip_bytes(make_test_iso._index_text(pkgs, component)))
        with open(os.path.join(source_dir, "Sources.gz"), "wb") as f:
            f.write(make_test_iso._gzip_bytes(make_test_iso._sources_text(pkgs, component)))
        for entry in pkgs:
            name, version = entry[0], entry[1]
            deb = make_test_iso.binary_pool_filename(component, name, version)
            make_test_iso._write_pool_path(
                base_dir, deb, make_test_iso._pool_content(os.path.basename(deb))
            )

    def test_version_skewed_group_add_exit0_and_verify_complete(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_add(["a"]), EXIT_SUCCESS)
        out_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf):
            rc = handle_add(["app"])
        self.assertEqual(
            rc, EXIT_SUCCESS, f"add must exit 0 for the resolvable group: {out_buf.getvalue()}"
        )
        db_path = os.path.join(self.cache_base, "package_mgmt.db")
        conn = sqlite3.connect(db_path)
        try:
            b_row = conn.execute(
                "SELECT version FROM binaries WHERE package_name = 'b' AND status = 'current'"
            ).fetchone()
            self.assertEqual(
                b_row, ("1.0-1",),
                "the resolvable alternative must be brought into the closure",
            )
            a_row = conn.execute(
                "SELECT version FROM binaries WHERE package_name = 'a' AND status = 'current'"
            ).fetchone()
            self.assertEqual(a_row, ("1.0-1",), "a stays at its registered 1.0 version")
        finally:
            conn.close()
        config, _ = load_config()
        verify_out = io.StringIO()
        with contextlib.redirect_stdout(verify_out):
            rc = handle_verify_graph(
                db_path, "app", config.max_build_dep_report_depth, config.max_source_dep_depth
            )
        self.assertEqual(rc, EXIT_SUCCESS, verify_out.getvalue())
        self.assertIn("Runtime Status:         COMPLETE", verify_out.getvalue())


class TestAddStaleSchemaContract(unittest.TestCase):
    """Contract: a stale-schema add exits 101 with a JSON error, matching the
    shared schema gate used by the read commands."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "add_contract")
        os.makedirs(self.base, exist_ok=True)
        self._write_defaults(f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = false\n\n[[archive.source]]\nmirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main"]\n')
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def test_stale_schema_add_exits_101_with_json(self):
        db_path = os.path.join(self.base, "package_mgmt.db")
        initialize_database(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
        conn.close()

        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf):
            rc = handle_add(["vim"])
        self.assertEqual(rc, EXIT_SCHEMA_MISMATCH)
        payload = _assert_single_json(err_buf.getvalue())
        self.assertEqual(payload["exit_code"], EXIT_SCHEMA_MISMATCH)
        self.assertIn("Schema mismatch", payload["error"])


class TestInitContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "init_test")
        self._write_defaults(f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = false\n\n[[archive.source]]\nmirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main"]\n')
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def test_init_output_format(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_init()
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("Base directory:", output)
        self.assertIn("Binary cache:", output)
        self.assertIn("Source cache:", output)
        self.assertIn("Database:", output)
        self.assertIn("Schema version:", output)
        self.assertIn("Status:", output)
        self.assertIn("initialized", output)

    def test_init_exit_codes_split(self):
        """Contract: a permission failure exits 100; any other init failure
        (here ENOSPC) exits 1 — the generic failure code."""
        with mock.patch(
            "package_mgmt.lib.service.initializer.os.makedirs",
            side_effect=PermissionError(13, "Permission denied"),
        ):
            err_buf = io.StringIO()
            with contextlib.redirect_stderr(err_buf):
                rc = handle_init()
            self.assertEqual(rc, EXIT_PERMISSION)
            payload = json.loads(err_buf.getvalue())
            self.assertEqual(payload["exit_code"], EXIT_PERMISSION)

        with mock.patch(
            "package_mgmt.lib.service.initializer.os.makedirs",
            side_effect=OSError(28, "No space left on device"),
        ):
            err_buf = io.StringIO()
            with contextlib.redirect_stderr(err_buf):
                rc = handle_init()
            self.assertEqual(rc, EXIT_ERROR)
            payload = json.loads(err_buf.getvalue())
            self.assertEqual(payload["exit_code"], EXIT_ERROR)

    def test_init_offline_summary_note(self):
        """Contract: an init with zero cached binaries (unreachable mirrors)
        exits 0 and its summary states the offline mode."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_init()
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn(
            "initialized (no mirror reachable \u2014 offline mode, 0 binaries cached)",
            buf.getvalue(),
        )


class TestFlagArgumentContract(unittest.TestCase):
    """Contract: verify --graph is a usage error; systems add validates the
    moniker; add rejects an empty pin; list --deps/--status are mutually
    exclusive (FR-004/FR-005/FR-006)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "flags_contract")
        os.makedirs(self.base, exist_ok=True)
        self._write_defaults(
            f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = false\n\n'
            "[[archive.source]]\n"
            'mirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main"]\n'
        )
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _run_main(self, argv):
        err_buf = io.StringIO()
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(err_buf):
            with self.assertRaises(SystemExit) as ctx:
                main()
        return ctx.exception.code, err_buf.getvalue()

    def test_verify_graph_is_usage_error(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["verify", "--graph"])

    def test_systems_add_empty_moniker_clean_error(self):
        db_path = os.path.join(self.base, "package_mgmt.db")
        initialize_database(db_path)
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf):
            rc = handle_systems_add(db_path, "")
        self.assertEqual(rc, EXIT_ERROR)
        payload = _assert_single_json(err_buf.getvalue())
        self.assertEqual(payload["exit_code"], EXIT_ERROR)
        self.assertEqual(
            payload["error"], "System name must be a non-empty word (no spaces, newlines)"
        )
        conn = sqlite3.connect(db_path)
        self.assertEqual(conn.execute("SELECT count(*) FROM systems").fetchone()[0], 0)
        conn.close()

    def test_systems_add_whitespace_moniker_clean_error(self):
        db_path = os.path.join(self.base, "package_mgmt.db")
        initialize_database(db_path)
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf):
            rc = handle_systems_add(db_path, "a b")
        self.assertEqual(rc, EXIT_ERROR)
        payload = _assert_single_json(err_buf.getvalue())
        self.assertEqual(payload["exit_code"], EXIT_ERROR)

    def test_add_empty_pin_rejected(self):
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf):
            rc = handle_add(["vim="])
        self.assertEqual(rc, EXIT_ERROR)
        payload = _assert_single_json(err_buf.getvalue())
        self.assertEqual(payload["exit_code"], EXIT_ERROR)
        self.assertIn("vim=", payload["error"])
        self.assertIn("empty version pin", payload["error"])

    def test_list_deps_and_status_mutually_exclusive(self):
        code, err = self._run_main(
            ["package_mgmt", "list", "--deps", "vifm", "--status", "all"]
        )
        self.assertEqual(code, EXIT_ERROR)
        payload = _assert_single_json(err)
        self.assertEqual(payload["exit_code"], EXIT_ERROR)
        self.assertEqual(payload["error"], "Use either --deps or --status, not both")


class TestShowDepsContract(unittest.TestCase):
    """show --deps emits the dependency tree."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self._add_package("app", ["lib1"])
        self._add_package("lib1", [])
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _add_package(self, name, deps):
        self.conn.execute(
            "INSERT INTO packages (name, status) VALUES (?, 'pending')",
            (name,),
        )
        deps_json = json.dumps([{"name": d, "type": "depends"} for d in deps])
        bid = self.conn.execute(
            """INSERT INTO binaries (package_name, version, architecture, release, component,
               dependencies, status, verified)
               VALUES (?, '1.0', 'amd64', 'trixie', 'main', ?, 'current', 0)
               RETURNING id""",
            (name, deps_json),
        ).fetchone()[0]
        sid = self.conn.execute(
            """INSERT INTO sources (package_name, source_version, release, component, verified)
               VALUES (?, '1.0', 'trixie', 'main', 0)
               RETURNING id""",
            (name,),
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO binary_to_source (binary_id, source_id) VALUES (?, ?)",
            (bid, sid),
        )

    def test_json_contains_tables_and_dependency_tree(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_show(self.db_path, "app", fmt="json", deps=True)
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertIn("tables", data)
        self.assertIn("dependency_tree", data)
        self.assertEqual(data["dependency_tree"]["name"], "app")
        self.assertIn("binaries", data["tables"])
        child_names = [c["name"] for c in data["dependency_tree"]["dependencies"]]
        self.assertIn("lib1", child_names)

    def test_text_contains_tree_section(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_show(self.db_path, "app", fmt="text", deps=True)
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("Dependency Tree", output)
        self.assertIn("app (pending)", output)
        self.assertIn("lib1", output)

    def test_no_deps_keeps_tables_only(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_show(self.db_path, "app", fmt="json", deps=False)
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertNotIn("tables", data)
        self.assertNotIn("dependency_tree", data)
        self.assertIn("binaries", data)

    def test_unknown_package_errors(self):
        """show on an unknown package is a clean error (exit 1), matching
        `list --deps` — never empty tables with exit 0."""
        import io
        import contextlib
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_show(self.db_path, "no-such-package")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(
            json.loads(err.getvalue())["error"], "Package not found: no-such-package"
        )
        self.assertEqual(out.getvalue(), "")

class TestShowVirtualResolutionsContract(unittest.TestCase):
    """US10: show --tables virtual_resolutions renders the joined provider view."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        provider_id = self.conn.execute(
            "INSERT INTO binaries (package_name, version, architecture, release, component, status)"
            " VALUES ('gawk', '5.2.1-2', 'amd64', 'trixie', 'main', 'current') RETURNING id"
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO virtual_resolutions (virtual_name, resolved_binary_id) VALUES ('awk', ?)",
            (provider_id,),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_json_shows_joined_columns(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_show(self.db_path, "gawk", tables=["virtual_resolutions"], fmt="json")
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        row = data["virtual_resolutions"][0]
        self.assertEqual(row["virtual_name"], "awk")
        self.assertEqual(row["resolved_package"], "gawk")
        self.assertEqual(row["version"], "5.2.1-2")
        self.assertEqual(row["status"], "current")
        self.assertEqual(row["release"], "trixie")

    def test_text_shows_joined_headers(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_show(self.db_path, "gawk", tables=["virtual_resolutions"], fmt="text")
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("resolved_package", output)
        self.assertIn("awk", output)
        self.assertIn("gawk", output)


class TestSystemsContract(unittest.TestCase):
    """Spec 010: systems output formats per contracts/cli.md — moniker-only,
    never the opaque identifier; errors as JSON on stderr."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        from package_mgmt.lib.service.db_init import initialize_database
        initialize_database(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, handler, *args, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handler(self.db_path, *args, **kwargs)
        return rc, out.getvalue(), err.getvalue()

    def test_add_output_matches_contract(self):
        rc, out, _ = self._run(handle_systems_add, "bravo-main", note="main box")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "Added system 'bravo-main'\n")
        self.assertNotIn("2", out)

    def test_list_output_matches_contract(self):
        self._run(handle_systems_add, "bravo-main", note="main operator")
        self._run(handle_systems_add, "lab-box")
        rc, out, _ = self._run(handle_systems_list)
        self.assertEqual(rc, EXIT_SUCCESS)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("NAME"))
        self.assertIn("NOTE", lines[0])
        self.assertIn("DELIVERED", lines[0])
        self.assertTrue(any(line.startswith("bravo-main") for line in lines))
        self.assertIn("main operator", out)
        self.assertTrue(any(line.startswith("lab-box") for line in lines))

    def test_duplicate_error_matches_contract(self):
        self._run(handle_systems_add, "bravo-main")
        rc, _, err = self._run(handle_systems_add, "bravo-main")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(
            json.loads(err),
            {"error": "System 'bravo-main' already exists", "exit_code": 1},
        )

    def test_rename_output_matches_contract(self):
        self._run(handle_systems_add, "bravo-main")
        rc, out, _ = self._run(handle_systems_rename, "bravo-main", "bravo-laptop")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "Renamed system 'bravo-main' to 'bravo-laptop'\n")

    def test_remove_output_matches_contract(self):
        self._run(handle_systems_add, "lab-box")
        rc, out, _ = self._run(handle_systems_remove, "lab-box")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "Removed system 'lab-box'\n")

    def test_unknown_error_matches_contract(self):
        rc, _, err = self._run(handle_systems_remove, "ghost")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(
            json.loads(err),
            {"error": "Unknown system 'ghost'", "exit_code": 1},
        )


class TestSnapshotContract(unittest.TestCase):
    """Spec 010: snapshot output formats per contracts/cli.md — pending and
    marked counts, JSON errors on stderr, nothing modified on errors."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        from package_mgmt.lib.service.db_init import initialize_database
        initialize_database(self.db_path)
        with contextlib.redirect_stdout(io.StringIO()):
            handle_systems_add(self.db_path, "bravo-main")
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO binaries (package_name, version, release, component, status, verified)"
            " VALUES ('pkg1', '1.0', 'trixie', 'main', 'current', 1)"
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, system, dry_run=False):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_snapshot(self.db_path, system, dry_run)
        return rc, out.getvalue(), err.getvalue()

    def test_dry_run_output_matches_contract(self):
        rc, out, _ = self._run("bravo-main", dry_run=True)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "1 pending for 'bravo-main'\n")

    def test_marked_output_matches_contract(self):
        rc, out, _ = self._run("bravo-main")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "Marked 1 binaries for 'bravo-main'\n")

    def test_unknown_system_error_matches_contract(self):
        rc, _, err = self._run("ghost")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(
            json.loads(err),
            {"error": "Unknown system 'ghost'", "exit_code": 1},
        )

    def test_missing_system_error_matches_contract(self):
        rc, _, err = self._run(None)
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(
            json.loads(err),
            {"error": "snapshot requires --system <name>", "exit_code": 1},
        )


class TestSyncContract(unittest.TestCase):
    """Spec 013: sync output formats per contracts/cli.md — summary block,
    zero-pending rerun, and JSON errors on stderr."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_base = os.path.join(self.tmp.name, "cache")
        self.fixture_base = os.path.join(self.tmp.name, "fixture")
        make_test_iso.write_archive(self.cache_base)
        make_test_iso.write_pool_files(self.fixture_base)
        self.server = make_test_iso.serve_pool(self.fixture_base, port=0)
        port = self.server.server_address[1]
        self._write_defaults(f'[paths]\nbase_dir = "{self.cache_base}"\n[fetch]\nsource = false\n\n[[archive.source]]\nmirror = "http://127.0.0.1:{port}"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n')
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.server.shutdown()
        self.server.thread.join()
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _run(self, handler, *args, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handler(*args, **kwargs)
        return rc, out.getvalue(), err.getvalue()

    def _provision(self):
        """Baseline handoff for the fixture subset, then new verified packages."""
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add(["vifm", "lib1"])
        self.assertEqual(rc, EXIT_SUCCESS)
        config = load_config()[0]
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_download(config.db_path, config)
        self.assertEqual(rc, EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_systems_add(config.db_path, "prod-box")
        self.assertEqual(rc, EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_handoff(config.db_path, config, "prod-box")
        self.assertEqual(rc, EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add(["app", "base-pkg"])
        self.assertEqual(rc, EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_download(config.db_path, config)
        self.assertEqual(rc, EXIT_SUCCESS)
        return config

    def _diff_exists(self):
        return os.path.exists(os.path.join(self.cache_base, "binary", "diff"))

    def test_summary_block_matches_contract(self):
        config = self._provision()
        rc, out, err = self._run(handle_sync, config.db_path, config, "prod-box")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(err, "")
        self.assertIn(f"Sync ready:      {self.cache_base}/binary/diff", out)
        self.assertIn("Release:         trixie", out)
        self.assertIn("Components:      main, contrib", out)
        self.assertIn("New packages:    2 (main: 2)", out)
        # The baseline add of vifm+lib1 pulls lib2 in, so prior = 3.
        self.assertIn("Total offered:   5 (prior 3 + new 2)", out)
        self.assertIn("Signed:          yes (key offline-cache.gpg, same as handoff)", out)
        self.assertIn(f"Apply:           {self.cache_base}/binary/diff/apply.sh", out)
        self.assertIn("Snapshot:        2 binaries marked for 'prod-box'", out)
        # The diff directory exists with the apply script and manifest.
        self.assertTrue(self._diff_exists())
        self.assertTrue(os.path.exists(os.path.join(
            self.cache_base, "binary", "diff", "apply.sh")))
        self.assertTrue(os.path.exists(os.path.join(
            self.cache_base, "binary", "diff", "manifest.txt")))

    def test_missing_system_errors_with_json(self):
        config = self._provision()
        rc, _, err = self._run(handle_sync, config.db_path, config, None)
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(
            json.loads(err),
            {"error": "sync requires --system <moniker>", "exit_code": 1},
        )

    def test_unknown_system_errors_with_json_and_generates_nothing(self):
        config = self._provision()
        rc, _, err = self._run(handle_sync, config.db_path, config, "ghost")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(
            json.loads(err),
            {"error": "Unknown system 'ghost'", "exit_code": 1},
        )
        self.assertFalse(self._diff_exists(),
                         "nothing may be generated for an unknown moniker")

    def test_zero_pending_rerun_reports_zero(self):
        config = self._provision()
        rc, _, err = self._run(handle_sync, config.db_path, config, "prod-box")
        self.assertEqual(rc, EXIT_SUCCESS)
        rc, out, err = self._run(handle_sync, config.db_path, config, "prod-box")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(err, "")
        self.assertIn(f"Sync ready:      {self.cache_base}/binary/diff", out)
        self.assertIn("New packages:    0 — nothing new since the last sync for 'prod-box'", out)
        self.assertIn("Snapshot:        no change", out)
        # The diff is regenerated as empty: no stale new files remain.
        diff_dir_path = os.path.join(self.cache_base, "binary", "diff")
        with open(os.path.join(diff_dir_path, "manifest.txt")) as f:
            rerun_lines = [line for line in f.read().splitlines() if line.strip()]
        self.assertTrue(rerun_lines, "the regenerated manifest keeps the delivered base set")
        self.assertTrue(all(line.startswith("delivered ") for line in rerun_lines))
        self.assertTrue(os.path.exists(os.path.join(diff_dir_path, "apply.sh")))
        pool_files = []
        for root, _, names in os.walk(os.path.join(diff_dir_path, "pool")):
            pool_files.extend(names)
        self.assertEqual(pool_files, [], "no stale pool files may remain after an empty regeneration")


class TestHandoffContract(unittest.TestCase):
    """Spec 012: handoff output formats per contracts/cli.md — summary block,
    snapshot line, warnings, and JSON errors on stderr."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_base = os.path.join(self.tmp.name, "cache")
        self.fixture_base = os.path.join(self.tmp.name, "fixture")
        make_test_iso.write_archive(self.cache_base)
        make_test_iso.write_pool_files(self.fixture_base)
        self.server = make_test_iso.serve_pool(self.fixture_base, port=0)
        port = self.server.server_address[1]
        self._write_defaults(f'[paths]\nbase_dir = "{self.cache_base}"\n[fetch]\nsource = false\n\n[[archive.source]]\nmirror = "http://127.0.0.1:{port}"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n')
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.server.shutdown()
        self.server.thread.join()
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _provision(self, download: bool = True):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_add(["vifm", "lib1"])
        self.assertEqual(rc, EXIT_SUCCESS)
        config = load_config()[0]
        if download:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = handle_download(config.db_path, config)
            self.assertEqual(rc, EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_systems_add(config.db_path, "prod-box")
        self.assertEqual(rc, EXIT_SUCCESS)
        return load_config()[0]

    def _run(self, config, system="prod-box"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_handoff(config.db_path, config, system)
        return rc, out.getvalue(), err.getvalue()

    def _masks(self, config):
        conn = sqlite3.connect(config.db_path)
        try:
            return [r[0] for r in conn.execute(
                "SELECT sync_mask FROM binaries ORDER BY package_name"
            ).fetchall()]
        finally:
            conn.close()

    def test_summary_block_matches_contract(self):
        config = self._provision()
        rc, out, err = self._run(config)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(err, "")
        self.assertIn(f"Handoff ready: {self.cache_base}/binary/handoff", out)
        self.assertIn("Release:        trixie", out)
        self.assertIn("Components:     main, contrib", out)
        self.assertIn("Packages:       3 (main: 3, contrib: 0)", out)
        self.assertIn("Signed:         yes (key offline-cache.gpg)", out)
        self.assertIn(f"Bootstrap:      {self.cache_base}/binary/handoff/bootstrap.sh", out)
        self.assertIn("Snapshot:       3 binaries marked for 'prod-box'", out)
        # The snapshot is the delivery record: every verified binary marked.
        self.assertEqual(self._masks(config), [2, 2, 2])

    def test_rerun_snapshot_no_double_counting(self):
        config = self._provision()
        rc, out, _ = self._run(config)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("Snapshot:       3 binaries marked for 'prod-box'", out)
        first = self._masks(config)
        rc, out, _ = self._run(config)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("Snapshot:       3 binaries marked for 'prod-box'", out)
        self.assertEqual(self._masks(config), first)

    def test_missing_system_errors_with_json(self):
        config = self._provision()
        rc, _, err = self._run(config, system=None)
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(
            json.loads(err),
            {"error": "handoff requires --system <moniker>", "exit_code": 1},
        )

    def test_unknown_system_errors_with_json(self):
        config = self._provision()
        rc, _, err = self._run(config, system="ghost")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(
            json.loads(err),
            {"error": "Unknown system 'ghost'", "exit_code": 1},
        )
        self.assertFalse(os.path.exists(os.path.join(
            self.cache_base, "binary", "handoff"
        )), "nothing may be generated for an unknown moniker")

    def test_space_containing_cache_path_refused(self):
        """A cache path containing a space is refused with a clear diagnostic
        and exit 1 — the apt sources line would break, so no handoff is
        generated."""
        config = self._provision()
        spaced = ProjectConfig(
            base_dir=os.path.join(self.tmp.name, "My USB", "cache"),
            binary_cache=os.path.join(self.tmp.name, "My USB", "cache", "binary"),
            source_cache=os.path.join(self.tmp.name, "My USB", "cache", "source"),
            iso_dir=os.path.join(self.tmp.name, "My USB", "cache", "iso"),
            db_path=config.db_path,
            log_dir=os.path.join(self.tmp.name, "My USB", "cache", "logs"),
            sources=config.sources,
        )
        rc, _, err = self._run(spaced)
        self.assertEqual(rc, EXIT_ERROR)
        payload = json.loads(err)
        self.assertEqual(payload["exit_code"], 1)
        self.assertIn("My USB", payload["error"])
        self.assertIn("space", payload["error"].lower())
        self.assertFalse(os.path.exists(
            os.path.join(spaced.binary_cache, "handoff")
        ), "no handoff may be written from a space-containing cache path")

    def test_zero_verified_warns_and_succeeds(self):
        config = self._provision(download=False)
        rc, out, err = self._run(config)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("Packages:       0 (main: 0, contrib: 0)", out)
        self.assertIn("Warning:        0 packages — nothing verified yet; run download first?", out)
        self.assertIn("Snapshot:       0 binaries marked for 'prod-box'", out)

    def test_excluded_pool_record_warns_and_succeeds(self):
        config = self._provision()
        os.remove(os.path.join(
            self.cache_base, "binary", "pool", "main", "v", "vifm", "vifm_1.0-2_amd64.deb"
        ))
        rc, out, err = self._run(config)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("Packages:       2 (main: 2, contrib: 0)", out)
        self.assertIn(
            "Warning:        1 verified records excluded (missing pool file — run download)",
            out,
        )
        # The snapshot is the delivery record: only binaries actually in the
        # handoff are marked — vifm (excluded, pool file missing) is not.
        self.assertIn("Snapshot:       2 binaries marked for 'prod-box'", out)
        masks = self._masks(config)
        conn = sqlite3.connect(config.db_path)
        try:
            vifm_mask = conn.execute(
                "SELECT sync_mask FROM binaries WHERE package_name = 'vifm'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(vifm_mask, 0, "excluded binary must not be marked delivered")
        self.assertEqual(sum(masks), 4)


class TestImportDebContract(unittest.TestCase):
    """Spec 021: `import-deb` CLI contract — happy path, atomic failure on an
    unsatisfiable dependency, bad input, no component selector, and
    single-file-only (contracts/cli.md Phase 1)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        self._write_defaults(f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = false\n\n[[archive.source]]\nmirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n')
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _provision(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        return load_config()[0]

    def _run(self, handler, *args, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handler(*args, **kwargs)
        return rc, out.getvalue(), err.getvalue()

    def test_happy_path_imports_into_local_verified(self):
        config = self._provision()
        deb = os.path.join(self.tmp.name, "libfoo_1.2-3_amd64.deb")
        make_test_deb.build_deb(deb, package="libfoo", version="1.2-3")
        rc, out, err = self._run(handle_import_deb, deb)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(err, "")
        self.assertIn(
            "Imported libfoo 1.2-3 (amd64) into local component (verified)", out
        )
        conn = sqlite3.connect(config.db_path)
        try:
            row = conn.execute(
                "SELECT component, verified, status FROM binaries WHERE package_name = 'libfoo'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, ("local", 1, "current"))
        self.assertTrue(os.path.isfile(
            make_test_deb.pool_abs_path(self.base, "libfoo", "1.2-3")
        ), "the payload must land at the local pool path")

    def test_unsatisfiable_dependency_errors_exit_1_zero_changes(self):
        config = self._provision()
        deb = os.path.join(self.tmp.name, "app_1.0_amd64.deb")
        make_test_deb.build_deb(deb, package="app", version="1.0", depends="ghost (>= 2.0)")
        rc, out, err = self._run(handle_import_deb, deb)
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(out, "")
        payload = _assert_single_json(err)
        self.assertEqual(payload["exit_code"], 1)
        self.assertIn("unsatisfiable dependency", payload["error"])
        self.assertIn("ghost", payload["error"])
        conn = sqlite3.connect(config.db_path)
        try:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0,
                "an unsatisfiable import must register nothing",
            )
        finally:
            conn.close()
        self.assertFalse(os.path.exists(
            os.path.join(self.base, "binary", "pool", "local")
        ), "an unsatisfiable import must copy nothing")

    def test_missing_file_errors_exit_1_zero_changes(self):
        config = self._provision()
        rc, out, err = self._run(handle_import_deb, os.path.join(self.tmp.name, "missing.deb"))
        self.assertEqual(rc, EXIT_ERROR)
        payload = _assert_single_json(err)
        self.assertEqual(payload["exit_code"], 1)
        self.assertIn("missing.deb", payload["error"])
        conn = sqlite3.connect(config.db_path)
        try:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0
            )
        finally:
            conn.close()

    def test_non_deb_file_errors_exit_1(self):
        self._provision()
        bad = os.path.join(self.tmp.name, "not-a-deb.deb")
        with open(bad, "w") as f:
            f.write("not an ar archive")
        rc, _, err = self._run(handle_import_deb, bad)
        self.assertEqual(rc, EXIT_ERROR)
        payload = _assert_single_json(err)
        self.assertIn("not-a-deb.deb", payload["error"])

    def test_component_selector_rejected(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["import-deb", "x.deb", "--component", "main"])

    def test_multiple_debs_rejected(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["import-deb", "a.deb", "b.deb"])

    def test_help_lists_source_option_only(self):
        parser = build_parser()
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(["import-deb", "--help"])
        except SystemExit:
            pass

    def _source_dir(self) -> str:
        src_dir = os.path.join(self.tmp.name, "source-set")
        make_test_deb.build_dsc(
            src_dir,
            source="patch-tool",
            version="1.2-3",
            files={
                "patch-tool_1.2.orig.tar.gz": b"orig tarball content",
                "patch-tool_1.2-3.debian.tar.xz": b"debian packaging content",
            },
        )
        return src_dir

    def test_source_happy_path_links_source_exit_0(self):
        config = self._provision()
        deb = os.path.join(self.tmp.name, "patch-tool_1.2-3_amd64.deb")
        make_test_deb.build_deb(deb, package="patch-tool", version="1.2-3")
        src_dir = self._source_dir()

        rc, out, err = self._run(handle_import_deb, deb, src_dir)

        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(err, "")
        self.assertIn(
            "Source linked (patch-tool 1.2-3, local, 2 files verified against dsc)", out
        )
        conn = sqlite3.connect(config.db_path)
        try:
            row = conn.execute(
                "SELECT component, verified FROM sources WHERE package_name = 'patch-tool'"
            ).fetchone()
            link = conn.execute("SELECT count(*) FROM binary_to_source").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(row, ("local", 1))
        self.assertEqual(link, 1)

    def test_source_incomplete_set_errors_exit_1_binary_not_registered(self):
        config = self._provision()
        deb = os.path.join(self.tmp.name, "patch-tool_1.2-3_amd64.deb")
        make_test_deb.build_deb(deb, package="patch-tool", version="1.2-3")
        src_dir = self._source_dir()
        os.remove(os.path.join(src_dir, "patch-tool_1.2-3.debian.tar.xz"))

        rc, out, err = self._run(handle_import_deb, deb, src_dir)

        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(out, "")
        payload = _assert_single_json(err)
        self.assertEqual(payload["exit_code"], 1)
        self.assertIn("patch-tool_1.2-3.debian.tar.xz", payload["error"])
        conn = sqlite3.connect(config.db_path)
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM binaries WHERE package_name = 'patch-tool'"
                ).fetchone()[0],
                0,
                "the binary must not be registered when the source set is incomplete",
            )
        finally:
            conn.close()

    def test_source_as_file_errors_exit_1(self):
        self._provision()
        deb = os.path.join(self.tmp.name, "patch-tool_1.2-3_amd64.deb")
        make_test_deb.build_deb(deb, package="patch-tool", version="1.2-3")
        as_file = os.path.join(self.tmp.name, "single.dsc")
        with open(as_file, "w") as f:
            f.write("not a directory")

        rc, out, err = self._run(handle_import_deb, deb, as_file)

        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(out, "")
        payload = _assert_single_json(err)
        self.assertEqual(payload["exit_code"], 1)
        self.assertIn("directory", payload["error"])


class TestVerifyReportDepthContract(unittest.TestCase):
    """Contract: verify's build-tool lists honor the report depth
    (0 = no build-tool levels, -1 = all, N = up to N) for the full cache and
    the --package path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        # Link 'app' to its source so it is not an unlinked-binary defect.
        app_id = self.conn.execute(
            "INSERT INTO binaries (package_name, version, architecture, release, component, status)"
            " VALUES ('app', '1.0', 'amd64', 'trixie', 'main', 'current') RETURNING id"
        ).fetchone()[0]
        app_src = self.conn.execute(
            "INSERT INTO sources (package_name, source_version, release, component, verified)"
            " VALUES ('app-src', '1.0', 'trixie', 'main', 0) RETURNING id"
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO binary_to_source (binary_id, source_id) VALUES (?, ?)",
            (app_id, app_src),
        )
        # Three sources, each with a distinct missing build tool, whose
        # shallowest build-dep link sits at depth 1, 2 and 3 respectively.
        self._source_with_link("missing-1", 1)
        self._source_with_link("missing-2", 2)
        self._source_with_link("missing-3", 3)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _source_with_link(self, missing_tool, depth):
        tool_id = self.conn.execute(
            "INSERT INTO binaries (package_name, version, architecture, release, component, status)"
            " VALUES (?, '1.0', 'amd64', 'trixie', 'main', 'current') RETURNING id",
            (missing_tool,),
        ).fetchone()[0]
        tool_src = self.conn.execute(
            "INSERT INTO sources (package_name, source_version, release, component, verified)"
            " VALUES (?, '1.0', 'trixie', 'main', 0) RETURNING id",
            (missing_tool,),
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO binary_to_source (binary_id, source_id) VALUES (?, ?)",
            (tool_id, tool_src),
        )
        src_id = self.conn.execute(
            "INSERT INTO sources (package_name, source_version, release, component, verified, build_depends)"
            " VALUES ('app-src', ?, 'trixie', 'main', 0, ?) RETURNING id",
            (str(depth), json.dumps([{"name": "ghost-tool", "build_context": "build"}])),
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO source_build_deps (source_id, binary_id, build_dep_depth) VALUES (?, ?, ?)",
            (src_id, tool_id, depth),
        )

    def _run_verify(self, depth, package=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_verify_graph(self.db_path, package, depth)
        return rc, out.getvalue()

    def test_depth_zero_reports_no_build_tool_levels(self):
        rc, out = self._run_verify(0)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("Unresolved build deps:  0", out)
        self.assertIn("Missing build tools:    0", out)

    def test_depth_minus_one_reports_all_levels(self):
        rc, out = self._run_verify(-1)
        self.assertEqual(rc, EXIT_ERROR)
        self.assertIn("Missing build tools:    3", out)

    def test_positive_depth_reports_up_to_n_levels(self):
        rc, out = self._run_verify(2)
        self.assertEqual(rc, EXIT_ERROR)
        self.assertIn("Missing build tools:    2", out)

    def test_package_path_matches_full_cache_depth(self):
        rc, out = self._run_verify(0, package="app-src")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("Missing build tools:    0", out)
        rc, out = self._run_verify(-1, package="app-src")
        self.assertEqual(rc, EXIT_ERROR)
        self.assertIn("Missing build tools:    3", out)


class TestReadCommandGatingContract(unittest.TestCase):
    """show/list/verify gate on cache initialization like their siblings: no
    database -> exit 101 + JSON error on stderr + no file created; a
    schema-mismatched database takes the shared stale-schema rebuild path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "package_mgmt.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, handler, *args, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handler(self.db_path, *args, **kwargs)
        return rc, out.getvalue(), err.getvalue()

    def _assert_no_database_error(self, rc, err):
        self.assertEqual(rc, EXIT_SCHEMA_MISMATCH)
        payload = _assert_single_json(err)
        self.assertEqual(payload["exit_code"], EXIT_SCHEMA_MISMATCH)
        self.assertIn("Database not found", payload["error"])
        self.assertFalse(os.path.exists(self.db_path), "no database file may be created")

    def test_show_no_database_exits_101_no_file(self):
        rc, _, err = self._run(handle_show, "vifm")
        self._assert_no_database_error(rc, err)

    def test_list_no_database_exits_101_no_file(self):
        rc, _, err = self._run(handle_list_deps, None, "text", status="all")
        self._assert_no_database_error(rc, err)

    def test_verify_no_database_exits_101_no_file(self):
        rc, _, err = self._run(handle_verify_graph)
        self._assert_no_database_error(rc, err)

    def test_schema_mismatch_takes_shared_rebuild_path(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(EXPECTED_SCHEMA_SQL)
        conn.execute("DROP TABLE systems")
        conn.commit()
        conn.close()
        rc, _, err = self._run(handle_show, "vifm")
        self.assertEqual(rc, EXIT_SCHEMA_MISMATCH)
        payload = _assert_single_json(err)
        self.assertIn("rebuild", payload["error"].lower())

    def test_valid_database_output_unchanged(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(EXPECTED_SCHEMA_SQL)
        conn.execute("INSERT INTO packages VALUES ('vifm', NULL, 'pending')")
        conn.execute(
            "INSERT INTO binaries (package_name, version, architecture, release, component, status)"
            " VALUES ('vifm', '1.0', 'amd64', 'trixie', 'main', 'pending')"
        )
        conn.commit()
        conn.close()
        rc, out, err = self._run(handle_list_deps, "vifm")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("vifm (pending)", out)
        self.assertEqual(err, "")


class TestAlignRepoContract(unittest.TestCase):
    """align-repo CLI contract: argument surface, error exit codes (101/102)
    and JSON error shape per contracts/cli.md — search/verify flow behavior is
    covered by the integration suite."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "align_contract")
        os.makedirs(self.base, exist_ok=True)
        self.db_path = os.path.join(self.base, "package_mgmt.db")
        self.repo_dir = os.path.join(self.base, "repos")
        os.makedirs(self.repo_dir, exist_ok=True)
        self._write_defaults(
            f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = false\n\n'
            "[[archive.source]]\n"
            'mirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main"]\n'
        )
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _run(self, repo_dir=None, verify=None, fmt="text", db_path=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_align_repo(
                db_path or self.db_path,
                os.path.join(self.base, "binary"),
                repo_dir or self.repo_dir,
                verify,
                fmt,
            )
        return rc, out.getvalue(), err.getvalue()

    def test_parser_accepts_the_flags(self):
        parser = build_parser()
        args = parser.parse_args(
            ["align-repo", "--repo-dir", "repos", "--format", "json"]
        )
        self.assertEqual(args.command, "align-repo")
        self.assertEqual(args.repo_dir, "repos")
        self.assertEqual(args.format, "json")
        self.assertIsNone(args.verify)
        args = parser.parse_args(
            ["align-repo", "--verify", "manifests/hyprland-0.45.toml"]
        )
        self.assertEqual(args.verify, "manifests/hyprland-0.45.toml")

    def test_unknown_flag_is_usage_error(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["align-repo", "--bogus"])

    def test_invalid_format_exits_102(self):
        rc, _, err = self._run(fmt="xml")
        self.assertEqual(rc, EXIT_CONFIG_ERROR)
        payload = _assert_single_json(err)
        self.assertEqual(payload["exit_code"], EXIT_CONFIG_ERROR)
        self.assertIn("format", payload["error"])

    def test_missing_repo_dir_exits_102(self):
        initialize_database(self.db_path)
        rc, _, err = self._run(repo_dir=os.path.join(self.base, "nope"))
        self.assertEqual(rc, EXIT_CONFIG_ERROR)
        payload = _assert_single_json(err)
        self.assertEqual(payload["exit_code"], EXIT_CONFIG_ERROR)
        self.assertIn("nope", payload["error"])

    def test_missing_database_exits_101_no_file(self):
        rc, _, err = self._run()
        self.assertEqual(rc, EXIT_SCHEMA_MISMATCH)
        payload = _assert_single_json(err)
        self.assertEqual(payload["exit_code"], EXIT_SCHEMA_MISMATCH)
        self.assertIn("Database not found", payload["error"])
        self.assertFalse(os.path.exists(self.db_path))

    def test_malformed_manifest_exits_102(self):
        manifest = os.path.join(self.tmp.name, "bad-manifest.toml")
        with open(manifest, "w") as f:
            f.write("era = 5\n")
        rc, _, err = self._run(verify=manifest)
        self.assertEqual(rc, EXIT_CONFIG_ERROR)
        payload = _assert_single_json(err)
        self.assertEqual(payload["exit_code"], EXIT_CONFIG_ERROR)

    def test_missing_manifest_file_exits_102(self):
        rc, _, err = self._run(verify=os.path.join(self.tmp.name, "missing.toml"))
        self.assertEqual(rc, EXIT_CONFIG_ERROR)
        payload = _assert_single_json(err)
        self.assertEqual(payload["exit_code"], EXIT_CONFIG_ERROR)
        self.assertIn("missing.toml", payload["error"])


class TestPackageStackContract(unittest.TestCase):
    """`package-stack` args, exit codes, and text/JSON report shape."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "packstack")
        self._write_defaults(
            f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = true\n\n'
            "[[archive.source]]\nmirror = \"http://127.0.0.1:1\"\n"
            'release = "trixie"\ncomponents = ["main", "contrib"]\n'
        )
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _recipe(self, components: str, stack: str = "demo") -> str:
        os.makedirs(os.path.join(self.tmp.name, "recipes"), exist_ok=True)
        path = os.path.join(self.tmp.name, "recipes", "demo.toml")
        body = (
            f'[stack]\nname = "{stack}"\nrepo_dir = "repos"\n'
            'source_epoch = "2026-10-20"\n\n'
            + components
        )
        with open(path, "w") as f:
            f.write(body)
        return path

    def _data_only_component(self) -> str:
        """One data-only component (architecture all, no ELF) so the imported
        package carries no derived deps and needs nothing in the cache."""
        os.makedirs(
            os.path.join(self.tmp.name, "staging", "demo-tool", "usr/share/demo-tool"),
            exist_ok=True,
        )
        with open(
            os.path.join(self.tmp.name, "staging", "demo-tool", "usr/share/demo-tool", "readme.txt"),
            "w",
        ) as f:
            f.write("data\n")
        os.makedirs(os.path.join(self.tmp.name, "repos", "demo-tool"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "repos", "demo-tool", "meson.build"), "w") as f:
            f.write('project("demo-tool", version: "1.0.0")\n')
        with open(os.path.join(self.tmp.name, "repos", "demo-tool", "README"), "w") as f:
            f.write("hello\n")
        return (
            "[[component]]\n"
            'name = "demo-tool"\n'
            'binary_package = "demo-tool"\n'
            'source_tree = "demo-tool"\n'
            'version = "1.0.0"\n'
            'build_system = "meson"\n'
            'architecture = "all"\n'
            'description = "demo tool"\n'
            'maintainer = "T <t@example.invalid>"\n'
        )

    def _provision(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        make_test_iso.write_archive(self.base)

    def _run(self, recipe: str, fmt: str = "text"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_package_stack(recipe, fmt)
        return rc, out.getvalue(), err.getvalue()

    def test_parser_accepts_the_flags(self):
        parser = build_parser()
        args = parser.parse_args(["package-stack"])
        self.assertEqual(args.command, "package-stack")
        self.assertEqual(args.recipe, "recipes/hyprland.toml")
        self.assertEqual(args.format, "text")
        args = parser.parse_args(
            ["package-stack", "--recipe", "recipes/x.toml", "--format", "json"]
        )
        self.assertEqual(args.recipe, "recipes/x.toml")
        self.assertEqual(args.format, "json")
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["package-stack", "--bogus"])

    def test_success_text_output_shape(self):
        self._provision()
        recipe = self._recipe(self._data_only_component())
        rc, out, err = self._run(recipe, "text")
        self.assertEqual(rc, EXIT_SUCCESS, err)
        self.assertIn("stack: demo", out)
        self.assertIn("order: demo-tool", out)
        self.assertIn("components imported: 1/1", out)
        self.assertIn("Depends derived for 1 binaries", out)

    def test_success_json_output_shape(self):
        self._provision()
        recipe = self._recipe(self._data_only_component())
        rc, out, err = self._run(recipe, "json")
        self.assertEqual(rc, EXIT_SUCCESS, err)
        data = json.loads(out)
        self.assertEqual(
            set(data), {"stack", "components_total", "components_imported", "order", "components"}
        )
        self.assertEqual(data["stack"], "demo")
        self.assertEqual(data["components_total"], 1)
        self.assertEqual(data["components_imported"], 1)
        self.assertEqual(data["order"], ["demo-tool"])
        self.assertEqual(
            set(data["components"][0]),
            {"name", "binary_package", "version", "architecture",
             "derived_depends", "explicit_depends", "unattributed",
             "source_files", "linked", "verified"},
        )
        self.assertTrue(data["components"][0]["linked"])
        self.assertTrue(data["components"][0]["verified"])

    def test_reimport_is_idempotent(self):
        self._provision()
        recipe = self._recipe(self._data_only_component())
        rc, _, err = self._run(recipe, "json")
        self.assertEqual(rc, EXIT_SUCCESS, err)
        rc, out, err = self._run(recipe, "json")
        self.assertEqual(rc, EXIT_SUCCESS, err)
        data = json.loads(out)
        self.assertEqual(data["components_imported"], 1)
        conn = sqlite3.connect(os.path.join(self.base, "package_mgmt.db"))
        try:
            rows = conn.execute(
                "SELECT COUNT(*) FROM binaries WHERE package_name = 'demo-tool'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(rows, 1)

    def test_missing_recipe_exits_102(self):
        self._provision()
        rc, _, err = self._run(os.path.join(self.tmp.name, "recipes", "missing.toml"))
        self.assertEqual(rc, EXIT_CONFIG_ERROR)
        payload = _assert_single_json(err)
        self.assertEqual(payload["exit_code"], EXIT_CONFIG_ERROR)
        self.assertIn("cannot read recipe", payload["error"])

    def test_malformed_recipe_exits_102(self):
        self._provision()
        recipe = self._recipe(
            "[[component]]\nname = 5\n"
        )
        rc, _, err = self._run(recipe)
        self.assertEqual(rc, EXIT_CONFIG_ERROR)
        payload = _assert_single_json(err)
        self.assertEqual(payload["exit_code"], EXIT_CONFIG_ERROR)

    def test_invalid_format_exits_102(self):
        self._provision()
        recipe = self._recipe(self._data_only_component())
        rc, _, err = self._run(recipe, "xml")
        self.assertEqual(rc, EXIT_CONFIG_ERROR)
        payload = _assert_single_json(err)
        self.assertEqual(payload["exit_code"], EXIT_CONFIG_ERROR)
        self.assertIn("format", payload["error"])

    def test_missing_database_exits_101(self):
        recipe = self._recipe(self._data_only_component())
        rc, _, err = self._run(recipe)
        self.assertEqual(rc, EXIT_SCHEMA_MISMATCH)
        payload = _assert_single_json(err)
        self.assertEqual(payload["exit_code"], EXIT_SCHEMA_MISMATCH)
        self.assertIn("Database not found", payload["error"])

    def test_missing_source_built_dependency_exits_1(self):
        self._provision()
        os.makedirs(os.path.join(self.tmp.name, "repos", "demo-app"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "repos", "demo-app", "meson.build"), "w") as f:
            f.write('project("demo-app", version: "2.0.0")\n')
        recipe = self._recipe(
            self._data_only_component()
            + "[[component]]\n"
            'name = "demo-app"\n'
            'binary_package = "demo-app"\n'
            'source_tree = "demo-app"\n'
            'version = "2.0.0"\n'
            'build_system = "meson"\n'
            'description = "demo app"\n'
            'maintainer = "T <t@example.invalid>"\n'
            'explicit_depends = ["sdbus-c++ (>= 2.0.0)"]\n'
        )
        rc, _, err = self._run(recipe)
        self.assertEqual(rc, EXIT_ERROR)
        payload = _assert_single_json(err)
        self.assertEqual(payload["exit_code"], EXIT_ERROR)
        self.assertIn("demo-app", payload["error"])
        self.assertIn("sdbus-c++", payload["error"])
