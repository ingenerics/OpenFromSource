# Research: Base Project Infrastructure

## Resolved Decisions

### Config Format: TOML

- **Decision**: TOML via Python stdlib `tomllib` (3.11+)
- **Rationale**: Zero extra dependencies, widely adopted (pyproject.toml),
  supports nested sections
- **Alternatives considered**: YAML (extra dep), INI (no nesting)

### Override Mechanism: Local Override File

- **Decision**: `project.local.toml` at project root; keys shadow defaults
- **Rationale**: Simple, explicit, version-control friendly (git-ignored)
- **Alternatives considered**: Environment variables (12-factor but adds
  complexity), both (over-engineered for v1)

### Cache Directory Permissions

- **Decision**: `755` (rwxr-xr-x) with user ownership; group writable if
  `PACKAGE_MGMT_GROUP` env var is set at creation time
- **Rationale**: Default works for single-user; group mechanism supports
  shared admin/operator workflows on the same machine
- **Alternatives considered**: ACL-based (over-engineered for v1), 777 (insecure)

### Database Schema Versioning

- **Decision**: Integer schema version stored in SQLite `PRAGMA user_version`;
  init validates against expected version and errors on mismatch
- **Rationale**: Lightweight, no migration framework needed; manual migration
  scripts can be added later when schema evolves
- **Alternatives considered**: Alembic (too heavy for v1), file-based versioning
  (fragile)

### Cache Structure: Debian Apt Layout with Binary/Source Separation

- **Decision**: Two separate cache directories (`binary/` and `source/`) each
  following the Debian apt repository layout (`pool/` + `dists/` with Release
  files). Binary cache stores `.deb` files; source cache stores `.dsc`,
  `.orig.tar.*`, `.debian.tar.*` files.
- **Rationale**: Mirrors Debian's own structure so existing apt tooling can
  consume it directly. Separation enables different handoff strategies —
  operators need only the binary cache for daily use; the source cache is
  larger and only required when rebuilds are needed.
- **Alternatives considered**: Single unified pool (harder to split handoff),
  custom layout (breaks apt compatibility)

### CLI Entry Point: console_scripts

- **Decision**: `package_mgmt` command registered via `console_scripts` in
  `pyproject.toml` pointing to `package_mgmt.cli:main`
- **Rationale**: Clean user-facing command name; `python -m package_mgmt` also
  works as fallback via `__main__.py`
- **Alternatives considered**: Shell wrapper script (fragile), `python -m
  src.cli.main` (leaks internals)

### Config File Keys (Default)

```toml
[paths]
base_dir = "/var/cache/package_mgmt"
binary_cache = "{base_dir}/binary"
source_cache = "{base_dir}/source"

[database]
path = "{base_dir}/package_mgmt.db"

[logging]
level = "INFO"
directory = "{base_dir}/logs"
```

- **Rationale**: Covers separate binary and source cache paths; `{...}`
  interpolation for consistency
