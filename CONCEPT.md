# Fsdantic: Conceptual Overview

**Purpose:** This document explains the core concepts and design philosophy behind Fsdantic.

---

## What is Fsdantic?

Fsdantic is a **Pydantic-based interface layer** for the AgentFS SDK. It transforms AgentFS's low-level filesystem and KV store APIs into high-level, type-safe, and developer-friendly abstractions.

### The Problem

AgentFS provides powerful virtual filesystem capabilities, but working directly with its API involves:

1. **Repetitive Boilerplate** - Manual serialization/deserialization, error handling, path management
2. **No Type Safety** - Raw dictionaries and strings without validation
3. **Low-Level Operations** - Recursive directory traversal, file copying, overlay merging
4. **Pattern Duplication** - Common patterns reimplemented in every project

### The Solution

Fsdantic addresses these issues by providing:

1. **Type-Safe Models** - Pydantic models with automatic validation
2. **High-Level Abstractions** - Repository pattern, query builders, file operations
3. **Reusable Patterns** - Materialization, overlay merging, content search
4. **Developer Experience** - Fluent APIs, clear error messages, comprehensive documentation

---

## Core Concepts

### 1. Type Safety Through Pydantic

Every AgentFS concept is represented as a Pydantic model:

```python
# Instead of raw dicts:
user_data = {"user_id": "alice", "name": "Alice"}

# Use validated models:
user = UserRecord(user_id="alice", name="Alice")  # Validates at runtime
```

**Benefits:**
- Automatic validation
- IDE autocomplete
- Clear error messages
- Self-documenting code

### 2. Repository Pattern for KV Store

The repository pattern abstracts KV store operations:

```python
# Instead of manual serialization:
await kv.set("user:alice", json.dumps(user_data))
data = await kv.get("user:alice")
user = UserRecord(**json.loads(data))

# Use typed repositories:
repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")
await repo.save("alice", user)
user = await repo.load("alice", UserRecord)  # Type-safe!
```

**Benefits:**
- Automatic serialization/deserialization
- Type-safe operations
- Namespace management
- Reduced boilerplate

### 3. Query-Based Filesystem Access

The View abstraction provides a query interface for filesystems:

```python
# Instead of recursive traversal:
def find_python_files(path):
    results = []
    for entry in os.listdir(path):
        full_path = os.path.join(path, entry)
        if os.path.isdir(full_path):
            results.extend(find_python_files(full_path))
        elif entry.endswith('.py'):
            results.append(full_path)
    return results

# Use declarative queries:
view = View(
    agent=agent_fs,
    query=ViewQuery(
        path_pattern="**/*.py",
        include_stats=True
    )
)
files = await view.load()
```

**Benefits:**
- Declarative vs imperative
- Built-in filtering (size, pattern, regex)
- Fluent API for composition
- Efficient implementation

### 4. Content Search

Content search extends the query interface:

```python
# Search for all TODOs in Python files
view = View(
    agent=agent_fs,
    query=ViewQuery(
        path_pattern="**/*.py",
        content_pattern="TODO",
        case_sensitive=False
    )
)
matches = await view.search_content()

for match in matches:
    print(f"{match.file}:{match.line}: {match.text}")
```

**Benefits:**
- Regex and simple pattern support
- Line number tracking
- Binary file handling
- Match limiting

### 5. Workspace Materialization

Materialization converts virtual filesystems to disk:

```python
# Materialize agent workspace to local disk
materializer = Materializer()
result = await materializer.materialize(
    agent_fs=agent,
    target_path=Path("./workspace"),
    base_fs=stable,
    clean=True,
    allow_root=Path("./")
)

print(f"Files: {result.files_written}, Bytes: {result.bytes_written}")
print(f"Errors: {len(result.errors)}")
```

**Safety & recovery:** With `clean=True`, materialization first writes into a sibling staging directory and only swaps into `target_path` after success. Invalid/dangerous targets are rejected, staged artifacts are cleaned up best-effort, and any cleanup issues are reported via `MaterializationResult.errors`. On cross-device renames, fsdantic falls back to non-atomic move semantics.

**Benefits:**
- Base + overlay layering
- Conflict resolution strategies
- Progress tracking
- Diff computation

### 6. Overlay Operations

Overlay operations manage AgentFS overlays:

```python
# Merge agent changes into stable
ops = OverlayOperations(strategy=MergeStrategy.OVERWRITE)
result = await ops.merge(source=agent_fs, target=stable_fs)

# List changes in overlay
changes = await ops.list_changes(agent_fs)

# Reset overlay to base state
removed = await ops.reset_overlay(agent_fs)
```

**Benefits:**
- Multiple merge strategies
- Conflict detection
- Change tracking
- Rollback capability

### 7. File Operations with Fallthrough

FileOperations provides a unified interface with automatic fallthrough:

```python
ops = FileOperations(agent_fs, base_fs=stable_fs)

# Read from overlay, fallthrough to base
content = await ops.read_file("config.json")

# Write to overlay only
await ops.write_file("output.txt", "Hello")

# Search across both layers
files = await ops.search_files("**/*.py")
```

**Benefits:**
- Transparent overlay handling
- Automatic fallthrough
- Simplified API
- Consistent error handling

---

## Design Principles

### 1. **Developer Experience First**

Fsdantic prioritizes ease of use:
- Clear, intuitive APIs
- Fluent interfaces for composition
- Comprehensive documentation
- Helpful error messages

### 2. **Type Safety Without Compromise**

Every operation is type-safe:
- Pydantic models for validation
- Generic types for repositories
- Strong typing throughout
- IDE autocomplete support

### 3. **Layered Abstractions**

Multiple abstraction levels:
- **Low-level**: Direct AgentFS SDK access
- **Mid-level**: Models, View, Repository
- **High-level**: Materializer, OverlayOperations, FileOperations

Users can choose their level based on needs.

### 4. **Composability**

Components work together seamlessly:
```python
# Compose View with FileOperations
view = View(agent=ops.agent_fs, query=ViewQuery(path_pattern="*.py"))
files = await view.load()

# Compose Repository with custom models
class UserRecord(KVRecord):  # Inherits timestamp tracking
    user_id: str
    name: str

repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")
```

### 5. **Extensibility**

Easy to extend with custom functionality:
- Inherit from `KVRecord` for custom base classes
- Implement `ConflictResolver` for custom merge logic
- Use `View.filter()` for custom predicates
- Extend models with computed fields

---

## Use Cases

### 1. **Agent Workspace Management**

Manage agent workspaces with materialization:
```python
# Materialize workspace for agent execution
result = await materializer.materialize(
    agent_fs=agent,
    target_path=workspace_dir / agent_id,
    base_fs=stable
)

# Agent runs in workspace_dir/agent_id

# Merge accepted changes back to stable
await ops.merge(source=agent, target=stable)
```

### 2. **Configuration Management**

Store configuration with type safety:
```python
class AppConfig(VersionedKVRecord):
    theme: str
    debug: bool
    settings: dict

config_repo = TypedKVRepository[AppConfig](agent_fs, prefix="config:")
await config_repo.save("app", AppConfig(theme="dark", debug=False, settings={}))

# Later...
config = await config_repo.load("app", AppConfig)
config.theme = "light"
config.increment_version()  # Auto-updates timestamp and version
await config_repo.save("app", config)
```

### 3. **Code Search and Analysis**

Search codebases efficiently:
```python
# Find all class definitions
view = View(
    agent=agent_fs,
    query=ViewQuery(
        path_pattern="**/*.py",
        content_regex=r"class\s+(\w+):",
        include_content=True
    )
)
matches = await view.search_content()

# Group files by extension
grouped = await view.group_by_extension()
print(f"Python files: {len(grouped['.py'])}")

# Find largest files
large = await view.largest_files(10)
```

### 4. **Data Migration**

Migrate data between AgentFS instances:
```python
# Export from source
source_repo = TypedKVRepository[Record](source_fs, prefix="data:")
records = await source_repo.list_all(Record)

# Import to target
target_repo = TypedKVRepository[Record](target_fs, prefix="data:")
for record in records:
    await target_repo.save(record.id, record)
```

### 5. **Testing and Mocking**

Test with in-memory filesystems:
```python
# Create test AgentFS
test_fs = await AgentFS.open(AgentFSOptions(id="test").model_dump())

# Populate with test data
ops = FileOperations(test_fs)
await ops.write_file("test.txt", "Test content")
await ops.write_file("data.json", json.dumps({"key": "value"}))

# Test code
result = await process_files(test_fs)
assert result.success
```

---

## Architecture Patterns

### Pattern 1: Layered Repository

```python
class BaseRecord(KVRecord):
    """Base for all records"""
    pass

class UserRecord(BaseRecord):
    user_id: str
    name: str

class AgentRecord(BaseRecord):
    agent_id: str
    status: str

class AppRepository:
    def __init__(self, agent_fs: AgentFS):
        self.users = TypedKVRepository[UserRecord](agent_fs, prefix="user:")
        self.agents = TypedKVRepository[AgentRecord](agent_fs, prefix="agent:")
```

### Pattern 2: Query Composition

```python
# Build complex queries through composition
base_view = View(agent=agent_fs, query=ViewQuery())

python_files = base_view.with_pattern("**/*.py")
recent_python = python_files.recent_files(timedelta(days=7))
large_recent_python = python_files.with_size_range(min_size=10000)
```

### Pattern 3: Multi-Stage Processing

```python
# Stage 1: Query
view = View(agent=agent_fs, query=ViewQuery(path_pattern="**/*.py"))
files = await view.load()

# Stage 2: Filter
large_files = [f for f in files if f.stats.size > 10000]

# Stage 3: Search content
view_with_search = view.with_content(True)
matches = await view_with_search.search_content()

# Stage 4: Materialize results
materializer = Materializer()
await materializer.materialize(agent_fs, output_dir)
```

---

## Integration with AgentFS

### AgentFS Concepts

1. **Virtual Filesystem** - SQLite-backed POSIX-like filesystem
2. **Overlays** - Copy-on-write layers for isolation
3. **KV Store** - Key-value store for metadata
4. **Tool Calls** - Audit trail for function calls

### Fsdantic Mapping

| AgentFS Concept | Fsdantic Abstraction |
|-----------------|----------------------|
| Filesystem | View, FileOperations |
| Overlays | OverlayOperations, Materializer |
| KV Store | TypedKVRepository |
| Tool Calls | ToolCall model |

### Interaction Model

```
┌─────────────────────────────────────┐
│         User Application            │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│           Fsdantic                  │
│  ┌──────────┐  ┌────────────────┐  │
│  │  Models  │  │   Repository   │  │
│  └──────────┘  └────────────────┘  │
│  ┌──────────┐  ┌────────────────┐  │
│  │   View   │  │  Operations    │  │
│  └──────────┘  └────────────────┘  │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│         AgentFS SDK                 │
│  ┌──────────┐  ┌────────────────┐  │
│  │    FS    │  │     KV Store   │  │
│  └──────────┘  └────────────────┘  │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│         SQLite Database             │
└─────────────────────────────────────┘
```

---

## Comparison with Direct AgentFS Usage

### Before Fsdantic

```python
# Manual serialization
user_data = {"name": "Alice", "age": 30}
await agent_fs.kv.set("user:alice", json.dumps(user_data))

# Manual deserialization
data = await agent_fs.kv.get("user:alice")
user = json.loads(data) if data else None

# Manual recursion
async def find_files(path, pattern):
    results = []
    entries = await agent_fs.fs.readdir(path)
    for entry in entries:
        full = f"{path}/{entry}"
        stat = await agent_fs.fs.stat(full)
        if stat.is_directory():
            results.extend(await find_files(full, pattern))
        elif fnmatch(entry, pattern):
            results.append(full)
    return results
```

### With Fsdantic

```python
# Type-safe repository
class User(BaseModel):
    name: str
    age: int

repo = TypedKVRepository[User](agent_fs, prefix="user:")
await repo.save("alice", User(name="Alice", age=30))
user = await repo.load("alice", User)

# Declarative queries
view = View(agent=agent_fs, query=ViewQuery(path_pattern="*.txt"))
files = await view.load()
```

**Benefits:**
- 70% less code
- Type safety
- Clearer intent
- Better error messages
- Easier to test

---

## Summary

Fsdantic transforms AgentFS from a low-level filesystem SDK into a high-level, type-safe, developer-friendly platform. It achieves this through:

1. **Pydantic Models** - Type safety and validation
2. **Repository Pattern** - Clean KV operations
3. **Query Interface** - Declarative filesystem access
4. **High-Level Operations** - Materialization, merging, file operations
5. **Composability** - Components work together seamlessly
6. **Extensibility** - Easy to customize and extend

The result is a library that reduces boilerplate, improves code quality, and accelerates development of AgentFS-based applications.
