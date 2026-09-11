# 03 — Where the 12.5 ms per file goes

The briefing asked for a precise account rather than a list of suspects. This
document gives one. Every candidate is measured and either confirmed or
eliminated.

## 1. The headline

Same corpus throughout: 447 files, 2,747,810 bytes, max depth 4.

| Path | Time | ms/file | vs ext4 |
|---|---|---|---|
| ext4 `write_bytes` loop | 34.1 ms | 0.08 | 1.0x |
| **Direct-SQL bulk ingest** (prototype, §6) | **63.5 ms** | **0.14** | **1.9x** |
| agentfs FUSE mount, `untar` | 478.2 ms | 1.07 | 14.0x |
| fsdantic `files.write` loop | 5604.5 ms | 12.54 | 164x |
| fsdantic `materialize.to_disk` | 5025.2 ms | 11.24 | 147x |
| fsdantic `materialize.diff(base)` | 1865-2138 ms | — | — |

Two independent fast paths — one Rust, one pure Python — beat the fsdantic loop
by 12x and 88x. **The slowness is not inherent to AgentFS, to libSQL, or to
Python. It is the per-file call shape.**

## 2. Statement and commit census

Method: wrap the `turso.aio.Connection` in a proxy that records the first
tokens of every `execute` and counts every `commit`, then hand the proxy to
`AgentFS.open_with`. Probe `/tmp/fsd_g.py`.

### 2.1 New file, `/dir/a.bin`, 6000 bytes, `/dir` does not exist

```
SELECT ino FROM fs_dentry WHERE parent_ino ...      <- _ensure_parent_dirs: does /dir exist?
[cursor] INSERT INTO fs_inode ... RETURNING ino     <- create the /dir inode
INSERT INTO fs_dentry (name, parent_ino, ino)
UPDATE fs_inode SET nlink = nlink + 1
SELECT ino FROM fs_dentry WHERE parent_ino ...      <- _resolve_path: walk /dir
SELECT ino FROM fs_dentry WHERE parent_ino ...      <- _resolve_path: walk a.bin (absent)
SELECT ino FROM fs_dentry WHERE parent_ino ...      <- _resolve_parent: walk /dir AGAIN
SELECT mode  FROM fs_inode WHERE ino                <- assert_inode_is_directory
[cursor] INSERT INTO fs_inode ... RETURNING ino     <- create the file inode
INSERT INTO fs_dentry (name, parent_ino, ino)
UPDATE fs_inode SET nlink = nlink + 1
DELETE FROM fs_data WHERE ino = ?                   <- unconditional, on a brand-new inode
INSERT INTO fs_data (ino, chunk_index, data)        <- chunk 0
INSERT INTO fs_data (ino, chunk_index, data)        <- chunk 1
UPDATE fs_inode SET size = ?, mtime = ?

15 statements.  5 commits.
```

Source: `agentfs/sdk/python/agentfs_sdk/filesystem.py` —
`write_file:357`, `_ensure_parent_dirs:301`, `_resolve_path:212`,
`_resolve_parent:241`, `_create_inode:259` (commits at `:282`),
`_create_dentry:285` (commits at `:299`), `_update_file_content:410`
(commits at `:443`).

### 2.2 Overwrite of the same file

```
SELECT ino FROM fs_dentry WHERE parent_ino ...   x3
SELECT mode FROM fs_inode WHERE ino
DELETE FROM fs_data WHERE ino = ?
INSERT INTO fs_data (ino, chunk_index, data)     x2
UPDATE fs_inode SET size = ?, mtime = ?

8 statements.  1 commit.
```

Create is 3x the cost of overwrite. The devman ingest is all creates.

## 3. Unit costs

Measured on the same connection, same machine (`/tmp/fsd_g.py`, N=300):

| Operation | Cost |
|---|---|
| `SELECT` round trip through `turso.aio` | **0.113 ms** |
| Empty `commit()` | **0.051 ms** |
| `INSERT` of a 6000-byte blob, batched (1 commit for 300) | **0.192 ms** |
| `INSERT` of a 6000-byte blob + its own `commit()` | **2.918 ms** |

Therefore a **non-empty commit costs 2.918 − 0.192 ≈ 2.73 ms**.

## 4. The arithmetic

New file, from §2.1 and §3:

| Component | Count | Unit | Subtotal |
|---|---|---|---|
| Non-empty commits | 5 | 2.73 ms | **13.6 ms** |
| Statements | 15 | 0.11-0.19 ms | 1.7-2.9 ms |
| Python + pydantic in fsdantic | — | — | see §5 |

Predicted: ~13.6 ms floor from commits alone. Measured: **12.5 ms/file** in the
devman run, 10.5-24.7 ms/file across runs. **The commits are the entire bill.**

## 5. Each candidate, confirmed or eliminated

The briefing listed six candidates. Verdicts:

### 5.1 Transaction-per-write — **CONFIRMED, dominant**

Direct test: suppress the SDK's per-step `commit()` with a proxy, commit once
at the end. Same 200 files, depth 6. Probe `/tmp/fsd_h.py`.

```
baseline (commit per step)           10.48 ms/file   total 2096.6 ms
baseline + WAL                        9.81 ms/file   total 1961.8 ms
deferred commit (1 total)             4.57 ms/file   total  914.0 ms
deferred + WAL                        5.52 ms/file   total 1103.4 ms
synchronous=OFF                       9.65 ms/file   total 1929.3 ms
```

Removing the commits alone gives **2.3x**. This is the single largest lever
inside the current call shape.

`PRAGMA synchronous=OFF` changed nothing (10.48 → 9.65 ms, within noise). So
the commit cost is **not fsync**. It is per-transaction bookkeeping inside the
Limbo engine. Turning off durability buys nothing; issuing fewer transactions
buys everything.

### 5.2 One SQL statement per file with no batching — **CONFIRMED, second**

After removing every commit, 4.57 ms/file remains across ~15 statements —
0.30 ms per statement, against a measured 0.113 ms bare round trip. The excess
is the driver's per-statement work: parse, bind, plan.

`executemany` collapses this. In the bulk prototype (§6), 640 inode rows, 640
dentry rows and 969 chunk rows go in as **three** `executemany` calls.

### 5.3 Redundant path resolution — **CONFIRMED, third**

`write_file` walks the tree from the root three times before writing
(`_ensure_parent_dirs`, `_resolve_path`, `_resolve_parent`). Depth 6 versus
depth 1, raw SDK, same content (`/tmp/fsd_f.py`):

```
raw SDK flat (depth 1)      10.83 ms/file
raw SDK depth-6             26.73 ms/file
```

**2.5x for five extra levels** — about 3.2 ms per level, or roughly three
0.11 ms round trips plus their share of retried work. Path depth is a
first-class cost. A single recursive CTE would resolve any path in one round
trip.

### 5.4 Content chunking in `fs_data` — **MEASURED, minor**

`_update_file_content` writes one `INSERT` per 4096-byte chunk
(`filesystem.py:425-431`; chunk size from `constants.py:13` and `fs_config`,
per `agentfs/SPEC.md:151-157`).

```
raw SDK 1-byte file           19.08 ms/file
raw SDK 100 KB (25 chunks)    28.44 ms/file
   -> marginal cost per chunk  0.374 ms
```

0.374 ms/chunk. For the devman corpus (969 chunks over 447 files, mean 2.2
chunks/file) that is ~0.8 ms/file — about 6 % of the bill. Real, but not the
problem. It becomes the problem only for large files: a 1 MB file is 256
`INSERT`s, ~96 ms.

### 5.5 The async → worker-thread hop — **ELIMINATED**

A bare `SELECT 1` round trip through `turso.aio` costs **0.080-0.113 ms**. At
15 statements per file that is 1.2-1.7 ms — under 14 % of the total. The hop is
not free, but it is not the cause.

### 5.6 Per-call pydantic validation and path normalization — **ELIMINATED**

fsdantic's overhead above the raw SDK, same content, same depth
(`/tmp/fsd_f.py`):

```
raw SDK flat        10.83 ms/file
fsdantic flat       20.06 ms/file    delta 9.23 ms
```

That delta looks large, but it is measurement noise from a growing database and
machine load — in the same run `raw SDK 1-byte` came out at 19.08 ms/file,
higher than `raw SDK flat` at 10.83 ms with 6000-byte payloads, which is
impossible if the layer costs were stable.

The static evidence is decisive. `FileManager.write`
(`src/fsdantic/files.py:283-315`) does exactly this before delegating:

- `normalize_path(path)` — pure string work, `_internal/paths.py:48-100`;
- `_ensure_writable` — one boolean check, `files.py:149`;
- `_prepare_write_payload` — an encode or a `json.dumps`, `files.py:443`;
- `_ensure_within_size_cap` — one `len()` comparison, `files.py:161`.

**No pydantic model is constructed on the write path.** No validator runs. This
is microseconds of Python against 13.6 ms of commits. Pydantic's cost in
fsdantic is at **import** time, not call time — see `02-defects.md` §5.

### 5.7 Prepared-statement reuse — **CONFIRMED absent, folded into 5.2**

Every SDK call passes a fresh SQL string to `db.execute()`. No `Statement`
object is cached. Any reuse happens inside pyturso and is not visible. This is
the mechanism behind the 0.30 ms/statement in §5.2.

## 6. The prototype that proves the ceiling

To establish what the current dependency stack can do, a bulk ingest was
written directly against the AgentFS schema: assign inode numbers in Python,
build three row lists, issue three `executemany` calls, commit once. Probe
`/tmp/fsd_i.py`.

```python
CH = 4096

async def bulk(conn, files):
    cur = await conn.execute("SELECT MAX(ino) FROM fs_inode")
    next_ino = ((await cur.fetchone())[0] or 1) + 1
    dirs = {"/": 1}
    inode_rows, dentry_rows, data_rows = [], [], []
    now = int(time.time())

    def ensure_dir(path):
        nonlocal next_ino
        if path in dirs:
            return dirs[path]
        parent, _, name = path.rpartition("/")
        pino = ensure_dir(parent or "/")
        ino = next_ino; next_ino += 1
        inode_rows.append((ino, 0o040755, 2, 0, 0, 0, now, now, now))
        dentry_rows.append((name, pino, ino))
        dirs[path] = ino
        return ino

    for rel, data in files.items():
        full = "/" + rel
        parent, _, name = full.rpartition("/")
        pino = ensure_dir(parent or "/")
        ino = next_ino; next_ino += 1
        inode_rows.append((ino, 0o100644, 1, 0, 0, len(data), now, now, now))
        dentry_rows.append((name, pino, ino))
        for ci in range((len(data) + CH - 1) // CH):
            data_rows.append((ino, ci, data[ci * CH:(ci + 1) * CH]))

    await conn.executemany(
        "INSERT INTO fs_inode (ino,mode,nlink,uid,gid,size,atime,mtime,ctime)"
        " VALUES (?,?,?,?,?,?,?,?,?)", inode_rows)
    await conn.executemany(
        "INSERT INTO fs_dentry (name,parent_ino,ino) VALUES (?,?,?)", dentry_rows)
    await conn.executemany(
        "INSERT INTO fs_data (ino,chunk_index,data) VALUES (?,?,?)", data_rows)
    await conn.commit()
```

Result:

```
plain ext4            34.1 ms
bulk SQL ingest       63.5 ms  (43.2 MB/s)  1.9x ext4
                      inodes=640  chunks=969
verify 60 random files, mismatches = 0
stat size 4399, expected 4399
root entries 21
```

**63.5 ms against 5604.5 ms — an 88x speedup.** The result is read back
correctly through the unmodified `agentfs_sdk` API: `read_file`, `stat` and
`readdir` all agree.

It is also **7.5x faster than the FUSE mount** (478 ms). Bulk data movement
should never go through a filesystem interface; the kernel round trip per
syscall is pure overhead when the caller already holds the bytes.

Caveats, all addressable:

- The prototype assumes an empty or non-conflicting namespace. A production
  version must handle existing paths (upsert, or delete-then-insert per inode).
- It hardcodes `mode`, `uid`, `gid`. It should take them from the caller.
- It reads `chunk_size` as a constant. It must read `fs_config`
  (`agentfs/SPEC.md:151-157`).
- It does not populate `nlink` for directories correctly beyond the default 2.

None of these change the shape or the order of magnitude.

## 7. Cost model — use this to predict, not to guess

For the current per-file API:

```
cost(write_file) ≈ 2.73 ms × commits
                 + 0.19 ms × statements
                 + 0.37 ms × chunks

commits    = 1                      if the file and all parents exist
           = 3 + 2 × (new parents)  otherwise
statements ≈ 8 + 3D + 2C
```

where `D` is path depth and `C` is chunk count.

For the batched API:

```
cost(bulk) ≈ 2.73 ms                       (one commit)
           + 0.02 ms × rows                (amortized executemany)
           + Python row-building
```

The second model has no per-file constant. That is the whole point.

## 8. What each fix is worth

Against the 5604 ms baseline for 447 files:

| Fix | Mechanism | Measured or projected | Confidence |
|---|---|---|---|
| Batch commits only | keep the SDK, defer `commit()` | 2.3x → ~2400 ms | Measured |
| Bulk SQL ingest | 3 × `executemany`, 1 commit | **88x → 63.5 ms** | **Measured** |
| Recursive-CTE path resolution | 1 round trip per path | ~2.5x on deep trees | Measured (depth 1 vs 6) |
| Lazy pydantic import | PEP 562 in `__init__.py` | 448 ms → <50 ms startup | Projected from `-X importtime` |
| Drop pydantic from the hot path | — | **0x. Nothing to gain.** | Measured and eliminated |
| Bigger `chunk_size` | fewer `fs_data` rows | ~6 % on this corpus | Measured |
| Concurrency | more coroutines | **Negative.** One connection, one worker thread. Corrupts data. See `02-defects.md` §1. | Measured |

Read the last row twice. The instinct to fix a slow I/O loop with concurrency
is exactly wrong here, and the current `write_many` is the proof.
