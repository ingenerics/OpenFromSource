import contextlib
import gzip
import hashlib
import io
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from email.utils import parsedate_to_datetime
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_deb  # noqa: E402
import make_test_iso  # noqa: E402
from db import config, insert_binary  # noqa: E402

from package_mgmt.cli.commands.handoff import handle_handoff  # noqa: E402
from package_mgmt.lib.model.config import ArchiveSource, ProjectConfig  # noqa: E402
from package_mgmt.lib.model.dependency import (  # noqa: E402
    DependencyGraph,
    serialize_depends_with_version_hash,
)
from package_mgmt.lib.service.db_init import initialize_database  # noqa: E402
from package_mgmt.lib.service.handoff import HandoffError, generate_diff, generate_handoff  # noqa: E402
from package_mgmt.lib.util.errors import EXIT_ERROR  # noqa: E402
from package_mgmt.lib.util.paths import (  # noqa: E402
    binary_index_path,
    diff_index_path,
    diff_manifest_path,
    diff_pool_path,
    diff_script_path,
    handoff_dir,
    handoff_gnupg_dir,
    handoff_index_path,
    handoff_key_path,
    handoff_pool_link,
    handoff_script_path,
)


def _multi_release_config(base_dir: str, mirror: str = "http://mirror.invalid") -> ProjectConfig:
    return ProjectConfig(
        base_dir=base_dir,
        binary_cache=f"{base_dir}/binary",
        source_cache=f"{base_dir}/source",
        iso_dir=f"{base_dir}/iso",
        db_path=f"{base_dir}/package_mgmt.db",
        log_dir=f"{base_dir}/logs",
        fetch_source=False,
        sources=(
            ArchiveSource(mirror=mirror, release="trixie", components=("main", "contrib")),
            ArchiveSource(mirror=mirror, release="trixie-backports", components=("main",)),
        ),
    )


def _main_only_config(base_dir: str, mirror: str = "http://mirror.invalid") -> ProjectConfig:
    """A config serving only trixie/main — a component (`contrib`) that the
    database may still hold verified records for."""
    return ProjectConfig(
        base_dir=base_dir,
        binary_cache=f"{base_dir}/binary",
        source_cache=f"{base_dir}/source",
        iso_dir=f"{base_dir}/iso",
        db_path=f"{base_dir}/package_mgmt.db",
        log_dir=f"{base_dir}/logs",
        fetch_source=False,
        sources=(
            ArchiveSource(mirror=mirror, release="trixie", components=("main",)),
        ),
    )


def _gpgv_verify(handoff: str, release: str) -> bool:
    """True when the handoff's Release.gpg verifies against its public key."""
    # gpgv requires a binary keyring; dearmor the shipped armored key first.
    with tempfile.TemporaryDirectory() as tmp:
        keyring = os.path.join(tmp, "keyring.gpg")
        subprocess.run(
            ["gpg", "--dearmor", "--output", keyring,
             os.path.join(handoff, "offline-cache.gpg")],
            check=True, capture_output=True, text=True,
        )
        result = subprocess.run(
            [
                "gpgv",
                "--keyring", keyring,
                os.path.join(handoff, "dists", release, "Release.gpg"),
                os.path.join(handoff, "dists", release, "Release"),
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0


def _read_packages(handoff: str, release: str, component: str) -> str:
    path = handoff_index_path(handoff.removesuffix("/handoff"), release, component)
    with open(path, "rb") as f:
        return gzip.decompress(f.read()).decode("utf-8")


def _release_date_epoch(release: str) -> int:
    """The epoch of a generated Release's `Date:` field."""
    for line in release.splitlines():
        if line.startswith("Date:"):
            return int(parsedate_to_datetime(line.split(":", 1)[1].strip()).timestamp())
    raise AssertionError("generated Release has no Date field")


@unittest.skipUnless(shutil.which("gpg") and shutil.which("gpgv"), "gpg/gpgv required")
class TestHandoffGenerator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        make_test_iso.write_pool_files(self.base)
        self.config = config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.handoff = handoff_dir(self.config.binary_cache)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _generate(self):
        return generate_handoff(self.config, self.conn)

    def test_space_containing_cache_path_refused(self):
        """A cache path containing a space is refused with a clear,
        actionable diagnostic — the generated apt source line would break on
        the whitespace, so nothing is written."""
        spaced = config(os.path.join(self.tmp.name, "My USB", "cache"))
        with self.assertRaises(HandoffError) as ctx:
            generate_handoff(spaced, self.conn)
        message = str(ctx.exception)
        self.assertIn("My USB", message)
        self.assertIn("space", message.lower())
        self.assertFalse(os.path.exists(handoff_dir(spaced.binary_cache)))

    def test_generates_filtered_index_for_verified_set(self):
        insert_binary(self.conn, "vifm", "1.0-2")
        insert_binary(self.conn, "lib1", "2.0-1")
        insert_binary(self.conn, "lib2", "3.0-1", verified=0)
        self.conn.commit()

        stats = self._generate()

        self.assertEqual(stats.packages, 2)
        self.assertEqual(stats.per_component, {"trixie/main": 2, "trixie/contrib": 0})
        text = _read_packages(self.handoff, "trixie", "main")
        paragraphs = text.strip().split("\n\n")
        self.assertEqual(len(paragraphs), 2)
        vifm = next(p for p in paragraphs if p.startswith("Package: vifm"))
        self.assertIn("Version: 1.0-2", vifm)
        self.assertIn("Architecture: amd64", vifm)
        self.assertIn("Depends: lib1, lib2", vifm)
        filename = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"
        self.assertIn(f"Filename: {filename}", vifm)
        content = make_test_iso._pool_content("vifm_1.0-2_amd64.deb")
        self.assertIn(f"Size: {len(content)}", vifm)
        self.assertIn(f"SHA256: {hashlib.sha256(content).hexdigest()}", vifm)
        lib1 = next(p for p in paragraphs if p.startswith("Package: lib1"))
        self.assertNotIn("Depends:", lib1)
        # contrib has no records: the index exists but is empty.
        self.assertEqual(_read_packages(self.handoff, "trixie", "contrib").strip(), "")

    def test_provides_passthrough(self):
        insert_binary(self.conn, "lib2", "3.0-1")
        self.conn.commit()
        self._generate()
        lib2 = next(
            p for p in _read_packages(self.handoff, "trixie", "main").strip().split("\n\n")
            if p.startswith("Package: lib2")
        )
        self.assertIn("Provides: virtual-lib2", lib2)

    def test_release_checksums_and_fields(self):
        insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        self._generate()

        release_path = os.path.join(self.handoff, "dists", "trixie", "Release")
        with open(release_path) as f:
            release = f.read()
        self.assertIn("Suite: trixie", release)
        self.assertIn("Codename: trixie", release)
        self.assertIn("Architectures: amd64", release)
        self.assertIn("Components: main contrib", release)
        self.assertRegex(release, r"Date: .{20,}")
        self.assertNotIn("Valid-Until:", release)
        index_path = os.path.join(self.handoff, "dists", "trixie", "main", "binary-amd64", "Packages.gz")
        with open(index_path, "rb") as f:
            data = f.read()
        rel = "main/binary-amd64/Packages.gz"
        for header, hasher in (("MD5Sum", hashlib.md5), ("SHA1", hashlib.sha1),
                               ("SHA256", hashlib.sha256), ("SHA512", hashlib.sha512)):
            self.assertIn(f"{hasher(data).hexdigest()} {len(data):16d} {rel}", release)

    def test_release_date_advances_on_regeneration(self):
        """`Date` reflects generation time — it advances on
        a fresh generate, never a frozen cache mtime."""
        insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        release_path = os.path.join(self.handoff, "dists", "trixie", "Release")
        with mock.patch("package_mgmt.lib.service.handoff.time.time", return_value=1700000000.0):
            self._generate()
        with open(release_path) as f:
            first = _release_date_epoch(f.read())
        with mock.patch("package_mgmt.lib.service.handoff.time.time", return_value=1700000200.0):
            self._generate()
        with open(release_path) as f:
            second = _release_date_epoch(f.read())
        self.assertEqual(first, 1700000000)
        self.assertEqual(second, 1700000200)
        self.assertNotEqual(first, second, "regeneration must advance Date to the new generation time")

    def test_source_date_epoch_pins_byte_identical_regeneration(self):
        """With `SOURCE_DATE_EPOCH` set (explicit opt-in),
        regeneration is byte-identical and `Date` is pinned to that epoch."""
        insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        release_path = os.path.join(self.handoff, "dists", "trixie", "Release")
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            self._generate()
            with open(release_path) as f:
                first = f.read()
            self._generate()
            with open(release_path) as f:
                second = f.read()
        self.assertEqual(_release_date_epoch(first), 1700000000)
        self.assertEqual(first, second, "SOURCE_DATE_EPOCH must yield byte-identical regeneration")

    def test_invalid_source_date_epoch_warns_and_falls_back(self):
        """An invalid `SOURCE_DATE_EPOCH` logs a warning and
        falls back to generation time."""
        insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        release_path = os.path.join(self.handoff, "dists", "trixie", "Release")
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "not-an-epoch"}):
            with mock.patch("package_mgmt.lib.service.handoff.time.time", return_value=1700000500.0):
                with mock.patch("package_mgmt.lib.service.handoff.logger.warning") as warn:
                    self._generate()
        self.assertTrue(warn.called, "invalid SOURCE_DATE_EPOCH must log a warning")
        with open(release_path) as f:
            self.assertEqual(_release_date_epoch(f.read()), 1700000500)

    def test_release_gpg_verifies_with_gpgv(self):
        insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        self._generate()
        self.assertTrue(_gpgv_verify(self.handoff, "trixie"),
                        "Release.gpg must be a valid detached signature of Release")

    def test_key_is_armored_public_key(self):
        self._generate()
        with open(handoff_key_path(self.config.binary_cache)) as f:
            key = f.read()
        self.assertTrue(key.startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----"))

    def test_pool_symlink_resolves_to_cache_pool(self):
        self._generate()
        link = handoff_pool_link(self.config.binary_cache)
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.path.realpath(link), os.path.join(self.config.binary_cache, "pool"))

    def test_handoff_preserves_multi_arch_field(self):
        """The handoff Packages entries preserve `Multi-Arch` so apt can
        resolve `name:any` dependencies (e.g. `perl:any`) offline — the
        dropped field broke the operator install proof (release checklist)."""
        insert_binary(self.conn, "vifm", "1.0-2")
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        index_path = binary_index_path(f"{self.base}/binary", "trixie", "main")
        with open(index_path, "rb") as f:
            raw = gzip.decompress(f.read()).decode("utf-8")
        marker = "Architecture: amd64\nFilename: pool/main/l/lib1/lib1_2.0-1_amd64.deb"
        self.assertIn(marker, raw)
        raw = raw.replace(marker, "Architecture: amd64\nMulti-Arch: foreign\n" + marker)
        with open(index_path, "wb") as f:
            f.write(gzip.compress(raw.encode("utf-8")))
        self._generate()
        text = _read_packages(self.handoff, "trixie", "main")
        lib1 = next(p for p in text.strip().split("\n\n") if p.startswith("Package: lib1"))
        self.assertIn("Multi-Arch: foreign", lib1)

    def test_handoff_no_invented_depends_for_pre_depends_only_archive_package(self):
        """apt's VersionHash is computed over {Installed-Size, Depends,
        Pre-Depends, Conflicts, Breaks, Replaces}; a package whose index entry
        (and .deb control) carries only Pre-Depends must NOT gain an invented
        `Depends:` from the resolver's merged view — dpkg records exactly the
        .deb's fields, so an invented `Depends` splits the repo version from
        the installed dpkg-status record and the identical version is reported
        as upgradable (spec 012 regression: debianutils/sed/tar/perl-base)."""
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        index_path = binary_index_path(f"{self.base}/binary", "trixie", "main")
        with open(index_path, "rb") as f:
            raw = gzip.decompress(f.read()).decode("utf-8")
        marker = "Architecture: amd64\nFilename: pool/main/l/lib1/lib1_2.0-1_amd64.deb"
        self.assertIn(marker, raw)
        raw = raw.replace(
            marker,
            "Architecture: amd64\nInstalled-Size: 42\nPre-Depends: libc6 (>= 2.0)\n"
            + marker,
        )
        with open(index_path, "wb") as f:
            f.write(gzip.compress(raw.encode("utf-8")))
        self._generate()
        lib1 = next(
            p for p in _read_packages(self.handoff, "trixie", "main").strip().split("\n\n")
            if p.startswith("Package: lib1")
        )
        self.assertIn("Installed-Size: 42", lib1)
        self.assertIn("Pre-Depends: libc6 (>= 2.0)", lib1)
        depends_lines = [ln for ln in lib1.splitlines() if ln.startswith("Depends: ")]
        self.assertEqual(
            depends_lines, [],
            "the merged Pre-Depends view must never be emitted as an invented Depends field",
        )

    def test_bootstrap_script_emitted_with_release_and_components(self):
        self._generate()
        script = handoff_script_path(self.config.binary_cache)
        self.assertTrue(os.path.isfile(script))
        mode = os.stat(script).st_mode
        self.assertTrue(mode & stat.S_IXUSR, "bootstrap.sh must be executable")
        with open(script) as f:
            content = f.read()
        self.assertIn('release_label="release trixie"', content)
        self.assertIn('releases="trixie"', content)
        self.assertIn('sources="trixie main contrib', content)

    def test_idempotent_regeneration(self):
        insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        self._generate()
        first = _read_packages(self.handoff, "trixie", "main")
        self._generate()
        self.assertEqual(_read_packages(self.handoff, "trixie", "main"), first)

    def test_regeneration_is_byte_identical(self):
        """Generation is atomic and reproducible — regenerating twice
        yields byte-identical `Release` and `Packages.gz` when the timestamp
        is deterministic (SOURCE_DATE_EPOCH override)."""
        insert_binary(self.conn, "vifm", "1.0-2")
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        old = os.environ.get("SOURCE_DATE_EPOCH")
        os.environ["SOURCE_DATE_EPOCH"] = "1750000000"
        try:
            self._generate()
            release_path = os.path.join(self.handoff, "dists", "trixie", "Release")
            with open(release_path, "rb") as f:
                first_release = f.read()
            with open(handoff_index_path(self.config.binary_cache, "trixie", "main"), "rb") as f:
                first_packages = f.read()
            # The Release Date must reflect the pinned epoch (not wall-clock).
            self.assertIn(b"Date: Sun, 15 Jun 2025 15:06:40 UTC", first_release)

            self._generate()
            with open(release_path, "rb") as f:
                second_release = f.read()
            with open(handoff_index_path(self.config.binary_cache, "trixie", "main"), "rb") as f:
                second_packages = f.read()
        finally:
            if old is None:
                os.environ.pop("SOURCE_DATE_EPOCH", None)
            else:
                os.environ["SOURCE_DATE_EPOCH"] = old
        self.assertEqual(second_release, first_release)
        self.assertEqual(second_packages, first_packages)

    def test_offline_cache_key_stable_across_regeneration(self):
        """The signing key persists outside the payload tree — regenerating
        leaves `offline-cache.gpg` byte-identical (a stable trust anchor), and
        `Release.gpg` still verifies against it."""
        insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        self._generate()
        key_path = handoff_key_path(self.config.binary_cache)
        with open(key_path, "rb") as f:
            first = f.read()
        self.assertTrue(os.path.isdir(os.path.join(self.base, ".gnupg-handoff")),
                        "the persistent keyring must live at {base_dir}/.gnupg-handoff")
        self._generate()
        with open(key_path, "rb") as f:
            second = f.read()
        self.assertEqual(second, first,
                         "offline-cache.gpg must not change across regenerations")
        self.assertTrue(_gpgv_verify(self.handoff, "trixie"),
                        "Release.gpg must still verify against the stable key")

    def test_handoff_payload_is_public_key_only(self):
        """The shipped payload carries only the public `offline-cache.gpg`;
        the secret keyring lives outside the payload tree."""
        insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        self._generate()
        self.assertTrue(os.path.isfile(handoff_key_path(self.config.binary_cache)),
                        "the public offline-cache.gpg must be shipped")
        gnupg_home = handoff_gnupg_dir(self.config.binary_cache)
        self.assertEqual(gnupg_home, os.path.join(self.base, ".gnupg-handoff"))
        self.assertTrue(os.path.isdir(gnupg_home),
                        "the secret keyring must live at {base_dir}/.gnupg-handoff")
        self.assertTrue(os.listdir(gnupg_home),
                        "the relocated keyring must hold the generated key material")
        for root, dirs, files in os.walk(self.handoff):
            for name in dirs + files:
                self.assertNotIn(".gnupg", name)
                self.assertNotIn("secring", name)
                self.assertNotIn("private-keys", name)

    def test_zero_verified_generates_empty_handoff(self):
        self.conn.commit()
        stats = self._generate()
        self.assertEqual(stats.packages, 0)
        self.assertEqual(_read_packages(self.handoff, "trixie", "main").strip(), "")
        self.assertTrue(os.path.isfile(os.path.join(self.handoff, "dists", "trixie", "Release.gpg")))
        self.assertTrue(os.path.isfile(handoff_script_path(self.config.binary_cache)))

    def test_excludes_verified_record_with_missing_pool_file(self):
        insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        os.remove(os.path.join(
            self.config.binary_cache, "pool", "main", "v", "vifm", "vifm_1.0-2_amd64.deb"
        ))
        stats = self._generate()
        self.assertEqual(stats.excluded_pool, ["vifm 1.0-2"])
        self.assertEqual(stats.packages, 0)
        self.assertNotIn("Package: vifm", _read_packages(self.handoff, "trixie", "main"))

    def test_unconfirmable_record_without_index_entry(self):
        insert_binary(self.conn, "ghost", "9.9-1")
        self.conn.commit()
        stats = self._generate()
        self.assertEqual(stats.unconfirmable, ["ghost 9.9-1"])
        self.assertEqual(stats.packages, 0)

    def test_missing_pool_file_reported_with_log_warning_and_count(self):
        """A verified record with a missing pool file is excluded from the
        generated index and reported (log warning + summary count)."""
        insert_binary(self.conn, "vifm", "1.0-2")
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()
        os.remove(os.path.join(
            self.config.binary_cache, "pool", "main", "v", "vifm", "vifm_1.0-2_amd64.deb"
        ))
        with self.assertLogs("package_mgmt.lib.service.handoff", level="WARNING") as captured:
            stats = self._generate()
        messages = [r.getMessage() for r in captured.records]
        self.assertTrue(any("vifm 1.0-2" in m for m in messages),
                        "the excluded record must be logged as a warning")
        self.assertEqual(stats.excluded_pool, ["vifm 1.0-2"])
        self.assertEqual(stats.packages, 1)
        text = _read_packages(self.handoff, "trixie", "main")
        self.assertNotIn("Package: vifm", text)
        self.assertIn("Package: lib1", text)

    def test_unconfirmable_reported_with_log_warning(self):
        """A verified record with no resolvable index entry is excluded and
        reported (log warning); it is the caller's command error."""
        insert_binary(self.conn, "ghost", "9.9-1")
        self.conn.commit()
        with self.assertLogs("package_mgmt.lib.service.handoff", level="WARNING") as captured:
            stats = self._generate()
        messages = [r.getMessage() for r in captured.records]
        self.assertTrue(any("ghost 9.9-1" in m for m in messages))
        self.assertEqual(stats.unconfirmable, ["ghost 9.9-1"])
        self.assertNotIn("Package: ghost", _read_packages(self.handoff, "trixie", "main"))

    def test_regeneration_after_pool_restored_includes_record(self):
        """After the pool file is restored (as `download`/`verify` would),
        regenerating the handoff includes the record again."""
        insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        pool_file = os.path.join(
            self.config.binary_cache, "pool", "main", "v", "vifm", "vifm_1.0-2_amd64.deb"
        )
        os.remove(pool_file)
        stats = self._generate()
        self.assertEqual(stats.excluded_pool, ["vifm 1.0-2"])
        self.assertNotIn("Package: vifm", _read_packages(self.handoff, "trixie", "main"))
        os.makedirs(os.path.dirname(pool_file), exist_ok=True)
        with open(pool_file, "wb") as f:
            f.write(make_test_iso._pool_content("vifm_1.0-2_amd64.deb"))
        stats = self._generate()
        self.assertEqual(stats.excluded_pool, [])
        self.assertEqual(stats.packages, 1)
        self.assertIn("Package: vifm", _read_packages(self.handoff, "trixie", "main"))

    def test_excluded_handoff_signature_still_verifies(self):
        """Even when records are excluded, the shipped Release.gpg still
        verifies against offline-cache.gpg (Constitution gate)."""
        insert_binary(self.conn, "vifm", "1.0-2")
        insert_binary(self.conn, "ghost", "9.9-1")
        self.conn.commit()
        stats = self._generate()
        self.assertEqual(stats.unconfirmable, ["ghost 9.9-1"])
        self.assertTrue(_gpgv_verify(self.handoff, "trixie"),
                        "Release.gpg must verify even with excluded records")

    def test_unconfirmable_record_is_command_error(self):
        """A record `verify` cannot fix (no resolvable index entry) is a
        command error per contracts/cli.md: JSON on stderr, exit 1, and it is
        never marked delivered."""
        insert_binary(self.conn, "ghost", "9.9-1")
        self.conn.execute("INSERT INTO systems (name, bit) VALUES ('prod-box', 2)")
        self.conn.commit()
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = handle_handoff(self.config.db_path, self.config, "prod-box")
        self.assertEqual(rc, EXIT_ERROR)
        stripped = err.getvalue().strip()
        if not stripped.startswith("{"):
            raise AssertionError(
                f"stderr carries non-JSON text before the payload: {stripped[:120]!r}"
            )
        payload = json.loads(stripped)
        self.assertEqual(payload["exit_code"], 1)
        self.assertIn("1 verified records excluded", payload["error"])
        row = self.conn.execute(
            "SELECT sync_mask FROM binaries WHERE package_name = 'ghost'"
        ).fetchone()
        self.assertEqual(row[0], 0, "the excluded record must not be marked delivered")

    def test_crafted_filename_traversal_excluded_from_handoff(self):
        """A verified record whose index Filename escapes the cache is excluded
        as unconfirmable (command error) and nothing is written outside the
        cache."""
        insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        index_path = binary_index_path(f"{self.base}/binary", "trixie", "main")
        with open(index_path, "rb") as f:
            raw = gzip.decompress(f.read()).decode("utf-8")
        paragraphs = raw.split("\n\n")
        for i, para in enumerate(paragraphs):
            if para.startswith("Package: vifm"):
                lines = [ln for ln in para.splitlines() if not ln.startswith("Filename: ")]
                paragraphs[i] = "\n".join(lines) + "\nFilename: ../../etc/passwd\n"
                break
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            gz.write(("\n\n".join(paragraphs)).encode("utf-8"))
        with open(index_path, "wb") as f:
            f.write(buf.getvalue())

        stats = self._generate()

        self.assertEqual(stats.unconfirmable, ["vifm 1.0-2"])
        self.assertEqual(stats.packages, 0)
        self.assertNotIn("Package: vifm", _read_packages(self.handoff, "trixie", "main"))
        self.assertFalse(os.path.exists(os.path.join(self.base, "binary", "etc")))

    def test_excludes_record_outside_configured_components(self):
        """A verified record under a component no longer in the config is
        omitted from every generated index, reported loudly, and its delivery
        id is never advanced."""
        vifm_id = insert_binary(self.conn, "vifm", "1.0-2")
        contrib_id = insert_binary(self.conn, "firmware-x", "1.0-1", component="contrib")
        self.conn.commit()

        reduced = _main_only_config(self.base)
        stats = generate_handoff(reduced, self.conn)

        self.assertEqual(stats.excluded_config, ["firmware-x 1.0-1"])
        self.assertIn("Package: vifm", _read_packages(self.handoff, "trixie", "main"))
        self.assertNotIn(
            "Package: firmware-x", _read_packages(self.handoff, "trixie", "main")
        )
        self.assertEqual(set(stats.delivered_ids), {vifm_id})
        self.assertNotIn(contrib_id, stats.delivered_ids)

    def test_mixed_records_and_restore_reincludes_out_of_config_record(self):
        """A mix of in- and out-of-config verified records → only the in-config
        records are emitted and delivered; restoring the component to the
        config re-includes the record (the exclusion is non-permanent)."""
        insert_binary(self.conn, "vifm", "1.0-2")
        insert_binary(self.conn, "firmware-x", "1.0-1", component="contrib")
        self.conn.commit()

        stats = generate_handoff(_main_only_config(self.base), self.conn)
        self.assertEqual(stats.excluded_config, ["firmware-x 1.0-1"])
        self.assertEqual(stats.packages, 1)
        self.assertIn("Package: vifm", _read_packages(self.handoff, "trixie", "main"))
        self.assertNotIn(
            "Package: firmware-x", _read_packages(self.handoff, "trixie", "main")
        )

        restored = generate_handoff(self.config, self.conn)
        self.assertEqual(restored.excluded_config, [])
        self.assertEqual(restored.packages, 2)
        self.assertIn(
            "Package: firmware-x", _read_packages(self.handoff, "trixie", "contrib")
        )

    def test_out_of_config_record_reported_as_log_warning(self):
        """Each excluded record is logged as a warning (loud, but non-fatal)."""
        insert_binary(self.conn, "vifm", "1.0-2")
        insert_binary(self.conn, "firmware-x", "1.0-1", component="contrib")
        self.conn.commit()
        with self.assertLogs("package_mgmt.lib.service.handoff", level="WARNING") as captured:
            stats = generate_handoff(_main_only_config(self.base), self.conn)
        messages = [r.getMessage() for r in captured.records]
        self.assertTrue(any("firmware-x 1.0-1" in m for m in messages),
                        "the excluded record must be logged as a warning")
        self.assertEqual(stats.excluded_config, ["firmware-x 1.0-1"])
        self.assertEqual(stats.packages, 1)


@unittest.skipUnless(shutil.which("gpg") and shutil.which("gpgv"), "gpg/gpgv required")
class TestMultiReleaseHandoff(unittest.TestCase):
    """Spec 012 multi-release: every configured release is served with its own
    Release/Release.gpg/indexes, no hash collision, and bootstrap.sh configures
    one apt source line per release."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        make_test_iso.write_pool_files(self.base)
        make_test_iso.write_backports_archive(self.base)
        self.config = _multi_release_config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.handoff = handoff_dir(self.config.binary_cache)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _read_release(self, release: str) -> str:
        path = os.path.join(self.handoff, "dists", release, "Release")
        with open(path) as f:
            return f.read()

    def _release_hash(self, release: str, rel_path: str) -> tuple[str, int]:
        """(sha256, size) for an index path as listed in a release file."""
        for line in self._read_release(release).splitlines():
            fields = line.split()
            if len(fields) >= 3 and len(fields[0]) == 64 and fields[-1] == rel_path:
                return fields[0], int(fields[1])
        raise AssertionError(f"{rel_path} not listed in {release} Release")

    def test_each_release_gets_release_signature_and_indexes(self):
        bp_id = insert_binary(self.conn, "backport-tool", "2.0-1",
                               release="trixie-backports", component="main")
        insert_binary(self.conn, "vifm", "1.0-2")
        insert_binary(self.conn, "lib1", "2.0-1")
        self.conn.commit()

        stats = generate_handoff(self.config, self.conn)

        self.assertEqual(stats.releases, ("trixie", "trixie-backports"))
        self.assertEqual(stats.release, "trixie")
        self.assertEqual(
            stats.release_components,
            {"trixie": ("main", "contrib"), "trixie-backports": ("main",)},
        )
        self.assertEqual(
            stats.per_component,
            {"trixie/main": 2, "trixie/contrib": 0, "trixie-backports/main": 1},
        )
        self.assertEqual(stats.packages, 3)
        self.assertIn(bp_id, stats.delivered_ids)
        self.assertEqual(len(stats.delivered_ids), 3)

        # Each release has its own Release file with its own Components.
        trixie = self._read_release("trixie")
        self.assertIn("Suite: trixie", trixie)
        self.assertIn("Components: main contrib", trixie)
        backports = self._read_release("trixie-backports")
        self.assertIn("Suite: trixie-backports", backports)
        self.assertIn("Components: main", backports)
        self.assertNotIn("contrib/", backports)

        # No hash collision: each Release lists only its own indexes, and the
        # listed SHA256 matches the actual Packages.gz bytes on disk.
        for release in ("trixie", "trixie-backports"):
            self.assertTrue(_gpgv_verify(self.handoff, release),
                            f"Release.gpg must verify for {release}")
            index_path = handoff_index_path(self.config.binary_cache, release, "main")
            with open(index_path, "rb") as f:
                data = f.read()
            sha256, size = self._release_hash(release, "main/binary-amd64/Packages.gz")
            self.assertEqual(sha256, hashlib.sha256(data).hexdigest())
            self.assertEqual(size, len(data))
        contrib_path = handoff_index_path(self.config.binary_cache, "trixie", "contrib")
        with open(contrib_path, "rb") as f:
            data = f.read()
        sha256, size = self._release_hash("trixie", "contrib/binary-amd64/Packages.gz")
        self.assertEqual(sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(size, len(data))

    def test_bootstrap_script_configures_one_source_line_per_release(self):
        insert_binary(self.conn, "backport-tool", "2.0-1",
                       release="trixie-backports", component="main")
        self.conn.commit()
        generate_handoff(self.config, self.conn)
        with open(handoff_script_path(self.config.binary_cache)) as f:
            content = f.read()
        self.assertIn('release_label="releases: trixie, trixie-backports"', content)
        self.assertIn('releases="trixie trixie-backports"', content)
        self.assertIn('sources="trixie main contrib', content)
        self.assertIn("trixie-backports main", content)

    def test_second_release_emitted_even_when_empty(self):
        insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()
        generate_handoff(self.config, self.conn)
        self.assertTrue(_gpgv_verify(self.handoff, "trixie"))
        self.assertTrue(_gpgv_verify(self.handoff, "trixie-backports"))
        self.assertEqual(
            _read_packages(self.handoff.removesuffix("/handoff"), "trixie-backports", "main").strip(),
            "",
        )

    def test_backports_binary_resolves_source_from_backports_release(self):
        """Resolving a fallback-release binary through the full index path
        links its source from its own release (the existing backports
        fixture), not the primary release."""
        from dataclasses import replace

        from package_mgmt.lib.service.resolver import PackageResolver

        config = replace(
            _multi_release_config(self.base),
            fetch_source=True,
            max_source_dep_depth=-1,
            max_build_dep_report_depth=2,
        )
        resolver = PackageResolver(config, self.conn)
        stats = resolver.resolve("backport-tool")
        self.assertEqual(stats.errors, [])
        rows = self.conn.execute(
            "SELECT package_name, source_version, release, component FROM sources"
        ).fetchall()
        self.assertEqual(
            rows, [("backport-tool", "2.0-1", "trixie-backports", "main")],
            "a backports binary must link its source from the backports release",
        )


class TestLocalComponentHandoff(unittest.TestCase):
    """The `local` component is served through the same handoff/diff channel:
    its Packages.gz paragraph is built from the DB record + pool payload (there
    is no archive index to confirm against), and it appears in the Release
    `Components:` line and the deb/apply source lines — but only when local
    records actually exist, so an empty cache never gains a bogus component."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        make_test_iso.write_pool_files(self.base)
        self.config = config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.handoff = handoff_dir(self.config.binary_cache)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _insert_local(self, name, version, deps=None, write_payload=True, version_hash=None):
        rid = insert_binary(self.conn, name, version, component="local")
        rel = make_test_deb.pool_rel_path(name, version)
        dest = os.path.join(self.config.binary_cache, rel)
        content = b""
        if write_payload:
            content = f"local-{name}-{version}".encode()
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(content)
        if deps:
            if version_hash:
                specs: list = []
                for dep in deps:
                    specs.extend(DependencyGraph.parse_depends_line(dep))
                deps_json = serialize_depends_with_version_hash(specs, version_hash)
            else:
                deps_json = json.dumps([{"name": d, "type": "depends"} for d in deps])
            self.conn.execute(
                "UPDATE binaries SET dependencies = ? WHERE id = ?",
                (deps_json, rid),
            )
        self.conn.commit()
        return rid, rel, dest, content

    def _insert_local_deb(self, name, version, **control):
        """Register a `local` record whose payload is a real `.deb` built with
        the given control fields, at the local pool path (the record's
        `dependencies` column is left NULL — the handoff must read the
        dependency fields from the payload's own control, not the DB)."""
        rid = insert_binary(self.conn, name, version, component="local")
        rel = make_test_deb.pool_rel_path(name, version)
        dest = os.path.join(self.config.binary_cache, rel)
        make_test_deb.build_deb(dest, package=name, version=version, **control)
        self.conn.commit()
        return rid, rel, dest

    def _local_packages(self):
        path = handoff_index_path(self.config.binary_cache, "trixie", "local")
        with open(path, "rb") as f:
            return gzip.decompress(f.read()).decode("utf-8")

    def test_handoff_emits_local_component_paragraph_from_db(self):
        _rid, rel, _dest, content = self._insert_local("libfoo", "1.2-3", deps=["lib1"])

        stats = generate_handoff(self.config, self.conn)

        self.assertIn("trixie/local", stats.per_component)
        self.assertEqual(stats.per_component["trixie/local"], 1)
        self.assertEqual(stats.packages, 1)
        text = self._local_packages()
        self.assertIn("Package: libfoo", text)
        self.assertIn("Version: 1.2-3", text)
        self.assertIn("Architecture: amd64", text)
        # `_insert_local` writes a payload that is not a readable .deb, so the
        # degraded fallback applies: without stored VersionHash text the merged
        # Depends+Pre-Depends view must NOT be emitted,
        # and the affected package is flagged as a warning.
        self.assertNotIn("Depends: lib1", text)
        self.assertEqual(stats.degraded_local, ["libfoo 1.2-3"])
        self.assertIn(f"Filename: {rel}", text)
        self.assertIn(f"Size: {len(content)}", text)
        self.assertIn(f"SHA256: {hashlib.sha256(content).hexdigest()}", text)

        with open(os.path.join(self.handoff, "dists", "trixie", "Release")) as f:
            release_text = f.read()
        self.assertIn("Components: main contrib local", release_text)
        with open(handoff_script_path(self.config.binary_cache)) as f:
            script = f.read()
        self.assertIn('sources="trixie main contrib local', script)

    def test_handoff_local_payload_control_fields_carried_verbatim(self):
        """A `local` payload is the shipped .deb: the handoff Packages stanza
        carries its control's apt VersionHash fields (`Depends`, `Pre-Depends`,
        `Installed-Size`, `Conflicts`, `Breaks`, `Replaces`) verbatim, so the
        operator's dpkg-status record hashes identically and an already
        installed version is not reported as upgradable (spec 012 regression).
        The control `Depends` text wins over the DB's reconstructed specs."""
        _rid, _rel, _dest = self._insert_local_deb(
            "libfoo", "1.2-3",
            depends="lib1 (>= 2.0)",
            pre_depends="pre-lib",
            installed_size="42",
            conflicts="oldfoo",
            breaks="brokenfoo",
            replaces="replacedfoo",
        )
        rid = self.conn.execute(
            "SELECT id FROM binaries WHERE package_name = 'libfoo'"
        ).fetchone()[0]
        self.conn.execute(
            "UPDATE binaries SET dependencies = ? WHERE id = ?",
            (json.dumps([{"name": "ghost", "type": "depends"}]), rid),
        )
        self.conn.commit()

        stats = generate_handoff(self.config, self.conn)

        self.assertEqual(stats.per_component["trixie/local"], 1)
        text = self._local_packages()
        para = next(p for p in text.strip().split("\n\n") if p.startswith("Package: libfoo"))
        self.assertIn("Depends: lib1 (>= 2.0)", para)
        self.assertIn("Pre-Depends: pre-lib", para)
        self.assertIn("Installed-Size: 42", para)
        self.assertIn("Conflicts: oldfoo", para)
        self.assertIn("Breaks: brokenfoo", para)
        self.assertIn("Replaces: replacedfoo", para)
        self.assertNotIn("ghost", para, "the control Depends must win over the DB reconstruction")

    def test_handoff_local_payload_descriptive_fields_carried(self):
        """A `local` stanza is a complete package description: the payload's
        `Section`, `Maintainer` and `Description` control fields are carried
        into the emitted Packages stanza, so `apt search` can match and display
        them (they are not apt VersionHash inputs, but must not be dropped)."""
        self._insert_local_deb("libfoo", "1.2-3")

        stats = generate_handoff(self.config, self.conn)

        self.assertEqual(stats.per_component["trixie/local"], 1)
        text = self._local_packages()
        para = next(p for p in text.strip().split("\n\n") if p.startswith("Package: libfoo"))
        self.assertIn("Section: misc", para)
        self.assertIn("Maintainer: Local Patch Test <test@example.invalid>", para)
        self.assertIn("Description: Test package for the local patch lane", para)

    def test_handoff_local_depends_keeps_original_control_text(self):
        """`Depends:` is the .deb's own field text, not the resolver's merged
        view (which folds Pre-Depends targets in) — the merged text would
        change apt's VersionHash and re-open the upgradable regression."""
        _rid, _rel, _dest = self._insert_local_deb(
            "libfoo", "1.2-3",
            depends="lib1",
            pre_depends="lib1, pre-lib",
        )

        generate_handoff(self.config, self.conn)

        para = next(
            p for p in self._local_packages().strip().split("\n\n")
            if p.startswith("Package: libfoo")
        )
        self.assertIn("Depends: lib1\n", para)
        self.assertIn("Pre-Depends: lib1, pre-lib\n", para)
        depends_lines = [ln for ln in para.splitlines() if ln.startswith("Depends: ")]
        self.assertEqual(
            depends_lines, ["Depends: lib1"],
            "the merged Depends+Pre-Depends text must never be emitted",
        )

    def test_handoff_local_pre_depends_only_no_invented_depends(self):
        """A readable `local` payload whose control carries Pre-Depends but no
        `Depends` must not gain an invented `Depends:` from the DB
        reconstruction — dpkg records exactly the .deb's fields, so an
        invented `Depends` changes apt's VersionHash and the identical
        installed version is reported as upgradable (spec 012 regression)."""
        _rid, _rel, _dest = self._insert_local_deb(
            "libfoo", "1.2-3",
            pre_depends="pre-lib",
            installed_size="42",
        )
        rid = self.conn.execute(
            "SELECT id FROM binaries WHERE package_name = 'libfoo'"
        ).fetchone()[0]
        self.conn.execute(
            "UPDATE binaries SET dependencies = ? WHERE id = ?",
            (json.dumps([{"name": "ghost", "type": "depends"}]), rid),
        )
        self.conn.commit()

        generate_handoff(self.config, self.conn)

        para = next(
            p for p in self._local_packages().strip().split("\n\n")
            if p.startswith("Package: libfoo")
        )
        self.assertIn("Pre-Depends: pre-lib", para)
        self.assertIn("Installed-Size: 42", para)
        depends_lines = [ln for ln in para.splitlines() if ln.startswith("Depends: ")]
        self.assertEqual(
            depends_lines, [],
            "a readable control without Depends must not gain one from the DB",
        )

    def test_handoff_local_unreadable_payload_falls_back_to_db_reconstruction(self):
        """A `local` payload that is not a readable .deb falls back to the
        parity-preserving reconstruction from the VersionHash field text
        captured at import time (never the merged Depends+Pre-Depends view)
        instead of failing the whole generation, and flags the affected
        package as a per-package warning."""
        _rid, _rel, _dest, _content = self._insert_local(
            "libfoo", "1.2-3", deps=["lib1 (>= 2.0)"],
            version_hash={
                "Depends": "lib1 (>= 2.0)",
                "Pre-Depends": "pre-lib",
                "Installed-Size": "42",
                "Conflicts": "oldfoo",
                "Breaks": "brokenfoo",
                "Replaces": "replacedfoo",
            },
        )

        stats = generate_handoff(self.config, self.conn)

        self.assertEqual(stats.packages, 1)
        self.assertEqual(stats.degraded_local, ["libfoo 1.2-3"])
        text = self._local_packages()
        para = next(p for p in text.strip().split("\n\n") if p.startswith("Package: libfoo"))
        self.assertIn("Depends: lib1 (>= 2.0)", para)
        self.assertIn("Pre-Depends: pre-lib", para)
        self.assertIn("Installed-Size: 42", para)
        self.assertIn("Conflicts: oldfoo", para)
        self.assertIn("Breaks: brokenfoo", para)
        self.assertIn("Replaces: replacedfoo", para)

    def test_handoff_local_unreadable_payload_old_row_degrades_to_no_fields(self):
        """A pre-022 `local` row (plain dependency list, no stored VersionHash
        text) degrades gracefully: the unreadable-payload fallback emits no
        VersionHash fields, still generates, and still warns."""
        _rid, _rel, _dest, _content = self._insert_local(
            "libfoo", "1.2-3", deps=["lib1"]
        )

        stats = generate_handoff(self.config, self.conn)

        self.assertEqual(stats.packages, 1)
        self.assertEqual(stats.degraded_local, ["libfoo 1.2-3"])
        text = self._local_packages()
        self.assertIn("Package: libfoo", text)
        lines = text.splitlines()
        self.assertNotIn("Depends: lib1", lines, "the merged view must never be emitted")
        self.assertNotIn("Installed-Size:", text)
        self.assertNotIn("Conflicts:", text)
        self.assertNotIn("Breaks:", text)
        self.assertNotIn("Replaces:", text)

    def test_read_deb_control_unreadable_payload_returns_empty(self):
        """`_read_deb_control` returns `{}` for a file that is not a readable
        .deb — the handoff falls back to the DB-only reconstruction instead of
        raising."""
        from package_mgmt.lib.service.handoff import _read_deb_control

        payload = os.path.join(self.tmp.name, "not-a-deb.deb")
        with open(payload, "wb") as f:
            f.write(b"this is not an ar archive")
        self.assertEqual(_read_deb_control(payload), {})

    def test_diff_local_payload_control_fields_carried_verbatim(self):
        """The diff Packages stanza for a `local` record carries the payload's
        control VersionHash fields the same way the handoff does — the operator
        applies the diff against an already-installed dpkg-status record."""
        _rid, _rel, _dest = self._insert_local_deb(
            "libfoo", "1.2-3",
            depends="lib1",
            pre_depends="pre-lib",
            installed_size="42",
        )
        self.conn.execute("INSERT INTO systems (name, bit) VALUES ('prod-box', 2)")
        self.conn.commit()

        stats = generate_diff(self.config, self.conn, 2)

        self.assertEqual(stats.new_count, 1)
        with open(diff_index_path(self.config.binary_cache, "trixie", "local"), "rb") as f:
            text = gzip.decompress(f.read()).decode("utf-8")
        para = next(p for p in text.strip().split("\n\n") if p.startswith("Package: libfoo"))
        self.assertIn("Depends: lib1", para)
        self.assertIn("Pre-Depends: pre-lib", para)
        self.assertIn("Installed-Size: 42", para)

    def test_handoff_no_local_records_no_bogus_local_component(self):
        insert_binary(self.conn, "vifm", "1.0-2")
        self.conn.commit()

        stats = generate_handoff(self.config, self.conn)

        self.assertNotIn("trixie/local", stats.per_component)
        self.assertFalse(
            os.path.exists(handoff_index_path(self.config.binary_cache, "trixie", "local"))
        )
        with open(os.path.join(self.handoff, "dists", "trixie", "Release")) as f:
            release_text = f.read()
        self.assertIn("Components: main contrib", release_text)
        self.assertNotIn(
            "local", release_text.split("Components:", 1)[1].splitlines()[0]
        )
        with open(handoff_script_path(self.config.binary_cache)) as f:
            script = f.read()
        sources_line = next(line for line in script.splitlines() if line.startswith("sources="))
        self.assertNotIn("local", sources_line)

    def test_handoff_excludes_local_record_with_missing_pool_file(self):
        _rid, _rel, _dest, _content = self._insert_local("libfoo", "1.2-3", write_payload=False)

        with self.assertLogs("package_mgmt.lib.service.handoff", level="WARNING") as captured:
            stats = generate_handoff(self.config, self.conn)

        self.assertEqual(stats.excluded_pool, ["libfoo 1.2-3"])
        self.assertEqual(stats.packages, 0)
        messages = [r.getMessage() for r in captured.records]
        self.assertTrue(
            any("libfoo 1.2-3" in m and "missing pool file" in m for m in messages)
        )
        self.assertNotIn("Package: libfoo", self._local_packages())

    def test_diff_serves_local_component_and_ships_payload(self):
        rid, rel, _dest, content = self._insert_local("libfoo", "1.2-3")
        self.conn.execute("INSERT INTO systems (name, bit) VALUES ('prod-box', 2)")
        self.conn.commit()

        stats = generate_diff(self.config, self.conn, 2)

        self.assertEqual(stats.new_count, 1)
        self.assertEqual(stats.shipped_ids, [rid])
        self.assertEqual(stats.per_component_new, {"trixie/local": 1})
        with open(diff_index_path(self.config.binary_cache, "trixie", "local"), "rb") as f:
            text = gzip.decompress(f.read()).decode("utf-8")
        self.assertIn("Package: libfoo", text)
        with open(diff_manifest_path(self.config.binary_cache)) as f:
            manifest = f.read()
        self.assertIn(
            f"new {hashlib.sha256(content).hexdigest()} {len(content)} {rel}", manifest
        )
        with open(
            os.path.join(diff_pool_path(self.config.binary_cache), rel.removeprefix("pool/")),
            "rb",
        ) as f:
            self.assertEqual(f.read(), content)
        with open(diff_script_path(self.config.binary_cache)) as f:
            script = f.read()
        self.assertIn("trixie main contrib local", script)


if __name__ == "__main__":
    unittest.main()
