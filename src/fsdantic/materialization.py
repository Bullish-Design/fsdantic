"""Workspace materialization for AgentFS overlays."""

from __future__ import annotations

import logging
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from errno import EXDEV
from pathlib import Path
from typing import TYPE_CHECKING

from agentfs_sdk import AgentFS, ErrnoException

from ._internal.errors import translate_agentfs_error
from ._internal.streaming import compare_streams
from .exceptions import WorkspaceError
from .files import FileManager
from .view import ViewQuery

if TYPE_CHECKING:
    from .workspace import Workspace

logger = logging.getLogger(__name__)


class ConflictResolution(StrEnum):
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
    old_size: int | None = None
    new_size: int | None = None


@dataclass
class FileFingerprint:
    """Lightweight metadata snapshot for diff pre-checks."""

    size: int
    mtime_ns: int | None = None


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
        progress_callback: Callable[[str, int, int], None] | None = None,
        allow_root: Path | None = None,
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
        base_fs: AgentFS | None = None,
        filters: ViewQuery | None = None,
        clean: bool = True,
        allow_root: Path | None = None,
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

        Filter semantics:
            - ``filters.path_pattern`` applies to files in both the base and
              overlay layers.  Directories are always descended into, so
              nested matches (e.g. ``src/**/*.py``) are found; empty
              directories may still be created for filtered-out subtrees.
            - Size constraints (``min_size``/``max_size``) are honored via
              the stat pass; regex/content filters are not applied.

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
        self._recover_orphaned_staging(target_path)
        staging_path = target_path.parent / f"{target_path.name}.tmp-{uuid.uuid4().hex}"

        stats = {
            "files_written": 0,
            "bytes_written": 0,
        }
        changes = []
        skipped = []
        errors = []

        # L14: pre-pass total for the progress callback (files that will be
        # copied after filter application).  Falls back to -1 when the tree
        # cannot be enumerated.
        progress_total = -1
        try:
            counted = 0
            if base_fs is not None:
                counted += await self._count_files_for_progress(base_fs, filters)
            counted += await self._count_files_for_progress(agent_fs, filters)
            progress_total = counted
        except ErrnoException:
            pass

        try:
            if staging_path.exists():
                shutil.rmtree(staging_path)
            staging_path.mkdir(parents=True, exist_ok=False)

            # Preserve existing files in no-clean mode by seeding the staging tree.
            if not clean and target_path.exists():
                shutil.copytree(target_path, staging_path, dirs_exist_ok=True)

            # Materialize base layer first if provided.  Filters apply to
            # both layers so a filtered-out base file is intentionally absent.
            if base_fs is not None:
                await self._copy_recursive(
                    base_fs,
                    "/",
                    staging_path,
                    stats,
                    changes,
                    skipped,
                    errors,
                    filters=filters,
                    progress_total=progress_total,
                )

            # Materialize overlay layer
            await self._copy_recursive(
                agent_fs,
                "/",
                staging_path,
                stats,
                changes,
                skipped,
                errors,
                filters=filters,
                progress_total=progress_total,
            )

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

    def _validate_target_path(self, target_path: Path, allow_root: Path | None) -> tuple[Path, Path]:
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
            raise ValueError(f"Target path {resolved_target} must be inside allow_root boundary {boundary}") from e

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

    def _recover_orphaned_staging(self, target_path: Path) -> None:
        """Recover or clean up orphaned staging/backup siblings (L13).

        A crash between ``target -> .bak-*`` and ``staging -> target`` strands
        the previous output as a ``.bak-*`` sibling.  This runs at the start
        of :meth:`materialize`:

        - If ``target_path`` is missing and exactly one ``.bak-*`` sibling
          exists (any age), restore it and log a warning.
        - Otherwise, remove stale ``.tmp-*`` staging dirs older than 24h.
          A leftover ``.bak-*`` with a healthy target is removed too.

        The rename swap itself remains non-atomic across concurrent processes;
        this is best-effort single-process recovery.
        """
        name = target_path.name
        parent = target_path.parent
        backups = sorted(parent.glob(f"{name}.bak-*"))
        stagings = sorted(parent.glob(f"{name}.tmp-*"))

        if not target_path.exists() and len(backups) == 1:
            backup = backups[0]
            logger.warning("Restoring orphaned backup %s to %s", backup, target_path)
            try:
                backup.rename(target_path)
                return
            except OSError as exc:
                logger.warning("Could not restore orphaned backup %s: %s", backup, exc)

        cutoff = time.time() - 24 * 3600
        for candidate in backups + stagings:
            try:
                if candidate.stat().st_mtime < cutoff:
                    self._safe_cleanup(candidate, [])
            except OSError:
                pass

    async def diff(self, overlay_fs: AgentFS, base_fs: AgentFS, path: str = "/") -> list[FileChange]:
        """Compute changes between overlay and base.

        Args:
            overlay_fs: Overlay filesystem
            base_fs: Base filesystem
            path: Root path to compare

        Returns:
            List of FileChange objects

        Semantics:
            - ``added``: present in overlay, absent from base.
            - ``modified``: present in both, different content/size.
            - ``deleted``: present in base, absent from overlay.  This is a
              *visibility delta*: materialize copies base first, so base-only
              files still reach the output tree.

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

        # Deleted files: present in base, absent from overlay.
        #
        # Semantics note: in the overlay model, "deleted" means "visible in
        # base but not in overlay".  Materialization copies base first, then
        # overlay, so base-only files ARE still materialized to disk; this is
        # a *visibility delta*, not a prediction of what materialize will
        # remove.
        for file_path in base_set - overlay_set:
            changes.append(
                FileChange(
                    path=file_path,
                    change_type="deleted",
                    old_size=base_files[file_path].size,
                )
            )

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

            # Same size: single byte-accurate comparison pass (chunk-boundary
            # independent).  A sha256 pre-pass was removed: it required two
            # additional full reads for zero benefit (a hash collision is the
            # only case where the old fallback would have fired).
            try:
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

    async def _count_files_for_progress(
        self,
        source_fs: AgentFS,
        filters: ViewQuery | None,
    ) -> int:
        """Count files that would be copied under ``filters`` (L14).

        Applies the same path/size filter rules as :meth:`_copy_recursive`.
        """
        files = await self._list_all_files(source_fs, "/")
        count = 0
        for path, fingerprint in files.items():
            if filters is not None:
                if not filters.matches_path(path):
                    continue
                if not filters.matches_size(fingerprint):
                    continue
            count += 1
        return count

    async def _copy_recursive(
        self,
        source_fs: AgentFS,
        src_path: str,
        dest_path: Path,
        stats: dict,
        changes: list[FileChange],
        skipped: list[str],
        errors: list[tuple[str, str]],
        filters: ViewQuery | None = None,
        progress_total: int = -1,
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
            filters: Optional filters to apply.  Only files are filtered
                (directories are always descended into); size constraints
                are also honored via the stat pass.
            progress_total: Total files expected for progress reporting
                (``-1`` when unknown).
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
                # Get stats first: we must know the entry type to decide
                # whether to descend or filter.
                stat = await source_fs.fs.stat(entry_path)

                # Directories are always descended into so that patterns like
                # ``src/**/*.py`` still find nested matches (glob semantics
                # are file-oriented).  Consequence: empty directories may
                # still be created for filtered-out subtrees.
                if stat.is_directory():
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
                        progress_total,
                    )
                    continue

                # H1: apply the path filter before any read work for files.
                if filters is not None and not filters.matches_path(entry_path):
                    continue
                # H1: size filters are free here because stats are already fetched.
                if filters is not None and not filters.matches_size(stat):
                    continue

                if stat.is_file():
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

                    # L14: label truthfully — "modified" when a file of a
                    # different size already exists at the destination
                    # (no-clean runs seeding the staging tree), else "added".
                    pre_existing_size = local_file.stat().st_size if local_file.exists() else None
                    change_type = (
                        "modified" if pre_existing_size is not None and pre_existing_size != len(content) else "added"
                    )

                    # Write to disk
                    local_file.write_bytes(content)

                    # Update stats
                    stats["files_written"] += 1
                    stats["bytes_written"] += len(content)

                    # Track change
                    changes.append(
                        FileChange(
                            path=entry_path,
                            change_type=change_type,
                            old_size=pre_existing_size,
                            new_size=len(content),
                        )
                    )

                    # Progress callback
                    if self.progress_callback:
                        self.progress_callback(entry_path, stats["files_written"], progress_total)

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

    def __init__(
        self,
        agent_fs: AgentFS,
        materializer: Materializer | None = None,
        readonly: bool = False,
    ):
        """Initialize the materialization manager.

        Args:
            agent_fs: Backing AgentFS instance to materialize.
            materializer: Optional :class:`Materializer` backend.
            readonly: When True, :meth:`to_disk` raises ``WorkspaceError``
                with ``code="WORKSPACE_READONLY"`` (materialization writes
                to the local filesystem).  ``diff``/``preview`` remain
                available (read-only).
        """
        self._agent_fs = agent_fs
        self._materializer = materializer or Materializer()
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

    async def to_disk(
        self,
        target_path: Path,
        *,
        base: AgentFS | Workspace | None = None,
        filters: ViewQuery | None = None,
        clean: bool = True,
        allow_root: Path | None = None,
    ) -> MaterializationResult:
        """Materialize this workspace to disk, optionally layering a base workspace.

        Raises:
            WorkspaceError: with ``code="WORKSPACE_READONLY"`` when this
                manager belongs to a read-only workspace (materialization
                writes to the local filesystem and is treated as a write
                operation).
        """
        self._ensure_writable("MaterializationManager.to_disk")
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
        base: AgentFS | Workspace,
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
        base: AgentFS | Workspace,
        *,
        path: str = "/",
    ) -> list[FileChange]:
        """Preview materialization changes (alias of :meth:`diff`)."""
        return await self.diff(base=base, path=path)
