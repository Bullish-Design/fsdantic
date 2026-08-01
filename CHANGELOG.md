# Changelog

All notable changes to fsdantic are documented in this file.

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
