# 04 — Options

Five genuine alternatives. They are not mutually exclusive; A and B compose
well. Each entry states what it buys, what it costs, what it forecloses, and
how it changes the public API.

Read [`03-performance.md`](03-performance.md) §6 and
[`01-gap-analysis.md`](01-gap-analysis.md) §3.3.3 first. Those two results —
88x from bulk SQL, and the exclusive lock held by a mount — decide most of what
follows.

## Option A — Optimize inside the current Python SDK architecture

Keep `agentfs_sdk` and `pyturso`. Add a fast path that writes the AgentFS
schema directly, and fix the call shape of the slow path.

### What it contains

1. **Bulk ingest / bulk export.** New APIs: `files.ingest_tree(...)`,
   `files.export_tree(...)`, `files.write_batch(...)`. One transaction, three
   `executemany` calls. Prototyped in `03-performance.md` §6.
2. **Batched transactions across many files.** A `workspace.batch()` context
   manager that suppresses the SDK's per-step commits and commits once at exit.
3. **Single-round-trip path resolution.** Replace the three root-walks in
   `write_file` with one recursive CTE over `fs_dentry`.
4. **O(changes) diff.** Replace `_list_all_files`
   (`src/fsdantic/materialization.py:562`) with one recursive CTE per layer,
   compare `(size, mtime)` first, read content only on a size match.
5. **Lazy import.** PEP 562 `__getattr__` in `src/fsdantic/__init__.py` so
   `models.py` builds on first use, not on import.
6. **Fix `write_many`** by making it a batch, and deprecate `concurrency_limit`.

### What it buys

| Metric | Now | After | Evidence |
|---|---|---|---|
| Ingest 447 files | 5604 ms | **~64 ms** | Measured (`/tmp/fsd_i.py`) |
| vs ext4 | 164x | **1.9x** | Measured |
| vs the FUSE mount | 11.7x slower | **7.5x faster** | Measured |
| `diff` on 447 files | 1865 ms | tens of ms | Projected from round-trip counts |
| `import fsdantic` | 448 ms | <50 ms | Projected from `-X importtime` |
| `write_many` correctness | 98 % item loss / abort | correct | By construction |

It also fixes atomicity for the ingest case as a side effect: one transaction
means no half-written tree (`02-defects.md` §6).

### What it costs

- Real engineering: fsdantic starts owning SQL against the AgentFS schema. That
  is roughly 400-600 lines of new code plus tests.
- **A schema coupling.** fsdantic must track `agentfs/SPEC.md` version 0.4:
  `fs_inode`, `fs_dentry`, `fs_data`, `fs_config.chunk_size`. If upstream bumps
  the schema, fsdantic breaks. Today only `agentfs_sdk` carries that risk.
- A test burden: every bulk path needs a round-trip test that reads back
  through the unmodified SDK API, exactly as the prototype does.

Mitigate the coupling by reading `fs_config.schema_version` at open time and
refusing anything other than `0.4` with a typed error. The Rust SDK does this
at `agentfs/sdk/rust/src/schema.rs:94-104`. The Python SDK does not — it
overwrites the version unconditionally
(`agentfs/sdk/python/agentfs_sdk/filesystem.py:175-178`), which is its own
latent bug.

### What it forecloses

Nothing. It adds no dependency, no build step, no binary. It is compatible with
every other option here.

### API change

Purely additive, except `write_many`. `concurrency_limit` should be deprecated
across `files`, `kv` and `repository` — the connection model gives it no
meaning, and leaving it in invites the next caller to hit `02-defects.md` §1.

### Verdict

**Do this first.** It is the only option with a measured 88x behind it, and the
only one that costs nothing structurally.

## Option B — Drive the Rust CLI as a subprocess

Keep the Python API for data. Shell out to `agentfs` for mount, serve, exec,
run, sync, migrate and encryption.

### What the new data changes

Before the CLI was built and measured, this option was speculative. It is now
grounded, and the grounding cuts both ways.

**In favor:** the mount works, it is correct, and it is fast. 1083 files
listed identically to the real tree, byte-identical content, symlinks
preserved, base directory untouched. `untar` of 447 files in 478 ms. inotify
fires on mount-side writes. This is a production-quality filesystem that
fsdantic can offer for the cost of a `subprocess` call.

**Against:** the exclusive lock. `01-gap-analysis.md` §3.3.3.

```
$ agentfs diff <agent>          # while mounted
Error: database error: Locking error: Failed locking file.
File is locked by another process
```

**fsdantic cannot open a database that a mount holds.** So the obvious design —
"`workspace.mount(path)` returns a handle and you keep using `workspace`" — is
impossible. This is the central design problem of the option, not a footnote.

### The three possible shapes

| Shape | Design | Verdict |
|---|---|---|
| **B1. Ownership handoff** | `async with workspace.mounted(path) as mnt:` closes the fsdantic connection, spawns `agentfs mount`, yields, unmounts, reopens. Inside the block the Python API is unavailable. | Honest and implementable. Awkward API: a workspace that closes itself. |
| **B2. Client of `serve`** | fsdantic never opens the database directly when a server owns it. It speaks to `agentfs serve mcp` over stdio, or mounts `agentfs serve nfs` and uses ordinary file I/O. | Cleanest multi-consumer story. Highest cost: a second transport, and the MCP tool set is only 12 data-plane calls (`cmd/mcp_server.rs:277-490`) — no glob, no query, no typed repository. |
| **B3. Task-scoped only** | fsdantic exposes `workspace.exec(cmd)` and `workspace.run(cmd)` as one-shot operations that mount, run and unmount. No long-lived mount, no concurrent Python access. | Smallest, safest, and covers the actual use case: "run a compiler over this workspace". Recommended. |

### What it buys

Every row of `01-gap-analysis.md` §3.3 and §3.4 that fsdantic currently scores
N: mount, NFS, exec, run, sessions, `ps`, prune, MCP, sync, encryption,
migrate. That is the largest capability jump available, by a wide margin.

### What it costs

- **A binary the user must install.** The CLI is not on PyPI. Building it is
  non-trivial: see [`07-agentfs-cli-build.md`](07-agentfs-cli-build.md). On
  this machine it needed stable cargo 1.95 instead of the nightly the toolchain
  file requests, `--no-default-features` to drop `reverie`, plus `gcc`,
  `pkg-config`, `fuse3`, `OPENSSL_DIR`, `OPENSSL_LIB_DIR` and a `LIBRARY_PATH`
  pointing at `xz` for `-llzma`. A user will not do this by accident.
- `--no-default-features` **disables `agentfs run`**. `mount`, `exec`, `serve`
  and `diff` still work. So a prebuilt-with-defaults binary is needed for the
  sandbox features, and that one needs the reverie git dependencies.
- Platform gating. FUSE is Linux-only in this CLI; macOS hard-errors on
  `--backend fuse` (`agentfs/cli/src/cmd/mount.rs:69-81`) and uses NFS.
  Windows is a stub (`cmd/run_windows.rs`, 22 lines).
- Subprocess error handling, version pinning, and a `agentfs --version` check.
- The lock. Every API has to be designed around it.

### What it forecloses

Nothing technically, but it sets a user expectation that fsdantic manages a
native binary. Once `workspace.mount()` ships, removing it is a breaking
change.

### API change

Additive and clearly separated. Everything goes behind an optional import that
raises a typed error when the binary is missing:

```python
from fsdantic.cli import require_agentfs      # raises AgentFSBinaryNotFound

async with workspace.exec(["pytest", "-q"]) as result:   # B3
    ...
```

Never make the core data API depend on it.

### Verdict

**Do this second, in shape B3.** It is the only path to the filesystem
capability, and B3 is the shape that survives the lock constraint.

## Option C — Bind the Rust core directly (PyO3)

Replace `agentfs_sdk` with a native extension over `agentfs-sdk` (the Rust
crate at `agentfs/sdk/rust`).

### Feasibility

Better than expected. `agentfs-sdk` is a clean core: `connection_pool`,
`error`, `filesystem/{agentfs, overlayfs, hostfs_linux, hostfs_darwin}`,
`kvstore`, `schema`, `toolcalls` (`agentfs/sdk/rust/src/lib.rs:1-6`). It has a
stable `FileSystem` trait (`filesystem/mod.rs:194-318`) and **zero FUSE, NFS or
clap dependencies** — those live in `cli/`.

Caveats:

- The API is `async fn` throughout via `async_trait`. A PyO3 layer needs
  `pyo3-async-runtimes` or a blocking wrapper over a tokio runtime.
- The crate has no `crate-type = ["cdylib"]` today.
- There is **no cargo workspace**. `dist-workspace.toml:1-2` declares a
  cargo-dist workspace with one member, `cargo:cli`. The three crates are
  standalone with path dependencies. `agentfs-sdk` carries its own
  `Cargo.lock`.
- No `pyo3` or `maturin` appears anywhere in the repository.

### What it buys

- The **real overlay model**: `fs_whiteout`, `fs_origin`, `overlayfs.rs`,
  `hostfs_linux.rs`. This would let fsdantic overlay a host directory, and
  would make fsdantic databases mountable and readable by `agentfs diff`. That
  is the single biggest architectural win available.
- Encryption, schema detection, migration, connection pooling and the LRU inode
  cache, all for free.
- Inode-level operations Python has never had: `chmod`, `chown`, `utimens`,
  `symlink`, `readlink`, `link`, `mknod`, `statfs`, `open`/file handles,
  `pread`/`pwrite`/`truncate`.
- Speed comparable to the FUSE numbers without the kernel round trip.

### What it costs

This is the expensive option, and the cost is not mostly code.

- **A native build step.** Wheels for Linux x86_64/aarch64, macOS
  x86_64/aarch64, Windows. `maturin` plus CI. `pip install fsdantic` stops
  being pure Python.
- **A fork to maintain.** The crate needs a `cdylib` target and a PyO3 layer.
  Either upstream accepts that, or fsdantic carries a Rust fork alongside the
  existing Python SDK fork.
- Rust expertise becomes a requirement for maintaining fsdantic.
- **It does not deliver mount.** FUSE and NFS are in `cli/`, not
  `sdk/rust`. Binding the SDK gets the overlay model, not the filesystem. To
  mount, you still need option B or a fourth crate.

### What it forecloses

The pure-Python identity, permanently. It also makes option D pointless and
option E incoherent.

### API change

Mostly invisible if the binding mirrors the current surface. But new
capabilities — host-directory base, encryption, symlinks — are large additive
API surface.

### Verdict

**Do not do this now.** The measured 88x from option A is available for zero
structural cost. Revisit C only if two conditions both hold: option A lands and
is still too slow (unlikely — it reaches 1.9x ext4), *and* the real overlay
model becomes a hard requirement. Note carefully: C is the *only* option that
gets `fs_whiteout`/`fs_origin` without reimplementing them in Python.

## Option D — Rewrite the hot path against `pyturso` directly, dropping `agentfs_sdk`

### What it is

Delete the `agentfs_sdk` dependency. fsdantic owns the schema and every SQL
statement.

### What it buys

Over option A: very little. Option A already writes direct SQL on the hot path;
it keeps the SDK for the cold path and for schema initialization. D's only
additions are:

- One less dependency, and the end of the fork documented in
  `docs/dependencies.md`. The fork exists solely to bump `pyturso==0.4.4` to
  `>=0.7.2`. Dropping `agentfs_sdk` dissolves that problem.
- Freedom to fix the SDK's own defects rather than work around them: the
  non-atomic `write_file` (`02-defects.md` §6), the unconditional
  `schema_version` overwrite (`filesystem.py:175-178`), the SQL injection via
  `f"LIMIT {limit}"` (`toolcalls.py:317,347`), the `mkdir` handler that
  swallows every exception as `EEXIST` (`filesystem.py:657-665`).

### What it costs

- fsdantic must reimplement everything: path resolution, `stat`, `readdir`,
  `rename`, `rm -r`, hard links, the `mode` encoding of `agentfs/SPEC.md:159`,
  root-inode initialization. That is the whole of `filesystem.py` (1100+ lines)
  plus `kvstore.py` and `toolcalls.py`.
- fsdantic owns full schema-compatibility risk with no upstream to defer to.
- It loses the SDK as an independent oracle. The prototype in
  `03-performance.md` §6 verified its output *by reading back through the
  unmodified SDK*. Remove the SDK and that check disappears.

### What it forecloses

Option C, in practice — after reimplementing the schema in Python, replacing it
with a Rust binding is a second rewrite.

### API change

None required. It is an implementation swap.

### Verdict

**Not now, but it is the natural end state of option A.** If A grows to cover
ingest, export, diff, resolution and stat, the SDK's remaining role is schema
initialization and a handful of cold-path calls. At that point D is a
subtraction, not a project. Do not target it directly; let A arrive there.
Reassess when the SDK's share of the code drops below roughly 20 %.

## Option E — Narrow the scope

Declare fsdantic a **data and control API for AgentFS databases**. State
plainly that it is not a filesystem, and point users to the `agentfs` CLI for
mount, exec and run.

### What it buys

- Honesty. The README stops implying a capability that
  `01-gap-analysis.md` §3.3 shows is absent.
- Focus. fsdantic's real value-add — typed models, glob and query, content
  search, typed repositories, versioned CAS, merge, tombstones, guarded
  materialization, read-only workspaces, WAL/MVCC control — is ten capabilities
  that AgentFS has no equivalent for. None of them needs a mount.
- It costs nothing. It is a documentation change.

### What it costs

- The "one stop shop" goal, as literally stated, is abandoned.
- Users who need a filesystem go elsewhere, and may not come back.

### What it forecloses

Nothing. E is compatible with A. E plus A is a coherent, defensible product:
**the fastest and most ergonomic way to move data into and out of AgentFS from
Python**, with the CLI named as the tool for filesystem exposure.

### API change

None. Documentation only.

### Verdict

**Adopt E's honesty regardless of what else you choose.** Even after option B
ships, fsdantic will be "a data API that can also drive a mount", not "a
filesystem". Say that. Then decide separately how far toward B to go.

## Comparison

| | A. Optimize | B. Drive CLI | C. PyO3 | D. Raw pyturso | E. Narrow |
|---|---|---|---|---|---|
| Ingest 447 files | **64 ms** | 478 ms | ~100 ms est. | 64 ms | 5604 ms |
| Closes the mount gap | No | **Yes** | No | No | No |
| Gets the real overlay model | No | Partly | **Yes** | No | No |
| New runtime dependency | None | A binary | A wheel | None (removes one) | None |
| Pure Python install | **Yes** | Yes (binary optional) | **No** | **Yes** | Yes |
| Effort | Medium | Medium | High | High | Trivial |
| Risk | Low | Medium | High | Medium | None |
| Reversible | Yes | Yes | **No** | Hard | Yes |
| Measured evidence | **88x** | **11.7x + works** | None | None | — |

## The combination worth building

**A + E now. B3 next. C only on evidence.**

- **A** gives a measured 88x and fixes a shipped correctness bug.
- **E** makes the documentation true today.
- **B3** adds the filesystem capability in the one shape the exclusive lock
  permits: task-scoped `exec`, mount and unmount inside a single call.
- **C** stays on the shelf. Revisit only if the real overlay model becomes a
  hard requirement.
- **D** arrives on its own if A succeeds. Do not aim at it.
