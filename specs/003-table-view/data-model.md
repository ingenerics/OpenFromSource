# Data Model: Table View

This feature introduces no new entities or tables. It reads from the existing database schema:

- **packages** — `name`, `requested_version`, `status`, `verified`
- **binaries** — `id`, `package_name`, `version`, `architecture`, `verified`, `dependencies`
- **sources** — `id`, `package_name`, `source_version`, `verified`
- **binary_to_source** — `binary_id`, `source_id`, `verified`

Column lists are discovered dynamically via `PRAGMA table_info()` so the view automatically reflects any future schema changes.
