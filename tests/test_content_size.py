"""Tests for open-time content-size caps (``Fsdantic.open(max_content_bytes=...)``).

``max_content_bytes`` caps write payloads at the API boundary:
``files.write``/``write_many`` measure the encoded payload; ``kv.set``/
``set_many`` measure the serialized JSON text (what actually gets stored).
Oversized payloads raise ``WorkspaceError`` with ``code="CONTENT_TOO_LARGE"``
before any storage is touched.  ``None`` (default) is unbounded.
"""

import pytest

from fsdantic import Fsdantic, WorkspaceError

pytestmark = pytest.mark.asyncio


class TestContentCap:
    async def test_write_over_cap_raises(self, temp_db_path):
        """A payload larger than the cap raises and leaves the file absent."""
        ws = await Fsdantic.open(path=temp_db_path, max_content_bytes=10)
        try:
            with pytest.raises(WorkspaceError) as exc_info:
                await ws.files.write("/big.txt", "x" * 11)
            assert exc_info.value.code == "CONTENT_TOO_LARGE"
            assert await ws.files.exists("/big.txt") is False
        finally:
            await ws.close()

    async def test_write_at_cap_ok(self, temp_db_path):
        """The boundary value passes."""
        ws = await Fsdantic.open(path=temp_db_path, max_content_bytes=10)
        try:
            await ws.files.write("/ok.txt", "x" * 10)
            assert await ws.files.read("/ok.txt", mode="text") == "x" * 10
        finally:
            await ws.close()

    async def test_write_binary_and_json_respected(self, temp_db_path):
        """Binary and JSON payloads are measured in encoded bytes."""
        ws = await Fsdantic.open(path=temp_db_path, max_content_bytes=5)
        try:
            with pytest.raises(WorkspaceError) as exc_info:
                await ws.files.write("/bin.bin", b"123456", mode="binary")
            assert exc_info.value.code == "CONTENT_TOO_LARGE"

            with pytest.raises(WorkspaceError):
                await ws.files.write("/data.json", {"a": "long"}, mode="json")

            await ws.files.write("/ok.txt", "12345")
            assert await ws.files.read("/ok.txt", mode="text") == "12345"
        finally:
            await ws.close()

    async def test_write_many_reports_oversized_items(self, temp_db_path):
        """write_many reports oversized items per-item without aborting the
        batch."""
        ws = await Fsdantic.open(path=temp_db_path, max_content_bytes=5)
        try:
            result = await ws.files.write_many([("/ok.txt", "ab"), ("/big.txt", "x" * 10)])
            by_path = {item.key_or_path: item for item in result.items}
            assert by_path["/ok.txt"].ok is True
            assert by_path["/big.txt"].ok is False
            assert "CONTENT_TOO_LARGE" not in by_path["/big.txt"].error  # str error
            assert await ws.files.exists("/ok.txt") is True
            assert await ws.files.exists("/big.txt") is False
        finally:
            await ws.close()

    async def test_kv_set_cap(self, temp_db_path):
        """kv.set measures the serialized JSON payload against the cap."""
        ws = await Fsdantic.open(path=temp_db_path, max_content_bytes=8)
        try:
            with pytest.raises(WorkspaceError) as exc_info:
                await ws.kv.set("k", "x" * 20)
            assert exc_info.value.code == "CONTENT_TOO_LARGE"
            assert await ws.kv.exists("k") is False

            await ws.kv.set("small", "ab")  # fits (2 bytes)
            assert await ws.kv.get("small") == "ab"

            # set_many reports oversized items per-item (no abort).
            result = await ws.kv.set_many([("k2", "x" * 20)])
            assert result.items[0].ok is False
            assert await ws.kv.exists("k2") is False
        finally:
            await ws.close()

    async def test_default_unbounded(self, temp_db_path):
        """No cap configured -> existing behavior unchanged."""
        ws = await Fsdantic.open(path=temp_db_path)
        try:
            assert ws.max_content_bytes is None
            payload = "x" * (10 * 1024 * 1024)
            await ws.files.write("/big.txt", payload)
            assert (await ws.files.stat("/big.txt")).size == len(payload)

            await ws.kv.set("big", "y" * (1024 * 1024))
            assert len(await ws.kv.get("big")) == 1024 * 1024
        finally:
            await ws.close()

    async def test_cap_propagates_to_managers(self, temp_db_path):
        """The cap is exposed on the workspace and both managers."""
        ws = await Fsdantic.open(path=temp_db_path, max_content_bytes=42)
        try:
            assert ws.max_content_bytes == 42
            assert ws.files.max_content_bytes == 42
            assert ws.kv.max_content_bytes == 42
            assert ws.kv.namespace("app:").max_content_bytes == 42
        finally:
            await ws.close()
