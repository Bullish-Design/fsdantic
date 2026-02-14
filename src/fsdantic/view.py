"""View interface for querying AgentFS filesystem."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from agentfs_sdk import AgentFS
from pydantic import BaseModel, Field, PrivateAttr, model_validator

from .models import FileEntry, FileStats


logger = logging.getLogger(__name__)


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


class ViewQuery(BaseModel):
    """Query specification for filesystem views.

    Examples:
        >>> query = ViewQuery(
        ...     path_pattern="*.py",
        ...     recursive=True,
        ...     include_content=True
        ... )
    """

    path_pattern: str = Field(
        default="*",
        description="Glob pattern for matching file paths (e.g., '*.py', '/data/**/*.json')"
    )
    recursive: bool = Field(
        default=True,
        description="Whether to search recursively in subdirectories"
    )
    include_content: bool = Field(
        default=False,
        description="Whether to load file contents"
    )
    include_stats: bool = Field(
        default=True,
        description="Whether to include file statistics"
    )
    regex_pattern: Optional[str] = Field(
        None,
        description="Optional regex pattern for more complex matching"
    )
    max_size: Optional[int] = Field(
        None,
        ge=0,
        description="Maximum file size in bytes (files larger than this are excluded)"
    )
    min_size: Optional[int] = Field(
        None,
        ge=0,
        description="Minimum file size in bytes (files smaller than this are excluded)"
    )
    content_pattern: Optional[str] = Field(
        None,
        description="Simple string pattern to search for in file contents"
    )
    content_regex: Optional[str] = Field(
        None,
        description="Regex pattern to search for in file contents"
    )
    case_sensitive: bool = Field(
        default=True,
        description="Whether content search is case-sensitive"
    )
    whole_word: bool = Field(
        default=False,
        description="Match whole words only for content search"
    )
    max_matches_per_file: Optional[int] = Field(
        None,
        description="Limit matches per file (None = unlimited)"
    )

    _normalized_path_pattern: str = PrivateAttr(default="*")
    _path_matcher: re.Pattern[str] = PrivateAttr(default_factory=lambda: re.compile(".*"))
    _regex_matcher: Optional[re.Pattern[str]] = PrivateAttr(default=None)

    @staticmethod
    def _normalize_path_pattern(pattern: str) -> str:
        """Normalize patterns so basename-only globs match across directories."""
        if "/" in pattern:
            return pattern
        return f"**/{pattern}"

    @staticmethod
    def _compile_glob_pattern(pattern: str) -> re.Pattern[str]:
        """Compile glob pattern to regex with explicit support for ** and path separators."""
        pieces: list[str] = ["^"]
        i = 0

        while i < len(pattern):
            # **/ matches zero or more directories
            if pattern[i:i + 3] == "**/":
                pieces.append("(?:.*/)?")
                i += 3
            elif pattern[i:i + 2] == "**":
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
    def _validate_and_prepare_matchers(self) -> "ViewQuery":
        if (
            self.min_size is not None
            and self.max_size is not None
            and self.min_size > self.max_size
        ):
            raise ValueError("min_size must be less than or equal to max_size")

        self._normalized_path_pattern = self._normalize_path_pattern(self.path_pattern)
        self._path_matcher = self._compile_glob_pattern(self._normalized_path_pattern)

        if self.regex_pattern:
            self._regex_matcher = re.compile(self.regex_pattern)
        else:
            self._regex_matcher = None

        return self

    def matches_path(self, path: str) -> bool:
        """Match a path against the prepared glob strategy."""
        return bool(self._path_matcher.match(path))

    def matches_regex(self, path: str) -> bool:
        """Match a path against optional regex filter."""
        if self._regex_matcher is None:
            return True
        return bool(self._regex_matcher.search(path))


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
    query: ViewQuery = Field(
        default_factory=ViewQuery,
        description="Query specification"
    )

    async def load(self) -> list[FileEntry]:
        """Load files matching the query specification.

        Returns:
            List of FileEntry objects matching the query

        Examples:
            >>> files = await view.load()
            >>> for file in files:
            ...     print(file.path)
        """
        entries: list[FileEntry] = []

        # Start from root
        await self._scan_directory("/", entries)

        # Apply size filters
        if self.query.max_size is not None or self.query.min_size is not None:
            entries = [
                e for e in entries
                if self._matches_size_filter(e)
            ]

        # Apply regex pattern if provided
        if self.query.regex_pattern:
            entries = [
                e for e in entries
                if self.query.matches_regex(e.path)
            ]

        return entries

    async def _scan_directory(
        self,
        path: str,
        entries: list[FileEntry]
    ) -> None:
        """Recursively scan a directory for matching files.

        Args:
            path: Directory path to scan
            entries: List to append matching entries to
        """
        try:
            # List directory contents
            items = await self.agent.fs.readdir(path)
        except FileNotFoundError:
            logger.debug("Directory disappeared during scan: %s", path)
            return

        for item in items:
            # Construct full path
            item_path = f"{path.rstrip('/')}/{item}"

            try:
                # Get file stats
                stats = await self.agent.fs.stat(item_path)
            except FileNotFoundError:
                logger.debug("Path disappeared before stat: %s", item_path)
                continue

            # Convert to our FileStats model
            file_stats = FileStats(
                size=stats.size,
                mtime=stats.mtime,
                is_file=stats.is_file(),
                is_directory=stats.is_directory(),
            )

            if file_stats.is_directory:
                # Recursively scan subdirectory if enabled
                if self.query.recursive:
                    await self._scan_directory(item_path, entries)
            elif file_stats.is_file:
                # Check if file matches pattern
                if self._matches_pattern(item_path):
                    # Load content if requested
                    content = None
                    if self.query.include_content:
                        try:
                            content = await self.agent.fs.read_file(item_path)
                        except FileNotFoundError:
                            logger.debug("Path disappeared before read: %s", item_path)
                        except Exception:
                            logger.exception("Failed reading file content for %s", item_path)

                    # Create entry
                    entry = FileEntry(
                        path=item_path,
                        stats=file_stats if self.query.include_stats else None,
                        content=content,
                    )
                    entries.append(entry)

    def _matches_pattern(self, path: str) -> bool:
        """Check if a path matches the query pattern.

        Args:
            path: File path to check

        Returns:
            True if path matches the pattern
        """
        return self.query.matches_path(path)

    def _matches_size_filter(self, entry: FileEntry) -> bool:
        """Check if an entry matches size filters.

        Args:
            entry: File entry to check

        Returns:
            True if entry matches size constraints
        """
        if not entry.stats:
            return True

        if self.query.min_size is not None:
            if entry.stats.size < self.query.min_size:
                return False

        if self.query.max_size is not None:
            if entry.stats.size > self.query.max_size:
                return False

        return True

    async def filter(
        self,
        predicate: Callable[[FileEntry], bool]
    ) -> list[FileEntry]:
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
        # Temporarily disable content loading for counting
        original_include_content = self.query.include_content
        self.query.include_content = False

        try:
            entries = await self.load()
            return len(entries)
        finally:
            # Restore original setting
            self.query.include_content = original_include_content

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

    async def search_content(self) -> list[SearchMatch]:
        r"""Search file contents matching query patterns.

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
            >>> matches = await view.search_content()
            >>> for match in matches:
            ...     print(f"{match.file}:{match.line}: {match.text}")
        """
        if not self.query.content_pattern and not self.query.content_regex:
            raise ValueError("Either content_pattern or content_regex must be set")

        # Load files with content
        original_include = self.query.include_content
        self.query.include_content = True

        try:
            files = await self.load()
        finally:
            self.query.include_content = original_include

        matches = []

        # Compile regex pattern
        if self.query.content_regex:
            pattern = self.query.content_regex
        else:
            pattern = re.escape(self.query.content_pattern)
            if self.query.whole_word:
                pattern = r"\b" + pattern + r"\b"

        flags = 0 if self.query.case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)

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
                    if (
                        self.query.max_matches_per_file
                        and file_matches >= self.query.max_matches_per_file
                    ):
                        break

                if (
                    self.query.max_matches_per_file
                    and file_matches >= self.query.max_matches_per_file
                ):
                    break

        return matches

    async def files_containing(
        self, pattern: str, regex: bool = False
    ) -> list[FileEntry]:
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
        query = self.query.model_copy(
            update={"content_regex" if regex else "content_pattern": pattern}
        )
        search_view = View(agent=self.agent, query=query)
        matches = await search_view.search_content()

        # Get unique files
        file_paths = set(m.file for m in matches)

        # Load file entries
        return [f for f in await self.load() if f.path in file_paths]

    def with_size_range(
        self, min_size: Optional[int] = None, max_size: Optional[int] = None
    ) -> "View":
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
        new_query = self.query.model_copy(
            update={"min_size": min_size, "max_size": max_size}
        )
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
        return [
            f
            for f in files
            if f.stats and f.stats.mtime.timestamp() >= cutoff
        ]

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
