"""Read-only view of a Rock Ridge installer image (ISO9660 + Rock Ridge).

Backed by the Debian ``python3-pycdlib`` package (API class ``PyCdlib``).
All errors are normalized to :class:`IsoError` — including pycdlib's
``IndexError`` from a binary-search bounds bug in 1.12.0+ds1 that fires
instead of the proper "path not found" exception.
"""

import io
import logging

from pycdlib import PyCdlib

from package_mgmt.lib.util.errors import IsoError

logger = logging.getLogger(__name__)


def _rr_name(record) -> str:
    """Rock Ridge name of a directory record, falling back to the ISO9660 name."""
    rr = record.rock_ridge
    if rr is not None and rr.name:
        name = rr.name()
        if name:
            return name.decode("utf-8", "replace")
    return record.file_identifier().decode("utf-8", "replace").rstrip(";")


class IsoImage:
    """A raw offline DVD installer image, read directly (no mounting)."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._iso = PyCdlib()
        self._rr = None
        self._opened = False
        self._closed = False
        try:
            self._iso.open(path)
            self._opened = True
            if not self._iso.has_rock_ridge():
                raise IsoError(f"Not a Rock Ridge image: {path}")
            self._rr = self._iso.get_rock_ridge_facade()
            self._validate_layout()
            self.suite, self.release, self.components = self._discover()
        except IsoError:
            self.close()
            raise
        except Exception as e:
            self.close()
            raise IsoError(f"Failed to read ISO image {path}: {e}") from e

    @property
    def path(self) -> str:
        return self._path

    def _validate_layout(self) -> None:
        if not self._is_dir("/dists"):
            raise IsoError(
                f"No dists/ directory in {self._path} — not a Debian installer image"
            )
        if not self._is_dir("/pool"):
            raise IsoError(
                f"No pool/ directory in {self._path} — not a Debian installer image"
            )

    def _is_dir(self, rr_path: str) -> bool:
        try:
            list(self._rr.list_children(rr_path=rr_path))
            return True
        except Exception:
            return False

    @staticmethod
    def _is_symlink(record) -> bool:
        rr = record.rock_ridge
        return rr is not None and bool(rr.is_symlink())

    def _discover(self) -> tuple[str, str, tuple[str, ...]]:
        suites: list[tuple[str, tuple[str, ...]]] = []
        for entry in self._rr.list_children(rr_path="/dists"):
            name = _rr_name(entry)
            if name in (".", "..") or self._is_symlink(entry):
                continue
            suite_path = f"/dists/{name}"
            if not self._is_dir(suite_path):
                continue
            components: list[str] = []
            for child in self._rr.list_children(rr_path=suite_path):
                comp = _rr_name(child)
                if comp in (".", "..") or self._is_symlink(child):
                    continue
                if self._is_dir(f"{suite_path}/{comp}/binary-amd64"):
                    components.append(comp)
            if components:
                suites.append((name, tuple(sorted(components))))

        if not suites:
            raise IsoError(
                f"No binary-amd64 package indexes in {self._path} — not an installer image"
            )

        suite, components = suites[0]
        release = self._release_codename(f"/dists/{suite}/Release") or suite
        logger.debug(
            "ISO %s: suite=%s release=%s components=%s",
            self._path, suite, release, components,
        )
        return suite, release, components

    def _release_codename(self, rr_path: str) -> str | None:
        try:
            data = self.read_file(rr_path).decode("utf-8", "replace")
        except IsoError:
            return None
        for line in data.splitlines():
            if line.startswith("Codename:"):
                return line.split(":", 1)[1].strip()
        return None

    def read_file(self, rr_path: str) -> bytes:
        buf = io.BytesIO()
        try:
            self._rr.get_file_from_iso_fp(buf, rr_path)
        except Exception as e:
            raise IsoError(
                f"Failed to read {rr_path} from {self._path}: {e}"
            ) from e
        return buf.getvalue()

    def file_paths(self) -> set[str]:
        """All file paths in the image (Rock Ridge names), one pass."""
        paths: set[str] = set()
        try:
            for path, _dirs, files in self._rr.walk(rr_path="/"):
                base = path.rstrip("/")
                for name in files:
                    paths.add(f"{base}/{name}")
        except Exception as e:
            raise IsoError(f"Failed to walk {self._path}: {e}") from e
        return paths

    def close(self) -> None:
        if self._opened and not self._closed:
            self._iso.close()
            self._closed = True

    def __enter__(self) -> "IsoImage":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
