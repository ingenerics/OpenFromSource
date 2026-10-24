# Pre-Depends Census: Alpha Release Blockers (US1 / B-1 evidence)

**Feature**: [spec.md](spec.md) · **Source archive**: Debian
trixie main `binary-amd64` (`Packages.xz`, 68,820 binary packages parsed).

**Method** (T014): for the resolved runtime closure of each alpha validation
package, count `Pre-Depends` targets that are **not** also listed in the same
package's `Depends` and **not** satisfied by a `Provides` within the closure —
these are the hard requirements the pre-fix loader (which read only `Depends`)
silently dropped from each package's dependency set.

## Results

| Root | Closure size | Pre-Depends targets also in `Depends` | In `Provides` | NOT in `Depends`/`Provides` (pre-fix gap) |
|------|--------------|----------------------------------------|---------------|--------------------------------------------|
| vifm | 27 | 0 | 0 | 2 (`perl-base`: `libc6`, `libcrypt1`) |
| dpkg | 26 | 0 | 0 | 5 (incl. `tar`: `libacl1`, `libc6`, `libselinux1`) |
| bash | 7 | 0 | 0 | 4 (`bash`/`debianutils`/`base-files`: `libc6`, `libtinfo6`, `awk`, …) |
| coreutils | 15 | 0 | 0 | 8 (`coreutils`: `libacl1`, `libc6`, `libcap2`, `libgmp10`, …) |
| systemd | 26 | 1 | 0 | 6 (`mount`: `libblkid1`, `libc6`, `libmount1`, …) |

Notable findings:

- **`base-files` Pre-Depends `awk`** — a Pre-Depends on a *virtual* package
  (spec's Phase 1 edge case); the closure walk must resolve it via its provider.
- **`systemd` Pre-Depends `libsystemd-shared`** — a package-internal Pre-Depends
  target (`libsystemd-shared` ships under a different package name).
- Duplicated `Depends`+`Pre-Depends` targets (the "appears exactly once" rule)
  were **not observed** on these closures — the duplication is rare in practice,
  but the merge still deduplicates (see unit tests).

## Conclusion

The pre-fix gap is real and broad: every alpha validation closure contains
Pre-Depends-only hard requirements that the loader previously dropped. With the
fix, each of these targets is merged into the resolved dependency set, and the
offline install simulation (`apt-get -s install` against the generated handoff,
see `tests/integration/test_handoff_flow.py::test_handoff_carries_pre_depends_and_installs_offline`)
succeeds with zero missing packages. Whether each target was already covered by
another closure member's `Depends` edge is beside the census's point — the
loader no longer depends on accidental coverage.

## Addendum (Phase 9 release-gate re-run)

Re-run at the release gate against the same trixie main index (68 820 packages),
counting `Pre-Depends` targets that are **not** in the same package's `Depends`
across the whole archive (not only closures):

- packages declaring `Pre-Depends`: **1 041**
- with a pre-dep target NOT duplicated in their own `Depends`: **1 020**
- of those, target not satisfiable via any `Provides` in the index: **1 019**

The gap is thus systemic, not confined to the five validation closures: the
archive-wide rate is ~98% of all Pre-Depends-bearing packages. The B-1 fix
(merge `Pre-Depends` into the resolved dependency set) is what captures these
hard requirements; the live offline install proof for the `vifm` closure
(`apt-get -s install vifm` against the generated handoff) succeeds.
