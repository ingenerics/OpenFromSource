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
import make_test_deb  # noqa: E402
import make_test_iso  # noqa: E402

from package_mgmt.cli.commands.add import handle_add  # noqa: E402
from package_mgmt.cli.commands.download import handle_download  # noqa: E402
from package_mgmt.cli.commands.handoff import handle_handoff  # noqa: E402
from package_mgmt.cli.commands.import_deb import handle_import_deb  # noqa: E402
from package_mgmt.cli.commands.init import handle_init  # noqa: E402
from package_mgmt.cli.commands.systems import handle_systems_add  # noqa: E402
from package_mgmt.lib.service.config_loader import load_config  # noqa: E402
from package_mgmt.lib.util.errors import EXIT_SUCCESS  # noqa: E402
from package_mgmt.lib.util.paths import handoff_script_path  # noqa: E402


@unittest.skipUnless(shutil.which("apt-get"), "apt-get required")
class ImportDebFlowTest(unittest.TestCase):
    """A locally imported `.deb` flows through the same channel as a verified
    download: the handoff serves it under the `local` component, and the
    operator's apt installs it offline from the handoff — with a newer local
    revision offered as an upgrade."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_base = os.path.join(self.tmp.name, "cache")
        self.fixture_base = os.path.join(self.tmp.name, "fixture")
        make_test_iso.write_archive(self.cache_base)
        make_test_iso.write_pool_files(self.fixture_base)
        self.server = make_test_iso.serve_pool(self.fixture_base, port=0)
        port = self.server.server_address[1]
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(
                f'[paths]\nbase_dir = "{self.cache_base}"\n[fetch]\nsource = false\n\n'
                f'[[archive.source]]\nmirror = "http://127.0.0.1:{port}"\n'
                f'release = "trixie"\ncomponents = ["main", "contrib"]\n'
            )
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
            self.assertEqual(handle_add(["lib1"]), EXIT_SUCCESS)
        config = load_config()[0]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_download(config.db_path, config), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_systems_add(config.db_path, "prod-box"), EXIT_SUCCESS)
        return config

    def _build_local_deb(self, version: str) -> str:
        deb_dir = os.path.join(self.tmp.name, "local")
        os.makedirs(deb_dir, exist_ok=True)
        path = os.path.join(deb_dir, f"patch-tool_{version}_amd64.deb")
        return make_test_deb.build_deb(
            path, package="patch-tool", version=version, depends="lib1"
        )

    def _import(self, config, deb: str):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_import_deb(deb, source=None), EXIT_SUCCESS)

    def _run_handoff(self, config):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_handoff(config.db_path, config, "prod-box")
        return rc, out.getvalue(), err.getvalue()

    def _local_packages(self, handoff: str) -> str:
        path = os.path.join(
            handoff, "dists", "trixie", "local", "binary-amd64", "Packages.gz"
        )
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return f.read()

    def _apt_env(self, config, sources_content: str, dpkg_status: str = "") -> dict:
        """Scratch apt directories; returns env overrides for apt-get. An empty
        dpkg status (the default) makes the test hermetic: the host's installed
        packages must not satisfy installs."""
        apt = os.path.join(self.tmp.name, "apt")
        for sub in ("lists", "cache", "archives", "confparts"):
            os.makedirs(os.path.join(apt, sub), exist_ok=True)
        with open(os.path.join(apt, "sources.list"), "w") as f:
            f.write(sources_content)
        status = os.path.join(apt, "dpkg-status")
        with open(status, "w") as f:
            f.write(dpkg_status)
        return {
            "Dir::Etc::sourcelist": os.path.join(apt, "sources.list"),
            "Dir::Etc::sourceparts": "-",
            "Dir::Etc::apt.conf": "/dev/null",
            "Dir::Etc::parts": os.path.join(apt, "confparts"),
            "Dir::State::lists": os.path.join(apt, "lists"),
            "Dir::State::status": status,
            "Dir::Cache": os.path.join(apt, "cache"),
            "Dir::Cache::archives": os.path.join(apt, "archives"),
            "Debug::NoLocking": "1",
        }

    def _apt_get(self, config, env: dict, *args) -> subprocess.CompletedProcess:
        cmd = ["apt-get"]
        for key, value in env.items():
            cmd += ["-o", f"{key}={value}"]
        cmd += list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_download_after_local_import_is_clean(self):
        """Importing a local `.deb` must not make the next `download` (and
        every subsequent one) report the record as a missing-`local`-index
        error: `local` is verified at import from the supplied payload, so
        `download` exits clean with zero command errors and the local row
        stays verified."""
        config = self._provision()
        deb = self._build_local_deb("1.2-3")
        self._import(config, deb)

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_download(config.db_path, config)
        self.assertEqual(rc, EXIT_SUCCESS, err.getvalue() + out.getvalue())
        self.assertNotIn("Cached index missing for trixie/local", out.getvalue())
        self.assertNotIn("Cached index missing for trixie/local", err.getvalue())

        conn = sqlite3.connect(config.db_path)
        try:
            row = conn.execute(
                "SELECT component, verified FROM binaries WHERE package_name = 'patch-tool'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, ("local", 1))

        # A second `download` (nothing to fetch) stays clean too.
        out2, err2 = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out2), contextlib.redirect_stderr(err2):
            rc2 = handle_download(config.db_path, config)
        self.assertEqual(rc2, EXIT_SUCCESS, err2.getvalue() + out2.getvalue())
        self.assertNotIn("Cached index missing for trixie/local", out2.getvalue())
        self.assertNotIn("Cached index missing for trixie/local", err2.getvalue())

    def test_local_patch_served_installed_offline_and_upgradable(self):
        config = self._provision()
        deb = self._build_local_deb("1.2-3")
        self._import(config, deb)

        rc, out, err = self._run_handoff(config)
        self.assertEqual(rc, EXIT_SUCCESS, err)

        # The operator's source line and the local index carry the patch.
        with open(handoff_script_path(config.binary_cache)) as f:
            script = f.read()
        self.assertIn("trixie main contrib local", script)
        handoff = os.path.join(config.binary_cache, "handoff")
        packages = self._local_packages(handoff)
        self.assertIn("Package: patch-tool", packages)
        self.assertIn("Version: 1.2-3", packages)
        self.assertIn("Depends: lib1", packages)

        # Transfer the handoff to a scratch operator cache and install offline.
        operator = os.path.join(self.tmp.name, "operator")
        shutil.copytree(handoff, operator, symlinks=True)
        env = self._apt_env(
            config,
            f"deb [signed-by={operator}/offline-cache.gpg] file:{operator} trixie main contrib local\n",
        )
        update = self._apt_get(config, env, "update")
        self.assertEqual(update.returncode, 0, update.stderr + update.stdout)
        install = self._apt_get(config, env, "install", "--simulate", "-y", "patch-tool")
        self.assertEqual(install.returncode, 0, install.stderr + install.stdout)
        self.assertIn("patch-tool", install.stdout)
        self.assertIn(
            "The following NEW packages will be installed", install.stdout
        )
        # The dependency resolves from the cache's main component, not the host.
        self.assertIn("lib1", install.stdout)

        # A newer local revision is offered as an upgrade.
        deb2 = self._build_local_deb("1.3-0")
        self._import(config, deb2)
        rc, out, err = self._run_handoff(config)
        self.assertEqual(rc, EXIT_SUCCESS, err)
        packages2 = self._local_packages(handoff)
        self.assertIn("Version: 1.3-0", packages2)

        shutil.rmtree(operator)
        shutil.copytree(handoff, operator, symlinks=True)
        # The operator already has 1.2-3 (and its dependency lib1) installed
        # (minimal dpkg status), so the newer local revision is presented as
        # the upgrade.
        status = (
            "Package: patch-tool\nStatus: install ok installed\n"
            "Architecture: amd64\nVersion: 1.2-3\n\n"
            "Package: lib1\nStatus: install ok installed\n"
            "Architecture: amd64\nVersion: 2.0-1\n\n"
        )
        env2 = self._apt_env(
            config,
            f"deb [signed-by={operator}/offline-cache.gpg] file:{operator} trixie main contrib local\n",
            dpkg_status=status,
        )
        update2 = self._apt_get(config, env2, "update")
        self.assertEqual(update2.returncode, 0, update2.stderr + update2.stdout)
        upgrade = self._apt_get(config, env2, "upgrade", "--simulate", "-y")
        self.assertEqual(upgrade.returncode, 0, upgrade.stderr + upgrade.stdout)
        self.assertIn("patch-tool", upgrade.stdout)
        self.assertIn("1.3-0", upgrade.stdout)
        self.assertIn("The following packages will be upgraded", upgrade.stdout)


if __name__ == "__main__":
    unittest.main()
