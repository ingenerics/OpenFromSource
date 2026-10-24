"""`package-stack` command: package and import a source-built stack (admin-only).

The generic packaging command consumes a declarative recipe and packages +
imports every component it describes into the cache's `local` component in
dependency order: each staged directory becomes a deterministic `.deb` with
auto-derived `Depends` and a complete rebuildable `.dsc` source set, both
imported, linked via `binary_to_source`, and verified. Exit codes follow the
shared convention: 0 success, 1 packaging/import failure, 101 stale/missing
database, 102 config/argument/recipe errors.
"""

import shutil
import sqlite3
import tempfile

from package_mgmt.lib.service.config_loader import load_config
from package_mgmt.lib.service.db_init import validate_schema_or_error
from package_mgmt.lib.service.debgen import DebGenError
from package_mgmt.lib.service.import_local import ImportDebError
from package_mgmt.lib.service.package_stack import (
    PackagingError,
    RecipeLoadError,
    RecipeValidationError,
    format_output,
    run_package_stack,
)
from package_mgmt.lib.service.srcgen import SrcGenError
from package_mgmt.lib.util.errors import (
    print_error,
    EXIT_SUCCESS,
    EXIT_ERROR,
    EXIT_CONFIG_ERROR,
    ConfigError,
)

_PACKAGING_ERRORS = (
    RecipeValidationError,
    PackagingError,
    DebGenError,
    SrcGenError,
    ImportDebError,
    sqlite3.Error,
)


def handle_package_stack(
    recipe_path: str = "recipes/hyprland.toml", fmt: str = "text"
) -> int:
    """Run the stack packaging pipeline over `recipe_path`."""
    if fmt not in ("text", "json"):
        print_error(
            f"invalid --format {fmt!r}; expected text or json", EXIT_CONFIG_ERROR
        )
        return EXIT_CONFIG_ERROR

    try:
        config, _ = load_config()
    except ConfigError as e:
        print_error(str(e), EXIT_CONFIG_ERROR)
        return EXIT_CONFIG_ERROR

    schema_error = validate_schema_or_error(config.db_path)
    if schema_error is not None:
        return schema_error

    work_dir = tempfile.mkdtemp(prefix="package-stack-")
    try:
        result = run_package_stack(config, recipe_path, work_dir=work_dir)
    except RecipeLoadError as e:
        print_error(str(e), EXIT_CONFIG_ERROR)
        return EXIT_CONFIG_ERROR
    except _PACKAGING_ERRORS as e:
        print_error(str(e), EXIT_ERROR)
        return EXIT_ERROR
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    print(format_output(result, fmt))
    return EXIT_SUCCESS
