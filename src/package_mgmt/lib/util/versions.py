from debian.debian_support import Version


def parse_version(version_str: str) -> Version:
    return Version(version_str)


def version_compare(a: str, b: str) -> int:
    va = parse_version(a)
    vb = parse_version(b)
    if va < vb:
        return -1
    elif va > vb:
        return 1
    return 0


def compare_versions(version: str, operator: str, target: str) -> bool:
    v = parse_version(version)
    t = parse_version(target)
    if operator == ">=":
        return v >= t
    if operator == ">" or operator == ">>":
        return v > t
    if operator == "<=":
        return v <= t
    if operator == "<" or operator == "<<":
        return v < t
    if operator == "=":
        return v == t
    if operator == "!=":
        return v != t
    return False


def satisfies_constraint(version: str, version_constraint: dict | None) -> bool:
    if not version_constraint:
        return True
    op = version_constraint.get("operator", ">=")
    target = version_constraint.get("version", "")
    return compare_versions(version, op, target)


def source_version_from_binary(binary_version: str) -> str:
    v = parse_version(binary_version)
    if v.debian_revision and "+b" in v.debian_revision:
        idx = v.debian_revision.index("+b")
        base_revision = v.debian_revision[:idx]
        if v.epoch:
            return f"{v.epoch}:{v.upstream_version}-{base_revision}"
        return f"{v.upstream_version}-{base_revision}"
    return binary_version
