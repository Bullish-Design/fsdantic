# Refactoring Step 1 — Error Handling Foundation

## Scope

This phase implements the error-handling foundation described in Phase 1 of `.refactor/REFACTORING_GUIDE.md` and is intentionally limited to:

- `src/fsdantic/exceptions.py`
- `src/fsdantic/_internal/errors.py`

---

## 1) Goal and Non-Goals

### Goal

Build a complete FSdantic domain exception taxonomy and a boundary translation layer so AgentFS-specific `agentfs_sdk.ErrnoException` errors are converted into FSdantic exceptions before crossing public FSdantic APIs.

### Non-Goals

- No backward-compatibility layer is required for legacy exception classes or old import paths.
- No API surface redesign outside the two scoped files.
- No behavior changes unrelated to error taxonomy and translation.

---

## 2) Implementation Checklist

### A. Domain exception taxonomy (`src/fsdantic/exceptions.py`)

- [ ] Add root exception: `FsdanticError`.
- [ ] Add filesystem domain base: `FileSystemError` with contextual attributes (at minimum `path`, and cause support).
- [ ] Add concrete filesystem subclasses:
  - [ ] `FileNotFoundError`
  - [ ] `FileExistsError`
  - [ ] `NotADirectoryError`
  - [ ] `IsADirectoryError`
  - [ ] `DirectoryNotEmptyError`
  - [ ] `PermissionError`
  - [ ] `InvalidPathError`
- [ ] Add KV/overlay/materialization/validation/search hierarchy as domain-level FSdantic errors, including structured fields where relevant:
  - [ ] `KVStoreError`, `KeyNotFoundError` (`key`)
  - [ ] `SerializationError`
  - [ ] `OverlayError`, `MergeConflictError` (`conflicts`)
  - [ ] `MaterializationError`
  - [ ] `ValidationError`
  - [ ] `ContentSearchError`

### B. Translation layer (`src/fsdantic/_internal/errors.py`)

- [ ] Define a mapping table for known `ErrnoException.code` values:
  - [ ] `ENOENT -> FileNotFoundError`
  - [ ] `EEXIST -> FileExistsError`
  - [ ] `ENOTDIR -> NotADirectoryError`
  - [ ] `EISDIR -> IsADirectoryError`
  - [ ] `ENOTEMPTY -> DirectoryNotEmptyError`
  - [ ] `EPERM -> PermissionError`
  - [ ] `EINVAL -> InvalidPathError`
- [ ] Ensure unknown errno codes map to a generic filesystem-level exception (`FileSystemError`).
- [ ] Implement `translate_agentfs_error(error, context="") -> FsdanticError`.
  - [ ] Include operation context in message (e.g., function name or action string).
  - [ ] Preserve path metadata from low-level error where available.
  - [ ] Attach low-level exception as both explicit cause payload (if modeled) and Python exception chaining source.
- [ ] Add `handle_agentfs_errors` decorator for async boundary methods.
  - [ ] Catch `ErrnoException` only.
  - [ ] Re-raise translated FSdantic exception using `raise ... from e`.

---

## 3) Architecture Acceptance Criteria

- No raw `ErrnoException` escapes from public FSdantic API boundaries.
- Every translated exception message includes operation context.
- Original low-level exception is preserved as the exception cause (`__cause__`) and available to diagnostics.

---

## 4) Detailed Testing Plan

Create/extend `tests/test_errors.py` with the following coverage.

### A. Per-errno mapping tests

- [ ] Unit test per known errno code (`ENOENT`, `EEXIST`, `ENOTDIR`, `EISDIR`, `ENOTEMPTY`, `EPERM`, `EINVAL`) asserting exact translated exception class.
- [ ] Assert translated exception includes contextual message content.
- [ ] Assert expected structured attributes are preserved/populated (e.g., `path`).

### B. Parametrized translation matrix

- [ ] Parametrized test for `(errno_code, expected_exception_class)` across all known mappings.
- [ ] Include default fallback case for unknown errno (e.g., `EUNKNOWN`) -> `FileSystemError`.

### C. Cause chaining and metadata

- [ ] Assert `translated_exc.__cause__ is original_errno_exc` for decorator-mediated paths.
- [ ] Assert structured attributes where applicable:
  - [ ] `path` on filesystem errors
  - [ ] `key` on `KeyNotFoundError`
  - [ ] `conflicts` on `MergeConflictError`

### D. Negative and robustness tests

- [ ] Malformed error object tests (missing `code`, missing `message`, missing `path`) to verify safe/explicit behavior.
- [ ] Non-`ErrnoException` passthrough tests for decorator behavior (exceptions must not be incorrectly translated).

### E. Mutation-oriented safety checks

- [ ] Add at least one assertion pattern that would fail if any single known errno mapping is removed or misrouted.
  - Recommendation: enforce exact matrix equality and validate coverage count for known mappings.

---

## 5) Definition of Done

- Public FSdantic APIs in scope raise only FSdantic exception types (not raw AgentFS errno exceptions).
- Translation logic handles all required known errno codes and unknown fallback.
- Exception messages include operation context.
- Cause chaining (`raise ... from e`) is validated by tests.
- `tests/test_errors.py` passes with high branch coverage over translation logic and decorator paths.
