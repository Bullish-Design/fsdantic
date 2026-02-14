# Migration Reference: Legacy API → Workspace-First API

This document is a **concept translation reference** for teams moving from older fsdantic usage to the workspace-first architecture.

It is **not** a compatibility promise and does not imply adapters/shims exist.
The recommended migration strategy is to rewrite call sites to the modern `Fsdantic.open(...) -> Workspace` model.

## New baseline

```python
from fsdantic import Fsdantic

async with await Fsdantic.open(id="my-agent") as workspace:
    ...
```

---

## 1) Entry point and wiring

### Old

```python
from agentfs_sdk import AgentFS
from fsdantic import AgentFSOptions, FileManager, View, ViewQuery

options = AgentFSOptions(id="my-agent")
agent = await AgentFS.open(options.model_dump())
ops = FileManager(agent)
view = View(agent=agent, query=ViewQuery(path_pattern="*.py"))
```

### New

```python
from fsdantic import Fsdantic, ViewQuery

async with await Fsdantic.open(id="my-agent") as workspace:
    await workspace.files.write("/hello.txt", "hi")
    files = await workspace.files.query(ViewQuery(path_pattern="*.py"))
```

---

## 2) Filesystem operations

### Old

```python
from fsdantic import FileOperations

ops = FileOperations(agent)
await ops.write_file("config.json", '{"debug": true}')
text = await ops.read_file("config.json")
```

### New

```python
await workspace.files.write("/config.json", {"debug": True}, mode="json")
text = await workspace.files.read("/config.json")
exists = await workspace.files.exists("/config.json")
stats = await workspace.files.stat("/config.json")
paths = await workspace.files.search("**/*.json")
await workspace.files.remove("/config.json")
```

---

## 3) Querying files

### Old

```python
from fsdantic import View, ViewQuery

view = View(agent=agent, query=ViewQuery(path_pattern="*.md", include_content=True))
entries = await view.load()
count = await view.count()
```

### New

```python
from fsdantic import ViewQuery

query = ViewQuery(path_pattern="*.md", include_content=True)
entries = await workspace.files.query(query)
count = await workspace.files.count(query)
```

Use `workspace.files.query/count` as the default path. Keep `View` for advanced/legacy patterns only.

---

## 4) KV operations

### Old

```python
await agent.kv.set("user:1", {"name": "Alice"})
user = await agent.kv.get("user:1")
```

### New

```python
await workspace.kv.set("user:1", {"name": "Alice"})
user = await workspace.kv.get("user:1")
items = await workspace.kv.list(prefix="user:")
await workspace.kv.delete("user:1")
```

### Typed repository migration

```python
from pydantic import BaseModel

class User(BaseModel):
    name: str

repo = workspace.kv.repository(prefix="user:", model_type=User)
await repo.save("1", User(name="Alice"))
alice = await repo.load("1")
```

---

## 5) Overlay operations

### Old

```python
from fsdantic import OverlayOperations, MergeStrategy

ops = OverlayOperations(strategy=MergeStrategy.OVERWRITE)
result = await ops.merge(source=source_agent, target=target_agent)
```

### New

```python
from fsdantic import MergeStrategy

result = await target_workspace.overlay.merge(source_workspace, strategy=MergeStrategy.OVERWRITE)
changes = await target_workspace.overlay.list_changes("/")
reset_count = await target_workspace.overlay.reset()
```

---

## 6) Materialization

### Old

```python
from fsdantic import Materializer

materializer = Materializer()
result = await materializer.materialize(agent_fs=agent, target_path=Path("./out"))
```

### New

```python
result = await workspace.materialize.to_disk(Path("./out"), clean=True)
preview = await workspace.materialize.preview(base_workspace)
diff = await workspace.materialize.diff(base_workspace)
```

---

## 7) Error handling migration

Catch fsdantic exceptions at service boundaries and map them to your app semantics.

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

try:
    data = await workspace.files.read("/missing.txt")
except FileNotFoundError:
    data = ""
except InvalidPathError:
    raise

try:
    timezone = await workspace.kv.get("settings:timezone")
except KeyNotFoundError:
    timezone = "UTC"
except SerializationError:
    raise
except KVStoreError:
    raise

merge = await workspace.overlay.merge(other_workspace, strategy=MergeStrategy.ERROR)
if merge.errors:
    raise RuntimeError(f"merge failed: {merge.errors}")

try:
    await workspace.files.remove("/tmp", recursive=False)
except DirectoryNotEmptyError:
    await workspace.files.remove("/tmp", recursive=True)
```

---

## Recommended rewrite order

1. Replace direct `AgentFS.open(...)` wiring with `Fsdantic.open(...)`.
2. Move file/KV operations behind workspace managers.
3. Replace ad-hoc query flows with `workspace.files.query/count`.
4. Migrate overlay/materialization usage to workspace managers.
5. Normalize boundary-level exception handling.
6. Keep `workspace.raw` only where high-level managers do not cover your use case.
