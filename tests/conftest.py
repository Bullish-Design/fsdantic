"""Pytest configuration for agentfs_pydantic tests."""

import pytest


@pytest.fixture
def sample_file_content():
    """Sample file content for testing."""
    return "This is a test file content."


@pytest.fixture
def sample_json_data():
    """Sample JSON data for testing."""
    return {"name": "test", "value": 123, "active": True}
