"""Tests for busy_timeout configuration and the ``serialized()`` primitive.

``busy_timeout_ms`` is applied by ``Fsdantic.open`` as ``PRAGMA
busy_timeout = {ms}`` on every connection created through the unified open
seam.  Default 5000; ``0`` disables the wait (the raw turso default).

On pyturso >= 0.7.2 (the pinned driver; see ``docs/concurrency.md``) a
contended async write busy-waits **with the GIL released**: the event loop
stays responsive and an in-process lock release from another connection
unblocks the waiter, so the "waits then succeeds" scenario is
orchestratable (see ``test_two_writers_waits_then_succeeds``).  These
tests pin the contract: the value is applied, ``0`` fails fast, a non-zero
timeout bounds the wait before the failure, and an in-process release lets
a waiting writer succeed.
"""

import asyncio
import time

import pytest

from fsdantic import Fsdantic

pytestmark = pytest.mark.asyncio


class TestBusyTimeoutPragma:
    async def test_busy_timeout_pragma_set(self, temp_db_path):
        """The configured value is applied to the connection and exposed."""
        workspace = await Fsdantic.open(path=temp_db_path, busy_timeout_ms=7500)
        try:
            assert workspace.busy_timeout_ms == 7500
            cursor = await workspace.connection.execute("PRAGMA busy_timeout")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 7500
        finally:
            await workspace.close()

    async def test_busy_timeout_default_applied(self, temp_db_path):
        """The default 5000 ms is applied when no value is given."""
        workspace = await Fsdantic.open(path=temp_db_path)
        try:
            assert workspace.busy_timeout_ms == 5000
            cursor = await workspace.connection.execute("PRAGMA busy_timeout")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 5000
        finally:
            await workspace.close()

    async def test_busy_timeout_zero_disables(self, temp_db_path):
        """busy_timeout_ms=0 sets the pragma to 0 (fail immediately)."""
        workspace = await Fsdantic.open(path=temp_db_path, busy_timeout_ms=0)
        try:
            assert workspace.busy_timeout_ms == 0
            cursor = await workspace.connection.execute("PRAGMA busy_timeout")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 0
        finally:
            await workspace.close()


class TestTwoWriters:
    async def test_two_writers_wait_bounded_by_timeout(self, temp_db_path):
        """A contended writer waits (not fails instantly) and the wait is
        bounded by busy_timeout_ms before raising "database is locked".

        ws1 never releases the lock, so the observable contract is: the
        failure is delayed by ~busy_timeout_ms rather than immediate.
        """
        ws1 = await Fsdantic.open(path=temp_db_path, busy_timeout_ms=700)
        ws2 = await Fsdantic.open(path=temp_db_path, busy_timeout_ms=700)
        try:
            await ws1.files.write("/init.txt", "init")

            # ws1 acquires and holds the write lock.  The default connection
            # uses isolation_level="DEFERRED" so the lock is held until the
            # explicit rollback below.
            await ws1.connection.execute("BEGIN IMMEDIATE")
            await ws1.connection.execute("UPDATE fs_inode SET mtime = mtime WHERE ino = 1")

            start = time.monotonic()
            with pytest.raises(Exception) as exc_info:
                await ws2.files.write("/locked.txt", "data")
            elapsed = time.monotonic() - start

            # It waited for ~busy_timeout (not the ~instant failure of
            # busy_timeout_ms=0), then failed with a lock error.
            assert "locked" in str(exc_info.value).lower()
            assert elapsed >= 0.4
            assert elapsed <= 2.5
        finally:
            await ws1.connection.rollback()
            await ws1.close()
            await ws2.close()

    async def test_two_writers_waits_then_succeeds(self, temp_db_path):
        """On pyturso >= 0.7.2 the busy-wait releases the GIL, so an
        in-process lock release unblocks a waiting writer: ws2's write waits
        for the lock and then SUCCEEDS once ws1 rolls back.

        This "waits then succeeds" scenario was not orchestratable on
        pyturso 0.4.4, where the busy-wait held the GIL and the release
        could not run until the wait timed out.
        """
        ws1 = await Fsdantic.open(path=temp_db_path, busy_timeout_ms=3000)
        ws2 = await Fsdantic.open(path=temp_db_path, busy_timeout_ms=3000)
        try:
            await ws1.files.write("/init.txt", "init")

            # ws1 acquires and holds the write lock; ws2's write enters the
            # busy-wait and cannot complete until ws1 releases.
            await ws1.connection.execute("BEGIN IMMEDIATE")
            await ws1.connection.execute("UPDATE fs_inode SET mtime = mtime WHERE ino = 1")

            start = time.monotonic()
            writer = asyncio.create_task(ws2.files.write("/released.txt", "data"))

            # Give ws2 a moment to enter the busy-wait, then release the
            # lock from ws1.  On pyturso >= 0.7.2 this rollback runs while
            # ws2 waits (GIL released), so ws2 unblocks and succeeds.
            await asyncio.sleep(0.25)
            await ws1.connection.rollback()

            await writer
            elapsed = time.monotonic() - start

            # The write succeeded after waiting ~0.25s (well under the
            # 3000 ms timeout), proving the in-process release unblocked it.
            assert elapsed < 2.5
            assert await ws2.files.read("/released.txt", mode="text") == "data"
        finally:
            await ws1.connection.rollback()
            await ws1.close()
            await ws2.close()

    async def test_two_writers_fail_without_timeout(self, temp_db_path):
        """With busy_timeout_ms=0 the second writer fails immediately on
        lock contention (no wait)."""
        ws1 = await Fsdantic.open(path=temp_db_path, busy_timeout_ms=0)
        ws2 = await Fsdantic.open(path=temp_db_path, busy_timeout_ms=0)
        try:
            # ws1 acquires and holds the write lock (same process is fine:
            # timeout 0 means no busy-wait, so nothing freezes).
            await ws1.connection.execute("BEGIN IMMEDIATE")
            await ws1.connection.execute("UPDATE fs_inode SET mtime = mtime WHERE ino = 1")

            start = time.monotonic()
            with pytest.raises(Exception) as exc_info:
                await ws2.files.write("/x.txt", "x")
            elapsed = time.monotonic() - start
            assert "locked" in str(exc_info.value).lower()
            # Failed fast — no busy wait.
            assert elapsed < 1.0
        finally:
            await ws1.connection.rollback()
            await ws1.close()
            await ws2.close()


class TestSerialized:
    async def test_serialized_context(self, temp_db_path):
        """Two coroutines interleaving read-modify-write inside
        serialized() never lose updates."""
        workspace = await Fsdantic.open(path=temp_db_path)
        try:
            await workspace.kv.set("counter", 0)

            async def worker(rounds: int) -> None:
                async with workspace.serialized():
                    for _ in range(rounds):
                        current = await workspace.kv.get("counter")
                        await workspace.kv.set("counter", current + 1)

            await asyncio.gather(worker(25), worker(25))
            assert await workspace.kv.get("counter") == 50
        finally:
            await workspace.close()
