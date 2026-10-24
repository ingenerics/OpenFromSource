# OpenFromSource

**Build your own Debian supply chain: a verifiable, offline-installable package cache. 
With (optional) sources included, you don't have to trust what you can't rebuild.**

OpenFromSource (aka `package_mgmt`) turns the public Debian archive into a 
curated, self-contained cache: it resolves complete dependency closures, downloads 
and hash-verifies every payload, and hands the result to offline machines as a
**signed apt repository on a USB stick**. No version drift across your machines,
no half-written caches, no unexplained binaries — if it's in the cache, it's
consistent, and the offline system can prove it.

> **Status: alpha.** Feature-complete against its spec and [`reviewed`](review/), 
> but interfaces, TOML keys, and operator scripts may still change without notice. 
> Alpha software — expect bugs; no warranty of any kind (see [`LICENSE`](LICENSE)).


## What it does

```
 Networked machine (Administrator)                Offline machine (Operator)
┌───────────────────────────────────────┐       ┌────────────────────────────┐
│ package_mgmt init                     │       │                            │
│ package_mgmt add --file requested.txt │       │                            │
│ package_mgmt download                 │       │                            │
│ package_mgmt verify                   │       │                            │
│ package_mgmt systems add mini-pc      │       │                            │
│ package_mgmt handoff --system mini-pc ├──USB─►│ sudo handoff/bootstrap.sh  │
│ package_mgmt sync    --system mini-pc ├──────►│ sudo diff/apply.sh (later) │
└───────────────────────────────────────┘       └────────────────────────────┘
                                                   apt update / apt install
```

- **init** creates the cache (a `pool/` of payloads + a sqlite database of metadata).
- **add** resolves packages with their *complete, never-truncated* runtime
  closure — version-constraint-aware, virtual packages included.
- **download** fetches every payload, sha256-verified against the signed
  archive index, atomically (`*.part` → rename); interrupted runs converge.
- **verify** gates the cache: is every runtime dependency present at a
  satisfying version, every source linked, every build tool accounted for?
- **handoff** derives a self-contained offline apt repo: filtered `Packages.gz`
  indexes, `Release` signed by a cache-local GPG key, and a `bootstrap.sh`
  that configures the offline machine in one step.
- **sync** ships *minimal diffs* to a system you've already handed off to, with
  a manifest the operator's `apply.sh` checksums before merging.

Indexes fetched from Debian are verified against the archive's signed
`InRelease` (a pinned keyring, no TOFU); everything the operator consumes is
signed again by a key that lives in the cache. Trust is established on the way
in and re-asserted on the way out.

<details>
<summary>Key-handling posture</summary>

The handoff payload itself carries no secret key material (only the public 
`offline-cache.gpg`). However, the handoff references the cache above it 
(its `pool`/`source` links point back into `package_mgmt/binary/`). 
This means the transported unit is the whole cache root, so a plain copy of
 `package_mgmt/` also carries the keyring (which lives at 
 `package_mgmt/.gnupg-handoff/`). Exclude `.gnupg-handoff/` from operator 
 media unless the operator is a trusted relay that must re-sign.

</details>

## Personas

| Persona | Where | What they do |
|---|---|---|
| **Administrator** | Networked | Builds and curates the cache: `init`, `add`, `ensure-base`, `download`, `verify`, `handoff`, `sync` |
| **Operator** | Offline / air-gapped | Consumes it: runs `bootstrap.sh` once, then plain `apt install`; applies later diffs with `apply.sh` |

## Profiles

Three drop-in TOML presets showcase the intended usage — they are **plain
config data, not code** ([`docs/profiles/`](docs/profiles/)):

| Profile | Runtime closure | Sources | Build tools | For |
|---|---|---|---|---|
| `regular_user` | full | — | — | Small cache; install-only users |
| `developer` | full | depth-limited | depth-limited | Rebuild the packages you care about |
| `graybeard` | full | **everything, recursively** | **everything** | Full rebuild from source — *turtles all the way down* |

The runtime closure is never capped (that's what keeps the cache installable);
only the source/build-tool support is bounded, via one setting
(`max_source_dep_depth = -1 | 0 | N`). The shipped default is `regular_user`,
so a fresh install can't pull 30 GB of sources by mistake.

## What else is it good for?

**Set up once, deploy everywhere.** The air-gapped use case is the obvious one,
but the same properties pay off elsewhere:

- Curate and validate your package set once, optionally modify the sources on
  the way in, and approve it for use across your (critical) infrastructure.
- The target system doesn't *have* to stay air-gapped: after bootstrapping a
  common baseline, you can point selected systems back at the real Debian repos
  (e.g. for security updates) where it makes sense.
- *Not implemented*: host the generated repo on a local network share and
  fan out to every connected system. The handoff is already a valid apt repo.

The point is, you're in control. You don't *have* to deal with version drift on
all systems. Do your homework once, get everything you need into the cache, and
then tear down and rebuild systems as you see fit — with consistent behavior.

## Read more

A tutorial a day keeps the `docs` away

1. [Creating an offline cache to hand over on a USB stick](https://github.com/ingenerics/OpenFromSource.Tutorials/blob/main/tutorials/1_creating_an_offline_cache.md)
2. [A newer kernel from backports](https://github.com/ingenerics/OpenFromSource.Tutorials/blob/main/tutorials/2_kernel_from_backports.md)
3. [Hyprland from backports, and the version-drift trap](https://github.com/ingenerics/OpenFromSource.Tutorials/blob/main/tutorials/3_hyprland_version_drift.md)
4. [An offline NAS installed from a USB stick](https://github.com/ingenerics/OpenFromSource.Tutorials/blob/main/tutorials/4_simplest_ewaste_nas.md)
5. [Ensuring the full base set from the installer DVD](https://github.com/ingenerics/OpenFromSource.Tutorials/blob/main/tutorials/5_ensure_base_packages.md)
6. [Building Hyprland from source](https://github.com/ingenerics/OpenFromSource.Tutorials/blob/main/tutorials/6_hyprland_from_source.md)
7. [Importing Hyprland into the cache](https://github.com/ingenerics/OpenFromSource.Tutorials/blob/main/tutorials/7_import_hyprland_stack.md)

Documentation *accumulated over time (read: your output may vary)*

1. [Quickstart & how-to](docs/001-quickstart-howto.md)
2. [Build-dep depth setting](docs/002-build-dep-depth.md)
3. [ensure-base (DVD-1 ingestion)](docs/003-ensure-base.md)
4. [The version drift trap](docs/004-version-drift-trap.md)

Other resources
- Feature specs — [specs/](specs/)
- Profile presets — [docs/profiles/](docs/profiles/)
- Reviews in chronological order — [review/](review/)
- Kimi's top alternative use cases *(not implemented)* — [high_value_alternative_use_cases](review/05_alpha_final_kimi_k3/high_value_alternative_use_cases.md)

## Spec-Driven Development

All code (up until alpha release) is generated by [deepseek-ai/DeepSeek-V4-Flash-0731](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731).

This project is built with SDD ([Spec Kit](https://github.com/github/spec-kit)): every feature exists first as a
spec, plan and task list, then as code. The complete decision history is in the
repo — [`specs/`](specs/) holds 25 feature specs (from base infrastructure and
dependency resolution to the alpha-blocker and polish cycles), and
[`review/`](review/) holds the consolidated pre-release review corpus and its
fix ledger. Quality gates: **976 tests, green under both documented runners**
(`pytest` *and* `python -m unittest`), `ruff` clean, contracts pinned per
feature.

The framework files in [`.specify/`](.specify/) and the `speckit.*` commands in
[`.opencode/commands/`](.opencode/commands/) are from Spec Kit (© GitHub, Inc.,
MIT-licensed, slightly modified); everything else is GPL-3.0 — see
[`NOTICE.md`](NOTICE.md).

### …and as a side effect: your (local) agent can ease your onboarding

Wonder why a feature was implemented a certain way or what the motivation was
behind a decision? Let your agent digest the `specs` (`docs` and `tutorials`)
and 9 times out of 10 you will have your answer.

A real codebase with real history, small enough to read, tricky enough to matter. 

### …leave me a review

I ran the same one-shot review prompt against different models and included the results:

- Kimi K3 (API cost $15) → [review](review/02_alpha_kimi_k3/ALPHA_REVIEW.md)
- GLM 5.3 (API cost $3.28) → [review](review/03_alpha_glm_5_3/ALPHA_REVIEW.md)
- Consolidated fix ledger → [review/04_alpha_summary/](review/04_alpha_summary/)
- Latest full re-review → [review](review/06_alpha_finally_kimi_k3/ALPHA_REVIEW.md)

The follow up changes and/or fixes were again done by DeepSeek-V4-Flash-0731.

Also a good example of why there is no "best" model — only a best model
for a specific use case, at a certain point in time. When it comes to the 
alpha reviews I preferred Kimi: cost aside, it was the one that spotted the 
`Pre-Depends` parsing bug that led to a genuinely broken cache.

<details>
<summary>The one-shot prompt used for the reviews</summary>

```
Analyze the current project. It is implemented using Spec Driven Development,
which also means the history (of the decision making) is included in the
project as markdown files. We are reaching the functionality required for the
alpha release. A thorough review is desired, both from technical and functional
point of view. First and foremost, any lingering bugs should be identified. At
a bare minimum, the packages should be resolved correctly for the 3 profiles
and each setup should result in a stable fully installable cache. Do not verify
the workflows through project command execution, instead rely only on file
inspection. Report back by writing a comprehensive markdown file at the root of
the project.
```

</details>

## Before you ask

Ubuntu? Nah, they just want you to use snaps ;)
