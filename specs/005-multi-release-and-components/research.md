# Research: Multi-Release and Multi-Component Support

## 1. Debian Archive Repository Layout

The standard Debian apt repository layout is:

```
pool/{component}/{prefix}/{package}_{version}_{arch}.deb
dists/{release}/{component}/binary-{arch}/Packages.gz
dists/{release}/{component}/source/Sources.gz
```

Key properties:
- `release` is the suite (e.g., `trixie`, `trixie-backports`, `trixie-security`)
- `component` is the archive area (`main`, `contrib`, `non-free`, `non-free-firmware`)
- `pool/` includes the component prefix: `pool/{component}/{prefix}/...`
- Multiple suites share the same `pool/` — packages from `trixie/main` and `trixie-backports/main` both live under `pool/main/`
- `dists/` separates metadata by suite and component; `pool/` is organized by component only

**Decision**: The cache mirrors this exactly. Each configured source produces `dists/{release}/{component}/` directories. All `.deb` files go into the shared `pool/{component}/` tree.

## 2. Pool Organization with Multiple Versions

Standard Debian pool naming convention:
- `pool/main/libd/libdrm2/libdrm2_2.4.110-1_amd64.deb`
- `pool/main/libd/libdrm2/libdrm2_2.4.120-1_amd64.deb`

Multiple versions coexist naturally — they have different filenames. Apt resolves which one to use based on the `Packages.gz` metadata in `dists/`.

**Decision**: Follow standard Debian pool layout. No special handling needed for multiple versions.

## 3. Config Format: Single Source vs Multiple Sources

The current config has scalar `release` and `component`. Two approaches for multi-source:

**Approach A (list of tables)**:
```toml
[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie"
components = ["main", "contrib", "non-free"]

[[archive.source]]
mirror = "http://deb.debian.org/debian"
release = "trixie-backports"
components = ["main"]
```

**Approach B (nested config)**:
```toml
[archive]
mirror = "http://deb.debian.org/debian"

[archive.suites.trixie]
components = ["main", "contrib", "non-free"]

[archive.suites.trixie-backports]
components = ["main"]
```

**Decision**: Approach A (list of tables). It is explicit, extensible, and clearly separates each source's identity. Backward compatibility is maintained by auto-converting the old scalar format to a single-entry list.

## 4. Status Flag: Overwrite vs Append

**Approach A (Overwrite on re-resolution)**:
- Pros: Fewer rows, simpler queries
- Cons: No history, no operator fallback, opaque debugging

**Approach B (Append with status)**:
- Pros: Full version history, operator can fall back, transparent debugging, supports offline rebuild scenario
- Cons: More rows, need to maintain the "at most one current" invariant

**Decision**: Approach B. The operator may need to rebuild against a superseded version if the current one causes issues. Append-only preserves options for the isolated environment.

## 5. Schema Version During Development

The current codebase has `SCHEMA_VERSION = 1`. No stable release has been made.

**Decision**: Keep `SCHEMA_VERSION = 1` during development. All schema changes are additive modifications to the DDL. Developers recreate their databases as needed. A formal migration mechanism will be introduced when the first stable release is prepared.

## 6. Suite Priority for Resolution

When the same package exists in multiple suites at different versions, the resolver needs deterministic selection rules.

**Decision**: Highest version wins. If multiple suites offer the same version, priority order is: `main` > `contrib` > `non-free` > `non-free-firmware`. User-configured suites (backports, updates) are ordered by their position in the config file, after the base components. This matches standard Debian convention where `main` is the primary source.
