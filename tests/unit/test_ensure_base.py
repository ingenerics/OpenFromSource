import dataclasses
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_iso  # noqa: E402

from package_mgmt.lib.model.config import ArchiveSource, ProjectConfig  # noqa: E402
from package_mgmt.lib.service.db_init import initialize_database  # noqa: E402
from package_mgmt.lib.service.ensure_base import (  # noqa: E402
    ISO_MIN_FULL_DVD_BINARIES,
    EnsureBaseStats,
    ensure_base,
    find_installer_image,
)
from package_mgmt.lib.util.errors import CacheNotEmptyError, IsoError  # noqa: E402

VIFM_DEB = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"


def _scratch_config(base: str) -> ProjectConfig:
    return ProjectConfig(
        base_dir=base,
        binary_cache=f"{base}/binary",
        source_cache=f"{base}/source",
        iso_dir=f"{base}/iso",
        db_path=f"{base}/package_mgmt.db",
        log_dir=f"{base}/logs",
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


class FindInstallerImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.iso_dir = os.path.join(self.tmp.name, "iso")
        os.makedirs(self.iso_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self, name="fx.iso", **opts):
        path = os.path.join(self.iso_dir, name)
        make_test_iso.build_iso(path, **opts)
        return path

    def test_single_image_found(self):
        path = self._build()
        self.assertEqual(find_installer_image(self.iso_dir), path)

    def test_non_iso_files_ignored(self):
        path = self._build()
        with open(os.path.join(self.iso_dir, "README.md"), "w") as f:
            f.write("readme")
        with open(os.path.join(self.iso_dir, "sha256sum"), "w") as f:
            f.write("sum")
        self.assertEqual(find_installer_image(self.iso_dir), path)

    def test_missing_dir_raises(self):
        with self.assertRaises(IsoError):
            find_installer_image(os.path.join(self.tmp.name, "nope"))

    def test_empty_dir_raises(self):
        with self.assertRaises(IsoError):
            find_installer_image(self.iso_dir)

    def test_multiple_images_error_names_all_files(self):
        self._build("a.iso")
        self._build("b.iso")
        with self.assertRaises(IsoError) as ctx:
            find_installer_image(self.iso_dir)
        message = str(ctx.exception)
        self.assertIn("a.iso", message)
        self.assertIn("b.iso", message)


class EnsureBaseIngestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        os.makedirs(os.path.join(self.base, "iso"))
        self.config = _scratch_config(self.base)
        make_test_iso.write_archive(self.base)
        initialize_database(self.config.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self, name="fx.iso", **opts):
        path = os.path.join(self.config.iso_dir, name)
        make_test_iso.build_iso(path, **opts)
        return path

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

    def test_registers_binaries_with_provenance_and_verified_zero(self):
        self._build()
        stats = ensure_base(self.config)
        self.assertEqual(stats.binaries, 6)
        self.assertEqual(stats.provides, 1)
        self.assertEqual(stats.release, "trixie")
        self.assertEqual(stats.components, ("contrib", "main"))

        conn = sqlite3.connect(self.config.db_path)
        try:
            rows = conn.execute(
                "SELECT package_name, version, release, component, verified, status"
                " FROM binaries ORDER BY package_name"
            ).fetchall()
            self.assertEqual(len(rows), 6)
            self.assertTrue(all(r[4] == 0 for r in rows), "verified must stay 0")
            self.assertTrue(all(r[3] in ("main", "contrib") for r in rows))
            self.assertTrue(all(r[2] == "trixie" for r in rows))
            self.assertEqual(conn.execute("SELECT count(*) FROM packages").fetchone()[0], 6)
            self.assertEqual(
                conn.execute("SELECT count(*) FROM provides").fetchone()[0], 1
            )
            deps = conn.execute(
                "SELECT dependencies FROM binaries WHERE package_name = 'vifm'"
            ).fetchone()[0]
            self.assertIn("lib1", deps)
        finally:
            conn.close()

    def test_minimal_image_triggers_netinst_warning(self):
        self._build("mini.iso", count=3)
        stats = ensure_base(self.config)
        self.assertEqual(stats.binaries, 4)
        self.assertGreaterEqual(len(stats.warnings), 2)
        self.assertIn("graybeard configuration", stats.warnings[0])
        self.assertIn("netinst or minimal", stats.warnings[1])

    def test_full_size_image_has_no_warning(self):
        self._build("full.iso", count=ISO_MIN_FULL_DVD_BINARIES + 1)
        make_test_iso.write_archive(self.base, count=ISO_MIN_FULL_DVD_BINARIES + 1)
        stats = ensure_base(self.config)
        self.assertGreater(stats.binaries, ISO_MIN_FULL_DVD_BINARIES)
        non_graybeard = [
            w for w in stats.warnings if "graybeard configuration" not in w
        ]
        self.assertEqual(non_graybeard, [])

    def test_resolution_creates_sources_and_links(self):
        self._build()
        stats = ensure_base(self.config)
        self.assertEqual(stats.resolved, 6)
        self.assertEqual(stats.total, 6)
        self.assertEqual(stats.errors, [])
        conn = sqlite3.connect(self.config.db_path)
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM sources").fetchone()[0], 6)
            # NOTE: binary_to_source has no UNIQUE constraint (pre-existing schema);
            # shared deps are re-linked by the resolver — assert distinct pairs.
            self.assertEqual(
                conn.execute(
                    "SELECT count(DISTINCT binary_id || '-' || source_id)"
                    " FROM binary_to_source"
                ).fetchone()[0],
                6,
            )
            self.assertEqual(
                conn.execute("SELECT count(*) FROM binaries WHERE verified = 1").fetchone()[0],
                0,
                "resolution must never set verified flags",
            )
            deps = conn.execute(
                "SELECT count(*) FROM binaries WHERE package_name IN ('lib1', 'lib2')"
            ).fetchone()[0]
            self.assertEqual(deps, 2, "vifm's dependencies must be resolved")
        finally:
            conn.close()

    def test_unresolvable_names_produce_errors(self):
        self._build("extra.iso", count=6)
        make_test_iso.write_archive(self.base, count=5)
        stats = ensure_base(self.config)
        self.assertGreater(len(stats.errors), 0)
        self.assertLess(stats.resolved, stats.total)
        self.assertTrue(any("pkg0001" in e for e in stats.errors))

    def test_rerun_refused_on_non_empty_cache(self):
        """ensure-base is fresh-cache only: a re-run on a populated cache is refused."""
        self._build()
        stats = ensure_base(self.config)
        self.assertEqual(stats.binaries, 6)
        with self.assertRaises(CacheNotEmptyError) as ctx:
            ensure_base(self.config)
        self.assertIn("fresh cache", str(ctx.exception))
        conn = sqlite3.connect(self.config.db_path)
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 6)
        finally:
            conn.close()

    def test_summary_caps_console_errors(self):
        import io
        import contextlib

        from package_mgmt.cli.commands.ensure_base import _print_summary, CONSOLE_ERROR_CAP

        errors = [f"error {i}" for i in range(CONSOLE_ERROR_CAP + 11)]
        stats = EnsureBaseStats(
            image_path="/x/fx.iso", release="trixie", components=("main",),
            resolved=0, total=61, errors=errors,
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _print_summary(stats)
        output = buf.getvalue()
        self.assertIn(f"(showing first {CONSOLE_ERROR_CAP}; 11 more in the log)", output)
        self.assertEqual(output.count("  - error "), CONSOLE_ERROR_CAP)

    def test_corrupt_index_raises_and_leaves_db_untouched(self):
        self._build("corrupt.iso", corrupt_index=True)
        with self.assertRaises(IsoError):
            ensure_base(self.config)
        conn = sqlite3.connect(self.config.db_path)
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM packages").fetchone()[0], 0)
        finally:
            conn.close()

    def test_omit_fixture_reports_mismatch_without_failing(self):
        self._build("omit.iso", omit=[VIFM_DEB.lstrip("/")])
        stats = ensure_base(self.config)
        self.assertEqual(stats.mismatches, 1)
        self.assertEqual(stats.mismatch_paths, (VIFM_DEB.lstrip("/"),))
        self.assertEqual(stats.binaries, 6, "record still registered")
        conn = sqlite3.connect(self.config.db_path)
        try:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM binaries WHERE verified = 1").fetchone()[0],
                0,
                "verified flags must never change",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM binaries WHERE package_name = 'vifm'"
                ).fetchone()[0],
                1,
            )
        finally:
            conn.close()

    def test_complete_fixture_has_no_mismatches(self):
        self._build()
        stats = ensure_base(self.config)
        self.assertEqual(stats.mismatches, 0)
        self.assertEqual(stats.mismatch_paths, ())

    def test_graybeard_missing_components_warning_is_first_warning(self):
        self._build()
        stats = ensure_base(self.config)
        self.assertGreaterEqual(len(stats.warnings), 1)
        self.assertIn("graybeard configuration", stats.warnings[0])
        self.assertIn("source = true", stats.warnings[0])
        self.assertIn("max_source_dep_depth = -1", stats.warnings[0])
        self.assertIn("non-free, non-free-firmware", stats.warnings[0])

    def test_complete_graybeard_config_has_no_component_warning(self):
        self._add_component_indexes("non-free", "non-free-firmware")
        self.config = dataclasses.replace(
            self.config,
            sources=(
                ArchiveSource(
                    mirror="http://127.0.0.1:1",
                    release="trixie",
                    components=("main", "contrib", "non-free", "non-free-firmware"),
                ),
            ),
        )
        self._build()
        stats = ensure_base(self.config)
        self.assertFalse(
            any("graybeard configuration" in w for w in stats.warnings)
        )

    def test_non_graybeard_config_has_no_component_warning(self):
        self.config = dataclasses.replace(
            self.config,
            fetch_source=False,
            max_source_dep_depth=0,
        )
        self._build()
        stats = ensure_base(self.config)
        self.assertFalse(
            any("graybeard configuration" in w for w in stats.warnings)
        )

    def test_stale_temp_dirs_cleaned_and_none_left_after_run(self):
        self._build()
        stale = os.path.join(self.base, "ensure-base-crashed")
        os.makedirs(os.path.join(stale, "sub"))
        with open(os.path.join(stale, "x.gz"), "w") as f:
            f.write("leftover")
        ensure_base(self.config)
        self.assertFalse(os.path.exists(stale), "stale temp dirs must be cleaned up")
        leftovers = [
            name for name in os.listdir(self.base) if name.startswith("ensure-base-")
        ]
        self.assertEqual(leftovers, [], "no temp dirs may remain after a run")


if __name__ == "__main__":
    unittest.main()
