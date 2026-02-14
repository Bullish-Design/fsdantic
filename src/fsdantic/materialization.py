"""Workspace materialization for AgentFS overlays."""

import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from agentfs_sdk import AgentFS, ErrnoException

from .view import ViewQuery


class ConflictResolution(str, Enum):
    """Strategy for handling file conflicts during materialization."""

    OVERWRITE = "overwrite"  # Overlay wins
    SKIP = "skip"  # Keep existing file
    ERROR = "error"  # Raise exception


@dataclass
class FileChange:
    """Represents a change between base and overlay.

    Attributes:
        path: File path
        change_type: Type of change ("added", "modified", "deleted")
        old_size: Previous file size (for modifications)
        new_size: New file size (for additions/modifications)
    """

    path: str
    change_type: str  # "added", "modified", "deleted"
    old_size: Optional[int] = None
    new_size: Optional[int] = None


@dataclass
class MaterializationResult:
    """Result of materialization operation.

    Attributes:
        target_path: Path where files were materialized
        files_written: Number of files written
        bytes_written: Total bytes written
        changes: List of file changes detected
        skipped: List of files skipped
        errors: List of errors encountered (path, error_message)
    """

    target_path: Path
    files_written: int
    bytes_written: int
    changes: list[FileChange]
    skipped: list[str]
    errors: list[tuple[str, str]]  # (path, error_message)


class Materializer:
    """Materialize AgentFS overlays to local filesystem.

    Provides functionality to copy files from AgentFS virtual filesystem
    to the local disk, with conflict resolution and progress tracking.

    Examples:
        >>> materializer = Materializer()
        >>> result = await materializer.materialize(
        ...     agent_fs=agent,
        ...     target_path=Path("./workspace"),
        ...     base_fs=stable
        ... )
        >>> print(f"Written {result.files_written} files")
    """

    def __init__(
        self,
        conflict_resolution: ConflictResolution = ConflictResolution.OVERWRITE,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ):
        """Initialize materializer.

        Args:
            conflict_resolution: How to handle existing files
            progress_callback: Optional callback(path, current, total)
        """
        self.conflict_resolution = conflict_resolution
        self.progress_callback = progress_callback

    async def materialize(
        self,
        agent_fs: AgentFS,
        target_path: Path,
        base_fs: Optional[AgentFS] = None,
        filters: Optional[ViewQuery] = None,
        clean: bool = True,
    ) -> MaterializationResult:
        """Materialize AgentFS contents to disk.

        Args:
            agent_fs: AgentFS overlay to materialize
            target_path: Local filesystem destination
            base_fs: Optional base layer to materialize first
            filters: Optional ViewQuery to filter files
            clean: If True, remove target_path contents first

        Returns:
            MaterializationResult with statistics

        Examples:
            >>> result = await materializer.materialize(
            ...     agent_fs=agent,
            ...     target_path=Path("./output")
            ... )
        """
        if clean and target_path.exists():
            shutil.rmtree(target_path)

        target_path.mkdir(parents=True, exist_ok=True)

        stats = {
            "files_written": 0,
            "bytes_written": 0,
        }
        changes = []
        skipped = []
        errors = []

        # Materialize base layer first if provided
        if base_fs is not None:
            await self._copy_recursive(
                base_fs, "/", target_path, stats, changes, skipped, errors
            )

        # Materialize overlay layer
        await self._copy_recursive(
            agent_fs, "/", target_path, stats, changes, skipped, errors, filters=filters
        )

        return MaterializationResult(
            target_path=target_path,
            files_written=stats["files_written"],
            bytes_written=stats["bytes_written"],
            changes=changes,
            skipped=skipped,
            errors=errors,
        )

    async def diff(
        self, overlay_fs: AgentFS, base_fs: AgentFS, path: str = "/"
    ) -> list[FileChange]:
        """Compute changes between overlay and base.

        Args:
            overlay_fs: Overlay filesystem
            base_fs: Base filesystem
            path: Root path to compare

        Returns:
            List of FileChange objects

        Examples:
            >>> changes = await materializer.diff(agent_fs, stable_fs)
            >>> for change in changes:
            ...     print(f"{change.change_type}: {change.path}")
        """
        changes = []

        # Get all files from both layers
        overlay_files = await self._list_all_files(overlay_fs, path)
        base_files = await self._list_all_files(base_fs, path)

        overlay_set = set(overlay_files.keys())
        base_set = set(base_files.keys())

        # Added files
        for file_path in overlay_set - base_set:
            changes.append(
                FileChange(
                    path=file_path, change_type="added", new_size=overlay_files[file_path]
                )
            )

        # Modified files
        for file_path in overlay_set & base_set:
            overlay_size = overlay_files[file_path]
            base_size = base_files[file_path]

            if overlay_size != base_size:
                changes.append(
                    FileChange(
                        path=file_path,
                        change_type="modified",
                        old_size=base_size,
                        new_size=overlay_size,
                    )
                )
            else:
                # Size same, check content
                try:
                    overlay_content = await overlay_fs.fs.read_file(file_path, encoding=None)
                    base_content = await base_fs.fs.read_file(file_path, encoding=None)

                    if overlay_content != base_content:
                        changes.append(
                            FileChange(
                                path=file_path,
                                change_type="modified",
                                old_size=base_size,
                                new_size=overlay_size,
                            )
                        )
                except ErrnoException as e:
                    # If files disappear during diff, skip only missing files
                    if e.code != "ENOENT":
                        raise

        return changes

    async def _copy_recursive(
        self,
        source_fs: AgentFS,
        src_path: str,
        dest_path: Path,
        stats: dict,
        changes: list[FileChange],
        skipped: list[str],
        errors: list[tuple[str, str]],
        filters: Optional[ViewQuery] = None,
    ) -> None:
        """Recursively copy files from AgentFS to disk.

        Args:
            source_fs: Source AgentFS filesystem
            src_path: Source path in AgentFS
            dest_path: Destination path on disk
            stats: Stats dictionary to update
            changes: List to append changes to
            skipped: List to append skipped files to
            errors: List to append errors to
            filters: Optional filters to apply
        """
        try:
            entries = await source_fs.fs.readdir(src_path)
        except ErrnoException as e:
            if e.code == "ENOENT":
                return
            errors.append((src_path, str(e)))
            return
        except Exception as e:
            errors.append((src_path, str(e)))
            return

        for entry_name in entries:
            entry_path = f"{src_path.rstrip('/')}/{entry_name}"

            try:
                # Get stats
                stat = await source_fs.fs.stat(entry_path)

                if stat.is_directory():
                    # Create directory and recurse
                    local_dir = dest_path / entry_name
                    local_dir.mkdir(exist_ok=True)
                    await self._copy_recursive(
                        source_fs,
                        entry_path,
                        local_dir,
                        stats,
                        changes,
                        skipped,
                        errors,
                        filters,
                    )
                elif stat.is_file():
                    # Copy file
                    local_file = dest_path / entry_name

                    # Check if file exists and handle conflict
                    if local_file.exists():
                        if self.conflict_resolution == ConflictResolution.SKIP:
                            skipped.append(entry_path)
                            continue
                        elif self.conflict_resolution == ConflictResolution.ERROR:
                            errors.append((entry_path, "File already exists"))
                            continue

                    # Read content
                    content = await source_fs.fs.read_file(entry_path, encoding=None)

                    # Write to disk
                    local_file.write_bytes(content)

                    # Update stats
                    stats["files_written"] += 1
                    stats["bytes_written"] += len(content)

                    # Track change
                    changes.append(
                        FileChange(path=entry_path, change_type="added", new_size=len(content))
                    )

                    # Progress callback
                    if self.progress_callback:
                        self.progress_callback(entry_path, stats["files_written"], -1)

            except Exception as e:
                errors.append((entry_path, str(e)))

    async def _list_all_files(self, fs: AgentFS, path: str) -> dict[str, int]:
        """Get all files with their sizes.

        Args:
            fs: AgentFS filesystem
            path: Root path to start from

        Returns:
            Dictionary mapping file paths to their sizes
        """
        files = {}

        async def walk(current_path: str):
            try:
                entries = await fs.fs.readdir(current_path)
                for entry_name in entries:
                    entry_path = f"{current_path.rstrip('/')}/{entry_name}"

                    try:
                        stat = await fs.fs.stat(entry_path)

                        if stat.is_directory():
                            await walk(entry_path)
                        else:
                            files[entry_path] = stat.size
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

        await walk(path)
        return files
