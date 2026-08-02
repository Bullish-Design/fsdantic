"""Regression tests for Phase 5: polish and hygiene (L1-L18)."""

import asyncio
import os
import time
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from fsdantic import (
    FileManager,
    KVManager,
    Materializer,
    OverlayOperations,
    ToolCall,
    ToolCallStatus,
    ViewQuery,
    __version__,
)
from fsdantic._internal.paths import normalize_path
from fsdantic.exceptions import InvalidPathError, OverlayError


@pytest.mark.asyncio
class TestConcurrencyLimits:
    """L1: read_many/get_many must bound fan-out."""

    async def test_read_many_respects_concurrency_limit(self, agent_fs):
        await agent_fs.fs.write_file("/a.txt", "a")
        await agent_fs.fs.write_file("/b.txt", "b")
        manager = FileManager(agent_fs)

        max_in_flight = 0
        current = 0
        orig_read = manager.read

        async def tracking_read(path, **kwargs):
            nonlocal max_in_flight, current
            current += 1
            max_in_flight = max(max_in_flight, current)
            await asyncio.sleep(0.01)
            try:
                return await orig_read(path, **kwargs)
            finally:
                current -= 1

        manager.read = tracking_read
        try:
            result = await manager.read_many(["/a.txt", "/b.txt", "/a.txt", "/b.txt"], concurrency_limit=2)
        finally:
            manager.read = orig_read

        assert all(item.ok for item in result.items)
        assert max_in_flight <= 2

    async def test_get_many_respects_concurrency_limit(self, agent_fs):
        kv = KVManager(agent_fs)
        await kv.set("k1", 1)
        await kv.set("k2", 2)
        await kv.set("k3", 3)

        max_in_flight = 0
        current = 0
        orig_get = kv.get

        async def tracking_get(key, **kwargs):
            nonlocal max_in_flight, current
            current += 1
            max_in_flight = max(max_in_flight, current)
            await asyncio.sleep(0.01)
            try:
                return await orig_get(key, **kwargs)
            finally:
                current -= 1

        kv.get = tracking_get
        try:
            result = await kv.get_many(["k1", "k2", "k3", "k1"], concurrency_limit=2, default=None)
        finally:
            kv.get = orig_get

        assert all(item.ok for item in result.items)
        assert max_in_flight <= 2


class TestQueryValidation:
    """L3: invalid regex must surface as pydantic ValidationError."""

    def test_query_invalid_regex_raises_validation_error(self):
        with pytest.raises(ValidationError) as exc_info:
            ViewQuery(regex_pattern="([unclosed")
        assert "regex_pattern" in str(exc_info.value)


class TestToolCallDuration:
    """L4: ToolCall serializes exactly one duration key."""

    def test_toolcall_dump_has_single_duration_key(self):
        tc = ToolCall(
            id=1,
            name="x",
            status=ToolCallStatus.SUCCESS,
            result={},
            started_at=datetime(2024, 1, 1),
            completed_at=datetime(2024, 1, 1, 0, 0, 1),
        )
        payload = tc.model_dump()
        duration_keys = [k for k in payload if "duration" in k]
        assert duration_keys == ["duration_ms"]
        assert payload["duration_ms"] == 1000.0

    def test_toolcall_explicit_duration_preferred(self):
        tc = ToolCall(
            id=1,
            name="x",
            status=ToolCallStatus.SUCCESS,
            result={},
            started_at=datetime(2024, 1, 1),
            completed_at=datetime(2024, 1, 1, 0, 0, 10),
            duration_ms=99.0,
        )
        assert tc.duration_ms == 99.0
        assert tc.model_dump()["duration_ms"] == 99.0


class TestPathNormalization:
    """L6: relative dot-dot semantics."""

    def test_normalize_path_relative_dotdot(self):
        assert normalize_path("..", absolute=False) == ".."
        assert normalize_path("../x", absolute=False) == "../x"

    def test_normalize_path_absolute_dotdot_collapses(self):
        assert normalize_path("/a/../b") == "/b"
        assert normalize_path("..") == "/"  # default absolute=True treats as absolute
        assert normalize_path("../x") == "/x"


class TestGlobSemantics:
    """L7: pin glob semantics: dotfiles, a/**, empty pattern."""

    def test_glob_matches_dotfiles(self):
        query = ViewQuery(path_pattern="*.py")
        assert query.matches_path("/.hidden.py") is True

    def test_glob_double_star_descendants_only(self):
        query = ViewQuery(path_pattern="a/**")
        assert query.matches_path("/a/b/c.py") is True
        assert query.matches_path("/a") is False

    def test_glob_empty_pattern_matches_all(self):
        query = ViewQuery(path_pattern="")
        assert query.matches_path("/x.txt") is True
        assert query.matches_path("/deep/nested/y.py") is True


class TestOverlayRoundtrip:
    """L9: merge and reset round-trip with nested paths."""

    async def test_merge_and_reset_roundtrip(self, agent_fs, stable_fs):
        ops = OverlayOperations()
        await agent_fs.fs.write_file("/data/nested/file.txt", "content")
        await agent_fs.fs.write_file("/data/nested/other.txt", "other")

        result = await ops.merge(agent_fs, stable_fs)
        assert result.files_merged == 2
        assert await stable_fs.fs.read_file("/data/nested/file.txt") == "content"

        removed = await ops.reset_overlay(agent_fs)
        assert removed == 2
        assert await agent_fs.fs.readdir("/data/nested") == []


def test_overlay_module_has_docstring():
    """L10: overlay module docstring is present (not swallowed by __future__)."""
    import fsdantic.overlay as overlay_module

    assert overlay_module.__doc__
    assert "overlay" in overlay_module.__doc__.lower()


@pytest.mark.asyncio
class TestEINVALContext:
    """L12: EINVAL errors carry syscall context for disambiguation."""

    def test_einval_translation_includes_syscall(self):
        from agentfs_sdk import ErrnoException

        from fsdantic._internal.errors import translate_agentfs_error

        err = ErrnoException(
            code="EINVAL",
            syscall="rename",
            path="/dir/sub/dir",
            message="invalid argument",
        )
        translated = translate_agentfs_error(err, "rename-into-subtree")

        assert isinstance(translated, InvalidPathError)
        context = translated.context or {}
        assert context.get("syscall") == "rename"
        assert context.get("agentfs_code") == "EINVAL"


@pytest.mark.asyncio
class TestStagingRecovery:
    """L13: orphaned .bak-*/.tmp-* siblings are recovered/cleaned."""

    async def test_materialize_recovers_orphaned_backup(self, tmp_path, agent_fs):
        out = Path(tmp_path) / "out"
        out.mkdir(parents=True)
        (out / "old.txt").write_text("stale")

        # Simulate a crash between target->backup and staging->target.
        backup = Path(tmp_path) / "out.bak-deadbeef"
        out.rename(backup)

        await agent_fs.fs.write_file("/new.txt", "new")
        await Materializer().materialize(agent_fs, out)

        assert out.exists()
        assert (out / "new.txt").read_text() == "new"
        assert not backup.exists()

    async def test_materialize_cleans_stale_staging(self, tmp_path, agent_fs):
        out = Path(tmp_path) / "out"
        out.mkdir(parents=True)
        (out / "keep.txt").write_text("keep")

        stale = Path(tmp_path) / "out.tmp-olduuid"
        stale.mkdir(parents=True)
        (stale / "junk.txt").write_text("junk")
        # Age it beyond the 24h cutoff.
        old_time = time.time() - 25 * 3600
        os.utime(stale, (old_time, old_time))

        await agent_fs.fs.write_file("/new.txt", "new")
        await Materializer().materialize(agent_fs, out, clean=False)

        assert not stale.exists()
        assert (out / "keep.txt").read_text() == "keep"


@pytest.mark.asyncio
class TestProgressAndLabels:
    """L14: progress callback receives a total; changes are truthfully labeled."""

    async def test_progress_callback_receives_total(self, tmp_path, agent_fs):
        await agent_fs.fs.write_file("/a.txt", "a")
        await agent_fs.fs.write_file("/b.txt", "b")

        calls: list[tuple[str, int, int]] = []
        materializer = Materializer(progress_callback=lambda path, cur, total: calls.append((path, cur, total)))

        out = Path(tmp_path) / "out"
        await materializer.materialize(agent_fs, out)

        assert len(calls) == 2
        assert {c[0] for c in calls} == {"/a.txt", "/b.txt"}
        assert all(c[2] == 2 for c in calls)  # total is 2, not -1

    async def test_changes_marks_modified_when_existing(self, tmp_path, agent_fs):
        out = Path(tmp_path) / "out"
        out.mkdir(parents=True)
        (out / "f.txt").write_text("OLD-CONTENT-IS-LONG")

        await agent_fs.fs.write_file("/f.txt", "NEW")

        result = await Materializer().materialize(agent_fs, out, clean=False)
        modified = [c for c in result.changes if c.change_type == "modified"]
        assert len(modified) == 1
        assert modified[0].path == "/f.txt"
        assert modified[0].old_size == len("OLD-CONTENT-IS-LONG")
        assert modified[0].new_size == len("NEW")


@pytest.mark.asyncio
class TestDiffSingleReadPass:
    """L5/L8: diff compares equal-size files in a single read pass per side."""

    async def test_diff_same_size_different_content(self, agent_fs, stable_fs):
        await agent_fs.fs.write_file("/f.txt", "AAAA")
        await stable_fs.fs.write_file("/f.txt", "BBBB")

        changes = await Materializer().diff(agent_fs, stable_fs)
        assert any(c.change_type == "modified" and c.path == "/f.txt" for c in changes)

    async def test_diff_reads_each_side_once(self, agent_fs, stable_fs):
        await agent_fs.fs.write_file("/f.txt", "AAAA")
        await stable_fs.fs.write_file("/f.txt", "BBBB")

        from fsdantic import FileManager

        reads = {"overlay": 0, "base": 0}
        orig_read_stream = FileManager.read_stream

        async def counting_read_stream(self, path, **kwargs):
            reads["overlay" if self.agent_fs is agent_fs else "base"] += 1
            async for chunk in orig_read_stream(self, path, **kwargs):
                yield chunk

        FileManager.read_stream = counting_read_stream
        try:
            await Materializer().diff(agent_fs, stable_fs)
        finally:
            FileManager.read_stream = orig_read_stream

        assert reads == {"overlay": 1, "base": 1}


class TestVersion:
    """L17: version drift fixed."""

    def test_version_matches_package(self):
        assert __version__ == "0.5.0"
