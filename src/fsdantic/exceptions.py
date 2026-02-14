"""Domain exception hierarchy for fsdantic."""

from __future__ import annotations

from __future__ import annotations

from typing import Any


class FsdanticError(Exception):
    """Base exception for all fsdantic errors."""


class FileSystemError(FsdanticError):
    """Base exception for filesystem operation errors."""

    def __init__(
        self,
        message: str,
        path: str | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.cause = cause


class FileNotFoundError(FileSystemError):
    """File or directory not found."""


class FileExistsError(FileSystemError):
    """File or directory already exists."""


class NotADirectoryError(FileSystemError):
    """Expected directory, got file."""


class IsADirectoryError(FileSystemError):
    """Expected file, got directory."""


class DirectoryNotEmptyError(FileSystemError):
    """Cannot remove non-empty directory."""


class PermissionError(FileSystemError):
    """Operation not permitted."""


class InvalidPathError(FileSystemError):
    """Invalid path argument."""


class KVStoreError(FsdanticError):
    """Base for KV store errors."""


class KeyNotFoundError(KVStoreError):
    """Key not found in KV store."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Key not found: {key}")
        self.key = key


class SerializationError(KVStoreError):
    """Failed to serialize/deserialize value."""


class OverlayError(FsdanticError):
    """Base for overlay operation errors."""

class FileSystemError(FsdanticError):
    """Base error for filesystem operations.

    Attributes:
        path: Filesystem path associated with the failure, if known.
        cause: Original low-level exception, if available.
    """

    def __init__(
        self,
        message: str,
        path: str | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.cause = cause


class FileNotFoundError(FileSystemError):
    """Raised when a requested file or directory does not exist."""



class MergeConflictError(OverlayError):
    """Error due to merge conflicts."""

    def __init__(self, message: str, conflicts: list[Any]) -> None:
class FileExistsError(FileSystemError):
    """Raised when a file or directory already exists."""


class NotADirectoryError(FileSystemError):
    """Raised when a directory operation targets a non-directory path."""


class IsADirectoryError(FileSystemError):
    """Raised when a file operation targets a directory path."""


class DirectoryNotEmptyError(FileSystemError):
    """Raised when attempting to remove a non-empty directory."""


class PermissionError(FileSystemError):
    """Raised when filesystem permissions deny an operation."""


class InvalidPathError(FileSystemError):
    """Raised when a provided filesystem path is invalid."""


class KVStoreError(FsdanticError):
    """Base error for key-value store operations."""


class KeyNotFoundError(KVStoreError):
    """Raised when a key does not exist in the KV store."""

    def __init__(self, key: str) -> None:
        message = f"Key not found: {key}"
        super().__init__(message)
        self.key = key


class SerializationError(KVStoreError):
    """Raised when KV data serialization or deserialization fails."""


class OverlayError(FsdanticError):
    """Base error for overlay operations."""


class MergeConflictError(OverlayError):
    """Raised when overlay merge conflicts are encountered."""

    def __init__(self, message: str, conflicts: list) -> None:
        super().__init__(message)
        self.conflicts = conflicts


class MaterializationError(FsdanticError):
    """Raised when workspace materialization fails."""


class ContentSearchError(FsdanticError):
    """Error during content search operations."""

class ValidationError(FsdanticError):
    """Raised when data validation fails."""


class ContentSearchError(FsdanticError):
    """Raised when content search operations fail."""
