"""Line-based parsers for project build-metadata dependency requirements.

Two build systems are in use across the source set (meson in the older
release eras, CMake in the newer ones), so requirements are extracted from
either `meson.build` or `CMakeLists.txt` depending on which file a pinned ref
ships. Parsing is deliberately line-based — no parser library — because the
requirements appear in a bounded set of call shapes and the tree must be
readable before any build toolchain is installed.
"""

import re

from package_mgmt.lib.model.alignment import BuildSystem, DependencyRequirement

_MESON_DEP_RE = re.compile(r"(?<![\w.])dependency\(\s*['\"]([^'\"]+)['\"]")
_MESON_VERSION_RE = re.compile(r"version\s*:\s*['\"]([^'\"]+)['\"]")
_MESON_OPTIONAL_RE = re.compile(r"required\s*:\s*(false|get_option\()")

_CMAKE_SET_RE = re.compile(r"set\(\s*([A-Za-z0-9_]+)\s+([^)]+?)\s*\)")
_CMAKE_FIND_RE = re.compile(r"find_package\(\s*([A-Za-z0-9_+./-]+)([^)]*)\)")
_CMAKE_PKG_CHECK_RE = re.compile(
    r"pkg_(?:check_modules|search_module)\(\s*[A-Za-z0-9_]+([^)]*)\)"
)

# CMake infrastructure/built-in modules that are not version-alignment
# dependencies (toolchain plumbing, not pkg-config/system libraries).
_CMAKE_INFRA_MODULES = {
    "PkgConfig",
    "FindPkgConfig",
    "Threads",
    "OpenGL",
    "OpenGLES2",
    "OpenGLES3",
    "EGL",
    "GLUT",
    "GTest",
    "GMock",
    "CTest",
    "Git",
    "Python3",
    "Python",
    "PackageHandleStandardArgs",
    "CMakePackageConfigHelpers",
    "FetchContent",
    "ExternalProject",
}

# pkg_check_modules flag keywords (uppercase, carry no module name).
_CMAKE_PKG_CHECK_FLAGS = {
    "REQUIRED",
    "QUIET",
    "IMPORTED_TARGET",
    "GLOBAL",
    "NO_CMAKE_PATH",
    "NO_CMAKE_ENVIRONMENT_PATH",
    "NO_CMAKE_SYSTEM_PATH",
    "NO_CMAKE_PACKAGE_REGISTRY",
}

_OPERATOR_RE = re.compile(r"^(>=|<=|>|<|!=|=)(.+)$")


def _split_version_spec(spec: str) -> tuple[str, str]:
    """Split a version string like `>=0.4.2` into (operator, version).

    An empty or operator-less spec means "any version is acceptable".
    """
    spec = spec.strip()
    if not spec:
        return "=", ""
    m = _OPERATOR_RE.match(spec)
    if m:
        return m.group(1), m.group(2).strip()
    return "=", spec


def _split_module_token(token: str) -> tuple[str, str, str]:
    """Split a `pkg_check_modules` token like `libinput>=1.29` into
    (module, operator, version); a bare token has no version constraint."""
    token = token.strip()
    m = re.match(r"^([A-Za-z0-9_+./-]+?)(>=|<=|>|<|=)(.+)$", token)
    if m:
        return m.group(1), m.group(2), m.group(3).strip()
    return token, "=", ""


def parse_meson_requirements(
    repo: str, text: str, path: str = "meson.build"
) -> list[DependencyRequirement]:
    """Extract requirements from a `meson.build` file.

    Recognizes `dependency('<module>', version: '<op><ver>')` calls. A call
    carrying `required: false` or a `get_option(...)` gate is optional.
    """
    reqs: list[DependencyRequirement] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        match = _MESON_DEP_RE.search(line)
        if not match:
            continue
        module = match.group(1)
        version_spec = ""
        version_match = _MESON_VERSION_RE.search(line)
        if version_match:
            version_spec = version_match.group(1)
        operator, version = _split_version_spec(version_spec)
        optional = bool(_MESON_OPTIONAL_RE.search(line))
        reqs.append(
            DependencyRequirement(
                repo=repo,
                module=module,
                operator=operator,
                version=version,
                source_ref=f"{repo}:{path}:{lineno}",
                optional=optional,
            )
        )
    return reqs


def parse_cmake_requirements(
    repo: str, text: str, path: str = "CMakeLists.txt"
) -> list[DependencyRequirement]:
    """Extract requirements from a `CMakeLists.txt` file.

    Handles `find_package(<Name> <ver> REQUIRED)` and `pkg_check_modules(...)`
    (which may span multiple lines) with `mod<op><ver>` tokens, plus `${VAR}`
    expansion from `set(VAR value)`. A find/check without `REQUIRED` (or with
    `QUIET`) is optional.
    """
    variables: dict[str, str] = {}
    for name, value in _CMAKE_SET_RE.findall(text):
        variables[name] = value.strip()
    text = re.sub(
        r"\$\{([A-Za-z0-9_]+)\}",
        lambda m: variables.get(m.group(1), m.group(0)),
        text,
    )

    events: list[tuple[int, str, re.Match]] = []
    for match in _CMAKE_FIND_RE.finditer(text):
        events.append((match.start(), "find", match))
    for match in _CMAKE_PKG_CHECK_RE.finditer(text):
        events.append((match.start(), "check", match))
    events.sort(key=lambda event: event[0])

    reqs: list[DependencyRequirement] = []
    for start, kind, match in events:
        lineno = text.count("\n", 0, start) + 1
        if kind == "find":
            name = match.group(1)
            if name in _CMAKE_INFRA_MODULES:
                continue
            rest = match.group(2).split()
            version = next((t for t in rest if t[0:1].isdigit()), "")
            operator, version = _split_version_spec(version)
            # find_package(<Name> <version> ...) treats the version as a
            # minimum (CMake semantics), not an exact pin.
            if version and operator == "=" and re.fullmatch(r"[\d.]+", version):
                operator = ">="
            optional = "REQUIRED" not in rest or "QUIET" in rest
            reqs.append(
                DependencyRequirement(
                    repo=repo,
                    module=name,
                    operator=operator,
                    version=version,
                    source_ref=f"{repo}:{path}:{lineno}",
                    optional=optional,
                )
            )
            continue
        args = match.group(1).split()
        required = "REQUIRED" in args
        optional = not required or "QUIET" in args
        for token in args:
            if not token or token in _CMAKE_PKG_CHECK_FLAGS:
                continue
            if token.isupper():
                continue
            module, operator, version = _split_module_token(token)
            reqs.append(
                DependencyRequirement(
                    repo=repo,
                    module=module,
                    operator=operator,
                    version=version,
                    source_ref=f"{repo}:{path}:{lineno}",
                    optional=optional,
                )
            )
    return reqs


PARSERS: dict[BuildSystem, object] = {
    "meson": parse_meson_requirements,
    "cmake": parse_cmake_requirements,
}
