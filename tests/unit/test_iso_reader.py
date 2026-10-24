import gzip
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_iso  # noqa: E402

from package_mgmt.lib.service.iso_reader import IsoImage  # noqa: E402
from package_mgmt.lib.util.errors import IsoError  # noqa: E402

VIFM_DEB = "/pool/main/v/vifm/vifm_1.0-2_amd64.deb"
MAIN_INDEX = "/dists/trixie/main/binary-amd64/Packages.gz"


class IsoReaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.fixture = os.path.join(cls.tmp.name, "fx.iso")
        cls.minimal = os.path.join(cls.tmp.name, "fx-min.iso")
        cls.omit = os.path.join(cls.tmp.name, "fx-omit.iso")
        cls.no_pool = os.path.join(cls.tmp.name, "fx-nopool.iso")
        cls.corrupt = os.path.join(cls.tmp.name, "fx-corrupt.iso")
        cls.not_iso = os.path.join(cls.tmp.name, "not-iso.txt")
        make_test_iso.build_iso(cls.fixture)
        make_test_iso.build_iso(cls.minimal, count=3)
        make_test_iso.build_iso(cls.omit, omit=[VIFM_DEB.lstrip("/")])
        make_test_iso.build_iso(cls.no_pool, no_pool=True)
        make_test_iso.build_iso(cls.corrupt, corrupt_index=True)
        with open(cls.not_iso, "w") as f:
            f.write("not an iso image\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_discover_release_and_components(self):
        with IsoImage(self.fixture) as img:
            self.assertEqual(img.suite, "trixie")
            self.assertEqual(img.release, "trixie")
            self.assertEqual(img.components, ("contrib", "main"))

    def test_read_file_returns_index_bytes(self):
        with IsoImage(self.fixture) as img:
            data = img.read_file(MAIN_INDEX)
        text = gzip.decompress(data).decode("utf-8")
        self.assertIn("Package: vifm", text)
        self.assertIn("pool/main/v/vifm/vifm_1.0-2_amd64.deb", text)

    def test_read_missing_file_raises_iso_error(self):
        with IsoImage(self.fixture) as img:
            with self.assertRaises(IsoError):
                img.read_file("/dists/trixie/main/binary-amd64/Packages.xz")

    def test_file_paths_contains_pool_files_and_symlink(self):
        with IsoImage(self.fixture) as img:
            paths = img.file_paths()
        self.assertIn(VIFM_DEB, paths)
        self.assertIn("/dists/stable", paths)
        self.assertIn("/dists/trixie/main/binary-amd64/Packages.gz", paths)
        self.assertNotIn(".", paths)

    def test_omit_fixture_lacks_the_file(self):
        with IsoImage(self.omit) as img:
            paths = img.file_paths()
        self.assertNotIn(VIFM_DEB, paths)
        self.assertIn("/pool/main/l/lib1/lib1_2.0-1_amd64.deb", paths)

    def test_non_iso_file_raises_iso_error(self):
        with self.assertRaises(IsoError):
            IsoImage(self.not_iso)

    def test_missing_file_raises_iso_error(self):
        with self.assertRaises(IsoError):
            IsoImage(os.path.join(self.tmp.name, "does-not-exist.iso"))

    def test_no_pool_fixture_raises_iso_error(self):
        with self.assertRaises(IsoError):
            IsoImage(self.no_pool)

    def test_corrupt_index_is_read_as_bytes(self):
        with IsoImage(self.corrupt) as img:
            data = img.read_file(MAIN_INDEX)
        self.assertEqual(data, b"NOT A GZIP FILE")

    def test_close_is_idempotent(self):
        img = IsoImage(self.fixture)
        img.close()
        img.close()

    def test_context_manager_closes(self):
        img = IsoImage(self.fixture)
        with img:
            pass
        img.close()

    def test_path_property(self):
        with IsoImage(self.fixture) as img:
            self.assertEqual(img.path, self.fixture)


if __name__ == "__main__":
    unittest.main()
