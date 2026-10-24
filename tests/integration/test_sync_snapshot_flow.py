import contextlib
import gzip
import io
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_iso  # noqa: E402

from package_mgmt.cli.commands.add import handle_add  # noqa: E402
from package_mgmt.cli.commands.init import handle_init  # noqa: E402
from package_mgmt.cli.commands.snapshot import handle_snapshot  # noqa: E402
from package_mgmt.cli.commands.sync import handle_sync  # noqa: E402
from package_mgmt.cli.commands.systems import handle_systems_add  # noqa: E402
from package_mgmt.lib.service.config_loader import load_config  # noqa: E402
from package_mgmt.lib.util.errors import EXIT_SUCCESS  # noqa: E402


class SyncSnapshotFlowTest(unittest.TestCase):
    """The two-points-in-time delta flow. Downloads
    are simulated by setting verified = 1 directly (no command sets it yet)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "sync_flow")
        self._write_defaults(f'[paths]\nbase_dir = "{self.base}"\n[fetch]\nsource = true\n[resolution]\nmax_source_dep_depth = -1\n\n[[archive.source]]\nmirror = "http://127.0.0.1:1"\nrelease = "trixie"\ncomponents = ["main", "contrib"]\n')
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _write_defaults(self, content: str):
        with open(os.path.join(self.tmp.name, "project.defaults.toml"), "w") as f:
            f.write(content)

    def _provision(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_init(), EXIT_SUCCESS)
        make_test_iso.write_archive(self.base)

    def _db_path(self):
        return os.path.join(self.base, "package_mgmt.db")

    def _db(self):
        return sqlite3.connect(self._db_path())

    def _simulate_verify_all(self):
        conn = self._db()
        conn.execute("UPDATE binaries SET verified = 1")
        conn.commit()
        conn.close()

    def _count_current_verified(self):
        conn = self._db()
        n = conn.execute(
            "SELECT COUNT(*) FROM binaries WHERE status = 'current' AND verified = 1"
        ).fetchone()[0]
        conn.close()
        return n

    def _snapshot(self, system, dry_run=False):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_snapshot(self._db_path(), system, dry_run)
        return rc, out.getvalue(), err.getvalue()

    def _sync(self, config, system="bravo-main"):
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

    def test_unverified_binaries_never_qualify(self):
        self._provision()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_add(["vifm", "lib1"], None, None), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            handle_systems_add(self._db_path(), "bravo-main")
        rc, out, _ = self._snapshot("bravo-main", dry_run=True)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "0 pending for 'bravo-main'\n")

    def test_delta_between_two_points_in_time(self):
        self._provision()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_add(["vifm"], None, None), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            handle_systems_add(self._db_path(), "bravo-main")

        self._simulate_verify_all()
        t1_count = self._count_current_verified()
        self.assertGreater(t1_count, 0)

        rc, out, _ = self._snapshot("bravo-main", dry_run=True)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, f"{t1_count} pending for 'bravo-main'\n")

        rc, out, _ = self._snapshot("bravo-main")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, f"Marked {t1_count} binaries for 'bravo-main'\n")

        rc, out, _ = self._snapshot("bravo-main")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(out, "Marked 0 binaries for 'bravo-main'\n")

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_add(["app", "base-pkg"], None, None), EXIT_SUCCESS)
        self._simulate_verify_all()

        after_count = self._count_current_verified()
        self.assertGreater(after_count, t1_count)

        rc, out, _ = self._snapshot("bravo-main", dry_run=True)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(
            out, f"{after_count - t1_count} pending for 'bravo-main'\n"
        )

        rc, out, _ = self._snapshot("bravo-main")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(
            out, f"Marked {after_count - t1_count} binaries for 'bravo-main'\n"
        )

    def test_snapshot_is_atomic_and_matches_verified_gate(self):
        self._provision()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_add(["vifm"], None, None), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            handle_systems_add(self._db_path(), "bravo-main")
        self._simulate_verify_all()
        conn = self._db()
        conn.execute(
            "UPDATE binaries SET verified = 0 WHERE package_name = 'vifm'"
        )
        conn.commit()
        conn.close()
        rc, out, _ = self._snapshot("bravo-main", dry_run=True)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("pending for 'bravo-main'", out)
        count = int(out.split()[0])
        self.assertEqual(count, self._count_current_verified())

    def test_rerequested_superseded_version_is_markable(self):
        """After re-requesting a superseded version, exactly
        one current binary exists and it is included in the marked set."""
        self._provision()
        binary_dir = os.path.join(
            self.base, "binary", "dists", "trixie", "main", "binary-amd64"
        )
        packages_gz = make_test_iso._gzip_bytes(
            make_test_iso._index_text(
                make_test_iso.BASE_PACKAGES + [("lib1", "1.0-1", None, None)], "main"
            )
        )
        with open(os.path.join(binary_dir, "Packages.gz"), "wb") as f:
            f.write(packages_gz)

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_add(["lib1=1.0-1"], None, None), EXIT_SUCCESS)
            self.assertEqual(handle_add(["lib1=2.0-1"], None, None), EXIT_SUCCESS)
            self.assertEqual(handle_add(["lib1=1.0-1"], None, None), EXIT_SUCCESS)

        conn = self._db()
        current = conn.execute(
            "SELECT version FROM binaries"
            " WHERE package_name = 'lib1' AND status = 'current'"
        ).fetchall()
        conn.close()
        self.assertEqual(current, [("1.0-1",)])

        self._simulate_verify_all()
        with contextlib.redirect_stdout(io.StringIO()):
            handle_systems_add(self._db_path(), "bravo-main")

        rc, out, _ = self._snapshot("bravo-main", dry_run=True)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(
            int(out.split()[0]), self._count_current_verified(),
            "the re-requested version must be in the pending set",
        )
        rc, out, _ = self._snapshot("bravo-main")
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertEqual(
            out, f"Marked {self._count_current_verified()} binaries for 'bravo-main'\n"
        )
        conn = self._db()
        lib1_mask = conn.execute(
            "SELECT sync_mask FROM binaries"
            " WHERE package_name = 'lib1' AND status = 'current'"
        ).fetchone()[0]
        conn.close()
        self.assertNotEqual(lib1_mask & 2, 0, "the re-requested version must be marked")

    def test_failed_sync_leaves_delivery_unchanged_and_clean_rerun_ships(self):
        """A failed sync (pool copy failure) exits
        non-zero, leaves the delivery record unchanged (no pending record is
        marked), and produces a diff with no entry for the failed record; a
        repaired clean re-run advances exactly the shipped ids."""
        self._provision()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handle_add(["vifm", "lib1"], None, None), EXIT_SUCCESS)
        with contextlib.redirect_stdout(io.StringIO()):
            handle_systems_add(self._db_path(), "bravo-main")
        self._simulate_verify_all()
        # Sync needs the real pool payloads on disk; write them, then corrupt
        # vifm's so the pool copy fails (checksum mismatch in _copy_verified).
        make_test_iso.write_pool_files(self.base)
        conn = self._db()
        pending = sorted(
            r[0] for r in conn.execute(
                "SELECT package_name FROM binaries"
                " WHERE status = 'current' AND verified = 1"
            ).fetchall()
        )
        conn.close()
        self.assertGreater(len(pending), 0)
        self.assertIn("vifm", pending)
        vifm_pool = os.path.join(
            self.base, "binary", "pool", "main", "v", "vifm", "vifm_1.0-2_amd64.deb"
        )
        with open(vifm_pool, "wb") as f:
            f.write(b"corrupted payload")

        config = load_config()[0]
        rc, out, err = self._sync(config)
        self.assertNotEqual(rc, EXIT_SUCCESS, "failed sync must exit non-zero")
        self.assertIn("excluded", err, f"expected exclusion message, got: {err!r}")

        conn = self._db()
        marked = conn.execute(
            "SELECT COUNT(*) FROM binaries"
            " WHERE status = 'current' AND verified = 1 AND (sync_mask & 2) != 0"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(marked, 0, "a failed sync must not advance delivery state")
        self.assertNotIn("Package: vifm", self._read_packages(config))
        with open(os.path.join(config.binary_cache, "diff", "manifest.txt")) as f:
            manifest = f.read()
        self.assertNotIn("vifm_1.0-2_amd64.deb", manifest)

        with open(vifm_pool, "wb") as f:
            f.write(make_test_iso._pool_content("vifm_1.0-2_amd64.deb"))
        rc, out, err = self._sync(config)
        self.assertEqual(rc, EXIT_SUCCESS, err)
        conn = self._db()
        marked = sorted(
            r[0] for r in conn.execute(
                "SELECT package_name FROM binaries"
                " WHERE status = 'current' AND verified = 1 AND (sync_mask & 2) != 0"
            ).fetchall()
        )
        conn.close()
        self.assertEqual(marked, pending,
                         "the clean re-run must advance exactly the shipped ids")


if __name__ == "__main__":
    unittest.main()
