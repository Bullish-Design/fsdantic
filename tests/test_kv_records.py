"""Tests for KVRecord and VersionedKVRecord base classes."""

import time

import pytest
from fsdantic import KVRecord, VersionedKVRecord


class TestKVRecord:
    """Test KVRecord base class."""

    def test_kvrecord_auto_timestamps(self):
        """KVRecord should auto-initialize created_at and updated_at."""

        class TestRecord(KVRecord):
            name: str

        before = time.time()
        record = TestRecord(name="test")
        after = time.time()

        assert before <= record.created_at <= after
        assert before <= record.updated_at <= after
        assert record.created_at == record.updated_at

    def test_kvrecord_explicit_timestamps(self):
        """KVRecord should accept explicit timestamp values."""

        class TestRecord(KVRecord):
            name: str

        explicit_time = 1234567890.0
        record = TestRecord(name="test", created_at=explicit_time, updated_at=explicit_time)

        assert record.created_at == explicit_time
        assert record.updated_at == explicit_time

    def test_kvrecord_mark_updated(self):
        """mark_updated() should update the updated_at timestamp."""

        class TestRecord(KVRecord):
            name: str

        record = TestRecord(name="test")
        original_created = record.created_at
        original_updated = record.updated_at

        # Wait a bit to ensure timestamp difference
        time.sleep(0.01)

        before_update = time.time()
        record.mark_updated()
        after_update = time.time()

        # created_at should not change
        assert record.created_at == original_created

        # updated_at should be newer
        assert record.updated_at > original_updated
        assert before_update <= record.updated_at <= after_update

    def test_kvrecord_multiple_updates(self):
        """Multiple mark_updated() calls should keep updating timestamp."""

        class TestRecord(KVRecord):
            name: str

        record = TestRecord(name="test")
        timestamps = [record.updated_at]

        for _ in range(3):
            time.sleep(0.01)
            record.mark_updated()
            timestamps.append(record.updated_at)

        # Each timestamp should be later than the previous
        for i in range(len(timestamps) - 1):
            assert timestamps[i + 1] > timestamps[i]

    def test_kvrecord_serialization(self):
        """KVRecord should serialize/deserialize correctly."""

        class TestRecord(KVRecord):
            name: str
            age: int

        record = TestRecord(name="Alice", age=30)
        data = record.model_dump()

        assert "name" in data
        assert "age" in data
        assert "created_at" in data
        assert "updated_at" in data

        # Deserialize
        restored = TestRecord.model_validate(data)
        assert restored.name == record.name
        assert restored.age == record.age
        assert restored.created_at == record.created_at
        assert restored.updated_at == record.updated_at


class TestVersionedKVRecord:
    """Test VersionedKVRecord class."""

    def test_versioned_record_default_version(self):
        """VersionedKVRecord should start at version 1."""

        class TestRecord(VersionedKVRecord):
            name: str

        record = TestRecord(name="test")
        assert record.version == 1

    def test_versioned_record_explicit_version(self):
        """VersionedKVRecord should accept explicit version."""

        class TestRecord(VersionedKVRecord):
            name: str

        record = TestRecord(name="test", version=5)
        assert record.version == 5

    def test_versioned_record_increment_version(self):
        """increment_version() should bump version and update timestamp."""

        class TestRecord(VersionedKVRecord):
            name: str

        record = TestRecord(name="test")
        assert record.version == 1

        original_updated = record.updated_at
        time.sleep(0.01)

        before = time.time()
        record.increment_version()
        after = time.time()

        assert record.version == 2
        assert record.updated_at > original_updated
        assert before <= record.updated_at <= after

    def test_versioned_record_multiple_increments(self):
        """Multiple increment_version() calls should keep incrementing."""

        class TestRecord(VersionedKVRecord):
            name: str

        record = TestRecord(name="test")
        assert record.version == 1

        record.increment_version()
        assert record.version == 2

        record.increment_version()
        assert record.version == 3

        record.increment_version()
        assert record.version == 4

    def test_versioned_record_inherits_kvrecord_features(self):
        """VersionedKVRecord should have all KVRecord features."""

        class TestRecord(VersionedKVRecord):
            name: str

        record = TestRecord(name="test")

        # Should have timestamps
        assert hasattr(record, "created_at")
        assert hasattr(record, "updated_at")

        # Should have mark_updated
        original_updated = record.updated_at
        time.sleep(0.01)
        record.mark_updated()
        assert record.updated_at > original_updated

    def test_versioned_record_increment_also_marks_updated(self):
        """increment_version() should also call mark_updated()."""

        class TestRecord(VersionedKVRecord):
            name: str

        record = TestRecord(name="test")
        original_updated = record.updated_at

        time.sleep(0.01)
        record.increment_version()

        # Both version and updated_at should change
        assert record.version == 2
        assert record.updated_at > original_updated

    def test_versioned_record_serialization(self):
        """VersionedKVRecord should serialize/deserialize correctly."""

        class TestRecord(VersionedKVRecord):
            name: str
            data: dict

        record = TestRecord(name="test", data={"key": "value"}, version=3)
        data = record.model_dump()

        assert "name" in data
        assert "data" in data
        assert "version" in data
        assert "created_at" in data
        assert "updated_at" in data
        assert data["version"] == 3

        # Deserialize
        restored = TestRecord.model_validate(data)
        assert restored.name == record.name
        assert restored.version == record.version
        assert restored.data == record.data


class TestKVRecordUsagePatterns:
    """Test common usage patterns with KVRecord classes."""

    def test_update_workflow(self):
        """Test typical update workflow."""

        class UserRecord(VersionedKVRecord):
            username: str
            email: str
            settings: dict

        # Create initial record
        user = UserRecord(username="alice", email="alice@example.com", settings={"theme": "dark"})

        assert user.version == 1
        v1_updated = user.updated_at

        # Simulate an update
        time.sleep(0.01)
        user.settings["theme"] = "light"
        user.increment_version()

        assert user.version == 2
        assert user.updated_at > v1_updated

    def test_comparison_tracking(self):
        """Test tracking changes between versions."""

        class ConfigRecord(VersionedKVRecord):
            settings: dict

        config = ConfigRecord(settings={"a": 1, "b": 2})
        v1 = config.model_dump()

        config.settings["c"] = 3
        config.increment_version()
        v2 = config.model_dump()

        assert v1["version"] == 1
        assert v2["version"] == 2
        assert v2["updated_at"] > v1["updated_at"]

    def test_manual_mark_updated_without_version_change(self):
        """Test updating timestamp without changing version."""

        class DataRecord(VersionedKVRecord):
            data: str

        record = DataRecord(data="initial")
        original_version = record.version
        original_updated = record.updated_at

        time.sleep(0.01)

        # Use mark_updated() instead of increment_version()
        record.mark_updated()

        # Version should not change
        assert record.version == original_version

        # But updated_at should
        assert record.updated_at > original_updated
