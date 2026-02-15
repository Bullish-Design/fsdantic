"""Error translation from AgentFS to fsdantic exceptions."""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from agentfs_sdk import ErrnoException

from fsdantic.exceptions import (
    DirectoryNotEmptyError,
    FileExistsError,
    FileNotFoundError,
    FileSystemError,
    FsdanticError,
    InvalidPathError,
    IsADirectoryError,
    NotADirectoryError,
    PermissionError,
)

ERRNO_EXCEPTION_MAP: dict[str, type[FileSystemError]] = {
    "ENOENT": FileNotFoundError,
    "EEXIST": FileExistsError,
    "ENOTDIR": NotADirectoryError,
    "EISDIR": IsADirectoryError,
    "ENOTEMPTY": DirectoryNotEmptyError,
    "EPERM": PermissionError,
    "EINVAL": InvalidPathError,
}


def translate_agentfs_error(error: Any, context: str = "") -> FsdanticError:
    """Translate AgentFS errors to fsdantic domain exceptions."""

    path = getattr(error, "path", None)
    base_message = getattr(error, "message", None) or str(error)
    message = f"{context}: {base_message}" if context else base_message
    agentfs_code = str(getattr(error, "code", "") or "")

    context_payload: dict[str, Any] = {}
    if context:
        context_payload["operation"] = context
    if path is not None:
        context_payload["path"] = path
    if agentfs_code:
        context_payload["agentfs_code"] = agentfs_code

    syscall = getattr(error, "syscall", None)
    if syscall is not None:
        context_payload["syscall"] = syscall

    exception_class = ERRNO_EXCEPTION_MAP.get(agentfs_code, FileSystemError)
    return exception_class(
        message,
        path=path,
        cause=error,
        context=context_payload or None,
    )


P = ParamSpec("P")
R = TypeVar("R")


def handle_agentfs_errors(
    func: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """Decorator for async methods that translates AgentFS ErrnoException errors."""

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await func(*args, **kwargs)
        except ErrnoException as e:
            translated = translate_agentfs_error(e, func.__name__)
            raise translated from e

    return wrapper
