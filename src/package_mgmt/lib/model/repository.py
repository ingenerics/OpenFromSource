from dataclasses import dataclass, field

from package_mgmt.lib.model.package import BinaryPackage, SourcePackage
from package_mgmt.lib.util.versions import parse_version, compare_versions

_COMPONENT_PRIORITY = {
    "local": -1,
    "main": 0,
    "contrib": 1,
    "non-free": 2,
    "non-free-firmware": 3,
}


def _component_order(comp: str) -> int:
    return _COMPONENT_PRIORITY.get(comp, 99)


@dataclass
class RepositoryIndex:
    binary_packages: dict[str, list[BinaryPackage]] = field(default_factory=dict)
    source_packages: dict[str, list[SourcePackage]] = field(default_factory=dict)

    def lookup_binary(self, name: str) -> BinaryPackage | None:
        entries = self.binary_packages.get(name)
        if not entries:
            return None
        return _best_entry(entries)

    def lookup_binary_by_source(self, name: str, release: str) -> BinaryPackage | None:
        entries = [e for e in self.binary_packages.get(name, []) if e.release == release]
        if not entries:
            return None
        return _best_entry(entries)

    def lookup_binary_satisfying(
        self, name: str, version_constraint: dict | None, release: str | None = None,
    ) -> BinaryPackage | None:
        entries = self.binary_packages.get(name)
        if not entries:
            return None
        if version_constraint:
            op = version_constraint.get("operator", ">=")
            target = version_constraint.get("version", "")
            entries = [e for e in entries if compare_versions(e.version, op, target)]
            if not entries:
                return None
        if release:
            release_entries = [e for e in entries if e.release == release]
            if release_entries:
                entries = release_entries
        return _best_entry(entries)

    def lookup_source(self, name: str) -> SourcePackage | None:
        entries = self.source_packages.get(name)
        if not entries:
            return None
        candidates = [(parse_version(e.version), -_component_order(e.component), e) for e in entries]
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return candidates[0][2]

    def lookup_source_by_source(self, name: str, release: str) -> SourcePackage | None:
        entries = [e for e in self.source_packages.get(name, []) if e.release == release]
        if not entries:
            return None
        candidates = [(parse_version(e.version), -_component_order(e.component), e) for e in entries]
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return candidates[0][2]

    def lookup_source_exact(
        self, name: str, version: str, release: str | None = None, component: str | None = None,
    ) -> SourcePackage | None:
        """Exact `(name, version)` source lookup, optionally scoped to a
        release/component. No version ordering — only an exact match wins."""
        entries = self.source_packages.get(name, [])
        if release:
            entries = [e for e in entries if e.release == release]
        if component:
            entries = [e for e in entries if e.component == component]
        for e in entries:
            if e.version == version:
                return e
        return None


def _best_entry(entries: list[BinaryPackage]) -> BinaryPackage:
    candidates = [(parse_version(e.version), -_component_order(e.component), e) for e in entries]
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2]
