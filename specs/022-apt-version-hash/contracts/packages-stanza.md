# Contracts: Apt VersionHash Fidelity

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md) · **Data model**: [data-model.md](../data-model.md)

**Contract policy.** No CLI contract changes — there is no new or modified command, so
the exit-code/JSON contract table in `specs/014-base-profiles/contracts/cli.md` is
**untouched** and `tests/contract/test_cli.py` stays green. The interface this feature
contracts is the **emitted `Packages` stanza** that apt consumes on the operator system
against the installed dpkg-status record.

---

## Phase 1 — the emitted `Packages` stanza preserves apt's VersionHash (FR-001…FR-003)

**Contract.** Every stanza emitted into a handoff or diff `Packages.gz` reproduces apt's
VersionHash inputs — `Installed-Size`, `Depends`, `Pre-Depends`, `Conflicts`, `Breaks`,
`Replaces` — verbatim from the package's actual control record (the cached archive index
for upstream components, the shipped `.deb` control for the `local` component), in that
order. A field the control lacks is absent from the stanza; in particular, `Depends` is
emitted **only** when the control has a `Depends` field, and never from the resolver's
merged `Depends`+`Pre-Depends` view. The stanza's VersionHash (DJB-2 over those fields,
whitespace and `=` stripped, `0:` epochs removed, case folded) therefore equals the
dpkg-status record apt sees for the installed package.

```text
# a package whose .deb control has Pre-Depends but no Depends (e.g. debianutils 5.23.2)
Package: debianutils
Version: 5.23.2
Architecture: amd64
Multi-Arch: foreign
Installed-Size: 225
Pre-Depends: libc6 (>= 2.38)
Breaks: ifupdown (<< 0.8.36+nmu1), printer-driver-pnm2ppa (<< 1.13-12), x11-common (<< 1:7.7+23~)
Filename: pool/main/d/debianutils/debianutils_5.23.2_amd64.deb
Size: 92360
SHA256: 720e43fda316676076498b4224ad3318049deafe60cc7c601011048de03ee97d
# NOTE: no "Depends:" line — the control has none, and emitting one would change the hash
```

**Assertions**:
- For every emitted stanza, the VersionHash over `{Installed-Size, Depends,
  Pre-Depends, Conflicts, Breaks, Replaces}` equals the same computation over the `.deb`
  control of that `(package, version, arch)` — measured across the whole cache, all
  components (0/207 on the real cache after the fix).
- A pre-depends-only package's stanza contains `Pre-Depends` but no `Depends` line.
- A package with both `Depends` and `Pre-Depends` emits each with its original text —
  unmerged and undeduplicated against the other.
- The emitted `Depends` never equals the merged resolver view (`depends_raw`).

---

## Phase 2 — upgrade accuracy on the operator (FR-004, US1)

**Contract.** With the cache configured as the operator's apt source, a package installed
at a version the cache also serves is **not** reported as upgradable; a strictly newer
cached version **is** reported as upgradable and installs offline.

```text
# operator, after handoff/apply + apt update
$ apt list --upgradable
# an installed package at the cache's version: NOT listed
# a package with a newer cached version:  pkg/trixie 1.2-3 amd64 [upgradable from: 1.1-1]
```

**Assertions**:
- Zero "X upgradable from X" self-upgrades for any package served by the cache (SC-001).
- A genuinely newer version appears in `apt list --upgradable` and upgrades cleanly with
  no network access (SC-003).

---

## Phase 3 — degraded local fallback (FR-006)

**Contract.** A `local` record whose payload is missing or not a readable `.deb` does not
abort handoff/diff generation: its stanza falls back to the database-only reconstruction
(`Depends` from the stored dependency specs, no other VersionHash fields) and generation
continues.

**Assertions**:
- Generation completes with the record included (or excluded per the existing
  missing-pool-file rule) and no failure.
- The fallback stanza carries `Depends` from the DB specs only when the payload is
  unreadable; a **readable** control with no `Depends` never gains one.

---

## Phase 4 — both channels, all components (FR-005)

**Contract.** The VersionHash-parity contract holds uniformly across the baseline
`handoff` and the incremental `diff`/`sync`, and across every component (`main`,
`contrib`, `non-free`, backports, `local`), because all share the same `_paragraph`
emission path.

**Assertions**:
- A parity scan over both `handoff` and `diff` indexes reports zero mismatches against
  the `.deb` controls.
- `Multi-Arch`/`Provides` are still carried (unchanged) so `name:any` resolution and
  virtual-package installs keep working.
