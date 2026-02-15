"""Focused tests for KVManager simple and typed pathways."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from fsdantic.exceptions import KVStoreError, KeyNotFoundError, SerializationError
from fsdantic.kv import KVManager
from fsdantic.workspace import Workspace


class FakeKVBackend:
    """Minimal in-memory KV backend used to isolate manager semantics."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.data.get(key)

    async def set(self, key: str, value: Any) -> None:
        if key.startswith("bad:serialize:"):
            raise TypeError("not serializable")
        if key.startswith("fail:set:"):
            raise RuntimeError("write failed")
        self.data[key] = value

    async def delete(self, key: str) -> None:
        if key.startswith("fail:delete:"):
            raise RuntimeError("delete failed")
        self.data.pop(key, None)

    async def list(self, prefix: str = "") -> list[dict[str, Any]]:
        if prefix.startswith("fail:list:"):
            raise RuntimeError("list failed")
        return [
            {"key": key, "value": value}
            for key, value in self.data.items()
            if key.startswith(prefix)
        ]


class FakeAgentFS:
    def __init__(self) -> None:
        self.kv = FakeKVBackend()


class Profile(BaseModel):
    required_name: str
    optional_nickname: str | None = None


class Settings(BaseModel):
    notifications: bool


class UserDocument(BaseModel):
    profile: Profile
    settings: Settings


@pytest.mark.asyncio
async def test_crud_default_and_missing_key_semantics() -> None:
    manager = KVManager(FakeAgentFS(), prefix="app:")

    assert await manager.exists("theme") is False
    assert await manager.get("theme", default="light") == "light"

    with pytest.raises(KeyNotFoundError, match="app:theme"):
        await manager.get("theme")

    await manager.set("theme", "dark")
    assert await manager.exists("theme") is True
    assert await manager.get("theme") == "dark"

    assert await manager.delete("theme") is True
    assert await manager.exists("theme") is False
    assert await manager.delete("theme") is False


@pytest.mark.asyncio
async def test_set_get_roundtrip_structured_payload() -> None:
    manager = KVManager(FakeAgentFS(), prefix="app:")
    payload = {"name": "Alice", "tags": ["admin"], "flags": {"active": True}}

    await manager.set("user:alice", payload)

    assert await manager.get("user:alice") == payload


@pytest.mark.asyncio
async def test_namespace_stacking_and_equivalent_construction_are_deterministic() -> None:
    agent = FakeAgentFS()
    chained = KVManager(agent).namespace("a").namespace("b")
    direct = KVManager(agent, prefix="a:b:")

    assert chained.prefix == "a:b:"
    assert direct.prefix == "a:b:"

    await chained.set("key", {"v": 1})

    assert await direct.get("key") == {"v": 1}
    assert await KVManager(agent).get("a:b:key") == {"v": 1}


@pytest.mark.asyncio
async def test_list_behavior_root_vs_prefixed_manager_key_format() -> None:
    agent = FakeAgentFS()
    root = KVManager(agent)

    await root.set("app:users:alice", {"name": "Alice"})
    await root.set("app:users:bob", {"name": "Bob"})
    await root.set("other:key", {"name": "Other"})

    # Root manager uses unqualified keys.
    assert await root.list("app:users:") == [
        {"key": "app:users:alice", "value": {"name": "Alice"}},
        {"key": "app:users:bob", "value": {"name": "Bob"}},
    ]

    prefixed = root.namespace("app")
    # Prefixed manager strips its prefix from returned keys.
    assert await prefixed.list("users:") == [
        {"key": "users:alice", "value": {"name": "Alice"}},
        {"key": "users:bob", "value": {"name": "Bob"}},
    ]


@pytest.mark.asyncio
async def test_workspace_kv_repository_typed_integration(agent_fs) -> None:
    workspace = Workspace(agent_fs)
    repo = workspace.kv.repository(prefix="users:", model_type=UserDocument)

    doc = UserDocument(
        profile=Profile(required_name="Alice", optional_nickname=None),
        settings=Settings(notifications=True),
    )
    await repo.save("alice", doc)

    loaded = await repo.load("alice")
    assert loaded is not None
    assert loaded.profile.required_name == "Alice"
    assert loaded.profile.optional_nickname is None
    assert loaded.settings.notifications is True

    # Data saved through typed repository is retrievable via simple KV with same scope.
    assert await workspace.kv.get("users:alice") == doc.model_dump(mode="json")


@pytest.mark.asyncio
async def test_set_serialization_failure_wraps_operation_key_and_cause() -> None:
    manager = KVManager(FakeAgentFS())

    with pytest.raises(SerializationError) as exc_info:
        await manager.set("bad:serialize:key", object())

    message = str(exc_info.value)
    assert "set" in message
    assert "bad:serialize:key" in message
    assert isinstance(exc_info.value.__cause__, TypeError)
    assert "not serializable" in str(exc_info.value.__cause__)


@pytest.mark.asyncio
async def test_get_malformed_payload_wraps_operation_key_and_cause() -> None:
    class DeserializationFailKVBackend(FakeKVBackend):
        async def get(self, key: str) -> Any:
            raise ValueError("bad json")

    agent = FakeAgentFS()
    agent.kv = DeserializationFailKVBackend()
    manager = KVManager(agent, prefix="app:")

    with pytest.raises(SerializationError) as exc_info:
        await manager.get("theme")

    message = str(exc_info.value)
    assert "get" in message
    assert "app:theme" in message
    assert isinstance(exc_info.value.__cause__, ValueError)
    assert "bad json" in str(exc_info.value.__cause__)




@pytest.mark.asyncio
async def test_transaction_commit_applies_staged_writes() -> None:
    manager = KVManager(FakeAgentFS(), prefix="app:")

    async with manager.transaction() as txn:
        await txn.set("one", 1)
        await txn.set("two", 2)

    assert await manager.get("one") == 1
    assert await manager.get("two") == 2


@pytest.mark.asyncio
async def test_transaction_best_effort_rollback_on_partial_failure() -> None:
    manager = KVManager(FakeAgentFS())

    with pytest.raises(KVStoreError, match="rolled back"):
        async with manager.transaction() as txn:
            await txn.set("ok:key", {"ok": True})
            await txn.set("fail:set:key", {"boom": True})

    assert await manager.exists("ok:key") is False

@pytest.mark.asyncio
async def test_wrapped_store_error_context_includes_operation_key_and_cause() -> None:
    manager = KVManager(FakeAgentFS())

    with pytest.raises(KVStoreError) as exc_info:
        await manager.set("fail:set:key", {"x": 1})

    message = str(exc_info.value)
    assert "operation=set" in message
    assert "fail:set:key" in message
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "write failed" in str(exc_info.value.__cause__)
