# CLI Contracts: Alpha-Release Preparation

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md) · **Data model**: [data-model.md](../data-model.md)

**Contract policy.** This feature adds **no new commands and no new flags**. It changes
observable behavior in three places and preserves the shared exit-code convention (0
success, 1 `EXIT_ERROR`, 101 `EXIT_SCHEMA_MISMATCH`, 102 `EXIT_CONFIG_ERROR`).
`tests/contract/test_cli.py` is extended **additively**; existing assertions stay green.

---

## 1. `ensure-base` — graybeard missing-component warning (first-line output)

**Trigger** (FR-009): config has `fetch.source = true` AND
`resolution.max_source_dep_depth = -1` (graybeard) AND the union of the config's trixie
`archive.source` components is missing any of `main`, `contrib`, `non-free`,
`non-free-firmware`. Otherwise no warning is emitted (including developer / regular_user
configs and complete graybeard configs).

**Text mode** — the warning is the **first** stdout lines, before the summary:

```text
$ package_mgmt ensure-base
WARNING: graybeard configuration (source = true, max_source_dep_depth = -1) is set
but the trixie sources are missing component(s): contrib, non-free, non-free-firmware.
The full-rebuild promise cannot hold for packages in those components — add them to
[[archive.source]] to restore complete source/build-tool coverage.
ISO image:        debian-trixie-DVD-1.iso (trixie: main)
Binary packages:  ...
...
```

The warning is advisory and non-blocking (exit code unchanged). It is the first entry of
the run's structured warnings (`EnsureBaseStats.warnings`), so it is present in any
machine-readable rendering of the run result.

**Contract tests** (additive): text mode — warning precedes the summary and names the
missing components; complete graybeard config → no such warning; developer config
(`source = true`, depth `2`) → no such warning; regular_user (`source = false`) → no such
warning.

---

## 2. `align-repo` — config-driven baseline (verdict semantics)

**No CLI surface change.** The baseline release/component is derived from
`config.sources[0]` instead of a hard-coded `trixie/main` (FR-006). The observable change
is the verdict on non-baseline configs:

| Config | Before | After |
|--------|--------|-------|
| trixie, first component `main` (standard) | correct verdict | unchanged (identical verdict) |
| non-trixie first source (e.g. `bookworm`) | wrong baseline's verdict | correct baseline from the configured source |
| trixie, first component not `main` (e.g. `contrib`) | wrong baseline | correct baseline from `sources[0].components[0]` |
| no cached index for the configured baseline | degrades to "everything unmet" | validates against the actual configured baseline (meaningful verdict) |

`BASELINE_LABEL` in text output reflects the configured source (e.g.
`bookworm/main (verified, read-only)`).

**Contract tests** (additive): a config-driven variant test asserts the correct verdict on
a non-trixie / first-component-not-`main` configuration, and that the standard trixie/main
verdict is unchanged.

---

## 3. `download` — no spurious failure after a local import

After any `import-deb` / `package-stack` (a `local` component row), `download` must exit
**0** with **zero command errors** even when nothing is pending (FR-003). The bogus
"Cached index missing for <release>/local" command error is eliminated; `local` records
stay `verified`.

```text
$ package_mgmt download
# expected: exit 0, no "cached index missing for trixie/local" command error
```

**Contract tests** (additive, engine-level): import a `local` deb → `download` → zero
command errors, local record remains verified.

---

## 4. Unchanged contracts (explicitly preserved)

- **Exit codes**: 0 / 1 / 101 / 102 unchanged across all commands (R-1's install fix does
  not change exit-code behavior — it removes the `ModuleNotFoundError` crash that never
  reached a clean exit code).
- **`download` / `handoff` / `sync` / `verify` output shapes**: unchanged except the
  `local`-row accounting fixes above (which remove spurious errors/warnings, not shapes).
- **`--format text|json`**: unchanged; the graybeard warning lives in the run's structured
  warnings (additive) and as first-line text output.
