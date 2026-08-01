"""Generic repository pattern for AgentFS KV operations."""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from agentfs_sdk import AgentFS
from pydantic import BaseModel, ValidationError

from ._internal.kv_cas import _sdk_serialize, cas_insert, cas_update
from .exceptions import FsdanticError, KVConflictError
from .kv import KVManager, _kv_denormalize, _kv_normalize
from .models import BatchItemResult, BatchResult, VersionedKVRecord

logger = logging.getLogger(__name__)

_MISSING = object()


class TypedKVRepository[T: BaseModel]:
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
        model_type: type[T] | None = None,
        key_builder: Callable[[str], str] | None = None,
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

    def _resolve_model_type(self, model_type: type[T] | None) -> type[T]:
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
            # Serialize with a JSON-native normalization pass so datetimes,
            # enums, bytes, and Path values become storable JSON values that
            # CAS payloads can compare deterministically.
            return await self._save_versioned(
                key=key,
                record=record,
                resolved_expected=resolved_expected,
            )

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

    async def _save_versioned(
        self,
        *,
        key: str,
        record: T,
        resolved_expected: int | None,
    ) -> None:
        """Versioned save with atomic compare-and-set semantics (C1, M1).

        The read-modify-write section is serialized intra-process with a
        per-key asyncio.Lock (defense in depth) and the payload-equality CAS
        is the authoritative guard against concurrent writers (including
        other processes/connections).
        """
        lock = await self._manager._key_lock(key)
        async with lock:
            conn = self.storage.get_database()
            current_raw = await self._manager.get_raw(key)

            if current_raw is None:
                # Create-only path: atomic INSERT guard.
                effective_expected = resolved_expected
                if effective_expected is not None:
                    raise KVConflictError(
                        key=key,
                        expected_version=effective_expected,
                        actual_version=None,
                    )
                if record.version != 1:
                    raise KVConflictError(
                        key=key,
                        expected_version=record.version,
                        actual_version=None,
                    )
                ok = await cas_insert(conn, key, _sdk_serialize(_kv_normalize(record.model_dump())))
                if not ok:
                    # Another writer created the key between our read and insert.
                    raise KVConflictError(
                        key=key,
                        expected_version=None,
                        actual_version=None,
                    )
                return

            # Existing key: compute the next payload with preserved created_at.
            current = json.loads(current_raw)
            actual_version = self._extract_version(current)
            effective_expected = resolved_expected if resolved_expected is not None else record.version
            if actual_version != effective_expected:
                raise KVConflictError(
                    key=key,
                    expected_version=effective_expected,
                    actual_version=actual_version,
                )

            updated_record = record.model_copy(deep=True)
            stored_created_at = current.get("created_at") if isinstance(current, dict) else None
            if stored_created_at is not None:
                updated_record.created_at = stored_created_at
            else:
                logger.debug(
                    "Legacy payload without created_at for key=%s; falling back to caller value",
                    key,
                )
            updated_record.version = actual_version
            updated_record.increment_version()

            ok = await cas_update(
                conn,
                key,
                current_raw,
                _sdk_serialize(_kv_normalize(updated_record.model_dump())),
            )
            if not ok:
                # The payload changed between read and write. Re-read to
                # report the actual (fresh) version.
                fresh = json.loads(await self._manager.get_raw(key))
                raise KVConflictError(
                    key=key,
                    expected_version=effective_expected,
                    actual_version=self._extract_version(fresh),
                )

            # Keep caller instance in sync after successful commit.
            record.version = updated_record.version
            record.updated_at = updated_record.updated_at
            record.created_at = updated_record.created_at
            return

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

    async def load(self, id: str, model_type: type[T] | None = None) -> T | None:
        """Load a record from KV store.

        Args:
            id: Record identifier
            model_type: Optional Pydantic model class. If omitted, uses the
                repository default `model_type` configured at construction.

        Returns:
            Model instance or None if not found

        Note: a stored JSON ``null`` value under ``id`` returns ``None`` and
        is indistinguishable from a missing key.  Use :meth:`exists` to
        disambiguate.

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
        return self._resolve_model_type(model_type).model_validate(_kv_denormalize(data))

    async def delete(self, id: str) -> None:
        """Delete a record from KV store.

        Args:
            id: Record identifier

        Examples:
            >>> await repo.delete("user1")
        """
        key = self.key_builder(id)
        await self._manager.delete(key)

    async def list_all(self, model_type: type[T] | None = None) -> list[T]:
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
                records.append(resolved_model_type.model_validate(_kv_denormalize(item["value"])))
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
        """Save many records with bounded concurrency and per-item outcomes.

        Every item routes through :meth:`save`, so versioned records inherit
        the full optimistic-concurrency semantics: version checks, CAS
        conflict detection, and ``created_at`` preservation.  Batch saves of
        brand-new records remain conflict-free; batch *updates* require
        callers to pass loaded records (version already set) or accept that a
        fresh record at version 1 conflicts when the stored version is > 1.
        """
        if concurrency_limit <= 0:
            raise ValueError("concurrency_limit must be greater than 0")
        if not records:
            return BatchResult()

        semaphore = asyncio.Semaphore(concurrency_limit)

        async def _save_one(index: int, record_id: str, record: T) -> BatchItemResult:
            async with semaphore:
                try:
                    await self.save(record_id, record)
                    return BatchItemResult(index=index, key_or_path=record_id, ok=True, value=True)
                except (FsdanticError, TypeError, ValueError) as exc:
                    return BatchItemResult(index=index, key_or_path=record_id, ok=False, error=str(exc))

        gathered = await asyncio.gather(
            *(_save_one(index, record_id, record) for index, (record_id, record) in enumerate(records)),
            return_exceptions=True,
        )

        items: list[BatchItemResult] = []
        for index, raw_result in enumerate(gathered):
            if isinstance(raw_result, BatchItemResult):
                items.append(raw_result)
            else:
                items.append(
                    BatchItemResult(
                        index=index,
                        key_or_path=records[index][0],
                        ok=False,
                        error=str(raw_result),
                    )
                )
        return BatchResult(items=items)

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
        model_type: type[T] | None = None,
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
                model = resolved_model_type.model_validate(_kv_denormalize(value))
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
        model_type: type[T] | None = None,
    ) -> dict[str, T | None]:
        """Compatibility wrapper for :meth:`load_many`."""
        batch = await self.load_many(ids, model_type=model_type, default=None)
        results: dict[str, T | None] = {}
        for record_id, item in zip(ids, batch.items, strict=True):
            results[record_id] = item.value if item.ok else None
        return results


class NamespacedKVStore:
    """Convenience wrapper for creating namespaced repositories."""

    def __init__(self, storage: AgentFS):
        self.storage = storage

    def namespace(self, prefix: str) -> TypedKVRepository:
        """Create a namespaced repository."""
        return TypedKVRepository(self.storage, prefix=prefix)
