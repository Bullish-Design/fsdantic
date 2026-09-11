# 02 — Defects

Each entry states the evidence, the root cause in real code, and the blast
radius. Entries are graded:

- **BUG** — the code does something wrong. Fix it.
- **LIMIT** — the code does what it was designed to do, and the design is too
  small for the goal. Redesign it.
- **RISK** — no failure observed yet; the code makes one reachable.

## 1. BUG — `files.write_many` corrupts, fails or aborts above `concurrency_limit=1`

**Severity: critical. This is shipped. It can abort the user's process.**

### 1.1 Evidence

Writing 50 new small files to one workspace. Probe `/tmp/fsd_b.py`.

```
limit=  1    355.2 ms failed= 0/50
limit=  2    320.8 ms failed=49/50   cannot start a write statement - SQL statements in progress
limit=  4     98.6 ms failed=49/50   cannot start a write statement - SQL statements in progress
limit= 16     86.1 ms failed=49/50   cannot start a write statement - SQL statements in progress
```

Only the first item succeeds. The batch reports `ok=False` on the other 49.

The default is `concurrency_limit=10` (`src/fsdantic/files.py:377`). **The
default is broken.** A caller who writes `await ws.files.write_many(pairs)`
loses 98 % of the batch.

### 1.2 Three distinct failure modes

Probe `/tmp/fsd_e.py` separates them by what the write targets.

| Case | Target | Result |
|---|---|---|
| **A. New files, shallow** | `/f0.txt` … `/f49.txt`, none exist | 49/50 fail: `cannot start a write statement - SQL statements in progress` |
| **B. Overwrite existing** | same 50 paths, seeded first | 2/50 fail: `UNIQUE constraint failed: fs_data.(ino, chunk_index) (19)` |
| **C. New files, shared new parent** | `/x/y/z/g0.txt` … `/g49.txt` | **Process aborts.** Rust panic, `SIGABRT`, core dumped |

Case C output:

```
thread '<unnamed>' panicked at core/storage/wal.rs:3367:9:
end_write_tx called while write lock not held according to connection state
```

Case C is the worst. It is not an exception. `try`/`except` cannot catch it.
The interpreter dies. A user calling `write_many` on a fresh directory tree —
the most natural use of the API — kills their process.

Case B is the second worst. `UNIQUE constraint failed: fs_data.(ino,
chunk_index)` means two interleaved writes collided inside the chunk table. A
partial chunk set is a **silently truncated file**. The batch reports 48 items
`ok=True`. Some of those files may be wrong.

### 1.3 Root cause

Three layers stack up.

**Layer 1 — the SDK holds a cursor across `await` points.**
`agentfs/sdk/python/agentfs_sdk/filesystem.py:259-283`, `_create_inode`:

```python
cursor = self._db.cursor()
try:
    await cursor.execute("INSERT INTO fs_inode ... RETURNING ino", (...))
    row = await cursor.fetchone()
    ...
finally:
    await cursor.close()
await self._db.commit()
```

The SDK's own comment at `filesystem.py:262-263` states the constraint: *"We
use RETURNING clause which requires explicit cursor close when working with
CDC-enabled TursoDB connections."* Between `cursor.execute` and `cursor.close`
there are two `await`s. The event loop can run another coroutine there. That
coroutine issues a write on the **same connection**, and the driver refuses
because a statement is still in progress.

**Layer 2 — `write_file` is not a transaction.** For a new file it commits five
times (`03-performance.md` §2). Each commit is a boundary another coroutine can
land inside. `_ensure_parent_dirs` (`filesystem.py:301-330`) is a
check-then-create race: two coroutines both see `/x/y/z` missing and both
create it.

**Layer 3 — fsdantic fans out onto one connection.**
`src/fsdantic/files.py:393-409`:

```python
semaphore = asyncio.Semaphore(concurrency_limit)
...
gathered = await asyncio.gather(
    *(_write_one(index, item) for index, item in enumerate(items)),
    return_exceptions=True,
)
```

Every `_write_one` calls `self.write` → `self.agent_fs.fs.write_file` → the one
shared `turso.aio.Connection` (`agentfs/sdk/python/agentfs_sdk/agentfs.py:42`).

**The premise of `write_many` contradicts the connection model beneath it.**
fsdantic's own README says so: *"Each connection serializes its own operations
via a dedicated worker thread, so no application-level locking is needed for
**sequential** async access on a single workspace."* `write_many` is not
sequential access. The docstring at `files.py:381` promises "bounded
concurrency"; the driver cannot deliver it.

### 1.4 Why the other batch APIs are fine

Probe `/tmp/fsd_c.py`, 30 items each, `concurrency_limit` 1 and 8:

```
files.read_many limit=1 failed=0/30      limit=8 failed=0/30
kv.get_many     limit=1 failed=0/30      limit=8 failed=0/30
kv.set_many     limit=1 failed=0/30      limit=8 failed=0/30
kv.delete_many  limit=1 failed=0/30      limit=8 failed=0/30
repo.save_many  limit=1 failed=0/30      limit=8 failed=0/30
repo.load_many  limit=1 failed=0/30      limit=8 failed=0/30
KVTransaction ok
```

All clean. The reason is precise: **none of them takes the cursor path.**
`KvStore.set`/`get`/`delete` (`agentfs/sdk/python/agentfs_sdk/kvstore.py`) issue
one `conn.execute` and one `conn.commit` with no `RETURNING` cursor held open.
The driver's per-connection worker thread serializes those cleanly.

`files.read_many` is clean despite `read_file` doing an `UPDATE fs_inode SET
atime` plus a commit (`filesystem.py:482-483`) — again, no held cursor.

`repository.load_many` has **no `concurrency_limit` parameter**
(`src/fsdantic/repository.py:392`). Passing one raises `TypeError`. The
briefing's question about it does not apply.

`materialization.py` uses **no concurrency at all**. `grep -n "gather\|Semaphore\|create_task"
src/fsdantic/materialization.py` returns nothing. `_copy_recursive`
(`materialization.py:435`) and `_list_all_files` (`materialization.py:562`) are
sequential `await` loops. Materialization is safe.

So the defect is **one method**: `FileManager.write_many`.

### 1.5 Blast radius

| Consumer | Impact |
|---|---|
| `files.write_many` with default args | 98 % item loss, or process abort |
| `overlay.merge` | Safe — sequential (`overlay.py:199-276`) |
| `materialize.to_disk` | Safe — sequential |
| Any user code that fans out `files.write` with `asyncio.gather` | Same three failure modes. The bug is in the pattern, not only the method. |

### 1.6 Fix

**Interim, today:** serialize internally. Ignore `concurrency_limit` for
writes, or clamp it to 1, and say so in the docstring and the README. A
`write_many` that is slow is better than one that truncates files and aborts
processes.

**Correct, Stage 1:** replace the fan-out with a single batched transaction
(`04-options.md` option A). It is both correct and 88x faster. The
`concurrency_limit` parameter should then be deprecated, not fixed — the
connection model gives it no meaning.

## 2. LIMIT — one connection per workspace, serialized by a worker thread

**Severity: design constraint. Accept it and design around it.**

`AgentFS` holds exactly one `turso.aio.Connection`
(`agentfs/sdk/python/agentfs_sdk/agentfs.py:42`), shared by `fs`, `kv` and
`tools` (`agentfs.py:106-108`). There is no pool, no per-task connection, no
lock.

`turso.aio` serializes work through a dedicated worker thread. That gives
safety for *sequential* async use and nothing for concurrent use. fsdantic
documents this correctly in `docs/concurrency.md` §"Single connection" and
`src/fsdantic/client.py:6-8`.

Consequences:

- Concurrency inside one workspace buys nothing. There is one worker thread.
- Concurrency **across** workspaces is also blocked: `turso.aio.connect` takes a
  file-level lock, so two processes cannot open one database file
  (`docs/concurrency.md`, "Multi-process caveat"). §7 below shows the Rust
  mount takes the same lock.
- Throughput must come from **fewer, larger operations**, never from more
  parallel ones. This single sentence should govern the whole redesign.

The SDK docstring at `agentfs.py:105` says the three components initialize "in
parallel". They are sequential `await`s. The comment is wrong. Harmless, but it
misleads a reader into thinking concurrency is available.

## 3. LIMIT — the write path costs 5 commits and 15 statements per new file

Full analysis in [`03-performance.md`](03-performance.md). Summary here because
it is the largest defect by user impact.

Measured with a spying connection proxy (`/tmp/fsd_g.py`), writing
`/dir/a.bin`, 6000 bytes, into a database where `/dir` does not exist:

| Phase | Statements | Commits |
|---|---|---|
| `_ensure_parent_dirs` creates `/dir` | 3 (+1 cursor `INSERT ... RETURNING`) | 2 |
| `_resolve_path` re-walks from root | 2 | 0 |
| `_resolve_parent` walks again | 1 | 0 |
| directory-mode guard | 1 | 0 |
| create the file inode + dentry | 2 (+1 cursor) | 2 |
| `_update_file_content` | 4 | 1 |
| **Total** | **15** | **5** |

A non-empty commit costs **2.7 ms** on this machine. Five commits is 13.6 ms.
Measured cost is 12.5 ms/file. The commits are the whole bill.

An overwrite of an existing file costs 8 statements and 1 commit — about
3.8 ms. So the create path is 3x the overwrite path.

`write_file` walks the path from the root **three times** before writing a
byte, in `_ensure_parent_dirs` (`filesystem.py:301`), `_resolve_path`
(`filesystem.py:212`) and `_resolve_parent` (`filesystem.py:241`). At depth D
that is ~3D serialized round trips.

## 4. LIMIT — `diff()` is O(whole tree), not O(changes)

### 4.1 Evidence

Base = 447 devman files. Overlay = one modified file, one new file.

```
diff(base)   1865.4 ms   changes=448   {'deleted': 446, 'added': 1, 'modified': 1}
```

Two real changes. 448 reported. 1.9 seconds.

### 4.2 Root cause

`src/fsdantic/materialization.py:319` calls `_list_all_files` twice — once per
layer. `_list_all_files` (`materialization.py:562-604`) walks with
`fs.fs.readdir(path)` then `fs.fs.stat(entry_path)` per entry. Each `stat`
runs a fresh `_resolve_path` from the root, so the cost is O(entries × depth)
round trips, times two layers.

Then, for every path present in both layers **at the same size**, `diff` reads
**both files end to end** and compares (`materialization.py:396-401`,
`compare_streams`). On an unchanged 447-file repository that is 894 full file
reads to discover that nothing changed.

The 446 `deleted` entries are correct by the documented contract — the
docstring at `materialization.py:339-341` calls it a "visibility delta" — but
that contract is not what a caller wants. A caller asks "what changed?" and
gets "everything in the base is missing from the overlay."

### 4.3 Why the design forces this

fsdantic's base and overlay are two independent flat databases
(`01-gap-analysis.md` §2). There is no shared inode space and no whiteout
table, so there is nothing to query for "the delta". The delta must be
recomputed by comparing two full trees. AgentFS's own `fs_whiteout` and
`fs_origin` exist to make this O(changes); fsdantic does not use them.

### 4.4 Fix

Two levels.

- **Cheap:** replace the `readdir`+`stat` walk with one recursive CTE over
  `fs_dentry` joined to `fs_inode`. One round trip per layer instead of
  thousands. Compare `(size, mtime)` first and only read content on a genuine
  size match. This keeps the two-database design and should cut the 1.9 s to
  tens of milliseconds.
- **Correct:** adopt the real AgentFS overlay tables so the delta is a table
  scan of `fs_whiteout` plus the delta layer's own `fs_dentry`. This also makes
  fsdantic databases mountable. Larger change; see `04-options.md`.

## 5. LIMIT — `import fsdantic` costs 335-448 ms

**This, not the workspace open, is what disqualified fsdantic from devman's
`enterShell`.**

```
$ python -X importtime -c "import fsdantic"
import time:       432 |     447639 | fsdantic
import time:       552 |     445919 |   fsdantic.client
import time:    205157 |     304873 |     fsdantic.models      <-- 205 ms self time
import time:       432 |      93127 |     agentfs_sdk
import time:       382 |      31869 |       pydantic
```

`src/fsdantic/models.py` builds its pydantic models at import time. That is
205 ms of the 448 ms. `agentfs_sdk` adds 93 ms, mostly `asyncio` and
`turso.lib_aio`.

Open cost, by contrast, is small:

| Action | Time |
|---|---|
| Open an existing 447-file database | 6.3-11.6 ms |
| Create a new database | 34.1 ms |
| Second open in the same process | 4.2-6.3 ms |

The briefed 44 ms was database creation. The real budget-breaker is 10x that,
and it happens before any fsdantic call runs.

**Fix:** make `models.py` lazy. Move heavy model construction behind
`__getattr__` at package level (PEP 562), so `import fsdantic` costs an import
of `client` only. Target under 50 ms.

## 6. RISK — `write_file` is not atomic

`agentfs/sdk/python/agentfs_sdk/filesystem.py` commits three to five times per
write with no enclosing transaction. Between those commits a reader sees:

- an inode with `nlink=0` and no dentry (after `_create_inode` commits at
  `filesystem.py:282`);
- a linked file with no content (after `_create_dentry` commits at
  `filesystem.py:299`);
- during overwrite, a file whose chunks were deleted but not yet reinserted
  (`_update_file_content` deletes at `filesystem.py:418` and commits at
  `filesystem.py:443`).

The third case is a **zero-length window on every overwrite**. A crash there
loses the file's content. `rename` has the same shape and admits it in a
comment at `filesystem.py:870-871`: *"turso.aio doesn't support explicit BEGIN,
but execute should be atomic."* It then calls
`_remove_dentry_and_maybe_inode`, which commits at `filesystem.py:825`, so a
failed `rename` has already committed the destination deletion.

fsdantic inherits all of this. It is not fsdantic's bug, but it is fsdantic's
problem: fsdantic is the layer that promises a clean API.

A bulk-ingest path built on one explicit transaction (option A) fixes this for
the ingest case as a side effect.

## 7. LIMIT — a mounted database is exclusively locked

Detailed in [`01-gap-analysis.md`](01-gap-analysis.md) §3.3.3.

```
$ agentfs diff <agent>          # while mounted
Error: database error: Locking error: Failed locking file.
File is locked by another process
```

Blast radius: **fsdantic and a live mount are mutually exclusive on one
database.** This kills the most attractive naive design — "mount it for the
compiler, drive it from Python at the same time". Any combined design needs an
explicit ownership handoff, or must route Python access through
`agentfs serve`.

## 8. Corrections to the briefed findings

| # | Briefed claim | Verdict |
|---|---|---|
| 1 | Correctness passes; COW + materialize model is sound | **Confirmed.** Re-verified: 447-file bulk ingest, 60 random files read back byte-identical, `stat` sizes correct, `readdir` correct. |
| 2 | ~150x slower than the filesystem; 44 ms workspace open blocked `enterShell` | **Confirmed on the ratio** (measured 128-164x across runs). **Wrong on the cause of the `enterShell` block.** Opening an existing database costs 6-12 ms. The 44 ms was database *creation*. The real blocker is `import fsdantic` at 335-448 ms — see §5. |
| 3 | `write_many` broken above `concurrency_limit=1`; error `cannot commit transaction - SQL statements in progress`; check the other batch APIs | **Confirmed but understated.** Error string is `cannot start a write statement - SQL statements in progress`. Two further failure modes found: silent chunk corruption (`UNIQUE constraint failed: fs_data.(ino, chunk_index)`) and an **uncatchable process abort** (Rust panic in `core/storage/wal.rs:3367`). **The other APIs are clean** — `read_many`, `kv.get_many`, `kv.set_many`, `kv.delete_many`, `repository.save_many` and `KVTransaction` all pass at limit 8. `repository.load_many` has no `concurrency_limit` parameter. `materialization.py` uses no concurrency. |
| 4 | `diff()` is O(whole tree) | **Confirmed.** 1865 ms, 448 changes reported for 2 real changes. Root cause traced to `_list_all_files` at `materialization.py:562`. |
| 5 | fsdantic cannot mount; FUSE/NFS only in the Rust CLI | **Confirmed, and now quantified.** The mount was built and measured. It works, it is 6-12x slower than ext4, and it is **11.7x faster than fsdantic** at writing the same tree. It also holds an exclusive lock that excludes fsdantic entirely. |
| — | (not briefed) | **New:** fsdantic does not use the AgentFS overlay model at all. No `fs_whiteout`, no `fs_origin`, no `fs_overlay_config`. fsdantic overlays are not mountable and not readable by `agentfs diff`. See `01-gap-analysis.md` §2. |
