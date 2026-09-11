# DESIGN: a resident fsdantic daemon

**Status:** proposal, not started
**Relates to:** `ISSUE.md` (this directory), `002-fsdantic-as-agentfs-platform/`
**Question it answers:** how does a caller pay 0 ms for `import fsdantic`, and
who owns the database lock?

---

## 1. Why a daemon

Three measured facts point the same way.

**1. The import costs more than the work.**

| Cost | Time |
|---|---|
| `import fsdantic` | 323.6 ms |
| interpreter + import | ~500 ms |
| open an existing database | 7.6 ms |
| bulk ingest, 447 files | 54.6 ms |

Once bulk ingest lands (`ISSUE.md` §10.2), a one-shot process spends **85% of
its life importing**. No amount of data-layer work fixes that. Of the 323 ms,
~205 ms is `models.py` building pydantic models — paid once per process, and a
resident process pays it once, ever.

**2. The database takes an exclusive lock.** While the AgentFS Rust mount holds
a workspace, any other opener fails:

```
Error: database error: Locking error: Failed locking file.
File is locked by another process
```

The same applies to two Python processes. Today the rule is unwritten and
callers discover it by failing. A daemon makes single ownership **the
architecture** instead of a hazard.

**3. A connection must serialize anyway.** `ISSUE.md` §4: one connection, one
worker thread, and concurrent statements corrupt data or panic the WAL layer.
Any correct design serializes per workspace. A daemon owning one queue per
workspace expresses that directly, and it removes the whole class of bug that
`write_many` fell into — clients cannot interleave what they cannot reach.

---

## 2. Transport is free

Measured on this machine, stdlib asyncio, no framework:

| Transport | Small RTT | 2.75 MB echo | Throughput |
|---|---|---|---|
| Unix domain socket | **0.037 ms** | 3.7 ms | 1478 MB/s |
| TCP loopback | 0.038 ms | 4.2 ms | 1317 MB/s |

Put that against the work being requested:

| Operation | Cost | Transport as % |
|---|---|---|
| `files.write`, one file | 7.26 ms | 0.5% |
| open a workspace | 7.6 ms | 0.5% |
| bulk ingest 447 files | 54.6 ms | 6.7% (one 2.75 MB transfer) |

**The transport does not matter. The framework on top of it does not matter
either** — any RPC layer sits above these numbers and can only add. Choose for
ergonomics and safety, not speed.

Recommendation: **Unix domain socket**, not TCP. It is marginally faster, and it
gets access control from filesystem permissions with no network exposure and no
port to collide. Offer TCP only if a remote client is a real requirement.

---

## 3. Should it be FastAPI?

FastAPI is a reasonable default, with caveats.

**For:** async all the way down, which matches fsdantic; pydantic models are
already the vocabulary, so request and response types come free and stay in
sync with the library; OpenAPI gives a generated client and live docs; the
team knows it.

**Against:** it adds `fastapi`, `starlette`, `uvicorn` and their trees to a
library whose current runtime dependency list is three entries. That worsens the
import problem for anyone importing fsdantic directly — the very thing this
design exists to fix. HTTP also has no natural framing for binary blobs; JSON
means base64, at +33% size and a full encode/decode on both ends of every file
transfer.

**Resolution: split the package.** `fsdantic` stays dependency-light. A separate
`fsdantic-daemon` (or a `[daemon]` extra) carries the server. A thin
`fsdantic.client` module speaks the protocol with stdlib only, so a client pays
no framework cost.

If HTTP is chosen, send file bytes as `application/octet-stream` bodies with
metadata in headers or the path — never base64 inside JSON.

**Alternative worth costing before committing:** a length-prefixed msgpack
protocol over the Unix socket, in ~200 lines of stdlib asyncio. It gives native
binary framing, no dependency, and no HTTP semantics to fight. It loses OpenAPI,
generated clients and the ability to `curl` the thing. Decide by whether
non-Python clients are in scope (§7).

---

## 4. Shape

```
   client (CLI, editor, agent, devman)
        │  Unix socket, one request per operation
        ▼
┌──────────────────────────────────────────────┐
│ fsdantic daemon (one per user)               │
│                                              │
│  router ── auth ── workspace registry        │
│                        │                     │
│         ┌──────────────┼──────────────┐      │
│         ▼              ▼              ▼      │
│    ws A queue     ws B queue     ws C queue  │  one serialized
│    (1 conn)       (1 conn)       (1 conn)    │  owner per db
└──────────────────────────────────────────────┘
        │              │              │
      a.db           b.db           c.db
```

**Rules the shape enforces:**

1. **One owner per database.** The registry holds at most one connection per
   path. A second request for the same workspace joins that queue; it does not
   open a second connection.
2. **Serialized per workspace, concurrent across workspaces.** This matches the
   connection model exactly (`ISSUE.md` §4) and needs no MVCC.
3. **Idle eviction.** Close a workspace after N seconds unused, so the daemon
   does not pin every database a user ever touched — and so the Rust mount can
   take a database when it needs one (§6).
4. **The daemon never blocks on a client.** Bulk transfers stream; a slow reader
   cannot stall another workspace's queue.

---

## 5. API surface

Mirror the existing manager split, so the daemon API and the library API stay
recognisable as one thing:

| Group | Operations |
|---|---|
| workspace | `open`, `close`, `list`, `info` |
| files | `read`, `write`, `stat`, `exists`, `list_dir`, `remove`, `search`, `query` |
| kv | `get`, `set`, `delete`, `list`, repository operations |
| overlay | `merge`, `diff`, `reset`, tombstones |
| materialize | `to_disk`, `preview`, `diff` |
| **bulk** | **`ingest_tree`, `export_tree`** |

The `bulk` group is new and is the reason the daemon pays off. It must be a
**single request**, not 447 — one call carrying the whole tree, executed as
three `executemany` statements and one commit (`ISSUE.md` §10.2). A per-file
RPC loop would cost 447 x 0.037 ms in transport, which is trivial, but 447 x
7.26 ms in commits, which is not. **The batching must happen server-side, in
SQL, not client-side in the protocol.**

### Two things worth adding that the library cannot offer

**Change notification.** The daemon owns every write, so it can publish a change
stream — the feature `002-fsdantic-as-agentfs-platform` records as absent from
both fsdantic and AgentFS. A subscribe endpoint over the same socket makes
reactive callers possible without polling. This is the strongest argument for
the daemon beyond startup cost, and it should be designed in from the start
rather than bolted on.

**Honest health reporting.** `info` should report which workspaces are open,
which are locked by a mount, queue depth, and idle timers. The current failure
mode is an opaque lock error from deep in the driver.

---

## 6. The hard problem: coexisting with the Rust mount

The daemon and `agentfs mount` both want exclusive access to the same file.
They cannot both have it.

Three options, in preference order:

**(a) Ownership handoff.** The daemon tracks mount state. `mount` requests make
it close its connection and mark the workspace `MOUNTED`; operations on a
mounted workspace return a clear typed error naming the holder, not a driver
lock message. `unmount` returns ownership. Simple, explicit, and it makes the
current silent hazard loud.

**(b) The daemon supervises mounts.** It shells out to `agentfs mount` and owns
the child process lifecycle, so ownership can never be ambiguous. More moving
parts, better guarantees, and it gives the daemon a natural place to expose
`mount`/`unmount` to clients that should not shell out themselves.

**(c) Go through `agentfs serve`.** AgentFS ships `serve nfs` and `serve mcp`.
If `serve` is upstream's intended multi-consumer path, fsdantic might be a
*client* of it rather than a competing db opener. **This needs investigation
before (a) or (b) is built** — it may be that upstream already solved this and
the daemon should not open databases directly at all. Listed as an open
question in `002-fsdantic-as-agentfs-platform/06-open-questions.md`.

Note the version split (`ISSUE.md` §6): the Rust CLI links turso 0.4.4 while
Python has pyturso 0.7.2. Any coexistence design should confirm the two engines
agree on locking semantics before relying on them sharing a file.

---

## 7. Open questions

1. **Is `agentfs serve` the intended answer?** Investigate before building.
   Cheapest possible check: start `agentfs serve nfs`, then try opening the same
   database from Python, and see whether the lock is still exclusive.
2. **Non-Python clients?** If yes, HTTP/OpenAPI earns its cost. If Python-only
   forever, the msgpack-over-Unix-socket option is smaller and faster to build.
3. **Who starts the daemon?** systemd user unit, socket activation, or
   auto-spawn on first client connect. Socket activation gives zero idle cost
   and no lifecycle code in the client. It is the natural fit on this machine,
   which already runs user units.
4. **Multi-user?** A per-user daemon with socket permissions is simple. A shared
   daemon needs real authentication and is a different project.
5. **Does the client stay async?** A sync client over a Unix socket is easy and
   would widen the audience beyond async callers — the whole library is
   currently async-only, which some callers cannot adopt.
6. **What happens to the direct-open API?** If `Fsdantic.open()` still opens
   databases directly, the lock hazard remains for anyone who uses it. Decide
   whether direct open becomes discouraged, daemon-routed, or unchanged.

---

## 8. Suggested sequence

Each stage is independently useful and has a gate.

| Stage | Work | Gate |
|---|---|---|
| 0 | Answer §7.1 — investigate `agentfs serve` | a written answer; may cancel the rest |
| 1 | Bulk ingest/export in the **library** (`ISSUE.md` §10.2) | ingest < 200 ms; the daemon needs this regardless |
| 2 | Minimal daemon: Unix socket, workspace registry, per-workspace queue, files + kv | a client operation costs < 1 ms more than the in-process call |
| 3 | `bulk.ingest_tree` / `bulk.export_tree` as single requests | round trip < 150 ms for 447 files, against 105 ms in-process |
| 4 | Mount coexistence (§6) | a mounted workspace returns a typed error naming the holder, never a driver lock message |
| 5 | Change-notification stream (§5) | a subscriber sees a write within 10 ms |

**Do stage 1 before stage 2.** The daemon's value is amortizing the import; if
the operations it serves are still 100x slower than they should be, the daemon
hides the real problem behind a socket.

---

## 9. What this does not fix

Stated plainly, so the daemon is not oversold:

- **It does not make AgentFS faster.** It removes startup cost and enforces
  correct serialization. The 103x comes from stage 1, not from the daemon.
- **It does not fix `write_many`.** That defect must be fixed in the library
  (`ISSUE.md` §10.1) whether or not a daemon exists.
- **It does not help external binaries.** `git`, compilers and test runners do
  `open()` and `stat()`. They need the FUSE mount, not an RPC API. The daemon
  and the mount serve different callers and neither replaces the other.
- **It does not fix `agentfs diff`** marking reads as modified (`ISSUE.md` §7).
  That is upstream.
