"""Legacy compatibility module for file operations.

FileManager in ``fsdantic.files`` is the primary public API.
"""

from typing import Any, Optional

from agentfs_sdk import AgentFS

from .files import FileManager


class FileOperations(FileManager):
    """Deprecated alias for FileManager."""

    def __init__(self, agent_fs: AgentFS, base_fs: Optional[AgentFS] = None):
        super().__init__(agent_fs, base_fs)

    async def read_file(self, path: str, *, encoding: Optional[str] = "utf-8") -> str | bytes:
        if encoding is None:
            return await self.read(path, mode="binary", encoding=None)
        return await self.read(path, mode="text", encoding=encoding)

    async def write_file(
        self,
        path: str,
        content: str | bytes | dict[str, Any] | list[Any],
        *,
        encoding: str = "utf-8",
    ) -> None:
        await self.write(path, content, encoding=encoding)

    async def file_exists(self, path: str) -> bool:
        return await self.exists(path)

    async def search_files(self, pattern: str, recursive: bool = True) -> list[str]:
        return await self.search(pattern, recursive=recursive)
