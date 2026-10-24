"""Unit tests for the source-stack packaging services (`debgen`, `srcgen`).

Covers the generated binary package contract (control completeness, derived
`Depends` merge with `explicit_depends`, un-attributable library reporting,
byte determinism, revision bump) and the generated source package contract
(complete `.dsc` + referenced files, checksums, offline `dpkg-source -x`,
determinism).
"""

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_deb  # noqa: E402
from db import config, insert_binary  # noqa: E402

from debian import deb822  # noqa: E402
from debian import debfile  # noqa: E402

from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL  # noqa: E402
from package_mgmt.lib.model.packaging import ComponentRecipe  # noqa: E402
from package_mgmt.lib.model.packaging import StackRecipe  # noqa: E402
from package_mgmt.lib.service.debgen import build_binary_package  # noqa: E402
from package_mgmt.lib.service.package_stack import (  # noqa: E402
    _local_packages_text,
    RecipeValidationError,
    load_recipe,
    resolve_component_order,
)
from package_mgmt.lib.service.srcgen import build_source_package  # noqa: E402

SOURCE_EPOCH = 1792454400  # 2026-10-20T00:00:00Z


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=True)


@unittest.skipUnless(shutil.which("gcc"), "gcc required")
class TestDebGen(unittest.TestCase):
    """Binary package generation from a staged install directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        comp_src = os.path.join(self.root, "cc")
        os.makedirs(comp_src)
        lib_c = os.path.join(comp_src, "lib.c")
        with open(lib_c, "w") as f:
            f.write("int companion_value(void) { return 42; }\n")
        _run(["gcc", "-shared", "-fPIC", "-Wl,-soname,libfoo-companion.so.0",
              "-o", os.path.join(comp_src, "libfoo-companion.so.0"), lib_c])
        os.symlink(
            "libfoo-companion.so.0", os.path.join(comp_src, "libfoo-companion.so")
        )
        main_c = os.path.join(comp_src, "main.c")
        with open(main_c, "w") as f:
            f.write(
                "extern int companion_value(void);"
                " int main(void) { return companion_value(); }\n"
            )
        _run(["gcc", "-o", os.path.join(comp_src, "fixture-tool"), main_c,
              "-L", comp_src, "-lfoo-companion"])
        self.comp_src = comp_src

    def _stage(self) -> str:
        stage = os.path.join(self.root, "stage")
        os.makedirs(os.path.join(stage, "usr/bin"))
        os.makedirs(os.path.join(stage, "usr/lib"))
        shutil.copy2(
            os.path.join(self.comp_src, "fixture-tool"),
            os.path.join(stage, "usr/bin", "fixture-tool"),
        )
        shutil.copy2(
            os.path.join(self.comp_src, "libfoo-companion.so.0"),
            os.path.join(stage, "usr/lib", "libfoo-companion.so.0"),
        )
        with open(os.path.join(stage, "usr/share-data.txt"), "w") as f:
            f.write("payload\n")
        return stage

    def _component(self, **overrides) -> ComponentRecipe:
        fields = dict(
            name="fixture-tool",
            binary_package="fixture-tool",
            source_tree="fixture-tool",
            version="1.2.3",
            build_system="meson",
            description="fixture tool",
            architecture="amd64",
            maintainer="T <t@example.invalid>",
            section="misc",
            homepage="https://example.invalid/fixture-tool",
            explicit_depends=("libfoo-companion (>= 1.0)",),
        )
        fields.update(overrides)
        return ComponentRecipe(**fields)

    def _build(self, stage: str, component: ComponentRecipe, work: str,
               default_maintainer: str | None = None):
        return build_binary_package(
            stage, component, work, SOURCE_EPOCH,
            default_maintainer=default_maintainer,
        )

    def _extract(self, deb_path: str) -> str:
        dest = os.path.join(self.root, "extract-" + os.path.basename(deb_path))
        _run(["dpkg-deb", "-x", deb_path, dest])
        control_dir = os.path.join(dest, "DEBIAN")
        os.makedirs(control_dir, exist_ok=True)
        _run(["dpkg-deb", "-e", deb_path, control_dir])
        return dest

    def test_control_completeness(self):
        """All required control fields present and parseable by DebFile."""
        stage = self._stage()
        res = self._build(stage, self._component(), os.path.join(self.root, "w1"))
        with debfile.DebFile(res.deb_path) as deb:
            control = deb.control.debcontrol()
        self.assertEqual(control["Package"], "fixture-tool")
        self.assertEqual(control["Version"], "1.2.3-1")
        self.assertEqual(control["Architecture"], "amd64")
        self.assertEqual(control["Section"], "misc")
        self.assertEqual(control["Priority"], "optional")
        self.assertEqual(control["Maintainer"], "T <t@example.invalid>")
        self.assertEqual(control["Homepage"], "https://example.invalid/fixture-tool")
        self.assertIn("Description", control)
        self.assertTrue(int(control["Installed-Size"]) > 0)
        self.assertIn("Depends", control)

    def test_depends_merge_and_unattributed_reported(self):
        """Derived Depends merged with explicit_depends; un-attributable
        library reported by name and never leaked into Depends."""
        stage = self._stage()
        res = self._build(stage, self._component(), os.path.join(self.root, "w2"))
        self.assertIn("libc6", res.derived_depends)
        self.assertEqual(res.unattributed, ("libfoo-companion.so.0",))
        self.assertIn("libc6", res.depends)
        self.assertIn("libfoo-companion (>= 1.0)", res.depends)
        self.assertNotIn("libfoo-companion.so.0", res.depends)
        with debfile.DebFile(res.deb_path) as deb:
            control = deb.control.debcontrol()
        self.assertEqual(control["Depends"], res.depends)

    def test_unattributed_reported_even_without_explicit_depends(self):
        """The un-attributable library is reported regardless; the recipe must
        declare it (the orchestration enforces the declaration)."""
        stage = self._stage()
        comp = self._component(explicit_depends=())
        res = self._build(stage, comp, os.path.join(self.root, "w3"))
        self.assertEqual(res.unattributed, ("libfoo-companion.so.0",))
        self.assertIn("libc6", res.depends)
        self.assertNotIn("libfoo-companion", res.depends)

    def test_deb_determinism(self):
        """Two runs over identical staged input produce byte-identical .debs."""
        stage = self._stage()
        comp = self._component()
        res1 = self._build(stage, comp, os.path.join(self.root, "d1"))
        res2 = self._build(stage, comp, os.path.join(self.root, "d2"))
        with open(res1.deb_path, "rb") as f:
            first = f.read()
        with open(res2.deb_path, "rb") as f:
            second = f.read()
        self.assertEqual(hashlib.sha256(first).hexdigest(),
                         hashlib.sha256(second).hexdigest())

    def test_revision_bump(self):
        """The Debian revision overrides the default -1 in the version."""
        stage = self._stage()
        res = self._build(stage, self._component(revision=3), os.path.join(self.root, "w4"))
        self.assertEqual(res.version, "1.2.3-3")
        with debfile.DebFile(res.deb_path) as deb:
            control = deb.control.debcontrol()
        self.assertEqual(control["Version"], "1.2.3-3")

    def test_extended_description_and_doc_files(self):
        """The binary control carries an extended description and the package
        ships `usr/share/doc/<pkg>/copyright` + `changelog.Debian.gz`."""
        stage = self._stage()
        res = self._build(stage, self._component(), os.path.join(self.root, "w5"))
        with debfile.DebFile(res.deb_path) as deb:
            control = deb.control.debcontrol()
        lines = control["Description"].splitlines()
        self.assertGreaterEqual(len(lines), 2, "extended description required")
        self.assertTrue(lines[1].startswith(" "), "continuation line must be indented")
        root = self._extract(res.deb_path)
        pkg = "fixture-tool"
        self.assertTrue(
            os.path.isfile(os.path.join(root, "usr", "share", "doc", pkg, "copyright"))
        )
        self.assertTrue(
            os.path.isfile(
                os.path.join(root, "usr", "share", "doc", pkg, "changelog.Debian.gz")
            )
        )

    def test_shared_library_trigger_shlibs_and_override(self):
        """A package shipping a shared library carries the ldconfig trigger,
        a `DEBIAN/shlibs` file, and the soname lintian override."""
        stage = self._stage()
        res = self._build(stage, self._component(), os.path.join(self.root, "w6"))
        root = self._extract(res.deb_path)
        debian = os.path.join(root, "DEBIAN")
        triggers = open(os.path.join(debian, "triggers")).read()
        self.assertIn("activate-noawait ldconfig", triggers)
        shlibs = open(os.path.join(debian, "shlibs")).read().strip()
        self.assertEqual(shlibs, "libfoo-companion 0 fixture-tool (>= 1.2.3-1)")
        overrides = open(
            os.path.join(
                root, "usr", "share", "lintian", "overrides", "fixture-tool"
            )
        ).read()
        self.assertIn("package-name-doesnt-match-sonames", overrides)
        self.assertIn("initial-upload-closes-no-bugs", overrides)

    def test_man_page_for_command(self):
        """An undocumented command gets `usr/share/man/man1/<name>.1.gz`."""
        stage = self._stage()
        res = self._build(stage, self._component(), os.path.join(self.root, "w7"))
        root = self._extract(res.deb_path)
        self.assertTrue(
            os.path.isfile(
                os.path.join(root, "usr", "share", "man", "man1", "fixture-tool.1.gz")
            )
        )

    def test_conffiles_for_etc_files(self):
        """Files under `etc/` are declared in `DEBIAN/conffiles`."""
        stage = self._stage()
        os.makedirs(os.path.join(stage, "etc", "pam.d"))
        with open(os.path.join(stage, "etc", "pam.d", "svc"), "w") as f:
            f.write("auth required pam_permit.so\n")
        res = self._build(stage, self._component(), os.path.join(self.root, "w8"))
        root = self._extract(res.deb_path)
        conffiles = open(os.path.join(root, "DEBIAN", "conffiles")).read().split()
        self.assertEqual(conffiles, ["/etc/pam.d/svc"])

    def test_dev_symlink_dropped(self):
        """The unversioned `lib*.so` development symlink is not shipped."""
        stage = self._stage()
        os.symlink(
            "libfoo-companion.so.0",
            os.path.join(stage, "usr", "lib", "libfoo-companion.so"),
        )
        res = self._build(stage, self._component(), os.path.join(self.root, "w9"))
        root = self._extract(res.deb_path)
        self.assertFalse(
            os.path.lexists(
                os.path.join(root, "usr", "lib", "libfoo-companion.so")
            ),
            "the dev symlink must not ship in the runtime package",
        )

    def test_maintainer_default_applied(self):
        """The `default_maintainer` is used when the recipe omits `maintainer`."""
        stage = self._stage()
        comp = self._component(maintainer="")
        res = self._build(
            stage, comp, os.path.join(self.root, "w10"),
            default_maintainer="OpenFromSource <noreply@example.com>",
        )
        with debfile.DebFile(res.deb_path) as deb:
            control = deb.control.debcontrol()
        self.assertEqual(
            control["Maintainer"], "OpenFromSource <noreply@example.com>"
        )


class TestSrcGen(unittest.TestCase):
    """Source package generation from a pinned source tree."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.repo = os.path.join(self.root, "repos")
        tree = os.path.join(self.repo, "fixture-tool")
        os.makedirs(os.path.join(tree, "sub"))
        with open(os.path.join(tree, "meson.build"), "w") as f:
            f.write('project("fixture-tool", version: "1.2.3")\n')
        with open(os.path.join(tree, "README"), "w") as f:
            f.write("hello\n")
        with open(os.path.join(tree, "sub", "nested.txt"), "w") as f:
            f.write("nested\n")
        os.makedirs(os.path.join(tree, "build"))
        with open(os.path.join(tree, "build", "artifact.bin"), "w") as f:
            f.write("excluded\n")
        self.tree = tree

    def _component(self, **overrides) -> ComponentRecipe:
        fields = dict(
            name="fixture-tool",
            binary_package="fixture-tool",
            source_tree="fixture-tool",
            version="1.2.3",
            build_system="meson",
            description="fixture tool",
            architecture="amd64",
            maintainer="T <t@example.invalid>",
            section="misc",
            homepage="https://example.invalid/fixture-tool",
            build_depends=("meson", "ninja-build"),
        )
        fields.update(overrides)
        return ComponentRecipe(**fields)

    def _build(self, component: ComponentRecipe, work: str,
               default_maintainer: str | None = None):
        return build_source_package(
            component, self.repo, work, SOURCE_EPOCH,
            default_maintainer=default_maintainer,
        )

    def _tree(self, res, *parts: str) -> str:
        return os.path.join(res.source_dir, res.source_name, *parts)

    def _dsc(self, source_dir: str) -> deb822.Dsc:
        dsc_names = [n for n in os.listdir(source_dir) if n.endswith(".dsc")]
        self.assertEqual(len(dsc_names), 1, dsc_names)
        with open(os.path.join(source_dir, dsc_names[0]), "r", encoding="utf-8") as f:
            return deb822.Dsc(f.read())

    def test_complete_dsc_set(self):
        """Exactly one .dsc plus every referenced file, in the source dir."""
        res = self._build(self._component(), os.path.join(self.root, "s1"))
        dsc = self._dsc(res.source_dir)
        self.assertEqual(dsc["Source"], "fixture-tool")
        self.assertEqual(dsc["Version"], "1.2.3-1")
        files = dsc["Files"]
        self.assertEqual(len(files), res.file_count)
        self.assertEqual(res.file_count, 2)
        for entry in files:
            self.assertTrue(
                os.path.isfile(os.path.join(res.source_dir, entry["name"])),
                entry["name"],
            )

    def test_checksums_match(self):
        """Every referenced file's SHA256 matches the .dsc's declared value."""
        res = self._build(self._component(), os.path.join(self.root, "s2"))
        dsc = self._dsc(res.source_dir)
        for entry in dsc["Checksums-Sha256"]:
            path = os.path.join(res.source_dir, entry["name"])
            digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
            self.assertEqual(digest, entry["sha256"], entry["name"])
            self.assertEqual(int(entry["size"]), os.path.getsize(path), entry["name"])

    def test_build_output_excluded_from_orig(self):
        """The orig tarball carries no build output or VCS metadata."""
        res = self._build(self._component(), os.path.join(self.root, "s3"))
        orig = os.path.join(res.source_dir, "fixture-tool_1.2.3.orig.tar.gz")
        names = _tar_names(orig)
        self.assertIn("fixture-tool-1.2.3/meson.build", names)
        self.assertIn("fixture-tool-1.2.3/sub/nested.txt", names)
        self.assertNotIn("build/artifact.bin", " ".join(names))
        self.assertFalse(any(".git" in name for name in names))

    @unittest.skipUnless(shutil.which("dpkg-source"), "dpkg-source required")
    def test_unpacks_offline(self):
        """dpkg-source -x of the generated set succeeds offline."""
        res = self._build(self._component(), os.path.join(self.root, "s4"))
        unpack = os.path.join(self.root, "unpack")
        os.makedirs(unpack)
        for name in os.listdir(res.source_dir):
            path = os.path.join(res.source_dir, name)
            if os.path.isfile(path):
                shutil.copy2(path, os.path.join(unpack, name))
        dsc_names = [n for n in os.listdir(unpack) if n.endswith(".dsc")]
        result = subprocess.run(
            ["dpkg-source", "-x", os.path.join(unpack, dsc_names[0])],
            cwd=unpack, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        extracted = os.path.join(unpack, "fixture-tool-1.2.3")
        self.assertTrue(os.path.isfile(os.path.join(extracted, "meson.build")))
        self.assertTrue(os.path.isfile(os.path.join(extracted, "sub", "nested.txt")))
        self.assertTrue(os.path.isdir(os.path.join(extracted, "debian")))

    def test_src_determinism(self):
        """Two runs over the identical tree produce byte-identical outputs."""
        comp = self._component()
        r1 = self._build(comp, os.path.join(self.root, "det1"))
        r2 = self._build(comp, os.path.join(self.root, "det2"))
        self.assertEqual(_dir_sha256(r1.source_dir), _dir_sha256(r2.source_dir))

    def test_rules_has_required_targets(self):
        """The generated `debian/rules` provides every target policy requires."""
        res = self._build(self._component(), os.path.join(self.root, "t1"))
        rules = open(self._tree(res, "debian", "rules")).read()
        phony = next(line for line in rules.splitlines() if line.startswith(".PHONY"))
        for target in (
            "build", "build-arch", "build-indep",
            "binary", "binary-arch", "binary-indep", "clean",
        ):
            self.assertIn(target, phony)

    def test_copyright_dep5_paragraph_separated(self):
        """The dep5 copyright separates the header from the `Files` stanza."""
        res = self._build(self._component(), os.path.join(self.root, "t2"))
        copyright_file = open(self._tree(res, "debian", "copyright")).read()
        self.assertIn("\n\nFiles: *", copyright_file)

    def test_changelog_numeric_offset_date(self):
        """The changelog trailer uses a numeric offset, not `GMT`."""
        res = self._build(self._component(), os.path.join(self.root, "t3"))
        changelog = open(self._tree(res, "debian", "changelog")).read()
        self.assertNotIn("GMT", changelog)
        self.assertRegex(changelog, r" \+0000\n")

    def test_maintainer_default_applied(self):
        """The `default_maintainer` is used when the recipe omits `maintainer`."""
        res = self._build(
            self._component(maintainer=""),
            os.path.join(self.root, "t4"),
            default_maintainer="OpenFromSource <noreply@example.com>",
        )
        control = open(self._tree(res, "debian", "control")).read()
        self.assertIn(
            "Maintainer: OpenFromSource <noreply@example.com>", control
        )


def _tar_names(path: str) -> list[str]:
    import tarfile

    with tarfile.open(path, mode="r:gz") as tar:
        return tar.getnames()


class TestResolveComponentOrder(unittest.TestCase):
    """Topological ordering resolves dependencies before dependents even when
    the recipe lists components in arbitrary order, and rejects cycles."""

    def _component(self, name: str, version: str = "1.0.0",
                   explicit_depends=()) -> ComponentRecipe:
        return ComponentRecipe(
            name=name,
            binary_package=f"pkg-{name}",
            source_tree=name,
            version=version,
            build_system="meson",
            description=f"{name} fixture",
            explicit_depends=tuple(explicit_depends),
        )

    def test_reverse_listed_chain_orders_dependencies_first(self):
        """Components listed dependent-first still order dependencies first,
        with a dependency naming a binary package resolving to its component."""
        a = self._component("a")
        b = self._component("b", explicit_depends=("pkg-a",))
        c = self._component("c", explicit_depends=("pkg-b",))
        recipe = StackRecipe(stack="demo", components=(c, b, a))
        self.assertEqual(
            resolve_component_order(recipe), ("a", "b", "c")
        )

    def test_binary_package_name_resolves_to_component(self):
        """A dependency declared via a component's binary_package (a companion
        runtime library) resolves to that component."""
        sdbus = self._component("sdbus-c++", version="2.0.0")
        app = self._component("hypridle", version="0.1.0",
                              explicit_depends=("pkg-sdbus-c++ (>= 2.0.0)",))
        recipe = StackRecipe(stack="demo", components=(app, sdbus))
        self.assertEqual(
            resolve_component_order(recipe), ("sdbus-c++", "hypridle")
        )

    def test_dependency_cycle_rejected(self):
        a = self._component("a", explicit_depends=("pkg-b",))
        b = self._component("b", explicit_depends=("pkg-a",))
        recipe = StackRecipe(stack="demo", components=(a, b))
        with self.assertRaises(RecipeValidationError) as ctx:
            resolve_component_order(recipe)
        self.assertIn("cycle", str(ctx.exception))

    def test_missing_source_built_dependency_named(self):
        """A declared dependency naming no recipe component fails loudly,
        naming the component and the missing dependency."""
        app = self._component("hypridle", explicit_depends=("sdbus-c++ (>= 2.0.0)",))
        recipe = StackRecipe(stack="demo", components=(app,))
        with self.assertRaises(RecipeValidationError) as ctx:
            resolve_component_order(recipe)
        self.assertIn("hypridle", str(ctx.exception))
        self.assertIn("sdbus-c++", str(ctx.exception))


class TestHyprlandRecipe(unittest.TestCase):
    """The shipped `recipes/hyprland.toml` validates cleanly.

    The canonical Hyprland recipe is the real-stack proof: 13 build
    components (the 12 hyprwm repositories of docs/005 plus the source-built
    `sdbus-c++` vendored at the portal subproject), unique `name`/
    `binary_package`, valid Debian versions, every declared dependency
    resolvable to a recipe component (including the source-built
    `sdbus-c++`), and no dependency cycle.
    """

    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cls.recipe_path = os.path.join(root, "recipes", "hyprland.toml")
        with open(cls.recipe_path, "rb") as f:
            repo_dir = tomllib.load(f).get("stack", {}).get("repo_dir", "repos")
        if not os.path.isdir(os.path.join(root, repo_dir)):
            raise unittest.SkipTest(
                f"pinned Hyprland source tree {repo_dir!r} not present "
                "(populate it locally via scripts/build-hyprland.sh Part 1)"
            )
        cls.recipe = load_recipe(cls.recipe_path)

    def test_thirteen_build_components(self):
        """The recipe carries all 13 build components of docs/005."""
        self.assertEqual(len(self.recipe.components), 13)
        names = {component.name for component in self.recipe.components}
        self.assertEqual(
            names,
            {
                "hyprland-protocols",
                "hyprwayland-scanner",
                "hyprutils",
                "hyprlang",
                "hyprgraphics",
                "aquamarine",
                "hyprcursor",
                "Hyprland",
                "sdbus-c++",
                "hypridle",
                "hyprlock",
                "hyprpaper",
                "xdg-desktop-portal-hyprland",
            },
        )

    def test_unique_names_and_binary_packages(self):
        """No duplicate component name or binary package."""
        names = [component.name for component in self.recipe.components]
        packages = [component.binary_package for component in self.recipe.components]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(packages), len(set(packages)))

    def test_sdbus_cpp_present(self):
        """The source-built dependency is itself a recipe component (edge
        case "Missing source-built dependency"): dependents can resolve it."""
        by_name = {component.name: component for component in self.recipe.components}
        sdbus = by_name["sdbus-c++"]
        self.assertEqual(sdbus.version, "2.0.0")
        self.assertEqual(sdbus.build_system, "cmake")
        self.assertTrue(
            sdbus.source_tree.endswith("subprojects/sdbus-cpp"),
            sdbus.source_tree,
        )

    def test_hypridle_depends_on_sdbus_cpp(self):
        """A dependent component declares the source-built dependency."""
        by_name = {component.name: component for component in self.recipe.components}
        hypridle = by_name["hypridle"]
        self.assertTrue(
            any("libsdbus-c++" in entry for entry in hypridle.explicit_depends),
            hypridle.explicit_depends,
        )

    def test_order_dependencies_first(self):
        """The topological order puts every source-built dependency before its
        dependents (protocols → … → sdbus-c++ → companion tools)."""
        order = resolve_component_order(self.recipe)
        self.assertEqual(len(order), 13)
        for name in ("hypridle", "hyprlock", "xdg-desktop-portal-hyprland"):
            self.assertLess(order.index("sdbus-c++"), order.index(name))
        self.assertLess(order.index("hyprutils"), order.index("hyprlang"))
        self.assertLess(order.index("hyprlang"), order.index("Hyprland"))

    def test_dependency_cycle_free(self):
        """`load_recipe` itself rejects cycles; a clean load proves acyclicity
        (the recipe parsed in setUpClass without a RecipeValidationError)."""
        # Re-load to surface any dependency error on this recipe directly.
        load_recipe(self.recipe_path)
        self.assertTrue(True)


def _dir_sha256(directory: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            result[name] = hashlib.sha256(open(path, "rb").read()).hexdigest()
    return result


class TestLocalPackagesTextWarnings(unittest.TestCase):
    """`package-stack`'s `local` index (FR-014): an unreadable `local` payload
    surfaces a warning naming the component instead of silently skipping the
    degraded record."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = config(self.tmp.name)
        self.conn = sqlite3.connect(os.path.join(self.tmp.name, "test.db"))
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _insert_local_unreadable(self, name, version):
        rid = insert_binary(self.conn, name, version, component="local")
        rel = make_test_deb.pool_rel_path(name, version)
        dest = os.path.join(self.config.binary_cache, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(f"local-{name}-{version}".encode())
        self.conn.commit()
        return rid

    def test_unreadable_local_payload_warns_naming_component(self):
        self._insert_local_unreadable("libfoo", "1.2-3")
        with self.assertLogs("package_mgmt.lib.service.package_stack", level="WARNING") as captured:
            text = _local_packages_text(self.conn, self.config)
        self.assertTrue(
            any("degraded local" in message for message in captured.output),
            f"expected a degraded-local warning, got: {captured.output}",
        )
        self.assertTrue(
            any("libfoo 1.2-3" in message for message in captured.output),
            "the warning must name the degraded package",
        )
        # The degraded record is still served (stored fields), not dropped.
        self.assertIn("Package: libfoo", text)
        self.assertIn("Version: 1.2-3", text)

    def test_missing_local_payload_warns_naming_component(self):
        insert_binary(self.conn, "ghost", "1.0-1", component="local")
        self.conn.commit()
        with self.assertLogs("package_mgmt.lib.service.package_stack", level="WARNING") as captured:
            text = _local_packages_text(self.conn, self.config)
        self.assertTrue(
            any("skipped local" in message for message in captured.output),
            f"expected a skip warning, got: {captured.output}",
        )
        self.assertNotIn("Package: ghost", text)


if __name__ == "__main__":
    unittest.main()
