# Quickstart: Source-Stack Packaging

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md) · **Data model**: [data-model.md](data-model.md)

Runnable validation guide for `package-stack` (spec FR-001…FR-014, SC-001…SC-005). It
proves the feature end-to-end: staged build → packaging → import into `local` → offline
install + rebuild, using the project's own mechanisms. Implementation details live in
`tasks.md`; this is a run/validate guide.

## Prerequisites

- The project installed (`pip install -e ".[dev]"`), both runners (`pytest`,
  `python -m unittest discover -s tests`), and a populated cache (`package_mgmt init`).
- The standard Debian packaging toolset on the host: `dpkg-deb`, `dpkg-shlibdeps`,
  `dpkg-source`, `fakeroot`.
- For the full Hyprland proof (US3): the pinned `repos/` tree (docs/005 Part 1) and the
  built stack.

---

## Phase 1 — One component through the generic path (US1/SC-001)

Proves the generic single-component pipeline with the fixture builders (no real build):

```bash
# build a tiny staged component + source tree (fixture), then package+import it
pytest tests/integration/test_package_stack_flow.py -q          # expected: all pass
```

The fixture test asserts: a one-entry recipe packages a staged dir into a `.deb` with an
auto-derived `Depends`, produces a complete `.dsc` + files, imports both into `local`
linked via `binary_to_source`, marks them `verified`, and that `list`/`show` display the
pair while `verify` reports it complete — all offline, no stack-specific code path
(spec US1 Independent Test).

```bash
# manual smoke (after the fixture proves the mechanism)
package_mgmt package-stack --recipe recipes/hyprland.toml --format json
# expected: components_imported == components_total, every component linked+verified
```

## Phase 2 — Dependency ordering (US2/FR-006)

Shuffle the recipe entries into arbitrary order (or use the fixture test that does) and
confirm `package-stack` still imports dependencies before dependents:

```bash
pytest tests/unit/test_packaging.py -k order -q      # expected: all pass
```

Assertions: order-independent input → topological import order; a component depending on
a missing source-built dependency (e.g. `hypridle` → `sdbus-c++`) fails loudly naming it;
a dependency cycle is rejected.

## Phase 3 — Whole-set validation (US2/FR-007)

After a full import, the command's own whole-set check must pass (contract
[cli.md](contracts/cli.md)):

```bash
package_mgmt package-stack --recipe recipes/hyprland.toml
# expected: "whole-set verify: PASS (no unlinked binaries, no unverified records)"
#           "simulated offline apt install: PASS (0 unmet dependencies)"
package_mgmt verify      # expected: COMPLETE, no unlinked/unverified for the set
```

## Phase 4 — Determinism and re-package (FR-009/FR-010/SC-005)

```bash
pytest tests/unit/test_packaging.py -k determinism -q   # byte-identical artifacts, two runs
```

And re-import idempotence: running `package-stack` twice over identical inputs imports
the identical pair once (no duplicate rows), and re-packaging the same upstream version at
changed source/recipe bumps the Debian revision so `apt upgrade` sees it.

## Phase 5 — Offline distribution + rebuild (US3/US4/SC-003/SC-004)

The imported `local` set flows through the existing channel (FR-012), now extended
**additively** with the operator's source channel (Q3/FR-008): the handoff emits a
`source/Sources` index for `local` and its bootstrap writes a `deb-src` line for `local`:

```bash
package_mgmt handoff --system <sys>     # serves local alongside main/contrib;
                                        # emits dists/<rel>/local/source/Sources[.gz],
                                        # signed in Release.gpg, deb-src line in the
                                        # operator's openfromsource-cache.list
# operator (offline):
#   bootstrap.sh → apt install hyprland hypridle hyprlock hyprpaper \
#                  xdg-desktop-portal-hyprland    # all from the cache, no dpkg -i
#   apt-get source hyprlang            # resolves from local/source/Sources (deb-src),
#                                      # .dsc + files retrieved offline, dpkg-source -x succeeds
```

Validate the source channel directly:

```bash
pytest tests/unit/test_srcindex.py -q       # index from DB rows: fields, checksums, Release hash, determinism
pytest tests/integration/test_package_stack_flow.py -q   # includes offline apt-get source of an imported component
```

Rebuild-reproducibility (US4): retrieve one component's source offline via `apt-get
source`, unpack with `dpkg-source -x` (network disabled), run the recorded rebuild
(`debian/rules`, the docs/005 §2.3 invocations), and compare the artifact against the
shipped binary — identical inputs → identical outputs (SC-004).

---

## Phase 6 — Full regression (Constitution)

```bash
pytest                                     # both runners must be green
python -m unittest discover -s tests
ruff check src/ tests/
```

Expected (documented in `tests/REGRESSION.md`): all tests green under both runners and
`ruff check` clean.

---

## Artifacts to inspect

- [cli.md](contracts/cli.md) — `package-stack` command/exit-code/JSON contract.
- [recipe.md](contracts/recipe.md) — recipe file-format contract (`recipes/hyprland.toml`).
- [artifacts.md](contracts/artifacts.md) — generated `.deb`/`.dsc` contracts.
- [data-model.md](data-model.md) — recipe model, generated artifacts, imported rows.
- `docs/005-hyprland-source-stack.md` — the staged build (`--stage`) + packaging path.
