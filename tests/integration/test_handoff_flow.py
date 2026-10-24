import contextlib
import gzip
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_iso  # noqa: E402

from package_mgmt.cli.commands.add import handle_add  # noqa: E402
from package_mgmt.cli.commands.download import handle_download  # noqa: E402
from package_mgmt.cli.commands.handoff import handle_handoff  # noqa: E402
from package_mgmt.cli.commands.init import handle_init  # noqa: E402
from package_mgmt.cli.commands.systems import handle_systems_add  # noqa: E402
from package_mgmt.lib.service.config_loader import load_config  # noqa: E402
from package_mgmt.lib.util.errors import EXIT_SUCCESS  # noqa: E402


@unittest.skipUnless(shutil.which("apt-get"), "apt-get required")
class HandoffFlowTest(unittest.TestCase):
    """The operator's apt consumes the handoff offline — real
    apt-get against the generated file:// source with scratch apt dirs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_base = os.path.join(self.tmp.name, "cache")
        self.fixture_base = os.path.join(self.tmp.name, "fixture")
        make_test_iso.write_archive(self.cache_base)
        make_test_iso.write_pool_files(self.fixture_base)
        self.server = make_test_iso.serve_pool(self.fixture_base, port=0)
        port = self.server.server_address[1]
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(f'[paths]\nbase_dir = "{self.cache_base}"\n[fetch]\nsource = false\n\n[[archive.source]]\nmirror = "http://127.0.0.1:{port}"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n')
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.server.shutdown()
        self.server.thread.join()
        self.tmp.cleanup()

    def _provision(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_add(["vifm", "lib1"]), EXIT_SUCCESS)
        config = load_config()[0]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_download(config.db_path, config), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_systems_add(config.db_path, "prod-box"), EXIT_SUCCESS)
        return config

    def _run_handoff(self, config, system="prod-box"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_handoff(config.db_path, config, system)
        return rc, out.getvalue(), err.getvalue()

    def _apt_env(self, config, sources_content: str) -> dict:
        """Scratch apt directories; returns env overrides for apt-get."""
        apt = os.path.join(self.tmp.name, "apt")
        for sub in ("lists", "cache", "archives", "confparts"):
            os.makedirs(os.path.join(apt, sub), exist_ok=True)
        sources = os.path.join(apt, "sources.list")
        with open(sources, "w") as f:
            f.write(sources_content)
        # An empty dpkg status makes the test hermetic: the host's installed
        # packages (e.g. vifm on the dev box) must not satisfy installs.
        status = os.path.join(apt, "dpkg-status")
        open(status, "w").close()
        return {
            "Dir::Etc::sourcelist": sources,
            "Dir::Etc::sourceparts": "-",
            "Dir::Etc::apt.conf": "/dev/null",
            "Dir::Etc::parts": os.path.join(apt, "confparts"),
            "Dir::State::lists": os.path.join(apt, "lists"),
            "Dir::State::status": status,
            "Dir::Cache": os.path.join(apt, "cache"),
            "Dir::Cache::archives": os.path.join(apt, "archives"),
            "Debug::NoLocking": "1",
        }

    def _apt_get(self, config, env: dict, *args, cwd=None) -> subprocess.CompletedProcess:
        cmd = ["apt-get"]
        for key, value in env.items():
            cmd += ["-o", f"{key}={value}"]
        cmd += list(args)
        return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)

    def test_apt_consumes_handoff_end_to_end(self):
        config = self._provision()
        rc, out, err = self._run_handoff(config)
        self.assertEqual(rc, EXIT_SUCCESS, err)
        handoff = os.path.join(config.binary_cache, "handoff")
        env = self._apt_env(
            config,
            f"deb [signed-by={handoff}/offline-cache.gpg] file:{handoff} trixie main contrib\n",
        )
        update = self._apt_get(config, env, "update")
        self.assertEqual(update.returncode, 0, update.stderr + update.stdout)
        install = self._apt_get(config, env, "install", "--download-only", "-y", "vifm")
        self.assertEqual(install.returncode, 0, install.stderr + install.stdout)
        self.assertIn("vifm", install.stdout)
        self.assertIn("The following NEW packages will be installed", install.stdout)
        # For file:// sources apt uses the pool files in place (no staging in
        # archives); `apt-get download` copies the .deb out as the delivery proof.
        download_dir = os.path.join(self.tmp.name, "apt", "download")
        os.makedirs(download_dir, exist_ok=True)
        download = self._apt_get(config, env, "download", "vifm", cwd=download_dir)
        self.assertEqual(download.returncode, 0, download.stderr + download.stdout)
        self.assertTrue(any(
            name.startswith("vifm") and name.endswith(".deb")
            for name in os.listdir(download_dir)
        ))
        # The delivery record: every verified binary marked for prod-box.
        conn = sqlite3.connect(config.db_path)
        try:
            rows = conn.execute(
                "SELECT package_name, sync_mask FROM binaries ORDER BY package_name"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual([mask for _, mask in rows], [2, 2, 2])

    def test_rerun_is_idempotent(self):
        config = self._provision()
        rc, out, err = self._run_handoff(config)
        self.assertEqual(rc, EXIT_SUCCESS, err)
        self.assertIn("Snapshot:       3 binaries marked for 'prod-box'", out)
        first_masks = []
        conn = sqlite3.connect(config.db_path)
        try:
            first_masks = [r[0] for r in conn.execute(
                "SELECT sync_mask FROM binaries ORDER BY package_name"
            ).fetchall()]
        finally:
            conn.close()
        rc, out, _ = self._run_handoff(config)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("Snapshot:       3 binaries marked for 'prod-box'", out)
        conn = sqlite3.connect(config.db_path)
        try:
            second_masks = [r[0] for r in conn.execute(
                "SELECT sync_mask FROM binaries ORDER BY package_name"
            ).fetchall()]
        finally:
            conn.close()
        self.assertEqual(second_masks, first_masks)

    def test_multi_release_handoff_consumable_by_apt(self):
        """A two-release cache (trixie + trixie-backports) is served fully:
        apt consumes both suites, and both a trixie and a backports package
        install from the handoff with no network."""
        make_test_iso.write_backports_archive(self.cache_base)
        make_test_iso.write_backports_archive(self.fixture_base)
        port = self.server.server_address[1]
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(
                f'[paths]\nbase_dir = "{self.cache_base}"\n[fetch]\nsource = false\n\n'
                f'[[archive.source]]\nmirror = "http://127.0.0.1:{port}"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n\n'
                f'[[archive.source]]\nmirror = "http://127.0.0.1:{port}"\nrelease = "trixie-backports"\ncomponents = ["main"]\n'
            )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                handle_add(["vifm", "lib1", "backport-tool"]), EXIT_SUCCESS
            )
        config = load_config()[0]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_download(config.db_path, config), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_systems_add(config.db_path, "prod-box"), EXIT_SUCCESS)

        rc, out, err = self._run_handoff(config)
        self.assertEqual(rc, EXIT_SUCCESS, err)
        self.assertIn("Release:        trixie, trixie-backports", out)
        self.assertIn(
            "Packages:       4 (trixie/main: 3, trixie/contrib: 0, trixie-backports/main: 1)",
            out,
        )

        handoff = os.path.join(config.binary_cache, "handoff")
        env = self._apt_env(
            config,
            "deb [signed-by={h}/offline-cache.gpg] file:{h} trixie main contrib\ndeb [signed-by={h}/offline-cache.gpg] file:{h} trixie-backports main\n".format(h=handoff),
        )
        update = self._apt_get(config, env, "update")
        self.assertEqual(update.returncode, 0, update.stderr + update.stdout)
        download_dir = os.path.join(self.tmp.name, "apt", "download")
        os.makedirs(download_dir, exist_ok=True)
        for pkg in ("vifm", "backport-tool"):
            download = self._apt_get(config, env, "download", pkg, cwd=download_dir)
            self.assertEqual(download.returncode, 0, download.stderr + download.stdout)
            self.assertTrue(
                any(name.startswith(pkg) and name.endswith(".deb")
                    for name in os.listdir(download_dir)),
                f"{pkg} must be downloadable from the handoff",
            )
        # Every delivered binary — across both releases — is the delivery record.
        conn = sqlite3.connect(config.db_path)
        try:
            rows = conn.execute(
                "SELECT package_name, sync_mask FROM binaries ORDER BY package_name"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual([mask for _, mask in rows], [2, 2, 2, 2])

    def test_handoff_carries_pre_depends_and_installs_offline(self):
        """A package with a Pre-Depends-only requirement ships a
        faithful `Pre-Depends:` stanza, and the operator's apt resolves the
        Pre-Depends target from the cache (offline install simulation)."""
        make_test_iso.write_pre_depends_archive(self.cache_base)
        make_test_iso.write_pre_depends_archive(self.fixture_base)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
            self.assertEqual(handle_add(["pre-dep-app"]), EXIT_SUCCESS)
        config = load_config()[0]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_download(config.db_path, config), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_systems_add(config.db_path, "prod-box"), EXIT_SUCCESS)

        rc, out, err = self._run_handoff(config)
        self.assertEqual(rc, EXIT_SUCCESS, err)

        handoff = os.path.join(config.binary_cache, "handoff")
        gz_path = os.path.join(
            handoff, "dists", "trixie", "main", "binary-amd64", "Packages.gz"
        )
        with gzip.open(gz_path, "rt", encoding="utf-8") as f:
            packages_text = f.read()
        self.assertIn("Package: pre-dep-app", packages_text)
        self.assertIn("Pre-Depends: pre-dep-lib", packages_text)

        env = self._apt_env(
            config,
            f"deb [signed-by={handoff}/offline-cache.gpg] file:{handoff} trixie main\n",
        )
        update = self._apt_get(config, env, "update")
        self.assertEqual(update.returncode, 0, update.stderr + update.stdout)
        install = self._apt_get(config, env, "install", "--simulate", "pre-dep-app")
        self.assertEqual(install.returncode, 0, install.stderr + install.stdout)
        self.assertIn("pre-dep-lib", install.stdout,
                      "the Pre-Depends-only target must resolve from the cache")

    def test_handoff_excludes_out_of_config_record_and_does_not_deliver(self):
        """With the config narrowed to main-only after verification, a verified
        contrib record is omitted from the handoff, reported loudly in the
        summary Warning line, and its delivery id is not advanced."""
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
            self.assertEqual(
                handle_add(["vifm", "lib1", "firmware-x"]), EXIT_SUCCESS
            )
        config = load_config()[0]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_download(config.db_path, config), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_systems_add(config.db_path, "prod-box"), EXIT_SUCCESS)

        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            port = self.server.server_address[1]
            f.write(
                f'[paths]\nbase_dir = "{self.cache_base}"\n[fetch]\nsource = false\n\n'
                f'[[archive.source]]\nmirror = "http://127.0.0.1:{port}"\nrelease = "trixie"\ncomponents = ["main"]\n'
            )
        narrowed = load_config()[0]

        rc, out, err = self._run_handoff(narrowed)
        self.assertEqual(rc, EXIT_SUCCESS, err)
        self.assertIn("1 verified records excluded", out)
        handoff = os.path.join(narrowed.binary_cache, "handoff")
        gz_path = os.path.join(
            handoff, "dists", "trixie", "main", "binary-amd64", "Packages.gz"
        )
        with gzip.open(gz_path, "rt", encoding="utf-8") as f:
            packages_text = f.read()
        self.assertNotIn("Package: firmware-x", packages_text)
        conn = sqlite3.connect(narrowed.db_path)
        try:
            row = conn.execute(
                "SELECT sync_mask FROM binaries WHERE package_name = 'firmware-x'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], 0, "the out-of-config record must not be marked delivered")


if __name__ == "__main__":
    unittest.main()
