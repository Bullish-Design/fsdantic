"""Performance and scalability checks.

This module intentionally splits:
1. Correctness-oriented behavior checks (always run)
2. Environment-dependent timing benchmarks (opt-in via markers)
"""

import os
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
import json
import statistics

import pytest
from agentfs_sdk import AgentFS, AgentFSOptions as SDKAgentFSOptions

from fsdantic import View, ViewQuery


STRICT_BENCHMARKS = os.getenv("FSDANTIC_STRICT_BENCHMARKS", "0") == "1"
BENCHMARK_OUTPUT_PATH = os.getenv("FSDANTIC_BENCHMARK_OUTPUT")


@pytest.fixture
async def perf_agent():
    """Create an isolated AgentFS instance for tests in this module."""
    with tempfile.TemporaryDirectory() as tmpdir:
        agent_path = os.path.join(tmpdir, "bench.db")
        agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))
        try:
            yield agent
        finally:
            await agent._db.close()


def _target(default_ms: float, strict_ms: float) -> float:
    """Return timing target based on strict benchmark mode."""
    return strict_ms if STRICT_BENCHMARKS else default_ms


async def _average_ms(iterations: int, op: Callable[[int], Awaitable[object]]) -> float:
    """Measure average execution time for async operation."""
    start = time.perf_counter()
    for i in range(iterations):
        await op(i)
    duration = time.perf_counter() - start
    return (duration / iterations) * 1000


async def _median_ms(iterations: int, op: Callable[[int], Awaitable[object]]) -> float:
    """Measure median execution time (ms) for async operation."""
    samples: list[float] = []
    for i in range(iterations):
        start = time.perf_counter()
        await op(i)
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def _record_benchmark_metric(scenario: str, median_ms: float) -> None:
    """Persist benchmark metric so external gate tooling can parse it."""
    if not BENCHMARK_OUTPUT_PATH:
        return

    output_path = Path(BENCHMARK_OUTPUT_PATH)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, object]
    if output_path.exists():
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    else:
        payload = {"schema_version": 1, "unit": "ms", "scenarios": {}}

    scenarios = payload.setdefault("scenarios", {})
    if not isinstance(scenarios, dict):
        raise ValueError("Invalid benchmark artifact format: scenarios must be an object")

    scenarios[scenario] = {"median_ms": round(median_ms, 4)}
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


@pytest.mark.asyncio
class TestPerformanceCorrectness:
    """Correctness checks for performance-sensitive code paths."""

    async def test_view_count_matches_load_without_content(self, perf_agent):
        for i in range(200):
            await perf_agent.fs.write_file(f"/file_{i}.txt", b"test")

        view = View(agent=perf_agent, query=ViewQuery(path_pattern="*.txt", include_content=False))
        loaded = await view.load()
        count = await view.count()

        assert count == len(loaded) == 200

    async def test_view_count_applies_size_filters(self, perf_agent):
        await perf_agent.fs.write_file("/small.txt", b"x")
        await perf_agent.fs.write_file("/medium.txt", b"x" * 10)
        await perf_agent.fs.write_file("/large.txt", b"x" * 100)

        view = View(
            agent=perf_agent,
            query=ViewQuery(path_pattern="*.txt", min_size=5, max_size=50),
        )

        count = await view.count()
        loaded = await view.load()

        assert count == 1
        assert len(loaded) == 1
        assert loaded[0].path == "/medium.txt"

    async def test_view_without_stats_does_not_populate_stats(self, perf_agent):
        await perf_agent.fs.write_file("/a.txt", b"hello")
        await perf_agent.fs.write_file("/b.txt", b"world")

        view = View(
            agent=perf_agent,
            query=ViewQuery(path_pattern="*.txt", include_stats=False, include_content=False),
        )
        files = await view.load()

        assert len(files) == 2
        assert all(f.stats is None for f in files)


@pytest.mark.benchmark
@pytest.mark.slow
@pytest.mark.asyncio
class TestMicrobenchmarks:
    """Environment-sensitive microbenchmarks.

    These tests are marked benchmark/slow so teams can exclude them by default:
      pytest -m "not benchmark"
    """

    async def test_file_write_average_latency(self, perf_agent):
        await perf_agent.fs.write_file("/warmup.txt", "warmup")

        avg_ms = await _average_ms(
            iterations=200,
            op=lambda i: perf_agent.fs.write_file(f"/file_{i}.txt", b"test content"),
        )
        median_ms = await _median_ms(
            iterations=200,
            op=lambda i: perf_agent.fs.write_file(f"/median_file_{i}.txt", b"test content"),
        )
        _record_benchmark_metric("file_write_average_latency", median_ms)

        target_ms = _target(default_ms=25.0, strict_ms=10.0)
        assert avg_ms < target_ms, (
            f"Average write latency {avg_ms:.2f}ms exceeded target {target_ms:.2f}ms "
            f"(strict={STRICT_BENCHMARKS})"
        )

    async def test_file_read_average_latency(self, perf_agent):
        for i in range(300):
            await perf_agent.fs.write_file(f"/file_{i}.txt", b"test content")

        await perf_agent.fs.read_file("/file_0.txt")

        avg_ms = await _average_ms(
            iterations=300,
            op=lambda i: perf_agent.fs.read_file(f"/file_{i}.txt"),
        )
        median_ms = await _median_ms(
            iterations=300,
            op=lambda i: perf_agent.fs.read_file(f"/file_{i}.txt"),
        )
        _record_benchmark_metric("file_read_average_latency", median_ms)

        target_ms = _target(default_ms=25.0, strict_ms=10.0)
        assert avg_ms < target_ms, (
            f"Average read latency {avg_ms:.2f}ms exceeded target {target_ms:.2f}ms "
            f"(strict={STRICT_BENCHMARKS})"
        )

    async def test_view_query_without_content_latency(self, perf_agent):
        for i in range(1500):
            await perf_agent.fs.write_file(f"/file_{i}.py", "# test")

        warmup = View(agent=perf_agent, query=ViewQuery(path_pattern="*.py", include_content=False))
        await warmup.load()

        samples: list[float] = []
        files = []
        for _ in range(5):
            start = time.perf_counter()
            files = await View(
                agent=perf_agent,
                query=ViewQuery(path_pattern="*.py", include_content=False),
            ).load()
            samples.append((time.perf_counter() - start) * 1000)
        duration_ms = statistics.median(samples)
        _record_benchmark_metric("view_query_without_content_latency", duration_ms)

        assert len(files) == 1500
        target_ms = _target(default_ms=600.0, strict_ms=150.0)
        assert duration_ms < target_ms, (
            f"View query (metadata only) took {duration_ms:.2f}ms, target {target_ms:.2f}ms "
            f"(strict={STRICT_BENCHMARKS})"
        )

    async def test_view_count_latency(self, perf_agent):
        for i in range(2000):
            await perf_agent.fs.write_file(f"/file_{i}.txt", b"test")

        await View(agent=perf_agent, query=ViewQuery(path_pattern="*")).count()

        samples: list[float] = []
        count = 0
        for _ in range(5):
            start = time.perf_counter()
            count = await View(agent=perf_agent, query=ViewQuery(path_pattern="*")).count()
            samples.append((time.perf_counter() - start) * 1000)
        duration_ms = statistics.median(samples)
        _record_benchmark_metric("view_count_latency", duration_ms)

        assert count == 2000
        target_ms = _target(default_ms=700.0, strict_ms=200.0)
        assert duration_ms < target_ms, (
            f"View.count() took {duration_ms:.2f}ms, target {target_ms:.2f}ms "
            f"(strict={STRICT_BENCHMARKS})"
        )
