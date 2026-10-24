"""Recipe loading/validation and stack packaging report formatting.

`load_recipe` parses the declarative TOML recipe (a tracked file) and
validates it: required/typed fields, unknown-field rejection, uniqueness of
component names and binary packages, valid Debian versions, resolvable
source trees, and a satisfiable, acyclic component dependency set. The
dependency order helper derives the dependencies-before-dependents import
order the pipeline later uses.

The output formatters render the `PackResult` report in text or stable JSON
(`TypedDict` is used only at the JSON serialization boundary).
"""

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import tomllib
from collections import deque
from datetime import datetime, timezone
from typing import TypedDict

from debian.debian_support import Version

from package_mgmt.lib.model.dependency import DependencyGraph
from package_mgmt.lib.model.packaging import (
    ComponentRecipe,
    ComponentResult,
    PackResult,
    StackRecipe,
)
from package_mgmt.lib.service.debgen import build_binary_package
from package_mgmt.lib.service.handoff import (
    paragraph,
    write_gzip,
    _release_text,
    hash_entries_for_pair,
    local_entries,
)
from package_mgmt.lib.service.import_local import import_local_deb
from package_mgmt.lib.service.srcgen import build_source_package

logger = logging.getLogger(__name__)

# Field sets the recipe parser accepts. Anything else is rejected so a typo
# never silently changes behavior.
_STACK_FIELDS = frozenset({"name", "repo_dir", "source_epoch"})
_COMPONENT_FIELDS = frozenset(
    {
        "name",
        "binary_package",
        "source_tree",
        "version",
        "revision",
        "build_system",
        "architecture",
        "description",
        "maintainer",
        "section",
        "homepage",
        "explicit_depends",
        "build_depends",
        "source_format",
    }
)

_DEFAULT_REPO_DIR = "repos"
_BUILD_SYSTEMS = frozenset({"meson", "cmake"})


class RecipeError(Exception):
    """Base class for recipe problems surfaced by `load_recipe`."""


class RecipeLoadError(RecipeError):
    """An unreadable or malformed recipe (a configuration-level failure).

    Covers file read errors, TOML parse errors, unknown fields, wrong types,
    duplicate names, invalid versions, and missing source trees.
    """


class RecipeValidationError(RecipeError):
    """A recipe whose declared dependency set is unsatisfiable.

    Naming the component and the problem (a missing source-built dependency
    or a dependency cycle).
    """


class PackagingError(Exception):
    """A packaging/import failure (exit 1).

    Raised by the orchestration when a component cannot be packaged or
    imported (missing staged directory, undeclared un-attributable library,
    unrebuildable source set, unsatisfiable dependency, ...). Any failure for
    a component leaves zero DB/pool changes for that component; components
    already imported remain.
    """


def _parse_stack(data: dict) -> tuple[str, str, str]:
    """Extract and validate the `[stack]` table -> (name, repo_dir, epoch)."""
    stack = data.get("stack")
    if not isinstance(stack, dict):
        raise RecipeLoadError("recipe is missing the [stack] table")
    unknown = sorted(set(stack) - _STACK_FIELDS)
    if unknown:
        raise RecipeLoadError("[stack] unknown field(s): " + ", ".join(unknown))
    name = stack.get("name")
    if not isinstance(name, str) or not name:
        raise RecipeLoadError("[stack] name is required")
    repo_dir = stack.get("repo_dir", _DEFAULT_REPO_DIR)
    if not isinstance(repo_dir, str) or not repo_dir:
        raise RecipeLoadError("[stack] repo_dir must be a non-empty string")
    source_epoch = stack.get("source_epoch", "")
    if not isinstance(source_epoch, str):
        raise RecipeLoadError("[stack] source_epoch must be an ISO date string")
    return name, repo_dir, source_epoch


def _parse_string_list(name: str, value, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RecipeLoadError(
            f"component {name}: '{field}' must be a list of strings"
        )
    return tuple(value)


def _parse_component(raw: dict) -> ComponentRecipe:
    unknown = sorted(set(raw) - _COMPONENT_FIELDS)
    if unknown:
        raise RecipeLoadError(
            "component unknown field(s): " + ", ".join(unknown)
        )
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise RecipeLoadError("component requires a non-empty 'name'")
    for field in ("binary_package", "source_tree", "version", "description"):
        value = raw.get(field)
        if not isinstance(value, str) or not value:
            raise RecipeLoadError(
                f"component {name}: '{field}' is required"
            )
    binary_package = raw["binary_package"]
    source_tree = raw["source_tree"]
    version = raw["version"]
    description = raw["description"]

    build_system = raw.get("build_system")
    if build_system not in _BUILD_SYSTEMS:
        raise RecipeLoadError(
            f"component {name}: build_system must be 'meson' or 'cmake'"
        )

    revision = raw.get("revision", 1)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise RecipeLoadError(
            f"component {name}: 'revision' must be a positive integer"
        )

    try:
        Version(version)
    except ValueError:
        raise RecipeLoadError(
            f"component {name}: invalid Debian version {version!r}"
        ) from None

    for field in ("architecture", "maintainer", "section", "homepage", "source_format"):
        value = raw.get(field)
        if value is not None and not isinstance(value, str):
            raise RecipeLoadError(f"component {name}: '{field}' must be a string")

    return ComponentRecipe(
        name=name,
        binary_package=binary_package,
        source_tree=source_tree,
        version=version,
        build_system=build_system,
        description=description,
        revision=revision,
        architecture=raw.get("architecture", "amd64"),
        maintainer=raw.get("maintainer", ""),
        section=raw.get("section", "misc"),
        homepage=raw.get("homepage", ""),
        explicit_depends=_parse_string_list(name, raw.get("explicit_depends"), "explicit_depends"),
        build_depends=_parse_string_list(name, raw.get("build_depends"), "build_depends"),
        source_format=raw.get("source_format", "3.0 (quilt)"),
    )


def _validate_uniqueness(components: tuple[ComponentRecipe, ...]) -> None:
    names: set[str] = set()
    binary_packages: set[str] = set()
    for comp in components:
        if comp.name in names:
            raise RecipeLoadError(f"duplicate component name {comp.name!r}")
        names.add(comp.name)
        if comp.binary_package in binary_packages:
            raise RecipeLoadError(
                f"duplicate binary package {comp.binary_package!r} "
                f"(component {comp.name})"
            )
        binary_packages.add(comp.binary_package)


def _validate_source_trees(repo_dir: str, components: tuple[ComponentRecipe, ...]) -> None:
    for comp in components:
        tree = os.path.abspath(os.path.join(repo_dir, comp.source_tree))
        if not os.path.isdir(tree):
            raise RecipeLoadError(
                f"component {comp.name}: source tree not found at {tree}"
            )


def _component_resolver(recipe: StackRecipe) -> dict[str, str]:
    """Map every package name a dependency may reference to a component name.

    A dependency resolves to a component when it names the component's
    `name` or its `binary_package` (a companion's runtime library).
    """
    resolver: dict[str, str] = {}
    for comp in recipe.components:
        resolver.setdefault(comp.name, comp.name)
        resolver.setdefault(comp.binary_package, comp.name)
    return resolver


def _dependency_edges(recipe: StackRecipe) -> dict[str, set[str]]:
    """Component name -> set of prerequisite component names declared via
    `explicit_depends`, resolved against the recipe.

    An explicit dependency that names no recipe component is a loud
    validation failure naming the gap: `explicit_depends` exists to declare
    source-built companion libraries the recipe must provide.
    """
    resolver = _component_resolver(recipe)
    edges: dict[str, set[str]] = {comp.name: set() for comp in recipe.components}
    for comp in recipe.components:
        for raw in comp.explicit_depends:
            specs = DependencyGraph.parse_depends_line(raw)
            if not specs:
                raise RecipeValidationError(
                    f"component {comp.name}: cannot parse explicit dependency {raw!r}"
                )
            spec = specs[0]
            candidates = [spec.name] + [a.name for a in spec.alternatives]
            resolved = [resolver[c] for c in candidates if c in resolver]
            if not resolved:
                raise RecipeValidationError(
                    f"component {comp.name} depends on {spec.name!r} "
                    "which is not in the recipe — add it or the import cannot resolve"
                )
            for prereq in resolved:
                if prereq != comp.name:
                    edges[comp.name].add(prereq)
    return edges


def _resolve_order(edges: dict[str, set[str]]) -> list[str]:
    """Dependencies-before-dependents order via Kahn's algorithm.

    Raises a validation error naming a component when the graph has a cycle.
    """
    indegree = {node: len(deps) for node, deps in edges.items()}
    dependents: dict[str, set[str]] = {node: set() for node in edges}
    for node, prereqs in edges.items():
        for prereq in prereqs:
            dependents[prereq].add(node)
    ready = deque(sorted(node for node, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while ready:
        node = ready.popleft()
        order.append(node)
        for dependent in sorted(dependents[node]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    if len(order) != len(edges):
        remaining = sorted(set(edges) - set(order))
        raise RecipeValidationError(
            f"dependency cycle involving component {remaining[0]!r}"
        )
    return order


def resolve_component_order(recipe: StackRecipe) -> tuple[str, ...]:
    """Dependencies-before-dependents import order for the recipe.

    The recipe's component list is order-independent: the import order is
    derived from each component's declared `depends` resolved against recipe
    names/binary packages. Raises `RecipeValidationError` naming the gap for
    a missing source-built dependency or a dependency cycle.
    """
    return tuple(_resolve_order(_dependency_edges(recipe)))


def load_recipe(recipe_path: str) -> StackRecipe:
    """Load and validate the declarative recipe at `recipe_path`.

    Returns the immutable `StackRecipe`. Raises `RecipeLoadError` for
    unreadable or malformed input and `RecipeValidationError` when the
    declared dependency set is unsatisfiable.
    """
    try:
        with open(recipe_path, "rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        raise RecipeLoadError(f"cannot read recipe {recipe_path}: {e.strerror}") from e
    except tomllib.TOMLDecodeError as e:
        raise RecipeLoadError(f"cannot parse recipe {recipe_path}: {e}") from e

    name, repo_dir, source_epoch = _parse_stack(data)

    raw_components = data.get("component")
    if not isinstance(raw_components, list) or not raw_components:
        raise RecipeLoadError(
            "recipe defines no [[component]] entries — at least one is required"
        )
    components = tuple(_parse_component(raw) for raw in raw_components)

    _validate_uniqueness(components)
    _validate_source_trees(repo_dir, components)

    recipe = StackRecipe(
        stack=name,
        repo_dir=repo_dir,
        source_epoch=source_epoch,
        components=components,
    )
    _resolve_order(_dependency_edges(recipe))
    return recipe


class ComponentResultJson(TypedDict):
    name: str
    binary_package: str
    version: str
    architecture: str
    derived_depends: str
    explicit_depends: list[str]
    unattributed: list[str]
    source_files: int
    linked: bool
    verified: bool


class PackResultJson(TypedDict):
    stack: str
    components_total: int
    components_imported: int
    order: list[str]
    components: list[ComponentResultJson]


def _component_json(result: ComponentResult) -> ComponentResultJson:
    return ComponentResultJson(
        name=result.name,
        binary_package=result.binary_package,
        version=result.version,
        architecture=result.architecture,
        derived_depends=result.derived_depends,
        explicit_depends=list(result.explicit_depends),
        unattributed=list(result.unattributed),
        source_files=result.source_files,
        linked=result.linked,
        verified=result.verified,
    )


def to_json(result: PackResult) -> PackResultJson:
    return PackResultJson(
        stack=result.stack,
        components_total=result.components_total,
        components_imported=result.components_imported,
        order=list(result.order),
        components=[_component_json(component) for component in result.components],
    )


def format_text(
    result: PackResult,
    verify_ok: bool | None = None,
    install_ok: bool | None = None,
) -> str:
    """Render the `PackResult` report as text.

    `verify_ok` and `install_ok` are the whole-set validation outcomes;
    explicit arguments override the outcomes the orchestration stored on the
    result.
    """
    if verify_ok is None:
        verify_ok = result.verify_ok
    if install_ok is None:
        install_ok = result.install_ok
    lines = [
        f"stack: {result.stack}",
        "order: " + " → ".join(result.order),
        f"components imported: {result.components_imported}/{result.components_total}",
    ]
    unattributed = sum(len(component.unattributed) for component in result.components)
    lines.append(
        f"Depends derived for {len(result.components)} binaries "
        f"(explicit deps merged; {unattributed} unattributed libraries)"
    )
    if verify_ok is not None:
        lines.append(
            "whole-set verify: "
            + ("PASS (no unlinked binaries, no unverified records)" if verify_ok else "FAIL")
        )
    if install_ok is not None:
        lines.append(
            "simulated offline apt install: "
            + ("PASS (0 unmet dependencies)" if install_ok else "FAIL")
        )
    return "\n".join(lines)


def format_output(result: PackResult, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(to_json(result))
    return format_text(result)


#: Default root holding each component's staged install dir (`<root>/<name>`);
#: the staged-build mode of the build script installs into this same layout.
DEFAULT_STAGE_ROOT = "staging"


def compute_source_epoch(recipe: StackRecipe, recipe_path: str | None = None) -> int:
    """The fixed `SOURCE_DATE_EPOCH` seconds for the stack's artifacts.

    Priority: the recipe's `source_epoch` ISO date, then the
    `SOURCE_DATE_EPOCH` environment variable, then the recipe file's mtime
    (stable for a tracked file).
    """
    if recipe.source_epoch:
        try:
            when = datetime.strptime(recipe.source_epoch, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            return int(when.timestamp())
        except ValueError:
            pass
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    if recipe_path:
        try:
            return int(os.path.getmtime(recipe_path))
        except OSError:
            pass
    return 0


def _declared_names(recipe: StackRecipe) -> set[str]:
    names: set[str] = set()
    for component in recipe.components:
        names.add(component.name)
        names.add(component.binary_package)
    return names


def _soname_base(soname: str) -> str:
    """`libsdbus-c++.so.2` -> `libsdbus-c++` (strip the versioned `.so` suffix)."""
    match = re.match(r"^(.*)\.so(?:\.\d+)*$", soname)
    return match.group(1) if match else soname


def undeclared_unattributed(
    recipe: StackRecipe, component: ComponentRecipe, unattributed: tuple[str, ...]
) -> tuple[str, ...]:
    """Un-attributable libraries the recipe does not declare.

    A soname is declared when its base name matches a recipe component name /
    binary package or an entry of the component's own `explicit_depends`.
    These libraries must be declared — they are never silently dropped.
    """
    declared = _declared_names(recipe)
    for explicit in component.explicit_depends:
        specs = DependencyGraph.parse_depends_line(explicit)
        if specs:
            declared.add(specs[0].name)
    return tuple(
        sorted({soname for soname in unattributed if _soname_base(soname) not in declared})
    )


def _component_by_name(recipe: StackRecipe, name: str) -> ComponentRecipe:
    for component in recipe.components:
        if component.name == name:
            return component
    raise PackagingError(f"recipe references unknown component {name!r}")


def whole_set_verify(
    conn: sqlite3.Connection, set_names: tuple[str, ...]
) -> tuple[bool, tuple[str, ...]]:
    """The `verify` semantics for the imported set.

    Every set binary must be `verified` and linked to an imported, verified
    source (no unlinked binaries, no unverified records). Returns
    `(ok, problems)` naming each gap.
    """
    problems: list[str] = []
    for name in set_names:
        row = conn.execute(
            "SELECT id, verified FROM binaries"
            " WHERE package_name = ? AND status = 'current'",
            (name,),
        ).fetchone()
        if row is None:
            problems.append(f"{name}: not imported into local")
            continue
        if row[1] != 1:
            problems.append(f"{name}: binary not verified")
        linked = conn.execute(
            "SELECT source_id FROM binary_to_source WHERE binary_id = ?", (row[0],)
        ).fetchone()
        if linked is None:
            problems.append(f"{name}: binary not linked to an imported source")
        else:
            source = conn.execute(
                "SELECT verified FROM sources WHERE id = ?", (linked[0],)
            ).fetchone()
            if source is None or source[0] != 1:
                problems.append(f"{name}: linked source not verified")
    return not problems, tuple(problems)


def _local_packages_text(
    conn: sqlite3.Connection, config
) -> str:
    """`Packages` paragraphs for the `local` component from verified rows.

    Serves every current verified `local` row — imports are recorded under the
    configured first release, but the scratch repo's `local` index is not
    scoped to it (historical behavior). A missing pool payload is skipped with
    a warning (it cannot be served); an unreadable payload falls back to the
    stored fields and is reported, so a degraded entry is never produced
    silently.
    """
    paragraphs: list[str] = []
    for rid, name, version, built, used_fallback in local_entries(conn, config):
        if built is None:
            logger.warning(
                "package-stack skipped local %s %s (id %d): missing pool payload",
                name, version, rid,
            )
            continue
        if used_fallback:
            logger.warning(
                "package-stack degraded local %s %s (id %d): unreadable pool payload, "
                "stored fields used",
                name, version, rid,
            )
        entry, _rel, _size, _sha256, _used_fallback = built
        paragraphs.append(paragraph(entry))
    return "\n\n".join(paragraphs) + ("\n" if paragraphs else "")


def _index_hashes_for_existing(
    repo_dir: str, release: str, component: str
) -> dict[str, dict]:
    """Hash entries for an existing component index (Packages/Packages.gz).

    apt's `file://` source requires a `Release` hash for the *uncompressed*
    `Packages` form even when the archive stores only `Packages.gz`: it
    fetches the `.gz` and verifies the decompressed content against the plain
    hash. When only one form is on disk, the missing plain hash is synthesized
    by decompressing the stored `.gz` — without touching the stored files, as
    the archive component dirs may be symlinks into the cache and must never
    be mutated. A `.gz` hash entry is emitted only when the `.gz` file is
    actually present, so apt never looks for a file that does not exist.
    """
    base = os.path.join(repo_dir, "dists", release, component, "binary-amd64")
    plain_path = os.path.join(base, "Packages")
    gz_path = os.path.join(base, "Packages.gz")
    plain_data = None
    if os.path.isfile(plain_path):
        with open(plain_path, "rb") as f:
            plain_data = f.read()
    gz_data = None
    if os.path.isfile(gz_path):
        with open(gz_path, "rb") as f:
            gz_data = f.read()
    return hash_entries_for_pair(
        component,
        "binary-amd64/Packages",
        plain_data,
        "binary-amd64/Packages.gz",
        gz_data,
    )


def _build_scratch_repo(
    conn: sqlite3.Connection,
    config,
    release: str,
    configured_components: tuple[str, ...],
    repo_dir: str,
    epoch: int,
) -> tuple[str, list[str]]:
    """An apt-usable scratch mirror of the cache (no signatures).

    Archive components symlink the cache's own index dirs (nothing to
    generate); the `local` component index is generated from verified DB
    rows. The pool is symlinked so every payload resolves. Returns the repo
    root and the component list it actually carries.
    """
    os.makedirs(repo_dir, exist_ok=True)
    dists = os.path.join(repo_dir, "dists", release)
    components: list[str] = []
    for component in configured_components:
        src = os.path.join(config.binary_cache, "dists", release, component)
        if os.path.isdir(src):
            os.makedirs(os.path.dirname(os.path.join(dists, component)), exist_ok=True)
            os.symlink(src, os.path.join(dists, component))
            components.append(component)
    components.append("local")

    local_dir = os.path.join(dists, "local", "binary-amd64")
    os.makedirs(local_dir, exist_ok=True)
    local_text = _local_packages_text(conn, config)
    with open(os.path.join(local_dir, "Packages"), "w", encoding="utf-8") as f:
        f.write(local_text)
    write_gzip(os.path.join(local_dir, "Packages.gz"), local_text)


    pool_src = os.path.join(config.binary_cache, "pool")
    if os.path.isdir(pool_src):
        os.symlink(pool_src, os.path.join(repo_dir, "pool"))

    index_hashes: dict[str, dict] = {}
    for component in components:
        index_hashes.update(_index_hashes_for_existing(repo_dir, release, component))
    release_text = _release_text(release, components, index_hashes, epoch)
    with open(os.path.join(repo_dir, "dists", release, "Release"), "w", encoding="utf-8") as f:
        f.write(release_text)
    return repo_dir, components


def _simulate_offline_install(
    conn: sqlite3.Connection,
    config,
    set_names: tuple[str, ...],
    repo_dir: str,
    release: str,
    components: list[str],
    apt_dir: str,
) -> tuple[bool, str]:
    """`apt-get install --simulate` of the set against the scratch repo.

    Fully offline (`file://`, `trusted=yes`). Returns `(ok, detail)`; the
    detail names the unmet dependencies on failure.
    """
    if not shutil.which("apt-get"):
        return False, "apt-get is not available on this host"
    for sub in ("lists", "cache", "archives", "confparts"):
        os.makedirs(os.path.join(apt_dir, sub), exist_ok=True)
    with open(os.path.join(apt_dir, "sources.list"), "w", encoding="utf-8") as f:
        f.write(f"deb [trusted=yes] file:{repo_dir} {release} {' '.join(components)}\n")
    with open(os.path.join(apt_dir, "dpkg-status"), "w", encoding="utf-8") as f:
        f.write("")
    env = {
        "Dir::Etc::sourcelist": os.path.join(apt_dir, "sources.list"),
        "Dir::Etc::sourceparts": "-",
        "Dir::Etc::apt.conf": "/dev/null",
        "Dir::Etc::parts": os.path.join(apt_dir, "confparts"),
        "Dir::State::lists": os.path.join(apt_dir, "lists"),
        "Dir::State::status": os.path.join(apt_dir, "dpkg-status"),
        "Dir::Cache": os.path.join(apt_dir, "cache"),
        "Dir::Cache::archives": os.path.join(apt_dir, "archives"),
        "Debug::NoLocking": "1",
    }

    def apt_get(*args: str) -> subprocess.CompletedProcess:
        cmd = ["apt-get"]
        for key, value in env.items():
            cmd += ["-o", f"{key}={value}"]
        cmd += list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    update = apt_get("update")
    if update.returncode != 0:
        return False, f"apt-get update failed: {update.stderr.strip()[:300]}"
    install = apt_get("install", "--simulate", "-y", *set_names)
    if install.returncode == 0:
        return True, ""
    reason = (install.stderr or install.stdout).strip()
    if "unmet dependencies" in reason or "E:" in reason:
        return False, reason[:300]
    return False, reason[:300] or "apt-get install --simulate failed"


def run_package_stack(
    config,
    recipe_path: str,
    stage_root: str = DEFAULT_STAGE_ROOT,
    work_dir: str | None = None,
) -> PackResult:
    """Package and import every recipe component into `local`, in order.

    For each component in dependency order: `debgen` builds the deterministic
    `.deb` from its staged directory, `srcgen` builds the rebuildable source
    set from the pinned tree, and `import_local_deb` registers + verifies the
    pair (re-resolution, checksums, `binary_to_source`, idempotence). An
    un-attributable library the recipe does not declare fails the component
    loudly. After the set is imported, the whole-set checks run: the `verify`
    semantics (no unlinked binaries, no unverified records for the set) and a
    simulated offline `apt install` of the whole set against a scratch mirror
    of the cache — both must pass before success. A component failure aborts
    with zero DB/pool changes for that component (prior components stay
    imported).
    """
    recipe = load_recipe(recipe_path)
    order = resolve_component_order(recipe)
    source_epoch = compute_source_epoch(recipe, recipe_path)
    work_root = work_dir or tempfile.mkdtemp(prefix="package-stack-")
    default_maintainer = f"OpenFromSource <{config.admin_email}>"

    conn = sqlite3.connect(config.db_path)
    results: list[ComponentResult] = []
    try:
        for name in order:
            component = _component_by_name(recipe, name)
            component_work = os.path.join(work_root, name)
            os.makedirs(component_work, exist_ok=True)
            stage_dir = os.path.join(stage_root, component.name)
            if not os.path.isdir(stage_dir):
                raise PackagingError(
                    f"component {component.name}: staged install directory not found at {stage_dir}"
                )

            deb = build_binary_package(
                stage_dir, component, os.path.join(component_work, "deb"), source_epoch,
                default_maintainer=default_maintainer,
            )

            undeclared = undeclared_unattributed(recipe, component, deb.unattributed)
            if undeclared:
                raise PackagingError(
                    f"component {component.name}: un-attributable library "
                    f"{undeclared[0]!r} is not declared in the recipe — "
                    "add it to explicit_depends"
                )

            src = build_source_package(
                component,
                recipe.repo_dir,
                os.path.join(component_work, "src"),
                source_epoch,
                default_maintainer=default_maintainer,
            )

            imported = import_local_deb(conn, config, deb.deb_path, source_dir=src.source_dir)
            results.append(
                ComponentResult(
                    name=component.name,
                    binary_package=component.binary_package,
                    version=imported.version,
                    architecture=imported.architecture,
                    derived_depends=deb.derived_depends,
                    explicit_depends=component.explicit_depends,
                    unattributed=deb.unattributed,
                    source_files=src.file_count,
                    linked=True,
                    verified=True,
                )
            )

        set_names = tuple(
            component.binary_package for component in _ordered_components(recipe, order)
        )
        verify_ok, problems = whole_set_verify(conn, set_names)
        if not verify_ok:
            raise PackagingError(
                "whole-set verify failed: " + "; ".join(problems)
            )

        release = config.sources[0].release
        configured_components = tuple(config.sources[0].components)
        repo_dir, components = _build_scratch_repo(
            conn, config, release, configured_components,
            os.path.join(work_root, "apt-repo"), source_epoch,
        )
        install_ok, detail = _simulate_offline_install(
            conn, config, set_names, repo_dir, release, components,
            os.path.join(work_root, "apt"),
        )
        if not install_ok:
            raise PackagingError(
                f"simulated offline apt install failed: {detail}"
            )
    finally:
        conn.close()

    return PackResult(
        stack=recipe.stack,
        components_total=len(recipe.components),
        components_imported=len(results),
        order=order,
        components=tuple(results),
        verify_ok=True,
        install_ok=True,
    )


def _ordered_components(recipe: StackRecipe, order: tuple[str, ...]) -> list[ComponentRecipe]:
    return [_component_by_name(recipe, name) for name in order]
