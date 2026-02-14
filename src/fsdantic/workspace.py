"""Workspace façade around AgentFS with lazy manager loading."""

from typing import Any

from agentfs_sdk import AgentFS

from .materialization import Materializer
from .files import FileManager
from .overlay import OverlayOperations


class Workspace:
    """Unified runtime façade around an AgentFS instance."""

    def __init__(self, raw: AgentFS):
        self._raw = raw
        self._files: FileManager | None = None
        self._kv: Any | None = None
        self._overlay: OverlayOperations | None = None
        self._materialize: Materializer | None = None
        self._closed = False

    @property
    def raw(self) -> AgentFS:
        """Expose the underlying AgentFS instance."""
        return self._raw

    @property
    def files(self) -> FileManager:
        """Lazy file manager."""
        if self._files is None:
            self._files = FileManager(self._raw)
        return self._files

    @property
    def kv(self) -> Any:
        """Lazy key-value manager (currently backed by AgentFS kv API)."""
        if self._kv is None:
            self._kv = self._raw.kv
        return self._kv

    @property
    def overlay(self) -> OverlayOperations:
        """Lazy overlay operations manager."""
        if self._overlay is None:
            self._overlay = OverlayOperations()
        return self._overlay

    @property
    def materialize(self) -> Materializer:
        """Lazy materialization manager."""
        if self._materialize is None:
            self._materialize = Materializer()
        return self._materialize

    async def close(self) -> None:
        """Close the workspace exactly once."""
        if self._closed:
            return
        await self._raw.close()
        self._closed = True

    async def __aenter__(self) -> "Workspace":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
