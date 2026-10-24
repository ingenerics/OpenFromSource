import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_fixture_repos  # noqa: E402

from package_mgmt.lib.model.alignment import (  # noqa: E402
    DependencyRequirement,
    ManifestRepo,
    VersionManifest,
)
from package_mgmt.lib.service.buildmeta import (  # noqa: E402
    parse_cmake_requirements,
    parse_meson_requirements,
)
from package_mgmt.lib.service.alignment import (  # noqa: E402
    AlignmentLoadError,
    Baseline,
    PKG_CONFIG_MAPPING,
    baseline_label,
    check_requirement,
    enumerate_eras,
    evaluate_era,
    load_baseline,
    load_manifest,
    write_manifest,
)
from package_mgmt.lib.service.db_init import initialize_database  # noqa: E402


def _baseline(mapping: dict[str, str] | None = None) -> Baseline:
    return Baseline(versions=dict(mapping or {}), provides={})


class TestMesonParser(unittest.TestCase):
    def test_dependency_with_version(self):
        reqs = parse_meson_requirements(
            "Hyprland", "aquamarine = dependency('aquamarine', version: '>=0.4.2')\n"
        )
        self.assertEqual(len(reqs), 1)
        r = reqs[0]
        self.assertEqual(r.repo, "Hyprland")
        self.assertEqual(r.module, "aquamarine")
        self.assertEqual(r.operator, ">=")
        self.assertEqual(r.version, "0.4.2")
        self.assertFalse(r.optional)
        self.assertEqual(r.source_ref, "Hyprland:meson.build:1")

    def test_required_false_is_optional(self):
        reqs = parse_meson_requirements(
            "Hyprland", "gio_dep = dependency('gio-2.0', required: false)\n"
        )
        self.assertEqual(len(reqs), 1)
        self.assertTrue(reqs[0].optional)

    def test_get_option_gated_is_optional(self):
        reqs = parse_meson_requirements(
            "Hyprland", "xcb_dep = dependency('xcb', required: get_option('xwayland'))\n"
        )
        self.assertEqual(len(reqs), 1)
        self.assertTrue(reqs[0].optional)

    def test_unversioned_dependency(self):
        reqs = parse_meson_requirements("Hyprland", "wl = dependency('wayland-server')\n")
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0].module, "wayland-server")
        self.assertEqual(reqs[0].version, "")

    def test_skips_non_dependency_lines(self):
        reqs = parse_meson_requirements(
            "Hyprland", "project('Hyprland', version: '0.45.0')\nfoo = 1\n"
        )
        self.assertEqual(reqs, [])

    def test_override_dependency_is_not_a_requirement(self):
        reqs = parse_meson_requirements(
            "hyprland-protocols",
            "meson.override_dependency('hyprland-protocols', hyprland_protocols)\n",
        )
        self.assertEqual(reqs, [])


class TestCmakeParser(unittest.TestCase):
    def test_find_package_with_version_is_a_minimum(self):
        reqs = parse_cmake_requirements(
            "Hyprland", "find_package(hyprwayland-scanner 0.4.0 REQUIRED)\n"
        )
        self.assertEqual(len(reqs), 1)
        r = reqs[0]
        self.assertEqual(r.module, "hyprwayland-scanner")
        self.assertEqual(r.operator, ">=")
        self.assertEqual(r.version, "0.4.0")
        self.assertFalse(r.optional)
        self.assertEqual(r.source_ref, "Hyprland:CMakeLists.txt:1")

    def test_find_package_infra_modules_skipped(self):
        text = (
            "find_package(PkgConfig REQUIRED)\n"
            "find_package(Threads REQUIRED)\n"
            "find_package(OpenGL REQUIRED COMPONENTS GLES3)\n"
            "find_package(GTest CONFIG REQUIRED)\n"
        )
        reqs = parse_cmake_requirements("Hyprland", text)
        self.assertEqual(reqs, [])

    def test_pkg_search_module_parsed_like_check(self):
        text = (
            "pkg_search_module(LUA REQUIRED IMPORTED_TARGET GLOBAL "
            "lua55 lua5.5 lua-55 lua-5.5 lua>=5.5 lua<5.6)\n"
        )
        reqs = parse_cmake_requirements("Hyprland", text)
        constraints = {(r.module, r.operator, r.version) for r in reqs}
        self.assertIn(("lua", ">=", "5.5"), constraints)
        self.assertIn(("lua", "<", "5.6"), constraints)
        self.assertIn(("lua55", "=", ""), constraints)
        self.assertFalse(reqs[0].optional)

    def test_find_package_no_version_is_required_any(self):
        reqs = parse_cmake_requirements(
            "Hyprland", "find_package(glaze CONFIG REQUIRED)\n"
        )
        self.assertEqual(len(reqs), 1)
        r = reqs[0]
        self.assertEqual(r.module, "glaze")
        self.assertEqual(r.version, "")
        self.assertFalse(r.optional)

    def test_find_package_without_required_is_optional(self):
        reqs = parse_cmake_requirements(
            "Hyprland", "find_package(OptionalLib QUIET)\n"
        )
        self.assertEqual(len(reqs), 1)
        self.assertTrue(reqs[0].optional)

    def test_pkg_check_modules_parses_module_tokens(self):
        text = (
            "pkg_check_modules(deps REQUIRED IMPORTED_TARGET GLOBAL\n"
            "  libinput>=1.29\n"
            "  wayland-protocols>=1.49\n"
            "  xkbcommon>=1.11.0\n"
            "  hyprutils>=0.14.0)\n"
        )
        reqs = parse_cmake_requirements("Hyprland", text)
        by_module = {r.module: r for r in reqs}
        self.assertEqual(
            set(by_module),
            {"libinput", "wayland-protocols", "xkbcommon", "hyprutils"},
        )
        self.assertEqual(by_module["libinput"].operator, ">=")
        self.assertEqual(by_module["libinput"].version, "1.29")
        self.assertFalse(by_module["libinput"].optional)

    def test_variable_expansion_from_set(self):
        text = (
            "set(LIBINPUT_MINIMUM_VERSION 1.29)\n"
            "pkg_check_modules(deps REQUIRED IMPORTED_TARGET GLOBAL\n"
            "  libinput>=${LIBINPUT_MINIMUM_VERSION})\n"
        )
        reqs = parse_cmake_requirements("Hyprland", text)
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0].version, "1.29")


class TestMappingAndCheck(unittest.TestCase):
    def test_curated_mapping_entries(self):
        self.assertEqual(PKG_CONFIG_MAPPING["wayland-server"], "libwayland-dev")
        self.assertEqual(PKG_CONFIG_MAPPING["libinput"], "libinput-dev")
        self.assertEqual(PKG_CONFIG_MAPPING["xkbcommon"], "libxkbcommon-dev")
        self.assertEqual(PKG_CONFIG_MAPPING["gio-2.0"], "libglib2.0-dev")
        self.assertEqual(PKG_CONFIG_MAPPING["gbm"], "libgbm-dev")
        self.assertEqual(PKG_CONFIG_MAPPING["pangocairo"], "libpango1.0-dev")

    def test_unknown_module_fails_loudly(self):
        req = DependencyRequirement(repo="Hyprland", module="glaze", version="7.2.0")
        unmet = check_requirement(req, _baseline(), {})
        self.assertIsNotNone(unmet)
        self.assertEqual(unmet.reason, "unknown_module")

    def test_no_baseline_record_is_not_in_trixie_main(self):
        req = DependencyRequirement(repo="Hyprland", module="libinput", version="1.28")
        unmet = check_requirement(req, _baseline(), {})
        self.assertEqual(unmet.reason, "not_in_trixie_main")
        self.assertEqual(unmet.cached_package, "libinput-dev")

    def test_boundary_exact_match_satisfied(self):
        req = DependencyRequirement(repo="Hyprland", module="libinput", version="1.28")
        unmet = check_requirement(req, _baseline({"libinput-dev": "1.28.1-1"}), {})
        self.assertIsNone(unmet)

    def test_too_low_version_rejected(self):
        req = DependencyRequirement(repo="Hyprland", module="libinput", version="1.29")
        unmet = check_requirement(req, _baseline({"libinput-dev": "1.28.1-1"}), {})
        self.assertIsNotNone(unmet)
        self.assertEqual(unmet.reason, "version_too_low")
        self.assertEqual(unmet.cached_version, "1.28.1-1")

    def test_epoch_handling(self):
        req = DependencyRequirement(
            repo="Hyprland", module="spdlog", version="1:1.15.2"
        )
        unmet = check_requirement(
            req, _baseline({"libspdlog-dev": "1:1.15.2+ds-2"}), {}
        )
        self.assertIsNone(unmet)

    def test_ecosystem_tier_compares_provided_version(self):
        req = DependencyRequirement(
            repo="Hyprland", module="hyprutils", version="0.14.0", tier="ecosystem"
        )
        unmet = check_requirement(req, _baseline(), {"hyprutils": "0.14.0"})
        self.assertIsNone(unmet)
        unmet = check_requirement(req, _baseline(), {"hyprutils": "0.13.0"})
        self.assertIsNotNone(unmet)
        self.assertEqual(unmet.reason, "version_too_low")

    def test_unversioned_requirement_satisfied_by_presence(self):
        req = DependencyRequirement(repo="Hyprland", module="wayland-server")
        unmet = check_requirement(req, _baseline({"libwayland-dev": "1.23.1-3"}), {})
        self.assertIsNone(unmet)


class TestEraEnumeration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repos = make_fixture_repos.build_fixture_repos(self.tmp.name)
        self.repo_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_enumerate_eras_newest_first(self):
        self.assertEqual(enumerate_eras(self.repo_dir), ["0.46", "0.45", "0.44"])

    def test_enumerate_eras_requires_anchor_repo(self):
        import shutil

        shutil.rmtree(os.path.join(self.repo_dir, "Hyprland"))
        with self.assertRaises(AlignmentLoadError):
            enumerate_eras(self.repo_dir)

    def test_era_anchored_on_anchor_repo_with_curated_map(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo_dir = tmp.name
        make_fixture_repos.build_era_anchor_set(repo_dir)
        self.assertEqual(enumerate_eras(repo_dir), ["0.45"])
        baseline = _baseline({"libwayland-dev": "1.23.1-3"})
        reqs, unmet = evaluate_era(
            repo_dir, ["Hyprland", "hyprwayland-scanner"], "0.45", baseline
        )
        self.assertEqual(unmet, [])
        ecosystem = [r for r in reqs if r.tier == "ecosystem"]
        self.assertEqual(len(ecosystem), 1)
        self.assertEqual(ecosystem[0].module, "hyprwayland-scanner")
        self.assertEqual(ecosystem[0].version, "0.3.10")

    def test_source_built_dependency_satisfied_in_ecosystem_tier(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo_dir = tmp.name
        make_fixture_repos.build_source_built_set(repo_dir)
        baseline = _baseline({"libwayland-dev": "1.23.1-3"})
        reqs, unmet = evaluate_era(
            repo_dir, ["Hyprland", "hypridle"], "0.45", baseline
        )
        self.assertEqual(unmet, [])
        sdbus = [r for r in reqs if r.module == "sdbus-c++"]
        self.assertEqual(len(sdbus), 1)
        self.assertEqual(sdbus[0].tier, "ecosystem")

    def test_evaluate_era_0_45_satisfied(self):
        baseline = _baseline(
            {
                "libwayland-dev": "1.23.1-3",
                "libinput-dev": "1.28.1-1",
                "libxkbcommon-dev": "1.7.0-2",
            }
        )
        reqs, unmet = evaluate_era(
            self.repo_dir,
            make_fixture_repos.REPO_NAMES,
            "0.45",
            baseline,
        )
        self.assertEqual(unmet, [])
        cache_tier = [r for r in reqs if r.tier == "cache"]
        ecosystem_tier = [r for r in reqs if r.tier == "ecosystem"]
        self.assertEqual(len(cache_tier), make_fixture_repos.CACHE_TIER_COUNT)
        self.assertEqual(len(ecosystem_tier), make_fixture_repos.ECOSYSTEM_TIER_COUNT)

    def test_evaluate_era_0_46_fails(self):
        baseline = _baseline(
            {
                "libwayland-dev": "1.23.1-3",
                "libinput-dev": "1.28.1-1",
                "libxkbcommon-dev": "1.7.0-2",
            }
        )
        reqs, unmet = evaluate_era(
            self.repo_dir,
            make_fixture_repos.REPO_NAMES,
            "0.46",
            baseline,
        )
        self.assertTrue(unmet)
        reasons = {u.reason for u in unmet}
        self.assertIn("version_too_low", reasons)
        self.assertIn("unknown_module", reasons)
        modules = {u.requirement.module for u in unmet}
        self.assertIn("libinput", modules)
        self.assertIn("glaze", modules)


class TestConfigDrivenBaseline(unittest.TestCase):
    """The alignment baseline is derived from the configured first archive
    source, not a hard-coded release/component pair."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _db_with(self, rows):
        db_path = os.path.join(self.tmp.name, "baseline.db")
        initialize_database(db_path)
        conn = sqlite3.connect(db_path)
        try:
            for name, version, release, component in rows:
                conn.execute(
                    "INSERT INTO binaries (package_name, version, architecture,"
                    " release, component, status, verified)"
                    " VALUES (?, ?, 'amd64', ?, ?, 'current', 1)",
                    (name, version, release, component),
                )
            conn.commit()
        finally:
            conn.close()
        return db_path

    def test_non_trixie_baseline_yields_a_meaningful_verdict(self):
        db_path = self._db_with(
            [("libwayland-dev", "1.23.1-3", "bookworm", "main")]
        )
        baseline = load_baseline(db_path, "", "bookworm", "main")
        self.assertEqual(baseline.cached_version("libwayland-dev"), "1.23.1-3")
        requirement = DependencyRequirement(
            repo="Hyprland", module="wayland-server", version="1.23.1"
        )
        self.assertIsNone(check_requirement(requirement, baseline, {}))
        hardcoded = load_baseline(db_path, "", "trixie", "main")
        self.assertIsNone(hardcoded.cached_version("libwayland-dev"))
        self.assertIsNotNone(check_requirement(requirement, hardcoded, {}))

    def test_first_component_not_main_baseline(self):
        db_path = self._db_with(
            [("libinput-dev", "1.28.1-1", "trixie", "contrib")]
        )
        baseline = load_baseline(db_path, "", "trixie", "contrib")
        self.assertEqual(baseline.cached_version("libinput-dev"), "1.28.1-1")
        requirement = DependencyRequirement(
            repo="Hyprland", module="libinput", version="1.28.1"
        )
        self.assertIsNone(check_requirement(requirement, baseline, {}))
        main_view = load_baseline(db_path, "", "trixie", "main")
        self.assertIsNone(main_view.cached_version("libinput-dev"))

    def test_standard_trixie_main_baseline_unchanged(self):
        db_path = self._db_with(
            [("libwayland-dev", "1.23.1-3", "trixie", "main")]
        )
        baseline = load_baseline(db_path, "", "trixie", "main")
        self.assertEqual(baseline.cached_version("libwayland-dev"), "1.23.1-3")
        requirement = DependencyRequirement(
            repo="Hyprland", module="wayland-server", version="1.23.1"
        )
        self.assertIsNone(check_requirement(requirement, baseline, {}))

    def test_baseline_label_reflects_configured_source(self):
        self.assertEqual(
            baseline_label("bookworm", "main"), "bookworm/main (verified, read-only)"
        )
        self.assertEqual(
            baseline_label("trixie", "contrib"),
            "trixie/contrib (verified, read-only)",
        )
        self.assertEqual(
            baseline_label("trixie", "main"), "trixie/main (verified, read-only)"
        )


class TestManifestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_write_and_load_round_trip(self):
        manifest = VersionManifest(
            era="0.45",
            chosen=date(2026, 9, 26),
            reason="Newest satisfying era",
            repos=(
                ManifestRepo(
                    name="Hyprland",
                    url="https://github.com/hyprwm/Hyprland",
                    tag="v0.45.0",
                    commit="0123456789abcdef0123456789abcdef01234567",
                    build="meson",
                    reason="aligns",
                ),
            ),
        )
        written = write_manifest(manifest, self.tmp.name)
        loaded = load_manifest(written)
        self.assertEqual(loaded.era, manifest.era)
        self.assertEqual(loaded.chosen, manifest.chosen)
        self.assertEqual(loaded.reason, manifest.reason)
        self.assertEqual(loaded.repos[0].name, "Hyprland")
        self.assertEqual(loaded.repos[0].commit, manifest.repos[0].commit)
        self.assertEqual(loaded.repos[0].build, "meson")

    def test_malformed_manifest_rejected(self):
        path = os.path.join(self.tmp.name, "bad.toml")
        with open(path, "w") as f:
            f.write("era = 5\n")
        with self.assertRaises(AlignmentLoadError):
            load_manifest(path)


if __name__ == "__main__":
    unittest.main()
