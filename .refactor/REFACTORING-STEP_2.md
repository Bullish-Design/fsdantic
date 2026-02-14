# Refactoring Step 2: Client + Workspace Onboarding API

## Phase 2 Scope

This phase introduces a single, ergonomic onboarding flow centered on `src/fsdantic/client.py` and `src/fsdantic/workspace.py`.

### Goals

- Introduce a single entrypoint: `Fsdantic.open(...)`.
- Introduce a unified `Workspace` façade exposing:
  - `files`
  - `kv`
  - `overlay`
  - `materialize`
  - `raw`

### Primary Files

- `src/fsdantic/client.py`
  - Defines the high-level client/factory API (`Fsdantic`).
  - Owns argument validation and open orchestration.
- `src/fsdantic/workspace.py`
  - Defines `Workspace` as the runtime façade around AgentFS.
  - Owns lazy manager wiring and lifecycle behavior.

---

## Implementation Checklist

### 1) `Fsdantic.open(id|path)` validation via `AgentFSOptions`

- [ ] Add/confirm classmethod `Fsdantic.open(...)` in `client.py`.
- [ ] Support mutually exclusive user-facing selectors:
  - [ ] `id=...`
  - [ ] `path=...`
- [ ] Reject invalid combinations:
  - [ ] neither provided
  - [ ] both provided
  - [ ] wrong types / empty values
- [ ] Normalize arguments into `AgentFSOptions`.
- [ ] Delegate option validation to `AgentFSOptions` so validation logic has one source of truth.
- [ ] Ensure validation errors are surfaced clearly and predictably.

### 2) `open_with_options()` path to open raw AgentFS and wrap `Workspace`

- [ ] Add/confirm `Fsdantic.open_with_options(options: AgentFSOptions)`.
- [ ] Convert options to the shape required by AgentFS SDK.
- [ ] Open underlying AgentFS instance.
- [ ] Wrap opened AgentFS in a new `Workspace` instance.
- [ ] Keep `open()` as syntactic sugar over `open_with_options()`.
- [ ] Avoid duplicating open logic between methods.

### 3) Workspace lazy manager initialization + async context manager support

- [ ] Define `Workspace` constructor with stored raw AgentFS reference.
- [ ] Add lazy properties:
  - [ ] `files`
  - [ ] `kv`
  - [ ] `overlay`
  - [ ] `materialize`
- [ ] Instantiate each manager only on first access.
- [ ] Cache each manager instance and return the same object on repeated access.
- [ ] Add `raw` property exposing underlying AgentFS object directly.
- [ ] Implement async context manager support:
  - [ ] `__aenter__` returns `self`
  - [ ] `__aexit__` closes underlying AgentFS
- [ ] Ensure close semantics are idempotent and safe under exceptions.

---

## Architecture Acceptance Criteria

### A. Public onboarding path is one import + one call

- [ ] A new user can start with:

  ```python
  from fsdantic import Fsdantic

  workspace = await Fsdantic.open(id="my-agent")
  ```

- [ ] Equivalent path-based onboarding is available:

  ```python
  workspace = await Fsdantic.open(path="/path/to/workspace")
  ```

- [ ] No additional required wiring to access core capabilities (`files`, `kv`, `overlay`, `materialize`).

### B. Lazy loading avoids unnecessary manager instantiation

- [ ] `Workspace` creation does not eagerly construct manager objects.
- [ ] Each manager is created only when first accessed.
- [ ] Re-accessing any manager returns the cached instance.
- [ ] Startup overhead remains minimal for workloads that only need a subset of managers.

---

## Detailed Testing Plan

## Test module: `tests/test_workspace.py`

### 1) Open behavior coverage

- [ ] **Open by ID**
  - Assert `Fsdantic.open(id=...)` succeeds and returns `Workspace`.
- [ ] **Open by path**
  - Assert `Fsdantic.open(path=...)` succeeds and returns `Workspace`.
- [ ] **Invalid args**
  - Assert failure for:
    - missing both `id` and `path`
    - providing both `id` and `path`
    - invalid type/value inputs
- [ ] **Options-based open**
  - Assert `Fsdantic.open_with_options(AgentFSOptions(...))` succeeds.
  - Assert behavior parity with `Fsdantic.open(...)`.

### 2) Lazy property semantics

- [ ] Access `workspace.files` twice; assert same object identity (`is`).
- [ ] Access `workspace.kv` twice; assert same object identity.
- [ ] Access `workspace.overlay` twice; assert same object identity.
- [ ] Access `workspace.materialize` twice; assert same object identity.
- [ ] Optionally instrument constructors/mocks to assert each manager instantiated once.

### 3) Raw AgentFS exposure

- [ ] Assert `workspace.raw` returns the underlying AgentFS object used at construction.
- [ ] Assert no wrappers/proxies alter identity unexpectedly.

### 4) Async context manager lifecycle guarantees

- [ ] **Success path**
  - `async with workspace:` should call underlying close exactly once on exit.
- [ ] **Exception path**
  - raising inside `async with` should still call close exactly once.
- [ ] Verify no double-close from context manager internals.

### 5) Lifecycle resilience tests

- [ ] Add tests for multiple open/close cycles in sequence.
- [ ] Confirm independent workspaces do not share manager instances.
- [ ] Confirm each opened workspace can close cleanly without leaking state to the next cycle.

---

## Definition of Done

Phase 2 is complete when all workspace creation flows are clearly documented and fully test-covered.

- [ ] `Fsdantic.open(id|path)` exists and validates via `AgentFSOptions`.
- [ ] `open_with_options()` path is implemented and used.
- [ ] `Workspace` exposes `files`, `kv`, `overlay`, `materialize`, and `raw`.
- [ ] Lazy manager initialization is implemented and verified.
- [ ] Async context manager lifecycle behavior is robust and tested.
- [ ] `tests/test_workspace.py` covers all scenarios listed above.
- [ ] Documentation reflects the finalized onboarding/lifecycle patterns.
