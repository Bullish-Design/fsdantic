"""Regression tests for Phase 2: silent-correctness fixes.

Covers:
- H1: materialize(filters=...) actually filters files (both layers).
- H3: diff() reports base-only files as ``deleted``.
- M3: View.search_content() does not mutate the shared query.
- M4: CALLBACK merge without a resolver raises OverlayError.
"""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest
from agentfs_sdk import AgentFS, AgentFSOptions as SDKAgentFSOptions

from fsdantic import (
    FileManager,
    Materializer,
    MergeStrategy,
    OverlayError,
    OverlayManager,
    OverlayOperations,
    View,
    ViewQuery,
)


async def _open_fs(tmpdir: str, name: str) -> AgentFS:
    return await AgentFS.open(SDKAgentFSOptions(path=os.path.join(tmpdir, name)))


@pytest.mark.asyncio
class TestMaterializeFilters:
    """H1: filters must be honored during materialization."""

    async def _seed(self, tmpdir: str) -> tuple[AgentFS, Path]:
        fs = await _open_fs(tmpdir, "seed.db")
        for path, content in [
            ("/keep.txt", "keep"),
            ("/drop.py", "drop"),
            ("/sub/keep2.txt", "k2"),
            ("/sub/drop2.py", "d2"),
        ]:
            await fs.fs.write_file(path, content)
        out = Path(tmpdir) / "out"
        return fs, out

    async def test_materialize_filters_only_matching_files(self, tmp_path):
        fs, out = await self._seed(str(tmp_path))
        try:
            result = await Materializer().materialize(
                fs, out, filters=ViewQuery(path_pattern="*.txt")
            )

            written = {c.path for c in result.changes}
            assert written == {"/keep.txt", "/sub/keep2.txt"}
            on_disk = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
            assert on_disk == {"keep.txt", "sub/keep2.txt"}
        finally:
            await fs.close()

    async def test_materialize_filters_applies_to_base_layer(self, tmp_path):
        base = await _open_fs(str(tmp_path), "base.db")
        overlay = await _open_fs(str(tmp_path), "overlay.db")
        out = Path(tmp_path) / "out"
        try:
            await base.fs.write_file("/base_keep.txt", "x")
            await base.fs.write_file("/base_drop.py", "x")
            await overlay.fs.write_file("/ov_keep.txt", "y")

            result = await Materializer().materialize(
                overlay,
                out,
                base_fs=base,
                filters=ViewQuery(path_pattern="*.txt"),
            )

            written = {c.path for c in result.changes}
            assert "/base_drop.py" not in written
            assert "/base_keep.txt" in written
            assert "/ov_keep.txt" in written
        finally:
            await base.close()
            await overlay.close()

    async def test_materialize_filters_nested_pattern(self, tmp_path):
        fs, out = await self._seed(str(tmp_path))
        try:
            result = await Materializer().materialize(
                fs, out, filters=ViewQuery(path_pattern="sub/*.txt")
            )
            assert {c.path for c in result.changes} == {"/sub/keep2.txt"}
        finally:
            await fs.close()

    async def test_materialize_filters_with_min_size(self, tmp_path):
        fs, out = await self._seed(str(tmp_path))
        try:
            # keep.txt/drop.py are 4 bytes; keep2.txt/drop2.py are 2 bytes.
            result = await Materializer().materialize(
                fs, out, filters=ViewQuery(path_pattern="*.txt", min_size=3)
            )
            assert {c.path for c in result.changes} == {"/keep.txt"}
        finally:
            await fs.close()


@pytest.mark.asyncio
class TestDiffDeleted:
    """H3: diff/preview must report base-only files as deleted."""

    async def test_diff_reports_deleted_files(self, tmp_path):
        base = await _open_fs(str(tmp_path), "b.db")
        overlay = await _open_fs(str(tmp_path), "o.db")
        try:
            await base.fs.write_file("/only_in_base.txt", "x")
            await base.fs.write_file("/same.txt", "identical")
            await overlay.fs.write_file("/same.txt", "identical")

            changes = await Materializer().diff(overlay, base)
            deleted = [(c.change_type, c.path, c.old_size) for c in changes if c.change_type == "deleted"]
            assert deleted == [("deleted", "/only_in_base.txt", 1)]
        finally:
            await base.close()
            await overlay.close()

    async def test_preview_includes_deleted(self, tmp_path):
        base = await _open_fs(str(tmp_path), "b.db")
        overlay = await _open_fs(str(tmp_path), "o.db")
        try:
            await base.fs.write_file("/gone.txt", "x")
            from fsdantic import MaterializationManager

            manager = MaterializationManager(overlay)
            changes = await manager.preview(base)
            assert any(c.change_type == "deleted" and c.path == "/gone.txt" for c in changes)
        finally:
            await base.close()
            await overlay.close()

    async def test_diff_empty_overlay_reports_all_base_files_deleted(self, tmp_path):
        base = await _open_fs(str(tmp_path), "b.db")
        overlay = await _open_fs(str(tmp_path), "o.db")
        try:
            await base.fs.write_file("/a.txt", "1")
            await base.fs.write_file("/b.txt", "2")
            changes = await Materializer().diff(overlay, base)
            assert {c.path for c in changes if c.change_type == "deleted"} == {"/a.txt", "/b.txt"}
        finally:
            await base.close()
            await overlay.close()


@pytest.mark.asyncio
class TestSearchContentNoMutation:
    """M3: search_content must not mutate the shared View query."""

    async def test_search_content_does_not_mutate_query(self, tmp_path):
        fs = await _open_fs(str(tmp_path), "a.db")
        try:
            await fs.fs.write_file("/z.txt", "hello world")
            view = View(
                agent=fs,
                query=ViewQuery(path_pattern="*.txt", include_content=False, content_pattern="hello"),
            )
            matches = await view.search_content()
            assert len(matches) == 1
            assert view.query.include_content is False
        finally:
            await fs.close()

    async def test_search_content_no_mutation_when_load_raises(self, tmp_path):
        fs = await _open_fs(str(tmp_path), "a.db")
        try:
            await fs.fs.write_file("/z.txt", "hello world")
            view = View(
                agent=fs,
                query=ViewQuery(path_pattern="*.txt", include_content=False, content_pattern="hello"),
            )
            orig_query = FileManager.query

            async def broken_query(self, q):
                raise RuntimeError("boom")

            FileManager.query = broken_query
            try:
                with pytest.raises(RuntimeError):
                    await view.search_content()
            finally:
                FileManager.query = orig_query

            assert view.query.include_content is False
        finally:
            await fs.close()

    async def test_search_content_concurrent_views(self, tmp_path):
        fs = await _open_fs(str(tmp_path), "a.db")
        try:
            await fs.fs.write_file("/z.txt", "hello world")
            view = View(
                agent=fs,
                query=ViewQuery(path_pattern="*.txt", include_content=False, content_pattern="hello"),
            )
            orig_query = FileManager.query

            async def slow_query(self, q):
                await asyncio.sleep(0.2)
                return await orig_query(self, q)

            FileManager.query = slow_query
            try:
                results = await asyncio.gather(view.search_content(), view.search_content())
            finally:
                FileManager.query = orig_query

            assert all(len(m) == 1 for m in results)
            assert view.query.include_content is False
        finally:
            await fs.close()


class _EchoResolver:
    """Resolver that returns a fixed payload."""

    def resolve(self, conflict) -> bytes:
        return b"RESOLVED"


@pytest.mark.asyncio
class TestCallbackRequiresResolver:
    """M4: CALLBACK with no resolver must fail loud."""

    async def test_merge_callback_without_resolver_raises(self, tmp_path):
        src = await _open_fs(str(tmp_path), "s.db")
        dst = await _open_fs(str(tmp_path), "d.db")
        try:
            await src.fs.write_file("/f.txt", "OVERLAY")
            await dst.fs.write_file("/f.txt", "BASE")

            with pytest.raises(OverlayError):
                await OverlayOperations(strategy=MergeStrategy.CALLBACK).merge(src, dst)

            # Target untouched.
            assert await dst.fs.read_file("/f.txt") == "BASE"
        finally:
            await src.close()
            await dst.close()

    async def test_merge_callback_with_resolver(self, tmp_path):
        src = await _open_fs(str(tmp_path), "s.db")
        dst = await _open_fs(str(tmp_path), "d.db")
        try:
            await src.fs.write_file("/f.txt", "OVERLAY")
            await dst.fs.write_file("/f.txt", "BASE")

            result = await OverlayOperations(strategy=MergeStrategy.CALLBACK).merge(
                src, dst, conflict_resolver=_EchoResolver()
            )
            assert await dst.fs.read_file("/f.txt") == "RESOLVED"
            assert len(result.conflicts) == 1
        finally:
            await src.close()
            await dst.close()

    async def test_overlay_manager_merge_passes_resolver(self, tmp_path):
        src = await _open_fs(str(tmp_path), "s.db")
        dst = await _open_fs(str(tmp_path), "d.db")
        try:
            await src.fs.write_file("/f.txt", "OVERLAY")
            await dst.fs.write_file("/f.txt", "BASE")

            manager = OverlayManager(dst)
            result = await manager.merge(
                src, strategy=MergeStrategy.CALLBACK, conflict_resolver=_EchoResolver()
            )
            assert await dst.fs.read_file("/f.txt") == "RESOLVED"
            assert result.files_merged == 1
        finally:
            await src.close()
            await dst.close()
