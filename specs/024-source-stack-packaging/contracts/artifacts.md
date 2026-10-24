# Contract: Generated `.deb` / `.dsc` Artifacts

**Feature**: [spec.md](../spec.md) · **Plan**: [plan.md](../plan.md) · **Data model**: [data-model.md](../data-model.md)

The artifacts `package-stack` generates must satisfy two contracts: (a) the existing
`import_local_deb` validation (they are imported through the spec 021 path unchanged —
see [../research.md](../research.md) §1), and (b) the feature's own determinism and
`Depends`-derivation requirements (FR-002/003/004/009).

---

## 1. Binary package (`.deb`)

Built by `debgen` from the staged install directory with standard Debian tooling
(`dpkg-deb`, `dpkg-shlibdeps`).

- **Control completeness (FR-002)**: `Package`, `Version` (`<version>-<revision>`),
  `Architecture`, `Section`, `Priority: optional`, `Maintainer`, `Homepage`,
  `Description`, `Installed-Size` (computed), and `Depends` — all present and parseable
  by `debian.debfile.DebFile` (the same parser `import_local_deb` uses).
- **Payload = staged dir**: the `.deb`'s data root is exactly the component's staged
  install directory (spec Assumption "file ownership decided by the staged install") —
  non-`/usr/bin` files (portal service files, sessions, wallpapers, XML protocol data)
  are owned by the component that staged them; nothing is missed or mis-attributed.
- **`Depends` derivation (FR-003)**: derived from the staged binaries' linked runtime
  libraries via `dpkg-shlibdeps`; recipe `explicit_depends` are merged in; every library
  `dpkg-shlibdeps` cannot attribute to a package is **reported by name** (never silently
  dropped — the recipe must declare it).
- **Determinism (FR-009)**: `SOURCE_DATE_EPOCH` fixed; tar member mtimes and ordering
  fixed; `dpkg-deb --root-owner-group`. Two runs over identical inputs produce
  byte-identical `.deb` (asserted in unit tests).

```text
# assert (unit): determinism
build_binary_package(stage_a) bytes == build_binary_package(stage_a) bytes
# assert (unit): unattributed library reported, import still requires it declared
unattributed == ["libfoo-companion.so.0"]  and  import fails until explicit_depends adds it
```

---

## 2. Source package (`.dsc` + referenced files)

Built by `srcgen` from the pinned source tree at the recorded version, via a generated
minimal `debian/` wrapper and `dpkg-source` (FR-004).

- **Complete set**: exactly one `.dsc` plus every file it references (orig + Debian
  tarballs) — the exact `--source <dir>` shape `import_local_deb._validate_source_set`
  requires (every referenced file present and checksum-matching; a bare `.dsc` is
  rejected).
- **Rebuildable (FR-004/FR-008)**: `dpkg-source -x` of the generated set succeeds
  offline; the recorded rebuild (`debian/rules` driving the component's own meson/cmake —
  the same invocations docs/005 §2.3 documents) reproduces the shipped binary
  (US4/quickstart).
- **Determinism (FR-009)**: fixed `SOURCE_DATE_EPOCH`, fixed tar mtimes/order, sorted
  control/changelog generation — two runs over identical trees + recipe produce
  byte-identical `.dsc` + files (asserted in unit tests).

---

## 3. Version / revision semantics (FR-010)

- Default version is `<version>-1`; the recipe can override the revision.
- Re-packaging the same upstream version at changed source/recipe bumps the revision
  (`<version>-2`, ...) so `apt upgrade` on the operator sees the new package.
- Re-importing an identical pair is idempotent (existing `ON CONFLICT` path — no
  duplicate rows, payload verified once).

---

## 4. Validation entry point

Both artifact kinds are validated by the **same code that validates hand-built ones**
(`import_local_deb`): a generated artifact that fails its contract (unparseable control,
invalid version, incomplete/checksum-mismatched source set) fails the component loudly
with zero changes — the command never registers a partial or unverifiable pair (FR-014).

---

## 5. Operator source index (`source/Sources` for `local`, Q3/FR-008)

A file artifact the handoff generates from the DB `sources` rows so the operator can
retrieve imported sources offline with `apt-get source` (spec FR-008/US4/SC-004; the
handoff today emits only binary `Packages[.gz]` — no deb-src index anywhere).

- **Location/shape**: `dists/<release>/local/source/Sources` + `Sources.gz`, one
  `deb-src` paragraph per verified `component='local'` source row: `Package`
  (source name), `Version`, `Directory` (relative `source/pool/...` path), and
  `Files:`/`Checksums-Sha256:`/`Size` matching the imported `.dsc` (the same fields apt
  uses to fetch + verify the source payloads).
- **Auth/consistency**: hash-listed in the handoff `Release` and covered by the existing
  `Release.gpg` signing alongside `Packages` — the source channel trusts the same
  Release as the binary channel; no separate trust path.
- **Operator config**: the handoff bootstrap writes a `deb-src` line for `local` to
  `/etc/apt/sources.list.d/openfromsource-cache.list` (config the handoff emits — no new
  operator tooling, FR-012).
- **Determinism/atomicity**: paragraphs serialized in stable order (fixed formatting)
  from `verified` rows only; generation is atomic with the existing `Release`/`Release.gpg`
  regeneration, so a failure leaves the previous signed index in place.

```text
# assert (integration): operator source channel
handoff --system <sys>                      # emits local/source/Sources[.gz] + signed Release
apt-get source <component> (offline)        # resolves from local/source/Sources, dpkg-source -x succeeds
```
