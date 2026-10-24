import json
import sys


EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_PERMISSION = 100
EXIT_SCHEMA_MISMATCH = 101
EXIT_CONFIG_ERROR = 102


class PackageMgmtError(Exception):
    pass


class ConfigError(PackageMgmtError):
    pass


class InitError(PackageMgmtError):
    """Initialization failure.

    `permission` distinguishes an actual permission failure (exit 100) from
    any other initialization error (generic failure, exit 1).
    """

    def __init__(self, message: str, permission: bool = False):
        super().__init__(message)
        self.permission = permission


class DatabaseError(PackageMgmtError):
    pass


class IsoError(PackageMgmtError):
    pass


class CacheNotEmptyError(PackageMgmtError):
    pass


def print_error(message: str, exit_code: int = EXIT_ERROR) -> None:
    payload = {"error": message, "exit_code": exit_code}
    print(json.dumps(payload), file=sys.stderr)
