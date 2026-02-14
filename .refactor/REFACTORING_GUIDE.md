# FSdantic Refactoring Guide

## Executive Summary

This document outlines a comprehensive refactoring of the FSdantic library to create a unified, ergonomic API with a single entry point and consistent patterns. This refactoring addresses the issues identified in `CODE_REVIEW.md` and establishes a clear architectural foundation for the library.

**Key Goals:**
- Provide a single, obvious entry point (`Fsdantic.open() -> Workspace`)
- Unify error handling with proper exception translation at boundaries
- Establish consistent API patterns for paths, content, and operations
- Create clear layering between high-level and low-level APIs
- Maintain performance while improving ergonomics

**Timeline:** 3-4 days of focused development work

---

## Current State Analysis

### Existing Architecture

The current FSdantic library consists of multiple peer abstractions:

```
fsdantic/
├── models.py          # Pydantic models for data structures
├── view.py            # View & ViewQuery for file querying
├── operations.py      # FileOperations for file I/O
├── repository.py      # TypedKVRepository for KV storage
├── overlay.py         # OverlayOperations for merging
├── materialization.py # Materializer for disk export
└── exceptions.py      # Custom exceptions (underutilized)
```

### Problems Identified

1. **No Single Entry Point**
   - Users must import and wire together multiple classes
   - Unclear which abstraction to use for common tasks
   - Example imports: `View`, `FileOperations`, `TypedKVRepository`, `OverlayOperations`

2. **Fragmented Error Model**
   - AgentFS uses `ErrnoException` (POSIX-style errors)
   - FSdantic defines custom exceptions but rarely uses them
   - Errors leak through abstraction boundaries
   - No consistent error translation layer

3. **API Inconsistencies**
   - Path handling: some methods strip leading `/`, others don't
   - Content handling: inconsistent binary/text encoding patterns
   - Some operations use AgentFS directly, bypassing FSdantic abstractions

4. **Distributed Performance Ergonomics**
   - Batch operations scattered across modules
   - No unified caching strategy
   - Performance tuning requires deep knowledge

5. **Unclear Layering**
   - Can "drop down" to AgentFS but path is unclear
   - No clear separation between high-level and low-level APIs
   - Difficult to know when to use which abstraction

---

## Target Architecture

### The 90% Mental Model

```python
from fsdantic import Fsdantic

# Single entry point
workspace = await Fsdantic.open(id="my-agent")

# Domain-specific namespaces
await workspace.files.write("/config.json", {"key": "value"})
content = await workspace.files.read("/config.json")
files = await workspace.files.search("*.py")

# KV operations
await workspace.kv.set("user:123", {"name": "Alice"})
user = await workspace.kv.get("user:123")

# Overlay operations
result = await workspace.overlay.merge(source=other_workspace)
changes = await workspace.overlay.list_changes()

# Materialization
result = await workspace.materialize.to_disk(Path("./output"))

# Raw AgentFS access when needed
await workspace.raw.fs.stat("/some/path")
await workspace.raw.kv.list("prefix:")
```

### New Module Structure

```
fsdantic/
├── __init__.py           # Public API exports
├── workspace.py          # NEW: Workspace façade class
├── client.py             # NEW: Fsdantic client factory
├── files.py              # NEW: Unified file operations (replaces operations.py + view.py)
├── kv.py                 # NEW: Unified KV operations (replaces repository.py)
├── overlay.py            # REFACTORED: Simplified overlay ops
├── materialization.py    # REFACTORED: Simplified materializer
├── models.py             # KEEP: Core Pydantic models
├── exceptions.py         # ENHANCED: Comprehensive error hierarchy
└── _internal/
    ├── errors.py         # Error translation layer
    └── compat.py         # AgentFS compatibility utilities
```

### Class Hierarchy

```
Fsdantic (factory)
    └── .open() -> Workspace

Workspace (façade)
    ├── .files -> FileManager
    ├── .kv -> KVManager
    ├── .overlay -> OverlayManager
    ├── .materialize -> MaterializationManager
    └── .raw -> AgentFS (direct access)

FileManager
    ├── .read(path, encoding=...) -> str | bytes
    ├── .write(path, content, encoding=...)
    ├── .exists(path) -> bool
    ├── .stat(path) -> FileStats
    ├── .list_dir(path) -> list[str]
    ├── .remove(path)
    ├── .search(pattern, **filters) -> list[FileEntry]
    ├── .query(**kwargs) -> View  # Advanced queries
    └── .tree(path, max_depth) -> dict

KVManager
    ├── .get(key, default=None) -> T | None
    ├── .set(key, value)
    ├── .delete(key)
    ├── .exists(key) -> bool
    ├── .list(prefix) -> list[KVEntry]
    ├── .repository(prefix, model_type) -> TypedKVRepository[T]
    └── .namespace(prefix) -> KVManager  # Scoped manager

OverlayManager
    ├── .merge(source, strategy=...) -> MergeResult
    ├── .list_changes(path="/") -> list[str]
    └── .reset(paths=None) -> int

MaterializationManager
    ├── .to_disk(target_path, clean=True, filters=...) -> MaterializationResult
    ├── .diff(base_workspace) -> list[FileChange]
    └── .preview(base_workspace) -> list[FileChange]
```

---

## Refactoring Work Items

### Phase 1: Error Handling Foundation (Day 1 - Morning)

**Objective:** Establish comprehensive error handling before refactoring operations.

#### 1.1 Enhance Exception Hierarchy

**File:** `src/fsdantic/exceptions.py`

```python
# Base exception
class FsdanticError(Exception):
    """Base exception for all FSdantic errors."""
    pass

# Domain-specific exceptions
class FileSystemError(FsdanticError):
    """Base for filesystem operation errors."""
    def __init__(self, message: str, path: str | None = None, cause: Exception | None = None):
        super().__init__(message)
        self.path = path
        self.cause = cause

class FileNotFoundError(FileSystemError):
    """File or directory not found."""
    pass

class FileExistsError(FileSystemError):
    """File or directory already exists."""
    pass

class NotADirectoryError(FileSystemError):
    """Expected directory, got file."""
    pass

class IsADirectoryError(FileSystemError):
    """Expected file, got directory."""
    pass

class DirectoryNotEmptyError(FileSystemError):
    """Cannot remove non-empty directory."""
    pass

class PermissionError(FileSystemError):
    """Operation not permitted."""
    pass

class InvalidPathError(FileSystemError):
    """Invalid path argument."""
    pass

class KVStoreError(FsdanticError):
    """Base for KV store errors."""
    pass

class KeyNotFoundError(KVStoreError):
    """Key not found in store."""
    def __init__(self, key: str):
        super().__init__(f"Key not found: {key}")
        self.key = key

class SerializationError(KVStoreError):
    """Failed to serialize/deserialize value."""
    pass

class OverlayError(FsdanticError):
    """Base for overlay operation errors."""
    pass

class MergeConflictError(OverlayError):
    """Merge conflicts detected."""
    def __init__(self, message: str, conflicts: list):
        super().__init__(message)
        self.conflicts = conflicts

class MaterializationError(FsdanticError):
    """Error during workspace materialization."""
    pass

class ValidationError(FsdanticError):
    """Model validation failed."""
    pass

class ContentSearchError(FsdanticError):
    """Error during content search."""
    pass
```

#### 1.2 Create Error Translation Layer

**File:** `src/fsdantic/_internal/errors.py`

```python
"""Error translation from AgentFS to FSdantic exceptions."""

from agentfs_sdk import ErrnoException
from fsdantic.exceptions import *

def translate_agentfs_error(error: ErrnoException, context: str = "") -> FsdanticError:
    """Translate AgentFS ErrnoException to FSdantic exception.

    Args:
        error: The AgentFS error to translate
        context: Optional context string for better error messages

    Returns:
        Appropriate FSdantic exception
    """
    path = error.path
    message = f"{error.message}"
    if context:
        message = f"{context}: {message}"

    error_map = {
        "ENOENT": FileNotFoundError,
        "EEXIST": FileExistsError,
        "ENOTDIR": NotADirectoryError,
        "EISDIR": IsADirectoryError,
        "ENOTEMPTY": DirectoryNotEmptyError,
        "EPERM": PermissionError,
        "EINVAL": InvalidPathError,
    }

    exception_class = error_map.get(error.code, FileSystemError)
    return exception_class(message, path=path, cause=error)

def handle_agentfs_errors(func):
    """Decorator to translate AgentFS errors to FSdantic errors."""
    import functools

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except ErrnoException as e:
            raise translate_agentfs_error(e, func.__name__) from e

    return wrapper
```

**Testing:**
- Unit tests for each error code translation
- Verify error messages include context
- Check that original error is preserved as `cause`

---

### Phase 2: Core Workspace & Client (Day 1 - Afternoon)

**Objective:** Create the unified entry point and façade.

#### 2.1 Create Fsdantic Client Factory

**File:** `src/fsdantic/client.py`

```python
"""Fsdantic client factory."""

from typing import Optional
from agentfs_sdk import AgentFS, AgentFSOptions as AgentFSOptionsRaw
from .workspace import Workspace
from .models import AgentFSOptions

class Fsdantic:
    """Factory for creating FSdantic workspaces.

    Examples:
        >>> # Open by ID
        >>> workspace = await Fsdantic.open(id="my-agent")
        >>>
        >>> # Open by path
        >>> workspace = await Fsdantic.open(path="./data/agent.db")
        >>>
        >>> # Using Pydantic model
        >>> options = AgentFSOptions(id="my-agent")
        >>> workspace = await Fsdantic.open_with_options(options)
    """

    @staticmethod
    async def open(
        id: Optional[str] = None,
        path: Optional[str] = None,
    ) -> Workspace:
        """Open a workspace.

        Args:
            id: Agent identifier (creates .agentfs/{id}.db)
            path: Custom database path

        Returns:
            Initialized Workspace instance

        Raises:
            ValidationError: If neither id nor path provided
        """
        options = AgentFSOptions(id=id, path=path)
        return await Fsdantic.open_with_options(options)

    @staticmethod
    async def open_with_options(options: AgentFSOptions) -> Workspace:
        """Open a workspace with validated options.

        Args:
            options: Validated AgentFSOptions model

        Returns:
            Initialized Workspace instance
        """
        # Convert to AgentFS options
        raw_options = AgentFSOptionsRaw(
            id=options.id,
            path=options.path
        )

        # Open AgentFS
        agentfs = await AgentFS.open(raw_options)

        # Wrap in Workspace
        return Workspace(agentfs)
```

#### 2.2 Create Workspace Façade

**File:** `src/fsdantic/workspace.py`

```python
"""Workspace façade providing unified access to all FSdantic operations."""

from typing import TYPE_CHECKING
from agentfs_sdk import AgentFS

if TYPE_CHECKING:
    from .files import FileManager
    from .kv import KVManager
    from .overlay import OverlayManager
    from .materialization import MaterializationManager

class Workspace:
    """Unified workspace for file and KV operations.

    The Workspace provides a clean, organized API for all FSdantic operations
    through domain-specific managers.

    Examples:
        >>> workspace = await Fsdantic.open(id="my-agent")
        >>>
        >>> # File operations
        >>> await workspace.files.write("/config.json", {"key": "value"})
        >>> data = await workspace.files.read("/config.json")
        >>>
        >>> # KV operations
        >>> await workspace.kv.set("user:123", {"name": "Alice"})
        >>> user = await workspace.kv.get("user:123")
        >>>
        >>> # Advanced operations
        >>> result = await workspace.overlay.merge(other_workspace)
        >>> await workspace.materialize.to_disk(Path("./output"))
        >>>
        >>> # Direct AgentFS access
        >>> stats = await workspace.raw.fs.stat("/path")
    """

    def __init__(self, agentfs: AgentFS):
        """Initialize workspace with AgentFS instance.

        Args:
            agentfs: Underlying AgentFS instance
        """
        self._agentfs = agentfs
        self._files: Optional[FileManager] = None
        self._kv: Optional[KVManager] = None
        self._overlay: Optional[OverlayManager] = None
        self._materialize: Optional[MaterializationManager] = None

    @property
    def files(self) -> "FileManager":
        """Access file operations."""
        if self._files is None:
            from .files import FileManager
            self._files = FileManager(self._agentfs)
        return self._files

    @property
    def kv(self) -> "KVManager":
        """Access KV store operations."""
        if self._kv is None:
            from .kv import KVManager
            self._kv = KVManager(self._agentfs)
        return self._kv

    @property
    def overlay(self) -> "OverlayManager":
        """Access overlay operations."""
        if self._overlay is None:
            from .overlay import OverlayManager
            self._overlay = OverlayManager(self._agentfs)
        return self._overlay

    @property
    def materialize(self) -> "MaterializationManager":
        """Access materialization operations."""
        if self._materialize is None:
            from .materialization import MaterializationManager
            self._materialize = MaterializationManager(self._agentfs)
        return self._materialize

    @property
    def raw(self) -> AgentFS:
        """Direct access to underlying AgentFS instance.

        Use this when you need direct AgentFS API access for advanced
        operations not covered by FSdantic abstractions.
        """
        return self._agentfs

    async def close(self) -> None:
        """Close the workspace and underlying database connection."""
        await self._agentfs.close()

    async def __aenter__(self) -> "Workspace":
        """Context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """Context manager exit."""
        await self.close()
```

**Testing:**
- Test workspace creation with id
- Test workspace creation with path
- Test workspace creation with options model
- Verify lazy loading of managers
- Test context manager protocol
- Test raw access

---

### Phase 3: File Manager (Day 2 - Morning)

**Objective:** Consolidate `View` and `FileOperations` into unified `FileManager`.

#### 3.1 Create FileManager

**File:** `src/fsdantic/files.py`

```python
"""Unified file operations manager."""

from pathlib import Path
from typing import Any, Callable, Optional
from agentfs_sdk import AgentFS
from ._internal.errors import handle_agentfs_errors
from .models import FileEntry, FileStats
from .view import View, ViewQuery
from .exceptions import *

class FileManager:
    """Unified manager for file operations.

    Provides both simple file I/O and advanced querying capabilities.

    Examples:
        >>> # Simple operations
        >>> await files.write("/config.json", {"key": "value"})
        >>> data = await files.read("/config.json")
        >>> exists = await files.exists("/config.json")
        >>>
        >>> # Search
        >>> py_files = await files.search("*.py")
        >>>
        >>> # Advanced queries
        >>> view = files.query(
        ...     pattern="*.py",
        ...     min_size=1024,
        ...     content_regex=r"class \w+"
        ... )
        >>> results = await view.load()
    """

    def __init__(self, agentfs: AgentFS):
        """Initialize file manager.

        Args:
            agentfs: Underlying AgentFS instance
        """
        self._agentfs = agentfs

    @handle_agentfs_errors
    async def read(
        self,
        path: str,
        *,
        encoding: Optional[str] = "utf-8"
    ) -> str | bytes:
        """Read file content.

        Args:
            path: File path to read
            encoding: Text encoding (None for binary mode)

        Returns:
            File content as string (if encoding) or bytes

        Raises:
            FileNotFoundError: If file doesn't exist
            IsADirectoryError: If path is a directory

        Examples:
            >>> text = await files.read("/config.json")
            >>> binary = await files.read("/image.png", encoding=None)
        """
        return await self._agentfs.fs.read_file(path, encoding=encoding)

    @handle_agentfs_errors
    async def write(
        self,
        path: str,
        content: str | bytes | dict | list,
        *,
        encoding: str = "utf-8"
    ) -> None:
        """Write content to file.

        Args:
            path: File path to write
            content: Content to write (auto-serializes dicts/lists to JSON)
            encoding: Text encoding for string content

        Raises:
            PermissionError: If write not permitted

        Examples:
            >>> await files.write("/config.json", {"key": "value"})
            >>> await files.write("/data.txt", "Hello World")
            >>> await files.write("/image.png", image_bytes, encoding=None)
        """
        # Auto-serialize JSON objects
        if isinstance(content, (dict, list)):
            import json
            content = json.dumps(content, indent=2)

        # Encode strings
        if isinstance(content, str):
            content = content.encode(encoding)

        await self._agentfs.fs.write_file(path, content)

    @handle_agentfs_errors
    async def exists(self, path: str) -> bool:
        """Check if file or directory exists.

        Args:
            path: Path to check

        Returns:
            True if exists, False otherwise

        Examples:
            >>> if await files.exists("/config.json"):
            ...     print("Config found")
        """
        try:
            await self._agentfs.fs.stat(path)
            return True
        except Exception:
            return False

    @handle_agentfs_errors
    async def stat(self, path: str) -> FileStats:
        """Get file statistics.

        Args:
            path: File path

        Returns:
            FileStats model with metadata

        Raises:
            FileNotFoundError: If file doesn't exist

        Examples:
            >>> stats = await files.stat("/data.txt")
            >>> print(f"Size: {stats.size} bytes")
        """
        raw_stats = await self._agentfs.fs.stat(path)
        return FileStats(
            size=raw_stats.size,
            mtime=raw_stats.mtime,
            is_file=raw_stats.is_file(),
            is_directory=raw_stats.is_directory(),
        )

    @handle_agentfs_errors
    async def list_dir(self, path: str = "/") -> list[str]:
        """List directory contents.

        Args:
            path: Directory path

        Returns:
            List of entry names (not full paths)

        Raises:
            FileNotFoundError: If directory doesn't exist
            NotADirectoryError: If path is a file

        Examples:
            >>> entries = await files.list_dir("/data")
            >>> for entry in entries:
            ...     print(entry)
        """
        return await self._agentfs.fs.readdir(path)

    @handle_agentfs_errors
    async def remove(self, path: str, *, recursive: bool = False) -> None:
        """Remove file or directory.

        Args:
            path: Path to remove
            recursive: If True, remove directories recursively

        Raises:
            FileNotFoundError: If path doesn't exist
            IsADirectoryError: If path is directory and recursive=False

        Examples:
            >>> await files.remove("/temp.txt")
            >>> await files.remove("/temp_dir", recursive=True)
        """
        stats = await self._agentfs.fs.stat(path)

        if stats.is_directory():
            if not recursive:
                raise IsADirectoryError(
                    "Use recursive=True to remove directories",
                    path=path
                )
            await self._agentfs.fs.rm(path.lstrip("/"), recursive=True)
        else:
            await self._agentfs.fs.unlink(path)

    async def search(
        self,
        pattern: str,
        *,
        recursive: bool = True,
        include_content: bool = False,
        **filters
    ) -> list[FileEntry]:
        """Search for files matching pattern.

        Args:
            pattern: Glob pattern (e.g., "*.py", "**/*.json")
            recursive: Search recursively
            include_content: Load file contents
            **filters: Additional filters (min_size, max_size, regex_pattern, etc.)

        Returns:
            List of matching FileEntry objects

        Examples:
            >>> # Find Python files
            >>> py_files = await files.search("*.py")
            >>>
            >>> # Find large JSON files
            >>> large_json = await files.search(
            ...     "*.json",
            ...     min_size=10000
            ... )
        """
        view = self.query(
            path_pattern=pattern,
            recursive=recursive,
            include_content=include_content,
            **filters
        )
        return await view.load()

    def query(self, **kwargs) -> View:
        """Create advanced query view.

        Args:
            **kwargs: ViewQuery parameters

        Returns:
            View instance for advanced querying

        Examples:
            >>> view = files.query(
            ...     path_pattern="**/*.py",
            ...     content_regex=r"class \w+",
            ...     min_size=100
            ... )
            >>> results = await view.load()
            >>> count = await view.count()
        """
        query = ViewQuery(**kwargs)
        return View(agent=self._agentfs, query=query)

    @handle_agentfs_errors
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
            Nested dict representing tree structure

        Examples:
            >>> tree = await files.tree("/src", max_depth=2)
            >>> print(tree)
        """
        async def walk(current_path: str, depth: int = 0):
            if max_depth is not None and depth >= max_depth:
                return {}

            result = {}
            try:
                entries = await self._agentfs.fs.readdir(current_path)
                for entry_name in entries:
                    entry_path = f"{current_path.rstrip('/')}/{entry_name}"
                    stat = await self._agentfs.fs.stat(entry_path)

                    if stat.is_directory():
                        result[entry_name] = await walk(entry_path, depth + 1)
                    else:
                        result[entry_name] = None
            except Exception:
                pass

            return result

        return await walk(path)
```

**Testing:**
- Test read with text encoding
- Test read with binary mode
- Test write with strings, bytes, dict, list
- Test JSON auto-serialization
- Test exists for files and directories
- Test stat returns correct FileStats
- Test list_dir
- Test remove file
- Test remove directory with recursive
- Test search with various patterns
- Test query builder
- Test tree generation
- Test error translation (file not found, etc.)

---

### Phase 4: KV Manager (Day 2 - Afternoon)

**Objective:** Consolidate KV operations into unified `KVManager`.

#### 4.1 Create KVManager

**File:** `src/fsdantic/kv.py`

```python
"""Unified KV store manager."""

from typing import Any, Generic, Optional, Type, TypeVar
from agentfs_sdk import AgentFS
from pydantic import BaseModel
from ._internal.errors import handle_agentfs_errors
from .repository import TypedKVRepository
from .models import KVEntry
from .exceptions import *

T = TypeVar("T", bound=BaseModel)

class KVManager:
    """Unified manager for key-value operations.

    Provides simple KV operations plus typed repositories for Pydantic models.

    Examples:
        >>> # Simple operations
        >>> await kv.set("user:123", {"name": "Alice"})
        >>> user = await kv.get("user:123")
        >>>
        >>> # Typed repositories
        >>> class User(BaseModel):
        ...     name: str
        ...     email: str
        >>>
        >>> users = kv.repository("user:", User)
        >>> await users.save("alice", User(name="Alice", email="alice@example.com"))
        >>> alice = await users.load("alice", User)
        >>>
        >>> # Namespaced managers
        >>> users_kv = kv.namespace("user:")
        >>> await users_kv.set("alice", {"name": "Alice"})
    """

    def __init__(self, agentfs: AgentFS, prefix: str = ""):
        """Initialize KV manager.

        Args:
            agentfs: Underlying AgentFS instance
            prefix: Optional key prefix for namespacing
        """
        self._agentfs = agentfs
        self._prefix = prefix

    def _make_key(self, key: str) -> str:
        """Apply prefix to key."""
        return f"{self._prefix}{key}"

    @handle_agentfs_errors
    async def get(
        self,
        key: str,
        default: Optional[T] = None
    ) -> Optional[T]:
        """Get value by key.

        Args:
            key: Key to retrieve
            default: Default value if key not found

        Returns:
            Value or default if not found

        Examples:
            >>> value = await kv.get("config:theme")
            >>> value = await kv.get("missing", default="fallback")
        """
        full_key = self._make_key(key)
        return await self._agentfs.kv.get(full_key, default)

    @handle_agentfs_errors
    async def set(self, key: str, value: Any) -> None:
        """Set key-value pair.

        Args:
            key: Key to set
            value: Value (JSON-serializable)

        Raises:
            SerializationError: If value cannot be serialized

        Examples:
            >>> await kv.set("user:123", {"name": "Alice"})
            >>> await kv.set("count", 42)
        """
        full_key = self._make_key(key)
        try:
            await self._agentfs.kv.set(full_key, value)
        except Exception as e:
            raise SerializationError(f"Failed to serialize value for key '{key}'") from e

    @handle_agentfs_errors
    async def delete(self, key: str) -> None:
        """Delete key-value pair.

        Args:
            key: Key to delete

        Examples:
            >>> await kv.delete("user:123")
        """
        full_key = self._make_key(key)
        await self._agentfs.kv.delete(full_key)

    @handle_agentfs_errors
    async def exists(self, key: str) -> bool:
        """Check if key exists.

        Args:
            key: Key to check

        Returns:
            True if key exists

        Examples:
            >>> if await kv.exists("user:123"):
            ...     print("User exists")
        """
        full_key = self._make_key(key)
        value = await self._agentfs.kv.get(full_key)
        return value is not None

    @handle_agentfs_errors
    async def list(self, prefix: str = "") -> list[KVEntry]:
        """List all keys matching prefix.

        Args:
            prefix: Prefix to match (in addition to manager prefix)

        Returns:
            List of KVEntry objects

        Examples:
            >>> entries = await kv.list("user:")
            >>> for entry in entries:
            ...     print(f"{entry.key}: {entry.value}")
        """
        full_prefix = self._make_key(prefix)
        items = await self._agentfs.kv.list(full_prefix)

        # Convert to KVEntry models
        entries = []
        for item in items:
            # Remove manager prefix from key for cleaner API
            key = item["key"]
            if self._prefix and key.startswith(self._prefix):
                key = key[len(self._prefix):]

            entries.append(KVEntry(key=key, value=item["value"]))

        return entries

    def repository(self, prefix: str, model_type: Type[T]) -> TypedKVRepository[T]:
        """Create typed repository for Pydantic models.

        Args:
            prefix: Key prefix for this repository
            model_type: Pydantic model class

        Returns:
            TypedKVRepository instance

        Examples:
            >>> class User(BaseModel):
            ...     name: str
            ...     email: str
            >>>
            >>> users = kv.repository("user:", User)
            >>> await users.save("alice", User(name="Alice", email="a@example.com"))
            >>> alice = await users.load("alice", User)
        """
        full_prefix = self._make_key(prefix)
        return TypedKVRepository(self._agentfs, prefix=full_prefix)

    def namespace(self, prefix: str) -> "KVManager":
        """Create namespaced KV manager.

        Args:
            prefix: Namespace prefix

        Returns:
            New KVManager with prefix

        Examples:
            >>> users = kv.namespace("user:")
            >>> await users.set("alice", {"name": "Alice"})
            >>> # Actual key will be "user:alice"
        """
        full_prefix = self._make_key(prefix)
        return KVManager(self._agentfs, prefix=full_prefix)
```

**Testing:**
- Test get with existing and missing keys
- Test get with default value
- Test set with various value types
- Test delete
- Test exists
- Test list with and without prefix
- Test repository creation
- Test namespace creation
- Test nested namespaces
- Test error handling

---

### Phase 5: Overlay & Materialization Managers (Day 3 - Morning)

**Objective:** Wrap existing overlay and materialization with manager pattern.

#### 5.1 Refactor OverlayManager

**File:** `src/fsdantic/overlay.py` (refactored)

```python
"""Overlay operations manager."""

from typing import Optional
from agentfs_sdk import AgentFS
from ._internal.errors import handle_agentfs_errors
from .exceptions import *

# Keep existing MergeStrategy, MergeConflict, MergeResult, ConflictResolver

class OverlayManager:
    """Manager for overlay operations.

    Examples:
        >>> result = await overlay.merge(
        ...     source_workspace,
        ...     strategy=MergeStrategy.OVERWRITE
        ... )
        >>> changes = await overlay.list_changes()
        >>> await overlay.reset()
    """

    def __init__(self, agentfs: AgentFS):
        """Initialize overlay manager.

        Args:
            agentfs: Underlying AgentFS instance
        """
        self._agentfs = agentfs
        # Reuse existing OverlayOperations internally
        from . import OverlayOperations as _OverlayOps
        self._ops = _OverlayOps()

    async def merge(
        self,
        source: "Workspace",
        *,
        path: str = "/",
        strategy: Optional[MergeStrategy] = None
    ) -> MergeResult:
        """Merge source workspace into this workspace.

        Args:
            source: Source Workspace to merge from
            path: Root path to merge
            strategy: Merge strategy (default: OVERWRITE)

        Returns:
            MergeResult with statistics

        Examples:
            >>> result = await workspace.overlay.merge(other_workspace)
            >>> print(f"Merged {result.files_merged} files")
        """
        return await self._ops.merge(
            source.raw,  # Get AgentFS from workspace
            self._agentfs,
            path=path,
            strategy=strategy
        )

    async def list_changes(self, path: str = "/") -> list[str]:
        """List files that exist in overlay.

        Args:
            path: Root path to check

        Returns:
            List of file paths in overlay

        Examples:
            >>> changes = await overlay.list_changes()
            >>> print(f"{len(changes)} files modified")
        """
        return await self._ops.list_changes(self._agentfs, path)

    async def reset(self, paths: Optional[list[str]] = None) -> int:
        """Reset overlay to base state.

        Args:
            paths: Specific paths to reset (None = reset all)

        Returns:
            Number of files reset

        Examples:
            >>> # Reset all changes
            >>> count = await overlay.reset()
            >>>
            >>> # Reset specific files
            >>> count = await overlay.reset(["/config.json"])
        """
        return await self._ops.reset_overlay(self._agentfs, paths)
```

#### 5.2 Refactor MaterializationManager

**File:** `src/fsdantic/materialization.py` (refactored)

```python
"""Materialization manager."""

from pathlib import Path
from typing import Optional
from agentfs_sdk import AgentFS
from .view import ViewQuery
from .exceptions import *

# Keep existing ConflictResolution, FileChange, MaterializationResult, Materializer

class MaterializationManager:
    """Manager for materialization operations.

    Examples:
        >>> # Export to disk
        >>> result = await materialize.to_disk(Path("./output"))
        >>>
        >>> # Preview changes
        >>> changes = await materialize.preview(base_workspace)
        >>>
        >>> # Generate diff
        >>> diff = await materialize.diff(base_workspace)
    """

    def __init__(self, agentfs: AgentFS):
        """Initialize materialization manager.

        Args:
            agentfs: Underlying AgentFS instance
        """
        self._agentfs = agentfs
        # Reuse existing Materializer internally
        from . import Materializer as _Materializer
        self._materializer = _Materializer()

    async def to_disk(
        self,
        target_path: Path,
        *,
        base_workspace: Optional["Workspace"] = None,
        filters: Optional[ViewQuery] = None,
        clean: bool = True
    ) -> MaterializationResult:
        """Materialize workspace to local disk.

        Args:
            target_path: Destination directory
            base_workspace: Optional base workspace to materialize first
            filters: Optional filters for which files to materialize
            clean: If True, remove target_path contents first

        Returns:
            MaterializationResult with statistics

        Examples:
            >>> result = await materialize.to_disk(Path("./output"))
            >>> print(f"Wrote {result.files_written} files")
        """
        base_fs = base_workspace.raw if base_workspace else None

        return await self._materializer.materialize(
            agent_fs=self._agentfs,
            target_path=target_path,
            base_fs=base_fs,
            filters=filters,
            clean=clean
        )

    async def diff(
        self,
        base_workspace: "Workspace",
        path: str = "/"
    ) -> list[FileChange]:
        """Compute changes between this and base workspace.

        Args:
            base_workspace: Base workspace to compare against
            path: Root path to compare

        Returns:
            List of FileChange objects

        Examples:
            >>> changes = await materialize.diff(base_workspace)
            >>> for change in changes:
            ...     print(f"{change.change_type}: {change.path}")
        """
        return await self._materializer.diff(
            overlay_fs=self._agentfs,
            base_fs=base_workspace.raw,
            path=path
        )

    async def preview(
        self,
        base_workspace: "Workspace",
        path: str = "/"
    ) -> list[FileChange]:
        """Preview changes (alias for diff).

        Args:
            base_workspace: Base workspace to compare against
            path: Root path to compare

        Returns:
            List of FileChange objects
        """
        return await self.diff(base_workspace, path)
```

**Testing:**
- Test overlay merge
- Test overlay list_changes
- Test overlay reset
- Test materialize to_disk
- Test materialize diff
- Test materialize preview
- Test with base workspaces

---

### Phase 6: Public API & Exports (Day 3 - Afternoon)

**Objective:** Update `__init__.py` with new API surface.

#### 6.1 Update Main __init__.py

**File:** `src/fsdantic/__init__.py`

```python
"""Fsdantic - Type-safe Pydantic interface for AgentFS SDK.

The primary entry point is the Fsdantic class:

    >>> from fsdantic import Fsdantic
    >>>
    >>> workspace = await Fsdantic.open(id="my-agent")
    >>>
    >>> # File operations
    >>> await workspace.files.write("/config.json", {"key": "value"})
    >>> data = await workspace.files.read("/config.json")
    >>>
    >>> # KV operations
    >>> await workspace.kv.set("user:123", {"name": "Alice"})
    >>> user = await workspace.kv.get("user:123")
"""

# Primary API
from .client import Fsdantic
from .workspace import Workspace

# Managers (available via workspace properties)
from .files import FileManager
from .kv import KVManager
from .overlay import OverlayManager
from .materialization import MaterializationManager

# Models
from .models import (
    AgentFSOptions,
    FileEntry,
    FileStats,
    KVEntry,
    KVRecord,
    ToolCall,
    ToolCallStats,
    ToolCallStatus,
    VersionedKVRecord,
)

# Advanced features
from .view import View, ViewQuery, SearchMatch
from .repository import TypedKVRepository, NamespacedKVStore

# Overlay & Materialization
from .overlay import (
    MergeStrategy,
    MergeResult,
    MergeConflict,
    ConflictResolver,
)
from .materialization import (
    MaterializationResult,
    FileChange,
    ConflictResolution,
    Materializer,  # For advanced use
)

# Exceptions
from .exceptions import (
    FsdanticError,
    FileSystemError,
    FileNotFoundError,
    FileExistsError,
    NotADirectoryError,
    IsADirectoryError,
    DirectoryNotEmptyError,
    PermissionError,
    InvalidPathError,
    KVStoreError,
    KeyNotFoundError,
    SerializationError,
    OverlayError,
    MergeConflictError,
    MaterializationError,
    ValidationError,
    ContentSearchError,
)

__version__ = "0.3.0"  # Major version bump for breaking changes

__all__ = [
    # Primary API
    "Fsdantic",
    "Workspace",
    # Managers
    "FileManager",
    "KVManager",
    "OverlayManager",
    "MaterializationManager",
    # Models
    "AgentFSOptions",
    "FileEntry",
    "FileStats",
    "KVEntry",
    "KVRecord",
    "ToolCall",
    "ToolCallStats",
    "ToolCallStatus",
    "VersionedKVRecord",
    # Advanced
    "View",
    "ViewQuery",
    "SearchMatch",
    "TypedKVRepository",
    "NamespacedKVStore",
    # Overlay
    "MergeStrategy",
    "MergeResult",
    "MergeConflict",
    "ConflictResolver",
    # Materialization
    "MaterializationResult",
    "FileChange",
    "ConflictResolution",
    "Materializer",
    # Exceptions
    "FsdanticError",
    "FileSystemError",
    "FileNotFoundError",
    "FileExistsError",
    "NotADirectoryError",
    "IsADirectoryError",
    "DirectoryNotEmptyError",
    "PermissionError",
    "InvalidPathError",
    "KVStoreError",
    "KeyNotFoundError",
    "SerializationError",
    "OverlayError",
    "MergeConflictError",
    "MaterializationError",
    "ValidationError",
    "ContentSearchError",
]
```

#### 6.2 Create _internal Package

**Files to create:**
- `src/fsdantic/_internal/__init__.py` (empty)
- `src/fsdantic/_internal/errors.py` (created in Phase 1)
- `src/fsdantic/_internal/compat.py` (for AgentFS compatibility helpers if needed)

**Testing:**
- Test all public imports
- Verify version bump
- Check that deprecated APIs are removed

---

### Phase 7: Documentation & Examples (Day 4 - Morning)

**Objective:** Update documentation and examples for new API.

#### 7.1 Update README.md

Add comprehensive examples showing:
- Quick start with new API
- File operations
- KV operations
- Overlay operations
- Materialization
- Advanced usage with View
- Error handling

#### 7.2 Create Migration Guide

**File:** `MIGRATION.md`

Document API changes:
- Old: `View(agent, query)` → New: `workspace.files.query()`
- Old: `FileOperations(agent)` → New: `workspace.files`
- Old: `TypedKVRepository(agent, prefix)` → New: `workspace.kv.repository(prefix, model)`
- Old: `OverlayOperations().merge()` → New: `workspace.overlay.merge()`
- Old: `Materializer().materialize()` → New: `workspace.materialize.to_disk()`

#### 7.3 Update Examples

**File:** `examples/basic_usage.py` (rewrite)

```python
"""Example usage of FSdantic library."""

import asyncio
from pathlib import Path
from fsdantic import Fsdantic

async def main():
    """Demonstrate new unified API."""

    # Single entry point
    async with await Fsdantic.open(id="demo") as workspace:
        # File operations
        await workspace.files.write("/config.json", {"theme": "dark"})
        config = await workspace.files.read("/config.json")
        print(f"Config: {config}")

        # Search files
        py_files = await workspace.files.search("*.py")
        print(f"Found {len(py_files)} Python files")

        # KV operations
        await workspace.kv.set("user:alice", {"name": "Alice", "age": 30})
        user = await workspace.kv.get("user:alice")
        print(f"User: {user}")

        # Materialization
        result = await workspace.materialize.to_disk(Path("./output"))
        print(f"Materialized {result.files_written} files")

if __name__ == "__main__":
    asyncio.run(main())
```

**Testing:**
- Run all examples
- Verify examples work with new API
- Check that documentation is accurate

---

### Phase 8: Testing & Validation (Day 4 - Afternoon)

**Objective:** Comprehensive testing of refactored code.

#### 8.1 Unit Tests

**Create/Update Test Files:**

1. **`tests/test_workspace.py`**
   - Test Fsdantic.open() with id
   - Test Fsdantic.open() with path
   - Test workspace.files lazy loading
   - Test workspace.kv lazy loading
   - Test workspace.overlay lazy loading
   - Test workspace.materialize lazy loading
   - Test workspace.raw access
   - Test context manager protocol

2. **`tests/test_files.py`**
   - Test FileManager.read() (text and binary)
   - Test FileManager.write() (all content types)
   - Test FileManager.exists()
   - Test FileManager.stat()
   - Test FileManager.list_dir()
   - Test FileManager.remove() (file and directory)
   - Test FileManager.search()
   - Test FileManager.query()
   - Test FileManager.tree()
   - Test error handling

3. **`tests/test_kv.py`**
   - Test KVManager.get()
   - Test KVManager.set()
   - Test KVManager.delete()
   - Test KVManager.exists()
   - Test KVManager.list()
   - Test KVManager.repository()
   - Test KVManager.namespace()
   - Test nested namespaces

4. **`tests/test_overlay.py`**
   - Test OverlayManager.merge()
   - Test OverlayManager.list_changes()
   - Test OverlayManager.reset()

5. **`tests/test_materialization.py`**
   - Test MaterializationManager.to_disk()
   - Test MaterializationManager.diff()
   - Test MaterializationManager.preview()

6. **`tests/test_errors.py`**
   - Test error translation from AgentFS
   - Test each exception type
   - Test error messages include context
   - Test error cause preservation

#### 8.2 Integration Tests

**Create:** `tests/test_integration_refactored.py`

Test complete workflows:
- Create workspace, write files, read files, materialize
- Create workspace, use KV, merge with another workspace
- Error handling across manager boundaries
- Context manager cleanup

#### 8.3 Performance Tests

**Update:** `tests/test_performance.py`

Ensure refactoring doesn't degrade performance:
- Benchmark file operations
- Benchmark KV operations
- Benchmark search operations
- Compare with baseline

#### 8.4 Backwards Compatibility Tests

Create deprecation tests if we decide to support a transition period:
- Old imports still work (with warnings)
- Old API patterns map to new API

**Test Execution:**
```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=fsdantic --cov-report=html

# Run performance tests
pytest tests/test_performance.py -v
```

---

## Testing & Validation Summary

### Test Coverage Goals

- **Unit Test Coverage:** 90%+
- **Integration Test Coverage:** 80%+
- **Critical Paths:** 100%

### Validation Checklist

- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] Performance tests show no regression
- [ ] Examples run without errors
- [ ] Documentation is accurate
- [ ] Type hints are complete
- [ ] Error messages are clear and actionable
- [ ] Edge cases are handled

### Manual Testing

1. **Install locally and test:**
   ```bash
   pip install -e .
   python examples/basic_usage.py
   ```

2. **Test error scenarios:**
   - File not found
   - Permission errors
   - Invalid paths
   - Serialization errors

3. **Test performance:**
   - Large file operations
   - Bulk KV operations
   - Complex queries

---

## Migration Strategy

### For New Projects

Simply use the new API:

```python
from fsdantic import Fsdantic

workspace = await Fsdantic.open(id="my-agent")
await workspace.files.write("/config.json", data)
```

### For Existing Code (If Needed)

Since this is a brand new library with zero users, **no migration needed**.

However, if we want to provide a transition period:

1. **Deprecation warnings:**
   ```python
   # In old modules
   import warnings

   def __init__(...):
       warnings.warn(
           "FileOperations is deprecated, use workspace.files instead",
           DeprecationWarning,
           stacklevel=2
       )
   ```

2. **Compatibility layer:**
   - Keep old modules for one version
   - Map old APIs to new APIs
   - Remove in next major version

---

## Timeline & Phases

### Day 1: Foundation
- **Morning:** Error handling (Phase 1)
- **Afternoon:** Workspace & Client (Phase 2)

### Day 2: Core Managers
- **Morning:** FileManager (Phase 3)
- **Afternoon:** KVManager (Phase 4)

### Day 3: Advanced Features
- **Morning:** Overlay & Materialization (Phase 5)
- **Afternoon:** Public API (Phase 6)

### Day 4: Completion
- **Morning:** Documentation (Phase 7)
- **Afternoon:** Testing (Phase 8)

### Total Estimated Time
**3-4 days** of focused development work

---

## Success Criteria

### Architecture Goals
- [x] Single entry point (`Fsdantic.open()`)
- [x] Domain-specific managers (`files`, `kv`, `overlay`, `materialize`)
- [x] Consistent error handling
- [x] Clear API patterns
- [x] Raw AgentFS access when needed

### Code Quality
- [ ] 90%+ test coverage
- [ ] Type hints on all public APIs
- [ ] Comprehensive docstrings
- [ ] Clear error messages

### Documentation
- [ ] Updated README with examples
- [ ] API reference documentation
- [ ] Migration guide (if needed)
- [ ] Examples run successfully

### User Experience
- [ ] Intuitive API that "just works"
- [ ] Clear error messages
- [ ] Predictable behavior
- [ ] Good performance

---

## Risk Mitigation

### Potential Risks

1. **Breaking existing code**
   - **Mitigation:** This is a new library with zero users, so acceptable
   - **Fallback:** Could provide compatibility layer if needed

2. **Performance regression**
   - **Mitigation:** Performance tests in Phase 8
   - **Fallback:** Optimize hot paths identified by benchmarks

3. **Incomplete error coverage**
   - **Mitigation:** Comprehensive error translation tests
   - **Fallback:** Add missing error cases as discovered

4. **API design issues**
   - **Mitigation:** Follow CODE_REVIEW.md recommendations closely
   - **Fallback:** Iterate on API based on testing feedback

---

## Next Steps

1. **Review this guide** with stakeholders
2. **Set up development branch** (`refactor/unified-api`)
3. **Begin Phase 1** (Error handling)
4. **Iterate through phases** following the timeline
5. **Conduct final review** before merging
6. **Update version** to 0.3.0
7. **Publish** refactored library

---

## Appendix: API Comparison

### Before (Current)

```python
from agentfs_sdk import AgentFS
from fsdantic import View, ViewQuery, FileOperations, TypedKVRepository

# Multiple steps to get started
options = AgentFSOptions(id="agent")
agentfs = await AgentFS.open(options.model_dump())

# Different APIs for different operations
view = View(agentfs, ViewQuery(path_pattern="*.py"))
files = await view.load()

ops = FileOperations(agentfs)
content = await ops.read_file("/config.json")

repo = TypedKVRepository(agentfs, prefix="user:")
await repo.save("alice", user)
```

### After (Target)

```python
from fsdantic import Fsdantic

# Single entry point
workspace = await Fsdantic.open(id="agent")

# Unified, organized API
files = await workspace.files.search("*.py")
content = await workspace.files.read("/config.json")

await workspace.kv.repository("user:", User).save("alice", user)
# Or simpler:
await workspace.kv.set("user:alice", user_dict)
```

### Improvement Summary

- **1 import** instead of 5+
- **1 entry point** instead of multiple classes to instantiate
- **Organized by domain** instead of scattered
- **Consistent patterns** across all operations
- **Easier to discover** what operations are available

---

## Conclusion

This refactoring transforms FSdantic from a collection of loosely coupled utilities into a cohesive, well-designed library with a clear mental model. The unified API makes it easy to get started while still providing access to advanced features when needed.

The phased approach ensures we build a solid foundation (error handling) before adding features, and comprehensive testing validates correctness throughout the process.
