# Research: Operator Track & CLI/Config Robustness

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

Scope source: `review/04_alpha_summary/02_high_priority_followups.md` entries F-18 (handoff exclusion),
F-19 (apply count capture), F-20 (config type validation), F-21 (local-overlay adoption), F-22 (read-command
gating) — the "Download / verify / operator track" handoff/apply items and the "Config / CLI / database"
cluster. No clarifications pending (spec "Clarifications" section); all five fixes are code-anchored in the
review and resolved behaviorally below. All decisions are verified against the current source (this branch's
state, after 016/017 landed).

---

## Decision 1 — Handoff out-of-config exclusion mirrors `generate_diff`, reported via a new stats list + summary Warning (F-18)

**Decision**: In `generate_handoff`, after `bases = _component_bases(config)` (which already reflects only the
*currently configured* `(release, component)` pairs whose archive index exists), compute `base_set = set(bases)`.
In the per-`(release, component)` record loop, before the index-existence check, if `(release, component) not in
base_set`, append the record to a new `HandoffStats.excluded_config` list, `logger.warning(...)` per record, and
`continue` — the record is never appended to a paragraph and its id is **never** added to `delivered_ids`.
`cli/commands/handoff.py:_print_summary` prints a visible Warning line (mirroring the existing `excluded_pool`
Warning) when `excluded_config` is non-empty.

**Rationale**: `generate_diff` already computes `base_set` and excludes out-of-base records
(`lib/service/handoff.py:1004-1037`) — this is pure parity ("mirror `generate_diff:1009`" in the review). The
`_component_bases` helper derives membership from `config.sources`, so dropping a component from the config
removes it from `bases` immediately; a membership check against `base_set` is the exact missing gate. Because
the check is re-derived from the current config on every run, a later re-expansion of the config restores the
records naturally (acceptance scenario 4 — exclusion is not permanent). "Loudly" is satisfied by the per-record
log **and** the summary Warning line (the review's F-18 test demands the record be "omitted **and** reported");
unlike `unconfirmable` records this is a Warning, not a command error — the spec (FR-001, acceptance 1) asks for
a notice, and turning a config-shrink into a hard failure would break the handoff workflow.

**Alternatives considered**:
- *Hard error (exit 1) on any out-of-config record* — rejected: the spec requires a visible exclusion notice,
  not a failure; the operator must still receive the in-config portion. Mirrors `excluded_pool` (Warning), not
  `unconfirmable` (error).
- *Silently drop (no reporting)* — rejected: that is the current bug; FR-001 explicitly forbids silence.
- *Persist the exclusion* — rejected: acceptance 4 requires non-permanence; the check is deliberately derived
  from the live config each run.

---

## Decision 2 — `apply.sh` routes apt output to a log file; the summary count line is exactly `New files: <number>` (F-19)

**Decision**: In the `APPLY_SCRIPT` template:
1. `run_apt_update` runs `apt-get … update > "<log>" 2>&1` (returning apt's exit code), where `<log>` is a
   transient file under the operator cache (e.g. `$cache_dir/diff/.apply-apt.log` — the diff's own dir is
   already written by this script). `apply_configure`'s stdout therefore carries **only** the count echoed by
   `copy_new_files`; apt/progress output can no longer leak into `new_files="$(apply_configure …)"`.
2. On apt failure, `apply_configure` prints `apt update failed — see <log> (excerpt below); the merged files
   and indexes were left in place for inspection.` to stderr followed by a bounded `tail` of the log (so the
   operator still sees why apt failed, with the merged state intact for inspection).
3. `apply_main`'s summary count line becomes exactly `New files: <number>` (dropping the "copied into pool" and
   "(already applied)" suffixes from that line); the already-applied state is reported on a separate line so the
   branch is observable and clean (e.g. `New files:       0` + `Already applied: all new files already present
   in the pool`). Exit code stays `0` for both merged and already-applied (spec 013).

**Rationale**: The bug is `new_files="$(apply_configure …)"` capturing `apt update`'s stdout, so the subsequent
`[ "$new_files" -eq 0 ]` sees multi-line apt noise → `[: …: integer expression expected`, the already-applied
branch is unreachable, and the summary shows raw apt output. The review's fix is explicit: "keep
`apply_configure`'s stdout reserved for the count (route apt output to stderr or a file/FD)". The spec's
independent test demands **stderr is empty** and the summary **matches `New files: <number>`** — routing to
stderr would still leave apt's progress lines on stderr, so a log file is the only routing that satisfies both
the clean-stderr and clean-stdout contracts simultaneously. FR-003 ("summary MUST contain only the count") plus
acceptance 1 ("summary is exactly `New files: <number>`") drive the exact count line; the already-applied state
moves to its own line so acceptance 3 ("already-applied branch reached and reported without an error") still
holds. The apt-failure message change is a deliberate contract update (docs inheritance; the existing
`ApplyScriptAptFailureTest` assertion is updated in this feature).

**Alternatives considered**:
- *Route apt output to stderr* — rejected: violates "stderr is empty" (apt prints `Get:`/`Hit:` progress on
  stdout, and redirecting it to stderr would make stderr noisy).
- *Route apt output to `/dev/null`* — rejected: hides the reason on an apt failure; the operator needs the
  failure detail, so it is logged and tailed on failure.
- *Keep the summary suffixes* — rejected: contradicts "summary is exactly `New files: <number>`" (FR-003 +
  acceptance 1); the already-applied nuance is preserved as a separate line.

---

## Decision 3 — Declarative type map validated in the merged loop; every config failure becomes `ConfigError` (F-20)

**Decision**: Add a `_TYPE_MAP` beside `_KEY_MAP` declaring the expected Python type for every known key
(`str` for the path/log keys, `bool` for `fetch.source`, `int` for the two depth keys). In `load_config`, in the
loop that maps merged `(section, key)` tuples onto `ProjectConfig` kwargs, validate each value's type against
the map and raise `ConfigError("[<section>] <key> must be <type>, got: <value!r>")` on mismatch. Booleans
accept **only** real TOML booleans (`type(value) is bool` — TOML `true`/`false` parse to Python `bool`), so a
string like `"yes"` is rejected. Additionally:
- Guard the `archive` handling: a non-table `archive` (`defaults_raw.get("archive")` returning a scalar, or a
  local `[archive]` that is not a dict) is a `ConfigError` naming the key and expected `table`, instead of the
  current `AttributeError` (`config_loader.py:192`).
- Keep the existing `_validate_depth_settings` for the `>= -1` range rule (it already rejects bools and
  non-ints and names the key); the type map covers the same keys' base type first.
- Wrap the `validate_absolute` call so a non-absolute `base_dir` raises `ConfigError` (currently a raw
  `ValueError` traceback) — consistent with "all config failures flow through the structured path".
- Unknown keys in `project.defaults.toml` are **rejected** with a structured error naming the key
  (`Unknown configuration key: [<section>] <key>`, exit 102 + JSON) — a clarification adopted from the review's
  "name the offending key" language (spec FR-005 / Q&A). Unknown keys in the **local overlay** remain silently
  ignored (US4 regression-preserving: only known keys are adopted there).

**Rationale**: The bugs are all value-type holes in the single loader: `base_dir = 123` → `TypeError` in
`validate_absolute`/`expand_path`, non-table `archive` → `AttributeError`, `binary_cache = 5` → `AttributeError`
in `expand_path`, and `fetch.source = "yes"` silently accepted (TOML strings are not booleans). A single
declarative map next to `_KEY_MAP` keeps the loader data-driven, validates every known key in one place, and
routes every failure through the existing `ConfigError` → exit 102 (+ JSON) path that `main.py` and
`handle_config` already honor — no command can run with a mistyped value, and no raw traceback escapes (the
spec's F-20 contract). `type(value) is bool` for booleans rejects `"yes"` while accepting TOML `true`/`false`
(FR-006); `isinstance(value, bool)`-aware int checks already exist in the depth validator, so bools never pass
as ints.

**Alternatives considered**:
- *Validate types only in the `config` command* — rejected: `main.py` catches only `ConfigError` from
  `load_config`; other commands would still trace back. Validation must live in the shared loader.
- *Use `tomllib`'s typing/TypedDict coercion* — rejected: TOML has no schema; a hand-rolled declarative map is
  the established pattern here and matches `_KEY_MAP`'s shape.
- *Reject unknown keys in defaults* — initially rejected as beyond scope (F-20 is about mistyped *values*),
  but **adopted as a clarification**: the merged loop already had to name keys for type errors, so rejecting
  unknown *defaults* keys reuses the same path (exit 102 + JSON) and matches the spec's FR-005. The local
  overlay stays lenient so existing local files keep working (US4).

---

## Decision 4 — Local overrides adopt known keys regardless of defaults presence; merged result validated (F-21)

**Decision**: In `load_config`, change the local-overlay loop condition from `if tup in defaults_flat` to
`if tup in _KEY_MAP`. A local override for a **known** key is adopted even when `project.defaults.toml` lacks
the section containing it, and `overridden_keys` is flagged exactly as today. The merged result
(`merged = {**defaults_flat, **overrides_raw}`) is then validated by the existing post-merge steps (Phase 3's
type map + `_validate_depth_settings`), so a bad depth in a local override fails at load **naming the key** even
when defaults lack `[resolution]`. `[[archive.source]]` replacement semantics are untouched: `_flatten` still
skips the `archive` section and the dedicated replacement logic at `config_loader.py:191-196` still applies only
when the local file carries an `archive.source` (a known-key semantic difference deliberately preserved).

**Rationale**: The bug is the override gate `if tup in defaults_flat` (`config_loader.py:175-181`) — an override
for a key absent from defaults (trimmed/profile drop-in defaults lacking `[resolution]`) is silently dropped, and
because it is dropped *before* validation, a bad depth in that override is never seen. Adopting any **known** key
(`_KEY_MAP` is the loader's own registry of recognized keys) restores the contract that a bad depth "in either
project TOML" fails at load, while the post-merge validation (already present, now applied to the adopted value)
names the key. Unknown local keys stay ignored (same policy as defaults — regression-preserving, and the archive
replacement keeps its special case). Flagging in `overridden_keys` means `config --format json` shows the
override flag even for absent-section keys (FR-008, acceptance 3).

**Alternatives considered**:
- *Adopt all local keys, then filter unknown in the merged loop* — functionally equivalent, but gating on
  `_KEY_MAP` at adoption time is the minimal diff and keeps `overridden_keys` accurate.
- *Apply overrides only when the key exists in the dataclass* — rejected: same effect but hides the loader's
  own registry; `_KEY_MAP` is the canonical known-key set.
- *Also drop defaults-section validation when absent* — rejected: the whole point is that merged validation is
  *retained* regardless of defaults presence (FR-009).

---

## Decision 5 — `show`/`list`/`verify` gate through the shared `validate_schema_or_error` (F-22)

**Decision**: In `handle_show`, `handle_list_deps`, and `handle_verify_graph`, call
`validate_schema_or_error(db_path)` at the top of the handler (before any `sqlite3.connect`); on a non-None
return, print nothing else and return that exit code (`EXIT_SCHEMA_MISMATCH` = 101). `validate_schema` already
checks `os.path.exists(db_path)` *before* opening a connection (raising "Database not found at {db_path}; run
'init' first"), so no stray 0-byte file is created on an uninitialized cache; a schema-mismatched database hits
the identical `_rebuild_message` path the sibling commands (systems, snapshot, handoff, sync) already use. For
`list`, the gate runs before both the `--status` and the `--deps` branches; for `show`, before the table-name
validation (a missing cache should be reported as uninitialized, matching siblings).

**Rationale**: The three commands bypass the gate their siblings share and call `sqlite3.connect` directly,
creating a 0-byte file when absent (which later makes gated commands misreport the DB as stale) and tracing back
on an uninitialized cache. Reusing `validate_schema_or_error` is the smallest possible change and guarantees the
same clean JSON error + exit 101 + no-file-created behavior and the same stale-schema message
(FR-010/FR-011). It is behavioral only — no schema change (spec Assumption). The `finally: conn.close()` /
success paths in all three handlers are unchanged, so a valid initialized cache behaves identically
(acceptance 3 — no regression).

**Alternatives considered**:
- *Inline a per-command existence check* — rejected: duplicates the shared gate and risks divergence in the
  stale-schema message; `validate_schema_or_error` exists precisely so all commands behave alike.
- *Create the DB implicitly via `init` on first use* — rejected: creates the stray file the review flags as the
  bug; read commands must be side-effect-free on an uninitialized cache.

---

## Consolidation

All five entries are resolved behaviorally with code anchors in `review/04_alpha_summary/02_high_priority_followups.md`;
no unresolved clarifications. Cross-cutting:
- **Schema freeze** holds — every decision is behavioral; F-22 adds no DB writes, F-18 no DB schema change.
- **No new runtime dependencies** — stdlib + the existing bash scripts only.
- **Contract handling** — 014 (config/CLI) is **extended** for F-20/F-21/F-22; 012 (handoff) and 013 (apply)
  operator contracts are **updated** for F-18/F-19 (docs inheritance per the review's cross-cutting note);
  `tests/contract/test_cli.py` stays green.
- **Both runners** — pytest + `python -m unittest discover -s tests` held green at every phase (baseline 608 /
  608 OK, contract 60 + 11 subtests).
