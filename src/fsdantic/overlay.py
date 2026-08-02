"""High-level operations for AgentFS overlay filesystems."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from agentfs_sdk import AgentFS, ErrnoException

from ._internal.errors import translate_agentfs_error
from ._internal.paths import join_normalized_path, normalize_path
from .exceptions import OverlayError, WorkspaceError

if TYPE_CHECKING:
    from .workspace import Workspace


class MergeStrategy(StrEnum):
    """Strategy for merging overlays.

    ``CALLBACK`` requires a conflict resolver; selecting it without one
    raises :class:`OverlayError` when a conflict is encountered.
    """

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
        conflict_resolver: ConflictResolver | None = None,
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
        strategy: MergeStrategy | None = None,
        conflict_resolver: ConflictResolver | None = None,
    ) -> MergeResult:
        """Merge source overlay into target filesystem.

        Args:
            source: Source overlay filesystem
            target: Target filesystem to merge into
            path: Root path to merge (default: "/")
            strategy: Override default merge strategy
            conflict_resolver: Optional resolver used when ``strategy`` is
                ``MergeStrategy.CALLBACK``.  Falls back to the resolver
                configured at construction.

        Returns:
            MergeResult with statistics

        Raises:
            OverlayError: when ``CALLBACK`` is selected and no resolver is
                available for a conflict.

        Examples:
            >>> # Merge agent overlay into stable
            >>> result = await ops.merge(agent_fs, stable_fs)
        """
        effective_strategy = strategy or self.strategy
        effective_resolver = conflict_resolver if conflict_resolver is not None else self.conflict_resolver

        stats = {"files_merged": 0}
        conflicts = []
        errors = []

        context = f"OverlayOperations.merge(path={path!r})"

        try:
            source_root_stat = await source.fs.stat(path)
        except ErrnoException as e:
            if e.code == "ENOENT":
                return MergeResult(files_merged=0, conflicts=conflicts, errors=errors)
            errors.append((path, str(translate_agentfs_error(e, context))))
            return MergeResult(files_merged=0, conflicts=conflicts, errors=errors)
        except (RuntimeError, TypeError, ValueError) as e:
            errors.append((path, str(e)))
            return MergeResult(files_merged=0, conflicts=conflicts, errors=errors)

        if source_root_stat.is_file():
            await self._merge_file(
                source, target, path, effective_strategy, stats, conflicts, errors, effective_resolver
            )
        elif source_root_stat.is_directory():
            # Recursively copy files from source to target
            await self._merge_recursive(
                source, target, path, effective_strategy, stats, conflicts, errors, effective_resolver
            )
        else:
            errors.append((path, "Path is not a file or directory"))

        return MergeResult(files_merged=stats["files_merged"], conflicts=conflicts, errors=errors)

    async def _merge_recursive(
        self,
        source: AgentFS,
        target: AgentFS,
        path: str,
        strategy: MergeStrategy,
        stats: dict,
        conflicts: list[MergeConflict],
        errors: list[tuple[str, str]],
        conflict_resolver: ConflictResolver | None = None,
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
            conflict_resolver: Optional resolver for CALLBACK strategy
        """
        context = f"OverlayOperations._merge_recursive(path={path!r})"

        try:
            entries = await source.fs.readdir(path)
        except ErrnoException as e:
            if e.code == "ENOENT":
                return
            errors.append((path, str(translate_agentfs_error(e, context))))
            return
        except (RuntimeError, TypeError, ValueError) as e:
            errors.append((path, str(e)))
            return

        for entry_name in entries:
            source_path = join_normalized_path(path, entry_name)

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
                            context = f"OverlayOperations._merge_recursive(path={source_path!r})"
                            raise translate_agentfs_error(e, context) from e
                        # Directory doesn't exist, create it
                        # Note: AgentFS mkdir creates parent dirs automatically
                        await target.fs.mkdir(source_path)

                    # Recurse
                    await self._merge_recursive(
                        source, target, source_path, strategy, stats, conflicts, errors, conflict_resolver
                    )
                    continue

                # Handle file
                if source_stat.is_file():
                    await self._merge_file(
                        source,
                        target,
                        source_path,
                        strategy,
                        stats,
                        conflicts,
                        errors,
                        conflict_resolver,
                    )

            except (RuntimeError, TypeError, ValueError) as e:
                errors.append((source_path, str(e)))

    async def _merge_file(
        self,
        source: AgentFS,
        target: AgentFS,
        source_path: str,
        strategy: MergeStrategy,
        stats: dict,
        conflicts: list[MergeConflict],
        errors: list[tuple[str, str]],
        conflict_resolver: ConflictResolver | None = None,
    ) -> None:
        """Merge a single file from source into target.

        Raises:
            OverlayError: when ``CALLBACK`` is selected and no resolver is
                available for the conflict.
        """
        try:
            source_content = await source.fs.read_file(source_path, encoding=None)

            # Check if file exists in target
            target_exists = False
            target_content = None
            try:
                target_content = await target.fs.read_file(source_path, encoding=None)
                target_exists = True
            except ErrnoException as e:
                if e.code != "ENOENT":
                    context = f"OverlayOperations._merge_file(path={source_path!r})"
                    raise translate_agentfs_error(e, context) from e

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
                    return
                if strategy == MergeStrategy.PRESERVE:
                    # Keep target version
                    conflicts.append(conflict)
                    return
                if strategy == MergeStrategy.CALLBACK:
                    if conflict_resolver is None:
                        raise OverlayError(
                            f"MergeStrategy.CALLBACK requires a conflict_resolver for conflict at '{source_path}'"
                        )
                    source_content = conflict_resolver.resolve(conflict)
                    conflicts.append(conflict)
                # OVERWRITE: use source_content (default)

            # Write to target using normalized absolute path (the SDK's
            # path normalization adds a leading slash anyway, so this is
            # self-consistent with the stat/read calls above).
            target_path = normalize_path(source_path)
            await target.fs.write_file(target_path, source_content)
            stats["files_merged"] += 1
        except ErrnoException as e:
            context = f"OverlayOperations._merge_file(path={source_path!r})"
            errors.append((source_path, str(translate_agentfs_error(e, context))))
        except (RuntimeError, TypeError, ValueError) as e:
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
                    full_path = join_normalized_path(current_path, entry_name)

                    try:
                        stat = await overlay.fs.stat(full_path)

                        if stat.is_directory():
                            await walk(full_path)
                        else:
                            files.append(full_path)
                    except ErrnoException as e:
                        if e.code != "ENOENT":
                            context = f"OverlayOperations.list_changes(path={full_path!r})"
                            raise translate_agentfs_error(e, context) from e
                        pass
            except ErrnoException as e:
                if e.code == "ENOENT":
                    pass
                else:
                    context = f"OverlayOperations.list_changes(path={current_path!r})"
                    raise translate_agentfs_error(e, context) from e

        await walk(path)
        return files

    async def reset_overlay(self, overlay: AgentFS, paths: list[str] | None = None) -> int:
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
        errors: list[tuple[str, str]] = []
        for path in paths:
            normalized_path = normalize_path(path)
            try:
                stat = await overlay.fs.stat(normalized_path)

                if stat.is_directory():
                    await overlay.fs.rm(normalized_path, recursive=True)
                else:
                    await overlay.fs.unlink(normalized_path)

                removed += 1
            except ErrnoException as e:
                if e.code == "ENOENT":
                    continue
                context = f"OverlayOperations.reset_overlay(path={path!r})"
                errors.append((path, str(translate_agentfs_error(e, context))))
            except (RuntimeError, TypeError, ValueError) as e:
                errors.append((path, str(e)))

        if errors:
            error_summary = "; ".join(f"{error_path}: {error_message}" for error_path, error_message in errors)
            raise OverlayError(
                f"Failed to reset {len(errors)} overlay path(s): {error_summary}",
                context={"failed": errors},
            )

        return removed


class OverlayManager:
    """Workspace-facing overlay API backed by :class:`OverlayOperations`."""

    def __init__(
        self,
        agent_fs: AgentFS,
        operations: OverlayOperations | None = None,
        readonly: bool = False,
    ):
        """Initialize the overlay manager.

        Args:
            agent_fs: Backing AgentFS instance (merge/reset target).
            operations: Optional :class:`OverlayOperations` backend.
            readonly: When True, write methods (``merge``/``reset``) raise
                ``WorkspaceError`` with ``code="WORKSPACE_READONLY"``.
        """
        self._agent_fs = agent_fs
        self._operations = operations or OverlayOperations()
        self._readonly = readonly

    @property
    def readonly(self) -> bool:
        """True when this manager enforces read-only mode."""
        return self._readonly

    @staticmethod
    def _resolve_agentfs(source: AgentFS | Workspace) -> AgentFS:
        """Resolve either Workspace or raw AgentFS into AgentFS."""
        raw = getattr(source, "raw", source)
        return raw

    def _ensure_writable(self, context: str) -> None:
        """Raise ``WorkspaceError(WORKSPACE_READONLY)`` on read-only managers.

        The connection guard is the primary enforcement; this check provides
        early, clear errors at the API boundary before any SDK work begins.
        """
        if self._readonly:
            raise WorkspaceError(
                f"{context}: workspace is read-only",
                code="WORKSPACE_READONLY",
            )

    async def merge(
        self,
        source: AgentFS | Workspace,
        path: str = "/",
        strategy: MergeStrategy | None = None,
        conflict_resolver: ConflictResolver | None = None,
    ) -> MergeResult:
        """Merge ``source`` into this workspace's backing filesystem.

        ``conflict_resolver`` is forwarded to the underlying
        :class:`OverlayOperations` call (required for
        ``MergeStrategy.CALLBACK``).

        Raises:
            WorkspaceError: with ``code="WORKSPACE_READONLY"`` when this
                manager belongs to a read-only workspace.
        """
        self._ensure_writable("OverlayManager.merge")
        source_fs = self._resolve_agentfs(source)
        return await self._operations.merge(
            source=source_fs,
            target=self._agent_fs,
            path=path,
            strategy=strategy,
            conflict_resolver=conflict_resolver,
        )

    async def list_changes(self, path: str = "/") -> list[str]:
        """List changed files currently present in this workspace overlay."""
        return await self._operations.list_changes(self._agent_fs, path=path)

    async def reset(self, paths: list[str] | None = None) -> int:
        """Reset selected paths (or all paths) in this workspace overlay.

        Raises:
            WorkspaceError: with ``code="WORKSPACE_READONLY"`` when this
                manager belongs to a read-only workspace.
        """
        self._ensure_writable("OverlayManager.reset")
        return await self._operations.reset_overlay(self._agent_fs, paths=paths)
