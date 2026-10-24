# Quickstart: Consolidation Fixes

Validation scenarios for the spec 007 behaviors. Uses the existing `vifm` / `hyprland` examples.

## 1. Build-Tool Resolution Completeness

```bash
package_mgmt add gcc
# Deep build-tool chains must resolve regardless of max_build_dep_report_depth
sqlite3 /var/cache/package_mgmt/package_mgmt.db \
  "SELECT MAX(build_dep_depth) FROM source_build_deps"
# Expect a depth > 2 if gcc's chain is deep enough; with default config links at
# depth 3+ must exist (previously they never were created).
```

## 2. Circular Dependency Reporting

Construct a synthetic cycle in a scratch database using a test index (see
`tests/unit/test_list.py` patterns), then:

```bash
package_mgmt add a
package_mgmt verify --graph
# Expect: "Circular dependencies: 1" with the connecting packages listed,
# and exit code non-zero.
```

## 3. Verify Current Tree Only

```bash
package_mgmt add vifm
package_mgmt add hyprland --source trixie-backports   # supersedes shared libs
package_mgmt verify --graph
# Total binaries counts only status='current' rows; no false COMPLETE.
package_mgmt list --status superseded                 # history remains visible
```

## 4. Missing Build Tool Surfaces

Temporarily configure a source set whose archive lacks a build tool referenced
by a package's Build-Depends, then:

```bash
package_mgmt add vifm
# add exits non-zero; summary lists the unresolved build tool
package_mgmt verify --graph
# Expect: "Missing build tools: 1" (computed, not hardcoded), Build Status INCOMPLETE
```

## 5. show --deps

```bash
package_mgmt show vifm --deps
# Tables, then a dependency tree identical to: package_mgmt list --deps vifm
package_mgmt show vifm --deps --format json | python3 -c \
  "import json,sys; d=json.load(sys.stdin); assert 'tables' in d and 'dependency_tree' in d"
```

## 6. Constraint-Aware Selection

With `trixie` (libdrm 2.4.110) and `trixie-backports` (libdrm 2.4.120) configured:

```bash
package_mgmt add hyprland --source trixie-backports
# Resolver picks versions satisfying constraints, even when a non-satisfying
# higher version exists in the primary source.
sqlite3 /var/cache/package_mgmt/package_mgmt.db \
  "SELECT package_name, version, status FROM binaries WHERE package_name LIKE 'libdrm%' ORDER BY id"
```

## 7. Config Validation

```toml
# project.local.toml — overlapping components must be rejected
[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie"
components = ["main"]

[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie"
components = ["main", "contrib"]
```

```bash
package_mgmt config
# Expect: "Duplicate (release, component) pair: (trixie, main)" — exit 102
```

## 8. Provides Repopulation

```bash
package_mgmt init
sqlite3 /var/cache/package_mgmt/package_mgmt.db "SELECT COUNT(*) FROM provides"  # snapshot A
package_mgmt init
sqlite3 /var/cache/package_mgmt/package_mgmt.db "SELECT COUNT(*) FROM provides"  # snapshot B == A
# Stale rows (e.g. from a removed package) no longer persist across inits.
```

## 9. Dev Bootstrap

```bash
cd OpenFromSource
export PYTHONPATH=src
python3 -m unittest discover -s tests         # full suite green (system python3-debian)
# Optional convenience tooling:
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```
