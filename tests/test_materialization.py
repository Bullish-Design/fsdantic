"""Tests for Materializer and workspace materialization."""

from pathlib import Path

import pytest

from fsdantic import ConflictResolution, Materializer


@pytest.mark.asyncio
class TestMaterializer:
    """Test basic Materializer functionality."""

    async def test_materialize_simple_files(self, agent_fs, temp_workspace_dir):
        """Should materialize files from AgentFS to disk."""
        # Create files in AgentFS
        await agent_fs.fs.write_file("/file1.txt", "content1")
        await agent_fs.fs.write_file("/file2.txt", "content2")

        materializer = Materializer()
        target = Path(temp_workspace_dir) / "output"

        result = await materializer.materialize(agent_fs, target)

        # Check result
        assert result.files_written == 2
        assert result.target_path == target

        # Verify files on disk
        assert (target / "file1.txt").exists()
        assert (target / "file2.txt").exists()
        assert (target / "file1.txt").read_text() == "content1"
        assert (target / "file2.txt").read_text() == "content2"

    async def test_materialize_nested_directories(self, agent_fs, temp_workspace_dir):
        """Should create nested directory structures."""
        await agent_fs.fs.write_file("/a/b/c/deep.txt", "deep content")
        await agent_fs.fs.write_file("/x/y/another.txt", "another")

        materializer = Materializer()
        target = Path(temp_workspace_dir) / "nested"

        result = await materializer.materialize(agent_fs, target)

        assert result.files_written == 2
        assert (target / "a" / "b" / "c" / "deep.txt").exists()
        assert (target / "x" / "y" / "another.txt").exists()

    async def test_materialize_with_clean(self, agent_fs, temp_workspace_dir):
        """Should clean target directory before materializing."""
        target = Path(temp_workspace_dir) / "clean_test"
        target.mkdir(parents=True)

        # Create existing file
        (target / "old_file.txt").write_text("old content")

        # Materialize new files
        await agent_fs.fs.write_file("/new_file.txt", "new content")

        materializer = Materializer()
        result = await materializer.materialize(agent_fs, target, clean=True)

        # Old file should be gone
        assert not (target / "old_file.txt").exists()

        # New file should exist
        assert (target / "new_file.txt").exists()

    async def test_materialize_without_clean(self, agent_fs, temp_workspace_dir):
        """Should preserve existing files when clean=False."""
        target = Path(temp_workspace_dir) / "no_clean"
        target.mkdir(parents=True)

        (target / "existing.txt").write_text("existing")

        await agent_fs.fs.write_file("/new.txt", "new")

        materializer = Materializer()
        result = await materializer.materialize(agent_fs, target, clean=False)

        # Both should exist
        assert (target / "existing.txt").exists()
        assert (target / "new.txt").exists()


@pytest.mark.asyncio
class TestMaterializerConflictResolution:
    """Test conflict resolution strategies."""

    async def test_conflict_overwrite(self, agent_fs, temp_workspace_dir):
        """OVERWRITE strategy should replace existing files."""
        target = Path(temp_workspace_dir) / "overwrite"
        target.mkdir(parents=True)

        # Create existing file
        (target / "conflict.txt").write_text("original content")

        # Materialize with conflicting file
        await agent_fs.fs.write_file("/conflict.txt", "new content")

        materializer = Materializer(conflict_resolution=ConflictResolution.OVERWRITE)
        result = await materializer.materialize(agent_fs, target, clean=False)

        # Should be overwritten
        assert (target / "conflict.txt").read_text() == "new content"

    async def test_conflict_skip(self, agent_fs, temp_workspace_dir):
        """SKIP strategy should preserve existing files."""
        target = Path(temp_workspace_dir) / "skip"
        target.mkdir(parents=True)

        (target / "conflict.txt").write_text("original content")

        await agent_fs.fs.write_file("/conflict.txt", "new content")

        materializer = Materializer(conflict_resolution=ConflictResolution.SKIP)
        result = await materializer.materialize(agent_fs, target, clean=False)

        # Should be preserved
        assert (target / "conflict.txt").read_text() == "original content"
        assert len(result.skipped) > 0

    async def test_conflict_error(self, agent_fs, temp_workspace_dir):
        """ERROR strategy should record errors for conflicts."""
        target = Path(temp_workspace_dir) / "error"
        target.mkdir(parents=True)

        (target / "conflict.txt").write_text("original")

        await agent_fs.fs.write_file("/conflict.txt", "new")

        materializer = Materializer(conflict_resolution=ConflictResolution.ERROR)
        result = await materializer.materialize(agent_fs, target, clean=False)

        # Should record error
        assert len(result.errors) > 0


@pytest.mark.asyncio
class TestMaterializerWithBaseLayers:
    """Test materialization with base filesystem."""

    async def test_materialize_base_then_overlay(self, agent_fs, stable_fs, temp_workspace_dir):
        """Should materialize base layer first, then overlay."""
        # Create base files
        await stable_fs.fs.write_file("/base.txt", "base content")
        await stable_fs.fs.write_file("/shared.txt", "base version")

        # Create overlay files
        await agent_fs.fs.write_file("/overlay.txt", "overlay content")
        await agent_fs.fs.write_file("/shared.txt", "overlay version")

        target = Path(temp_workspace_dir) / "layered"

        materializer = Materializer()
        result = await materializer.materialize(agent_fs, target, base_fs=stable_fs)

        # Should have both base and overlay files
        assert (target / "base.txt").exists()
        assert (target / "overlay.txt").exists()

        # Overlay should win for shared file
        assert (target / "shared.txt").read_text() == "overlay version"

    async def test_materialize_base_only(self, stable_fs, temp_workspace_dir):
        """Should materialize base layer alone."""
        await stable_fs.fs.write_file("/base1.txt", "content1")
        await stable_fs.fs.write_file("/base2.txt", "content2")

        target = Path(temp_workspace_dir) / "base_only"

        materializer = Materializer()
        result = await materializer.materialize(stable_fs, target)

        assert result.files_written == 2
        assert (target / "base1.txt").exists()
        assert (target / "base2.txt").exists()


@pytest.mark.asyncio
class TestMaterializerStats:
    """Test materialization result statistics."""

    async def test_files_written_count(self, agent_fs, temp_workspace_dir):
        """Should count files written correctly."""
        await agent_fs.fs.write_file("/file1.txt", "a")
        await agent_fs.fs.write_file("/file2.txt", "b")
        await agent_fs.fs.write_file("/file3.txt", "c")

        materializer = Materializer()
        target = Path(temp_workspace_dir) / "count"

        result = await materializer.materialize(agent_fs, target)

        assert result.files_written == 3

    async def test_bytes_written_count(self, agent_fs, temp_workspace_dir):
        """Should count bytes written correctly."""
        content1 = "x" * 100
        content2 = "y" * 200

        await agent_fs.fs.write_file("/file1.txt", content1)
        await agent_fs.fs.write_file("/file2.txt", content2)

        materializer = Materializer()
        target = Path(temp_workspace_dir) / "bytes"

        result = await materializer.materialize(agent_fs, target)

        expected_bytes = len(content1.encode()) + len(content2.encode())
        assert result.bytes_written == expected_bytes

    async def test_changes_tracking(self, agent_fs, temp_workspace_dir):
        """Should track file changes."""
        await agent_fs.fs.write_file("/new.txt", "content")

        materializer = Materializer()
        target = Path(temp_workspace_dir) / "changes"

        result = await materializer.materialize(agent_fs, target)

        assert len(result.changes) > 0
        # All should be "added" for new materialization
        assert all(c.change_type == "added" for c in result.changes)


@pytest.mark.asyncio
class TestMaterializerDiff:
    """Test diff computation between layers."""

    async def test_diff_added_files(self, agent_fs, stable_fs):
        """Should detect files added in overlay."""
        await stable_fs.fs.write_file("/base.txt", "base")
        await agent_fs.fs.write_file("/new.txt", "new")

        materializer = Materializer()
        changes = await materializer.diff(agent_fs, stable_fs)

        added = [c for c in changes if c.change_type == "added"]
        assert len(added) > 0
        assert any(c.path == "/new.txt" for c in added)

    async def test_diff_modified_files(self, agent_fs, stable_fs):
        """Should detect modified files."""
        await stable_fs.fs.write_file("/file.txt", "original")
        await agent_fs.fs.write_file("/file.txt", "modified with more content")

        materializer = Materializer()
        changes = await materializer.diff(agent_fs, stable_fs)

        modified = [c for c in changes if c.change_type == "modified"]
        assert len(modified) > 0
        assert any(c.path == "/file.txt" for c in modified)

    async def test_diff_no_changes(self, agent_fs, stable_fs):
        """Should return empty diff when no changes."""
        await stable_fs.fs.write_file("/same.txt", "content")
        await agent_fs.fs.write_file("/same.txt", "content")

        materializer = Materializer()
        changes = await materializer.diff(agent_fs, stable_fs)

        # Should be empty or only contain files with same content
        modified = [c for c in changes if c.change_type == "modified" and c.path == "/same.txt"]
        assert len(modified) == 0

    async def test_diff_detects_same_size_content_change(self, agent_fs, stable_fs):
        """Diff should detect modifications even when old/new sizes are equal."""
        await stable_fs.fs.write_file("/same-size.bin", b"abc123")
        await agent_fs.fs.write_file("/same-size.bin", b"xyz123")

        materializer = Materializer()
        changes = await materializer.diff(agent_fs, stable_fs)

        modified = [c for c in changes if c.path == "/same-size.bin" and c.change_type == "modified"]
        assert len(modified) == 1
        assert modified[0].old_size == 6
        assert modified[0].new_size == 6


@pytest.mark.asyncio
class TestMaterializerProgressCallback:
    """Test progress callback functionality."""

    async def test_progress_callback_called(self, agent_fs, temp_workspace_dir):
        """Should call progress callback for each file."""
        await agent_fs.fs.write_file("/file1.txt", "content1")
        await agent_fs.fs.write_file("/file2.txt", "content2")
        await agent_fs.fs.write_file("/file3.txt", "content3")

        progress_calls = []

        def callback(path, current, total):
            progress_calls.append((path, current, total))

        materializer = Materializer(progress_callback=callback)
        target = Path(temp_workspace_dir) / "progress"

        await materializer.materialize(agent_fs, target)

        # Should have been called for each file
        assert len(progress_calls) == 3

    async def test_progress_callback_with_errors(self, agent_fs, temp_workspace_dir):
        """Progress callback should still work with errors."""
        await agent_fs.fs.write_file("/file1.txt", "content")

        calls = []

        def callback(path, current, total):
            calls.append(path)

        materializer = Materializer(progress_callback=callback)
        target = Path(temp_workspace_dir) / "errors"

        result = await materializer.materialize(agent_fs, target)

        # Should have been called despite any errors
        assert len(calls) > 0


@pytest.mark.asyncio
class TestMaterializerEdgeCases:
    """Test edge cases and error conditions."""

    async def test_empty_filesystem(self, agent_fs, temp_workspace_dir):
        """Should handle empty filesystem."""
        materializer = Materializer()
        target = Path(temp_workspace_dir) / "empty"

        result = await materializer.materialize(agent_fs, target)

        assert result.files_written == 0
        assert target.exists()

    async def test_binary_files(self, agent_fs, temp_workspace_dir):
        """Should handle binary files correctly."""
        binary_content = bytes(range(256))
        await agent_fs.fs.write_file("/binary.dat", binary_content)

        materializer = Materializer()
        target = Path(temp_workspace_dir) / "binary"

        result = await materializer.materialize(agent_fs, target)

        assert (target / "binary.dat").exists()
        assert (target / "binary.dat").read_bytes() == binary_content

    async def test_large_files(self, agent_fs, temp_workspace_dir):
        """Should handle large files."""
        large_content = "x" * (1024 * 1024)  # 1MB
        await agent_fs.fs.write_file("/large.txt", large_content)

        materializer = Materializer()
        target = Path(temp_workspace_dir) / "large"

        result = await materializer.materialize(agent_fs, target)

        assert (target / "large.txt").exists()
        assert len((target / "large.txt").read_text()) == len(large_content)

    async def test_many_files(self, agent_fs, temp_workspace_dir):
        """Should handle many files efficiently."""
        # Create 100 files
        for i in range(100):
            await agent_fs.fs.write_file(f"/file{i}.txt", f"content{i}")

        materializer = Materializer()
        target = Path(temp_workspace_dir) / "many"

        result = await materializer.materialize(agent_fs, target)

        assert result.files_written == 100
        # Verify a few
        assert (target / "file0.txt").exists()
        assert (target / "file99.txt").exists()

    async def test_special_characters_in_paths(self, agent_fs, temp_workspace_dir):
        """Should handle special characters in file paths."""
        await agent_fs.fs.write_file("/file-with-dash.txt", "content")
        await agent_fs.fs.write_file("/file_with_underscore.txt", "content")
        await agent_fs.fs.write_file("/file.multiple.dots.txt", "content")

        materializer = Materializer()
        target = Path(temp_workspace_dir) / "special"

        result = await materializer.materialize(agent_fs, target)

        assert (target / "file-with-dash.txt").exists()
        assert (target / "file_with_underscore.txt").exists()
        assert (target / "file.multiple.dots.txt").exists()

    async def test_target_path_creation(self, agent_fs, temp_workspace_dir):
        """Should create target path if it doesn't exist."""
        await agent_fs.fs.write_file("/test.txt", "content")

        materializer = Materializer()
        target = Path(temp_workspace_dir) / "nested" / "path" / "output"

        result = await materializer.materialize(agent_fs, target)

        assert target.exists()
        assert (target / "test.txt").exists()
