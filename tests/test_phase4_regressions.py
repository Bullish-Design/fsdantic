"""Regression tests for Phase 4: open-path parity and layering.

Covers:
- M7: MVCC open validates IDs exactly like the non-MVCC path.
- M8: list_dir falls back to base; overlay wins when both exist.
"""

import glob
import os

import pytest

from fsdantic import Fsdantic, FileManager


@pytest.mark.asyncio
class TestMvccOpenValidation:
    """M7: both open paths must validate the selector identically."""

    def _cleanup_agentfs_dir(self):
        os.makedirs(".agentfs", exist_ok=True)
        for path in glob.glob(".agentfs/*"):
            os.remove(path)

    async def test_mvcc_open_rejects_invalid_id(self):
        self._cleanup_agentfs_dir()
        with pytest.raises(ValueError):
            await Fsdantic.open(id="bad id!!", enable_mvcc=True)
        assert glob.glob(".agentfs/bad id!!.db") == []
        self._cleanup_agentfs_dir()

    async def test_mvcc_open_strips_whitespace_id(self):
        self._cleanup_agentfs_dir()
        workspace = await Fsdantic.open(id="  abc  ", enable_mvcc=True)
        try:
            assert glob.glob(".agentfs/abc.db") != []
        finally:
            await workspace.close()
            self._cleanup_agentfs_dir()

    async def test_open_with_options_validates_id(self, tmp_path):
        from fsdantic import AgentFSOptions

        options = AgentFSOptions(path=str(tmp_path / "valid.db"))
        workspace = await Fsdantic.open_with_options(options)
        try:
            assert workspace is not None
        finally:
            await workspace.close()

    async def test_mvcc_open_by_path_unchanged(self, tmp_path):
        db_path = str(tmp_path / "mvcc.db")
        workspace = await Fsdantic.open(path=db_path, enable_mvcc=True)
        try:
            assert os.path.exists(db_path)
        finally:
            await workspace.close()

    async def test_open_with_options_rejects_invalid_id(self, tmp_path):
        from fsdantic import AgentFSOptions

        options = AgentFSOptions(id="bad id!!")
        with pytest.raises(ValueError):
            await Fsdantic.open_with_options(options)


@pytest.mark.asyncio
class TestListDirBaseFallback:
    """M8: list_dir must be consistent with read/stat/exists layering."""

    async def test_list_dir_falls_back_to_base(self, agent_fs, stable_fs):
        await stable_fs.fs.write_file("/in_base.txt", "x")
        manager = FileManager(agent_fs, base_fs=stable_fs)
        assert await manager.list_dir("/") == ["in_base.txt"]

    async def test_list_dir_overlay_wins(self, agent_fs, stable_fs):
        await stable_fs.fs.write_file("/base_only.txt", "b")
        await agent_fs.fs.write_file("/overlay_only.txt", "o")

        manager = FileManager(agent_fs, base_fs=stable_fs)
        entries = await manager.list_dir("/")
        assert "overlay_only.txt" in entries
        assert "base_only.txt" not in entries

    async def test_list_dir_missing_in_both_raises_file_not_found(self, agent_fs, stable_fs):
        from fsdantic import FileNotFoundError

        manager = FileManager(agent_fs, base_fs=stable_fs)
        with pytest.raises(FileNotFoundError):
            await manager.list_dir("/no/such/dir")

    async def test_list_dir_full_output_fallback(self, agent_fs, stable_fs):
        await stable_fs.fs.write_file("/in_base.txt", "x")
        await stable_fs.fs.write_file("/sub/deep.txt", "y")

        manager = FileManager(agent_fs, base_fs=stable_fs)
        full = await manager.list_dir("/", output="full")
        assert "/in_base.txt" in full
        assert "/sub" in full

        sub_full = await manager.list_dir("/sub", output="full")
        assert sub_full == ["/sub/deep.txt"]

    async def test_list_dir_empty_overlay_does_not_shadow_base(self, agent_fs, stable_fs):
        """An empty overlay directory listing falls through to base."""
        await stable_fs.fs.write_file("/nested/in_base.txt", "x")
        manager = FileManager(agent_fs, base_fs=stable_fs)
        entries = await manager.list_dir("/")
        assert "nested" in entries
