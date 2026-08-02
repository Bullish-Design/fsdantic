"""Atomic compare-and-set primitives over the AgentFS KV table.

Coupling note: these statements target the AgentFS kv_store schema::

    kv_store(key TEXT PRIMARY KEY, value TEXT NOT NULL,
             created_at INTEGER DEFAULT (unixepoch()),
             updated_at INTEGER DEFAULT (unixepoch()))

agentfs-sdk is pinned ``>=0.6.4`` and this schema is stable (see
``sdk/python/agentfs_sdk/kvstore.py`` in the vendored SDK under
``.context/agentfs-main/``).  The column names and the JSON-text payload
format are part of the coupling contract; keep this file in sync with any
upstream schema change.

Commit semantics (verified against pyturso 0.7.2; unchanged from 0.4.4)::

    - ``cursor.rowcount`` is accurate for INSERT ... ON CONFLICT DO NOTHING
      (1 on insert, 0 on conflict) and for UPDATE ... WHERE key=? AND value=?
      (1 on match, 0 on no-match).
    - The default turso connection uses ``isolation_level='DEFERRED'``; a
      bare ``execute()`` is NOT persisted until ``commit()``.  The AgentFS
      SDK commits after every write, so these helpers follow the same
      pattern: commit immediately after each mutating statement.
"""

from __future__ import annotations

import json
from typing import Any

from turso.aio import Connection

_MISSING = object()


def _sdk_serialize(value: Any) -> str:
    """Serialize a value exactly like the AgentFS SDK ``KvStore.set``.

    The SDK stores ``json.dumps(value)`` (``ensure_ascii=True``, no indent).
    Re-serializing a payload fetched via :func:`get_raw` reproduces the stored
    text byte-for-byte for JSON-native values (dict key order is preserved
    through ``json.loads``/``json.dumps``), which makes the payload-equality
    CAS sound.
    """
    return json.dumps(value)


async def cas_insert(conn: Connection, key: str, raw_value: str) -> bool:
    """Create ``key`` only if absent.

    Returns ``True`` when the row was inserted, ``False`` when the key
    already existed (conflict).
    """
    cursor = await conn.execute(
        "INSERT INTO kv_store (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
        (key, raw_value),
    )
    inserted = cursor.rowcount == 1
    await conn.commit()
    return inserted


async def cas_update(conn: Connection, key: str, expected_raw: str, new_raw: str) -> bool:
    """Update ``key`` only if its current value equals ``expected_raw``.

    Returns ``True`` when the update applied, ``False`` when the stored
    payload no longer matches (conflict).
    """
    cursor = await conn.execute(
        "UPDATE kv_store SET value = ?, updated_at = unixepoch() WHERE key = ? AND value = ?",
        (new_raw, key, expected_raw),
    )
    updated = cursor.rowcount == 1
    await conn.commit()
    return updated


async def key_exists(conn: Connection, key: str) -> bool:
    """O(1) existence check.  True when the key has a row, regardless of value.

    Important: results are consumed with ``fetchall()``, NOT ``fetchone()``.
    In pyturso, a ``fetchone()`` that returns a row leaves the statement
    active (``Status.Row``), which holds an implicit READ transaction open
    on the connection; a subsequent interleaved DELETE+commit on the same
    connection can then be silently lost.  ``fetchall()`` exhausts the
    statement, which finalizes it and releases the read transaction.
    """
    cursor = await conn.execute("SELECT 1 FROM kv_store WHERE key = ?", (key,))
    rows = await cursor.fetchall()
    return len(rows) > 0


async def get_raw(conn: Connection, key: str) -> str | None:
    """Return the raw JSON text for ``key``, or ``None`` when missing.

    Uses ``fetchall()`` for the same cursor-finalization reason as
    :func:`key_exists` (pyturso leaves a read transaction open when a
    ``fetchone()`` returns a row).
    """
    cursor = await conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,))
    rows = await cursor.fetchall()
    if not rows:
        return None
    return rows[0][0]
