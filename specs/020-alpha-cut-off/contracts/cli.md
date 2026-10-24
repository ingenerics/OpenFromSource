# Contracts: Alpha Cut-off

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md) · **Data model**: [data-model.md](../data-model.md)

**Contract policy (spec Assumption).** The exit-code/JSON contract table in
`specs/014-base-profiles/contracts/cli.md` is **extended, not diverged from**; `tests/contract/test_cli.py` stays
green throughout. Exit codes are unchanged: 0 success, 1 general error, 100 permission, 101 stale/missing
database, 102 config error. Two of the four findings are code fixes (N-3, N-4); two are documentation-only
(N-1, N-2) and carry no CLI contract change.

---

## Phase 1 — Alternative groups resolve constraint-correctly (FR-001…FR-004)

**Contract.** `add` exits 0 for a package **only when its resolved closure is the one `apt` would actually
install** — in particular, an alternative group `a (>= V) | b` is satisfied only when a group member's `current`
version satisfies that member's own constraint. A too-old primary does not win an alternative; a resolvable
alternative is brought into the closure instead. This closes the last contradiction between `add` (exit 0) and
`verify` (INCOMPLETE on the same cache).

```text
# a 1.0 is already current; archive holds a 1.0 and b 1.0; app depends "a (>= 2.0) | b"
$ package_mgmt add app
Package app resolved from trixie/main (…)
# exit 0 — and the closure now contains b, NOT a

$ package_mgmt verify app
Runtime Status:         COMPLETE
# exit 0 — the cache apt can install
```

**Assertions**:
- Pre-register `a 1.0` as `current`; resolve `a (>= 2.0) | b` (index holds `a 1.0`, `b`) → `b` is registered
  `current`, no error remains, `add` exits 0, `verify` reports COMPLETE, `apt-get install` succeeds.
- Regression: pre-register `a 2.0`; resolve `a (>= 2.0) | b` → group satisfied by `a`, `b` NOT resolved, no
  error, exit 0.
- Regression: a virtual member (via `provides`) with any `current` provider satisfies the group (over-inclusive,
  FR-003) — no behavior change.
- Build-dep twin: source `app` with build-deps `a (>= 2.0) | b`, `a 1.0` current → `b` linked as a build tool, no
  "Build tool not resolved" error, `verify` build status COMPLETE.

---

## Phase 2 — Distribution artifact carries the trust anchor (FR-005…FR-006)

**Contract.** A wheel or sdist built from the source contains `package_mgmt/data/debian-archive-keyring.gpg` at
the exact path the tool resolves at runtime, so a standard (non-editable) install can verify remote indexes
(`init`/`add`/`download` against any mirror that requires signature verification). The supported install modes are
documented.

**Assertions**:
- `pyproject.toml` declares `[tool.setuptools.package-data]` → `package_mgmt = ["data/*.gpg"]`.
- The keyring file exists at `src/package_mgmt/data/debian-archive-keyring.gpg`.
- A built wheel's contents include `package_mgmt/data/debian-archive-keyring.gpg` (pre-tag verification step,
  FR-006).
- A standard install of that wheel runs `init`/`add` against a signature-verifying mirror without a spurious
  "cannot verify signature" failure.
- `docs/001` states the supported install modes (editable dev flow + standard install working from the artifact).

---

## Phase 3 — `init` connectivity guidance (FR-007, docs)

**Contract (documentation).** `docs/001` step 4 states that `init` requires access to at least one configured
mirror, that its job is fetching the indexes and building the virtual-package snapshot, and that an `init`
reporting "0 binaries cached" (offline mode) or a partial fetch is not a usable cache — the recovery is to resolve
connectivity and re-run `init` from a clean cache. **No change to `init` behavior or exit codes** (offline `init`
still exits 0 with the honest offline note, per P-4).

---

## Phase 4 — Graybeard is a stand-alone, fresh-cache-only profile (FR-008, docs)

**Contract (documentation).** The profile files state the supported profile-transition direction as
`regular_user → developer` (including deepening developer depth), and state that graybeard is a stand-alone,
fresh-cache-only profile run from the get-go — not reached by reusing a cache grown under a shallower profile.
No change to resolver behavior or exit codes.

**Assertions** (enforced by `tests/unit/test_config.py`'s profile-file-data test):
- `graybeard.toml` does NOT name graybeard as part of a grow-deeper chain; it states the fresh-cache-only stance.
- `regular_user.toml`'s grow-deeper direction ends at `developer` (graybeard absent).
- `developer.toml`'s grow-deeper direction is `regular_user → developer (depth 1) → developer (depth N)`.
- Spec 014's historical chain wording is superseded, not re-edited.
