"""High-level fsdantic client entrypoint.

Concurrency contract
--------------------

* **Single connection**: each ``turso.aio.Connection`` serializes its own
  operations via a dedicated worker thread — no application-level locking
  is needed for sequential async access on a single connection.
* **WAL mode** (default): unlimited concurrent readers alongside a single
  writer on the same database file.  When a writer contends for the write
  lock it waits up to ``busy_timeout_ms`` (default 5000) instead of failing
  immediately; pass ``busy_timeout_ms=0`` to disable the wait (matching the
  turso default).  On pyturso >= 0.7.2 (the version fsdantic pins via the
  agentfs-sdk fork) the busy-wait releases the GIL: the event loop stays
  responsive and an in-process lock release can unblock the waiter, so
  "wait, then succeed" is the normal contention outcome.  Concurrent
  multi-process access to a DB file is still not supported.
* **MVCC** (``enable_mvcc=True``, ``PRAGMA journal_mode = "mvcc"``):
  multiple connections can write concurrently without lock contention
  (``BEGIN CONCURRENT`` transactions are accepted).  Caveat (verified by
  probe on pyturso 0.7.2): pyturso's Python API opens an independent MVCC
  store per connection, so **write-write conflicts are not reliably
  surfaced** — concurrent same-row writes are effectively last-write-wins
  and callers must not rely on a conflict error.  Use
  :meth:`Workspace.serialized` (same-process) or the repository's SQL CAS
  for atomicity.
"""

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


async def _enable_mvcc(conn: TursoConnection) -> None:
    """Enable libSQL MVCC journaling on a turso connection.

    pyturso 0.7.x (Limbo engine) enables MVCC via ``PRAGMA journal_mode =
    "mvcc"``; the ``experimental_features="mvcc"`` connect option is a
    no-op on every released pyturso (0.4.4's libSQL build had no MVCC
    support at all).  The pragma's result row MUST be consumed
    (``fetchone``) — without it the connection does not recognize MVCC
    mode and ``BEGIN CONCURRENT`` raises "Concurrent transaction mode is
    only supported when MVCC is enabled".
    """
    cursor = await conn.execute('PRAGMA journal_mode = "mvcc"')
    result = await cursor.fetchone()
    if result and result[0] != "mvcc":
        logger.warning("Failed to enable MVCC mode, got: %s", result[0])


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
        busy_timeout_ms: int = 5000,
        max_content_bytes: int | None = None,
    ) -> Workspace:
        """Open a workspace by ID or path with optional concurrency,
        read-only, and content-size configuration.

        Exactly one of ``id`` or ``path`` must be provided.

        Args:
            id: Agent identifier (creates ``.agentfs/{id}.db``).
            path: Explicit path to the database file.
            enable_wal: If True (default), enable WAL journal mode for
                concurrent read access alongside writes.
            enable_mvcc: If True, enable libSQL MVCC journaling
                (``PRAGMA journal_mode = "mvcc"``) for concurrent writes
                from multiple connections without lock contention.
                Forces ``enable_wal=True`` (the mvcc journal replaces
                WAL).  Note: the driver does not reliably surface
                write-write conflicts (see the module docstring) — use
                :meth:`Workspace.serialized` or the repository CAS for
                atomic read-modify-write.
            readonly: If True, open the workspace read-only.  Write
                operations — through the manager APIs (``files.write``,
                ``kv.set``, ``overlay.merge``, ...) or raw statements on
                :attr:`Workspace.connection` — raise
                :class:`WorkspaceError` (``WORKSPACE_READONLY``).  The
                database file must already exist (``WORKSPACE_NOT_FOUND``
                otherwise).  Reads never write: the SDK's access-time
                maintenance write is neutralized.
            busy_timeout_ms: Maximum milliseconds a write waits on a
                contended write lock before failing with "database is
                locked".  Default 5000.  Pass ``0`` to disable the wait
                (fail immediately, the raw turso default); negative values
                leave the timeout untouched.
            max_content_bytes: Optional cap on write payload sizes
                (``files.write``/``write_many`` payloads and ``kv.set``/
                ``set_many`` JSON payloads).  Payloads larger than the cap
                raise ``WorkspaceError`` (``CONTENT_TOO_LARGE``) before any
                storage is touched.  ``None`` (default) is unbounded.

        Concurrency contract (see the module docstring):

            * **WAL mode** (default): unlimited readers, single writer;
              the writer waits up to ``busy_timeout_ms`` on contention.
            * **MVCC mode**: multiple connections can write concurrently
              without lock contention; write-write conflicts are NOT
              reliably surfaced by the driver (each ``connect()`` opens an
              independent MVCC store) — use ``Workspace.serialized`` or the
              repository's SQL CAS for atomic read-modify-write sequences.
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
            busy_timeout_ms=busy_timeout_ms,
            max_content_bytes=max_content_bytes,
        )

    @classmethod
    async def _open_shared(
        cls,
        options: AgentFSOptions,
        *,
        enable_wal: bool = True,
        enable_mvcc: bool = False,
        readonly: bool = False,
        busy_timeout_ms: int = 5000,
        max_content_bytes: int | None = None,
    ) -> Workspace:
        """Unified connection seam for both open paths.

        Both ``open`` (non-MVCC) and the MVCC path create and own the turso
        connection here, so WAL setup, busy-timeout configuration, and
        read-only guarding happen in exactly one place.

        Order matters:

        1. resolve the DB path (rejecting missing files when ``readonly``);
        2. ``turso_connect``;
        3. ``_enable_wal`` (non-MVCC only) — must happen **before**
           locking (it writes the DB header);
        4. ``PRAGMA busy_timeout`` — connection-level setting, applied when
           ``busy_timeout_ms >= 0`` (``0`` disables the wait explicitly);
        5. wrap the connection in ``_ReadonlyGuard``;
        6. ``AgentFS.open_with(guard)`` — schema init must run **unlocked**;
        7. ``_enable_mvcc`` (MVCC only) — the journal mode must switch to
           "mvcc" **after** schema init (mvcc-mode DDL is kept in the
           in-memory MVCC store and is not visible to other connections);
        8. ``guard.lock()`` + ``PRAGMA query_only = 1`` (numeric! ``= ON``
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
            isolation_level=None if enable_mvcc else "DEFERRED",
        )

        # MVCC mode must be switched on AFTER schema init: the Limbo engine
        # keeps mvcc-mode DDL in the in-memory MVCC store, so a schema
        # created under ``journal_mode = "mvcc"`` is not visible to other
        # connections.  WAL, by contrast, is applied up front (it writes the
        # DB header).
        if enable_wal and not enable_mvcc:
            try:
                await _enable_wal(conn)
            except Exception as exc:
                logger.debug("Could not enable WAL mode: %s", exc)

        if busy_timeout_ms is not None and busy_timeout_ms >= 0:
            await conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")

        guard = _ReadonlyGuard(conn)

        # Schema init (CREATE TABLE IF NOT EXISTS, WAL journal config rows)
        # must run in pass-through, before enforcement starts.
        agentfs = await AgentFS.open_with(guard)

        if enable_mvcc:
            try:
                await _enable_mvcc(conn)
            except Exception as exc:
                logger.debug("Could not enable MVCC mode: %s", exc)

        if readonly:
            guard.lock()
            # Hard backstop on the underlying connection: even statements
            # that reach it without passing the guard (e.g. via a raw
            # ``cursor()``) are rejected by libSQL.  Note the numeric 1:
            # ``PRAGMA query_only = ON`` fails to parse on pyturso (verified
            # on both 0.4.4 and 0.7.2).
            await conn.execute("PRAGMA query_only = 1")

        return Workspace(
            agentfs,
            readonly=readonly,
            busy_timeout_ms=busy_timeout_ms,
            max_content_bytes=max_content_bytes,
        )

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
