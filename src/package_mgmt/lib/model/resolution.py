from dataclasses import dataclass, field


@dataclass(frozen=True)
class PackageRequest:
    """A batch resolution request: a name with its optional requested version.

    The name and its requested version travel together (no flat name lists);
    version pins like `vim=1.2.3` are first-class. Consumed only at the public
    batch boundary (resolve_batch / resolve); internal walk helpers keep plain
    (name, requested_version) arguments.
    """

    name: str
    requested_version: str | None = None


@dataclass(frozen=True)
class PackageListResult:
    """Typed parse result of a package-list manifest (spec 009).

    `requests` holds the validated package requests in file order; `errors`
    holds one message per malformed line, each naming the file and line
    number. Validation is all-or-nothing: if `errors` is non-empty the
    invocation aborts before any resolution.
    """

    requests: tuple[PackageRequest, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderCandidate:
    package_name: str
    version: str
    architecture: str | None
    release: str
    component: str
    provides_version: str | None = None


@dataclass(frozen=True)
class ResolutionStats:
    packages: int = 0
    binaries: int = 0
    sources: int = 0
    build_tools: int = 0
    build_dep_links: int = 0
    virtual_count: int = 0
    errors: list[str] = field(default_factory=list)
    resolved_source: str | None = None
    fallback_count: int = 0
