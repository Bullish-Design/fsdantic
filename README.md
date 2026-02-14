# Fsdantic

A comprehensive Pydantic-based interface library for the [AgentFS SDK](https://docs.turso.tech/agentfs/python-sdk). Fsdantic provides type-safe models, high-level abstractions, and powerful utilities for working with AgentFS virtual filesystems.

## Features

- 🎯 **Type-safe models** - Pydantic models for all core AgentFS objects
- 🔍 **Query interface** - Powerful `View` system for querying filesystem with filters and content search
- 📦 **Repository pattern** - Generic typed repositories for KV store operations
- 💾 **Workspace materialization** - Convert virtual filesystems to disk with conflict resolution
- 🔄 **Overlay operations** - High-level overlay merging and management
- 📁 **File operations** - Simplified file I/O with automatic base layer fallthrough
- 🔎 **Content search** - Regex and pattern-based file content searching
- ✅ **Validation** - Automatic validation of options and data structures
- 🚀 **Async-first** - Built on async/await for optimal performance
- 📖 **Well documented** - Comprehensive documentation and examples

## Installation

```bash
uv add fsdantic
```

Or with pip:

```bash
pip install fsdantic
```

## Quick Start

```python
import asyncio
from agentfs_sdk import AgentFS
from fsdantic import AgentFSOptions, View, ViewQuery, FileOperations

async def main():
    # Create validated options
    options = AgentFSOptions(id="my-agent")

    async with await AgentFS.open(options.model_dump()) as agent:
        # Simple file operations
        ops = FileOperations(agent)
        await ops.write_file("hello.txt", "Hello, World!")
        content = await ops.read_file("hello.txt")

        # Create a view to query the filesystem
        view = View(
            agent=agent,
            query=ViewQuery(
                path_pattern="*.py",
                recursive=True,
                include_content=True
            )
        )

        # Load all matching Python files
        python_files = await view.load()
        for file in python_files:
            print(f"{file.path}: {file.stats.size} bytes")

asyncio.run(main())
```

## Core Models

### AgentFSOptions

Validated options for opening an AgentFS filesystem:

```python
from fsdantic import AgentFSOptions

# By agent ID
options = AgentFSOptions(id="my-agent")

# By custom path
options = AgentFSOptions(path="./data/mydb.db")
```

### FileEntry

Represents a file in the filesystem with optional stats and content:

```python
from fsdantic import FileEntry, FileStats

entry = FileEntry(
    path="/notes/todo.txt",
    stats=FileStats(
        size=1024,
        mtime=datetime.now(),
        is_file=True,
        is_directory=False
    ),
    content="Task 1\nTask 2"
)
```

### ToolCall & ToolCallStats

Type-safe models for tracking tool/function calls:

```python
from fsdantic import ToolCall, ToolCallStats

call = ToolCall(
    id=1,
    name="search",
    parameters={"query": "Python"},
    result={"results": ["result1", "result2"]},
    status="success",
    started_at=datetime.now(),
    completed_at=datetime.now()
)

stats = ToolCallStats(
    name="search",
    total_calls=100,
    successful=95,
    failed=5,
    avg_duration_ms=123.45
)
```

## View Query System

The `View` class provides a powerful interface for querying the AgentFS filesystem.

### Basic Queries

```python
from fsdantic import View, ViewQuery

# Query all files recursively
view = View(agent=agent, query=ViewQuery(path_pattern="*", recursive=True))
all_files = await view.load()

# Query specific file types
md_view = View(
    agent=agent,
    query=ViewQuery(
        path_pattern="*.md",
        recursive=True,
        include_content=True
    )
)
markdown_files = await md_view.load()
```

### Size Filters

```python
# Files larger than 1KB
large_files = View(
    agent=agent,
    query=ViewQuery(
        path_pattern="*",
        recursive=True,
        min_size=1024
    )
)

# Files smaller than 100KB
small_files = View(
    agent=agent,
    query=ViewQuery(
        path_pattern="*",
        recursive=True,
        max_size=102400
    )
)
```

### Regex Patterns

For more complex matching, use regex patterns:

```python
# Match files in specific directory
notes_view = View(
    agent=agent,
    query=ViewQuery(
        path_pattern="*",
        recursive=True,
        regex_pattern=r"^/notes/"
    )
)
```

### Fluent API

Chain view modifications for cleaner code:

```python
# Query JSON files with content
json_files = await (
    View(agent=agent)
    .with_pattern("*.json")
    .with_content(True)
    .load()
)

# Query without content (faster)
file_list = await (
    View(agent=agent)
    .with_pattern("*.py")
    .with_content(False)
    .load()
)
```

### Custom Filters

Use predicates for advanced filtering:

```python
# Files modified today
from datetime import datetime

today = datetime.now().date()
recent_files = await view.filter(
    lambda f: f.stats and f.stats.mtime.date() == today
)

# Large Python files
large_py_files = await view.filter(
    lambda f: f.path.endswith('.py') and f.stats and f.stats.size > 10000
)
```

### Efficient Counting

Count files without loading content:

```python
view = View(agent=agent, query=ViewQuery(path_pattern="*.py"))
count = await view.count()
print(f"Found {count} Python files")
```

## ViewQuery Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `path_pattern` | `str` | `"*"` | Glob pattern for matching file paths |
| `recursive` | `bool` | `True` | Search recursively in subdirectories |
| `include_content` | `bool` | `False` | Load file contents |
| `include_stats` | `bool` | `True` | Include file statistics |
| `regex_pattern` | `Optional[str]` | `None` | Additional regex pattern for matching |
| `max_size` | `Optional[int]` | `None` | Maximum file size in bytes |
| `min_size` | `Optional[int]` | `None` | Minimum file size in bytes |

## Pattern Matching

### Glob Patterns

- `*` - Match any characters except `/`
- `**` - Match any characters including `/` (recursive)
- `?` - Match single character
- `[abc]` - Match any character in brackets
- `[!abc]` - Match any character not in brackets

Examples:
- `*.py` - All Python files in current directory
- `**/*.py` - All Python files recursively
- `/data/**/*.json` - All JSON files under /data
- `test_*.py` - Files starting with "test_"
- `[!_]*.py` - Python files not starting with underscore

### Regex Patterns

For more complex matching, combine with `regex_pattern`:

```python
view = View(
    agent=agent,
    query=ViewQuery(
        path_pattern="*",
        regex_pattern=r"^/src/.*\.(py|pyx)$"  # Python/Cython files in /src
    )
)
```

## Complete Example

```python
import asyncio
from agentfs_sdk import AgentFS
from fsdantic import AgentFSOptions, View, ViewQuery

async def main():
    # Create AgentFS with validated options
    options = AgentFSOptions(id="my-agent")

    async with await AgentFS.open(options.model_dump()) as agent:
        # Create some sample files
        await agent.fs.write_file("/notes/todo.txt", "Task 1\nTask 2")
        await agent.fs.write_file("/notes/ideas.md", "# Ideas\n\n- Idea 1")
        await agent.fs.write_file("/config/settings.json", '{"theme": "dark"}')

        # Query all markdown files with content
        md_view = View(
            agent=agent,
            query=ViewQuery(
                path_pattern="*.md",
                recursive=True,
                include_content=True
            )
        )

        md_files = await md_view.load()

        for file in md_files:
            print(f"File: {file.path}")
            print(f"Size: {file.stats.size} bytes")
            print(f"Content: {file.content[:100]}...")
            print()

if __name__ == "__main__":
    asyncio.run(main())
```

## New in Version 0.2.0

Fsdantic 0.2.0 introduces powerful new abstractions based on common usage patterns:

### Repository Pattern

Generic typed repositories for type-safe KV operations:

```python
from fsdantic import TypedKVRepository, KVRecord
from pydantic import BaseModel

class UserRecord(KVRecord):  # Auto-includes created_at, updated_at
    user_id: str
    name: str
    email: str

# Create a typed repository
repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")

# Type-safe operations
await repo.save("alice", UserRecord(user_id="alice", name="Alice", email="alice@example.com"))
user = await repo.load("alice", UserRecord)  # Returns Optional[UserRecord]
all_users = await repo.list_all(UserRecord)  # Returns list[UserRecord]
```

### Content Search

Search file contents with regex or simple patterns:

```python
from fsdantic import View, ViewQuery

# Search for all class definitions in Python files
view = View(
    agent=agent_fs,
    query=ViewQuery(
        path_pattern="**/*.py",
        content_regex=r"class\s+(\w+):",
        include_content=True
    )
)

matches = await view.search_content()
for match in matches:
    print(f"{match.file}:{match.line}: {match.text}")

# Find files containing "TODO"
files_with_todos = await view.files_containing("TODO")
```

### Workspace Materialization

Convert virtual filesystems to disk:

```python
from fsdantic import Materializer, ConflictResolution
from pathlib import Path

materializer = Materializer(conflict_resolution=ConflictResolution.OVERWRITE)
result = await materializer.materialize(
    agent_fs=agent,
    target_path=Path("./workspace"),
    base_fs=stable,
    clean=True
)

print(f"Materialized {result.files_written} files ({result.bytes_written} bytes)")
if result.errors:
    print(f"Errors: {len(result.errors)}")
```

### Overlay Operations

Merge overlays and manage changes:

```python
from fsdantic import OverlayOperations, MergeStrategy

ops = OverlayOperations(strategy=MergeStrategy.OVERWRITE)

# Merge agent changes into stable
result = await ops.merge(source=agent_fs, target=stable_fs)
print(f"Merged {result.files_merged} files with {len(result.conflicts)} conflicts")

# List changed files
changes = await ops.list_changes(agent_fs)

# Reset overlay to base state
removed = await ops.reset_overlay(agent_fs)
```

### File Operations

Simplified file operations with automatic fallthrough:

```python
from fsdantic import FileOperations

ops = FileOperations(agent_fs, base_fs=stable_fs)

# Read with fallthrough (tries overlay, then base)
content = await ops.read_file("config.json")

# Write to overlay only
await ops.write_file("output.txt", "Hello World")

# Search files
python_files = await ops.search_files("**/*.py")

# Get typed file stats
stats = await ops.stat("config.json")
print(stats.size, stats.is_file, stats.is_directory)

# Get directory tree
tree = await ops.tree("/src")
```

### Query Builder Enhancements

New convenience methods for common operations:

```python
from fsdantic import View, ViewQuery
from datetime import timedelta

view = View(agent=agent_fs, query=ViewQuery())

# Files modified in the last hour
recent = await view.recent_files(timedelta(hours=1))

# Top 10 largest files
largest = await view.largest_files(10)

# Total size of all Python files
total_size = await view.with_pattern("**/*.py").total_size()

# Group files by extension
grouped = await view.group_by_extension()
print(f"Python files: {len(grouped.get('.py', []))}")
```

## Documentation

- **[SPEC.md](SPEC.md)** - Technical specification and API reference
- **[CONCEPT.md](CONCEPT.md)** - Conceptual overview and design philosophy
- **[AGENTS.md](AGENTS.md)** - Agent skills and development best practices
- **[FSDANTIC_EXTENSION_PLAN.md](FSDANTIC_EXTENSION_PLAN.md)** - Detailed implementation plan

## Development

This project uses [uv](https://github.com/astral-sh/uv) for dependency management:

```bash
# Install dependencies
uv sync

# Run example
uv run examples/basic_usage.py

# Run tests (if available)
uv run pytest
```

## Requirements

- Python >= 3.11
- pydantic >= 2.0.0
- agentfs-sdk >= 0.1.0

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Links

- [AgentFS Documentation](https://docs.turso.tech/agentfs)
- [AgentFS Python SDK](https://docs.turso.tech/agentfs/python-sdk)
- [Pydantic Documentation](https://docs.pydantic.dev/)
