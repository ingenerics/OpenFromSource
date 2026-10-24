# Data Model: Alpha-Release Preparation

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

**Schema freeze (spec 010/011).** No database schema changes, no migrations, no new
tables or columns. Every fix either reuses existing DB values (`binaries.status`
`'current'`/`'superseded'`, `component='local'`) or lives entirely outside the DB
(signing-keyring path, config contract, docs, version, test fixtures). This mirrors the
file-artifact / config-only posture of specs 021/023/024.

---

## 1. `binaries.status` transitions for `local` imports (R-3)

The only DB-semantic change, and it reuses an existing value and pattern. Importing a
newer `local` version supersedes the prior `current` row for the same
`(package_name, architecture, component='local')`:

| Prior state | Action | New state |
|-------------|--------|-----------|
| one `current` row for `(name, arch, 'local')`, **different** version | mark old `status='superseded'`; upsert the new row (`verified=0` → `1` after payload copy) | one `current` row (new version); one `superseded` row (old version) |
| one `current` row, **same** version | `ON CONFLICT` update (idempotent re-import) | unchanged semantics, no duplicate |
| no prior row | plain upsert | one `current` row |

- **Supersede scope**: only rows with `component='local'` and the same
  `package_name`/`architecture`. Archive rows are untouched (their resolver path already
  supersedes, `resolver.py:1118-1138`).
- **Primitive reuse**: the supersede logic is extracted into one shared helper
  (`supersede_current_binary`) used by both the archive path and the import lane — one
  supersede semantics (research §3).
- **Payload retention**: the superseded row's pool payload is **not deleted** — the pool
  is content-addressed by `name/version` and still checksummed on sync; lagging operator
  snapshots referencing the old version keep resolving. Only the index `current` row is
  superseded.
- **Determinism**: after supersede there is exactly one `current` row per name/arch, so
  `_find_current_binary` (`resolver.py:111-117`) and `whole_set_verify`'s row read
  (`package_stack.py:514-518`) become deterministic (no duplicate-current ambiguity).

**Validation rule** (FR-004/FR-005): exactly one `current` `local` row per
`(package_name, architecture)`; a name may additionally have `superseded` `local` rows
(retained) and a `current` archive row — each is a distinct, legitimate record.

---

## 2. Signing keyring (R-5) — relocated path, not a DB entity

| Aspect | Value |
|--------|-------|
| Old canonical home | `{binary_cache}/handoff/.gnupg` (inside the payload tree) — no longer written/used |
| New canonical home | `{base_dir}/.gnupg-handoff/` (derived via `util/paths.handoff_gnupg_dir`) |
| Migration | none (pre-alpha; legacy in-tree keyrings are unused, a fresh key is generated) |
| Payload contents | `offline-cache.gpg` (public key) + `Release.gpg` signatures only — **no private key** |
| Trust anchor | unchanged: the key is generated once and reused (`_ensure_key`), so `offline-cache.gpg` stays stable across handoff/diff regenerations |
| Copy posture (documented) | a plain copy of `{base_dir}` carries the keyring; exclude `.gnupg-handoff/` from operator media unless the operator is a trusted relay that must re-sign |

**Validation rule** (FR-007/FR-008): a generated handoff payload tree contains no file
under the secret keyring path; only the public key ships.

---

## 3. Config contract (R-4, graybeard warning, R-10)

No config schema change — the same `ProjectConfig` fields are read/validated differently:

### 3.1 `align-repo` baseline (R-4)

The baseline release/component is **derived**, not hard-coded: `config.sources[0]`
(`release`, `components[0]`). `BASELINE_LABEL` is refreshed from the same source.

### 3.2 Graybeard component completeness (graybeard warning)

The graybeard trigger is the config pair `fetch.source = true` AND
`resolution.max_source_dep_depth = -1`. The check is the union of components across the
config's `archive.source` entries with `release = "trixie"` against the four canonical
components `{main, contrib, non-free, non-free-firmware}` (the graybeard preset set).

### 3.3 Absolute path validation (R-10)

| Field | Rule |
|-------|------|
| `paths.base_dir` | already validated absolute (`config_loader.py:246-249`) |
| `paths.binary_cache`, `paths.source_cache`, `paths.iso_dir`, `database.path`, `logging.directory` | now validated absolute **after** `expand_path`; a relative override is rejected with a clear `ConfigError` (no silent CWD-dependent cache locations) |

---

## 4. Version (Phase 3)

| Source | Value |
|--------|-------|
| `pyproject.toml [project] version` | `0.1.0` → `0.1.0-alpha` (Phase-3 exit gate) |

The package `__init__.py` carries no version constant; `pyproject.toml` is the single
source of truth (verified: no git tags exist). Release-facing references (README,
quickstart) are made consistent with `0.1.0-alpha`.

---

## 5. Runtime dependency set (R-1)

| Package | Location | Reason |
|---------|----------|--------|
| `python-debian` | `dependencies` (unchanged) | index/deb822 parsing |
| `pycdlib` | **moved** from `optional-dependencies.dev` → `dependencies` | module-top import in `iso_reader.py:12`; every command must import without extras |

**Validation rule** (FR-001/FR-002): `pyproject.toml [project] dependencies` contains
`pycdlib` (declarative contract check); a no-extras install imports the CLI and runs
`--help`; the built wheel ships the keyring and runs without dev extras.

---

## 6. Test fixtures consolidation (F9)

`tests/fixtures/db.py` (new) hosts the shared `_insert_binary`, `_config`, and
`_pool_prefix` helpers, replacing ~15 near-identical definitions across `test_sync.py`,
`test_diff_generator.py`, `test_handoff.py`, `test_srcindex.py`, `test_downloader.py`,
`test_import_deb.py`, `test_systems.py`, `test_snapshot.py`, `test_apply_script.py`,
`test_bootstrap_script.py`, `test_resolver_build_deps.py`, and the fixture
`make_test_deb.py:177` copy. One update point for schema fixtures; no behavior change.

---

## 7. State transitions (summary)

```text
R-3 (per local import):
  current(name,arch,'local',v_old) ──(import v_new)──► superseded(name,arch,'local',v_old)
                                                     + current(name,arch,'local',v_new)  [payloads both retained]

R-5 (per handoff/diff regeneration):
  secret keyring at {base_dir}/.gnupg-handoff/  (persistent, generated once)
  handoff payload  =  public offline-cache.gpg + signed Release  (no private key)

Phase 3 (exit gate):
  pyproject version  0.1.0 ──► 0.1.0-alpha   (tutorials complete and agreed)
```

---

## 8. Non-entities (explicitly out of the model)

- **L-1…L-12 / R-7 backlog** (sync delivered-defect check, stat miscounts, `Pre-Depends`
  dedup, `Size: None` guard, FAT traceback, downloader self-heal, ensure-base wedge,
  `_lookup_binary_entry` fallback, decode/parser self-heal, batch-constraint log line)
  and the **residual N-2b** note are deferred to a post-alpha polish spec (per user
  instruction). No data-model impact.
- **Physical proofs** (FAT-USB bootstrap, graybeard DVD-1) and **wheel inspection** are
  release checks (quickstart / release notes), not data.
