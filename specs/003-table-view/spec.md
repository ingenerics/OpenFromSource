# Feature Specification: Table View

**Feature Branch**: `003-table-view`
**Status**: Draft

**Input**: User description: "CLI command to view database contents in a formatted table view, with per-table filtering and optional JSON output."

## User Scenarios & Testing *(mandatory)*

### Project Roles

| Persona | Environment | Role |
|---------|-------------|------|
| **Administrator** | Networked system (has internet / Debian archive access) | Inspects cache contents during build-up |
| **Operator** | Offline system (no network) | Inspects received cache to verify its contents |

**Phase 1 — Basic Table View**: Both personas can inspect the database by specifying a package name. The command displays matching rows from all relevant tables (packages, binaries, sources, binary_to_source) as formatted tables in console output. Flag-based filtering restricts which tables are shown. JSON output provides programmatic access.

**Phase 2 — Dependency Integration**: The table view optionally includes dependency information similar to `list --deps`, showing the resolved dependency tree alongside the table data.

---

### User Story 1 — Package Table View (Priority: P1)

An administrator or operator wants to see everything the system knows about a particular package — its package record, binary entries, linked sources, and binary-to-source mappings — all in one command.

**Why this priority**: Without a dedicated view command, inspecting the database requires manual SQL queries. A formatted table view provides immediate insight into the cache state without needing to know the schema or use `sqlite3` directly.

**Independent Test**: After resolving a package (e.g. `package_mgmt add vifm`), run `package_mgmt show vifm`. The output shows four tables: packages, binaries, sources, and binary_to_source, each with the matching rows for vifm. Re-run with `--tables binaries,sources` — only those two tables are shown. Re-run with `--format json` — output is valid JSON with the same data grouped by table.

**Acceptance Scenarios**:

1. **Given** a populated database with records for `vifm`,
   **When** the user runs `package_mgmt show vifm`,
   **Then** the output contains four formatted tables (packages, binaries, sources, binary_to_source), each showing the rows where the package name matches.

2. **Given** the user wants to view only specific tables,
   **When** the user runs `package_mgmt show vifm --tables packages,binaries`,
   **Then** only the `packages` and `binaries` tables are shown, with headers and formatting.

3. **Given** the user wants machine-readable output,
   **When** the user runs `package_mgmt show vifm --format json`,
   **Then** the output is valid JSON with keys for each table and arrays of row objects.

4. **Given** a package name that does not exist in any table,
   **When** the user runs `package_mgmt show nonexistent-pkg`,
   **Then** the output shows each table with zero rows (empty tables), not an error.

5. **Given** a populated database,
   **When** the user runs `package_mgmt show vifm` and there are multiple binary rows for different architectures,
   **Then** all binary rows are shown in the binaries table, not just the first match.

---

### User Story 2 — Dependency Integration (Priority: P2)

The table view optionally includes the resolved dependency tree alongside the table data, providing a single command that shows both raw records and dependency relationships.

**Why this priority**: Combining table data with dependency information gives a complete picture of a package's state in the cache without running two separate commands.

**Independent Test**: Run `package_mgmt show vifm --deps`. The output includes the standard table view followed by a dependency tree section identical to `list --deps vifm`.

**Acceptance Scenarios**:

1. **Given** resolved dependency records for `vifm`,
   **When** the user runs `package_mgmt show vifm --deps`,
   **Then** the output includes the table view for all tables, followed by the dependency tree for vifm, matching `list --deps vifm` output.

2. **Given** the dependency tree is large,
   **When** the user adds `--format json` to the `--deps` flag,
   **Then** the JSON output includes both `tables` and `dependency_tree` top-level keys.

---

### Edge Cases

- What happens when a package name matches zero rows in every table? — All tables are shown with zero rows (empty tables), not an error.
- What if the `--tables` flag specifies a table name that doesn't exist? — The command reports the invalid table name and lists the valid options.
- How does the command handle very wide tables (many columns) in console mode? — Columns are truncated to fit the terminal width; JSON output always includes all columns.
- What if the database is not initialized or unreachable? — The command reports a clear error and exits with a non-zero code.
- How are NULL values displayed in console table mode? — NULL values are shown as `NULL` in their cell.
- How does JSON handle NULL values? — JSON uses `null` for NULL database values.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a `show` command that accepts a package name and displays matching rows from all tables (packages, binaries, sources, binary_to_source) as formatted tables.
- **FR-002**: System MUST support a `--tables` flag that accepts a comma-separated list of table names to restrict which tables are displayed. Valid values: `packages`, `binaries`, `sources`, `binary_to_source`.
- **FR-003**: System MUST support a `--format` flag with values `text` (default, formatted console tables) and `json` (machine-readable JSON).
- **FR-004**: System MUST search each table by the package-name column (`packages.name`, `binaries.package_name`, `sources.package_name`, joining through `binary_to_source` for that table).
- **FR-005**: For `--format text`, each table MUST be displayed with a header row, aligned columns, and a separator between header and data rows.
- **FR-006**: For `--format json`, output MUST be a single JSON object with table names as keys and arrays of row objects as values.
- **FR-007**: System MUST return exit code 0 even when no rows match (empty tables), and a non-zero exit code only on actual errors.
- **FR-008**: System MUST report an error and list valid options when `--tables` contains an unrecognised table name.
- **FR-009**: System MUST display NULL values as the literal string `NULL` in text mode and as JSON `null` in JSON mode.
- **FR-010**: System MUST support a `--deps` flag (Phase 2) that appends the dependency tree output alongside the table view, matching `list --deps` format.
- **FR-011**: When `--deps` is combined with `--format json`, the JSON output MUST include both a `tables` object and a `dependency_tree` object.

### Key Entities *(include if feature involves data)*

- **Package (DB `packages` table)**: Base record with name, requested_version, status, verified.
- **Binary (DB `binaries` table)**: Binary build record with package_name, version, architecture, dependencies, verified.
- **Source (DB `sources` table)**: Source package record with package_name, source_version, verified.
- **BinaryToSource (DB `binary_to_source` table)**: Link table with binary_id, source_id, verified.
- **TableView**: Rendered output — either console-formatted tables or JSON objects.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A package with rows in all four tables displays all four tables correctly formatted in under 500ms on a standard Debian Trixie workstation.
- **SC-002**: The `--tables` flag correctly filters output — specifying one table shows exactly that table, with zero output from other tables.
- **SC-003**: JSON output validates against a defined schema (keys = table names, values = arrays of row dicts) using `python3 -c "import json; json.loads(output)"`.
- **SC-004**: A non-existent package shows all four tables with zero rows and exits with code 0 — no error, no crash.
- **SC-005**: An invalid table name in `--tables` produces a specific error message listing valid table names and exits with a non-zero code.
- **SC-006**: Displaying a database with at least 100 binary rows for a single package completes in under 2 seconds.

## Assumptions

- **Command Name**: The show command is named `package_mgmt show <package>`. If this conflicts with existing commands, rename accordingly.
- **Default Table Set**: By default, all four tables (packages, binaries, sources, binary_to_source) are shown. The `--tables` flag filters this set.
- **Simple Column Display**: All columns of each table are shown. No column-selection or column-hiding flag is provided in this initial version.
- **Terminal Width**: Console output assumes a minimum terminal width of 80 columns. Wider tables are truncated with column headers shortened to fit.
- **No Ordering Guarantee**: Row order within each table is not guaranteed to be deterministic unless database ordering is specified.
- **Phase 2 Dependency Reference**: The `--deps` flag output format matches the existing `list --deps` output. Implementation may reuse the same underlying code.
