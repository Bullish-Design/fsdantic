# ISSUE: `files.write_many` corrupts data and aborts the process

**Status:** open, unfixed
**Severity:** high — silent data loss, and an uncatchable process abort
**Component:** `src/fsdantic/files.py::FileManager.write_many`
**Found:** 2026-08-28, during an evaluation of fsdantic as a filesystem layer

---

## 1. Summary

`FileManager.write_many` accepts a `concurrency_limit` parameter and fans out
writes with an `asyncio.Semaphore`. Any value above 1 breaks.

Three failure modes appear, in rising order of harm:

1. Most items fail with `cannot start a write statement - SQL statements in progress`.
2. Some items report `ok=True` but store truncated or unreadable content.
3. The process aborts with a Rust panic that Python cannot catch.

`concurrency_limit=1` is the only safe value. The parameter's default is 10.

The pyturso 0.7.2 upgrade does **not** fix this. It was never able to: the
defect is a single connection issuing overlapping statements, and MVCC
addresses contention between separate connections. See §5.

---

## 2. Environment

| Component | Version |
|---|---|
| fsdantic | 0.7.0 |
| agentfs-sdk | 0.6.4 (fork `v0.6.4-pyturso-0.7.2`) |
| pyturso | 0.7.2 |
| pydantic | 2.14.0b1 |
| Python | 3.13.13 |
| Kernel | 6.18.38 |

---

## 3. Reproduction

Write 50 files of 200 bytes to one workspace. Run each configuration in a fresh
subprocess, because one failure mode aborts the interpreter.

```python
ws = await Fsdantic.open(path=path, enable_mvcc=mvcc)
pairs = [(f"/f{i}.txt", b"x" * 200) for i in range(50)]
res = await ws.files.write_many(pairs, mode="binary", concurrency_limit=limit)
```

`flat` writes to the workspace root. `newdir/` writes all 50 files into one
directory that does not yet exist.

| mvcc | limit | paths | time (ms) | failed | wrong on read-back | outcome |
|---|---|---|---|---|---|---|
| False | 1 | flat | 373.0 | 0 | 0 | ok |
| False | 4 | flat | 111.6 | 49 | 48 | `cannot start a write statement` |
| False | 8 | flat | — | — | — | **CRASH** `rc=-6` (SIGABRT) |
| True | 1 | flat | 541.3 | 0 | 0 | ok |
| True | 4 | flat | 260.3 | 49 | 48 | `cannot start a write statement` |
| True | 8 | flat | 136.8 | 48 | 47 | `cannot start a write statement` |
| False | 1 | newdir/ | 510.1 | 0 | 0 | ok |
| False | 4 | newdir/ | — | — | — | **CRASH** `rc=-6` (SIGABRT) |
| False | 8 | newdir/ | — | — | — | **CRASH** `rc=-6` (SIGABRT) |
| True | 1 | newdir/ | 779.5 | 0 | 0 | ok |
| True | 4 | newdir/ | 318.3 | 46 | 36 | `cannot start a write statement` |
| True | 8 | newdir/ | 169.8 | 46 | 46 | `cannot start a write statement` |

Read the table this way:

- **`limit=1` always passes.** Every other row fails.
- **MVCC does not repair the writes.** Failure counts stay near 100%.
- **MVCC converts aborts into errors.** Every `CRASH` row has `enable_mvcc=False`.
- **A shared new parent directory makes it worse.** Without MVCC it crashes at
  `limit=4`, not just at 8.

### 3.1 The process abort

Forty files at `concurrency_limit=8` on default settings:

```
thread '<unnamed>' panicked at core/storage/wal.rs:3367:9:
end_write_tx called while write lock not held according to connection state
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace
timeout: the monitored command dumped core
```

The panic crosses the FFI boundary and aborts the interpreter. `try`/`except`
cannot contain it. A caller loses the whole process, not one write.

### 3.2 Silent corruption

An earlier pass recorded `UNIQUE constraint failed: fs_data.(ino, chunk_index)`
with items reported `ok=True` whose stored content was truncated. Two writers
interleaved chunk rows for one inode.

**Note on provenance:** the "wrong on read-back" column above counts both failed
writes and corrupt writes. It does not isolate the `ok=True`-but-wrong case. The
attempt to isolate it hit the §3.1 panic and died before finishing. Treat the
silent-corruption mode as confirmed but not separately quantified in this
session.

---

## 4. Root cause

`write_many` fans out with a semaphore (`files.py:393-406`; the default
`concurrency_limit=10` is declared at `files.py:377`):

```python
semaphore = asyncio.Semaphore(concurrency_limit)

async def _write_one(index, item):
    async with semaphore:
        await self.write(normalized_path, content, mode=mode, encoding=encoding)

gathered = await asyncio.gather(*(...))
```

Every `_write_one` call uses the **same workspace and the same connection**.

The SDK's write path takes a `RETURNING`-cursor route that holds a cursor open
across an `await`. Two coroutines interleaved on one connection leave a
statement in progress when the next one starts a write. The driver reports
`cannot start a write statement - SQL statements in progress`. When the
interleaving corrupts the connection's own lock bookkeeping, the WAL layer
panics instead.

fsdantic's `README.md` already states the constraint:

> Each connection serializes its own operations via a dedicated worker thread,
> so no application-level locking is needed for **sequential** async access on a
> single workspace.

`write_many` breaks that rule from inside the library. The API's contract and
the connection model beneath it disagree.

### 4.1 Scope — what else is affected

Verified during the platform analysis (`002-fsdantic-as-agentfs-platform`):

| API | Status at `concurrency_limit=8` |
|---|---|
| `files.write_many` | **broken** — this issue |
| `files.read_many` | passes |
| `kv.get_many` / `set_many` / `delete_many` | passes |
| `repository.save_many` | passes |
| `KVTransaction` | passes |
| `repository.load_many` | no `concurrency_limit` parameter |
| `materialization.py` | uses no concurrency |

Only `files.write_many` takes the `RETURNING`-cursor path. Reads do not hold a
write lock, so the read batches are safe.

---

## 5. Why the pyturso 0.7.2 upgrade did not fix this

The fork at `v0.6.4-pyturso-0.7.2` exists to carry pyturso 0.7.2.
`docs/dependencies.md` gives two reasons, both real and both delivered:

1. A GIL-releasing busy-wait. On 0.4.4 a contended async write froze the event
   loop for the whole `busy_timeout_ms`.
2. MVCC journaling. `PRAGMA journal_mode = "mvcc"` does not exist on 0.4.4.

Neither addresses this defect.

**MVCC gives concurrent connections, not concurrent statements on one
connection.** This bug needs the second. `write_many` shares one connection
across every coroutine it spawns, so no journal mode helps.

MVCC does give one measurable benefit: it turns the process abort into a
catchable per-item error. That is worth keeping, but it converts an abort into
a failure — it does not produce a correct write.

**Conclusion: no driver version fixes an API that contradicts its own connection
model.** The fix must be in fsdantic.

---

## 6. Related finding — the turso version split

The Rust CLI links a different engine version than the Python stack.

| Side | Engine |
|---|---|
| Python (`pyturso`) | **0.7.2** |
| Rust CLI (`cli/Cargo.lock`: `turso`, `turso_core`) | **0.4.4** |

The fork commit changed only `sdk/python/pyproject.toml`. The Rust crates kept
their upstream pins.

This is not a bug on its own. The mount runs as a separate process, so the GIL
fix is irrelevant there. Record it as a decision to make: the two halves of the
stack now run different engines, and any behavior attributed to "the newer
turso" applies to the Python side only.

---

## 7. Related finding — AgentFS overlay diff marks reads as modified

This is an **upstream AgentFS defect**, not fsdantic's, but it blocks the same
use case. Recorded here so the two are not confused.

Minimal reproduction with the Rust CLI (`agentfs` v0.6.4, built from source):

```
base directory: 6 files
$ agentfs init --base /tmp/afs-base clean
$ agentfs mount clean /tmp/afs-clean/mnt
$ cat /tmp/afs-clean/mnt/f1.txt      # a pure read, nothing else
content1
$ fusermount3 -u /tmp/afs-clean/mnt
$ agentfs diff clean
M f /f1.txt
delta db size: 4096 bytes
```

One `cat` marks the file modified. The delta is a single page, so content is not
copied up — the diff treats inode presence in the delta as a modification.

At repository scale the diff becomes useless: on a 1083-file checkout it
reported **1824 entries for 3 real changes**, marking 466 files modified when
exactly one had been written. `agentfs diff` has no flag to change this.

**Workaround:** `git status` inside the mount reports only the true changes. Git
compares content, so it is immune. Any promote-what-changed workflow should use
git rather than `agentfs diff`.

---

## 8. Related finding — fsdantic overlays are not AgentFS overlays

fsdantic never writes `fs_whiteout`, `fs_origin` or `fs_overlay_config`.
Verified: zero references in `src/`, while the Rust SDK implements them in
`sdk/rust/src/filesystem/overlayfs.rs`.

fsdantic implements copy-on-write **above** two independent flat databases,
using KV tombstones under the reserved `fsdantic:tombstone:` prefix.

Consequences:

- An fsdantic overlay cannot be mounted.
- `agentfs diff` cannot read an fsdantic overlay.
- `overlay.merge` and `materialize.to_disk(base=...)` are library conventions,
  not the AgentFS overlay format.

This is the largest architectural gap between fsdantic and "a one stop shop for
AgentFS". It is analysed in `002-fsdantic-as-agentfs-platform/01-gap-analysis.md`.

---

## 9. Impact

| Consumer | Effect |
|---|---|
| Any caller using the default `concurrency_limit=10` | data loss or a process abort |
| Callers who check `result.items` | still exposed — `ok=True` can be wrong |
| Bulk ingest of a directory tree | the obvious API for it is the broken one |
| Long-running services | an uncatchable abort takes the whole process |

The default value is the dangerous one. A caller who never passes
`concurrency_limit` gets 10.

---

## 10. Recommended fixes

### 10.1 Immediate — stop the harm

Serialize inside `write_many`. Keep the signature, ignore or clamp the
parameter, and document it:

```python
# The connection serializes through one worker thread. Concurrent writes
# interleave statements on it, which corrupts chunk rows and can panic the
# WAL layer (core/storage/wal.rs:3367). Fan-out is not safe here.
for index, item in enumerate(items):
    results.append(await _write_one(index, item))
```

Cost: `limit=1` measured 373 ms for 50 files. That is the current safe path
anyway. Correct and slow beats fast and corrupt.

Add a regression test that writes 50 files at the default limit and asserts
every item is `ok` and reads back byte-identical.

### 10.2 Short term — make it fast the right way

Do not restore fan-out. Batch instead. A direct-SQL bulk ingest — three
`executemany` calls and one commit — was prototyped in
`002-fsdantic-as-agentfs-platform/03-performance.md` §6 and **independently
re-measured for this issue**:

| Direction | Current API | Bulk SQL | Gain |
|---|---|---|---|
| ingest 447 files | 5604.5 ms | **54.6 ms** | 103x |
| export 447 files | 5545.5 ms | **50.9 ms** | 109x |
| full round trip | 11.1 s | **105 ms** | 106x |

One connection, one transaction, no interleaving. Correct *and* fast.

Both directions verified for correctness, not only speed:

- Ingest reads back through the unmodified public API with 0 mismatches on 60
  random files; `stat` size and `list_dir` counts both agree.
- Export produces a tree byte-identical to `materialize.to_disk`: 447 files
  each, 0 missing, 0 extra, 0 content differences.

For scale: the bulk path lands at **1.6x ext4** and is **8.8x faster than the
AgentFS FUSE mount** (478 ms). Once the caller already holds the bytes, moving
them through a filesystem interface is pure overhead.

**Two caveats on the 54.6 ms figure.** It ingests into an empty namespace. A
production version must handle existing paths (upsert, or delete-then-insert
per inode), which this does not measure. And it writes `fs_inode`, `fs_dentry`
and `fs_data` directly, so fsdantic takes on responsibility for AgentFS schema
compatibility and breaks if upstream changes the schema.

### 10.3 Upstream — file two bugs

Against `tursodatabase/agentfs`:

1. **The WAL panic.** `end_write_tx called while write lock not held according
   to connection state`, `core/storage/wal.rs:3367`. A library must not abort
   its host process. Return an error.
2. **Overlay diff marks reads as modified.** §7 has a six-file reproduction.

Both are reproducible without fsdantic in the loop.

### 10.4 Documentation

`README.md` §2.5 currently presents `write_many` as a working bounded-fan-out
API:

> ```python
> write_result = await workspace.files.write_many(
>     [("/out-1.txt", "one"), ("/out-2.txt", "two")],
>     concurrency_limit=5,
> )
> ```

That example corrupts data at scale. Correct it in the same change as the fix.

---

## 11. Reproduction scripts

Written during this session. Recreate from the code in §3 and §4 if they are
gone.

| Script | Purpose |
|---|---|
| `/tmp/mvcc_test.py` | the §3 matrix — mvcc x limit x path shape, subprocess-isolated |
| `/tmp/_silent.py` | isolates `ok=True`-but-wrong; triggers the §3.1 panic |
| `/tmp/fsdantic_devman_probe.py` | 447-file round-trip and correctness check |
| `/tmp/fsdantic_perf_probe.py` | ext4 vs fsdantic ingest baseline |
| `/tmp/fuse_bench.py` | FUSE mount vs ext4 |
| `/tmp/inotify_test.py` | inotify behavior over the mount |

---

## 12. Appendix — supporting measurements

Context for the fixes above. Full analysis in
`002-fsdantic-as-agentfs-platform/`.

### Ingest, 447 files / 2.75 MB

| Path | Time | vs ext4 |
|---|---|---|
| plain write to ext4 | 34.1 ms | 1.0x |
| **direct-SQL bulk (re-measured)** | **54.6 ms** | **1.6x** |
| AgentFS FUSE mount (untar) | 478.2 ms | 14x |
| fsdantic sequential | 5604.5 ms | 164x |

fsdantic is 103x slower than the same database can be driven, and 11.7x slower
than the Rust mount. The cost is not inherent to AgentFS or libSQL.

The platform analysis reported 63.5 ms for the bulk path. An independent
re-run for this issue measured **54.6 ms**, with identical row counts
(inodes=640, chunks=969). Use 54.6 ms; the difference is machine noise, and
both support the same conclusion.

### Where the per-file cost goes

One new-file `write_file` issues 15 SQL statements and **5 commits**. Commit
cost measured directly on this stack:

| Operation | Cost |
|---|---|
| empty commit | 0.038 ms |
| non-empty commit | 1.724 ms |
| `files.write`, one 500-byte file | 7.26 ms |

7.26 / 1.724 = **4.2 commits per write**. Commits are effectively the entire
bill; everything else is rounding. `PRAGMA synchronous=OFF` changed nothing, so
it is engine bookkeeping, not fsync.

**Projection, not measured:** removing four of the five commits should take a
single `files.write` from 7.3 ms to roughly 2 ms — about 4x. This needs a change
to the SDK write path, so it was not tested here. It matters because §10.2's
bulk API does not help a caller writing one file at a time.

### Workspace open and import

| Operation | Cost |
|---|---|
| create a new database | 60.3 ms |
| open an existing database | 7.6 - 11.8 ms |
| `import fsdantic` | 323.6 ms |
| interpreter + import | ~500 ms |

The import dominates. Of it, ~205 ms is `models.py` building pydantic models.
Any latency-sensitive caller needs a resident process, not a faster import.

**This outranks the data-layer work for short-lived callers.** Once bulk ingest
is 54.6 ms, a one-shot process spends 85% of its life importing. Optimizing the
data layer alone would fix the wrong end. See `DAEMON.md` in this directory for
the resident-process design that removes it.

### AgentFS FUSE mount vs ext4

| Workload | ext4 | FUSE | ratio |
|---|---|---|---|
| untar 447 files | 44.4 ms | 478.2 ms | 10.8x |
| cat whole tree | 13.0 ms | 76.6 ms | 5.9x |
| `find -type f` | 6.6 ms | 40.2 ms | 6.1x |
| `grep -r` | 11.9 ms | 77.6 ms | 6.5x |
| create 500 small files | 32.4 ms | 373.7 ms | 11.5x |
| find over 1083-file repo | 36.0 ms | 1736.9 ms | 48.2x |

The mount is sound: 1083 files, listings identical to the source tree,
byte-identical content hash, symlinks preserved, copy-on-write isolation exact.
`git`, `python` and `ruff` all run correctly inside it. inotify fires for writes
made through the mount, and stays silent for out-of-band writes to the base
directory — which remain visible on read.

The build recipe is in
`002-fsdantic-as-agentfs-platform/07-agentfs-cli-build.md`.
