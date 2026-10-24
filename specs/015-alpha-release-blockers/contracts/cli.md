# CLI Contracts: Alpha Release Blockers

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md)

Extends (never contradicts) the existing contracts in `specs/014-base-profiles/contracts/cli.md`. Two phases
touch CLI-observable behavior: **Phase 3** (sync delivery) and **Phase 6** (JSON error purity). The exit-code
scheme (`0` success / `101` DB-missing-stale / `102` config / `1` generic error) is unchanged.

---

## Phase 6 — Machine-readable error output (FR-012)

**Contract.** In `--format json` mode, a failing command MUST write **exactly one JSON document** to stderr and
no other text (no log lines, no tracebacks, no blank lines). Text mode and the log file are unaffected.

Applies to every JSON-capable error path, at minimum: `config`, `handoff`, `sync`, and any DB-gated command that
emits `_print_error`.

**Shape** (unchanged from 014): a JSON object with at least an `error` key and, where defined, an `exit_code` key.

```text
$ package_mgmt config --format json            # with an invalid depth in the TOML
{"error": "max_source_dep_depth must be an integer, got: -2", "exit_code": 102}
```

**Assertions**:
- stderr contains exactly one JSON document (parses as JSON, no surrounding text).
- Exit code unchanged from 014 (`102` for config errors, etc.).
- Text mode still prints the human-readable message.

---

## Phase 3 — Sync delivery semantics (FR-007)

**Contract.** `package_mgmt sync --system <moniker>` advances the delivery snapshot **only** on a clean result.

| Outcome | Exit code | Delivery state | Diff produced |
|---------|-----------|----------------|---------------|
| no pending | `0` | unchanged | none (existing zero-pending path) |
| clean generate | `0` | advanced for `shipped_ids` only | complete, references only copied payloads |
| any excluded pending/delivered | `1` (error) | **unchanged** | complete for successful records; no entry for failed ones |

**Assertions**:
- After a failed sync (a pool file missing/unreadable), the delivery record for the system is unchanged and the
  produced `diff/` contains no stanza/manifest entry for the failed record.
- A subsequent clean sync succeeds and advances exactly the successfully shipped ids.

---

## Invariants (unchanged, reasserted)

- JSON error payloads remain parseable by scripts (this is what Phase 6 restores).
- Both documented test commands (`pytest`, `python -m unittest discover -s tests`) are green from a clean
  checkout (FR-013) — enforced in `tests/contract/test_cli.py`.
