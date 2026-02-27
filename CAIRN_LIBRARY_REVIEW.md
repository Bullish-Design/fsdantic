# Cairn Library Code Review

**Review Date:** 2026-02-27
**Reviewer:** Claude Code
**Scope:** Complete library analysis including concurrency, architecture, and test suite
**AgentFS Version Analyzed:** 0.4 (from SPEC.md)

---

## Executive Summary

Cairn is an orchestration runtime for sandboxed code execution on FSdantic workspaces. This review analyzes concurrency issues, evaluates core vs. non-core functionality, and provides recommendations for improvements in both Cairn and FSdantic.

### Key Findings

1. **Concurrency Analysis:** The `CAIRN_CONCURRENCY_RECOMMENDATIONS.md` document is **largely accurate** - atime updates on reads are spec-mandated in AgentFS, and the SDK lacks concurrency protections
2. **Core Functionality:** Grail is the execution engine and can be abstracted; LLM/function-calling extensions are properly separated
3. **FSdantic Recommendations:** Several improvements should be made upstream in FSdantic to mitigate concurrency issues
4. **Architecture:** Well-structured with clear separation of concerns

---

## Part 1: Concurrency Issues Analysis (Revised)

### 1.1 AgentFS Concurrency Characteristics (Verified)

After reviewing the AgentFS SDK source code, I can confirm the following:

#### Verified: atime IS Updated on Reads

**Location:** `agentfs-main/sdk/python/agentfs_sdk/filesystem.py:480-483`
```python
# Update atime
now = int(time.time())
await self._db.execute("UPDATE fs_inode SET atime = ? WHERE ino = ?", (now, ino))
await self._db.commit()
```

**This is spec-mandated behavior.** From `SPEC.md` lines 356-359:
```
#### Reading a File
1. Resolve path to inode
2. Fetch all chunks in order
3. Concatenate chunks in order
4. Update access time:
   UPDATE fs_inode SET atime = ? WHERE ino = ?
```

**Implication:** Every `read_file()` operation performs a WRITE to the database, converting what should be a read-only operation into a write operation that requires an exclusive lock.

#### Verified: No Concurrency Protections in SDK

The AgentFS Python SDK does NOT implement:
- `PRAGMA busy_timeout` configuration
- WAL (Write-Ahead Logging) mode
- Any explicit locking mechanisms
- Transaction batching (every operation calls `commit()` immediately)

**Evidence:** Searched entire `agentfs_sdk/` directory - no references to `busy_timeout`, `journal_mode`, or `WAL`.

#### Verified: Single Connection Per Instance

Each `AgentFS` instance uses a single `turso.aio.Connection`:
```python
# agentfs.py:90-91
db = await connect(db_path)
return await AgentFS.open_with(db)
```

### 1.2 Validating the Recommendations Document

The `CAIRN_CONCURRENCY_RECOMMENDATIONS.md` document claims:

| Claim | Verdict | Evidence |
|-------|---------|----------|
| "AgentFS uses SQLite (via turso)" | **Correct** | Uses `turso.aio.Connection` |
| "SQLite allows a single writer at a time" | **Correct** | Standard SQLite behavior |
| "No busy timeout handling" | **Correct** | No PRAGMA busy_timeout in SDK |
| "Reads update atime, turning reads into writes" | **Correct** | Verified in `filesystem.py:480-483` |
| "Concurrent operations fail with 'database is locked'" | **Correct** | Expected with default SQLite journal mode |

**Conclusion:** The recommendations document accurately identifies the root causes of concurrency issues.

### 1.3 Concurrency Architecture in Cairn

#### What Cairn Does Correctly

1. **Per-Agent Database Isolation**
   - Each agent has its own `agent-{id}.db` file
   - Agents don't share databases during execution
   - This prevents inter-agent contention

2. **Semaphore-Based Execution Control** (`orchestrator.py:124`)
   ```python
   self._semaphore = asyncio.Semaphore(self.config.max_concurrent_agents)
   ```

3. **Optimistic Locking for Lifecycle Records** (`lifecycle.py`)
   - Uses `VersionedKVRecord` with version checking
   - Proper retry logic with exponential backoff

4. **WorkspaceCache with asyncio.Lock** (`workspace_cache.py:26`)
   ```python
   self._lock = asyncio.Lock()
   ```

#### Where Cairn Has Concurrency Risks

1. **External Functions Access Multiple Workspaces**

   `external_functions.py:42-47` shows `read_file` accessing both agent and stable workspaces:
   ```python
   async def read_file(self, path: str) -> str:
       try:
           content = await self.agent_fs.files.read(request.path)
       except FileNotFoundError:
           content = await self.stable_fs.files.read(request.path)
   ```

   If multiple agents execute concurrently and fall back to `stable_fs`, they're all hitting the same `stable.db` with read operations that trigger atime updates.

2. **`accept_agent()` Merges to Stable** (`orchestrator.py:374`)
   ```python
   merge_result = await self.stable.overlay.merge(agent_fs, strategy=MergeStrategy.OVERWRITE)
   ```

   No lock protects concurrent accepts from interleaving merge operations.

3. **FileWatcher Writes to Stable** (`watcher.py`)

   The FileWatcher syncs project directory changes to `stable.db`. If running concurrently with accept operations or agent reads, this can cause lock contention.

### 1.4 Concurrency Risk Matrix

| Scenario | Databases Affected | Risk Level | Current Mitigation |
|----------|-------------------|------------|-------------------|
| Multiple agents executing | agent-*.db (separate) | **Low** | Per-agent isolation |
| Agents reading from stable | stable.db | **Medium** | None |
| Multiple accepts | stable.db | **High** | None |
| FileWatcher + agents | stable.db | **Medium** | None |
| Lifecycle updates | bin.db | **Low** | Optimistic locking |

---

## Part 2: Recommendations

### 2.1 FSdantic Changes (Upstream - We Own This)

Since we own FSdantic, these changes should be made there:

#### Priority 1: Add Readonly Mode Support

FSdantic's `Workspace` class should support a `readonly` mode that skips atime updates:

```python
# Proposed FSdantic change
class Workspace:
    def __init__(self, raw: AgentFS, readonly: bool = False):
        self._raw = raw
        self._readonly = readonly
        # ...

    @property
    def files(self) -> FileManager:
        if self._files is None:
            self._files = FileManager(self._raw, readonly=self._readonly)
        return self._files
```

```python
# FileManager modification
class FileManager:
    def __init__(self, agent_fs: AgentFS, base_fs: AgentFS = None, readonly: bool = False):
        self._readonly = readonly
        # ...

    async def read(self, path: str, ...):
        # Read file content
        content = await self._read_content(path)

        # Skip atime update if readonly
        if not self._readonly:
            await self._update_atime(path)

        return content
```

**Impact:** Eliminates write contention for read-heavy workloads accessing stable.db.

**Note:** This requires either:
- A) AgentFS SDK to support skipping atime updates (preferred)
- B) FSdantic to cache reads and skip re-reading for atime-only updates

#### Priority 2: Add Per-Workspace asyncio.Lock

Add an optional lock to `Workspace` for callers that need to serialize access:

```python
class Workspace:
    def __init__(self, raw: AgentFS):
        self._raw = raw
        self._lock = asyncio.Lock()  # New
        # ...

    @asynccontextmanager
    async def serialized(self):
        """Context manager for serialized access to this workspace."""
        async with self._lock:
            yield self
```

**Usage in Cairn:**
```python
async with self.stable.serialized():
    await self.stable.overlay.merge(agent_fs, strategy=MergeStrategy.OVERWRITE)
```

#### Priority 3: Add Busy Timeout Configuration

FSdantic should pass through busy timeout configuration to AgentFS:

```python
# In Fsdantic.open()
async def open(path: str = None, id: str = None, busy_timeout_ms: int = 5000):
    agent_fs = await AgentFS.open(options)

    # Set busy timeout pragma (requires AgentFS SDK support)
    await agent_fs.get_database().execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")

    return Workspace(agent_fs)
```

**Impact:** Operations will wait and retry instead of immediately failing with "database is locked".

#### Priority 4: Enable WAL Mode Option

```python
async def open(path: str = None, id: str = None, journal_mode: str = "DELETE"):
    agent_fs = await AgentFS.open(options)

    if journal_mode == "WAL":
        await agent_fs.get_database().execute("PRAGMA journal_mode = WAL")

    return Workspace(agent_fs)
```

**Impact:** WAL allows concurrent readers while one writer is active.

### 2.2 Cairn Changes (This Library)

#### Priority 1: Add Stable Workspace Lock

```python
# In CairnOrchestrator.__init__
self._stable_lock = asyncio.Lock()

# In accept_agent
async def accept_agent(self, agent_id: str) -> None:
    ctx = self._get_agent(agent_id)
    if ctx.state is not AgentState.REVIEWING:
        raise ValueError(f"Agent {agent_id} not in reviewing state")

    async with self._stable_lock:  # NEW
        agent_fs = await self._get_agent_workspace(ctx)
        merge_result = await self.stable.overlay.merge(agent_fs, strategy=MergeStrategy.OVERWRITE)
        # ... rest of method
```

#### Priority 2: Open Stable Workspace as Readonly for Agent Reads

```python
# In external_functions.py
class CairnExternalFunctions:
    def __init__(self, agent_id: str, agent_fs: Workspace, stable_fs: Workspace):
        self.agent_id = agent_id
        self.agent_fs = agent_fs
        self.stable_fs = stable_fs  # Should be opened with readonly=True
```

```python
# In orchestrator.py initialization
self.stable = await Fsdantic.open(path=str(self.agentfs_dir / "stable.db"))
self.stable_readonly = await Fsdantic.open(
    path=str(self.agentfs_dir / "stable.db"),
    readonly=True  # For agent read operations
)
```

#### Priority 3: Pause FileWatcher During Accept

```python
async def accept_agent(self, agent_id: str) -> None:
    async with self._stable_lock:
        if self.watcher:
            self.watcher.pause()  # Stop syncing during merge
        try:
            # ... merge operations
        finally:
            if self.watcher:
                self.watcher.resume()
```

### 2.3 Test Additions Required

```python
# tests/cairn/integration/test_concurrent_stable_access.py

@pytest.mark.asyncio
async def test_concurrent_stable_reads_during_agent_execution(tmp_path):
    """Multiple agents reading from stable.db should not cause lock errors."""
    orch = await create_orchestrator(tmp_path, max_concurrent_agents=5)

    # Spawn 5 agents that all read from stable
    agent_ids = [await orch.spawn_agent(f"read task {i}") for i in range(5)]

    # Wait for completion - should not raise database locked errors
    results = await asyncio.gather(
        *[wait_for_agent_completion(orch, aid) for aid in agent_ids],
        return_exceptions=True
    )

    # No database locked errors
    for r in results:
        assert not isinstance(r, Exception), f"Got error: {r}"


@pytest.mark.asyncio
async def test_concurrent_accept_operations(tmp_path):
    """Two agents accepting simultaneously should not corrupt stable.db."""
    orch = await create_orchestrator(tmp_path)

    # Create two agents in REVIEWING state
    agent1 = await create_reviewing_agent(orch, "agent1", file_content="content1")
    agent2 = await create_reviewing_agent(orch, "agent2", file_content="content2")

    # Accept both concurrently
    await asyncio.gather(
        orch.accept_agent(agent1),
        orch.accept_agent(agent2),
    )

    # Both changes should be in stable
    assert await orch.stable.files.exists("file1.txt")
    assert await orch.stable.files.exists("file2.txt")
```

---

## Part 3: Grail Integration Analysis

### 3.1 What Grail Does

Grail is a Python sandbox execution engine:
- Loads `.pym` script files
- Validates scripts before execution (`script.check()`)
- Executes with explicit external function injection
- Enforces sandbox policy (no imports, no direct I/O)

### 3.2 Grail Usage in Cairn

Grail is used in one file: `orchestrator.py`

```python
# Loading (lines 66-96)
def _load_grail_script(pym_path: Path) -> GrailScript

# Validation (line 549)
check_result = script.check()

# Execution (lines 582-585)
await script.run(inputs={"task_description": ctx.task}, externals=tools)
```

### 3.3 Is Grail Core Functionality?

**If "core" = copy-on-write workspace management:** No, Grail is not needed.

**If "core" = orchestrating agent code execution:** Yes, Grail (or equivalent) is required.

### 3.4 Recommendation: Abstract Execution Engine

```python
# Proposed abstraction
@runtime_checkable
class ExecutionEngine(Protocol):
    """Protocol for script execution engines."""

    async def validate(self, code_path: Path) -> tuple[bool, list[str]]:
        """Validate code before execution. Returns (valid, errors)."""
        ...

    async def execute(
        self,
        code_path: Path,
        inputs: dict[str, Any],
        externals: ExternalTools,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Execute code with given inputs and external functions."""
        ...


class GrailExecutionEngine:
    """Default execution engine using Grail."""

    async def validate(self, code_path: Path) -> tuple[bool, list[str]]:
        script = _load_grail_script(code_path)
        result = script.check()
        return result.valid, [str(e) for e in (result.errors or [])]

    async def execute(self, code_path, inputs, externals, timeout_seconds):
        script = _load_grail_script(code_path)
        return await run_with_timeout(
            script.run(inputs=inputs, externals=externals),
            timeout_seconds=timeout_seconds,
        )
```

**Benefits:**
- Grail becomes an optional dependency
- Alternative engines can be plugged in
- Easier testing with mock engines

---

## Part 4: Non-Core Components Analysis

### 4.1 Components That ARE Core

| Component | Purpose | Core? |
|-----------|---------|-------|
| `WorkspaceManager` | Workspace lifecycle with cleanup | Yes |
| `WorkspaceCache` | LRU cache for workspaces | Yes |
| `LifecycleStore` | Agent state persistence | Yes |
| `CairnOrchestrator` | Central coordination | Yes |
| `AgentContext/AgentState` | Agent data models | Yes |
| `CairnExternalFunctions` | Sandboxed file operations | Yes |
| `TaskQueue` | Priority task scheduling | Yes |

### 4.2 Components That Are Properly Separated

| Component | Location | Status |
|-----------|----------|--------|
| LLM Provider | `extensions/cairn-llm/` | Correctly separated |
| Git Provider | `extensions/cairn-git/` | Correctly separated |
| Registry Provider | `extensions/cairn-registry/` | Correctly separated |

### 4.3 Components That Could Be Extracted

| Component | Recommendation |
|-----------|----------------|
| Grail integration | Abstract behind `ExecutionEngine` protocol |
| Signal handling | Could be optional extension |
| CLI | Already transport adapter, could be separate package |
| FileWatcher | Could be optional for non-live-sync use cases |

---

## Part 5: Test Suite Review

### 5.1 Coverage Summary

| Test Area | Files | Coverage | Notes |
|-----------|-------|----------|-------|
| Orchestrator | `test_orchestrator.py` | Good | State transitions well tested |
| Lifecycle | `test_lifecycle.py` | Good | Retry logic covered |
| Concurrency | `test_concurrency.py` | Adequate | Semaphore tested, stable access not |
| Workspace | `test_workspace*.py` | Good | Cache and manager covered |

### 5.2 Missing Test Coverage

1. **Concurrent stable.db access** - No tests for multiple agents reading stable simultaneously
2. **Concurrent accept operations** - No tests for racing accept operations
3. **FileWatcher + accept interleaving** - No tests for sync during merge
4. **Database locked error handling** - No tests for retry on SQLite busy

### 5.3 Test Quality Issues

1. **Inconsistent cleanup** - Some tests use `_safe_close()`, others inline
2. **Magic strings** - Agent IDs scattered without factory pattern
3. **Limited property testing** - Could use Hypothesis for edge cases

---

## Part 6: Architecture Diagram (Updated)

```
                    ┌─────────────────────────────────────┐
                    │         User / CLI / Signals        │
                    └───────────────┬─────────────────────┘
                                    │
                                    ▼
                    ┌─────────────────────────────────────┐
                    │        CairnOrchestrator            │
                    │  ┌─────────────────────────────┐    │
                    │  │  _stable_lock (NEEDED)      │    │  ◄── NEW: Add asyncio.Lock
                    │  └─────────────────────────────┘    │
                    │  ┌─────────────────────────────┐    │
                    │  │  TaskQueue + Semaphore      │    │
                    │  │  (concurrency control)      │    │
                    │  └─────────────────────────────┘    │
                    └───────────────┬─────────────────────┘
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          │                         │                         │
          ▼                         ▼                         ▼
┌─────────────────┐    ┌─────────────────────┐    ┌─────────────────┐
│  CodeProvider   │    │  ExecutionEngine    │    │ ExternalFunctions│
│  (plugin)       │    │  (abstracted)       │    │  (file ops)     │
└─────────────────┘    └─────────────────────┘    └────────┬────────┘
                                                           │
                    ┌──────────────────────────────────────┘
                    │
                    ▼
          ┌─────────────────────────────────────┐
          │           FSdantic                  │
          │  ┌───────────────────────────────┐  │
          │  │  NEW: readonly mode           │  │  ◄── Skips atime update
          │  │  NEW: busy_timeout config     │  │  ◄── Wait on lock
          │  │  NEW: WAL mode option         │  │  ◄── Concurrent reads
          │  │  NEW: per-workspace lock      │  │  ◄── Serialize access
          │  └───────────────────────────────┘  │
          └───────────────┬─────────────────────┘
                          │
                          ▼
          ┌─────────────────────────────────────┐
          │           AgentFS SDK               │
          │  (atime updates on read - by spec)  │  ◄── Cannot change
          └───────────────┬─────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
     stable.db      agent-*.db        bin.db
     (CONTENTION    (isolated)        (lifecycle)
      RISK)
```

---

## Part 7: Implementation Priority

### Immediate (Before Production)

1. **Add `_stable_lock` in CairnOrchestrator** - Prevents accept race conditions
2. **Add concurrent access tests** - Validate fix works

### Short-Term (FSdantic Changes)

3. **Add `readonly` mode to FSdantic Workspace** - Eliminates read contention
4. **Add `busy_timeout` pass-through** - Graceful lock waiting
5. **Add per-workspace lock** - Optional serialization

### Medium-Term

6. **Abstract Grail behind ExecutionEngine protocol** - Plugin architecture
7. **Add WAL mode option** - Better concurrent read performance
8. **Comprehensive concurrent test suite** - Property-based testing

---

## Conclusion

The `CAIRN_CONCURRENCY_RECOMMENDATIONS.md` document accurately identifies the root causes of concurrency issues:
- atime updates on reads are **spec-mandated in AgentFS**
- The AgentFS SDK lacks busy timeout and WAL configuration
- Concurrent access to `stable.db` causes lock contention

The recommended fix approach is correct:
1. **FSdantic should add `readonly` mode** to skip atime updates for read-only access patterns
2. **FSdantic should add busy timeout and WAL mode configuration**
3. **Cairn should add a stable workspace lock** for accept operations
4. **Cairn should use separate readonly workspace** for agent reads

Since we cannot modify AgentFS, FSdantic is the correct place to add mitigation layers. The changes are straightforward and maintain backward compatibility.
