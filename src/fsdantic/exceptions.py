"""Domain exception hierarchy for fsdantic."""

from __future__ import annotations

from typing import Any


class FsdanticError(Exception):
    """Base exception for all fsdantic errors."""


class RepositoryError(FsdanticError):
    """Base error for repository-related operations."""


class FileSystemError(FsdanticError):
    """Base error for filesystem operations."""

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
        super().__init__(f"Key not found: {key}")
        self.key = key


class SerializationError(KVStoreError):
    """Raised when KV data serialization or deserialization fails."""


class OverlayError(FsdanticError):
    """Base error for overlay operations."""


class MergeConflictError(OverlayError):
    """Raised when overlay merge conflicts are encountered."""

    def __init__(self, message: str, conflicts: list[Any]) -> None:
        super().__init__(message)
        self.conflicts = conflicts


class MaterializationError(FsdanticError):
    """Raised when workspace materialization fails."""


class ValidationError(FsdanticError):
    """Raised when data validation fails."""


class ContentSearchError(FsdanticError):
    """Raised when content search operations fail."""
