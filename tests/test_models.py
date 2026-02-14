"""Tests for agentfs_pydantic models."""

from datetime import datetime, timedelta

import pytest
from agentfs_pydantic import (
    AgentFSOptions,
    FileEntry,
    FileStats,
    KVEntry,
    ToolCall,
    ToolCallStats,
    ViewQuery,
)


def test_agentfs_options_with_id():
    """Test AgentFSOptions with id parameter."""
    options = AgentFSOptions(id="test-agent")
    assert options.id == "test-agent"
    assert options.path is None


def test_agentfs_options_with_path():
    """Test AgentFSOptions with path parameter."""
    options = AgentFSOptions(path="/tmp/test.db")
    assert options.path == "/tmp/test.db"
    assert options.id is None


def test_agentfs_options_requires_id_or_path():
    """Test that AgentFSOptions requires either id or path."""
    with pytest.raises(ValueError, match="Either 'id' or 'path' must be provided"):
        AgentFSOptions()


def test_file_stats():
    """Test FileStats model."""
    now = datetime.now()
    stats = FileStats(
        size=1024,
        mtime=now,
        is_file=True,
        is_directory=False
    )

    assert stats.size == 1024
    assert stats.mtime == now
    assert stats.is_file is True
    assert stats.is_directory is False
    assert stats.is_dir() is False


def test_file_entry():
    """Test FileEntry model."""
    now = datetime.now()
    stats = FileStats(
        size=1024,
        mtime=now,
        is_file=True,
        is_directory=False
    )

    entry = FileEntry(
        path="/test/file.txt",
        stats=stats,
        content="test content"
    )

    assert entry.path == "/test/file.txt"
    assert entry.stats == stats
    assert entry.content == "test content"


def test_file_entry_minimal():
    """Test FileEntry with minimal data."""
    entry = FileEntry(path="/test/file.txt")

    assert entry.path == "/test/file.txt"
    assert entry.stats is None
    assert entry.content is None


def test_tool_call():
    """Test ToolCall model."""
    now = datetime.now()
    call = ToolCall(
        id=1,
        name="search",
        parameters={"query": "test"},
        result={"results": ["result1"]},
        status="success",
        started_at=now,
        completed_at=now,
        duration_ms=123.45
    )

    assert call.id == 1
    assert call.name == "search"
    assert call.parameters == {"query": "test"}
    assert call.result == {"results": ["result1"]}
    assert call.status == "success"
    assert call.error is None


def test_tool_call_requires_error_for_error_status():
    """ToolCall should require error message when status is error."""
    now = datetime.now()
    with pytest.raises(ValueError, match="error is required when status is 'error'"):
        ToolCall(
            id=2,
            name="search",
            status="error",
            started_at=now,
            completed_at=now,
        )


def test_tool_call_requires_result_for_success_status():
    """ToolCall should require result payload when status is success."""
    now = datetime.now()
    with pytest.raises(ValueError, match="result is required when status is 'success'"):
        ToolCall(
            id=3,
            name="search",
            status="success",
            started_at=now,
            completed_at=now,
        )


def test_tool_call_computes_duration_when_missing():
    """ToolCall should compute duration from started/completed timestamps."""
    started = datetime.now()
    completed = started + timedelta(seconds=1.25)
    call = ToolCall(
        id=4,
        name="search",
        status="success",
        result={"results": []},
        started_at=started,
        completed_at=completed,
    )

    assert call.duration_ms == pytest.approx(1250.0)


def test_tool_call_prefers_explicit_duration():
    """ToolCall should preserve explicit duration over computed value."""
    started = datetime.now()
    completed = started + timedelta(seconds=2)
    call = ToolCall(
        id=5,
        name="search",
        status="success",
        result={"results": []},
        started_at=started,
        completed_at=completed,
        duration_ms=99.0,
    )

    assert call.duration_ms == 99.0


def test_tool_call_coerces_legacy_status_strings():
    """ToolCall should coerce known legacy status strings."""
    now = datetime.now()
    call = ToolCall(
        id=6,
        name="search",
        status="failed",
        error="boom",
        started_at=now,
        completed_at=now,
    )

    assert call.status == "error"


def test_tool_call_stats():
    """Test ToolCallStats model."""
    stats = ToolCallStats(
        name="search",
        total_calls=100,
        successful=95,
        failed=5,
        avg_duration_ms=123.45
    )

    assert stats.name == "search"
    assert stats.total_calls == 100
    assert stats.successful == 95
    assert stats.failed == 5
    assert stats.avg_duration_ms == 123.45


def test_kv_entry():
    """Test KVEntry model."""
    entry = KVEntry(
        key="user:123",
        value={"name": "Alice", "age": 30}
    )

    assert entry.key == "user:123"
    assert entry.value == {"name": "Alice", "age": 30}


def test_view_query_defaults():
    """Test ViewQuery with default values."""
    query = ViewQuery()

    assert query.path_pattern == "*"
    assert query.recursive is True
    assert query.include_content is False
    assert query.include_stats is True
    assert query.regex_pattern is None
    assert query.max_size is None
    assert query.min_size is None


def test_view_query_custom():
    """Test ViewQuery with custom values."""
    query = ViewQuery(
        path_pattern="*.py",
        recursive=False,
        include_content=True,
        include_stats=False,
        regex_pattern=r"^/src/",
        max_size=10000,
        min_size=100
    )

    assert query.path_pattern == "*.py"
    assert query.recursive is False
    assert query.include_content is True
    assert query.include_stats is False
    assert query.regex_pattern == r"^/src/"
    assert query.max_size == 10000
    assert query.min_size == 100


def test_model_serialization():
    """Test that models can be serialized to JSON."""
    options = AgentFSOptions(id="test-agent")
    json_data = options.model_dump_json()

    assert "test-agent" in json_data

    # Test deserialization
    restored = AgentFSOptions.model_validate_json(json_data)
    assert restored.id == "test-agent"


def test_view_query_rejects_negative_min_size():
    """Test ViewQuery rejects negative min_size."""
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        ViewQuery(min_size=-1)


def test_view_query_rejects_negative_max_size():
    """Test ViewQuery rejects negative max_size."""
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        ViewQuery(max_size=-1)


def test_view_query_rejects_min_size_greater_than_max_size():
    """Test ViewQuery rejects invalid size ranges."""
    with pytest.raises(ValueError, match="min_size must be less than or equal to max_size"):
        ViewQuery(min_size=100, max_size=10)


def test_view_query_pattern_matching_corner_cases():
    """Test ViewQuery pattern matching across basename and recursive patterns."""
    query = ViewQuery(path_pattern="*.py")

    assert query.matches_path("/main.py") is True
    assert query.matches_path("/src/main.py") is True
    assert query.matches_path("/src/main.txt") is False

    rooted_query = ViewQuery(path_pattern="/data/*.json")
    assert rooted_query.matches_path("/data/a.json") is True
    assert rooted_query.matches_path("/data/sub/a.json") is False

    recursive_query = ViewQuery(path_pattern="/data/**/*.json")
    assert recursive_query.matches_path("/data/sub/a.json") is True
    assert recursive_query.matches_path("/data/a.json") is True


def test_view_query_regex_matcher_optional_and_compiled():
    """Test regex matcher behavior with and without regex_pattern."""
    no_regex = ViewQuery()
    assert no_regex.matches_regex("/anything") is True

    regex = ViewQuery(regex_pattern=r"\.py$")
    assert regex.matches_regex("/src/main.py") is True
    assert regex.matches_regex("/src/main.txt") is False
