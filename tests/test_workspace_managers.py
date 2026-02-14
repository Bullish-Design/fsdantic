"""Tests for workspace overlay/materialization manager APIs."""

from pathlib import Path

import pytest

from fsdantic import (
    ConflictResolution,
    MaterializationManager,
    Materializer,
    MergeStrategy,
    OverlayManager,
    Workspace,
)


@pytest.mark.asyncio
class TestWorkspaceManagerTypes:
    async def test_workspace_exposes_manager_types(self, agent_fs):
        workspace = Workspace(agent_fs)

        assert isinstance(workspace.overlay, OverlayManager)
        assert isinstance(workspace.materialize, MaterializationManager)


@pytest.mark.asyncio
class TestOverlayManager:
    async def test_merge_accepts_workspace_source(self, agent_fs, stable_fs):
        source = Workspace(agent_fs)
        target = Workspace(stable_fs)

        await source.files.write("/shared.txt", "overlay")
        await target.files.write("/shared.txt", "base")
        await source.files.write("/added.txt", "new")

        result = await target.overlay.merge(source, strategy=MergeStrategy.OVERWRITE)

        assert result.files_merged >= 2
        assert await target.files.read("/shared.txt") == "overlay"
        assert await target.files.read("/added.txt") == "new"

    async def test_reset_and_list_changes_use_workspace_overlay(self, agent_fs):
        workspace = Workspace(agent_fs)
        await workspace.files.write("/a.txt", "a")
        await workspace.files.write("/nested/b.txt", "b")

        changes = await workspace.overlay.list_changes()
        assert "/a.txt" in changes
        assert "/nested/b.txt" in changes

        removed = await workspace.overlay.reset(paths=["/a.txt"])
        assert removed == 1

        changes = await workspace.overlay.list_changes()
        assert "/a.txt" not in changes
        assert "/nested/b.txt" in changes


@pytest.mark.asyncio
class TestMaterializationManager:
    async def test_to_disk_diff_and_preview_accept_workspace_base(
        self, agent_fs, stable_fs, temp_workspace_dir
    ):
        overlay = Workspace(agent_fs)
        base = Workspace(stable_fs)

        await base.files.write("/base.txt", "base")
        await overlay.files.write("/base.txt", "overlay")
        await overlay.files.write("/new.txt", "new")

        target = Path(temp_workspace_dir) / "materialized"
        result = await overlay.materialize.to_disk(target, base=base)

        assert result.files_written >= 2
        assert (target / "base.txt").read_text() == "overlay"
        assert (target / "new.txt").read_text() == "new"

        diff = await overlay.materialize.diff(base)
        preview = await overlay.materialize.preview(base)

        assert {(c.path, c.change_type) for c in diff} == {
            (c.path, c.change_type) for c in preview
        }
        assert any(c.path == "/new.txt" and c.change_type == "added" for c in diff)
        assert any(c.path == "/base.txt" and c.change_type == "modified" for c in diff)

    async def test_to_disk_reports_partial_failures(self, agent_fs, temp_workspace_dir):
        workspace = Workspace(agent_fs)
        await workspace.files.write("/conflict.txt", "overlay")

        target = Path(temp_workspace_dir) / "partial"
        target.mkdir(parents=True, exist_ok=True)
        (target / "conflict.txt").write_text("existing")

        workspace._materialize = MaterializationManager(
            workspace.raw,
            materializer=Materializer(conflict_resolution=ConflictResolution.ERROR),
        )

        result = await workspace.materialize.to_disk(target, clean=False)

        assert result.files_written == 0
        assert any(path == "/conflict.txt" for path, _ in result.errors)
