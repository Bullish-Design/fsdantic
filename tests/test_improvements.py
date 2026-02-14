"""Tests for library improvements (batch operations, exceptions, etc.)."""

import pytest
from pydantic import BaseModel

from fsdantic import (
    ContentSearchError,
    FsdanticError,
    MaterializationError,
    MergeConflictError,
    RepositoryError,
    TypedKVRepository,
    ValidationError,
    View,
    ViewQuery,
)


class BatchRecord(BaseModel):
    """Test model for batch operations."""

    name: str
    value: int


@pytest.mark.asyncio
class TestBatchOperations:
    """Test batch repository operations."""

    async def test_save_batch(self, agent_fs):
        """Should save multiple records in batch."""
        repo = TypedKVRepository[BatchRecord](agent_fs, prefix="test:")

        records = [
            ("rec1", BatchRecord(name="Record 1", value=1)),
            ("rec2", BatchRecord(name="Record 2", value=2)),
            ("rec3", BatchRecord(name="Record 3", value=3)),
        ]

        await repo.save_batch(records)

        # Verify all were saved
        for record_id, expected in records:
            loaded = await repo.load(record_id, BatchRecord)
            assert loaded is not None
            assert loaded.name == expected.name
            assert loaded.value == expected.value

    async def test_load_batch(self, agent_fs):
        """Should load multiple records in batch."""
        repo = TypedKVRepository[BatchRecord](agent_fs, prefix="test:")

        # Save some records
        await repo.save("r1", BatchRecord(name="One", value=1))
        await repo.save("r2", BatchRecord(name="Two", value=2))
        await repo.save("r3", BatchRecord(name="Three", value=3))

        # Load in batch
        results = await repo.load_batch(["r1", "r2", "r3"], BatchRecord)

        assert len(results) == 3
        assert results["r1"].name == "One"
        assert results["r2"].name == "Two"
        assert results["r3"].name == "Three"

    async def test_load_batch_with_missing_records(self, agent_fs):
        """Should return None for missing records in batch load."""
        repo = TypedKVRepository[BatchRecord](agent_fs, prefix="test:")

        await repo.save("exists", BatchRecord(name="Exists", value=1))

        results = await repo.load_batch(["exists", "missing"], BatchRecord)

        assert len(results) == 2
        assert results["exists"] is not None
        assert results["missing"] is None

    async def test_delete_batch(self, agent_fs):
        """Should delete multiple records in batch."""
        repo = TypedKVRepository[BatchRecord](agent_fs, prefix="test:")

        # Create records
        await repo.save("del1", BatchRecord(name="Delete 1", value=1))
        await repo.save("del2", BatchRecord(name="Delete 2", value=2))
        await repo.save("keep", BatchRecord(name="Keep", value=3))

        # Delete in batch
        await repo.delete_batch(["del1", "del2"])

        # Verify deletions
        assert await repo.load("del1", BatchRecord) is None
        assert await repo.load("del2", BatchRecord) is None

        # Verify kept record
        kept = await repo.load("keep", BatchRecord)
        assert kept is not None
        assert kept.name == "Keep"

    async def test_batch_operations_empty_lists(self, agent_fs):
        """Should handle empty batch operations gracefully."""
        repo = TypedKVRepository[BatchRecord](agent_fs, prefix="test:")

        # Empty save batch
        await repo.save_batch([])

        # Empty load batch
        results = await repo.load_batch([], BatchRecord)
        assert results == {}

        # Empty delete batch
        await repo.delete_batch([])

    async def test_batch_save_large_batch(self, agent_fs):
        """Should handle large batches efficiently."""
        repo = TypedKVRepository[BatchRecord](agent_fs, prefix="test:")

        # Create 100 records
        records = [(f"rec{i}", BatchRecord(name=f"Record {i}", value=i)) for i in range(100)]

        await repo.save_batch(records)

        # Verify count
        all_ids = await repo.list_ids()
        assert len(all_ids) == 100

    async def test_batch_operations_workflow(self, agent_fs):
        """Test complete batch workflow."""
        repo = TypedKVRepository[BatchRecord](agent_fs, prefix="test:")

        # 1. Batch save
        initial = [
            ("a", BatchRecord(name="A", value=1)),
            ("b", BatchRecord(name="B", value=2)),
            ("c", BatchRecord(name="C", value=3)),
        ]
        await repo.save_batch(initial)

        # 2. Batch load
        loaded = await repo.load_batch(["a", "b", "c"], BatchRecord)
        assert len(loaded) == 3

        # 3. Batch delete
        await repo.delete_batch(["a", "b"])

        # 4. Verify final state
        remaining = await repo.list_ids()
        assert len(remaining) == 1
        assert "c" in remaining


class TestCustomExceptions:
    """Test custom exception classes."""

    def test_fsdantic_error_is_base(self):
        """FsdanticError should be base for all custom exceptions."""
        assert issubclass(RepositoryError, FsdanticError)
        assert issubclass(MaterializationError, FsdanticError)
        assert issubclass(MergeConflictError, FsdanticError)
        assert issubclass(ValidationError, FsdanticError)
        assert issubclass(ContentSearchError, FsdanticError)

    def test_repository_error(self):
        """Should create and catch RepositoryError."""
        with pytest.raises(RepositoryError):
            raise RepositoryError("Test repository error")

    def test_materialization_error(self):
        """Should create and catch MaterializationError."""
        with pytest.raises(MaterializationError):
            raise MaterializationError("Test materialization error")

    def test_merge_conflict_error(self):
        """Should create MergeConflictError with conflicts."""
        conflicts = [{"path": "/file.txt", "reason": "modified in both"}]

        error = MergeConflictError("Conflicts detected", conflicts)

        assert str(error) == "Conflicts detected"
        assert error.conflicts == conflicts

    def test_merge_conflict_error_catchable(self):
        """Should be catchable as MergeConflictError."""
        conflicts = [{"path": "/test.txt"}]

        with pytest.raises(MergeConflictError) as exc_info:
            raise MergeConflictError("Test", conflicts)

        assert exc_info.value.conflicts == conflicts

    def test_validation_error(self):
        """Should create and catch ValidationError."""
        with pytest.raises(ValidationError):
            raise ValidationError("Invalid data")

    def test_content_search_error(self):
        """Should create and catch ContentSearchError."""
        with pytest.raises(ContentSearchError):
            raise ContentSearchError("Search failed")

    def test_exception_hierarchy(self):
        """Custom exceptions should be catchable as FsdanticError."""
        with pytest.raises(FsdanticError):
            raise RepositoryError("Any fsdantic error")

        with pytest.raises(FsdanticError):
            raise MaterializationError("Any fsdantic error")

        with pytest.raises(FsdanticError):
            raise MergeConflictError("Any fsdantic error", [])


@pytest.mark.asyncio
class TestErrorScenarios:
    """Test realistic error scenarios with custom exceptions."""

    async def test_view_search_without_pattern_raises_error(self, agent_fs):
        """Should raise error when searching without pattern."""
        await agent_fs.fs.write_file("/test.txt", "content")

        view = View(
            agent=agent_fs,
            query=ViewQuery(path_pattern="*.txt", include_content=True),
        )

        # search_content requires content_pattern or content_regex
        with pytest.raises(ValueError):
            await view.search_content()

    async def test_batch_operations_maintain_consistency(self, agent_fs):
        """Batch operations should maintain data consistency."""
        repo = TypedKVRepository[BatchRecord](agent_fs, prefix="test:")

        # Save batch
        records = [(f"r{i}", BatchRecord(name=f"Record {i}", value=i)) for i in range(10)]
        await repo.save_batch(records)

        # Load batch
        loaded = await repo.load_batch([f"r{i}" for i in range(10)], BatchRecord)

        # All should be loaded correctly
        for i in range(10):
            assert loaded[f"r{i}"].value == i

        # Delete half
        await repo.delete_batch([f"r{i}" for i in range(0, 10, 2)])

        # Verify deletions
        remaining = await repo.list_ids()
        assert len(remaining) == 5
        assert all(f"r{i}" in remaining for i in range(1, 10, 2))


class TestImportedExceptions:
    """Test that exceptions are properly exported."""

    def test_all_exceptions_importable(self):
        """All exceptions should be importable from fsdantic."""
        from fsdantic import (
            ContentSearchError,
            FsdanticError,
            MaterializationError,
            MergeConflictError,
            RepositoryError,
            ValidationError,
        )

        # Should not raise
        assert FsdanticError
        assert RepositoryError
        assert MaterializationError
        assert MergeConflictError
        assert ValidationError
        assert ContentSearchError

    def test_exceptions_in_all(self):
        """Exceptions should be in __all__."""
        import fsdantic

        assert "FsdanticError" in fsdantic.__all__
        assert "RepositoryError" in fsdantic.__all__
        assert "MaterializationError" in fsdantic.__all__
        assert "MergeConflictError" in fsdantic.__all__
        assert "ValidationError" in fsdantic.__all__
        assert "ContentSearchError" in fsdantic.__all__
