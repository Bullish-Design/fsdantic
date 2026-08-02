"""Tests for overlay tombstones: deleting files in a merge target from the
source workspace.

A tombstone is a deletion intent recorded in a workspace's KV store
(``fsdantic:tombstone:<path>``) by ``OverlayManager.tombstone``, which also
removes the path from the workspace's own overlay.  ``merge`` replays the
*source* workspace's tombstones against the target filesystem, so a sandbox
can delete files in a stable workspace it pushes into.
"""

import pytest
from agentfs_sdk import ErrnoException

from fsdantic import Fsdantic, OverlayOperations, PermissionError, WorkspaceError

pytestmark = pytest.mark.asyncio


class _StubStats:
    def __init__(self, *, is_file=True, is_directory=False):
        self._is_file = is_file
        self._is_directory = is_directory

    def is_file(self):
        return self._is_file

    def is_directory(self):
        return self._is_directory


class _FailingTargetFS:
    """Duck-typed target filesystem whose removal operations always fail."""

    async def stat(self, path):
        return _StubStats()

    async def write_file(self, path, content):
        return None

    async def mkdir(self, path):
        return None

    async def readdir(self, path):
        return []

    async def rm(self, path, recursive=False):
        raise ErrnoException("EPERM", "rm", path=path, message="denied")

    async def unlink(self, path):
        raise ErrnoException("EPERM", "unlink", path=path, message="denied")


class _StubAgent:
    def __init__(self, fs):
        self.fs = fs


class _EPERMSourceFS:
    """Source whose stat fails with a non-ENOENT error for every path but
    the root."""

    async def stat(self, path):
        if path == "/":
            return _StubStats(is_file=False, is_directory=True)
        raise ErrnoException("EPERM", "stat", path=path, message="denied")

    async def readdir(self, path):
        return []


class _OtherKindSourceFS:
    """Source whose tombstoned path stats as neither file nor directory."""

    async def stat(self, path):
        if path == "/":
            return _StubStats(is_file=False, is_directory=True)
        return _StubStats(is_file=False, is_directory=False)

    async def readdir(self, path):
        return []


class _KVWithMarker:
    """KV store that reports one pre-recorded tombstone marker."""

    async def list(self, prefix):
        return [{"key": f"{prefix}/x.txt", "value": {"path": "/x.txt"}}]

    async def set(self, key, value):
        return None

    async def delete(self, key):
        return None

    async def get(self, key, default=None):
        return default


class _StubAgentWithKV:
    def __init__(self, fs, kv):
        self.fs = fs
        self.kv = kv


class TestTombstone:
    async def test_tombstone_removes_overlay_file_and_records_marker(self, temp_db_path):
        sandbox = await Fsdantic.open(path=temp_db_path)
        try:
            await sandbox.files.write("/x.txt", "content")
            assert await sandbox.files.exists("/x.txt")

            await sandbox.overlay.tombstone("/x.txt")

            assert await sandbox.files.exists("/x.txt") is False
            assert await sandbox.overlay.list_tombstones() == ["/x.txt"]
        finally:
            await sandbox.close()

    async def test_tombstone_missing_path_still_records_intent(self, temp_db_path):
        """A stable-only file can be tombstoned from a sandbox that never
        had it: the local removal is tolerated and the intent recorded."""
        sandbox = await Fsdantic.open(path=temp_db_path)
        try:
            await sandbox.overlay.tombstone("/legacy.txt")

            assert await sandbox.overlay.list_tombstones() == ["/legacy.txt"]
        finally:
            await sandbox.close()

    async def test_tombstone_normalizes_path(self, temp_db_path):
        sandbox = await Fsdantic.open(path=temp_db_path)
        try:
            await sandbox.files.write("/dir/file.txt", "x")

            await sandbox.overlay.tombstone("dir//./file.txt")

            assert await sandbox.overlay.list_tombstones() == ["/dir/file.txt"]
        finally:
            await sandbox.close()

    async def test_tombstone_directory_removes_recursively(self, temp_db_path):
        sandbox = await Fsdantic.open(path=temp_db_path)
        try:
            await sandbox.files.write("/dir/a.txt", "a")
            await sandbox.files.write("/dir/sub/b.txt", "b")

            await sandbox.overlay.tombstone("/dir")

            assert await sandbox.files.exists("/dir/a.txt") is False
            assert await sandbox.overlay.list_tombstones() == ["/dir"]
        finally:
            await sandbox.close()

    async def test_tombstone_root_rejected_and_no_marker(self, temp_db_path):
        """Tombstoning the filesystem root is rejected (SDK EPERM) and no
        marker is recorded."""
        sandbox = await Fsdantic.open(path=temp_db_path)
        try:
            with pytest.raises(PermissionError):
                await sandbox.overlay.tombstone("/")

            assert await sandbox.overlay.list_tombstones() == []
        finally:
            await sandbox.close()


class TestTombstoneMerge:
    async def test_merge_applies_tombstone_to_target(self, temp_db_path):
        """Pushing the sandbox into stable deletes the tombstoned file there."""
        stable = await Fsdantic.open(path=temp_db_path)
        await stable.files.write("/x.txt", "base")
        await stable.close()

        sandbox = await Fsdantic.open(path=f"{temp_db_path}.sandbox")
        await sandbox.overlay.tombstone("/x.txt")
        stable2 = await Fsdantic.open(path=temp_db_path)
        try:
            result = await stable2.overlay.merge(sandbox)

            assert result.tombstones_applied == 1
            assert result.files_merged == 0
            assert result.errors == []
            assert await stable2.files.exists("/x.txt") is False
        finally:
            await stable2.close()
            await sandbox.close()

    async def test_merge_mixed_files_and_tombstones(self, temp_db_path):
        """A merge both copies new files and applies tombstones; both counts
        are reported."""
        stable = await Fsdantic.open(path=temp_db_path)
        await stable.files.write("/gone.txt", "base")
        await stable.close()

        sandbox = await Fsdantic.open(path=f"{temp_db_path}.sandbox")
        await sandbox.files.write("/gone.txt", "sandbox")
        await sandbox.overlay.tombstone("/gone.txt")
        await sandbox.files.write("/new.txt", "new")
        stable2 = await Fsdantic.open(path=temp_db_path)
        try:
            result = await stable2.overlay.merge(sandbox)

            assert result.files_merged == 1
            assert result.tombstones_applied == 1
            assert result.errors == []
            assert await stable2.files.exists("/gone.txt") is False
            assert await stable2.files.read("/new.txt") == "new"
        finally:
            await stable2.close()
            await sandbox.close()

    async def test_merge_recreated_file_overrides_tombstone(self, temp_db_path):
        """Re-creating a tombstoned file in the source makes the marker
        inert: the file phase copies it and the tombstone is skipped."""
        stable = await Fsdantic.open(path=temp_db_path)
        await stable.files.write("/x.txt", "base")
        await stable.close()

        sandbox = await Fsdantic.open(path=f"{temp_db_path}.sandbox")
        await sandbox.files.write("/x.txt", "v1")
        await sandbox.overlay.tombstone("/x.txt")
        await sandbox.files.write("/x.txt", "v2")  # re-created
        stable2 = await Fsdantic.open(path=temp_db_path)
        try:
            result = await stable2.overlay.merge(sandbox)

            assert result.files_merged == 1
            assert result.tombstones_applied == 0
            assert result.errors == []
            assert await stable2.files.read("/x.txt") == "v2"
        finally:
            await stable2.close()
            await sandbox.close()

    async def test_merge_scopes_tombstones_to_merge_path(self, temp_db_path):
        """merge(path=...) only applies tombstones at or under that root."""
        stable = await Fsdantic.open(path=temp_db_path)
        await stable.files.write("/outside.txt", "o")
        await stable.files.write("/sub/inside.txt", "i")
        await stable.close()

        sandbox = await Fsdantic.open(path=f"{temp_db_path}.sandbox")
        await sandbox.overlay.tombstone("/outside.txt")
        await sandbox.overlay.tombstone("/sub/inside.txt")
        await sandbox.files.write("/sub/exists.txt", "e")
        stable2 = await Fsdantic.open(path=temp_db_path)
        try:
            result = await stable2.overlay.merge(sandbox, path="/sub")

            assert result.tombstones_applied == 1
            assert result.errors == []
            assert await stable2.files.exists("/sub/inside.txt") is False
            assert await stable2.files.exists("/outside.txt") is True
            assert await stable2.files.read("/sub/exists.txt") == "e"
        finally:
            await stable2.close()
            await sandbox.close()

    async def test_merge_markers_persist_and_merge_is_idempotent(self, temp_db_path):
        """Applying a tombstone does not consume the marker; a re-merge
        re-applies it without errors."""
        stable = await Fsdantic.open(path=temp_db_path)
        await stable.files.write("/x.txt", "base")
        await stable.close()

        sandbox = await Fsdantic.open(path=f"{temp_db_path}.sandbox")
        await sandbox.overlay.tombstone("/x.txt")
        stable2 = await Fsdantic.open(path=temp_db_path)
        try:
            first = await stable2.overlay.merge(sandbox)
            assert first.tombstones_applied == 1
            assert await sandbox.overlay.list_tombstones() == ["/x.txt"]

            second = await stable2.overlay.merge(sandbox)
            assert second.tombstones_applied == 1
            assert second.errors == []
            assert await stable2.files.exists("/x.txt") is False
        finally:
            await stable2.close()
            await sandbox.close()

    async def test_merge_records_tombstone_failure_and_keeps_marker(self, temp_db_path):
        """A target removal failure is recorded on the result and the marker
        is kept for a later retry."""
        sandbox = await Fsdantic.open(path=temp_db_path)
        await sandbox.overlay.tombstone("/x.txt")
        try:
            target = _StubAgent(_FailingTargetFS())
            result = await OverlayOperations().merge(sandbox.raw, target)

            assert result.tombstones_applied == 0
            assert len(result.errors) == 1
            assert result.errors[0][0] == "/x.txt"
            assert "denied" in result.errors[0][1]
            assert await sandbox.overlay.list_tombstones() == ["/x.txt"]
        finally:
            await sandbox.close()

    async def test_merge_records_source_stat_failure_and_keeps_marker(self, temp_db_path):
        """A non-ENOENT failure while stat'ing the tombstoned path in the
        source is recorded as an error and the marker is kept."""
        target = await Fsdantic.open(path=temp_db_path)
        try:
            source = _StubAgentWithKV(_EPERMSourceFS(), _KVWithMarker())
            result = await OverlayOperations().merge(source, target.raw)

            assert result.tombstones_applied == 0
            assert len(result.errors) == 1
            assert result.errors[0][0] == "/x.txt"
            assert "denied" in result.errors[0][1]
        finally:
            await target.close()

    async def test_merge_applies_tombstone_when_source_path_is_neither_kind(self, temp_db_path):
        """A tombstoned path whose source stat is neither file nor directory
        still falls through to the target application."""
        target = await Fsdantic.open(path=temp_db_path)
        try:
            source = _StubAgentWithKV(_OtherKindSourceFS(), _KVWithMarker())
            result = await OverlayOperations().merge(source, target.raw)

            assert result.tombstones_applied == 1
            assert result.errors == []
        finally:
            await target.close()

    async def test_merge_tombstone_deletes_directory_from_target(self, temp_db_path):
        """Tombstoning a directory removes it (recursively) from the target."""
        stable = await Fsdantic.open(path=temp_db_path)
        await stable.files.write("/dir/a.txt", "a")
        await stable.close()

        sandbox = await Fsdantic.open(path=f"{temp_db_path}.sandbox")
        await sandbox.overlay.tombstone("/dir")
        stable2 = await Fsdantic.open(path=temp_db_path)
        try:
            result = await stable2.overlay.merge(sandbox)

            assert result.tombstones_applied == 1
            assert result.errors == []
            assert await stable2.files.exists("/dir/a.txt") is False
        finally:
            await stable2.close()
            await sandbox.close()


class TestTombstoneClear:
    async def test_clear_tombstones_specific_and_all(self, temp_db_path):
        sandbox = await Fsdantic.open(path=temp_db_path)
        try:
            await sandbox.overlay.tombstone("/a.txt")
            await sandbox.overlay.tombstone("/b.txt")

            assert await sandbox.overlay.clear_tombstones(["/a.txt"]) == 1
            assert await sandbox.overlay.list_tombstones() == ["/b.txt"]

            assert await sandbox.overlay.clear_tombstones() == 1
            assert await sandbox.overlay.list_tombstones() == []
        finally:
            await sandbox.close()

    async def test_clear_tombstones_missing_path_is_noop(self, temp_db_path):
        sandbox = await Fsdantic.open(path=temp_db_path)
        try:
            await sandbox.overlay.clear_tombstone("/never-recorded.txt")
            assert await sandbox.overlay.list_tombstones() == []
        finally:
            await sandbox.close()


class TestTombstoneReadonly:
    async def test_readonly_workspace_rejects_tombstone_writes(self, temp_db_path):
        writer = await Fsdantic.open(path=temp_db_path)
        await writer.files.write("/x.txt", "x")
        await writer.close()

        ro = await Fsdantic.open(path=temp_db_path, readonly=True)
        try:
            with pytest.raises(WorkspaceError) as exc_info:
                await ro.overlay.tombstone("/y.txt")
            assert exc_info.value.code == "WORKSPACE_READONLY"

            with pytest.raises(WorkspaceError) as exc_info:
                await ro.overlay.clear_tombstones()
            assert exc_info.value.code == "WORKSPACE_READONLY"

            # Reads are allowed.
            assert await ro.overlay.list_tombstones() == []
        finally:
            await ro.close()
