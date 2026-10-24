import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fixtures"))
import make_test_iso  # noqa: E402
from db import config  # noqa: E402

from package_mgmt.lib.service.db_init import initialize_database  # noqa: E402
from package_mgmt.lib.service.handoff import BOOTSTRAP_SCRIPT, generate_handoff  # noqa: E402


class BootstrapScriptUnitTest(unittest.TestCase):
    """Spec 012: the operator `bootstrap.sh` is unit-tested by sourcing its
    plain functions with explicit scratch paths — no test-only hooks in the
    shipped script, no root, no host modification."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.script = os.path.join(self.tmp.name, "bootstrap.sh")
        with open(self.script, "w") as f:
            f.write(BOOTSTRAP_SCRIPT)

    def tearDown(self):
        self.tmp.cleanup()

    def _bash(self, code: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-c", code], capture_output=True, text=True)

    def _call(self, fn: str, *args: str) -> subprocess.CompletedProcess:
        call = f"{fn} " + " ".join(shlex.quote(str(a)) for a in args)
        return self._bash(f"source {shlex.quote(self.script)}; {call}")

    def _scratch_handoff(self) -> tuple[str, str]:
        """A structurally complete scratch handoff (existence only): the
        per-release Release + Release.gpg + Packages.gz, the cache pool dir,
        and the `handoff/pool -> ../pool` symlink the generator ships."""
        cache = os.path.join(self.tmp.name, "cache")
        handoff = os.path.join(cache, "handoff")
        for rel in ("trixie",):
            rel_dir = os.path.join(handoff, "dists", rel)
            os.makedirs(rel_dir, exist_ok=True)
            with open(os.path.join(rel_dir, "Release"), "w") as f:
                f.write("release")
            with open(os.path.join(rel_dir, "Release.gpg"), "w") as f:
                f.write("sig")
            for comp in ("main", "contrib"):
                idx = os.path.join(rel_dir, comp, "binary-amd64")
                os.makedirs(idx, exist_ok=True)
                with open(os.path.join(idx, "Packages.gz"), "w") as f:
                    f.write("gz")
        os.makedirs(os.path.join(cache, "pool"), exist_ok=True)
        os.symlink("../pool", os.path.join(handoff, "pool"))
        return cache, handoff

    def _os_release(self, codename: str | None = None) -> str:
        path = os.path.join(self.tmp.name, "os-release")
        if codename is None:
            return path
        with open(path, "w") as f:
            f.write(f"PRETTY_NAME=\"OpenFromSource test\"\nVERSION_CODENAME={codename}\n")
        return path

    def _entry(self) -> str:
        return os.path.join(self.tmp.name, "sources.list.d", "openfromsource-cache.list")

    # -- handoff_complete -------------------------------------------------

    def test_handoff_complete_ok(self):
        cache, _ = self._scratch_handoff()
        result = self._call("handoff_complete", os.path.join(cache, "handoff"),
                            cache, "trixie main contrib")
        self.assertEqual(result.returncode, 0)

    def test_handoff_complete_missing_release_gpg(self):
        cache, handoff = self._scratch_handoff()
        os.remove(os.path.join(handoff, "dists", "trixie", "Release.gpg"))
        result = self._call("handoff_complete", handoff, cache, "trixie main contrib")
        self.assertEqual(result.returncode, 1)

    def test_handoff_complete_missing_release(self):
        """handoff_complete fails when a release's `Release`
        file is missing (in addition to `Release.gpg`)."""
        cache, handoff = self._scratch_handoff()
        os.remove(os.path.join(handoff, "dists", "trixie", "Release"))
        result = self._call("handoff_complete", handoff, cache, "trixie main contrib")
        self.assertEqual(result.returncode, 1)

    def test_handoff_complete_missing_pool_link(self):
        """handoff_complete fails when the `handoff/pool`
        link does not resolve to a directory — the pool is unreachable through
        the handoff (e.g. the symlink was stripped by a linkless medium)."""
        cache, handoff = self._scratch_handoff()
        os.remove(os.path.join(handoff, "pool"))
        result = self._call("handoff_complete", handoff, cache, "trixie main contrib")
        self.assertEqual(result.returncode, 1)

    def test_handoff_complete_missing_component_index(self):
        cache, handoff = self._scratch_handoff()
        os.remove(os.path.join(handoff, "dists", "trixie", "main", "binary-amd64", "Packages.gz"))
        result = self._call("handoff_complete", handoff, cache, "trixie main contrib")
        self.assertEqual(result.returncode, 1)

    def test_handoff_complete_missing_pool(self):
        cache, handoff = self._scratch_handoff()
        shutil.rmtree(os.path.join(cache, "pool"))
        result = self._call("handoff_complete", handoff, cache, "trixie main contrib")
        self.assertEqual(result.returncode, 1)

    # -- release_match ----------------------------------------------------

    def test_release_match_matching_codename(self):
        result = self._call("release_match", self._os_release("trixie"), "trixie trixie-backports")
        self.assertEqual(result.returncode, 0)

    def test_release_match_mismatch(self):
        result = self._call("release_match", self._os_release("bookworm"), "trixie")
        self.assertEqual(result.returncode, 2)

    def test_release_match_absent_os_release_skips_check(self):
        result = self._call("release_match", self._os_release(None), "trixie")
        self.assertEqual(result.returncode, 0)

    def test_release_match_cannot_determine_codename(self):
        path = os.path.join(self.tmp.name, "os-release")
        with open(path, "w") as f:
            f.write("PRETTY_NAME=\"OpenFromSource test\"\n")
        result = self._call("release_match", path, "trixie")
        self.assertEqual(result.returncode, 1)

    def test_system_codename_parses_quoted_and_unquoted(self):
        for value in ('"trixie"', "trixie"):
            path = os.path.join(self.tmp.name, "os-release")
            with open(path, "w") as f:
                f.write(f"VERSION_CODENAME={value}\n")
            result = self._call("system_codename", path)
            self.assertEqual(result.stdout.strip(), "trixie")

    # -- write_sources_entry / remove_sources_entry -----------------------

    def test_write_sources_entry_single_release(self):
        entry = self._entry()
        result = self._call("write_sources_entry", entry, "/cache/handoff", "trixie main contrib")
        self.assertEqual(result.returncode, 0)
        with open(entry) as f:
            self.assertEqual(
                f.read(),
                "deb [signed-by=/cache/handoff/offline-cache.gpg] file:/cache/handoff trixie main contrib\n",
            )

    def test_write_sources_entry_multi_release(self):
        entry = self._entry()
        self._call(
            "write_sources_entry", entry, "/cache/handoff",
            "trixie main contrib\ntrixie-backports main",
        )
        with open(entry) as f:
            self.assertEqual(
                f.read(),
                "deb [signed-by=/cache/handoff/offline-cache.gpg] file:/cache/handoff trixie main contrib\n"
                "deb [signed-by=/cache/handoff/offline-cache.gpg] file:/cache/handoff trixie-backports main\n",
            )

    def test_write_sources_entry_is_idempotent_no_duplicates(self):
        entry = self._entry()
        self._call("write_sources_entry", entry, "/cache/handoff", "trixie main contrib")
        self._call("write_sources_entry", entry, "/cache/handoff", "trixie main contrib")
        with open(entry) as f:
            self.assertEqual(
                f.read(),
                "deb [signed-by=/cache/handoff/offline-cache.gpg] file:/cache/handoff trixie main contrib\n",
            )
        leftovers = [
            n for n in os.listdir(os.path.dirname(entry))
            if n != "openfromsource-cache.list"
        ]
        self.assertEqual(leftovers, [], "no temp files may remain")

    def test_write_sources_entry_refuses_space_containing_path(self):
        """A handoff dir containing a space is refused with a clear
        diagnostic and no `deb` line is written — the whitespace-delimited
        apt source line would otherwise break."""
        entry = self._entry()
        result = self._call("write_sources_entry", entry, "/media/My USB/cache/handoff",
                            "trixie main contrib")
        self.assertEqual(result.returncode, 1)
        self.assertIn("space", result.stderr.lower())
        self.assertIn("My USB", result.stderr)
        self.assertFalse(os.path.exists(entry), "no apt source may be written from a space-containing path")

    def test_write_sources_entry_emits_deb_src_for_local(self):
        """A `local` component gets a matching `deb-src` line (the operator's
        source channel, spec 024 Q3) alongside the `deb` line."""
        entry = self._entry()
        result = self._call(
            "write_sources_entry", entry, "/cache/handoff", "trixie main contrib local"
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

    def test_write_sources_entry_no_deb_src_without_local(self):
        """Without a `local` component no `deb-src` line is emitted."""
        entry = self._entry()
        result = self._call(
            "write_sources_entry", entry, "/cache/handoff", "trixie main contrib"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(entry) as f:
            self.assertNotIn("deb-src", f.read())

    def test_handoff_complete_requires_local_source_index(self):
        """When the operator's source channel is configured (local is served),
        `handoff_complete` requires the `local/source/Sources.gz` index to
        exist — the deb-src line must never point at a missing index."""
        cache, handoff = self._scratch_handoff()
        # Serve `local` with a binary index but no source index.
        local = os.path.join(handoff, "dists", "trixie", "local", "binary-amd64")
        os.makedirs(local, exist_ok=True)
        with open(os.path.join(local, "Packages.gz"), "w") as f:
            f.write("gz")
        result = self._call(
            "handoff_complete", handoff, cache, "trixie main contrib local"
        )
        self.assertEqual(result.returncode, 1)
        # Once the source index exists the handoff is complete.
        src = os.path.join(handoff, "dists", "trixie", "local", "source")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "Sources.gz"), "w") as f:
            f.write("gz")
        result = self._call(
            "handoff_complete", handoff, cache, "trixie main contrib local"
        )
        self.assertEqual(result.returncode, 0)

    def test_remove_sources_entry_deletes_only_itself(self):
        d = os.path.join(self.tmp.name, "sources.list.d")
        os.makedirs(d, exist_ok=True)
        entry, other = os.path.join(d, "openfromsource-cache.list"), os.path.join(d, "other.list")
        for path in (entry, other):
            with open(path, "w") as f:
                f.write("deb [signed-by=/x/offline-cache.gpg] file:/x trixie main\n")
        result = self._call("remove_sources_entry", entry)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(entry))
        self.assertTrue(os.path.exists(other), "unrelated files must be untouched")

    # -- bootstrap_configure ordering (validation before write) ------------

    def test_configure_preflight_failure_writes_nothing(self):
        cache, handoff = self._scratch_handoff()
        os.remove(os.path.join(handoff, "dists", "trixie", "Release.gpg"))
        entry = self._entry()
        result = self._call("bootstrap_configure", cache, entry, "/nonexistent",
                            "trixie main contrib", "trixie")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"Handoff not found at {cache}/handoff — is the cache complete?", result.stderr)
        self.assertFalse(os.path.exists(entry), "nothing may be written on preflight failure")

    def test_configure_release_mismatch_writes_nothing(self):
        cache, _ = self._scratch_handoff()
        entry = self._entry()
        result = self._call("bootstrap_configure", cache, entry, self._os_release("bookworm"),
                            "trixie main contrib", "trixie")
        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "Release mismatch: cache is for trixie, this system is bookworm. Refusing to configure.",
            result.stderr,
        )
        self.assertFalse(os.path.exists(entry), "nothing may be written on mismatch")

    # -- ensure_pool_link / unreachable pool -------------------------------

    def test_ensure_pool_link_fails_hard_when_ln_fails(self):
        """When the pool symlink cannot be created (a
        linkless medium such as FAT), ensure_pool_link fails hard — non-zero
        exit with a clear, actionable message about the unreachable pool."""
        cache, handoff = self._scratch_handoff()
        code = (
            f"source {shlex.quote(self.script)};"
            " ln() { echo 'linkless media' >&2; return 1; };"
            f" rm -f {shlex.quote(handoff)}/pool;"
            f" ensure_pool_link {shlex.quote(handoff)}"
        )
        result = self._bash(code)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pool", result.stderr.lower())
        self.assertIn("symlink", result.stderr.lower())
        self.assertFalse(os.path.exists(os.path.join(handoff, "pool")))

    def test_bootstrap_never_configures_source_with_unreachable_pool(self):
        """On linkless media the bootstrap fails hard and
        writes no apt source — it never reports success while the pool is
        unreachable through the handoff."""
        cache, handoff = self._scratch_handoff()
        entry = self._entry()
        os.remove(os.path.join(handoff, "pool"))
        code = (
            f"source {shlex.quote(self.script)};"
            " ln() { echo 'linkless media' >&2; return 1; };"
            f" bootstrap_configure {shlex.quote(cache)} {shlex.quote(entry)}"
            " /nonexistent 'trixie main contrib' trixie"
        )
        result = self._bash(code)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(entry), "no apt source may be configured when the pool is unreachable")

    # -- bootstrap_main arg parsing / root check --------------------------

    def test_main_unknown_option_errors(self):
        result = self._call("bootstrap_main", "--bogus")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr.strip(),
            "No such option: --bogus. Usage: bootstrap.sh [<cache-dir>] [--remove]",
        )

    @unittest.skipIf(os.geteuid() == 0, "non-root refusal needs a non-root user")
    def test_main_non_root_refusal(self):
        result = self._call("bootstrap_main")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Not running as root — run with sudo.", result.stderr)


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("apt-get") and shutil.which("gpg")
    and shutil.which("gpgv"),
    "bash, apt-get and gpg/gpgv required",
)
class BootstrapConfigureAptTest(unittest.TestCase):
    """bootstrap_configure against a real generated
    handoff — apt update success and the failure path (unreadable index →
    entry left in place, clear error, exit 3), isolated via a scratch state
    root."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        make_test_iso.write_pool_files(self.base)
        self.config = config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.conn.execute(
            "INSERT INTO binaries (package_name, version, architecture, release,"
            " component, status, verified)"
            " VALUES ('vifm', '1.0-2', 'amd64', 'trixie', 'main', 'current', 1)"
        )
        self.conn.commit()
        generate_handoff(self.config, self.conn)
        self.script = os.path.join(self.tmp.name, "bootstrap.sh")
        with open(self.script, "w") as f:
            f.write(BOOTSTRAP_SCRIPT)
        self.cache = self.config.binary_cache
        self.entry = os.path.join(self.tmp.name, "sources.list.d", "openfromsource-cache.list")
        self.state_root = os.path.join(self.tmp.name, "state")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _configure(self, sources_file: str) -> subprocess.CompletedProcess:
        code = (
            f"source {shlex.quote(self.script)};"
            f" bootstrap_configure {shlex.quote(self.cache)} {shlex.quote(sources_file)}"
            f" /nonexistent trixie\\ main\\ contrib trixie {shlex.quote(self.state_root)}"
        )
        return subprocess.run(["bash", "-c", code], capture_output=True, text=True)

    def test_configure_success_writes_entry_and_runs_apt(self):
        result = self._configure(self.entry)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        with open(self.entry) as f:
            self.assertEqual(
                f.read(),
                f"deb [signed-by={os.path.realpath(self.cache)}/handoff/offline-cache.gpg]"
                f" file:{os.path.realpath(self.cache)}/handoff"
                " trixie main contrib\n",
            )
        link = os.path.join(self.cache, "handoff", "pool")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.path.realpath(link), os.path.join(self.cache, "pool"))

    def test_apt_update_failure_leaves_entry_in_place(self):
        index = os.path.join(
            self.cache, "handoff", "dists", "trixie", "main", "binary-amd64", "Packages.gz"
        )
        os.chmod(index, 0)
        try:
            result = self._configure(self.entry)
        finally:
            os.chmod(index, 0o644)
        self.assertEqual(result.returncode, 3)
        self.assertIn(
            "apt update failed — see output above; the source entry was left in place"
            " for inspection.",
            result.stderr,
        )
        self.assertTrue(
            os.path.exists(self.entry),
            "the source entry must remain after an apt update failure",
        )


if __name__ == "__main__":
    unittest.main()
