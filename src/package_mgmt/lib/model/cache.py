from dataclasses import dataclass


@dataclass(frozen=True)
class BinaryCache:
    path: str
    exists: bool


@dataclass(frozen=True)
class SourceCache:
    path: str
    exists: bool
