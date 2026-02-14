"""Tool calls tracking implementation"""

import json
import time
from dataclasses import dataclass
from typing import Any, List, Literal, Optional

from turso.aio import Connection


@dataclass
class ToolCall:
    """Tool call record

    Attributes:
        id: Unique identifier for the tool call
        name: Name of the tool
        parameters: Tool parameters (optional)
        result: Tool result (optional)
        error: Error message if the call failed (optional)
        status: Status of the tool call
        started_at: Unix timestamp when the call started
        completed_at: Unix timestamp when the call completed (optional)
        duration_ms: Duration in milliseconds (optional)
    """

    id: int
    name: str
    parameters: Optional[Any] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    status: Literal["pending", "success", "error"] = "pending"
    started_at: int = 0
    completed_at: Optional[int] = None
    duration_ms: Optional[int] = None


@dataclass
class ToolCallStats:
    """Tool call statistics

    Attributes:
        name: Name of the tool
        total_calls: Total number of calls
        successful: Number of successful calls
        failed: Number of failed calls
        avg_duration_ms: Average duration in milliseconds
    """

    name: str
    total_calls: int
    successful: int
    failed: int
    avg_duration_ms: float


class ToolCalls:
    """Tool calls tracking backed by SQLite

    Provides tracking and analytics for tool/function calls,
    recording timing, parameters, results, and errors.
    """

    def __init__(self, db: Connection):
        """Private constructor - use ToolCalls.from_database() instead"""
        self._db = db

    @staticmethod
    async def from_database(db: Connection) -> "ToolCalls":
        """Create a ToolCalls from an existing database connection

        Args:
            db: An existing pyturso.aio Connection

        Returns:
            Fully initialized ToolCalls instance
        """
        tools = ToolCalls(db)
        await tools._initialize()
        return tools

    async def _initialize(self) -> None:
        """Initialize the database schema"""
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS tool_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                parameters TEXT,
                result TEXT,
                error TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                started_at INTEGER NOT NULL,
                completed_at INTEGER,
                duration_ms INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_tool_calls_name
            ON tool_calls(name);

            CREATE INDEX IF NOT EXISTS idx_tool_calls_started_at
            ON tool_calls(started_at);
        """)
        await self._db.commit()

    async def start(self, name: str, parameters: Optional[Any] = None) -> int:
        """Start a new tool call and mark it as pending

        Args:
            name: Name of the tool
            parameters: Tool parameters (will be JSON serialized)

        Returns:
            ID of the created tool call record

        Example:
            >>> call_id = await tools.start('search', {'query': 'Python'})

        Note: We use RETURNING clause which requires explicit cursor close
        when working with CDC-enabled TursoDB connections.
        """
        serialized_params = json.dumps(parameters) if parameters is not None else None
        started_at = int(time.time())

        cursor = self._db.cursor()
        try:
            await cursor.execute(
                """
                INSERT INTO tool_calls (name, parameters, status, started_at)
                VALUES (?, ?, 'pending', ?)
                RETURNING id
                """,
                (name, serialized_params, started_at),
            )
            row = await cursor.fetchone()
            assert row is not None
            call_id = row[0]
        finally:
            await cursor.close()
        await self._db.commit()
        return call_id

    async def success(self, call_id: int, result: Optional[Any] = None) -> None:
        """Mark a tool call as successful

        Args:
            call_id: ID of the tool call
            result: Tool result (will be JSON serialized)

        Example:
            >>> await tools.success(call_id, {'results': [...]})
        """
        serialized_result = json.dumps(result) if result is not None else None
        completed_at = int(time.time())

        # Get the started_at time to calculate duration
        cursor = await self._db.execute(
            "SELECT started_at FROM tool_calls WHERE id = ?", (call_id,)
        )
        row = await cursor.fetchone()

        if not row:
            raise ValueError(f"Tool call with ID {call_id} not found")

        duration_ms = (completed_at - row[0]) * 1000

        await self._db.execute(
            """
            UPDATE tool_calls
            SET status = 'success', result = ?, completed_at = ?, duration_ms = ?
            WHERE id = ?
            """,
            (serialized_result, completed_at, duration_ms, call_id),
        )
        await self._db.commit()

    async def error(self, call_id: int, error: str) -> None:
        """Mark a tool call as failed

        Args:
            call_id: ID of the tool call
            error: Error message

        Example:
            >>> await tools.error(call_id, 'Connection timeout')
        """
        completed_at = int(time.time())

        # Get the started_at time to calculate duration
        cursor = await self._db.execute(
            "SELECT started_at FROM tool_calls WHERE id = ?", (call_id,)
        )
        row = await cursor.fetchone()

        if not row:
            raise ValueError(f"Tool call with ID {call_id} not found")

        duration_ms = (completed_at - row[0]) * 1000

        await self._db.execute(
            """
            UPDATE tool_calls
            SET status = 'error', error = ?, completed_at = ?, duration_ms = ?
            WHERE id = ?
            """,
            (error, completed_at, duration_ms, call_id),
        )
        await self._db.commit()

    async def record(
        self,
        name: str,
        started_at: int,
        completed_at: int,
        parameters: Optional[Any] = None,
        result: Optional[Any] = None,
        error: Optional[str] = None,
    ) -> int:
        """Record a completed tool call

        Either result or error should be provided, not both.

        Args:
            name: Name of the tool
            started_at: Unix timestamp when the call started
            completed_at: Unix timestamp when the call completed
            parameters: Tool parameters (will be JSON serialized)
            result: Tool result (will be JSON serialized)
            error: Error message if the call failed

        Returns:
            ID of the created tool call record

        Example:
            >>> call_id = await tools.record(
            ...     'search',
            ...     started_at=1234567890,
            ...     completed_at=1234567892,
            ...     parameters={'query': 'Python'},
            ...     result={'results': [...]}
            ... )

        Note: We use RETURNING clause which requires explicit cursor close
        when working with CDC-enabled TursoDB connections.
        """
        serialized_params = json.dumps(parameters) if parameters is not None else None
        serialized_result = json.dumps(result) if result is not None else None
        duration_ms = (completed_at - started_at) * 1000
        status = "error" if error else "success"

        cursor = self._db.cursor()
        try:
            await cursor.execute(
                """
                INSERT INTO tool_calls (
                    name, parameters, result, error, status,
                    started_at, completed_at, duration_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    name,
                    serialized_params,
                    serialized_result,
                    error,
                    status,
                    started_at,
                    completed_at,
                    duration_ms,
                ),
            )
            row = await cursor.fetchone()
            assert row is not None
            call_id = row[0]
        finally:
            await cursor.close()
        await self._db.commit()
        return call_id

    async def get(self, call_id: int) -> Optional[ToolCall]:
        """Get a specific tool call by ID

        Args:
            call_id: ID of the tool call

        Returns:
            ToolCall object or None if not found

        Example:
            >>> call = await tools.get(123)
            >>> if call:
            >>>     print(f"Tool: {call.name}, Status: {call.status}")
        """
        cursor = await self._db.execute("SELECT * FROM tool_calls WHERE id = ?", (call_id,))
        row = await cursor.fetchone()

        if not row:
            return None

        return self._row_to_tool_call(row)

    async def get_by_name(self, name: str, limit: Optional[int] = None) -> List[ToolCall]:
        """Query tool calls by name

        Args:
            name: Name of the tool
            limit: Maximum number of results (optional)

        Returns:
            List of ToolCall objects, ordered by most recent first

        Example:
            >>> calls = await tools.get_by_name('search', limit=10)
            >>> for call in calls:
            >>>     print(f"ID: {call.id}, Status: {call.status}")
        """
        limit_clause = f"LIMIT {limit}" if limit is not None else ""

        cursor = await self._db.execute(
            f"""
            SELECT * FROM tool_calls
            WHERE name = ?
            ORDER BY started_at DESC
            {limit_clause}
            """,
            (name,),
        )
        rows = await cursor.fetchall()

        return [self._row_to_tool_call(row) for row in rows]

    async def get_recent(self, since: int, limit: Optional[int] = None) -> List[ToolCall]:
        """Query recent tool calls

        Args:
            since: Unix timestamp to filter calls after
            limit: Maximum number of results (optional)

        Returns:
            List of ToolCall objects, ordered by most recent first

        Example:
            >>> # Get calls from the last hour
            >>> since = int(time.time()) - 3600
            >>> calls = await tools.get_recent(since)
        """
        limit_clause = f"LIMIT {limit}" if limit is not None else ""

        cursor = await self._db.execute(
            f"""
            SELECT * FROM tool_calls
            WHERE started_at > ?
            ORDER BY started_at DESC
            {limit_clause}
            """,
            (since,),
        )
        rows = await cursor.fetchall()

        return [self._row_to_tool_call(row) for row in rows]

    async def get_stats(self) -> List[ToolCallStats]:
        """Get performance statistics for all tools

        Only includes completed calls (success or error), not pending ones.

        Returns:
            List of ToolCallStats objects, ordered by total calls descending

        Example:
            >>> stats = await tools.get_stats()
            >>> for stat in stats:
            >>>     print(f"{stat.name}: {stat.total_calls} calls, "
            >>>           f"{stat.avg_duration_ms:.2f}ms avg")
        """
        cursor = await self._db.execute("""
            SELECT
                name,
                COUNT(*) as total_calls,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as failed,
                AVG(duration_ms) as avg_duration_ms
            FROM tool_calls
            WHERE status != 'pending'
            GROUP BY name
            ORDER BY total_calls DESC
        """)
        rows = await cursor.fetchall()

        return [
            ToolCallStats(
                name=row[0],
                total_calls=row[1],
                successful=row[2],
                failed=row[3],
                avg_duration_ms=row[4] if row[4] is not None else 0.0,
            )
            for row in rows
        ]

    def _row_to_tool_call(self, row: tuple) -> ToolCall:
        """Helper to convert database row to ToolCall object"""
        return ToolCall(
            id=row[0],
            name=row[1],
            parameters=json.loads(row[2]) if row[2] is not None else None,
            result=json.loads(row[3]) if row[3] is not None else None,
            error=row[4] if row[4] is not None else None,
            status=row[5],
            started_at=row[6],
            completed_at=row[7] if row[7] is not None else None,
            duration_ms=row[8] if row[8] is not None else None,
        )
