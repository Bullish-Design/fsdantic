# Concurrency contract

Fsdantic's concurrency behavior is configured entirely through
[`Fsdantic.open`](../src/fsdantic/client.py) (`enable_wal`, `enable_mvcc`,
`busy_timeout_ms`). This page documents the semantics.

## Single connection

Each `turso.aio.Connection` serializes its own operations via a dedicated
worker thread — operations issued sequentially through one workspace never
interleave with each other. No application-level locking is needed for
sequential async access on a single connection.

## WAL mode (default)

`enable_wal=True` (the default) enables `PRAGMA journal_mode=wal`:

- Unlimited concurrent readers alongside a single writer on the same
  database file.
- A writer that contends for the write lock **waits** up to
  `busy_timeout_ms` (default 5000) instead of failing immediately with
  "database is locked".
- Pass `busy_timeout_ms=0` to disable the wait (the raw turso default:
  fail immediately on contention).

### pyturso 0.4.4 limitation (busy-wait holds the GIL)

A contended async write busy-waits inside pyturso's native libSQL layer
**without releasing the GIL**: the event loop (and every other Python
thread in the process) is frozen for up to `busy_timeout_ms` before the
write either succeeds (only if the lock is released by something outside
the process) or raises `OperationalError: database is locked`.  Because
pyturso's local libSQL build also takes a file-level lock at `connect()`
(no concurrent multi-process access to a database file), an in-process
writer cannot be unblocked from another thread — in practice a single
process sees contention as "freeze for up to `busy_timeout_ms`, then
raise".  `busy_timeout_ms` still bounds that freeze; `0` makes it fail
instantly.

## MVCC (`enable_mvcc=True`)

MVCC enables `BEGIN CONCURRENT` support (`experimental_features="mvcc"`,
`isolation_level=None`). Multiple connections can write concurrently:

- Non-conflicting writes succeed.
- Conflicting writes raise `DatabaseError` at **execute** time.

Callers must catch `DatabaseError` and retry the write. Fsdantic provides
[`Workspace.serialized`](../src/fsdantic/workspace.py) as a same-process
serialization *primitive* (a per-workspace `asyncio.Lock`) for atomic
read-modify-write sequences — callers own the policy of when it is needed.
It does not coordinate across processes or connections.
