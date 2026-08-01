"""Regression tests for Phase 1: atomic CAS versioned save (C1, M1, M2).

Covers:
- C1: concurrent save() never silently loses an update (SQL-level CAS).
- M1: save() preserves created_at across updates.
- M2: save_many() inherits version checks/increments/conflicts.
"""

import asyncio
import time

import pytest
from pydantic import BaseModel

from fsdantic import KVConflictError, TypedKVRepository, VersionedKVRecord


class RaceRecord(VersionedKVRecord):
    """Versioned record used by race tests."""

    value: str


class PlainRecord(BaseModel):
    """Non-versioned record used by conflict tests."""

    value: str


@pytest.mark.asyncio
class TestSaveAtomicCas:
    """C1: concurrent saves must not silently lose an update."""

    async def test_save_version_race_no_lost_update(self, agent_fs):
        """Two concurrent savers: exactly one conflicts, one value survives."""
        repo = TypedKVRepository[RaceRecord](agent_fs, prefix="rec:")
        await repo.save("k", RaceRecord(value="v0"))

        gate = asyncio.Event()
        orig_get = repo._manager.get
        barrier_count = 0

        async def sync_get(key, default=object()):
            nonlocal barrier_count
            barrier_count += 1
            if barrier_count == 2:
                gate.set()
            else:
                await gate.wait()
            return await orig_get(key, default=default)

        repo._manager.get = sync_get
        try:

            async def save_a() -> None:
                await repo.save("k", RaceRecord(value="A"))

            async def save_b() -> None:
                await repo.save("k", RaceRecord(value="B"))

            results = await asyncio.gather(save_a(), save_b(), return_exceptions=True)
        finally:
            repo._manager.get = orig_get

        conflicts = [r for r in results if isinstance(r, KVConflictError)]
        ok_saves = [r for r in results if r is None]

        assert len(conflicts) == 1, f"expected exactly one conflict, got {results}"
        assert len(ok_saves) == 1

        stored = await agent_fs.kv.get("rec:k")
        assert stored["version"] == 2
        assert stored["value"] in ("A", "B")

    async def test_save_create_race_no_lost_update(self, agent_fs):
        """Concurrent creates of a brand-new key never lose an update.

        Intra-process, the per-key asyncio.Lock serializes the two saves:
        the first creates at version 1 and the second upgrades to version 2
        (matching sequential fresh-record save semantics).  No update is lost
        and the version counter stays monotonic.  The cross-process guard is
        the SQL ``cas_insert`` conflict return (asserted separately).
        """
        repo = TypedKVRepository[RaceRecord](agent_fs, prefix="rec:")

        async def save_one(label: str):
            await repo.save("new", RaceRecord(value=label))

        results = await asyncio.gather(save_one("x"), save_one("y"), return_exceptions=True)
        assert all(r is None for r in results), f"unexpected errors: {results}"

        stored = await agent_fs.kv.get("rec:new")
        assert stored["version"] == 2
        assert stored["value"] in ("x", "y")

    async def test_cas_update_conflict_reports_actual_version(self, agent_fs):
        """A stale expected_version produces a conflict with the fresh version."""
        repo = TypedKVRepository[RaceRecord](agent_fs, prefix="rec:")
        await repo.save("k", RaceRecord(value="v1"))

        # Advance the stored version behind the caller's back.
        await repo.save("k", RaceRecord(value="v2"))

        with pytest.raises(KVConflictError) as exc_info:
            await repo.save("k", RaceRecord(value="v3"), expected_version=1)

        assert exc_info.value.expected_version == 1
        assert exc_info.value.actual_version == 2

    async def test_cas_helpers_single_and_mvcc_connections(self, agent_fs):
        """CAS primitives behave identically on a single connection and two."""
        from fsdantic._internal.kv_cas import cas_insert, cas_update, get_raw, key_exists

        conn = agent_fs.get_database()

        # Single connection semantics.
        assert await cas_insert(conn, "cas:k1", '"v1"') is True
        assert await cas_insert(conn, "cas:k1", '"v2"') is False  # conflict
        assert await key_exists(conn, "cas:k1") is True
        assert await key_exists(conn, "cas:missing") is False
        assert await get_raw(conn, "cas:k1") == '"v1"'
        assert await cas_update(conn, "cas:k1", '"v1"', '"v3"') is True
        assert await cas_update(conn, "cas:k1", '"v1"', '"v4"') is False  # stale

        # Readable through the SDK after our direct writes.
        assert await agent_fs.kv.get("cas:k1") == "v3"

    async def test_cas_insert_conflict_blocks_duplicate_create(self, agent_fs):
        """Cross-process guard: cas_insert returns False when the key exists."""
        from fsdantic._internal.kv_cas import cas_insert

        conn = agent_fs.get_database()
        assert await cas_insert(conn, "cas:create", '"payload"') is True
        # A second creator sees the row and must not overwrite it.
        assert await cas_insert(conn, "cas:create", '"other"') is False
        assert await agent_fs.kv.get("cas:create") == "payload"


@pytest.mark.asyncio
class TestSaveCreatedAt:
    """M1: created_at must be preserved across updates."""

    async def test_save_existing_key_preserves_created_at(self, agent_fs):
        repo = TypedKVRepository[RaceRecord](agent_fs, prefix="rec:")

        first = RaceRecord(value="v1")
        await repo.save("k", first)
        stored_first = await agent_fs.kv.get("rec:k")

        time.sleep(0.01)
        second = RaceRecord(value="v2")  # fresh caller record
        await repo.save("k", second)
        stored_second = await agent_fs.kv.get("rec:k")

        assert stored_first["created_at"] == stored_second["created_at"]
        assert stored_second["version"] == 2

    async def test_save_legacy_payload_without_created_at(self, agent_fs):
        """A legacy payload without created_at does not crash the CAS save."""
        repo = TypedKVRepository[RaceRecord](agent_fs, prefix="rec:")
        await agent_fs.kv.set("rec:k", {"version": 1, "value": "legacy"})

        await repo.save("k", RaceRecord(value="new"))
        stored = await agent_fs.kv.get("rec:k")
        assert stored["version"] == 2
        assert stored["value"] == "new"


@pytest.mark.asyncio
class TestSaveManyVersioning:
    """M2: save_many must go through the versioned save path."""

    async def test_save_many_increments_version(self, agent_fs):
        repo = TypedKVRepository[RaceRecord](agent_fs, prefix="rec:")
        await repo.save("k", RaceRecord(value="v1"))

        loaded = await repo.load("k", RaceRecord)
        assert loaded is not None
        loaded.value = "v2"
        await repo.save_many([("k", loaded)])

        stored = await agent_fs.kv.get("rec:k")
        assert stored["version"] == 2
        assert stored["value"] == "v2"

    async def test_save_many_conflict_reports_error_per_item(self, agent_fs):
        repo = TypedKVRepository[RaceRecord](agent_fs, prefix="rec:")
        await repo.save("k", RaceRecord(value="v1"))
        await repo.save("k", RaceRecord(value="v2"))  # stored version is now 2

        stale = RaceRecord(value="stale")  # caller record at version 1
        result = await repo.save_many([("k", stale), ("other", RaceRecord(value="ok"))])

        by_key = {item.key_or_path: item for item in result.items}
        assert by_key["k"].ok is False
        assert "version conflict" in (by_key["k"].error or "")
        assert by_key["other"].ok is True

        # The other item really was written.
        stored_other = await agent_fs.kv.get("rec:other")
        assert stored_other["value"] == "ok"
