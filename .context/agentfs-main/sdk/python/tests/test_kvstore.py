"""KvStore Integration Tests"""

import os
import tempfile

import pytest
from turso.aio import connect

from agentfs_sdk import KvStore


@pytest.mark.asyncio
class TestKvStoreBasicOperations:
    """Basic KvStore operations"""

    async def test_set_and_get_string(self):
        """Should set and get a string value"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            await kv.set("test-key", "test-value")
            value = await kv.get("test-key")
            assert value == "test-value"
            await db.close()

    async def test_set_and_get_object(self):
        """Should set and get an object value"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            test_object = {"name": "test", "count": 42, "nested": {"value": True}}
            await kv.set("object-key", test_object)
            value = await kv.get("object-key")
            assert value == test_object
            await db.close()

    async def test_set_and_get_number(self):
        """Should set and get a number value"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            await kv.set("number-key", 12345)
            value = await kv.get("number-key")
            assert value == 12345
            await db.close()

    async def test_set_and_get_boolean(self):
        """Should set and get a boolean value"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            await kv.set("bool-key", True)
            value = await kv.get("bool-key")
            assert value is True
            await db.close()

    async def test_set_and_get_array(self):
        """Should set and get an array value"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            test_array = [1, 2, "three", {"four": 4}]
            await kv.set("array-key", test_array)
            value = await kv.get("array-key")
            assert value == test_array
            await db.close()

    async def test_set_and_list_values(self):
        """Should set and list values"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            await kv.set("g1:k1", 1)
            await kv.set("g1:k2", 2)
            await kv.set("g2:k1", 3)
            await kv.set("g2:k2", 4)

            result1 = await kv.list("g1:")
            assert result1 == [{"key": "g1:k1", "value": 1}, {"key": "g1:k2", "value": 2}]

            result2 = await kv.list("g1:k1")
            assert result2 == [{"key": "g1:k1", "value": 1}]

            result3 = await kv.list("g1:k3")
            assert result3 == []

            result4 = await kv.list("g2:")
            assert result4 == [{"key": "g2:k1", "value": 3}, {"key": "g2:k2", "value": 4}]

            await db.close()


@pytest.mark.asyncio
class TestKvStoreUpdateOperations:
    """KvStore update operations"""

    async def test_update_existing_value(self):
        """Should update an existing value"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            await kv.set("update-key", "initial-value")
            await kv.set("update-key", "updated-value")
            value = await kv.get("update-key")
            assert value == "updated-value"
            await db.close()

    async def test_update_value_type(self):
        """Should update value type"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            await kv.set("type-key", "string-value")
            await kv.set("type-key", {"object": "value"})
            value = await kv.get("type-key")
            assert value == {"object": "value"}
            await db.close()


@pytest.mark.asyncio
class TestKvStoreDeleteOperations:
    """KvStore delete operations"""

    async def test_delete_existing_key(self):
        """Should delete an existing key"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            await kv.set("delete-key", "value-to-delete")
            await kv.delete("delete-key")
            value = await kv.get("delete-key")
            assert value is None
            await db.close()

    async def test_delete_nonexistent_key(self):
        """Should handle deleting non-existent key"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            # Should not throw an error when deleting a non-existent key
            await kv.delete("non-existent-key")
            await db.close()


@pytest.mark.asyncio
class TestKvStoreEdgeCases:
    """KvStore edge cases"""

    async def test_get_nonexistent_key(self):
        """Should return None for non-existent key"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            value = await kv.get("non-existent-key")
            assert value is None
            await db.close()

    async def test_get_with_default(self):
        """Should return default value for non-existent key"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            value = await kv.get("non-existent-key", default="default-value")
            assert value == "default-value"
            await db.close()

    async def test_handle_null_values(self):
        """Should handle null values"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            await kv.set("null-key", None)
            value = await kv.get("null-key")
            assert value is None
            await db.close()

    async def test_handle_empty_string(self):
        """Should handle empty string"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            await kv.set("empty-key", "")
            value = await kv.get("empty-key")
            assert value == ""
            await db.close()

    async def test_handle_zero_value(self):
        """Should handle zero value"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            await kv.set("zero-key", 0)
            value = await kv.get("zero-key")
            assert value == 0
            await db.close()

    async def test_keys_with_special_characters(self):
        """Should handle keys with special characters"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            special_key = "key:with/special.chars@123"
            await kv.set(special_key, "value")
            value = await kv.get(special_key)
            assert value == "value"
            await db.close()


@pytest.mark.asyncio
class TestKvStoreLargeData:
    """KvStore large data tests"""

    async def test_large_string_values(self):
        """Should handle large string values"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            large_string = "x" * 10000
            await kv.set("large-string", large_string)
            value = await kv.get("large-string")
            assert value == large_string
            await db.close()

    async def test_large_object_values(self):
        """Should handle large object values"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            large_object = {
                "items": [
                    {"id": i, "name": f"Item {i}", "data": f"Data for item {i}"}
                    for i in range(1000)
                ]
            }
            await kv.set("large-object", large_object)
            value = await kv.get("large-object")
            assert value == large_object
            await db.close()


@pytest.mark.asyncio
class TestKvStorePersistence:
    """KvStore persistence tests"""

    async def test_persist_across_instances(self):
        """Should persist data across KvStore instances"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            db = await connect(db_path)
            await db.execute("PRAGMA unstable_capture_data_changes_conn('full')")
            kv = await KvStore.from_database(db)

            await kv.set("persist-key", "persist-value")

            # Create new KvStore instance with same database
            new_kv = await KvStore.from_database(db)
            value = await new_kv.get("persist-key")
            assert value == "persist-value"

            await db.close()
