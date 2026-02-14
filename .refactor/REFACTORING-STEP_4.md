# Phase 4 Refactoring Plan: KV API Consolidation

## Scope

Consolidate KV operations into two clear pathways in `src/fsdantic/kv.py`, with explicit integration boundaries to `src/fsdantic/repository.py`:

1. **Simple KV pathway (convenience API)** for direct key-value usage.
2. **Typed KV pathway (repository API)** for schema-driven persistence via Pydantic models.

The refactor should preserve backwards-compatible behavior where feasible while improving ergonomics, diagnostics, and composability.

---

## Implementation Checklist

### Core API surface (`src/fsdantic/kv.py`)

- [ ] Implement `get` with:
  - [ ] optional default value support
  - [ ] missing-key semantics that are explicit and documented
  - [ ] consistent deserialization path and error wrapping
- [ ] Implement `set` with:
  - [ ] deterministic serialization behavior
  - [ ] serialization error wrapping with key/prefix context
- [ ] Implement `delete` with:
  - [ ] predictable behavior for absent keys
  - [ ] return semantics (e.g., boolean or no-op) clearly documented
- [ ] Implement `exists` with:
  - [ ] direct key existence checks under effective namespace/prefix
- [ ] Implement `list` with:
  - [ ] deterministic ordering (if practical)
  - [ ] clear key formatting expectations relative to prefix handling
- [ ] Implement `repository` factory/accessor to expose typed workflows backed by `src/fsdantic/repository.py`.
- [ ] Implement `namespace` for deriving nested KV managers with composed prefixes.

### Prefix scoping & namespace rules

- [ ] Define and enforce prefix composition rules for nested namespaces.
- [ ] Ensure namespace stacking yields deterministic key composition.
- [ ] Normalize separators and empty-prefix edge cases.
- [ ] Document whether returned/listed keys are raw, fully-qualified, or stripped.

### Error handling and diagnostics

- [ ] Wrap serialization/deserialization failures in domain-appropriate exceptions.
- [ ] Include clear diagnostics in wrapped errors:
  - [ ] operation (`get`/`set`/etc.)
  - [ ] effective key
  - [ ] namespace/prefix context
  - [ ] root exception details
- [ ] Keep diagnostics actionable without leaking unnecessary internal details.

---

## Architecture Acceptance Criteria

- **Separation of concerns is explicit:**
  - Convenience KV API (`src/fsdantic/kv.py`) handles untyped/simple operations and namespace ergonomics.
  - Typed repository API (`src/fsdantic/repository.py`) owns model-centric validation and typed persistence abstractions.
- `kv.py` typed access points delegate cleanly to repository constructs rather than duplicating typed persistence logic.
- Public API makes it obvious when to use simple KV calls vs typed repository patterns.

---

## Detailed Testing Plan

### 1) CRUD + defaults + existence (`tests/test_kv.py`)

- [ ] Verify `set` then `get` round-trips representative primitive and structured payloads.
- [ ] Verify `get` default handling for missing keys.
- [ ] Verify missing-key semantics when no default is provided.
- [ ] Verify `exists` before/after `set` and after `delete`.
- [ ] Verify `delete` behavior on missing keys is stable and documented.

### 2) Namespace stacking determinism

- [ ] Add tests for multi-level `namespace(...)` chaining.
- [ ] Assert effective key composition is deterministic and separator-normalized.
- [ ] Validate identical composition regardless of equivalent construction patterns (where applicable).

### 3) `list` behavior and prefix stripping

- [ ] Test `list` when manager is root/no prefix.
- [ ] Test `list` when manager has prefix.
- [ ] Validate behavior with/without manager prefix stripping (as designed).
- [ ] Ensure listed keys match documented contract (qualified vs relative).

### 4) Typed repository compatibility

- [ ] Add compatibility tests between KV manager `repository(...)` integration and `src/fsdantic/repository.py`.
- [ ] Use representative Pydantic models (required fields, optional fields, nested data).
- [ ] Validate save/load parity, model validation expectations, and key scoping compatibility.

### 5) Error-path tests

- [ ] Serialization failure test (unsupported type or forced serializer failure) for `set`.
- [ ] Deserialization failure test for `get` with malformed stored payload.
- [ ] Assert wrapped exceptions include useful context (operation + key + cause).
- [ ] Missing-key error/default tests confirm intended semantics and messages.

---

## Definition of Done

Phase 4 is complete when:

- KV operations present a straightforward API for simple use cases.
- Namespace and prefix behavior is deterministic, well-tested, and documented.
- Typed workflows are cleanly exposed via repository integration without architectural leakage.
- Serialization/deserialization failures produce clear, contextual diagnostics.
- The KV API is both easy to adopt for untyped usage and extensible for typed Pydantic model workflows.
