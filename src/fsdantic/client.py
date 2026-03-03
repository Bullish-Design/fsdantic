"""High-level fsdantic client entrypoint."""

from __future__ import annotations

import logging

from agentfs_sdk import AgentFS, AgentFSOptions as SDKAgentFSOptions
from turso.aio import Connection as TursoConnection
from turso.aio import connect as turso_connect

from .models import AgentFSOptions
from .workspace import Workspace

logger = logging.getLogger(__name__)


async def _enable_wal(conn: TursoConnection) -> None:
    """Enable WAL journal mode on a turso connection."""
    cursor = await conn.execute("PRAGMA journal_mode=wal")
    result = await cursor.fetchone()
    if result and result[0] != "wal":
        logger.warning("Failed to enable WAL mode, got: %s", result[0])


class Fsdantic:
    """Factory/entrypoint for opening fsdantic workspaces."""

    @classmethod
    async def open(
        cls,
        *,
        id: str | None = None,
        path: str | None = None,
        enable_wal: bool = True,
        enable_mvcc: bool = False,
    ) -> Workspace:
        """Open a workspace by ID or path with optional concurrency configuration.

        Exactly one of ``id`` or ``path`` must be provided.

        Args:
            id: Agent identifier (creates ``.agentfs/{id}.db``).
            path: Explicit path to the database file.
            enable_wal: If True (default), enable WAL journal mode for
                concurrent read access alongside writes.
            enable_mvcc: If True, enable MVCC with ``BEGIN CONCURRENT``
                support for optimistic concurrent writes from multiple
                connections.  Forces ``enable_wal=True``.

        Concurrency notes:
            * **WAL mode** (default): unlimited concurrent readers alongside
              a single writer on the same database file.
            * **MVCC mode**: multiple connections can write concurrently.
              Non-conflicting writes succeed; conflicting writes raise
              ``DatabaseError`` at execute time.
            * Each ``turso.aio.Connection`` serializes its own operations
              via a dedicated worker thread — no application-level locking
              is needed for sequential async access on a single connection.
        """
        if enable_mvcc:
            enable_wal = True

        options = AgentFSOptions(id=id, path=path)

        if enable_mvcc:
            return await cls._open_mvcc(options, enable_wal=enable_wal)

        workspace = await cls.open_with_options(options)

        if enable_wal:
            try:
                await _enable_wal(workspace.connection)
            except Exception as exc:
                logger.debug("Could not enable WAL mode: %s", exc)

        return workspace

    @classmethod
    async def _open_mvcc(
        cls,
        options: AgentFSOptions,
        *,
        enable_wal: bool = True,
    ) -> Workspace:
        """Open a workspace with MVCC support via turso.aio.connect().

        Uses ``experimental_features='mvcc'`` and ``isolation_level=None``
        (autocommit) to enable ``BEGIN CONCURRENT`` transactions.
        """
        sdk_options = SDKAgentFSOptions(id=options.id, path=options.path)

        # Resolve the database path the same way AgentFS.open() does
        if sdk_options.path:
            db_path = sdk_options.path
        elif sdk_options.id:
            import os

            directory = ".agentfs"
            os.makedirs(directory, exist_ok=True)
            db_path = f"{directory}/{sdk_options.id}.db"
        else:
            msg = "AgentFS.open() requires at least 'id' or 'path'."
            raise ValueError(msg)

        conn = await turso_connect(
            db_path,
            experimental_features="mvcc",
            isolation_level=None,
        )

        if enable_wal:
            await _enable_wal(conn)

        agentfs = await AgentFS.open_with(conn)
        return Workspace(agentfs)

    @classmethod
    async def open_with_options(cls, options: AgentFSOptions) -> Workspace:
        """Open a workspace from validated options.

        This is the low-level path that does not accept concurrency
        parameters.  Use :meth:`open` for WAL/MVCC configuration.
        """
        sdk_options = SDKAgentFSOptions(id=options.id, path=options.path)
        agentfs = await AgentFS.open(sdk_options)
        return Workspace(agentfs)
