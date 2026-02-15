"""Pydantic models for AgentFS SDK."""

import time
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from ._internal.paths import normalize_path


class AgentFSOptions(BaseModel):
    """Options for opening an AgentFS filesystem.

    Either `id` or `path` must be provided.

    Examples:
        >>> options = AgentFSOptions(id="my-agent")
        >>> options = AgentFSOptions(path="./data/mydb.db")
    """

    id: Optional[str] = Field(
        None,
        description="Agent identifier (creates .agentfs/{id}.db)"
    )
    path: Optional[str] = Field(
        None,
        description="Custom database path"
    )

    @field_validator("id", "path", mode="before")
    @classmethod
    def validate_id_or_path(cls, v: Any) -> Optional[str]:
        """Validate selector type and emptiness."""
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("Selector values must be strings")
        if v is not None and not v.strip():
            raise ValueError("Selector values cannot be empty")
        return v

    @model_validator(mode="after")
    def validate_exclusive_selector(self) -> "AgentFSOptions":
        """Require exactly one selector for onboarding."""
        if not self.id and not self.path:
            raise ValueError("Either 'id' or 'path' must be provided")
        if self.id and self.path:
            raise ValueError("Provide exactly one of 'id' or 'path', not both")
        return self


class FileStats(BaseModel):
    """File metadata from filesystem stat operation.

    Examples:
        >>> stats = FileStats(
        ...     size=1024,
        ...     mtime=datetime.now(),
        ...     is_file=True,
        ...     is_directory=False
        ... )
    """

    size: int = Field(description="File size in bytes")
    mtime: datetime = Field(description="Last modification time")
    is_file: bool = Field(description="True if entry is a file")
    is_directory: bool = Field(description="True if entry is a directory")

    def is_dir(self) -> bool:
        """Alias for is_directory."""
        return self.is_directory


class ToolCall(BaseModel):
    """Represents a tool/function call in the system.

    Examples:
        >>> call = ToolCall(
        ...     id=1,
        ...     name="search",
        ...     parameters={"query": "Python"},
        ...     result={"results": ["result1", "result2"]},
        ...     status="success",
        ...     started_at=datetime.now(),
        ...     completed_at=datetime.now()
        ... )
    """

    id: int = Field(description="Unique call identifier")
    name: str = Field(description="Tool/function name")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Input parameters"
    )
    result: Optional[dict[str, Any]] = Field(
        None,
        description="Call result (for successful calls)"
    )
    error: Optional[str] = Field(
        None,
        description="Error message (for failed calls)"
    )
    status: "ToolCallStatus" = Field(description="Call status: 'pending', 'success', or 'error'")
    started_at: datetime = Field(description="Call start timestamp")
    completed_at: Optional[datetime] = Field(
        None,
        description="Call completion timestamp"
    )
    explicit_duration_ms: Optional[float] = Field(
        None,
        alias="duration_ms",
        description="Call duration in milliseconds"
    )

    @field_validator("status", mode="before")
    @classmethod
    def coerce_legacy_status(cls, value: Any) -> Any:
        """Coerce legacy status strings into canonical enum values."""
        if isinstance(value, ToolCallStatus):
            return value
        if not isinstance(value, str):
            return value

        normalized = value.strip().lower()
        legacy_map = {
            "ok": ToolCallStatus.SUCCESS,
            "done": ToolCallStatus.SUCCESS,
            "failed": ToolCallStatus.ERROR,
            "failure": ToolCallStatus.ERROR,
            "in_progress": ToolCallStatus.PENDING,
        }
        return legacy_map.get(normalized, normalized)

    @model_validator(mode="after")
    def validate_status_consistency(self) -> "ToolCall":
        """Enforce consistency between status and result/error payloads."""
        if self.status == ToolCallStatus.ERROR and not self.error:
            raise ValueError("error is required when status is 'error'")
        if self.status == ToolCallStatus.SUCCESS and self.result is None:
            raise ValueError("result is required when status is 'success'")
        return self

    @computed_field
    @property
    def duration_ms(self) -> Optional[float]:
        """Return explicit duration when provided, otherwise compute from timestamps."""
        if self.explicit_duration_ms is not None:
            return self.explicit_duration_ms
        if self.completed_at is None:
            return None
        delta = self.completed_at - self.started_at
        return delta.total_seconds() * 1000


class ToolCallStatus(str, Enum):
    """Enumerates supported tool call statuses."""

    PENDING = "pending"
    SUCCESS = "success"
    ERROR = "error"


class ToolCallStats(BaseModel):
    """Statistics for a specific tool/function.

    Examples:
        >>> stats = ToolCallStats(
        ...     name="search",
        ...     total_calls=100,
        ...     successful=95,
        ...     failed=5,
        ...     avg_duration_ms=123.45
        ... )
    """

    name: str = Field(description="Tool/function name")
    total_calls: int = Field(description="Total number of calls")
    successful: int = Field(description="Number of successful calls")
    failed: int = Field(description="Number of failed calls")
    avg_duration_ms: float = Field(description="Average call duration in milliseconds")


class KVEntry(BaseModel):
    """Key-value store entry.

    Examples:
        >>> entry = KVEntry(key="user:123", value={"name": "Alice", "age": 30})
    """

    key: str = Field(description="Entry key")
    value: Any = Field(description="Entry value (JSON-serializable)")


class BatchItemResult(BaseModel):
    """Outcome for a single item in a batch operation."""

    index: int = Field(description="Original position in the caller input list")
    key_or_path: str = Field(description="Input key/path/identifier for this result")
    ok: bool = Field(description="True when the item operation succeeded")
    value: Any = Field(default=None, description="Result value for successful items")
    error: Optional[str] = Field(default=None, description="Error message for failed items")


class BatchResult(BaseModel):
    """Deterministic aggregate result for a batch operation.

    ``items`` is always returned in the same order as the corresponding input list.
    """

    items: list[BatchItemResult] = Field(
        default_factory=list,
        description="Per-item outcomes preserving caller input order",
    )


class FileEntry(BaseModel):
    """Filesystem entry with path and optional metadata.

    Examples:
        >>> entry = FileEntry(
        ...     path="/data/config.json",
        ...     stats=FileStats(
        ...         size=1024,
        ...         mtime=datetime.now(),
        ...         is_file=True,
        ...         is_directory=False
        ...     )
        ... )
    """

    path: str = Field(description="File path")

    @field_validator("path", mode="before")
    @classmethod
    def normalize_entry_path(cls, value: Any) -> str:
        """Normalize file paths so API outputs are consistent."""
        if not isinstance(value, str):
            raise ValueError("path must be a string")
        return normalize_path(value)
    stats: Optional[FileStats] = Field(
        None,
        description="File statistics/metadata"
    )
    content: Optional[str | bytes] = Field(
        None,
        description="File content (if loaded)"
    )


class KVRecord(BaseModel):
    """Base model for records stored in KV store.

    Provides automatic timestamp tracking for creation and updates.

    Examples:
        >>> class UserRecord(KVRecord):
        ...     user_id: str
        ...     name: str
        ...     email: str
        >>>
        >>> user = UserRecord(user_id="alice", name="Alice", email="alice@example.com")
        >>> user.created_at  # Automatically set
        >>> user.mark_updated()  # Update the timestamp
    """

    created_at: float = Field(
        default_factory=time.time,
        description="Creation timestamp (Unix epoch)"
    )
    updated_at: float = Field(
        description="Last update timestamp (Unix epoch)"
    )

    @model_validator(mode="before")
    @classmethod
    def sync_initial_timestamps(cls, data: Any) -> Any:
        """Initialize updated_at from created_at when not explicitly provided."""
        if not isinstance(data, dict):
            return data

        if "updated_at" in data:
            return data

        normalized = dict(data)
        if "created_at" in normalized:
            normalized["updated_at"] = normalized["created_at"]
        else:
            now = time.time()
            normalized["created_at"] = now
            normalized["updated_at"] = now

        return normalized

    def mark_updated(self) -> None:
        """Update the updated_at timestamp to current time."""
        self.updated_at = time.time()


class VersionedKVRecord(KVRecord):
    """KV record with version tracking.

    Extends KVRecord to include version numbering for tracking
    changes to records over time.

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
