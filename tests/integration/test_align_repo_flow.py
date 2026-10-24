import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_fixture_repos  # noqa: E402
import make_test_iso  # noqa: E402

from package_mgmt.cli.commands.align_repo import handle_align_repo  # noqa: E402
from package_mgmt.lib.service.alignment import load_manifest  # noqa: E402
from package_mgmt.lib.service.db_init import initialize_database  # noqa: E402
from package_mgmt.lib.util.errors import (  # noqa: E402
    EXIT_CONFIG_ERROR,
    EXIT_ERROR,
    EXIT_SUCCESS,
)

BASELINE_PKGS = [
    ("libwayland-dev", "1.23.1-3"),
    ("libinput-dev", "1.28.1-1"),
    ("libxkbcommon-dev", "1.7.0-2"),
    ("wayland-protocols", "1.44-1"),
]


class TestAlignRepoFlow(unittest.TestCase):
    """End-to-end align-repo against a fixture cache + fixture source repos:
    search success/failure, manifest writing, and --verify re-validation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_base = os.path.join(self.tmp.name, "cache")
        self.db_path = os.path.join(self.cache_base, "package_mgmt.db")
        self.binary_cache = os.path.join(self.cache_base, "binary")
        self.repo_dir = os.path.join(self.tmp.name, "repos")
        self._write_cache()
        make_fixture_repos.build_fixture_repos(self.repo_dir)
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_cache(self):
        os.makedirs(self.cache_base, exist_ok=True)
        initialize_database(self.db_path)
        conn = sqlite3.connect(self.db_path)
        for name, version in BASELINE_PKGS:
            conn.execute(
                "INSERT INTO binaries (package_name, version, architecture, release,"
                " component, status, verified)"
                " VALUES (?, ?, 'amd64', 'trixie', 'main', 'current', 1)",
                (name, version),
            )
        conn.commit()
        conn.close()
        binary_dir = os.path.join(
            self.binary_cache, "dists", "trixie", "main", "binary-amd64"
        )
        os.makedirs(binary_dir, exist_ok=True)
        pkgs = [
            (
                name,
                version,
                None,
                "wayland-server (= 1.23.1)" if name == "libwayland-dev" else None,
            )
            for name, version in BASELINE_PKGS
        ]
        with open(os.path.join(binary_dir, "Packages.gz"), "wb") as f:
            f.write(make_test_iso._gzip_bytes(make_test_iso._index_text(pkgs, "main")))

    def _run(self, repo_dir=None, verify=None, fmt="text"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_align_repo(
                self.db_path,
                self.binary_cache,
                repo_dir or self.repo_dir,
                verify,
                fmt,
            )
        return rc, out.getvalue(), err.getvalue()

    def _manifest_path(self):
        return os.path.join(self.tmp.name, "manifests", "hyprland-0.45.toml")

    def _checkout_manifest(self, manifest):
        for repo in manifest.repos:
            subprocess.run(
                ["git", "-C", os.path.join(self.repo_dir, repo.name),
                 "checkout", "-q", repo.commit],
                check=True,
            )

    def test_search_success_writes_manifest(self):
        rc, out, err = self._run()
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(err, "")
        self.assertIn("result: PASS", out)
        self.assertIn("0.45 selected", out)
        self.assertIn("manifest written: manifests/hyprland-0.45.toml", out)
        manifest = load_manifest(self._manifest_path())
        self.assertEqual(manifest.era, "0.45")
        self.assertEqual(len(manifest.repos), len(make_fixture_repos.REPO_NAMES))

    def test_search_json_shape(self):
        rc, out, _ = self._run(fmt="json")
        self.assertEqual(rc, EXIT_SUCCESS)
        data = json.loads(out)
        self.assertEqual(data["mode"], "search")
        self.assertEqual(data["repo_dir"], self.repo_dir)
        self.assertEqual(data["selected_era"], "0.45")
        self.assertEqual(data["result"], "PASS")
        self.assertEqual(data["manifest_written"], "manifests/hyprland-0.45.toml")
        self.assertEqual(data["candidates_evaluated"], ["0.44", "0.45", "0.46"])
        self.assertEqual(data["unmet"], [])
        self.assertIn("requirements_checked", data)

    def test_search_failure_names_unmet(self):
        failing_dir = os.path.join(self.tmp.name, "failing")
        make_fixture_repos.build_failing_only_repo(failing_dir)
        rc, out, err = self._run(repo_dir=failing_dir)
        self.assertEqual(rc, EXIT_ERROR)
        self.assertEqual(err, "")
        self.assertIn("result: FAIL", out)
        self.assertIn("libinput", out)
        self.assertIn("version_too_low", out)
        self.assertFalse(
            os.path.exists(os.path.join(self.tmp.name, "manifests")),
            "no manifest may be written when no era satisfies",
        )
        rc2, out2, _ = self._run(repo_dir=failing_dir, fmt="json")
        self.assertEqual(rc2, EXIT_ERROR)
        data = json.loads(out2)
        self.assertEqual(data["result"], "FAIL")
        self.assertIsNone(data["selected_era"])
        modules = {u["module"] for u in data["unmet"]}
        self.assertIn("libinput", modules)
        self.assertIn("glaze", modules)

    def test_verify_pass(self):
        self._run()
        manifest = load_manifest(self._manifest_path())
        self._checkout_manifest(manifest)
        rc, out, err = self._run(verify=self._manifest_path())
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(err, "")
        self.assertIn("(verify)", out)
        self.assertIn("result: PASS", out)

    def test_verify_fail_after_cache_change(self):
        self._run()
        manifest = load_manifest(self._manifest_path())
        self._checkout_manifest(manifest)
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM binaries WHERE package_name = 'libinput-dev'")
        conn.commit()
        conn.close()
        rc, out, _ = self._run(verify=self._manifest_path())
        self.assertEqual(rc, EXIT_ERROR)
        self.assertIn("result: FAIL", out)
        self.assertIn("libinput", out)

    def test_verify_missing_repo_exits_102(self):
        self._run()
        partial_dir = os.path.join(self.tmp.name, "partial-repos")
        make_fixture_repos.build_fixture_repos(partial_dir)
        shutil.rmtree(os.path.join(partial_dir, "Hyprland"))
        rc, _, err = self._run(repo_dir=partial_dir, verify=self._manifest_path())
        self.assertEqual(rc, EXIT_CONFIG_ERROR)
        self.assertIn("Hyprland", err)

    def test_verify_head_mismatch_exits_102(self):
        self._run()
        manifest = load_manifest(self._manifest_path())
        self._checkout_manifest(manifest)
        subprocess.run(
            ["git", "-C", os.path.join(self.repo_dir, "Hyprland"),
             "commit", "--allow-empty", "-m", "drift"],
            check=True,
        )
        rc, _, err = self._run(verify=self._manifest_path())
        self.assertEqual(rc, EXIT_CONFIG_ERROR)
        self.assertIn("does not match", err)


if __name__ == "__main__":
    unittest.main()
