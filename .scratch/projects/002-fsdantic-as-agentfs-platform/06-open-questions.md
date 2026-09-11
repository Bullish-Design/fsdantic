# 06 — Open questions

These need the owner's decision. Each states the question, the evidence, the
options, and what the answer blocks. They are ordered by how much of
[`05-plan.md`](05-plan.md) each one gates.

## Q1 — Which reading of "one stop shop" is the goal?

[`01-gap-analysis.md`](01-gap-analysis.md) §1 lists four readings. They cost
different amounts and lead to different products.

| Reading | Cost | Result |
|---|---|---|
| **A. Typed data API** | Zero | fsdantic 0.7.0 today |
| **B. Fast data plane** | Stages 0-3, under a month | 1.9x ext4, correct, honest |
| **C. Full control plane** | Add Stage 4 plus sync, encryption, migrate | Python can drive everything the CLI does |
| **D. A filesystem** | Option C (PyO3) plus a FUSE server, or a hard CLI dependency | External binaries see the workspace |

**Every other question below assumes at least B.** If the answer is D, most of
this project's recommendation is wrong and the conversation should start at
[`04-options.md`](04-options.md) option C.

**Blocks:** everything.

## Q2 — Is a native dependency or a build step acceptable?

Today `pip install fsdantic` is pure Python. Three futures:

| Future | Install | Buys |
|---|---|---|
| **Stay pure Python** | `pip install fsdantic` | Nothing new. Option A still reaches 1.9x ext4. |
| **Optional binary** | `pip install fsdantic` + user installs `agentfs` | Mount, exec, serve, sync, encryption. Option B. |
| **Native wheel** | `pip install fsdantic` pulls a compiled extension | The real overlay model, host-directory base, inode ops. Option C. |

The middle option is the one Stage 4 assumes, and it is not free for the user.
The build recipe in [`07-agentfs-cli-build.md`](07-agentfs-cli-build.md) needed
a non-default toolchain, a feature flag, and four environment variables. There
are no published binaries on PyPI.

Sub-question: **would you ship prebuilt `agentfs` binaries yourself** — as a
`fsdantic-agentfs-bin` package with platform wheels? That converts a user
problem into a release-engineering problem, and it is the only way "optional
binary" becomes usable by ordinary users.

**Blocks:** Stage 4, and option C entirely.

## Q3 — Can the agentfs fork be patched upstream?

fsdantic already maintains a fork:
`Bullish-Design/agentfs @ v0.6.4-pyturso-0.7.2`, whose only change is bumping
`pyturso==0.4.4` to `>=0.7.2,<0.8` (`docs/dependencies.md`).

The investigation found several defects in the upstream Python SDK that a fork
could fix cheaply:

| Defect | Location | Fix |
|---|---|---|
| `write_file` commits 3-5 times, not atomic | `filesystem.py:282,299,443` | One transaction |
| Cursor held across `await` breaks concurrency | `filesystem.py:259-283` | Fetch and close before yielding |
| No batch API | all of `filesystem.py` | Add `write_files()` |
| `schema_version` overwritten unconditionally on every open | `filesystem.py:175-178` | Check, do not write |
| SQL injection via `f"LIMIT {limit}"` | `toolcalls.py:317,347` | Bind the parameter |
| `mkdir` reports every exception as `EEXIST` | `filesystem.py:657-665` | Narrow the handler |
| `copy_file` commits the destination delete before copying | `filesystem.py:1051-1052` | One transaction |
| No path resolution for `..` — a file can literally be named `..` | `filesystem.py:198-203` | Normalize |
| "Initialize all components in parallel" — they are sequential | `agentfs.py:105` | Fix the comment |

Three paths:

1. **Upstream them.** Best outcome. Removes the fork's reason to exist over
   time. Depends on upstream's responsiveness, which is unknown.
2. **Carry them in the fork.** Immediate, but the fork stops being
   code-identical to upstream, and the refresh procedure in
   `docs/dependencies.md` step 2 — "the fork must stay code-identical to
   upstream" — has to change.
3. **Work around them in fsdantic.** What option A does. It leaves the defects
   in place for anyone using `agentfs_sdk` directly.

The SQL injection and the atomicity bug are worth reporting upstream regardless
of which path you take.

**Blocks:** nothing directly. Changes how much of option A is workaround versus
fix. If path 1 or 2 succeeds, option D gets easier and option A gets smaller.

## Q4 — Does mount support mean shelling out to a binary the user must install?

Stated plainly: **yes, unless you choose option C and then also write a FUSE
server.** FUSE and NFS live only in `agentfs/cli/`. Binding
`agentfs-sdk` gets the overlay model, not the mount.

So the real question is which of these you are willing to ship:

| Shape | User burden | Maintenance burden |
|---|---|---|
| Document `agentfs`, do not wrap it | User installs and learns a second tool | None |
| `workspace.exec()` over the binary (Stage 4, B3) | User installs the binary | Subprocess, version pin, platform matrix |
| Ship prebuilt binaries in a wheel | None | Release engineering for 5 targets |
| PyO3 binding plus a Python FUSE server | None | Rust plus a FUSE implementation. Very high. |

The measurements support the middle rows: the mount is correct and fast
(478 ms for 447 files, 11.7x faster than fsdantic today), so wrapping it
delivers real value. The blocker is distribution, not capability.

**Blocks:** Stage 4's shape.

## Q5 — Should fsdantic be a client of `agentfs serve` rather than a database opener?

This is the question the exclusive lock forces, and it may be the most
important architectural question in the project.

**The constraint.** A mount holds the database exclusively. `agentfs diff`
fails with `Locking error: Failed locking file. File is locked by another
process`, and `turso.aio.connect` cannot open it either
(`01-gap-analysis.md` §3.3.3). One database, one owner, always.

**The observation.** The CLI ships two servers — `agentfs serve nfs` and
`agentfs serve mcp` (`agentfs/cli/src/opts.rs:314-318`). A server owning the
database and multiplexing clients is the standard answer to an exclusive lock.
It may be the intended multi-consumer path.

Three positions:

| Position | Design | Cost |
|---|---|---|
| **Direct opener** (today) | fsdantic opens the file. One process, ever. | Zero. Cannot coexist with a mount. |
| **Handoff** | fsdantic closes before the mount, reopens after. Stage 4 B3. | Low. Serializes access in time. |
| **Server client** | `agentfs serve` owns the file; fsdantic speaks MCP over stdio, or mounts the NFS export and uses ordinary file I/O. | High. New transport. |

Arguments against the server-client position, so it gets a fair hearing:

- The MCP server exposes **12 data-plane tools only** — `read_file`,
  `write_file`, `readdir`, `mkdir`, `remove`, `rename`, `stat`, `access`, and
  four KV calls (`agentfs/cli/src/cmd/mcp_server.rs:277-490`). None of
  fsdantic's value-add — glob, query, content search, typed repositories,
  versioned CAS, merge, materialize — has an equivalent. fsdantic would have to
  rebuild all of it on a much slower per-call transport.
- Both `serve` variants were deprecated as top-level commands and re-exposed
  under `serve` (`main.rs:255,268`). Their stability is unclear.
- Per-call latency over stdio or NFS is far worse than 0.11 ms.

Arguments for it:

- It is the only design where fsdantic and other consumers share one workspace
  at the same time.
- It removes the need to manage mounts from Python.

**A concrete sub-question worth asking upstream:** is `serve` intended as the
multi-consumer path, and is a Python client expected to be a first-class
consumer of it?

**Blocks:** any requirement for concurrent multi-process access. If no such
requirement exists, take the handoff position and move on.

## Q6 — Should fsdantic adopt the real AgentFS overlay model?

fsdantic reimplements copy-on-write above two independent flat databases and
never writes `fs_whiteout`, `fs_origin` or `fs_overlay_config`
(`01-gap-analysis.md` §2).

| Keep the current model | Adopt the AgentFS model |
|---|---|
| Works today. Verified byte-exact. | Databases become mountable and readable by `agentfs diff` |
| `diff` is O(tree) by construction | `diff` becomes O(changes) — a scan of `fs_whiteout` plus the delta |
| A base can only be another database | A base can be a **host directory** (`--base`) |
| Tombstones are a private KV convention | Whiteouts are the specified mechanism (`SPEC.md:495`) |
| Merge reads both files in full | Copy-up is the specified operation |
| No new work | Substantial: reimplement `overlayfs.rs` in Python, or take option C |

Note that Stage 2's cheap fix makes `diff` fast **without** this change. So
this question is not about performance. It is about whether an fsdantic
workspace should be a first-class AgentFS artifact that other tools can read.

If the answer is yes, option C becomes much more attractive, because
`overlayfs.rs` and `hostfs_linux.rs` already exist in Rust and would not need
reimplementing.

**Blocks:** the long-term relationship between fsdantic and the rest of the
AgentFS ecosystem.

## Q7 — Should fsdantic stay async-only?

Every public method is `async`. The driver is `turso.aio`. A single connection
serializes through one worker thread (`02-defects.md` §2).

The observation that motivates the question: **fsdantic's async surface buys
nothing.** Concurrency inside a workspace is impossible; attempting it is what
produces the `write_many` bug. The `async` keyword communicates a capability
that does not exist.

| Option | For | Against |
|---|---|---|
| **Stay async-only** | No change. Matches the driver. Composes with async callers. | Forces `asyncio.run` on every sync caller. Misleads about concurrency. |
| **Add a sync facade** | Scripts, CLIs and `enterShell`-style hooks become natural. Honest about the model. | A second API surface to test and document. Needs a `turso` sync connection or a worker loop. |
| **Go sync-only** | Simplest, most honest. | Breaking. Excludes async consumers. |

A sync facade interacts with Q1: reading **B** (fast data plane) makes
"ingest a tree, export a tree" the main use case, and that use case is usually
called from a script, not an event loop.

**Blocks:** the public API shape of Stage 1's new bulk methods. Decide before
they ship, not after.

## Q8 — Is the `enterShell` use case still in scope?

devman's `enterShell` has a measured 10 ms budget. The real cost is
`import fsdantic` at 335-448 ms (`02-defects.md` §5), not the 44 ms open the
briefing identified.

Stage 2 targets under 80 ms. **That still misses the budget by 8x.** A pure
Python import cannot reach 10 ms; `agentfs_sdk` alone costs 93 ms, mostly
`asyncio` and `turso.lib_aio`.

Honest options for that specific use case:

| Option | Feasible |
|---|---|
| Faster import | No. Floor is ~90 ms with the current dependencies. |
| A long-lived daemon that `enterShell` talks to over a socket | Yes. Amortizes the import. |
| A compiled helper for the hot path | Yes, at the cost of Q2. |
| Declare `enterShell` out of scope | Yes. Cheapest and possibly correct. |

**Blocks:** whether Stage 2's import work is worth doing at all. It is
worthwhile for general ergonomics regardless, but it will not win back devman.

## Decision order

Answer them in this order. Each answer narrows the next.

1. **Q1** — what is the goal? Everything follows.
2. **Q2** — is a native dependency acceptable? Gates options B and C.
3. **Q7** — async-only? Must be settled before Stage 1 ships its API.
4. **Q8** — is `enterShell` in scope? Cheap to answer, changes Stage 2's value.
5. **Q4** — what does mount support mean concretely? Shapes Stage 4.
6. **Q5** — direct opener, handoff, or server client? Only if multi-process
   access is required.
7. **Q3** — upstream, fork, or work around? Affects effort, not direction.
8. **Q6** — adopt the real overlay model? A long-term direction, not a blocker.

Stages 0 and 1 in [`05-plan.md`](05-plan.md) are safe to start **before any of
these are answered**. Fixing a correctness bug and making ingest 88x faster is
correct under every answer to every question above.
