"""High-level operations for AgentFS overlay filesystems."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Protocol

from agentfs_sdk import AgentFS, ErrnoException


class MergeStrategy(str, Enum):
    """Strategy for merging overlays."""

    OVERWRITE = "overwrite"  # Overlay wins on conflicts
    PRESERVE = "preserve"  # Base wins on conflicts
    ERROR = "error"  # Raise on conflicts
    CALLBACK = "callback"  # Use callback for conflicts


@dataclass
class MergeConflict:
    """Represents a merge conflict.

    Attributes:
        path: File path where conflict occurred
        overlay_size: Size of file in overlay
        base_size: Size of file in base
        overlay_content: File content from overlay
        base_content: File content from base
    """

    path: str
    overlay_size: int
    base_size: int
    overlay_content: bytes
    base_content: bytes


@dataclass
class MergeResult:
    """Result of merge operation.

    Attributes:
        files_merged: Number of files merged
        conflicts: List of conflicts encountered
        errors: List of errors (path, error_message)
    """

    files_merged: int
    conflicts: list[MergeConflict]
    errors: list[tuple[str, str]]


class ConflictResolver(Protocol):
    """Protocol for custom conflict resolution."""

    def resolve(self, conflict: MergeConflict) -> bytes:
        """Resolve a conflict and return content to use."""
        ...


class OverlayOperations:
    """High-level operations on AgentFS overlay filesystems.

    Provides utilities for merging overlays, listing changes, and
    resetting overlays to base state.

    Examples:
        >>> ops = OverlayOperations()
        >>> result = await ops.merge(
        ...     source=agent_fs,
        ...     target=stable_fs,
        ...     strategy=MergeStrategy.OVERWRITE
        ... )
        >>> print(f"Merged {result.files_merged} files")
    """

    def __init__(
        self,
        strategy: MergeStrategy = MergeStrategy.OVERWRITE,
        conflict_resolver: Optional[ConflictResolver] = None,
    ):
        """Initialize overlay operations.

        Args:
            strategy: Default merge strategy
            conflict_resolver: Optional custom conflict resolver
        """
        self.strategy = strategy
        self.conflict_resolver = conflict_resolver

    async def merge(
        self,
        source: AgentFS,
        target: AgentFS,
        path: str = "/",
        strategy: Optional[MergeStrategy] = None,
    ) -> MergeResult:
        """Merge source overlay into target filesystem.

        Args:
            source: Source overlay filesystem
            target: Target filesystem to merge into
            path: Root path to merge (default: "/")
            strategy: Override default merge strategy

        Returns:
            MergeResult with statistics

        Examples:
            >>> # Merge agent overlay into stable
            >>> result = await ops.merge(agent_fs, stable_fs)
        """
        effective_strategy = strategy or self.strategy

        stats = {"files_merged": 0}
        conflicts = []
        errors = []

        # Recursively copy files from source to target
        await self._merge_recursive(
            source, target, path, effective_strategy, stats, conflicts, errors
        )

        return MergeResult(
            files_merged=stats["files_merged"], conflicts=conflicts, errors=errors
        )

    async def _merge_recursive(
        self,
        source: AgentFS,
        target: AgentFS,
        path: str,
        strategy: MergeStrategy,
        stats: dict,
        conflicts: list[MergeConflict],
        errors: list[tuple[str, str]],
    ) -> None:
        """Recursively merge directory contents.

        Args:
            source: Source filesystem
            target: Target filesystem
            path: Current path being merged
            strategy: Merge strategy
            stats: Stats dictionary to update
            conflicts: List to append conflicts to
            errors: List to append errors to
        """
        try:
            entries = await source.fs.readdir(path)
        except ErrnoException as e:
            if e.code == "ENOENT":
                return
            errors.append((path, str(e)))
            return
        except Exception as e:
            errors.append((path, str(e)))
            return

        for entry_name in entries:
            source_path = f"{path.rstrip('/')}/{entry_name}"

            try:
                # Get source stats
                source_stat = await source.fs.stat(source_path)

                # Check if directory
                if source_stat.is_directory():
                    # Ensure directory exists in target
                    try:
                        await target.fs.stat(source_path)
                    except ErrnoException as e:
                        if e.code != "ENOENT":
                            raise
                        # Directory doesn't exist, create it
                        # Note: AgentFS mkdir creates parent dirs automatically
                        await target.fs.mkdir(source_path.lstrip("/"))

                    # Recurse
                    await self._merge_recursive(
                        source, target, source_path, strategy, stats, conflicts, errors
                    )
                    continue

                # Handle file
                if source_stat.is_file():
                    source_content = await source.fs.read_file(source_path)

                    # Check if file exists in target
                    target_exists = False
                    target_content = None
                    try:
                        target_content = await target.fs.read_file(source_path)
                        target_exists = True
                    except ErrnoException as e:
                        if e.code != "ENOENT":
                            raise
                        pass

                    # Handle conflict
                    if target_exists and source_content != target_content:
                        conflict = MergeConflict(
                            path=source_path,
                            overlay_size=len(source_content),
                            base_size=len(target_content) if target_content else 0,
                            overlay_content=source_content,
                            base_content=target_content or b"",
                        )

                        if strategy == MergeStrategy.ERROR:
                            errors.append((source_path, "Conflict detected"))
                            continue
                        elif strategy == MergeStrategy.PRESERVE:
                            # Keep target version
                            conflicts.append(conflict)
                            continue
                        elif strategy == MergeStrategy.CALLBACK:
                            if self.conflict_resolver:
                                source_content = self.conflict_resolver.resolve(conflict)
                            conflicts.append(conflict)
                        # OVERWRITE: use source_content (default)

                    # Write to target
                    # Use relative path (strip leading /)
                    target_path = source_path.lstrip("/")
                    await target.fs.write_file(target_path, source_content)
                    stats["files_merged"] += 1

            except Exception as e:
                errors.append((source_path, str(e)))

    async def list_changes(self, overlay: AgentFS, path: str = "/") -> list[str]:
        """List files that exist in overlay at path.

        This returns files that have been written to the overlay,
        which may include modifications to base files.

        Args:
            overlay: Overlay filesystem
            path: Root path to check

        Returns:
            List of file paths in overlay

        Examples:
            >>> changes = await ops.list_changes(agent_fs)
            >>> print(f"Found {len(changes)} changed files")
        """
        files = []

        async def walk(current_path: str):
            try:
                entries = await overlay.fs.readdir(current_path)
                for entry_name in entries:
                    full_path = f"{current_path.rstrip('/')}/{entry_name}"

                    try:
                        stat = await overlay.fs.stat(full_path)

                        if stat.is_directory():
                            await walk(full_path)
                        else:
                            files.append(full_path)
                    except ErrnoException as e:
                        if e.code != "ENOENT":
                            raise
                        pass
            except ErrnoException as e:
                if e.code == "ENOENT":
                    pass
                else:
                    raise

        await walk(path)
        return files

    async def reset_overlay(
        self, overlay: AgentFS, paths: Optional[list[str]] = None
    ) -> int:
        """Remove files from overlay (reset to base state).

        Args:
            overlay: Overlay filesystem
            paths: Specific paths to reset (None = reset all)

        Returns:
            Number of files removed

        Examples:
            >>> # Reset specific file
            >>> await ops.reset_overlay(agent_fs, ["/data/temp.txt"])
            >>>
            >>> # Reset all overlay changes
            >>> await ops.reset_overlay(agent_fs)
        """
        if paths is None:
            # Get all overlay files
            paths = await self.list_changes(overlay)

        removed = 0
        for path in paths:
            try:
                await overlay.fs.remove(path.lstrip("/"))
                removed += 1
            except Exception:
                pass

        return removed
