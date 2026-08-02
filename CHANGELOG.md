# Changelog

All notable changes to fsdantic are documented in this file.

## [Unreleased]

### Added

- **Overlay tombstones**: `workspace.overlay.tombstone(path)` removes a
  path from the workspace's own overlay and records a deletion intent in
  its KV store (`fsdantic:tombstone:<path>`).  `merge()` now replays the
  source's tombstones against the target filesystem within the merge scope
  (`MergeResult.tombstones_applied` reports the count), so a sandbox can
  delete files in a stable workspace it pushes into.  Markers are managed
  with `list_tombstones()`/`clear_tombstone()`/`clear_tombstones()`; they
  persist until cleared or the file is re-created in the source (which
  makes the marker inert).

## [0.6.0] - 2026-08-02

### Changed

- **agentfs-sdk is now consumed from the `Bullish-Design/agentfs` fork**
  (`git+https://github.com/Bullish-Design/agentfs@v0.6.4-pyturso-0.7.2`,
  subdirectory `sdk/python`) instead of the PyPI release.  The fork is
  upstream v0.6.4 code unchanged, with a single patch: its
  `pyturso==0.4.4` pin is bumped to `pyturso>=0.7.2,<0.8`.  fsdantic also
  declares `pyturso>=0.7.2,<0.8` explicitly so published installs resolve
  the tested driver.  (Upstream 0.6.4 still pins pyturso 0.4.4; the fork
  is the only way a coherent dependency graph can carry pyturso 0.7.2.)
- **pyturso is now 0.7.2**, which **releases the GIL during the contended
  busy-wait** (verified by probe: an in-process lock release from another
  connection unblocks a waiting writer and the event loop stays
  responsive).  The F2 caveat — "the event loop is frozen for up to
  `busy_timeout_ms`" — is removed from the `client` module docstring and
  `docs/concurrency.md`; `TestTwoWriters` gains
  `test_two_writers_waits_then_succeeds`, which orchestrates the "waits
  then succeeds" scenario that was impossible on pyturso 0.4.4.
- **`enable_mvcc=True` now actually enables libSQL MVCC journaling**
  (`PRAGMA journal_mode = "mvcc"` on pyturso >= 0.7.2) instead of silently
  opening in WAL mode (`experimental_features="mvcc"` was a no-op on every
  released pyturso; 0.4.4's libSQL build had no MVCC support at all and
  rejected `BEGIN CONCURRENT`).  The journal-mode switch happens AFTER SDK
  schema init, because the Limbo engine keeps mvcc-mode DDL in the
  in-memory MVCC store and it is not visible to other connections
  otherwise.  **Conflict-detection caveat (verified by probe):** pyturso's
  Python API opens an independent MVCC store per connection, so write-write
  conflicts are NOT reliably surfaced through the driver — concurrent
  same-row writes are effectively last-write-wins.  The previously
  documented "conflicting writes raise `DatabaseError` at execute time"
  contract is removed from the `client` module docstring and
  `docs/concurrency.md`; correctness relies on `Workspace.serialized()`
  and the repository's per-key SQL CAS.  `TestMVCCMode` now pins the real
  journal mode (`mvcc`) and that `BEGIN CONCURRENT` is accepted on both
  connections.
- Verified unchanged on 0.7.2: `connect()` signature (no `readonly`
  param), `PRAGMA query_only = ON` parse failure (`= 1` still required),
  CAS `rowcount` accuracy, the `fetchone()` read-transaction caveat, and
  the local libSQL file lock at `connect()` (still no multi-process
  access).  `UPDATE ... SET x = (subquery)` is now accepted (previously
  rejected on 0.4.4); `test_readonly.py`'s subquery workaround remains
  valid either way.

## [0.5.0] - 2026-08-01

### Added

- **Base-union `query`/`search`** — `FileManager.query(query,
  include_base=True)` and `search(pattern, include_base=True)` also query
  the base layer and return an overlay-wins union (base entries whose paths
  are absent from the overlay results are appended after the overlay
  entries).  Directory shadowing follows `list_dir` (an empty overlay
  directory does not shadow base content).  Default `include_base=False`
  preserves the overlay-only behavior. (F5)
- **`max_content_bytes` parameter** — `Fsdantic.open(...,
  max_content_bytes=N)` caps write payloads at the API boundary:
  `files.write`/`write_many` measure the encoded payload and `kv.set`/
  `set_many` measure the serialized JSON text.  Oversized payloads raise
  `WorkspaceError` (`CONTENT_TOO_LARGE`) before storage is touched;
  batch APIs report oversized items per-item.  `None` (default) is
  unbounded.  Exposed as `Workspace.max_content_bytes`. (F4)
- **`KVManager.increment(key, amount=1)`** — atomic counter increment that
  creates the key at 0 when absent, rejects non-numeric stored values with
  `SerializationError`, and returns the new value.  Same-process increments
  are serialized per key (no lost updates); the cross-process/MVCC race on
  the read-modify-write is documented (use the repository's per-key SQL CAS
  for that).  Rejected on read-only workspaces. (F3)
- **`busy_timeout_ms` parameter** — `Fsdantic.open(..., busy_timeout_ms=5000)`
  applies `PRAGMA busy_timeout = {ms}` on every connection created through
  the unified open seam (default 5000; `0` disables the wait).  Exposed as
  `Workspace.busy_timeout_ms`.  The MVCC/WAL conflict contract is documented
  in the `client` module docstring and `docs/concurrency.md`. (F2)
- **`Workspace.serialized()`** — a per-workspace `asyncio.Lock`
  asynccontextmanager primitive for same-process serialization of
  read-modify-write sequences.  Callers own the policy of when to use it. (F2)
- **`readonly` workspace mode** — `Fsdantic.open(..., readonly=True)` opens a
  workspace for read-only inspection.  Write operations raise
  `WorkspaceError` (`WORKSPACE_READONLY`) at the manager API boundary
  (`files.write`/`write_many`/`remove`, `kv.set`/`set_many`/`delete`/
  `delete_many`, `overlay.merge`/`reset`, `materialize.to_disk`) and at the
  raw connection level via a new connection guard (`_ReadonlyGuard`).
  Read-only reads perform **no** access-time UPDATE (the SDK's `atime`
  maintenance write is neutralized), and reads/`stat`/`list_dir`/`tree`/
  `exists`/`query`/`search` all work unchanged.  Opening a nonexistent
  database read-only raises `WorkspaceError` (`WORKSPACE_NOT_FOUND`).  (F1)
- **`WorkspaceError`** exception class with `default_code="WORKSPACE_ERROR"`
  and the `WORKSPACE_READONLY`/`WORKSPACE_NOT_FOUND` codes. (F1)

### Changed

- `Fsdantic.open` now owns connection creation in both the standard and MVCC
  paths (single unified seam): resolve path → `turso_connect` → WAL enable →
  `_ReadonlyGuard` wrap → `AgentFS.open_with`.  `open_with_options` remains
  SDK-direct and does not support `readonly` (documented). (F1)
- Documented the pyturso 0.4.4 busy-wait caveat: a contended async write
  holds the GIL for up to `busy_timeout_ms` (event loop frozen) before
  failing with "database is locked"; concurrent multi-process access to a
  DB file is not supported by the local libSQL build. (F2)
- `Workspace.connection` on readonly workspaces returns the connection guard:
  read PRAGMAs pass through, write statements are rejected. (F1)
- `Workspace`, `FileManager`, `KVManager`, `OverlayManager`, and
  `MaterializationManager` expose a `readonly` flag; child KV namespaces
  inherit it. (F1)

## [0.4.0] - 2026-08-01

Behavioral refactor driven by an adversarial code review (see
`.scratch/projects/016-fsdantic-refactor/` for the full inventory and
reproductions).

### Breaking changes

- **`TypedKVRepository.save` (versioned records)** now performs an atomic
  SQL compare-and-set. Concurrent writers can no longer silently lose an
  update; the losing writer raises `KVConflictError`. `created_at` is
  preserved across update-style saves. (C1, M1)
- **`save_many`** now routes every item through `save`, inheriting version
  checks, increments, and CAS conflict detection. Per-item conflicts are
  reported in `BatchResult`. Batch *updates* require loaded records (or
  fresh records matching the stored version). (M2)
- **`Materializer.diff`/`preview`** now report base-only files with
  `change_type="deleted"`. This is a *visibility delta*: materialize copies
  base first, so base-only files still reach the output tree. (H3)
- **`MergeStrategy.CALLBACK`** without a conflict resolver now raises
  `OverlayError` at the conflict site instead of silently overwriting.
  `OverlayOperations.merge` and `OverlayManager.merge` accept a per-call
  `conflict_resolver`. (M4)
- **`reset_overlay`** raises `OverlayError` (with `context={"failed": [...]}`)
  on partial failure instead of a bare `RuntimeError`. (L2)
- **`ToolCall.duration_ms`** is now a plain field (a single serialized key)
  rather than a computed field aliased with `explicit_duration_ms`. The
  value is derived from `started_at`/`completed_at` at construction when not
  explicitly provided. (L4)

### Behavior changes

- `materialize(filters=...)` is honored for files in both the base and
  overlay layers; directories are always descended into so nested matches
  are found; size constraints are applied via the stat pass. (H1)
- `compare_streams` is chunk-boundary independent (identical content split
  differently compares equal). (H2)
- `FileManager.list_dir` falls back to base on `ENOENT` or an empty overlay
  listing; overlay wins when it has entries — consistent with
  `read`/`stat`/`exists`. (M8)
- Missing-key KV operations (`get`/`delete`/`exists`) use an O(1) existence
  check instead of an O(n) prefix scan. (M5)
- Raw `KVManager` values support `datetime`, `bytes`, `Enum`, `Path`, `UUID`,
  and `set` via a JSON normalization pass. Typed repositories round-trip
  these losslessly. Raw `get` returns the JSON-native form (documented
  asymmetry). (M6)
- The MVCC open path validates agent IDs exactly like the non-MVCC path and
  strips whitespace-padded selectors. (M7)
- `View.search_content` no longer mutates the shared query. (M3)
- `read_many`/`get_many` accept `concurrency_limit` (default 10), matching
  the write paths. (L1)
- `progress_callback` receives a real total (when enumerable) and change
  labels distinguish `added` vs `modified`. (L14)
- Orphaned `.bak-*`/`.tmp-*` staging siblings are recovered or cleaned at
  the start of `materialize`. (L13)
- `FileOperations` is exported in `fsdantic.__all__`. (L15)

### Fixes

- `diff` compares equal-size files with a single byte-accurate pass (no
  redundant hash + compare double-read). (L5/L8)
- Invalid `regex_pattern` surfaces as `ValidationError` instead of leaking
  `re.error`. (L3)
- Hypothesis property tests no longer fail on the 200 ms deadline under
  load; benchmark thresholds are environment-tolerant outside strict mode.
  (L18)
- Version drift in the public API contract test resolved. (L17)
- Module docstring restored in `overlay.py`; ruff clean across `src/`. (L10,
  L16)
