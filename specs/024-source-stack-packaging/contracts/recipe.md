# Contract: Packaging Recipe

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md) · **Data model**: [data-model.md](../data-model.md)

The declarative, per-stack input of `package-stack` (FR-001/FR-011). TOML, tracked in the
repo under `recipes/<stack>.toml`; parsed with the stdlib `tomllib`. It is the **entire**
stack-specific knowledge the generic tool consumes — the boundary between "generic tool"
and "per-stack knowledge".

```toml
[stack]
name = "hyprland"
repo_dir = "repos"
source_epoch = "2026-10-20"

[[component]]
name = "hyprland-protocols"
binary_package = "hyprland-protocols"
source_tree = "hyprland-protocols"
version = "0.6.0"
revision = 1
build_system = "meson"
architecture = "all"
description = "Hyprland Wayland protocol headers"
section = "libdevel"
homepage = "https://github.com/hyprwm/hyprland-protocols"

[[component]]
name = "sdbus-c++"
binary_package = "libsdbus-c++"
source_tree = "xdg-desktop-portal-hyprland/subprojects/sdbus-cpp"
version = "2.0.0"
revision = 1
build_system = "cmake"
architecture = "amd64"
description = "High-level C++ D-Bus library (source-built dependency)"
section = "libs"
homepage = "https://github.com/Kistler-Group/sdbus-cpp"

[[component]]
name = "hypridle"
binary_package = "hypridle"
source_tree = "hypridle"
version = "0.2.0"
revision = 1
build_system = "cmake"
architecture = "amd64"
description = "Hyprland idle daemon"
section = "x11"
homepage = "https://github.com/hyprwm/hypridle"
explicit_depends = ["libsdbus-c++ (>= 2.0.0)"]
```

## Fields

`[stack]`:
| Field | Required | Default | Meaning |
|-------|----------|---------|---------|
| `name` | yes | — | stack name (report/JSON) |
| `repo_dir` | no | `repos` | directory holding the pinned source trees |
| `source_epoch` | no | recipe date | fixed `SOURCE_DATE_EPOCH` for deterministic artifacts (FR-009) |

`[[component]]` (repeatable; at least one):
| Field | Required | Default | Meaning |
|-------|----------|---------|---------|
| `name` | yes | — | unique component name (also the staged-dir name) |
| `binary_package` | yes | — | binary package name (unique in the set) |
| `source_tree` | yes | — | path under `repo_dir` (may be a vendored subproject path, e.g. `sdbus-c++`) |
| `version` | yes | — | upstream version in Debian notation |
| `revision` | no | `1` | Debian revision (`<version>-<revision>`); bump on re-package (FR-010) |
| `build_system` | yes | — | `"meson"` or `"cmake"` — drives the generated `debian/rules` (FR-004) |
| `architecture` | no | `amd64` | control `Architecture`; `all` for data-only components (e.g. `wayland-protocols`) |
| `description` | yes | — | control `Description` (FR-002) |
| `maintainer` | no | `OpenFromSource <admin_email>` (project `[metadata] admin_email`, default `noreply@example.com`) | control `Maintainer` |
| `section` | no | `misc` | control `Section` |
| `homepage` | no | — | control `Homepage` |
| `explicit_depends` | no | `[]` | raw Debian relation strings merged into the derived `Depends` (FR-003) |
| `build_depends` | no | `[]` | merged into the generated source control's `Build-Depends` |
| `source_format` | no | `3.0 (quilt)` | source package format for `dpkg-source` (FR-004) |

## Validation rules

- `name` and `binary_package` are unique within the recipe.
- `source_tree` must exist under `repo_dir`; a missing tree is a loud error naming the
  component (FR-014).
- `version` must be a valid Debian version (the same check `import_local_deb` applies).
- Every dependency a component declares (explicit or derived) on another **component**
  must resolve to a `name` in the recipe — a missing source-built dependency fails loudly
  naming it (spec edge case; FR-006).
- The dependency graph induced by `explicit_depends` must be acyclic; a cycle is a loud
  error.
- Unknown fields are rejected (a typo must not silently change behavior).
