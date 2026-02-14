"""Tests for KVManager simple and typed bridges."""

import pytest
from pydantic import BaseModel

from fsdantic.kv import KVManager
from fsdantic.repository import TypedKVRepository


class FakeKVBackend:
    def __init__(self):
        self.data: dict[str, object] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value):
        self.data[key] = value

    async def delete(self, key: str):
        self.data.pop(key, None)

    async def list(self, prefix: str = ""):
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
    assert entries == [{"key": "app:theme", "value": "dark"}]

    await manager.delete("theme")
    assert await manager.exists("theme") is False


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
