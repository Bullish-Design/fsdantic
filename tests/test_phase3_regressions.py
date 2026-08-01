"""Regression tests for Phase 3: KV layer O(1) existence and serialization.

Covers:
- M5: missing-key ops never invoke the O(n) kv.list prefix scan.
- M6: datetime/bytes/Enum/Path values survive raw KV and typed repository saves.
"""

import base64
import os
from datetime import datetime
from enum import Enum
from pathlib import Path

import pytest
from pydantic import BaseModel

from fsdantic import (
    KVManager,
    SerializationError,
    ToolCall,
    ToolCallStatus,
    TypedKVRepository,
)


class Color(Enum):
    """Test enum for serialization round-trips."""

    RED = "red"
    BLUE = "blue"


class ByteRecord(BaseModel):
    """Model with a bytes field (round-trips via mode='json')."""

    name: str
    payload: bytes


class EnumRecord(BaseModel):
    """Model with an enum and a Path field."""

    color: Color
    location: Path


@pytest.mark.asyncio
class TestMissingKeyConstantTime:
    """M5: missing-key ops must not call kv.list."""

    async def test_missing_key_get_is_constant_time(self, agent_fs):
        kv = KVManager(agent_fs)

        called: list[str] = []
        orig_list = agent_fs.kv.list

        async def spy_list(prefix):
            called.append(prefix)
            return await orig_list(prefix)

        agent_fs.kv.list = spy_list
        try:
            assert await kv.get("missing", default=None) is None
            with pytest.raises(Exception):
                await kv.get("missing")
            assert await kv.delete("missing") is False
            assert await kv.exists("missing") is False
        finally:
            agent_fs.kv.list = orig_list

        assert called == [], f"kv.list was invoked {len(called)} times on misses"

    async def test_stored_null_get_returns_none_not_missing(self, agent_fs):
        kv = KVManager(agent_fs)
        await kv.set("nul", None)
        assert await kv.get("nul") is None
        assert await kv.exists("nul") is True
        assert await kv.delete("nul") is True
        assert await kv.delete("nul") is False

    async def test_delete_missing_key_false(self, agent_fs):
        kv = KVManager(agent_fs)
        assert await kv.delete("never-existed") is False

    async def test_existence_check_works_on_sdk_created_db(self, agent_fs):
        """The direct SQL existence check works on a DB created by the SDK."""
        kv = KVManager(agent_fs)
        await kv.set("k", {"v": 1})
        assert await kv.get("k") == {"v": 1}
        assert await kv.get("k2", default=None) is None


@pytest.mark.asyncio
class TestKVSerialization:
    """M6: non-JSON values must be storable via raw KV and typed repos."""

    async def test_kv_set_datetime_roundtrip_raw(self, agent_fs):
        kv = KVManager(agent_fs)
        dt = datetime(2024, 5, 6, 7, 8, 9)
        await kv.set("dt", {"ts": dt})
        stored = await kv.get("dt")
        assert stored == {"ts": "2024-05-06T07:08:09"}
        assert stored["ts"] == dt.isoformat()

    async def test_repo_save_toolcall_roundtrip(self, agent_fs):
        repo = TypedKVRepository[ToolCall](agent_fs, prefix="tc:")
        tc = ToolCall(
            id=1,
            name="search",
            status=ToolCallStatus.SUCCESS,
            result={"a": 1},
            started_at=datetime(2024, 1, 1),
            completed_at=datetime(2024, 1, 1, 0, 0, 1),
        )
        await repo.save("1", tc)

        loaded = await repo.load("1", ToolCall)
        assert loaded is not None
        assert loaded.started_at == tc.started_at
        assert loaded.completed_at == tc.completed_at
        assert loaded.duration_ms == 1000.0

    async def test_repo_save_model_with_bytes_field(self, agent_fs):
        repo = TypedKVRepository[ByteRecord](agent_fs, prefix="br:")
        record = ByteRecord(name="bin", payload=b"\x00\x01\xff")
        await repo.save("k", record)

        loaded = await repo.load("k", ByteRecord)
        assert loaded is not None
        assert loaded.payload == b"\x00\x01\xff"

    async def test_kv_set_enum_and_path(self, agent_fs):
        kv = KVManager(agent_fs)
        repo = TypedKVRepository[EnumRecord](agent_fs, prefix="er:")
        await kv.set("raw", {"color": Color.RED, "path": Path("/tmp/a")})
        assert await kv.get("raw") == {"color": "red", "path": "/tmp/a"}

        record = EnumRecord(color=Color.BLUE, location=Path("/data"))
        await repo.save("k", record)
        loaded = await repo.load("k", EnumRecord)
        assert loaded is not None
        assert loaded.color is Color.BLUE
        assert loaded.location == Path("/data")

    async def test_kv_set_bytes_marker_encoding(self, agent_fs):
        kv = KVManager(agent_fs)
        raw = b"\x01\x02\x03"
        await kv.set("bytes", {"data": raw})
        stored = await kv.get("bytes")
        assert stored == {"data": {"$fsdantic:bytes": base64.b64encode(raw).decode("ascii")}}

    async def test_kv_serialization_error_message_mentions_type(self, agent_fs):
        kv = KVManager(agent_fs)
        with pytest.raises(SerializationError) as exc_info:
            await kv.set("bad", object())
        assert "object" in str(exc_info.value).lower()

    async def test_raw_kv_nested_normalization(self, agent_fs):
        kv = KVManager(agent_fs)
        await kv.set("nested", {"inner": [1, datetime(2024, 1, 1)], "tup": (1, 2)})
        assert await kv.get("nested") == {"inner": [1, "2024-01-01T00:00:00"], "tup": [1, 2]}
