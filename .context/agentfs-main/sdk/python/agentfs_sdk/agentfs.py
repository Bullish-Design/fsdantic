"""Main AgentFS class"""

import os
import re
from dataclasses import dataclass
from typing import Optional

from turso.aio import Connection, connect

from .filesystem import Filesystem
from .kvstore import KvStore
from .toolcalls import ToolCalls


@dataclass
class AgentFSOptions:
    """Configuration options for opening an AgentFS instance

    Attributes:
        id: Unique identifier for the agent.
            - If provided without `path`: Creates storage at `.agentfs/{id}.db`
            - If provided with `path`: Uses the specified path
        path: Explicit path to the database file.
            - If provided: Uses the specified path directly
            - Can be combined with `id`
    """

    id: Optional[str] = None
    path: Optional[str] = None


class AgentFS:
    """AgentFS - A filesystem and key-value store for AI agents

    Provides a unified interface for persistent storage using SQLite,
    with support for key-value storage, filesystem operations, and
    tool call tracking.
    """

    def __init__(self, db: Connection, kv: KvStore, fs: Filesystem, tools: ToolCalls):
        """Private constructor - use AgentFS.open() instead"""
        self._db = db
        self.kv = kv
        self.fs = fs
        self.tools = tools

    @staticmethod
    async def open(options: AgentFSOptions) -> "AgentFS":
        """Open an agent filesystem

        Args:
            options: Configuration options (id and/or path required)

        Returns:
            Fully initialized AgentFS instance

        Raises:
            ValueError: If neither id nor path is provided, or if id contains invalid characters

        Example:
            >>> # Using id (creates .agentfs/my-agent.db)
            >>> agent = await AgentFS.open(AgentFSOptions(id='my-agent'))
            >>>
            >>> # Using id with custom path
            >>> agent = await AgentFS.open(AgentFSOptions(id='my-agent', path='./data/mydb.db'))
            >>>
            >>> # Using path only
            >>> agent = await AgentFS.open(AgentFSOptions(path='./data/mydb.db'))
        """
        # Require at least id or path
        if not options.id and not options.path:
            raise ValueError("AgentFS.open() requires at least 'id' or 'path'.")

        # Validate agent ID if provided
        if options.id and not re.match(r"^[a-zA-Z0-9_-]+$", options.id):
            raise ValueError(
                "Agent ID must contain only alphanumeric characters, hyphens, and underscores"
            )

        # Determine database path: explicit path takes precedence, otherwise use id-based path
        if options.path:
            db_path = options.path
        else:
            # id is guaranteed to be defined here (we checked not id and not path above)
            directory = ".agentfs"
            if not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
            db_path = f"{directory}/{options.id}.db"

        # Connect to the database to ensure it's created
        db = await connect(db_path)

        return await AgentFS.open_with(db)

    @staticmethod
    async def open_with(db: Connection) -> "AgentFS":
        """Open an AgentFS instance with an existing database connection

        Args:
            db: An existing pyturso.aio Connection

        Returns:
            Fully initialized AgentFS instance
        """
        # Initialize all components in parallel
        kv = await KvStore.from_database(db)
        fs = await Filesystem.from_database(db)
        tools = await ToolCalls.from_database(db)

        return AgentFS(db, kv, fs, tools)

    def get_database(self) -> Connection:
        """Get the underlying Database connection"""
        return self._db

    async def close(self) -> None:
        """Close the database connection"""
        await self._db.close()

    async def __aenter__(self) -> "AgentFS":
        """Context manager entry"""
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """Context manager exit"""
        await self.close()
