# Data Model: Apt VersionHash Fidelity

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md)

**Schema freeze (spec 010/011).** No database schema changes, no migrations, no new
columns or tables. The feature adds fields to the in-memory `BinaryPackage` model and
defines the emitted-stanza ↔ control-field mapping that guarantees apt VersionHash parity.

---

## 1. `BinaryPackage` model additions (`lib/model/package.py`)

The immutable (`frozen=True`) `BinaryPackage` dataclass gains four fields plus the
distinct original-`Depends` field, all plain `str | None` metadata:

| Field | Meaning | Populated by |
|-------|---------|--------------|
| `depends_field` | The original `Depends` field text from the control/index — kept separate from `depends_raw` (which merges `Pre-Depends` targets in for the resolver's closure walk). `None` when the control has no `Depends` field. | `load_binary_index` (`para.get("Depends")`), `_local_binary_entry` (`.deb` control) |
| `installed_size` | The `Installed-Size` field text | `load_binary_index`, `_local_binary_entry` |
| `conflicts_raw` | The `Conflicts` field text | `load_binary_index`, `_local_binary_entry` |
| `breaks_raw` | The `Breaks` field text | `load_binary_index`, `_local_binary_entry` |
| `replaces_raw` | The `Replaces` field text | `load_binary_index`, `_local_binary_entry` |

**Validation rules (FR-001/002/003)**: `depends_field` is `None` when the control/index
has no `Depends` field — it must never be synthesised from `depends_raw`. The four
VersionHash fields must be carried verbatim; `None` means "absent from the stanza", never
"reconstruct a default".

---

## 2. VersionHash-relevant field mapping (emitted stanza ↔ control)

The contract that guarantees parity — `_paragraph` (`handoff.py`) emits exactly these
fields, in this order, verbatim from the package's control record:

| # | Emitted field | Source | Absence |
|---|---------------|--------|---------|
| 1 | `Installed-Size` | archive index / `.deb` control | not emitted |
| 2 | `Depends` | **`depends_field` only** (never `depends_raw`) | not emitted |
| 3 | `Pre-Depends` | control/index `Pre-Depends` | not emitted |
| 4 | `Conflicts` | control/index `Conflicts` | not emitted |
| 5 | `Breaks` | control/index `Breaks` | not emitted |
| 6 | `Replaces` | control/index `Replaces` | not emitted |

**Constraint (FR-002)**: `Depends` is emitted **only** from `depends_field`. The resolver's
merged `depends_raw` (`Depends` + deduplicated `Pre-Depends`) is internal to the closure
walk and must never appear in a generated stanza.

**Key relationship**: the emitted stanza's VersionHash over `{Installed-Size, Depends,
Pre-Depends, Conflicts, Breaks, Replaces}` (in that order; whitespace and `=` stripped,
`0:` epochs removed, case folded) MUST equal the VersionHash of the dpkg-status record
that installing the shipped `.deb` produces. Equal version + equal hash → merged (not
upgradable); equal version + different hash → separate version objects ("upgradable from
X to X").

---

## 3. Field provenance by component

| Component | VersionHash fields come from | Fallback |
|-----------|------------------------------|----------|
| `main` / `contrib` / `non-free` / backports | cached archive `Packages` index (`load_binary_index`) | none (index is authoritative) |
| `local` (readable payload) | shipped `.deb` control (`_read_deb_control`, `debian.debfile.DebFile`) | none (control is authoritative; a missing `Depends` stays missing) |
| `local` (unreadable payload) | DB-only reconstruction from the VersionHash field values captured and stored at load time (never the merged `depends_raw` view) | parity-preserving fallback — the reconstruction emits the stored original field values so the stanza remains parity-correct; generation must not fail, and a per-package warning flags the affected package (FR-006) |

---

## 4. Derived entity: the VersionHash contract

Not a database entity — the documented algorithm apt applies (research §1). It is the
acceptance oracle for every emitted stanza and the knowledge this feature preserves
in-project:

- Fields, in order: `Installed-Size`, `Depends`, `Pre-Depends`, `Conflicts`, `Breaks`,
  `Replaces`.
- Hash: DJB-2-style, start `5381`, `hash = 33 * hash + tolower(c)`.
- Normalisation: skip whitespace and `=`; strip `0:` epochs; fold case.
- Merge semantics: same version + same hash merge; same version + different hash stay
  separate → false "upgradable".

---

## 5. Pool payload (filesystem)

No layout change. The `.deb` payloads under `{base}/binary/pool/…` are the parity
reference for `local` records (their control files) and are installed verbatim by the
operator, producing the dpkg-status record the emitted stanza must match.

---

## 6. Handoff/diff serving (filesystem + index)

No structural change. The same `Packages.gz` files are emitted; only the per-stanza
fields change to guarantee VersionHash parity. Both `handoff` and `diff`/`sync` channels
share `_paragraph`, so the contract holds uniformly (FR-005).

---

## State transitions

The fix introduces no state machine — it changes the *content* of emitted metadata, not
any lifecycle. The only conditional is per-record:

```
[control readable] -> emit VersionHash fields verbatim (depends_field only for Depends)
[control unreadable (local)] -> DB-only reconstruction, generation continues (FR-006)
```

A failure to read a `local` payload never aborts generation (non-blocking); the fallback still emits a parity-correct stanza from the stored field values, and a per-package warning flags the affected package so the Administrator knows the payload is missing/corrupt (FR-006).
