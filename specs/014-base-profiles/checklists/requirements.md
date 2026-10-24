# Specification Quality Checklist: Base Profiles

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Feature**: [spec.md](../spec.md)

## Content Quality

- [X] No implementation details (languages, frameworks, APIs)
- [X] Focused on user value and business needs
- [X] Written for non-technical stakeholders
- [X] All mandatory sections completed

## Requirement Completeness

- [X] No [NEEDS CLARIFICATION] markers remain
- [X] Requirements are testable and unambiguous
- [X] Success criteria are measurable
- [X] Success criteria are technology-agnostic (no implementation details)
- [X] All acceptance scenarios are defined
- [X] Edge cases are identified
- [X] Scope is clearly bounded
- [X] Dependencies and assumptions identified

## Feature Readiness

- [X] All functional requirements have clear acceptance criteria
- [X] User scenarios cover primary flows
- [X] Feature meets measurable outcomes defined in Success Criteria
- [X] No implementation details leak into specification

## Notes

- Items marked incomplete require spec updates before `/speckit.clarify` or `/speckit.plan`
- Design revision: the setting is `max_source_dep_depth` — runtime binaries are never capped (version-drift rationale documented in spec Assumptions and research Decision 1); sources + build tools are capped together so every fetched source is buildable.
- Depth-value convention: both `max_source_dep_depth` and `max_build_dep_report_depth` use `-1` = unlimited, `0` = none, positive = levels (replaces the old "`0` = unlimited"). `project.defaults.toml` ships the regular_user presets (`source = false`, `max_source_dep_depth = 0`). Regression coverage for both settings at both `0` and `-1` is mandatory (spec SC-006; plan's mandatory regression test matrix).
- Defaults alignment: the `ProjectConfig` dataclass defaults match the shipped presets exactly — `fetch_source = False`, `max_source_dep_depth = 0` — one default story, unconfigured == regular_user; `-1` (unlimited) is an explicit opt-in. Existing tests relying on the old `fetch_source = True` default (e.g. `test_cli.py` `test_json_format_structure`, `test_ensure_base._scratch_config`) need an explicit override.
- Verify gate: no schema change — FR-015 weakened. `verify --graph` reads the configured depth: at a finite depth, unlinked binaries are informational-only (never defects); at `-1`, the strict unlinked check applies unchanged (regression-preserving). In-scope missing-source detection is dropped at finite depths.
- Profiles are data-only presets; tutorials explicitly deferred per user input ("We will add tutorials for those later").
