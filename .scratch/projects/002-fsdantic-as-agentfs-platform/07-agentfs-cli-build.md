# 07 — Building and measuring the agentfs CLI

Record of how the Rust CLI was built and exercised. The recipe is non-obvious.
Several steps fail with unhelpful errors if you guess. Keep this file.

Source: `/home/andrew/Documents/Projects/agentfs`, CLI version 0.6.4.
Platform: NixOS, Linux 6.18.38, x86_64.

## 1. Build recipe

Build time: **2m04s**, clean.

### The four non-obvious parts

1. **Use stable cargo 1.95, not the nightly the toolchain file requests.** The
   repository's `rust-toolchain` asks for a nightly. Stable 1.95 builds it.
2. **Pass `--no-default-features`.** The default feature set includes
   `sandbox`, which pulls the `reverie`, `reverie-ptrace` and `reverie-process`
   git dependencies (`agentfs/cli/Cargo.toml`, `[features]`). Those do not
   build cleanly here.
3. **Provide the native libraries.** `gcc`, `pkg-config`, `fuse3`.
4. **Set the OpenSSL and lzma paths.** `OPENSSL_DIR`, `OPENSSL_LIB_DIR`, and a
   `LIBRARY_PATH` that includes `xz` so the linker resolves `-llzma`.

### Shape of the invocation

```
# toolchain: stable cargo 1.95
# build inputs: gcc, pkg-config, fuse3, openssl, xz
export OPENSSL_DIR=<openssl prefix>
export OPENSSL_LIB_DIR=<openssl prefix>/lib
export LIBRARY_PATH=<xz prefix>/lib:$LIBRARY_PATH

cargo build --release --no-default-features
```

### What `--no-default-features` costs

| Command | Available |
|---|---|
| `agentfs mount` | **Yes** |
| `agentfs exec` | **Yes** |
| `agentfs serve` (nfs, mcp) | **Yes** |
| `agentfs diff` | **Yes** |
| `agentfs init`, `fs`, `timeline`, `ps`, `prune`, `migrate`, `sync` | **Yes** |
| `agentfs run` | **No** — needs the `sandbox` feature |

This matters for [`05-plan.md`](05-plan.md) Stage 4. A binary a user builds
from this recipe has no `run` subcommand. Do not wrap `run`.

## 2. Verifying the mount

```
agentfs init <id> --base <DIR>      # create a COW overlay over a real directory
agentfs mount <id> <MOUNTPOINT>     # daemonizes by default; -f to stay foreground
```

Against the devman repository:

| Check | Result |
|---|---|
| File count through the mount | 1083, identical to the real tree |
| Directory listings | Identical |
| Content hash of all tracked files | Byte-identical |
| Symlinks | Preserved |
| Writes through the mount | Land in the delta layer |
| Base directory after writes (`git status`) | Clean, untouched |

The copy-on-write contract holds.

## 3. Measured performance

447-file corpus, 3.4 MB, FUSE backend.

| Operation | ext4 | FUSE | Ratio |
|---|---|---|---|
| untar 447 files | 44.4 ms | 478.2 ms | 10.8x |
| `cat` whole tree | 13.0 ms | 76.6 ms | 5.9x |
| `find -type f` | 6.6 ms | 40.2 ms | 6.1x |
| `grep -r` | 11.9 ms | 77.6 ms | 6.5x |
| `stat` each file | 655.0 ms | 761.5 ms | 1.2x |
| create 500 small files | 32.4 ms | 373.7 ms | 11.5x |
| `find` over the 1083-file repo | 36.0 ms | 1736.9 ms | 48.2x |

Reference points from [`03-performance.md`](03-performance.md), same corpus:

| Path | Write 447 files |
|---|---|
| ext4 | 34.1 ms |
| **Bulk SQL prototype** | **63.5 ms** |
| **FUSE mount** | **478.2 ms** |
| **fsdantic** | **5604.5 ms** |

Two readings.

- The FUSE mount is **11.7x faster than fsdantic** while also serving the
  kernel VFS. AgentFS storage is not the bottleneck; the Python per-file call
  shape is.
- The bulk SQL prototype is **7.5x faster than the mount**. For bulk data
  movement, going through a filesystem interface is pure overhead when the
  caller already holds the bytes. Mount is for external binaries, not for
  ingest.

The `find` result (48x on 1083 files) is the mount's worst case. Overlay
`lookup` consults the delta, then the whiteout table, then the base
(`agentfs/SPEC.md:559-562`), so traversal pays three lookups per entry.

## 4. inotify behavior

| Change made | Visible through the mount | inotify event |
|---|---|---|
| Write, modify, delete **through the mount** | Yes | Yes — `CREATE`, `MODIFY`, `CLOSE_WRITE`, `DELETE` |
| Write directly to the **base directory** | Yes | **No** |

The mount is coherent-on-read with the base but silent on notification.

Consequence for fsdantic: a change-notification feature built on a mount
watcher sees mount-side writes only. It misses every out-of-band edit to the
base, and it misses every write fsdantic makes to the database. Say so in the
docs before shipping such a feature.

## 5. The exclusive lock

```
$ agentfs diff <agent>          # while the same agent is mounted
Error: database error: Locking error: Failed locking file.
File is locked by another process
```

A mount holds the database exclusively for its whole lifetime. While it does:

- `agentfs diff`, `agentfs fs ls` and `agentfs timeline` fail.
- **fsdantic cannot open the database.** `turso.aio.connect` takes a file-level
  lock at connect time (`docs/concurrency.md`, "Multi-process caveat").

This is the central design constraint on combining fsdantic with the Rust
tooling. See [`01-gap-analysis.md`](01-gap-analysis.md) §3.3.3,
[`04-options.md`](04-options.md) option B, and
[`06-open-questions.md`](06-open-questions.md) Q5.

## 6. Full command surface, v0.6.4

For the capability matrix in [`01-gap-analysis.md`](01-gap-analysis.md) §3.
Flags come from `agentfs/cli/src/opts.rs`.

| Command | Notes |
|---|---|
| `completions` | install / uninstall / show |
| `init [ID]` | `--base <PATH>` for a COW overlay over a host directory; `--key`/`--cipher` for encryption; `--force`; `-c/--command`; `--backend`; sync options |
| `sync <ID> <pull\|push\|stats\|checkpoint>` | Turso remote sync |
| `fs <ID> <ls\|cat\|write>` | The whole `fs` surface. No mkdir, rm, stat or cp. |
| `run [CMD]` | Sandboxed COW over cwd. `--session`, `--allow`, `--experimental-sandbox`, `--strace`. **Needs the `sandbox` feature.** |
| `exec <ID> <CMD>` | Mount to a temp dir, run, unmount. Unix only. |
| `mount [ID] [MOUNTPOINT]` | With no args, lists mounts. `-f`, `--allow-root`, `--system`, `--uid`, `--gid`, `--backend`, `-a` |
| `diff <ID>` | Base vs delta. Overlay mode only. |
| `timeline <ID>` | `tool_calls` audit log. `--limit`, `--filter`, `--status`, `--format` |
| `nfs <ID>` | Deprecated. Use `serve nfs`. |
| `mcp-server <ID>` | Deprecated. Use `serve mcp`. 12 tools. |
| `serve <nfs\|mcp>` | The preferred server entry points |
| `ps` | List active `run` sessions and their processes |
| `prune mounts` | Unmount unused agentfs mountpoints. `--force` |
| `migrate <ID>` | Upgrade schema to 0.4. `--dry-run` |

Platform gating worth remembering: FUSE is the Linux default and NFS the
default elsewhere (`opts.rs:18-31`); macOS hard-errors on `--backend fuse`
(`cmd/mount.rs:69-81`); `run` on Windows is a 22-line stub
(`cmd/run_windows.rs`).
