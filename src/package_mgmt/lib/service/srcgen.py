"""Source package generation (`srcgen`): pinned source tree -> rebuildable `.dsc` set.

The component's pinned source tree (`repo_dir`/`source_tree`) is copied to a
scratch dir (build output and VCS metadata excluded), wrapped in a generated
minimal `debian/` directory (control with `Build-Depends` from the recipe,
`rules` driving the component's meson/cmake, changelog, copyright, and the
recipe's `source_format`), and built with `dpkg-source -b` under a fixed
`SOURCE_DATE_EPOCH`.

The result is exactly one `.dsc` plus every file it references (orig + Debian
tarballs) — the shape `import_local_deb` validates — and `dpkg-source -x` of
the set succeeds offline. Fixed `SOURCE_DATE_EPOCH`, fixed tar
mtimes/order and sorted generation make two runs over identical trees produce
byte-identical output.
"""

import email.utils
import gzip
import io
import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass

from package_mgmt.lib.model.packaging import ComponentRecipe
from package_mgmt.lib.service.packaging_util import (
    changelog_date,
    resolved_maintainer,
)

_RULES_MESON = """#!/usr/bin/make -f
PREFIX = /usr
DESTDIR ?= $(CURDIR)/debian/tmp

.PHONY: build build-arch build-indep binary binary-arch binary-indep clean

build build-arch build-indep:
\tmeson setup build . --prefix=$(PREFIX)
\tninja -C build
\tDESTDIR=$(DESTDIR) ninja -C build install

binary binary-arch binary-indep: build

clean:
\trm -rf build
"""

_RULES_CMAKE = """#!/usr/bin/make -f
PREFIX = /usr
DESTDIR ?= $(CURDIR)/debian/tmp

.PHONY: build build-arch build-indep binary binary-arch binary-indep clean

build build-arch build-indep:
\tcmake -S . -B build -DCMAKE_INSTALL_PREFIX=$(PREFIX) -DCMAKE_BUILD_TYPE=Release
\tcmake --build build
\tcmake --install build --prefix $(PREFIX) --destdir $(DESTDIR)

binary binary-arch binary-indep: build

clean:
\trm -rf build
"""

_CONTROL = """Source: {source}
Section: {section}
Priority: optional
Maintainer: {maintainer}
Standards-Version: 4.6.2
Build-Depends: {build_depends}

Package: {source}
Architecture: {arch}
Description: {description}
"""

_CHANGELOG = """{source} ({version}) unstable; urgency=low

  * Packaged from the source stack.

 -- {maintainer}  {date}
"""

_COPYRIGHT = """Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: {source}
{source_line}
Files: *
Copyright: {year} {maintainer}
License: other
 Comment: The license terms are recorded in the upstream source tree.
"""

_BUILD_DEPENDS = {"meson": "meson, ninja-build", "cmake": "cmake, ninja-build"}


class SrcGenError(Exception):
    """A source-package generation failure (exit 1, zero changes)."""


@dataclass(frozen=True)
class SourceBuildResult:
    """The generated source package set.

    `source_dir` holds exactly one `.dsc` plus every referenced file;
    `file_count` is the number of payload files the `.dsc` lists.
    """

    source_dir: str
    source_name: str
    version: str
    file_count: int


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name == ".git":
            ignored.add(name)
            continue
        full = os.path.join(directory, name)
        if name == "build" and os.path.isdir(full):
            ignored.add(name)
    return ignored


def _copy_tree(source_tree: str, tree_dst: str) -> None:
    try:
        shutil.copytree(source_tree, tree_dst, symlinks=True, ignore=_copy_ignore)
    except OSError as e:
        raise SrcGenError(f"cannot copy source tree {source_tree}: {e}") from e


def _write_debian_wrapper(
    tree_dst: str,
    component: ComponentRecipe,
    version: str,
    source_epoch: int,
    maintainer: str,
) -> None:
    source = component.binary_package
    debian_dir = os.path.join(tree_dst, "debian")
    os.makedirs(os.path.join(debian_dir, "source"), exist_ok=True)

    build_depends = ", ".join(component.build_depends)
    if not build_depends:
        build_depends = _BUILD_DEPENDS[component.build_system]
    control = _CONTROL.format(
        source=source,
        section=component.section,
        maintainer=maintainer,
        build_depends=build_depends,
        arch=component.architecture,
        description=component.description,
    )
    with open(os.path.join(debian_dir, "control"), "w", encoding="utf-8") as f:
        f.write(control)

    rules = _RULES_MESON if component.build_system == "meson" else _RULES_CMAKE
    rules_path = os.path.join(debian_dir, "rules")
    with open(rules_path, "w", encoding="utf-8") as f:
        f.write(rules)
    os.chmod(rules_path, 0o755)

    date = changelog_date(source_epoch)
    changelog = _CHANGELOG.format(
        source=source, version=version, maintainer=maintainer, date=date
    )
    with open(os.path.join(debian_dir, "changelog"), "w", encoding="utf-8") as f:
        f.write(changelog)

    source_line = f"Source: {component.homepage}\n" if component.homepage else ""
    copyright = _COPYRIGHT.format(
        source=source,
        source_line=source_line,
        year=email.utils.parsedate_to_datetime(date).year,
        maintainer=maintainer,
    )
    with open(os.path.join(debian_dir, "copyright"), "w", encoding="utf-8") as f:
        f.write(copyright)

    with open(os.path.join(debian_dir, "source", "format"), "w", encoding="utf-8") as f:
        f.write(component.source_format + "\n")


def _tar_add(
    tar: tarfile.TarFile,
    path: str,
    arc: str,
    epoch: int,
) -> None:
    """Add one entry (file, dir, or symlink) with fixed metadata and order."""
    info = tarfile.TarInfo(arc)
    info.mtime = epoch
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    if os.path.islink(path):
        info.type = tarfile.SYMTYPE
        info.linkname = os.readlink(path)
        info.mode = 0o777
        info.size = 0
        tar.addfile(info)
    elif os.path.isdir(path):
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        info.size = 0
        tar.addfile(info)
        for name in sorted(os.listdir(path)):
            _tar_add(tar, os.path.join(path, name), f"{arc}/{name}", epoch)
    else:
        with open(path, "rb") as f:
            data = f.read()
        info.type = tarfile.REGTYPE
        info.mode = 0o644
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


def _write_orig_tarball(
    tree_dst: str, out_path: str, source: str, upstream: str, epoch: int
) -> None:
    """Deterministic upstream tarball (top dir `<source>-<upstream>`)."""
    top = f"{source}-{upstream}"
    raw_buf = io.BytesIO()
    with tarfile.open(fileobj=raw_buf, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for name in sorted(os.listdir(tree_dst)):
            if name == "debian":
                continue
            _tar_add(tar, os.path.join(tree_dst, name), f"{top}/{name}", epoch)
    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb", mtime=0) as gz:
        gz.write(raw_buf.getvalue())
    with open(out_path, "wb") as f:
        f.write(gz_buf.getvalue())


def _dsc_file_count(source_dir: str) -> int:
    dsc_names = [
        name
        for name in os.listdir(source_dir)
        if name.endswith(".dsc") and os.path.isfile(os.path.join(source_dir, name))
    ]
    if len(dsc_names) != 1:
        raise SrcGenError(
            f"source package generation produced {len(dsc_names)} .dsc files "
            f"in {source_dir}"
        )
    from debian import deb822

    with open(os.path.join(source_dir, dsc_names[0]), "r", encoding="utf-8", errors="replace") as f:
        dsc = deb822.Dsc(f.read())
    # The reported count keeps the original `_dsc_file_count` semantics
    # exactly: the length of the `Checksums-Sha256` file list (falling back to
    # `Files`), not the unique-name union across both blocks.
    return len((dsc.get("Checksums-Sha256") or dsc.get("Files")) or [])


def build_source_package(
    component: ComponentRecipe,
    repo_dir: str,
    work_dir: str,
    source_epoch: int,
    default_maintainer: str | None = None,
) -> SourceBuildResult:
    """Build a deterministic source package from the pinned source tree.

    `default_maintainer` supplies the `Maintainer` when the recipe omits one.
    Returns the source directory (exactly one `.dsc` + referenced files).
    Raises `SrcGenError` on any failure.
    """
    source = component.binary_package
    version = f"{component.version}-{component.revision}"
    maintainer = resolved_maintainer(component, default_maintainer)
    source_tree = os.path.abspath(os.path.join(repo_dir, component.source_tree))
    if not os.path.isdir(source_tree):
        raise SrcGenError(
            f"component {component.name}: source tree not found at {source_tree}"
        )

    tree_dst = os.path.join(work_dir, source)
    _copy_tree(source_tree, tree_dst)
    _write_debian_wrapper(tree_dst, component, version, source_epoch, maintainer)

    orig_path = os.path.join(work_dir, f"{source}_{component.version}.orig.tar.gz")
    _write_orig_tarball(tree_dst, orig_path, source, component.version, source_epoch)

    env = dict(os.environ, SOURCE_DATE_EPOCH=str(source_epoch))
    cmd = ["dpkg-source", "-b", tree_dst]
    try:
        result = subprocess.run(
            cmd, cwd=work_dir, capture_output=True, text=True, env=env, check=False
        )
    except OSError as e:
        raise SrcGenError(f"cannot run dpkg-source: {e}") from e
    if result.returncode != 0:
        raise SrcGenError(
            f"component {component.name}: dpkg-source failed: {result.stderr.strip()}"
        )

    file_count = _dsc_file_count(work_dir)
    return SourceBuildResult(
        source_dir=work_dir,
        source_name=source,
        version=version,
        file_count=file_count,
    )
