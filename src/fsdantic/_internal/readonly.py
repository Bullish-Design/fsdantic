"""Read-only enforcement proxy over a turso connection.

The vendored AgentFS SDK performs a write on every read
(``UPDATE fs_inode SET atime`` inside ``Filesystem.read_file``), so a pure
``PRAGMA query_only``-style backstop is not sufficient for read-only
workspaces: every ``read_file`` would raise.  Fsdantic therefore owns a
small delegating wrapper around ``turso.aio.Connection`` that:

- passes everything through via ``__getattr__`` until :meth:`_ReadonlyGuard.lock`
  flips enforcement on;
- once locked, **swallows** the SDK's access-time maintenance write (exact
  statement prefix) so reads succeed without writing;
- once locked, **rejects** every other write statement with a typed
  :class:`~fsdantic.exceptions.WorkspaceError`.

The guard is connection-level (not statement-level): ``cursor``/``close``/
properties/``__aenter__``/``__aexit__`` are covered automatically by the
``__getattr__`` delegation, and the only SQL entry points the SDK uses for
writes — ``execute``/``executemany``/``executescript``/``commit`` — are
overridden explicitly so a new write path cannot silently bypass
enforcement.  As a hard backstop for anything that still reaches the raw
connection (e.g. cursors created via ``connection.cursor()``), the open
path additionally applies ``PRAGMA query_only = 1`` on the underlying
connection after locking.
"""

from __future__ import annotations

from typing import Any

from turso.aio import Connection as TursoConnection

from ..exceptions import WorkspaceError

# First SQL keywords that begin a write statement.  This mirrors pyturso's
# own ``_is_dml`` (INSERT/UPDATE/DELETE/REPLACE) and extends it with DDL
# (CREATE/DROP/ALTER).  ``WITH``-prefixed DML is conservatively NOT
# classified — the same caveat pyturso documents for ``_is_dml`` — because
# distinguishing ``WITH ... SELECT`` from ``WITH ... UPDATE`` would require
# full SQL parsing.  Acceptable today: no SDK path issues ``WITH``-prefixed
# DML; audit if that ever changes.
_WRITE_KEYWORDS: frozenset[str] = frozenset(
    {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER"}
)

# Prefix of the AgentFS SDK's access-time maintenance write
# (``agentfs_sdk/filesystem.py`` -> ``Filesystem.read_file``)::
#
#     UPDATE fs_inode SET atime = ? WHERE ino = ?
#
# Matched after uppercasing + stripping so the swallow is robust to
# whitespace/case variance while remaining an exact prefix pin.  If the SDK
# ever changes this statement, the swallow misses and the read raises under
# ``PRAGMA query_only = 1`` — this is the atime-SQL pin asserted by
# ``tests/test_readonly.py``.
_ATIME_PREFIX = "UPDATE FS_INODE SET ATIME"


def _first_keyword(sql: str) -> str:
    """Return the uppercased first SQL keyword, ignoring leading whitespace
    and ``--``/``/* */`` comments (mirrors pyturso's ``_first_keyword``)."""
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        if c.isspace():
            i += 1
            continue
        if c == "-" and i + 1 < n and sql[i + 1] == "-":
            i += 2
            while i < n and sql[i] not in ("\r", "\n"):
                i += 1
            continue
        if c == "/" and i + 1 < n and sql[i + 1] == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i = min(i + 2, n)
            continue
        break
    j = i
    while j < n and (sql[j].isalpha() or sql[j] == "_"):
        j += 1
    return sql[i:j].upper()


class _ReadonlyGuard:
    """Delegating connection proxy that enforces read-only mode.

    All attributes are delegated to the wrapped ``turso.aio.Connection``
    except the explicit overrides below (``execute``/``executemany``/
    ``executescript``/``commit``) and the read-only state itself
    (``locked``/``readonly``).  The proxy is created **unlocked** so schema
    initialization through ``AgentFS.open_with(guard)`` runs in
    pass-through; :meth:`lock` flips enforcement on afterwards.
    """

    def __init__(self, conn: TursoConnection) -> None:
        """Wrap ``conn``.  Starts unlocked (read/write pass-through)."""
        self._conn = conn
        self._locked = False

    # -- read-only state ---------------------------------------------------

    @property
    def readonly(self) -> bool:
        """True once enforcement is active (after :meth:`lock`)."""
        return self._locked

    def lock(self) -> None:
        """Flip enforcement on.

        After this call, write statements raise :class:`WorkspaceError` and
        the SDK's atime maintenance write is swallowed.  Callers must run
        schema initialization *before* locking.
        """
        self._locked = True

    # -- delegation --------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Delegate every other attribute (methods, properties, dunders)."""
        return getattr(self._conn, name)

    # -- explicit SQL entry points ----------------------------------------

    async def execute(self, sql: str, parameters: Any = ()) -> Any:
        """Execute ``sql`` subject to read-only enforcement when locked.

        When unlocked, passes through unchanged.  When locked:

        1. The SDK's atime maintenance write is **swallowed** — a no-op
           ``SELECT 0`` cursor is returned so ``read_file`` keeps working
           without touching the database.
        2. Any other statement whose first keyword is a write keyword
           raises :class:`WorkspaceError` (``WORKSPACE_READONLY``).
        3. Everything else (SELECT, PRAGMA, ...) passes through.
        """
        if not self._locked:
            return await self._conn.execute(sql, parameters)

        stripped = sql.strip()
        if stripped.upper().startswith(_ATIME_PREFIX):
            # Access-time maintenance write: neutralize it.  The SDK's
            # ``commit()`` after this is harmless (a SELECT cursor).
            return await self._conn.execute("SELECT 0")

        if _first_keyword(sql) in _WRITE_KEYWORDS:
            raise WorkspaceError(
                "Workspace is read-only: write statements are rejected",
                code="WORKSPACE_READONLY",
            )

        return await self._conn.execute(sql, parameters)

    async def executemany(self, sql: str, parameters: Any) -> Any:
        """Reject write statements when locked; otherwise pass through."""
        if self._locked and _first_keyword(sql) in _WRITE_KEYWORDS:
            raise WorkspaceError(
                "Workspace is read-only: write statements are rejected",
                code="WORKSPACE_READONLY",
            )
        return await self._conn.executemany(sql, parameters)

    async def executescript(self, sql_script: str) -> Any:
        """Reject scripts when locked (scripts are write-oriented by nature).

        The SDK only uses ``executescript`` for schema initialization, which
        runs before :meth:`lock`.  A script cannot be classified by first
        keyword alone, so when locked it is rejected conservatively.
        """
        if self._locked:
            raise WorkspaceError(
                "Workspace is read-only: write statements are rejected",
                code="WORKSPACE_READONLY",
            )
        return await self._conn.executescript(sql_script)

    async def commit(self) -> None:
        """Pass commits through.

        Read-only reads end with a ``commit`` (the SDK commits after the
        swallowed atime write); committing an empty transaction is harmless.
        """
        return await self._conn.commit()
