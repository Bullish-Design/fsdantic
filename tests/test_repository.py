"""Tests for TypedKVRepository and NamespacedKVStore."""

import pytest
from pydantic import BaseModel

from fsdantic import KVRecord, TypedKVRepository, VersionedKVRecord, NamespacedKVStore


class UserRecord(BaseModel):
    """Test model for repository."""

    name: str
    email: str
    age: int


class ConfigRecord(KVRecord):
    """Test model with timestamps."""

    theme: str
    notifications: bool


class VersionedConfigRecord(VersionedKVRecord):
    """Test model with versioning."""

    settings: dict


@pytest.mark.asyncio
class TestTypedKVRepository:
    """Test TypedKVRepository functionality."""

    async def test_save_and_load(self, agent_fs):
        """Should save and load records correctly."""
        repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")

        user = UserRecord(name="Alice", email="alice@example.com", age=30)
        await repo.save("alice", user)

        loaded = await repo.load("alice", UserRecord)
        assert loaded is not None
        assert loaded.name == "Alice"
        assert loaded.email == "alice@example.com"
        assert loaded.age == 30

    async def test_load_nonexistent_returns_none(self, agent_fs):
        """Loading nonexistent record should return None."""
        repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")

        loaded = await repo.load("nonexistent", UserRecord)
        assert loaded is None

    async def test_delete_record(self, agent_fs):
        """Should delete records."""
        repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")

        user = UserRecord(name="Bob", email="bob@example.com", age=25)
        await repo.save("bob", user)

        # Verify it exists
        loaded = await repo.load("bob", UserRecord)
        assert loaded is not None

        # Delete it
        await repo.delete("bob")

        # Verify it's gone
        loaded = await repo.load("bob", UserRecord)
        assert loaded is None

    async def test_exists(self, agent_fs):
        """Should check record existence."""
        repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")

        # Initially doesn't exist
        assert await repo.exists("charlie") is False

        # Create it
        user = UserRecord(name="Charlie", email="charlie@example.com", age=35)
        await repo.save("charlie", user)

        # Now it exists
        assert await repo.exists("charlie") is True

        # Delete it
        await repo.delete("charlie")

        # Gone again
        assert await repo.exists("charlie") is False

    async def test_list_all(self, agent_fs):
        """Should list all records with prefix."""
        repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")

        # Create multiple users
        users = [
            ("alice", UserRecord(name="Alice", email="alice@example.com", age=30)),
            ("bob", UserRecord(name="Bob", email="bob@example.com", age=25)),
            ("charlie", UserRecord(name="Charlie", email="charlie@example.com", age=35)),
        ]

        for user_id, user in users:
            await repo.save(user_id, user)

        # List all
        all_users = await repo.list_all(UserRecord)

        assert len(all_users) == 3
        names = {u.name for u in all_users}
        assert names == {"Alice", "Bob", "Charlie"}

    async def test_list_ids(self, agent_fs):
        """Should list all record IDs."""
        repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")

        # Create records
        await repo.save("user1", UserRecord(name="Alice", email="alice@example.com", age=30))
        await repo.save("user2", UserRecord(name="Bob", email="bob@example.com", age=25))
        await repo.save("user3", UserRecord(name="Charlie", email="charlie@example.com", age=35))

        # List IDs
        ids = await repo.list_ids()

        assert len(ids) == 3
        assert set(ids) == {"user1", "user2", "user3"}

    async def test_prefix_isolation(self, agent_fs):
        """Records with different prefixes should be isolated."""
        users_repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")
        configs_repo = TypedKVRepository[ConfigRecord](agent_fs, prefix="config:")

        # Create records in each
        await users_repo.save("alice", UserRecord(name="Alice", email="alice@example.com", age=30))
        await configs_repo.save("app", ConfigRecord(theme="dark", notifications=True))

        # Each repo should only see its own records
        assert len(await users_repo.list_ids()) == 1
        assert len(await configs_repo.list_ids()) == 1

        assert await users_repo.exists("alice") is True
        assert await users_repo.exists("app") is False

        assert await configs_repo.exists("app") is True
        assert await configs_repo.exists("alice") is False

    async def test_custom_key_builder(self, agent_fs):
        """Should support custom key building."""

        def custom_key_builder(id: str) -> str:
            return f"custom:prefix:{id}:suffix"

        repo = TypedKVRepository[UserRecord](agent_fs, key_builder=custom_key_builder)

        user = UserRecord(name="Alice", email="alice@example.com", age=30)
        await repo.save("alice", user)

        # Verify custom key was used
        raw_value = await agent_fs.kv.get("custom:prefix:alice:suffix")
        assert raw_value is not None

    async def test_update_record(self, agent_fs):
        """Should update existing records."""
        repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")

        # Create initial
        user = UserRecord(name="Alice", email="alice@example.com", age=30)
        await repo.save("alice", user)

        # Update
        updated_user = UserRecord(name="Alice Smith", email="alice.smith@example.com", age=31)
        await repo.save("alice", updated_user)

        # Load and verify
        loaded = await repo.load("alice", UserRecord)
        assert loaded.name == "Alice Smith"
        assert loaded.email == "alice.smith@example.com"
        assert loaded.age == 31

    async def test_with_kvrecord(self, agent_fs):
        """Should work with KVRecord base class."""
        repo = TypedKVRepository[ConfigRecord](agent_fs, prefix="config:")

        config = ConfigRecord(theme="dark", notifications=True)
        await repo.save("app", config)

        loaded = await repo.load("app", ConfigRecord)
        assert loaded is not None
        assert loaded.theme == "dark"
        assert loaded.notifications is True
        assert hasattr(loaded, "created_at")
        assert hasattr(loaded, "updated_at")

    async def test_with_versioned_kvrecord(self, agent_fs):
        """Should work with VersionedKVRecord."""
        repo = TypedKVRepository[VersionedConfigRecord](agent_fs, prefix="vconfig:")

        config = VersionedConfigRecord(settings={"key": "value"})
        await repo.save("app", config)

        loaded = await repo.load("app", VersionedConfigRecord)
        assert loaded is not None
        assert loaded.settings == {"key": "value"}
        assert loaded.version == 1
        assert hasattr(loaded, "created_at")

    async def test_list_all_skips_invalid_records(self, agent_fs):
        """list_all should skip records that fail validation."""
        repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")

        # Save valid record
        await repo.save("alice", UserRecord(name="Alice", email="alice@example.com", age=30))

        # Manually save invalid data
        await agent_fs.kv.set("user:invalid", {"name": "Bob", "email": "invalid"})  # Missing age

        # list_all should skip the invalid record
        all_users = await repo.list_all(UserRecord)
        assert len(all_users) == 1
        assert all_users[0].name == "Alice"

    async def test_empty_prefix(self, agent_fs):
        """Should work with empty prefix."""
        repo = TypedKVRepository[UserRecord](agent_fs, prefix="")

        user = UserRecord(name="Alice", email="alice@example.com", age=30)
        await repo.save("alice", user)

        loaded = await repo.load("alice", UserRecord)
        assert loaded is not None
        assert loaded.name == "Alice"


@pytest.mark.asyncio
class TestNamespacedKVStore:
    """Test NamespacedKVStore functionality."""

    async def test_namespace_creation(self, agent_fs):
        """Should create namespaced repositories."""
        kv = NamespacedKVStore(agent_fs)

        users_repo = kv.namespace("user:")
        configs_repo = kv.namespace("config:")

        assert users_repo.prefix == "user:"
        assert configs_repo.prefix == "config:"

    async def test_namespaced_repos_are_isolated(self, agent_fs):
        """Namespaced repos should be isolated from each other."""
        kv = NamespacedKVStore(agent_fs)

        users = kv.namespace("user:")
        admins = kv.namespace("admin:")

        # Save to each
        await users.save("alice", UserRecord(name="Alice", email="alice@example.com", age=30))
        await admins.save("alice", UserRecord(name="Admin Alice", email="admin@example.com", age=40))

        # Load from each
        user = await users.load("alice", UserRecord)
        admin = await admins.load("alice", UserRecord)

        assert user.name == "Alice"
        assert admin.name == "Admin Alice"

    async def test_multiple_namespaces(self, agent_fs):
        """Should handle multiple namespaces correctly."""
        kv = NamespacedKVStore(agent_fs)

        users = kv.namespace("user:")
        configs = kv.namespace("config:")
        sessions = kv.namespace("session:")

        # Create records in each namespace
        await users.save("u1", UserRecord(name="User1", email="u1@example.com", age=20))
        await configs.save("c1", ConfigRecord(theme="light", notifications=False))
        await sessions.save("s1", UserRecord(name="Session", email="s@example.com", age=25))

        # Verify isolation
        assert len(await users.list_ids()) == 1
        assert len(await configs.list_ids()) == 1
        assert len(await sessions.list_ids()) == 1


@pytest.mark.asyncio
class TestRepositoryEdgeCases:
    """Test edge cases and error conditions."""

    async def test_save_with_special_characters_in_id(self, agent_fs):
        """Should handle special characters in IDs."""
        repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")

        # IDs with special characters
        special_ids = ["user-123", "user_456", "user.789", "user@domain"]

        for user_id in special_ids:
            user = UserRecord(name="Test", email="test@example.com", age=30)
            await repo.save(user_id, user)

            loaded = await repo.load(user_id, UserRecord)
            assert loaded is not None
            assert loaded.name == "Test"

    async def test_large_number_of_records(self, agent_fs):
        """Should handle many records efficiently."""
        repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")

        # Create 100 records
        for i in range(100):
            user = UserRecord(name=f"User{i}", email=f"user{i}@example.com", age=20 + (i % 50))
            await repo.save(f"user{i}", user)

        # List all
        all_users = await repo.list_all(UserRecord)
        assert len(all_users) == 100

        # List IDs
        all_ids = await repo.list_ids()
        assert len(all_ids) == 100

    async def test_complex_nested_models(self, agent_fs):
        """Should handle complex nested Pydantic models."""

        class Address(BaseModel):
            street: str
            city: str
            country: str

        class ComplexUser(BaseModel):
            name: str
            addresses: list[Address]
            metadata: dict

        repo = TypedKVRepository[ComplexUser](agent_fs, prefix="complex:")

        user = ComplexUser(
            name="Alice",
            addresses=[
                Address(street="123 Main St", city="NYC", country="USA"),
                Address(street="456 Oak Ave", city="SF", country="USA"),
            ],
            metadata={"role": "admin", "level": 5},
        )

        await repo.save("alice", user)

        loaded = await repo.load("alice", ComplexUser)
        assert loaded is not None
        assert loaded.name == "Alice"
        assert len(loaded.addresses) == 2
        assert loaded.addresses[0].city == "NYC"
        assert loaded.metadata["role"] == "admin"
