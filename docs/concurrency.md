# Concurrency contract

Fsdantic's concurrency behavior is configured entirely through
[`Fsdantic.open`](../src/fsdantic/client.py) (`enable_wal`, `enable_mvcc`,
`busy_timeout_ms`). This page documents the semantics.

> Driver note: fsdantic consumes pyturso **>= 0.7.2** (via the
> `Bullish-Design/agentfs` SDK fork, which bumps upstream's `pyturso==0.4.4`
> pin). The behaviors below are verified against 0.7.2; the only documented
> 0.4.4-specific caveat (GIL-holding busy-wait) no longer applies.

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

### Busy-wait on contention (pyturso >= 0.7.2)

A contended async write busy-waits inside pyturso's native libSQL layer
**with the GIL released**: the event loop and other Python threads stay
responsive during the wait (verified by probe — a watchdog thread keeps
running and an in-process lock release from another connection unblocks
the waiter).  The write then **succeeds** once the lock is released,
"wait, then succeed" being the normal contention outcome, bounded by
`busy_timeout_ms`.  If the lock is not released within the timeout the
write raises `OperationalError: database is locked`.

Multi-process caveat (unchanged from pyturso 0.4.4, verified on 0.7.2):
pyturso's local libSQL build takes a file-level lock at `connect()` —
concurrent multi-process access to a database file is still not
supported.  `busy_timeout_ms=0` disables the wait (fail immediately).

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
