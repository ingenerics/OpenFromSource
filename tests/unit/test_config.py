import dataclasses
import os
import shutil
import tempfile
import tomllib
import types
import unittest

from package_mgmt.lib.model.config import ArchiveSource, ProjectConfig
from package_mgmt.lib.service.config_loader import (
    graybeard_missing_components_warning,
    load_config,
    source_depth_zero_warning,
)
from package_mgmt.lib.util.errors import ConfigError


class TestProjectConfig(unittest.TestCase):
    def test_frozen(self):
        cfg = ProjectConfig()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cfg.base_dir = "/other"

    def test_default_sources(self):
        cfg = ProjectConfig()
        self.assertEqual(len(cfg.sources), 1)
        src = cfg.sources[0]
        self.assertEqual(src.mirror, "https://deb.debian.org/debian")
        self.assertEqual(src.release, "trixie")
        self.assertEqual(src.components, ("main",))

    def test_defaults_match_shipped_presets(self):
        """Dataclass defaults match the shipped regular_user
        presets — `fetch_source == False`, `max_source_dep_depth == 0`, and
        `max_build_dep_report_depth == 2` (one default story: unconfigured ==
        regular_user; `-1` is an explicit opt-in)."""
        cfg = ProjectConfig()
        self.assertIs(cfg.fetch_source, False)
        self.assertEqual(cfg.max_source_dep_depth, 0)
        self.assertEqual(cfg.max_build_dep_report_depth, 2)

    def test_admin_email_default(self):
        """The default `admin_email` is `noreply@example.com`."""
        self.assertEqual(ProjectConfig().admin_email, "noreply@example.com")


class TestLoadConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.dir, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _write_local(self, content: str):
        with open(os.path.join(self.dir, "project.local.toml"), "w") as f:
            f.write(content)

    def test_expands_base_dir_in_paths(self):
        self._write_defaults('[paths]\nbase_dir = "/tmp/cache"\n')
        config, _ = load_config(self.dir)
        self.assertEqual(config.binary_cache, "/tmp/cache/binary")
        self.assertEqual(config.source_cache, "/tmp/cache/source")
        self.assertEqual(config.iso_dir, "/tmp/cache/iso")
        self.assertEqual(config.db_path, "/tmp/cache/package_mgmt.db")

    def test_local_override_updates_base_dir(self):
        self._write_defaults('[paths]\nbase_dir = "/tmp/default"\n')
        self._write_local('[paths]\nbase_dir = "/tmp/override"\n')
        config, overridden = load_config(self.dir)
        self.assertEqual(config.base_dir, "/tmp/override")
        self.assertIn("base_dir", overridden)

    def test_local_override_cascades_to_expanded_paths(self):
        self._write_defaults('[paths]\nbase_dir = "/tmp/default"\n')
        self._write_local('[paths]\nbase_dir = "/tmp/alt"\n')
        config, _ = load_config(self.dir)
        self.assertEqual(config.binary_cache, "/tmp/alt/binary")
        self.assertEqual(config.db_path, "/tmp/alt/package_mgmt.db")

    def test_relative_path_override_is_rejected(self):
        self._write_defaults('[paths]\nbase_dir = "/tmp/cache"\n')
        self._write_local('[paths]\nbinary_cache = "relative/cache"\n')
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.dir)
        self.assertEqual(
            str(ctx.exception),
            "[paths] binary_cache must be an absolute path after expansion, "
            "got: relative/cache",
        )

    def test_relative_db_path_override_is_rejected(self):
        self._write_defaults('[paths]\nbase_dir = "/tmp/cache"\n')
        self._write_local('[database]\npath = "db/pkg.db"\n')
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.dir)
        self.assertEqual(
            str(ctx.exception),
            "[database] path must be an absolute path after expansion, got: db/pkg.db",
        )

    def test_absolute_path_override_accepted(self):
        self._write_defaults('[paths]\nbase_dir = "/tmp/cache"\n')
        self._write_local('[paths]\nbinary_cache = "/var/pkg/binary"\n')
        config, overridden = load_config(self.dir)
        self.assertEqual(config.binary_cache, "/var/pkg/binary")
        self.assertIn("binary_cache", overridden)

    def test_unknown_keys_silently_ignored(self):
        self._write_defaults('[paths]\nbase_dir = "/tmp/cache"\n')
        self._write_local('[unknown_section]\nfoo = "bar"\n')
        config, overridden = load_config(self.dir)
        self.assertEqual(config.base_dir, "/tmp/cache")
        self.assertEqual(len(overridden), 0)

    def test_unparseable_toml_raises_config_error(self):
        self._write_defaults("not valid toml {{{{\n")
        with self.assertRaises(ConfigError):
            load_config(self.dir)

    def test_missing_defaults_file_raises(self):
        with self.assertRaises(ConfigError):
            load_config("/nonexistent/path")

    def test_can_set_all_fields_via_toml(self):
        self._write_defaults("""[paths]
base_dir = "/var/cache/pkg"
binary_cache = "{base_dir}/custom_binary"
source_cache = "{base_dir}/custom_source"

[database]
path = "{base_dir}/my.db"

[logging]
level = "DEBUG"
directory = "{base_dir}/my_logs"
""")
        config, overridden = load_config(self.dir)
        self.assertEqual(config.base_dir, "/var/cache/pkg")
        self.assertEqual(config.binary_cache, "/var/cache/pkg/custom_binary")
        self.assertEqual(config.source_cache, "/var/cache/pkg/custom_source")
        self.assertEqual(config.db_path, "/var/cache/pkg/my.db")
        self.assertEqual(config.log_level, "DEBUG")
        self.assertEqual(overridden, set())

    def test_defaults_applied_for_missing_sections(self):
        self._write_defaults('[paths]\nbase_dir = "/tmp/cache"\n')
        config, _ = load_config(self.dir)
        self.assertEqual(config.log_level, "INFO")
        self.assertEqual(config.log_dir, "/tmp/cache/logs")

    def test_admin_email_loads_from_metadata_section(self):
        self._write_defaults(
            '[paths]\nbase_dir = "/tmp/cache"\n[metadata]\nadmin_email = "ops@example.com"\n'
        )
        config, _ = load_config(self.dir)
        self.assertEqual(config.admin_email, "ops@example.com")

    def test_admin_email_local_override(self):
        self._write_defaults(
            '[paths]\nbase_dir = "/tmp/cache"\n[metadata]\nadmin_email = "ops@example.com"\n'
        )
        self._write_local('[metadata]\nadmin_email = "admin@example.org"\n')
        config, overridden = load_config(self.dir)
        self.assertEqual(config.admin_email, "admin@example.org")
        self.assertIn("admin_email", overridden)

    def test_multi_source_config_parses_correctly(self):
        self._write_defaults("""[paths]
base_dir = "/tmp/cache"

[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie"
components = ["main", "contrib"]

[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie-backports"
components = ["main"]
""")
        config, _ = load_config(self.dir)
        self.assertEqual(len(config.sources), 2)
        self.assertEqual(config.sources[0].release, "trixie")
        self.assertEqual(config.sources[0].components, ("main", "contrib"))
        self.assertEqual(config.sources[1].release, "trixie-backports")
        self.assertEqual(config.sources[1].components, ("main",))

    def test_duplicate_release_component_raises_error(self):
        self._write_defaults("""[paths]
base_dir = "/tmp/cache"

[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie"
components = ["main"]

[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie"
components = ["main"]
""")
        with self.assertRaises(ConfigError):
            load_config(self.dir)

    def test_overlapping_component_lists_are_rejected_per_pair(self):
        self._write_defaults("""[paths]
base_dir = "/tmp/cache"

[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie"
components = ["main"]

[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie"
components = ["main", "contrib"]
""")
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.dir)
        self.assertIn("(trixie, main)", str(ctx.exception),
                      "error must name the offending (release, component) pair")

    def test_disjoint_component_lists_across_releases_accepted(self):
        self._write_defaults("""[paths]
base_dir = "/tmp/cache"

[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie"
components = ["main", "contrib"]

[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie-backports"
components = ["contrib", "non-free"]
""")
        config, _ = load_config(self.dir)
        self.assertEqual(len(config.sources), 2)
        self.assertEqual(config.sources[0].components, ("main", "contrib"))
        self.assertEqual(config.sources[1].components, ("contrib", "non-free"))

    def test_missing_archive_source_uses_default(self):
        self._write_defaults('[paths]\nbase_dir = "/tmp/cache"\n')
        config, _ = load_config(self.dir)
        self.assertEqual(len(config.sources), 1)
        self.assertEqual(config.sources[0].release, "trixie")

    def test_local_override_replaces_archive_sources(self):
        self._write_defaults("""[paths]
base_dir = "/tmp/cache"

[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie"
components = ["main"]
""")
        self._write_local("""[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "bookworm"
components = ["main", "contrib", "non-free"]
""")
        config, overridden = load_config(self.dir)
        self.assertEqual(len(config.sources), 1)
        self.assertEqual(config.sources[0].release, "bookworm")
        self.assertEqual(config.sources[0].components, ("main", "contrib", "non-free"))
        self.assertIn("sources", overridden)


class TestConfigTypeValidation(unittest.TestCase):
    """Every known configuration value is validated against its expected type
    at load, and unknown key names are rejected — a mistyped value or an
    unknown key yields a `ConfigError` naming the key (never a raw traceback
    or silent acceptance). Booleans accept only real TOML booleans."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.dir, "project.defaults.toml"), "w") as f:
            f.write(content)

    def test_mistyped_values_name_key_and_expected_type(self):
        cases = [
            ('[paths]\nbase_dir = 123\n', "[paths] base_dir", "a string"),
            ('[paths]\nbase_dir = "/tmp/x"\nbinary_cache = 5\n', "[paths] binary_cache", "a string"),
            ('[paths]\nbase_dir = "/tmp/x"\n[fetch]\nsource = "yes"\n', "[fetch] source", "a boolean"),
            ('[paths]\nbase_dir = "/tmp/x"\n[metadata]\nadmin_email = 5\n', "[metadata] admin_email", "a string"),
        ]
        for content, key, type_label in cases:
            with self.subTest(content=content):
                self._write_defaults(content)
                with self.assertRaises(ConfigError) as ctx:
                    load_config(self.dir)
                self.assertIn(key, str(ctx.exception), "error must name the key")
                self.assertIn(type_label, str(ctx.exception), "error must name the expected type")

    def test_non_table_archive_raises_config_error(self):
        self._write_defaults('archive = 5\n[paths]\nbase_dir = "/tmp/x"\n')
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.dir)
        self.assertIn("[archive]", str(ctx.exception))
        self.assertIn("table", str(ctx.exception))

    def test_string_booleans_rejected_and_real_booleans_accepted(self):
        for value in ('"yes"', '"true"', '"1"'):
            with self.subTest(value=value):
                self._write_defaults(
                    f'[paths]\nbase_dir = "/tmp/x"\n[fetch]\nsource = {value}\n'
                )
                with self.assertRaises(ConfigError) as ctx:
                    load_config(self.dir)
                self.assertIn("[fetch] source", str(ctx.exception))
                self.assertIn("a boolean", str(ctx.exception))
        for value, expected in (("true", True), ("false", False)):
            with self.subTest(value=value):
                self._write_defaults(
                    f'[paths]\nbase_dir = "/tmp/x"\n[fetch]\nsource = {value}\n'
                )
                config, _ = load_config(self.dir)
                self.assertIs(config.fetch_source, expected)

    def test_unknown_key_names_are_rejected(self):
        cases = [
            ('[paths]\nbase_dir = "/tmp/x"\nbinry_cache = "/tmp/x/b"\n', "binry_cache"),
            ('[paths]\nbase_dir = "/tmp/x"\n[resolution]\nmax_source_dep_th = 1\n', "max_source_dep_th"),
            ('[paths]\nbase_dir = "/tmp/x"\n[resolution]\nmax_build_dep_report_depth_extra = 1\n', "max_build_dep_report_depth_extra"),
        ]
        for content, key in cases:
            with self.subTest(key=key):
                self._write_defaults(content)
                with self.assertRaises(ConfigError) as ctx:
                    load_config(self.dir)
                self.assertIn(key, str(ctx.exception), "error must name the unknown key")

    def test_correctly_typed_values_load_unchanged(self):
        self._write_defaults("""[paths]
base_dir = "/var/cache/pkg"
binary_cache = "{base_dir}/custom_binary"
source_cache = "{base_dir}/custom_source"
iso_dir = "{base_dir}/custom_iso"

[database]
path = "{base_dir}/my.db"

[logging]
level = "DEBUG"
directory = "{base_dir}/my_logs"

[fetch]
source = true

[resolution]
max_source_dep_depth = -1
max_build_dep_report_depth = 3
""")
        config, _ = load_config(self.dir)
        self.assertEqual(config.base_dir, "/var/cache/pkg")
        self.assertEqual(config.binary_cache, "/var/cache/pkg/custom_binary")
        self.assertEqual(config.source_cache, "/var/cache/pkg/custom_source")
        self.assertEqual(config.iso_dir, "/var/cache/pkg/custom_iso")
        self.assertEqual(config.db_path, "/var/cache/pkg/my.db")
        self.assertEqual(config.log_level, "DEBUG")
        self.assertEqual(config.log_dir, "/var/cache/pkg/my_logs")
        self.assertIs(config.fetch_source, True)
        self.assertEqual(config.max_source_dep_depth, -1)
        self.assertEqual(config.max_build_dep_report_depth, 3)


class TestLocalOverridesApply(unittest.TestCase):
    """A local override for a known key applies even when the defaults file
    lacks the section containing it, and the merged result is validated after
    overrides are applied — so a bad depth in a local override fails at load
    naming the key. `[[archive.source]]` replacement semantics are preserved."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.dir, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _write_local(self, content: str):
        with open(os.path.join(self.dir, "project.local.toml"), "w") as f:
            f.write(content)

    def test_valid_depth_override_applies_without_defaults_section(self):
        self._write_defaults('[paths]\nbase_dir = "/tmp/cache"\n')
        self._write_local('[resolution]\nmax_source_dep_depth = 2\n')
        config, overridden = load_config(self.dir)
        self.assertEqual(config.max_source_dep_depth, 2,
                         "the absent-section override must be applied")
        self.assertIn("max_source_dep_depth", overridden,
                      "the override must be flagged")

    def test_bad_depth_override_fails_naming_key(self):
        self._write_defaults('[paths]\nbase_dir = "/tmp/cache"\n')
        self._write_local('[resolution]\nmax_source_dep_depth = -2\n')
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.dir)
        self.assertIn("max_source_dep_depth", str(ctx.exception),
                      "the merged result must be validated after overrides apply")

    def test_local_archive_source_replacement_preserved(self):
        self._write_defaults("""[paths]
base_dir = "/tmp/cache"

[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie"
components = ["main"]
""")
        self._write_local("""[[archive.source]]
mirror = "http://mirror.example.com/debian"
release = "bookworm"
components = ["main", "contrib"]
""")
        config, overridden = load_config(self.dir)
        self.assertEqual(len(config.sources), 1)
        self.assertEqual(config.sources[0].mirror, "https://mirror.example.com/debian")
        self.assertEqual(config.sources[0].release, "bookworm")
        self.assertEqual(config.sources[0].components, ("main", "contrib"))
        self.assertIn("sources", overridden)


class TestDepthValidation(unittest.TestCase):
    """Both depth settings must be an integer >= -1.

    `-1` = unlimited, `0` = none, positive `N` = up to `N` levels. A value
    below `-1`, a non-integer (`1.5`), or a boolean (`true`) raises
    `ConfigError` naming the key at load time, before any command runs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.dir, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _write_local(self, content: str):
        with open(os.path.join(self.dir, "project.local.toml"), "w") as f:
            f.write(content)

    def _defaults(self, key: str, value: str) -> str:
        return f'[paths]\nbase_dir = "/tmp/cache"\n[resolution]\n{key} = {value}\n'

    def test_absent_depth_keys_use_defaults(self):
        self._write_defaults('[paths]\nbase_dir = "/tmp/cache"\n')
        config, _ = load_config(self.dir)
        self.assertEqual(config.max_source_dep_depth, 0)
        self.assertEqual(config.max_build_dep_report_depth, 2)

    def test_absent_source_depth_uses_default(self):
        self._write_defaults('[paths]\nbase_dir = "/tmp/cache"\n[resolution]\nmax_build_dep_report_depth = 2\n')
        config, _ = load_config(self.dir)
        self.assertEqual(config.max_source_dep_depth, 0)

    def test_invalid_source_depth_values_rejected(self):
        for value in ("-2", "1.5", "true"):
            with self.subTest(value=value):
                self._write_defaults(self._defaults("max_source_dep_depth", value))
                with self.assertRaises(ConfigError) as ctx:
                    load_config(self.dir)
                self.assertIn("max_source_dep_depth", str(ctx.exception))
                self.assertIn("integer >= -1", str(ctx.exception))

    def test_invalid_report_depth_values_rejected(self):
        for value in ("-2", "1.5", "true"):
            with self.subTest(value=value):
                self._write_defaults(self._defaults("max_build_dep_report_depth", value))
                with self.assertRaises(ConfigError) as ctx:
                    load_config(self.dir)
                self.assertIn("max_build_dep_report_depth", str(ctx.exception))
                self.assertIn("integer >= -1", str(ctx.exception))

    def test_valid_depth_values_accepted(self):
        for key in ("max_source_dep_depth", "max_build_dep_report_depth"):
            for value in ("-1", "0", "1", "3"):
                with self.subTest(key=key, value=value):
                    self._write_defaults(self._defaults(key, value))
                    config, _ = load_config(self.dir)
                    self.assertEqual(getattr(config, key), int(value))

    def test_source_depth_override_recorded(self):
        self._write_defaults('[paths]\nbase_dir = "/tmp/cache"\n[resolution]\nmax_source_dep_depth = 0\n')
        self._write_local('[resolution]\nmax_source_dep_depth = 2\n')
        config, overridden = load_config(self.dir)
        self.assertEqual(config.max_source_dep_depth, 2)
        self.assertIn("max_source_dep_depth", overridden)

    def test_source_depth_in_defaults_not_overridden(self):
        self._write_defaults(self._defaults("max_source_dep_depth", "1"))
        config, overridden = load_config(self.dir)
        self.assertEqual(config.max_source_dep_depth, 1)
        self.assertNotIn("max_source_dep_depth", overridden)


class TestSourceDepthWarning(unittest.TestCase):
    """The depth-0 warning fires when source fetching is enabled
    but `max_source_dep_depth = 0` (no sources will be fetched); it is absent
    when `source = false` (regular_user — the depth is moot) or when the depth
    actually fetches something (`-1`/positive)."""

    def test_warning_fires_when_source_enabled_and_depth_zero(self):
        config = types.SimpleNamespace(fetch_source=True, max_source_dep_depth=0)
        text = source_depth_zero_warning(config)
        self.assertIsNotNone(text)
        self.assertIn("max_source_dep_depth", text)
        self.assertIn("-1", text)

    def test_no_warning_when_source_disabled(self):
        config = types.SimpleNamespace(fetch_source=False, max_source_dep_depth=0)
        self.assertIsNone(source_depth_zero_warning(config))

    def test_no_warning_when_depth_positive(self):
        config = types.SimpleNamespace(fetch_source=True, max_source_dep_depth=1)
        self.assertIsNone(source_depth_zero_warning(config))

    def test_no_warning_when_depth_unlimited(self):
        config = types.SimpleNamespace(fetch_source=True, max_source_dep_depth=-1)
        self.assertIsNone(source_depth_zero_warning(config))

    def test_absent_depth_defaults_to_zero(self):
        config = types.SimpleNamespace(fetch_source=True)
        self.assertIsNotNone(source_depth_zero_warning(config),
                             "an unconfigured depth is 0 (regular_user presets); source=true warns")

    def test_real_project_config_respected(self):
        cfg = ProjectConfig(fetch_source=True, max_source_dep_depth=0)
        self.assertIsNotNone(source_depth_zero_warning(cfg))
        cfg2 = ProjectConfig(fetch_source=False, max_source_dep_depth=0)
        self.assertIsNone(source_depth_zero_warning(cfg2))


class TestGraybeardMissingComponentsWarning(unittest.TestCase):
    """The graybeard missing-component warning fires only when the
    configuration is graybeard (`source = true` AND
    `max_source_dep_depth = -1`) AND the union of the config's trixie-source
    components is missing any of the four canonical components (`main`,
    `contrib`, `non-free`, `non-free-firmware`). It returns None for complete
    graybeard configs and for every non-graybeard config."""

    def _config(self, **kwargs):
        defaults = dict(
            fetch_source=True,
            max_source_dep_depth=-1,
            sources=(
                ArchiveSource(
                    mirror="http://127.0.0.1:1",
                    release="trixie",
                    components=("main", "contrib"),
                ),
            ),
        )
        defaults.update(kwargs)
        return types.SimpleNamespace(**defaults)

    def _missing_part(self, text: str) -> str:
        return text.split("missing component(s):", 1)[1]

    def test_warning_fires_when_graybeard_missing_canonical_components(self):
        text = graybeard_missing_components_warning(self._config())
        self.assertIsNotNone(text)
        self.assertIn("graybeard configuration", text)
        self.assertIn("source = true", text)
        self.assertIn("max_source_dep_depth = -1", text)

    def test_warning_names_only_missing_components(self):
        text = graybeard_missing_components_warning(self._config())
        missing = self._missing_part(text)
        self.assertIn("non-free, non-free-firmware", missing)
        self.assertNotIn("main", missing)
        self.assertNotIn("contrib", missing)

    def test_warning_lists_single_missing_component(self):
        config = self._config(
            sources=(
                ArchiveSource(
                    mirror="http://127.0.0.1:1",
                    release="trixie",
                    components=("main", "contrib", "non-free"),
                ),
            ),
        )
        text = graybeard_missing_components_warning(config)
        self.assertIsNotNone(text)
        self.assertIn("non-free-firmware", self._missing_part(text))

    def test_no_warning_when_graybeard_complete(self):
        config = self._config(
            sources=(
                ArchiveSource(
                    mirror="http://127.0.0.1:1",
                    release="trixie",
                    components=("main", "contrib", "non-free", "non-free-firmware"),
                ),
            ),
        )
        self.assertIsNone(graybeard_missing_components_warning(config))

    def test_no_warning_when_source_disabled(self):
        self.assertIsNone(graybeard_missing_components_warning(self._config(fetch_source=False)))

    def test_no_warning_when_depth_finite(self):
        self.assertIsNone(graybeard_missing_components_warning(self._config(max_source_dep_depth=1)))
        self.assertIsNone(graybeard_missing_components_warning(self._config(max_source_dep_depth=2)))

    def test_no_warning_when_depth_zero(self):
        self.assertIsNone(graybeard_missing_components_warning(self._config(max_source_dep_depth=0)))

    def test_no_warning_when_depth_absent(self):
        config = types.SimpleNamespace(fetch_source=True)
        self.assertIsNone(graybeard_missing_components_warning(config))

    def test_warning_when_no_trixie_sources(self):
        config = self._config(
            sources=(
                ArchiveSource(
                    mirror="http://127.0.0.1:1",
                    release="bookworm",
                    components=("main",),
                ),
            ),
        )
        text = graybeard_missing_components_warning(config)
        self.assertIsNotNone(text)
        self.assertIn(
            "main, contrib, non-free, non-free-firmware", self._missing_part(text)
        )

    def test_real_project_config_respected(self):
        cfg = ProjectConfig(fetch_source=True, max_source_dep_depth=-1)
        self.assertIsNotNone(graybeard_missing_components_warning(cfg))
        complete = ProjectConfig(
            fetch_source=True,
            max_source_dep_depth=-1,
            sources=(
                ArchiveSource(
                    mirror="https://deb.debian.org/debian",
                    release="trixie",
                    components=("main", "contrib", "non-free", "non-free-firmware"),
                ),
            ),
        )
        self.assertIsNone(graybeard_missing_components_warning(complete))


class TestProfileFilesAreData(unittest.TestCase):
    """The three profile presets under `docs/profiles/` are
    plain TOML data files — complete, standalone project settings with no code
    and no class/profile-name-keyed behavior."""

    PROFILES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "profiles")

    def test_all_three_profile_files_exist(self):
        for name in ("regular_user", "developer", "graybeard"):
            self.assertTrue(
                os.path.isfile(os.path.join(self.PROFILES_DIR, f"{name}.toml")),
                f"missing {name}.toml under docs/profiles/",
            )

    def test_each_profile_is_valid_complete_toml_data(self):
        for name in ("regular_user", "developer", "graybeard"):
            with self.subTest(profile=name):
                path = os.path.join(self.PROFILES_DIR, f"{name}.toml")
                with open(path, "rb") as f:
                    data = tomllib.load(f)
                # Complete standalone project setting (loads as defaults):
                for section in ("paths", "database", "logging", "archive", "fetch", "resolution"):
                    self.assertIn(section, data, f"{name} missing [{section}]")
                self.assertIn("base_dir", data["paths"])
                self.assertIn("path", data["database"])
                self.assertIn("source", data["archive"])
                self.assertIn("source", data["fetch"])
                self.assertIn("max_source_dep_depth", data["resolution"])
                self.assertIn("max_build_dep_report_depth", data["resolution"])
                # Plain data — no code constructs.
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                for token in ("def ", "class ", "import ", "lambda", "):", "{{"):
                    self.assertNotIn(token, text, f"{name} contains code token {token!r}")

    def test_profile_distinguishing_settings(self):
        expected = {
            "regular_user": {"fetch": {"source": False}, "resolution": {"max_source_dep_depth": 0}},
            "developer": {"fetch": {"source": True}, "resolution": {"max_source_dep_depth": 1}},
            "graybeard": {"fetch": {"source": True}, "resolution": {"max_source_dep_depth": -1}},
        }
        for name, exp in expected.items():
            with self.subTest(profile=name):
                path = os.path.join(self.PROFILES_DIR, f"{name}.toml")
                with open(path, "rb") as f:
                    data = tomllib.load(f)
                self.assertIs(data["fetch"]["source"], exp["fetch"]["source"])
                self.assertEqual(
                    data["resolution"]["max_source_dep_depth"],
                    exp["resolution"]["max_source_dep_depth"],
                )

    def test_each_profile_loads_as_a_valid_project_config(self):
        for name in ("regular_user", "developer", "graybeard"):
            with self.subTest(profile=name):
                with tempfile.TemporaryDirectory() as tmp:
                    shutil.copy(
                        os.path.join(self.PROFILES_DIR, f"{name}.toml"),
                        os.path.join(tmp, "project.defaults.toml"),
                    )
                    config, _ = load_config(tmp)
                    self.assertIsInstance(config, ProjectConfig)
                    self.assertIsInstance(config.max_source_dep_depth, int)

    def test_grow_deeper_direction_never_mentions_graybeard(self):
        # The supported grow-deeper direction is regular_user -> developer;
        # graybeard is a stand-alone, fresh-cache-only profile and must not be
        # part of any profile's grow-deeper chain (a line naming graybeard
        # alongside the "->" transition arrow).
        for name in ("regular_user", "developer", "graybeard"):
            with self.subTest(profile=name):
                path = os.path.join(self.PROFILES_DIR, f"{name}.toml")
                with open(path, encoding="utf-8") as f:
                    text = f.read().lower()
                chain_lines = [
                    line for line in text.splitlines()
                    if "graybeard" in line and "->" in line
                ]
                self.assertEqual(
                    chain_lines, [],
                    f"{name} must not name graybeard in a grow-deeper chain",
                )

    def test_graybeard_states_fresh_cache_only_standalone(self):
        path = os.path.join(self.PROFILES_DIR, "graybeard.toml")
        with open(path, encoding="utf-8") as f:
            text = f.read().lower()
        self.assertIn(
            "fresh", text,
            "graybeard must be documented as a fresh-cache-only profile",
        )
        self.assertIn(
            "not part of a grow-deeper chain", text,
            "graybeard must be documented as stand-alone (outside the grow-deeper chain)",
        )


class TestMirrorNormalization(unittest.TestCase):
    """Spec 012 security hardening: remote `http://` mirrors are upgraded to
    `https://` at load time; loopback mirrors keep `http://` (tests, local
    archives)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.dir, "project.defaults.toml"), "w") as f:
            f.write(content)

    def test_remote_http_mirror_upgraded_to_https(self):
        self._write_defaults("""[paths]
base_dir = "/tmp/cache"

[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie"
components = ["main"]
""")
        config, _ = load_config(self.dir)
        self.assertEqual(config.sources[0].mirror, "https://deb.debian.org/debian")

    def test_remote_http_mirror_with_path_upgraded_to_https(self):
        self._write_defaults("""[paths]
base_dir = "/tmp/cache"

[[archive.source]]
mirror = "http://mirror.example.com/debian"
release = "trixie"
components = ["main"]
""")
        config, _ = load_config(self.dir)
        self.assertEqual(config.sources[0].mirror, "https://mirror.example.com/debian")

    def test_https_mirror_left_unchanged(self):
        self._write_defaults("""[paths]
base_dir = "/tmp/cache"

[[archive.source]]
mirror = "https://deb.debian.org/debian"
release = "trixie"
components = ["main"]
""")
        config, _ = load_config(self.dir)
        self.assertEqual(config.sources[0].mirror, "https://deb.debian.org/debian")

    def test_loopback_http_mirror_kept(self):
        self._write_defaults("""[paths]
base_dir = "/tmp/cache"

[[archive.source]]
mirror = "http://127.0.0.1:8000"
release = "trixie"
components = ["main"]
""")
        config, _ = load_config(self.dir)
        self.assertEqual(config.sources[0].mirror, "http://127.0.0.1:8000")

    def test_localhost_http_mirror_kept(self):
        self._write_defaults("""[paths]
base_dir = "/tmp/cache"

[[archive.source]]
mirror = "http://localhost:8000"
release = "trixie"
components = ["main"]
""")
        config, _ = load_config(self.dir)
        self.assertEqual(config.sources[0].mirror, "http://localhost:8000")
