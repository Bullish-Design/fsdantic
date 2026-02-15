"""Domain exception hierarchy for fsdantic."""

from __future__ import annotations

from typing import Any


def _safe_context_value(value: Any) -> Any:
    """Return a JSON-friendly representation for context values."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, dict):
        return {str(key): _safe_context_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_context_value(item) for item in value]
    return repr(value)


class FsdanticError(Exception):
    """Base exception for all fsdantic errors."""

    default_code = "FSDANTIC_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        context: dict[str, Any] | None = None,
        cause: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code or self.default_code
        self.context = context
        self.cause = cause

    def to_dict(self) -> dict[str, Any]:
        """Serialize this error into a stable machine-readable dictionary."""
        payload: dict[str, Any] = {
            "type": self.__class__.__name__,
            "message": str(self.args[0]) if self.args else "",
            "code": self.code,
        }
        if self.context:
            payload["context"] = _safe_context_value(self.context)
        if self.cause is not None:
            payload["cause"] = {
                "type": self.cause.__class__.__name__,
                "message": str(self.cause),
            }
        return payload

    def __str__(self) -> str:
        message = str(self.args[0]) if self.args else self.__class__.__name__
        details = []
        if self.context:
            details.append(f"context={_safe_context_value(self.context)}")
        if self.cause is not None:
            details.append(f"cause={self.cause.__class__.__name__}: {self.cause}")
        if not details:
            return message
        return f"{message} | {'; '.join(details)}"

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.to_dict()!r})"


class RepositoryError(FsdanticError):
    """Base error for repository-related operations."""

    default_code = "REPOSITORY_ERROR"


class FileSystemError(FsdanticError):
    """Base error for filesystem operations.

    Attributes:
        path: Filesystem path associated with the failure, if known.
        cause: Original low-level exception, if available.
    """

    default_code = "FS_ERROR"

    def __init__(
        self,
        message: str,
        path: str | None = None,
        cause: Any | None = None,
        *,
        context: dict[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
        merged_context = {"path": path} if path is not None else {}
        if context:
            merged_context.update(context)
        super().__init__(
            message,
            code=code,
            context=merged_context or None,
            cause=cause,
        )
        self.path = path


class FileNotFoundError(FileSystemError):
    """Raised when a requested file or directory does not exist."""

    default_code = "FS_NOT_FOUND"


class FileExistsError(FileSystemError):
    """Raised when a file or directory already exists."""

    default_code = "FS_ALREADY_EXISTS"


class NotADirectoryError(FileSystemError):
    """Raised when a directory operation targets a non-directory path."""

    default_code = "FS_NOT_A_DIRECTORY"


class IsADirectoryError(FileSystemError):
    """Raised when a file operation targets a directory path."""

    default_code = "FS_IS_A_DIRECTORY"


class DirectoryNotEmptyError(FileSystemError):
    """Raised when attempting to remove a non-empty directory."""

    default_code = "FS_DIRECTORY_NOT_EMPTY"


class PermissionError(FileSystemError):
    """Raised when filesystem permissions deny an operation."""

    default_code = "FS_PERMISSION_DENIED"


class InvalidPathError(FileSystemError):
    """Raised when a provided filesystem path is invalid."""

    default_code = "FS_INVALID_PATH"


class KVStoreError(FsdanticError):
    """Base error for key-value store operations."""

    default_code = "KV_ERROR"


class KVConflictError(KVStoreError):
    """Raised when optimistic concurrency checks fail for a KV write.

    Attributes:
        code: Machine-readable error code for programmatic handling.
        key: Conflicting key.
        expected_version: Version/etag expected by the caller.
        actual_version: Current version/etag observed in storage.
    """

    default_code = "KV_CONFLICT"

    def __init__(
        self,
        key: str,
        expected_version: int | None,
        actual_version: int | None,
        *,
        cause: Any | None = None,
    ) -> None:
        super().__init__(
            "KV version conflict for key "
            f"'{key}' (expected={expected_version}, actual={actual_version})",
            context={
                "key": key,
                "expected_version": expected_version,
                "actual_version": actual_version,
            },
            cause=cause,
        )
        self.key = key
        self.expected_version = expected_version
        self.actual_version = actual_version


class KeyNotFoundError(KVStoreError):
    """Raised when a key does not exist in the KV store."""

    default_code = "KV_KEY_NOT_FOUND"

    def __init__(self, key: str, cause: Any | None = None) -> None:
        super().__init__(f"Key not found: {key}", context={"key": key}, cause=cause)
        self.key = key


class SerializationError(KVStoreError):
    """Raised when KV data serialization or deserialization fails."""

    default_code = "KV_SERIALIZATION_ERROR"


class OverlayError(FsdanticError):
    """Base error for overlay operations."""

    default_code = "OVERLAY_ERROR"


class MergeConflictError(OverlayError):
    """Raised when overlay merge conflicts are encountered."""

    default_code = "OVERLAY_CONFLICT"

    def __init__(
        self,
        message: str,
        conflicts: list[Any],
        cause: Any | None = None,
    ) -> None:
        super().__init__(message, context={"conflicts": conflicts}, cause=cause)
        self.conflicts = conflicts

    def __str__(self) -> str:
        """Return only the base message for backward compatibility."""
        return str(self.args[0]) if self.args else self.__class__.__name__


class MaterializationError(FsdanticError):
    """Raised when workspace materialization fails."""

    default_code = "MATERIALIZATION_ERROR"


class ValidationError(FsdanticError):
    """Raised when data validation fails."""

    default_code = "VALIDATION_ERROR"


class ContentSearchError(FsdanticError):
    """Raised when content search operations fail."""

    default_code = "CONTENT_SEARCH_ERROR"
