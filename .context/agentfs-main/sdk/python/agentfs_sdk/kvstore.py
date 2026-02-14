"""Key-Value Store implementation"""

import json
from typing import Any, Dict, List, Optional, TypeVar

from turso.aio import Connection

T = TypeVar("T")


class KvStore:
    """Key-Value store backed by SQLite

    Provides a simple key-value interface with JSON serialization
    for storing arbitrary Python objects.
    """

    def __init__(self, db: Connection):
        """Private constructor - use KvStore.from_database() instead"""
        self._db = db

    @staticmethod
    async def from_database(db: Connection) -> "KvStore":
        """Create a KvStore from an existing database connection

        Args:
            db: An existing pyturso.aio Connection

        Returns:
            Fully initialized KvStore instance
        """
        kv = KvStore(db)
        await kv._initialize()
        return kv

    async def _initialize(self) -> None:
        """Initialize the database schema"""
        # Create the key-value store table if it doesn't exist
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS kv_store (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                created_at INTEGER DEFAULT (unixepoch()),
                updated_at INTEGER DEFAULT (unixepoch())
            );

            CREATE INDEX IF NOT EXISTS idx_kv_store_created_at
            ON kv_store(created_at);
        """)
        await self._db.commit()

    async def set(self, key: str, value: Any) -> None:
        """Set a key-value pair

        Args:
            key: The key to store
            value: The value to store (will be JSON serialized)

        Example:
            >>> await kv.set('user:123', {'name': 'Alice', 'age': 30})
        """
        # Serialize the value to JSON
        serialized_value = json.dumps(value)

        # Use prepared statement to insert or update
        await self._db.execute(
            """
            INSERT INTO kv_store (key, value, updated_at)
            VALUES (?, ?, unixepoch())
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = unixepoch()
            """,
            (key, serialized_value),
        )
        await self._db.commit()

    async def get(self, key: str, default: Optional[T] = None) -> Optional[T]:
        """Get a value by key

        Args:
            key: The key to retrieve
            default: Default value if key is not found

        Returns:
            The deserialized value, or default if key doesn't exist

        Example:
            >>> user = await kv.get('user:123')
            >>> if user:
            >>>     print(user['name'])
        """
        cursor = await self._db.execute("SELECT value FROM kv_store WHERE key = ?", (key,))
        row = await cursor.fetchone()

        if not row:
            return default

        # Deserialize the JSON value
        return json.loads(row[0])

    async def list(self, prefix: str) -> List[Dict[str, Any]]:
        """List all keys matching a prefix

        Args:
            prefix: The prefix to match

        Returns:
            List of dictionaries with 'key' and 'value' fields

        Example:
            >>> users = await kv.list('user:')
            >>> for item in users:
            >>>     print(f"{item['key']}: {item['value']}")
        """
        # Escape special characters for LIKE query
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

        cursor = await self._db.execute(
            "SELECT key, value FROM kv_store WHERE key LIKE ? ESCAPE '\\'", (escaped + "%",)
        )
        rows = await cursor.fetchall()

        return [{"key": row[0], "value": json.loads(row[1])} for row in rows]

    async def delete(self, key: str) -> None:
        """Delete a key-value pair

        Args:
            key: The key to delete

        Example:
            >>> await kv.delete('user:123')
        """
        await self._db.execute("DELETE FROM kv_store WHERE key = ?", (key,))
        await self._db.commit()
