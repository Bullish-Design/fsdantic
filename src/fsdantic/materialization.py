"""Workspace materialization for AgentFS overlays."""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from enum import Enum
from errno import EXDEV
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from agentfs_sdk import AgentFS, ErrnoException

from ._internal.errors import translate_agentfs_error
from ._internal.streaming import compare_streams, hash_stream
from .files import FileManager
from .view import ViewQuery

if TYPE_CHECKING:
    from .workspace import Workspace


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
class FileFingerprint:
    """Lightweight metadata snapshot for diff pre-checks."""

    size: int
    mtime_ns: Optional[int] = None


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
        allow_root: Optional[Path] = None,
    ):
        """Initialize materializer.

        Args:
            conflict_resolution: How to handle existing files
            progress_callback: Optional callback(path, current, total)
            allow_root: Optional directory boundary that materialization targets
                must be inside. If None, each target's parent is used.
        """
        self.conflict_resolution = conflict_resolution
        self.progress_callback = progress_callback
        self.allow_root = allow_root

    async def materialize(
        self,
        agent_fs: AgentFS,
        target_path: Path,
        base_fs: Optional[AgentFS] = None,
        filters: Optional[ViewQuery] = None,
        clean: bool = True,
        allow_root: Optional[Path] = None,
    ) -> MaterializationResult:
        """Materialize AgentFS contents to disk.

        Args:
            agent_fs: AgentFS overlay to materialize
            target_path: Local filesystem destination
            base_fs: Optional base layer to materialize first
            filters: Optional ViewQuery to filter files
            clean: If True, remove target_path contents first
            allow_root: Optional directory boundary override for this run

        Returns:
            MaterializationResult with statistics

        Examples:
            >>> result = await materializer.materialize(
            ...     agent_fs=agent,
            ...     target_path=Path("./output")
            ... )
        """
        target_path, _ = self._validate_target_path(
            target_path=target_path,
            allow_root=allow_root or self.allow_root,
        )
        staging_path = target_path.parent / f"{target_path.name}.tmp-{uuid.uuid4().hex}"

        stats = {
            "files_written": 0,
            "bytes_written": 0,
        }
        changes = []
        skipped = []
        errors = []

        try:
            if staging_path.exists():
                shutil.rmtree(staging_path)
            staging_path.mkdir(parents=True, exist_ok=False)

            # Preserve existing files in no-clean mode by seeding the staging tree.
            if not clean and target_path.exists():
                shutil.copytree(target_path, staging_path, dirs_exist_ok=True)

            # Materialize base layer first if provided
            if base_fs is not None:
                await self._copy_recursive(base_fs, "/", staging_path, stats, changes, skipped, errors)

            # Materialize overlay layer
            await self._copy_recursive(agent_fs, "/", staging_path, stats, changes, skipped, errors, filters=filters)

            if not errors:
                self._swap_staging_to_target(staging_path=staging_path, target_path=target_path)
        except (OSError, ValueError) as e:
            errors.append((str(target_path), str(e)))
        finally:
            self._safe_cleanup(staging_path, errors)

        return MaterializationResult(
            target_path=target_path,
            files_written=stats["files_written"],
            bytes_written=stats["bytes_written"],
            changes=changes,
            skipped=skipped,
            errors=errors,
        )

    def _validate_target_path(self, target_path: Path, allow_root: Optional[Path]) -> tuple[Path, Path]:
        """Validate target path and allowed boundary for safe materialization."""
        resolved_target = target_path.expanduser().resolve(strict=False)
        boundary = (allow_root or resolved_target.parent).expanduser().resolve(strict=False)

        if resolved_target == resolved_target.parent:
            raise ValueError(f"Refusing to materialize to filesystem root: {resolved_target}")
        if boundary == boundary.parent:
            raise ValueError(f"Refusing to use filesystem root as allow_root boundary: {boundary}")
        try:
            resolved_target.relative_to(boundary)
        except ValueError as e:
            raise ValueError(
                f"Target path {resolved_target} must be inside allow_root boundary {boundary}"
            ) from e

        return resolved_target, boundary

    def _swap_staging_to_target(self, staging_path: Path, target_path: Path) -> None:
        """Promote staged output to final target.

        On the same filesystem, rename operations are atomic per operation. The
        promotion uses rename-based swap first; if rename is unsupported (for
        example cross-device `EXDEV`), it falls back to a non-atomic copy/move.
        """
        backup_path = target_path.parent / f"{target_path.name}.bak-{uuid.uuid4().hex}"
        target_exists = target_path.exists()

        if not target_exists:
            staging_path.rename(target_path)
            return

        try:
            target_path.rename(backup_path)
            try:
                staging_path.rename(target_path)
            except OSError:
                backup_path.rename(target_path)
                raise
            self._safe_cleanup(backup_path, [])
        except OSError as e:
            if e.errno != EXDEV:
                raise
            # Cross-device rename fallback: not atomic.
            if target_path.exists():
                shutil.rmtree(target_path)
            shutil.move(str(staging_path), str(target_path))

    def _safe_cleanup(self, path: Path, errors: list[tuple[str, str]]) -> None:
        """Best-effort cleanup for staging/backup paths with error tracking."""
        if not path.exists():
            return

        try:
            shutil.rmtree(path)
        except OSError as e:
            errors.append((str(path), f"cleanup_failed: {e}"))

    async def diff(self, overlay_fs: AgentFS, base_fs: AgentFS, path: str = "/") -> list[FileChange]:
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
        overlay_manager = FileManager(overlay_fs)
        base_manager = FileManager(base_fs)

        # Get all files from both layers
        overlay_files = await self._list_all_files(overlay_fs, path)
        base_files = await self._list_all_files(base_fs, path)

        overlay_set = set(overlay_files.keys())
        base_set = set(base_files.keys())

        # Added files
        for file_path in overlay_set - base_set:
            changes.append(FileChange(path=file_path, change_type="added", new_size=overlay_files[file_path].size))

        # Modified files
        for file_path in overlay_set & base_set:
            overlay_meta = overlay_files[file_path]
            base_meta = base_files[file_path]

            if overlay_meta.size != base_meta.size:
                changes.append(
                    FileChange(
                        path=file_path,
                        change_type="modified",
                        old_size=base_meta.size,
                        new_size=overlay_meta.size,
                    )
                )
                continue

            # Hash-first comparison, then byte-accurate fallback on mismatch.
            try:
                overlay_hash = await hash_stream(overlay_manager.read_stream(file_path))
                base_hash = await hash_stream(base_manager.read_stream(file_path))

                if overlay_hash != base_hash:
                    is_equal = await compare_streams(
                        overlay_manager.read_stream(file_path),
                        base_manager.read_stream(file_path),
                    )
                    if not is_equal:
                        changes.append(
                            FileChange(
                                path=file_path,
                                change_type="modified",
                                old_size=base_meta.size,
                                new_size=overlay_meta.size,
                            )
                        )
            except ErrnoException as e:
                # If files disappear during diff, skip only missing files
                if e.code != "ENOENT":
                    context = f"Materializer.diff(path={file_path!r})"
                    raise translate_agentfs_error(e, context) from e

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
        context = f"Materializer._copy_recursive(src_path={src_path!r})"

        try:
            entries = await source_fs.fs.readdir(src_path)
        except ErrnoException as e:
            if e.code == "ENOENT":
                return
            errors.append((src_path, str(translate_agentfs_error(e, context))))
            return
        except OSError as e:
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
                    changes.append(FileChange(path=entry_path, change_type="added", new_size=len(content)))

                    # Progress callback
                    if self.progress_callback:
                        self.progress_callback(entry_path, stats["files_written"], -1)

            except ErrnoException as e:
                context = f"Materializer._copy_recursive(entry_path={entry_path!r})"
                errors.append((entry_path, str(translate_agentfs_error(e, context))))
            except OSError as e:
                errors.append((entry_path, str(e)))

    async def _list_all_files(self, fs: AgentFS, path: str) -> dict[str, FileFingerprint]:
        """Get all files with lightweight metadata for diff checks.

        Args:
            fs: AgentFS filesystem
            path: Root path to start from

        Returns:
            Dictionary mapping file paths to metadata fingerprints
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
                            mtime_ns = getattr(stat, "mtime_ns", None)
                            if mtime_ns is None:
                                mtime = getattr(stat, "mtime", None)
                                mtime_ns = int(mtime * 1_000_000_000) if isinstance(mtime, (int, float)) else None
                            files[entry_path] = FileFingerprint(size=stat.size, mtime_ns=mtime_ns)
                    except ErrnoException as e:
                        if e.code == "ENOENT":
                            pass
                        else:
                            context = f"Materializer._list_all_files(path={entry_path!r})"
                            raise translate_agentfs_error(e, context) from e
            except ErrnoException as e:
                if e.code == "ENOENT":
                    pass
                else:
                    context = f"Materializer._list_all_files(path={current_path!r})"
                    raise translate_agentfs_error(e, context) from e

        await walk(path)
        return files


class MaterializationManager:
    """Workspace-facing materialization API backed by :class:`Materializer`."""

    def __init__(self, agent_fs: AgentFS, materializer: Optional[Materializer] = None):
        self._agent_fs = agent_fs
        self._materializer = materializer or Materializer()

    @staticmethod
    def _resolve_agentfs(source: AgentFS | "Workspace") -> AgentFS:
        """Resolve either Workspace or raw AgentFS into AgentFS."""
        raw = getattr(source, "raw", source)
        return raw

    async def to_disk(
        self,
        target_path: Path,
        *,
        base: AgentFS | "Workspace" | None = None,
        filters: Optional[ViewQuery] = None,
        clean: bool = True,
        allow_root: Optional[Path] = None,
    ) -> MaterializationResult:
        """Materialize this workspace to disk, optionally layering a base workspace."""
        base_fs = self._resolve_agentfs(base) if base is not None else None
        return await self._materializer.materialize(
            agent_fs=self._agent_fs,
            target_path=target_path,
            base_fs=base_fs,
            filters=filters,
            clean=clean,
            allow_root=allow_root,
        )

    async def diff(
        self,
        base: AgentFS | "Workspace",
        *,
        path: str = "/",
    ) -> list[FileChange]:
        """Diff this workspace against ``base`` within ``path``."""
        base_fs = self._resolve_agentfs(base)
        return await self._materializer.diff(
            overlay_fs=self._agent_fs,
            base_fs=base_fs,
            path=path,
        )

    async def preview(
        self,
        base: AgentFS | "Workspace",
        *,
        path: str = "/",
    ) -> list[FileChange]:
        """Preview materialization changes (alias of :meth:`diff`)."""
        return await self.diff(base=base, path=path)
