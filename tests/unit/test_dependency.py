import sqlite3
import tempfile
import unittest

from package_mgmt.lib.model.database import EXPECTED_SCHEMA_SQL
from package_mgmt.lib.model.dependency import (
    Alternative,
    DependencyGraph,
    DependencySpec,
    deps_satisfied,
    deserialize_depends,
    parse_build_depends_field,
    provider_satisfies_constraint,
    serialize_depends,
)


class TestPerAlternativeConstraints(unittest.TestCase):
    """Every alternative in a group keeps its own version constraint through
    parse, serialize, and deserialize."""

    def test_second_alternative_keeps_its_own_constraint(self):
        specs = DependencyGraph.parse_depends_line("foo (= 9.9) | bar (= 2.0)")
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec.name, "foo")
        self.assertEqual(spec.version_constraint, {"operator": "=", "version": "9.9"})
        self.assertEqual(len(spec.alternatives), 1)
        alt = spec.alternatives[0]
        self.assertIsInstance(alt, Alternative)
        self.assertEqual(alt.name, "bar")
        self.assertEqual(alt.version_constraint, {"operator": "=", "version": "2.0"})
        self.assertIsNone(alt.architecture)

    def test_round_trip_preserves_every_alternative_constraint(self):
        specs = DependencyGraph.parse_depends_line("foo (>= 1.0) | bar (= 2.0) | baz")
        round_tripped = deserialize_depends(serialize_depends(specs))
        self.assertEqual(len(round_tripped), 1)
        spec = round_tripped[0]
        self.assertEqual([a.name for a in spec.alternatives], ["bar", "baz"])
        self.assertEqual(
            spec.alternatives[0].version_constraint,
            {"operator": "=", "version": "2.0"},
        )
        self.assertIsNone(spec.alternatives[1].version_constraint)
        self.assertEqual(spec.version_constraint, {"operator": ">=", "version": "1.0"})

    def test_alternative_without_constraint_is_none(self):
        specs = DependencyGraph.parse_depends_line("foo | bar")
        alt = specs[0].alternatives[0]
        self.assertEqual(alt.name, "bar")
        self.assertIsNone(alt.version_constraint)
        self.assertIsNone(alt.architecture)

    def test_alternative_with_arch_captured(self):
        specs = DependencyGraph.parse_depends_line("foo | bar:any")
        alt = specs[0].alternatives[0]
        self.assertEqual(alt.name, "bar")
        self.assertEqual(alt.architecture, "any")


class TestLegacyAlternativeDeserialization(unittest.TestCase):
    """Legacy string-form alternatives (hand-built old JSON) still deserialize
    as `Alternative(name, None, None)` — backward compatibility."""

    def test_legacy_string_alternatives_deserialize(self):
        old_json = (
            '[{"name": "foo", "type": "depends",'
            ' "alternatives": ["bar", "baz"]}]'
        )
        specs = deserialize_depends(old_json)
        self.assertEqual(len(specs), 1)
        alts = specs[0].alternatives
        self.assertEqual([a.name for a in alts], ["bar", "baz"])
        for a in alts:
            self.assertIsInstance(a, Alternative)
            self.assertIsNone(a.version_constraint)
            self.assertIsNone(a.architecture)

    def test_legacy_alternatives_absent(self):
        old_json = '[{"name": "foo", "type": "depends"}]'
        specs = deserialize_depends(old_json)
        self.assertEqual(specs[0].alternatives, ())


class TestArchRestrictionFiltering(unittest.TestCase):
    """`[arch]` restrictions are honored: kept when they include the target
    arch, dropped only when they exclude it."""

    def test_arch_kept_when_includes_target(self):
        specs = DependencyGraph.parse_depends_line("foo [amd64]")
        self.assertEqual([s.name for s in specs], ["foo"])
        self.assertEqual(specs[0].arch_restriction, ("amd64",))

    def test_arch_dropped_when_excludes_target(self):
        self.assertEqual(DependencyGraph.parse_depends_line("foo [i386]"), [])

    def test_arch_dropped_when_negated_target(self):
        self.assertEqual(DependencyGraph.parse_depends_line("foo [!amd64]"), [])

    def test_multi_token_arch_kept_when_includes_target(self):
        specs = DependencyGraph.parse_depends_line("foo [amd64 i386]")
        self.assertEqual([s.name for s in specs], ["foo"])
        self.assertEqual(specs[0].arch_restriction, ("amd64", "i386"))

    def test_mixed_negation_arch_kept(self):
        specs = DependencyGraph.parse_depends_line("foo [amd64 !i386]")
        self.assertEqual([s.name for s in specs], ["foo"])
        self.assertEqual(specs[0].arch_restriction, ("amd64", "!i386"))

    def test_arch_excluded_alternative_is_dropped_others_kept(self):
        specs = DependencyGraph.parse_depends_line("foo | bar [i386]")
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].name, "foo")
        self.assertEqual(specs[0].alternatives, ())

    def test_arch_excluded_primary_falls_through_to_kept_alternative(self):
        specs = DependencyGraph.parse_depends_line("foo [i386] | bar")
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].name, "bar")

    def test_target_arch_override(self):
        kept = DependencyGraph.parse_depends_line("foo [i386]", target_arch="i386")
        self.assertEqual([s.name for s in kept], ["foo"])
        dropped = DependencyGraph.parse_depends_line("foo [amd64]", target_arch="i386")
        self.assertEqual(dropped, [])


class TestBuildProfiles(unittest.TestCase):
    """Multi-token `<profile>` groups are accepted conservatively (kept,
    over-inclusive); nothing is dropped silently."""

    def test_multi_profile_tokens_kept_with_constraint(self):
        specs = DependencyGraph.parse_depends_line("debhelper-compat (= 13) <!nocheck !cross>")
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec.name, "debhelper-compat")
        self.assertEqual(spec.version_constraint, {"operator": "=", "version": "13"})
        self.assertEqual(spec.profiles, ("!nocheck", "!cross"))

    def test_multiple_profile_groups_accumulate(self):
        specs = DependencyGraph.parse_depends_line("foo <cross> <stage1>")
        self.assertEqual(specs[0].profiles, ("cross", "stage1"))

    def test_profile_round_trip(self):
        specs = DependencyGraph.parse_depends_line("foo [amd64] <!nocheck>")
        round_tripped = deserialize_depends(serialize_depends(specs))
        self.assertEqual(round_tripped[0].arch_restriction, ("amd64",))
        self.assertEqual(round_tripped[0].profiles, ("!nocheck",))

    def test_unparseable_token_warns_and_remainder_parses(self):
        with self.assertLogs("package_mgmt.lib.model.dependency", level="WARNING") as cm:
            specs = DependencyGraph.parse_depends_line("foo, we!rd()token!!!, bar")
        self.assertEqual([s.name for s in specs], ["foo", "bar"])
        self.assertTrue(
            any("Cannot parse dependency token" in m for m in cm.output),
            f"expected a parse warning, got: {cm.output}",
        )


class TestArchWildcardFiltering(unittest.TestCase):
    """Debian arch wildcards (`linux-any`, `any-<cpu>`, `<os>-<cpu>`, `any`)
    are honored by `[arch]` filtering, both plain and negated — previously
    only literal arch names like `[amd64]` matched."""

    def test_linux_any_kept_on_linux_target(self):
        specs = DependencyGraph.parse_depends_line("foo [linux-any]")
        self.assertEqual([s.name for s in specs], ["foo"])
        self.assertEqual(specs[0].arch_restriction, ("linux-any",))

    def test_linux_any_dropped_on_non_linux_target(self):
        self.assertEqual(
            DependencyGraph.parse_depends_line("foo [linux-any]", target_arch="kfreebsd-amd64"),
            [],
        )

    def test_any_amd64_kept_on_amd64_target(self):
        specs = DependencyGraph.parse_depends_line("foo [any-amd64]")
        self.assertEqual([s.name for s in specs], ["foo"])

    def test_any_kept_on_any_target(self):
        specs = DependencyGraph.parse_depends_line("foo [any]")
        self.assertEqual([s.name for s in specs], ["foo"])

    def test_os_arch_explicit_kept(self):
        specs = DependencyGraph.parse_depends_line("foo [linux-arm64]", target_arch="arm64")
        self.assertEqual([s.name for s in specs], ["foo"])

    def test_os_arch_explicit_dropped_on_other_cpu(self):
        self.assertEqual(DependencyGraph.parse_depends_line("foo [linux-arm64]"), [])

    def test_negated_linux_any_dropped_on_linux_target(self):
        self.assertEqual(DependencyGraph.parse_depends_line("foo [!linux-any]"), [])

    def test_negated_linux_any_kept_on_non_linux_target(self):
        specs = DependencyGraph.parse_depends_line("foo [!linux-any]", target_arch="kfreebsd-amd64")
        self.assertEqual([s.name for s in specs], ["foo"])

    def test_build_depends_field_keeps_linux_any(self):
        specs = parse_build_depends_field(
            "libwayland-dev [linux-any], wayland-protocols [linux-any], xvfb <!nocheck>"
        )
        self.assertEqual(
            [s.name for s in specs],
            ["libwayland-dev", "wayland-protocols", "xvfb"],
        )
        self.assertEqual(specs[0].arch_restriction, ("linux-any",))


class TestBuildDependsArchProfiles(unittest.TestCase):
    """`parse_build_depends_field` honors `[arch]`/multi-profile build-dep
    lines the same way as the runtime parser."""

    def test_build_depends_field_honors_arch_and_profiles(self):
        specs = parse_build_depends_field(
            "debhelper-compat (= 13) <!nocheck !cross>, gperf [i386]"
        )
        self.assertEqual(len(specs), 1, f"got: {[s.name for s in specs]}")
        spec = specs[0]
        self.assertEqual(spec.name, "debhelper-compat")
        self.assertEqual(spec.build_context, "build")
        self.assertEqual(spec.version_constraint, {"operator": "=", "version": "13"})
        self.assertEqual(spec.profiles, ("!nocheck", "!cross"))
        self.assertEqual(spec.arch_restriction, None)

    def test_build_depends_field_keeps_arch_including_tool(self):
        specs = parse_build_depends_field("gperf [amd64 i386]")
        self.assertEqual([s.name for s in specs], ["gperf"])
        self.assertEqual(specs[0].arch_restriction, ("amd64", "i386"))


class TestDepsSatisfiedPredicate(unittest.TestCase):
    """The shared `deps_satisfied` rule (direct current-row check + versioned-
    `provides` fallback) — the single predicate used by `import-local`,
    `verify`, and the resolver's `select_provider`, so all three agree."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(f"{self.tmp.name}/test.db")
        self.conn.executescript(EXPECTED_SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _add_binary(self, package, version, status="current", provides=None):
        self.conn.execute(
            "INSERT INTO binaries"
            " (package_name, version, architecture, release, component, status, verified)"
            " VALUES (?, ?, 'amd64', 'trixie', 'main', ?, 1)",
            (package, version, status),
        )
        binary_id = self.conn.execute(
            "SELECT id FROM binaries WHERE package_name = ? AND version = ?",
            (package, version),
        ).fetchone()[0]
        for virtual_name, pversion in provides or ():
            self.conn.execute(
                """INSERT INTO provides
                   (package_name, version, architecture, release, component,
                    virtual_name, provides_version)
                   VALUES (?, ?, 'amd64', 'trixie', 'main', ?, ?)""",
                (package, version, virtual_name, pversion or ""),
            )
        self.conn.commit()
        return binary_id

    def _spec(self, name, constraint=None, alternatives=()):
        return DependencySpec(name=name, version_constraint=constraint, alternatives=alternatives)

    def test_direct_current_row_satisfies_constraint(self):
        self._add_binary("libfoo", "1.5")
        spec = self._spec("libfoo", {"operator": ">=", "version": "1.0"})
        self.assertTrue(deps_satisfied(self.conn, "libfoo", spec))

    def test_too_old_direct_row_fails(self):
        self._add_binary("libfoo", "1.5")
        spec = self._spec("libfoo", {"operator": ">=", "version": "2.0"})
        self.assertFalse(deps_satisfied(self.conn, "libfoo", spec))

    def test_unversioned_provides_checked_against_provider_version(self):
        self._add_binary("prov", "3.0", provides=[("libfoo", None)])
        spec = self._spec("libfoo", {"operator": ">=", "version": "2.0"})
        self.assertTrue(deps_satisfied(self.conn, "libfoo", spec))
        spec2 = self._spec("libfoo", {"operator": ">=", "version": "4.0"})
        self.assertFalse(deps_satisfied(self.conn, "libfoo", spec2))

    def test_versioned_provides_checked_against_declared_version(self):
        self._add_binary("prov", "1.0", provides=[("libfoo", "2.5")])
        spec = self._spec("libfoo", {"operator": ">=", "version": "2.0"})
        self.assertTrue(deps_satisfied(self.conn, "libfoo", spec))
        spec2 = self._spec("libfoo", {"operator": ">=", "version": "3.0"})
        self.assertFalse(deps_satisfied(self.conn, "libfoo", spec2))

    def test_alternative_satisfied_under_own_constraint(self):
        self._add_binary("bar", "2.0")
        spec = self._spec(
            "foo",
            {"operator": ">=", "version": "9.0"},
            alternatives=[Alternative("bar", {"operator": "=", "version": "2.0"})],
        )
        self.assertTrue(deps_satisfied(self.conn, "foo", spec))

    def test_alternative_too_old_fails(self):
        self._add_binary("bar", "1.0")
        spec = self._spec(
            "foo",
            {"operator": ">=", "version": "9.0"},
            alternatives=[Alternative("bar", {"operator": "=", "version": "2.0"})],
        )
        self.assertFalse(deps_satisfied(self.conn, "foo", spec))

    def test_non_current_row_is_not_a_provider(self):
        self._add_binary("prov", "3.0", status="superseded", provides=[("libfoo", None)])
        spec = self._spec("libfoo", {"operator": ">=", "version": "2.0"})
        self.assertFalse(deps_satisfied(self.conn, "libfoo", spec))


class TestProviderSatisfiesConstraint(unittest.TestCase):
    """`provider_satisfies_constraint`: versioned provides checked against its
    declared version, unversioned (''/None) against the provider's own version,
    unconstrained as always-satisfied."""

    def test_versioned_provides(self):
        self.assertTrue(provider_satisfies_constraint(
            {"operator": ">=", "version": "2.0"}, "2.5", "1.0"))
        self.assertFalse(provider_satisfies_constraint(
            {"operator": ">=", "version": "3.0"}, "2.5", "1.0"))

    def test_unversioned_provides_falls_back_to_provider_version(self):
        self.assertTrue(provider_satisfies_constraint(
            {"operator": ">=", "version": "2.0"}, "", "3.0"))
        self.assertTrue(provider_satisfies_constraint(
            {"operator": ">=", "version": "2.0"}, None, "3.0"))
        self.assertFalse(provider_satisfies_constraint(
            {"operator": ">=", "version": "4.0"}, "", "3.0"))

    def test_unconstrained_always_satisfied(self):
        self.assertTrue(provider_satisfies_constraint(None, None, "1.0"))


if __name__ == "__main__":
    unittest.main()
