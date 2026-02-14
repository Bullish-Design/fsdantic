"""Workspace façade around AgentFS with lazy manager loading."""

from agentfs_sdk import AgentFS

from .files import FileManager
from .kv import KVManager
from .materialization import MaterializationManager
from .overlay import OverlayManager


class Workspace:
    """Unified runtime façade around an AgentFS instance."""

    def __init__(self, raw: AgentFS):
        self._raw = raw
        self._files: FileManager | None = None
        self._kv: KVManager | None = None
        self._overlay: OverlayManager | None = None
        self._materialize: MaterializationManager | None = None
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
    def kv(self) -> KVManager:
        """Lazy key-value manager for simple and typed KV workflows."""
        if self._kv is None:
            self._kv = KVManager(self._raw)
        return self._kv

    @property
    def overlay(self) -> OverlayManager:
        """Lazy overlay manager."""
        if self._overlay is None:
            self._overlay = OverlayManager(self._raw)
        return self._overlay

    @property
    def materialize(self) -> MaterializationManager:
        """Lazy materialization manager."""
        if self._materialize is None:
            self._materialize = MaterializationManager(self._raw)
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
