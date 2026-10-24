# CLI Contracts: Resolver & Dependency Correctness

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md)

Extends (never contradicts) the existing contracts in `specs/014-base-profiles/contracts/cli.md`. Two phases
change CLI-observable behavior — **Phase 1** (add/`--file` on satisfied alternative groups) and **Phase 5**
(`verify --graph` version-constrained satisfaction). The exit-code scheme, JSON error shapes, and all other CLI
surface are unchanged. Phases 2/3/4/6 are internal (source linkage, dependency parsing, batch-scoping) with no CLI
surface change; their diagnostics are log-level only.

---

## Phase 1 — `add` / `--file` with a satisfied alternative group (FR-001/FR-002)

**Contract.** A request whose dependency is an alternative group is a **success** when at least one member of the
group resolves within the configured components — even when the **first** alternative is absent. Success means:
zero errors attributed to the group, exit 0, and (in a `--file` batch) the request counted as resolved. An error is
recorded for the group only when **every** member fails.

This **corrects** the previous behavior where the failed first alternative's error was retained and `add` exited 1
despite the closure being complete. It does not change any exit code or output shape — only which case produces
errors.

```text
$ package_mgmt add pkg            # pkg Depends "ghost | realpkg"; only realpkg exists
Package pkg resolved from trixie/main (5 dependencies)
$ echo $?
0
```

**Assertions**:
- `ghost | realpkg` with only `realpkg` present → closure contains `realpkg`, `errors == []`, exit 0.
- The same request in a `--file` manifest → summary shows the request resolved and zero errors.
- `a | b` with **both** absent → exactly one error for the group, request counted failed, exit 1 (unchanged).
- A mixed batch (a satisfied group next to a genuinely failing segment) → errors attributed only to the failing
  segment (per-segment attribution preserved).

---

## Phase 5 — `verify --graph` checks version constraints on direct-name deps (FR-011)

**Contract.** `verify --graph` reports a version-constrained direct-name dependency satisfied only when a **current
binary whose version satisfies the constraint** exists — not on mere presence. An unsatisfied constraint (e.g.
`libfoo (>= 2.0)` with only `libfoo 1.5`) is reported as `INCOMPLETE` with a non-zero exit, matching the operator's
own install check. Alternatives are walked with their own constraints; providers keep the documented over-inclusive
behavior.

The status/exit semantics are unchanged: COMPLETE ⇒ exit 0, INCOMPLETE ⇒ exit 1. The change is that the runtime
completeness check is now **constraint-aware** — previously-correct caches stay COMPLETE; caches whose declared
constraint is unsatisfied become INCOMPLETE (the intended correction).

**Assertions**:
- `app` Depends `libfoo (>= 2.0)` with only `libfoo 1.5` current → `_dep_satisfied` False, `verify --graph`
  INCOMPLETE, exit 1.
- `libfoo (= 1.5)` with `libfoo 1.5` → COMPLETE, exit 0.
- An alternative group satisfied only by its second alternative (under that alternative's own constraint) → COMPLETE.
- A versionless direct dep with any current binary → satisfied (regression).
- A provider-satisfied dep → satisfied (over-inclusive, regression).

---

## Unchanged surface (reasserted from 014)

- Exit codes: `0` success · `102` config · `101` stale/missing DB · `1` other error.
- JSON error shape on stderr: exactly one JSON document (`{"error": ..., "exit_code": ...}`).
- `add`/`--file` output shape and summary layout: unchanged — only the error **content** for satisfied alternative
  groups becomes truthful.
- `verify --graph` output layout and status labels: unchanged — only the satisfaction **decision** becomes
  constraint-aware.
- No new commands, no new flags, no operator artifacts, no changes to `handoff` (012), `sync`/`apply.sh` (013),
  `list`, `show`, `download`, `config`, or `init`.

## Diagnostics note (Phases 2/4, log-only)

- Phase 2 source-version divergence and Phase 4 unparseable-dependency-token warnings are emitted through the
  logging system (log file; `logger.warning`), **not** to stdout/stderr, and never affect exit codes. This keeps the
  CLI contract clean while satisfying the "never silent" requirements (FR-005/FR-010).
