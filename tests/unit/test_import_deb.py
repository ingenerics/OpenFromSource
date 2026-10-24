import contextlib
import gzip
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_deb  # noqa: E402
import make_test_iso  # noqa: E402
from db import config, insert_binary  # noqa: E402

from package_mgmt.lib.service.db_init import initialize_database  # noqa: E402
from package_mgmt.lib.service.downloader import download_pending_sources, pending_binaries  # noqa: E402
from package_mgmt.lib.service.handoff import generate_diff, generate_handoff  # noqa: E402
from package_mgmt.lib.service.import_local import (  # noqa: E402
    ImportDebError,
    import_local_deb,
)
from package_mgmt.lib.util.paths import diff_index_path, handoff_index_path  # noqa: E402
from package_mgmt.cli.commands.list import handle_list_deps  # noqa: E402
from package_mgmt.cli.commands.show import handle_show  # noqa: E402
from package_mgmt.cli.commands.verify import handle_verify_graph  # noqa: E402


class TestImportDebParse(unittest.TestCase):
    """The parsed control yields the exact record fields for the DB row."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        os.makedirs(self.base, exist_ok=True)
        self.config = config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.conn.execute("INSERT INTO systems (name, bit) VALUES ('prod-box', 2)")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_control_parse_produces_expected_record(self):
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        deb = os.path.join(self.tmp.name, "libfoo_1.2-3_amd64.deb")
        make_test_deb.build_deb(
            deb, package="libfoo", version="1.2-3",
            depends="lib1 (>= 2.0)", provides="virtual-foo",
        )
        result = import_local_deb(self.conn, self.config, deb)
        self.assertEqual(result.package, "libfoo")
        self.assertEqual(result.version, "1.2-3")
        self.assertEqual(result.architecture, "amd64")

        row = self.conn.execute(
            "SELECT package_name, version, architecture, release, component, dependencies,"
            " status, verified FROM binaries WHERE package_name = 'libfoo'"
        ).fetchone()
        self.assertEqual(
            row,
            (
                "libfoo", "1.2-3", "amd64", "trixie", "local",
                json.dumps({
                    "dependencies": [
                        {
                            "name": "lib1",
                            "type": "depends",
                            "version_constraint": {"operator": ">=", "version": "2.0"},
                        }
                    ],
                    "version_hash": {"Depends": "lib1 (>= 2.0)"},
                }),
                "current", 1,
            ),
        )

    def test_pre_depends_parsed_into_record(self):
        insert_binary(self.conn, "lib1", "2.0-1")
        insert_binary(self.conn, "pre-lib", "1.0-1")
        self.conn.commit()
        deb = os.path.join(self.tmp.name, "app_1.0_amd64.deb")
        make_test_deb.build_deb(
            deb, package="app", version="1.0",
            depends="lib1", pre_depends="pre-lib",
        )
        import_local_deb(self.conn, self.config, deb)
        row = self.conn.execute(
            "SELECT dependencies FROM binaries WHERE package_name = 'app'"
        ).fetchone()
        data = json.loads(row[0])
        names = sorted(s["name"] for s in data["dependencies"])
        self.assertEqual(names, ["lib1", "pre-lib"])


class TestImportDebRegistration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        os.makedirs(self.base, exist_ok=True)
        self.config = config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.conn.execute("INSERT INTO systems (name, bit) VALUES ('prod-box', 2)")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_satisfiable_deps_registers_local_verified_and_copies_payload(self):
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        deb = os.path.join(self.tmp.name, "libfoo_1.2-3_amd64.deb")
        make_test_deb.build_deb(deb, package="libfoo", version="1.2-3", depends="lib1")

        result = import_local_deb(self.conn, self.config, deb)

        self.assertEqual(result.package, "libfoo")
        row = self.conn.execute(
            "SELECT component, verified, status FROM binaries WHERE package_name = 'libfoo'"
        ).fetchone()
        self.assertEqual(row, ("local", 1, "current"))
        dest = make_test_deb.pool_abs_path(self.base, "libfoo", "1.2-3")
        self.assertTrue(os.path.isfile(dest), f"payload not at {dest}")
        with open(deb, "rb") as f:
            src_digest = make_test_deb.sha256(f.read())
        with open(dest, "rb") as f:
            self.assertEqual(src_digest, make_test_deb.sha256(f.read()))

    def test_copied_payload_mode_matches_downloader_convention(self):
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        deb = os.path.join(self.tmp.name, "libfoo_1.2-3_amd64.deb")
        make_test_deb.build_deb(deb, package="libfoo", version="1.2-3", depends="lib1")

        import_local_deb(self.conn, self.config, deb)
        dest = make_test_deb.pool_abs_path(self.base, "libfoo", "1.2-3")
        self.assertEqual(
            os.stat(dest).st_mode & 0o777, 0o644,
            "the copied local payload must be world-readable (0644), not the "
            "mkstemp 0600 — the downloader's pool files are 0644",
        )

    def test_unsatisfiable_dependency_atomic_failure_zero_changes(self):
        self.conn.commit()
        deb = os.path.join(self.tmp.name, "libfoo_1.2-3_amd64.deb")
        make_test_deb.build_deb(deb, package="libfoo", version="1.2-3", depends="ghost (>= 2.0)")

        with self.assertRaises(ImportDebError) as ctx:
            import_local_deb(self.conn, self.config, deb)
        message = str(ctx.exception)
        self.assertIn("unsatisfiable dependency", message)
        self.assertIn("ghost", message)

        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0,
            "no binary rows may be registered on an unsatisfiable import",
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM packages").fetchone()[0], 0,
            "no package rows may be registered on an unsatisfiable import",
        )
        pool_files = []
        for root, _, names in os.walk(os.path.join(self.base, "binary", "pool")):
            pool_files.extend(names)
        self.assertEqual(pool_files, [], "no pool files may be written on a failed import")

    def test_unsatisfiable_pre_depends_atomic_failure(self):
        self.conn.commit()
        deb = os.path.join(self.tmp.name, "app_1.0_amd64.deb")
        make_test_deb.build_deb(deb, package="app", version="1.0", pre_depends="ghost")

        with self.assertRaises(ImportDebError):
            import_local_deb(self.conn, self.config, deb)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0
        )

    def test_reimport_is_idempotent_no_duplicate_rows(self):
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        deb = os.path.join(self.tmp.name, "libfoo_1.2-3_amd64.deb")
        make_test_deb.build_deb(deb, package="libfoo", version="1.2-3", depends="lib1")

        first = import_local_deb(self.conn, self.config, deb)
        second = import_local_deb(self.conn, self.config, deb)

        self.assertEqual(first.package, second.package)
        rows = self.conn.execute(
            "SELECT package_name, version, architecture, release, component FROM binaries"
            " WHERE package_name = 'libfoo'"
        ).fetchall()
        self.assertEqual(
            rows,
            [("libfoo", "1.2-3", "amd64", "trixie", "local")],
            "re-import must not create duplicate rows",
        )
        dest = make_test_deb.pool_abs_path(self.base, "libfoo", "1.2-3")
        self.assertTrue(os.path.isfile(dest))

    def test_invalid_file_clear_error_zero_changes(self):
        self.conn.commit()
        bad = os.path.join(self.tmp.name, "not-a-deb.deb")
        with open(bad, "w") as f:
            f.write("this is not an ar archive")

        with self.assertRaises(ImportDebError) as ctx:
            import_local_deb(self.conn, self.config, bad)
        self.assertIn("not-a-deb.deb", str(ctx.exception))
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0
        )
        self.assertFalse(os.path.exists(
            os.path.join(self.base, "binary", "pool", "local")
        ), "no local pool tree may be created on a failed import")

    def test_missing_file_clear_error(self):
        self.conn.commit()
        missing = os.path.join(self.tmp.name, "missing.deb")
        with self.assertRaises(ImportDebError) as ctx:
            import_local_deb(self.conn, self.config, missing)
        self.assertIn("missing.deb", str(ctx.exception))
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0
        )

    def test_corrupt_deb_clear_error_zero_changes(self):
        self.conn.commit()
        deb = os.path.join(self.tmp.name, "libfoo_1.2-3_amd64.deb")
        make_test_deb.build_deb(deb, package="libfoo", version="1.2-3", depends="lib1")
        with open(deb, "r+b") as f:
            f.seek(20)
            f.write(b"\x00" * 8)
        with self.assertRaises(ImportDebError):
            import_local_deb(self.conn, self.config, deb)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0
        )


class TestImportDebDependencyAlternatives(unittest.TestCase):
    """Alternative groups (`a | b`) satisfy when any member is in the cache."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        os.makedirs(self.base, exist_ok=True)
        self.config = config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_alternative_group_satisfied_by_one_member(self):
        insert_binary(self.conn, "b", "1.0-1")
        self.conn.commit()
        deb = os.path.join(self.tmp.name, "app_1.0_amd64.deb")
        make_test_deb.build_deb(deb, package="app", version="1.0", depends="a | b")
        result = import_local_deb(self.conn, self.config, deb)
        self.assertEqual(result.package, "app")

    def test_alternative_group_unsatisfied_when_no_member(self):
        self.conn.commit()
        deb = os.path.join(self.tmp.name, "app_1.0_amd64.deb")
        make_test_deb.build_deb(deb, package="app", version="1.0", depends="a | b")
        with self.assertRaises(ImportDebError) as ctx:
            import_local_deb(self.conn, self.config, deb)
        self.assertIn("a | b", str(ctx.exception))
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM binaries").fetchone()[0], 0
        )


class TestImportDebProvides(unittest.TestCase):
    """A declared dependency satisfied by a virtual provider in the cache."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        os.makedirs(self.base, exist_ok=True)
        self.config = config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_dependency_satisfied_via_provides(self):
        insert_binary(self.conn, "mail-transport", "1.0-1")
        self.conn.execute(
            "INSERT INTO provides (package_name, version, architecture, release, component,"
            " virtual_name, provides_version)"
            " VALUES ('mail-transport', '1.0-1', 'amd64', 'trixie', 'main', 'mailx', NULL)"
        )
        self.conn.commit()
        deb = os.path.join(self.tmp.name, "mutt_1.0_amd64.deb")
        make_test_deb.build_deb(deb, package="mutt", version="1.0", depends="mailx")
        result = import_local_deb(self.conn, self.config, deb)
        self.assertEqual(result.package, "mutt")


class TestImportDebFirstClassCitizen(unittest.TestCase):
    """A local import is a first-class cache citizen: `download`'s pending set
    ignores it (verified at import), and `list`/`show`/`verify` surface it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        os.makedirs(self.base, exist_ok=True)
        self.config = config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_download_pending_set_excludes_local_verified_row(self):
        insert_binary(self.conn, "lib1", "2.0-1", verified=0)
        self.conn.commit()
        deb = os.path.join(self.tmp.name, "libfoo_1.2-3_amd64.deb")
        make_test_deb.build_deb(deb, package="libfoo", version="1.2-3", depends="lib1")
        import_local_deb(self.conn, self.config, deb)
        pending = [r[1] for r in pending_binaries(self.conn)]
        self.assertIn("lib1", pending, "an unverified upstream record stays pending")
        self.assertNotIn("libfoo", pending, "a verified local record is never re-downloaded")

    def test_list_status_all_shows_local_row(self):
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        deb = os.path.join(self.tmp.name, "libfoo_1.2-3_amd64.deb")
        make_test_deb.build_deb(deb, package="libfoo", version="1.2-3", depends="lib1")
        import_local_deb(self.conn, self.config, deb)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_list_deps(self.config.db_path, None, fmt="json", status="all")
        self.assertEqual(rc, 0)
        rows = json.loads(buf.getvalue())
        local = next(r for r in rows if r["package"] == "libfoo")
        self.assertEqual(local["component"], "local")

    def test_show_displays_local_row(self):
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        deb = os.path.join(self.tmp.name, "libfoo_1.2-3_amd64.deb")
        make_test_deb.build_deb(deb, package="libfoo", version="1.2-3", depends="lib1")
        import_local_deb(self.conn, self.config, deb)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_show(self.config.db_path, "libfoo", fmt="json")
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        binary = next(b for b in data["binaries"] if b["package_name"] == "libfoo")
        self.assertEqual(binary["component"], "local")
        self.assertEqual(binary["verified"], 1)

    def test_verify_reports_complete_for_local_import(self):
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        deb = os.path.join(self.tmp.name, "libfoo_1.2-3_amd64.deb")
        make_test_deb.build_deb(deb, package="libfoo", version="1.2-3", depends="lib1")
        import_local_deb(self.conn, self.config, deb)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_verify_graph(
                self.config.db_path, None,
                self.config.max_build_dep_report_depth, self.config.max_source_dep_depth,
            )
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("Runtime Status:         COMPLETE", buf.getvalue())


class TestImportDebSource(unittest.TestCase):
    """`--source <dir>` imports the complete rebuilt source set: registered
    under `local`, verified, linked to the imported binary, and every
    referenced file present and checksum-valid against the `.dsc` — with an
    atomic, zero-change failure on any gap in the set."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        os.makedirs(self.base, exist_ok=True)
        self.config = config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.conn.execute("INSERT INTO systems (name, bit) VALUES ('prod-box', 2)")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _deb(self) -> str:
        deb = os.path.join(self.tmp.name, "patch-tool_1.2-3_amd64.deb")
        make_test_deb.build_deb(deb, package="patch-tool", version="1.2-3", depends="lib1")
        return deb

    def _source_set(self, missing: str | None = None, corrupt: str | None = None):
        """(dir, dsc_path, files) — the complete rebuilt source set. `missing`
        drops the file from the directory (the `.dsc` still references it);
        `corrupt` tampers with the file after the `.dsc` was written."""
        files = {
            "patch-tool_1.2.orig.tar.gz": b"orig tarball content",
            "patch-tool_1.2-3.debian.tar.xz": b"debian packaging content",
        }
        src_dir = os.path.join(self.tmp.name, "source-set")
        dsc = make_test_deb.build_dsc(src_dir, source="patch-tool", version="1.2-3", files=files)
        if missing:
            os.remove(os.path.join(src_dir, missing))
        if corrupt:
            with open(os.path.join(src_dir, corrupt), "wb") as f:
                f.write(b"tampered content")
        return src_dir, dsc, files

    def _source_pool_files(self):
        pool = os.path.join(self.base, "source", "pool", "local")
        return sorted(
            os.path.relpath(os.path.join(root, name), pool)
            for root, _, names in os.walk(pool) for name in names
        )

    def test_complete_source_set_registers_verified_and_links(self):
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        deb = self._deb()
        src_dir, _dsc, files = self._source_set()

        result = import_local_deb(self.conn, self.config, deb, source_dir=src_dir)

        self.assertIn("Source linked (patch-tool 1.2-3, local, 2 files verified against dsc)",
                      result.source_summary)
        row = self.conn.execute(
            "SELECT package_name, source_version, release, component, verified FROM sources"
        ).fetchone()
        self.assertEqual(row, ("patch-tool", "1.2-3", "trixie", "local", 1))
        link = self.conn.execute(
            "SELECT count(*) FROM binary_to_source"
        ).fetchone()[0]
        self.assertEqual(link, 1)
        expected = sorted(
            [f"p/patch-tool/{name}" for name in files]
            + ["p/patch-tool/patch-tool_1.2-3.dsc"]
        )
        self.assertEqual(self._source_pool_files(), expected)
        for name, content in files.items():
            dest = os.path.join(self.base, "source", "pool", "local", "p", "patch-tool", name)
            with open(dest, "rb") as f:
                self.assertEqual(make_test_deb.sha256(content), make_test_deb.sha256(f.read()))

    def test_missing_referenced_file_atomic_failure(self):
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        deb = self._deb()
        src_dir, _dsc, _files = self._source_set(missing="patch-tool_1.2-3.debian.tar.xz")

        with self.assertRaises(ImportDebError) as ctx:
            import_local_deb(self.conn, self.config, deb, source_dir=src_dir)
        self.assertIn("patch-tool_1.2-3.debian.tar.xz", str(ctx.exception))
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM binaries WHERE package_name = 'patch-tool'"
            ).fetchone()[0], 0,
            "the binary must not be registered when the source set is incomplete",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM sources WHERE package_name = 'patch-tool'"
            ).fetchone()[0], 0,
        )
        self.assertFalse(os.path.exists(os.path.join(self.base, "binary", "pool", "local")))
        self.assertFalse(os.path.exists(os.path.join(self.base, "source", "pool", "local")))

    def test_checksum_mismatch_atomic_failure(self):
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        deb = self._deb()
        src_dir, _dsc, _files = self._source_set(corrupt="patch-tool_1.2.orig.tar.gz")

        with self.assertRaises(ImportDebError) as ctx:
            import_local_deb(self.conn, self.config, deb, source_dir=src_dir)
        message = str(ctx.exception)
        self.assertIn("checksum", message.lower())
        self.assertIn("patch-tool_1.2.orig.tar.gz", message)
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM binaries WHERE package_name = 'patch-tool'"
            ).fetchone()[0], 0
        )

    def test_source_pointing_at_file_atomic_failure(self):
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        deb = self._deb()
        not_a_dir = os.path.join(self.tmp.name, "single.dsc")
        with open(not_a_dir, "w") as f:
            f.write("not a directory")

        with self.assertRaises(ImportDebError) as ctx:
            import_local_deb(self.conn, self.config, deb, source_dir=not_a_dir)
        self.assertIn("directory", str(ctx.exception))
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM binaries WHERE package_name = 'patch-tool'"
            ).fetchone()[0], 0
        )

    def test_no_source_no_row_no_link_verify_passes(self):
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        deb = self._deb()

        result = import_local_deb(self.conn, self.config, deb)

        self.assertIsNone(result.source_summary)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM sources").fetchone()[0], 0)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM binary_to_source").fetchone()[0], 0
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = handle_verify_graph(
                self.config.db_path, None,
                self.config.max_build_dep_report_depth, self.config.max_source_dep_depth,
            )
        self.assertEqual(rc, 0, buf.getvalue())

    def test_archive_source_phase_skips_local_sources(self):
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        deb = self._deb()
        src_dir, _dsc, _files = self._source_set()
        import_local_deb(self.conn, self.config, deb, source_dir=src_dir)

        config = replace(self.config, fetch_source=True)
        stats = download_pending_sources(config, self.conn)

        self.assertEqual(stats.command_errors, [],
                         "a verified local source must not become a missing-index error")
        self.assertEqual(stats.failures, [])


@unittest.skipUnless(shutil.which("gpg") and shutil.which("gpgv"), "gpg/gpgv required")
class TestImportDebSupersede(unittest.TestCase):
    """Importing a newer `local` revision supersedes the prior `current` row:
    exactly one `current` row per package/architecture, the handoff/diff local
    index emit only the newest stanza, the stale payload is retained in the
    pool (content-addressed, still checksummed so a lagging snapshot that
    references it keeps installing), and a re-import of the identical version
    stays idempotent."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        make_test_iso.write_pool_files(self.base)
        self.config = config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.conn.execute("INSERT INTO systems (name, bit) VALUES ('prod-box', 2)")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _import(self, name: str, version: str):
        deb = os.path.join(self.tmp.name, f"{name}_{version}_amd64.deb")
        make_test_deb.build_deb(deb, package=name, version=version)
        return import_local_deb(self.conn, self.config, deb)

    def _local_rows(self):
        return self.conn.execute(
            "SELECT version, status FROM binaries"
            " WHERE package_name = 'patch-tool' ORDER BY version"
        ).fetchall()

    def _index_text(self, path: str) -> str:
        with open(path, "rb") as f:
            return gzip.decompress(f.read()).decode("utf-8")

    def test_newer_local_import_supersedes_prior_current_row(self):
        self._import("patch-tool", "1.2-3")
        self._import("patch-tool", "1.3-0")

        self.assertEqual(
            self._local_rows(),
            [("1.2-3", "superseded"), ("1.3-0", "current")],
            "exactly one current local row must remain, the prior one superseded",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM binaries"
                " WHERE package_name = 'patch-tool' AND status = 'current'"
            ).fetchone()[0],
            1,
        )

    def test_superseded_payload_retained_and_still_checksummed(self):
        deb123 = os.path.join(self.tmp.name, "patch-tool_1.2-3_amd64.deb")
        make_test_deb.build_deb(deb123, package="patch-tool", version="1.2-3")
        import_local_deb(self.conn, self.config, deb123)
        self._import("patch-tool", "1.3-0")

        dest123 = make_test_deb.pool_abs_path(self.base, "patch-tool", "1.2-3")
        self.assertTrue(
            os.path.isfile(dest123),
            "the superseded payload must be retained in the pool",
        )
        with open(deb123, "rb") as f:
            expected = make_test_deb.sha256(f.read())
        with open(dest123, "rb") as f:
            self.assertEqual(make_test_deb.sha256(f.read()), expected)

    def test_handoff_and_diff_emit_only_the_newest_local_revision(self):
        self._import("patch-tool", "1.2-3")
        self._import("patch-tool", "1.3-0")

        generate_handoff(self.config, self.conn)
        handoff_text = self._index_text(
            handoff_index_path(self.config.binary_cache, "trixie", "local")
        )
        self.assertIn("Version: 1.3-0", handoff_text)
        self.assertNotIn("Version: 1.2-3", handoff_text)

        generate_diff(self.config, self.conn, 2)
        diff_text = self._index_text(
            diff_index_path(self.config.binary_cache, "trixie", "local")
        )
        self.assertIn("Version: 1.3-0", diff_text)
        self.assertNotIn("Version: 1.2-3", diff_text)

    def test_reimport_of_identical_version_stays_idempotent(self):
        self._import("patch-tool", "1.2-3")
        self._import("patch-tool", "1.3-0")
        self._import("patch-tool", "1.3-0")

        self.assertEqual(
            self._local_rows(),
            [("1.2-3", "superseded"), ("1.3-0", "current")],
            "re-importing the identical version must not duplicate or re-supersede",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM binaries"
                " WHERE package_name = 'patch-tool' AND status = 'current'"
            ).fetchone()[0],
            1,
        )


if __name__ == "__main__":
    unittest.main()