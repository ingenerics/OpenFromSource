from dataclasses import dataclass


@dataclass(frozen=True)
class ArchiveSource:
    mirror: str
    release: str
    components: tuple[str, ...]


@dataclass(frozen=True)
class ProjectConfig:
    base_dir: str = "/var/cache/package_mgmt"
    binary_cache: str = "{base_dir}/binary"
    source_cache: str = "{base_dir}/source"
    iso_dir: str = "{base_dir}/iso"
    db_path: str = "{base_dir}/package_mgmt.db"
    log_level: str = "INFO"
    log_dir: str = "{base_dir}/logs"
    sources: tuple[ArchiveSource, ...] = (
        ArchiveSource(
            mirror="https://deb.debian.org/debian",
            release="trixie",
            components=("main",),
        ),
    )
    fetch_source: bool = False
    max_build_dep_report_depth: int = 2
    max_source_dep_depth: int = 0
    admin_email: str = "noreply@example.com"
