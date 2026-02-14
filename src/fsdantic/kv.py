"""Simple key-value manager with optional typed repository helpers."""

from __future__ import annotations

from typing import Any

from agentfs_sdk import AgentFS

from .repository import TypedKVRepository


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

    async def get(self, key: str) -> Any:
        """Get a value by key using simple KV semantics.

        This is for direct, untyped KV access. For model validation and typed
        records, prefer `repository()`.
        """
        return await self._agent_fs.kv.get(self._qualify_key(key))

    async def set(self, key: str, value: Any) -> None:
        """Set a value by key using simple KV semantics.

        This stores raw KV values directly. For Pydantic models, prefer
        `repository().save(...)`.
        """
        await self._agent_fs.kv.set(self._qualify_key(key), value)

    async def delete(self, key: str) -> None:
        """Delete a value by key using simple KV semantics."""
        await self._agent_fs.kv.delete(self._qualify_key(key))

    async def exists(self, key: str) -> bool:
        """Return whether a key exists using simple KV semantics."""
        return await self.get(key) is not None

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

    def repository(self, prefix: str = "") -> TypedKVRepository:
        """Create a typed repository bridged to this manager namespace.

        Use this when you want typed, model-validated records instead of raw
        simple KV values.
        """
        return TypedKVRepository(
            self._agent_fs,
            prefix=self._compose_prefix(self._prefix, prefix),
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
