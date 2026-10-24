import io
import contextlib
import os
import sqlite3
import tempfile
import unittest

from package_mgmt.lib.service.config_loader import load_config
from package_mgmt.lib.util.errors import ConfigError


class TestMultiSourceIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "multi_test")
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def test_config_loads_single_source_by_default(self):
        self._write_defaults('[paths]\nbase_dir = "/tmp/multi_test"\n')
        config, _ = load_config(".")
        self.assertEqual(len(config.sources), 1)
        self.assertEqual(config.sources[0].release, "trixie")
        self.assertEqual(config.sources[0].components, ("main",))

    def test_config_loads_multi_source(self):
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
        config, _ = load_config(".")
        self.assertEqual(len(config.sources), 2)
        self.assertEqual(config.sources[0].release, "trixie")
        self.assertEqual(config.sources[0].components, ("main", "contrib"))
        self.assertEqual(config.sources[1].release, "trixie-backports")

    def test_duplicate_source_rejected(self):
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
            load_config(".")

    def test_init_creates_multi_source_directory_structure(self):
        self._write_defaults(f"""[paths]
base_dir = "{self.base}"
[fetch]
source = false

[[archive.source]]
mirror = "http://127.0.0.1:1"
release = "trixie"
components = ["main", "contrib"]

[[archive.source]]
mirror = "http://127.0.0.1:1"
release = "trixie-backports"
components = ["main"]
""")
        from package_mgmt.cli.commands.init import handle_init
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_init()
        self.assertEqual(rc, 0)

        self.assertTrue(os.path.isdir(os.path.join(self.base, "binary", "dists", "trixie", "main", "binary-amd64")))
        self.assertTrue(os.path.isdir(os.path.join(self.base, "binary", "dists", "trixie", "contrib", "binary-amd64")))
        self.assertTrue(os.path.isdir(os.path.join(self.base, "binary", "dists", "trixie-backports", "main", "binary-amd64")))
        self.assertTrue(os.path.isdir(os.path.join(self.base, "binary", "pool", "main")))
        self.assertTrue(os.path.isdir(os.path.join(self.base, "binary", "pool", "contrib")))
        self.assertTrue(os.path.isfile(os.path.join(self.base, "package_mgmt.db")))

        conn = sqlite3.connect(os.path.join(self.base, "package_mgmt.db"))
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(binaries)")
        cols = [r[1] for r in cursor.fetchall()]
        self.assertIn("release", cols)
        self.assertIn("component", cols)
        self.assertIn("status", cols)
        conn.close()

    def test_config_output_shows_multi_source(self):
        self._write_defaults("""[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie"
components = ["main", "contrib"]
""")
        from package_mgmt.cli.commands.config import handle_config
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_config()
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("Archive Sources", output)
        self.assertIn("trixie", output)
        self.assertIn("main, contrib", output)
