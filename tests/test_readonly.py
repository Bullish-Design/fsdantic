"""Tests for read-only workspace mode (``Fsdantic.open(readonly=True)``).

The read-only mode is enforced at two layers:

- the fsdantic manager APIs raise ``WorkspaceError(WORKSPACE_READONLY)``
  early (clear errors at the API boundary);
- the connection guard (``_ReadonlyGuard``) rejects raw write statements on
  ``Workspace.connection`` and swallows the AgentFS SDK's atime maintenance
  write so reads never write.
"""

import pytest

from fsdantic import Fsdantic, WorkspaceError

pytestmark = pytest.mark.asyncio

# Table set created by the AgentFS SDK schema init (Filesystem/KvStore/ToolCalls).
_SDK_TABLES = {
    "fs_config",
    "fs_inode",
    "fs_dentry",
    "fs_data",
    "fs_symlink",
    "kv_store",
    "tool_calls",
}


def _collect_tree_paths(node: dict, acc: set[str]) -> None:
    acc.add(node["path"])
    for child in node["children"]:
        _collect_tree_paths(child, acc)


class TestReadonlyWritesRejected:
    async def test_readonly_open_rejects_writes(self, temp_db_path):
        """Manager write APIs raise WorkspaceError on a readonly workspace."""
        writer = await Fsdantic.open(path=temp_db_path)
        await writer.files.write("/base.txt", "base")
        await writer.close()

        ro = await Fsdantic.open(path=temp_db_path, readonly=True)
        try:
            with pytest.raises(WorkspaceError) as exc_info:
                await ro.files.write("/new.txt", "x")
            assert exc_info.value.code == "WORKSPACE_READONLY"

            with pytest.raises(WorkspaceError) as exc_info:
                await ro.files.remove("/base.txt")
            assert exc_info.value.code == "WORKSPACE_READONLY"

            with pytest.raises(WorkspaceError) as exc_info:
                await ro.files.write_many([("/a.txt", "a")])
            assert exc_info.value.code == "WORKSPACE_READONLY"

            with pytest.raises(WorkspaceError) as exc_info:
                await ro.kv.set("key", 1)
            assert exc_info.value.code == "WORKSPACE_READONLY"

            with pytest.raises(WorkspaceError) as exc_info:
                await ro.kv.delete("missing-key")
            assert exc_info.value.code == "WORKSPACE_READONLY"

            with pytest.raises(WorkspaceError) as exc_info:
                await ro.kv.set_many([("k", 1)])
            assert exc_info.value.code == "WORKSPACE_READONLY"

            with pytest.raises(WorkspaceError) as exc_info:
                await ro.kv.delete_many(["k"])
            assert exc_info.value.code == "WORKSPACE_READONLY"

            with pytest.raises(WorkspaceError) as exc_info:
                await ro.overlay.merge(ro.raw)
            assert exc_info.value.code == "WORKSPACE_READONLY"

            with pytest.raises(WorkspaceError) as exc_info:
                await ro.overlay.reset()
            assert exc_info.value.code == "WORKSPACE_READONLY"

            with pytest.raises(WorkspaceError) as exc_info:
                await ro.materialize.to_disk(temp_db_path)
            assert exc_info.value.code == "WORKSPACE_READONLY"

            # Empty batches do no work and are allowed to return normally.
            assert await ro.files.write_many([]) is not None
            assert await ro.kv.set_many([]) is not None
            assert await ro.kv.delete_many([]) is not None
        finally:
            await ro.close()

    async def test_readonly_raw_connection_rejects_writes(self, temp_db_path):
        """Raw writes through ``workspace.connection`` are rejected (no manager
        bypass)."""
        writer = await Fsdantic.open(path=temp_db_path)
        await writer.files.write("/base.txt", "base")
        await writer.close()

        ro = await Fsdantic.open(path=temp_db_path, readonly=True)
        try:
            # Not the atime statement (that one is swallowed); a generic
            # fs_inode write must raise.
            with pytest.raises(WorkspaceError) as exc_info:
                await ro.connection.execute(
                    "UPDATE fs_inode SET mtime = 1"
                    " WHERE ino = (SELECT ino FROM fs_dentry WHERE name = 'base.txt')"
                )
            assert exc_info.value.code == "WORKSPACE_READONLY"

            with pytest.raises(WorkspaceError) as exc_info:
                await ro.connection.execute("INSERT INTO kv_store (key, value) VALUES ('x', 'y')")
            assert exc_info.value.code == "WORKSPACE_READONLY"

            with pytest.raises(WorkspaceError) as exc_info:
                await ro.connection.executescript("CREATE TABLE should_not_exist (id INTEGER)")
            assert exc_info.value.code == "WORKSPACE_READONLY"

            # Read statements still pass through.
            cursor = await ro.connection.execute("SELECT 1")
            assert (await cursor.fetchone())[0] == 1
        finally:
            await ro.close()


class TestReadonlyReadsWork:
    async def test_readonly_read_skips_atime_write(self, temp_db_path):
        """Reads on a readonly workspace perform no atime UPDATE.

        This doubles as the atime-SQL pin: if the AgentFS SDK ever changes
        its access-time maintenance statement (``UPDATE fs_inode SET atime``
        inside ``Filesystem.read_file``), the guard's swallow misses and the
        read raises under ``PRAGMA query_only = 1``.
        """
        writer = await Fsdantic.open(path=temp_db_path)
        try:
            await writer.files.write("/readonly_atime.txt", "payload")

            # Pin the inode's atime to a sentinel so any atime write by the
            # readonly read is observable even within the same second.
            # (libSQL does not support subqueries inside UPDATE, so resolve
            # the inode with a SELECT first.)
            cursor = await writer.connection.execute(
                "SELECT ino FROM fs_dentry WHERE name = 'readonly_atime.txt'"
            )
            row = await cursor.fetchone()
            assert row is not None
            ino = row[0]
            await writer.connection.execute("UPDATE fs_inode SET atime = 12345 WHERE ino = ?", (ino,))
            await writer.connection.commit()

            reader = await Fsdantic.open(path=temp_db_path, readonly=True)
            try:
                # The read must succeed (no lock contention) and return the
                # payload.
                content = await reader.files.read("/readonly_atime.txt", mode="text")
                assert content == "payload"
            finally:
                await reader.close()

            # The atime must be unchanged: the readonly read never wrote.
            cursor = await writer.connection.execute("SELECT atime FROM fs_inode WHERE ino = ?", (ino,))
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 12345
        finally:
            await writer.close()

    async def test_readonly_workspace_inspector(self, temp_db_path):
        """End-to-end read-only inspection: tree/list/read/stat/exists work."""
        writer = await Fsdantic.open(path=temp_db_path)
        await writer.files.write("/docs/guide.md", "# Guide")
        await writer.files.write("/data/config.json", '{"a": 1}')
        await writer.close()

        ro = await Fsdantic.open(path=temp_db_path, readonly=True)
        try:
            tree = await ro.files.tree()
            paths: set[str] = set()
            _collect_tree_paths(tree, paths)
            assert "/" in paths
            assert "/docs" in paths
            assert "/docs/guide.md" in paths
            assert "/data/config.json" in paths

            assert set(await ro.files.list_dir("/")) == {"docs", "data"}
            assert await ro.files.read("/docs/guide.md", mode="text") == "# Guide"
            assert (await ro.files.stat("/docs/guide.md")).size == 7
            assert await ro.files.exists("/data/config.json") is True
            assert await ro.files.exists("/missing.txt") is False

            # KV reads and queries are allowed too.
            assert await ro.kv.get("missing", default=None) is None
            assert await ro.files.search("**/*.json") == ["/data/config.json"]
        finally:
            await ro.close()


class TestReadonlyOpenBehavior:
    async def test_readonly_schema_initialized(self, temp_db_path):
        """Schema init ran in pass-through: all tables and WAL mode exist."""
        writer = await Fsdantic.open(path=temp_db_path)
        await writer.close()

        ro = await Fsdantic.open(path=temp_db_path, readonly=True)
        try:
            conn = ro.connection
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
            tables = {row[0] for row in await cursor.fetchall()}
            assert _SDK_TABLES <= tables

            cursor = await conn.execute("PRAGMA journal_mode")
            result = await cursor.fetchone()
            assert result is not None and result[0] == "wal"
        finally:
            await ro.close()

    async def test_readonly_flag_propagates(self, temp_db_path):
        """The readonly flag propagates from workspace to all managers."""
        writer = await Fsdantic.open(path=temp_db_path)
        await writer.close()

        ro = await Fsdantic.open(path=temp_db_path, readonly=True)
        try:
            assert ro.readonly is True
            assert ro.files.readonly is True
            assert ro.kv.readonly is True
            assert ro.overlay.readonly is True
            assert ro.materialize.readonly is True
            # Child namespaces inherit the flag.
            assert ro.kv.namespace("app:").readonly is True
        finally:
            await ro.close()

    async def test_readonly_open_missing_db_raises(self, temp_db_path):
        """Readonly open of a nonexistent DB raises WORKSPACE_NOT_FOUND."""
        with pytest.raises(WorkspaceError) as exc_info:
            await Fsdantic.open(path=temp_db_path, readonly=True)
        assert exc_info.value.code == "WORKSPACE_NOT_FOUND"
