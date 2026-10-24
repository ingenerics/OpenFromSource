# Quickstart: Alpha-Release Preparation

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md) · **Data model**: [data-model.md](data-model.md)
· **Contracts**: [cli.md](contracts/cli.md) · [config.md](contracts/config.md) · [packaging.md](contracts/packaging.md)

Runnable validation guide for spec FR-001…FR-021, SC-001…SC-009. It proves every review
finding is fixed end-to-end with the project's own mechanisms. Implementation details
live in `tasks.md`; this is a run/validate guide.

## Prerequisites

- The project installed (`pip install -e ".[dev]"`), both runners (`pytest`,
  `python -m unittest discover -s tests`), and a populated fixture cache.
- For the physical proofs (Phase 6): a FAT-USB stick and a Debian Trixie DVD-1 image.

---

## Phase 1 — A standard install works (R-1 / FR-001/FR-002)

```bash
# declarative check: pycdlib is a runtime dependency
pytest tests/unit/test_packaging_install.py -q          # expected: pass

# release check (no extras): fresh venv, install, import + --help
python -m venv /tmp/ofa-venv
/tmp/ofa-venv/bin/pip install .                         # NO [dev] extras
/tmp/ofa-venv/bin/python -c "import package_mgmt.cli.main"   # no ModuleNotFoundError
/tmp/ofa-venv/bin/package_mgmt --help                   # exit 0, usage printed
```

Built-artifact check (closes review 020/N-4): `pip wheel . -w /tmp/wheels`, install the
wheel into a fresh no-extras venv, run `--help` and a fixture-ISO `ensure-base` — the
wheel ships the archive-signing keyring and runs without dev extras
([packaging.md](contracts/packaging.md) §1).

---

## Phase 2 — `download` stays clean after local imports (R-2 / FR-003)

```bash
pytest tests/unit/test_downloader.py -k local -q        # engine regression
pytest tests/integration/test_import_deb_flow.py -q     # download after import
```

Expected: import a `local` deb, then `download` exits 0 with zero command errors and no
"cached index missing for trixie/local" message; the `local` record stays `verified`
([cli.md](contracts/cli.md) §3).

---

## Phase 3 — Newer local imports supersede older ones (R-3 / FR-004/005)

```bash
pytest tests/unit/test_import_deb.py -k supersede -q    # cross-version + retention
```

Expected: importing `1.3-0` after `1.2-3` leaves exactly one `current` local row, the
handoff/diff emit only `1.3-0`, the `1.2-3` payload is retained and still checksummed on
sync (a lagging operator snapshot referencing `1.2-3` still installs), and a re-import of
the identical version stays idempotent.

---

## Phase 4 — Config-driven `align-repo` baseline (R-4 / FR-006)

```bash
pytest tests/unit/test_alignment.py -k baseline -q      # config-driven variant
```

Expected: on a non-trixie or first-component-not-`main` config, `align-repo` validates
against the configured baseline and reports a meaningful verdict (no false "everything
unmet"); the standard trixie/main verdict is unchanged ([cli.md](contracts/cli.md) §2).

---

## Phase 5 — Keyring posture, graybeard warning, paper-trail (R-5/R-6, graybeard, FR-007…FR-010)

```bash
pytest tests/unit/test_paths.py -q                      # keyring outside payload tree
pytest tests/unit/test_handoff.py -q                    # payload: public key only, no secret
pytest tests/unit/test_config.py -q                     # graybeard helper; relative-path rejection
pytest tests/unit/test_ensure_base.py -q                # graybeard warning service
pytest tests/contract/test_cli.py -q                    # warning first-line + exit codes
```

Expected:

```text
$ package_mgmt ensure-base          # under graybeard config missing contrib/non-free/...
WARNING: graybeard configuration (source = true, max_source_dep_depth = -1) is set
but the trixie sources are missing component(s): contrib, non-free, non-free-firmware.
...   # warning is the FIRST stdout lines, then the summary
```

- Keyring: `handoff_gnupg_dir` = `{base_dir}/.gnupg-handoff/`; a generated handoff payload
  contains no private key material ([packaging.md](contracts/packaging.md) §3).
- Config: a relative `binary_cache`/`iso_dir`/etc. override is rejected with exit 102 and a
  clear message ([config.md](contracts/config.md) §1).
- Paper-trail (R-6): `README.md` figures read 25 specs / 976 tests; a `LICENSE` file
  exists at the root; the `AGENTS.md` SPECKIT block points at this feature;
  `manifests/hyprland-0.49.toml`'s `reason` records the manual build-verified 0.49 pin.

---

## Phase 6 — Consolidation, craft, and verification (F1…F10, R-8…R-11, FR-011…FR-020)

```bash
pytest tests/unit/test_fs_util.py -q                    # F1 shared fs/hash helpers
pytest tests/unit/test_dependency.py -q                 # F2 shared dep-satisfaction predicate
pytest tests/unit/test_package_stack.py -q              # F4 unreadable payload → warning
pytest tests/unit/test_index_loader.py -q               # R-9 fetch_fn forwarded
# full suite — both runners + ruff + contract (FR-020 baseline re-record)
pytest
python -m unittest discover -s tests
ruff check src/ tests/
# then re-record tests/REGRESSION.md at/above the 914 baseline
```

Physical proofs (FR-020, appended to release notes):

```bash
# FAT-USB bootstrap: build a cache, copy to a FAT-USB stick, run bootstrap.sh on the
# target, `apt update` + `apt install` from the cache — must succeed with no network.
# Graybeard DVD-1: init + ensure-base from the DVD-1 image under the graybeard config,
# offline install simulation — must complete and be appended to the release notes.
```

---

## Phase 7 — Tutorials + version bump (Phase 3 / FR-021, SC-009)

Collaborative authoring sessions (Administrator + agent): agree topics/scope and a **new**
tutorial style (distinct from `docs/001`–`docs/005`), author the tutorials, and validate
that each tutorial's commands match the implemented behavior. At the end of Phase 3:

```bash
grep '^version' pyproject.toml    # expected: version = "0.1.0-alpha"
grep -n '0.1.0-alpha' README.md   # release-facing references consistent
```

---

## Artifacts to inspect

- [cli.md](contracts/cli.md) — `ensure-base` graybeard warning, `align-repo` baseline,
  `download`-after-import CLI behavior.
- [config.md](contracts/config.md) — path validation, graybeard detection, baseline
  derivation, keyring path.
- [packaging.md](contracts/packaging.md) — runtime deps, no-extras install + wheel, handoff
  keyring contract, version bump.
- [data-model.md](data-model.md) — `local` supersede status transitions, keyring path,
  config contract, version.
- `tests/REGRESSION.md` — re-recorded baseline (FR-020).
