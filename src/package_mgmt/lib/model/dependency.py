import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from package_mgmt.lib.util.versions import satisfies_constraint

logger = logging.getLogger(__name__)

# Matches one dependency member:
#   name (:arch)? (op ver)? [arch list]? <profiles>...
# The `[arch list]` group and **multiple** `<profile>` groups are captured so
# arch restrictions and build profiles are never silently dropped.
_ALT_PATTERN = re.compile(
    r"^([a-zA-Z0-9][a-zA-Z0-9+._-]*)"
    r"(?::([a-zA-Z0-9]+))?"
    r"\s*"
    r"(?:\(([<>=!]+)\s*([^)]+)\))?"
    r"\s*"
    r"(\[[^\]]*\])?"
    r"((?:\s*<[^<>]+>)*)"
    r"\s*$"
)


def _split_whitespace_tokens(text: str) -> tuple[str, ...]:
    return tuple(t for t in text.split() if t)


def _tuple_or_none(value) -> tuple[str, ...] | None:
    return tuple(value) if value else None


def _arch_restriction_from(match_text: str | None) -> tuple[str, ...] | None:
    """Parse a `[arch list]` group like `[amd64 !i386]` into raw tokens."""
    if not match_text:
        return None
    inner = match_text.strip()[1:-1]
    return _split_whitespace_tokens(inner) or None


def _profiles_from(match_text: str) -> tuple[str, ...] | None:
    """Parse the `<profile>` groups (`<!nocheck !cross>` or `<cross> <stage1>`)
    into tokens, `!` negation preserved."""
    tokens: list[str] = []
    for group in re.findall(r"<([^<>]+)>", match_text):
        tokens.extend(_split_whitespace_tokens(group))
    return tuple(tokens) if tokens else None


def _arch_os_cpu(arch: str) -> tuple[str, str]:
    """Split a Debian arch (or arch wildcard) into `(os, cpu)`. A bare name
    like `amd64` is the Linux port (elided `linux-` prefix); `linux-any` ->
    `(linux, any)`, `any-amd64` -> `(any, amd64)`."""
    if "-" in arch:
        os_name, _, cpu = arch.partition("-")
        return os_name, cpu
    return "linux", arch


def _arch_match(target_arch: str, spec: str) -> bool:
    """dpkg `arch_match`: exact equality, `any`, or the `<os>-any` /
    `any-<cpu>` / `<os>-<cpu>` wildcard forms, on either side."""
    if target_arch == spec:
        return True
    if target_arch == "any" or spec == "any":
        return True
    t_os, t_cpu = _arch_os_cpu(target_arch)
    s_os, s_cpu = _arch_os_cpu(spec)
    if t_os == s_os and t_cpu == s_cpu:
        return True
    if s_os == "any" and s_cpu == t_cpu:
        return True
    if s_cpu == "any" and s_os == t_os:
        return True
    return False


def _arch_allows(restriction: tuple[str, ...] | None, target_arch: str) -> bool:
    """Debian `[arch]` restriction semantics (dpkg `arch_is_concerned`): a
    negated token that matches the target excludes it; a positive token that
    matches includes it. Tokens may be arch wildcards (`any`, `linux-any`,
    `any-<cpu>`, `<os>-<cpu>`). A restriction with only negated tokens allows
    everything not excluded; one with positive tokens allows only matches."""
    if not restriction:
        return True
    have_positive = False
    for token in restriction:
        if token.startswith("!"):
            if _arch_match(target_arch, token[1:]):
                return False
        else:
            have_positive = True
            if _arch_match(target_arch, token):
                return True
    return not have_positive


@dataclass(frozen=True)
class Alternative:
    """One member of an alternative (`a | b`) group with its **own** version
    constraint and `:arch`: a group is satisfied only when a member satisfies
    its own constraint."""

    name: str
    version_constraint: dict | None = None
    architecture: str | None = None

    def to_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {"name": self.name}
        if self.version_constraint:
            result["version_constraint"] = self.version_constraint
        if self.architecture:
            result["architecture"] = self.architecture
        return result

    @classmethod
    def from_json(cls, data) -> "Alternative":
        if isinstance(data, str):
            # Legacy string-form alternative from a pre-feature cache.
            return cls(name=data)
        return cls(
            name=data["name"],
            version_constraint=data.get("version_constraint"),
            architecture=data.get("architecture"),
        )


def _parse_alternatives(alts) -> tuple[Alternative, ...]:
    """Normalize a stored `alternatives` list to `Alternative`s. Accepts both
    dict-form (new) and string-form (legacy) elements — backward compatible."""
    if not alts:
        return ()
    return tuple(Alternative.from_json(a) for a in alts)


@dataclass(frozen=True)
class DependencySpec:
    name: str
    version_constraint: dict | None = None
    architecture: str | None = None
    dep_type: str = "depends"
    alternatives: tuple[Alternative, ...] = ()
    arch_restriction: tuple[str, ...] | None = None
    profiles: tuple[str, ...] | None = None
    circular: bool = False

    def to_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "type": self.dep_type,
        }
        if self.version_constraint:
            result["version_constraint"] = self.version_constraint
        if self.architecture:
            result["architecture"] = self.architecture
        if self.alternatives:
            result["alternatives"] = [a.to_json() for a in self.alternatives]
        if self.arch_restriction:
            result["arch_restriction"] = list(self.arch_restriction)
        if self.profiles:
            result["profiles"] = list(self.profiles)
        if self.circular:
            result["circular"] = True
        return result

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "DependencySpec":
        return cls(
            name=data["name"],
            version_constraint=data.get("version_constraint"),
            architecture=data.get("architecture"),
            dep_type=data.get("type", "depends"),
            alternatives=_parse_alternatives(data.get("alternatives")),
            arch_restriction=_tuple_or_none(data.get("arch_restriction")),
            profiles=_tuple_or_none(data.get("profiles")),
            circular=bool(data.get("circular", False)),
        )


def serialize_depends(dep_specs: list[DependencySpec]) -> str:
    return json.dumps([d.to_json() for d in dep_specs])


def provider_satisfies_constraint(
    version_constraint: dict | None,
    provides_version: str | None,
    provider_version: str | None,
) -> bool:
    """True when a provider satisfies a version constraint.

    A versioned provides is checked against its declared version; an unversioned
    provides (stored as `''` or `None`) is checked against the provider's own
    version. `satisfies_constraint` treats an unconstrained dependency as
    satisfied, so passing `None` here degrades to a plain "a provider exists"
    check — the same posture as the direct-name check.
    """
    return satisfies_constraint(provides_version or provider_version, version_constraint)


def deps_satisfied(
    conn: sqlite3.Connection, dep_name: str, spec: DependencySpec
) -> bool:
    """True when the dependency (or any of its alternatives) is satisfied by a
    current binary — directly by name at a version satisfying the constraint,
    or via a provides row (versioned provides checked against the constraint,
    unversioned checked against the provider's own version)."""
    candidates = [(dep_name, spec.version_constraint)]
    candidates += [
        (a.name.split(":")[0], a.version_constraint) for a in spec.alternatives
    ]
    for name, vc in candidates:
        direct = conn.execute(
            "SELECT version FROM binaries"
            " WHERE package_name = ? AND status = 'current'",
            (name,),
        ).fetchall()
        if any(satisfies_constraint(ver, vc) for (ver,) in direct):
            return True
        providers = conn.execute(
            "SELECT b.version, p.provides_version FROM provides p"
            " JOIN binaries b ON b.package_name = p.package_name"
            " WHERE p.virtual_name = ? AND b.status = 'current'",
            (name,),
        ).fetchall()
        if any(
            provider_satisfies_constraint(vc, pv, bv) for bv, pv in providers
        ):
            return True
    return False


def serialize_depends_with_version_hash(
    dep_specs: list[DependencySpec],
    version_hash_fields: dict[str, str],
) -> str:
    """Serialize dependencies plus the original apt VersionHash field text.

    Used for `local` rows so a later generation-time fallback — an unreadable 
    pool payload — can still reproduce the installed record's VersionHash from 
    the values captured when the payload was readable. Old rows (and non-local 
    rows) stay a plain list and degrade gracefully: `deserialize_depends` and 
    `version_hash_fields_from_depends_json` accept both shapes.
    """
    payload: dict[str, Any] = {"dependencies": [d.to_json() for d in dep_specs]}
    if version_hash_fields:
        payload["version_hash"] = dict(version_hash_fields)
    return json.dumps(payload)


def deserialize_depends(raw: str) -> list[DependencySpec]:
    data = json.loads(raw)
    if isinstance(data, dict):
        data = data["dependencies"]
    return [DependencySpec.from_json(item) for item in data]


def version_hash_fields_from_depends_json(raw: str | None) -> dict[str, str]:
    """The original VersionHash field text stored by `import-deb`, or `{}`.

    Non-`local` rows and old rows store a plain dependency list and carry no
    `version_hash` payload; returning `{}` there is the graceful degradation.
    """
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        return {}
    fields = data.get("version_hash")
    return dict(fields) if isinstance(fields, dict) else {}


class DependencyGraph:
    @staticmethod
    def parse_depends_line(depends_raw: str, target_arch: str = "amd64") -> list[DependencySpec]:
        specs: list[DependencySpec] = []
        for part in depends_raw.split(","):
            part = part.strip()
            if not part:
                continue
            alternatives = [alt.strip() for alt in part.split("|")]
            first = True
            alt_objects: list[Alternative] = []
            for alt in alternatives:
                if not alt:
                    continue
                m = _ALT_PATTERN.match(alt)
                if not m:
                    # Never drop silently: warn and let the rest parse.
                    logger.warning("Cannot parse dependency token: %r", alt)
                    continue
                name = m.group(1)
                arch = m.group(2)
                op = m.group(3)
                ver = m.group(4)
                vc = {"operator": op, "version": ver} if op and ver else None
                arch_restriction = _arch_restriction_from(m.group(5))
                profiles = _profiles_from(m.group(6))
                if not _arch_allows(arch_restriction, target_arch):
                    continue
                if first:
                    specs.append(
                        DependencySpec(
                            name=name,
                            version_constraint=vc,
                            architecture=arch,
                            arch_restriction=arch_restriction,
                            profiles=profiles,
                        )
                    )
                    first = False
                else:
                    alt_objects.append(
                        Alternative(name=name, version_constraint=vc, architecture=arch)
                    )
            if alt_objects:
                specs[-1] = DependencySpec(
                    name=specs[-1].name,
                    version_constraint=specs[-1].version_constraint,
                    architecture=specs[-1].architecture,
                    dep_type=specs[-1].dep_type,
                    alternatives=tuple(alt_objects),
                    arch_restriction=specs[-1].arch_restriction,
                    profiles=specs[-1].profiles,
                )
        return specs


@dataclass(frozen=True)
class BuildDependencySpec:
    name: str
    version_constraint: dict | None = None
    architecture: str | None = None
    build_context: str = "build"
    profiles: tuple[str, ...] | None = None
    alternatives: tuple[Alternative, ...] = ()
    arch_restriction: tuple[str, ...] | None = None

    def to_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "build_context": self.build_context,
        }
        if self.version_constraint:
            d["version_constraint"] = self.version_constraint
        if self.architecture:
            d["architecture"] = self.architecture
        if self.profiles:
            d["profiles"] = list(self.profiles)
        if self.alternatives:
            d["alternatives"] = [a.to_json() for a in self.alternatives]
        if self.arch_restriction:
            d["arch_restriction"] = list(self.arch_restriction)
        return d

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "BuildDependencySpec":
        return cls(
            name=data["name"],
            version_constraint=data.get("version_constraint"),
            architecture=data.get("architecture"),
            build_context=data.get("build_context", "build"),
            profiles=_tuple_or_none(data.get("profiles")),
            alternatives=_parse_alternatives(data.get("alternatives")),
            arch_restriction=_tuple_or_none(data.get("arch_restriction")),
        )


def serialize_build_depends(specs: list[BuildDependencySpec]) -> str:
    return json.dumps([s.to_json() for s in specs])


def parse_build_depends_field(
    raw: str | None, context: str = "build", target_arch: str = "amd64",
) -> list[BuildDependencySpec]:
    if not raw:
        return []
    specs: list[BuildDependencySpec] = []
    for dep in DependencyGraph.parse_depends_line(raw, target_arch):
        specs.append(BuildDependencySpec(
            name=dep.name,
            version_constraint=dep.version_constraint,
            architecture=dep.architecture,
            build_context=context,
            arch_restriction=dep.arch_restriction,
            profiles=dep.profiles,
            alternatives=dep.alternatives,
        ))
    return specs
