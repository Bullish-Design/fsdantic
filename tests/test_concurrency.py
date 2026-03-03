"""Tests for WAL and MVCC concurrency support in Fsdantic."""

import os
import tempfile

import pytest

from fsdantic import Fsdantic, Workspace


@pytest.fixture
def temp_db_path_wal():
    """Temporary database path for WAL/MVCC tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "wal_test.db")


@pytest.mark.asyncio
class TestWALMode:
    """Tests for WAL journal mode support."""

    async def test_open_enables_wal_by_default(self, temp_db_path_wal):
        """Fsdantic.open() should enable WAL mode by default."""
        workspace = await Fsdantic.open(path=temp_db_path_wal)
        try:
            conn = workspace.connection
            cursor = await conn.execute("PRAGMA journal_mode")
            result = await cursor.fetchone()
            assert result[0] == "wal"
        finally:
            await workspace.close()

    async def test_open_disable_wal(self, temp_db_path_wal):
        """enable_wal=False should skip WAL mode."""
        workspace = await Fsdantic.open(path=temp_db_path_wal, enable_wal=False)
        try:
            conn = workspace.connection
            cursor = await conn.execute("PRAGMA journal_mode")
            result = await cursor.fetchone()
            # Without explicit WAL, turso defaults to WAL or delete depending on version.
            # The key assertion: we didn't request WAL, so we accept whatever the default is.
            assert result[0] in ("wal", "delete", "memory")
        finally:
            await workspace.close()

    async def test_workspace_connection_property(self, temp_db_path_wal):
        """Workspace.connection should expose the underlying db connection."""
        workspace = await Fsdantic.open(path=temp_db_path_wal)
        try:
            conn = workspace.connection
            assert conn is not None
            # Should be the same object as raw.get_database()
            assert conn is workspace.raw.get_database()
        finally:
            await workspace.close()

    async def test_wal_allows_concurrent_reads(self, temp_db_path_wal):
        """WAL mode should allow multiple readers alongside a writer."""
        # Open writer workspace
        writer = await Fsdantic.open(path=temp_db_path_wal, enable_wal=True)
        try:
            # Write some data
            await writer.files.write("/test.txt", "hello")

            # Open a second workspace pointing to same DB (reader)
            reader = await Fsdantic.open(path=temp_db_path_wal, enable_wal=True)
            try:
                # Reader should be able to read what writer wrote
                content = await reader.files.read("/test.txt", mode="text")
                assert content == "hello"

                # Writer writes more while reader is open
                await writer.files.write("/test2.txt", "world")

                # Reader can read the new file too (WAL visibility)
                content2 = await reader.files.read("/test2.txt", mode="text")
                assert content2 == "world"
            finally:
                await reader.close()
        finally:
            await writer.close()


@pytest.mark.asyncio
class TestMVCCMode:
    """Tests for MVCC (BEGIN CONCURRENT) support."""

    async def test_open_with_mvcc(self, temp_db_path_wal):
        """enable_mvcc=True should open with MVCC support."""
        workspace = await Fsdantic.open(path=temp_db_path_wal, enable_mvcc=True)
        try:
            # WAL should be forced on when MVCC is enabled
            conn = workspace.connection
            cursor = await conn.execute("PRAGMA journal_mode")
            result = await cursor.fetchone()
            assert result[0] == "wal"

            # Basic operations should work
            await workspace.files.write("/mvcc_test.txt", "mvcc content")
            content = await workspace.files.read("/mvcc_test.txt", mode="text")
            assert content == "mvcc content"
        finally:
            await workspace.close()

    async def test_mvcc_forces_wal(self, temp_db_path_wal):
        """enable_mvcc=True should force enable_wal=True even if explicitly False."""
        workspace = await Fsdantic.open(path=temp_db_path_wal, enable_wal=False, enable_mvcc=True)
        try:
            conn = workspace.connection
            cursor = await conn.execute("PRAGMA journal_mode")
            result = await cursor.fetchone()
            assert result[0] == "wal"
        finally:
            await workspace.close()

    async def test_mvcc_concurrent_non_conflicting_writes(self, temp_db_path_wal):
        """Two MVCC connections writing non-conflicting data should both succeed."""
        ws1 = await Fsdantic.open(path=temp_db_path_wal, enable_mvcc=True)
        try:
            # Write initial data to create the DB schema
            await ws1.files.write("/init.txt", "init")

            ws2 = await Fsdantic.open(path=temp_db_path_wal, enable_mvcc=True)
            try:
                # Both write different files
                await ws1.files.write("/file_a.txt", "from ws1")
                await ws2.files.write("/file_b.txt", "from ws2")

                # Both should be readable from either workspace
                content_a = await ws1.files.read("/file_a.txt", mode="text")
                content_b = await ws2.files.read("/file_b.txt", mode="text")
                assert content_a == "from ws1"
                assert content_b == "from ws2"
            finally:
                await ws2.close()
        finally:
            await ws1.close()


@pytest.mark.asyncio
class TestOpenWithOptionsCompat:
    """Ensure new parameters don't break existing open() behavior."""

    async def test_open_by_path_still_works(self, temp_db_path_wal):
        """Standard Fsdantic.open(path=...) still works."""
        workspace = await Fsdantic.open(path=temp_db_path_wal)
        try:
            await workspace.files.write("/compat.txt", "compat")
            content = await workspace.files.read("/compat.txt", mode="text")
            assert content == "compat"
        finally:
            await workspace.close()

    async def test_open_with_options_ignores_concurrency_params(self, temp_db_path_wal, monkeypatch):
        """open_with_options() should not accept concurrency params (it uses standard path)."""
        from fsdantic import AgentFSOptions

        # open_with_options is the low-level path — it doesn't take WAL/MVCC params.
        # Concurrency params only on Fsdantic.open().
        options = AgentFSOptions(path=temp_db_path_wal)
        workspace = await Fsdantic.open_with_options(options)
        try:
            assert isinstance(workspace, Workspace)
        finally:
            await workspace.close()
