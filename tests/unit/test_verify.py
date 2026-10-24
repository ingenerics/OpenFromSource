import contextlib
import io
import json
import os
import re
import sqlite3
import tempfile
import unittest

from package_mgmt.lib.model.config import ProjectConfig
from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL
from package_mgmt.lib.model.dependency import (
    Alternative,
    DependencySpec,
    deps_satisfied,
)
from package_mgmt.lib.util.errors import EXIT_SUCCESS, EXIT_SCHEMA_MISMATCH
from package_mgmt.cli.main import build_parser
from package_mgmt.cli.commands.verify import (
    handle_verify_graph,
    _find_circular,
    _find_unlinked_binaries,
    _find_build_dep_issues,
    _find_unresolved_deps,
)


class VerifyTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(f"{self.tmp.name}/test.db")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()
        self.db_path = self.tmp.name + "/test.db"

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add_binary(self, name, deps=None, status="current", circular=0, linked=True, version="1.0"):
        deps_json = json.dumps(deps) if deps is not None else None
        cursor = self.conn.execute(
            """INSERT INTO binaries (package_name, version, architecture, release, component,
               dependencies, status, verified, circular)
               VALUES (?, ?, 'amd64', 'trixie', 'main', ?, ?, 0, ?)
               RETURNING id""",
            (name, version, deps_json, status, circular),
        )
        binary_id = cursor.fetchone()[0]
        if linked:
            src = self.conn.execute(
                """INSERT INTO sources (package_name, source_version, release, component, verified)
                   VALUES (?, '1.0', 'trixie', 'main', 0)
                   RETURNING id""",
                (name,),
            ).fetchone()[0]
            self.conn.execute(
                "INSERT INTO binary_to_source (binary_id, source_id) VALUES (?, ?)",
                (binary_id, src),
            )
        self.conn.commit()
        return binary_id

    def add_source_with_build_deps(self, binary_id, entries):
        src_id = self.conn.execute(
            """INSERT INTO sources (package_name, source_version, release, component, verified, build_depends)
               VALUES ('app-src', '1.0', 'trixie', 'main', 0, ?)
               RETURNING id""",
            (json.dumps(entries),),
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO binary_to_source (binary_id, source_id) VALUES (?, ?)",
            (binary_id, src_id),
        )
        self.conn.commit()
        return src_id

    def link_build_dep(self, src_id, binary_id, depth=1):
        self.conn.execute(
            "INSERT INTO source_build_deps (source_id, binary_id, build_dep_depth) VALUES (?, ?, ?)",
            (src_id, binary_id, depth),
        )
        self.conn.commit()

    def run_verify(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = handle_verify_graph(self.db_path)
        return exit_code, buf.getvalue()

    @staticmethod
    def _field(output: str, label: str) -> int:
        match = re.search(rf"^{re.escape(label)}:\s+(\d+)", output, re.MULTILINE)
        assert match, f"label '{label}' not found in output:\n{output}"
        return int(match.group(1))


class TestVerifyCurrentTree(VerifyTestBase):
    """verify counts and checks only status='current' rows."""

    def test_total_binaries_counts_current_only(self):
        self.add_binary("app")
        self.add_binary("old", status="superseded")
        exit_code, output = self.run_verify()
        self.assertEqual(exit_code, 0)
        self.assertEqual(self._field(output, "Total binaries"), 1)

    def test_superseded_binary_does_not_satisfy_dependency(self):
        self.add_binary("app", deps=[{"name": "libold", "type": "depends"}])
        self.add_binary("libold", status="superseded")
        exit_code, output = self.run_verify()
        self.assertNotEqual(exit_code, 0)
        self.assertIn("libold", output)

    def test_current_binary_satisfies_dependency(self):
        self.add_binary("app", deps=[{"name": "libnew", "type": "depends"}])
        self.add_binary("libnew")
        exit_code, _ = self.run_verify()
        self.assertEqual(exit_code, 0)

    def test_superseded_unlinked_binary_not_reported(self):
        self.add_binary("app", deps=[])
        self.add_binary("detached", deps=[], status="superseded", linked=False)
        unlinked = _find_unlinked_binaries(self.conn, None)
        self.assertEqual(unlinked, [], "superseded binaries must not appear as unlinked")

    def test_current_unlinked_binary_reported(self):
        self.add_binary("app", deps=[], linked=False)
        unlinked = _find_unlinked_binaries(self.conn, None)
        self.assertEqual(len(unlinked), 1)


class TestFindCircularCurrentOnly(VerifyTestBase):
    """The circular report counts only status='current' rows, so
    verify and list --deps agree on the circular set."""

    def test_find_circular_counts_only_current_rows(self):
        self.add_binary(
            "a",
            deps=[{"name": "b", "type": "depends", "circular": True}],
            circular=1,
        )
        self.add_binary(
            "old",
            deps=[{"name": "x", "type": "depends", "circular": True}],
            circular=1,
            status="superseded",
        )
        circular = _find_circular(self.conn, None)
        self.assertEqual(
            circular, ["a -> b (circular)"],
            "a superseded circular row must not be reported",
        )


class TestVerifyBuildTools(VerifyTestBase):
    """Missing and unresolved build tools are computed, not hardcoded."""

    @staticmethod
    def _entry(name):
        return {"name": name, "build_context": "build"}

    def test_missing_build_tool_reported(self):
        self.add_binary("app", deps=[])
        self.add_source_with_build_deps(1, [self._entry("debhelper-extra")])
        _, _, unresolved, missing = _find_build_dep_issues(self.conn, None)
        self.assertEqual(missing, ["debhelper-extra — required by app-src, no record in cache"])
        self.assertEqual(unresolved, [])

    def test_unresolved_build_tool_with_current_binary_reported(self):
        self.add_binary("app", deps=[])
        self.add_binary("debhelper", deps=[])
        self.add_source_with_build_deps(1, [self._entry("debhelper")])
        _, _, unresolved, missing = _find_build_dep_issues(self.conn, None)
        self.assertEqual(len(unresolved), 1)
        self.assertIn("debhelper", unresolved[0])
        self.assertEqual(missing, [])

    def test_satisfied_build_tool_not_reported(self):
        self.add_binary("app", deps=[])
        make_id = self.add_binary("make", deps=[])
        src_id = self.add_source_with_build_deps(1, [self._entry("make")])
        self.link_build_dep(src_id, make_id)
        _, _, unresolved, missing = _find_build_dep_issues(self.conn, None)
        self.assertEqual(unresolved, [])
        self.assertEqual(missing, [])

    def test_build_issues_drive_exit_code(self):
        self.add_binary("app", deps=[])
        self.add_source_with_build_deps(1, [self._entry("missing-tool-xyz")])
        exit_code, output = self.run_verify()
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(self._field(output, "Missing build tools"), 1)


class TestVerifyReportDepth(VerifyTestBase):
    """max_build_dep_report_depth Setting B — `-1` reports
    all levels, `0` reports no build-tool levels (only the always-on depth-0
    runtime check), positive `N` reports up to `N` levels, and the default
    stays 2."""

    @staticmethod
    def _dep_names(deps):
        return sorted(d.split(" — ")[0].split(" (")[0] for d in deps)

    def setUp(self):
        super().setUp()
        # app is a depth-0 runtime binary (not a build tool) with an unresolved
        # dep; tool1/tool2 are build tools linked at build_dep_depth 1 and 2.
        self.add_binary("app", deps=[{"name": "ghostlib", "type": "depends"}])
        tool1 = self.add_binary("tool1", deps=[{"name": "ghostdep1", "type": "depends"}])
        tool2 = self.add_binary("tool2", deps=[{"name": "ghostdep2", "type": "depends"}])
        src_id = self.add_source_with_build_deps(1, [])
        self.link_build_dep(src_id, tool1, depth=1)
        self.link_build_dep(src_id, tool2, depth=2)

    def test_report_all_at_minus_one(self):
        self.assertEqual(
            self._dep_names(_find_unresolved_deps(self.conn, None, -1)),
            ["ghostdep1", "ghostdep2", "ghostlib"],
            "-1 must report every level",
        )

    def test_zero_reports_only_depth0_runtime_check(self):
        self.assertEqual(
            self._dep_names(_find_unresolved_deps(self.conn, None, 0)),
            ["ghostlib"],
            "0 must report no build-tool levels — only the depth-0 runtime check",
        )

    def test_positive_n_reports_up_to_n_levels(self):
        self.assertEqual(
            self._dep_names(_find_unresolved_deps(self.conn, None, 1)),
            ["ghostdep1", "ghostlib"],
        )
        self.assertEqual(
            self._dep_names(_find_unresolved_deps(self.conn, None, 2)),
            ["ghostdep1", "ghostdep2", "ghostlib"],
        )

    def test_unset_key_uses_default_two(self):
        config = ProjectConfig()
        self.assertEqual(config.max_build_dep_report_depth, 2)
        self.assertEqual(
            self._dep_names(
                _find_unresolved_deps(self.conn, None, config.max_build_dep_report_depth)
            ),
            ["ghostdep1", "ghostdep2", "ghostlib"],
        )


class TestVerifyBuildDepReportDepth(VerifyTestBase):
    """_find_build_dep_issues respects the report depth — 0
    reports no build-tool levels, -1 reports all, N reports up to N — in
    both the full-cache and --package paths."""

    @staticmethod
    def _entry(name):
        return {"name": name, "build_context": "build"}

    def setUp(self):
        super().setUp()
        self.add_binary("app", deps=[])
        # Three sources, each with a distinct missing build tool, whose
        # shallowest build-dep link sits at depth 1, 2 and 3 respectively.
        tool1 = self.add_binary("tool1", deps=[])
        src1 = self._add_source(1, [self._entry("missing-1")])
        self.link_build_dep(src1, tool1, depth=1)
        tool2 = self.add_binary("tool2", deps=[])
        src2 = self._add_source(2, [self._entry("missing-2")])
        self.link_build_dep(src2, tool2, depth=2)
        tool3 = self.add_binary("tool3", deps=[])
        src3 = self._add_source(3, [self._entry("missing-3")])
        self.link_build_dep(src3, tool3, depth=3)

    def _add_source(self, version, entries):
        """A source named 'app-src' at a distinct source version (the
        (package_name, source_version, release, component) key is unique)."""
        src_id = self.conn.execute(
            """INSERT INTO sources (package_name, source_version, release, component, verified, build_depends)
               VALUES ('app-src', ?, 'trixie', 'main', 0, ?)
               RETURNING id""",
            (str(version), json.dumps(entries)),
        ).fetchone()[0]
        self.conn.commit()
        return src_id

    @staticmethod
    def _missing_names(missing):
        return sorted(line.split(" \u2014 ")[0] for line in missing)

    def test_zero_reports_no_build_tool_levels(self):
        _, _, unresolved, missing = _find_build_dep_issues(self.conn, None, 0)
        self.assertEqual(unresolved, [])
        self.assertEqual(self._missing_names(missing), [])

    def test_minus_one_reports_all_levels(self):
        _, _, _, missing = _find_build_dep_issues(self.conn, None, -1)
        self.assertEqual(
            self._missing_names(missing), ["missing-1", "missing-2", "missing-3"]
        )

    def test_positive_n_reports_up_to_n_levels(self):
        _, _, _, missing = _find_build_dep_issues(self.conn, None, 1)
        self.assertEqual(self._missing_names(missing), ["missing-1"])
        _, _, _, missing = _find_build_dep_issues(self.conn, None, 2)
        self.assertEqual(self._missing_names(missing), ["missing-1", "missing-2"])

    def test_package_path_applies_the_same_filter(self):
        _, _, _, missing = _find_build_dep_issues(self.conn, "app-src", 0)
        self.assertEqual(self._missing_names(missing), [])
        _, _, _, missing = _find_build_dep_issues(self.conn, "app-src", 1)
        self.assertEqual(self._missing_names(missing), ["missing-1"])
        _, _, _, missing = _find_build_dep_issues(self.conn, "app-src", -1)
        self.assertEqual(
            self._missing_names(missing), ["missing-1", "missing-2", "missing-3"]
        )


class TestVerifyPackageDepth(VerifyTestBase):
    """verify --package X applies the report-depth filter to the package's
    build-tool levels exactly like the full-cache path."""

    def setUp(self):
        super().setUp()
        # "app" is a runtime binary that is also used as a build tool at
        # depth 2; its runtime dep ghostlib is unresolved.
        app_id = self.add_binary("app", deps=[{"name": "ghostlib", "type": "depends"}])
        src_id = self.add_source_with_build_deps(app_id, [])
        self.link_build_dep(src_id, app_id, depth=2)

    def test_minus_one_reports_build_tool_level(self):
        self.assertEqual(
            _find_unresolved_deps(self.conn, "app", -1),
            ["ghostlib \u2014 no binary record for matching version"],
        )

    def test_two_reports_build_tool_level(self):
        self.assertEqual(
            _find_unresolved_deps(self.conn, "app", 2),
            ["ghostlib \u2014 no binary record for matching version"],
        )

    def test_zero_filters_build_tool_level(self):
        self.assertEqual(_find_unresolved_deps(self.conn, "app", 0), [])

    def test_one_filters_build_tool_level(self):
        self.assertEqual(_find_unresolved_deps(self.conn, "app", 1), [])


class TestVerifyFlagRemoved(unittest.TestCase):
    """verify --graph is a usage error (the flag is removed); bare verify is
    the single graph-verification command."""

    def test_graph_flag_is_usage_error(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["verify", "--graph"])

    def test_bare_verify_parses(self):
        parser = build_parser()
        args = parser.parse_args(["verify"])
        self.assertEqual(args.command, "verify")
        self.assertFalse(hasattr(args, "graph"))


class TestVerifyDepthGate(VerifyTestBase):
    """At a finite max_source_dep_depth, binaries without a
    binary_to_source link are informational-only (never defects) and the cache
    reports COMPLETE; at `-1` unlinked binaries are defects."""

    def _run(self, max_source_depth=-1, max_report_depth=0):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = handle_verify_graph(
                self.db_path, None, max_report_depth, max_source_depth
            )
        return exit_code, buf.getvalue()

    def test_finite_depth_unlinked_are_informational_only(self):
        self.add_binary("app", deps=[], linked=False)
        self.add_binary("lib", deps=[])
        exit_code, output = self._run(max_source_depth=1)
        self.assertEqual(exit_code, 0, "out-of-scope binaries are never defects")
        self.assertIn("Status:                 COMPLETE", output)
        self.assertIn("Runtime Status:         COMPLETE", output)
        self.assertEqual(self._field(output, "Unlinked binaries"), 0)
        self.assertIn("Beyond source depth:", output)
        self.assertIn("app (= 1.0)", output)

    def test_zero_depth_unlinked_are_informational_only(self):
        self.add_binary("app", deps=[], linked=False)
        exit_code, output = self._run(max_source_depth=0)
        self.assertEqual(exit_code, 0)
        self.assertIn("Beyond source depth:", output)
        self.assertEqual(self._field(output, "Beyond source depth"), 1)

    def test_minus_one_keeps_strict_unlinked_check(self):
        self.add_binary("app", deps=[], linked=False)
        self.add_binary("lib", deps=[])
        exit_code, output = self._run(max_source_depth=-1)
        self.assertNotEqual(exit_code, 0, "at -1 missing sources are defects")
        self.assertEqual(self._field(output, "Unlinked binaries"), 1)
        self.assertIn("Runtime Status:         INCOMPLETE", output)
        self.assertNotIn("Beyond source depth:", output)

    def test_finite_depth_in_scope_missing_source_is_buildable(self):
        """An in-scope source whose build tool is missing still
        flips the build status — only the *unlinked* gate is relaxed at a
        finite depth."""
        self.add_binary("app", deps=[], linked=False)
        self.add_source_with_build_deps(1, [{"name": "missing-tool", "build_context": "build"}])
        exit_code, output = self._run(max_source_depth=1)
        self.assertNotEqual(exit_code, 0, "missing build tools are still defects")
        self.assertIn("Missing build tools:    1", output)


if __name__ == "__main__":
    unittest.main()


class TestVerifyVirtualBuildTools(VerifyTestBase):
    """Build-tool satisfaction is provider- and constraint-aware."""

    def _add_provides(self, name, version, virtual, provides_version=None):
        self.conn.execute(
            """INSERT INTO provides (package_name, version, architecture, release, component,
               virtual_name, provides_version)
               VALUES (?, ?, 'amd64', 'trixie', 'main', ?, ?)""",
            (name, version, virtual, provides_version),
        )
        self.conn.commit()

    @staticmethod
    def _entry(name, constraint=None, alternatives=None):
        e = {"name": name, "build_context": "build"}
        if constraint:
            e["version_constraint"] = constraint
        if alternatives:
            e["alternatives"] = alternatives
        return e

    def test_virtual_build_dep_satisfied_by_provider_link(self):
        self.add_binary("app", deps=[])
        debhelper_id = self.add_binary("debhelper", deps=[])
        self._add_provides("debhelper", "1.0", "debhelper-compat", "13")
        src_id = self.add_source_with_build_deps(1, [self._entry("debhelper-compat", {"operator": "=", "version": "13"})])
        self.link_build_dep(src_id, debhelper_id)

        _, _, unresolved, missing = _find_build_dep_issues(self.conn, None)
        self.assertEqual(unresolved, [])
        self.assertEqual(missing, [], "link to the provider binary must satisfy the virtual build dep")

    def test_virtual_build_dep_with_matching_provider_but_no_link(self):
        self.add_binary("app", deps=[])
        self.add_binary("debhelper", deps=[])
        self._add_provides("debhelper", "1.0", "debhelper-compat", "13")
        self.add_source_with_build_deps(1, [self._entry("debhelper-compat", {"operator": "=", "version": "13"})])

        _, _, unresolved, missing = _find_build_dep_issues(self.conn, None)
        self.assertEqual(len(unresolved), 1, "provider exists in cache but is not linked")
        self.assertIn("debhelper-compat", unresolved[0])
        self.assertEqual(missing, [])

    def test_constraint_unsatisfiable_by_provider_is_missing(self):
        self.add_binary("app", deps=[])
        self.add_binary("debhelper", deps=[])
        self._add_provides("debhelper", "1.0", "debhelper-compat", "9")
        src_id = self.add_source_with_build_deps(1, [self._entry("debhelper-compat", {"operator": "=", "version": "13"})])
        self.link_build_dep(src_id, self._last_binary_id("debhelper"))

        _, _, unresolved, missing = _find_build_dep_issues(self.conn, None)
        self.assertEqual(len(missing), 1, "(= 13) vs provides_version 9 must not be satisfied by the link")

    def _last_binary_id(self, name):
        return self.conn.execute(
            "SELECT id FROM binaries WHERE package_name = ? ORDER BY id DESC LIMIT 1", (name,)
        ).fetchone()[0]

    def test_build_dep_alternative_satisfied_in_cache(self):
        self.add_binary("app", deps=[])
        self.add_binary("liblua5.4-dev", deps=[])
        self.add_source_with_build_deps(
            1, [self._entry("liblua5.1-dev", alternatives=["liblua5.4-dev"])]
        )

        _, _, unresolved, missing = _find_build_dep_issues(self.conn, None)
        self.assertEqual(len(unresolved), 1,
                         "the alternative is usable in the cache but this source is not linked to it")
        self.assertIn("liblua5.4-dev", unresolved[0])
        self.assertEqual(missing, [], "an available alternative must not be reported as missing")

    def test_build_dep_alternative_linked_is_satisfied(self):
        self.add_binary("app", deps=[])
        alt_id = self.add_binary("liblua5.4-dev", deps=[])
        src_id = self.add_source_with_build_deps(
            1, [self._entry("liblua5.1-dev", alternatives=["liblua5.4-dev"])]
        )
        self.link_build_dep(src_id, alt_id)

        _, _, unresolved, missing = _find_build_dep_issues(self.conn, None)
        self.assertEqual(unresolved, [])
        self.assertEqual(missing, [], "a linked alternative fully satisfies the build dep")

    def test_build_dep_alternative_none_available_is_missing(self):
        self.add_binary("app", deps=[])
        self.add_source_with_build_deps(
            1, [self._entry("liblua5.1-dev", alternatives=["liblua5.4-dev"])]
        )

        _, _, unresolved, missing = _find_build_dep_issues(self.conn, None)
        self.assertEqual(len(missing), 1)
        self.assertIn("liblua5.1-dev | liblua5.4-dev", missing[0])

    def test_build_dep_alternative_own_constraint_unsatisfied_by_link(self):
        """A dict-form alternative is checked under its OWN
        constraint — a linked binary at a non-matching version must not
        satisfy it."""
        self.add_binary("app", deps=[])
        self.add_binary("liblua5.4-dev", version="1.0")
        src_id = self.add_source_with_build_deps(
            1,
            [{
                "name": "liblua5.1-dev",
                "build_context": "build",
                "alternatives": [{
                    "name": "liblua5.4-dev",
                    "version_constraint": {"operator": "=", "version": "2.0"},
                }],
            }],
        )
        self.link_build_dep(src_id, self._last_binary_id("liblua5.4-dev"))

        _, _, unresolved, missing = _find_build_dep_issues(self.conn, None)
        self.assertEqual(
            unresolved, [],
            "the link to liblua5.4-dev 1.0 must not satisfy the (= 2.0) alternative",
        )
        self.assertEqual(len(missing), 1, "only the (= 2.0) alternative can be reported")
        self.assertIn("liblua5.1-dev | liblua5.4-dev", missing[0])

    def test_build_dep_alternative_own_constraint_satisfied_by_link(self):
        """A dict-form alternative IS satisfied when the linked
        binary's version meets its own constraint."""
        self.add_binary("app", deps=[])
        self.add_binary("liblua5.4-dev", version="2.0")
        src_id = self.add_source_with_build_deps(
            1,
            [{
                "name": "liblua5.1-dev",
                "build_context": "build",
                "alternatives": [{
                    "name": "liblua5.4-dev",
                    "version_constraint": {"operator": "=", "version": "2.0"},
                }],
            }],
        )
        self.link_build_dep(src_id, self._last_binary_id("liblua5.4-dev"))

        _, _, unresolved, missing = _find_build_dep_issues(self.conn, None)
        self.assertEqual(unresolved, [])
        self.assertEqual(missing, [], "a linked alternative at its matching version satisfies the build dep")


class TestVerifyCircularInformational(VerifyTestBase):
    """Circular dependencies are reported but do not fail verification."""

    def test_circular_dep_does_not_flip_exit_code(self):
        self.add_binary("app", deps=[{"name": "lib", "type": "depends"}], circular=1)
        self.add_binary("lib", deps=[{"name": "app", "type": "depends"}], circular=1)
        exit_code, output = self.run_verify()
        self.assertEqual(exit_code, 0, "resolved cycles in real Debian trees are informational")
        self.assertIn("Circular dependencies:", output)

    def test_runtime_virtual_constraint_unsatisfied_reported(self):
        self.add_binary("app", deps=[{"name": "awk", "type": "depends",
                                      "version_constraint": {"operator": ">=", "version": "9.0"}}])
        self.add_binary("gawk", deps=[])
        self.conn.execute(
            "INSERT INTO provides (package_name, version, architecture, release, component, virtual_name)"
            " VALUES ('gawk', '5.2.1', 'amd64', 'trixie', 'main', 'awk')"
        )
        self.conn.commit()
        exit_code, _ = self.run_verify()
        self.assertNotEqual(exit_code, 0, "provider 5.2.1 must not satisfy awk (>= 9.0)")


class TestVerifyRuntimeAlternatives(VerifyTestBase):
    """Runtime dependency alternatives are satisfied by providers too."""

    def test_alternative_satisfied_via_provider(self):
        self.add_binary(
            "librust-object-dev",
            deps=[{
                "name": "librust-hashbrown-0.15+default-dev",
                "type": "depends",
                "alternatives": ["librust-hashbrown-0.14+default-dev"],
            }],
        )
        self.add_binary("librust-hashbrown-dev", deps=[])
        self.conn.execute(
            """INSERT INTO provides (package_name, version, architecture, release, component, virtual_name, provides_version)
               VALUES ('librust-hashbrown-dev', '1.0', 'amd64', 'trixie', 'main', 'librust-hashbrown-0.14+default-dev', '1.0')"""
        )
        self.conn.commit()

        exit_code, output = self.run_verify()
        self.assertEqual(exit_code, 0, "the alternative's provider must satisfy the dependency")
        self.assertNotIn("librust-hashbrown-0.15+default-dev", output)

    def test_alternative_virtual_without_provider_reported(self):
        self.add_binary(
            "librust-object-dev",
            deps=[{
                "name": "librust-hashbrown-0.15+default-dev",
                "type": "depends",
                "alternatives": ["librust-hashbrown-0.14+default-dev"],
            }],
        )
        exit_code, output = self.run_verify()
        self.assertNotEqual(exit_code, 0, "no provider for either alternative must be unresolved")
        self.assertIn("librust-hashbrown-0.15+default-dev", output)


class TestVerifyVersionConstraints(VerifyTestBase):
    """verify reports a version-constrained direct-name
    dependency satisfied only when a current binary's version satisfies the
    constraint — not on mere presence — and walks alternatives under their own
    constraints. Providers keep their documented over-inclusive behavior."""

    @staticmethod
    def _spec(name, constraint=None, alternatives=()):
        return DependencySpec(
            name=name,
            version_constraint=constraint,
            alternatives=alternatives,
        )

    def test_too_old_binary_fails_ge_constraint(self):
        spec = self._spec("libfoo", {"operator": ">=", "version": "2.0"})
        self.add_binary("app", deps=[spec.to_json()])
        self.add_binary("libfoo", version="1.5")
        self.assertFalse(
            deps_satisfied(self.conn, "libfoo", spec),
            "a current binary too old for (>= 2.0) must not satisfy the dep",
        )
        exit_code, output = self.run_verify()
        self.assertNotEqual(exit_code, 0, "an unsatisfied version constraint must exit non-zero")
        self.assertIn("Runtime Status:         INCOMPLETE", output)
        self.assertIn("libfoo (>= 2.0)", output)

    def test_exact_constraint_satisfied_is_complete(self):
        spec = self._spec("libfoo", {"operator": "=", "version": "1.5"})
        self.add_binary("app", deps=[spec.to_json()])
        self.add_binary("libfoo", version="1.5")
        self.assertTrue(deps_satisfied(self.conn, "libfoo", spec))
        exit_code, output = self.run_verify()
        self.assertEqual(exit_code, 0, "an exact (= 1.5) match must be COMPLETE")
        self.assertIn("Runtime Status:         COMPLETE", output)

    def test_exact_constraint_mismatch_is_incomplete(self):
        spec = self._spec("libfoo", {"operator": "=", "version": "1.5"})
        self.add_binary("app", deps=[spec.to_json()])
        self.add_binary("libfoo", version="1.4")
        self.assertFalse(deps_satisfied(self.conn, "libfoo", spec))
        exit_code, _ = self.run_verify()
        self.assertNotEqual(exit_code, 0)

    def test_alternative_satisfied_by_second_under_own_constraint(self):
        spec = DependencySpec(
            name="ghost",
            alternatives=(
                Alternative(
                    name="libfoo",
                    version_constraint={"operator": "=", "version": "2.0"},
                ),
            ),
        )
        self.add_binary("app", deps=[spec.to_json()])
        self.add_binary("libfoo", version="2.0")
        exit_code, output = self.run_verify()
        self.assertEqual(
            exit_code, 0,
            "the group must be satisfied by the second alternative under its own constraint",
        )
        self.assertIn("Runtime Status:         COMPLETE", output)

    def test_alternative_own_constraint_unsatisfied_is_incomplete(self):
        spec = DependencySpec(
            name="ghost",
            alternatives=(
                Alternative(
                    name="libfoo",
                    version_constraint={"operator": "=", "version": "2.0"},
                ),
            ),
        )
        self.add_binary("app", deps=[spec.to_json()])
        self.add_binary("libfoo", version="1.5")
        exit_code, output = self.run_verify()
        self.assertNotEqual(
            exit_code, 0,
            "an alternative that fails its own constraint must leave the group unsatisfied",
        )
        self.assertIn("Runtime Status:         INCOMPLETE", output)

    def test_versionless_direct_dep_still_satisfied(self):
        spec = self._spec("libnew")
        self.add_binary("app", deps=[spec.to_json()])
        self.add_binary("libnew")
        exit_code, output = self.run_verify()
        self.assertEqual(exit_code, 0, "a versionless direct dep with any current binary stays satisfied")
        self.assertIn("Runtime Status:         COMPLETE", output)

    def test_provider_satisfied_dep_stays_over_inclusive(self):
        spec = self._spec("awk", {"operator": ">=", "version": "5.0"})
        self.add_binary("app", deps=[spec.to_json()])
        self.add_binary("gawk", version="5.2.1")
        self.conn.execute(
            "INSERT INTO provides (package_name, version, architecture, release, component, virtual_name)"
            " VALUES ('gawk', '5.2.1', 'amd64', 'trixie', 'main', 'awk')"
        )
        self.conn.commit()
        exit_code, output = self.run_verify()
        self.assertEqual(
            exit_code, 0,
            "a provider whose version satisfies the constraint (unversioned provides) stays satisfied",
        )
        self.assertIn("Runtime Status:         COMPLETE", output)


class TestVerifyGate(unittest.TestCase):
    """verify gates on cache initialization like its siblings: with no
    database it returns 101, prints a clean error, and creates no file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "package_mgmt.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_database_exits_101_no_file(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = handle_verify_graph(self.db_path)
        self.assertEqual(rc, EXIT_SCHEMA_MISMATCH)
        self.assertIn("Database not found", err.getvalue())
        self.assertFalse(os.path.exists(self.db_path), "no database file may be created")

    def test_valid_database_unchanged(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(EXPECTED_SCHEMA_SQL)
        conn.commit()
        conn.close()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = handle_verify_graph(self.db_path)
        self.assertEqual(rc, EXIT_SUCCESS)
        self.assertIn("Total packages:         0", out.getvalue())
