# Packaging Contracts: Alpha-Release Preparation

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md) · **Data model**: [data-model.md](../data-model.md)

**Contract policy.** This feature changes two packaging facts: the runtime dependency set
(R-1) and the contents of the handoff payload's key material (R-5). Both are contract
checks the release process must verify against the **built artifact**, not just the source
tree.

---

## 1. Runtime dependencies (R-1)

`[project] dependencies` MUST contain `pycdlib` (moved from the `dev` extra):

```toml
[project]
dependencies = ["python-debian", "pycdlib"]
```

**Why**: the CLI imports `pycdlib` at module top (`lib/service/iso_reader.py:12`) via the
`ensure-base` command chain, so *every* command — including `--help` — requires it at
import time. A documented standard install (`pip install .`, no extras) must run every
command.

**Contract checks** (declarative + release):
- Declarative unit check: `pyproject.toml [project] dependencies` contains `pycdlib`.
- Release check (quickstart FR-001/FR-002): fresh venv → `pip install .` (no extras) →
  `python -c "import package_mgmt.cli.main"` and `package_mgmt --help` succeed.
- Built-artifact check (closes review 020/N-4): install the built wheel (no extras) in a
  fresh venv → import + `--help` succeed, and the wheel ships
  `package_mgmt/data/debian-archive-keyring.gpg` (`[tool.setuptools.package-data]`),
  so remote index verification works from a plain installed copy.

---

## 2. Version (Phase 3 exit gate)

`[project] version` is set to `0.1.0-alpha` (from `0.1.0`) at the end of Phase 3, after
the tutorials are complete and agreed. `pyproject.toml` is the single source of truth
(the package `__init__.py` carries no version constant; no git tags exist). Release-facing
references (README, quickstart) are made consistent with `0.1.0-alpha`.

---

## 3. Handoff payload key material (R-5)

| Artifact | Contract |
|----------|----------|
| Public key `offline-cache.gpg` | present in the handoff payload (`{binary_cache}/handoff/`) — the operator's trust anchor, unchanged |
| `Release.gpg` (detached armored, per release) | present, signed with the cache-local key — unchanged |
| **Private keyring** | **absent** from the handoff payload tree; the secret keyring lives at `{base_dir}/.gnupg-handoff/` |
| Legacy `{binary_cache}/handoff/.gnupg` | no longer written or used; no migration |

**Contract checks**:
- Unit (`tests/unit/test_paths.py`): `handoff_gnupg_dir` resolves to
  `{base_dir}/.gnupg-handoff/`, outside the payload tree.
- Unit (`tests/unit/test_handoff.py`): after `generate_handoff`, the payload tree contains
  the public key but **no** secret keyring material.
- Documentation check: README + docs/001 record the copy posture — exclude
  `.gnupg-handoff/` from operator media unless the operator is a trusted relay that must
  re-sign.

---

## 4. Operator impact (unchanged surface)

The operator installs from the cache exactly as before (`bootstrap.sh` → `apt`). The only
difference is that the media no longer carries the private signing key by default; the
public key they verify with is identical.
