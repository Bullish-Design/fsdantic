"""Tests for the atomic KV increment API (``KVManager.increment``).

Same-process increments are serialized per key via ``asyncio.Lock``.
Cross-process (MVCC) increments can still race on the read-modify-write —
documented limitation; the per-key SQL CAS in ``TypedKVRepository`` is the
cross-process-safe primitive.
"""

import asyncio

import pytest

from fsdantic import Fsdantic, SerializationError, WorkspaceError

pytestmark = pytest.mark.asyncio


class TestIncrement:
    async def test_increment_creates_missing_key(self, temp_db_path):
        """Missing keys start at 0: first increment returns the amount."""
        ws = await Fsdantic.open(path=temp_db_path)
        try:
            assert await ws.kv.increment("counter") == 1
            assert await ws.kv.increment("counter") == 2
            assert await ws.kv.get("counter") == 2
        finally:
            await ws.close()

    async def test_increment_amount(self, temp_db_path):
        """A custom amount is applied; float values keep floating-point math."""
        ws = await Fsdantic.open(path=temp_db_path)
        try:
            await ws.kv.set("counter", 5)
            assert await ws.kv.increment("counter", amount=3) == 8
            assert await ws.kv.increment("counter", amount=0.5) == 8.5
        finally:
            await ws.close()

    async def test_increment_negative(self, temp_db_path):
        """Negative amounts decrement."""
        ws = await Fsdantic.open(path=temp_db_path)
        try:
            await ws.kv.set("counter", 5)
            assert await ws.kv.increment("counter", -1) == 4
            assert await ws.kv.increment("counter", -10) == -6
        finally:
            await ws.close()

    async def test_increment_non_numeric_raises(self, temp_db_path):
        """Non-numeric stored values raise SerializationError (bool too)."""
        ws = await Fsdantic.open(path=temp_db_path)
        try:
            await ws.kv.set("counter", "not-a-number")
            with pytest.raises(SerializationError):
                await ws.kv.increment("counter")

            await ws.kv.set("flag", True)
            with pytest.raises(SerializationError):
                await ws.kv.increment("flag")
        finally:
            await ws.close()

    async def test_increment_many_concurrent(self, temp_db_path):
        """50 concurrent increments on one key yield exactly 50 (no lost
        updates)."""
        ws = await Fsdantic.open(path=temp_db_path)
        try:
            results = await asyncio.gather(*(ws.kv.increment("counter") for _ in range(50)))
            assert await ws.kv.get("counter") == 50
            # Each caller saw a distinct new value (1..50) — no two
            # read-modify-write cycles collided.
            assert sum(results) == 50 * 51 // 2
        finally:
            await ws.close()

    async def test_increment_readonly_rejects(self, temp_db_path):
        """Increment is a write: rejected on read-only workspaces."""
        writer = await Fsdantic.open(path=temp_db_path)
        await writer.close()

        ro = await Fsdantic.open(path=temp_db_path, readonly=True)
        try:
            with pytest.raises(WorkspaceError) as exc_info:
                await ro.kv.increment("counter")
            assert exc_info.value.code == "WORKSPACE_READONLY"
        finally:
            await ro.close()
