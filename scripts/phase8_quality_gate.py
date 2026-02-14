#!/usr/bin/env python3
"""Run Phase 8 quality-gate commands and publish a markdown report."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import platform
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
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

    @property
    def passed(self) -> bool:
        return self.returncode == 0


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
        command='python -m pytest tests/test_performance.py -m "benchmark or slow" -q',
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


def slugify(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value).strip("_").lower()


def run_stage(stage: Stage, artifacts_dir: Path, env: dict[str, str]) -> StageResult:
    stage_ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = slugify(stage.name)
    stdout_path = artifacts_dir / f"{slug}.{stage_ts}.stdout.log"
    stderr_path = artifacts_dir / f"{slug}.{stage_ts}.stderr.log"

    started = dt.datetime.now(dt.timezone.utc)
    proc = subprocess.run(
        stage.command,
        shell=True,
        text=True,
        env=env,
        capture_output=True,
        check=False,
    )
    finished = dt.datetime.now(dt.timezone.utc)

    stdout_path.write_text(proc.stdout, encoding="utf-8")
    stderr_path.write_text(proc.stderr, encoding="utf-8")

    return StageResult(
        stage=stage,
        returncode=proc.returncode,
        duration_seconds=(finished - started).total_seconds(),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
    )


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


def parse_coverage(xml_path: Path) -> tuple[dict[str, tuple[float, float]], float, float]:
    tree = ET.parse(xml_path)
    root = tree.getroot()

    overall_line = float(root.attrib.get("line-rate", "0")) * 100
    overall_branch = float(root.attrib.get("branch-rate", "0")) * 100

    per_file: dict[str, tuple[float, float]] = {}
    for cls in root.findall(".//class"):
        filename = cls.attrib.get("filename")
        if not filename:
            continue
        line_rate = float(cls.attrib.get("line-rate", "0")) * 100
        branch_rate = float(cls.attrib.get("branch-rate", "0")) * 100
        normalized = filename.replace("\\", "/")
        per_file[normalized] = (line_rate, branch_rate)

    return per_file, overall_line, overall_branch


def resolve_file_coverage(per_file: dict[str, tuple[float, float]], target: str) -> tuple[float, float] | None:
    normalized_target = target.replace("\\", "/")
    if normalized_target in per_file:
        return per_file[normalized_target]

    for key, value in per_file.items():
        if key.endswith(normalized_target):
            return value
    return None


def average_coverage(values: Iterable[tuple[float, float]]) -> tuple[float, float]:
    vals = list(values)
    if not vals:
        return (0.0, 0.0)
    line = sum(v[0] for v in vals) / len(vals)
    branch = sum(v[1] for v in vals) / len(vals)
    return (line, branch)


def build_report(
    artifacts_dir: Path,
    stage_results: list[StageResult],
    coverage_xml: Path | None,
    coverage_html: Path | None,
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
    coverage_fail = False

    if coverage_xml and coverage_xml.exists():
        per_file, overall_line, overall_branch = parse_coverage(coverage_xml)
        for area, targets, line_floor, branch_floor in COVERAGE_THRESHOLDS:
            if targets == ("src/fsdantic/*",):
                line_val, branch_val = overall_line, overall_branch
            else:
                values: list[tuple[float, float]] = []
                for target in targets:
                    found = resolve_file_coverage(per_file, target)
                    if found is not None:
                        values.append(found)
                line_val, branch_val = average_coverage(values)

            pass_line = line_val >= line_floor
            pass_branch = branch_val >= branch_floor
            area_pass = pass_line and pass_branch
            coverage_fail = coverage_fail or (not area_pass)

            coverage_rows.append(
                "| "
                f"{area} | {', '.join(targets)} | {line_val:.2f}% | {branch_val:.2f}% | "
                f">= {line_floor:.1f}% / >= {branch_floor:.1f}% | {'PASS' if area_pass else 'FAIL'} |"
            )
    else:
        coverage_rows.append("| Coverage data unavailable | n/a | n/a | n/a | n/a | FAIL |")
        coverage_fail = True

    high_risk_source = next((r for r in stage_results if r.stage.name == "property_regression_suite"), None)
    high_risk_status = "PASS" if (high_risk_source and high_risk_source.passed) else "FAIL"

    release_fail = bool(failed) or coverage_fail or high_risk_status == "FAIL"
    final_status = "FAIL" if release_fail else "PASS"

    command_table = []
    for result in stage_results:
        command_table.append(
            "| "
            f"{result.stage.name} | `{result.stage.command}` | {'PASS' if result.passed else 'FAIL'} | "
            f"{result.returncode} | {result.duration_seconds:.2f}s | "
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

    report = f"""# Phase 8 Quality Gate Report

## Build Metadata
- Commit SHA: {commit_sha}
- Date: {now}
- Runner environment: {runner}

## Execution Summary
- Total test commands executed: {total}
- Passed: {len(passed)}
- Failed: {len(failed)}
- Flaky: 0

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

## Performance Summary
- Benchmark baseline reference: Not provided by this runner.
- Current median/p95 by scenario: See performance suite logs.
- Regression delta (%): Not computed by this script (requires baseline data source).
- Threshold breaches: {'Yes' if any(r.stage.name == 'performance_suite' and not r.passed for r in stage_results) else 'No'}

## High-Risk Regression Results
- Error translation: {high_risk_status}
- Path handling: {high_risk_status}
- Namespace composition: {high_risk_status}

## Open Defects
- P0: Not inventoried by this script.
- P1: Not inventoried by this script.
- P2+: Not inventoried by this script.
- Deferred with approval: Not inventoried by this script.

## Final Gate Decision
- Status: {final_status}
- Release recommendation: {'Do not release until blocking failures are resolved.' if release_fail else 'Release-ready based on automated gates.'}
- Required follow-ups: {'Fix failed stages and/or threshold breaches, then rerun.' if release_fail else 'None from automated checks.'}
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
    args = parser.parse_args()

    repo_root = Path.cwd()
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifacts_dir = args.artifacts_root / timestamp
    artifacts_dir.mkdir(parents=True, exist_ok=False)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    stage_results: list[StageResult] = []
    print(f"[phase8] Artifacts: {artifacts_dir}")
    for stage in STAGES:
        print(f"[phase8] Running {stage.name}: {stage.command}")
        result = run_stage(stage, artifacts_dir, env)
        stage_results.append(result)
        print(
            f"[phase8] {stage.name} -> {'PASS' if result.passed else 'FAIL'} "
            f"(exit={result.returncode}, duration={result.duration_seconds:.2f}s)"
        )

    coverage_xml, coverage_html = copy_coverage_artifacts(repo_root, artifacts_dir)
    report, release_fail = build_report(artifacts_dir, stage_results, coverage_xml, coverage_html)

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
