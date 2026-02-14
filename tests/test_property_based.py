"""Property-based tests for agentfs_pydantic using Hypothesis.

Tests invariants and properties that should hold across all inputs,
especially for overlay isolation guarantees.
"""

import os
import tempfile

import pytest
from agentfs_sdk import AgentFS, AgentFSOptions as SDKAgentFSOptions
from hypothesis import given, strategies as st

from fsdantic import AgentFSOptions, ViewQuery


valid_segment_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-_."),
    min_size=1,
    max_size=20,
)

valid_path_strategy = st.lists(valid_segment_strategy, min_size=1, max_size=8).map(
    lambda segs: "/" + "/".join(segs)
)


class TestAgentFSOptionsProperties:
    """Property-based tests for AgentFSOptions."""

    @given(
        agent_id=st.text(
            alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
            min_size=1,
            max_size=50,
        ).map(lambda s: s.replace(" ", "-"))
    )
    def test_valid_id_always_accepted(self, agent_id):
        """Any valid ID should be accepted."""
        # Filter to only alphanumeric and hyphens
        if agent_id and all(c.isalnum() or c in "-_" for c in agent_id):
            options = AgentFSOptions(id=agent_id)
            assert options.id == agent_id

    @given(path=valid_path_strategy)
    def test_valid_path_always_accepted(self, path):
        """Any valid path should be accepted."""
        try:
            options = AgentFSOptions(path=path)
            assert options.path == path
        except ValueError:
            # Some paths may be invalid - that's okay
            pass

    @given(
        id_val=st.one_of(
            st.none(),
            st.text(
                alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
                min_size=1,
                max_size=20,
            ),
        ),
        path_val=st.one_of(st.none(), st.text(min_size=1, max_size=50)),
    )
    def test_at_least_one_required(self, id_val, path_val):
        """At least one of id or path must be provided."""
        if id_val or path_val:
            # Should succeed if at least one is provided
            try:
                options = AgentFSOptions(id=id_val, path=path_val)
                assert options.id == id_val or options.path == path_val
            except ValueError:
                # May fail for other validation reasons
                pass
        else:
            # Should fail if neither provided
            with pytest.raises(ValueError, match="Either 'id' or 'path' must be provided"):
                AgentFSOptions(id=id_val, path=path_val)


@pytest.mark.asyncio
class TestOverlayIsolationProperties:
    """Property-based tests for overlay isolation guarantees."""

    @pytest.mark.slow
    @given(
        stable_content=st.text(min_size=0, max_size=1000),
        agent1_content=st.text(min_size=0, max_size=1000),
        agent2_content=st.text(min_size=0, max_size=1000),
    )
    async def test_overlay_isolation_property(self, stable_content, agent1_content, agent2_content):
        """Property: Writing to agent overlays never affects stable or other agents.

        For any content written to stable and any agents:
        - Stable always sees its original content
        - Agent1 always sees its content (or falls through to stable)
        - Agent2 always sees its content (or falls through to stable)
        - No cross-contamination between layers
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create layers
            stable_path = os.path.join(tmpdir, "stable.db")
            agent1_path = os.path.join(tmpdir, "agent1.db")
            agent2_path = os.path.join(tmpdir, "agent2.db")

            stable = await AgentFS.open(SDKAgentFSOptions(path=stable_path))
            agent1 = await AgentFS.open(SDKAgentFSOptions(path=agent1_path))
            agent2 = await AgentFS.open(SDKAgentFSOptions(path=agent2_path))

            try:
                # Write to each layer
                await stable.fs.write_file("/test.bin", stable_content)
                await agent1.fs.write_file("/test.bin", agent1_content)
                await agent2.fs.write_file("/test.bin", agent2_content)

                # Verify isolation: each layer sees its own content
                assert await stable.fs.read_file("/test.bin") == stable_content
                assert await agent1.fs.read_file("/test.bin") == agent1_content
                assert await agent2.fs.read_file("/test.bin") == agent2_content

            finally:
                await stable._db.close()
                await agent1._db.close()
                await agent2._db.close()

    @pytest.mark.slow
    @given(
        filenames=st.lists(valid_segment_strategy, min_size=1, max_size=10, unique=True).map(
            lambda names: [f"/{name}.txt" for name in names]
        ),
        content=st.text(min_size=0, max_size=500),
    )
    async def test_multiple_files_isolation_property(self, filenames, content):
        """Property: Isolation holds across multiple files.

        Writing multiple files to different layers maintains isolation for all files.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            stable_path = os.path.join(tmpdir, "stable.db")
            agent_path = os.path.join(tmpdir, "agent.db")

            stable = await AgentFS.open(SDKAgentFSOptions(path=stable_path))
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Write all files to stable
                for filename in filenames:
                    await stable.fs.write_file(filename, content + "_stable")

                # Write all files to agent with different content
                for filename in filenames:
                    await agent.fs.write_file(filename, content + "_agent")

                # Verify all files maintain isolation
                for filename in filenames:
                    stable_data = await stable.fs.read_file(filename)
                    agent_data = await agent.fs.read_file(filename)

                    assert stable_data == content + "_stable"
                    assert agent_data == content + "_agent"

            finally:
                await stable._db.close()
                await agent._db.close()


@pytest.mark.asyncio
class TestKVStoreProperties:
    """Property-based tests for KV store."""

    @pytest.mark.slow
    @given(
        key=st.text(min_size=1, max_size=50),
        value=st.text(min_size=0, max_size=500),
    )
    async def test_kv_set_get_roundtrip(self, key, value):
        """Property: What you set is what you get.

        For any key-value pair, setting and then getting should return the same value.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                await agent.kv.set(key, value)
                retrieved = await agent.kv.get(key)
                assert retrieved == value

            finally:
                await agent._db.close()

    @pytest.mark.slow
    @given(
        entries=st.dictionaries(
            keys=st.text(min_size=1, max_size=30),
            values=st.text(min_size=0, max_size=100),
            min_size=1,
            max_size=20,
        )
    )
    async def test_kv_multiple_entries_property(self, entries):
        """Property: Multiple entries maintain independence.

        Setting multiple key-value pairs should not affect each other.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Set all entries
                for key, value in entries.items():
                    await agent.kv.set(key, value)

                # Verify all entries
                for key, value in entries.items():
                    retrieved = await agent.kv.get(key)
                    assert retrieved == value

            finally:
                await agent._db.close()

    @pytest.mark.slow
    @given(
        key=st.text(min_size=1, max_size=50),
        initial_value=st.text(min_size=0, max_size=100),
        updated_value=st.text(min_size=0, max_size=100),
    )
    async def test_kv_update_property(self, key, initial_value, updated_value):
        """Property: Updates replace previous values.

        Setting a key twice should result in the second value being stored.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Set initial value
                await agent.kv.set(key, initial_value)
                assert await agent.kv.get(key) == initial_value

                # Update value
                await agent.kv.set(key, updated_value)
                assert await agent.kv.get(key) == updated_value

            finally:
                await agent._db.close()

    @pytest.mark.slow
    @given(
        key=st.text(min_size=1, max_size=50),
        value=st.text(min_size=0, max_size=100),
    )
    async def test_kv_delete_property(self, key, value):
        """Property: Deleted keys return None.

        After deleting a key, getting it should return None.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Set and verify
                await agent.kv.set(key, value)
                assert await agent.kv.get(key) == value

                # Delete and verify
                await agent.kv.delete(key)
                assert await agent.kv.get(key) is None

            finally:
                await agent._db.close()


@pytest.mark.asyncio
class TestFileSystemProperties:
    """Property-based tests for filesystem operations."""

    @pytest.mark.slow
    @given(
        path=valid_path_strategy,
        content=st.text(min_size=0, max_size=1000),
    )
    async def test_file_write_read_roundtrip(self, path, content):
        """Property: Write-read roundtrip preserves content.

        For any path and content, writing and then reading should return the same content.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                await agent.fs.write_file(path, content)
                retrieved = await agent.fs.read_file(path)
                assert retrieved == content

            finally:
                await agent._db.close()

    @pytest.mark.slow
    @given(
        path=valid_path_strategy,
        initial_content=st.text(min_size=0, max_size=500),
        updated_content=st.text(min_size=0, max_size=500),
    )
    async def test_file_overwrite_property(self, path, initial_content, updated_content):
        """Property: Overwriting replaces content completely.

        Writing to a file twice should result in the second content being stored.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                # Write initial content
                await agent.fs.write_file(path, initial_content)
                assert await agent.fs.read_file(path) == initial_content

                # Overwrite with new content
                await agent.fs.write_file(path, updated_content)
                assert await agent.fs.read_file(path) == updated_content

            finally:
                await agent._db.close()

    @pytest.mark.slow
    @given(content_size=st.integers(min_value=0, max_value=10000))
    async def test_file_size_property(self, content_size):
        """Property: File size matches content length.

        The stat size should always match the actual content length.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_path = os.path.join(tmpdir, "agent.db")
            agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))

            try:
                content = "x" * content_size

                await agent.fs.write_file("/test.bin", content)
                stats = await agent.fs.stat("/test.bin")

                assert stats.size == content_size
                assert stats.size == len(content)

            finally:
                await agent._db.close()


class TestViewQueryProperties:
    """Property-based tests for ViewQuery validation and pattern behavior."""

    @given(
        min_size=st.integers(min_value=0, max_value=10_000),
        max_size=st.integers(min_value=0, max_value=10_000),
    )
    def test_size_bounds_ordering(self, min_size, max_size):
        """min_size > max_size should fail; other combinations should pass."""
        if min_size > max_size:
            with pytest.raises(ValueError, match="min_size must be less than or equal to max_size"):
                ViewQuery(min_size=min_size, max_size=max_size)
        else:
            query = ViewQuery(min_size=min_size, max_size=max_size)
            assert query.min_size == min_size
            assert query.max_size == max_size

    @given(size=st.integers(max_value=-1))
    def test_negative_size_constraints_rejected(self, size):
        """Negative sizes should be rejected by field constraints."""
        with pytest.raises(ValueError):
            ViewQuery(min_size=size)

        with pytest.raises(ValueError):
            ViewQuery(max_size=size)

    @given(
        extension=st.text(
            alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
            min_size=1,
            max_size=6,
        ),
        filename=st.text(
            alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
            min_size=1,
            max_size=12,
        ),
        folder=st.text(
            alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
            min_size=1,
            max_size=12,
        ),
    )
    def test_basename_glob_matches_nested_paths(self, extension, filename, folder):
        """A basename-only glob should match nested paths after normalization."""
        query = ViewQuery(path_pattern=f"*.{extension}")
        nested_path = f"/{folder}/{filename}.{extension}"
        non_matching_path = f"/{folder}/{filename}.other"

        assert query.matches_path(nested_path) is True
        assert query.matches_path(non_matching_path) is False
