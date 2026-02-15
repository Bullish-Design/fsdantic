"""Primary public API for file operations and traversal."""

import asyncio
import logging
import codecs
import json
import re
from collections.abc import AsyncIterator
from typing import Any, Literal, Optional, overload

from agentfs_sdk import AgentFS, ErrnoException
from pydantic import BaseModel, Field, PrivateAttr, model_validator

from ._internal.errors import translate_agentfs_error
from .exceptions import FileNotFoundError
from ._internal.paths import join_normalized_path, normalize_glob_pattern, normalize_path
from .models import BatchItemResult, BatchResult, FileEntry, FileStats


logger = logging.getLogger(__name__)


class _UnsetEncoding:
    """Sentinel type for omitted encoding arguments."""


_UNSET = _UnsetEncoding()


class FileQuery(BaseModel):
    """Structured query contract for filesystem traversal and filtering."""

    path_pattern: str = Field(
        default="*",
        description="Glob pattern for matching file paths (e.g., '*.py', '/data/**/*.json')",
    )
    recursive: bool = Field(default=True, description="Whether to search subdirectories")
    include_content: bool = Field(default=False, description="Whether to load file contents")
    include_stats: bool = Field(default=True, description="Whether to include file statistics")
    regex_pattern: Optional[str] = Field(None, description="Optional regex path filter")
    max_size: Optional[int] = Field(None, ge=0, description="Maximum file size in bytes")
    min_size: Optional[int] = Field(None, ge=0, description="Minimum file size in bytes")

    _normalized_path_pattern: str = PrivateAttr(default="*")
    _path_matcher: re.Pattern[str] = PrivateAttr(default_factory=lambda: re.compile(".*"))
    _regex_matcher: Optional[re.Pattern[str]] = PrivateAttr(default=None)

    @staticmethod
    def _normalize_path_pattern(pattern: str) -> str:
        normalized = normalize_glob_pattern(pattern)
        if "/" not in normalized:
            normalized = f"**/{normalized}"
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        return normalized

    @staticmethod
    def _compile_glob_pattern(pattern: str) -> re.Pattern[str]:
        pieces: list[str] = ["^"]
        i = 0
        while i < len(pattern):
            if pattern[i : i + 3] == "**/":
                pieces.append("(?:.*/)?")
                i += 3
            elif pattern[i : i + 2] == "**":
                pieces.append(".*")
                i += 2
            elif pattern[i] == "*":
                pieces.append("[^/]*")
                i += 1
            elif pattern[i] == "?":
                pieces.append("[^/]")
                i += 1
            else:
                pieces.append(re.escape(pattern[i]))
                i += 1
        pieces.append("$")
        return re.compile("".join(pieces))

    @model_validator(mode="after")
    def _validate_and_prepare_matchers(self) -> "FileQuery":
        if self.min_size is not None and self.max_size is not None and self.min_size > self.max_size:
            raise ValueError("min_size must be less than or equal to max_size")

        self._normalized_path_pattern = self._normalize_path_pattern(self.path_pattern)
        self._path_matcher = self._compile_glob_pattern(self._normalized_path_pattern)
        self._regex_matcher = re.compile(self.regex_pattern) if self.regex_pattern else None
        return self

    def matches_path(self, path: str) -> bool:
        return bool(self._path_matcher.match(normalize_path(path)))

    def matches_regex(self, path: str) -> bool:
        if self._regex_matcher is None:
            return True
        return bool(self._regex_matcher.search(normalize_path(path)))

    def needs_file_stats(self) -> bool:
        return self.include_stats or self.min_size is not None or self.max_size is not None

    def matches_size(self, raw_stats: Any | None) -> bool:
        if raw_stats is None:
            return True
        if self.min_size is not None and raw_stats.size < self.min_size:
            return False
        if self.max_size is not None and raw_stats.size > self.max_size:
            return False
        return True


class FileManager:
    """Primary high-level API for file operations with optional base fallthrough."""

    _JSON_INDENT = 2
    _JSON_SEPARATORS = (",", ": ")

    def __init__(self, agent_fs: AgentFS, base_fs: Optional[AgentFS] = None):
        self.agent_fs = agent_fs
        self.base_fs = base_fs

    @overload
    async def read(
        self,
        path: str,
        *,
        mode: Literal["text"] = "text",
        encoding: str = "utf-8",
    ) -> str: ...

    @overload
    async def read(
        self,
        path: str,
        *,
        mode: Literal["binary"],
        encoding: None = None,
    ) -> bytes: ...

    async def read(
        self,
        path: str,
        *,
        mode: Literal["text", "binary"] = "text",
        encoding: str | None | _UnsetEncoding = _UNSET,
    ) -> str | bytes:
        """Read a file using explicit mode semantics.

        Reads from overlay first and falls through to ``base_fs`` on ``ENOENT``.
        For backward compatibility, directory reads (``EISDIR``) are normalized
        to ``FileNotFoundError`` for this method.

        * ``mode='text'`` returns ``str`` and requires a valid text ``encoding``.
        * ``mode='binary'`` returns ``bytes`` and requires ``encoding=None``.

        Use :meth:`read_stream` when handling large binary files to avoid keeping
        the full content in memory at once. Use ``read()`` for convenience when
        full in-memory content is acceptable.
        """
        path = normalize_path(path)
        context = f"FileManager.read(path={path!r})"
        resolved_encoding: Optional[str]

        if mode == "text":
            if encoding is _UNSET:
                encoding = "utf-8"
            if encoding is None:
                raise ValueError("encoding must be provided when mode='text'")
            self._validate_encoding(encoding)
            resolved_encoding = encoding
        elif mode == "binary":
            if encoding is _UNSET:
                resolved_encoding = None
            elif encoding is None:
                resolved_encoding = None
            else:
                raise ValueError("encoding must be None when mode='binary'")
        else:
            raise ValueError("mode must be 'text' or 'binary'")

        try:
            return await self.agent_fs.fs.read_file(path, encoding=resolved_encoding)
        except ErrnoException as e:
            if e.code == "EISDIR":
                base_message = getattr(e, "message", None) or str(e)
                raise FileNotFoundError(
                    f"{context}: {base_message}",
                    path=getattr(e, "path", None),
                    cause=e,
                ) from e
            if e.code != "ENOENT":
                raise translate_agentfs_error(e, context) from e
            if self.base_fs is None:
                raise translate_agentfs_error(e, context) from e

        try:
            return await self.base_fs.fs.read_file(path, encoding=resolved_encoding)
        except ErrnoException as base_error:
            raise translate_agentfs_error(base_error, context) from base_error

    async def read_stream(
        self,
        path: str,
        *,
        chunk_size: int = 65536,
    ) -> AsyncIterator[bytes]:
        """Read a file as a chunked async byte stream.

        AgentFS currently exposes ``read_file()`` but does not expose a native
        streaming API. This method therefore uses a fallback strategy: it reads
        the file once in binary mode and yields ``chunk_size`` byte slices from
        that in-memory payload.

        Reads from overlay first and falls through to ``base_fs`` on ``ENOENT``.

        Args:
            path: File path to read.
            chunk_size: Number of bytes per yielded chunk. Must be greater than 0.

        Yields:
            Byte chunks in file order.
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than 0")

        payload = await self.read(path, mode="binary")
        if not isinstance(payload, bytes):  # pragma: no cover - defensive guard
            raise TypeError("read_stream expected bytes payload")

        for offset in range(0, len(payload), chunk_size):
            yield payload[offset : offset + chunk_size]

    async def write(
        self,
        path: str,
        content: str | bytes | dict[str, Any] | list[Any],
        *,
        mode: Literal["text", "binary", "json"] | None = None,
        encoding: str = "utf-8",
    ) -> None:
        """Write to overlay filesystem only.

        Existing files are overwritten. Parent directories are created automatically
        by AgentFS when needed.

        ``content`` may be ``str``, ``bytes``, ``dict``, or ``list``.
        ``mode`` may be specified explicitly (``text``/``binary``/``json``) or inferred
        from content type.
        """
        path = normalize_path(path)
        payload = self._prepare_write_payload(content, mode=mode, encoding=encoding)

        context = f"FileManager.write(path={path!r})"
        try:
            await self.agent_fs.fs.write_file(path, payload)
        except ErrnoException as e:
            raise translate_agentfs_error(e, context) from e

    async def read_many(
        self,
        paths: list[str],
        *,
        mode: Literal["text", "binary"] = "text",
        encoding: str | None | _UnsetEncoding = _UNSET,
    ) -> BatchResult:
        """Read multiple files with deterministic ordering and per-item outcomes.

        This API always returns a ``BatchResult`` containing one ``BatchItemResult``
        per input path in the same order as ``paths``. Partial failures do not abort
        the batch; failed items include ``error`` and can be retried individually.
        """
        if not paths:
            return BatchResult()

        async def _read_one(index: int, raw_path: str) -> BatchItemResult:
            path = normalize_path(raw_path)
            try:
                value = await self.read(path, mode=mode, encoding=encoding)
                return BatchItemResult(index=index, key_or_path=path, ok=True, value=value)
            except Exception as exc:  # pragma: no cover - defensive fallback
                return BatchItemResult(index=index, key_or_path=path, ok=False, error=str(exc))

        gathered = await asyncio.gather(
            *(_read_one(index, path) for index, path in enumerate(paths)),
            return_exceptions=True,
        )

        items: list[BatchItemResult] = []
        for index, raw_result in enumerate(gathered):
            if isinstance(raw_result, BatchItemResult):
                items.append(raw_result)
            else:
                path = normalize_path(paths[index])
                items.append(
                    BatchItemResult(
                        index=index,
                        key_or_path=path,
                        ok=False,
                        error=str(raw_result),
                    )
                )
        return BatchResult(items=items)

    async def write_many(
        self,
        items: list[tuple[str, str | bytes | dict[str, Any] | list[Any]]],
        *,
        mode: Literal["text", "binary", "json"] | None = None,
        encoding: str = "utf-8",
        concurrency_limit: int = 10,
    ) -> BatchResult:
        """Write multiple files with bounded concurrency and per-item outcomes.

        ``concurrency_limit`` bounds fan-out using ``asyncio.Semaphore``. Results
        preserve the original ``items`` ordering. Failed writes are reported per item
        and do not cancel successful writes. Retry guidance: build a new batch from
        items where ``ok`` is ``False``.
        """
        if concurrency_limit <= 0:
            raise ValueError("concurrency_limit must be greater than 0")
        if not items:
            return BatchResult()

        semaphore = asyncio.Semaphore(concurrency_limit)

        async def _write_one(index: int, item: tuple[str, str | bytes | dict[str, Any] | list[Any]]) -> BatchItemResult:
            path, content = item
            normalized_path = normalize_path(path)
            async with semaphore:
                try:
                    await self.write(normalized_path, content, mode=mode, encoding=encoding)
                    return BatchItemResult(index=index, key_or_path=normalized_path, ok=True, value=True)
                except Exception as exc:  # pragma: no cover - defensive fallback
                    return BatchItemResult(index=index, key_or_path=normalized_path, ok=False, error=str(exc))

        gathered = await asyncio.gather(
            *(_write_one(index, item) for index, item in enumerate(items)),
            return_exceptions=True,
        )

        results: list[BatchItemResult] = []
        for index, raw_result in enumerate(gathered):
            if isinstance(raw_result, BatchItemResult):
                results.append(raw_result)
            else:
                normalized_path = normalize_path(items[index][0])
                results.append(
                    BatchItemResult(
                        index=index,
                        key_or_path=normalized_path,
                        ok=False,
                        error=str(raw_result),
                    )
                )
        return BatchResult(items=results)

    @staticmethod
    def _validate_encoding(encoding: str) -> None:
        try:
            codecs.lookup(encoding)
        except LookupError as e:
            raise ValueError(f"Unknown encoding: {encoding}") from e

    @classmethod
    def _serialize_json(cls, content: dict[str, Any] | list[Any]) -> str:
        return json.dumps(
            content,
            ensure_ascii=False,
            indent=cls._JSON_INDENT,
            separators=cls._JSON_SEPARATORS,
        )

    @classmethod
    def _prepare_write_payload(
        cls,
        content: str | bytes | dict[str, Any] | list[Any],
        *,
        mode: Literal["text", "binary", "json"] | None,
        encoding: str,
    ) -> bytes:
        inferred_mode: Literal["text", "binary", "json"]
        if mode is None:
            if isinstance(content, bytes):
                inferred_mode = "binary"
            elif isinstance(content, str):
                inferred_mode = "text"
            elif isinstance(content, (dict, list)):
                inferred_mode = "json"
            else:
                raise TypeError("content must be str, bytes, dict, or list")
        else:
            inferred_mode = mode

        if inferred_mode == "binary":
            if not isinstance(content, bytes):
                raise TypeError("mode='binary' requires bytes content")
            return content

        cls._validate_encoding(encoding)
        if inferred_mode == "text":
            if not isinstance(content, str):
                raise TypeError("mode='text' requires str content")
            return content.encode(encoding)

        if inferred_mode == "json":
            if not isinstance(content, (dict, list)):
                raise TypeError("mode='json' requires dict or list content")
            return cls._serialize_json(content).encode(encoding)

        raise ValueError("mode must be 'text', 'binary', or 'json'")

    async def exists(self, path: str) -> bool:
        """Check whether a path exists in overlay or base."""
        path = normalize_path(path)
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
        path = normalize_path(path)
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

        return self._to_file_stats(stats)

    async def list_dir(
        self,
        path: str,
        *,
        output: Literal["name", "relative", "full"] = "name",
    ) -> list[str]:
        """List directory entries at path in deterministic sorted order.

        Args:
            path: Directory path to list.
            output: Output path style for each entry:
                - ``"name"``: base names only (e.g., ``"main.py"``)
                - ``"relative"``: path relative to ``path`` (e.g., ``"src/main.py"``)
                - ``"full"``: normalized absolute paths (e.g., ``"/project/main.py"``)
        """
        path = normalize_path(path)
        context = f"FileManager.list_dir(path={path!r}, output={output!r})"
        if output not in {"name", "relative", "full"}:
            raise ValueError("output must be 'name', 'relative', or 'full'")

        try:
            entries = await self.agent_fs.fs.readdir(path)
        except ErrnoException as e:
            raise translate_agentfs_error(e, context) from e

        sorted_entries = sorted(entries)
        if output == "name" or output == "relative":
            return sorted_entries
        return [join_normalized_path(path, entry) for entry in sorted_entries]

    async def remove(self, path: str, *, recursive: bool = False) -> None:
        """Remove a file or directory path from overlay.

        Args:
            path: Path to remove.
            recursive: Directory removal policy. If ``False``, removing a directory
                fails predictably (non-empty directories raise ``DirectoryNotEmptyError``;
                empty directories are removed). If ``True``, directories are removed
                recursively.
        """
        path = normalize_path(path)
        context = f"FileManager.remove(path={path!r}, recursive={recursive!r})"
        try:
            stats = await self.agent_fs.fs.stat(path)
            if stats.is_directory():
                if recursive:
                    await self.agent_fs.fs.rm(path, recursive=True)
                else:
                    await self.agent_fs.fs.rmdir(path)
                return

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

    async def query(self, query: FileQuery) -> list[FileEntry]:
        """Run a query contract and return matching FileEntry records."""
        entries: list[FileEntry] = []
        include_stats = query.needs_file_stats()

        async for item_path, stats in self.traverse_files("/", recursive=query.recursive, include_stats=include_stats):
            if not query.matches_path(item_path):
                continue
            if not query.matches_regex(item_path):
                continue
            if not query.matches_size(stats):
                continue

            content = None
            if query.include_content:
                try:
                    content = await self.agent_fs.fs.read_file(item_path)
                except UnicodeDecodeError:
                    try:
                        content = await self.agent_fs.fs.read_file(item_path, encoding=None)
                    except ErrnoException as e:
                        if e.code == "ENOENT":
                            logger.debug("Path disappeared before binary read: %s", item_path)
                            continue
                        context = f"FileManager.query(path={item_path!r})"
                        raise translate_agentfs_error(e, context) from e
                except ErrnoException as e:
                    if e.code == "ENOENT":
                        logger.debug("Path disappeared before read: %s", item_path)
                        continue
                    context = f"FileManager.query(path={item_path!r})"
                    raise translate_agentfs_error(e, context) from e

            entries.append(
                FileEntry(
                    path=item_path,
                    stats=self._to_file_stats(stats) if query.include_stats and stats else None,
                    content=content,
                )
            )

        return entries

    async def count(self, query: FileQuery) -> int:
        """Count files matching a query contract."""
        count = 0
        include_stats = query.min_size is not None or query.max_size is not None
        async for item_path, stats in self.traverse_files("/", recursive=query.recursive, include_stats=include_stats):
            if not query.matches_path(item_path):
                continue
            if not query.matches_regex(item_path):
                continue
            if not query.matches_size(stats):
                continue
            count += 1
        return count

    async def tree(self, path: str = "/", max_depth: Optional[int] = None) -> dict[str, Any]:
        """Return a stable tree schema rooted at path.

        Returns a node dictionary with the shape:

        ``{ "name": str, "path": str, "type": "file"|"directory", "children": list[node] }``

        * ``children`` is always present and sorted by (type, name): directories first,
          then files, both alphabetically.
        * file nodes always have ``children=[]``.
        """
        path = normalize_path(path)

        async def walk(current_path: str, depth: int = 0) -> dict[str, Any]:
            node_name = "/" if current_path == "/" else current_path.rsplit("/", 1)[-1]
            node: dict[str, Any] = {
                "name": node_name,
                "path": current_path,
                "type": "directory",
                "children": [],
            }

            if max_depth is not None and depth >= max_depth:
                return node

            try:
                entries = await self.agent_fs.fs.readdir(current_path)
            except ErrnoException as e:
                if e.code == "ENOENT":
                    return node
                context = f"FileManager.tree(path={path!r}, current_path={current_path!r})"
                raise translate_agentfs_error(e, context) from e

            children: list[dict[str, Any]] = []
            for entry_name in sorted(entries):
                entry_path = join_normalized_path(current_path, entry_name)
                try:
                    stat = await self.agent_fs.fs.stat(entry_path)
                except ErrnoException as e:
                    if e.code == "ENOENT":
                        continue
                    context = f"FileManager.tree(path={path!r}, current_path={current_path!r})"
                    raise translate_agentfs_error(e, context) from e

                if stat.is_directory():
                    children.append(await walk(entry_path, depth + 1))
                else:
                    children.append(
                        {
                            "name": entry_name,
                            "path": entry_path,
                            "type": "file",
                            "children": [],
                        }
                    )

            node["children"] = sorted(children, key=lambda c: (c["type"] != "directory", c["name"]))
            return node

        return await walk(path)

    async def traverse_files(
        self, root: str = "/", *, recursive: bool = True, include_stats: bool = False
    ) -> AsyncIterator[tuple[str, Any | None]]:
        """Traverse filesystem and yield file paths with optional raw stats."""
        root = normalize_path(root)
        pending = [root]

        while pending:
            path = pending.pop()
            try:
                items = await self.agent_fs.fs.readdir(path)
            except ErrnoException as error:
                if error.code == "ENOENT":
                    continue
                context = f"FileManager.traverse_files(root={root!r}, current_path={path!r})"
                raise translate_agentfs_error(error, context) from error

            for item in items:
                item_path = join_normalized_path(path, item)
                try:
                    stats = await self.agent_fs.fs.stat(item_path)
                except ErrnoException as error:
                    if error.code == "ENOENT":
                        continue
                    context = f"FileManager.traverse_files(root={root!r}, current_path={item_path!r})"
                    raise translate_agentfs_error(error, context) from error

                if stats.is_directory():
                    if recursive:
                        pending.append(item_path)
                    continue

                if stats.is_file():
                    yield item_path, stats if include_stats else None

    @staticmethod
    def _to_file_stats(raw_stats: Any) -> FileStats:
        return FileStats(
            size=raw_stats.size,
            mtime=raw_stats.mtime,
            is_file=raw_stats.is_file(),
            is_directory=raw_stats.is_directory(),
        )
