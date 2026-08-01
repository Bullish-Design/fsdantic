# Fsdantic

Fsdantic is a workspace-first, async Python library for building on top of the AgentFS SDK.

The primary API is:

- `Fsdantic.open(...)` → `Workspace`
- `workspace.files` for filesystem workflows
- `workspace.kv` for key/value workflows
- `workspace.overlay` for merge/reset workflows
- `workspace.materialize` for diff/preview/export workflows
- `workspace.raw` for direct AgentFS escape hatches

## Installation

```bash
uv add fsdantic
# or
pip install fsdantic
```

## Quickstart (workspace lifecycle)

```python
import asyncio
from fsdantic import Fsdantic


async def main() -> None:
    async with await Fsdantic.open(id="my-agent") as workspace:
        await workspace.files.write("/hello.txt", "Hello from fsdantic")
        text = await workspace.files.read("/hello.txt")
        print(text)


asyncio.run(main())
```

Use `async with` so the workspace is always closed cleanly.

---

## Core flows

### 1) File operations

```python
from fsdantic import ViewQuery

# write/read (text)
await workspace.files.write("/docs/readme.txt", "hello")
text = await workspace.files.read("/docs/readme.txt", mode="text")

# write/read (binary)
await workspace.files.write("/blob.bin", b"\x00\x01", mode="binary")
data = await workspace.files.read("/blob.bin", mode="binary", encoding=None)

# write/read JSON
await workspace.files.write("/config.json", {"debug": True}, mode="json")
config_text = await workspace.files.read("/config.json")

# existence + stat
exists = await workspace.files.exists("/config.json")
stats = await workspace.files.stat("/config.json")
print(stats.size, stats.is_file)

# list directory (falls back to base layer when configured, 0.4.0)
names = await workspace.files.list_dir("/docs", output="name")
full_paths = await workspace.files.list_dir("/docs", output="full")

# quick glob search
python_paths = await workspace.files.search("**/*.py")

# advanced query
entries = await workspace.files.query(
    ViewQuery(
        path_pattern="**/*.md",
        include_stats=True,
        include_content=False,
        min_size=10,
    )
)

# remove file or directory
await workspace.files.remove("/docs/readme.txt")
await workspace.files.remove("/docs", recursive=True)
```

### 2) KV operations

```python
from pydantic import BaseModel


class User(BaseModel):
    name: str
    role: str


# simple key/value
await workspace.kv.set("app:theme", "dark")
theme = await workspace.kv.get("app:theme")
items = await workspace.kv.list(prefix="app:")
await workspace.kv.delete("app:theme")

# namespaced KV manager
users_kv = workspace.kv.namespace("users")
await users_kv.set("alice", {"name": "Alice", "role": "admin"})
alice_raw = await users_kv.get("alice")

# typed repository
repo = workspace.kv.repository(prefix="users:", model_type=User)
await repo.save("bob", User(name="Bob", role="dev"))
bob = await repo.load("bob")
all_users = await repo.list_all()

# grouped operations with best-effort transaction semantics
async with workspace.kv.transaction() as txn:
    await txn.set("users:count", 42)
    await txn.delete("users:legacy")

# optimistic concurrency (additive APIs)
await repo.compare_and_set("bob", User(name="Bob", role="lead"), etag="1")

# versioned records: atomic CAS, no lost updates, created_at preserved
from fsdantic import VersionedKVRecord


class Config(VersionedKVRecord):
    settings: dict


config_repo = workspace.kv.repository(prefix="config:", model_type=Config)
await config_repo.save("app", Config(settings={"theme": "dark"}))
loaded = await config_repo.load("app")
loaded.settings["theme"] = "light"
await config_repo.save("app", loaded)  # stored version increments; created_at unchanged
```

**Versioned save semantics (0.4.0):**

- `save()` on a `VersionedKVRecord` uses atomic SQL compare-and-set. Two
  concurrent savers cannot silently lose an update — the losing writer raises
  `KVConflictError`. Use `expected_version`/`etag` for explicit optimistic
  concurrency.
- `save_many()` routes every item through `save()`, so versioned records
  inherit version checks, increments, and per-item conflict reporting in the
  returned `BatchResult`. Batch *updates* require loaded records (or fresh
  records matching the stored version).
- `load()` returns `None` for a stored JSON `null`; this is indistinguishable
  from a missing key — use `exists()` to disambiguate.

**KV serialization (0.4.0):** raw values may contain datetimes, bytes, enums,
`Path`, `UUID`, and sets — they are normalized to JSON-native forms before
storage (datetimes become ISO-8601 strings, bytes become a
`{"$fsdantic:bytes": "<base64>"}` marker). Typed repositories round-trip these
losslessly; raw `get()` returns the JSON-native form.

### 2.5) Batch APIs (deterministic ordering + partial failures)

```python
# file batch reads
file_reads = await workspace.files.read_many(["/a.txt", "/missing.txt", "/b.txt"])
for item in file_reads.items:  # item.index preserves caller order
    if item.ok:
        print(item.key_or_path, item.value)
    else:
        print("read failed", item.key_or_path, item.error)

# bounded fan-out writes
write_result = await workspace.files.write_many(
    [("/out-1.txt", "one"), ("/out-2.txt", "two")],
    concurrency_limit=5,
)

# KV + repository batch APIs
kv_result = await workspace.kv.get_many(["settings:theme", "settings:tz"], default="UTC")
repo_result = await repo.load_many(["alice", "bob"], default=None)
```

Batch APIs return per-item outcomes instead of all-or-nothing behavior.
Successful and failed items are returned together in input order, so callers can
retry only failed items by filtering ``result.items`` where ``ok`` is ``False``.


### 2.6) KV consistency guarantees: atomic vs best-effort

- **Single-key KV writes** (`set`, `delete`, repository `save`) are atomic at the
  backend key level.
- **Grouped writes via `KVTransaction`** are **best-effort**, not fully atomic:
  operations are staged in memory, then committed in order on context exit.
  If a later write fails, fsdantic attempts rollback of already-applied writes.
  Rollback can also fail, so callers should treat grouped commits as
  compensation-based and idempotency-friendly.
- **Optimistic concurrency** for versioned records is enforced with atomic
  SQL compare-and-set: concurrent writers raise `KVConflictError` instead of
  silently losing an update. `expected_version`/`etag` checks and additive
  methods (`save_if_version`, `compare_and_set`) remain available, and
  `save_many` inherits the same semantics.
  Conflicts raise `KVConflictError` with `code`, `key`, `expected_version`, and
  `actual_version` for machine-readable handling.
- **Missing-key operations are O(1):** `get`/`delete`/`exists` use a direct
  existence check instead of an O(n) prefix scan.

### 3) Overlay operations

```python
from fsdantic import Fsdantic, MergeStrategy

async with await Fsdantic.open(id="source-agent") as source:
    await source.files.write("/shared/file.txt", "source version")

    async with await Fsdantic.open(id="target-agent") as target:
        result = await target.overlay.merge(source, strategy=MergeStrategy.OVERWRITE)
        print("merged", result.files_merged)

        if result.conflicts:
            print("conflicts:", [c.path for c in result.conflicts])

        if result.errors:
            print("errors:", result.errors)

        changed_paths = await target.overlay.list_changes("/")
        print("overlay paths", changed_paths)

        removed_count = await target.overlay.reset(paths=["/shared/file.txt"])
        print("reset paths", removed_count)
```

Merge/overlay notes (0.4.0):

- `MergeStrategy.CALLBACK` **requires** a `conflict_resolver`. Without one, a
  conflict raises `OverlayError` (no silent overwrite). Pass the resolver
  per-call: `await target.overlay.merge(source, strategy=MergeStrategy.CALLBACK,
  conflict_resolver=my_resolver)`.
- `reset_overlay()` raises `OverlayError` (with `context={"failed": [...]}`)
  on partial failure instead of a bare `RuntimeError`.

### 4) Materialization (preview/diff/export)

`clean=True` now uses a guarded staging workflow: files are materialized into a temporary sibling directory and only promoted to `target_path` after a successful run. This avoids partial output in the final target on failures.

Safety semantics:
- Rejects dangerous targets (for example filesystem roots).
- Enforces an allow-root boundary (`allow_root`) so resolved targets cannot escape a trusted directory.
- Uses rename/swap semantics when supported by the filesystem.
- Falls back to non-atomic move on cross-device rename (`EXDEV`) and records cleanup issues in `MaterializationResult.errors`.

Recovery behavior:
- On failures before promotion, the previous target remains untouched.
- Temporary staging paths are cleaned up best-effort; cleanup failures are surfaced in `result.errors`.
- Orphaned `.bak-*`/`.tmp-*` siblings from a crashed swap are recovered or
  cleaned at the start of the next run.

Filter and diff notes (0.4.0):
- `materialize(..., filters=ViewQuery(...))` is honored for files in both the
  base and overlay layers; directories are always descended into, so nested
  matches are found. Size constraints are applied via the stat pass.
- `diff()`/`preview()` report base-only files with `change_type="deleted"`.
  This is a *visibility delta*: materialize copies base first, so base-only
  files still reach the output tree.


```python
from pathlib import Path
from fsdantic import Fsdantic

async with await Fsdantic.open(id="base-agent") as base:
    await base.files.write("/a.txt", "base")

    async with await Fsdantic.open(id="overlay-agent") as overlay:
        await overlay.files.write("/a.txt", "overlay")
        await overlay.files.write("/b.txt", "new")

        # preview and diff are equivalent in current API
        preview = await overlay.materialize.preview(base)
        diff = await overlay.materialize.diff(base)
        print([(change.change_type, change.path) for change in diff])

        result = await overlay.materialize.to_disk(
            Path("./materialized"),
            base=base,
            clean=True,
            allow_root=Path("./"),
        )

        print(result.files_written, result.bytes_written)
        if result.errors:
            print("materialization errors", result.errors)
```

---

## Error handling patterns

Catch fsdantic exceptions at API boundaries, recover where sensible, and re-raise when the caller should decide.

```python
from fsdantic import (
    DirectoryNotEmptyError,
    FileNotFoundError,
    InvalidPathError,
    KeyNotFoundError,
    KVStoreError,
    MergeStrategy,
    SerializationError,
)

# Not found + invalid path
try:
    content = await workspace.files.read("/missing.txt")
except FileNotFoundError:
    content = ""  # recover with default
except InvalidPathError:
    raise  # caller provided invalid input; propagate

# Reading a directory path is normalized to file-not-found for compatibility
try:
    await workspace.files.read("/some-directory")
except FileNotFoundError:
    pass

# Directory removal policy
try:
    await workspace.files.remove("/tmp", recursive=False)
except DirectoryNotEmptyError:
    await workspace.files.remove("/tmp", recursive=True)

# KV missing key
try:
    value = await workspace.kv.get("settings:timezone")
except KeyNotFoundError:
    value = "UTC"
except SerializationError:
    raise  # stored data is malformed for expected usage
except KVStoreError:
    raise  # infrastructure/storage failure

# Merge conflict/error handling
merge = await workspace.overlay.merge(other_workspace, strategy=MergeStrategy.ERROR)
if merge.errors:
    raise RuntimeError(f"merge failed: {merge.errors}")
```

---

## Raw AgentFS access (escape hatch)

Prefer managers first. Use `workspace.raw` only for low-level SDK features not exposed by fsdantic.

```python
stat = await workspace.raw.fs.stat("/hello.txt")
entries = await workspace.raw.kv.list(prefix="app:")
```

## API summary

- Entry point: `Fsdantic.open(...)`
- Workspace managers: `files`, `kv`, `overlay`, `materialize`
- Typed models: `FileEntry`, `FileStats`, `KVRecord`, `VersionedKVRecord`, etc.
- Advanced querying: `View`, `ViewQuery`, `FileQuery` (all available via `from fsdantic import ...`)
