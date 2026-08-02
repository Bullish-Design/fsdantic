# Changelog

All notable changes to fsdantic are documented in this file.

## Unreleased

### Added

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
