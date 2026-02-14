"""Performance and scalability checks.

This module intentionally splits:
1. Correctness-oriented behavior checks (always run)
2. Environment-dependent timing benchmarks (opt-in via markers)
"""

import os
import tempfile
import time
from collections.abc import Awaitable, Callable

import pytest
from agentfs_sdk import AgentFS, AgentFSOptions as SDKAgentFSOptions

from fsdantic import View, ViewQuery


STRICT_BENCHMARKS = os.getenv("FSDANTIC_STRICT_BENCHMARKS", "0") == "1"


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

        start = time.perf_counter()
        files = await View(
            agent=perf_agent,
            query=ViewQuery(path_pattern="*.py", include_content=False),
        ).load()
        duration_ms = (time.perf_counter() - start) * 1000

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

        start = time.perf_counter()
        count = await View(agent=perf_agent, query=ViewQuery(path_pattern="*")).count()
        duration_ms = (time.perf_counter() - start) * 1000

        assert count == 2000
        target_ms = _target(default_ms=700.0, strict_ms=200.0)
        assert duration_ms < target_ms, (
            f"View.count() took {duration_ms:.2f}ms, target {target_ms:.2f}ms "
            f"(strict={STRICT_BENCHMARKS})"
        )
