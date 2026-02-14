"""Basic tests for AgentFS Python SDK"""

import os
import tempfile

import pytest

from agentfs_sdk import AgentFS, AgentFSOptions


@pytest.mark.asyncio
async def test_agentfs_open_with_id():
    """Test opening AgentFS with an ID"""
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        os.chdir(tmpdir)

        try:
            agentfs = await AgentFS.open(AgentFSOptions(id="test-agent"))
            assert agentfs is not None
            assert agentfs.kv is not None
            assert agentfs.fs is not None
            assert agentfs.tools is not None

            await agentfs.close()
        finally:
            os.chdir(old_cwd)


@pytest.mark.asyncio
async def test_agentfs_open_with_path():
    """Test opening AgentFS with a custom path"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        agentfs = await AgentFS.open(AgentFSOptions(path=db_path))
        assert agentfs is not None

        await agentfs.close()
        assert os.path.exists(db_path)


@pytest.mark.asyncio
async def test_kvstore_basic():
    """Test basic key-value operations"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        agentfs = await AgentFS.open(AgentFSOptions(path=db_path))

        # Set and get
        await agentfs.kv.set("test_key", "test_value")
        value = await agentfs.kv.get("test_key")
        assert value == "test_value"

        # Set complex object
        obj = {"name": "Alice", "age": 30}
        await agentfs.kv.set("user", obj)
        retrieved = await agentfs.kv.get("user")
        assert retrieved == obj

        # Delete
        await agentfs.kv.delete("test_key")
        value = await agentfs.kv.get("test_key")
        assert value is None

        await agentfs.close()


@pytest.mark.asyncio
async def test_filesystem_basic():
    """Test basic filesystem operations"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        agentfs = await AgentFS.open(AgentFSOptions(path=db_path))

        # Write and read file
        await agentfs.fs.write_file("/test.txt", "Hello, World!")
        content = await agentfs.fs.read_file("/test.txt")
        assert content == "Hello, World!"

        # Create nested file (auto-create parent dirs)
        await agentfs.fs.write_file("/dir1/dir2/file.txt", "nested")
        content = await agentfs.fs.read_file("/dir1/dir2/file.txt")
        assert content == "nested"

        # List directory
        files = await agentfs.fs.readdir("/dir1")
        assert "dir2" in files

        # Get stats
        stats = await agentfs.fs.stat("/test.txt")
        assert stats.is_file()
        assert stats.size == len("Hello, World!")

        await agentfs.close()


@pytest.mark.asyncio
async def test_toolcalls_basic():
    """Test basic tool call tracking"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        agentfs = await AgentFS.open(AgentFSOptions(path=db_path))

        # Record a tool call
        import time

        start = int(time.time())
        end = start + 1

        call_id = await agentfs.tools.record(
            "test_tool", start, end, parameters={"param": "value"}, result={"result": "success"}
        )

        assert call_id > 0

        # Get the tool call
        call = await agentfs.tools.get(call_id)
        assert call is not None
        assert call.name == "test_tool"
        assert call.parameters == {"param": "value"}
        assert call.result == {"result": "success"}
        assert call.status == "success"

        # Get stats
        stats = await agentfs.tools.get_stats()
        assert len(stats) == 1
        assert stats[0].name == "test_tool"
        assert stats[0].total_calls == 1

        await agentfs.close()


@pytest.mark.asyncio
async def test_context_manager():
    """Test using AgentFS as a context manager"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")

        async with await AgentFS.open(AgentFSOptions(path=db_path)) as agentfs:
            await agentfs.kv.set("test", "value")
            value = await agentfs.kv.get("test")
            assert value == "value"

        # Database should be closed after exiting context
