# Contract: Version Manifest File Format

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md) · **Data model**: [data-model.md](../data-model.md)

**Contract policy.** The version manifest is a **machine-checkable file artifact**
(FR-005, Q5) — git-tracked, deterministic, and the single source of truth driving
acquisition and the build. It is not a database record. It is **produced by
`package_mgmt align-repo --repo-dir DIR` on success** (Q7) and consumed by the documented
acquisition/build procedures (docs/005).

---

## Manifest file (`manifests/hyprland-<era>.toml`)

TOML (same format family as the project's config), UTF-8, LF endings. Example for a
hypothetical era (the real era/tags/commits are an implementation deliverable):

```toml
# manifests/hyprland-0.45.toml
era = "0.45"
chosen = "2026-10-20"
reason = "Newest Hyprland era whose declared build/runtime deps are all satisfied by the
frozen trixie tree (wayland 1.23.1, libinput 1.28.1, wayland-protocols 1.44, gcc-14
14.2.0); the newer CMake era requires glaze (FetchContent, unpackaged) and
libinput>=1.29 / wayland-protocols>=1.49 / xkbcommon>=1.11.0."

[[repo]]
name = "Hyprland"
url = "https://github.com/hyprwm/Hyprland"
tag = "v0.45.0"
commit = "0123456789abcdef0123456789abcdef01234567"
build = "meson"
reason = "Chosen era anchor; v0.45.0 aligns with trixie."

[[repo]]
name = "hyprwayland-scanner"
url = "https://github.com/hyprwm/hyprwayland-scanner"
tag = "v0.4.4"
commit = "89abcdef0123456789abcdef0123456789abcdef"
build = "meson"
reason = "Meets Hyprland/hyprpaper/hyprlock/hypridle scanner minimums."
```

## Field contract

| Key | Required | Type | Constraint |
|-----|----------|------|------------|
| `era` | yes | string | non-empty; stable label for the era |
| `chosen` | yes | date (YYYY-MM-DD) | ISO 8601 date |
| `reason` | yes | string | selection rationale (FR-005) |
| `[[repo]].name` | yes | string | unique; one of the 12 in-scope repos (Q4); the 4 out-of-scope tools must be absent |
| `[[repo]].url` | yes | string | git URL |
| `[[repo]].tag` | yes | string | non-empty tag |
| `[[repo]].commit` | yes | string | full 40-hex SHA (the pin, FR-006/009) |
| `[[repo]].build` | yes | enum `meson` \| `cmake` | build-metadata format of the ref (research §1) |
| `[[repo]].reason` | yes | string | per-ref rationale |

## Validation rules (enforced by `align-repo` when producing)

- All 12 required fields present for every repo; no extra repos.
- The 12 in-scope repo names are exactly present (missing/extra repo = load failure).
- `commit` is a full 40-hex SHA.
- `build` ∈ {`meson`, `cmake`}.
- `chosen` parses as ISO date.
- A manifest that cannot be produced/validated is reported as a load/validation error
  (contract: [align-repo.md](align-repo.md)), never a partial result.

## Assertions

- The manifest `align-repo` writes is readable and validates (all rules above) — a
  malformed manifest is an error with a non-zero exit, not a partial alignment result.
- `repos/<name>` `HEAD` equals `[[repo]].commit` for every repo after acquisition (US2);
  a mismatch is a failed acquisition, and `align-repo` re-deriving from a mismatched tree
  fails loudly (spec edge case "Corrupt/incomplete source acquisition").
- The manifest is re-runnable: re-running `align-repo` against a changed cache or tree
  only ever fails (or selects an older era) if alignment no longer holds (FR-013).
