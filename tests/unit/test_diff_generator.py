import gzip
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_iso  # noqa: E402
from db import config, insert_binary, pool_prefix  # noqa: E402

from package_mgmt.lib.service.db_init import initialize_database  # noqa: E402
from package_mgmt.lib.service.handoff import generate_diff, generate_handoff  # noqa: E402
from package_mgmt.lib.service.snapshot import mark_delivered  # noqa: E402
from package_mgmt.lib.util.paths import (  # noqa: E402
    diff_dir,
    diff_index_path,
    diff_key_path,
    diff_manifest_path,
    diff_pool_path,
    diff_script_path,
    diff_signature_path,
    handoff_dir,
    handoff_key_path,
)


def _read_packages(cache: str, release: str, component: str) -> str:
    path = diff_index_path(cache, release, component)
    with open(path, "rb") as f:
        return gzip.decompress(f.read()).decode("utf-8")


def _pool_files(diff_pool: str) -> list[str]:
    files = []
    for root, _, names in os.walk(diff_pool):
        for name in names:
            files.append(os.path.relpath(os.path.join(root, name), diff_pool))
    return sorted(files)


@unittest.skipUnless(shutil.which("gpg") and shutil.which("gpgv"), "gpg/gpgv required")
class TestDiffGenerator(unittest.TestCase):
    """generate_diff produces the minimal diff — the pending
    (new) files only in diff/pool/, the COMPLETE per-release indexes (prior +
    new), a manifest whose `new|delivered` lines carry per-file SHA256/size,
    and Release/Release.gpg signed with the same key as the baseline handoff.

    The snapshot marking (`mark_delivered`) is tested here at the record level
    (exactly the shipped pending ids; a re-run ships nothing new).
    """

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
        self.cache = self.config.binary_cache

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _baseline(self, names):
        """Insert + mark delivered the baseline set, then generate the full
        handoff (spec 012) so the diff can sign with the same key."""
        ids = [insert_binary(self.conn, n, v) for n, v in names]
        self.conn.commit()
        self.conn.execute(
            "UPDATE binaries SET sync_mask = sync_mask | 2 WHERE id IN (%s)"
            % ",".join("?" for _ in ids),
            ids,
        )
        self.conn.commit()
        generate_handoff(self.config, self.conn)
        return ids

    def _generate(self, bit: int = 2):
        return generate_diff(self.config, self.conn, bit)

    def _insert_local_source(self, name, version):
        self.conn.execute(
            "INSERT INTO sources (package_name, source_version, release, component, verified)"
            " VALUES (?, ?, 'trixie', 'local', 1)",
            (name, version),
        )

    def _write_source_set(self, name, version):
        """Write a `.dsc` into the local source pool dir so the source index
        has a payload to hash (the layout `import_local_deb` produces)."""
        directory = os.path.join(
            self.config.source_cache, "pool", "local", pool_prefix(name), name
        )
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, f"{name}_{version}.dsc"), "wb") as f:
            f.write(f"dsc {name} {version}".encode())

    @staticmethod
    def _release_source_hashes(release_text: str) -> dict[str, str]:
        """The SHA256 hash entries for `local/source/...` paths in a Release."""
        hashes: dict[str, str] = {}
        in_sha256 = False
        for line in release_text.splitlines():
            if line == "SHA256:":
                in_sha256 = True
                continue
            if not in_sha256:
                continue
            parts = line.split()
            if len(parts) == 3 and parts[2].startswith("local/source/"):
                hashes[parts[2]] = parts[0]
        return hashes

    def test_diff_local_release_ships_source_index_matching_handoff(self):
        """A diff that serves the `local` component ships its deb-src index
        (`local/source/Sources.gz`) and hash-lists it in the local Release,
        exactly as the handoff does — a diff sync never drops the operator's
        source channel."""
        insert_binary(self.conn, "vifm", "1.0-2")
        insert_binary(self.conn, "libfoo", "1.2-3", component="local")
        self._insert_local_source("libfoo", "1.2-3")
        self._write_source_set("libfoo", "1.2-3")
        self.conn.commit()

        generate_handoff(self.config, self.conn)
        self._generate()

        for label, root in (("handoff", handoff_dir(self.cache)), ("diff", diff_dir(self.cache))):
            sources_gz = os.path.join(
                root, "dists", "trixie", "local", "source", "Sources.gz"
            )
            self.assertTrue(
                os.path.exists(sources_gz),
                f"{label} must ship the local source index",
            )
            with open(os.path.join(root, "dists", "trixie", "Release")) as f:
                release = f.read()
            self.assertIn(
                "local/source/Sources", release,
                f"{label} Release must hash-list the local source index",
            )

        with open(os.path.join(diff_dir(self.cache), "dists", "trixie", "Release")) as f:
            diff_release = f.read()
        with open(os.path.join(handoff_dir(self.cache), "dists", "trixie", "Release")) as f:
            handoff_release = f.read()
        self.assertEqual(
            self._release_source_hashes(diff_release),
            self._release_source_hashes(handoff_release),
            "the diff's local source hashes must match the handoff's",
        )

    def test_diff_pool_contains_only_pending_files(self):
        self._baseline([("vifm", "1.0-2"), ("lib1", "2.0-1")])
        app_id = insert_binary(self.conn, "app", "1.5-1")
        base_id = insert_binary(self.conn, "base-pkg", "1.0-1")
        self.conn.commit()

        stats = self._generate()

        self.assertEqual(stats.shipped_ids, [app_id, base_id])
        self.assertEqual(stats.new_count, 2)
        self.assertEqual(stats.prior_count, 2)
        self.assertEqual(
            _pool_files(diff_pool_path(self.cache)),
            [
                "main/a/app/app_1.5-1_amd64.deb",
                "main/b/base-pkg/base-pkg_1.0-1_amd64.deb",
            ],
        )
        for rel, content in (
            ("main/a/app/app_1.5-1_amd64.deb", make_test_iso._pool_content("app_1.5-1_amd64.deb")),
            ("main/b/base-pkg/base-pkg_1.0-1_amd64.deb", make_test_iso._pool_content("base-pkg_1.0-1_amd64.deb")),
        ):
            with open(os.path.join(diff_pool_path(self.cache), rel), "rb") as f:
                self.assertEqual(f.read(), content)

    def test_manifest_lines_new_and_delivered(self):
        self._baseline([("vifm", "1.0-2"), ("lib1", "2.0-1")])
        insert_binary(self.conn, "app", "1.5-1")
        insert_binary(self.conn, "base-pkg", "1.0-1")
        self.conn.commit()
        self._generate()

        entries = {}
        with open(diff_manifest_path(self.cache)) as f:
            for line in f:
                parts = line.split()
                if not parts:
                    continue
                self.assertEqual(len(parts), 4, f"malformed manifest line: {line!r}")
                kind, sha, size, path = parts
                self.assertIn(kind, ("new", "delivered"))
                entries[path] = (kind, sha, int(size))

        self.assertEqual(
            entries,
            {
                "pool/main/v/vifm/vifm_1.0-2_amd64.deb": (
                    "delivered",
                    hashlib.sha256(make_test_iso._pool_content("vifm_1.0-2_amd64.deb")).hexdigest(),
                    len(make_test_iso._pool_content("vifm_1.0-2_amd64.deb")),
                ),
                "pool/main/l/lib1/lib1_2.0-1_amd64.deb": (
                    "delivered",
                    hashlib.sha256(make_test_iso._pool_content("lib1_2.0-1_amd64.deb")).hexdigest(),
                    len(make_test_iso._pool_content("lib1_2.0-1_amd64.deb")),
                ),
                "pool/main/a/app/app_1.5-1_amd64.deb": (
                    "new",
                    hashlib.sha256(make_test_iso._pool_content("app_1.5-1_amd64.deb")).hexdigest(),
                    len(make_test_iso._pool_content("app_1.5-1_amd64.deb")),
                ),
                "pool/main/b/base-pkg/base-pkg_1.0-1_amd64.deb": (
                    "new",
                    hashlib.sha256(make_test_iso._pool_content("base-pkg_1.0-1_amd64.deb")).hexdigest(),
                    len(make_test_iso._pool_content("base-pkg_1.0-1_amd64.deb")),
                ),
            },
        )
        # Every manifest path is exactly the index Filename of a verified binary.
        self.assertIn("Filename: pool/main/v/vifm/vifm_1.0-2_amd64.deb",
                      _read_packages(self.cache, "trixie", "main"))

    def test_complete_index_covers_delivered_and_new(self):
        self._baseline([("vifm", "1.0-2")])
        insert_binary(self.conn, "app", "1.5-1")
        insert_binary(self.conn, "base-pkg", "1.0-1")
        self.conn.commit()
        self._generate()

        text = _read_packages(self.cache, "trixie", "main")
        paragraphs = text.strip().split("\n\n")
        self.assertEqual(len(paragraphs), 3)
        by_package = {}
        for para in paragraphs:
            lines = dict(line.split(": ", 1) for line in para.splitlines())
            by_package[lines["Package"]] = lines
        for name in ("vifm", "app", "base-pkg"):
            self.assertIn(name, by_package)
        app = by_package["app"]
        content = make_test_iso._pool_content("app_1.5-1_amd64.deb")
        self.assertEqual(app["Filename"], "pool/main/a/app/app_1.5-1_amd64.deb")
        self.assertEqual(int(app["Size"]), len(content))
        self.assertEqual(app["SHA256"], hashlib.sha256(content).hexdigest())

    def test_release_signed_with_same_key_as_baseline_handoff(self):
        self._baseline([("vifm", "1.0-2")])
        insert_binary(self.conn, "app", "1.5-1")
        self.conn.commit()
        self._generate()

        with open(handoff_key_path(self.cache), "rb") as f:
            handoff_key = f.read()
        with open(diff_key_path(self.cache), "rb") as f:
            diff_key = f.read()
        self.assertEqual(diff_key, handoff_key,
                         "offline-cache.gpg in the diff must be the baseline handoff's key")

        with tempfile.TemporaryDirectory() as tmp:
            keyring = os.path.join(tmp, "keyring.gpg")
            subprocess.run(
                ["gpg", "--dearmor", "--output", keyring, diff_key_path(self.cache)],
                check=True, capture_output=True, text=True,
            )
            result = subprocess.run(
                [
                    "gpgv", "--keyring", keyring,
                    diff_signature_path(self.cache, "trixie"),
                    os.path.join(diff_dir(self.cache), "dists", "trixie", "Release"),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0,
                             "diff Release.gpg must verify against offline-cache.gpg")

    def test_snapshot_marks_exactly_shipped_ids_and_rerun_empty(self):
        self._baseline([("vifm", "1.0-2")])
        app_id = insert_binary(self.conn, "app", "1.5-1")
        self.conn.commit()
        stats = self._generate()
        self.assertEqual(stats.shipped_ids, [app_id])

        mark_delivered(self.conn, "prod-box", stats.shipped_ids)
        masks = [r[0] for r in self.conn.execute(
            "SELECT sync_mask FROM binaries ORDER BY package_name").fetchall()]
        self.assertEqual(masks, [2, 2], "only the shipped pending id must be marked")

        stats2 = self._generate()
        self.assertEqual(stats2.shipped_ids, [])
        self.assertEqual(stats2.new_count, 0)
        self.assertEqual(_pool_files(diff_pool_path(self.cache)), [])

    def test_excluded_pending_record_reported_and_never_shipped(self):
        self._baseline([("vifm", "1.0-2")])
        insert_binary(self.conn, "app", "1.5-1")
        self.conn.commit()
        os.remove(os.path.join(
            self.cache, "pool", "main", "a", "app", "app_1.5-1_amd64.deb"
        ))

        stats = self._generate()

        self.assertEqual(stats.excluded_pending, ["app 1.5-1"])
        self.assertEqual(stats.shipped_ids, [], "the excluded record must never ship")
        self.assertEqual(_pool_files(diff_pool_path(self.cache)), [])
        self.assertNotIn("Package: app", _read_packages(self.cache, "trixie", "main"))

    def test_excluded_delivered_record_is_defect_state(self):
        self._baseline([("vifm", "1.0-2")])
        insert_binary(self.conn, "app", "1.5-1")
        self.conn.commit()
        os.remove(os.path.join(
            self.cache, "pool", "main", "v", "vifm", "vifm_1.0-2_amd64.deb"
        ))

        stats = self._generate()

        self.assertEqual(stats.excluded_delivered, ["vifm 1.0-2"])
        self.assertEqual(stats.prior_count, 0)
        self.assertNotIn("Package: vifm", _read_packages(self.cache, "trixie", "main"))

    def test_failed_pool_copy_leaves_no_stanza_or_manifest_entry(self):
        """When a pool copy fails, the produced diff must
        contain no Packages stanza and no manifest line for the failed record
        — the diff never references a payload it does not contain."""
        self._baseline([("vifm", "1.0-2")])
        app_id = insert_binary(self.conn, "app", "1.5-1")
        base_id = insert_binary(self.conn, "base-pkg", "1.0-1")
        self.conn.commit()
        # Corrupt app's pool payload so _copy_verified raises a checksum
        # mismatch — the copy itself fails, unlike a pre-copy missing-file
        # exclusion.
        app_pool = os.path.join(
            self.cache, "pool", "main", "a", "app", "app_1.5-1_amd64.deb"
        )
        with open(app_pool, "wb") as f:
            f.write(b"corrupted payload")

        stats = self._generate()

        self.assertEqual(stats.excluded_pending, ["app 1.5-1"])
        self.assertNotIn(app_id, stats.shipped_ids)
        self.assertEqual(stats.shipped_ids, [base_id])
        self.assertEqual(
            _pool_files(diff_pool_path(self.cache)),
            ["main/b/base-pkg/base-pkg_1.0-1_amd64.deb"],
        )
        self.assertNotIn("Package: app", _read_packages(self.cache, "trixie", "main"))
        with open(diff_manifest_path(self.cache)) as f:
            manifest = f.read()
        self.assertNotIn("app_1.5-1_amd64.deb", manifest)

    def test_diff_script_emitted_and_layout_complete(self):
        self._baseline([("vifm", "1.0-2")])
        insert_binary(self.conn, "app", "1.5-1")
        self.conn.commit()
        self._generate()

        script = diff_script_path(self.cache)
        self.assertTrue(os.path.isfile(script))
        self.assertTrue(os.stat(script).st_mode & 0o100)
        with open(script) as f:
            content = f.read()
        self.assertIn("apply_configure", content)
        self.assertIn('releases="trixie"', content)
        for rel in ("dists/trixie/Release", "dists/trixie/Release.gpg",
                    "dists/trixie/main/binary-amd64/Packages.gz",
                    "manifest.txt", "offline-cache.gpg", "pool"):
            self.assertTrue(os.path.exists(os.path.join(diff_dir(self.cache), rel)),
                            f"diff must contain {rel}")

    def test_regeneration_is_idempotent(self):
        self._baseline([("vifm", "1.0-2")])
        insert_binary(self.conn, "app", "1.5-1")
        self.conn.commit()
        self._generate()
        first_manifest = open(diff_manifest_path(self.cache)).read()
        first_index = _read_packages(self.cache, "trixie", "main")
        self._generate()
        self.assertEqual(open(diff_manifest_path(self.cache)).read(), first_manifest)
        self.assertEqual(_read_packages(self.cache, "trixie", "main"), first_index)


if __name__ == "__main__":
    unittest.main()
