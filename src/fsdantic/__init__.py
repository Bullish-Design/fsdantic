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
    KVConflictError,
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
from .files import FileManager, FileQuery
from .kv import KVManager, KVTransaction
from .operations import FileOperations
from .materialization import (
    ConflictResolution,
    FileChange,
    MaterializationManager,
    MaterializationResult,
    Materializer,
)
from .models import (
    AgentFSOptions,
    BatchItemResult,
    BatchResult,
    FileEntry,
    FileStats,
    KVEntry,
    KVRecord,
    ToolCall,
    ToolCallStats,
    ToolCallStatus,
    VersionedKVRecord,
)
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

__version__ = "0.3.0"

__all__ = [
    # Primary API
    "Fsdantic",
    "Workspace",
    # Managers
    "FileManager",
    "FileQuery",
    "KVManager",
    "KVTransaction",
    "OverlayManager",
    "OverlayOperations",
    "MaterializationManager",
    # Models
    "AgentFSOptions",
    "BatchItemResult",
    "BatchResult",
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
    "KVConflictError",
    "KVStoreError",
    "KeyNotFoundError",
    "SerializationError",
    "OverlayError",
    "MergeConflictError",
    "MaterializationError",
    "ValidationError",
    "ContentSearchError",
]
