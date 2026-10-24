SCHEMA_VERSION = 1

EXPECTED_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS packages (
    name TEXT NOT NULL PRIMARY KEY,
    requested_version TEXT,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS binaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_name TEXT NOT NULL,
    version TEXT NOT NULL,
    architecture TEXT,
    release TEXT NOT NULL,
    component TEXT NOT NULL,
    dependencies TEXT,
    status TEXT NOT NULL DEFAULT 'current',
    verified INTEGER NOT NULL DEFAULT 0,
    circular INTEGER NOT NULL DEFAULT 0,
    sync_mask INTEGER NOT NULL DEFAULT 0,
    UNIQUE(package_name, version, architecture, release, component)
);

CREATE TABLE IF NOT EXISTS systems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    bit INTEGER NOT NULL UNIQUE,
    note TEXT,
    retired INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_systems_active_name
    ON systems(name) WHERE retired = 0;

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_name TEXT NOT NULL,
    source_version TEXT,
    release TEXT NOT NULL,
    component TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    build_depends TEXT,
    UNIQUE(package_name, source_version, release, component)
);

CREATE TABLE IF NOT EXISTS binary_to_source (
    binary_id INTEGER NOT NULL REFERENCES binaries(id),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    UNIQUE(binary_id, source_id)
);

CREATE TABLE IF NOT EXISTS source_build_deps (
    source_id INTEGER NOT NULL REFERENCES sources(id),
    binary_id INTEGER NOT NULL REFERENCES binaries(id),
    build_dep_depth INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source_id, binary_id)
);

CREATE TABLE IF NOT EXISTS provides (
    package_name TEXT NOT NULL,
    version TEXT NOT NULL,
    architecture TEXT,
    release TEXT NOT NULL,
    component TEXT NOT NULL,
    virtual_name TEXT NOT NULL,
    provides_version TEXT,
    PRIMARY KEY (package_name, version, architecture, release, component, virtual_name, provides_version)
);

CREATE INDEX IF NOT EXISTS idx_provides_virtual_name ON provides(virtual_name);

CREATE UNIQUE INDEX IF NOT EXISTS uq_binary_to_source
    ON binary_to_source(binary_id, source_id);

CREATE TABLE IF NOT EXISTS virtual_resolutions (
    virtual_name TEXT PRIMARY KEY,
    resolved_binary_id INTEGER NOT NULL REFERENCES binaries(id)
);
"""
