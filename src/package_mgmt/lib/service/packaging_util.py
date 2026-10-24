"""Shared packaging helpers for the deb/source generators and the importer.

`debgen`, `srcgen` and `import_local` each computed the RFC 2822 changelog
date, the fallback maintainer, and the `.dsc` file list independently. Those
rules are consolidated here so the three generation paths cannot diverge: a
changelog trailer line a parser would reject, a missing maintainer, or a
mismatched `.dsc` file list is handled identically everywhere.
"""

import email.utils
from datetime import datetime, timezone

from package_mgmt.lib.model.packaging import ComponentRecipe

FALLBACK_MAINTAINER = "OpenFromSource <noreply@example.com>"


def changelog_date(source_epoch: int) -> str:
    """RFC 2822 date with a numeric offset, as `dpkg-parsechangelog` expects
    (a `GMT` suffix is rejected as a badly formatted trailer line)."""
    return email.utils.format_datetime(
        datetime.fromtimestamp(source_epoch, tz=timezone.utc)
    )


def resolved_maintainer(component: ComponentRecipe, default_maintainer: str | None) -> str:
    return component.maintainer or default_maintainer or FALLBACK_MAINTAINER


def read_dsc_files(dsc) -> dict[str, dict]:
    """{name: {"sha256": ..., "size": int}} from a `.dsc`'s `Files:` and
    `Checksums-Sha256:` paragraphs (the file list and expected hashes)."""
    files: dict[str, dict] = {}
    for entry in dsc.get("Files") or []:
        name = entry.get("name")
        if name:
            files.setdefault(name, {})["size"] = int(entry.get("size") or 0)
    for entry in dsc.get("Checksums-Sha256") or []:
        name = entry.get("name")
        if name:
            files.setdefault(name, {})["sha256"] = entry.get("sha256")
            files[name]["size"] = int(entry.get("size") or 0)
    return files
