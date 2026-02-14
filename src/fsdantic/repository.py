"""Generic repository pattern for AgentFS KV operations."""

from typing import Callable, Generic, Optional, Type, TypeVar

from agentfs_sdk import AgentFS
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class TypedKVRepository(Generic[T]):
    """Generic typed KV operations for Pydantic models.

    Provides a type-safe repository pattern for storing and retrieving
    Pydantic models in the AgentFS key-value store.

    Examples:
        >>> from pydantic import BaseModel
        >>> class UserRecord(BaseModel):
        ...     name: str
        ...     age: int
        >>>
        >>> repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")
        >>> await repo.save("alice", UserRecord(name="Alice", age=30))
        >>> user = await repo.load("alice", UserRecord)
        >>> print(user.name)  # "Alice"
    """

    def __init__(
        self,
        storage: AgentFS,
        prefix: str = "",
        key_builder: Optional[Callable[[str], str]] = None,
    ):
        """Initialize repository.

        Args:
            storage: AgentFS instance
            prefix: Key prefix for namespacing (e.g., "user:", "agent:")
            key_builder: Optional function to build keys from IDs
        """
        self.storage = storage
        self.prefix = prefix
        self.key_builder = key_builder or (lambda id: f"{prefix}{id}")

    async def save(self, id: str, record: T) -> None:
        """Save a record to KV store.

        Args:
            id: Record identifier
            record: Pydantic model instance to save

        Examples:
            >>> await repo.save("user1", UserRecord(name="Bob", age=25))
        """
        key = self.key_builder(id)
        # AgentFS KV store accepts dicts, not JSON strings
        await self.storage.kv.set(key, record.model_dump())

    async def load(self, id: str, model_type: Type[T]) -> Optional[T]:
        """Load a record from KV store.

        Args:
            id: Record identifier
            model_type: Pydantic model class

        Returns:
            Model instance or None if not found

        Examples:
            >>> user = await repo.load("user1", UserRecord)
            >>> if user:
            ...     print(user.name)
        """
        key = self.key_builder(id)
        data = await self.storage.kv.get(key)
        if data is None:
            return None
        # AgentFS KV store returns dict, not JSON string
        return model_type.model_validate(data)

    async def delete(self, id: str) -> None:
        """Delete a record from KV store.

        Args:
            id: Record identifier

        Examples:
            >>> await repo.delete("user1")
        """
        key = self.key_builder(id)
        await self.storage.kv.delete(key)

    async def list_all(self, model_type: Type[T]) -> list[T]:
        """List all records with the configured prefix.

        Args:
            model_type: Pydantic model class

        Returns:
            List of all matching records

        Examples:
            >>> all_users = await repo.list_all(UserRecord)
            >>> for user in all_users:
            ...     print(user.name)
        """
        # AgentFS KV store list() returns list of dicts with 'key' and 'value'
        items = await self.storage.kv.list(self.prefix)
        records: list[T] = []

        for item in items:
            try:
                # item is a dict with 'key' and 'value' fields
                # value is already deserialized from JSON
                records.append(model_type.model_validate(item["value"]))
            except Exception:
                # Skip invalid records
                continue

        return records

    async def exists(self, id: str) -> bool:
        """Check if a record exists.

        Args:
            id: Record identifier

        Returns:
            True if record exists

        Examples:
            >>> if await repo.exists("user1"):
            ...     print("User exists")
        """
        key = self.key_builder(id)
        data = await self.storage.kv.get(key)
        return data is not None

    async def list_ids(self) -> list[str]:
        """List all IDs with the configured prefix.

        Returns:
            List of record IDs (with prefix removed)

        Examples:
            >>> ids = await repo.list_ids()
            >>> print(f"Found {len(ids)} records")
        """
        items = await self.storage.kv.list(self.prefix)
        ids = []

        for item in items:
            key = item["key"]
            if key.startswith(self.prefix):
                ids.append(key[len(self.prefix) :])

        return ids

    async def save_batch(self, records: list[tuple[str, T]]) -> None:
        """Save multiple records in batch.

        Args:
            records: List of (id, record) tuples to save

        Examples:
            >>> await repo.save_batch([
            ...     ("user1", UserRecord(name="Alice", age=30)),
            ...     ("user2", UserRecord(name="Bob", age=25))
            ... ])
        """
        for record_id, record in records:
            await self.save(record_id, record)

    async def delete_batch(self, ids: list[str]) -> None:
        """Delete multiple records in batch.

        Args:
            ids: List of record IDs to delete

        Examples:
            >>> await repo.delete_batch(["user1", "user2", "user3"])
        """
        for record_id in ids:
            await self.delete(record_id)

    async def load_batch(self, ids: list[str], model_type: Type[T]) -> dict[str, Optional[T]]:
        """Load multiple records in batch.

        Args:
            ids: List of record IDs to load
            model_type: Pydantic model class

        Returns:
            Dictionary mapping IDs to records (None if not found)

        Examples:
            >>> records = await repo.load_batch(["user1", "user2"], UserRecord)
            >>> for id, record in records.items():
            ...     if record:
            ...         print(f"{id}: {record.name}")
        """
        results = {}
        for record_id in ids:
            results[record_id] = await self.load(record_id, model_type)
        return results


class NamespacedKVStore:
    """Convenience wrapper for creating namespaced repositories.

    Simplifies the creation of multiple repositories with different
    prefixes from a single AgentFS instance.

    Examples:
        >>> kv = NamespacedKVStore(agent_fs)
        >>> users = kv.namespace("user:")
        >>> await users.save("alice", UserRecord(...))
        >>>
        >>> agents = kv.namespace("agent:")
        >>> await agents.save("agent1", AgentRecord(...))
    """

    def __init__(self, storage: AgentFS):
        """Initialize namespaced KV store.

        Args:
            storage: AgentFS instance
        """
        self.storage = storage

    def namespace(self, prefix: str) -> TypedKVRepository:
        """Create a namespaced repository.

        Args:
            prefix: Namespace prefix

        Returns:
            TypedKVRepository instance

        Examples:
            >>> users_repo = kv.namespace("user:")
            >>> await users_repo.save("alice", user)
        """
        return TypedKVRepository(self.storage, prefix=prefix)
