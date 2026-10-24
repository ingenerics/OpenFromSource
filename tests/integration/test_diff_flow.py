import contextlib
import gzip
import io
import os
import shlex
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
from package_mgmt.cli.commands.sync import handle_sync  # noqa: E402
from package_mgmt.cli.commands.systems import handle_systems_add  # noqa: E402
from package_mgmt.lib.service.config_loader import load_config  # noqa: E402
from package_mgmt.lib.util.errors import EXIT_SUCCESS  # noqa: E402


class DiffFlowAdminTest(unittest.TestCase):
    """The full admin cycle — baseline handoff + snapshot, grow
    the cache, sync produces the minimal diff (exactly the new files, complete
    per-release indexes, correct manifest/base set), and a re-run ships nothing
    new (the snapshot already advanced)."""

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
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_handoff(config.db_path, config, "prod-box"), EXIT_SUCCESS)
        return config

    def _grow_cache(self, config):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_add(["app", "base-pkg", "firmware-x"]), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_download(config.db_path, config), EXIT_SUCCESS)

    def _run_sync(self, config, system="prod-box"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_sync(config.db_path, config, system)
        return rc, out.getvalue(), err.getvalue()

    def _read_packages(self, config, component="main"):
        path = os.path.join(
            config.binary_cache, "diff", "dists", "trixie", component,
            "binary-amd64", "Packages.gz",
        )
        with open(path, "rb") as f:
            return gzip.decompress(f.read()).decode("utf-8")

    def _pool_files(self, config):
        pool_root = os.path.join(config.binary_cache, "diff", "pool")
        files = []
        for root, _, names in os.walk(pool_root):
            for name in names:
                files.append(os.path.relpath(os.path.join(root, name), pool_root))
        return sorted(files)

    def test_sync_ships_exactly_the_new_packages(self):
        config = self._provision()
        self._grow_cache(config)

        rc, out, err = self._run_sync(config)
        self.assertEqual(rc, EXIT_SUCCESS, err)
        self.assertIn("New packages:    3 (main: 2, contrib: 1)", out)
        # vifm/lib1/lib2 form the baseline handoff (add pulls lib2 in as a
        # dependency of vifm), so prior = 3.
        self.assertIn("Total offered:   6 (prior 3 + new 3)", out)

        self.assertEqual(self._pool_files(config), [
            "contrib/f/firmware-x/firmware-x_1.0-1_amd64.deb",
            "main/a/app/app_1.5-1_amd64.deb",
            "main/b/base-pkg/base-pkg_1.0-1_amd64.deb",
        ])

        main_text = self._read_packages(config)
        for name in ("vifm", "lib1", "lib2", "app", "base-pkg"):
            self.assertIn(f"Package: {name}", main_text)
        contrib_text = self._read_packages(config, component="contrib")
        self.assertIn("Package: firmware-x", contrib_text)

        with open(os.path.join(config.binary_cache, "diff", "manifest.txt")) as f:
            lines = [ln.split() for ln in f.read().splitlines()]
        delivered = {p for kind, _, _, p in lines if kind == "delivered"}
        new = {p for kind, _, _, p in lines if kind == "new"}
        self.assertEqual(delivered, {
            "pool/main/v/vifm/vifm_1.0-2_amd64.deb",
            "pool/main/l/lib1/lib1_2.0-1_amd64.deb",
            "pool/main/l/lib2/lib2_3.0-1_amd64.deb",
        })
        self.assertEqual(new, {
            "pool/main/a/app/app_1.5-1_amd64.deb",
            "pool/main/b/base-pkg/base-pkg_1.0-1_amd64.deb",
            "pool/contrib/f/firmware-x/firmware-x_1.0-1_amd64.deb",
        })

        conn = sqlite3.connect(config.db_path)
        try:
            rows = dict(conn.execute(
                "SELECT package_name, sync_mask FROM binaries"
                " WHERE status='current' AND verified=1 ORDER BY package_name").fetchall())
        finally:
            conn.close()
        for name in ("vifm", "lib1", "lib2", "app", "base-pkg", "firmware-x"):
            self.assertEqual(rows[name] & 2, 2, f"{name} must be marked delivered")

    def test_rerun_reports_zero_and_ships_nothing(self):
        config = self._provision()
        self._grow_cache(config)
        rc, _, _ = self._run_sync(config)
        self.assertEqual(rc, EXIT_SUCCESS)
        after_first_sync = self._pool_files(config)
        self.assertNotEqual(after_first_sync, [])

        rc, out, err = self._run_sync(config)
        self.assertEqual(rc, EXIT_SUCCESS, err)
        self.assertIn(
            "New packages:    0 — nothing new since the last sync for 'prod-box'", out
        )
        # The zero-pending re-run regenerates the diff as empty: stale new
        # files from the first sync are removed and the manifest keeps only
        # the delivered base set.
        self.assertEqual(self._pool_files(config), [])
        with open(os.path.join(config.binary_cache, "diff", "manifest.txt")) as f:
            rerun_lines = [line for line in f.read().splitlines() if line.strip()]
        self.assertTrue(rerun_lines)
        self.assertTrue(all(line.startswith("delivered ") for line in rerun_lines))


@unittest.skipUnless(shutil.which("apt-get"), "apt-get required")
class DiffFlowOperatorTest(unittest.TestCase):
    """The operator merges the diff into an existing bootstrapped
    cache and apt consumes prior + new offline.

    The operator cache is built from the baseline state (handoff + pool copied
    before the admin cache grew), then the diff is applied via the shipped
    `apply_configure` (sourced with a scratch state root — the real flow, no
    root, no host modification). Real apt-get against scratch apt dirs then
    confirms the new package is offered/downloadable offline and every
    prior package remains offered.
    """

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

    def _run(self, fn, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = fn(*args)
        return rc, out.getvalue(), err.getvalue()

    def _provision_baseline(self):
        """Fixture cache at the baseline: init + add/download + systems add +
        handoff (spec 012 full handoff + snapshot) — the state the operator
        already bootstrapped from."""
        self.assertEqual(self._run(handle_init)[0], EXIT_SUCCESS)
        self.assertEqual(self._run(handle_add, ["vifm", "lib1"])[0], EXIT_SUCCESS)
        config = load_config()[0]
        self.assertEqual(self._run(handle_download, config.db_path, config)[0], EXIT_SUCCESS)
        self.assertEqual(self._run(handle_systems_add, config.db_path, "prod-box")[0], EXIT_SUCCESS)
        rc, out, err = self._run(handle_handoff, config.db_path, config, "prod-box")
        self.assertEqual(rc, EXIT_SUCCESS, err)
        return config

    def _grow_and_sync(self, config):
        self.assertEqual(self._run(handle_add, ["app", "base-pkg", "firmware-x"])[0], EXIT_SUCCESS)
        self.assertEqual(self._run(handle_download, config.db_path, config)[0], EXIT_SUCCESS)
        rc, out, err = self._run(handle_sync, config.db_path, config, "prod-box")
        self.assertEqual(rc, EXIT_SUCCESS, err)
        self.assertIn("New packages:    3 (main: 2, contrib: 1)", out)
        return out

    def _operator_baseline(self, config) -> str:
        """Snapshot the operator's cache at the baseline state (handoff + pool
        as bootstrapped from spec 012) BEFORE the admin cache grows — so the
        operator's pool has only the delivered files and the diff is what
        brings the new ones."""
        op = os.path.join(self.tmp.name, "operator")
        shutil.copytree(os.path.join(config.binary_cache, "pool"), os.path.join(op, "pool"))
        shutil.copytree(
            os.path.join(config.binary_cache, "handoff"),
            os.path.join(op, "handoff"),
            symlinks=True,
        )
        return op

    def _ship_diff(self, config, op: str) -> None:
        """Copy the diff into the operator's cache (or beside it)."""
        shutil.copytree(os.path.join(config.binary_cache, "diff"), os.path.join(op, "diff"))

    def _apply(self, op: str) -> subprocess.CompletedProcess:
        """Run the shipped apply flow against the scratch operator cache —
        `apply_configure` (sourced, explicit paths) with a scratch apt state
        root, exactly the contracts/cli.md testability convention."""
        script = os.path.join(op, "diff", "apply.sh")
        sources_list = os.path.join(self.tmp.name, "operator-sources.list")
        with open(sources_list, "w") as f:
            f.write(f"deb [signed-by={op}/handoff/offline-cache.gpg] file:{op}/handoff trixie main contrib\n")
        state_root = os.path.join(self.tmp.name, "state")
        code = (
            f"source {shlex.quote(script)};"
            f" apply_configure {shlex.quote(op)} {shlex.quote(sources_list)}"
            f" /nonexistent 'trixie main contrib' trixie {shlex.quote(state_root)}"
        )
        return subprocess.run(["bash", "-c", code], capture_output=True, text=True)

    def _apt_env(self, op: str) -> dict:
        apt = os.path.join(self.tmp.name, "apt")
        for sub in ("lists", "cache", "archives", "confparts"):
            os.makedirs(os.path.join(apt, sub), exist_ok=True)
        sources = os.path.join(apt, "sources.list")
        with open(sources, "w") as f:
            f.write(f"deb [signed-by={op}/handoff/offline-cache.gpg] file:{op}/handoff trixie main contrib\n")
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

    def _apt_get(self, env: dict, *args, cwd=None) -> subprocess.CompletedProcess:
        cmd = ["apt-get"]
        for key, value in env.items():
            cmd += ["-o", f"{key}={value}"]
        cmd += list(args)
        return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)

    def _read_packages(self, op: str, component: str) -> str:
        path = os.path.join(
            op, "handoff", "dists", "trixie", component, "binary-amd64", "Packages.gz"
        )
        with open(path, "rb") as f:
            return gzip.decompress(f.read()).decode("utf-8")

    def test_operator_applies_diff_and_apt_consumes_prior_plus_new(self):
        config = self._provision_baseline()
        op = self._operator_baseline(config)
        self._grow_and_sync(config)
        self._ship_diff(config, op)

        result = self._apply(op)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(
            result.stdout.strip().splitlines()[-1], "3",
            "apply_configure reports 3 new files copied",
        )

        # The operator's pool gained exactly the diff's new files.
        for rel in (
            "main/a/app/app_1.5-1_amd64.deb",
            "main/b/base-pkg/base-pkg_1.0-1_amd64.deb",
            "contrib/f/firmware-x/firmware-x_1.0-1_amd64.deb",
        ):
            self.assertTrue(
                os.path.isfile(os.path.join(op, "pool", rel)), f"new file must enter the pool: {rel}"
            )

        # The handoff dists now present the COMPLETE set (prior + new).
        main_text = self._read_packages(op, "main")
        for name in ("vifm", "lib1", "lib2", "app", "base-pkg"):
            self.assertIn(f"Package: {name}", main_text)
        self.assertIn("Package: firmware-x", self._read_packages(op, "contrib"))

        # Real apt consumption, offline: the new package is offered and
        # downloadable from the cache; every prior package remains offered.
        env = self._apt_env(op)
        update = self._apt_get(env, "update")
        self.assertEqual(update.returncode, 0, update.stderr + update.stdout)

        download_dir = os.path.join(self.tmp.name, "apt", "download")
        os.makedirs(download_dir, exist_ok=True)
        for pkg in ("app", "firmware-x"):
            download = self._apt_get(env, "download", pkg, cwd=download_dir)
            self.assertEqual(download.returncode, 0, download.stderr + download.stdout)
            self.assertTrue(
                any(name.startswith(pkg) and name.endswith(".deb")
                    for name in os.listdir(download_dir)),
                f"{pkg} must be downloadable from the merged cache offline",
            )
        for pkg in ("vifm", "lib1"):
            download = self._apt_get(env, "download", pkg, cwd=download_dir)
            self.assertEqual(
                download.returncode, 0,
                msg="every prior package must remain offered: "
                    + download.stderr + download.stdout,
            )

        # The new package INSTALLS from the cache offline (quickstart scenario
        # 2): apt resolves and stages the .deb with no network.
        install = self._apt_get(env, "install", "--download-only", "-y", "app")
        self.assertEqual(install.returncode, 0, install.stderr + install.stdout)
        self.assertIn("The following NEW packages will be installed", install.stdout)
        self.assertIn("app", install.stdout)

        # Exact set (quickstart scenario 3): a package NOT in the
        # merged cache is refused by apt's normal "unable to locate" — no
        # phantom offers.
        missing = self._apt_get(env, "download", "no-such-package", cwd=download_dir)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("Unable to locate package", missing.stderr)

    def test_apply_prints_clean_count_and_already_applied_rerun(self):
        """The generated apply.sh's merge prints a clean count: stdout is
        exactly `New files: <number>`, stderr is empty, apt output goes to the
        transient log, and a re-run reaches the already-applied branch cleanly."""
        config = self._provision_baseline()
        op = self._operator_baseline(config)
        self._grow_and_sync(config)
        self._ship_diff(config, op)

        first = self._apply(op)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.assertEqual(
            first.stdout.strip().splitlines(), ["3"],
            "stdout must be exactly the copied-file count",
        )
        self.assertEqual(first.stderr.strip(), "", "stderr must be empty on success")
        self.assertNotIn("integer expression expected", first.stdout + first.stderr)
        self.assertTrue(
            os.path.isfile(os.path.join(op, "diff", ".apply-apt.log")),
            "apt output must go to the transient log file",
        )

        second = self._apply(op)
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(
            second.stdout.strip().splitlines(), ["0"],
            "a re-apply must report 0 files copied (already-applied branch)",
        )
        self.assertEqual(second.stderr.strip(), "", "the already-applied branch must be clean")
        self.assertNotIn("integer expression expected", second.stdout + second.stderr)


if __name__ == "__main__":
    unittest.main()
