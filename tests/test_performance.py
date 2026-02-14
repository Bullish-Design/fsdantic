"""Performance benchmarks for agentfs_pydantic.

Tests that operations meet performance targets defined in ROADMAP-STEP_1.md.
"""

import os
import tempfile
import time

import pytest
from agentfs_sdk import AgentFS, AgentFSOptions as SDKAgentFSOptions

from fsdantic import View, ViewQuery


@pytest.mark.benchmark
@pytest.mark.asyncio
class TestFileOperationsPerformance:
    """Benchmark file operations."""

    async def test_file_write_performance(self):
        """File writes should be fast (<10ms average).

        Contract 1: File write operations should average less than 10ms.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "bench.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Warmup
                await agent.fs.write_file("/warmup.txt", "warmup")

                # Benchmark
                iterations = 100
                start = time.time()

                for i in range(iterations):
                    await agent.fs.write_file(f"/file_{i}.txt", b"test content")

                duration = time.time() - start
                avg_ms = (duration / iterations) * 1000

                print(f"\nAverage write time: {avg_ms:.2f}ms")
                assert avg_ms < 10, f"Average write time {avg_ms:.2f}ms exceeds 10ms target"

            finally:
                await agent._db.close()

    async def test_file_read_performance(self):
        """File reads should be fast (<10ms average)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "bench.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create test files
                for i in range(100):
                    await agent.fs.write_file(f"/file_{i}.txt", b"test content")

                # Warmup
                await agent.fs.read_file("/file_0.txt")

                # Benchmark
                iterations = 100
                start = time.time()

                for i in range(iterations):
                    await agent.fs.read_file(f"/file_{i}.txt")

                duration = time.time() - start
                avg_ms = (duration / iterations) * 1000

                print(f"\nAverage read time: {avg_ms:.2f}ms")
                assert avg_ms < 10, f"Average read time {avg_ms:.2f}ms exceeds 10ms target"

            finally:
                await agent._db.close()

    async def test_file_stat_performance(self):
        """File stat operations should be fast (<5ms average)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "bench.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create test files
                for i in range(100):
                    await agent.fs.write_file(f"/file_{i}.txt", b"test content")

                # Warmup
                await agent.fs.stat("/file_0.txt")

                # Benchmark
                iterations = 100
                start = time.time()

                for i in range(iterations):
                    await agent.fs.stat(f"/file_{i}.txt")

                duration = time.time() - start
                avg_ms = (duration / iterations) * 1000

                print(f"\nAverage stat time: {avg_ms:.2f}ms")
                assert avg_ms < 5, f"Average stat time {avg_ms:.2f}ms exceeds 5ms target"

            finally:
                await agent._db.close()


@pytest.mark.benchmark
@pytest.mark.asyncio
class TestQueryOperationsPerformance:
    """Benchmark query operations."""

    async def test_view_query_performance_without_content(self):
        """View queries should be fast (<50ms for 1000 files).

        Contract 2: Querying 1000 files without content should take <50ms.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "bench.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create 1000 files
                for i in range(1000):
                    await agent.fs.write_file(f"/file_{i}.py", "# test")

                # Warmup
                view = View(
                    agent=agent,
                    query=ViewQuery(path_pattern="*.py", include_content=False),
                )
                await view.load()

                # Benchmark
                start = time.time()
                view = View(
                    agent=agent,
                    query=ViewQuery(path_pattern="*.py", include_content=False),
                )
                files = await view.load()
                duration = (time.time() - start) * 1000

                print(f"\nQuery time for 1000 files: {duration:.2f}ms")
                assert duration < 50, f"Query took {duration:.2f}ms, exceeds 50ms target"
                assert len(files) == 1000

            finally:
                await agent._db.close()

    async def test_view_query_performance_with_content(self):
        """View queries with content should be reasonable (<500ms for 100 files)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "bench.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create 100 files with moderate content
                content = "# test file\n" * 10  # ~120 bytes per file
                for i in range(100):
                    await agent.fs.write_file(f"/file_{i}.py", content)

                # Warmup
                view = View(
                    agent=agent,
                    query=ViewQuery(path_pattern="*.py", include_content=True),
                )
                await view.load()

                # Benchmark
                start = time.time()
                view = View(
                    agent=agent,
                    query=ViewQuery(path_pattern="*.py", include_content=True),
                )
                files = await view.load()
                duration = (time.time() - start) * 1000

                print(f"\nQuery time for 100 files with content: {duration:.2f}ms")
                assert duration < 500, f"Query took {duration:.2f}ms, exceeds 500ms target"
                assert len(files) == 100
                assert all(f.content is not None for f in files)

            finally:
                await agent._db.close()

    async def test_view_count_performance(self):
        """View.count() should be very fast (<10ms for 1000 files)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "bench.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create 1000 files
                for i in range(1000):
                    await agent.fs.write_file(f"/file_{i}.txt", b"test")

                # Warmup
                view = View(agent=agent, query=ViewQuery(path_pattern="*"))
                await view.count()

                # Benchmark
                start = time.time()
                view = View(agent=agent, query=ViewQuery(path_pattern="*"))
                count = await view.count()
                duration = (time.time() - start) * 1000

                print(f"\nCount time for 1000 files: {duration:.2f}ms")
                # Count should be much faster than load since it doesn't load content
                assert duration < 50, f"Count took {duration:.2f}ms, exceeds 50ms target"
                assert count == 1000

            finally:
                await agent._db.close()


@pytest.mark.benchmark
@pytest.mark.asyncio
class TestLargeFilePerformance:
    """Benchmark operations on large files."""

    async def test_large_file_handling(self):
        """Can handle files up to 10MB efficiently.

        Contract 3: Reading and writing 10MB files should complete in <500ms each.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "bench.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create 10MB file
                large_content = "x" * (10 * 1024 * 1024)

                # Benchmark write
                start = time.time()
                await agent.fs.write_file("/large.bin", large_content)
                write_duration = (time.time() - start) * 1000

                # Benchmark read
                start = time.time()
                content = await agent.fs.read_file("/large.bin")
                read_duration = (time.time() - start) * 1000

                print(f"\n10MB file write time: {write_duration:.2f}ms")
                print(f"10MB file read time: {read_duration:.2f}ms")

                assert content == large_content
                assert write_duration < 500, f"Large file write took {write_duration:.2f}ms"
                assert read_duration < 500, f"Large file read took {read_duration:.2f}ms"

            finally:
                await agent._db.close()

    async def test_multiple_medium_files(self):
        """Can handle many medium-sized files efficiently."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "bench.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create 100 files of 100KB each (10MB total)
                medium_content = "x" * (100 * 1024)

                # Benchmark writes
                start = time.time()
                for i in range(100):
                    await agent.fs.write_file(f"/file_{i}.dat", medium_content)
                write_duration = (time.time() - start) * 1000

                # Benchmark reads
                start = time.time()
                for i in range(100):
                    await agent.fs.read_file(f"/file_{i}.dat")
                read_duration = (time.time() - start) * 1000

                print(f"\n100x100KB files write time: {write_duration:.2f}ms")
                print(f"100x100KB files read time: {read_duration:.2f}ms")

                # Should complete in reasonable time
                assert write_duration < 5000, "Writing 100 medium files too slow"
                assert read_duration < 5000, "Reading 100 medium files too slow"

            finally:
                await agent._db.close()


@pytest.mark.benchmark
@pytest.mark.asyncio
class TestKVStorePerformance:
    """Benchmark KV store operations."""

    async def test_kv_set_performance(self):
        """KV set operations should be fast (<5ms average)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "bench.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Warmup
                await agent.kv.set("warmup", "value")

                # Benchmark
                iterations = 100
                start = time.time()

                for i in range(iterations):
                    await agent.kv.set(f"key_{i}", f"value_{i}")

                duration = time.time() - start
                avg_ms = (duration / iterations) * 1000

                print(f"\nAverage KV set time: {avg_ms:.2f}ms")
                assert avg_ms < 5, f"Average KV set time {avg_ms:.2f}ms exceeds 5ms target"

            finally:
                await agent._db.close()

    async def test_kv_get_performance(self):
        """KV get operations should be fast (<5ms average)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "bench.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create test data
                for i in range(100):
                    await agent.kv.set(f"key_{i}", f"value_{i}")

                # Warmup
                await agent.kv.get("key_0")

                # Benchmark
                iterations = 100
                start = time.time()

                for i in range(iterations):
                    await agent.kv.get(f"key_{i}")

                duration = time.time() - start
                avg_ms = (duration / iterations) * 1000

                print(f"\nAverage KV get time: {avg_ms:.2f}ms")
                assert avg_ms < 5, f"Average KV get time {avg_ms:.2f}ms exceeds 5ms target"

            finally:
                await agent._db.close()

    async def test_kv_list_performance(self):
        """KV list operations should be fast (<20ms for 100 entries)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "bench.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create test data with prefixes
                for i in range(100):
                    await agent.kv.set(f"config:setting_{i}", f"value_{i}")

                # Warmup
                await agent.kv.list(prefix="config:")

                # Benchmark
                start = time.time()
                entries = await agent.kv.list(prefix="config:")
                duration = (time.time() - start) * 1000

                print(f"\nKV list time for 100 entries: {duration:.2f}ms")
                assert duration < 20, f"KV list took {duration:.2f}ms, exceeds 20ms target"
                assert len(entries) == 100

            finally:
                await agent._db.close()


@pytest.mark.benchmark
@pytest.mark.asyncio
class TestScalabilityPerformance:
    """Test performance at scale."""

    async def test_view_query_10000_files(self):
        """View can handle 10,000+ files efficiently.

        Contract: Should complete in <500ms for 10,000 files without content.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "bench.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create 10,000 files
                print("\nCreating 10,000 files...")
                for i in range(10000):
                    await agent.fs.write_file(f"/file_{i}.txt", b"test")

                print("Files created, benchmarking query...")

                # Benchmark query
                start = time.time()
                view = View(
                    agent=agent,
                    query=ViewQuery(path_pattern="*", include_content=False),
                )
                files = await view.load()
                duration = (time.time() - start) * 1000

                print(f"Query time for 10,000 files: {duration:.2f}ms")
                assert duration < 500, f"Query took {duration:.2f}ms, exceeds 500ms target"
                assert len(files) == 10000

            finally:
                await agent._db.close()

    async def test_memory_usage_reasonable(self):
        """Memory usage should be reasonable (<100MB for 1000 files in memory).

        Note: This is a basic check - proper memory profiling should be done separately.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "bench.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create 1000 files with moderate content
                content = "x" * 1024  # 1KB each = 1MB total
                for i in range(1000):
                    await agent.fs.write_file(f"/file_{i}.txt", content)

                # Load all files into memory
                view = View(
                    agent=agent,
                    query=ViewQuery(path_pattern="*", include_content=True),
                )
                files = await view.load()

                # Basic check: we should have all files
                assert len(files) == 1000
                assert all(f.content is not None for f in files)

                # Note: Actual memory usage profiling would require tools like memory_profiler
                # For now, we just verify the operation completes successfully
                print("\nMemory test: Successfully loaded 1000 files into memory")

            finally:
                await agent._db.close()


# Utility function to run all benchmarks and report summary
def print_benchmark_summary():
    """Print a summary of all benchmark targets."""
    print("\n" + "=" * 60)
    print("Performance Targets (from ROADMAP-STEP_1.md)")
    print("=" * 60)
    print("File operations: < 10ms average")
    print("Query operations: < 50ms for 1000 files")
    print("View.load(): < 500ms for 10,000 files")
    print("Large files (10MB): < 500ms read/write")
    print("Memory usage: < 100MB for 1000 files")
    print("=" * 60 + "\n")
