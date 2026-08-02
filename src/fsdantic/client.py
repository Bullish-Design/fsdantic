"""High-level fsdantic client entrypoint."""

from __future__ import annotations

import logging
import os
import re

from agentfs_sdk import AgentFS
from agentfs_sdk import AgentFSOptions as SDKAgentFSOptions
from turso.aio import Connection as TursoConnection
from turso.aio import connect as turso_connect

from ._internal.readonly import _ReadonlyGuard
from .exceptions import WorkspaceError
from .models import AgentFSOptions
from .workspace import Workspace

logger = logging.getLogger(__name__)


# Mirrors the AgentFS SDK's agent-id validation
# (``sdk/python/agentfs_sdk/agentfs.py``): alphanumerics, hyphens, underscores.
_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_selector(options: AgentFSOptions) -> None:
    """Validate the selector shape for both open paths (mirrors SDK rules).

    Raises:
        ValueError: when ``options.id`` contains characters outside the SDK
            agent-id alphabet.
    """
    if options.id is not None and not _ID_REGEX.match(options.id):
        raise ValueError("Agent ID must contain only alphanumeric characters, hyphens, and underscores")


def _resolve_db_path(options: AgentFSOptions) -> str:
    """Single source of truth for database path resolution (mirrors SDK logic).

    Raises:
        ValueError: when neither ``id`` nor ``path`` is provided.
    """
    if options.path:
        return options.path
    if options.id:
        directory = ".agentfs"
        os.makedirs(directory, exist_ok=True)
        return f"{directory}/{options.id}.db"
    raise ValueError("AgentFS.open() requires at least 'id' or 'path'.")


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
        readonly: bool = False,
    ) -> Workspace:
        """Open a workspace by ID or path with optional concurrency and
        read-only configuration.

        Exactly one of ``id`` or ``path`` must be provided.

        Args:
            id: Agent identifier (creates ``.agentfs/{id}.db``).
            path: Explicit path to the database file.
            enable_wal: If True (default), enable WAL journal mode for
                concurrent read access alongside writes.
            enable_mvcc: If True, enable MVCC with ``BEGIN CONCURRENT``
                support for optimistic concurrent writes from multiple
                connections.  Forces ``enable_wal=True``.
            readonly: If True, open the workspace read-only.  Write
                operations — through the manager APIs (``files.write``,
                ``kv.set``, ``overlay.merge``, ...) or raw statements on
                :attr:`Workspace.connection` — raise
                :class:`WorkspaceError` (``WORKSPACE_READONLY``).  The
                database file must already exist (``WORKSPACE_NOT_FOUND``
                otherwise).  Reads never write: the SDK's access-time
                maintenance write is neutralized.

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
        return await cls._open_shared(
            options,
            enable_wal=enable_wal,
            enable_mvcc=enable_mvcc,
            readonly=readonly,
        )

    @classmethod
    async def _open_shared(
        cls,
        options: AgentFSOptions,
        *,
        enable_wal: bool = True,
        enable_mvcc: bool = False,
        readonly: bool = False,
    ) -> Workspace:
        """Unified connection seam for both open paths.

        Both ``open`` (non-MVCC) and the MVCC path create and own the turso
        connection here, so WAL setup, read-only guarding, and (later) busy
        timeout configuration happen in exactly one place.

        Order matters for read-only workspaces:

        1. resolve the DB path (rejecting missing files when ``readonly``);
        2. ``turso_connect``;
        3. ``_enable_wal`` — must happen **before** locking (it writes the
           DB header);
        4. wrap the connection in ``_ReadonlyGuard``;
        5. ``AgentFS.open_with(guard)`` — schema init must run **unlocked**;
        6. ``guard.lock()`` + ``PRAGMA query_only = 1`` (numeric! ``= ON``
           fails to parse on pyturso) as a hard backstop for anything that
           bypasses the proxy (e.g. cursors from ``connection.cursor()``).
        """
        _validate_selector(options)
        db_path = _resolve_db_path(options)

        if readonly and not os.path.exists(db_path):
            raise WorkspaceError(
                f"Cannot open read-only workspace: database file does not exist: {db_path}",
                code="WORKSPACE_NOT_FOUND",
            )

        conn = await turso_connect(
            db_path,
            experimental_features="mvcc" if enable_mvcc else None,
            isolation_level=None if enable_mvcc else "DEFERRED",
        )

        if enable_wal:
            try:
                await _enable_wal(conn)
            except Exception as exc:
                logger.debug("Could not enable WAL mode: %s", exc)

        guard = _ReadonlyGuard(conn)

        # Schema init (CREATE TABLE IF NOT EXISTS, WAL journal config rows)
        # must run in pass-through, before enforcement starts.
        agentfs = await AgentFS.open_with(guard)

        if readonly:
            guard.lock()
            # Hard backstop on the underlying connection: even statements
            # that reach it without passing the guard (e.g. via a raw
            # ``cursor()``) are rejected by libSQL.  Note the numeric 1:
            # ``PRAGMA query_only = ON`` fails to parse on pyturso 0.4.4.
            await conn.execute("PRAGMA query_only = 1")

        return Workspace(agentfs, readonly=readonly)

    @classmethod
    async def open_with_options(cls, options: AgentFSOptions) -> Workspace:
        """Open a workspace from validated options.

        This is the low-level path that does not accept concurrency or
        read-only parameters (it connects through the AgentFS SDK directly,
        bypassing the fsdantic connection seam).  Use :meth:`open` for
        WAL/MVCC/readonly configuration.
        """
        _validate_selector(options)
        sdk_options = SDKAgentFSOptions(id=options.id, path=options.path)
        agentfs = await AgentFS.open(sdk_options)
        return Workspace(agentfs)
