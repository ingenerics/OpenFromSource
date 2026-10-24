# Config Contracts: Alpha-Release Preparation

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md) · **Data model**: [data-model.md](../data-model.md)

**Contract policy.** No new config keys are added. This feature tightens validation of
existing keys (R-10), formalizes how existing keys drive two behaviors (graybeard
warning, align-repo baseline), and fixes the canonical signing-keyring path (R-5). The
shared loader (`lib/service/config_loader.py`) is the single validation point.

---

## 1. Absolute path validation (R-10)

`paths.base_dir` is already validated absolute. The other path-valued keys are now
validated absolute **after** `expand_path` (i.e. after `{base_dir}` substitution):

| Key | Validation |
|-----|-----------|
| `paths.binary_cache` | absolute after expansion |
| `paths.source_cache` | absolute after expansion |
| `paths.iso_dir` | absolute after expansion |
| `database.path` | absolute after expansion |
| `logging.directory` | absolute after expansion |

A relative override is rejected at load time, before any command runs, via the shared
error convention (exit 102):

```text
$ package_mgmt add vifm     # [paths] binary_cache = "relative/cache"
Config error: [paths] binary_cache must be an absolute path after expansion, got: relative/cache
```

stderr JSON (with `--format json`): `{"error": "[paths] binary_cache must be an absolute path after expansion, got: relative/cache", "exit_code": 102}`

Absolute overrides (the normal case) are accepted unchanged. This removes the silent
CWD-dependence of cache locations.

---

## 2. Graybeard detection and component completeness (graybeard warning)

**Graybeard** is detected by config values (no profile concept in the tool):

```text
[fetch]       source = true
[resolution]  max_source_dep_depth = -1
```

**Component completeness**: the union of `components` across all `[[archive.source]]`
entries with `release = "trixie"` must equal the four canonical components
`{main, contrib, non-free, non-free-firmware}` (the graybeard preset set). If any of the
four is missing under graybeard config, `ensure-base` starts its output with the advisory
warning naming the missing components (see [cli.md](cli.md) §1).

Non-graybeard configs (source off, or a finite depth) never emit the warning, regardless
of component coverage.

---

## 3. `align-repo` baseline derivation (R-4)

The era-gate baseline is derived from the **first configured archive source**:

```text
release   = config.sources[0].release
component = config.sources[0].components[0]
label     = f"{release}/{component} (verified, read-only)"     # replaces hard-coded "trixie/main…"
```

No new key; `config.sources` already exists. The DB query and the cached-index
cross-check use these derived values instead of the hard-coded `WHERE release='trixie'
AND component='main'` (see [cli.md](cli.md) §2 for the observable verdict change).

---

## 4. Signing-keyring path (R-5)

The canonical persistent keyring home is **derived**, not configurable:

```text
handoff_gnupg_dir = {base_dir}/.gnupg-handoff/
```

- Derived via `util/paths.handoff_gnupg_dir`; **no new config key** (consistent with the
  other derived `handoff_*` paths).
- The old in-tree location `{binary_cache}/handoff/.gnupg` is no longer written or used;
  no migration is performed (pre-alpha).
- The handoff payload contains only the public `offline-cache.gpg` (no private key) — see
  [packaging.md](packaging.md) §3.
- Documented copy posture: a plain copy of `{base_dir}` carries the keyring; admins SHOULD
  exclude `.gnupg-handoff/` from operator media unless the operator is a trusted relay
  that must re-sign.
