"""Fsdantic - Type-safe Pydantic interface for AgentFS SDK."""

from .exceptions import (
    ContentSearchError,
    DirectoryNotEmptyError,
    FileExistsError,
    FileNotFoundError,
    FileSystemError,
    FsdanticError,
    InvalidPathError,
    IsADirectoryError,
    KVStoreError,
    KeyNotFoundError,
    MaterializationError,
    MergeConflictError,
    NotADirectoryError,
    OverlayError,
    PermissionError,
    SerializationError,
    ValidationError,
)
from .materialization import (
    ConflictResolution,
    FileChange,
    MaterializationResult,
    Materializer,
)
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
from .operations import FileOperations
from .overlay import (
    ConflictResolver,
    MergeConflict,
    MergeResult,
    MergeStrategy,
    OverlayOperations,
)
from .repository import NamespacedKVStore, TypedKVRepository
from .view import SearchMatch, View, ViewQuery

__version__ = "0.2.0"

__all__ = [
    # Core models
    "AgentFSOptions",
    "FileEntry",
    "FileStats",
    "KVEntry",
    "KVRecord",
    "ToolCall",
    "ToolCallStats",
    "ToolCallStatus",
    "VersionedKVRecord",
    # View and search
    "View",
    "ViewQuery",
    "SearchMatch",
    # Repository pattern
    "TypedKVRepository",
    "NamespacedKVStore",
    # Materialization
    "Materializer",
    "MaterializationResult",
    "FileChange",
    "ConflictResolution",
    # Overlay operations
    "OverlayOperations",
    "MergeStrategy",
    "MergeResult",
    "MergeConflict",
    "ConflictResolver",
    # File operations
    "FileOperations",
    # Forward-architecture exceptions
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
