"""Integration test for the single-component packaging flow.

A fixture recipe over a staged install dir + pinned source tree flows through
the real pipeline: `debgen` builds the `.deb`, `srcgen` the rebuildable `.dsc`
set, `import_local_deb` registers + verifies the pair into `local` with the
`binary_to_source` link, and the cache shows the pair and installs it offline
via the handoff channel. The un-attributable-library enforcement is exercised
with a real ELF binary: an undeclared companion library fails the component
loudly with zero DB changes.
"""

import contextlib
import gzip
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_iso  # noqa: E402

from package_mgmt.cli.commands.handoff import handle_handoff  # noqa: E402
from package_mgmt.cli.commands.init import handle_init  # noqa: E402
from package_mgmt.cli.commands.list import handle_list_deps  # noqa: E402
from package_mgmt.cli.commands.package_stack import handle_package_stack  # noqa: E402
from package_mgmt.cli.commands.show import handle_show  # noqa: E402
from package_mgmt.cli.commands.systems import handle_systems_add  # noqa: E402
from package_mgmt.cli.commands.verify import handle_verify_graph  # noqa: E402
from package_mgmt.lib.service.config_loader import load_config  # noqa: E402
from package_mgmt.lib.util.errors import (  # noqa: E402
    EXIT_SUCCESS,
    EXIT_ERROR,
)
from package_mgmt.lib.util.paths import handoff_script_path  # noqa: E402


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=True)


class _PackageStackBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.cache_base)
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(
                f'[paths]\nbase_dir = "{self.cache_base}"\n[fetch]\nsource = true\n\n'
                "[[archive.source]]\nmirror = \"http://127.0.0.1:1\"\n"
                'release = "trixie"\ncomponents = ["main", "contrib"]\n'
            )
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _provision(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        return load_config()[0]

    def _write_recipe(self, components: str, name: str = "demo") -> str:
        os.makedirs(os.path.join(self.tmp.name, "recipes"), exist_ok=True)
        path = os.path.join(self.tmp.name, "recipes", f"{name}.toml")
        with open(path, "w") as f:
            f.write(
                f'[stack]\nname = "{name}"\nrepo_dir = "repos"\n'
                'source_epoch = "2026-10-20"\n\n' + components
            )
        return path

    def _run_package_stack(self, recipe: str, fmt: str = "text"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_package_stack(recipe, fmt)
        return rc, out.getvalue(), err.getvalue()

    def _apt_env(self, sources_content: str) -> dict:
        apt = os.path.join(self.tmp.name, "apt")
        for sub in ("lists", "cache", "archives", "confparts"):
            os.makedirs(os.path.join(apt, sub), exist_ok=True)
        with open(os.path.join(apt, "sources.list"), "w") as f:
            f.write(sources_content)
        status = os.path.join(apt, "dpkg-status")
        with open(status, "w") as f:
            f.write("")
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

    def _apt_get(self, env: dict, *args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
        cmd = ["apt-get"]
        for key, value in env.items():
            cmd += ["-o", f"{key}={value}"]
        cmd += list(args)
        return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)


class TestPackageStackFlow(_PackageStackBase):
    """A single data-only component imports, links, verifies, and installs
    offline — no stack-specific code path."""

    def _provision_fixture(self) -> str:
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
        component = (
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
        return self._write_recipe(component)

    def test_single_component_imports_links_verifies(self):
        config = self._provision()
        recipe = self._provision_fixture()
        rc, out, err = self._run_package_stack(recipe)
        self.assertEqual(rc, EXIT_SUCCESS, err)
        self.assertIn("components imported: 1/1", out)

        import sqlite3

        conn = sqlite3.connect(config.db_path)
        try:
            binary = conn.execute(
                "SELECT package_name, version, component, verified, status"
                " FROM binaries WHERE package_name = 'demo-tool'"
            ).fetchone()
            source = conn.execute(
                "SELECT package_name, source_version, component, verified"
                " FROM sources WHERE package_name = 'demo-tool'"
            ).fetchone()
            link = conn.execute(
                "SELECT COUNT(*) FROM binary_to_source"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(binary, ("demo-tool", "1.0.0-1", "local", 1, "current"))
        self.assertEqual(source, ("demo-tool", "1.0.0-1", "local", 1))
        self.assertEqual(link, 1)

        # pool payloads exist (binary .deb + the source set)
        self.assertTrue(
            os.path.isfile(
                os.path.join(config.binary_cache, "pool", "local", "d", "demo-tool", "demo-tool_1.0.0-1_all.deb")
            )
        )
        source_pool = os.path.join(config.source_cache, "pool", "local", "d", "demo-tool")
        self.assertEqual(
            sorted(os.listdir(source_pool)),
            [
                "demo-tool_1.0.0-1.debian.tar.xz",
                "demo-tool_1.0.0-1.dsc",
                "demo-tool_1.0.0.orig.tar.gz",
            ],
        )

    def test_list_show_verify_display_the_pair(self):
        config = self._provision()
        recipe = self._provision_fixture()
        rc, _, err = self._run_package_stack(recipe)
        self.assertEqual(rc, EXIT_SUCCESS, err)

        with contextlib.redirect_stdout(io.StringIO()) as list_out:
            rc = handle_list_deps(config.db_path, "demo-tool", fmt="text")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("demo-tool (current)", list_out.getvalue())

        with contextlib.redirect_stdout(io.StringIO()) as show_out:
            rc = handle_show(config.db_path, "demo-tool", fmt="text")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("local", show_out.getvalue())
        self.assertIn("current", show_out.getvalue())

        with contextlib.redirect_stdout(io.StringIO()) as show_json:
            rc = handle_show(config.db_path, "demo-tool", fmt="json")
        self.assertEqual(rc, EXIT_SUCCESS)
        import json

        data = json.loads(show_json.getvalue())
        binary = next(
            row for row in data["binaries"]
            if row["package_name"] == "demo-tool"
        )
        self.assertEqual(binary["component"], "local")
        self.assertEqual(binary["status"], "current")
        self.assertEqual(binary["verified"], 1)

        with contextlib.redirect_stdout(io.StringIO()) as verify_out:
            rc = handle_verify_graph(config.db_path)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("Status:                 COMPLETE", verify_out.getvalue())
        self.assertIn("Runtime Status:         COMPLETE", verify_out.getvalue())

    def test_installable_offline_via_handoff(self):
        config = self._provision()
        recipe = self._provision_fixture()
        rc, _, err = self._run_package_stack(recipe)
        self.assertEqual(rc, EXIT_SUCCESS, err)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_systems_add(config.db_path, "prod-box"), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_handoff(config.db_path, config, "prod-box")
        self.assertEqual(rc, EXIT_SUCCESS)

        with open(handoff_script_path(config.binary_cache)) as f:
            script = f.read()
        self.assertIn("trixie main contrib local", script)

        operator = os.path.join(self.tmp.name, "operator")
        handoff = os.path.join(config.binary_cache, "handoff")
        shutil.copytree(handoff, operator, symlinks=True)
        with gzip.open(
            os.path.join(operator, "dists", "trixie", "local", "binary-amd64", "Packages.gz"),
            "rt", encoding="utf-8",
        ) as f:
            packages = f.read()
        self.assertIn("Package: demo-tool", packages)
        self.assertIn("Version: 1.0.0-1", packages)

        env = self._apt_env(
            f"deb [signed-by={operator}/offline-cache.gpg] file:{operator} trixie main contrib local\n"
        )
        update = self._apt_get(env, "update")
        self.assertEqual(update.returncode, 0, update.stderr + update.stdout)
        install = self._apt_get(env, "install", "--simulate", "-y", "demo-tool")
        self.assertEqual(install.returncode, 0, install.stderr + install.stdout)
        self.assertIn("demo-tool", install.stdout)
        self.assertIn("The following NEW packages will be installed", install.stdout)


class TestOperatorSourceChannel(_PackageStackBase):
    """The operator retrieves an imported component's source offline via
    `apt-get source` from the handoff's `local/source/Sources` (deb-src) index
    — the `.dsc` + referenced tarballs resolve through the handoff's `source`
    link, and `dpkg-source -x` unpacks them with no network (Q3/FR-008)."""

    def _provision_fixture(self) -> str:
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
        component = (
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
        return self._write_recipe(component)

    def test_apt_get_source_resolves_offline_and_unpacks(self):
        config = self._provision()
        recipe = self._provision_fixture()
        rc, _, err = self._run_package_stack(recipe)
        self.assertEqual(rc, EXIT_SUCCESS, err)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_systems_add(config.db_path, "prod-box"), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = handle_handoff(config.db_path, config, "prod-box")
        self.assertEqual(rc, EXIT_SUCCESS)

        handoff = os.path.join(config.binary_cache, "handoff")
        # the source channel index exists and is reachable through the link
        self.assertTrue(
            os.path.isfile(
                os.path.join(handoff, "dists", "trixie", "local", "source", "Sources.gz")
            )
        )
        link = os.path.join(handoff, "source")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.path.realpath(link), config.source_cache)

        env = self._apt_env(
            f"deb [signed-by={handoff}/offline-cache.gpg] file:{handoff} trixie main contrib local\n"
            f"deb-src [signed-by={handoff}/offline-cache.gpg] file:{handoff} trixie local\n"
        )
        update = self._apt_get(env, "update")
        self.assertEqual(update.returncode, 0, update.stderr + update.stdout)

        src_dir = os.path.join(self.tmp.name, "src")
        os.makedirs(src_dir)
        source = self._apt_get(env, "source", "--download-only", "demo-tool", cwd=src_dir)
        self.assertEqual(source.returncode, 0, source.stderr + source.stdout)
        self.assertTrue(os.path.isfile(os.path.join(src_dir, "demo-tool_1.0.0-1.dsc")))
        self.assertTrue(
            os.path.isfile(os.path.join(src_dir, "demo-tool_1.0.0.orig.tar.gz"))
        )
        self.assertTrue(
            os.path.isfile(os.path.join(src_dir, "demo-tool_1.0.0-1.debian.tar.xz"))
        )
        unpack = subprocess.run(
            ["dpkg-source", "-x", os.path.join(src_dir, "demo-tool_1.0.0-1.dsc")],
            cwd=src_dir, capture_output=True, text=True,
        )
        self.assertEqual(unpack.returncode, 0, unpack.stderr)
        extracted = os.path.join(src_dir, "demo-tool-1.0.0")
        self.assertTrue(os.path.isfile(os.path.join(extracted, "meson.build")))
        self.assertTrue(os.path.isdir(os.path.join(extracted, "debian")))


class TestTwoComponentStackFlow(_PackageStackBase):
    """A two-component set in arbitrary recipe order imports dependencies
    first, links both, and passes the whole-set `verify` + simulated offline
    `apt install`."""

    def _provision_fixture(self) -> str:
        for name, version in (("lib-companion", "1.0.0"), ("demo-app", "2.0.0")):
            data_dir = os.path.join(self.tmp.name, "staging", name, f"usr/share/{name}")
            os.makedirs(data_dir, exist_ok=True)
            with open(os.path.join(data_dir, "data.txt"), "w") as f:
                f.write(f"{name}\n")
            tree = os.path.join(self.tmp.name, "repos", name)
            os.makedirs(tree, exist_ok=True)
            with open(os.path.join(tree, "meson.build"), "w") as f:
                f.write(f'project("{name}", version: "{version}")\n')
        # dependent first, dependency second — order-independence is required
        components = (
            "[[component]]\n"
            'name = "demo-app"\n'
            'binary_package = "demo-app"\n'
            'source_tree = "demo-app"\n'
            'version = "2.0.0"\n'
            'build_system = "meson"\n'
            'architecture = "all"\n'
            'description = "demo app"\n'
            'maintainer = "T <t@example.invalid>"\n'
            'explicit_depends = ["lib-companion (>= 1.0)"]\n'
            "\n"
            "[[component]]\n"
            'name = "lib-companion"\n'
            'binary_package = "lib-companion"\n'
            'source_tree = "lib-companion"\n'
            'version = "1.0.0"\n'
            'build_system = "meson"\n'
            'architecture = "all"\n'
            'description = "companion library"\n'
            'maintainer = "T <t@example.invalid>"\n'
        )
        return self._write_recipe(components)

    def test_orders_dependencies_first_and_validates_whole_set(self):
        config = self._provision()
        recipe = self._provision_fixture()
        rc, out, err = self._run_package_stack(recipe)
        self.assertEqual(rc, EXIT_SUCCESS, err)
        self.assertIn("order: lib-companion → demo-app", out)
        self.assertIn("components imported: 2/2", out)
        self.assertIn("whole-set verify: PASS", out)
        self.assertIn("simulated offline apt install: PASS", out)

        import sqlite3

        conn = sqlite3.connect(config.db_path)
        try:
            rows = conn.execute(
                "SELECT package_name, component, verified"
                " FROM binaries WHERE component = 'local' ORDER BY package_name"
            ).fetchall()
            links = conn.execute(
                "SELECT COUNT(*) FROM binary_to_source"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(
            rows,
            [("demo-app", "local", 1), ("lib-companion", "local", 1)],
        )
        self.assertEqual(links, 2)

        with contextlib.redirect_stdout(io.StringIO()) as verify_out:
            rc = handle_verify_graph(config.db_path)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("Status:                 COMPLETE", verify_out.getvalue())
        self.assertIn("Runtime Status:         COMPLETE", verify_out.getvalue())

        # the dependency tree shows the companion under the app
        with contextlib.redirect_stdout(io.StringIO()) as list_out:
            rc = handle_list_deps(config.db_path, "demo-app", fmt="text")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("demo-app (current)", list_out.getvalue())
        self.assertIn("lib-companion", list_out.getvalue())


@unittest.skipUnless(shutil.which("gcc"), "gcc required")
class TestUndeclaredUnattributedFlow(_PackageStackBase):
    """A real ELF binary linking an undeclared companion library fails the
    component loudly with zero DB changes — the library is never silently
    dropped."""

    def _provision_fixture(self) -> str:
        comp_src = os.path.join(self.tmp.name, "cc")
        os.makedirs(comp_src)
        with open(os.path.join(comp_src, "lib.c"), "w") as f:
            f.write("int companion_value(void) { return 42; }\n")
        _run(["gcc", "-shared", "-fPIC", "-Wl,-soname,libfoo-companion.so.0",
              "-o", os.path.join(comp_src, "libfoo-companion.so.0"),
              os.path.join(comp_src, "lib.c")])
        os.symlink("libfoo-companion.so.0", os.path.join(comp_src, "libfoo-companion.so"))
        with open(os.path.join(comp_src, "main.c"), "w") as f:
            f.write(
                "extern int companion_value(void);"
                " int main(void) { return companion_value(); }\n"
            )
        _run(["gcc", "-o", os.path.join(comp_src, "fixture-tool"),
              os.path.join(comp_src, "main.c"), "-L", comp_src, "-lfoo-companion"])

        stage = os.path.join(self.tmp.name, "staging", "fixture-tool")
        os.makedirs(os.path.join(stage, "usr/bin"))
        os.makedirs(os.path.join(stage, "usr/lib"))
        shutil.copy2(
            os.path.join(comp_src, "fixture-tool"), os.path.join(stage, "usr/bin", "fixture-tool")
        )
        shutil.copy2(
            os.path.join(comp_src, "libfoo-companion.so.0"),
            os.path.join(stage, "usr/lib", "libfoo-companion.so.0"),
        )
        os.makedirs(os.path.join(self.tmp.name, "repos", "fixture-tool"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "repos", "fixture-tool", "meson.build"), "w") as f:
            f.write('project("fixture-tool", version: "1.2.3")\n')
        component = (
            "[[component]]\n"
            'name = "fixture-tool"\n'
            'binary_package = "fixture-tool"\n'
            'source_tree = "fixture-tool"\n'
            'version = "1.2.3"\n'
            'build_system = "meson"\n'
            'description = "fixture tool"\n'
            'maintainer = "T <t@example.invalid>"\n'
        )
        return self._write_recipe(component)

    def test_undeclared_unattributed_library_fails_loudly_zero_changes(self):
        config = self._provision()
        recipe = self._provision_fixture()
        rc, _, err = self._run_package_stack(recipe)
        self.assertEqual(rc, EXIT_ERROR)
        self.assertIn("un-attributable library", err)
        self.assertIn("libfoo-companion.so.0", err)
        self.assertIn("fixture-tool", err)

        import sqlite3

        conn = sqlite3.connect(config.db_path)
        try:
            binaries = conn.execute("SELECT COUNT(*) FROM binaries").fetchone()[0]
            sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(binaries, 0)
        self.assertEqual(sources, 0)


if __name__ == "__main__":
    unittest.main()
