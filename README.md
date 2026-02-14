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

# list directory
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
```

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

### 4) Materialization (preview/diff/export)

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
        print([change.path for change in diff])

        result = await overlay.materialize.to_disk(
            Path("./materialized"),
            base=base,
            clean=True,
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
- Advanced querying: `View`, `ViewQuery` (or `FileQuery` from `fsdantic.files`)
