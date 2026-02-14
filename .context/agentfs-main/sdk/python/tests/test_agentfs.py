"""AgentFS Integration Tests"""

import os
import tempfile

import pytest

from agentfs_sdk import AgentFS, AgentFSOptions


@pytest.mark.asyncio
class TestAgentFSIntegration:
    """Integration tests for AgentFS"""

    async def test_initialize_with_id(self):
        """Should successfully initialize with an id"""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)

            try:
                agent = await AgentFS.open(AgentFSOptions(id="test-agent"))
                assert agent is not None
                assert isinstance(agent, AgentFS)
                await agent.close()
            finally:
                os.chdir(old_cwd)

    async def test_initialize_with_path(self):
        """Should initialize with explicit path"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            agent = await AgentFS.open(AgentFSOptions(path=db_path))
            assert agent is not None
            assert isinstance(agent, AgentFS)
            await agent.close()
            assert os.path.exists(db_path)

    async def test_require_id_or_path(self):
        """Should require at least id or path"""
        with pytest.raises(ValueError, match="requires at least 'id' or 'path'"):
            await AgentFS.open(AgentFSOptions())

    async def test_multiple_instances_different_ids(self):
        """Should allow multiple instances with different ids"""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)

            try:
                agent1 = await AgentFS.open(AgentFSOptions(id="test-agent-1"))
                agent2 = await AgentFS.open(AgentFSOptions(id="test-agent-2"))

                assert agent1 is not None
                assert agent2 is not None
                assert agent1 is not agent2

                await agent1.close()
                await agent2.close()
            finally:
                os.chdir(old_cwd)

    async def test_database_persistence_to_agentfs_directory(self):
        """Should persist database file to .agentfs directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)

            try:
                agent = await AgentFS.open(AgentFSOptions(id="test-agent"))
                await agent.close()

                # Check that database file exists in .agentfs directory
                db_path = ".agentfs/test-agent.db"
                assert os.path.exists(db_path)
            finally:
                os.chdir(old_cwd)

    async def test_reuse_existing_database(self):
        """Should reuse existing database file with same id"""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)

            try:
                # Create first instance and write data
                agent1 = await AgentFS.open(AgentFSOptions(id="persistence-test"))
                await agent1.kv.set("test", "value1")
                await agent1.close()

                # Create second instance with same id - should be able to read the data
                agent2 = await AgentFS.open(AgentFSOptions(id="persistence-test"))
                value = await agent2.kv.get("test")

                assert agent1 is not None
                assert agent2 is not None
                assert value == "value1"

                await agent2.close()
            finally:
                os.chdir(old_cwd)

    async def test_context_manager(self):
        """Should work as a context manager"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")

            async with await AgentFS.open(AgentFSOptions(path=db_path)) as agent:
                await agent.kv.set("test", "value")
                value = await agent.kv.get("test")
                assert value == "value"

    async def test_get_database(self):
        """Should return the underlying database connection"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            agent = await AgentFS.open(AgentFSOptions(path=db_path))

            db = agent.get_database()
            assert db is not None

            await agent.close()

    async def test_validate_id_format(self):
        """Should validate agent ID format"""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)

            try:
                # Invalid characters in ID
                with pytest.raises(ValueError, match="alphanumeric characters"):
                    await AgentFS.open(AgentFSOptions(id="invalid id with spaces"))

                with pytest.raises(ValueError, match="alphanumeric characters"):
                    await AgentFS.open(AgentFSOptions(id="invalid@id"))
            finally:
                os.chdir(old_cwd)
