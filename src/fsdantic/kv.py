"""Simple key-value manager with optional typed repository helpers."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from agentfs_sdk import AgentFS, ErrnoException
from pydantic import BaseModel

from ._internal.kv_cas import get_raw as _kv_get_raw
from ._internal.kv_cas import key_exists
from .exceptions import FsdanticError, KeyNotFoundError, KVStoreError, SerializationError, WorkspaceError
from .models import BatchItemResult, BatchResult

if TYPE_CHECKING:
    from .repository import TypedKVRepository


_MISSING = object()

# Marker key used to encode ``bytes`` values for storage.
_BYTES_MARKER = "$fsdantic:bytes"


def _kv_normalize(value: Any) -> Any:
    """Recursively convert non-JSON values to JSON-native equivalents.

    Handles: datetime/date -> ISO-8601 string, bytes -> ``{"$fsdantic:bytes":
    "<base64>"}``, set/frozenset -> sorted list, Enum -> its value, and
    Path/UUID -> string.  Any other value is passed through so the SDK's
    ``json.dumps`` can raise for genuinely unserializable objects (which
    :meth:`KVManager.set` wraps in :class:`SerializationError`).
    """
    if isinstance(value, dict):
        return {str(key): _kv_normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_kv_normalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_kv_normalize(item) for item in value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {_BYTES_MARKER: base64.b64encode(value).decode("ascii")}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (Path, UUID)):
        return str(value)
    return value


def _kv_denormalize(value: Any) -> Any:
    """Recursively convert fsdantic marker encodings back to Python objects.

    The inverse of :func:`_kv_normalize` for the encodings that are not
    JSON-native round-trips: ``{"$fsdantic:bytes": "<base64>"}`` becomes
    ``bytes`` again.  Used by the typed repository layer so byte fields
    round-trip losslessly.  Malformed markers are left as plain dicts.
    """
    if isinstance(value, dict):
        if len(value) == 1 and _BYTES_MARKER in value and isinstance(value[_BYTES_MARKER], str):
            try:
                return base64.b64decode(value[_BYTES_MARKER], validate=True)
            except (ValueError, TypeError):
                pass
        return {str(key): _kv_denormalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_kv_denormalize(item) for item in value]
    return value


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

    def __init__(self, manager: KVManager) -> None:
        self._manager = manager
        self._staged: dict[str, _StagedOperation] = {}
        self._committed = False

    async def __aenter__(self) -> KVTransaction:
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
                    rollback_errors.append(f"key={staged.key}: {rollback_exc}")

            if rollback_errors:
                raise KVStoreError(
                    "KV transaction commit failed and rollback was partial; manual reconciliation may be required"
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

    def __init__(
        self,
        agent_fs: AgentFS,
        prefix: str = "",
        readonly: bool = False,
        max_content_bytes: int | None = None,
    ):
        """Initialize a KV manager.

        Args:
            agent_fs: Backing AgentFS instance.
            prefix: Namespace prefix automatically applied to keys.
            readonly: When True, write methods (``set``/``set_many``/
                ``delete``/``delete_many``/``increment``) raise
                ``WorkspaceError`` with ``code="WORKSPACE_READONLY"`` before
                touching storage.
            max_content_bytes: Optional cap on serialized JSON payload sizes
                for ``set``/``set_many``.  Larger payloads raise
                ``WorkspaceError`` with ``code="CONTENT_TOO_LARGE"`` before
                touching storage.  ``None`` (default) is unbounded.
        """
        self._agent_fs = agent_fs
        self._prefix = self._compose_prefix("", prefix)
        self._readonly = readonly
        self._max_content_bytes = max_content_bytes
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    @property
    def readonly(self) -> bool:
        """True when this manager enforces read-only mode."""
        return self._readonly

    @property
    def max_content_bytes(self) -> int | None:
        """The serialized-payload cap (bytes), or None when unbounded."""
        return self._max_content_bytes

    def _ensure_writable(self, context: str) -> None:
        """Raise ``WorkspaceError(WORKSPACE_READONLY)`` on read-only managers.

        The connection guard is the primary enforcement; this check provides
        early, clear errors at the API boundary before any SDK work begins.
        """
        if self._readonly:
            raise WorkspaceError(
                f"{context}: workspace is read-only",
                code="WORKSPACE_READONLY",
            )

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

    def _conn(self):
        """Return the raw turso connection backing this manager."""
        return self._agent_fs.get_database()

    async def _key_lock(self, qualified_key: str) -> asyncio.Lock:
        """Return the per-key asyncio.Lock for intra-process serialization.

        The lock registry never shrinks; keys are typically bounded by the
        application's key space.  This is defense-in-depth for same-process
        tasks: the SQL CAS in ``_internal/kv_cas`` remains the source of
        truth for cross-process safety.
        """
        async with self._locks_guard:
            return self._locks.setdefault(qualified_key, asyncio.Lock())

    async def get_raw(self, key: str) -> str | None:
        """Return the raw JSON text stored for a key, or ``None`` when missing.

        This is an O(1) direct SQL read used by the repository layer to
        implement atomic compare-and-set without JSON round-trips.

        Coupling note: targets the AgentFS ``kv_store`` schema directly
        (see ``_internal/kv_cas``).
        """
        qualified_key = self._qualify_key(key)
        return await _kv_get_raw(self._conn(), qualified_key)

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

        Note: a stored JSON ``null`` value is returned as ``None`` and is
        indistinguishable from a missing key via this method.  Use
        :meth:`exists` to disambiguate.
        """
        qualified_key = self._qualify_key(key)
        try:
            value = await self._agent_fs.kv.get(qualified_key)
        except (TypeError, ValueError) as exc:
            raise SerializationError(
                f"KV deserialization failed during get for key='{qualified_key}' (prefix='{self._prefix}')"
            ) from exc

        if value is not None:
            return value

        # ``None`` here means either "stored literal null" or "missing".
        # Disambiguate with an O(1) existence check instead of an O(n)
        # prefix scan that JSON-deserializes every matching key.
        if await key_exists(self._conn(), qualified_key):
            return value  # stored literal null
        if default is not _MISSING:
            return default
        raise KeyNotFoundError(qualified_key)

    async def set(self, key: str, value: Any) -> None:
        """Set a value by key using simple KV semantics.

        This stores raw KV values directly. For Pydantic models, prefer
        ``repository().save(...)``.

        Non-JSON values are normalized before storage: datetimes become
        ISO-8601 strings, bytes become ``{"$fsdantic:bytes": "<base64>"}``,
        sets become sorted lists, enums become their values, and Path/UUID
        become strings.

        Decode asymmetry (documented): raw :meth:`get` returns the JSON-native
        form, NOT the original Python object (datetimes come back as ISO
        strings, bytes as the marker dict).  Typed repositories round-trip
        losslessly because pydantic re-coerces during ``model_validate``.

        Raises:
            WorkspaceError: with ``code="WORKSPACE_READONLY"`` when this
                manager belongs to a read-only workspace, or
                ``code="CONTENT_TOO_LARGE"`` when the serialized payload
                exceeds the configured ``max_content_bytes`` cap.
        """
        self._ensure_writable(f"KVManager.set(key={key!r})")
        qualified_key = self._qualify_key(key)
        try:
            normalized = _kv_normalize(value)
            if self._max_content_bytes is not None:
                stored_size = len(json.dumps(normalized))
                if stored_size > self._max_content_bytes:
                    raise WorkspaceError(
                        f"KVManager.set(key={key!r}): content of {stored_size} bytes exceeds "
                        f"the configured max_content_bytes={self._max_content_bytes}",
                        code="CONTENT_TOO_LARGE",
                    )
            await self._agent_fs.kv.set(qualified_key, normalized)
        except (TypeError, ValueError) as exc:
            raise SerializationError(
                f"KV serialization failed during set for key='{qualified_key}' "
                f"(prefix='{self._prefix}'): unsupported type {type(value).__name__}"
            ) from exc
        except (ErrnoException, RuntimeError) as exc:
            raise KVStoreError(f"KV operation=set failed for key='{qualified_key}' (prefix='{self._prefix}')") from exc

    async def delete(self, key: str) -> bool:
        """Delete a value by key using simple KV semantics.

        Contract:
            - Returns `True` when a key existed and was deleted.
            - Returns `False` when the key did not exist.
            - Missing-key deletes are a stable no-op.

        Raises:
            WorkspaceError: with ``code="WORKSPACE_READONLY"`` when this
                manager belongs to a read-only workspace.
        """
        self._ensure_writable(f"KVManager.delete(key={key!r})")
        qualified_key = self._qualify_key(key)
        if not await key_exists(self._conn(), qualified_key):
            return False

        # TOCTOU note: another writer could delete the key between the
        # existence check and the delete; the delete then silently no-ops.
        # Acceptable for this API (documented); the CAS pattern could close
        # the gap via ``DELETE ... WHERE key=?`` + rowcount if ever needed.
        try:
            await self._agent_fs.kv.delete(qualified_key)
        except (ErrnoException, RuntimeError) as exc:
            raise KVStoreError(
                f"KV operation=delete failed for key='{qualified_key}' (prefix='{self._prefix}')"
            ) from exc
        return True

    async def get_many(self, keys: list[str], *, default: Any = _MISSING, concurrency_limit: int = 10) -> BatchResult:
        """Get many keys with deterministic ordering and per-item outcomes.

        The return order exactly matches the input order. Missing keys are
        failures when ``default`` is omitted and successes with ``value=default``
        when ``default`` is provided.

        ``concurrency_limit`` bounds fan-out using ``asyncio.Semaphore``
        (matching ``set_many``/``delete_many``).
        """
        if concurrency_limit <= 0:
            raise ValueError("concurrency_limit must be greater than 0")
        if not keys:
            return BatchResult()

        semaphore = asyncio.Semaphore(concurrency_limit)

        async def _get_one(index: int, key: str) -> BatchItemResult:
            async with semaphore:
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

        self._ensure_writable("KVManager.set_many")

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

        self._ensure_writable("KVManager.delete_many")

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

    async def _get_typed_number(self, qualified_key: str) -> int | float:
        """Return the numeric value stored at ``qualified_key`` (0 when missing).

        Uses the raw SQL read (``_internal/kv_cas.get_raw``) so the stored
        JSON text is decoded without the SDK's ``json.loads`` error wrapping.

        Raises:
            SerializationError: when the stored value is not a JSON number
                (``bool`` counts as non-numeric).
        """
        raw = await _kv_get_raw(self._conn(), qualified_key)
        if raw is None:
            return 0
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise SerializationError(
                f"KV increment target key='{qualified_key}' holds invalid JSON"
            ) from exc
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SerializationError(
                f"KV increment target key='{qualified_key}' holds a non-numeric value "
                f"(type={type(value).__name__})"
            )
        return value

    async def increment(self, key: str, amount: int | float = 1) -> int | float:
        """Atomically increment a numeric value and return the new value.

        Creates the key with value 0 when absent.  Non-numeric stored values
        raise :class:`SerializationError`.  The new value is stored as a bare
        JSON number (consistent with ``_kv_normalize``).

        Concurrency: same-process increments are serialized per key via an
        ``asyncio.Lock`` (``_key_lock``).  Cross-process increments (e.g.
        multiple MVCC connections) can still race on the read-modify-write
        because the lock is per-process — use the per-key SQL CAS in
        :class:`~fsdantic.repository.TypedKVRepository` for cross-process
        safety.

        Examples:
            >>> await workspace.kv.set("turns", 3)
            >>> await workspace.kv.increment("turns")  # 4

        Raises:
            WorkspaceError: with ``code="WORKSPACE_READONLY"`` when this
                manager belongs to a read-only workspace.
        """
        self._ensure_writable(f"KVManager.increment(key={key!r})")
        qualified_key = self._qualify_key(key)
        lock = await self._key_lock(qualified_key)
        async with lock:
            current = await self._get_typed_number(qualified_key)
            new_value = current + amount
            await self._agent_fs.kv.set(qualified_key, _kv_normalize(new_value))
            return new_value

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
            {**item, "key": item["key"][len(self._prefix) :]} for item in items if item["key"].startswith(self._prefix)
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

    def namespace(self, prefix: str) -> KVManager:
        """Create a child KV manager scoped to a nested namespace prefix.

        The returned manager supports both simple KV methods and typed
        repositories while applying the combined prefix.  The child manager
        inherits this manager's read-only state and content cap.
        """
        return KVManager(
            self._agent_fs,
            prefix=self._compose_prefix(self._prefix, prefix),
            readonly=self._readonly,
            max_content_bytes=self._max_content_bytes,
        )
