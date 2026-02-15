"""View interface for querying AgentFS filesystem."""

import codecs
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from collections.abc import AsyncIterator
from typing import Callable, Optional

from agentfs_sdk import AgentFS
from pydantic import BaseModel, Field

from .files import FileManager, FileQuery
from .models import FileEntry


@dataclass
class SearchMatch:
    """A single content search match.

    Represents a match found when searching file contents with regex
    or string patterns.

    Examples:
        >>> match = SearchMatch(
        ...     file="/src/main.py",
        ...     line=42,
        ...     text="def process(data):",
        ...     column=0
        ... )
    """

    file: str
    line: int
    text: str
    column: Optional[int] = None
    match_start: Optional[int] = None
    match_end: Optional[int] = None


class ViewQuery(FileQuery):
    """Backward-compatible query model with content-search fields."""

    content_pattern: Optional[str] = Field(
        None,
        description="Simple string pattern to search for in file contents",
    )
    content_regex: Optional[str] = Field(
        None,
        description="Regex pattern to search for in file contents",
    )
    case_sensitive: bool = Field(
        default=True,
        description="Whether content search is case-sensitive",
    )
    whole_word: bool = Field(
        default=False,
        description="Match whole words only for content search",
    )
    max_matches_per_file: Optional[int] = Field(
        None,
        description="Limit matches per file (None = unlimited)",
    )


class View(BaseModel):
    """View of the AgentFS filesystem with query capabilities.

    A View represents a filtered/queried view of the filesystem based on
    a query specification. It provides methods to load matching files.

    Examples:
        >>> async with await AgentFS.open(AgentFSOptions(id="my-agent")) as agent:
        ...     view = View(agent=agent, query=ViewQuery(path_pattern="*.py"))
        ...     files = await view.load()
        ...     for file in files:
        ...         print(f"{file.path}: {file.stats.size} bytes")
    """

    model_config = {"arbitrary_types_allowed": True}

    agent: AgentFS = Field(description="AgentFS instance")
    query: ViewQuery = Field(default_factory=ViewQuery, description="Query specification")

    async def load(self) -> list[FileEntry]:
        """Load files matching the query specification.

        Returns:
            List of FileEntry objects matching the query

        Examples:
            >>> files = await view.load()
            >>> for file in files:
            ...     print(file.path)
        """
        manager = FileManager(self.agent)
        return await manager.query(self.query)

    async def filter(self, predicate: Callable[[FileEntry], bool]) -> list[FileEntry]:
        """Load and filter files using a custom predicate function.

        Args:
            predicate: Function that takes a FileEntry and returns bool

        Returns:
            List of FileEntry objects that match the predicate

        Examples:
            >>> # Get only files larger than 1KB
            >>> large_files = await view.filter(lambda f: f.stats.size > 1024)
        """
        entries = await self.load()
        return [e for e in entries if predicate(e)]

    async def count(self) -> int:
        """Count files matching the query without loading content.

        Returns:
            Number of matching files

        Examples:
            >>> count = await view.count()
            >>> print(f"Found {count} matching files")
        """
        manager = FileManager(self.agent)
        return await manager.count(self.query)

    def with_pattern(self, pattern: str) -> "View":
        """Create a new view with a different path pattern.

        Args:
            pattern: New glob pattern

        Returns:
            New View instance with updated pattern

        Examples:
            >>> python_files = view.with_pattern("*.py")
            >>> json_files = view.with_pattern("**/*.json")
        """
        new_query = self.query.model_copy(update={"path_pattern": pattern})
        return View(agent=self.agent, query=new_query)

    def with_content(self, include: bool = True) -> "View":
        """Create a new view with content loading enabled or disabled.

        Args:
            include: Whether to include file contents

        Returns:
            New View instance with updated content setting

        Examples:
            >>> view_with_content = view.with_content(True)
        """
        new_query = self.query.model_copy(update={"include_content": include})
        return View(agent=self.agent, query=new_query)

    async def search_content(self, *, streaming: bool = False, chunk_size: int = 65536) -> list[SearchMatch]:
        r"""Search file contents matching query patterns.

        Args:
            streaming: If True, search content via :meth:`FileManager.read_stream`.
                This avoids loading each full file into memory in one payload.
            chunk_size: Chunk size used when ``streaming=True``.

        Returns:
            List of SearchMatch objects

        Examples:
            >>> view = View(
            ...     agent=agent,
            ...     query=ViewQuery(
            ...         path_pattern="**/*.py",
            ...         content_regex=r"def\s+\w+\(.*\):"
            ...     )
            ... )
            >>> matches = await view.search_content(streaming=True)
            >>> for match in matches:
            ...     print(f"{match.file}:{match.line}: {match.text}")
        """
        if not self.query.content_pattern and not self.query.content_regex:
            raise ValueError("Either content_pattern or content_regex must be set")
        if streaming and chunk_size <= 0:
            raise ValueError("chunk_size must be greater than 0")

        matches: list[SearchMatch] = []

        # Compile regex pattern
        if self.query.content_regex:
            pattern = self.query.content_regex
        else:
            pattern = re.escape(self.query.content_pattern)
            if self.query.whole_word:
                pattern = r"\b" + pattern + r"\b"

        flags = 0 if self.query.case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)

        if streaming:
            manager = FileManager(self.agent)
            file_query = self.query.model_copy(update={"include_content": False})
            files = await manager.query(file_query)
            for file in files:
                file_matches = 0
                try:
                    line_num = 0
                    async for line in self._iter_text_lines(manager.read_stream(file.path, chunk_size=chunk_size)):
                        line_num += 1
                        for match in regex.finditer(line):
                            matches.append(
                                SearchMatch(
                                    file=file.path,
                                    line=line_num,
                                    text=line.strip(),
                                    column=match.start(),
                                    match_start=match.start(),
                                    match_end=match.end(),
                                )
                            )
                            file_matches += 1
                            if self.query.max_matches_per_file and file_matches >= self.query.max_matches_per_file:
                                break

                        if self.query.max_matches_per_file and file_matches >= self.query.max_matches_per_file:
                            break
                except UnicodeDecodeError:
                    continue
            return matches

        # Non-streaming path: load files with content
        original_include = self.query.include_content
        self.query.include_content = True

        try:
            files = await self.load()
        finally:
            self.query.include_content = original_include

        # Search each file
        for file in files:
            if not file.content:
                continue

            # Handle bytes or string content
            content = file.content
            if isinstance(content, bytes):
                try:
                    content = content.decode("utf-8")
                except UnicodeDecodeError:
                    continue  # Skip binary files

            lines = content.split("\n")
            file_matches = 0

            for line_num, line in enumerate(lines, start=1):
                for match in regex.finditer(line):
                    matches.append(
                        SearchMatch(
                            file=file.path,
                            line=line_num,
                            text=line.strip(),
                            column=match.start(),
                            match_start=match.start(),
                            match_end=match.end(),
                        )
                    )

                    file_matches += 1
                    if self.query.max_matches_per_file and file_matches >= self.query.max_matches_per_file:
                        break

                if self.query.max_matches_per_file and file_matches >= self.query.max_matches_per_file:
                    break

        return matches

    @staticmethod
    async def _iter_text_lines(chunks: AsyncIterator[bytes], encoding: str = "utf-8") -> AsyncIterator[str]:
        """Yield decoded text lines from a byte chunk stream."""
        decoder = codecs.getincrementaldecoder(encoding)()
        buffer = ""

        async for chunk in chunks:
            text = decoder.decode(chunk)
            buffer += text
            while True:
                new_line_index = buffer.find("\n")
                if new_line_index == -1:
                    break
                yield buffer[:new_line_index]
                buffer = buffer[new_line_index + 1 :]

        remaining = decoder.decode(b"", final=True)
        buffer += remaining
        if buffer:
            yield buffer

    async def files_containing(self, pattern: str, regex: bool = False) -> list[FileEntry]:
        """Get files that contain the specified pattern.

        Args:
            pattern: Pattern to search for
            regex: If True, treat pattern as regex

        Returns:
            List of FileEntry objects that contain the pattern

        Examples:
            >>> files = await view.files_containing("TODO")
            >>> print(f"Found {len(files)} files with TODOs")
        """
        query = self.query.model_copy(update={"content_regex" if regex else "content_pattern": pattern})
        search_view = View(agent=self.agent, query=query)
        matches = await search_view.search_content()

        # Get unique files
        file_paths = set(m.file for m in matches)

        # Load file entries
        return [f for f in await self.load() if f.path in file_paths]

    def with_size_range(self, min_size: Optional[int] = None, max_size: Optional[int] = None) -> "View":
        """Create view with size constraints.

        Args:
            min_size: Minimum file size in bytes
            max_size: Maximum file size in bytes

        Returns:
            New View instance with updated size constraints

        Examples:
            >>> # Files between 1KB and 1MB
            >>> view = view.with_size_range(1024, 1024*1024)
        """
        new_query = self.query.model_copy(update={"min_size": min_size, "max_size": max_size})
        return View(agent=self.agent, query=new_query)

    def with_regex(self, pattern: str) -> "View":
        r"""Create view with regex path filter.

        Args:
            pattern: Regex pattern for matching file paths

        Returns:
            New View instance with updated regex pattern

        Examples:
            >>> # Python files in src/ directory
            >>> view = view.with_regex(r"^src/.*\.py$")
        """
        new_query = self.query.model_copy(update={"regex_pattern": pattern})
        return View(agent=self.agent, query=new_query)

    async def recent_files(self, max_age: timedelta | float) -> list[FileEntry]:
        """Get files modified within time window.

        Args:
            max_age: Maximum age as timedelta or seconds

        Returns:
            List of files modified within the specified time window

        Examples:
            >>> # Files modified in last hour
            >>> recent = await view.recent_files(timedelta(hours=1))
        """
        if isinstance(max_age, timedelta):
            max_age = max_age.total_seconds()

        cutoff = datetime.now().timestamp() - max_age

        files = await self.load()
        return [f for f in files if f.stats and f.stats.mtime.timestamp() >= cutoff]

    async def largest_files(self, n: int = 10) -> list[FileEntry]:
        """Get N largest files.

        Args:
            n: Number of files to return

        Returns:
            List of the N largest files

        Examples:
            >>> # Top 10 largest files
            >>> large = await view.largest_files(10)
        """
        files = await self.load()
        files_with_size = [f for f in files if f.stats]
        files_with_size.sort(key=lambda f: f.stats.size, reverse=True)
        return files_with_size[:n]

    async def total_size(self) -> int:
        """Calculate total size of matching files.

        Returns:
            Total size in bytes of all matching files

        Examples:
            >>> # Total size of Python files
            >>> size = await view.with_pattern("*.py").total_size()
            >>> print(f"Total size: {size / 1024 / 1024:.2f} MB")
        """
        files = await self.load()
        return sum(f.stats.size for f in files if f.stats)

    async def group_by_extension(self) -> dict[str, list[FileEntry]]:
        """Group files by extension.

        Returns:
            Dictionary mapping extensions to lists of files

        Examples:
            >>> grouped = await view.group_by_extension()
            >>> print(f"Python files: {len(grouped.get('.py', []))}")
        """
        files = await self.load()
        groups: dict[str, list[FileEntry]] = {}

        for file in files:
            ext = Path(file.path).suffix or "(no extension)"
            if ext not in groups:
                groups[ext] = []
            groups[ext].append(file)

        return groups
