import os
import unittest

from package_mgmt.lib.util.paths import (
    binary_index_path,
    diff_dir,
    diff_index_path,
    diff_key_path,
    diff_manifest_path,
    diff_pool_path,
    diff_release_dir,
    diff_script_path,
    diff_signature_path,
    handoff_dir,
    handoff_gnupg_dir,
    handoff_index_path,
    handoff_key_path,
    handoff_pool_link,
    handoff_release_dir,
    handoff_script_path,
    handoff_signature_path,
    safe_pool_path,
    validate_absolute,
)


class TestValidateAbsolute(unittest.TestCase):
    """A relative path is rejected (never silently reinterpreted against the
    current working directory) so the config loader can name the key; absolute
    paths pass through unchanged."""

    def test_relative_path_rejected(self):
        with self.assertRaises(ValueError):
            validate_absolute("relative/path")

    def test_absolute_path_unchanged(self):
        self.assertEqual(validate_absolute("/tmp/cache"), "/tmp/cache")

    def test_absolute_with_dot_segments_normalized(self):
        self.assertEqual(
            validate_absolute("/tmp/../tmp/cache"), "/tmp/cache"
        )

    def test_tilde_expanded_then_validated(self):
        self.assertEqual(validate_absolute("~/x"), os.path.join(os.path.expanduser("~"), "x"))


class TestSafePoolPath(unittest.TestCase):
    """Spec 012 security hardening: index Filename/Directory values must never
    escape the cache pool (no `..`, no absolute paths, pool/-only)."""

    def setUp(self):
        self.cache = "/tmp/cache"

    def test_valid_pool_path_joined(self):
        self.assertEqual(
            safe_pool_path(self.cache, "pool/main/v/vifm/vifm_1.0-2_amd64.deb"),
            "/tmp/cache/pool/main/v/vifm/vifm_1.0-2_amd64.deb",
        )

    def test_pool_root_allowed(self):
        self.assertEqual(safe_pool_path(self.cache, "pool"), "/tmp/cache/pool")

    def test_dot_segments_normalized(self):
        self.assertEqual(
            safe_pool_path(self.cache, "pool/./main/../main/a.deb"),
            "/tmp/cache/pool/main/a.deb",
        )

    def test_parent_traversal_rejected(self):
        self.assertIsNone(safe_pool_path(self.cache, "../../etc/passwd"))

    def test_nested_parent_traversal_rejected(self):
        self.assertIsNone(safe_pool_path(self.cache, "pool/../../etc/passwd"))

    def test_absolute_path_rejected(self):
        self.assertIsNone(safe_pool_path(self.cache, "/etc/passwd"))

    def test_absolute_within_pool_rejected(self):
        self.assertIsNone(safe_pool_path(self.cache, "/tmp/cache/pool/a.deb"))

    def test_empty_path_rejected(self):
        self.assertIsNone(safe_pool_path(self.cache, ""))

    def test_path_outside_pool_rejected(self):
        self.assertIsNone(safe_pool_path(self.cache, "dists/trixie/Release"))

    def test_bare_dotdot_rejected(self):
        self.assertIsNone(safe_pool_path(self.cache, ".."))


class TestHandoffPaths(unittest.TestCase):
    def setUp(self):
        self.cache = "/tmp/cache"

    def test_handoff_dir_under_binary_cache(self):
        self.assertEqual(handoff_dir(self.cache), "/tmp/cache/handoff")

    def test_handoff_release_dir(self):
        self.assertEqual(
            handoff_release_dir(self.cache, "trixie"),
            "/tmp/cache/handoff/dists/trixie",
        )

    def test_handoff_index_path_matches_data_model_layout(self):
        self.assertEqual(
            handoff_index_path(self.cache, "trixie", "main"),
            "/tmp/cache/handoff/dists/trixie/main/binary-amd64/Packages.gz",
        )

    def test_handoff_index_path_arch_param(self):
        self.assertEqual(
            handoff_index_path(self.cache, "trixie", "contrib", arch="arm64"),
            "/tmp/cache/handoff/dists/trixie/contrib/binary-arm64/Packages.gz",
        )

    def test_handoff_key_and_script_paths(self):
        self.assertEqual(handoff_key_path(self.cache), "/tmp/cache/handoff/offline-cache.gpg")
        self.assertEqual(handoff_script_path(self.cache), "/tmp/cache/handoff/bootstrap.sh")

    def test_handoff_gnupg_dir_is_base_dir_scoped_outside_payload(self):
        binary_cache = "/var/cache/package_mgmt/binary"
        self.assertEqual(
            handoff_gnupg_dir(binary_cache),
            "/var/cache/package_mgmt/.gnupg-handoff",
        )
        gnupg = handoff_gnupg_dir(binary_cache)
        self.assertFalse(gnupg.startswith(handoff_dir(binary_cache)))
        self.assertFalse(gnupg.startswith(binary_cache + os.sep))

    def test_handoff_signature_path(self):
        self.assertEqual(
            handoff_signature_path(self.cache, "trixie"),
            "/tmp/cache/handoff/dists/trixie/Release.gpg",
        )

    def test_handoff_pool_link(self):
        self.assertEqual(handoff_pool_link(self.cache), "/tmp/cache/handoff/pool")

    def test_distinct_from_archive_index_paths(self):
        # The handoff tree must never collide with the cache's own archive
        # indexes (spec 010/011 index-read design keeps those untouched).
        self.assertNotEqual(
            handoff_index_path(self.cache, "trixie", "main"),
            binary_index_path(self.cache, "trixie", "main"),
        )
        self.assertTrue(
            handoff_index_path(self.cache, "trixie", "main").startswith(
                os.path.join(handoff_dir(self.cache), "dists")
            )
        )


class TestDiffPaths(unittest.TestCase):
    """Spec 013: the diff cache layout (data-model.md) mirrors the handoff but
    lives in its own subtree under `{binary_cache}/diff/`."""

    def setUp(self):
        self.cache = "/tmp/cache"

    def test_diff_dir_under_binary_cache(self):
        self.assertEqual(diff_dir(self.cache), "/tmp/cache/diff")

    def test_diff_release_dir(self):
        self.assertEqual(
            diff_release_dir(self.cache, "trixie"),
            "/tmp/cache/diff/dists/trixie",
        )

    def test_diff_index_path_matches_data_model_layout(self):
        self.assertEqual(
            diff_index_path(self.cache, "trixie", "main"),
            "/tmp/cache/diff/dists/trixie/main/binary-amd64/Packages.gz",
        )

    def test_diff_index_path_arch_param(self):
        self.assertEqual(
            diff_index_path(self.cache, "trixie", "contrib", arch="arm64"),
            "/tmp/cache/diff/dists/trixie/contrib/binary-arm64/Packages.gz",
        )

    def test_diff_manifest_and_script_paths(self):
        self.assertEqual(diff_manifest_path(self.cache), "/tmp/cache/diff/manifest.txt")
        self.assertEqual(diff_script_path(self.cache), "/tmp/cache/diff/apply.sh")

    def test_diff_key_and_signature_paths(self):
        self.assertEqual(diff_key_path(self.cache), "/tmp/cache/diff/offline-cache.gpg")
        self.assertEqual(
            diff_signature_path(self.cache, "trixie"),
            "/tmp/cache/diff/dists/trixie/Release.gpg",
        )

    def test_diff_pool_path(self):
        self.assertEqual(diff_pool_path(self.cache), "/tmp/cache/diff/pool")

    def test_distinct_from_handoff_and_archive_paths(self):
        # The diff tree must never collide with the handoff or the cache's own
        # archive indexes (spec 010/011 index-read design keeps those untouched).
        self.assertNotEqual(diff_dir(self.cache), handoff_dir(self.cache))
        self.assertNotEqual(diff_index_path(self.cache, "trixie", "main"), handoff_index_path(self.cache, "trixie", "main"))
        self.assertNotEqual(diff_index_path(self.cache, "trixie", "main"), binary_index_path(self.cache, "trixie", "main"))
        self.assertTrue(
            diff_index_path(self.cache, "trixie", "main").startswith(
                os.path.join(diff_dir(self.cache), "dists")
            )
        )


if __name__ == "__main__":
    unittest.main()
