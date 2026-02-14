# Fsdantic Agent Skills

**Purpose:** This document defines agent skills and best practices for developing with Fsdantic.

---

## Overview

This document provides guidance for AI agents and developers working with the Fsdantic library. It includes:

1. **Agent Skills** - Reusable agent capabilities
2. **Development Patterns** - Best practices for coding
3. **Common Pitfalls** - What to avoid
4. **Integration Guides** - Working with AgentFS SDK

---

## Agent Skills

### SKILL: AgentFS Code Search

**When to Use:**
- Before writing any code that interfaces with AgentFS SDK
- When implementing new features that use AgentFS APIs
- When debugging AgentFS integration issues
- When verifying API signatures and usage patterns

**Description:**
Search the included AgentFS SDK code in the `.context/` directory to ensure correct API usage.

**Process:**

1. **Identify the AgentFS component** you need to work with:
   - Filesystem operations → `agentfs-main/sdk/python/agentfs_sdk/filesystem.py`
   - KV store operations → `agentfs-main/sdk/python/agentfs_sdk/kvstore.py`
   - Tool call tracking → `agentfs-main/sdk/python/agentfs_sdk/toolcalls.py`
   - Main AgentFS class → `agentfs-main/sdk/python/agentfs_sdk/agentfs.py`

2. **Search the relevant file** for the method/class you need:
   ```
   Read file: .context/agentfs-main/sdk/python/agentfs_sdk/[module].py
   ```

3. **Verify the API signature**:
   - Parameter names and types
   - Return types
   - Async/sync behavior
   - Error conditions

4. **Check usage examples** in the SDK code and tests:
   ```
   Read file: .context/agentfs-main/sdk/python/tests/[test_module].py
   Read file: .context/agentfs-main/sdk/python/examples/[example].py
   ```

5. **Implement with confidence** knowing the API is correct

**Example Usage:**

```markdown
Task: Implement a function that reads a file from AgentFS

Agent thought process:
1. I need to use AgentFS filesystem API
2. Let me check .context/agentfs-main/sdk/python/agentfs_sdk/filesystem.py
3. Found: async def read_file(path: str, encoding: Optional[str] = "utf-8") -> Union[str, bytes]
4. It returns bytes if encoding=None, str otherwise
5. Now I can implement correctly:

async def read_agentfs_file(fs: AgentFS, path: str) -> bytes:
    return await fs.fs.read_file(path, encoding=None)
```

**Key Files in .context/:**

```
.context/
└── agentfs-main/
    ├── SPEC.md                        # Specification document
    ├── README.md                      # Overview and quick start
    ├── MANUAL.md                      # Detailed manual
    └── sdk/python/
        ├── agentfs_sdk/
        │   ├── __init__.py            # Main exports
        │   ├── agentfs.py             # AgentFS main class
        │   ├── filesystem.py          # Filesystem interface
        │   ├── kvstore.py             # KV store interface
        │   ├── toolcalls.py           # Tool call tracking
        │   ├── constants.py           # Constants
        │   ├── guards.py              # Validation guards
        │   └── errors.py              # Error classes
        ├── tests/                     # Unit tests (usage examples)
        └── examples/                  # Example code
```

**Important Notes:**

1. **Always search first** - Don't assume API signatures
2. **Check return types** - AgentFS methods may return different types than expected
3. **Verify async/sync** - Most AgentFS methods are async
4. **Read tests** - Tests show real usage patterns
5. **Check error handling** - Understand what exceptions can be raised

---

### SKILL: Pydantic Model Design

**When to Use:**
- Creating new models for KV storage
- Extending existing Fsdantic models
- Designing data schemas

**Best Practices:**

1. **Use KVRecord base classes:**
   ```python
   from fsdantic import KVRecord, VersionedKVRecord

   class UserRecord(KVRecord):  # Auto-includes created_at, updated_at
       user_id: str
       name: str
       email: str

   class ConfigRecord(VersionedKVRecord):  # Also includes version
       settings: dict
   ```

2. **Add validation:**
   ```python
   from pydantic import field_validator

   class UserRecord(KVRecord):
       user_id: str
       email: str

       @field_validator("email")
       @classmethod
       def validate_email(cls, v):
           if "@" not in v:
               raise ValueError("Invalid email")
           return v
   ```

3. **Use computed fields:**
   ```python
   from pydantic import computed_field

   class UserRecord(KVRecord):
       first_name: str
       last_name: str

       @computed_field
       @property
       def full_name(self) -> str:
           return f"{self.first_name} {self.last_name}"
   ```

4. **Document with docstrings:**
   ```python
   class UserRecord(KVRecord):
       """User account information.

       Examples:
           >>> user = UserRecord(user_id="alice", name="Alice")
           >>> user.mark_updated()
       """
       user_id: str = Field(description="Unique user identifier")
       name: str = Field(description="User display name")
   ```

---

### SKILL: Query Optimization

**When to Use:**
- Working with large filesystems
- Performance-critical operations
- Memory-constrained environments

**Optimization Techniques:**

1. **Disable content loading when not needed:**
   ```python
   # BAD: Loads all file contents into memory
   view = View(agent=fs, query=ViewQuery(
       path_pattern="**/*.py",
       include_content=True  # Unnecessary!
   ))
   files = await view.load()
   print(len(files))

   # GOOD: Only loads metadata
   view = View(agent=fs, query=ViewQuery(
       path_pattern="**/*.py",
       include_content=False
   ))
   files = await view.load()
   print(len(files))
   ```

2. **Use filters to reduce results:**
   ```python
   # BAD: Load everything, filter in memory
   view = View(agent=fs, query=ViewQuery(path_pattern="**/*"))
   files = await view.load()
   large_files = [f for f in files if f.stats.size > 10000]

   # GOOD: Filter during traversal
   view = View(agent=fs, query=ViewQuery(
       path_pattern="**/*",
       min_size=10000
   ))
   files = await view.load()
   ```

3. **Use count() instead of load() when possible:**
   ```python
   # BAD: Load all files just to count
   files = await view.load()
   count = len(files)

   # GOOD: Count without loading
   count = await view.count()
   ```

4. **Limit recursive depth:**
   ```python
   # BAD: Search entire tree
   view = View(agent=fs, query=ViewQuery(
       path_pattern="**/*.py",
       recursive=True
   ))

   # GOOD: Search only top level
   view = View(agent=fs, query=ViewQuery(
       path_pattern="*.py",
       recursive=False
   ))
   ```

5. **Use specific patterns:**
   ```python
   # BAD: Match too broadly
   view = View(agent=fs, query=ViewQuery(path_pattern="**/*"))

   # GOOD: Be specific
   view = View(agent=fs, query=ViewQuery(path_pattern="src/**/*.py"))
   ```

---

### SKILL: Error Handling

**When to Use:**
- All production code
- Operations that may fail
- User-facing functionality

**Patterns:**

1. **Handle FileNotFoundError:**
   ```python
   from fsdantic import FileOperations

   ops = FileOperations(agent_fs, base_fs=stable_fs)

   try:
       content = await ops.read_file("config.json")
   except FileNotFoundError:
       # File doesn't exist in either layer
       content = "{}"  # Use default
   ```

2. **Handle ValidationError:**
   ```python
   from pydantic import ValidationError
   from fsdantic import TypedKVRepository

   repo = TypedKVRepository[UserRecord](fs, prefix="user:")

   try:
       user = await repo.load("alice", UserRecord)
   except ValidationError as e:
       # Data doesn't match model
       print(f"Invalid data: {e}")
       user = None
   ```

3. **Handle materialization errors:**
   ```python
   from fsdantic import Materializer

   materializer = Materializer()
   result = await materializer.materialize(agent_fs, target_path)

   if result.errors:
       print(f"Failed to copy {len(result.errors)} files:")
       for path, error in result.errors:
           print(f"  {path}: {error}")
   ```

4. **Handle merge conflicts:**
   ```python
   from fsdantic import OverlayOperations, MergeStrategy

   ops = OverlayOperations(strategy=MergeStrategy.ERROR)
   result = await ops.merge(source, target)

   if result.errors:
       print("Merge failed due to conflicts:")
       for path, error in result.errors:
           print(f"  {path}: {error}")
   ```

---

### SKILL: Testing Fsdantic Code

**When to Use:**
- Writing tests for Fsdantic-based applications
- Validating functionality
- Regression testing

**Patterns:**

1. **Create test fixtures:**
   ```python
   import pytest
   from agentfs_sdk import AgentFS
   from fsdantic import AgentFSOptions

   @pytest.fixture
   async def test_fs():
       fs = await AgentFS.open(AgentFSOptions(id="test").model_dump())
       yield fs
       await fs.close()

   @pytest.fixture
   async def test_repo(test_fs):
       from fsdantic import TypedKVRepository
       return TypedKVRepository[TestRecord](test_fs, prefix="test:")
   ```

2. **Test repository operations:**
   ```python
   @pytest.mark.asyncio
   async def test_save_and_load(test_repo):
       record = TestRecord(id="1", value=42)
       await test_repo.save("1", record)

       loaded = await test_repo.load("1", TestRecord)
       assert loaded is not None
       assert loaded.value == 42
   ```

3. **Test view queries:**
   ```python
   @pytest.mark.asyncio
   async def test_view_query(test_fs):
       from fsdantic import View, ViewQuery, FileOperations

       # Create test files
       ops = FileOperations(test_fs)
       await ops.write_file("test1.py", "print('hello')")
       await ops.write_file("test2.txt", "text")

       # Query Python files
       view = View(agent=test_fs, query=ViewQuery(path_pattern="*.py"))
       files = await view.load()

       assert len(files) == 1
       assert files[0].path == "/test1.py"
   ```

4. **Test materialization:**
   ```python
   @pytest.mark.asyncio
   async def test_materialization(test_fs, tmp_path):
       from fsdantic import Materializer, FileOperations

       # Create files
       ops = FileOperations(test_fs)
       await ops.write_file("test.txt", "content")

       # Materialize
       materializer = Materializer()
       result = await materializer.materialize(test_fs, tmp_path)

       assert result.files_written == 1
       assert (tmp_path / "test.txt").read_text() == "content"
   ```

---

## Development Patterns

### Pattern 1: Layered Architecture

Organize code into layers:

```python
# data_layer.py
from fsdantic import TypedKVRepository, KVRecord

class UserRecord(KVRecord):
    user_id: str
    name: str

class DataLayer:
    def __init__(self, agent_fs):
        self.users = TypedKVRepository[UserRecord](agent_fs, prefix="user:")

# business_layer.py
class BusinessLayer:
    def __init__(self, data: DataLayer):
        self.data = data

    async def create_user(self, user_id: str, name: str):
        user = UserRecord(user_id=user_id, name=name)
        await self.data.users.save(user_id, user)

# app.py
async def main():
    agent_fs = await AgentFS.open(...)
    data = DataLayer(agent_fs)
    business = BusinessLayer(data)
    await business.create_user("alice", "Alice")
```

### Pattern 2: Dependency Injection

Inject dependencies for testability:

```python
class UserService:
    def __init__(self, user_repo: TypedKVRepository[UserRecord]):
        self.users = user_repo

    async def get_user(self, user_id: str) -> Optional[UserRecord]:
        return await self.users.load(user_id, UserRecord)

# In production:
repo = TypedKVRepository[UserRecord](agent_fs, prefix="user:")
service = UserService(repo)

# In tests:
mock_repo = MockRepository()
service = UserService(mock_repo)
```

### Pattern 3: Factory Pattern

Create factories for complex initialization:

```python
class RepositoryFactory:
    def __init__(self, agent_fs: AgentFS):
        self.agent_fs = agent_fs

    def create_user_repo(self) -> TypedKVRepository[UserRecord]:
        return TypedKVRepository[UserRecord](self.agent_fs, prefix="user:")

    def create_agent_repo(self) -> TypedKVRepository[AgentRecord]:
        return TypedKVRepository[AgentRecord](self.agent_fs, prefix="agent:")

factory = RepositoryFactory(agent_fs)
users = factory.create_user_repo()
agents = factory.create_agent_repo()
```

---

## Common Pitfalls

### Pitfall 1: Not Using AgentFS Code Search

**Problem:**
```python
# Assuming API without checking
async def read_file(fs, path):
    return await fs.fs.readFile(path)  # WRONG! Method is read_file, not readFile
```

**Solution:**
```python
# Always check .context/ first
# Verified from filesystem.py: async def read_file(self, path: str, encoding: Optional[str] = "utf-8")
async def read_file(fs, path):
    return await fs.fs.read_file(path)  # CORRECT!
```

### Pitfall 2: Loading Unnecessary Content

**Problem:**
```python
# Loads all file contents into memory
view = View(agent=fs, query=ViewQuery(
    path_pattern="**/*",
    include_content=True
))
files = await view.load()
for file in files:
    print(file.path)  # Only need path, not content!
```

**Solution:**
```python
# Don't load content when not needed
view = View(agent=fs, query=ViewQuery(
    path_pattern="**/*",
    include_content=False
))
files = await view.load()
for file in files:
    print(file.path)
```

### Pitfall 3: Ignoring Errors

**Problem:**
```python
result = await materializer.materialize(agent_fs, target_path)
print(f"Copied {result.files_written} files")  # Ignores errors!
```

**Solution:**
```python
result = await materializer.materialize(agent_fs, target_path)
print(f"Copied {result.files_written} files")
if result.errors:
    print(f"Errors: {len(result.errors)}")
    for path, error in result.errors:
        print(f"  {path}: {error}")
```

### Pitfall 4: Not Using Type Hints

**Problem:**
```python
async def get_user(repo, id):  # No types!
    return await repo.load(id, UserRecord)
```

**Solution:**
```python
async def get_user(repo: TypedKVRepository[UserRecord], id: str) -> Optional[UserRecord]:
    return await repo.load(id, UserRecord)
```

### Pitfall 5: Mixing Sync and Async

**Problem:**
```python
def process_files(fs):  # Not async!
    files = view.load()  # Calling async without await
```

**Solution:**
```python
async def process_files(fs):  # Async function
    files = await view.load()  # Properly await
```

---

## Integration with AgentFS SDK

### Accessing Underlying AgentFS

Fsdantic wraps AgentFS, but you can always access it:

```python
from fsdantic import FileOperations

ops = FileOperations(agent_fs)

# Use Fsdantic
content = await ops.read_file("file.txt")

# Access underlying AgentFS directly
stat = await ops.agent_fs.fs.stat("/file.txt")
print(f"Inode: {stat.ino}, Size: {stat.size}")
```

### Mixing Fsdantic with Direct AgentFS

```python
from fsdantic import TypedKVRepository, View, ViewQuery

# Use repository for KV
repo = TypedKVRepository[Config](agent_fs, prefix="config:")
await repo.save("app", config)

# Use direct AgentFS for filesystem
await agent_fs.fs.write_file("output.txt", b"content")

# Use View for queries
view = View(agent=agent_fs, query=ViewQuery(path_pattern="*.txt"))
files = await view.load()
```

### When to Use Direct AgentFS

Use direct AgentFS when:
1. Fsdantic doesn't provide the needed functionality
2. Performance is critical and you need low-level control
3. You need access to advanced features (e.g., symlinks, permissions)

Use Fsdantic when:
1. You want type safety
2. You need common patterns (repository, query, materialization)
3. You value developer experience over raw performance

---

## Summary

This document provides agent skills and best practices for working with Fsdantic:

1. **AgentFS Code Search** - Always verify API usage by checking `.context/`
2. **Pydantic Model Design** - Use base classes, validation, and documentation
3. **Query Optimization** - Minimize memory usage and improve performance
4. **Error Handling** - Handle all error cases properly
5. **Testing** - Write comprehensive tests with fixtures
6. **Development Patterns** - Use layered architecture and dependency injection
7. **Avoid Pitfalls** - Check APIs, load only what's needed, handle errors

By following these guidelines, you'll write better, more reliable code with Fsdantic.
