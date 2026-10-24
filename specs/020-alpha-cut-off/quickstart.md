# Quickstart / Validation: Alpha Cut-off

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md) · **Data model**: [data-model.md](data-model.md)
· **Contracts**: [contracts/cli.md](contracts/cli.md)

This is the runnable validation guide for the final pre-alpha release spec. It proves each of the four review
findings (N-1…N-4) is closed per its triaged resolution. Prerequisites: the project installed (`pip install -e .`
or a standard `pip install .`), both test runners (`pytest` and `python -m unittest discover -s tests`), and a
scratch cache dir for the resolver scenarios.

## Phase 1 — Constraint-aware alternative probes (N-3)

**Scenario 1a — too-old primary does not suppress the alternative (the N-3 hole).**

```text
# scratch cache with a config pointing at a fixture/loopback archive holding a 1.0 and b
$ package_mgmt add a            # register a 1.0 as current
$ package_mgmt add app          # app depends "a (>= 2.0) | b"
```

**Expected**: `app` resolves with `b` in the closure, no error, exit 0; `package_mgmt verify app` reports
Runtime Status COMPLETE and exits 0; an `apt-get install app` from the handed-off cache succeeds.

**Scenario 1b — constraint-satisfying member still wins (regression).**

```text
$ package_mgmt add a            # register a 2.0 as current
$ package_mgmt add app          # app depends "a (>= 2.0) | b"
```

**Expected**: group satisfied by `a`; `b` NOT added; no error, exit 0 (behavior unchanged).

**Scenario 1c — build-dep twin.**

```text
# source app with build-deps "a (>= 2.0) | b", a 1.0 current
$ package_mgmt add app          # fetch_source = true, developer profile
$ package_mgmt verify app
```

**Expected**: `b` linked as the build tool, no "Build tool not resolved" error, build status COMPLETE.

**Automated**: `tests/unit/test_resolver.py` — new tests under the runtime-alternative and build-dep classes
(must FAIL pre-fix for 1a/1c; must PASS post-fix). `tests/contract/test_cli.py` extended with the add-exit-0 →
verify-COMPLETE assertion.

## Phase 2 — Distributable trust anchor (N-4)

**Scenario 2a — packaging declaration (automated).**

```text
$ pytest tests/unit/test_packaging.py -v
```

**Expected**: green — asserts `pyproject.toml` declares `[tool.setuptools.package-data]` →
`package_mgmt = ["data/*.gpg"]` and the keyring file exists in the tree.

**Scenario 2b — build-and-inspect (pre-tag verification, FR-006).**

```text
$ uv build --wheel --out-dir /tmp/wheels
$ python -m zipfile -l /tmp/wheels/package_mgmt-0.1.0-*.whl | grep debian-archive-keyring.gpg
```

**Expected**: `package_mgmt/data/debian-archive-keyring.gpg` appears in the wheel listing.

**Scenario 2c — standard install works.**

```text
# fresh venv, install the built wheel (not editable)
$ pip install /tmp/wheels/package_mgmt-0.1.0-*.whl
$ package_mgmt init             # against a signature-verifying mirror / loopback archive
```

**Expected**: `init` verifies and loads indexes without a spurious "cannot verify signature" / missing-keyring
failure.

## Phase 3 — `init` connectivity guidance (N-1)

**Scenario 3.** Read `docs/001-quickstart-howto.md` step 4. **Expected**: the section states that `init` requires
access to at least one configured mirror, that an `init` reporting "0 binaries cached" (offline mode) or a partial
fetch is not a usable cache, and that the recovery is to resolve connectivity and re-run from a clean cache.
`package_mgmt init` behavior is unchanged (offline `init` still exits 0 with the honest offline note).

## Phase 4 — Graybeard stand-alone (N-2)

**Scenario 4.** Read `docs/profiles/graybeard.toml`, `regular_user.toml`, `developer.toml`. **Expected**:
graybeard is stated as a stand-alone, fresh-cache-only profile (not in a grow-deeper chain); the supported
grow-deeper direction is `regular_user → developer`. Enforced by `tests/unit/test_config.py`
(`TestProfileFilesAreData`). `package_mgmt` resolver behavior is unchanged.

## Full gate

```text
$ pytest tests/ -v
$ python -m unittest discover -s tests
```

**Expected**: both runners green on the post-019 baseline (707) plus the new N-3/N-4 tests; contract suite green.
