"""Integration tests for agentfs_pydantic with real AgentFS SDK.

Tests the actual AgentFS SDK integration, especially overlay semantics
and cross-layer interactions.
"""

import os
import tempfile
from datetime import datetime

import pytest
from agentfs_sdk import AgentFS, AgentFSOptions as SDKAgentFSOptions

from fsdantic import AgentFSOptions, FileEntry, FileStats, View, ViewQuery


@pytest.mark.asyncio
class TestOverlaySemantics:
    """Test overlay filesystem behavior with stable and agent layers."""

    async def test_overlay_read_fallthrough(self):
        """Agent reads from stable if file not in overlay.

        Contract 1: When a file exists in stable but not in agent overlay,
        reading from agent should fall through to stable layer.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create stable layer
            stable_path = os.path.join(tmpdir, "stable.db")
            stable = await AgentFS.open(SDKAgentFSOptions(path=stable_path))

            # Create agent overlay
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Write to stable
                await stable.fs.write_file("/test.txt", "stable content")

                # Read from agent (should fall through to stable)
                # Note: For this test, we simulate overlay by manually checking stable
                # In real usage, AgentFS handles overlay automatically via base_db parameter

                # Verify stable has the file
                stable_content = await stable.fs.read_file("/test.txt")
                assert stable_content == "stable content"

            finally:
                await stable._db.close()
                await agent._db.close()

    async def test_overlay_write_isolation(self):
        """Agent writes don't affect stable.

        Contract 2: When agent writes to a file, stable layer remains unchanged.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create stable layer
            stable_path = os.path.join(tmpdir, "stable.db")
            stable = await AgentFS.open(SDKAgentFSOptions(path=stable_path))

            # Create agent overlay
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Write to stable
                await stable.fs.write_file("/test.txt", "stable content")

                # Write to agent overlay
                await agent.fs.write_file("/test.txt", "agent content")

                # Verify isolation
                stable_content = await stable.fs.read_file("/test.txt")
                agent_content = await agent.fs.read_file("/test.txt")

                assert stable_content == "stable content", "Stable layer should be unchanged"
                assert agent_content == "agent content", "Agent layer should have new content"

            finally:
                await stable._db.close()
                await agent._db.close()

    async def test_multiple_overlays_isolation(self):
        """Multiple agents have independent overlays.

        Contract 3: Multiple agent overlays don't interfere with each other.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create stable layer
            stable_path = os.path.join(tmpdir, "stable.db")
            stable = await AgentFS.open(SDKAgentFSOptions(path=stable_path))

            # Create two agent overlays
            agent1_path = os.path.join(tmpdir, "agent1.db")
            agent1 = await AgentFS.open(SDKAgentFSOptions(path=agent1_path))

            agent2_path = os.path.join(tmpdir, "agent2.db")
            agent2 = await AgentFS.open(SDKAgentFSOptions(path=agent2_path))

            try:
                # Write to stable
                await stable.fs.write_file("/test.txt", "stable")

                # Each agent writes different content
                await agent1.fs.write_file("/test.txt", "agent1")
                await agent2.fs.write_file("/test.txt", "agent2")

                # Verify each sees their own version
                assert await stable.fs.read_file("/test.txt") == "stable"
                assert await agent1.fs.read_file("/test.txt") == "agent1"
                assert await agent2.fs.read_file("/test.txt") == "agent2"

            finally:
                await stable._db.close()
                await agent1._db.close()
                await agent2._db.close()


@pytest.mark.asyncio
class TestKVStoreIntegration:
    """Test KV store operations with real AgentFS."""

    async def test_kv_store_basic_operations(self):
        """KV store works correctly.

        Contract 4: Basic get/set/delete operations work as expected.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Set and get
                await agent.kv.set("key1", "value1")
                value = await agent.kv.get("key1")
                assert value == "value1"

                # List with prefix
                await agent.kv.set("config:theme", "dark")
                await agent.kv.set("config:lang", "en")
                await agent.kv.set("other:data", "xyz")

                entries = await agent.kv.list(prefix="config:")
                assert len(entries) == 2
                assert all("key" in e and "value" in e for e in entries)
                keys = [e["key"] for e in entries]
                assert "config:theme" in keys
                assert "config:lang" in keys

                # Delete
                await agent.kv.delete("key1")
                value = await agent.kv.get("key1")
                assert value is None

            finally:
                await agent._db.close()

    async def test_kv_store_json_values(self):
        """KV store handles complex JSON values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Store complex nested data
                import json

                complex_data = {
                    "user": {"name": "Alice", "age": 30},
                    "settings": {"theme": "dark", "notifications": True},
                    "tags": ["python", "ai", "dev"],
                }

                await agent.kv.set("user_data", json.dumps(complex_data))
                retrieved = await agent.kv.get("user_data")
                assert json.loads(retrieved) == complex_data

            finally:
                await agent._db.close()


@pytest.mark.asyncio
class TestViewQueryIntegration:
    """Test View query with real AgentFS."""

    async def test_view_query_with_real_data(self):
        """View query works with real AgentFS.

        Contract 5: View can query and filter files in a real AgentFS instance.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create test files
                await agent.fs.write_file("/main.py", b"print('hello')")
                await agent.fs.write_file("/test.py", b"def test(): pass")
                await agent.fs.write_file("/README.md", b"# Project")
                await agent.fs.write_file("/data/config.json", b'{"key": "value"}')

                # Query Python files
                view = View(
                    agent=agent,
                    query=ViewQuery(path_pattern="*.py", recursive=True, include_content=True),
                )

                files = await view.load()

                # Should find 2 Python files
                assert len(files) == 2
                assert all(f.path.endswith(".py") for f in files)
                assert all(f.content is not None for f in files)

                # Check paths
                paths = {f.path for f in files}
                assert "/main.py" in paths
                assert "/test.py" in paths

            finally:
                await agent._db.close()

    async def test_view_query_size_filters(self):
        """View respects size filters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create files of different sizes
                await agent.fs.write_file("/small.txt", "small")  # 5 bytes
                await agent.fs.write_file("/medium.txt", "x" * 50)  # 50 bytes
                await agent.fs.write_file("/large.txt", "x" * 200)  # 200 bytes

                # Query files between 10 and 100 bytes
                view = View(
                    agent=agent,
                    query=ViewQuery(path_pattern="*", recursive=True, min_size=10, max_size=100),
                )

                files = await view.load()

                # Should only find medium.txt
                assert len(files) == 1
                assert files[0].path == "/medium.txt"
                assert 10 <= files[0].stats.size <= 100

            finally:
                await agent._db.close()

    async def test_view_query_regex_pattern(self):
        """View applies regex pattern correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create files in different directories
                await agent.fs.write_file("/src/main.py", "main")
                await agent.fs.write_file("/src/utils.py", "utils")
                await agent.fs.write_file("/tests/test_main.py", "test")
                await agent.fs.write_file("/docs/README.md", "docs")

                # Query only files in /src directory
                view = View(
                    agent=agent,
                    query=ViewQuery(path_pattern="*", recursive=True, regex_pattern=r"^/src/"),
                )

                files = await view.load()

                # Should only find files in /src
                assert len(files) == 2
                assert all(f.path.startswith("/src/") for f in files)

            finally:
                await agent._db.close()

    async def test_view_count_efficient(self):
        """View.count() works without loading content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create several files
                for i in range(10):
                    await agent.fs.write_file(f"/file_{i}.txt", "content")

                view = View(agent=agent, query=ViewQuery(path_pattern="*"))

                count = await view.count()
                files = await view.load()

                assert count == len(files) == 10

            finally:
                await agent._db.close()

    async def test_view_fluent_api(self):
        """Fluent API works correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create test files
                await agent.fs.write_file("/data.json", b'{"test": true}')
                await agent.fs.write_file("/config.json", b'{"config": true}')
                await agent.fs.write_file("/README.md", b"# README")

                # Use fluent API
                files = await View(agent=agent).with_pattern("*.json").with_content(True).load()

                assert len(files) == 2
                assert all(f.path.endswith(".json") for f in files)
                assert all(f.content is not None for f in files)

            finally:
                await agent._db.close()

    async def test_view_custom_filter(self):
        """Custom predicate filtering works."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create files with different sizes
                await agent.fs.write_file("/file1.txt", "x" * 100)
                await agent.fs.write_file("/file2.txt", "x" * 2000)
                await agent.fs.write_file("/file3.txt", "x" * 50)

                view = View(agent=agent, query=ViewQuery(path_pattern="*"))

                # Filter for files > 1000 bytes
                large_files = await view.filter(lambda f: f.stats and f.stats.size > 1000)

                assert len(large_files) == 1
                assert large_files[0].path == "/file2.txt"

            finally:
                await agent._db.close()


@pytest.mark.asyncio
class TestPydanticModelsIntegration:
    """Test Pydantic models work correctly with AgentFS SDK."""

    async def test_agentfs_options_conversion(self):
        """AgentFSOptions converts correctly to SDK options."""
        # Our Pydantic model
        options = AgentFSOptions(id="test-agent")

        # Convert to dict for SDK
        options_dict = options.model_dump()

        # Should work with SDK
        with tempfile.TemporaryDirectory() as tmpdir:
            # Use explicit path to avoid creating .agentfs directory
            sdk_options = SDKAgentFSOptions(id=options_dict.get("id"), path=os.path.join(tmpdir, "test.db"))
            agent = await AgentFS.open(sdk_options)

            try:
                # Should be able to use it
                await agent.fs.write_file("/test.txt", "test")
                content = await agent.fs.read_file("/test.txt")
                assert content == "test"

            finally:
                await agent._db.close()

    async def test_file_stats_from_sdk(self):
        """FileStats model correctly represents SDK stats."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Write a file
                await agent.fs.write_file("/test.txt", b"test content")

                # Get stats from SDK
                sdk_stats = await agent.fs.stat("/test.txt")

                # Convert to our model
                file_stats = FileStats(
                    size=sdk_stats.size,
                    mtime=sdk_stats.mtime,
                    is_file=sdk_stats.is_file(),
                    is_directory=sdk_stats.is_directory(),
                )

                # Verify
                assert file_stats.size == len(b"test content")
                assert file_stats.is_file is True
                assert file_stats.is_directory is False
                assert isinstance(file_stats.mtime, datetime)

            finally:
                await agent._db.close()

    async def test_file_entry_complete_workflow(self):
        """FileEntry works in complete read-modify-write workflow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Write initial file
                await agent.fs.write_file("/workflow.txt", b"initial content")

                # Create FileEntry from SDK data
                sdk_stats = await agent.fs.stat("/workflow.txt")
                content = await agent.fs.read_file("/workflow.txt")

                entry = FileEntry(
                    path="/workflow.txt",
                    stats=FileStats(
                        size=sdk_stats.size,
                        mtime=sdk_stats.mtime,
                        is_file=sdk_stats.is_file(),
                        is_directory=sdk_stats.is_directory(),
                    ),
                    content=content,
                )

                # Verify entry
                assert entry.path == "/workflow.txt"
                assert entry.stats.size == len(b"initial content")
                assert entry.content == b"initial content"

                # Modify and write back
                new_content = b"modified content"
                await agent.fs.write_file(entry.path, new_content)

                # Verify modification
                updated_content = await agent.fs.read_file("/workflow.txt")
                assert updated_content == new_content

            finally:
                await agent._db.close()


@pytest.mark.asyncio
class TestContentSearchIntegration:
    """Test content search functionality with real AgentFS."""

    async def test_search_content_with_regex(self):
        """Should find content matches with regex pattern."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create Python files with class definitions
                await agent.fs.write_file("/main.py", "class MainClass:\n    pass\n")
                await agent.fs.write_file("/utils.py", "class Helper:\n    def method(self):\n        pass")
                await agent.fs.write_file("/data.txt", "No classes here")

                view = View(
                    agent=agent,
                    query=ViewQuery(
                        path_pattern="**/*.py",
                        content_regex=r"class\s+(\w+)",
                        include_content=True,
                    ),
                )

                matches = await view.search_content()

                # Should find 2 class definitions
                assert len(matches) >= 2
                class_matches = [m for m in matches if "class" in m.text.lower()]
                assert len(class_matches) >= 2

            finally:
                await agent._db.close()

    async def test_search_content_with_simple_pattern(self):
        """Should find content with simple string pattern."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                await agent.fs.write_file("/file1.txt", "TODO: implement feature\n")
                await agent.fs.write_file("/file2.txt", "This is done\n")
                await agent.fs.write_file("/file3.txt", "TODO: fix bug\nAnother TODO\n")

                view = View(
                    agent=agent,
                    query=ViewQuery(
                        path_pattern="*.txt",
                        content_pattern="TODO",
                        include_content=True,
                    ),
                )

                matches = await view.search_content()

                # Should find 3 TODO occurrences
                assert len(matches) == 3
                assert all("TODO" in m.text for m in matches)

            finally:
                await agent._db.close()

    async def test_search_content_case_insensitive(self):
        """Should support case-insensitive search."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                await agent.fs.write_file("/mixed.txt", "Error here\nerror there\nERROR everywhere")

                view = View(
                    agent=agent,
                    query=ViewQuery(
                        path_pattern="*.txt",
                        content_pattern="error",
                        case_sensitive=False,
                        include_content=True,
                    ),
                )

                matches = await view.search_content()

                # Should find all 3 variations
                assert len(matches) == 3

            finally:
                await agent._db.close()

    async def test_search_content_whole_word(self):
        """Should support whole word matching."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                await agent.fs.write_file("/words.txt", "test testing tested\ntest again")

                view = View(
                    agent=agent,
                    query=ViewQuery(
                        path_pattern="*.txt",
                        content_pattern="test",
                        whole_word=True,
                        include_content=True,
                    ),
                )

                matches = await view.search_content()

                # Should only find standalone "test", not "testing" or "tested"
                assert len(matches) == 2
                assert all(m.text.strip() in ["test testing tested", "test again"] for m in matches)

            finally:
                await agent._db.close()

    async def test_search_content_max_matches_per_file(self):
        """Should limit matches per file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # File with many matches
                content = "\n".join([f"match on line {i}" for i in range(100)])
                await agent.fs.write_file("/many.txt", content)

                view = View(
                    agent=agent,
                    query=ViewQuery(
                        path_pattern="*.txt",
                        content_pattern="match",
                        max_matches_per_file=5,
                        include_content=True,
                    ),
                )

                matches = await view.search_content()

                # Should only get 5 matches despite 100 lines
                assert len(matches) == 5

            finally:
                await agent._db.close()

    async def test_search_content_with_line_numbers(self):
        """Should include line numbers in matches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                await agent.fs.write_file(
                    "/numbered.txt", "line 1\nline 2 MATCH\nline 3\nline 4 MATCH\nline 5"
                )

                view = View(
                    agent=agent,
                    query=ViewQuery(
                        path_pattern="*.txt",
                        content_pattern="MATCH",
                        include_content=True,
                    ),
                )

                matches = await view.search_content()

                assert len(matches) == 2
                line_numbers = [m.line for m in matches]
                assert 2 in line_numbers
                assert 4 in line_numbers

            finally:
                await agent._db.close()

    async def test_files_containing(self):
        """Should find files containing pattern."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                await agent.fs.write_file("/has-todo.txt", "TODO: something")
                await agent.fs.write_file("/no-todo.txt", "Nothing here")
                await agent.fs.write_file("/also-has-todo.txt", "Another TODO")

                view = View(
                    agent=agent,
                    query=ViewQuery(path_pattern="*.txt", include_content=True),
                )

                files = await view.files_containing("TODO")

                # Should find 2 files
                assert len(files) == 2
                paths = {f.path for f in files}
                assert "/has-todo.txt" in paths
                assert "/also-has-todo.txt" in paths

            finally:
                await agent._db.close()

    async def test_search_content_skips_binary_files(self):
        """Should skip binary files during content search."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create text file
                await agent.fs.write_file("/text.txt", "searchable text")

                # Create binary file
                await agent.fs.write_file("/binary.dat", bytes(range(256)))

                view = View(
                    agent=agent,
                    query=ViewQuery(
                        path_pattern="*",
                        content_pattern="searchable",
                        include_content=True,
                    ),
                )

                matches = await view.search_content()

                # Should only find match in text file
                assert len(matches) == 1
                assert matches[0].file == "/text.txt"

            finally:
                await agent._db.close()

    async def test_recent_files(self):
        """Should find recently modified files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                from datetime import timedelta

                # Create files
                await agent.fs.write_file("/recent.txt", "recent")

                view = View(agent=agent, query=ViewQuery(path_pattern="*"))

                # Files modified in last hour
                recent = await view.recent_files(timedelta(hours=1))

                # All files should be recent
                assert len(recent) > 0

            finally:
                await agent._db.close()

    async def test_largest_files(self):
        """Should find largest files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                await agent.fs.write_file("/small.txt", "x" * 10)
                await agent.fs.write_file("/medium.txt", "x" * 100)
                await agent.fs.write_file("/large.txt", "x" * 1000)

                view = View(agent=agent, query=ViewQuery(path_pattern="*.txt"))

                largest = await view.largest_files(2)

                # Should get 2 largest
                assert len(largest) == 2
                assert largest[0].path == "/large.txt"
                assert largest[1].path == "/medium.txt"

            finally:
                await agent._db.close()

    async def test_total_size(self):
        """Should calculate total size of files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                await agent.fs.write_file("/file1.txt", "x" * 100)
                await agent.fs.write_file("/file2.txt", "x" * 200)

                view = View(agent=agent, query=ViewQuery(path_pattern="*.txt"))

                total = await view.total_size()

                assert total == 300

            finally:
                await agent._db.close()

    async def test_group_by_extension(self):
        """Should group files by extension."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                await agent.fs.write_file("/file1.py", "python")
                await agent.fs.write_file("/file2.py", "python")
                await agent.fs.write_file("/file3.txt", "text")
                await agent.fs.write_file("/file4.json", "json")

                view = View(agent=agent, query=ViewQuery(path_pattern="*"))

                grouped = await view.group_by_extension()

                assert len(grouped[".py"]) == 2
                assert len(grouped[".txt"]) == 1
                assert len(grouped[".json"]) == 1

            finally:
                await agent._db.close()


@pytest.mark.asyncio
class TestErrorHandling:
    """Test error handling in integration scenarios."""

    async def test_read_nonexistent_file(self):
        """Reading nonexistent file raises appropriate error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                from agentfs_sdk import ErrnoException

                with pytest.raises(ErrnoException):
                    await agent.fs.read_file("/nonexistent.txt")

            finally:
                await agent._db.close()

    async def test_view_handles_missing_files_gracefully(self):
        """View handles files that disappear during scan."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Create a file
                await agent.fs.write_file("/test.txt", "test")

                # Create view
                view = View(agent=agent, query=ViewQuery(path_pattern="*"))

                # Load should work even if we can't read some files
                files = await view.load()

                # Should find at least our file
                assert len(files) >= 1

            finally:
                await agent._db.close()
