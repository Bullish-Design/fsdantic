"""Primary public API for file operations and traversal."""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Optional

from agentfs_sdk import AgentFS, ErrnoException

from ._internal.errors import translate_agentfs_error
from .models import FileEntry, FileStats


if TYPE_CHECKING:
    from .view import ViewQuery


class FileManager:
    """Primary high-level API for file operations with optional base fallthrough."""

    def __init__(self, agent_fs: AgentFS, base_fs: Optional[AgentFS] = None):
        self.agent_fs = agent_fs
        self.base_fs = base_fs

    async def read(self, path: str, *, encoding: Optional[str] = "utf-8") -> str | bytes:
        """Read a file with overlay-first and optional base fallthrough semantics."""
        context = f"FileManager.read(path={path!r})"

        try:
            return await self.agent_fs.fs.read_file(path, encoding=encoding)
        except ErrnoException as e:
            if e.code != "ENOENT":
                raise translate_agentfs_error(e, context) from e
            if self.base_fs is None:
                raise translate_agentfs_error(e, context) from e

        try:
            return await self.base_fs.fs.read_file(path, encoding=encoding)
        except ErrnoException as base_error:
            raise translate_agentfs_error(base_error, context) from base_error

    async def write(self, path: str, content: str | bytes, *, encoding: str = "utf-8") -> None:
        """Write a file to overlay filesystem only."""
        if isinstance(content, str):
            content = content.encode(encoding)

        context = f"FileManager.write(path={path!r})"
        try:
            await self.agent_fs.fs.write_file(path, content)
        except ErrnoException as e:
            raise translate_agentfs_error(e, context) from e

    async def exists(self, path: str) -> bool:
        """Check whether a path exists in overlay or base."""
        context = f"FileManager.exists(path={path!r})"

        try:
            await self.agent_fs.fs.stat(path)
            return True
        except ErrnoException as e:
            if e.code != "ENOENT":
                raise translate_agentfs_error(e, context) from e

        if self.base_fs:
            try:
                await self.base_fs.fs.stat(path)
                return True
            except ErrnoException as base_err:
                if base_err.code != "ENOENT":
                    raise translate_agentfs_error(base_err, context) from base_err

        return False

    async def stat(self, path: str) -> FileStats:
        """Return typed file stats from overlay with optional base fallthrough."""
        context = f"FileManager.stat(path={path!r})"

        try:
            stats = await self.agent_fs.fs.stat(path)
        except ErrnoException as e:
            if e.code != "ENOENT":
                raise translate_agentfs_error(e, context) from e
            if self.base_fs is None:
                raise translate_agentfs_error(e, context) from e
            try:
                stats = await self.base_fs.fs.stat(path)
            except ErrnoException as base_error:
                raise translate_agentfs_error(base_error, context) from base_error

        return FileStats(
            size=stats.size,
            mtime=stats.mtime,
            is_file=stats.is_file(),
            is_directory=stats.is_directory(),
        )

    async def list_dir(self, path: str) -> list[str]:
        """List directory entries at path."""
        context = f"FileManager.list_dir(path={path!r})"
        try:
            entries = await self.agent_fs.fs.readdir(path)
        except ErrnoException as e:
            raise translate_agentfs_error(e, context) from e
        return list(entries)

    async def remove(self, path: str) -> None:
        """Remove a file path from overlay."""
        context = f"FileManager.remove(path={path!r})"
        try:
            await self.agent_fs.fs.unlink(path)
        except ErrnoException as e:
            raise translate_agentfs_error(e, context) from e

    async def search(self, pattern: str, recursive: bool = True) -> list[str]:
        """Search for files matching a glob pattern."""
        from .view import ViewQuery

        entries = await self.query(
            ViewQuery(
                path_pattern=pattern,
                recursive=recursive,
                include_stats=False,
                include_content=False,
            )
        )
        return [entry.path for entry in entries]

    async def query(self, query: "ViewQuery") -> list[FileEntry]:
        """Run a ViewQuery and return matching FileEntry records."""
        from .view import View

        return await View(agent=self.agent_fs, query=query).load()

    async def tree(
        self, path: str = "/", max_depth: Optional[int] = None
    ) -> dict[str, Any]:
        """Return nested directory tree rooted at path."""

        async def walk(current_path: str, depth: int = 0) -> dict[str, Any]:
            if max_depth is not None and depth >= max_depth:
                return {}

            result: dict[str, Any] = {}

            try:
                entries = await self.agent_fs.fs.readdir(current_path)
            except ErrnoException as e:
                if e.code == "ENOENT":
                    return result
                context = f"FileManager.tree(path={path!r}, current_path={current_path!r})"
                raise translate_agentfs_error(e, context) from e

            for entry_name in entries:
                entry_path = f"{current_path.rstrip('/')}/{entry_name}"
                try:
                    stat = await self.agent_fs.fs.stat(entry_path)
                except ErrnoException as e:
                    if e.code == "ENOENT":
                        continue
                    context = (
                        f"FileManager.tree(path={path!r}, current_path={current_path!r})"
                    )
                    raise translate_agentfs_error(e, context) from e

                if stat.is_directory():
                    result[entry_name] = await walk(entry_path, depth + 1)
                else:
                    result[entry_name] = None

            return result

        return await walk(path)

    async def traverse_files(
        self, root: str = "/", *, recursive: bool = True, include_stats: bool = False
    ) -> AsyncIterator[tuple[str, Any | None]]:
        """Traverse filesystem and yield file paths with optional raw stats."""
        pending = [root]

        while pending:
            path = pending.pop()
            try:
                items = await self.agent_fs.fs.readdir(path)
            except FileNotFoundError:
                continue

            for item in items:
                item_path = f"{path.rstrip('/')}/{item}"
                try:
                    stats = await self.agent_fs.fs.stat(item_path)
                except FileNotFoundError:
                    continue

                if stats.is_directory():
                    if recursive:
                        pending.append(item_path)
                    continue

                if stats.is_file():
                    yield item_path, stats if include_stats else None

