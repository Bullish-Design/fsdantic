# Fsdantic Technical Specification

**Version:** 0.3.0
**Date:** 2026-02-14
**Status:** Active Development

---

## Overview

Fsdantic is a comprehensive Pydantic-based interface library for the AgentFS SDK. It provides type-safe models, high-level abstractions, and powerful utilities for working with AgentFS virtual filesystems, overlays, and key-value stores.

### Key Features

1. **Type-Safe Models** - Pydantic models for all AgentFS concepts
2. **Repository Pattern** - Generic typed repositories for KV operations
3. **Query Interface** - Powerful filesystem query and search capabilities
4. **Workspace Materialization** - Convert virtual filesystems to disk
5. **Overlay Operations** - High-level overlay merging and management
6. **File Operations** - Simplified file I/O with automatic fallthrough
7. **Content Search** - Regex and pattern-based file content searching

---

## Architecture

### Module Structure

```
fsdantic/
├── __init__.py           # Public API exports
├── models.py             # Core Pydantic models
├── view.py               # Filesystem query interface
├── repository.py         # Generic KV repository pattern
├── materialization.py    # Workspace materialization
├── overlay.py            # Overlay operations
└── operations.py         # File operations helper
```

### Core Dependencies

- **agentfs-sdk** >= 0.6.0 - AgentFS virtual filesystem
- **pydantic** >= 2.0.0 - Data validation and serialization

---

## Module Specifications

### 1. models.py

#### Core Models

**AgentFSOptions**
- Purpose: Options for opening AgentFS instances
- Fields:
  - `id: Optional[str]` - Agent identifier
  - `path: Optional[str]` - Custom database path
- Validation: Ensures at least one of `id` or `path` is provided

**FileStats**
- Purpose: File metadata representation
- Fields:
  - `size: int` - File size in bytes
  - `mtime: datetime` - Last modification time
  - `is_file: bool` - True if regular file
  - `is_directory: bool` - True if directory

**FileEntry**
- Purpose: Filesystem entry with optional content
- Fields:
  - `path: str` - File path
  - `stats: Optional[FileStats]` - File metadata
  - `content: Optional[str | bytes]` - File content

**ToolCall**
- Purpose: Tool/function call tracking
- Fields:
  - `id: int` - Unique identifier
  - `name: str` - Tool name
  - `parameters: dict` - Input parameters
  - `result: Optional[dict]` - Call result
  - `error: Optional[str]` - Error message
  - `status: ToolCallStatus` - Call status (pending/success/error)
  - `started_at: datetime` - Start timestamp
  - `completed_at: Optional[datetime]` - Completion timestamp
- Computed: `duration_ms` - Call duration in milliseconds

**KVEntry**
- Purpose: Key-value store entry
- Fields:
  - `key: str` - Entry key
  - `value: Any` - Entry value (JSON-serializable)

#### Base Classes for KV Records

**KVRecord**
- Purpose: Base model for KV store records with timestamp tracking
- Fields:
  - `created_at: float` - Creation timestamp (Unix epoch)
  - `updated_at: float` - Last update timestamp (Unix epoch)
- Methods:
  - `mark_updated()` - Update the timestamp

**VersionedKVRecord**
- Purpose: KV record with version tracking
- Extends: `KVRecord`
- Fields:
  - `version: int` - Record version number (default: 1)
- Methods:
  - `increment_version()` - Increment version and update timestamp

---

### 2. view.py

#### ViewQuery Model

**Purpose:** Query specification for filesystem views

**Fields:**
- `path_pattern: str` - Glob pattern (default: "*")
- `recursive: bool` - Search subdirectories (default: True)
- `include_content: bool` - Load file contents (default: False)
- `include_stats: bool` - Include file metadata (default: True)
- `regex_pattern: Optional[str]` - Additional regex filter
- `max_size: Optional[int]` - Maximum file size filter
- `min_size: Optional[int]` - Minimum file size filter

**Content Search Fields:**
- `content_pattern: Optional[str]` - Simple string pattern for content search
- `content_regex: Optional[str]` - Regex pattern for content search
- `case_sensitive: bool` - Case-sensitive search (default: True)
- `whole_word: bool` - Match whole words only (default: False)
- `max_matches_per_file: Optional[int]` - Limit matches per file

**Methods:**
- `matches_path(path: str) -> bool` - Check if path matches glob pattern
- `matches_regex(path: str) -> bool` - Check if path matches regex

#### View Class

**Purpose:** Filesystem view with query and search capabilities

**Fields:**
- `agent: AgentFS` - AgentFS instance
- `query: ViewQuery` - Query specification

**Core Methods:**
- `load() -> list[FileEntry]` - Load matching files
- `filter(predicate) -> list[FileEntry]` - Filter with custom predicate
- `count() -> int` - Count matching files

**Fluent API Methods:**
- `with_pattern(pattern: str) -> View` - Create view with new pattern
- `with_content(include: bool) -> View` - Toggle content loading
- `with_size_range(min_size, max_size) -> View` - Set size constraints
- `with_regex(pattern: str) -> View` - Add regex filter

**Content Search Methods:**
- `search_content() -> list[SearchMatch]` - Search file contents
- `files_containing(pattern: str, regex: bool) -> list[FileEntry]` - Find files with pattern

**Aggregation Methods:**
- `recent_files(max_age: timedelta | float) -> list[FileEntry]` - Files modified recently
- `largest_files(n: int) -> list[FileEntry]` - Top N largest files
- `total_size() -> int` - Total size of matching files
- `group_by_extension() -> dict[str, list[FileEntry]]` - Group by extension

#### SearchMatch Dataclass

**Purpose:** Represents a content search match

**Fields:**
- `file: str` - File path
- `line: int` - Line number
- `text: str` - Line text
- `column: Optional[int]` - Column position
- `match_start: Optional[int]` - Match start position
- `match_end: Optional[int]` - Match end position

---

### 3. repository.py

#### TypedKVRepository[T]

**Purpose:** Generic typed repository for KV operations

**Type Parameter:** `T` - Pydantic model type

**Constructor:**
```python
def __init__(
    storage: AgentFS,
    prefix: str = "",
    key_builder: Optional[Callable[[str], str]] = None
)
```

**Methods:**
- `save(id: str, record: T) -> None` - Save record
- `load(id: str, model_type: Type[T]) -> Optional[T]` - Load record
- `delete(id: str) -> None` - Delete record
- `list_all(model_type: Type[T]) -> list[T]` - List all records
- `exists(id: str) -> bool` - Check if record exists
- `list_ids() -> list[str]` - List all record IDs

**Implementation Details:**
- Uses AgentFS KV store `set()` and `get()` methods
- Serializes models using `model_dump()` and `model_validate()`
- Filters by prefix using `kv.list(prefix)`

#### NamespacedKVStore

**Purpose:** Convenience wrapper for creating namespaced repositories

**Methods:**
- `namespace(prefix: str) -> TypedKVRepository` - Create namespaced repository

---

### 4. materialization.py

#### Materializer

**Purpose:** Materialize AgentFS overlays to local filesystem

**Constructor:**
```python
def __init__(
    conflict_resolution: ConflictResolution = ConflictResolution.OVERWRITE,
    progress_callback: Optional[Callable[[str, int, int], None]] = None
)
```

**Methods:**
- `materialize(agent_fs, target_path, base_fs?, filters?, clean?) -> MaterializationResult` - Materialize to disk
- `diff(overlay_fs, base_fs, path?) -> list[FileChange]` - Compute changes

**ConflictResolution Enum:**
- `OVERWRITE` - Overlay wins
- `SKIP` - Keep existing file
- `ERROR` - Raise exception

**MaterializationResult:**
- `target_path: Path` - Destination path
- `files_written: int` - Number of files written
- `bytes_written: int` - Total bytes written
- `changes: list[FileChange]` - List of changes
- `skipped: list[str]` - Skipped files
- `errors: list[tuple[str, str]]` - Errors encountered

**FileChange:**
- `path: str` - File path
- `change_type: str` - "added", "modified", or "deleted"
- `old_size: Optional[int]` - Previous size
- `new_size: Optional[int]` - New size

---

### 5. overlay.py

#### OverlayOperations

**Purpose:** High-level overlay filesystem operations

**Constructor:**
```python
def __init__(
    strategy: MergeStrategy = MergeStrategy.OVERWRITE,
    conflict_resolver: Optional[ConflictResolver] = None
)
```

**Methods:**
- `merge(source, target, path?, strategy?) -> MergeResult` - Merge overlays
- `list_changes(overlay, path?) -> list[str]` - List overlay changes
- `reset_overlay(overlay, paths?) -> int` - Reset overlay to base state

**MergeStrategy Enum:**
- `OVERWRITE` - Overlay wins on conflicts
- `PRESERVE` - Base wins on conflicts
- `ERROR` - Raise on conflicts
- `CALLBACK` - Use callback for conflicts

**MergeResult:**
- `files_merged: int` - Number of files merged
- `conflicts: list[MergeConflict]` - Conflicts encountered
- `errors: list[tuple[str, str]]` - Errors encountered

**MergeConflict:**
- `path: str` - File path
- `overlay_size: int` - Overlay file size
- `base_size: int` - Base file size
- `overlay_content: bytes` - Overlay content
- `base_content: bytes` - Base content

**ConflictResolver Protocol:**
```python
class ConflictResolver(Protocol):
    def resolve(conflict: MergeConflict) -> bytes:
        ...
```

---

### 6. operations.py

#### FileOperations

**Purpose:** Simplified file operations with overlay fallthrough

**Constructor:**
```python
def __init__(agent_fs: AgentFS, base_fs: Optional[AgentFS] = None)
```

**Methods:**
- `read_file(path, encoding?) -> str | bytes` - Read file with fallthrough
- `write_file(path, content, encoding?) -> None` - Write file to overlay
- `file_exists(path) -> bool` - Check existence in overlay or base
- `list_dir(path, output?) -> list[str]` - List directory contents in deterministic sorted order (`output`: name|relative|full)
- `search_files(pattern, recursive?) -> list[str]` - Search files by pattern
- `stat(path) -> Stats` - Get file statistics with fallthrough
- `remove(path, recursive=False) -> None` - Remove file or directory with explicit directory policy
- `tree(path?, max_depth?) -> dict` - Get stable tree nodes: `{name, path, type, children}` (deterministic ordering)

**Fallthrough Behavior:**
- Read operations try overlay first, then fall through to base
- Write operations always write to overlay
- File existence checks both layers

---

## Usage Patterns

### Pattern 1: Typed Repository

```python
from fsdantic import TypedKVRepository
from pydantic import BaseModel

class UserRecord(BaseModel):
    user_id: str
    name: str
    email: str

repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")
await repo.save("alice", UserRecord(user_id="alice", name="Alice", email="alice@example.com"))
user = await repo.load("alice", UserRecord)
```

### Pattern 2: Content Search

```python
from fsdantic import View, ViewQuery

view = View(
    agent=agent_fs,
    query=ViewQuery(
        path_pattern="**/*.py",
        content_regex=r"class\s+\w+",
        include_content=True
    )
)
matches = await view.search_content()
for match in matches:
    print(f"{match.file}:{match.line}: {match.text}")
```

### Pattern 3: Workspace Materialization

```python
from fsdantic import Materializer

materializer = Materializer()
result = await materializer.materialize(
    agent_fs=agent,
    target_path=Path("./workspace"),
    base_fs=stable
)
print(f"Written {result.files_written} files ({result.bytes_written} bytes)")
```

### Pattern 4: Overlay Merging

```python
from fsdantic import OverlayOperations, MergeStrategy

ops = OverlayOperations(strategy=MergeStrategy.OVERWRITE)
result = await ops.merge(source=agent_fs, target=stable_fs)
print(f"Merged {result.files_merged} files with {len(result.conflicts)} conflicts")
```

### Pattern 5: File Operations

```python
from fsdantic import FileOperations

ops = FileOperations(agent_fs, base_fs=stable_fs)
content = await ops.read_file("config.json")
await ops.write_file("output.txt", "Hello World")
files = await ops.search_files("**/*.py")
```

---

## Performance Considerations

### Large Filesystem Operations

- Use `include_content=False` when content is not needed
- Use `include_stats=False` when statistics are not needed
- Leverage `max_size` and `min_size` filters to reduce results
- Use progress callbacks for long-running materializations

### Content Search

- Content search loads all matching files into memory
- Use path patterns to limit search scope
- Consider `max_matches_per_file` for large files
- Binary files are automatically skipped

### Repository Operations

- `list_all()` loads all records into memory
- Use `list_ids()` for listing without loading data
- Consider pagination for large datasets

---

## Error Handling

### Common Exceptions

- `FileNotFoundError` - File or directory not found
- `ValueError` - Invalid query parameters
- `ValidationError` - Pydantic model validation failed

### Best Practices

1. Always use try-except for file operations
2. Validate models before saving to repository
3. Check `exists()` before loading to avoid exceptions
4. Handle `MaterializationResult.errors` for failed files

---

## Version History

### 0.3.0 (2026-02-14)
- **Breaking:** Curated and tightened top-level import surface in `fsdantic.__init__`; consumers should import only names included in `fsdantic.__all__`.
- Added explicit export boundaries for supported managers, models, and exception hierarchy.

### 0.2.0 (2026-02-14)
- Added repository pattern (`TypedKVRepository`, `NamespacedKVStore`)
- Added materialization support (`Materializer`)
- Added overlay operations (`OverlayOperations`)
- Added file operations helper (`FileOperations`)
- Extended View with content search and aggregations
- Added KV base classes (`KVRecord`, `VersionedKVRecord`)

### 0.1.0 (Initial Release)
- Core models (`AgentFSOptions`, `FileStats`, `FileEntry`, `ToolCall`, etc.)
- Basic View and ViewQuery functionality
- Glob pattern matching
- Size filtering

---

## Future Enhancements

- Streaming content search for large files
- Parallel materialization for faster copying
- Incremental materialization (only changed files)
- Transaction support for KV operations
- Caching layer for frequently accessed files
- Watch mode for real-time filesystem monitoring
