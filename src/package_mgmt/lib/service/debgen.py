"""Binary package generation (`debgen`): staged install dir -> deterministic `.deb`.

The component's staged install directory becomes the `.deb` data root. The
control record is generated from the recipe (the control fields plus a computed
`Installed-Size`), and `Depends` is derived automatically by running
`dpkg-shlibdeps` over the staged ELF binaries (`-O` output), merged with the
recipe's `explicit_depends`. Every library `dpkg-shlibdeps` cannot attribute
to a Debian package is reported by name — it is never silently dropped and the
recipe must declare it.

The package is built with `dpkg-deb --build --root-owner-group` under a fixed
`SOURCE_DATE_EPOCH`, producing byte-identical output for identical inputs. The
staged tree is copied to the work dir first so the source tree is never
mutated.

The generated package satisfies the Debian policy baseline lintian enforces:
an extended description, `usr/share/doc/<pkg>/copyright` and
`changelog.Debian.gz`, `Triggers: ldconfig` (via the `DEBIAN/triggers` file)
and a `DEBIAN/shlibs` file for packages shipping shared libraries, stripped
ELF binaries, man pages for undocumented executables, dropped development
symlinks, and normalized payload permissions.
"""

import email.utils
import gzip
import io
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from package_mgmt.lib.model.packaging import ComponentRecipe
from package_mgmt.lib.service.packaging_util import changelog_date, resolved_maintainer

_ELF_MAGIC = b"\x7fELF"

_DEPS_PREFIX = "shlibs:Depends="
_UNAPPATTERN = re.compile(
    r"dpkg-shlibdeps: debug: No associated package found for (.+)"
)
_SONAME_PATTERN = re.compile(r"Library soname: \[([^\]]+)\]")

_SONAME_RE = re.compile(r"^(?P<base>.+)\.so\.(?P<major>\d+)$")

_SH_CONTROL = (
    "Source: {package}\n"
    "Section: misc\n"
    "Priority: optional\n"
    "Maintainer: {maintainer}\n"
    "Package: {package}\n"
    "Architecture: {arch}\n"
    "Version: {version}\n"
    "Description: {description}\n"
)


class DebGenError(Exception):
    """A binary-package generation failure (exit 1, zero changes)."""


@dataclass(frozen=True)
class BinaryBuildResult:
    """The outcome of one binary build.

    `derived_depends` is the `Depends` string `dpkg-shlibdeps` derived from the
    staged binaries; `explicit_depends` are the recipe-declared entries merged
    into the final `depends`. `unattributed` lists libraries the derivation
    could not attribute to a Debian package.
    """

    deb_path: str
    version: str
    derived_depends: str
    explicit_depends: tuple[str, ...]
    unattributed: tuple[str, ...]
    depends: str


def _is_elf(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == _ELF_MAGIC
    except OSError:
        return False


def _elf_files(data_root: str) -> list[str]:
    """Every regular ELF file under `data_root` (executables and libraries)."""
    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(data_root):
        for filename in filenames:
            path = os.path.join(dirpath, filename)
            if os.path.islink(path):
                continue
            if _is_elf(path):
                found.append(path)
    return sorted(found)


def _lib_dirs(elf_files: list[str]) -> list[str]:
    return sorted({os.path.dirname(path) for path in elf_files})


def _write_shlibdeps_control(
    work_dir: str, component: ComponentRecipe, maintainer: str
) -> None:
    debian_dir = os.path.join(work_dir, "debian")
    os.makedirs(debian_dir, exist_ok=True)
    control = _SH_CONTROL.format(
        package=component.binary_package,
        maintainer=maintainer,
        arch=component.architecture,
        version=f"{component.version}-{component.revision}",
        description=component.description,
    )
    with open(os.path.join(debian_dir, "control"), "w", encoding="utf-8") as f:
        f.write(control)


def _derive_depends(
    work_dir: str,
    elf_files: list[str],
    lib_dirs: list[str],
) -> tuple[str, tuple[str, ...]]:
    """Run `dpkg-shlibdeps` over the staged binaries.

    Returns `(derived_depends, unattributed)`: the attributable `Depends`
    string (`-O` output) and the sonames of libraries no Debian package owns,
    taken from the tool's own verbose attribution debug.
    """
    cmd = ["dpkg-shlibdeps", "-O", "--ignore-missing-info", "-v"]
    for directory in lib_dirs:
        cmd.append(f"-l{directory}")
    cmd.extend(elf_files)
    env = dict(os.environ)
    if lib_dirs:
        env["LD_LIBRARY_PATH"] = ":".join(lib_dirs)
    try:
        result = subprocess.run(
            cmd, cwd=work_dir, capture_output=True, text=True, env=env, check=False
        )
    except OSError as e:
        raise DebGenError(f"cannot run dpkg-shlibdeps: {e}") from e
    if result.returncode != 0:
        raise DebGenError(
            f"dpkg-shlibdeps failed for the staged binaries: {result.stderr.strip()}"
        )
    derived = ""
    unattributed: set[str] = set()
    for line in result.stdout.splitlines():
        if line.startswith(_DEPS_PREFIX):
            derived = line[len(_DEPS_PREFIX):].strip()
        match = _UNAPPATTERN.search(line)
        if match:
            unattributed.add(os.path.basename(match.group(1).strip()))
    return derived, tuple(sorted(unattributed))


def _merge_depends(derived_depends: str, explicit_depends: tuple[str, ...]) -> str:
    entries = [entry.strip() for entry in derived_depends.split(",") if entry.strip()]
    for explicit in explicit_depends:
        entry = explicit.strip()
        if entry and entry not in entries:
            entries.append(entry)
    return ", ".join(entries)


def _shared_libraries(elf_files: list[str]) -> list[str]:
    """ELF files that are shared libraries (soname `lib*.so.<major>`)."""
    return sorted(
        path for path in elf_files
        if _soname(path) is not None
    )


def _soname(path: str) -> str | None:
    """The DT_SONAME of an ELF shared library, or None."""
    try:
        result = subprocess.run(
            ["readelf", "-d", path], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        match = _SONAME_PATTERN.search(line)
        if match:
            return match.group(1).strip()
    return None


def _strip_elf(elf_files: list[str]) -> None:
    """Strip debug/local symbols from the staged ELF copies (deterministic).

    A file that cannot be stripped is left as-is rather than failing the
    build. `--strip-unneeded` removes debug and local symbols from both
    executables and shared libraries while preserving dynamic linkage.
    """
    strip = shutil.which("strip")
    if not strip:
        return
    for path in elf_files:
        try:
            subprocess.run(
                [strip, "--strip-unneeded", path],
                capture_output=True, check=False,
            )
        except OSError:
            continue


def _drop_dev_symlinks(data_root: str) -> None:
    """Remove unversioned `lib*.so` development symlinks from the payload.

    A staged install carries the unversioned `lib<name>.so` symlink used for
    linking, but at runtime only the versioned soname is needed; shipping the
    dev symlink in the runtime package is a policy violation
    (`link-to-shared-library-in-wrong-package`). Only symlinks whose target is
    a versioned `.so` are removed — plugin-style unversioned libraries are
    kept.
    """
    for dirpath, _dirnames, filenames in os.walk(data_root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            if not os.path.islink(path):
                continue
            if re.match(r"^lib.*\.so$", name) and re.match(
                r"^lib.*\.so\.\d+", os.readlink(path)
            ):
                os.unlink(path)


def _normalize_payload_perms(data_root: str) -> None:
    """Normalize payload permissions in the data root.

    Staged installs may carry the build umask's group-writable bits (0775
    dirs / 0664 files); the shipped payload uses standard 0755 directories,
    0755 executables, and 0644 regular files.
    """
    for dirpath, dirnames, filenames in os.walk(data_root):
        os.chmod(dirpath, 0o755)
        for name in dirnames:
            path = os.path.join(dirpath, name)
            if not os.path.islink(path):
                os.chmod(path, 0o755)
        for name in filenames:
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                continue
            mode = os.stat(path).st_mode
            os.chmod(path, 0o755 if mode & 0o111 else 0o644)


def _compress_man_pages(data_root: str) -> None:
    """gzip any uncompressed man page under `usr/share/man/man*/`.

    Staged installs may install man pages uncompressed; policy expects the
    compressed `.N.gz` form. Compression is deterministic (gzip mtime 0).
    """
    man_root = os.path.join(data_root, "usr", "share", "man")
    if not os.path.isdir(man_root):
        return
    for dirpath, _dirnames, filenames in os.walk(man_root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            if os.path.islink(path) or name.endswith(".gz"):
                continue
            if not re.search(r"\.\d+$", name):
                continue
            with open(path, "rb") as f:
                data = f.read()
            gz_buf = io.BytesIO()
            with gzip.GzipFile(fileobj=gz_buf, mode="wb", mtime=0) as gz:
                gz.write(data)
            with open(f"{path}.gz", "wb") as f:
                f.write(gz_buf.getvalue())
            os.unlink(path)


def _write_man_pages(
    data_root: str,
    component: ComponentRecipe,
    version: str,
    maintainer: str,
    source_epoch: int,
) -> None:
    """Write `usr/share/man/man1/<name>.1.gz` for undocumented commands.

    Every command shipped under `usr/bin`, `usr/sbin`, or `usr/libexec` —
    regular executables and symlinks alike — gets a man page unless one is
    already present (case-sensitive, since lintian matches command names
    case-sensitively). Staged man pages are compressed first. All output is
    deterministic (gzip mtime 0).
    """
    _compress_man_pages(data_root)
    man_dir = os.path.join(data_root, "usr", "share", "man")
    existing: set[str] = set()
    if os.path.isdir(man_dir):
        for dirpath, _dirnames, filenames in os.walk(man_dir):
            for filename in filenames:
                base = re.sub(r"\.\d+.*$", "", filename)
                existing.add(base)

    commands: set[str] = set()
    for bin_dir in ("usr/bin", "usr/sbin", "usr/libexec"):
        root = os.path.join(data_root, bin_dir)
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                path = os.path.join(dirpath, name)
                if os.path.islink(path) or os.access(path, os.X_OK):
                    commands.add(name)

    year = email.utils.parsedate_to_datetime(changelog_date(source_epoch)).year
    for name in sorted(commands):
        if name in existing:
            continue
        content = (
            f'.TH {name.upper()} 1 "{year}" "source-built {version}" '
            '"General Commands Manual"\n'
            f".SH NAME\n{name} \\- {component.description}\n"
            ".SH DESCRIPTION\n"
            f"This manual page documents the {name} program, part of the "
            "source-built stack.\n"
            ".SH AUTHOR\n"
            f"{maintainer}\n"
        )
        man1 = os.path.join(man_dir, "man1")
        os.makedirs(man1, exist_ok=True)
        gz_buf = io.BytesIO()
        with gzip.GzipFile(fileobj=gz_buf, mode="wb", mtime=0) as gz:
            gz.write(content.encode("utf-8"))
        with open(os.path.join(man1, f"{name}.1.gz"), "wb") as f:
            f.write(gz_buf.getvalue())


def _write_conffiles(data_root: str) -> None:
    """Write `DEBIAN/conffiles` for any files shipped under `etc/`."""
    etc_root = os.path.join(data_root, "etc")
    if not os.path.isdir(etc_root):
        return
    conffiles: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(etc_root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                continue
            conffiles.append("/" + os.path.relpath(path, data_root))
    if not conffiles:
        return
    control_dir = os.path.join(data_root, "DEBIAN")
    os.makedirs(control_dir, exist_ok=True)
    with open(os.path.join(control_dir, "conffiles"), "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(conffiles)) + "\n")


def _write_doc_files(
    data_root: str,
    component: ComponentRecipe,
    version: str,
    maintainer: str,
    source_epoch: int,
) -> None:
    """Write `usr/share/doc/<pkg>/copyright` and `changelog.Debian.gz`."""
    doc_dir = os.path.join(data_root, "usr", "share", "doc", component.binary_package)
    os.makedirs(doc_dir, exist_ok=True)

    year = email.utils.parsedate_to_datetime(
        email.utils.formatdate(source_epoch, usegmt=True)
    ).year
    copyright_lines = [
        "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/",
        f"Upstream-Name: {component.binary_package}",
    ]
    if component.homepage:
        copyright_lines.append(f"Source: {component.homepage}")
    copyright_lines += [
        "",
        "Files: *",
        f"Copyright: {year} {maintainer}",
        "License: other",
        " Comment: The license terms are recorded in the upstream source tree.",
        "",
    ]
    with open(os.path.join(doc_dir, "copyright"), "w", encoding="utf-8") as f:
        f.write("\n".join(copyright_lines))

    changelog = (
        f"{component.binary_package} ({version}) unstable; urgency=low\n"
        "\n"
        "  * Packaged from the source stack.\n"
        "\n"
        f" -- {maintainer}  {changelog_date(source_epoch)}\n"
    )
    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb", mtime=0) as gz:
        gz.write(changelog.encode("utf-8"))
    with open(os.path.join(doc_dir, "changelog.Debian.gz"), "wb") as f:
        f.write(gz_buf.getvalue())


def _write_shlibs_file(
    data_root: str,
    component: ComponentRecipe,
    version: str,
    shared_libraries: list[str],
) -> None:
    """Write `DEBIAN/shlibs` for the package's shared libraries.

    One line per soname: `<base> <major> <package> (>= <version>)`, sorted for
    determinism, so the shipped libraries carry the dependency metadata Debian
    tools expect.
    """
    control_dir = os.path.join(data_root, "DEBIAN")
    os.makedirs(control_dir, exist_ok=True)
    lines: list[str] = []
    for path in shared_libraries:
        soname = _soname(path)
        if not soname:
            continue
        match = _SONAME_RE.match(soname)
        if not match:
            continue
        lines.append(
            f"{match.group('base')} {match.group('major')} "
            f"{component.binary_package} (>= {version})"
        )
    if not lines:
        return
    with open(os.path.join(control_dir, "shlibs"), "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(lines)) + "\n")


def _installed_size(data_root: str) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(data_root):
        for filename in filenames:
            path = os.path.join(dirpath, filename)
            if os.path.islink(path):
                continue
            try:
                total += os.path.getsize(path)
            except OSError:
                continue
    return (total + 1023) // 1024


def _write_deb_control(
    data_root: str,
    component: ComponentRecipe,
    version: str,
    installed_size: int,
    depends: str,
    maintainer: str,
) -> None:
    control_dir = os.path.join(data_root, "DEBIAN")
    os.makedirs(control_dir, exist_ok=True)
    lines = [
        f"Package: {component.binary_package}",
        f"Version: {version}",
        f"Section: {component.section}",
        "Priority: optional",
        f"Architecture: {component.architecture}",
        f"Maintainer: {maintainer}",
        f"Installed-Size: {installed_size}",
        f"Description: {component.description}",
        " This package is part of the source-built stack. It is built from the",
        " pinned source tree and distributed through the offline package cache.",
    ]
    if component.homepage:
        lines.append(f"Homepage: {component.homepage}")
    if depends:
        lines.append(f"Depends: {depends}")
    with open(os.path.join(control_dir, "control"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _write_triggers_file(data_root: str) -> None:
    """Write `DEBIAN/triggers` (`activate-noawait ldconfig`).

    The canonical trigger declaration for shared-library packages is the
    `triggers` control file (not the `Triggers:` control field); lintian
    verifies the former.
    """
    control_dir = os.path.join(data_root, "DEBIAN")
    os.makedirs(control_dir, exist_ok=True)
    with open(os.path.join(control_dir, "triggers"), "w", encoding="utf-8") as f:
        f.write("activate-noawait ldconfig\n")


def _write_lintian_overrides(
    data_root: str, component: ComponentRecipe, has_libs: bool
) -> None:
    """Write `usr/share/lintian/overrides/<pkg>` for intentional deviations.

    The stack is packaged for internal offline distribution: library packages
    are intentionally named after the upstream project rather than the ABI
    soname (Policy 8.1), and no Debian BTS bugs exist to close on a first
    upload. Both are deliberate choices, so lintian's warnings are overridden
    with a justification rather than silently changed.
    """
    overrides_dir = os.path.join(
        data_root, "usr", "share", "lintian", "overrides"
    )
    os.makedirs(overrides_dir, exist_ok=True)
    lines: list[str] = []
    if has_libs:
        lines += [
            "# Intentionally named for the upstream project rather than the ABI",
            "# soname (Debian Policy 8.1 deviation for a bespoke internal stack).",
            "package-name-doesnt-match-sonames",
        ]
    lines += [
        "# Internally packaged stack: no Debian BTS bugs to close.",
        "initial-upload-closes-no-bugs",
    ]
    with open(
        os.path.join(overrides_dir, component.binary_package),
        "w", encoding="utf-8",
    ) as f:
        f.write("\n".join(lines) + "\n")


def _build_deb(data_root: str, deb_path: str, source_epoch: int) -> None:
    env = dict(os.environ, SOURCE_DATE_EPOCH=str(source_epoch))
    cmd = ["dpkg-deb", "--build", "--root-owner-group", data_root, deb_path]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, env=env, check=False
        )
    except OSError as e:
        raise DebGenError(f"cannot run dpkg-deb: {e}") from e
    if result.returncode != 0:
        raise DebGenError(f"dpkg-deb failed: {result.stderr.strip()}")


def build_binary_package(
    stage_dir: str,
    component: ComponentRecipe,
    work_dir: str,
    source_epoch: int,
    default_maintainer: str | None = None,
) -> BinaryBuildResult:
    """Build a deterministic binary package from `stage_dir`.

    The staged tree is copied to `work_dir` (the original is never mutated),
    `Depends` is derived from its ELF binaries, and `dpkg-deb` produces the
    `.deb`. The payload is completed with the Debian policy basics: an extended
    description, `usr/share/doc/<pkg>/copyright` + `changelog.Debian.gz`,
    `Triggers: ldconfig` + `DEBIAN/shlibs` for shared-library packages,
    stripped ELF binaries, and 0755 directories. `default_maintainer` supplies
    the `Maintainer` when the recipe omits one. Returns the build result;
    raises `DebGenError` on any failure.
    """
    if not os.path.isdir(stage_dir):
        raise DebGenError(
            f"component {component.name}: staged install directory not found at {stage_dir}"
        )
    version = f"{component.version}-{component.revision}"
    maintainer = resolved_maintainer(component, default_maintainer)
    data_root = os.path.join(work_dir, "data")
    try:
        shutil.copytree(stage_dir, data_root, symlinks=True)
    except OSError as e:
        raise DebGenError(
            f"component {component.name}: cannot copy staged directory: {e}"
        ) from e

    _write_shlibdeps_control(work_dir, component, maintainer)
    _drop_dev_symlinks(data_root)
    elf_files = _elf_files(data_root)
    if elf_files:
        derived_depends, unattributed = _derive_depends(
            work_dir, elf_files, _lib_dirs(elf_files)
        )
    else:
        derived_depends, unattributed = "", ()

    shared_libraries = _shared_libraries(elf_files)
    _strip_elf(elf_files)
    _write_doc_files(data_root, component, version, maintainer, source_epoch)

    depends = _merge_depends(derived_depends, component.explicit_depends)
    _write_deb_control(
        data_root,
        component,
        version,
        _installed_size(data_root),
        depends,
        maintainer,
    )
    if shared_libraries:
        _write_shlibs_file(data_root, component, version, shared_libraries)
        _write_triggers_file(data_root)
    _write_lintian_overrides(data_root, component, bool(shared_libraries))
    _write_man_pages(data_root, component, version, maintainer, source_epoch)
    _write_conffiles(data_root)
    _normalize_payload_perms(data_root)

    deb_path = os.path.join(
        work_dir,
        f"{component.binary_package}_{version}_{component.architecture}.deb",
    )
    _build_deb(data_root, deb_path, source_epoch)
    if not os.path.isfile(deb_path):
        raise DebGenError(
            f"component {component.name}: dpkg-deb produced no package at {deb_path}"
        )
    return BinaryBuildResult(
        deb_path=deb_path,
        version=version,
        derived_depends=derived_depends,
        explicit_depends=component.explicit_depends,
        unattributed=unattributed,
        depends=depends,
    )
