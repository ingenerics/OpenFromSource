import unittest

from package_mgmt.lib.model.package import BinaryPackage, SourcePackage
from package_mgmt.lib.model.repository import RepositoryIndex, _best_entry, _component_order


def _binary(version, release="trixie", component="main"):
    return BinaryPackage(
        package="gawk", version=version, architecture="amd64",
        release=release, component=component,
        depends_raw=None, provides_raw=None,
        source_field=None, filename=None, sha256=None,
    )


def _source(version, release="trixie", component="main"):
    return SourcePackage(
        package="gawk", version=version, release=release, component=component,
        directory=None, files=None, build_depends=None,
    )


class TestComponentTieBreak(unittest.TestCase):
    """On an exact-version tie across components, the highest-priority
    component (main) wins so the pool path reflects the primary component."""

    def test_best_entry_main_wins_exact_version_tie(self):
        entries = [
            _binary("5.2.1", component="non-free"),
            _binary("5.2.1", component="contrib"),
            _binary("5.2.1", component="main"),
        ]
        best = _best_entry(entries)
        self.assertEqual(best.component, "main")

    def test_lookup_binary_main_wins_exact_version_tie(self):
        index = RepositoryIndex()
        index.binary_packages["gawk"] = [
            _binary("5.2.1", component="non-free"),
            _binary("5.2.1", component="contrib"),
            _binary("5.2.1", component="main"),
        ]
        self.assertEqual(index.lookup_binary("gawk").component, "main")

    def test_lookup_binary_by_source_main_wins_exact_version_tie(self):
        index = RepositoryIndex()
        index.binary_packages["gawk"] = [
            _binary("5.2.1", component="non-free"),
            _binary("5.2.1", component="contrib"),
            _binary("5.2.1", component="main"),
        ]
        self.assertEqual(index.lookup_binary_by_source("gawk", "trixie").component, "main")

    def test_lookup_source_main_wins_exact_version_tie(self):
        index = RepositoryIndex()
        index.source_packages["gawk"] = [
            _source("5.2.1", component="non-free"),
            _source("5.2.1", component="contrib"),
            _source("5.2.1", component="main"),
        ]
        self.assertEqual(index.lookup_source("gawk").component, "main")

    def test_lookup_source_by_source_main_wins_exact_version_tie(self):
        index = RepositoryIndex()
        index.source_packages["gawk"] = [
            _source("5.2.1", component="non-free"),
            _source("5.2.1", component="contrib"),
            _source("5.2.1", component="main"),
        ]
        self.assertEqual(index.lookup_source_by_source("gawk", "trixie").component, "main")

    def test_higher_version_wins_over_component(self):
        entries = [
            _binary("5.2.1", component="main"),
            _binary("5.3.0", component="non-free"),
        ]
        best = _best_entry(entries)
        self.assertEqual(best.version, "5.3.0")
        self.assertEqual(best.component, "non-free")

    def test_main_beats_contrib_on_tie(self):
        entries = [
            _binary("1.0", component="contrib"),
            _binary("1.0", component="main"),
        ]
        best = _best_entry(entries)
        self.assertEqual(best.component, "main")


class TestLocalComponentPriority(unittest.TestCase):
    """`local` is the highest-priority component: on an exact-version tie it
    beats every upstream component, while a strictly newer upstream version
    still wins (version-then-component tie-break, Clarification Q1)."""

    def test_local_component_order_is_highest(self):
        self.assertEqual(_component_order("local"), -1)
        self.assertLess(_component_order("local"), _component_order("main"))

    def test_best_entry_local_wins_exact_version_tie(self):
        entries = [
            _binary("5.2.1", component="main"),
            _binary("5.2.1", component="local"),
        ]
        self.assertEqual(_best_entry(entries).component, "local")

    def test_lookup_binary_local_wins_exact_version_tie(self):
        index = RepositoryIndex()
        index.binary_packages["gawk"] = [
            _binary("5.2.1", component="main"),
            _binary("5.2.1", component="local"),
        ]
        self.assertEqual(index.lookup_binary("gawk").component, "local")

    def test_lookup_binary_by_source_local_wins_exact_version_tie(self):
        index = RepositoryIndex()
        index.binary_packages["gawk"] = [
            _binary("5.2.1", component="main"),
            _binary("5.2.1", component="local"),
        ]
        self.assertEqual(index.lookup_binary_by_source("gawk", "trixie").component, "local")

    def test_lookup_binary_satisfying_local_wins_tie(self):
        index = RepositoryIndex()
        index.binary_packages["gawk"] = [
            _binary("5.2.1", component="main"),
            _binary("5.2.1", component="local"),
        ]
        best = index.lookup_binary_satisfying(
            "gawk", {"operator": ">=", "version": "5.2.0"}, "trixie"
        )
        self.assertEqual(best.component, "local")

    def test_lookup_source_local_wins_exact_version_tie(self):
        index = RepositoryIndex()
        index.source_packages["gawk"] = [
            _source("5.2.1", component="main"),
            _source("5.2.1", component="local"),
        ]
        self.assertEqual(index.lookup_source("gawk").component, "local")

    def test_lookup_source_by_source_local_wins_exact_version_tie(self):
        index = RepositoryIndex()
        index.source_packages["gawk"] = [
            _source("5.2.1", component="main"),
            _source("5.2.1", component="local"),
        ]
        self.assertEqual(index.lookup_source_by_source("gawk", "trixie").component, "local")

    def test_newer_upstream_still_wins_over_older_local(self):
        index = RepositoryIndex()
        index.binary_packages["gawk"] = [
            _binary("5.2.1", component="local"),
            _binary("5.3.0", component="main"),
        ]
        best = index.lookup_binary("gawk")
        self.assertEqual(best.version, "5.3.0")
        self.assertEqual(best.component, "main")

    def test_no_local_copy_component_order_unchanged(self):
        index = RepositoryIndex()
        index.binary_packages["gawk"] = [
            _binary("5.2.1", component="contrib"),
            _binary("5.2.1", component="main"),
        ]
        self.assertEqual(index.lookup_binary("gawk").component, "main")


if __name__ == "__main__":
    unittest.main()
