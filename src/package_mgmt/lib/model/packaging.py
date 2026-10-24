"""Domain models for source-stack packaging.

Frozen, immutable dataclasses for the declarative recipe input and the
command result report (Constitution VI: concrete types, no implicit dict
shapes in domain objects; `TypedDict` is used only at the JSON output
boundary).
"""

from dataclasses import dataclass, field
from typing import Literal


BuildSystem = Literal["meson", "cmake"]


@dataclass(frozen=True)
class ComponentRecipe:
    """One source-built component of a stack.

    `name` is the unique recipe key (also the staged-dir name and the
    reference target for dependency resolution); `binary_package` is the
    Debian binary package the component ships. `source_tree` is a path
    relative to the recipe's `repo_dir` and may point at a vendored
    subproject (e.g. a submodule path). `revision` is the Debian revision;
    re-packaging the same upstream version bumps it so `apt upgrade`
    semantics hold on the operator side.
    """

    name: str
    binary_package: str
    source_tree: str
    version: str
    build_system: BuildSystem
    description: str
    revision: int = 1
    architecture: str = "amd64"
    maintainer: str = ""
    section: str = "misc"
    homepage: str = ""
    explicit_depends: tuple[str, ...] = ()
    build_depends: tuple[str, ...] = ()
    source_format: str = "3.0 (quilt)"


@dataclass(frozen=True)
class StackRecipe:
    """The declarative per-stack input of `package-stack`.

    `repo_dir` holds the pinned source trees (default `repos/`).
    `source_epoch` is the ISO date fixed as `SOURCE_DATE_EPOCH` for
    deterministic artifacts; components are listed in arbitrary order — the
    import order is derived by topological sort, not by file order.
    """

    stack: str
    components: tuple[ComponentRecipe, ...] = ()
    repo_dir: str = "repos"
    source_epoch: str = ""


@dataclass(frozen=True)
class ComponentResult:
    """The outcome for one packaged and imported component.

    `derived_depends` is the `Depends` string `dpkg-shlibdeps` derived from
    the staged binaries; `explicit_depends` are the recipe-declared
    requirements merged in. `unattributed` lists libraries the derivation
    could not attribute to a Debian package — these must be declared by the
    recipe and are never silently dropped. `source_files` counts the source
    payloads verified against the `.dsc`.
    """

    name: str
    binary_package: str
    version: str
    architecture: str
    derived_depends: str = ""
    explicit_depends: tuple[str, ...] = ()
    unattributed: tuple[str, ...] = ()
    source_files: int = 0
    linked: bool = False
    verified: bool = False


@dataclass(frozen=True)
class PackResult:
    """The `package-stack` report (text or JSON).

    `order` is the resolved dependency order of the imported set;
    `components_total` is the recipe size and `components_imported` the
    number successfully imported. `verify_ok`/`install_ok` carry the
    whole-set validation outcomes (whole-set `verify` and the simulated
    offline `apt install`); they are report-only fields and never appear in
    the JSON boundary.
    """

    stack: str
    components_total: int = 0
    components_imported: int = 0
    order: tuple[str, ...] = ()
    components: tuple[ComponentResult, ...] = field(default_factory=tuple)
    verify_ok: bool | None = None
    install_ok: bool | None = None
