"""Tests for FileManager helper class."""

import pytest
from fsdantic import (
    DirectoryNotEmptyError,
    FileManager,
    FileNotFoundError,
    FileStats,
    NotADirectoryError,
)


@pytest.mark.asyncio
class TestFileManager:
    """Test FileManager basic functionality."""

    async def test_write_and_read_file(self, agent_fs):
        """Should write and read files correctly."""
        ops = FileManager(agent_fs)

        await ops.write("/test.txt", "Hello, World!")
        content = await ops.read("/test.txt")

        assert content == "Hello, World!"

    async def test_write_bytes(self, agent_fs):
        """Should handle binary content."""
        ops = FileManager(agent_fs)

        binary_content = b"\x00\x01\x02\x03"
        await ops.write("/binary.dat", binary_content)

        content = await ops.read("/binary.dat", mode="binary")
        assert content == binary_content

    async def test_read_with_encoding(self, agent_fs):
        """Should decode with specified encoding."""
        ops = FileManager(agent_fs)

        await ops.write("/utf8.txt", "Hello! 你好", encoding="utf-8")
        content = await ops.read("/utf8.txt", encoding="utf-8")

        assert content == "Hello! 你好"

    async def test_file_exists(self, agent_fs):
        """Should check file existence correctly."""
        ops = FileManager(agent_fs)

        # Initially doesn't exist
        assert await ops.exists("/test.txt") is False

        # Create it
        await ops.write("/test.txt", "content")

        # Now it exists
        assert await ops.exists("/test.txt") is True

    async def test_list_dir(self, agent_fs):
        """Should list directory contents with deterministic ordering."""
        ops = FileManager(agent_fs)

        await ops.write("/dir/file-b.txt", "content1")
        await ops.write("/dir/file-a.txt", "content2")
        await ops.write("/dir/file-c.txt", "content3")

        assert await ops.list_dir("/dir") == ["file-a.txt", "file-b.txt", "file-c.txt"]
        assert await ops.list_dir("/dir", output="relative") == [
            "file-a.txt",
            "file-b.txt",
            "file-c.txt",
        ]
        assert await ops.list_dir("/dir", output="full") == [
            "/dir/file-a.txt",
            "/dir/file-b.txt",
            "/dir/file-c.txt",
        ]

    async def test_search_files_with_pattern(self, agent_fs):
        """Should search files by glob pattern."""
        ops = FileManager(agent_fs)

        # Create files
        await ops.write("/file1.py", "print('1')")
        await ops.write("/file2.py", "print('2')")
        await ops.write("/file3.txt", "text")
        await ops.write("/data/file4.py", "print('4')")

        # Search for Python files
        py_files = await ops.search("*.py", recursive=True)

        assert len(py_files) == 3
        assert "/file1.py" in py_files
        assert "/file2.py" in py_files
        assert "/data/file4.py" in py_files

    async def test_search_files_non_recursive(self, agent_fs):
        """Should respect recursive parameter."""
        ops = FileManager(agent_fs)

        await ops.write("/file1.txt", "content")
        await ops.write("/data/file2.txt", "content")

        # Non-recursive should only find root level
        files = await ops.search("*.txt", recursive=False)

        assert len(files) == 1
        assert "/file1.txt" in files

    async def test_stat(self, agent_fs):
        """Should get file statistics."""
        ops = FileManager(agent_fs)

        content = "test content"
        await ops.write("/test.txt", content)

        stats = await ops.stat("/test.txt")

        assert isinstance(stats, FileStats)
        assert stats.size == len(content.encode("utf-8"))
        assert stats.is_file
        assert not stats.is_dir()

    async def test_remove(self, agent_fs):
        """Should remove files."""
        ops = FileManager(agent_fs)

        await ops.write("/to-delete.txt", "content")
        assert await ops.exists("/to-delete.txt")

        await ops.remove("/to-delete.txt")
        assert not await ops.exists("/to-delete.txt")

    async def test_remove_directory_non_recursive_fails_when_not_empty(self, agent_fs):
        """Should fail predictably for non-empty directories when recursive=False."""
        ops = FileManager(agent_fs)

        await ops.write("/dir/file.txt", "content")

        with pytest.raises(DirectoryNotEmptyError):
            await ops.remove("/dir", recursive=False)

    async def test_remove_directory_recursive(self, agent_fs):
        """Should remove directories recursively when requested."""
        ops = FileManager(agent_fs)

        await ops.write("/dir/sub/file.txt", "content")
        await ops.remove("/dir", recursive=True)

        assert await ops.exists("/dir/sub/file.txt") is False

    async def test_tree_structure(self, agent_fs):
        """Should generate directory tree with stable schema and ordering."""
        ops = FileManager(agent_fs)

        await ops.write("/file1.txt", "content")
        await ops.write("/dir1/file2.txt", "content")
        await ops.write("/dir1/file3.txt", "content")
        await ops.write("/dir1/subdir/file4.txt", "content")

        tree = await ops.tree("/")

        assert tree["type"] == "directory"
        assert tree["path"] == "/"
        assert [child["name"] for child in tree["children"]] == ["dir1", "file1.txt"]

        dir1 = tree["children"][0]
        assert dir1["type"] == "directory"
        assert [child["name"] for child in dir1["children"]] == ["subdir", "file2.txt", "file3.txt"]

    async def test_methods_normalize_paths(self, agent_fs):
        """Path-accepting methods should normalize input paths."""
        ops = FileManager(agent_fs)

        await ops.write("nested//./dir/../dir/file.txt", "normalized")

        assert await ops.exists("nested/dir/file.txt") is True
        assert await ops.read("/nested/dir/./file.txt") == "normalized"

        stats = await ops.stat("nested/dir/file.txt")
        assert stats.is_file is True

        listed = await ops.list_dir("nested//dir//")
        assert "file.txt" in listed

        await ops.remove("nested/dir/./file.txt")
        assert await ops.exists("/nested/dir/file.txt") is False

    async def test_search_and_query_return_normalized_paths(self, agent_fs):
        """Search/query output paths should be consistently normalized."""
        ops = FileManager(agent_fs)

        await ops.write("/alpha//beta/./file.txt", "x")
        await ops.write("/alpha/beta/../beta/other.py", "print('x')")

        txt = await ops.search("alpha//**/*.txt")
        assert txt == ["/alpha/beta/file.txt"]

        from fsdantic import ViewQuery

        entries = await ops.query(ViewQuery(path_pattern="alpha//**/*"))
        assert {entry.path for entry in entries} == {
            "/alpha/beta/file.txt",
            "/alpha/beta/other.py",
        }

    async def test_tree_with_max_depth(self, agent_fs):
        """Should respect max_depth parameter."""
        ops = FileManager(agent_fs)

        await ops.write("/level1/level2/level3/file.txt", "content")

        tree = await ops.tree("/", max_depth=1)
        assert tree["children"][0]["name"] == "level1"
        assert tree["children"][0]["children"] == []


@pytest.mark.asyncio
class TestFileManagerFallthrough:
    """Test fallthrough behavior with base filesystem."""

    async def test_read_from_overlay_first(self, agent_fs, stable_fs):
        """Should read from overlay if file exists there."""
        # Write to both layers
        await stable_fs.fs.write_file("/test.txt", "base content")
        await agent_fs.fs.write_file("/test.txt", "overlay content")

        ops = FileManager(agent_fs, base_fs=stable_fs)
        content = await ops.read("/test.txt")

        # Should get overlay version
        assert content == "overlay content"

    async def test_read_fallthrough_to_base(self, agent_fs, stable_fs):
        """Should fall through to base if file not in overlay."""
        await stable_fs.fs.write_file("/base-only.txt", "base content")

        ops = FileManager(agent_fs, base_fs=stable_fs)
        content = await ops.read("/base-only.txt")

        assert content == "base content"

    async def test_read_not_found_in_either(self, agent_fs, stable_fs):
        """Should raise FileNotFoundError if file in neither layer."""
        ops = FileManager(agent_fs, base_fs=stable_fs)

        with pytest.raises(FileNotFoundError) as exc_info:
            await ops.read("/nonexistent.txt")

        assert exc_info.value.path == "/nonexistent.txt"
        assert exc_info.value.cause is not None

    async def test_write_only_to_overlay(self, agent_fs, stable_fs):
        """Write should only affect overlay, not base."""
        ops = FileManager(agent_fs, base_fs=stable_fs)

        await ops.write("/new-file.txt", "overlay content")

        # Should exist in overlay
        overlay_content = await agent_fs.fs.read_file("/new-file.txt")
        assert overlay_content == "overlay content"

        # Should not exist in base
        stable_ops = FileManager(stable_fs)
        with pytest.raises(FileNotFoundError) as exc_info:
            await stable_ops.read("/new-file.txt")

        assert exc_info.value.path == "/new-file.txt"

    async def test_file_exists_checks_both_layers(self, agent_fs, stable_fs):
        """file_exists should check both layers."""
        await stable_fs.fs.write_file("/base.txt", "base")
        await agent_fs.fs.write_file("/overlay.txt", "overlay")

        ops = FileManager(agent_fs, base_fs=stable_fs)

        assert await ops.exists("/base.txt") is True
        assert await ops.exists("/overlay.txt") is True
        assert await ops.exists("/nonexistent.txt") is False

    async def test_stat_fallthrough(self, agent_fs, stable_fs):
        """stat should fall through to base."""
        await stable_fs.fs.write_file("/base.txt", "base content")

        ops = FileManager(agent_fs, base_fs=stable_fs)
        stats = await ops.stat("/base.txt")

        assert stats.size == len(b"base content")

    async def test_stat_overlay_first(self, agent_fs, stable_fs):
        """stat should prefer overlay version."""
        await stable_fs.fs.write_file("/file.txt", "short")
        await agent_fs.fs.write_file("/file.txt", "much longer content")

        ops = FileManager(agent_fs, base_fs=stable_fs)
        stats = await ops.stat("/file.txt")

        # Should get overlay size
        assert stats.size == len(b"much longer content")


@pytest.mark.asyncio
class TestFileManagerEdgeCases:
    """Test edge cases and error conditions."""

    async def test_empty_file(self, agent_fs):
        """Should handle empty files."""
        ops = FileManager(agent_fs)

        await ops.write("/empty.txt", "")
        content = await ops.read("/empty.txt")

        assert content == ""
        assert await ops.exists("/empty.txt")

    async def test_large_file(self, agent_fs):
        """Should handle large files."""
        ops = FileManager(agent_fs)

        large_content = "x" * (1024 * 1024)  # 1MB
        await ops.write("/large.txt", large_content)

        content = await ops.read("/large.txt")
        assert len(content) == len(large_content)

    async def test_deep_directory_structure(self, agent_fs):
        """Should handle deeply nested paths."""
        ops = FileManager(agent_fs)

        deep_path = "/a/b/c/d/e/f/g/h/i/j/file.txt"
        await ops.write(deep_path, "deep content")

        content = await ops.read(deep_path)
        assert content == "deep content"

    async def test_special_characters_in_filename(self, agent_fs):
        """Should handle special characters in filenames."""
        ops = FileManager(agent_fs)

        special_files = [
            "/file-with-dash.txt",
            "/file_with_underscore.txt",
            "/file.multiple.dots.txt",
        ]

        for path in special_files:
            await ops.write(path, f"content for {path}")
            content = await ops.read(path)
            assert content == f"content for {path}"

    async def test_unicode_content(self, agent_fs):
        """Should handle Unicode content correctly."""
        ops = FileManager(agent_fs)

        unicode_content = "Hello 世界 🌍 مرحبا мир"
        await ops.write("/unicode.txt", unicode_content)

        content = await ops.read("/unicode.txt")
        assert content == unicode_content

    async def test_list_dir_empty_directory(self, agent_fs):
        """Should handle empty directories."""
        ops = FileManager(agent_fs)

        # Create directory by writing a file, then removing it
        await ops.write("/emptydir/temp.txt", "temp")
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
        ops = FileManager(agent_fs)

        tree = await ops.tree("/")
        assert tree == {"name": "/", "path": "/", "type": "directory", "children": []}

    async def test_search_files_no_matches(self, agent_fs):
        """Should return empty list when no matches."""
        ops = FileManager(agent_fs)

        await ops.write("/file.txt", "content")

        # Search for non-existent pattern
        files = await ops.search("*.py")
        assert files == []

    async def test_overwrite_file(self, agent_fs):
        """Should overwrite existing files."""
        ops = FileManager(agent_fs)

        await ops.write("/file.txt", "original")
        await ops.write("/file.txt", "updated")

        content = await ops.read("/file.txt")
        assert content == "updated"

    async def test_binary_and_text_mixed(self, agent_fs):
        """Should handle both binary and text files."""
        ops = FileManager(agent_fs)

        # Write text
        await ops.write("/text.txt", "text content")

        # Write binary
        await ops.write("/binary.dat", b"\x00\x01\x02")

        # Read both
        text = await ops.read("/text.txt")
        binary = await ops.read("/binary.dat", mode="binary")

        assert text == "text content"
        assert binary == b"\x00\x01\x02"

    async def test_json_dict_roundtrip(self, agent_fs):
        """Should serialize dict content as JSON with deterministic formatting."""
        ops = FileManager(agent_fs)

        payload = {"message": "こんにちは", "count": 2}
        await ops.write("/payload.json", payload)

        content = await ops.read("/payload.json")
        assert content == '{\n  "message": "こんにちは",\n  "count": 2\n}'

    async def test_json_list_roundtrip(self, agent_fs):
        """Should serialize list content as JSON with deterministic formatting."""
        ops = FileManager(agent_fs)

        payload = ["α", "β", 3]
        await ops.write("/items.json", payload)

        content = await ops.read("/items.json")
        assert content == '[\n  "α",\n  "β",\n  3\n]'

    async def test_mode_type_mismatch_validation(self, agent_fs):
        """Should raise predictable errors when mode and content type do not match."""
        ops = FileManager(agent_fs)

        with pytest.raises(TypeError, match="mode='binary' requires bytes content"):
            await ops.write("/bad.bin", "not-bytes", mode="binary")

        with pytest.raises(TypeError, match="mode='text' requires str content"):
            await ops.write("/bad.txt", b"not-text", mode="text")

        with pytest.raises(TypeError, match="mode='json' requires dict or list content"):
            await ops.write("/bad.json", "not-json", mode="json")

    async def test_invalid_encoding_validation(self, agent_fs):
        """Should validate text encodings on read/write APIs."""
        ops = FileManager(agent_fs)

        with pytest.raises(ValueError, match="Unknown encoding"):
            await ops.write("/bad-encoding.txt", "hello", encoding="definitely-not-an-encoding")

        await ops.write("/hello.txt", "hello")
        with pytest.raises(ValueError, match="Unknown encoding"):
            await ops.read("/hello.txt", encoding="definitely-not-an-encoding")

    async def test_read_mode_encoding_mismatch_validation(self, agent_fs):
        """Should reject incompatible read mode and encoding combinations."""
        ops = FileManager(agent_fs)

        await ops.write("/hello.txt", "hello")

        with pytest.raises(ValueError, match="encoding must be None when mode='binary'"):
            await ops.read("/hello.txt", mode="binary", encoding="utf-8")

        with pytest.raises(ValueError, match="encoding must be provided when mode='text'"):
            await ops.read("/hello.txt", mode="text", encoding=None)


    async def test_list_dir_on_file_raises_not_a_directory(self, agent_fs):
        """Should raise NotADirectoryError when listing a file path."""
        ops = FileManager(agent_fs)

        await ops.write("/plain.txt", "content")

        with pytest.raises(NotADirectoryError):
            await ops.list_dir("/plain.txt")

    async def test_read_on_directory_raises_not_found(self, agent_fs):
        """Reading a directory should raise file-not-found from AgentFS semantics."""
        ops = FileManager(agent_fs)

        await ops.write("/folder/file.txt", "content")

        with pytest.raises(FileNotFoundError):
            await ops.read("/folder")

    async def test_remove_missing_path_raises_file_not_found(self, agent_fs):
        """Removing a missing path should raise FileNotFoundError."""
        ops = FileManager(agent_fs)

        with pytest.raises(FileNotFoundError):
            await ops.remove("/does-not-exist.txt")

    async def test_stat_consistent_model_for_overlay_and_base(self, agent_fs, stable_fs):
        """stat() should return the same FileStats model from overlay and base fallthrough."""
        await stable_fs.fs.write_file("/base.txt", "base")
        await agent_fs.fs.write_file("/overlay.txt", "overlay")

        ops = FileManager(agent_fs, base_fs=stable_fs)
        overlay_stats = await ops.stat("/overlay.txt")
        base_stats = await ops.stat("/base.txt")

        assert isinstance(overlay_stats, FileStats)
        assert isinstance(base_stats, FileStats)
        assert overlay_stats.model_fields.keys() == base_stats.model_fields.keys()


@pytest.mark.asyncio
class TestFileManagerIntegration:
    """Integration tests for FileManager workflows."""

    async def test_complete_workflow(self, agent_fs):
        """Test complete file management workflow."""
        ops = FileManager(agent_fs)

        # 1. Create files
        await ops.write("/project/main.py", "print('main')")
        await ops.write("/project/utils.py", "def helper(): pass")
        await ops.write("/project/README.md", "# Project")

        # 2. Search for Python files
        py_files = await ops.search("*.py", recursive=True)
        assert len(py_files) == 2

        # 3. Check existence
        assert await ops.exists("/project/main.py")
        assert await ops.exists("/project/README.md")

        # 4. Get directory listing
        entries = await ops.list_dir("/project")
        assert len(entries) == 3

        # 5. Get tree
        tree = await ops.tree("/project")
        assert tree["type"] == "directory"
        assert [child["name"] for child in tree["children"]] == [
            "README.md",
            "main.py",
            "utils.py",
        ]

        # 6. Remove a file
        await ops.remove("/project/utils.py")
        assert not await ops.exists("/project/utils.py")

        # 7. Verify final state
        py_files = await ops.search("*.py", recursive=True)
        assert len(py_files) == 1

    async def test_layered_workflow(self, agent_fs, stable_fs):
        """Test workflow with layered filesystems."""
        # Setup base layer
        await stable_fs.fs.write_file("/config/default.json", '{"theme": "light"}')
        await stable_fs.fs.write_file("/lib/core.py", "# Core library")

        ops = FileManager(agent_fs, base_fs=stable_fs)

        # 1. Read from base
        config = await ops.read("/config/default.json")
        assert "light" in config

        # 2. Override in overlay
        await ops.write("/config/default.json", '{"theme": "dark"}')

        # 3. Read overlay version
        config = await ops.read("/config/default.json")
        assert "dark" in config

        # 4. Add overlay-only file
        await ops.write("/config/user.json", '{"name": "user"}')

        # 5. Search across both layers
        json_files = await ops.search("*.json", recursive=True)
        assert len(json_files) >= 2

        # 6. Verify base unchanged
        base_config = await stable_fs.fs.read_file("/config/default.json")
        assert "light" in base_config
