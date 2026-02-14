# AgentFS-Pydantic Extension Implementation Plan

**Version:** 1.0
**Date:** 2026-02-14
**Status:** Design Phase
**Goal:** Extend agentfs_pydantic to simplify cairn and provide reusable patterns for all AgentFS users

---

## Executive Summary

This plan details the implementation of 7 major extensions to agentfs_pydantic that will:
- Reduce cairn codebase by ~250-300 lines
- Extract reusable patterns for all AgentFS users
- Improve maintainability and testability
- Create a more composable architecture

**Estimated Impact:**
- Development time: 3-4 weeks (phased rollout)
- Code reduction in cairn: ~40%
- New reusable modules: 4
- Enhanced existing modules: 2

---

## Table of Contents

1. [Opportunity 1: Generic Repository Pattern](#opportunity-1-generic-repository-pattern)
2. [Opportunity 2: Workspace Materialization](#opportunity-2-workspace-materialization)
3. [Opportunity 3: Enhanced View with Content Search](#opportunity-3-enhanced-view-with-content-search)
4. [Opportunity 4: Overlay Operations](#opportunity-4-overlay-operations)
5. [Opportunity 5: File Operations Helper](#opportunity-5-file-operations-helper)
6. [Opportunity 6: KV Models Base Classes](#opportunity-6-kv-models-base-classes)
7. [Opportunity 7: Query Builder Enhancements](#opportunity-7-query-builder-enhancements)
8. [Implementation Phases](#implementation-phases)
9. [Testing Strategy](#testing-strategy)
10. [Migration Guide](#migration-guide)
11. [Backward Compatibility](#backward-compatibility)

---

## Opportunity 1: Generic Repository Pattern

### Overview
Create a generic, typed repository pattern for AgentFS KV operations using Python generics.

### API Design

```python
# agentfs-pydantic/src/agentfs_pydantic/repository.py

from typing import TypeVar, Generic, Type, Optional, Callable
from pydantic import BaseModel
from agentfs_sdk import AgentFS

T = TypeVar('T', bound=BaseModel)

class TypedKVRepository(Generic[T]):
    """Generic typed KV operations for Pydantic models.

    Examples:
        >>> repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")
        >>> await repo.save("alice", UserRecord(name="Alice", age=30))
        >>> user = await repo.load("alice", UserRecord)
    """

    def __init__(
        self,
        storage: AgentFS,
        prefix: str = "",
        key_builder: Optional[Callable[[str], str]] = None
    ):
        """Initialize repository.

        Args:
            storage: AgentFS instance
            prefix: Key prefix for namespacing (e.g., "user:", "agent:")
            key_builder: Optional function to build keys from IDs
        """
        self.storage = storage
        self.prefix = prefix
        self.key_builder = key_builder or (lambda id: f"{prefix}{id}")

    async def save(self, id: str, record: T) -> None:
        """Save a record to KV store.

        Args:
            id: Record identifier
            record: Pydantic model instance to save
        """
        key = self.key_builder(id)
        await self.storage.kv.set(key, record.model_dump_json())

    async def load(self, id: str, model_type: Type[T]) -> Optional[T]:
        """Load a record from KV store.

        Args:
            id: Record identifier
            model_type: Pydantic model class

        Returns:
            Model instance or None if not found
        """
        key = self.key_builder(id)
        data = await self.storage.kv.get(key)
        if data is None:
            return None
        return model_type.model_validate_json(data)

    async def delete(self, id: str) -> None:
        """Delete a record from KV store.

        Args:
            id: Record identifier
        """
        key = self.key_builder(id)
        await self.storage.kv.delete(key)

    async def list_all(self, model_type: Type[T]) -> list[T]:
        """List all records with the configured prefix.

        Args:
            model_type: Pydantic model class

        Returns:
            List of all matching records
        """
        keys = await self.storage.kv.list()
        records: list[T] = []

        for key in keys:
            if not key.startswith(self.prefix):
                continue

            data = await self.storage.kv.get(key)
            if data:
                try:
                    records.append(model_type.model_validate_json(data))
                except Exception:
                    # Skip invalid records
                    continue

        return records

    async def exists(self, id: str) -> bool:
        """Check if a record exists.

        Args:
            id: Record identifier

        Returns:
            True if record exists
        """
        key = self.key_builder(id)
        data = await self.storage.kv.get(key)
        return data is not None

    async def list_ids(self) -> list[str]:
        """List all IDs with the configured prefix.

        Returns:
            List of record IDs (with prefix removed)
        """
        keys = await self.storage.kv.list()
        ids = []

        for key in keys:
            if key.startswith(self.prefix):
                ids.append(key[len(self.prefix):])

        return ids


class NamespacedKVStore:
    """Convenience wrapper for creating namespaced repositories.

    Examples:
        >>> kv = NamespacedKVStore(agent_fs)
        >>> users = kv.namespace("user:")
        >>> await users.save("alice", UserRecord(...))
    """

    def __init__(self, storage: AgentFS):
        self.storage = storage

    def namespace(self, prefix: str) -> TypedKVRepository:
        """Create a namespaced repository.

        Args:
            prefix: Namespace prefix

        Returns:
            TypedKVRepository instance
        """
        return TypedKVRepository(self.storage, prefix=prefix)
```

### Implementation Steps

1. **Week 1, Day 1-2: Core Implementation**
   - Create `agentfs-pydantic/src/agentfs_pydantic/repository.py`
   - Implement `TypedKVRepository` class
   - Implement `NamespacedKVStore` helper
   - Add comprehensive docstrings

2. **Week 1, Day 3: Testing**
   - Create `agentfs-pydantic/tests/test_repository.py`
   - Test save/load/delete operations
   - Test list operations with filtering
   - Test error handling (invalid JSON, missing keys)
   - Test concurrent access patterns

3. **Week 1, Day 4: Documentation**
   - Add usage examples to README
   - Create migration guide from raw KV to repository
   - Document best practices

4. **Week 1, Day 5: Cairn Migration**
   - Update `cairn/kv_store.py` to use `TypedKVRepository`
   - Simplify `KVRepository` class
   - Update `cairn/lifecycle.py` to use new pattern
   - Run cairn tests to verify compatibility

### Cairn Migration Example

**Before:**
```python
# cairn/kv_store.py (62 lines)
class KVRepository:
    def __init__(self, storage: AgentFS):
        self.storage = storage

    async def save_lifecycle(self, record: LifecycleRecord) -> None:
        await self.storage.kv.set(agent_key(record.agent_id), record.model_dump_json())

    async def load_lifecycle(self, agent_id: str) -> LifecycleRecord | None:
        data = await self.storage.kv.get(agent_key(agent_id))
        if data is None:
            return None
        return LifecycleRecord.model_validate_json(data)

    # ... 40 more lines
```

**After:**
```python
# cairn/kv_store.py (20 lines)
from agentfs_pydantic.repository import TypedKVRepository
from cairn.kv_models import LifecycleRecord, SubmissionRecord, agent_key, AGENT_KEY_PREFIX

class KVRepository:
    """Cairn-specific KV repository with typed operations."""

    def __init__(self, storage: AgentFS):
        self.lifecycle_repo = TypedKVRepository[LifecycleRecord](
            storage,
            prefix=AGENT_KEY_PREFIX,
            key_builder=agent_key
        )
        self.storage = storage  # For submission operations

    async def save_lifecycle(self, record: LifecycleRecord) -> None:
        await self.lifecycle_repo.save(record.agent_id, record)

    async def load_lifecycle(self, agent_id: str) -> LifecycleRecord | None:
        return await self.lifecycle_repo.load(agent_id, LifecycleRecord)

    async def delete_lifecycle(self, agent_id: str) -> None:
        await self.lifecycle_repo.delete(agent_id)

    async def list_lifecycle(self) -> list[LifecycleRecord]:
        return await self.lifecycle_repo.list_all(LifecycleRecord)

    # Keep submission methods as-is (specialized logic)
```

### Success Metrics
- ✅ Reduce cairn/kv_store.py from 62 to ~25 lines
- ✅ 100% test coverage for repository operations
- ✅ Zero breaking changes to cairn API
- ✅ Documentation with 3+ usage examples

---

## Opportunity 2: Workspace Materialization

### Overview
Provide a general-purpose workspace materialization system for AgentFS overlays.

### API Design

```python
# agentfs-pydantic/src/agentfs_pydantic/materialization.py

from pathlib import Path
from typing import Optional, Callable, Any
from dataclasses import dataclass
from enum import Enum
from agentfs_sdk import AgentFS
from .view import ViewQuery


class ConflictResolution(str, Enum):
    """Strategy for handling file conflicts during materialization."""
    OVERWRITE = "overwrite"  # Overlay wins
    SKIP = "skip"            # Keep existing file
    ERROR = "error"          # Raise exception


@dataclass
class FileChange:
    """Represents a change between base and overlay."""
    path: str
    change_type: str  # "added", "modified", "deleted"
    old_size: Optional[int] = None
    new_size: Optional[int] = None


@dataclass
class MaterializationResult:
    """Result of materialization operation."""
    target_path: Path
    files_written: int
    bytes_written: int
    changes: list[FileChange]
    skipped: list[str]
    errors: list[tuple[str, str]]  # (path, error_message)


class Materializer:
    """Materialize AgentFS overlays to local filesystem.

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
        progress_callback: Optional[Callable[[str, int, int], None]] = None
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
        clean: bool = True
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
        """
        if clean and target_path.exists():
            import shutil
            shutil.rmtree(target_path)

        target_path.mkdir(parents=True, exist_ok=True)

        files_written = 0
        bytes_written = 0
        changes = []
        skipped = []
        errors = []

        # Materialize base layer first if provided
        if base_fs is not None:
            await self._copy_recursive(
                base_fs, "/", target_path,
                files_written, bytes_written, changes, skipped, errors
            )

        # Materialize overlay layer
        await self._copy_recursive(
            agent_fs, "/", target_path,
            files_written, bytes_written, changes, skipped, errors,
            filters=filters
        )

        return MaterializationResult(
            target_path=target_path,
            files_written=files_written,
            bytes_written=bytes_written,
            changes=changes,
            skipped=skipped,
            errors=errors
        )

    async def diff(
        self,
        overlay_fs: AgentFS,
        base_fs: AgentFS,
        path: str = "/"
    ) -> list[FileChange]:
        """Compute changes between overlay and base.

        Args:
            overlay_fs: Overlay filesystem
            base_fs: Base filesystem
            path: Root path to compare

        Returns:
            List of FileChange objects
        """
        changes = []

        # Get all files from both layers
        overlay_files = await self._list_all_files(overlay_fs, path)
        base_files = await self._list_all_files(base_fs, path)

        overlay_set = set(overlay_files.keys())
        base_set = set(base_files.keys())

        # Added files
        for file_path in overlay_set - base_set:
            changes.append(FileChange(
                path=file_path,
                change_type="added",
                new_size=overlay_files[file_path]
            ))

        # Deleted files (if overlay has whiteouts)
        for file_path in base_set - overlay_set:
            # Check if explicitly deleted in overlay
            # This requires checking whiteout table (future enhancement)
            pass

        # Modified files
        for file_path in overlay_set & base_set:
            overlay_size = overlay_files[file_path]
            base_size = base_files[file_path]

            if overlay_size != base_size:
                changes.append(FileChange(
                    path=file_path,
                    change_type="modified",
                    old_size=base_size,
                    new_size=overlay_size
                ))
            else:
                # Size same, check content
                overlay_content = await overlay_fs.fs.read_file(file_path)
                base_content = await base_fs.fs.read_file(file_path)

                if overlay_content != base_content:
                    changes.append(FileChange(
                        path=file_path,
                        change_type="modified",
                        old_size=base_size,
                        new_size=overlay_size
                    ))

        return changes

    async def _copy_recursive(
        self,
        source_fs: AgentFS,
        src_path: str,
        dest_path: Path,
        files_written: int,
        bytes_written: int,
        changes: list[FileChange],
        skipped: list[str],
        errors: list[tuple[str, str]],
        filters: Optional[ViewQuery] = None
    ) -> None:
        """Recursively copy files from AgentFS to disk."""
        # Implementation similar to current cairn/workspace.py
        # but with progress tracking and filtering
        pass

    async def _list_all_files(
        self,
        fs: AgentFS,
        path: str
    ) -> dict[str, int]:
        """Get all files with their sizes."""
        files = {}

        async def walk(current_path: str):
            try:
                entries = await fs.fs.readdir(current_path)
                for entry in entries:
                    entry_path = f"{current_path.rstrip('/')}/{entry.name}"

                    if getattr(entry, 'type', '') == 'directory':
                        await walk(entry_path)
                    else:
                        stat = await fs.fs.stat(entry_path)
                        files[entry_path] = stat.size
            except Exception:
                pass

        await walk(path)
        return files
```

### Implementation Steps

1. **Week 2, Day 1-2: Core Implementation**
   - Create `materialization.py` module
   - Implement `Materializer` class
   - Implement conflict resolution strategies
   - Add progress callback support

2. **Week 2, Day 3: Diff Implementation**
   - Implement `diff()` method
   - Add whiteout awareness for deleted files
   - Add content comparison options

3. **Week 2, Day 4: Testing**
   - Test materialization with various overlay scenarios
   - Test diff accuracy
   - Test conflict resolution strategies
   - Test large file handling
   - Test progress callbacks

4. **Week 2, Day 5: Cairn Migration**
   - Update `cairn/workspace.py` to use `Materializer`
   - Add preview diff functionality to cairn
   - Update orchestrator to use new API

### Cairn Migration Example

**Before:**
```python
# cairn/workspace.py (77 lines)
class WorkspaceMaterializer:
    # ... lots of manual copy logic
    async def _copy_recursive(self, source_fs, src_path, dest_path):
        # 30+ lines of recursive copy logic
```

**After:**
```python
# cairn/workspace.py (15 lines)
from agentfs_pydantic.materialization import Materializer

class WorkspaceMaterializer:
    def __init__(self, cairn_home: Path, stable_fs: AgentFS):
        self.materializer = Materializer()
        self.workspace_dir = cairn_home / "workspaces"
        self.stable_fs = stable_fs

    async def materialize(self, agent_id: str, agent_fs: AgentFS) -> Path:
        result = await self.materializer.materialize(
            agent_fs=agent_fs,
            target_path=self.workspace_dir / agent_id,
            base_fs=self.stable_fs
        )
        return result.target_path

    async def cleanup(self, agent_id: str) -> None:
        workspace = self.workspace_dir / agent_id
        if workspace.exists():
            shutil.rmtree(workspace)
```

### Success Metrics
- ✅ Reduce cairn/workspace.py from 77 to ~20 lines
- ✅ Support progress callbacks for large materializations
- ✅ Accurate diff computation
- ✅ Handle edge cases (symlinks, permissions, binary files)

---

## Opportunity 3: Enhanced View with Content Search

### Overview
Extend the existing `View` class to support content searching within files.

### API Design

```python
# agentfs-pydantic/src/agentfs_pydantic/view.py (EXTEND EXISTING)

from dataclasses import dataclass
import re


@dataclass
class SearchMatch:
    """A single content search match.

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
    # ... existing fields ...

    # NEW: Content search fields
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
        description="Match whole words only"
    )
    max_matches_per_file: Optional[int] = Field(
        None,
        description="Limit matches per file (None = unlimited)"
    )


class View(BaseModel):
    # ... existing fields and methods ...

    async def search_content(self) -> list[SearchMatch]:
        """Search file contents matching query patterns.

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
                pattern = r'\b' + pattern + r'\b'

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
                    content = content.decode('utf-8')
                except UnicodeDecodeError:
                    continue  # Skip binary files

            lines = content.split('\n')
            file_matches = 0

            for line_num, line in enumerate(lines, start=1):
                for match in regex.finditer(line):
                    matches.append(SearchMatch(
                        file=file.path,
                        line=line_num,
                        text=line.strip(),
                        column=match.start(),
                        match_start=match.start(),
                        match_end=match.end()
                    ))

                    file_matches += 1
                    if (self.query.max_matches_per_file and
                        file_matches >= self.query.max_matches_per_file):
                        break

                if (self.query.max_matches_per_file and
                    file_matches >= self.query.max_matches_per_file):
                    break

        return matches

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
        query = self.query.model_copy(update={
            "content_regex" if regex else "content_pattern": pattern
        })
        search_view = View(agent=self.agent, query=query)
        matches = await search_view.search_content()

        # Get unique files
        file_paths = set(m.file for m in matches)

        # Load file entries
        return [f for f in await self.load() if f.path in file_paths]
```

### Implementation Steps

1. **Week 2, Day 1-2: Extend ViewQuery**
   - Add content search fields to `ViewQuery`
   - Add `SearchMatch` dataclass
   - Update validation logic

2. **Week 2, Day 3: Implement search_content()**
   - Implement regex matching
   - Handle binary vs text files
   - Add match limiting
   - Handle encoding errors gracefully

3. **Week 2, Day 4: Testing**
   - Test simple pattern search
   - Test regex search
   - Test case sensitivity
   - Test whole word matching
   - Test binary file handling
   - Test large file performance

4. **Week 2, Day 5: Cairn Migration**
   - Update `cairn/external_functions.py` to use `View.search_content()`
   - Remove custom search implementation
   - Verify external function tests pass

### Cairn Migration Example

**Before:**
```python
# cairn/external_functions.py (40+ lines for search_content)
async def search_content(self, pattern: str, path: str = ".") -> list[dict]:
    results = []

    async def search_in_file(file_path: str) -> None:
        try:
            content = await self.read_file(file_path)
            lines = content.split("\n")
            import re
            regex = re.compile(pattern)

            for line_num, line in enumerate(lines, start=1):
                if regex.search(line):
                    match = SearchContentMatch(...)
                    results.append(match.model_dump())
        except Exception:
            pass

    files = await self.search_files("*")
    for file_path in files:
        await search_in_file(file_path)

    return results
```

**After:**
```python
# cairn/external_functions.py (5 lines for search_content)
from agentfs_pydantic import View, ViewQuery

async def search_content(self, pattern: str, path: str = ".") -> list[dict]:
    view = View(
        agent=self.agent_fs,
        query=ViewQuery(path_pattern="**/*", content_regex=pattern)
    )
    matches = await view.search_content()
    return [m.__dict__ for m in matches]
```

### Success Metrics
- ✅ Reduce search_content implementation from 40 to ~5 lines
- ✅ Support regex and simple patterns
- ✅ Handle large files efficiently
- ✅ Gracefully handle binary files

---

## Opportunity 4: Overlay Operations

### Overview
Provide high-level operations for working with AgentFS overlay filesystems.

### API Design

```python
# agentfs-pydantic/src/agentfs_pydantic/overlay.py

from typing import Optional, Protocol, Any
from dataclasses import dataclass
from enum import Enum
from agentfs_sdk import AgentFS


class MergeStrategy(str, Enum):
    """Strategy for merging overlays."""
    OVERWRITE = "overwrite"  # Overlay wins on conflicts
    PRESERVE = "preserve"    # Base wins on conflicts
    ERROR = "error"          # Raise on conflicts
    CALLBACK = "callback"    # Use callback for conflicts


@dataclass
class MergeConflict:
    """Represents a merge conflict."""
    path: str
    overlay_size: int
    base_size: int
    overlay_content: bytes
    base_content: bytes


@dataclass
class MergeResult:
    """Result of merge operation."""
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
        conflict_resolver: Optional[ConflictResolver] = None
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
        strategy: Optional[MergeStrategy] = None
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

        files_merged = 0
        conflicts = []
        errors = []

        # Recursively copy files from source to target
        await self._merge_recursive(
            source, target, path,
            effective_strategy, files_merged, conflicts, errors
        )

        return MergeResult(
            files_merged=files_merged,
            conflicts=conflicts,
            errors=errors
        )

    async def _merge_recursive(
        self,
        source: AgentFS,
        target: AgentFS,
        path: str,
        strategy: MergeStrategy,
        files_merged: int,
        conflicts: list[MergeConflict],
        errors: list[tuple[str, str]]
    ) -> None:
        """Recursively merge directory contents."""
        try:
            entries = await source.fs.readdir(path)
        except FileNotFoundError:
            return

        for entry in entries:
            entry_name = getattr(entry, 'name', None)
            if not entry_name:
                continue

            source_path = f"{path.rstrip('/')}/{entry_name}"

            # Check if directory
            if self._is_directory(entry):
                # Create directory in target if needed
                try:
                    await target.fs.stat(source_path)
                except FileNotFoundError:
                    # Directory doesn't exist in target, create it
                    # (AgentFS creates parent dirs automatically on write)
                    pass

                # Recurse
                await self._merge_recursive(
                    source, target, source_path,
                    strategy, files_merged, conflicts, errors
                )
                continue

            # Handle file
            try:
                source_content = await source.fs.read_file(source_path)

                # Check if file exists in target
                target_exists = False
                target_content = None
                try:
                    target_content = await target.fs.read_file(source_path)
                    target_exists = True
                except FileNotFoundError:
                    pass

                # Handle conflict
                if target_exists and source_content != target_content:
                    conflict = MergeConflict(
                        path=source_path,
                        overlay_size=len(source_content),
                        base_size=len(target_content) if target_content else 0,
                        overlay_content=source_content,
                        base_content=target_content or b""
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
                target_path = source_path.lstrip('/')
                await target.fs.write_file(target_path, source_content)
                files_merged += 1

            except Exception as e:
                errors.append((source_path, str(e)))

    async def list_changes(
        self,
        overlay: AgentFS,
        path: str = "/"
    ) -> list[str]:
        """List files that exist in overlay at path.

        This returns files that have been written to the overlay,
        which may include modifications to base files.

        Args:
            overlay: Overlay filesystem
            path: Root path to check

        Returns:
            List of file paths in overlay
        """
        files = []

        async def walk(current_path: str):
            try:
                entries = await overlay.fs.readdir(current_path)
                for entry in entries:
                    entry_name = getattr(entry, 'name', None)
                    if not entry_name:
                        continue

                    full_path = f"{current_path.rstrip('/')}/{entry_name}"

                    if self._is_directory(entry):
                        await walk(full_path)
                    else:
                        files.append(full_path)
            except FileNotFoundError:
                pass

        await walk(path)
        return files

    async def reset_overlay(
        self,
        overlay: AgentFS,
        paths: Optional[list[str]] = None
    ) -> int:
        """Remove files from overlay (reset to base state).

        Args:
            overlay: Overlay filesystem
            paths: Specific paths to reset (None = reset all)

        Returns:
            Number of files removed
        """
        if paths is None:
            # Get all overlay files
            paths = await self.list_changes(overlay)

        removed = 0
        for path in paths:
            try:
                await overlay.fs.remove(path.lstrip('/'))
                removed += 1
            except Exception:
                pass

        return removed

    def _is_directory(self, entry: Any) -> bool:
        """Check if entry is a directory."""
        entry_type = getattr(entry, 'type', None)
        return bool(
            entry_type == "directory"
            or entry_type == "dir"
            or getattr(entry, 'is_directory', False)
            or getattr(entry, 'is_dir', False)
        )
```

### Implementation Steps

1. **Week 3, Day 1-2: Core Implementation**
   - Create `overlay.py` module
   - Implement `OverlayOperations` class
   - Implement merge strategies
   - Add conflict detection

2. **Week 3, Day 3: Advanced Features**
   - Implement custom conflict resolvers
   - Add `list_changes()` method
   - Add `reset_overlay()` method

3. **Week 3, Day 4: Testing**
   - Test merge with various strategies
   - Test conflict detection
   - Test error handling
   - Test large overlay merges

4. **Week 3, Day 5: Cairn Migration**
   - Update `cairn/orchestrator.py` to use `OverlayOperations`
   - Simplify `_merge_overlay_to_stable()` method
   - Add rollback capability using `reset_overlay()`

### Cairn Migration Example

**Before:**
```python
# cairn/orchestrator.py (30+ lines)
async def _merge_overlay_to_stable(self, source: AgentFS, target: AgentFS, src_path: str = "/") -> None:
    """Copy source overlay files into stable AgentFS recursively."""
    for base in (src_path, src_path.lstrip("/") or "."):
        try:
            entries = await source.fs.readdir(base)
            break
        except FileNotFoundError:
            entries = []

    for entry in entries:
        name = getattr(entry, "name", None)
        if not name:
            continue

        source_child = f"{src_path.rstrip('/')}/{name}" if src_path != "/" else f"/{name}"
        source_child_rel = source_child.lstrip("/")

        if self._is_directory_entry(entry):
            await self._merge_overlay_to_stable(source, target, source_child)
            continue

        file_bytes = await source.fs.read_file(source_child)
        await target.fs.write_file(source_child_rel, file_bytes)
```

**After:**
```python
# cairn/orchestrator.py (3 lines)
from agentfs_pydantic.overlay import OverlayOperations, MergeStrategy

async def accept_agent(self, agent_id: str) -> None:
    ctx = self._get_agent(agent_id)
    ctx.transition(AgentState.ACCEPTED)
    await self._save_lifecycle_record(ctx)

    # Merge overlay to stable
    overlay_ops = OverlayOperations(strategy=MergeStrategy.OVERWRITE)
    await overlay_ops.merge(ctx.agent_fs, self.stable)

    await self.trash_agent(agent_id)
```

### Success Metrics
- ✅ Reduce merge code from 30+ to ~3 lines
- ✅ Support multiple merge strategies
- ✅ Handle conflicts gracefully
- ✅ Add rollback capability

---

## Opportunity 5: File Operations Helper

### Overview
Provide a high-level facade for common file operations with overlay fallthrough.

### API Design

```python
# agentfs-pydantic/src/agentfs_pydantic/operations.py

from typing import Optional, Any
from pathlib import Path
from agentfs_sdk import AgentFS
from .view import View, ViewQuery


class FileOperations:
    """High-level file operations with overlay fallthrough.

    This class provides a simplified interface for working with
    overlay filesystems, automatically handling fallthrough to base layer.

    Examples:
        >>> ops = FileOperations(agent_fs, base_fs=stable_fs)
        >>> content = await ops.read_file("config.json")
        >>> await ops.write_file("output.txt", "Hello World")
        >>> files = await ops.search_files("*.py")
    """

    def __init__(
        self,
        agent_fs: AgentFS,
        base_fs: Optional[AgentFS] = None
    ):
        """Initialize file operations.

        Args:
            agent_fs: Agent overlay filesystem
            base_fs: Optional base filesystem for fallthrough
        """
        self.agent_fs = agent_fs
        self.base_fs = base_fs

    async def read_file(self, path: str, *, encoding: str = "utf-8") -> str | bytes:
        """Read file from overlay with fallthrough to base.

        Args:
            path: File path to read
            encoding: Text encoding (None for binary)

        Returns:
            File content as string or bytes

        Raises:
            FileNotFoundError: If file doesn't exist in either layer
        """
        # Try overlay first
        try:
            content = await self.agent_fs.fs.read_file(path)
        except FileNotFoundError:
            # Fallthrough to base
            if self.base_fs is None:
                raise
            content = await self.base_fs.fs.read_file(path)

        # Decode if encoding specified
        if encoding:
            return content.decode(encoding)
        return content

    async def write_file(
        self,
        path: str,
        content: str | bytes,
        *,
        encoding: str = "utf-8"
    ) -> None:
        """Write file to overlay only.

        Args:
            path: File path to write
            content: File content
            encoding: Text encoding if content is string
        """
        # Encode if string
        if isinstance(content, str):
            content = content.encode(encoding)

        # Always write to overlay
        await self.agent_fs.fs.write_file(path, content)

    async def file_exists(self, path: str) -> bool:
        """Check if file exists in overlay or base.

        Args:
            path: File path to check

        Returns:
            True if file exists in either layer
        """
        try:
            await self.agent_fs.fs.stat(path)
            return True
        except FileNotFoundError:
            if self.base_fs:
                try:
                    await self.base_fs.fs.stat(path)
                    return True
                except FileNotFoundError:
                    pass
            return False

    async def list_dir(self, path: str) -> list[str]:
        """List directory contents from overlay.

        The overlay automatically merges with base layer.

        Args:
            path: Directory path

        Returns:
            List of entry names
        """
        entries = await self.agent_fs.fs.readdir(path)
        return [entry.name for entry in entries]

    async def search_files(
        self,
        pattern: str,
        recursive: bool = True
    ) -> list[str]:
        """Search for files matching glob pattern.

        Args:
            pattern: Glob pattern (e.g., "*.py", "**/*.json")
            recursive: Search recursively

        Returns:
            List of matching file paths
        """
        view = View(
            agent=self.agent_fs,
            query=ViewQuery(
                path_pattern=pattern,
                recursive=recursive,
                include_stats=False,
                include_content=False
            )
        )

        files = await view.load()
        return [f.path for f in files]

    async def stat(self, path: str) -> Any:
        """Get file statistics.

        Args:
            path: File path

        Returns:
            File stat object
        """
        try:
            return await self.agent_fs.fs.stat(path)
        except FileNotFoundError:
            if self.base_fs:
                return await self.base_fs.fs.stat(path)
            raise

    async def remove(self, path: str) -> None:
        """Remove file from overlay.

        This creates a whiteout in the overlay if file exists in base.

        Args:
            path: File path to remove
        """
        await self.agent_fs.fs.remove(path)

    async def tree(
        self,
        path: str = "/",
        max_depth: Optional[int] = None
    ) -> dict[str, Any]:
        """Get directory tree structure.

        Args:
            path: Root path
            max_depth: Maximum depth to traverse

        Returns:
            Nested dict representing directory tree
        """
        tree = {}

        async def walk(current_path: str, depth: int = 0):
            if max_depth is not None and depth >= max_depth:
                return {}

            result = {}

            try:
                entries = await self.agent_fs.fs.readdir(current_path)
                for entry in entries:
                    entry_path = f"{current_path.rstrip('/')}/{entry.name}"

                    if getattr(entry, 'type', '') == 'directory':
                        result[entry.name] = await walk(entry_path, depth + 1)
                    else:
                        result[entry.name] = None  # File (leaf node)
            except FileNotFoundError:
                pass

            return result

        return await walk(path)
```

### Implementation Steps

1. **Week 3, Day 1-2: Core Implementation**
   - Create `operations.py` module
   - Implement basic file operations
   - Add overlay fallthrough logic

2. **Week 3, Day 3: Advanced Operations**
   - Implement `search_files()` using View
   - Implement `tree()` method
   - Add bulk operations (copy, move)

3. **Week 3, Day 4: Testing**
   - Test fallthrough behavior
   - Test overlay isolation
   - Test search integration
   - Test error handling

4. **Week 3, Day 5: Cairn Migration**
   - Update `cairn/external_functions.py` to use `FileOperations`
   - Remove manual fallthrough logic
   - Simplify external function implementations

### Cairn Migration Example

**Before:**
```python
# cairn/external_functions.py (150+ lines)
async def read_file(self, path: str) -> str:
    request = ReadFileRequest(path=path)
    try:
        content = await self.agent_fs.fs.read_file(request.path)
    except FileNotFoundError:
        content = await self.stable_fs.fs.read_file(request.path)
    return content.decode("utf-8")

async def write_file(self, path: str, content: str) -> bool:
    request = WriteFileRequest(path=path, content=content)
    await self.agent_fs.fs.write_file(request.path, request.content.encode("utf-8"))
    return True

async def file_exists(self, path: str) -> bool:
    try:
        await self.agent_fs.fs.stat(path)
        return True
    except FileNotFoundError:
        return False

# ... many more methods with similar patterns
```

**After:**
```python
# cairn/external_functions.py (30 lines)
from agentfs_pydantic.operations import FileOperations

class CairnExternalFunctions:
    def __init__(self, agent_id, agent_fs, stable_fs, llm):
        self.agent_id = agent_id
        self.ops = FileOperations(agent_fs, base_fs=stable_fs)
        self.llm = llm

    async def read_file(self, path: str) -> str:
        ReadFileRequest(path=path)  # Validation only
        return await self.ops.read_file(path)

    async def write_file(self, path: str, content: str) -> bool:
        WriteFileRequest(path=path, content=content)  # Validation
        await self.ops.write_file(path, content)
        return True

    async def file_exists(self, path: str) -> bool:
        FileExistsRequest(path=path)
        return await self.ops.file_exists(path)

    async def search_files(self, pattern: str) -> list[str]:
        SearchFilesRequest(pattern=pattern)
        return await self.ops.search_files(pattern)

    # Keep only LLM and submission methods with custom logic
```

### Success Metrics
- ✅ Reduce external_functions.py from 150+ to ~50 lines
- ✅ Standardize overlay fallthrough pattern
- ✅ Eliminate duplicate file operation logic
- ✅ Improve testability

---

## Opportunity 6: KV Models Base Classes

### Overview
Provide base Pydantic models for common KV record patterns.

### API Design

```python
# agentfs-pydantic/src/agentfs_pydantic/models.py (EXTEND)

import time
from pydantic import BaseModel, Field


class KVRecord(BaseModel):
    """Base model for records stored in KV store.

    Provides automatic timestamp tracking.

    Examples:
        >>> class UserRecord(KVRecord):
        ...     user_id: str
        ...     name: str
        ...     email: str
        >>>
        >>> user = UserRecord(user_id="alice", name="Alice", email="alice@example.com")
        >>> user.created_at  # Automatically set
    """

    created_at: float = Field(
        default_factory=time.time,
        description="Creation timestamp (Unix epoch)"
    )
    updated_at: float = Field(
        default_factory=time.time,
        description="Last update timestamp (Unix epoch)"
    )

    def mark_updated(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = time.time()


class VersionedKVRecord(KVRecord):
    """KV record with version tracking.

    Examples:
        >>> class ConfigRecord(VersionedKVRecord):
        ...     settings: dict
        >>>
        >>> config = ConfigRecord(settings={"theme": "dark"})
        >>> config.version  # 1
        >>> config.increment_version()
        >>> config.version  # 2
    """

    version: int = Field(
        default=1,
        description="Record version number"
    )

    def increment_version(self) -> None:
        """Increment version and update timestamp."""
        self.version += 1
        self.mark_updated()
```

### Implementation Steps

1. **Week 4, Day 1: Implementation**
   - Add `KVRecord` base class
   - Add `VersionedKVRecord` base class
   - Add convenience methods

2. **Week 4, Day 2: Testing**
   - Test timestamp tracking
   - Test version tracking
   - Test serialization

3. **Week 4, Day 3: Cairn Migration**
   - Update cairn KV models to extend base classes
   - Remove duplicate timestamp fields

### Cairn Migration Example

**Before:**
```python
# cairn/kv_models.py
class LifecycleRecord(BaseModel):
    agent_id: str
    state: AgentState
    created_at: float = Field(default_factory=time.time)
    state_changed_at: float = Field(default_factory=time.time)
    # ...
```

**After:**
```python
# cairn/kv_models.py
from agentfs_pydantic.models import KVRecord

class LifecycleRecord(KVRecord):
    agent_id: str
    state: AgentState
    state_changed_at: float = Field(default_factory=time.time)
    # created_at and updated_at inherited from KVRecord
```

### Success Metrics
- ✅ Reduce timestamp boilerplate in cairn
- ✅ Provide standard KV record patterns
- ⚠️ Low impact (only ~10 lines saved)

---

## Opportunity 7: Query Builder Enhancements

### Overview
Add convenience methods and fluent interfaces to View for common operations.

### API Design

```python
# agentfs-pydantic/src/agentfs_pydantic/view.py (EXTEND)

from datetime import datetime, timedelta


class View(BaseModel):
    # ... existing methods ...

    def with_size_range(
        self,
        min_size: Optional[int] = None,
        max_size: Optional[int] = None
    ) -> "View":
        """Create view with size constraints.

        Examples:
            >>> # Files between 1KB and 1MB
            >>> view = view.with_size_range(1024, 1024*1024)
        """
        new_query = self.query.model_copy(update={
            "min_size": min_size,
            "max_size": max_size
        })
        return View(agent=self.agent, query=new_query)

    def with_regex(self, pattern: str) -> "View":
        """Create view with regex path filter.

        Examples:
            >>> # Python files in src/ directory
            >>> view = view.with_regex(r"^src/.*\.py$")
        """
        new_query = self.query.model_copy(update={
            "regex_pattern": pattern
        })
        return View(agent=self.agent, query=new_query)

    async def recent_files(
        self,
        max_age: timedelta | float
    ) -> list[FileEntry]:
        """Get files modified within time window.

        Args:
            max_age: Maximum age as timedelta or seconds

        Examples:
            >>> # Files modified in last hour
            >>> recent = await view.recent_files(timedelta(hours=1))
        """
        if isinstance(max_age, timedelta):
            max_age = max_age.total_seconds()

        cutoff = datetime.now().timestamp() - max_age

        files = await self.load()
        return [
            f for f in files
            if f.stats and f.stats.mtime.timestamp() >= cutoff
        ]

    async def largest_files(self, n: int = 10) -> list[FileEntry]:
        """Get N largest files.

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

        Examples:
            >>> # Total size of Python files
            >>> size = await view.with_pattern("*.py").total_size()
        """
        files = await self.load()
        return sum(f.stats.size for f in files if f.stats)

    async def group_by_extension(self) -> dict[str, list[FileEntry]]:
        """Group files by extension.

        Examples:
            >>> grouped = await view.group_by_extension()
            >>> print(f"Python files: {len(grouped['.py'])}")
        """
        files = await self.load()
        groups: dict[str, list[FileEntry]] = {}

        for file in files:
            ext = Path(file.path).suffix or "(no extension)"
            if ext not in groups:
                groups[ext] = []
            groups[ext].append(file)

        return groups
```

### Implementation Steps

1. **Week 4, Day 1-2: Implementation**
   - Add fluent helper methods
   - Add aggregation methods
   - Add grouping methods

2. **Week 4, Day 3: Testing**
   - Test each new method
   - Test chaining
   - Test edge cases

3. **Week 4, Day 4: Documentation**
   - Add examples to README
   - Update API docs

### Success Metrics
- ✅ Provide 6+ new convenience methods
- ✅ Support method chaining
- ✅ Improve developer experience

---

## Implementation Phases

### Phase 1: Foundation (Week 1)
**Focus:** Core infrastructure that other opportunities depend on

**Deliverables:**
1. ✅ Generic Repository Pattern (Opportunity 1)
   - Create `agentfs_pydantic/repository.py`
   - Migrate cairn/kv_store.py
   - Full test coverage

**Success Criteria:**
- All repository tests pass
- Cairn tests pass with new implementation
- Documentation complete

---

### Phase 2: File System Operations (Week 2)
**Focus:** File and workspace operations

**Deliverables:**
1. ✅ Workspace Materialization (Opportunity 2)
   - Create `agentfs_pydantic/materialization.py`
   - Migrate cairn/workspace.py

2. ✅ Enhanced View with Content Search (Opportunity 3)
   - Extend `agentfs_pydantic/view.py`
   - Add SearchMatch model
   - Migrate cairn search functions

**Success Criteria:**
- Materialization supports diff/merge
- Content search handles regex and patterns
- All tests pass

---

### Phase 3: Overlay Operations (Week 3)
**Focus:** High-level overlay abstractions

**Deliverables:**
1. ✅ Overlay Operations (Opportunity 4)
   - Create `agentfs_pydantic/overlay.py`
   - Migrate cairn merge logic

2. ✅ File Operations Helper (Opportunity 5)
   - Create `agentfs_pydantic/operations.py`
   - Migrate cairn external functions

**Success Criteria:**
- Overlay merge supports multiple strategies
- File operations provide clean API
- Cairn external_functions.py reduced by 60%+

---

### Phase 4: Polish & Extensions (Week 4)
**Focus:** Developer experience improvements

**Deliverables:**
1. ✅ KV Models Base Classes (Opportunity 6)
   - Extend `agentfs_pydantic/models.py`
   - Migrate cairn models

2. ✅ Query Builder Enhancements (Opportunity 7)
   - Extend `agentfs_pydantic/view.py`
   - Add convenience methods

3. ✅ Documentation & Examples
   - Complete API documentation
   - Add usage examples
   - Migration guide

**Success Criteria:**
- All enhancements documented
- Migration guide complete
- Example code for all new features

---

## Testing Strategy

### Unit Tests

```python
# agentfs-pydantic/tests/test_repository.py

import pytest
from agentfs_sdk import AgentFS
from agentfs_pydantic import AgentFSOptions
from agentfs_pydantic.repository import TypedKVRepository
from pydantic import BaseModel

class TestRecord(BaseModel):
    id: str
    value: int

@pytest.fixture
async def agent_fs():
    fs = await AgentFS.open(AgentFSOptions(id="test-repo").model_dump())
    yield fs
    await fs.close()

@pytest.mark.asyncio
async def test_save_and_load(agent_fs):
    repo = TypedKVRepository[TestRecord](agent_fs, prefix="test:")

    record = TestRecord(id="1", value=42)
    await repo.save("1", record)

    loaded = await repo.load("1", TestRecord)
    assert loaded is not None
    assert loaded.id == "1"
    assert loaded.value == 42

@pytest.mark.asyncio
async def test_list_all(agent_fs):
    repo = TypedKVRepository[TestRecord](agent_fs, prefix="test:")

    await repo.save("1", TestRecord(id="1", value=1))
    await repo.save("2", TestRecord(id="2", value=2))

    records = await repo.list_all(TestRecord)
    assert len(records) == 2

# ... more tests
```

### Integration Tests

```python
# cairn/tests/test_lifecycle_integration.py

@pytest.mark.asyncio
async def test_lifecycle_with_repository():
    """Test that lifecycle store works with new repository."""
    orchestrator = CairnOrchestrator()
    await orchestrator.initialize()

    agent_id = await orchestrator.spawn_agent("test task")

    # Verify lifecycle record exists
    record = await orchestrator.lifecycle.load(agent_id)
    assert record is not None
    assert record.agent_id == agent_id
```

### Performance Tests

```python
# agentfs-pydantic/tests/test_performance.py

@pytest.mark.asyncio
async def test_materialization_performance():
    """Test materialization of large overlay."""
    # Create overlay with 1000 files
    # Measure materialization time
    # Assert < 5 seconds for 1000 files
```

### Test Coverage Goals
- Unit tests: 90%+ coverage
- Integration tests: All major workflows
- Performance tests: All async operations

---

## Migration Guide

### For Cairn Developers

#### Step 1: Update Dependencies

```toml
# pyproject.toml
[project]
dependencies = [
    "agentfs-pydantic>=0.2.0",  # Updated version with extensions
    # ...
]
```

#### Step 2: Update Imports

**Before:**
```python
from cairn.kv_store import KVRepository
from cairn.workspace import WorkspaceMaterializer
```

**After:**
```python
from agentfs_pydantic.repository import TypedKVRepository
from agentfs_pydantic.materialization import Materializer
from agentfs_pydantic.overlay import OverlayOperations
from agentfs_pydantic.operations import FileOperations
```

#### Step 3: Simplify Code

See individual migration examples in each opportunity section above.

#### Step 4: Run Tests

```bash
# Run cairn tests to verify compatibility
pytest cairn/tests/

# Run agentfs-pydantic tests
cd agentfs-pydantic
pytest tests/
```

### For External Users

If you're building your own AgentFS application:

```python
# Example: Custom agent system using new patterns

from agentfs_sdk import AgentFS
from agentfs_pydantic import (
    AgentFSOptions,
    View,
    ViewQuery
)
from agentfs_pydantic.repository import TypedKVRepository
from agentfs_pydantic.operations import FileOperations
from agentfs_pydantic.overlay import OverlayOperations
from pydantic import BaseModel

class MyAgentState(BaseModel):
    agent_id: str
    status: str
    result: dict | None = None

async def my_agent_system():
    # Open AgentFS
    stable = await AgentFS.open(AgentFSOptions(id="stable").model_dump())
    agent = await AgentFS.open(AgentFSOptions(id="agent-1").model_dump())

    # Use typed repository
    repo = TypedKVRepository[MyAgentState](agent, prefix="agent:")
    await repo.save("agent-1", MyAgentState(
        agent_id="agent-1",
        status="running"
    ))

    # Use file operations
    ops = FileOperations(agent, base_fs=stable)
    await ops.write_file("output.txt", "Hello World")

    # Search files
    python_files = await ops.search_files("**/*.py")

    # Merge to stable when done
    overlay_ops = OverlayOperations()
    await overlay_ops.merge(agent, stable)
```

---

## Backward Compatibility

### Versioning Strategy

- **agentfs-pydantic 0.1.x**: Current version
- **agentfs-pydantic 0.2.0**: Add new modules (backward compatible)
- **agentfs-pydantic 0.3.0**: Any breaking changes (if needed)

### Compatibility Guarantees

1. **All new modules are additive** - existing code continues to work
2. **Existing API unchanged** - `View`, `ViewQuery`, models stay compatible
3. **Optional dependencies** - new features don't require changes to existing code

### Deprecation Policy

If any existing APIs need changes:
1. Deprecate old API in version N
2. Keep deprecated API working with warnings
3. Remove deprecated API in version N+2 (min 6 months)

---

## Documentation Plan

### API Documentation

```markdown
# agentfs-pydantic/docs/api/repository.md

# TypedKVRepository

Generic repository for typed KV operations.

## Usage

```python
from agentfs_pydantic.repository import TypedKVRepository

repo = TypedKVRepository[MyModel](
    storage=agent_fs,
    prefix="mydata:"
)

await repo.save("key1", MyModel(...))
record = await repo.load("key1", MyModel)
```

## API Reference

### `__init__(storage, prefix, key_builder)`
...

### `save(id, record)`
...
```

### Examples Repository

Create `agentfs-pydantic/examples/` with:
- `01_basic_repository.py`
- `02_materialization.py`
- `03_overlay_operations.py`
- `04_file_operations.py`
- `05_content_search.py`
- `06_complete_workflow.py`

### Migration Cookbook

Create `agentfs-pydantic/docs/migration.md` with before/after examples for common patterns.

---

## Success Metrics

### Quantitative Goals

1. **Code Reduction**
   - Cairn: Reduce by 250-300 lines (40% of target modules)
   - agentfs-pydantic: Add ~800 lines of reusable code

2. **Test Coverage**
   - agentfs-pydantic: Maintain 90%+ coverage
   - Cairn: Maintain 85%+ coverage

3. **Performance**
   - No regression in existing operations
   - Materialization: <5s for 1000 files
   - Search: <2s for 1000 files

4. **Documentation**
   - 100% API documentation
   - 6+ working examples
   - Complete migration guide

### Qualitative Goals

1. **Developer Experience**
   - Simpler, more intuitive APIs
   - Better error messages
   - More consistent patterns

2. **Reusability**
   - All new modules useful outside cairn
   - Clear, focused responsibilities
   - Well-documented extension points

3. **Maintainability**
   - Less code duplication
   - Clearer separation of concerns
   - Easier to test

---

## Risk Assessment

### Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Breaking changes in agentfs-sdk | High | Medium | Pin versions, comprehensive tests |
| Performance regression | Medium | Low | Performance test suite, benchmarking |
| API complexity | Medium | Medium | User testing, iterative design |
| Migration issues | High | Low | Thorough testing, clear migration guide |

### Rollback Plan

If issues arise:
1. Keep old implementations temporarily
2. Feature flag new vs old code
3. Gradual rollout (phase by phase)
4. Quick revert capability

---

## Timeline Summary

| Week | Phase | Deliverables |
|------|-------|--------------|
| 1 | Foundation | Repository pattern |
| 2 | File Ops | Materialization, Content Search |
| 3 | Overlay Ops | Overlay operations, File operations |
| 4 | Polish | KV models, Query builder, Docs |

**Total Estimated Time:** 4 weeks

---

## Next Steps

1. **Review & Approval** (2 days)
   - Review this plan
   - Gather feedback
   - Adjust priorities

2. **Setup** (1 day)
   - Create feature branch
   - Set up test infrastructure
   - Create tracking issues

3. **Begin Phase 1** (Week 1)
   - Start with Repository pattern
   - Daily progress reviews
   - Adjust as needed

---

## Appendix: File Structure After Implementation

```
agentfs-pydantic/
├── src/agentfs_pydantic/
│   ├── __init__.py
│   ├── models.py              # Extended with KVRecord base classes
│   ├── view.py                # Extended with content search & query helpers
│   ├── repository.py          # NEW: Typed KV repository
│   ├── materialization.py     # NEW: Workspace materialization
│   ├── overlay.py             # NEW: Overlay operations
│   └── operations.py          # NEW: File operations helper
├── tests/
│   ├── test_repository.py     # NEW
│   ├── test_materialization.py # NEW
│   ├── test_overlay.py        # NEW
│   ├── test_operations.py     # NEW
│   ├── test_view_extended.py # NEW
│   └── ...
├── examples/                  # NEW
│   ├── 01_basic_repository.py
│   ├── 02_materialization.py
│   ├── 03_overlay_operations.py
│   └── ...
└── docs/                      # NEW
    ├── api/
    │   ├── repository.md
    │   ├── materialization.md
    │   └── ...
    └── migration.md

cairn/
├── __init__.py               # Updated imports
├── orchestrator.py           # Simplified (~20 lines removed)
├── external_functions.py     # Simplified (~100 lines removed)
├── kv_store.py              # Simplified (~40 lines removed)
├── workspace.py             # Simplified (~60 lines removed)
├── lifecycle.py             # Minimal changes
└── ...
```

---

**End of Implementation Plan**

*This is a living document. Update as implementation progresses.*
