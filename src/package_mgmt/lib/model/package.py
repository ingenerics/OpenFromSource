from dataclasses import dataclass


@dataclass(frozen=True)
class BinaryPackage:
    package: str
    version: str
    architecture: str
    release: str
    component: str
    depends_raw: str | None
    provides_raw: str | None
    source_field: str | None
    filename: str | None
    sha256: str | None
    size: str | None = None
    pre_depends_raw: str | None = None
    multi_arch: str | None = None
    # The original `Depends` field text, kept separate from `depends_raw` (which
    # merges Pre-Depends targets in for the resolver's closure walk). The repo
    # emission uses this so the generated Packages stanza matches the control
    # file apt's VersionHash sees.
    depends_field: str | None = None
    # apt's VersionHash is computed over {Installed-Size, Depends, Pre-Depends,
    # Conflicts, Breaks, Replaces}; the handoff repo must carry them verbatim or
    # apt refuses to merge the repo version with the installed dpkg-status
    # version and reports an identical version as upgradable.
    installed_size: str | None = None
    conflicts_raw: str | None = None
    breaks_raw: str | None = None
    replaces_raw: str | None = None
    section: str | None = None
    maintainer: str | None = None
    homepage: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class SourceFile:
    name: str
    size: int | None
    sha256: str | None


@dataclass(frozen=True)
class SourcePackage:
    package: str
    version: str
    release: str
    component: str
    directory: str | None
    files: list[dict] | None
    build_depends: str | None
    build_depends_arch: str | None = None
    build_depends_indep: str | None = None
    parsed_files: list[SourceFile] | None = None
