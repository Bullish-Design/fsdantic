"""High-level file operations with overlay fallthrough."""

from pathlib import Path
from typing import Any, Optional

from agentfs_sdk import AgentFS, ErrnoException

from .view import View, ViewQuery


class FileOperations:
    """High-level file operations with overlay fallthrough.

    This class provides a simplified interface for working with
    overlay filesystems, automatically handling fallthrough to base layer.

    Examples:
        >>> ops = FileOperations(agent_fs, base_fs=stable_fs)
        >>> content = await ops.read_file("config.json")
        >>> await ops.write_file("output.txt", "Hello World")
        >>> files = await ops.search_files("*.py")
    """

    def __init__(self, agent_fs: AgentFS, base_fs: Optional[AgentFS] = None):
        """Initialize file operations.

        Args:
            agent_fs: Agent overlay filesystem
            base_fs: Optional base filesystem for fallthrough
        """
        self.agent_fs = agent_fs
        self.base_fs = base_fs

    async def read_file(self, path: str, *, encoding: str = "utf-8") -> str | bytes:
        """Read file from overlay with fallthrough to base.

        Args:
            path: File path to read
            encoding: Text encoding (None for binary)

        Returns:
            File content as string or bytes

        Raises:
            FileNotFoundError: If file doesn't exist in either layer

        Examples:
            >>> content = await ops.read_file("README.md")
            >>> binary = await ops.read_file("image.png", encoding=None)
        """
        # Try overlay first
        try:
            content = await self.agent_fs.fs.read_file(path)
        except ErrnoException as e:
            if e.code != "ENOENT":
                raise
            # Fallthrough to base
            if self.base_fs is None:
                raise
            content = await self.base_fs.fs.read_file(path)

        # Decode if encoding specified
        if encoding:
            return content.decode(encoding)
        return content

    async def write_file(
        self, path: str, content: str | bytes, *, encoding: str = "utf-8"
    ) -> None:
        """Write file to overlay only.

        Args:
            path: File path to write
            content: File content
            encoding: Text encoding if content is string

        Examples:
            >>> await ops.write_file("output.txt", "Hello World")
            >>> await ops.write_file("data.bin", b"\\x00\\x01", encoding=None)
        """
        # Encode if string
        if isinstance(content, str):
            content = content.encode(encoding)

        # Always write to overlay
        await self.agent_fs.fs.write_file(path, content)

    async def file_exists(self, path: str) -> bool:
        """Check if file exists in overlay or base.

        Args:
            path: File path to check

        Returns:
            True if file exists in either layer

        Examples:
            >>> if await ops.file_exists("config.json"):
            ...     print("Config exists")
        """
        try:
            await self.agent_fs.fs.stat(path)
            return True
        except ErrnoException as e:
            if e.code != "ENOENT":
                raise
            if self.base_fs:
                try:
                    await self.base_fs.fs.stat(path)
                    return True
                except ErrnoException as base_err:
                    if base_err.code != "ENOENT":
                        raise
                    pass
            return False

    async def list_dir(self, path: str) -> list[str]:
        """List directory contents from overlay.

        The overlay automatically merges with base layer.

        Args:
            path: Directory path

        Returns:
            List of entry names

        Examples:
            >>> entries = await ops.list_dir("/data")
            >>> for entry in entries:
            ...     print(entry)
        """
        entries = await self.agent_fs.fs.readdir(path)
        return list(entries)

    async def search_files(self, pattern: str, recursive: bool = True) -> list[str]:
        """Search for files matching glob pattern.

        Args:
            pattern: Glob pattern (e.g., "*.py", "**/*.json")
            recursive: Search recursively

        Returns:
            List of matching file paths

        Examples:
            >>> py_files = await ops.search_files("**/*.py")
            >>> json_files = await ops.search_files("*.json", recursive=False)
        """
        view = View(
            agent=self.agent_fs,
            query=ViewQuery(
                path_pattern=pattern,
                recursive=recursive,
                include_stats=False,
                include_content=False,
            ),
        )

        files = await view.load()
        return [f.path for f in files]

    async def stat(self, path: str) -> Any:
        """Get file statistics.

        Args:
            path: File path

        Returns:
            File stat object

        Examples:
            >>> stat = await ops.stat("file.txt")
            >>> print(f"Size: {stat.size} bytes")
        """
        try:
            return await self.agent_fs.fs.stat(path)
        except ErrnoException as e:
            if e.code != "ENOENT":
                raise
            if self.base_fs:
                return await self.base_fs.fs.stat(path)
            raise

    async def remove(self, path: str) -> None:
        """Remove file from overlay.

        This creates a whiteout in the overlay if file exists in base.

        Args:
            path: File path to remove

        Examples:
            >>> await ops.remove("temp.txt")
        """
        await self.agent_fs.fs.remove(path)

    async def tree(
        self, path: str = "/", max_depth: Optional[int] = None
    ) -> dict[str, Any]:
        """Get directory tree structure.

        Args:
            path: Root path
            max_depth: Maximum depth to traverse

        Returns:
            Nested dict representing directory tree

        Examples:
            >>> tree = await ops.tree("/src")
            >>> print(tree)  # {'main.py': None, 'lib': {'utils.py': None}}
        """

        async def walk(current_path: str, depth: int = 0):
            if max_depth is not None and depth >= max_depth:
                return {}

            result = {}

            try:
                entries = await self.agent_fs.fs.readdir(current_path)
                for entry_name in entries:
                    entry_path = f"{current_path.rstrip('/')}/{entry_name}"

                    try:
                        stat = await self.agent_fs.fs.stat(entry_path)

                        if stat.is_directory():
                            result[entry_name] = await walk(entry_path, depth + 1)
                        else:
                            result[entry_name] = None  # File (leaf node)
                    except ErrnoException as e:
                        if e.code == "ENOENT":
                            pass
                        else:
                            raise
            except ErrnoException as e:
                if e.code == "ENOENT":
                    pass
                else:
                    raise

            return result

        return await walk(path)
