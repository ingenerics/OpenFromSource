# Research: Virtual Package Resolution

## 1. Provides Table: FK vs Natural Key

**Approach A (FK to binaries.id)**:
- Pros: Referential integrity, compact integer key
- Cons: Requires a `binaries` row to exist for every provider → forces creation of placeholder binaries records during init, pollutes the binaries table with entries that were never resolved

**Approach B (Natural key, no FK)**:
- Pros: Self-contained index metadata, no dependency on binaries table, no placeholder records, clean separation (provides = index snapshot, binaries = resolution state)
- Cons: Wider rows, joins require compound key

**Decision**: Approach B. The provides table is index metadata, not resolution state. It is populated during init from package index files and queried during resolution. The wider rows are negligible given the index on `virtual_name`. The natural key `(package_name, version, architecture, release, component, virtual_name)` extends the binary identity with virtual_name. The first five columns match the binaries table's UNIQUE constraint, allowing the resolver to query provides for candidates and then use the key to look up or create the corresponding binaries row.

## 2. VirtualResolution: Persistent Cache

**Decision**: The `virtual_resolutions` table is populated lazily during `add` and persists across invocations. It uses a FK to `binaries(id)` because the referenced binaries row must exist — it is created at resolution time when the provider is selected. On subsequent `add` commands, the resolver checks `virtual_resolutions` first, avoiding re-querying the provides table and ensuring deterministic re-resolution.

## 3. Provides Field Parsing

The `Provides` field in Packages.gz uses the same comma-separated format as `Depends`, with optional version constraints in parentheses (e.g., `Provides: awk, awk-api-2.0 (= 2.0)`). The existing `DependencyGraph.parse_depends_line()` can be reused for parsing, extracting `(virtual_name, version)` tuples.

## 4. Deterministic Selection Algorithm

When multiple packages provide the same virtual name, the selection follows:
1. Filter candidates by version constraint (if the dependency specifies one)
2. Sort alphabetically by package name (ascending)
3. Prefer packages already in the dependency tree (in `self._visited`)
4. Select the first candidate

This ensures deterministic behavior: same index + same constraints → same provider.

## 5. Relationship to Multi-Release Support (Spec 005)

This spec builds on the multi-release foundation. The `provides` table includes `release` and `component` columns, matching the provenance model from spec 005. During resolution, the primary source (from the `--source` flag or default) is preferred when selecting among multiple providers for the same virtual name.
