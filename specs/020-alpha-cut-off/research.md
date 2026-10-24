# Research: Alpha Cut-off

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

**Method.** The four findings (N-1…N-4) of `review/05_alpha_final_kimi_k3/ALPHA_REVIEW.md` were traced against
the current source at HEAD `55cdf26` and triaged in session (recorded in the spec's Clarifications section).
Two became code fixes (N-3, N-4); two became documented limitations (N-1, N-2). This file records the decisions,
the code evidence, and the alternatives considered — including one empirically resolved packaging unknown.

---

## 1. N-3 — Constraint-aware alternative-group probes (implemented)

**Decision**: make both "already satisfied" probes — the runtime probe (`resolver.py:1059-1069`) and the build-dep
twin (`resolver.py:693-703`) — constraint-aware, mirroring the sibling build-dep link block
(`resolver.py:716-744`) that already evaluates each candidate's version against its own name's constraint.

**Evidence** (traced scenario):
1. `a 1.0` is `current` (prior batch). `add app` where `app` depends `a (>= 2.0) | b`; the index holds `a 1.0`
   and `b 1.0`.
2. `_resolve_one('a', {>= 2.0})` fails the constraint, finds no satisfies-all entry, and appends
   `"No version of a satisfies constraint (>= 2.0)"` (`resolver.py:880-892`), returning `None`.
3. The probe at `resolver.py:1059-1069` runs `SELECT 1 FROM binaries WHERE package_name = 'a' AND status='current'`
   → the `a 1.0` row exists → `any_satisfied = True` → the `if not any_satisfied` guard (`:1070`) never tries `b`,
   and `del stats["errors"][error_mark:]` (`:1083`) retracts the error. `add` exits 0.
4. `verify`'s `_dep_satisfied` evaluates constraints (`verify.py:181-188`) → INCOMPLETE; the operator's
   `apt-get install app` fails — the cache is simultaneously "successfully resolved" and uninstallable.

**Rationale**: the link block at `resolver.py:716-744` already implements the correct pattern (fetch the member's
`(id, version)`, check `satisfies_constraint(version, name_constraints.get(name))`, for both direct binaries and
`virtual_resolutions` providers). The two higher probes drifted from it. The fix is small and low-risk.

**Design decisions**:
- **Runtime probe** (`resolver.py:1059-1069`): for each group member, fetch the `current` binary's version and
  evaluate `satisfies_constraint(version, member_vc)` where `member_vc` is the primary's `spec.version_constraint`
  or the alternative's `alt.version_constraint`. The provides-join leg (`provides p JOIN binaries b`, virtual
  members) keeps its documented **over-inclusive** semantics — any `current` provider counts (FR-003; providers
  are over-inclusive by design per F-13). Only a constraint-wise-satisfying member retracts the failed-primary
  error; otherwise the alternatives are tried (FR-001/FR-002).
- **Build-dep twin** (`resolver.py:693-703`): mirror the link block exactly — direct-binary and
  `virtual_resolutions`-provider members are both constraint-checked (FR-004). When no member constraint-wise
  satisfies, the resolvable alternative is linked and no "Build tool not resolved" error remains.
- **DRY option**: extract the link block's satisfaction loop (fetch `(id, version)` per member, constraint-check)
  into a module-level helper reused by the probe(s) and the link block, so the two cannot drift again. Tasks will
  decide between a shared helper and an inline mirror; the behavior contract is identical either way.

**Alternatives considered**:
- *Repopulate / re-resolve inside the probe* (call `_resolve_one` for each member before probing): heavier,
  duplicates the per-attempt error bookkeeping already present; rejected.
- *Reject too-old primaries in `_resolve_one` before the probe*: the constraint failure is already correctly
  recorded there; the bug is purely the probe's existence check. Rejected.
- *Keep the status-only probe* (review's own note): leaves the N-3 hole open. Rejected.

**Tests (TDD, must fail pre-fix)**: `tests/unit/test_resolver.py` — `a (>= 2.0) | b` with `a 1.0` current →
`b` resolved, no error, exit 0; build-dep twin with resolvable alternative → linked, no "not resolved" error;
regression guards for a constraint-satisfying member (group satisfied, no alternative) and for the over-inclusive
virtual-provider leg.

---

## 2. N-4 — Distributable trust anchor (implemented, empirically verified)

**Decision**: declare the archive-signing keyring as package data via
`[tool.setuptools.package-data]` → `package_mgmt = ["data/*.gpg"]`; build wheel + sdist and inspect that the
keyring is present; add a packaging-contract unit test; document the supported install modes.

**Evidence**: `pyproject.toml` declares `build-backend = "setuptools.build_meta:__legacy__"` with
`[tool.setuptools.packages.find]` and **no** `package-data`/`include-package-data`/`MANIFEST.in`. A wheel built
from the current tree contains only `.py` modules + dist-info — **no `.gpg`**. The runtime anchor lives at
`src/package_mgmt/data/debian-archive-keyring.gpg` and is resolved by `index_loader.py:22-24`
(`os.path.join(os.path.dirname(__file__), "..", "..", "data", …)`) — from `lib/service/` that lands at the
installed `package_mgmt/data/`. Every remote-mirror fetch requires it (`index_loader.py:355-356`); a standard
install would fail closed with `IndexVerificationError` on every real archive.

**Empirical result** (resolved in research): appending the `[tool.setuptools.package-data]` block and rebuilding
with the **legacy backend unchanged** produces a wheel containing
`package_mgmt/data/debian-archive-keyring.gpg` (55 918 bytes). The declared backend honors the `[tool.setuptools]`
pyproject config. No backend change needed.

**Design decisions**:
- Keep the runtime `os.path.join` resolution — installed wheels land as normal files, not zip-imports, so
  `importlib.resources` migration is **not** required (review's optional suggestion, consciously dropped).
- The packaging-contract test (`tests/unit/test_packaging.py`, new) asserts that `pyproject.toml` declares
  `data/*.gpg` for `package_mgmt` and that the keyring file exists in the tree — catching regressions without a
  full build in the pytest run.
- The build-and-inspect wheel/sdist check is the pre-tag verification step (spec FR-006); it also documents the
  supported install modes (editable dev flow in `docs/001`; standard `pip install .` now works).

**Alternatives considered**:
- *`MANIFEST.in` + `include-package-data = true`*: works for sdists but adds a file and is redundant with the
  pyproject declaration; rejected.
- *Switch to `setuptools.build_meta` (non-legacy)*: unnecessary churn; the legacy backend already honors the
  pyproject config (verified). Rejected.
- *`importlib.resources`*: only needed for zip-import safety, not wheels; adds code. Rejected.

---

## 3. N-1 — `init` connectivity guidance (documented limitation)

**Decision**: no resolver/`init` code change. Document that `init` requires connectivity and that an offline/
partial `init` is not a usable cache — start again from scratch.

**Rationale** (triage): `init`'s whole job is network work — fetch indexes and build the `provides` snapshot
(`docs/001` step 4). Running it offline is a contradiction. The air-gapped path, `ensure-base`, is
self-sufficient: it creates the DB itself (`ensure_base.py:207`) and populates `provides` from the image
(`ensure_base.py:218`), so N-1's stale-snapshot gap never touches the offline-installer flow. `add` without `init`
is effectively a non-starter (it needs `init`'s cache directories). The only real smell — `init` exiting 0 with
"offline mode, 0 binaries cached" (`init.py:143-149`) — is an honest report (per P-4), not a bug to engineer
around.

**Design decision**: the guidance lands in `docs/001-quickstart-howto.md` step 4 (after the expected-output block):
`init` requires at least one reachable configured mirror; "0 binaries cached" / partial fetch ⇒ not usable; recover
by resolving connectivity and re-running from a clean cache. The misleading "Virtual package 'X' has no providers
in the index" error (`resolver.py:898`) is left as-is (no hint) — keeping this feature's N-1 strictly doc-only per
the spec.

**Alternatives considered**:
- *Repopulate `provides` inside `ensure_indexes`* (the review's fix): solves a flow we now document as
  unsupported; adds resolver complexity. Rejected in triage.
- *Make `init` exit non-zero on zero cached indexes*: a behavioral change contradicting the P-4 offline-mode
  contract; could break the pre-create-skeleton use case. Rejected (note in the spec as optional hardening).

---

## 4. N-2 — Graybeard stand-alone / fresh-cache-only (documented limitation)

**Decision**: no resolver change. Pull graybeard out of the grow-deeper chain; document it as a stand-alone,
fresh-cache-only profile. `regular_user → developer` (including deepening developer depth) remains the documented
supported direction.

**Evidence**: the grow-deeper chain including graybeard is documented at `docs/profiles/graybeard.toml:20-22`,
`docs/profiles/regular_user.toml:16` (developer.toml:17 already stops at developer), and spec 014's FR-014 wording
("the knob is documented for the grow-deeper direction only … regular_user → developer depth 1 → developer depth N
→ graybeard; no constraint enforces this"). The N-2 code defect: all three cached-virtual return branches gate
`_ensure_source` behind `_min_level_repair_enabled()` (`resolver.py:927-933, 949-951`), which is `depth > 0`
(`resolver.py:464-472`) — never at `-1`. On a **fresh** graybeard cache the provider's source is created on first
resolution (`_register_source_for`, `resolver.py:1027`), so the cached branch is fine; the defect only bites on
cross-profile reuse (regular_user → graybeard), which this feature now documents as unsupported.

**Design decisions**:
- `graybeard.toml:20-22`: replace the grow-deeper line with a stand-alone, fresh-cache-only statement, consistent
  with `ensure-base`'s existing fresh-only refusal (`ensure_base.py:172-186`, `docs/003-ensure-base.md:27`).
  Keep the FR-014 retention sentence (still true for developer deepening).
- `regular_user.toml:16`: grow-deeper direction ends at developer (drop graybeard).
- `developer.toml:17`: confirm/align wording (already stops at developer).
- Spec 014's historical wording is **superseded** by this feature, not retro-edited (specs are a historical
  record); the plan/spec state the supersession (Constitution VIII covers live docs).
- `tests/unit/test_config.py`'s profile-file-data test (`TestProfileFilesAreData`) is extended to assert the new
  wording, so the profile-doc contract is enforced.
- The review's part (b) — subtree re-walk machinery + transition fixtures — is intentionally **not** built.

**Alternatives considered**:
- *Implement the N-2 repair fixes (parts a+b)*: the review's part (a) (drop the gate) is trivial, but part (b)
  (subtree re-walk) is real machinery for a cross-profile flow we are redefining as unsupported. Rejected in
  triage for "keep it simple."
- *Enforce fresh-cache in code for `-1` (refuse non-empty caches)*: a behavioral change with test impact;
  inconsistent with FR-014's "no constraint enforced" stance. Rejected — documentation only, matching the ensure-
  base precedent's *documented* fresh-only stance.

---

## Consolidated decisions

| Finding | Resolution | Artifact |
|---------|-----------|----------|
| N-3 constraint-blind probes | Implement (code) | `resolver.py` probes; `tests/unit/test_resolver.py` |
| N-4 keyring missing from artifact | Implement (code) | `pyproject.toml` package-data; `tests/unit/test_packaging.py`; build-and-inspect pre-tag |
| N-1 offline `init` | Document (docs) | `docs/001` step 4 |
| N-2 graybeard grow-deeper | Document (docs) | `docs/profiles/{graybeard,regular_user,developer}.toml`; `tests/unit/test_config.py` |
