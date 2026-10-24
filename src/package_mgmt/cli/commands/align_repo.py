"""`align-repo` command: determine or verify the version-aligned source set.

Search mode scans the acquired source repositories for the newest release era
whose declared dependency requirements are all satisfied by the frozen cache
and produces the version manifest; `--verify` mode re-validates a given
manifest against the current cache and repositories without searching or
writing (re-runnable alignment check).
"""

import json
from datetime import date
from typing import NotRequired, TypedDict

from package_mgmt.lib.model.alignment import AlignmentReport, UnmetRequirement
from package_mgmt.lib.service.alignment import (
    AlignmentLoadError,
    baseline_label,
    load_baseline,
    load_manifest,
    run_search,
    run_verify,
)
from package_mgmt.lib.service.db_init import validate_schema_or_error
from package_mgmt.lib.util.errors import (
    EXIT_CONFIG_ERROR,
    EXIT_ERROR,
    EXIT_SUCCESS,
    print_error,
)
from package_mgmt.lib.util.paths import binary_index_path, resolve_index_path


class UnmetJson(TypedDict):
    repo: str
    module: str
    operator: str
    version: str
    tier: str
    cached_package: str | None
    cached_version: str | None
    reason: str
    source_ref: str


class AlignmentJson(TypedDict):
    mode: str
    repo_dir: str
    baseline: str
    requirements_checked: int
    result: str
    unmet: list[UnmetJson]
    manifest: NotRequired[str]
    candidates_evaluated: NotRequired[list[str]]
    selected_era: NotRequired[str]
    manifest_written: NotRequired[str]


def _unmet_json(unmet: UnmetRequirement) -> UnmetJson:
    requirement = unmet.requirement
    return UnmetJson(
        repo=requirement.repo,
        module=requirement.module,
        operator=requirement.operator,
        version=requirement.version,
        tier=requirement.tier,
        cached_package=unmet.cached_package,
        cached_version=unmet.cached_version,
        reason=unmet.reason,
        source_ref=requirement.source_ref,
    )


def _report_json(report: AlignmentReport) -> AlignmentJson:
    payload = AlignmentJson(
        mode=report.mode,
        repo_dir=report.repo_dir,
        baseline=report.baseline,
        requirements_checked=report.requirements_checked,
        result="PASS" if report.satisfied else "FAIL",
        unmet=[_unmet_json(u) for u in report.unmet],
    )
    if report.mode == "search":
        payload["candidates_evaluated"] = list(report.candidates_evaluated or ())
        payload["selected_era"] = report.selected_era
        payload["manifest_written"] = report.manifest_written
    else:
        payload["manifest"] = report.manifest or ""
    return payload


def _emit_json(report: AlignmentReport) -> None:
    print(json.dumps(_report_json(report)))


def _constraint_text(module: str, operator: str, version: str) -> str:
    if not version:
        return module
    return f"{module}{operator}{version}"


def _unmet_text(unmet: UnmetRequirement) -> str:
    requirement = unmet.requirement
    constraint = _constraint_text(
        requirement.module, requirement.operator, requirement.version
    )
    if unmet.cached_package:
        evidence = (
            f"cached {unmet.cached_package} "
            f"{unmet.cached_version or 'no version'}: {unmet.reason}"
        )
    else:
        evidence = f"no trixie/main record: {unmet.reason}"
    return (
        f"  FAIL {requirement.repo} -> {constraint}   ({evidence})\n"
        f"       src: {requirement.source_ref}"
    )


def _emit_text(report: AlignmentReport) -> None:
    if report.mode == "search":
        print("Alignment: scanning the acquired repos for the newest satisfying era")
    else:
        print(f"Alignment (verify): {report.manifest or ''}")
    print(f"  baseline: {report.baseline}")
    if report.mode == "search":
        candidates = " → ".join(report.candidates_evaluated or ())
        if report.selected_era is not None:
            print(
                f"  candidates evaluated: {candidates} "
                f"(newest fails, {report.selected_era} selected)"
            )
        else:
            print(f"  candidates evaluated: {candidates} (all fail)")
    print(f"  requirements checked: {report.requirements_checked}")
    if report.mode == "search" and report.manifest_written is not None:
        print(f"  manifest written: {report.manifest_written}")
    if not report.satisfied:
        newest_era = ""
        if report.mode == "search" and report.candidates_evaluated:
            newest_era = report.candidates_evaluated[-1]
        if newest_era:
            print(
                f"  newest era {newest_era} — {len(report.unmet)} unmet requirement(s)"
            )
        for unmet in report.unmet:
            print(_unmet_text(unmet))
    print(f"  result: {'PASS' if report.satisfied else 'FAIL'}")


def _packages_gz_path(binary_cache: str, release: str, component: str) -> str:
    return resolve_index_path(binary_index_path(binary_cache, release, component))


def handle_align_repo(
    db_path: str,
    binary_cache: str,
    repo_dir: str = "repos",
    verify: str | None = None,
    fmt: str = "text",
    config=None,
) -> int:
    if fmt not in ("text", "json"):
        print_error(
            f"invalid --format {fmt!r}; expected text or json", EXIT_CONFIG_ERROR
        )
        return EXIT_CONFIG_ERROR

    if config is not None and config.sources:
        release = config.sources[0].release
        component = config.sources[0].components[0]
    else:
        release, component = "trixie", "main"

    manifest = None
    if verify is not None:
        try:
            manifest = load_manifest(verify)
        except AlignmentLoadError as e:
            print_error(str(e), EXIT_CONFIG_ERROR)
            return EXIT_CONFIG_ERROR

    schema_error = validate_schema_or_error(db_path)
    if schema_error is not None:
        return schema_error

    try:
        baseline = load_baseline(
            db_path, _packages_gz_path(binary_cache, release, component),
            release, component,
        )
        label = baseline_label(release, component)
        if verify is not None:
            report = run_verify(repo_dir, manifest, baseline, verify, label)
        else:
            report = run_search(repo_dir, baseline, date.today(), label)
    except AlignmentLoadError as e:
        print_error(str(e), EXIT_CONFIG_ERROR)
        return EXIT_CONFIG_ERROR

    if fmt == "json":
        _emit_json(report)
    else:
        _emit_text(report)
    return EXIT_SUCCESS if report.satisfied else EXIT_ERROR
