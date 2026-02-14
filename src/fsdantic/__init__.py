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
from .client import Fsdantic
from .materialization import (
    ConflictResolution,
    FileChange,
    MaterializationManager,
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
from .files import FileManager, FileQuery
from .kv import KVManager
from .overlay import (
    ConflictResolver,
    MergeConflict,
    MergeResult,
    MergeStrategy,
    OverlayManager,
    OverlayOperations,
)
from .repository import NamespacedKVStore, TypedKVRepository
from .workspace import Workspace
from .view import SearchMatch, View, ViewQuery

__version__ = "0.2.0"

__all__ = [
    # Core models
    "Fsdantic",
    "Workspace",
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
    "MaterializationManager",
    "MaterializationResult",
    "FileChange",
    "ConflictResolution",
    # Overlay operations
    "OverlayManager",
    "OverlayOperations",
    "MergeStrategy",
    "MergeResult",
    "MergeConflict",
    "ConflictResolver",
    # File operations
    "FileManager",
    "FileQuery",
    "KVManager",
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
