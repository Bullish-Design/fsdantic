"""Workspace façade around AgentFS with lazy manager loading."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from agentfs_sdk import AgentFS

from .files import FileManager
from .kv import KVManager
from .materialization import MaterializationManager
from .overlay import OverlayManager

if TYPE_CHECKING:
    from turso.aio import Connection as TursoConnection


class Workspace:
    """Unified runtime façade around an AgentFS instance."""

    def __init__(
        self,
        raw: AgentFS,
        readonly: bool = False,
        busy_timeout_ms: int | None = None,
        max_content_bytes: int | None = None,
    ):
        self._raw = raw
        self._readonly = readonly
        self._busy_timeout_ms = busy_timeout_ms
        self._max_content_bytes = max_content_bytes
        self._files: FileManager | None = None
        self._kv: KVManager | None = None
        self._overlay: OverlayManager | None = None
        self._materialize: MaterializationManager | None = None
        self._serialize_lock = asyncio.Lock()
        self._closed = False

    @property
    def raw(self) -> AgentFS:
        """Expose the underlying AgentFS instance."""
        return self._raw

    @property
    def readonly(self) -> bool:
        """True when this workspace is open in read-only mode.

        Read-only workspaces reject write operations with
        ``WorkspaceError(code="WORKSPACE_READONLY")`` at the manager API
        boundary and at the raw connection level (via the connection
        guard).
        """
        return self._readonly

    @property
    def busy_timeout_ms(self) -> int | None:
        """The write-lock busy timeout (ms) configured at open time.

        Set by :meth:`Fsdantic.open` (default 5000; ``0`` disables the
        wait).  ``None`` when the workspace was constructed directly or via
        ``open_with_options`` (no timeout configured — the raw turso
        default of 0 applies).
        """
        return self._busy_timeout_ms

    @property
    def max_content_bytes(self) -> int | None:
        """Write payload cap (bytes) configured at open time.

        Set by :meth:`Fsdantic.open` (default ``None`` = unbounded).
        Payloads larger than the cap raise ``WorkspaceError`` with
        ``code="CONTENT_TOO_LARGE"``.
        """
        return self._max_content_bytes

    @property
    def connection(self) -> TursoConnection:
        """Expose the underlying database connection.

        This is the public accessor for the turso ``Connection`` backing
        this workspace.  Useful for running PRAGMAs, inspecting journal
        mode, or performing advanced operations (e.g. ``BEGIN CONCURRENT``
        under MVCC).

        On read-only workspaces this returns the fsdantic ``_ReadonlyGuard``
        proxy (which satisfies the turso ``Connection`` protocol): read-ish
        statements and PRAGMAs (e.g. ``PRAGMA busy_timeout``) pass through,
        while write statements raise ``WorkspaceError``
        (``WORKSPACE_READONLY``).
        """
        return self._raw.get_database()

    @property
    def files(self) -> FileManager:
        """Lazy file manager."""
        if self._files is None:
            self._files = FileManager(
                self._raw,
                readonly=self._readonly,
                max_content_bytes=self._max_content_bytes,
            )
        return self._files

    @property
    def kv(self) -> KVManager:
        """Lazy key-value manager for simple and typed KV workflows."""
        if self._kv is None:
            self._kv = KVManager(
                self._raw,
                readonly=self._readonly,
                max_content_bytes=self._max_content_bytes,
            )
        return self._kv

    @property
    def overlay(self) -> OverlayManager:
        """Lazy overlay manager."""
        if self._overlay is None:
            self._overlay = OverlayManager(self._raw, readonly=self._readonly)
        return self._overlay

    @property
    def materialize(self) -> MaterializationManager:
        """Lazy materialization manager."""
        if self._materialize is None:
            self._materialize = MaterializationManager(self._raw, readonly=self._readonly)
        return self._materialize

    @asynccontextmanager
    async def serialized(self) -> AsyncIterator[None]:
        """Serialize concurrent async access to this workspace.

        Yields while holding a per-workspace ``asyncio.Lock``.  This is a
        *primitive* for same-process coordination (e.g. atomic
        read-modify-write sequences on shared keys): callers own the policy
        of when it is needed.  It does NOT coordinate across processes or
        connections — use MVCC retries for that.

        Examples:
            >>> async with workspace.serialized():
            ...     current = await workspace.kv.get("counter")
            ...     await workspace.kv.set("counter", current + 1)
        """
        async with self._serialize_lock:
            yield

    async def close(self) -> None:
        """Close the workspace exactly once."""
        if self._closed:
            return
        await self._raw.close()
        self._closed = True

    async def __aenter__(self) -> Workspace:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
