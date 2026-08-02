"""Tests for base-union query/search semantics.

``FileManager.query(include_base=True)`` (and ``search(include_base=True)``)
return an overlay-wins union: overlay entries first, then base-layer entries
whose paths are absent from the overlay results.  Default ``include_base=False``
preserves the overlay-only behavior.  Directory shadowing follows ``list_dir``:
an empty overlay directory does not shadow base content.
"""

import pytest

from fsdantic import FileManager, FileQuery

pytestmark = pytest.mark.asyncio


class TestQueryUnion:
    async def test_query_include_base_union(self, agent_fs, stable_fs):
        """Base-only files appear; files in both layers return overlay content."""
        await stable_fs.fs.write_file("/base_only.txt", "base content")
        await stable_fs.fs.write_file("/shared.txt", "base version")
        await agent_fs.fs.write_file("/overlay_only.txt", "overlay content")
        await agent_fs.fs.write_file("/shared.txt", "overlay version")

        manager = FileManager(agent_fs, base_fs=stable_fs)
        entries = await manager.query(
            FileQuery(path_pattern="*.txt", include_content=True),
            include_base=True,
        )
        by_path = {entry.path: entry for entry in entries}

        assert set(by_path) == {"/base_only.txt", "/shared.txt", "/overlay_only.txt"}
        # Overlay wins on collisions.
        assert by_path["/shared.txt"].content == "overlay version"
        assert by_path["/base_only.txt"].content == "base content"
        assert by_path["/overlay_only.txt"].content == "overlay content"

    async def test_query_default_unchanged(self, agent_fs, stable_fs):
        """include_base=False returns only overlay entries (regression)."""
        await stable_fs.fs.write_file("/base_only.txt", "base")
        await agent_fs.fs.write_file("/overlay_only.txt", "overlay")

        manager = FileManager(agent_fs, base_fs=stable_fs)
        entries = await manager.query(FileQuery(path_pattern="*.txt"))
        assert [entry.path for entry in entries] == ["/overlay_only.txt"]

    async def test_query_include_base_no_base_fs_is_noop(self, agent_fs):
        """include_base=True with no base_fs configured behaves like default."""
        await agent_fs.fs.write_file("/a.txt", "a")
        manager = FileManager(agent_fs)
        entries = await manager.query(FileQuery(path_pattern="*.txt"), include_base=True)
        assert [entry.path for entry in entries] == ["/a.txt"]

    async def test_empty_overlay_directory_does_not_shadow_base(self, agent_fs, stable_fs):
        """An empty overlay directory does not hide base files beneath it."""
        await stable_fs.fs.write_file("/lib/shared.py", "# shared")
        await agent_fs.fs.mkdir("/lib")  # empty overlay directory

        manager = FileManager(agent_fs, base_fs=stable_fs)
        entries = await manager.query(FileQuery(path_pattern="**/*.py"), include_base=True)
        assert [entry.path for entry in entries] == ["/lib/shared.py"]

    async def test_search_include_base(self, agent_fs, stable_fs):
        """search(include_base=True) covers base-only files."""
        await stable_fs.fs.write_file("/base_only.py", "base")
        await agent_fs.fs.write_file("/overlay_only.py", "overlay")
        await agent_fs.fs.write_file("/shared.py", "overlay")

        manager = FileManager(agent_fs, base_fs=stable_fs)
        assert set(await manager.search("**/*.py", include_base=True)) == {
            "/base_only.py",
            "/overlay_only.py",
            "/shared.py",
        }
        # Default: overlay only.
        assert set(await manager.search("**/*.py")) == {"/overlay_only.py", "/shared.py"}

    async def test_query_include_base_filters_apply_to_both_layers(self, agent_fs, stable_fs):
        """Path/size filters apply to the base layer too."""
        await stable_fs.fs.write_file("/keep.txt", "base")
        await stable_fs.fs.write_file("/drop.txt", "base")
        await agent_fs.fs.write_file("/keep.txt", "overlay")

        manager = FileManager(agent_fs, base_fs=stable_fs)
        entries = await manager.query(
            FileQuery(path_pattern="keep.txt"),
            include_base=True,
        )
        assert [entry.path for entry in entries] == ["/keep.txt"]
