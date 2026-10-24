from dataclasses import dataclass, field
from datetime import date
from typing import Literal


BuildSystem = Literal["meson", "cmake"]
RequirementTier = Literal["cache", "ecosystem"]
ReportMode = Literal["search", "verify"]


@dataclass(frozen=True)
class ManifestRepo:
    """One repository entry in a version manifest.

    `commit` is the reproducibility pin: the acquisition step checks out this
    exact SHA in `repos/<name>` and the alignment check reads the build
    metadata at that commit. `build` selects which metadata parser applies
    (meson.build vs CMakeLists.txt).
    """

    name: str
    url: str
    tag: str
    commit: str
    build: BuildSystem
    reason: str


@dataclass(frozen=True)
class VersionManifest:
    """The machine-checkable result of the era selection.

    Carries the era label, the selection rationale, and one `ManifestRepo`
    per in-scope project. Produced by the alignment command on success and
    validated against the current cache when re-verifying.
    """

    era: str
    chosen: date
    reason: str
    repos: tuple[ManifestRepo, ...] = ()


@dataclass(frozen=True)
class DependencyRequirement:
    """A single declared dependency requirement parsed from build metadata.

    `tier` says how the requirement is validated: ``cache`` compares against
    the version cached for the owning Debian package, ``ecosystem`` compares
    against the version the selected source project will provide. `source_ref`
    pins the requirement to `repo:<relative-path>:<line>` for traceability in
    failure reports.
    """

    repo: str
    module: str
    operator: str = ">="
    version: str = ""
    tier: RequirementTier = "cache"
    source_ref: str = ""
    optional: bool = False


@dataclass(frozen=True)
class UnmetRequirement:
    """A requirement that failed validation, with the reason and evidence."""

    requirement: DependencyRequirement
    cached_package: str | None = None
    cached_version: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class AlignmentReport:
    """The aggregate result of a search or verify run.

    `mode` distinguishes the two run modes. Search-only fields
    (`candidates_evaluated`, `selected_era`, `manifest_written`) are present
    only in search mode; `manifest` only in verify mode. `satisfied` maps
    directly to the exit code: True -> 0, False -> 1.
    """

    mode: ReportMode
    repo_dir: str
    baseline: str
    manifest: str | None = None
    candidates_evaluated: tuple[str, ...] | None = None
    selected_era: str | None = None
    manifest_written: str | None = None
    requirements_checked: int = 0
    satisfied: bool = False
    unmet: tuple[UnmetRequirement, ...] = field(default_factory=tuple)
