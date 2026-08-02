"""Tests for workspace onboarding and lifecycle."""

import pytest
from pydantic import ValidationError

from fsdantic import AgentFSOptions, Fsdantic, Workspace
from fsdantic.client import SDKAgentFSOptions


class FakeAgentFS:
    """Small fake AgentFS object for workspace tests."""

    def __init__(self):
        self.kv = object()
        self.closed_calls = 0

    async def close(self):
        self.closed_calls += 1


@pytest.mark.asyncio
class TestOpenBehavior:
    async def test_open_by_id(self, monkeypatch):
        captured = {}

        class _FakeConnection:
            """Minimal stand-in for the turso connection used by the open seam."""

            async def execute(self, sql, parameters=()):
                return self

            async def fetchone(self):
                return ("wal",)

        async def fake_connect(database, **kwargs):
            captured["database"] = database
            captured["kwargs"] = kwargs
            return _FakeConnection()

        async def fake_open_with(conn):
            captured["conn"] = conn
            return FakeAgentFS()

        monkeypatch.setattr("fsdantic.client.turso_connect", fake_connect)
        monkeypatch.setattr("fsdantic.client.AgentFS.open_with", fake_open_with)

        workspace = await Fsdantic.open(id="agent-123")

        assert isinstance(workspace, Workspace)
        assert workspace.readonly is False
        # The unified seam resolves the id-based path itself and hands a
        # connection proxy (readonly guard) to AgentFS.open_with.
        assert captured["database"] == ".agentfs/agent-123.db"
        assert captured["kwargs"] == {
            "experimental_features": None,
            "isolation_level": "DEFERRED",
        }
        from fsdantic._internal.readonly import _ReadonlyGuard

        assert isinstance(captured["conn"], _ReadonlyGuard)
        assert captured["conn"].readonly is False

    async def test_open_by_path(self, monkeypatch, tmp_path):
        captured = {}

        class _FakeConnection:
            async def execute(self, sql, parameters=()):
                return self

            async def fetchone(self):
                return ("wal",)

        async def fake_connect(database, **kwargs):
            captured["database"] = database
            captured["kwargs"] = kwargs
            return _FakeConnection()

        async def fake_open_with(conn):
            captured["conn"] = conn
            return FakeAgentFS()

        monkeypatch.setattr("fsdantic.client.turso_connect", fake_connect)
        monkeypatch.setattr("fsdantic.client.AgentFS.open_with", fake_open_with)

        db_path = str(tmp_path / "agent.db")
        workspace = await Fsdantic.open(path=db_path)

        assert isinstance(workspace, Workspace)
        assert captured["database"] == db_path
        assert captured["kwargs"] == {
            "experimental_features": None,
            "isolation_level": "DEFERRED",
        }

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({}, None),
            ({"id": "abc", "path": "/tmp/test.db"}, None),
            ({"id": ""}, None),
            ({"path": ""}, None),
            ({"id": 1}, "Selector values must be strings"),
        ],
    )
    async def test_open_invalid_args(self, kwargs, message):
        with pytest.raises(ValidationError) as exc_info:
            await Fsdantic.open(**kwargs)

        if message is not None:
            assert message in str(exc_info.value)

    async def test_open_with_options(self, monkeypatch, tmp_path):
        captured = {}

        async def fake_open(options):
            captured["options"] = options
            return FakeAgentFS()

        monkeypatch.setattr("fsdantic.client.AgentFS.open", fake_open)

        options = AgentFSOptions(path=str(tmp_path / "agent.db"))
        workspace = await Fsdantic.open_with_options(options)

        assert isinstance(workspace, Workspace)
        assert isinstance(captured["options"], SDKAgentFSOptions)
        assert captured["options"].path == options.path


@pytest.mark.asyncio
class TestWorkspaceSemantics:
    async def test_lazy_properties_are_cached(self):
        raw = FakeAgentFS()
        workspace = Workspace(raw)

        assert workspace.files is workspace.files
        assert workspace.kv is workspace.kv
        assert workspace.overlay is workspace.overlay
        assert workspace.materialize is workspace.materialize

    async def test_raw_exposure_preserves_identity(self):
        raw = FakeAgentFS()
        workspace = Workspace(raw)

        assert workspace.raw is raw

    async def test_context_manager_closes_on_success(self):
        raw = FakeAgentFS()
        workspace = Workspace(raw)

        async with workspace as opened:
            assert opened is workspace

        assert raw.closed_calls == 1

    async def test_context_manager_closes_on_exception(self):
        raw = FakeAgentFS()
        workspace = Workspace(raw)

        with pytest.raises(RuntimeError):
            async with workspace:
                raise RuntimeError("boom")

        assert raw.closed_calls == 1

    async def test_idempotent_close(self):
        raw = FakeAgentFS()
        workspace = Workspace(raw)

        await workspace.close()
        await workspace.close()

        assert raw.closed_calls == 1

    async def test_independent_workspaces_do_not_share_managers(self):
        first = Workspace(FakeAgentFS())
        second = Workspace(FakeAgentFS())

        assert first.files is not second.files
        assert first.kv is not second.kv
        assert first.overlay is not second.overlay
        assert first.materialize is not second.materialize
