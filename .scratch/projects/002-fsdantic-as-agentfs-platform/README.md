# 002 — fsdantic as an AgentFS platform

**Status:** investigation complete, decision pending.
**Date:** 2026-08-28.
**Goal under evaluation:** make fsdantic the one stop shop for AgentFS — the
cleanest, fastest, most complete *opinionated* AgentFS filesystem for Python.

## Documents

| File | Contents |
|---|---|
| [`01-gap-analysis.md`](01-gap-analysis.md) | What "one stop shop" must mean. Capability matrix: AgentFS vs fsdantic. |
| [`02-defects.md`](02-defects.md) | Every defect found, with evidence, root cause and blast radius. |
| [`03-performance.md`](03-performance.md) | Where the milliseconds go. Measured, statement by statement. |
| [`04-options.md`](04-options.md) | Five fix options, each with cost, benefit and API impact. |
| [`05-plan.md`](05-plan.md) | Staged plan with measurement gates. |
| [`06-open-questions.md`](06-open-questions.md) | Decisions only the owner can make. |
| [`07-agentfs-cli-build.md`](07-agentfs-cli-build.md) | How to build and measure the Rust CLI. Non-obvious; record it. |

## Test bed

All numbers in these documents come from one machine and one corpus.

| Item | Value |
|---|---|
| Machine | Linux 6.18.38, ext4, x86_64 |
| Python | 3.13.14 |
| fsdantic | 0.7.0 (`src/fsdantic`, working tree) |
| agentfs-sdk | 0.6.4 (Bullish-Design fork, pyturso pin bumped) |
| pyturso | 0.7.2 (Limbo engine) |
| pydantic | 2.14.0b1 |
| agentfs CLI | 0.6.4, built from source, `--no-default-features`, FUSE backend |
| Corpus | devman tracked files: 447 files, 2,747,810 bytes, max depth 4 |
| Interpreter | `/home/andrew/Documents/Projects/fsdantic/.venv/bin/python` |

Probe scripts live in `/tmp/fsd_[a-k].py`. They are throwaway. Rebuild them
from the code blocks in [`03-performance.md`](03-performance.md).

Numbers vary by up to 2x between runs on a loaded machine. Ratios are stable.
Quote ratios, not absolute milliseconds.

## The five findings that matter

1. **The model is correct.** Copy-on-write ingest, overlay and materialize
   produce byte-exact output. 447 files in, 448 files out, 0 mismatches,
   tombstone applied. Nothing below asks you to change the data model.

2. **The write path is ~100x too slow, and the cause is known and local.** One
   `write_file` for a new file issues 15 SQL statements and **5 commits**. A
   commit costs 2.7 ms. Nothing about AgentFS or libSQL forces this: a
   direct-SQL bulk ingest of the same 447 files, through the same driver, into
   the same schema, takes **63.5 ms** against fsdantic's 5604 ms. That is an
   **88x speedup** and **1.8x the cost of writing to ext4**. The fix is
   batching, not a rewrite.

3. **The Rust FUSE mount confirms the overhead is fsdantic's, not AgentFS's.**
   The same 447-file tree untars through a FUSE mount in **478 ms** — 11.7x
   faster than fsdantic, with full POSIX semantics on top. Two independent
   paths (bulk SQL and FUSE) both beat the Python per-file loop by an order of
   magnitude. The 12.5 ms/file is recoverable overhead.

4. **`files.write_many` is a shipped correctness bug with three failure
   modes**, one of which aborts the process with an uncatchable Rust panic.
   Fix it before anything else.

5. **A mounted database is exclusively locked.** `agentfs diff` against a
   mounted workspace fails with `Locking error: Failed locking file. File is
   locked by another process`. fsdantic cannot open a database that a mount
   holds. This is the central design constraint on every option that combines
   fsdantic with the Rust tooling.

## The performance picture in one table

Same corpus. 447 files, 2,747,810 bytes.

| Path | Time | ms/file | vs ext4 |
|---|---|---|---|
| ext4 (`write_bytes` loop) | 34.1 ms | 0.08 | 1.0x |
| **Bulk SQL into the AgentFS schema** (prototype) | **63.5 ms** | **0.14** | **1.9x** |
| agentfs FUSE mount (`untar`) | 478.2 ms | 1.07 | 14.0x |
| fsdantic `files.write` loop | 5604.5 ms | 12.54 | 164x |

The prototype is **7.5x faster than the FUSE mount** for bulk data movement,
because it skips the kernel round trip per syscall. FUSE wins on POSIX access
for external binaries. They are complementary, not competing.

## Recommendation in one paragraph

Fix `write_many` now (Stage 0). Then add bulk-ingest and bulk-export paths that
write the AgentFS schema directly through `executemany` in one transaction
(Stage 1) — measured at 88x, no new dependency, no native build. Then make
`diff` O(changes) with a recursive CTE over `fs_dentry` instead of a
`readdir`+`stat` walk, and make `import fsdantic` cheap (Stage 2). Only then
open the mount question. The answer there is **not** a PyO3 binding: it is an
optional subprocess driver over the `agentfs` binary, designed around an
explicit **ownership handoff**, because the exclusive lock makes fsdantic and a
live mount mutually exclusive on one database. Full sequencing and measurement
gates are in [`05-plan.md`](05-plan.md).

## Corrections to the briefing

Four briefed findings needed correction. Details in
[`02-defects.md`](02-defects.md) §8.

- The `write_many` error string is **`cannot start a write statement - SQL
  statements in progress`**, not `cannot commit transaction - ...`, and there
  are **two worse failure modes** the briefing missed, including an
  unrecoverable native abort (`SIGABRT`).
- **`read_many`, `kv.get_many`, `kv.set_many`, `kv.delete_many`,
  `repository.save_many` and `KVTransaction` are all clean.** Only
  `files.write_many` is affected. `repository.load_many` has no
  `concurrency_limit` parameter at all, so the briefing's question about it is
  moot. `materialization.py` uses no concurrency; it is sequential and safe.
- The 44 ms workspace open is **not** the `enterShell` blocker.
  **`import fsdantic` costs 335-448 ms**, of which 205 ms is building the
  pydantic models in `src/fsdantic/models.py`. That is 10x the open cost and
  40x the stated 10 ms budget. Opening an *existing* database costs 6-12 ms;
  the 44 ms figure was database *creation*.
- "fsdantic is a thin typed wrapper over the Python SDK" understates the
  divergence. fsdantic **reimplements copy-on-write above two independent flat
  databases** and never writes `fs_whiteout`, `fs_origin` or
  `fs_overlay_config`. An fsdantic overlay is not mountable and not readable by
  `agentfs diff`. See [`01-gap-analysis.md`](01-gap-analysis.md) §2.
