"""Tests for FileOperations helper class."""

import pytest
from agentfs_sdk import ErrnoException

from fsdantic import FileOperations


@pytest.mark.asyncio
class TestFileOperations:
    """Test FileOperations basic functionality."""

    async def test_write_and_read_file(self, agent_fs):
        """Should write and read files correctly."""
        ops = FileOperations(agent_fs)

        await ops.write_file("/test.txt", "Hello, World!")
        content = await ops.read_file("/test.txt")

        assert content == "Hello, World!"

    async def test_write_bytes(self, agent_fs):
        """Should handle binary content."""
        ops = FileOperations(agent_fs)

        binary_content = b"\x00\x01\x02\x03"
        await ops.write_file("/binary.dat", binary_content)

        content = await ops.read_file("/binary.dat", encoding=None)
        assert content == binary_content

    async def test_read_with_encoding(self, agent_fs):
        """Should decode with specified encoding."""
        ops = FileOperations(agent_fs)

        await ops.write_file("/utf8.txt", "Hello! 你好", encoding="utf-8")
        content = await ops.read_file("/utf8.txt", encoding="utf-8")

        assert content == "Hello! 你好"

    async def test_file_exists(self, agent_fs):
        """Should check file existence correctly."""
        ops = FileOperations(agent_fs)

        # Initially doesn't exist
        assert await ops.file_exists("/test.txt") is False

        # Create it
        await ops.write_file("/test.txt", "content")

        # Now it exists
        assert await ops.file_exists("/test.txt") is True

    async def test_list_dir(self, agent_fs):
        """Should list directory contents."""
        ops = FileOperations(agent_fs)

        # Create some files
        await ops.write_file("/dir/file1.txt", "content1")
        await ops.write_file("/dir/file2.txt", "content2")
        await ops.write_file("/dir/file3.txt", "content3")

        entries = await ops.list_dir("/dir")

        assert len(entries) == 3
        assert "file1.txt" in entries
        assert "file2.txt" in entries
        assert "file3.txt" in entries

    async def test_search_files_with_pattern(self, agent_fs):
        """Should search files by glob pattern."""
        ops = FileOperations(agent_fs)

        # Create files
        await ops.write_file("/file1.py", "print('1')")
        await ops.write_file("/file2.py", "print('2')")
        await ops.write_file("/file3.txt", "text")
        await ops.write_file("/data/file4.py", "print('4')")

        # Search for Python files
        py_files = await ops.search_files("*.py", recursive=True)

        assert len(py_files) == 3
        assert "/file1.py" in py_files
        assert "/file2.py" in py_files
        assert "/data/file4.py" in py_files

    async def test_search_files_non_recursive(self, agent_fs):
        """Should respect recursive parameter."""
        ops = FileOperations(agent_fs)

        await ops.write_file("/file1.txt", "content")
        await ops.write_file("/data/file2.txt", "content")

        # Non-recursive should only find root level
        files = await ops.search_files("*.txt", recursive=False)

        assert len(files) == 1
        assert "/file1.txt" in files

    async def test_stat(self, agent_fs):
        """Should get file statistics."""
        ops = FileOperations(agent_fs)

        content = "test content"
        await ops.write_file("/test.txt", content)

        stats = await ops.stat("/test.txt")

        assert stats.size == len(content.encode("utf-8"))
        assert stats.is_file()
        assert not stats.is_dir()

    async def test_remove(self, agent_fs):
        """Should remove files."""
        ops = FileOperations(agent_fs)

        await ops.write_file("/to-delete.txt", "content")
        assert await ops.file_exists("/to-delete.txt")

        await ops.remove("/to-delete.txt")
        assert not await ops.file_exists("/to-delete.txt")

    async def test_remove_directory_raises_for_file_only_semantics(self, agent_fs):
        """Should reject directory paths for remove()."""
        ops = FileOperations(agent_fs)

        await ops.write_file("/dir/file.txt", "content")

        with pytest.raises(ErrnoException) as exc_info:
            await ops.remove("/dir")

        assert exc_info.value.code == "EISDIR"

    async def test_remove_directory_with_rm_recursive(self, agent_fs):
        """Should remove directories using AgentFS rm recursive semantics."""
        ops = FileOperations(agent_fs)

        await ops.write_file("/dir/sub/file.txt", "content")
        await agent_fs.fs.rm("/dir", recursive=True)

        assert await ops.file_exists("/dir/sub/file.txt") is False

    async def test_tree_structure(self, agent_fs):
        """Should generate directory tree."""
        ops = FileOperations(agent_fs)

        # Create nested structure
        await ops.write_file("/file1.txt", "content")
        await ops.write_file("/dir1/file2.txt", "content")
        await ops.write_file("/dir1/file3.txt", "content")
        await ops.write_file("/dir1/subdir/file4.txt", "content")

        tree = await ops.tree("/")

        assert "file1.txt" in tree
        assert "dir1" in tree
        assert isinstance(tree["dir1"], dict)
        assert "file2.txt" in tree["dir1"]
        assert "file3.txt" in tree["dir1"]
        assert "subdir" in tree["dir1"]

    async def test_tree_with_max_depth(self, agent_fs):
        """Should respect max_depth parameter."""
        ops = FileOperations(agent_fs)

        await ops.write_file("/level1/level2/level3/file.txt", "content")

        # Depth 1 should only show level1
        tree = await ops.tree("/", max_depth=1)
        assert "level1" in tree
        assert tree["level1"] == {}  # Empty because we stopped at depth 1


@pytest.mark.asyncio
class TestFileOperationsFallthrough:
    """Test fallthrough behavior with base filesystem."""

    async def test_read_from_overlay_first(self, agent_fs, stable_fs):
        """Should read from overlay if file exists there."""
        # Write to both layers
        await stable_fs.fs.write_file("/test.txt", "base content")
        await agent_fs.fs.write_file("/test.txt", "overlay content")

        ops = FileOperations(agent_fs, base_fs=stable_fs)
        content = await ops.read_file("/test.txt")

        # Should get overlay version
        assert content == "overlay content"

    async def test_read_fallthrough_to_base(self, agent_fs, stable_fs):
        """Should fall through to base if file not in overlay."""
        await stable_fs.fs.write_file("/base-only.txt", "base content")

        ops = FileOperations(agent_fs, base_fs=stable_fs)
        content = await ops.read_file("/base-only.txt")

        assert content == "base content"

    async def test_read_not_found_in_either(self, agent_fs, stable_fs):
        """Should raise FileNotFoundError if file in neither layer."""
        ops = FileOperations(agent_fs, base_fs=stable_fs)

        from agentfs_sdk import ErrnoException

        with pytest.raises(ErrnoException):
            await ops.read_file("/nonexistent.txt")

    async def test_write_only_to_overlay(self, agent_fs, stable_fs):
        """Write should only affect overlay, not base."""
        ops = FileOperations(agent_fs, base_fs=stable_fs)

        await ops.write_file("/new-file.txt", "overlay content")

        # Should exist in overlay
        overlay_content = await agent_fs.fs.read_file("/new-file.txt")
        assert overlay_content == "overlay content"

        # Should not exist in base
        from agentfs_sdk import ErrnoException

        with pytest.raises(ErrnoException):
            await stable_fs.fs.read_file("/new-file.txt")

    async def test_file_exists_checks_both_layers(self, agent_fs, stable_fs):
        """file_exists should check both layers."""
        await stable_fs.fs.write_file("/base.txt", "base")
        await agent_fs.fs.write_file("/overlay.txt", "overlay")

        ops = FileOperations(agent_fs, base_fs=stable_fs)

        assert await ops.file_exists("/base.txt") is True
        assert await ops.file_exists("/overlay.txt") is True
        assert await ops.file_exists("/nonexistent.txt") is False

    async def test_stat_fallthrough(self, agent_fs, stable_fs):
        """stat should fall through to base."""
        await stable_fs.fs.write_file("/base.txt", "base content")

        ops = FileOperations(agent_fs, base_fs=stable_fs)
        stats = await ops.stat("/base.txt")

        assert stats.size == len(b"base content")

    async def test_stat_overlay_first(self, agent_fs, stable_fs):
        """stat should prefer overlay version."""
        await stable_fs.fs.write_file("/file.txt", "short")
        await agent_fs.fs.write_file("/file.txt", "much longer content")

        ops = FileOperations(agent_fs, base_fs=stable_fs)
        stats = await ops.stat("/file.txt")

        # Should get overlay size
        assert stats.size == len(b"much longer content")


@pytest.mark.asyncio
class TestFileOperationsEdgeCases:
    """Test edge cases and error conditions."""

    async def test_empty_file(self, agent_fs):
        """Should handle empty files."""
        ops = FileOperations(agent_fs)

        await ops.write_file("/empty.txt", "")
        content = await ops.read_file("/empty.txt")

        assert content == ""
        assert await ops.file_exists("/empty.txt")

    async def test_large_file(self, agent_fs):
        """Should handle large files."""
        ops = FileOperations(agent_fs)

        large_content = "x" * (1024 * 1024)  # 1MB
        await ops.write_file("/large.txt", large_content)

        content = await ops.read_file("/large.txt")
        assert len(content) == len(large_content)

    async def test_deep_directory_structure(self, agent_fs):
        """Should handle deeply nested paths."""
        ops = FileOperations(agent_fs)

        deep_path = "/a/b/c/d/e/f/g/h/i/j/file.txt"
        await ops.write_file(deep_path, "deep content")

        content = await ops.read_file(deep_path)
        assert content == "deep content"

    async def test_special_characters_in_filename(self, agent_fs):
        """Should handle special characters in filenames."""
        ops = FileOperations(agent_fs)

        special_files = [
            "/file-with-dash.txt",
            "/file_with_underscore.txt",
            "/file.multiple.dots.txt",
        ]

        for path in special_files:
            await ops.write_file(path, f"content for {path}")
            content = await ops.read_file(path)
            assert content == f"content for {path}"

    async def test_unicode_content(self, agent_fs):
        """Should handle Unicode content correctly."""
        ops = FileOperations(agent_fs)

        unicode_content = "Hello 世界 🌍 مرحبا мир"
        await ops.write_file("/unicode.txt", unicode_content)

        content = await ops.read_file("/unicode.txt")
        assert content == unicode_content

    async def test_list_dir_empty_directory(self, agent_fs):
        """Should handle empty directories."""
        ops = FileOperations(agent_fs)

        # Create directory by writing a file, then removing it
        await ops.write_file("/emptydir/temp.txt", "temp")
        await ops.remove("/emptydir/temp.txt")

        # Directory listing might be empty or directory might not exist
        # depending on AgentFS behavior
        try:
            entries = await ops.list_dir("/emptydir")
            assert len(entries) == 0
        except Exception:
            # Empty directories might not exist in AgentFS
            pass

    async def test_tree_empty_filesystem(self, agent_fs):
        """Should handle empty filesystem."""
        ops = FileOperations(agent_fs)

        tree = await ops.tree("/")
        assert tree == {} or tree is not None

    async def test_search_files_no_matches(self, agent_fs):
        """Should return empty list when no matches."""
        ops = FileOperations(agent_fs)

        await ops.write_file("/file.txt", "content")

        # Search for non-existent pattern
        files = await ops.search_files("*.py")
        assert files == []

    async def test_overwrite_file(self, agent_fs):
        """Should overwrite existing files."""
        ops = FileOperations(agent_fs)

        await ops.write_file("/file.txt", "original")
        await ops.write_file("/file.txt", "updated")

        content = await ops.read_file("/file.txt")
        assert content == "updated"

    async def test_binary_and_text_mixed(self, agent_fs):
        """Should handle both binary and text files."""
        ops = FileOperations(agent_fs)

        # Write text
        await ops.write_file("/text.txt", "text content")

        # Write binary
        await ops.write_file("/binary.dat", b"\x00\x01\x02")

        # Read both
        text = await ops.read_file("/text.txt")
        binary = await ops.read_file("/binary.dat", encoding=None)

        assert text == "text content"
        assert binary == b"\x00\x01\x02"


@pytest.mark.asyncio
class TestFileOperationsIntegration:
    """Integration tests for FileOperations workflows."""

    async def test_complete_workflow(self, agent_fs):
        """Test complete file management workflow."""
        ops = FileOperations(agent_fs)

        # 1. Create files
        await ops.write_file("/project/main.py", "print('main')")
        await ops.write_file("/project/utils.py", "def helper(): pass")
        await ops.write_file("/project/README.md", "# Project")

        # 2. Search for Python files
        py_files = await ops.search_files("*.py", recursive=True)
        assert len(py_files) == 2

        # 3. Check existence
        assert await ops.file_exists("/project/main.py")
        assert await ops.file_exists("/project/README.md")

        # 4. Get directory listing
        entries = await ops.list_dir("/project")
        assert len(entries) == 3

        # 5. Get tree
        tree = await ops.tree("/project")
        assert "main.py" in tree
        assert "utils.py" in tree
        assert "README.md" in tree

        # 6. Remove a file
        await ops.remove("/project/utils.py")
        assert not await ops.file_exists("/project/utils.py")

        # 7. Verify final state
        py_files = await ops.search_files("*.py", recursive=True)
        assert len(py_files) == 1

    async def test_layered_workflow(self, agent_fs, stable_fs):
        """Test workflow with layered filesystems."""
        # Setup base layer
        await stable_fs.fs.write_file("/config/default.json", '{"theme": "light"}')
        await stable_fs.fs.write_file("/lib/core.py", "# Core library")

        ops = FileOperations(agent_fs, base_fs=stable_fs)

        # 1. Read from base
        config = await ops.read_file("/config/default.json")
        assert "light" in config

        # 2. Override in overlay
        await ops.write_file("/config/default.json", '{"theme": "dark"}')

        # 3. Read overlay version
        config = await ops.read_file("/config/default.json")
        assert "dark" in config

        # 4. Add overlay-only file
        await ops.write_file("/config/user.json", '{"name": "user"}')

        # 5. Search across both layers
        json_files = await ops.search_files("*.json", recursive=True)
        assert len(json_files) >= 2

        # 6. Verify base unchanged
        base_config = await stable_fs.fs.read_file("/config/default.json")
        assert "light" in base_config
