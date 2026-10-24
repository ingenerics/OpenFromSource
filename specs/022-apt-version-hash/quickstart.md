# Quickstart / Validation: Apt VersionHash Fidelity

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md) · **Data model**: [data-model.md](data-model.md)
· **Contracts**: [contracts/packages-stanza.md](contracts/packages-stanza.md)

This is the runnable validation guide. It proves the feature end-to-end: every emitted
handoff/diff `Packages` stanza reproduces apt's VersionHash from the actual package
control, so an already-installed version is not reported as upgradable while a genuinely
newer one still is.

Prerequisites: the project installed (`pip install -e .`), both test runners (`pytest`
and `python -m unittest discover -s tests`), a cache with a generated handoff, and (for
the operator scenario) a scratch apt root or the real operator box.

## Phase 1 — VersionHash parity scan (FR-001…FR-003, SC-002/SC-004)

**Scenario 1a — unit-level oracle.** Port apt's VersionHash exactly as recorded in
[research.md](research.md) §1 (fields `{Installed-Size, Depends, Pre-Depends, Conflicts,
Breaks, Replaces}` in order; DJB-2 hash from `5381`; skip whitespace and `=`; strip `0:`
epochs; fold case). Then, over a generated handoff index:

```text
# for every (package, version, arch) stanza in handoff's trixie/main Packages.gz:
#   hs = version_hash(parse_fields(stanza))
#   ds = version_hash(parse_fields(dpkg-deb -f <pool .deb> <fields>...))
#   assert hs == ds
```

**Expected**: zero mismatches across the whole cache (on the real cache after the fix:
**0/207**). Before the fix this scan flagged exactly the 7 reported packages
(`debianutils`, `libexpat1`, `libpam-modules`, `mount`, `perl-base`, `sed`, `tar`), all
because of an invented `Depends` field.

**Scenario 1b — pre-depends-only package.** For a package whose `.deb` control has
`Pre-Depends` but no `Depends`:

**Expected**: the emitted stanza contains `Pre-Depends` verbatim and **no** `Depends`
line, and its VersionHash equals the `.deb` control's.

## Phase 2 — unit & contract regression (all FRs)

```text
$ pytest tests/unit/test_handoff.py tests/unit/test_index_loader.py
$ python -m unittest discover -s tests
```

**Expected**: green. The regression tests assert:
- `test_handoff_no_invented_depends_for_pre_depends_only_archive_package` — archive
  stanza for a pre-depends-only package has no `Depends` line (FR-002);
- `test_handoff_local_pre_depends_only_no_invented_depends` — the same for a readable
  `local` payload, even when the DB holds reconstructed deps;
- `test_handoff_local_payload_control_fields_carried_verbatim` /
  `test_handoff_local_depends_keeps_original_control_text` /
  `test_handoff_local_unreadable_payload_falls_back_to_db_reconstruction` /
  `test_diff_local_payload_control_fields_carried_verbatim` — verbatim carry, no merge,
  and the degraded fallback;
- `test_version_hash_fields_captured` — `load_binary_index` captures all VersionHash
  fields.

## Phase 3 — operator upgrade accuracy (FR-004, US1)

**Scenario 3a — no false upgradable.** On the operator with the cache configured as the
apt source (`bootstrap.sh` / `apply.sh`), where a package is installed at a version the
cache also serves:

```text
$ sudo apt update
$ apt list --upgradable
```

**Expected**: the identical-version package is **not** listed (no "X upgradable from X").

**Scenario 3b — genuine upgrade.** With a strictly newer cached version available:

```text
$ sudo apt upgrade --simulate
```

**Expected**: the newer version is offered and installs offline.

## Regression

After all scenarios: `pytest` and `python -m unittest discover -s tests` are green
(baseline 806), and `tests/contract/test_cli.py` passes unchanged (no CLI contract
change).

## Validation Log

Run end-to-end against the real cache at `/var/cache/package_mgmt/binary`:

- **Phase 1** — VersionHash parity scan ported from apt 3.0.3's
  `debListParser::VersionHash`. **Before** the fix: 7/207 stanzas mismatched the `.deb`
  controls, all with a spurious `Depends` (the 7 reported packages). **After** the fix:
  **0/207** mismatches. The `local` component was not present in this cache; the
  unreadable-payload fallback is covered by unit tests.
- **Phase 2** — 806 tests pass (incl. 42 in `test_handoff.py`, 2 of them new regression
  tests); `ruff check` clean.
- **Phase 3** — operator-level behaviour follows directly from Phase 1: with 0/207 hash
  mismatches, equal-version stanzas merge with the installed dpkg-status records, so
  `apt list --upgradable` reports no self-upgrades; strictly newer versions remain
  upgradable (version comparison precedes the hash check in apt's merge logic).
- The generated handoff at `/var/cache/package_mgmt/binary/handoff` was **not**
  regenerated (user decision) — it still carries the pre-fix stanzas; it must be
  regenerated with `package_mgmt handoff` and re-exported to the operator for the fix to
  take effect there.
