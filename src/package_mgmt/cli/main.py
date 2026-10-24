import argparse
import logging
import os
import sys

from package_mgmt.cli.commands.add import handle_add
from package_mgmt.cli.commands.align_repo import handle_align_repo
from package_mgmt.cli.commands.config import handle_config
from package_mgmt.cli.commands.download import handle_download
from package_mgmt.cli.commands.ensure_base import handle_ensure_base
from package_mgmt.cli.commands.handoff import handle_handoff
from package_mgmt.cli.commands.import_deb import handle_import_deb
from package_mgmt.cli.commands.init import handle_init
from package_mgmt.cli.commands.package_stack import handle_package_stack
from package_mgmt.cli.commands.list import handle_list_deps
from package_mgmt.cli.commands.show import handle_show
from package_mgmt.cli.commands.snapshot import handle_snapshot
from package_mgmt.cli.commands.sync import handle_sync
from package_mgmt.cli.commands.systems import (
    handle_systems_add,
    handle_systems_list,
    handle_systems_remove,
    handle_systems_rename,
)
from package_mgmt.cli.commands.verify import handle_verify_graph
from package_mgmt.lib.util.errors import ConfigError, print_error, EXIT_ERROR


_config_error: str | None = None


def _require_config(config):
    if config is None:
        from package_mgmt.lib.util.errors import print_error, EXIT_CONFIG_ERROR
        message = _config_error or "Configuration error: unable to load project config"
        print_error(message, EXIT_CONFIG_ERROR)
        sys.exit(EXIT_CONFIG_ERROR)
    return config


def _setup_logging(config) -> None:
    level = getattr(config, "log_level", "INFO")
    log_dir = getattr(config, "log_dir", None)
    handlers = [logging.StreamHandler()]
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            handlers.append(logging.FileHandler(os.path.join(log_dir, "package_mgmt.log")))
        except OSError:
            logging.getLogger(__name__).warning(
                "log_dir %s is not writable; falling back to stderr logging", log_dir
            )
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(levelname)s:%(name)s:%(message)s",
        handlers=handlers,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="package_mgmt",
        description="OpenFromSource package management CLI",
    )
    subparsers = parser.add_subparsers(dest="command")
    _register_add(subparsers)
    _register_ensure_base(subparsers)
    _register_import_deb(subparsers)
    _register_init(subparsers)
    _register_download(subparsers)
    _register_config(subparsers)
    _register_list(subparsers)
    _register_show(subparsers)
    _register_verify(subparsers)
    _register_systems(subparsers)
    _register_snapshot(subparsers)
    _register_handoff(subparsers)
    _register_sync(subparsers)
    _register_align_repo(subparsers)
    _register_package_stack(subparsers)
    return parser


def _register_add(subparsers) -> None:
    add_parser = subparsers.add_parser("add", help="Resolve and register a package and its dependencies")
    add_parser.add_argument("package", nargs="*", help="Package name(s) with optional =version pin (mutually exclusive with --file)")
    add_parser.add_argument("--file", action="append", dest="files", metavar="PATH",
                            help="Package-list file, one package per line with optional =version pin (repeatable; mutually exclusive with positional names)")
    add_parser.add_argument("--source", type=str, help="Override default resolution source (release name)")


def _register_ensure_base(subparsers) -> None:
    subparsers.add_parser("ensure-base", help="Register offline DVD installer image metadata in the cache")


def _register_import_deb(subparsers) -> None:
    import_parser = subparsers.add_parser(
        "import-deb", help="Import a locally built .deb into the local component"
    )
    import_parser.add_argument("deb", type=str, help="Path to the .deb file")
    import_parser.add_argument(
        "--source", type=str, metavar="PATH",
        help="Directory holding the complete rebuilt source set (.dsc + referenced files)",
    )


def _register_download(subparsers) -> None:
    subparsers.add_parser("download", help="Download and verify pending files into the cache pool")


def _register_init(subparsers) -> None:
    subparsers.add_parser("init", help="Initialize cache directories and database")


def _register_config(subparsers) -> None:
    config_parser = subparsers.add_parser("config", help="Display configuration")
    config_parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")


def _register_list(subparsers) -> None:
    list_parser = subparsers.add_parser("list", help="Display resolved dependency graph")
    list_parser.add_argument("--deps", type=str, help="Package name to inspect")
    list_parser.add_argument("--status", choices=["current", "superseded", "all"], help="Filter binaries by status")
    list_parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    list_parser.add_argument("--full", action="store_true", help="Show full tree including repeated subtrees")
    list_parser.add_argument("--include-build-deps", action="store_true", help="Show build-tool dependencies in the tree")


def _register_show(subparsers) -> None:
    show_parser = subparsers.add_parser("show", help="Display database records for a package")
    show_parser.add_argument("package", type=str, help="Package name to look up")
    show_parser.add_argument("--tables", type=str, help="Comma-separated table names to show")
    show_parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    show_parser.add_argument("--deps", action="store_true", help="Include dependency tree in output")


def _register_verify(subparsers) -> None:
    verify_parser = subparsers.add_parser("verify", help="Verify dependency graph completeness")
    verify_parser.add_argument("--package", type=str, help="Restrict to a single package's tree")


def _register_systems(subparsers) -> None:
    systems_parser = subparsers.add_parser("systems", help="Manage sync system registry")
    systems_sub = systems_parser.add_subparsers(dest="systems_command", required=True)

    add_parser = systems_sub.add_parser("add", help="Add a sync system")
    add_parser.add_argument("name", type=str, help="System moniker")
    add_parser.add_argument("--note", type=str, help="Optional note")

    systems_sub.add_parser("list", help="List sync systems")

    rename_parser = systems_sub.add_parser("rename", help="Rename a sync system")
    rename_parser.add_argument("name", type=str, help="Current moniker")
    rename_parser.add_argument("new_name", type=str, help="New moniker")

    remove_parser = systems_sub.add_parser("remove", help="Remove a sync system")
    remove_parser.add_argument("name", type=str, help="Moniker to remove")


def _register_snapshot(subparsers) -> None:
    snapshot_parser = subparsers.add_parser(
        "snapshot", help="Mark the current, verified cache state as delivered for a system"
    )
    snapshot_parser.add_argument(
        "--system", type=str, help="System moniker to snapshot for"
    )
    snapshot_parser.add_argument(
        "--dry-run", action="store_true", help="Preview the pending count without marking"
    )


def _register_handoff(subparsers) -> None:
    handoff_parser = subparsers.add_parser(
        "handoff", help="Prepare the cache for operator handoff (spec 012)"
    )
    handoff_parser.add_argument(
        "--system", type=str, help="System moniker to snapshot the delivery for (spec 010)"
    )


def _register_sync(subparsers) -> None:
    sync_parser = subparsers.add_parser(
        "sync", help="Prepare a minimal diff cache of new packages for a system (spec 013)"
    )
    sync_parser.add_argument(
        "--system", type=str, help="System moniker to sync the delivery for"
    )


def _register_align_repo(subparsers) -> None:
    align_parser = subparsers.add_parser(
        "align-repo",
        help="Determine or verify the version-aligned source set (admin-only)",
    )
    align_parser.add_argument(
        "--verify", type=str, metavar="MANIFEST",
        help="Re-validate a version manifest against the current cache and repos "
        "(never searches or writes)",
    )
    align_parser.add_argument(
        "--repo-dir", type=str, metavar="DIR", default=None,
        help="Directory holding the acquired source repos (default: repos/)",
    )
    align_parser.add_argument(
        "--format", type=str, default="text",
        help="Output format: text or json",
    )


def _register_package_stack(subparsers) -> None:
    stack_parser = subparsers.add_parser(
        "package-stack",
        help="Package and import a source-built stack into the local component (admin-only)",
    )
    stack_parser.add_argument(
        "--recipe", type=str, default="recipes/hyprland.toml",
        help="Declarative recipe file (default: recipes/hyprland.toml)",
    )
    stack_parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Output format (default: text)",
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    from package_mgmt.lib.service.config_loader import load_config
    global _config_error
    try:
        config, _ = load_config()
    except ConfigError as e:
        # Surface the real reason config loading failed instead of swallowing
        # it behind a generic placeholder (spec 012 code-quality debt). Only
        # the expected ConfigError is caught — real bugs must crash loudly.
        _config_error = f"Configuration error: {e}"
        config = None
    _setup_logging(config)

    if args.command is None:
        parser.print_help()
        return
    elif args.command == "add":
        sys.exit(handle_add(args.package, getattr(args, 'source', None), getattr(args, 'files', None)))
    elif args.command == "ensure-base":
        sys.exit(handle_ensure_base())
    elif args.command == "import-deb":
        config = _require_config(config)
        sys.exit(handle_import_deb(args.deb, getattr(args, 'source', None)))
    elif args.command == "list":
        config = _require_config(config)
        status = getattr(args, 'status', None)
        if status and getattr(args, 'deps', None):
            # The two filters are mutually exclusive: a clean usage error
            # rather than one silently winning over the other.
            print_error("Use either --deps or --status, not both", EXIT_ERROR)
            sys.exit(EXIT_ERROR)
        if status:
            sys.exit(handle_list_deps(config.db_path, args.deps, args.format, args.full, getattr(args, 'include_build_deps', False), status))
        else:
            sys.exit(handle_list_deps(config.db_path, args.deps, args.format, args.full, getattr(args, 'include_build_deps', False)))
    elif args.command == "show":
        config = _require_config(config)
        tables = args.tables.split(",") if args.tables else None
        sys.exit(handle_show(config.db_path, args.package, tables, args.format, getattr(args, 'deps', False)))
    elif args.command == "verify":
        config = _require_config(config)
        sys.exit(handle_verify_graph(
            config.db_path,
            getattr(args, 'package', None),
            config.max_build_dep_report_depth,
            config.max_source_dep_depth,
        ))
    elif args.command == "config":
        sys.exit(handle_config(args.format))
    elif args.command == "init":
        sys.exit(handle_init())
    elif args.command == "download":
        config = _require_config(config)
        sys.exit(handle_download(config.db_path, config))
    elif args.command == "systems":
        config = _require_config(config)
        if args.systems_command == "add":
            sys.exit(handle_systems_add(config.db_path, args.name, getattr(args, "note", None)))
        elif args.systems_command == "list":
            sys.exit(handle_systems_list(config.db_path))
        elif args.systems_command == "rename":
            sys.exit(handle_systems_rename(config.db_path, args.name, args.new_name))
        elif args.systems_command == "remove":
            sys.exit(handle_systems_remove(config.db_path, args.name))
    elif args.command == "snapshot":
        config = _require_config(config)
        sys.exit(handle_snapshot(config.db_path, getattr(args, "system", None), getattr(args, "dry_run", False)))
    elif args.command == "handoff":
        config = _require_config(config)
        sys.exit(handle_handoff(config.db_path, config, getattr(args, "system", None)))
    elif args.command == "sync":
        config = _require_config(config)
        sys.exit(handle_sync(config.db_path, config, getattr(args, "system", None)))
    elif args.command == "align-repo":
        config = _require_config(config)
        sys.exit(handle_align_repo(
            config.db_path,
            config.binary_cache,
            getattr(args, "repo_dir", None) or "repos",
            getattr(args, "verify", None),
            getattr(args, "format", "text"),
            config,
        ))
    elif args.command == "package-stack":
        config = _require_config(config)
        sys.exit(handle_package_stack(
            getattr(args, "recipe", "recipes/hyprland.toml"),
            getattr(args, "format", "text"),
        ))
