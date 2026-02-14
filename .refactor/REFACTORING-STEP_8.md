# Refactoring Phase 8: Test Plan Execution and Quality Gates

## Objective

Phase 8 is the final verification gate for the refactor. The goal is to prove that the refactored codebase is correct, architecturally compliant, and performant enough to ship without compatibility exceptions.

## Scope

This phase covers final verification of:

- **Correctness** across unit, integration, and regression behavior.
- **Architecture compliance** with the refactored manager boundaries and shared conventions.
- **Performance** against baseline expectations and anti-regression thresholds.

Phase 8 is not a feature-implementation phase. It is a release-quality validation and decision phase.

## Implementation Checklist

- [ ] Consolidate and standardize all test suites (unit, integration, performance) into one execution plan.
- [ ] Define and enforce minimum coverage thresholds per package area.
- [ ] Establish release-blocking criteria for failures, flakes, and unresolved regressions.
- [ ] Run full quality gate matrix and archive artifacts (logs, coverage XML/HTML, benchmark output).
- [ ] Produce final pass/fail report with unresolved defect inventory.

## Coverage Thresholds by Package Area

The following minimums are required for acceptance:

| Package area | Path | Line coverage | Branch coverage |
| --- | --- | ---: | ---: |
| Core models and exceptions | `src/fsdantic/models.py`, `src/fsdantic/exceptions.py` | 95% | 90% |
| File and query operations | `src/fsdantic/operations.py`, `src/fsdantic/view.py` | 92% | 88% |
| Repository and KV behaviors | `src/fsdantic/repository.py` | 92% | 88% |
| Overlay and materialization | `src/fsdantic/overlay.py`, `src/fsdantic/materialization.py` | 90% | 85% |
| Public API surface | `src/fsdantic/__init__.py` | 100% | 100% |
| Whole project floor | `src/fsdantic/*` | 92% | 88% |

If per-area measurement is not directly available from one command, generate area-specific coverage reports via targeted test runs and combine results in the final report.

## Release-Blocking Quality Gates

A release is blocked if **any** of the following conditions occur:

1. Any unit, integration, or performance command in the matrix fails.
2. Any package area falls below its minimum coverage threshold.
3. Any high-risk regression check fails (error translation, path handling, namespace composition).
4. A test is flaky beyond rerun policy limits and root cause is unresolved.
5. Any P0/P1 defect remains open without explicit approved deferment.
6. Performance regressions exceed accepted tolerance (default: >10% slower median vs baseline in benchmark-marked tests).

## Architecture Acceptance Criteria

The refactor is accepted only if test evidence demonstrates consistent behavior across all managers and surfaces:

- File operations manager behavior is consistent with repository and overlay paths where contracts overlap.
- Error translation remains uniform and no raw low-level errors leak through refactored boundaries.
- Namespace/key composition behavior remains deterministic and backward-compatible where required.
- Materialization and overlay flows preserve expected semantics under merge and conflict scenarios.

**Hard rule:** Refactor accepted only if tests prove consistency across all managers.

## Detailed Testing Plan

### Command Matrix

> Run from repository root. Capture stdout/stderr to artifacts for auditability.

| Stage | Purpose | Command | Expected outcome |
| --- | --- | --- | --- |
| Lint/import sanity | Fast preflight correctness check | `python -m pytest --collect-only -q` | Collection succeeds with no import or discovery errors |
| Unit suite | Validate isolated behavior | `python -m pytest tests -m "not slow and not benchmark"` | All unit-focused tests pass |
| Integration suite | Validate cross-module contracts | `python -m pytest tests/test_integration.py tests/test_repository.py tests/test_operations.py tests/test_overlay.py tests/test_materialization.py` | All integration paths pass |
| Property/regression suite | Validate invariants and edge cases | `python -m pytest tests/test_property_based.py tests/test_improvements.py` | Invariant and regression checks pass |
| Coverage (global) | Enforce project-level coverage floor | `python -m pytest tests --cov=fsdantic --cov-branch --cov-report=term-missing --cov-report=xml --cov-report=html` | Global and per-area thresholds met |
| Performance suite | Detect regressions vs baseline | `python -m pytest tests/test_performance.py -m "benchmark or slow" -q` | No benchmark regression beyond tolerance |
| Full gate | Final gate in one command | `python -m pytest tests` | Full suite passes cleanly |

### Flakiness Policy and Rerun Rules

- A failing test may be rerun up to **2 additional times** only when failure signature indicates non-determinism (timing/order/environment contention).
- A test is marked **flaky** if outcomes differ across reruns without code changes.
- Flaky tests do **not** auto-pass quality gates:
  - If flaky but non-critical and mitigated, gate status is **conditional fail** pending issue ticket.
  - If flaky in high-risk path or release-critical flow, gate status is **hard fail**.
- Every flaky failure must produce:
  - test name and seed/context,
  - failure frequency,
  - suspected cause,
  - linked remediation issue.

### Regression Checks for High-Risk Paths

The following regression themes are mandatory and release-blocking:

1. **Error translation**
   - Validate mapping from low-level failures to fsdantic exception hierarchy.
   - Confirm no unexpected raw backend exceptions leak through public managers.
2. **Path handling**
   - Validate absolute/relative path normalization and round-tripping.
   - Validate behavior parity for leading slash and nested path operations.
3. **Namespace composition**
   - Validate deterministic key prefixing and nested namespace behavior.
   - Validate consistency between repository and manager-level namespace contracts.

### Test Result Report Template

Use this template for Phase 8 sign-off:

```markdown
# Phase 8 Quality Gate Report

## Build Metadata
- Commit SHA:
- Date:
- Runner environment:

## Execution Summary
- Total test commands executed:
- Passed:
- Failed:
- Flaky:

## Coverage Summary
- Global line coverage:
- Global branch coverage:
- Per-area coverage table:

## Performance Summary
- Benchmark baseline reference:
- Current median/p95 by scenario:
- Regression delta (%):
- Threshold breaches:

## High-Risk Regression Results
- Error translation: PASS/FAIL
- Path handling: PASS/FAIL
- Namespace composition: PASS/FAIL

## Open Defects
- P0:
- P1:
- P2+:
- Deferred with approval:

## Final Gate Decision
- Status: PASS / FAIL
- Release recommendation:
- Required follow-ups:
```

## Definition of Done

Phase 8 is complete only when all conditions are true:

- All required suites (unit, integration, coverage, performance) pass under the command matrix.
- Coverage thresholds are met globally and for each package area listed in this document.
- High-risk regression checks are green for error translation, path handling, and namespace composition.
- No unresolved release-blocking defects remain.
- Final quality gate report is published and archived.

**Done means:** the refactored branch meets quality bars and is release-ready without compatibility exceptions.
