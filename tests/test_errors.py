"""Tests for AgentFS-to-fsdantic error translation and structured metadata."""

import asyncio

import pytest
from agentfs_sdk import ErrnoException

from fsdantic import (
    DirectoryNotEmptyError,
    FileExistsError,
    FileNotFoundError,
    FileSystemError,
    InvalidPathError,
    IsADirectoryError,
    NotADirectoryError,
    PermissionError,
)
from fsdantic._internal.errors import (
    ERRNO_EXCEPTION_MAP,
    handle_agentfs_errors,
    translate_agentfs_error,
)
from fsdantic.exceptions import FsdanticError


@pytest.mark.parametrize(
    ("code", "expected_type", "expected_error_code"),
    [
        ("ENOENT", FileNotFoundError, "FS_NOT_FOUND"),
        ("EEXIST", FileExistsError, "FS_ALREADY_EXISTS"),
        ("ENOTDIR", NotADirectoryError, "FS_NOT_A_DIRECTORY"),
        ("EISDIR", IsADirectoryError, "FS_IS_A_DIRECTORY"),
        ("ENOTEMPTY", DirectoryNotEmptyError, "FS_DIRECTORY_NOT_EMPTY"),
        ("EPERM", PermissionError, "FS_PERMISSION_DENIED"),
        ("EINVAL", InvalidPathError, "FS_INVALID_PATH"),
    ],
)
def test_per_errno_mappings(code, expected_type, expected_error_code):
    error = ErrnoException(code=code, syscall="stat", path="/tmp/x", message="boom")

    translated = translate_agentfs_error(error, context="op")

    assert isinstance(translated, expected_type)
    assert translated.path == "/tmp/x"
    assert translated.cause is error
    assert translated.code == expected_error_code
    assert str(translated).startswith("op:")


def test_fsdantic_error_to_dict_and_safe_context():
    error = FsdanticError(
        "top-level failed",
        code="GENERIC_FAILURE",
        context={"payload": b"abc", "nested": {"ok": True}},
    )

    payload = error.to_dict()

    assert payload["type"] == "FsdanticError"
    assert payload["message"] == "top-level failed"
    assert payload["code"] == "GENERIC_FAILURE"
    assert payload["context"]["payload"] == "<bytes:3>"
    assert payload["context"]["nested"]["ok"] is True


def test_per_errno_mapping_covers_every_required_code_once():
    """Every required errno key must be explicitly covered by the test matrix."""
    covered_codes = {
        "ENOENT",
        "EEXIST",
        "ENOTDIR",
        "EISDIR",
        "ENOTEMPTY",
        "EPERM",
        "EINVAL",
    }
    assert covered_codes == set(ERRNO_EXCEPTION_MAP)


def test_mapping_matrix_coverage_is_strict():
    """Fail loudly if a required errno mapping is removed/misrouted."""
    expected = {
        "ENOENT": FileNotFoundError,
        "EEXIST": FileExistsError,
        "ENOTDIR": NotADirectoryError,
        "EISDIR": IsADirectoryError,
        "ENOTEMPTY": DirectoryNotEmptyError,
        "EPERM": PermissionError,
        "EINVAL": InvalidPathError,
    }
    assert ERRNO_EXCEPTION_MAP == expected


@pytest.mark.parametrize(
    ("code", "expected_type", "expected_error_code"),
    [
        ("ENOENT", FileNotFoundError, "FS_NOT_FOUND"),
        ("EEXIST", FileExistsError, "FS_ALREADY_EXISTS"),
        ("ENOTDIR", NotADirectoryError, "FS_NOT_A_DIRECTORY"),
        ("EISDIR", IsADirectoryError, "FS_IS_A_DIRECTORY"),
        ("ENOTEMPTY", DirectoryNotEmptyError, "FS_DIRECTORY_NOT_EMPTY"),
        ("EPERM", PermissionError, "FS_PERMISSION_DENIED"),
        ("EINVAL", InvalidPathError, "FS_INVALID_PATH"),
        ("ENOSYS", FileSystemError, "FS_ERROR"),
        ("UNKNOWN", FileSystemError, "FS_ERROR"),
        ("", FileSystemError, "FS_ERROR"),
    ],
)
def test_translation_matrix_with_unknown_fallback(code, expected_type, expected_error_code):
    error = ErrnoException(code=code, syscall="open", path="/data.txt", message="bad")

    translated = translate_agentfs_error(error)

    assert isinstance(translated, expected_type)
    assert translated.code == expected_error_code
    if expected_type is FileSystemError:
        assert translated.path == "/data.txt"
        assert translated.cause is error


def test_translation_context_is_consistent_with_decorator():
    error = ErrnoException("ENOENT", "open", path="/missing", message="not found")

    direct = translate_agentfs_error(error, context="raises_errno")

    @handle_agentfs_errors
    async def raises_errno():
        raise error

    with pytest.raises(FileNotFoundError) as exc_info:
        asyncio.run(raises_errno())

    decorated = exc_info.value
    assert decorated.code == direct.code
    assert decorated.path == direct.path
    assert decorated.context == direct.context


def test_decorator_chains_cause():
    @handle_agentfs_errors
    async def raises_errno():
        raise ErrnoException("ENOENT", "open", path="/missing", message="not found")

    with pytest.raises(FileNotFoundError) as exc_info:
        asyncio.run(raises_errno())

    assert exc_info.value.path == "/missing"
    assert isinstance(exc_info.value.__cause__, ErrnoException)
    assert exc_info.value.__cause__.code == "ENOENT"


def test_translated_exception_can_be_chained_via_raise_from():
    original = ErrnoException("EEXIST", "open", path="/existing", message="already exists")

    with pytest.raises(FileExistsError) as exc_info:
        raise translate_agentfs_error(original, context="write") from original

    assert exc_info.value.path == "/existing"
    assert exc_info.value.cause is original
    assert exc_info.value.__cause__ is original


def test_decorator_non_errno_passthrough():
    @handle_agentfs_errors
    async def raises_runtime_error():
        raise RuntimeError("passthrough")

    with pytest.raises(RuntimeError, match="passthrough"):
        asyncio.run(raises_runtime_error())


def test_decorator_non_errno_passthrough_preserves_cause_chain():
    @handle_agentfs_errors
    async def raises_wrapped_non_errno():
        try:
            raise ValueError("low-level")
        except ValueError as error:
            raise RuntimeError("top-level") from error

    with pytest.raises(RuntimeError, match="top-level") as exc_info:
        asyncio.run(raises_wrapped_non_errno())

    assert isinstance(exc_info.value.__cause__, ValueError)


def test_key_structured_field():
    from fsdantic import KeyNotFoundError

    exc = KeyNotFoundError("config:missing")
    assert exc.key == "config:missing"
    assert exc.code == "KV_KEY_NOT_FOUND"


def test_conflict_structured_field():
    from fsdantic import MergeConflictError

    conflicts = [{"path": "/a.txt"}]
    exc = MergeConflictError("merge failed", conflicts=conflicts)
    assert exc.conflicts == conflicts
    assert exc.code == "OVERLAY_CONFLICT"


def test_malformed_error_object_missing_all_fields():
    class BrokenError:
        pass

    translated = translate_agentfs_error(BrokenError())  # type: ignore[arg-type]

    assert isinstance(translated, FileSystemError)
    assert translated.path is None
    assert translated.cause.__class__.__name__ == "BrokenError"


def test_malformed_error_object_partial_fields():
    class BrokenError:
        code = "EEXIST"
        path = "/tmp/file"

        def __str__(self):
            return "broken string"

    translated = translate_agentfs_error(BrokenError())  # type: ignore[arg-type]

    assert isinstance(translated, FileExistsError)
    assert translated.path == "/tmp/file"
    assert "broken string" in str(translated)
