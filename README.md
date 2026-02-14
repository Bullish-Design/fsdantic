# AgentFS Pydantic Models

A minimal Python library providing Pydantic models and a powerful query interface for the [AgentFS SDK](https://docs.turso.tech/agentfs/python-sdk).

## Features

- 🎯 **Type-safe models** - Pydantic models for all core AgentFS objects
- 🔍 **Query interface** - Powerful `View` system for querying filesystem with filters
- ✅ **Validation** - Automatic validation of options and data structures
- 🚀 **Async-first** - Built on async/await for optimal performance
- 📦 **Minimal dependencies** - Only requires `pydantic` and `agentfs-sdk`

## Installation

```bash
uv add agentfs-pydantic
```

Or with pip:

```bash
pip install agentfs-pydantic
```

## Quick Start

```python
import asyncio
from agentfs_sdk import AgentFS
from agentfs_pydantic import AgentFSOptions, View, ViewQuery

async def main():
    # Create validated options
    options = AgentFSOptions(id="my-agent")

    async with await AgentFS.open(options.model_dump()) as agent:
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
from agentfs_pydantic import AgentFSOptions

# By agent ID
options = AgentFSOptions(id="my-agent")

# By custom path
options = AgentFSOptions(path="./data/mydb.db")
```

### FileEntry

Represents a file in the filesystem with optional stats and content:

```python
from agentfs_pydantic import FileEntry, FileStats

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
from agentfs_pydantic import ToolCall, ToolCallStats

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
from agentfs_pydantic import View, ViewQuery

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
from agentfs_pydantic import AgentFSOptions, View, ViewQuery

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
