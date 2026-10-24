# Research: Table View

## Resolved Decisions

No research unknowns exist for this feature. All technology choices are dictated by the existing project infrastructure:

- **Database access**: `sqlite3` (stdlib) — same as all existing commands
- **Output formatting**: Manual text table formatting with column alignment; `json.dumps` for JSON output
- **Table metadata**: `PRAGMA table_info()` to discover column names dynamically from the schema
- **No new dependencies**: The feature uses only stdlib modules already in use

The key design question — whether to hardcode column lists or discover them dynamically — is resolved in favour of dynamic discovery via `PRAGMA table_info()`. This ensures the `show` command stays correct if the schema evolves (new columns added), without requiring code changes.
