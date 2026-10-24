import hashlib
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
from package_mgmt.lib.service.handoff import (  # noqa: E402
    APPLY_SCRIPT,
    generate_diff,
    generate_handoff,
)


class ApplyScriptUnitTest(unittest.TestCase):
    """The operator `apply.sh` is unit-tested by sourcing
    its plain functions with explicit scratch paths — no test-only hooks, no
    root, no host modification (contracts/cli.md Testability section).

    Diff structure preflight refusal, release-mismatch
    refusal, the non-root refusal, copy + verify (a new file enters the
    operator's pool only after its SHA256/size match the manifest; temp +
    rename leaves no partial files), and the per-file atomic replacement of
    the handoff dists with the diff's complete ones (offline-cache.gpg
    copied over when the handoff lacks one).

    Ordering (a `delivered` base-set file missing from the
    operator's pool refuses the apply before anything changes),
    corrupt-diff refusal (a tampered manifest line), existing-file
    conflict refusal, idempotent re-apply, and interrupted-apply
    convergence — a re-run converges to the same state as a single clean run
    with no partial temp files.
    """

    RELEASES = "trixie"
    SOURCES = "trixie main contrib"
    LABEL = "release trixie"
    DELIVERED_REL = "pool/main/v/vifm/vifm_1.0-2_amd64.deb"
    NEW_REL = "pool/main/a/app/app_1.5-1_amd64.deb"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.script = os.path.join(self.tmp.name, "apply.sh")
        content = (
            APPLY_SCRIPT
            .replace("__RELEASES__", self.RELEASES)
            .replace("__SOURCES__", self.SOURCES)
            .replace("__RELEASE_LABEL__", self.LABEL)
        )
        with open(self.script, "w") as f:
            f.write(content)

    def tearDown(self):
        self.tmp.cleanup()

    def _bash(self, code: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-c", code], capture_output=True, text=True)

    def _call(self, fn: str, *args: str) -> subprocess.CompletedProcess:
        call = f"{fn} " + " ".join(shlex.quote(str(a)) for a in args)
        return self._bash(f"source {shlex.quote(self.script)}; {call}")

    def _sha(self, path: str) -> str:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def _write(self, rel: str, content: str) -> str:
        path = os.path.join(self.tmp.name, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return path

    def _scratch(self) -> tuple[str, str, str]:
        """A structurally complete scratch operator cache + diff.

        The operator cache has a pool holding one delivered base file and a
        handoff whose dists/ indexes will be replaced. The diff carries the
        same delivered file in its manifest (base set) plus one new pool file
        and complete indexes with distinguishable content, so the replace
        test can assert the handoff files were actually swapped.
        """
        cache = os.path.join(self.tmp.name, "cache")
        pool = os.path.join(cache, "pool")
        handoff = os.path.join(cache, "handoff")
        diff = os.path.join(cache, "diff")

        delivered_path = os.path.join(pool, self.DELIVERED_REL.removeprefix("pool/"))
        new_path = os.path.join(pool, self.NEW_REL.removeprefix("pool/"))
        os.makedirs(os.path.dirname(delivered_path), exist_ok=True)
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        with open(delivered_path, "w") as f:
            f.write("delivered-deb-bytes")

        diff_delivered = os.path.join(diff, self.DELIVERED_REL)
        diff_new = os.path.join(diff, self.NEW_REL)
        os.makedirs(os.path.dirname(diff_delivered), exist_ok=True)
        os.makedirs(os.path.dirname(diff_new), exist_ok=True)
        # The diff carries only the new file physically; the delivered file
        # is the base set (checked by existence in the operator's pool).
        with open(diff_new, "w") as f:
            f.write("new-deb-bytes")

        for base in (handoff, diff):
            rel_dir = os.path.join(base, "dists", "trixie")
            os.makedirs(rel_dir, exist_ok=True)
            for name, content in (
                ("Release", "OLD-RELEASE" if base == handoff else "NEW-RELEASE"),
                ("Release.gpg", "OLD-SIG" if base == handoff else "NEW-SIG"),
            ):
                with open(os.path.join(rel_dir, name), "w") as f:
                    f.write(content)
            for comp in ("main", "contrib"):
                idx = os.path.join(rel_dir, comp, "binary-amd64")
                os.makedirs(idx, exist_ok=True)
                with open(os.path.join(idx, "Packages.gz"), "w") as f:
                    f.write("OLD-INDEX" if base == handoff else "NEW-INDEX")

        with open(os.path.join(diff, "offline-cache.gpg"), "w") as f:
            f.write("ARMORED-KEY")
        with open(os.path.join(diff, "manifest.txt"), "w") as f:
            f.write(
                f"delivered {self._sha(delivered_path)} {os.path.getsize(delivered_path)} {self.DELIVERED_REL}\n"
                f"new {self._sha(diff_new)} {os.path.getsize(diff_new)} {self.NEW_REL}\n"
            )
        return cache, handoff, diff

    def _os_release(self, codename: str | None = None) -> str:
        path = os.path.join(self.tmp.name, "os-release")
        if codename is None:
            return path
        with open(path, "w") as f:
            f.write(f"PRETTY_NAME=\"OpenFromSource test\"\nVERSION_CODENAME={codename}\n")
        return path

    def _apply_configure(self, cache: str, os_release: str,
                         state_root: str = "") -> subprocess.CompletedProcess:
        code = (
            f"source {shlex.quote(self.script)};"
            f" apply_configure {shlex.quote(cache)} '' {shlex.quote(os_release)}"
            f" {shlex.quote(self.SOURCES)} {shlex.quote(self.RELEASES)}"
            + (f" {shlex.quote(state_root)}" if state_root else "")
        )
        return subprocess.run(["bash", "-c", code], capture_output=True, text=True)

    def _leftover_temps(self, root: str) -> list[str]:
        found = []
        for dirpath, _, names in os.walk(root):
            for name in names:
                if ".apply." in name:
                    found.append(os.path.join(dirpath, name))
        return found

    # -- diff structure preflight ------------------------------------------

    def test_apply_configure_refuses_missing_manifest(self):
        cache, _, diff = self._scratch()
        os.remove(os.path.join(diff, "manifest.txt"))
        result = self._apply_configure(cache, self._os_release(None))
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"Diff not found at {cache}/diff — is the diff complete?", result.stderr)
        self.assertFalse(
            os.path.exists(os.path.join(cache, "pool", "main", "a", "app", "app_1.5-1_amd64.deb")),
            "nothing may be copied on a preflight failure",
        )

    def test_apply_configure_refuses_missing_release_gpg(self):
        cache, _, diff = self._scratch()
        os.remove(os.path.join(diff, "dists", "trixie", "Release.gpg"))
        result = self._apply_configure(cache, self._os_release(None))
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"Diff not found at {cache}/diff — is the diff complete?", result.stderr)

    def test_apply_configure_refuses_missing_component_index(self):
        cache, _, diff = self._scratch()
        os.remove(os.path.join(diff, "dists", "trixie", "main", "binary-amd64", "Packages.gz"))
        result = self._apply_configure(cache, self._os_release(None))
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"Diff not found at {cache}/diff — is the diff complete?", result.stderr)

    def test_apply_configure_refuses_missing_pool(self):
        cache, _, diff = self._scratch()
        shutil.rmtree(os.path.join(diff, "pool"))
        result = self._apply_configure(cache, self._os_release(None))
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"Diff not found at {cache}/diff — is the diff complete?", result.stderr)

    # -- release mismatch ---------------------------------------------------

    def test_apply_configure_release_mismatch(self):
        cache, handoff, _ = self._scratch()
        handoff_index = os.path.join(
            handoff, "dists", "trixie", "main", "binary-amd64", "Packages.gz"
        )
        with open(handoff_index) as f:
            before = f.read()
        result = self._apply_configure(cache, self._os_release("bookworm"))
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "Release mismatch: diff is for trixie, this system is bookworm. Refusing to apply.",
            result.stderr,
        )
        with open(handoff_index) as f:
            self.assertEqual(f.read(), before,
                             "nothing may change on a release mismatch")

    # -- apply_main non-root refusal ----------------------------------------

    @unittest.skipIf(os.geteuid() == 0, "non-root refusal needs a non-root user")
    def test_apply_main_non_root_refusal(self):
        result = self._call("apply_main")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Not running as root — run with sudo.", result.stderr)

    # -- copy + verify ------------------------------------------------------

    def test_copy_new_file_verified_into_pool(self):
        cache, _, diff = self._scratch()
        result = self._call("copy_new_files", diff, cache)
        self.assertEqual(result.returncode, 0, result.stderr)
        dest = os.path.join(cache, "pool", "main", "a", "app", "app_1.5-1_amd64.deb")
        self.assertTrue(os.path.isfile(dest), "the new file must land in the operator's pool")
        with open(dest) as f:
            self.assertEqual(f.read(), "new-deb-bytes")
        self.assertEqual(self._leftover_temps(cache), [], "no partial temp files may remain")

    def test_copy_refuses_tampered_new_file(self):
        cache, _, diff = self._scratch()
        new_file = os.path.join(diff, "pool", "main", "a", "app", "app_1.5-1_amd64.deb")
        with open(new_file, "w") as f:
            f.write("tampered-bytes")
        expected = hashlib.sha256(b"new-deb-bytes").hexdigest()
        actual = hashlib.sha256(b"tampered-bytes").hexdigest()
        result = self._call("copy_new_files", diff, cache)
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            f"Checksum mismatch for {self.NEW_REL}: expected {expected}, got {actual}. Refusing to apply.",
            result.stderr,
        )
        dest = os.path.join(cache, "pool", "main", "a", "app", "app_1.5-1_amd64.deb")
        self.assertFalse(os.path.exists(dest), "a tampered file must never enter the pool")
        self.assertEqual(self._leftover_temps(cache), [], "no partial temp files may remain")

    # -- replace handoff dists ----------------------------------------------

    def test_replace_handoff_dists_atomic_per_file(self):
        cache, handoff, diff = self._scratch()
        self.assertFalse(
            os.path.exists(os.path.join(handoff, "offline-cache.gpg")),
            "precondition: the handoff lacks offline-cache.gpg",
        )
        result = self._call("replace_handoff_dists", diff, cache, self.SOURCES)
        self.assertEqual(result.returncode, 0, result.stderr)

        for rel in ("main", "contrib"):
            index = os.path.join(
                handoff, "dists", "trixie", rel, "binary-amd64", "Packages.gz"
            )
            with open(index) as f:
                self.assertEqual(f.read(), "NEW-INDEX",
                                 "handoff Packages.gz must be the diff's complete one")
        for name in ("Release", "Release.gpg"):
            path = os.path.join(handoff, "dists", "trixie", name)
            with open(path) as f:
                self.assertEqual(f.read(), "NEW-" + ("RELEASE" if name == "Release" else "SIG"))
        key = os.path.join(handoff, "offline-cache.gpg")
        self.assertTrue(os.path.isfile(key),
                        "offline-cache.gpg must be copied over when the handoff lacks one")
        self.assertEqual(self._leftover_temps(handoff), [], "replacement must be atomic, no temp files")

    # -- ordering / integrity -------------------------------------------------

    def test_apply_configure_refuses_missing_delivered_base_set(self):
        cache, handoff, _ = self._scratch()
        delivered = os.path.join(
            cache, "pool", "main", "v", "vifm", "vifm_1.0-2_amd64.deb"
        )
        os.remove(delivered)
        result = self._apply_configure(cache, self._os_release(None))
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            f"Missing intermediate sync: {self.DELIVERED_REL} is not in the cache pool"
            " — an earlier diff must be applied first. Refusing to apply.",
            result.stderr,
        )
        self.assertFalse(
            os.path.exists(os.path.join(cache, "pool", "main", "a", "app", "app_1.5-1_amd64.deb")),
            "nothing may be copied when the base set is incomplete",
        )
        with open(os.path.join(
            handoff, "dists", "trixie", "main", "binary-amd64", "Packages.gz"
        )) as f:
            self.assertEqual(f.read(), "OLD-INDEX", "the handoff must be untouched")

    def test_copy_refuses_corrupt_manifest_line(self):
        cache, _, diff = self._scratch()
        manifest = os.path.join(diff, "manifest.txt")
        with open(manifest) as f:
            lines = f.read().splitlines()
        wrong_sha = "0" * 64
        with open(manifest, "w") as f:
            for line in lines:
                parts = line.split()
                if parts[0] == "new":
                    f.write(f"new {wrong_sha} {parts[2]} {parts[3]}\n")
                else:
                    f.write(line + "\n")
        actual = hashlib.sha256(b"new-deb-bytes").hexdigest()
        result = self._call("copy_new_files", diff, cache)
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            f"Checksum mismatch for {self.NEW_REL}: expected {wrong_sha}, got {actual}. Refusing to apply.",
            result.stderr,
        )
        dest = os.path.join(cache, "pool", "main", "a", "app", "app_1.5-1_amd64.deb")
        self.assertFalse(os.path.exists(dest), "nothing may be copied from a corrupt manifest")

    def test_copy_refuses_existing_file_conflict(self):
        cache, _, diff = self._scratch()
        dest = os.path.join(cache, "pool", "main", "a", "app", "app_1.5-1_amd64.deb")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as f:
            f.write("different-bytes")
        result = self._call("copy_new_files", diff, cache)
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            f"Existing file conflict: {self.NEW_REL} is already in the pool with a"
            " different checksum. Refusing to apply.",
            result.stderr,
        )
        with open(dest) as f:
            self.assertEqual(f.read(), "different-bytes",
                             "the conflicting file must never be overwritten")

    # -- idempotency / failure convergence ------------------------------------

    def test_rerun_of_applied_diff_is_idempotent(self):
        cache, _, diff = self._scratch()
        first = self._call("copy_new_files", diff, cache)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout.strip(), "1")
        dest = os.path.join(cache, "pool", "main", "a", "app", "app_1.5-1_amd64.deb")
        with open(dest) as f:
            content_before = f.read()

        second = self._call("copy_new_files", diff, cache)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout.strip(), "0",
                         "a re-apply must copy nothing (idempotent)")
        with open(dest) as f:
            self.assertEqual(f.read(), content_before,
                             "the already-applied file must be left unchanged")
        self.assertEqual(self._leftover_temps(cache), [], "no temp files after the re-apply")

    def test_interrupted_apply_converges_on_rerun(self):
        cache, handoff, diff = self._scratch()
        # Simulate a mid-copy interruption: a partial temp file where the new
        # file lands in the pool, and the handoff dists partially replaced
        # (main Packages.gz already swapped, Release/Release.gpg/contrib not),
        # with a stale temp file left in the handoff dists too.
        pool_app_dir = os.path.join(cache, "pool", "main", "a", "app")
        os.makedirs(pool_app_dir, exist_ok=True)
        with open(os.path.join(pool_app_dir, ".apply.partial.12345"), "w") as f:
            f.write("partial")
        main_index = os.path.join(
            handoff, "dists", "trixie", "main", "binary-amd64", "Packages.gz"
        )
        with open(main_index, "w") as f:
            f.write("NEW-INDEX")
        with open(os.path.join(handoff, "dists", "trixie", ".apply.stale.6789"), "w") as f:
            f.write("partial-index")

        result = self._call("copy_new_files", diff, cache)
        self.assertEqual(result.returncode, 0, result.stderr)
        result2 = self._call("replace_handoff_dists", diff, cache, self.SOURCES)
        self.assertEqual(result2.returncode, 0, result2.stderr)

        dest = os.path.join(cache, "pool", "main", "a", "app", "app_1.5-1_amd64.deb")
        with open(dest) as f:
            self.assertEqual(f.read(), "new-deb-bytes",
                             "the destination must hold the complete verified file")
        for rel in ("main", "contrib"):
            with open(os.path.join(
                handoff, "dists", "trixie", rel, "binary-amd64", "Packages.gz"
            )) as f:
                self.assertEqual(f.read(), "NEW-INDEX", "handoff indexes must be consistent")
        self.assertEqual(self._leftover_temps(cache), [],
                         "no partial temp files may remain after convergence")
        self.assertEqual(self._leftover_temps(handoff), [],
                         "no partial temp files may remain after convergence")


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("apt-get") and shutil.which("gpg")
    and shutil.which("gpgv"),
    "bash, apt-get and gpg/gpgv required",
)
class _RealDiffApplyTest(unittest.TestCase):
    """Shared fixture for exercising the real `apply_configure`: a real
    generated handoff + diff (vifm delivered, app pending) with the operator
    cache at the baseline and the shipped apply.sh sourced against a scratch
    apt state root (the contracts/cli.md testability convention)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "cache")
        make_test_iso.write_archive(self.base)
        make_test_iso.write_pool_files(self.base)
        self.config = config(self.base)
        initialize_database(self.config.db_path)
        self.conn = sqlite3.connect(self.config.db_path)
        self.conn.execute("INSERT INTO systems (name, bit) VALUES ('prod-box', 2)")
        # Baseline: vifm verified + delivered (sync_mask bit 2) -> the full
        # handoff ships it alone; then app is added pending -> the diff ships
        # app as the only new file.
        self.conn.execute(
            "INSERT INTO binaries (package_name, version, architecture, release,"
            " component, status, verified, sync_mask)"
            " VALUES ('vifm', '1.0-2', 'amd64', 'trixie', 'main', 'current', 1, 2)"
        )
        self.conn.commit()
        generate_handoff(self.config, self.conn)
        self.conn.execute(
            "INSERT INTO binaries (package_name, version, architecture, release,"
            " component, status, verified)"
            " VALUES ('app', '1.5-1', 'amd64', 'trixie', 'main', 'current', 1)"
        )
        self.conn.commit()
        generate_diff(self.config, self.conn, 2)

        # Operator cache at the baseline: pool holds only the delivered vifm
        # file, handoff is the baseline, and the diff was copied in.
        self.op = os.path.join(self.tmp.name, "operator")
        delivered_src = os.path.join(
            self.config.binary_cache, "pool", "main", "v", "vifm", "vifm_1.0-2_amd64.deb"
        )
        delivered_dst = os.path.join(
            self.op, "pool", "main", "v", "vifm", "vifm_1.0-2_amd64.deb"
        )
        os.makedirs(os.path.dirname(delivered_dst), exist_ok=True)
        shutil.copy(delivered_src, delivered_dst)
        shutil.copytree(
            os.path.join(self.config.binary_cache, "handoff"),
            os.path.join(self.op, "handoff"),
            symlinks=True,
        )
        shutil.copytree(os.path.join(self.config.binary_cache, "diff"), os.path.join(self.op, "diff"))

        self.script = os.path.join(self.tmp.name, "apply.sh")
        with open(self.script, "w") as f:
            f.write(
                APPLY_SCRIPT
                .replace("__RELEASES__", "trixie")
                .replace("__SOURCES__", "trixie main contrib")
                .replace("__RELEASE_LABEL__", "release trixie")
            )
        self.sources_list = os.path.join(self.tmp.name, "sources.list")
        with open(self.sources_list, "w") as f:
            f.write(f"deb [signed-by={self.op}/handoff/offline-cache.gpg] file:{self.op}/handoff trixie main contrib\n")
        self.state_root = os.path.join(self.tmp.name, "state")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _apply(self) -> subprocess.CompletedProcess:
        code = (
            f"source {shlex.quote(self.script)};"
            f" apply_configure {shlex.quote(self.op)} {shlex.quote(self.sources_list)}"
            f" /nonexistent 'trixie main contrib' trixie {shlex.quote(self.state_root)}"
        )
        return subprocess.run(["bash", "-c", code], capture_output=True, text=True)

    def _call(self, fn: str, *args: str) -> subprocess.CompletedProcess:
        call = f"{fn} " + " ".join(shlex.quote(str(a)) for a in args)
        return subprocess.run(
            ["bash", "-c", f"source {shlex.quote(self.script)}; {call}"],
            capture_output=True, text=True,
        )


class ApplyScriptSuccessTest(_RealDiffApplyTest):
    """The operator's merge prints a clean, parseable count: `apply_configure`'s
    stdout is exactly the copied-file count, stderr is empty, apt's output goes
    to the transient log file, and the summary block is exactly
    `New files: <number>` with the already-applied state on a separate line —
    so a successful apply never prints a shell integer-comparison error and the
    already-applied branch is reachable."""

    def test_success_path_stdout_is_exactly_the_count(self):
        result = self._apply()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(
            result.stdout.strip().splitlines(), ["1"],
            "stdout must carry only the copied-file count (apt output must not leak)",
        )
        self.assertEqual(result.stderr.strip(), "", "stderr must be empty on success")
        self.assertNotIn("integer expression expected", result.stdout + result.stderr)
        log = os.path.join(self.op, "diff", ".apply-apt.log")
        self.assertTrue(os.path.isfile(log), "apt output must land in the log file")
        with open(log) as f:
            self.assertTrue(f.read().strip(), "the log must hold apt's output")

    def test_rerun_on_already_applied_diff_returns_zero(self):
        first = self._apply()
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.assertEqual(first.stdout.strip().splitlines(), ["1"])
        second = self._apply()
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(
            second.stdout.strip().splitlines(), ["0"],
            "a re-apply must report 0 files copied (already-applied branch)",
        )
        self.assertEqual(second.stderr.strip(), "", "the already-applied branch must be clean")
        self.assertNotIn("integer expression expected", second.stdout + second.stderr)

    def test_apply_summary_count_line_and_already_applied_line(self):
        merged = self._call("apply_summary", "7", "342", "7", "trixie")
        self.assertEqual(merged.returncode, 0, merged.stderr)
        merged_lines = merged.stdout.splitlines()
        self.assertEqual(merged_lines[0], "New files:       7",
                         "the count line must be exactly `New files: <number>`")
        self.assertNotIn("copied into pool", merged_lines[0])
        self.assertNotIn("(already applied)", merged_lines[0])
        self.assertIn("Offered after:   349 packages (7 new)", merged_lines)

        already = self._call("apply_summary", "0", "349", "0", "trixie")
        self.assertEqual(already.returncode, 0, already.stderr)
        already_lines = already.stdout.splitlines()
        self.assertEqual(already_lines[0], "New files:       0",
                         "a zero-file diff must report 0")
        self.assertEqual(
            already_lines[1], "Already applied: all new files already present in the pool",
            "the already-applied state must be reported on a separate line",
        )
        self.assertIn("Offered after:   349 packages (0 new)", already_lines)


class ApplyScriptAptFailureTest(_RealDiffApplyTest):
    """An `apt update` failure after the merge leaves the
    merged files and indexes in place for inspection, with the exact contract
    error text (naming the log) and a non-zero return. Runs `apply_configure`
    against a real generated handoff + diff with a scratch apt state root (the
    contracts/cli.md testability convention)."""

    def test_apt_update_failure_leaves_merged_state_in_place(self):
        # Induce a post-merge apt failure: make the diff's main index
        # unreadable, so the atomic replace cannot restore it and apt cannot
        # parse the resulting index — while the merge itself (pool + the
        # replaceable indexes) has already happened.
        index = os.path.join(
            self.op, "diff", "dists", "trixie", "main", "binary-amd64", "Packages.gz"
        )
        os.chmod(index, 0)
        try:
            result = self._apply()
        finally:
            os.chmod(index, 0o644)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            f"apt update failed — see {self.op}/diff/.apply-apt.log (excerpt below);"
            " the merged files and indexes were left in place for inspection.",
            result.stderr,
        )
        log = os.path.join(self.op, "diff", ".apply-apt.log")
        self.assertTrue(os.path.isfile(log), "the apt log must exist on failure")
        with open(log) as f:
            log_text = f.read()
        self.assertTrue(log_text.strip(), "the log must hold apt's failure output")
        excerpt = [ln for ln in result.stderr.splitlines()
                   if ln.strip() and "apt update failed" not in ln]
        self.assertTrue(excerpt, "stderr must carry a tailed excerpt of the log")
        self.assertTrue(
            any(ln in log_text for ln in excerpt),
            "stderr must tail an excerpt from the apt log",
        )
        # The merged state must remain for inspection: the new file is
        # in the pool and the handoff Release was swapped for the diff's
        # (which carries the app index hash the baseline lacked).
        self.assertTrue(
            os.path.isfile(os.path.join(self.op, "pool", "main", "a", "app", "app_1.5-1_amd64.deb")),
            "the merged pool file must be left in place",
        )
        with open(os.path.join(self.op, "handoff", "dists", "trixie", "Release"), "rb") as f:
            handoff_release = f.read()
        with open(os.path.join(self.op, "diff", "dists", "trixie", "Release"), "rb") as f:
            diff_release = f.read()
        self.assertEqual(handoff_release, diff_release,
                         "the handoff Release must be the merged (diff) one")


if __name__ == "__main__":
    unittest.main()
