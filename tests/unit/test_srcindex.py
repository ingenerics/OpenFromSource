"""Unit tests for the operator source-channel index (`srcindex`).

Covers generation of the handoff `source/Sources` (deb-src) index for the
`local` component from verified DB `sources` rows: correct paragraph fields
(`Package`/`Version`/`Directory`/`Files:`/`Checksums-Sha256:` matching the
pool payloads), `Sources.gz` + plain outputs, stable (version, name) ordering,
byte-determinism, and the handoff wiring — the index hash-listed + signed in
the handoff `Release`, reachable through the handoff's `source` link, and the
bootstrap `deb-src` line for `local`.
"""

import gzip
import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_deb  # noqa: E402
import make_test_iso  # noqa: E402
from db import config, insert_binary  # noqa: E402

from package_mgmt.lib.service.db_init import initialize_database  # noqa: E402
from package_mgmt.lib.service.handoff import BOOTSTRAP_SCRIPT, generate_handoff  # noqa: E402
from package_mgmt.lib.service.srcindex import (  # noqa: E402
    local_source_pool_dir,
    source_index_text,
    source_paragraph,
    verified_local_sources,
    write_source_index,
)
from package_mgmt.lib.util.paths import (  # noqa: E402
    handoff_script_path,
    handoff_source_link,
)


def _insert_local_source(conn, name, version, release="trixie", verified=1) -> int:
    cur = conn.execute(
        "INSERT INTO sources (package_name, source_version, release, component, verified)"
        " VALUES (?, ?, ?, 'local', ?) RETURNING id",
        (name, version, release, verified),
    )
    return cur.fetchone()[0]


def _write_source_set(config, name, version, files) -> str:
    """Write `files` (filename -> bytes) into the local source pool dir for
    `name` (the layout `import_local_deb` produces: the `.dsc` + referenced
    tarballs copied flat). Returns the pool directory."""
    directory = local_source_pool_dir(config, name)
    os.makedirs(directory, exist_ok=True)
    for fname, content in files.items():
        with open(os.path.join(directory, fname), "wb") as f:
            f.write(content)
    return directory


class TestSrcIndexGeneration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        make_test_iso.write_pool_files(self.base)
        self.config = config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _fixture_source(self, name="demo-tool", version="1.0.0-1") -> dict[str, bytes]:
        files = {
            f"{name}_{version}.dsc": f"dsc {name}".encode(),
            f"{name}_1.0.0.orig.tar.gz": b"orig tarball content",
            f"{name}_{version}.debian.tar.xz": b"debian packaging content",
        }
        _insert_local_source(self.conn, name, version)
        self.conn.commit()
        _write_source_set(self.config, name, version, files)
        return files

    def test_paragraph_fields_from_db_rows_and_pool(self):
        files = self._fixture_source()
        rows = verified_local_sources(self.conn, "trixie")
        self.assertEqual(rows, [("demo-tool", "1.0.0-1")])
        para = source_paragraph(self.conn, self.config, "demo-tool", "1.0.0-1")
        self.assertIsNotNone(para)
        self.assertIn("Package: demo-tool", para)
        self.assertIn("Version: 1.0.0-1", para)
        self.assertIn("Directory: source/pool/local/d/demo-tool", para)
        for fname, content in files.items():
            self.assertIn(
                f" {hashlib.md5(content).hexdigest()} {len(content)} {fname}", para
            )
            self.assertIn(
                f" {hashlib.sha256(content).hexdigest()} {len(content)} {fname}", para
            )

    def test_stable_ordering_by_version_then_name(self):
        for name, version in (("alpha", "1.0-1"), ("beta", "1.0-1"), ("zeta", "2.0-1")):
            _insert_local_source(self.conn, name, version)
            _write_source_set(
                self.config, name, version, {f"{name}_{version}.dsc": b"dsc"}
            )
        self.conn.commit()
        text = source_index_text(self.conn, self.config, "trixie")
        self.assertLess(text.index("Package: alpha"), text.index("Package: beta"))
        self.assertLess(text.index("Package: beta"), text.index("Package: zeta"))

    def test_byte_determinism(self):
        self._fixture_source()
        first = source_index_text(self.conn, self.config, "trixie")
        second = source_index_text(self.conn, self.config, "trixie")
        self.assertEqual(first, second)

    def test_write_source_index_plain_and_gz_with_matching_hashes(self):
        self._fixture_source()
        component_root = os.path.join(self.tmp.name, "dists", "trixie", "local")
        entries = write_source_index(self.conn, self.config, "trixie", "local", component_root)
        plain_path = os.path.join(component_root, "source", "Sources")
        gz_path = os.path.join(component_root, "source", "Sources.gz")
        self.assertTrue(os.path.isfile(plain_path))
        self.assertTrue(os.path.isfile(gz_path))
        text = source_index_text(self.conn, self.config, "trixie")
        with open(plain_path) as f:
            self.assertEqual(f.read(), text)
        with open(gz_path, "rb") as f:
            self.assertEqual(gzip.decompress(f.read()).decode("utf-8"), text)
        self.assertIn("local/source/Sources", entries)
        self.assertIn("local/source/Sources.gz", entries)
        with open(plain_path, "rb") as f:
            self.assertEqual(
                entries["local/source/Sources"]["SHA256"],
                hashlib.sha256(f.read()).hexdigest(),
            )
        with open(gz_path, "rb") as f:
            self.assertEqual(
                entries["local/source/Sources.gz"]["SHA256"],
                hashlib.sha256(f.read()).hexdigest(),
            )
        self.assertEqual(entries["local/source/Sources"]["size"], os.path.getsize(plain_path))

    def test_unverified_source_row_excluded(self):
        _insert_local_source(self.conn, "wip", "0.1-1", verified=0)
        self.conn.commit()
        _write_source_set(self.config, "wip", "0.1-1", {"wip_0.1-1.dsc": b"dsc"})
        self.assertEqual(verified_local_sources(self.conn, "trixie"), [])
        self.assertEqual(source_index_text(self.conn, self.config, "trixie"), "")

    def test_missing_pool_files_skipped(self):
        _insert_local_source(self.conn, "ghost", "9.9-1")
        self.conn.commit()
        self.assertIsNone(source_paragraph(self.conn, self.config, "ghost", "9.9-1"))
        self.assertEqual(source_index_text(self.conn, self.config, "trixie"), "")

    def test_bootstrap_write_sources_entry_emits_deb_src_for_local(self):
        """The operator `bootstrap.sh` writes a `deb-src` line for `local`
        alongside the `deb` line, and no deb-src line when `local` is absent."""
        script = os.path.join(self.tmp.name, "bootstrap.sh")
        with open(script, "w") as f:
            f.write(BOOTSTRAP_SCRIPT)
        entry = os.path.join(self.tmp.name, "openfromsource-cache.list")
        result = subprocess_run(
            ["bash", "-c",
             f"source {shlex_quote(script)}; "
             f"write_sources_entry {shlex_quote(entry)} /cache/handoff 'trixie main contrib local'"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(entry) as f:
            content = f.read()
        self.assertIn(
            "deb [signed-by=/cache/handoff/offline-cache.gpg] file:/cache/handoff trixie main contrib local\n",
            content,
        )
        self.assertIn(
            "deb-src [signed-by=/cache/handoff/offline-cache.gpg] file:/cache/handoff trixie local\n",
            content,
        )
        no_local_entry = os.path.join(self.tmp.name, "no-local.list")
        result = subprocess_run(
            ["bash", "-c",
             f"source {shlex_quote(script)}; "
             f"write_sources_entry {shlex_quote(no_local_entry)} /cache/handoff 'trixie main contrib'"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(no_local_entry) as f:
            self.assertNotIn("deb-src", f.read())


class TestSrcIndexHandoffWiring(unittest.TestCase):
    """The generated handoff carries the operator's source channel: the
    `local/source/Sources[.gz]` index is written, hash-listed + signed in the
    `Release`, reachable through the handoff's `source` link, and the
    bootstrap's sources config carries the `deb-src` line for `local`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        make_test_iso.write_pool_files(self.base)
        self.config = config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _insert_local_binary(self, name, version):
        rid = insert_binary(self.conn, name, version, component="local")
        rel = make_test_deb.pool_rel_path(name, version)
        dest = os.path.join(self.config.binary_cache, rel)
        make_test_deb.build_deb(dest, package=name, version=version)
        self.conn.commit()
        return rid, rel, dest

    def _fixture_local_pair(self):
        self._insert_local_binary("demo-tool", "1.0.0-1")
        _insert_local_source(self.conn, "demo-tool", "1.0.0-1")
        self.conn.commit()
        _write_source_set(
            self.config, "demo-tool", "1.0.0-1",
            {
                "demo-tool_1.0.0-1.dsc": b"dsc",
                "demo-tool_1.0.0.orig.tar.gz": b"orig",
                "demo-tool_1.0.0-1.debian.tar.xz": b"debian",
            },
        )

    def test_handoff_emits_source_index_hash_lists_and_deb_src(self):
        self._fixture_local_pair()
        generate_handoff(self.config, self.conn)

        handoff = os.path.join(self.config.binary_cache, "handoff")
        src_dir = os.path.join(handoff, "dists", "trixie", "local", "source")
        self.assertTrue(os.path.isfile(os.path.join(src_dir, "Sources")))
        self.assertTrue(os.path.isfile(os.path.join(src_dir, "Sources.gz")))
        with open(os.path.join(src_dir, "Sources"), "rb") as f:
            plain = f.read()
        self.assertIn(b"Package: demo-tool", plain)
        self.assertIn(b"Directory: source/pool/local/d/demo-tool", plain)

        # hash-listed in the Release alongside Packages
        with open(os.path.join(handoff, "dists", "trixie", "Release")) as f:
            release = f.read()
        for rel_path in ("local/source/Sources", "local/source/Sources.gz"):
            path = os.path.join(handoff, "dists", "trixie", rel_path)
            with open(path, "rb") as f:
                data = f.read()
            self.assertIn(
                f"{hashlib.sha256(data).hexdigest()} {len(data):16d} {rel_path}", release
            )

        # the source channel is reachable through the handoff's source link
        link = handoff_source_link(self.config.binary_cache)
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.path.realpath(link), os.path.join(self.base, "source"))

        # the bootstrap carries the deb-src emission and the local component
        with open(handoff_script_path(self.config.binary_cache)) as f:
            script = f.read()
        self.assertIn('sources="trixie main contrib local', script)
        self.assertIn("deb-src", script)

    def test_handoff_releases_source_index_byte_identical(self):
        self._fixture_local_pair()
        old = os.environ.get("SOURCE_DATE_EPOCH")
        os.environ["SOURCE_DATE_EPOCH"] = "1750000000"
        try:
            generate_handoff(self.config, self.conn)
            path = os.path.join(
                self.config.binary_cache, "handoff", "dists", "trixie",
                "local", "source", "Sources.gz",
            )
            with open(path, "rb") as f:
                first = f.read()
            generate_handoff(self.config, self.conn)
            with open(path, "rb") as f:
                second = f.read()
        finally:
            if old is None:
                os.environ.pop("SOURCE_DATE_EPOCH", None)
            else:
                os.environ["SOURCE_DATE_EPOCH"] = old
        self.assertEqual(first, second, "regeneration must be byte-identical")


def subprocess_run(cmd):
    import subprocess

    return subprocess.run(cmd, capture_output=True, text=True)


def shlex_quote(value):
    import shlex

    return shlex.quote(value)


if __name__ == "__main__":
    unittest.main()
