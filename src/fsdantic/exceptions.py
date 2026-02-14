"""Custom exceptions for fsdantic library."""

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


class RepositoryError(FsdanticError):
    """Error in repository operations."""


class MaterializationError(FsdanticError):
    """Error during workspace materialization."""


class MergeConflictError(OverlayError):
    """Error due to merge conflicts."""

    def __init__(self, message: str, conflicts: list[Any]) -> None:
        super().__init__(message)
        self.conflicts = conflicts


class ValidationError(FsdanticError):
    """Error during model validation."""


class ContentSearchError(FsdanticError):
    """Error during content search operations."""
