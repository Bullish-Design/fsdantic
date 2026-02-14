"""Tests for OverlayOperations and overlay merging."""

import pytest

from fsdantic import MergeStrategy, OverlayOperations


@pytest.mark.asyncio
class TestOverlayOperationsMerge:
    """Test merge operations between overlays."""

    async def test_merge_simple_files(self, agent_fs, stable_fs):
        """Should merge files from source to target."""
        # Create files in source
        await agent_fs.fs.write_file("/file1.txt", "content1")
        await agent_fs.fs.write_file("/file2.txt", "content2")

        ops = OverlayOperations()
        result = await ops.merge(agent_fs, stable_fs)

        # Files should be in target
        assert await stable_fs.fs.read_file("/file1.txt") == "content1"
        assert await stable_fs.fs.read_file("/file2.txt") == "content2"
        assert result.files_merged == 2

    async def test_merge_nested_directories(self, agent_fs, stable_fs):
        """Should create nested directories during merge."""
        await agent_fs.fs.write_file("/a/b/c/deep.txt", "deep")

        ops = OverlayOperations()
        result = await ops.merge(agent_fs, stable_fs)

        content = await stable_fs.fs.read_file("/a/b/c/deep.txt")
        assert content == "deep"

    async def test_merge_with_specific_path(self, agent_fs, stable_fs):
        """Should merge only specified path."""
        await agent_fs.fs.write_file("/include/file.txt", "include")
        await agent_fs.fs.write_file("/exclude/file.txt", "exclude")

        ops = OverlayOperations()
        result = await ops.merge(agent_fs, stable_fs, path="/include")

        # Should have merged /include
        assert await stable_fs.fs.read_file("/include/file.txt") == "include"

        # /exclude should not exist
        from agentfs_sdk import ErrnoException

        with pytest.raises(ErrnoException):
            await stable_fs.fs.read_file("/exclude/file.txt")

    async def test_merge_with_specific_file_path(self, agent_fs, stable_fs):
        """Should merge exactly one file when path points to a file."""
        await stable_fs.fs.write_file("/single-file", "target content")
        await agent_fs.fs.write_file("/single-file", "source content")
        await agent_fs.fs.write_file("/other-file", "other content")

        ops = OverlayOperations(strategy=MergeStrategy.PRESERVE)
        result = await ops.merge(agent_fs, stable_fs, path="/single-file")

        # Should process only the single file path and preserve target on conflict
        assert result.files_merged == 0
        assert len(result.conflicts) == 1
        assert result.conflicts[0].path == "/single-file"
        assert result.errors == []
        assert await stable_fs.fs.read_file("/single-file") == "target content"

        from agentfs_sdk import ErrnoException

        with pytest.raises(ErrnoException):
            await stable_fs.fs.read_file("/other-file")

    async def test_merge_files_merged_count(self, agent_fs, stable_fs):
        """Should count merged files correctly."""
        for i in range(5):
            await agent_fs.fs.write_file(f"/file{i}.txt", f"content{i}")

        ops = OverlayOperations()
        result = await ops.merge(agent_fs, stable_fs)

        assert result.files_merged == 5


@pytest.mark.asyncio
class TestMergeStrategies:
    """Test different merge strategies."""

    async def test_strategy_overwrite(self, agent_fs, stable_fs):
        """OVERWRITE strategy should prefer source content."""
        await stable_fs.fs.write_file("/conflict.txt", "target content")
        await agent_fs.fs.write_file("/conflict.txt", "source content")

        ops = OverlayOperations(strategy=MergeStrategy.OVERWRITE)
        result = await ops.merge(agent_fs, stable_fs)

        # Source should win
        content = await stable_fs.fs.read_file("/conflict.txt")
        assert content == "source content"

    async def test_strategy_preserve(self, agent_fs, stable_fs):
        """PRESERVE strategy should keep target content."""
        await stable_fs.fs.write_file("/conflict.txt", "target content")
        await agent_fs.fs.write_file("/conflict.txt", "source content")

        ops = OverlayOperations(strategy=MergeStrategy.PRESERVE)
        result = await ops.merge(agent_fs, stable_fs)

        # Target should be preserved
        content = await stable_fs.fs.read_file("/conflict.txt")
        assert content == "target content"

        # Should record conflict
        assert len(result.conflicts) > 0

    async def test_strategy_error(self, agent_fs, stable_fs):
        """ERROR strategy should record conflicts as errors."""
        await stable_fs.fs.write_file("/conflict.txt", "target")
        await agent_fs.fs.write_file("/conflict.txt", "source")

        ops = OverlayOperations(strategy=MergeStrategy.ERROR)
        result = await ops.merge(agent_fs, stable_fs)

        # Should record as error
        assert len(result.errors) > 0

    async def test_strategy_callback(self, agent_fs, stable_fs):
        """CALLBACK strategy should use conflict resolver."""
        await stable_fs.fs.write_file("/conflict.txt", "target")
        await agent_fs.fs.write_file("/conflict.txt", "source")

        # Custom resolver that merges content
        class MergeResolver:
            def resolve(self, conflict):
                return b"merged: " + conflict.overlay_content + b" + " + conflict.base_content

        resolver = MergeResolver()
        ops = OverlayOperations(strategy=MergeStrategy.CALLBACK, conflict_resolver=resolver)
        result = await ops.merge(agent_fs, stable_fs)

        # Should use resolver result
        content = await stable_fs.fs.read_file("/conflict.txt")
        assert "merged:" in content
        assert "source" in content
        assert "target" in content

    async def test_no_conflict_when_same_content(self, agent_fs, stable_fs):
        """Should not report conflict when content is identical."""
        same_content = "same content"
        await stable_fs.fs.write_file("/same.txt", same_content)
        await agent_fs.fs.write_file("/same.txt", same_content)

        ops = OverlayOperations(strategy=MergeStrategy.PRESERVE)
        result = await ops.merge(agent_fs, stable_fs)

        # No conflicts for identical content
        assert len(result.conflicts) == 0

    async def test_override_strategy_in_merge_call(self, agent_fs, stable_fs):
        """Should allow overriding strategy per merge call."""
        await stable_fs.fs.write_file("/file.txt", "target")
        await agent_fs.fs.write_file("/file.txt", "source")

        # Create with PRESERVE default
        ops = OverlayOperations(strategy=MergeStrategy.PRESERVE)

        # Override with OVERWRITE for this call
        result = await ops.merge(agent_fs, stable_fs, strategy=MergeStrategy.OVERWRITE)

        # Should use OVERWRITE
        content = await stable_fs.fs.read_file("/file.txt")
        assert content == "source"


@pytest.mark.asyncio
class TestOverlayOperationsListChanges:
    """Test listing changes in overlay."""

    async def test_list_changes_empty(self, agent_fs):
        """Should return empty list for empty overlay."""
        ops = OverlayOperations()
        changes = await ops.list_changes(agent_fs)

        assert changes == []

    async def test_list_changes_with_files(self, agent_fs):
        """Should list all files in overlay."""
        await agent_fs.fs.write_file("/file1.txt", "content1")
        await agent_fs.fs.write_file("/file2.txt", "content2")
        await agent_fs.fs.write_file("/dir/file3.txt", "content3")

        ops = OverlayOperations()
        changes = await ops.list_changes(agent_fs)

        assert len(changes) == 3
        assert "/file1.txt" in changes
        assert "/file2.txt" in changes
        assert "/dir/file3.txt" in changes

    async def test_list_changes_with_path(self, agent_fs):
        """Should list changes under specific path."""
        await agent_fs.fs.write_file("/include/file1.txt", "content")
        await agent_fs.fs.write_file("/exclude/file2.txt", "content")

        ops = OverlayOperations()
        changes = await ops.list_changes(agent_fs, path="/include")

        # Should only list files under /include
        assert all(c.startswith("/include") for c in changes)


@pytest.mark.asyncio
class TestOverlayOperationsReset:
    """Test resetting overlay to base state."""

    async def test_reset_overlay_all(self, agent_fs):
        """Should remove all files from overlay."""
        # Create files
        await agent_fs.fs.write_file("/file1.txt", "content1")
        await agent_fs.fs.write_file("/file2.txt", "content2")
        await agent_fs.fs.write_file("/file3.txt", "content3")
        await agent_fs.fs.write_file("/nested/file4.txt", "content4")

        ops = OverlayOperations()

        # Verify files exist
        assert len(await ops.list_changes(agent_fs)) == 4

        # Reset
        removed = await ops.reset_overlay(agent_fs)

        # Should have removed all top-level files and nested directory
        assert removed == 4
        assert len(await ops.list_changes(agent_fs)) == 0

    async def test_reset_overlay_specific_paths(self, agent_fs):
        """Should remove only specified paths."""
        await agent_fs.fs.write_file("/keep.txt", "keep")
        await agent_fs.fs.write_file("/remove1.txt", "remove")
        await agent_fs.fs.write_file("/remove2.txt", "remove")
        await agent_fs.fs.write_file("/remove-dir/file3.txt", "remove")

        ops = OverlayOperations()
        removed = await ops.reset_overlay(
            agent_fs, paths=["/remove1.txt", "/remove2.txt", "/remove-dir"]
        )

        assert removed == 3

        # /keep.txt should still exist
        changes = await ops.list_changes(agent_fs)
        assert "/keep.txt" in changes
        assert "/remove1.txt" not in changes
        assert "/remove2.txt" not in changes
        assert "/remove-dir/file3.txt" not in changes

    async def test_reset_empty_overlay(self, agent_fs):
        """Should handle resetting empty overlay."""
        ops = OverlayOperations()
        removed = await ops.reset_overlay(agent_fs)

        assert removed == 0

    async def test_reset_nonexistent_paths(self, agent_fs):
        """Should handle nonexistent paths gracefully."""
        await agent_fs.fs.write_file("/exists.txt", "exists")

        ops = OverlayOperations()
        removed = await ops.reset_overlay(
            agent_fs, paths=["/nonexistent.txt", "/exists.txt"]
        )

        # Should skip nonexistent path and remove existing path
        assert removed == 1

    async def test_reset_overlay_reports_errors(self, agent_fs):
        """Should raise with details when paths fail to reset."""
        ops = OverlayOperations()

        with pytest.raises(RuntimeError, match="Failed to reset"):
            await ops.reset_overlay(agent_fs, paths=["/"])


@pytest.mark.asyncio
class TestMergeConflicts:
    """Test conflict detection and handling."""

    async def test_conflict_detection(self, agent_fs, stable_fs):
        """Should detect conflicts correctly."""
        await stable_fs.fs.write_file("/conflict.txt", "base")
        await agent_fs.fs.write_file("/conflict.txt", "overlay")

        ops = OverlayOperations(strategy=MergeStrategy.PRESERVE)
        result = await ops.merge(agent_fs, stable_fs)

        assert len(result.conflicts) == 1
        conflict = result.conflicts[0]

        assert conflict.path == "/conflict.txt"
        assert conflict.overlay_content == b"overlay"
        assert conflict.base_content == b"base"

    async def test_multiple_conflicts(self, agent_fs, stable_fs):
        """Should handle multiple conflicts."""
        for i in range(5):
            await stable_fs.fs.write_file(f"/file{i}.txt", f"base{i}")
            await agent_fs.fs.write_file(f"/file{i}.txt", f"overlay{i}")

        ops = OverlayOperations(strategy=MergeStrategy.PRESERVE)
        result = await ops.merge(agent_fs, stable_fs)

        assert len(result.conflicts) == 5

    async def test_conflict_sizes(self, agent_fs, stable_fs):
        """Should record conflict file sizes."""
        await stable_fs.fs.write_file("/conflict.txt", "short")
        await agent_fs.fs.write_file("/conflict.txt", "much longer content")

        ops = OverlayOperations(strategy=MergeStrategy.PRESERVE)
        result = await ops.merge(agent_fs, stable_fs)

        conflict = result.conflicts[0]
        assert conflict.overlay_size == len(b"much longer content")
        assert conflict.base_size == len(b"short")


@pytest.mark.asyncio
class TestOverlayOperationsEdgeCases:
    """Test edge cases and error conditions."""

    async def test_merge_empty_source(self, agent_fs, stable_fs):
        """Should handle empty source overlay."""
        ops = OverlayOperations()
        result = await ops.merge(agent_fs, stable_fs)

        assert result.files_merged == 0
        assert len(result.conflicts) == 0
        assert len(result.errors) == 0

    async def test_merge_to_empty_target(self, agent_fs, stable_fs):
        """Should merge to empty target without conflicts."""
        await agent_fs.fs.write_file("/file.txt", "content")

        ops = OverlayOperations()
        result = await ops.merge(agent_fs, stable_fs)

        assert result.files_merged == 1
        assert len(result.conflicts) == 0

    async def test_merge_binary_files(self, agent_fs, stable_fs):
        """Should handle binary files correctly."""
        binary = bytes(range(256))
        await agent_fs.fs.write_file("/binary.dat", binary)

        ops = OverlayOperations()
        result = await ops.merge(agent_fs, stable_fs)

        # Default read_file() returns text, so request bytes explicitly for binary assertions.
        merged_content = await stable_fs.fs.read_file("/binary.dat", encoding=None)
        assert merged_content == binary

    async def test_merge_large_files(self, agent_fs, stable_fs):
        """Should handle large files."""
        large = "x" * (1024 * 1024)  # 1MB
        await agent_fs.fs.write_file("/large.txt", large)

        ops = OverlayOperations()
        result = await ops.merge(agent_fs, stable_fs)

        content = await stable_fs.fs.read_file("/large.txt")
        assert len(content) == len(large)

    async def test_merge_many_files(self, agent_fs, stable_fs):
        """Should handle merging many files."""
        # Create 100 files
        for i in range(100):
            await agent_fs.fs.write_file(f"/file{i}.txt", f"content{i}")

        ops = OverlayOperations()
        result = await ops.merge(agent_fs, stable_fs)

        assert result.files_merged == 100

    async def test_merge_deep_nesting(self, agent_fs, stable_fs):
        """Should handle deeply nested paths."""
        deep_path = "/a/b/c/d/e/f/g/h/i/j/file.txt"
        await agent_fs.fs.write_file(deep_path, "deep")

        ops = OverlayOperations()
        result = await ops.merge(agent_fs, stable_fs)

        content = await stable_fs.fs.read_file(deep_path)
        assert content == "deep"


@pytest.mark.asyncio
class TestOverlayOperationsIntegration:
    """Integration tests for complete workflows."""

    async def test_merge_and_reset_workflow(self, agent_fs, stable_fs):
        """Test merge followed by reset."""
        # Create files in agent
        await agent_fs.fs.write_file("/work.txt", "work content")
        await agent_fs.fs.write_file("/temp.txt", "temp content")

        ops = OverlayOperations()

        # Merge to stable
        merge_result = await ops.merge(agent_fs, stable_fs)
        assert merge_result.files_merged == 2

        # Reset agent
        removed = await ops.reset_overlay(agent_fs)
        assert removed == 2

        # Agent should be clean
        changes = await ops.list_changes(agent_fs)
        assert len(changes) == 0

        # Stable should have the files
        assert await stable_fs.fs.read_file("/work.txt") == "work content"

    async def test_iterative_merges(self, agent_fs, stable_fs):
        """Test multiple merge operations."""
        ops = OverlayOperations()

        # First batch
        await agent_fs.fs.write_file("/batch1.txt", "first")
        await ops.merge(agent_fs, stable_fs)
        await ops.reset_overlay(agent_fs)

        # Second batch
        await agent_fs.fs.write_file("/batch2.txt", "second")
        await ops.merge(agent_fs, stable_fs)
        await ops.reset_overlay(agent_fs)

        # Both should be in stable
        assert await stable_fs.fs.read_file("/batch1.txt") == "first"
        assert await stable_fs.fs.read_file("/batch2.txt") == "second"

    async def test_conflict_resolution_workflow(self, agent_fs, stable_fs):
        """Test handling conflicts in a realistic workflow."""
        # Base version
        await stable_fs.fs.write_file("/config.json", '{"version": 1}')

        # Agent modifies
        await agent_fs.fs.write_file("/config.json", '{"version": 2}')

        ops = OverlayOperations(strategy=MergeStrategy.OVERWRITE)
        result = await ops.merge(agent_fs, stable_fs)

        # Should have merged with overwrite
        config = await stable_fs.fs.read_file("/config.json")
        assert '"version": 2' in config

    async def test_selective_merge_with_reset(self, agent_fs, stable_fs):
        """Test merging some files and resetting others."""
        # Create multiple files
        await agent_fs.fs.write_file("/keep.txt", "keep")
        await agent_fs.fs.write_file("/discard.txt", "discard")

        ops = OverlayOperations()

        # Merge only /keep.txt by using path parameter
        await ops.merge(agent_fs, stable_fs, path="/keep.txt")

        # Reset /discard.txt
        await ops.reset_overlay(agent_fs, paths=["/discard.txt"])

        # Stable should only have /keep.txt
        assert await stable_fs.fs.read_file("/keep.txt") == "keep"

        from agentfs_sdk import ErrnoException

        with pytest.raises(ErrnoException):
            await stable_fs.fs.read_file("/discard.txt")
