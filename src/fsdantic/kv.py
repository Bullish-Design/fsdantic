"""Simple key-value manager with optional typed repository helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from agentfs_sdk import AgentFS, ErrnoException

from .exceptions import FsdanticError, KVStoreError, KeyNotFoundError, SerializationError
from .models import BatchItemResult, BatchResult

if TYPE_CHECKING:
    from .repository import TypedKVRepository


_MISSING = object()


@dataclass(slots=True)
class _StagedOperation:
    op: str
    key: str
    value: Any = None


class KVTransaction:
    """Best-effort transaction for grouped KV operations.

    Operations are staged in memory and applied at commit time.

    Atomicity/rollback semantics:
    - If the backend supports real transactions natively, callers should prefer
      those primitives directly.
    - This abstraction performs **best-effort rollback** only: if commit fails
      midway, fsdantic attempts to undo already-applied operations in reverse
      order.
    - Rollback itself can fail due to backend errors; in that case a
      ``KVStoreError`` is raised describing that both commit and rollback had
      errors and manual reconciliation may be required.
    """

    def __init__(self, manager: "KVManager") -> None:
        self._manager = manager
        self._staged: dict[str, _StagedOperation] = {}
        self._committed = False

    async def __aenter__(self) -> "KVTransaction":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc is not None:
            self._staged.clear()
            return False
        await self.commit()
        return False

    def _stage(self, op: str, key: str, value: Any = None) -> None:
        self._staged[key] = _StagedOperation(op=op, key=key, value=value)

    async def set(self, key: str, value: Any) -> None:
        """Stage a set operation."""
        self._stage("set", key, value)

    async def delete(self, key: str) -> None:
        """Stage a delete operation."""
        self._stage("delete", key)

    async def get(self, key: str, default: Any = _MISSING) -> Any:
        """Read through staged state, falling back to the underlying KV manager."""
        staged = self._staged.get(key)
        if staged is not None:
            if staged.op == "delete":
                if default is not _MISSING:
                    return default
                raise KeyNotFoundError(self._manager._qualify_key(key))
            return staged.value
        return await self._manager.get(key, default=default)

    async def commit(self) -> None:
        """Apply staged operations and best-effort rollback on failure."""
        if self._committed:
            return

        tx_missing = object()
        applied: list[tuple[_StagedOperation, bool, Any]] = []

        try:
            for staged in self._staged.values():
                old_value = await self._manager.get(staged.key, default=tx_missing)
                existed = old_value is not tx_missing

                if staged.op == "set":
                    await self._manager.set(staged.key, staged.value)
                else:
                    await self._manager.delete(staged.key)

                applied.append((staged, existed, old_value))
        except (FsdanticError, ErrnoException, TypeError, ValueError) as exc:
            rollback_errors: list[str] = []
            for staged, existed, old_value in reversed(applied):
                try:
                    if existed:
                        await self._manager.set(staged.key, old_value)
                    else:
                        await self._manager.delete(staged.key)
                except (
                    FsdanticError,
                    ErrnoException,
                    RuntimeError,
                    TypeError,
                    ValueError,
                ) as rollback_exc:  # pragma: no cover - defensive
                    rollback_errors.append(
                        f"key={staged.key}: {rollback_exc}"
                    )

            if rollback_errors:
                raise KVStoreError(
                    "KV transaction commit failed and rollback was partial; "
                    "manual reconciliation may be required"
                ) from exc

            raise KVStoreError("KV transaction commit failed; applied changes were rolled back") from exc

        self._committed = True
        self._staged.clear()


class KVManager:
    """High-level key-value manager.

    Use this class for simple key-value operations (`get`, `set`, `delete`,
    `exists`, `list`) against the workspace KV store.

    For type-safe model workflows, use `repository()` to create a
    `TypedKVRepository`, or `namespace()` to scope both simple KV and
    typed repositories to a specific prefix.
    """

    def __init__(self, agent_fs: AgentFS, prefix: str = ""):
        """Initialize a KV manager.

        Args:
            agent_fs: Backing AgentFS instance.
            prefix: Namespace prefix automatically applied to keys.
        """
        self._agent_fs = agent_fs
        self._prefix = self._compose_prefix("", prefix)

    @staticmethod
    def _compose_prefix(base: str, child: str) -> str:
        """Compose and normalize namespace prefixes.

        Canonical prefix rules:
        - Empty segments are ignored.
        - Prefix segments are separated by a single ":".
        - Non-empty composed prefixes always end with ":".

        Examples:
            "app" + "user" -> "app:user:"
            "app:" + "user:" -> "app:user:"
            "" + "" -> ""
        """

        segments: list[str] = []
        for part in (base, child):
            if not part:
                continue
            normalized = part.strip(":")
            if normalized:
                segments.extend(segment for segment in normalized.split(":") if segment)

        return ":".join(segments) + (":" if segments else "")

    @property
    def agent_fs(self) -> AgentFS:
        """Return the backing AgentFS instance."""
        return self._agent_fs

    @property
    def prefix(self) -> str:
        """Return the effective namespace prefix for this manager."""
        return self._prefix

    def _qualify_key(self, key: str) -> str:
        """Return the fully-qualified KV key for this manager namespace."""
        return f"{self._prefix}{key}"

    def transaction(self) -> KVTransaction:
        """Create a best-effort transaction context for grouped KV operations."""
        return KVTransaction(self)

    async def get(self, key: str, default: Any = _MISSING) -> Any:
        """Get a value by key using simple KV semantics.

        This is for direct, untyped KV access. For model validation and typed
        records, prefer `repository()`.

        Contract:
            - If `key` exists, return its stored value.
            - If `key` does not exist and `default` is provided, return `default`.
            - If `key` does not exist and no `default` is provided,
              raise `KeyNotFoundError`.
        """
        qualified_key = self._qualify_key(key)
        try:
            value = await self._agent_fs.kv.get(qualified_key)
        except (TypeError, ValueError) as exc:
            raise SerializationError(
                f"KV deserialization failed during get for key='{qualified_key}' "
                f"(prefix='{self._prefix}')"
            ) from exc

        if value is not None:
            return value

        try:
            matched = await self._agent_fs.kv.list(prefix=qualified_key)
        except (ErrnoException, RuntimeError) as exc:
            raise KVStoreError(
                f"KV operation=get-check-missing failed for key='{qualified_key}' "
                f"(prefix='{self._prefix}')"
            ) from exc

        exists = any(item.get("key") == qualified_key for item in matched)
        if exists:
            return value
        if default is not _MISSING:
            return default
        raise KeyNotFoundError(qualified_key)

    async def set(self, key: str, value: Any) -> None:
        """Set a value by key using simple KV semantics.

        This stores raw KV values directly. For Pydantic models, prefer
        `repository().save(...)`.
        """
        qualified_key = self._qualify_key(key)
        try:
            await self._agent_fs.kv.set(qualified_key, value)
        except (TypeError, ValueError) as exc:
            raise SerializationError(
                f"KV serialization failed during set for key='{qualified_key}' "
                f"(prefix='{self._prefix}')"
            ) from exc
        except (ErrnoException, RuntimeError) as exc:
            raise KVStoreError(
                f"KV operation=set failed for key='{qualified_key}' "
                f"(prefix='{self._prefix}')"
            ) from exc

    async def delete(self, key: str) -> bool:
        """Delete a value by key using simple KV semantics.

        Contract:
            - Returns `True` when a key existed and was deleted.
            - Returns `False` when the key did not exist.
            - Missing-key deletes are a stable no-op.
        """
        qualified_key = self._qualify_key(key)
        try:
            matched = await self._agent_fs.kv.list(prefix=qualified_key)
        except (ErrnoException, RuntimeError) as exc:
            raise KVStoreError(
                f"KV operation=delete-check-exists failed for key='{qualified_key}' "
                f"(prefix='{self._prefix}')"
            ) from exc

        exists = any(item.get("key") == qualified_key for item in matched)
        if not exists:
            return False

        try:
            await self._agent_fs.kv.delete(qualified_key)
        except (ErrnoException, RuntimeError) as exc:
            raise KVStoreError(
                f"KV operation=delete failed for key='{qualified_key}' "
                f"(prefix='{self._prefix}')"
            ) from exc
        return True

    async def get_many(self, keys: list[str], *, default: Any = _MISSING) -> BatchResult:
        """Get many keys with deterministic ordering and per-item outcomes.

        The return order exactly matches the input order. Missing keys are
        failures when ``default`` is omitted and successes with ``value=default``
        when ``default`` is provided.
        """
        if not keys:
            return BatchResult()

        async def _get_one(index: int, key: str) -> BatchItemResult:
            try:
                value = await self.get(key, default=default)
                return BatchItemResult(index=index, key_or_path=key, ok=True, value=value)
            except (FsdanticError, TypeError, ValueError) as exc:  # pragma: no cover - defensive fallback
                return BatchItemResult(index=index, key_or_path=key, ok=False, error=str(exc))

        gathered = await asyncio.gather(
            *(_get_one(index, key) for index, key in enumerate(keys)),
            return_exceptions=True,
        )

        items: list[BatchItemResult] = []
        for index, raw_result in enumerate(gathered):
            if isinstance(raw_result, BatchItemResult):
                items.append(raw_result)
            else:
                items.append(BatchItemResult(index=index, key_or_path=keys[index], ok=False, error=str(raw_result)))
        return BatchResult(items=items)

    async def set_many(
        self,
        items: list[tuple[str, Any]],
        *,
        concurrency_limit: int = 10,
    ) -> BatchResult:
        """Set many keys with bounded concurrency and per-item outcomes."""
        if concurrency_limit <= 0:
            raise ValueError("concurrency_limit must be greater than 0")
        if not items:
            return BatchResult()

        semaphore = asyncio.Semaphore(concurrency_limit)

        async def _set_one(index: int, item: tuple[str, Any]) -> BatchItemResult:
            key, value = item
            async with semaphore:
                try:
                    await self.set(key, value)
                    return BatchItemResult(index=index, key_or_path=key, ok=True, value=True)
                except (FsdanticError, TypeError, ValueError) as exc:  # pragma: no cover - defensive fallback
                    return BatchItemResult(index=index, key_or_path=key, ok=False, error=str(exc))

        gathered = await asyncio.gather(
            *(_set_one(index, item) for index, item in enumerate(items)),
            return_exceptions=True,
        )

        results: list[BatchItemResult] = []
        for index, raw_result in enumerate(gathered):
            if isinstance(raw_result, BatchItemResult):
                results.append(raw_result)
            else:
                key = items[index][0]
                results.append(BatchItemResult(index=index, key_or_path=key, ok=False, error=str(raw_result)))
        return BatchResult(items=results)

    async def delete_many(self, keys: list[str], *, concurrency_limit: int = 10) -> BatchResult:
        """Delete many keys with bounded concurrency and per-item outcomes."""
        if concurrency_limit <= 0:
            raise ValueError("concurrency_limit must be greater than 0")
        if not keys:
            return BatchResult()

        semaphore = asyncio.Semaphore(concurrency_limit)

        async def _delete_one(index: int, key: str) -> BatchItemResult:
            async with semaphore:
                try:
                    deleted = await self.delete(key)
                    return BatchItemResult(index=index, key_or_path=key, ok=True, value=deleted)
                except FsdanticError as exc:  # pragma: no cover - defensive fallback
                    return BatchItemResult(index=index, key_or_path=key, ok=False, error=str(exc))

        gathered = await asyncio.gather(
            *(_delete_one(index, key) for index, key in enumerate(keys)),
            return_exceptions=True,
        )

        results: list[BatchItemResult] = []
        for index, raw_result in enumerate(gathered):
            if isinstance(raw_result, BatchItemResult):
                results.append(raw_result)
            else:
                results.append(BatchItemResult(index=index, key_or_path=keys[index], ok=False, error=str(raw_result)))
        return BatchResult(items=results)

    async def exists(self, key: str) -> bool:
        """Return whether a key exists using simple KV semantics."""
        try:
            await self.get(key)
        except KeyNotFoundError:
            return False
        return True

    async def list(self, prefix: str = "") -> list[dict[str, Any]]:
        """List key-value entries for a simple KV prefix.

        Args:
            prefix: Optional additional prefix inside this manager's namespace.

        Returns:
            Entries with keys relative to this manager namespace.

        Contract:
            - Input `prefix` is interpreted as manager-relative.
            - Returned `item["key"]` values are manager-relative.
            - Underlying AgentFS calls always use fully-qualified keys.
        """
        qualified_prefix = self._qualify_key(prefix)
        items = await self._agent_fs.kv.list(prefix=qualified_prefix)
        return [
            {**item, "key": item["key"][len(self._prefix) :]}
            for item in items
            if item["key"].startswith(self._prefix)
        ]

    def repository(
        self,
        prefix: str = "",
        model_type: type[BaseModel] | None = None,
    ) -> TypedKVRepository:
        """Create a typed repository scoped to this manager namespace.

        Args:
            prefix: Optional child namespace for repository keys, composed
                with this manager's namespace using canonical `:` semantics.
            model_type: Optional default model class for typed loading APIs.

        Returns:
            A `TypedKVRepository` configured as the implementation engine for
            model validation and typed load/list operations.

        Examples:
            >>> await workspace.kv.set("theme", "dark")
            >>> theme = await workspace.kv.get("theme")
            >>>
            >>> users = workspace.kv.repository(prefix="user:", model_type=UserRecord)
            >>> await users.save("alice", UserRecord(name="Alice"))
            >>> alice = await users.load("alice")
        """
        from .repository import TypedKVRepository

        return TypedKVRepository(
            self._agent_fs,
            prefix=self._compose_prefix(self._prefix, prefix),
            model_type=model_type,
        )

    def namespace(self, prefix: str) -> "KVManager":
        """Create a child KV manager scoped to a nested namespace prefix.

        The returned manager supports both simple KV methods and typed
        repositories while applying the combined prefix.
        """
        return KVManager(
            self._agent_fs,
            prefix=self._compose_prefix(self._prefix, prefix),
        )
