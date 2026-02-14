#!/usr/bin/env python3
"""Run Phase 8 quality-gate commands and publish a markdown report."""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
import platform
import re
import shutil
import shlex
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class Stage:
    name: str
    purpose: str
    command: str


@dataclass
class StageResult:
    stage: Stage
    returncode: int
    duration_seconds: float
    stdout_path: Path
    stderr_path: Path
    started_at: str
    finished_at: str
    attempt_count: int = 1
    rerun_performed: bool = False
    final_attempt_junit: Path | None = None

    @property
    def passed(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class CoverageStat:
    lines_covered: int = 0
    lines_total: int = 0
    branches_covered: int = 0
    branches_total: int = 0

    @property
    def line_rate(self) -> float:
        if self.lines_total == 0:
            return 100.0
        return (self.lines_covered / self.lines_total) * 100

    @property
    def branch_rate(self) -> float:
        if self.branches_total == 0:
            return 100.0
        return (self.branches_covered / self.branches_total) * 100

    def add(self, other: "CoverageStat") -> "CoverageStat":
        return CoverageStat(
            lines_covered=self.lines_covered + other.lines_covered,
            lines_total=self.lines_total + other.lines_total,
            branches_covered=self.branches_covered + other.branches_covered,
            branches_total=self.branches_total + other.branches_total,
        )


@dataclass(frozen=True)
<<<<<<< HEAD:scripts/phase8_quality_gate.py
class BenchmarkComparison:
    scenario: str
    baseline_median_ms: float
    current_median_ms: float
    regression_percent: float
    passed: bool
||||||| 0f58dc0:scripts/phase8_quality_gate.py
=======
class TestAttemptOutcome:
    node_id: str
    outcome: str
    stage_name: str
    attempt_index: int


@dataclass(frozen=True)
class FlakyRecord:
    node_id: str
    stage_name: str
    run_outcomes: tuple[str, ...]
    failure_frequency: float
    environment_context: str
    suspected_cause: str
    remediation_issue: str
    risk_level: str
>>>>>>> main:scripts/phase8_quality_gate_1.py


STAGES: list[Stage] = [
    Stage(
        name="lint_import_sanity",
        purpose="Fast preflight correctness check",
        command="python -m pytest --collect-only -q",
    ),
    Stage(
        name="unit_suite",
        purpose="Validate isolated behavior",
        command='python -m pytest tests -m "not slow and not benchmark"',
    ),
    Stage(
        name="integration_suite",
        purpose="Validate cross-module contracts",
        command=(
            "python -m pytest tests/test_integration.py tests/test_repository.py "
            "tests/test_operations.py tests/test_overlay.py tests/test_materialization.py"
        ),
    ),
    Stage(
        name="property_regression_suite",
        purpose="Validate invariants and edge cases",
        command="python -m pytest tests/test_property_based.py tests/test_improvements.py",
    ),
    Stage(
        name="coverage_global",
        purpose="Enforce project-level coverage floor",
        command=(
            "python -m pytest tests --cov=fsdantic --cov-branch --cov-report=term-missing "
            "--cov-report=xml --cov-report=html"
        ),
    ),
    Stage(
        name="performance_suite",
        purpose="Detect regressions vs baseline",
        command='python -m pytest tests/test_performance.py -m "benchmark" -q',
    ),
    Stage(
        name="full_gate",
        purpose="Final gate in one command",
        command="python -m pytest tests",
    ),
]


COVERAGE_THRESHOLDS: list[tuple[str, tuple[str, ...], float, float]] = [
    ("Core models and exceptions", ("src/fsdantic/models.py", "src/fsdantic/exceptions.py"), 95.0, 90.0),
    ("File and query operations", ("src/fsdantic/operations.py", "src/fsdantic/view.py"), 92.0, 88.0),
    ("Repository and KV behaviors", ("src/fsdantic/repository.py",), 92.0, 88.0),
    ("Overlay and materialization", ("src/fsdantic/overlay.py", "src/fsdantic/materialization.py"), 90.0, 85.0),
    ("Public API surface", ("src/fsdantic/__init__.py",), 100.0, 100.0),
    ("Whole project floor", ("src/fsdantic/*",), 92.0, 88.0),
]

MAX_RERUN_ATTEMPTS = 2
HARD_FAIL_FLAKY_STAGES = {"property_regression_suite", "full_gate"}
RETRY_EXCLUDED_STAGES = {"lint_import_sanity"}


def is_pytest_command(command: str) -> bool:
    return bool(re.match(r"^\s*python\s+-m\s+pytest\b", command))


def with_junit_xml(command: str, junit_path: Path) -> str:
    if "--junitxml" in command:
        return command
    return f"{command} --junitxml {junit_path}"


def build_targeted_rerun_command(command: str, node_ids: list[str]) -> str:
    if not node_ids:
        return command
    quoted = " ".join(shlex.quote(node_id) for node_id in node_ids)
    return f"{command} {quoted}"


def parse_junit_outcomes(junit_path: Path, stage_name: str, attempt_index: int) -> list[TestAttemptOutcome]:
    if not junit_path.exists():
        return []

    root = ET.parse(junit_path).getroot()
    outcomes: list[TestAttemptOutcome] = []
    for testcase in root.findall(".//testcase"):
        node_id = testcase.attrib.get("classname", "")
        if node_id:
            node_id = f"{node_id}::{testcase.attrib.get('name', '').strip(':')}"
        else:
            node_id = testcase.attrib.get("name", "unknown")

        if testcase.find("./failure") is not None:
            outcome = "failed"
        elif testcase.find("./error") is not None:
            outcome = "error"
        elif testcase.find("./skipped") is not None:
            outcome = "skipped"
        else:
            outcome = "passed"

        outcomes.append(
            TestAttemptOutcome(
                node_id=node_id,
                outcome=outcome,
                stage_name=stage_name,
                attempt_index=attempt_index,
            )
        )

    return outcomes


def failed_node_ids(outcomes: list[TestAttemptOutcome]) -> list[str]:
    return sorted({o.node_id for o in outcomes if o.outcome in {"failed", "error"}})


def is_retry_eligible(stage: Stage, result: StageResult, outcomes: list[TestAttemptOutcome]) -> bool:
    if stage.name in RETRY_EXCLUDED_STAGES:
        return False
    if not is_pytest_command(stage.command):
        return False
    if result.returncode == 0:
        return False
    return bool(failed_node_ids(outcomes))


def slugify(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value).strip("_").lower()


def run_stage(stage: Stage, artifacts_dir: Path, env: dict[str, str]) -> tuple[StageResult, list[list[TestAttemptOutcome]]]:
    stage_ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = slugify(stage.name)
    stdout_path = artifacts_dir / f"{slug}.{stage_ts}.stdout.log"
    stderr_path = artifacts_dir / f"{slug}.{stage_ts}.stderr.log"

    started = dt.datetime.now(dt.timezone.utc)

    base_command = stage.command
    command = base_command
    attempt = 0
    attempt_outcomes: list[list[TestAttemptOutcome]] = []
    final_proc: subprocess.CompletedProcess[str] | None = None
    final_junit: Path | None = None

    while True:
        attempt += 1
        junit_path: Path | None = None
        attempt_command = command
        if is_pytest_command(command):
            junit_path = artifacts_dir / f"{slug}.{stage_ts}.attempt{attempt}.junit.xml"
            attempt_command = with_junit_xml(command, junit_path)

        proc = subprocess.run(
            attempt_command,
            shell=True,
            text=True,
            env=env,
            capture_output=True,
            check=False,
        )
        final_proc = proc
        if junit_path:
            final_junit = junit_path
            attempt_outcomes.append(parse_junit_outcomes(junit_path, stage.name, attempt))
        else:
            attempt_outcomes.append([])

        if attempt == 1:
            stdout_path.write_text(proc.stdout, encoding="utf-8")
            stderr_path.write_text(proc.stderr, encoding="utf-8")

        current_failures = failed_node_ids(attempt_outcomes[-1])
        eligible = is_retry_eligible(
            stage,
            StageResult(
                stage=stage,
                returncode=proc.returncode,
                duration_seconds=0,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                started_at="",
                finished_at="",
            ),
            attempt_outcomes[-1],
        )
        if not eligible or attempt > MAX_RERUN_ATTEMPTS:
            if attempt > 1:
                stdout_path.write_text(proc.stdout, encoding="utf-8")
                stderr_path.write_text(proc.stderr, encoding="utf-8")
            break

        command = build_targeted_rerun_command(base_command, current_failures)

    finished = dt.datetime.now(dt.timezone.utc)
    assert final_proc is not None

    result = StageResult(
        stage=stage,
        returncode=final_proc.returncode,
        duration_seconds=(finished - started).total_seconds(),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        attempt_count=attempt,
        rerun_performed=attempt > 1,
        final_attempt_junit=final_junit,
    )
    return result, attempt_outcomes


def git_commit_sha() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    if proc.returncode == 0:
        return proc.stdout.strip()
    return "unknown"


def copy_coverage_artifacts(repo_root: Path, artifacts_dir: Path) -> tuple[Path | None, Path | None]:
    xml_src = repo_root / "coverage.xml"
    html_src = repo_root / "htmlcov"
    xml_dst = artifacts_dir / "coverage.xml"
    html_dst = artifacts_dir / "htmlcov"

    xml_out: Path | None = None
    html_out: Path | None = None

    if xml_src.exists():
        shutil.copy2(xml_src, xml_dst)
        xml_out = xml_dst

    if html_src.exists() and html_src.is_dir():
        if html_dst.exists():
            shutil.rmtree(html_dst)
        shutil.copytree(html_src, html_dst)
        html_out = html_dst

    return xml_out, html_out


def parse_coverage(xml_path: Path) -> tuple[dict[str, CoverageStat], CoverageStat]:
    tree = ET.parse(xml_path)
    root = tree.getroot()

    per_file: dict[str, CoverageStat] = {}
    for cls in root.findall(".//class"):
        filename = cls.attrib.get("filename")
        if not filename:
            continue

        lines_total = int(cls.attrib.get("lines-valid", "0") or 0)
        lines_covered = int(cls.attrib.get("lines-covered", "0") or 0)

        branches_total = 0
        branches_covered = 0
        for line in cls.findall("./lines/line"):
            branch = line.attrib.get("branch", "false").lower() == "true"
            if not branch:
                continue
            cond_cov = line.attrib.get("condition-coverage", "")
            if "(" in cond_cov and "/" in cond_cov and ")" in cond_cov:
                fragment = cond_cov.split("(", 1)[1].split(")", 1)[0]
                covered_raw, total_raw = fragment.split("/", 1)
                branches_covered += int(covered_raw.strip())
                branches_total += int(total_raw.strip())

        normalized = filename.replace("\\", "/")
        per_file[normalized] = CoverageStat(
            lines_covered=lines_covered,
            lines_total=lines_total,
            branches_covered=branches_covered,
            branches_total=branches_total,
        )

    overall = CoverageStat(
        lines_covered=int(root.attrib.get("lines-covered", "0") or 0),
        lines_total=int(root.attrib.get("lines-valid", "0") or 0),
        branches_covered=int(root.attrib.get("branches-covered", "0") or 0),
        branches_total=int(root.attrib.get("branches-valid", "0") or 0),
    )
    return per_file, overall


def normalize_coverage_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def compute_area_coverage(per_file: dict[str, CoverageStat], targets: tuple[str, ...], overall: CoverageStat) -> CoverageStat:
    if targets == ("src/fsdantic/*",):
        return overall

    matched: set[str] = set()
    for key in per_file:
        normalized_key = normalize_coverage_path(key)
        for target in targets:
            normalized_target = normalize_coverage_path(target)
            if fnmatch.fnmatch(normalized_key, normalized_target) or normalized_key.endswith(normalized_target):
                matched.add(key)

    aggregate = CoverageStat()
    for key in matched:
        aggregate = aggregate.add(per_file[key])
    return aggregate


def print_coverage_threshold_table(rows: list[tuple[str, str, float, float, float, float, str]]) -> None:
    headers = ("Area", "Path", "Line", "Branch", "Threshold", "Status")
    formatted_rows = [
        (area, path, f"{line_val:.2f}%", f"{branch_val:.2f}%", f">= {line_floor:.1f}% / >= {branch_floor:.1f}%", status)
        for area, path, line_val, branch_val, line_floor, branch_floor, status in rows
    ]
    widths = [len(h) for h in headers]
    for row in formatted_rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row)]

    def render(parts: tuple[str, ...]) -> str:
        return " | ".join(cell.ljust(width) for cell, width in zip(parts, widths))

    print("[phase8] Coverage thresholds")
    print(render(headers))
    print("-+-".join("-" * width for width in widths))
    for row in formatted_rows:
        print(render(row))


<<<<<<< HEAD:scripts/phase8_quality_gate.py
def load_benchmark_medians(artifact_path: Path) -> dict[str, float]:
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios", {})
    if not isinstance(scenarios, dict):
        raise ValueError(f"Invalid benchmark artifact at {artifact_path}: scenarios must be an object")

    medians: dict[str, float] = {}
    for scenario, values in scenarios.items():
        if not isinstance(values, dict):
            raise ValueError(
                f"Invalid benchmark artifact at {artifact_path}: scenario '{scenario}' must be an object"
            )
        median_ms = values.get("median_ms")
        if not isinstance(median_ms, int | float):
            raise ValueError(
                f"Invalid benchmark artifact at {artifact_path}: scenario '{scenario}' missing numeric median_ms"
            )
        medians[scenario] = float(median_ms)
    return medians


def compare_benchmark_medians(
    baseline_medians: dict[str, float],
    current_medians: dict[str, float],
    tolerance_percent: float,
) -> tuple[list[BenchmarkComparison], list[str], list[str], bool]:
    comparisons: list[BenchmarkComparison] = []

    missing_from_current = sorted(set(baseline_medians) - set(current_medians))
    missing_from_baseline = sorted(set(current_medians) - set(baseline_medians))

    for scenario in sorted(set(baseline_medians) & set(current_medians)):
        baseline = baseline_medians[scenario]
        current = current_medians[scenario]
        if baseline == 0:
            regression = 0.0 if current == 0 else float("inf")
        else:
            regression = ((current - baseline) / baseline) * 100
        passed = regression <= tolerance_percent
        comparisons.append(
            BenchmarkComparison(
                scenario=scenario,
                baseline_median_ms=baseline,
                current_median_ms=current,
                regression_percent=regression,
                passed=passed,
            )
        )

    regression_fail = any(not row.passed for row in comparisons)
    mismatch_fail = bool(missing_from_current or missing_from_baseline)
    return comparisons, missing_from_current, missing_from_baseline, regression_fail or mismatch_fail
||||||| 0f58dc0:scripts/phase8_quality_gate.py
=======
def classify_flaky_records(
    stage_results: list[StageResult],
    outcomes_by_stage: dict[str, list[list[TestAttemptOutcome]]],
    env_context: str,
) -> list[FlakyRecord]:
    records: list[FlakyRecord] = []

    for result in stage_results:
        attempts = outcomes_by_stage.get(result.stage.name, [])
        if len(attempts) <= 1:
            continue

        per_node: dict[str, list[str]] = {}
        for run in attempts:
            for outcome in run:
                if outcome.outcome not in {"passed", "failed", "error"}:
                    continue
                per_node.setdefault(outcome.node_id, []).append(outcome.outcome)

        for node_id, run_outcomes in per_node.items():
            unique = set(run_outcomes)
            if len(unique) <= 1:
                continue

            failures = sum(1 for outcome in run_outcomes if outcome in {"failed", "error"})
            frequency = failures / len(run_outcomes)
            risk_level = "high" if result.stage.name in HARD_FAIL_FLAKY_STAGES else "non-critical"
            suspected = "Intermittent timing or shared-state dependency"
            remediation = "TODO-ISSUE"
            records.append(
                FlakyRecord(
                    node_id=node_id,
                    stage_name=result.stage.name,
                    run_outcomes=tuple(run_outcomes),
                    failure_frequency=frequency,
                    environment_context=env_context,
                    suspected_cause=suspected,
                    remediation_issue=remediation,
                    risk_level=risk_level,
                )
            )

    return sorted(records, key=lambda r: (r.risk_level != "high", r.stage_name, r.node_id))


def write_flaky_report(artifacts_dir: Path, flaky_records: list[FlakyRecord]) -> Path:
    report_path = artifacts_dir / "phase8_flaky_report.json"
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "flaky_tests": [
            {
                "node_id": r.node_id,
                "stage_name": r.stage_name,
                "run_outcomes": list(r.run_outcomes),
                "failure_frequency": round(r.failure_frequency, 4),
                "environment_context": r.environment_context,
                "suspected_cause": r.suspected_cause,
                "remediation_issue": r.remediation_issue,
                "risk_level": r.risk_level,
            }
            for r in flaky_records
        ],
    }
    report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return report_path
>>>>>>> main:scripts/phase8_quality_gate_1.py


def build_report(
    artifacts_dir: Path,
    stage_results: list[StageResult],
    coverage_xml: Path | None,
    coverage_html: Path | None,
<<<<<<< HEAD:scripts/phase8_quality_gate.py
    benchmark_baseline_path: Path,
    benchmark_tolerance_percent: float,
    benchmark_comparisons: list[BenchmarkComparison],
    benchmark_missing_from_current: list[str],
    benchmark_missing_from_baseline: list[str],
    benchmark_gate_failed: bool,
||||||| 0f58dc0:scripts/phase8_quality_gate.py
=======
    flaky_records: list[FlakyRecord],
    flaky_report_path: Path,
>>>>>>> main:scripts/phase8_quality_gate_1.py
) -> tuple[str, bool]:
    passed = [r for r in stage_results if r.passed]
    failed = [r for r in stage_results if not r.passed]

    total = len(stage_results)
    commit_sha = git_commit_sha()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    runner = f"{platform.platform()} | python {platform.python_version()} | host {socket.gethostname()}"

    overall_line = 0.0
    overall_branch = 0.0
    coverage_rows: list[str] = []
    coverage_table_rows: list[tuple[str, str, float, float, float, float, str]] = []
    coverage_fail = False

    if coverage_xml and coverage_xml.exists():
        per_file, overall = parse_coverage(coverage_xml)
        overall_line = overall.line_rate
        overall_branch = overall.branch_rate

        for area, targets, line_floor, branch_floor in COVERAGE_THRESHOLDS:
            aggregate = compute_area_coverage(per_file, targets, overall)
            line_val, branch_val = aggregate.line_rate, aggregate.branch_rate

            pass_line = line_val >= line_floor
            pass_branch = branch_val >= branch_floor
            area_pass = pass_line and pass_branch
            coverage_fail = coverage_fail or (not area_pass)
            status = "PASS" if area_pass else "FAIL"
            path_text = ", ".join(targets)

            coverage_table_rows.append((area, path_text, line_val, branch_val, line_floor, branch_floor, status))

            coverage_rows.append(
                "| "
                f"{area} | {path_text} | {line_val:.2f}% | {branch_val:.2f}% | "
                f">= {line_floor:.1f}% / >= {branch_floor:.1f}% | {status} |"
            )

        print_coverage_threshold_table(coverage_table_rows)
    else:
        coverage_rows.append("| Coverage data unavailable | n/a | n/a | n/a | n/a | FAIL |")
        coverage_fail = True

    high_risk_source = next((r for r in stage_results if r.stage.name == "property_regression_suite"), None)
    high_risk_status = "PASS" if (high_risk_source and high_risk_source.passed) else "FAIL"

<<<<<<< HEAD:scripts/phase8_quality_gate.py
    release_fail = bool(failed) or coverage_fail or high_risk_status == "FAIL" or benchmark_gate_failed
||||||| 0f58dc0:scripts/phase8_quality_gate.py
    release_fail = bool(failed) or coverage_fail or high_risk_status == "FAIL"
=======
    unresolved_flaky = [r for r in flaky_records if r.remediation_issue == "TODO-ISSUE"]
    high_risk_flaky = [r for r in unresolved_flaky if r.risk_level == "high"]
    non_critical_unresolved = [r for r in unresolved_flaky if r.risk_level != "high"]

    flaky_hard_fail = bool(high_risk_flaky)
    flaky_conditional_fail = bool(non_critical_unresolved)

    release_fail = bool(failed) or coverage_fail or high_risk_status == "FAIL" or flaky_hard_fail or flaky_conditional_fail
>>>>>>> main:scripts/phase8_quality_gate_1.py
    final_status = "FAIL" if release_fail else "PASS"

    flaky_rows = [
        "| "
        f"{r.node_id} | {r.stage_name} | {', '.join(r.run_outcomes)} | {r.failure_frequency:.2f} | "
        f"{r.risk_level} | {r.remediation_issue} |"
        for r in flaky_records
    ]
    if not flaky_rows:
        flaky_rows.append("| none | n/a | n/a | 0.00 | n/a | n/a |")

    command_table = []
    for result in stage_results:
        command_table.append(
            "| "
            f"{result.stage.name} | `{result.stage.command}` | {'PASS' if result.passed else 'FAIL'} | "
            f"{result.returncode} | {result.duration_seconds:.2f}s ({result.attempt_count} attempt(s)) | "
            f"[{result.stdout_path.name}]({result.stdout_path.name}) | "
            f"[{result.stderr_path.name}]({result.stderr_path.name}) |"
        )

    coverage_links = []
    if coverage_xml:
        coverage_links.append(f"- coverage.xml: [{coverage_xml.name}]({coverage_xml.name})")
    if coverage_html:
        index = coverage_html / "index.html"
        if index.exists():
            coverage_links.append(f"- htmlcov: [htmlcov/index.html](htmlcov/index.html)")

    coverage_artifacts_text = "\n".join(coverage_links) if coverage_links else "- none"

    performance_rows = []
    for row in benchmark_comparisons:
        regression = f"{row.regression_percent:.2f}%" if row.regression_percent != float("inf") else "inf%"
        performance_rows.append(
            "| "
            f"{row.scenario} | {row.baseline_median_ms:.4f} | {row.current_median_ms:.4f} | "
            f"{regression} | {'PASS' if row.passed else 'FAIL'} |"
        )
    if not performance_rows:
        performance_rows.append("| No benchmark medians compared | n/a | n/a | n/a | FAIL |")

    report = f"""# Phase 8 Quality Gate Report

## Build Metadata
- Commit SHA: {commit_sha}
- Date: {now}
- Runner environment: {runner}

## Execution Summary
- Total test commands executed: {total}
- Passed: {len(passed)}
- Failed: {len(failed)}
- Flaky: {len(flaky_records)}

## Command Outcomes
| Stage | Command | Status | Exit Code | Duration | Stdout | Stderr |
| --- | --- | --- | ---: | ---: | --- | --- |
{os.linesep.join(command_table)}

## Coverage Summary
- Global line coverage: {overall_line:.2f}%
- Global branch coverage: {overall_branch:.2f}%
- Per-area coverage table:
| Package area | Path | Line | Branch | Threshold | Status |
| --- | --- | ---: | ---: | --- | --- |
{os.linesep.join(coverage_rows)}

Coverage artifacts:
{coverage_artifacts_text}

Flaky artifact:
- flaky report: [{flaky_report_path.name}]({flaky_report_path.name})

## Performance Summary
- Benchmark baseline reference: `{benchmark_baseline_path}`
- Regression tolerance: {benchmark_tolerance_percent:.2f}%
- Scenario comparisons:
| Scenario | Baseline median (ms) | Current median (ms) | Regression (%) | Status |
| --- | ---: | ---: | ---: | --- |
{os.linesep.join(performance_rows)}
- Missing from current run: {', '.join(benchmark_missing_from_current) if benchmark_missing_from_current else 'none'}
- Missing from baseline: {', '.join(benchmark_missing_from_baseline) if benchmark_missing_from_baseline else 'none'}
- Threshold breaches: {'Yes' if benchmark_gate_failed else 'No'}

## High-Risk Regression Results
- Error translation: {high_risk_status}
- Path handling: {high_risk_status}
- Namespace composition: {high_risk_status}

## Open Defects
- P0: Not inventoried by this script.
- P1: Not inventoried by this script.
- P2+: Not inventoried by this script.
- Deferred with approval: Not inventoried by this script.

## Flaky Classification (Step 8)
- Hard-fail flaky tests (high-risk/release-critical, unresolved): {len(high_risk_flaky)}
- Conditional-fail flaky tests (non-critical, unresolved): {len(non_critical_unresolved)}
- Remediation tracking placeholders used: {'Yes' if unresolved_flaky else 'No'}

| Test node id | Stage | Run outcomes | Failure frequency | Risk | Remediation issue |
| --- | --- | --- | ---: | --- | --- |
{os.linesep.join(flaky_rows)}

## Final Gate Decision
- Status: {final_status}
- Release recommendation: {'Do not release until blocking failures are resolved.' if release_fail else 'Release-ready based on automated gates.'}
- Required follow-ups: {'Resolve failed stages, threshold breaches, and unresolved flaky remediations, then rerun.' if release_fail else 'None from automated checks.'}
"""

    return report, release_fail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts-root",
        type=Path,
        default=Path("artifacts/phase8"),
        help="Base artifact directory (default: artifacts/phase8)",
    )
    parser.add_argument(
        "--benchmark-baseline",
        type=Path,
        default=Path("tests/performance_baseline.json"),
        help="Baseline benchmark medians JSON (default: tests/performance_baseline.json)",
    )
    parser.add_argument(
        "--benchmark-tolerance-percent",
        type=float,
        default=10.0,
        help="Allowed performance regression percent before failing (default: 10)",
    )
    args = parser.parse_args()

    repo_root = Path.cwd()
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifacts_dir = args.artifacts_root / timestamp
    artifacts_dir.mkdir(parents=True, exist_ok=False)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    benchmark_current_path = artifacts_dir / "performance_current.json"
    env["FSDANTIC_BENCHMARK_OUTPUT"] = str(benchmark_current_path)

    stage_results: list[StageResult] = []
    outcomes_by_stage: dict[str, list[list[TestAttemptOutcome]]] = {}
    print(f"[phase8] Artifacts: {artifacts_dir}")
    for stage in STAGES:
        print(f"[phase8] Running {stage.name}: {stage.command}")
        result, attempt_outcomes = run_stage(stage, artifacts_dir, env)
        stage_results.append(result)
        outcomes_by_stage[stage.name] = attempt_outcomes
        print(
            f"[phase8] {stage.name} -> {'PASS' if result.passed else 'FAIL'} "
            f"(exit={result.returncode}, duration={result.duration_seconds:.2f}s, attempts={result.attempt_count})"
        )

    runner = f"{platform.platform()} | python {platform.python_version()} | host {socket.gethostname()}"
    flaky_records = classify_flaky_records(stage_results, outcomes_by_stage, runner)
    flaky_report_path = write_flaky_report(artifacts_dir, flaky_records)

    coverage_xml, coverage_html = copy_coverage_artifacts(repo_root, artifacts_dir)
<<<<<<< HEAD:scripts/phase8_quality_gate.py

    benchmark_comparisons: list[BenchmarkComparison] = []
    benchmark_missing_from_current: list[str] = []
    benchmark_missing_from_baseline: list[str] = []
    benchmark_gate_failed = False

    try:
        if not args.benchmark_baseline.exists():
            raise FileNotFoundError(f"Benchmark baseline not found: {args.benchmark_baseline}")
        if not benchmark_current_path.exists():
            raise FileNotFoundError(f"Benchmark current metrics not found: {benchmark_current_path}")

        baseline_medians = load_benchmark_medians(args.benchmark_baseline)
        current_medians = load_benchmark_medians(benchmark_current_path)
        (
            benchmark_comparisons,
            benchmark_missing_from_current,
            benchmark_missing_from_baseline,
            benchmark_gate_failed,
        ) = compare_benchmark_medians(
            baseline_medians,
            current_medians,
            args.benchmark_tolerance_percent,
        )
    except (OSError, ValueError) as exc:
        print(f"[phase8] Performance baseline comparison failed: {exc}")
        benchmark_gate_failed = True

    report, release_fail = build_report(
        artifacts_dir,
        stage_results,
        coverage_xml,
        coverage_html,
        args.benchmark_baseline,
        args.benchmark_tolerance_percent,
        benchmark_comparisons,
        benchmark_missing_from_current,
        benchmark_missing_from_baseline,
        benchmark_gate_failed,
||||||| 0f58dc0:scripts/phase8_quality_gate.py
    report, release_fail = build_report(artifacts_dir, stage_results, coverage_xml, coverage_html)
=======
    report, release_fail = build_report(
        artifacts_dir,
        stage_results,
        coverage_xml,
        coverage_html,
        flaky_records,
        flaky_report_path,
>>>>>>> main:scripts/phase8_quality_gate_1.py
    )

    report_path = artifacts_dir / "phase8_quality_gate_report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"[phase8] Report written: {report_path}")

    if release_fail:
        print("[phase8] FINAL STATUS: FAIL")
        return 1

    print("[phase8] FINAL STATUS: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
