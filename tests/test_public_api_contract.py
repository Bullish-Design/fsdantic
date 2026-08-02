"""Public API contract tests for top-level ``fsdantic`` exports.

These checks are intentionally import-only and static so they do not depend on
AgentFS runtime integration.
"""

import fsdantic


def test___all___exact_expected_exports() -> None:
    """Top-level public API should match the intended contract exactly."""
    expected_exports = {
        # Core models
        "Fsdantic",
        "Workspace",
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
        # File and KV operations
        "FileManager",
        "FileQuery",
        "FileOperations",
        "KVManager",
        "KVTransaction",
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
        "WorkspaceError",
    }

    assert set(fsdantic.__all__) == expected_exports
    assert len(fsdantic.__all__) == len(expected_exports)


def test_workspace_first_top_level_import_smoke() -> None:
    """Workspace-first docs imports should resolve from top-level package."""
    # Core entry points
    assert fsdantic.Fsdantic is not None
    assert fsdantic.Workspace is not None

    # Managers used across docs/examples
    assert fsdantic.FileManager is not None
    assert fsdantic.KVManager is not None
    assert fsdantic.MaterializationManager is not None
    assert fsdantic.OverlayManager is not None

    # Key models and enums
    assert fsdantic.AgentFSOptions is not None
    assert fsdantic.KVRecord is not None
    assert fsdantic.View is not None
    assert fsdantic.ViewQuery is not None
    assert fsdantic.MergeStrategy is not None

    # Key exception surface
    assert fsdantic.FsdanticError is not None
    assert fsdantic.FileSystemError is not None
    assert fsdantic.KVStoreError is not None


def test_internal_helpers_not_exposed_at_top_level() -> None:
    """Internal helper symbols should not be importable from ``fsdantic``."""
    assert not hasattr(fsdantic, "translate_agentfs_error")
    assert not hasattr(fsdantic, "normalize_path")
    assert not hasattr(fsdantic, "ERRNO_EXCEPTION_MAP")


def test_version_is_breaking_refactor_value() -> None:
    """Public package version should reflect the post-refactor breaking release."""
    assert fsdantic.__version__ == "0.7.0"


def test_dependency_stack_reproducibility() -> None:
    """The installed driver/SDK must match the tested dependency contract.

    fsdantic consumes the ``Bullish-Design/agentfs`` fork
    (``agentfs-sdk @ git+https://...@v0.6.4-pyturso-0.7.2``) specifically
    because upstream agentfs-sdk pins ``pyturso==0.4.4``, which lacks the
    GIL-releasing busy-wait and the MVCC journaling that fsdantic 0.6.0
    documents.  If this environment resolves the PyPI agentfs-sdk with its
    pyturso 0.4.4 pin (e.g. a ``--no-deps`` install), these checks fail and
    the ``docs/concurrency.md`` contract is not in effect.
    """
    import re
    from importlib.metadata import version

    agentfs = version("agentfs-sdk")
    pyturso_ver = version("pyturso")

    # The fork keeps upstream's version string; the pyturso check below is
    # the discriminating signal (PyPI agentfs-sdk 0.6.4 also reports 0.6.4
    # but pulls pyturso==0.4.4).
    assert agentfs == "0.6.4", f"expected fork agentfs-sdk 0.6.4, got {agentfs!r}"

    match = re.match(r"(\d+)\.(\d+)\.(\d+)", pyturso_ver)
    assert match is not None, f"unparseable pyturso version {pyturso_ver!r}"
    parts = tuple(int(g) for g in match.groups())
    assert (0, 7, 2) <= parts < (0, 8, 0), (
        f"pyturso {pyturso_ver} out of tested range [0.7.2, 0.8.0); "
        "the agentfs-sdk fork pin is not in effect"
    )
