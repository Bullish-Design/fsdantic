"""View interface for querying AgentFS filesystem."""

import re
from typing import Callable, Optional

from agentfs_sdk import AgentFS
from pydantic import BaseModel, Field, PrivateAttr, model_validator

from .models import FileEntry, FileStats


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

            for item in items:
                # Construct full path
                item_path = f"{path.rstrip('/')}/{item}"

                try:
                    # Get file stats
                    stats = await self.agent.fs.stat(item_path)

                    # Convert to our FileStats model
                    file_stats = FileStats(
                        size=stats.size,
                        mtime=stats.mtime,
                        is_file=stats.is_file(),
                        is_directory=stats.is_dir()
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
                                except Exception:
                                    # If content loading fails, continue without it
                                    content = None

                            # Create entry
                            entry = FileEntry(
                                path=item_path,
                                stats=file_stats if self.query.include_stats else None,
                                content=content
                            )
                            entries.append(entry)

                except Exception:
                    # Skip files that can't be accessed
                    continue

        except Exception:
            # Skip directories that can't be accessed
            pass

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
