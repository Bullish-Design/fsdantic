# 01 — Gap analysis

## 1. What "one stop shop for AgentFS" must mean

The phrase has four possible readings. They cost different amounts. Choose one
before reading the rest of this project.

| Reading | Claim to a user | Hardest requirement |
|---|---|---|
| **A. Typed data API** | "Talk to an AgentFS database from Python without writing SQL." | Nothing new. fsdantic 0.7.0 already does this. |
| **B. Fast data plane** | "Move a repository into and out of AgentFS at filesystem speed." | Bulk ingest and bulk export within 5x of ext4. |
| **C. Full control plane** | "Do everything `agentfs` the CLI does, from Python." | Mount, serve, exec, run, sync, encryption, migrate. |
| **D. A filesystem** | "Point `gcc`, `git` and `pytest` at an AgentFS workspace." | A FUSE or NFS server. Not achievable in pure Python. |

fsdantic today is **A**. The devman investigation asked for **B** and **D** and
got neither. The rest of this document measures the distance.

## 2. The architectural fact that frames everything

**fsdantic does not use the AgentFS overlay model.** It reimplements
copy-on-write above two independent, flat AgentFS databases.

Evidence:

- `grep -rn "fs_whiteout\|fs_origin\|fs_overlay_config\|base_path" src/` in
  `/home/andrew/Documents/Projects/fsdantic` returns **zero hits**.
- AgentFS's real COW tables are specified in
  `/home/andrew/Documents/Projects/agentfs/SPEC.md` §"Overlay Filesystem"
  (line 487): `fs_whiteout` (line 495), `fs_origin` (line 570), plus the
  undocumented `fs_overlay_config` written by
  `agentfs/sdk/rust/src/filesystem/overlayfs.rs:105-115`.
- The Python SDK implements none of them. Its schema DDL is
  `agentfs/sdk/python/agentfs_sdk/filesystem.py:109-153` and creates exactly
  `fs_config`, `fs_inode`, `fs_dentry`, `fs_data`, `fs_symlink`.
- fsdantic's substitute for a whiteout is a KV row under the reserved prefix
  `fsdantic:tombstone:` (`src/fsdantic/overlay.py:22`).
- fsdantic's substitute for a copy-up is a full content copy: read the source
  file, read the target file, compare, write the target
  (`src/fsdantic/overlay.py:277-345`).

Three consequences follow, and they are the real subject of this project.

1. **fsdantic databases are not portable to the Rust tooling.** `agentfs
   mount`, `agentfs diff` and `agentfs run --session` all read
   `fs_overlay_config.base_path` (`agentfs/cli/src/cmd/mount.rs:151`,
   `cmd/exec.rs:45`). An fsdantic overlay has no such row. The base layer is a
   second `.db` file that only fsdantic knows about. A user who adopts
   fsdantic's overlay cannot later mount it.
2. **Every overlay operation is O(content), not O(delta).** A merge reads both
   full files. A diff walks both full trees. The AgentFS design avoids this
   with whiteouts and origin tracking; fsdantic pays the cost the design
   exists to remove.
3. **The tombstone lives in the KV store, not the filesystem.** It survives
   `list_changes()` and materialization by being invisible to them
   (`src/fsdantic/overlay.py:19-27`). This works. It is also a private
   convention that no other AgentFS tool will honor.

This is not a bug. It is the only overlay that the Python SDK makes possible.
It is, however, the single largest divergence between "typed wrapper over the
Python SDK" and "one stop shop for AgentFS".

## 3. Capability matrix

Legend: **Y** full, **P** partial, **N** absent, **—** not applicable.

Rust column = the `agentfs` CLI, version 0.6.4. Py-SDK column =
`agentfs_sdk` 0.6.4. fsdantic column = 0.7.0.

### 3.1 Data plane

| Capability | Rust CLI | Py SDK | fsdantic | Notes |
|---|---|---|---|---|
| Read file | Y `cmd/fs.rs:105` | Y `filesystem.py:445` | Y `files.py:189` | fsdantic adds text/binary/json modes |
| Write file | Y `cmd/fs.rs:129` | Y `filesystem.py:357` | Y `files.py:283` | |
| Partial read / write at offset | Y (FUSE `pread`/`pwrite`) | N | N | `read_stream` reads whole chunks only |
| Append | Y (FUSE) | N | N | |
| Truncate | Y (FUSE) | N | N | |
| readdir | Y `cmd/fs.rs:15` | Y `filesystem.py:489` | Y `files.py:522` | fsdantic adds base-layer fallback |
| Recursive walk / tree | N (CLI) | N | Y `files.py:731` | Python-side walk, O(tree) |
| Glob / query | N | N | Y `files.py:605,633`, `view.py` | fsdantic-only value-add |
| Content search | N | N | Y `view.py:159` | fsdantic-only value-add |
| stat | Y | Y `filesystem.py:578` | Y `files.py:503` | |
| mkdir / rmdir / rm | Y (FUSE) | Y `filesystem.py:624,667,720` | P `files.py:575` | fsdantic exposes `remove` only |
| rename | Y (FUSE) | Y `filesystem.py:827` | N | Not exposed by fsdantic |
| copy_file | Y | Y `filesystem.py:977` | N | Not exposed |
| symlink / readlink | Y | N (table unused) | N | `fs_symlink` created, never written |
| hardlink | Y | N | N | |
| chmod / chown / utimens | Y | N | N | |
| statfs | Y | N | N | |
| KV get/set/list/delete | Y (MCP) | Y `kvstore.py` | Y `kv.py` | fsdantic adds typing, CAS, namespaces |
| Typed repository / versioned records | — | N | Y `repository.py` | fsdantic-only value-add |
| Tool calls / timeline data | Y `cmd/timeline.rs` | Y `toolcalls.py` | P `models.py` | Models exported, no manager |
| Bulk / batch write in one transaction | — | N | N | **The performance gap.** See `03-performance.md` |

### 3.2 Overlay and change tracking

| Capability | Rust CLI | Py SDK | fsdantic | Notes |
|---|---|---|---|---|
| True COW overlay over a base | Y `overlayfs.rs` | N | P | fsdantic simulates with 2 DBs |
| Whiteouts (`fs_whiteout`) | Y SPEC:495 | N | P | KV tombstones instead |
| Inode origin (`fs_origin`) | Y SPEC:570 | N | N | `stat` cannot report the base inode |
| Host directory as read-only base | Y `hostfs_linux.rs` | N | N | fsdantic base must be another DB |
| `diff` base vs delta | Y `cmd/fs.rs:199` | N | P `materialization.py:319` | O(tree), see `02-defects.md` §4 |
| Merge / reset / promote | N | N | Y `overlay.py` | fsdantic-only value-add |
| Materialize to disk | N (mount instead) | N | Y `materialization.py` | fsdantic-only value-add, guarded swap |

### 3.3 Filesystem exposure — the hard gap

| Capability | Rust CLI | Py SDK | fsdantic |
|---|---|---|---|
| FUSE mount | Y `cli/src/fuse.rs`, `cli/src/fuser/` (vendored fuser) | N | N |
| NFSv3 server | Y `cli/src/nfs.rs`, `cli/src/nfsserve/` | N | N |
| `mount` / `--foreground` / daemonize | Y `cmd/mount.rs`, `daemon.rs` | N | N |
| `--allow-root`, `--system` (allow_other), `--uid`/`--gid` | Y `opts.rs:216-254` | N | N |
| `exec` — mount, run a command, unmount | Y `cmd/exec.rs:21` | N | N |
| `run` — sandbox with COW over cwd | Y `cmd/run_linux.rs`, `sandbox/linux.rs` | N | N |
| Linux user + mount namespaces | Y `sandbox/linux.rs:587,612` | N | N |
| macOS `sandbox-exec` profiles | Y `sandbox/darwin.rs` | N | N |
| ptrace sandbox (`--experimental-sandbox`) | Y `sandbox/linux_ptrace.rs` | N | N |
| Sessions (`--session`, join a live mount) | Y `sandbox/linux.rs:159-176,317` | N | N |
| `ps` — list sessions and processes | Y `cmd/ps.rs` | N | N |
| `prune mounts` | Y `cmd/mount.rs:550` | N | N |
| Enumerate mounts from `/proc/mounts` | Y `sdk/rust/src/lib.rs:47-71` | N | N |
| MCP server (`serve mcp`) | Y `cmd/mcp_server.rs`, 12 tools | N | N |

**This block is the answer to "why is fsdantic not a filesystem".** An external
binary calls `open()` and `stat()`. Those syscalls reach a kernel VFS. Only a
FUSE or NFS server can put an AgentFS database behind that VFS. Both exist only
in the Rust `cli/` crate. No amount of Python API design closes this.

#### 3.3.1 The mount was built and measured

The CLI was built from source and the FUSE path exercised against the devman
repository. Build recipe: [`07-agentfs-cli-build.md`](07-agentfs-cli-build.md).

`agentfs init --base <DIR>` plus `agentfs mount` produced a working
copy-on-write filesystem over a real directory. Verified:

| Check | Result |
|---|---|
| File count through the mount | 1083, identical to the real tree |
| Content hash of all tracked files | Byte-identical |
| Symlinks | Preserved |
| Writes through the mount | Land in the delta layer |
| Base directory after writes | Untouched (`git status` clean) |

Performance through the mount, same 447-file corpus:

| Operation | ext4 | FUSE | Ratio |
|---|---|---|---|
| untar 447 files | 44.4 ms | 478.2 ms | 10.8x |
| `cat` whole tree | 13.0 ms | 76.6 ms | 5.9x |
| `find -type f` | 6.6 ms | 40.2 ms | 6.1x |
| `grep -r` | 11.9 ms | 77.6 ms | 6.5x |
| `stat` each file | 655.0 ms | 761.5 ms | 1.2x |
| create 500 small files | 32.4 ms | 373.7 ms | 11.5x |
| `find` over 1083-file repo | 36.0 ms | 1736.9 ms | 48.2x |

Two conclusions.

1. **The mount is usable.** A 6-12x penalty on bulk I/O is the normal FUSE
   tax. External binaries work. This is a real filesystem.
2. **The mount writes 447 files in 478 ms; fsdantic takes 5604 ms.** The Rust
   path is 11.7x faster *while also* serving the kernel VFS. AgentFS storage is
   not the bottleneck. See [`03-performance.md`](03-performance.md).

The `find` result over 1083 files (48x) is the worst case and is worth noting:
directory traversal through the overlay is the mount's weak spot, because each
`lookup` must consult the delta, then the whiteout table, then the base
(`agentfs/SPEC.md:559-562`).

#### 3.3.2 inotify is coherent-on-read, silent on notification

Change notification through the mount is asymmetric.

| Change made | Visible through the mount | inotify event fires |
|---|---|---|
| Write, modify or delete **through the mount** | Yes | Yes — `CREATE`, `MODIFY`, `CLOSE_WRITE`, `DELETE` |
| Write directly to the **base directory** | Yes | **No** |

Any fsdantic feature that promises "tell me when the workspace changes" must
state this limit. A watcher on the mount sees mount-side writes only. It misses
every out-of-band edit to the base, and it misses every write fsdantic itself
makes to the database.

#### 3.3.3 A mounted database is exclusively locked

This is the hardest constraint in the whole investigation.

```
$ agentfs diff <agent>          # while the same agent is mounted
Error: database error: Locking error: Failed locking file.
File is locked by another process
```

The mount holds an exclusive file lock for its whole lifetime. While it holds
it:

- `agentfs diff`, `agentfs fs ls` and `agentfs timeline` all fail.
- **fsdantic cannot open the database at all.** `turso.aio.connect` takes a
  file-level lock at connect time (documented in `docs/concurrency.md`,
  "Multi-process caveat").

So "mount the workspace and also drive it from Python" is not a design that
exists today. Every option in [`04-options.md`](04-options.md) that combines
fsdantic with a mount must answer this first. Two answers are available:

- **Ownership handoff** — exactly one process holds the database at a time.
  fsdantic prepares, closes, mounts, waits, unmounts, reopens.
- **Go through a server** — `agentfs serve nfs` or `agentfs serve mcp` owns the
  database, and fsdantic becomes a *client* of that server rather than a second
  opener. This is likely the intended multi-consumer path and deserves an
  explicit decision. See [`06-open-questions.md`](06-open-questions.md) Q5.

### 3.4 Lifecycle, storage and operations

| Capability | Rust CLI | Py SDK | fsdantic |
|---|---|---|---|
| Create DB by ID (`.agentfs/<id>.db`) | Y `cmd/init.rs:72` | Y `agentfs.py:81-88` | Y `client.py:64-76` |
| Create as overlay over a host dir (`--base`) | Y `opts.rs:80` | N | N |
| Encryption `--key` / `--cipher` (8 AEGIS/AES ciphers) | Y `main.rs:11-25` | N | N |
| Remote sync `pull`/`push`/`stats`/`checkpoint` | Y `cmd/sync.rs` | N | N |
| Partial sync bootstrap strategies | Y `cmd/init.rs:30-70` | N | N |
| Schema version detection | Y `sdk/rust/src/schema.rs:54-89` | N | N |
| `migrate` 0.0 → 0.2 → 0.4 | Y `cmd/migrate.rs` | N | N |
| WAL / MVCC journal control | N | N | Y `client.py:79-101` |
| Read-only workspaces | N | N | Y `_internal/readonly.py` |
| Busy timeout | N | N | Y `client.py:241-243` |
| Shell completions | Y `cmd/completions.rs` | — | — |

### 3.5 Score

| Group | AgentFS capabilities | fsdantic covers |
|---|---|---|
| Data plane | 19 | 11 full, 2 partial |
| Overlay | 7 | 3 full, 2 partial |
| Filesystem exposure | 14 | **0** |
| Lifecycle / ops | 9 | 1 full |
| **fsdantic-only value-add** | — | glob, query, content search, typed repository, versioned CAS, merge, tombstones, materialize, read-only, WAL/MVCC control |

fsdantic covers roughly **30 %** of what AgentFS can do. It adds ten things
AgentFS has no equivalent for. The two lists barely overlap: fsdantic is not a
subset of the CLI, it is a different product built on a subset of the storage.

## 4. What a "one stop shop" would have to add

Ordered by how hard each is.

| Missing capability | Difficulty | Why |
|---|---|---|
| Bulk ingest / export | **Low** | Pure SQL. Prototyped at 88x. See `04-options.md` option A. |
| O(changes) diff | Low | One recursive CTE against `fs_dentry`. |
| `rename`, `copy_file`, `exists` passthroughs | Low | The Py SDK already has them. |
| Fast import (lazy pydantic models) | Low | 205 ms of the 448 ms import is `models.py`. |
| Real `fs_whiteout` / `fs_origin` overlay | Medium | Write the tables the SPEC defines. Makes DBs CLI-compatible. |
| Encryption, sync, migrate | Medium | Turso features the Python driver may not expose. Verify first. |
| `symlink`, `chmod`, offset I/O | Medium | New SQL against `fs_symlink` and `fs_data`. |
| MCP server | Medium | Pure Python is viable. 12 tools, all data-plane. |
| Sessions / `ps` | Medium-High | Needs a mount to join. Meaningless without one. |
| Sandboxed `exec` / `run` | High | Namespaces, `sandbox-exec`, ptrace. Not Python work. |
| **FUSE / NFS mount** | **High** | Either bind the Rust crate, shell out to the binary, or write a Python FUSE server. |

The line between Low and High is the line between reading option A and reading
options B and C in `04-options.md`.
