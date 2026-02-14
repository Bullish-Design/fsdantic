"""Pytest configuration and fixtures for fsdantic tests."""

import os
import tempfile
from datetime import datetime
from typing import AsyncGenerator

import pytest
from agentfs_sdk import AgentFS, AgentFSOptions as SDKAgentFSOptions


@pytest.fixture
def sample_file_content():
    """Sample file content for testing."""
    return "This is a test file content."


@pytest.fixture
def sample_json_data():
    """Sample JSON data for testing."""
    return {"name": "test", "value": 123, "active": True}


@pytest.fixture
def temp_db_path():
    """Provide a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "test.db")


@pytest.fixture
async def agent_fs():
    """Provide a fresh AgentFS instance."""
    with tempfile.TemporaryDirectory() as tmpdir:
        agent_path = os.path.join(tmpdir, "agent.db")
        agent = await AgentFS.open(SDKAgentFSOptions(path=agent_path))
        try:
            yield agent
        finally:
            await agent._db.close()


@pytest.fixture
async def stable_fs():
    """Provide a stable/base AgentFS instance."""
    with tempfile.TemporaryDirectory() as tmpdir:
        stable_path = os.path.join(tmpdir, "stable.db")
        stable = await AgentFS.open(SDKAgentFSOptions(path=stable_path))
        try:
            yield stable
        finally:
            await stable._db.close()


@pytest.fixture
async def agent_with_files(agent_fs):
    """Provide AgentFS with sample files already created."""
    # Create sample file structure
    await agent_fs.fs.write_file("/test.txt", "test content")
    await agent_fs.fs.write_file("/data/config.json", '{"key": "value"}')
    await agent_fs.fs.write_file("/src/main.py", "print('hello')")
    await agent_fs.fs.write_file("/src/utils.py", "def helper(): pass")
    await agent_fs.fs.write_file("/docs/README.md", "# Documentation")
    return agent_fs


@pytest.fixture
async def stable_with_base_files(stable_fs):
    """Provide stable FS with base files."""
    await stable_fs.fs.write_file("/base.txt", "base content")
    await stable_fs.fs.write_file("/config/settings.json", '{"theme": "light"}')
    await stable_fs.fs.write_file("/lib/shared.py", "# Shared library")
    return stable_fs


@pytest.fixture
def sample_records():
    """Provide sample record data for repository tests."""
    return [
        {"id": "user1", "name": "Alice", "email": "alice@example.com", "age": 30},
        {"id": "user2", "name": "Bob", "email": "bob@example.com", "age": 25},
        {"id": "user3", "name": "Charlie", "email": "charlie@example.com", "age": 35},
    ]


@pytest.fixture
def large_file_content():
    """Generate large file content for testing."""
    return "x" * (10 * 1024 * 1024)  # 10MB


@pytest.fixture
def temp_workspace_dir():
    """Provide a temporary workspace directory for materialization."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir
