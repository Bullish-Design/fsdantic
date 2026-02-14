"""Tests for KVManager simple and typed bridges."""

import pytest
from pydantic import BaseModel

from fsdantic.exceptions import KVStoreError, KeyNotFoundError, SerializationError
from fsdantic.kv import KVManager
from fsdantic.repository import TypedKVRepository


class FakeKVBackend:
    def __init__(self):
        self.data: dict[str, object] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value):
        if key.startswith("fail:set:"):
            raise RuntimeError("write failed")
        if key.startswith("bad:serialize:"):
            raise TypeError("not serializable")
        self.data[key] = value

    async def delete(self, key: str):
        if key.startswith("fail:delete:"):
            raise RuntimeError("delete failed")
        self.data.pop(key, None)

    async def list(self, prefix: str = ""):
        if prefix.startswith("fail:list:"):
            raise RuntimeError("list failed")
        return [
            {"key": key, "value": value}
            for key, value in self.data.items()
            if key.startswith(prefix)
        ]


class FakeAgentFS:
    def __init__(self):
        self.kv = FakeKVBackend()


class UserRecord(BaseModel):
    name: str


@pytest.mark.asyncio
async def test_simple_kv_operations_respect_prefix():
    manager = KVManager(FakeAgentFS(), prefix="app:")

    await manager.set("theme", "dark")

    assert await manager.get("theme") == "dark"
    assert await manager.exists("theme") is True

    entries = await manager.list()
    assert entries == [{"key": "theme", "value": "dark"}]

    assert await manager.delete("theme") is True
    assert await manager.exists("theme") is False


@pytest.mark.asyncio
async def test_get_missing_key_contract_default_and_error():
    manager = KVManager(FakeAgentFS(), prefix="app:")

    assert await manager.get("unknown", default="fallback") == "fallback"

    with pytest.raises(KeyNotFoundError):
        await manager.get("unknown")


@pytest.mark.asyncio
async def test_delete_missing_key_is_stable_noop_false():
    manager = KVManager(FakeAgentFS(), prefix="app:")

    assert await manager.delete("missing") is False


@pytest.mark.asyncio
async def test_set_wraps_serialization_and_store_errors():
    manager = KVManager(FakeAgentFS())

    with pytest.raises(SerializationError, match="serialization failed"):
        await manager.set("bad:serialize:key", object())

    with pytest.raises(KVStoreError, match="operation=set"):
        await manager.set("fail:set:key", {"v": 1})


@pytest.mark.asyncio
async def test_get_wraps_deserialization_failures_with_context():
    class DeserializationFailKVBackend(FakeKVBackend):
        async def get(self, key: str):
            raise ValueError("bad json")

    agent = FakeAgentFS()
    agent.kv = DeserializationFailKVBackend()
    manager = KVManager(agent, prefix="app:")

    with pytest.raises(SerializationError, match="app:theme"):
        await manager.get("theme")


@pytest.mark.asyncio
async def test_delete_wraps_store_errors_with_context():
    manager = KVManager(FakeAgentFS())
    await manager.set("fail:delete:key", {"v": 1})

    with pytest.raises(KVStoreError, match="operation=delete"):
        await manager.delete("fail:delete:key")


@pytest.mark.asyncio
async def test_repository_bridge_uses_manager_error_semantics():
    manager = KVManager(FakeAgentFS())
    repo = manager.repository()

    with pytest.raises(KVStoreError, match="operation=set"):
        await repo.save("fail:set:key", UserRecord(name="broken"))


@pytest.mark.asyncio
async def test_namespace_and_repository_bridge():
    manager = KVManager(FakeAgentFS(), prefix="app:")

    users = manager.namespace("user:")
    repo = users.repository()

    assert isinstance(repo, TypedKVRepository)
    assert users.prefix == "app:user:"

    await repo.save("alice", UserRecord(name="Alice"))

    loaded = await repo.load("alice", UserRecord)
    assert loaded is not None
    assert loaded.name == "Alice"

    assert await users.get("alice") == {"name": "Alice"}


@pytest.mark.asyncio
async def test_repository_accepts_additional_prefix():
    manager = KVManager(FakeAgentFS(), prefix="base:")
    repo = manager.repository("cfg:")

    await repo.save("theme", UserRecord(name="dark"))
    assert await manager.get("cfg:theme") == {"name": "dark"}


@pytest.mark.asyncio
async def test_namespace_prefix_composition_is_canonical_and_stackable():
    manager = KVManager(FakeAgentFS(), prefix=":base::")
    nested = manager.namespace("::users").namespace("prefs::")

    assert manager.prefix == "base:"
    assert nested.prefix == "base:users:prefs:"


@pytest.mark.asyncio
async def test_list_contract_uses_relative_input_and_output_keys():
    manager = KVManager(FakeAgentFS(), prefix="app")

    await manager.set("users:alice", {"name": "Alice"})
    await manager.set("users:bob", {"name": "Bob"})

    entries = await manager.list("users:")
    assert entries == [
        {"key": "users:alice", "value": {"name": "Alice"}},
        {"key": "users:bob", "value": {"name": "Bob"}},
    ]


@pytest.mark.asyncio
async def test_repository_prefix_uses_same_composition_rules_as_manager():
    manager = KVManager(FakeAgentFS(), prefix="base")
    nested = manager.namespace("users:")

    repo = nested.repository("prefs")
    await repo.save("alice", UserRecord(name="Alice"))

    assert await manager.get("users:prefs:alice") == {"name": "Alice"}
