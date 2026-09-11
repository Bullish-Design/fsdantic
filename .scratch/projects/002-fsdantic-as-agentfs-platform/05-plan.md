# 05 — Staged plan

Sequenced by cost and risk, cheapest and safest first. Every stage ends with a
**gate**: a number that must be measured before the next stage is justified. If
a gate fails, stop and reassess rather than continuing.

Recommended combination, from [`04-options.md`](04-options.md): **A + E now,
B3 next, C only on evidence.**

## Stage 0 — Fix the shipped correctness bug

**Cost: hours. Risk: none. Do this before anything else.**

`files.write_many` loses 98 % of a batch at its own default, silently truncates
files on overwrite, and aborts the process on a fresh directory tree
(`02-defects.md` §1). It is released in 0.7.0.

### Do

1. In `src/fsdantic/files.py:371-425`, ignore `concurrency_limit` for writes.
   Run items sequentially. Keep the `BatchResult` shape so nothing breaks.
2. Deprecate the `concurrency_limit` parameter with a `DeprecationWarning` on
   `files.write_many`. State that the connection model gives it no meaning.
3. Document the constraint in `README.md` §2.5 and `docs/concurrency.md`:
   **never fan out writes on one workspace with `asyncio.gather`.** The failure
   modes include an uncatchable process abort. Users who wrote that pattern by
   hand are exposed too, and only the documentation reaches them.
4. Add three regression tests, one per failure mode in `02-defects.md` §1.2:
   new-shallow, overwrite, new-shared-parent. Assert `failed == 0`.
5. Add a `CHANGELOG.md` entry. Grade it a correctness fix, not a performance
   regression, even though sequential is slower.

### Do not

Do not try to make concurrency work with a lock around each write. It hides the
bug without buying throughput — there is one connection and one worker thread
(`02-defects.md` §2). Stage 1 is the real fix.

### Gate 0

- `write_many` with 500 new files across a fresh deep tree: **0 failures, no
  panic, no abort.**
- Full suite green: `pytest tests/ -q` at 435 passed or better.
- `ruff check src/` clean.

## Stage 1 — Bulk ingest and export

**Cost: ~1-2 weeks. Risk: low. This is the payload of the whole project.**

Implements [`04-options.md`](04-options.md) option A items 1, 2 and 6.
Prototype and measurements: [`03-performance.md`](03-performance.md) §6.

### Do

1. Add `src/fsdantic/_internal/bulk.py`. It owns direct SQL against the
   AgentFS 0.4 schema: `fs_inode`, `fs_dentry`, `fs_data`.
2. Read `fs_config.chunk_size` at open time. Do not hardcode 4096
   (`agentfs/SPEC.md:151-157`).
3. Read `fs_config.schema_version` at open time and refuse anything other than
   `0.4` with a typed `WorkspaceError`. The Rust SDK does this at
   `agentfs/sdk/rust/src/schema.rs:94-104`; the Python SDK does not.
4. Public API, additive:
   - `files.ingest_tree(source: Path, *, prefix="/", filters=None) -> IngestResult`
   - `files.export_tree(target: Path, ...)` — reuse the existing guarded
     staging swap in `materialization.py:250`.
   - `files.write_batch(items) -> BatchResult` — the batched replacement for
     `write_many`.
5. Reimplement `write_many` on top of `write_batch`.
6. Handle the cases the prototype skipped: existing paths, caller-supplied
   `mode`/`uid`/`gid`, correct directory `nlink`.
7. Every bulk write commits **once**, in one explicit transaction. This also
   fixes ingest atomicity (`02-defects.md` §6).

### Test discipline

Verify bulk writes by **reading back through the unmodified `agentfs_sdk`
API** — `read_file`, `stat`, `readdir` — exactly as the prototype does. The SDK
is the oracle. Do not verify direct SQL with direct SQL.

Add a property test: ingest a random tree, export it, compare byte for byte.

### Gate 1

Against the devman corpus, 447 files, 2,747,810 bytes:

| Metric | Target | Prototype achieved |
|---|---|---|
| `ingest_tree` | **< 200 ms** | 63.5 ms |
| vs ext4 | **< 6x** | 1.9x |
| Content mismatches | **0** | 0 |
| `export_tree` round trip | **< 400 ms** | not yet measured |
| Suite | green | — |

If ingest lands under 200 ms, Stage 1 has delivered a **28x** improvement and
the performance objection to fsdantic is answered. If it lands above 500 ms,
stop: the prototype's assumptions did not survive production requirements, and
options C and D need reconsidering.

## Stage 2 — Make diff and startup cheap

**Cost: ~1 week. Risk: low.**

Implements option A items 3, 4 and 5.

### Do

1. **O(changes) diff.** Replace `_list_all_files`
   (`src/fsdantic/materialization.py:562-604`) with one recursive CTE over
   `fs_dentry` joined to `fs_inode`. One round trip per layer instead of
   thousands. Compare `(size, mtime)` first; read content only when sizes
   match. Keep the documented "visibility delta" semantics but add an
   `include_base_only: bool = False` parameter so a caller can ask the question
   they actually have.
2. **Single-round-trip path resolution** for the remaining per-file paths.
   Measured worth: ~2.5x on a depth-6 tree
   ([`03-performance.md`](03-performance.md) §5.3).
3. **Lazy import.** PEP 562 `__getattr__` in `src/fsdantic/__init__.py`.
   `models.py` costs 205 ms of a 448 ms import (`02-defects.md` §5).

### Gate 2

| Metric | Now | Target |
|---|---|---|
| `diff(base)` on 447 files, 2 real changes | 1865 ms | **< 150 ms** |
| Changes reported | 448 | **2**, with `include_base_only=False` |
| `python -c "import fsdantic"` | 448 ms | **< 80 ms** |
| `Fsdantic.open` on an existing DB | 6-12 ms | unchanged |

The import gate matters for a specific reason: it is what excluded fsdantic
from devman's `enterShell`, which has a measured 10 ms budget. Even at 80 ms
fsdantic will not fit that budget. State this plainly rather than implying the
gate solves it. An `enterShell` integration needs a subprocess or a daemon, not
a faster import.

## Stage 3 — Documentation truth

**Cost: hours. Risk: none. Can run in parallel with Stage 2.**

Implements option E.

### Do

1. Add a "What fsdantic is not" section to `README.md`. State that fsdantic is
   a data and control API, that AgentFS workspaces are not mountable from
   Python, and that external binaries cannot see an fsdantic workspace. Name
   the `agentfs` CLI as the tool for that.
2. Document the overlay divergence (`01-gap-analysis.md` §2): fsdantic's
   overlay is two independent databases plus KV tombstones, **not** the
   AgentFS `fs_whiteout`/`fs_origin` model. An fsdantic overlay is not
   mountable and `agentfs diff` cannot read it. A user planning to move between
   the two tools must know this before they start.
3. Document the concurrency rule as a rule, not a note: **one workspace, one
   sequential caller.** Never fan out with `asyncio.gather`.
4. Add the capability matrix from `01-gap-analysis.md` §3 to the docs, trimmed.
   It is the fastest way for a user to find out whether fsdantic does what they
   need.

### Gate 3

A reader who has not seen this project can answer, from the README alone:
"can I run `pytest` against an fsdantic workspace?" — and get "no, use
`agentfs exec`."

## Stage 4 — Optional CLI driver

**Cost: ~2-3 weeks. Risk: medium. Only start after Gates 0-3 pass.**

Implements option B, shape **B3** — task-scoped only.

### Why B3 and not B1 or B2

A mount holds an exclusive file lock. `agentfs diff` against a mounted
workspace fails with `Locking error: Failed locking file. File is locked by
another process`, and `turso.aio.connect` cannot open it either
(`01-gap-analysis.md` §3.3.3). So a long-lived mount with concurrent Python
access does not exist. B3 sidesteps this by never holding both at once.

### Do

1. Add `src/fsdantic/cli.py`. Locate the binary, check `agentfs --version`
   against a pinned range, raise a typed `AgentFSBinaryNotFound` when absent.
   **Never import this from the core data path.**
2. Ship it as an extra: `pip install fsdantic[cli]` installs nothing but
   enables the import and documents the binary requirement.
3. Public API, one shape only:

   ```python
   result = await workspace.exec(["pytest", "-q"], timeout=600)
   ```

   Semantics: close the fsdantic connection, spawn `agentfs exec`, run the
   command with cwd at the mount, unmount, reopen the connection, return the
   exit code and output. Document that the workspace is unusable for the
   duration.
4. Document the platform matrix: FUSE on Linux, NFS on macOS
   (`agentfs/cli/src/cmd/mount.rs:69-81`), Windows unsupported.
5. Document the inotify asymmetry (`01-gap-analysis.md` §3.3.2): events fire
   for writes through the mount, never for out-of-band writes to the base. Do
   not build a change-notification feature on it without saying so.
6. Record the build recipe in the docs and link
   [`07-agentfs-cli-build.md`](07-agentfs-cli-build.md). A user who must build
   the binary will not succeed by guessing.

### Do not

- Do not expose a long-lived `workspace.mount()`. The lock makes it a trap.
- Do not make `agentfs` a hard dependency of `fsdantic`.
- Do not wrap `agentfs run`. `--no-default-features` disables it, so any binary
  a user builds from the working recipe will not have it.

### Gate 4

- `workspace.exec(["true"])` round trips in **< 2 s** including mount and
  unmount.
- With the binary absent, importing `fsdantic` still works and
  `workspace.exec` raises `AgentFSBinaryNotFound` with an actionable message.
- The workspace is usable again after `exec` returns. Verified by a write
  immediately afterwards.
- The mount is gone after `exec` returns, on both the success and the failure
  path. Verified against `/proc/mounts`.

## Stage 5 — Reassess

After Gates 0-4, re-measure and re-read [`04-options.md`](04-options.md).

Trigger conditions for the expensive options:

| If | Then consider |
|---|---|
| Ingest is still above 500 ms after Stage 1 | Option C (PyO3) or D (raw pyturso) |
| Users need a host directory as a read-only base | **Option C only.** `hostfs_linux.rs` has no Python equivalent. |
| Users need fsdantic databases to be mountable | Option C, or reimplement `fs_whiteout`/`fs_origin` in Python |
| Users need encryption or remote sync | Option B for the CLI flags, or C for the Rust `EncryptionConfig` |
| `agentfs_sdk` drops below ~20 % of the code | Option D, as a subtraction |
| Multiple processes must share one workspace | Option B2 — `agentfs serve` as the owner, fsdantic as a client |

## Summary

| Stage | Content | Cost | Risk | Key gate |
|---|---|---|---|---|
| 0 | Fix `write_many` | Hours | None | 0 failures, no abort |
| 1 | Bulk ingest / export | 1-2 wk | Low | **Ingest < 200 ms** |
| 2 | O(changes) diff, lazy import | 1 wk | Low | diff < 150 ms, import < 80 ms |
| 3 | Documentation truth | Hours | None | README answers the mount question |
| 4 | Optional CLI `exec` | 2-3 wk | Medium | Round trip < 2 s, clean unmount |
| 5 | Reassess | — | — | — |

Stages 0 through 3 take under a month, add no dependency, and convert fsdantic
from 164x slower than ext4 to roughly 2x. That is the work with evidence behind
it. Stage 4 is the first stage that asks the user for something, and it should
not start until the first four gates are green.
