"""Generic repository pattern for AgentFS KV operations."""

from typing import Any, Callable, Generic, Optional, Type, TypeVar

from agentfs_sdk import AgentFS
from pydantic import BaseModel, ValidationError

from .exceptions import KVConflictError
from .kv import KVManager
from .models import BatchItemResult, BatchResult, VersionedKVRecord

_MISSING = object()

T = TypeVar("T", bound=BaseModel)


class TypedKVRepository(Generic[T]):
    """Generic typed KV operations for Pydantic models.

    Provides a type-safe repository pattern for storing and retrieving
    Pydantic models in the AgentFS key-value store.

    Examples:
        >>> from pydantic import BaseModel, ValidationError
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
        model_type: Optional[Type[T]] = None,
        key_builder: Optional[Callable[[str], str]] = None,
    ):
        """Initialize repository.

        Args:
            storage: AgentFS instance
            prefix: Key prefix for namespacing (e.g., "user:", "agent:")
            model_type: Optional default Pydantic model class used by
                `load`, `list_all`, and `load_many` when not provided
            key_builder: Optional function to build keys from IDs
        """
        self.storage = storage
        self.prefix = prefix
        self.model_type = model_type
        self.key_builder = key_builder or (lambda id: f"{prefix}{id}")
        self._manager = KVManager(storage)

    def _resolve_model_type(self, model_type: Optional[Type[T]]) -> Type[T]:
        resolved = model_type or self.model_type
        if resolved is None:
            raise ValueError(
                "model_type is required. Provide it to the method call or set "
                "a default when constructing TypedKVRepository."
            )
        return resolved

    @staticmethod
    def _coerce_expected_version(
        *,
        expected_version: int | None,
        etag: int | str | None,
    ) -> int | None:
        if expected_version is not None and etag is not None:
            raise ValueError("Provide either expected_version or etag, not both")
        if etag is None:
            return expected_version
        if isinstance(etag, int):
            return etag
        if isinstance(etag, str) and etag.isdigit():
            return int(etag)
        raise ValueError("etag must be an int or numeric string")

    @staticmethod
    def _extract_version(payload: Any) -> int | None:
        if isinstance(payload, dict):
            value = payload.get("version")
            if isinstance(value, int):
                return value
        return None

    async def save(
        self,
        id: str,
        record: T,
        *,
        expected_version: int | None = None,
        etag: int | str | None = None,
    ) -> None:
        """Save a record to KV store.

        For ``VersionedKVRecord`` values, this method applies optimistic
        concurrency checks and version increments:
        - New records are created at version ``1``.
        - Existing records require matching version/etag (or the record's own
          version when no explicit expected version is provided).
        - On success, the stored and in-memory record version is incremented.

        Args:
            id: Record identifier
            record: Pydantic model instance to save
            expected_version: Optional optimistic concurrency expected version
            etag: Optional alias for expected_version
        """
        key = self.key_builder(id)
        resolved_expected = self._coerce_expected_version(expected_version=expected_version, etag=etag)

        if isinstance(record, VersionedKVRecord):
            current = await self._manager.get(key, default=None)
            actual_version = self._extract_version(current)

            if current is None:
                effective_expected = resolved_expected
                if effective_expected is not None:
                    raise KVConflictError(key=key, expected_version=effective_expected, actual_version=None)
                if record.version != 1:
                    raise KVConflictError(key=key, expected_version=record.version, actual_version=None)
                await self._manager.set(key, record.model_dump())
                return

            effective_expected = resolved_expected if resolved_expected is not None else record.version
            if actual_version != effective_expected:
                raise KVConflictError(
                    key=key,
                    expected_version=effective_expected,
                    actual_version=actual_version,
                )

            updated_record = record.model_copy(deep=True)
            updated_record.version = actual_version
            updated_record.increment_version()
            await self._manager.set(key, updated_record.model_dump())

            # Keep caller instance in sync after successful commit.
            record.version = updated_record.version
            record.updated_at = updated_record.updated_at
            return

        if resolved_expected is not None:
            current = await self._manager.get(key, default=None)
            actual_version = self._extract_version(current)
            if actual_version != resolved_expected:
                raise KVConflictError(
                    key=key,
                    expected_version=resolved_expected,
                    actual_version=actual_version,
                )

        # AgentFS KV store accepts dicts, not JSON strings
        await self._manager.set(key, record.model_dump())

    async def save_if_version(self, id: str, record: T, expected_version: int) -> None:
        """Save only when current version matches ``expected_version``."""
        await self.save(id, record, expected_version=expected_version)

    async def compare_and_set(
        self,
        id: str,
        record: T,
        *,
        expected_version: int | None = None,
        etag: int | str | None = None,
    ) -> None:
        """Alias for save with explicit optimistic concurrency semantics."""
        await self.save(id, record, expected_version=expected_version, etag=etag)

    async def load(self, id: str, model_type: Optional[Type[T]] = None) -> Optional[T]:
        """Load a record from KV store.

        Args:
            id: Record identifier
            model_type: Optional Pydantic model class. If omitted, uses the
                repository default `model_type` configured at construction.

        Returns:
            Model instance or None if not found

        Examples:
            >>> user = await repo.load("user1", UserRecord)
            >>> if user:
            ...     print(user.name)
        """
        key = self.key_builder(id)
        data = await self._manager.get(key, default=None)
        if data is None:
            return None
        # AgentFS KV store returns dict, not JSON string
        return self._resolve_model_type(model_type).model_validate(data)

    async def delete(self, id: str) -> None:
        """Delete a record from KV store.

        Args:
            id: Record identifier

        Examples:
            >>> await repo.delete("user1")
        """
        key = self.key_builder(id)
        await self._manager.delete(key)

    async def list_all(self, model_type: Optional[Type[T]] = None) -> list[T]:
        """List all records with the configured prefix.

        Args:
            model_type: Optional Pydantic model class. If omitted, uses the
                repository default `model_type` configured at construction.

        Returns:
            List of all matching records

        Examples:
            >>> all_users = await repo.list_all(UserRecord)
            >>> for user in all_users:
            ...     print(user.name)
        """
        # AgentFS KV store list() returns list of dicts with 'key' and 'value'
        items = await self._manager.list(self.prefix)
        records: list[T] = []
        resolved_model_type = self._resolve_model_type(model_type)

        for item in items:
            try:
                records.append(resolved_model_type.model_validate(item["value"]))
            except ValidationError:
                continue

        return records

    async def exists(self, id: str) -> bool:
        """Check if a record exists."""
        key = self.key_builder(id)
        return await self._manager.exists(key)

    async def list_ids(self) -> list[str]:
        """List all IDs with the configured prefix."""
        items = await self._manager.list(self.prefix)
        ids = []

        for item in items:
            key = item["key"]
            if key.startswith(self.prefix):
                ids.append(key[len(self.prefix) :])

        return ids

    async def save_many(
        self,
        records: list[tuple[str, T]],
        *,
        concurrency_limit: int = 10,
    ) -> BatchResult:
        """Save many records with bounded concurrency and per-item outcomes."""
        payload = [(self.key_builder(record_id), record.model_dump()) for record_id, record in records]
        return await self._manager.set_many(payload, concurrency_limit=concurrency_limit)

    async def delete_many(
        self,
        ids: list[str],
        *,
        concurrency_limit: int = 10,
    ) -> BatchResult:
        """Delete many records with bounded concurrency and per-item outcomes."""
        keys = [self.key_builder(record_id) for record_id in ids]
        return await self._manager.delete_many(keys, concurrency_limit=concurrency_limit)

    async def load_many(
        self,
        ids: list[str],
        model_type: Optional[Type[T]] = None,
        *,
        default: Any = _MISSING,
    ) -> BatchResult:
        """Load many records with deterministic ordering and per-item outcomes."""
        resolved_model_type = self._resolve_model_type(model_type)
        keys = [self.key_builder(record_id) for record_id in ids]
        raw_result = await self._manager.get_many(keys, default=default)

        items: list[BatchItemResult] = []
        for index, item in enumerate(raw_result.items):
            if not item.ok:
                items.append(
                    BatchItemResult(
                        index=index,
                        key_or_path=ids[index],
                        ok=False,
                        error=item.error,
                    )
                )
                continue

            value = item.value
            if value is None:
                items.append(BatchItemResult(index=index, key_or_path=ids[index], ok=True, value=None))
                continue

            try:
                model = resolved_model_type.model_validate(value)
                items.append(BatchItemResult(index=index, key_or_path=ids[index], ok=True, value=model))
            except ValidationError as exc:
                items.append(
                    BatchItemResult(
                        index=index,
                        key_or_path=ids[index],
                        ok=False,
                        error=str(exc),
                    )
                )

        return BatchResult(items=items)

    async def save_batch(self, records: list[tuple[str, T]]) -> None:
        """Compatibility wrapper for :meth:`save_many`."""
        await self.save_many(records)

    async def delete_batch(self, ids: list[str]) -> None:
        """Compatibility wrapper for :meth:`delete_many`."""
        await self.delete_many(ids)

    async def load_batch(
        self,
        ids: list[str],
        model_type: Optional[Type[T]] = None,
    ) -> dict[str, Optional[T]]:
        """Compatibility wrapper for :meth:`load_many`."""
        batch = await self.load_many(ids, model_type=model_type, default=None)
        results: dict[str, Optional[T]] = {}
        for record_id, item in zip(ids, batch.items):
            results[record_id] = item.value if item.ok else None
        return results


class NamespacedKVStore:
    """Convenience wrapper for creating namespaced repositories."""

    def __init__(self, storage: AgentFS):
        self.storage = storage

    def namespace(self, prefix: str) -> TypedKVRepository:
        """Create a namespaced repository."""
        return TypedKVRepository(self.storage, prefix=prefix)
