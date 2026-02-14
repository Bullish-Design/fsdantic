# Phase 3 Refactoring Plan: `files.py` + `view.py` Integration

## Scope

- Replace the fragmented file I/O and search surface area with a single `FileManager` API centered in `src/fsdantic/files.py`.
- Consolidate filesystem-facing operations currently spread across helper methods and view-adjacent query behavior.
- Integrate search/query/tree capabilities with `src/fsdantic/view.py` so call sites can rely on one coherent file access layer.

---

## Implementation Checklist

### Core `FileManager` surface

- [ ] Implement `read(...)`
  - [ ] Support explicit text/binary mode semantics.
  - [ ] Respect encoding when reading text.
  - [ ] Return stable, documented output types.
- [ ] Implement `write(...)`
  - [ ] Support text and binary payloads.
  - [ ] Add JSON auto-serialization for `dict`/`list` input.
  - [ ] Document overwrite behavior and parent directory expectations.
- [ ] Implement `exists(...)`
  - [ ] Provide cheap existence checks with normalized paths.
- [ ] Implement `stat(...)`
  - [ ] Return consistent metadata shape.
  - [ ] Normalize interpretation across AgentFS and local/materialized contexts.
- [ ] Implement `list_dir(...)`
  - [ ] Define deterministic ordering.
  - [ ] Clarify whether results are names, relative paths, or full paths.
- [ ] Implement `remove(...)`
  - [ ] Support file deletion and directory deletion policy (recursive/non-recursive).
  - [ ] Enforce explicit behavior for non-empty directories.
- [ ] Implement `search(...)`
  - [ ] Pattern-driven matching (glob semantics).
  - [ ] Integrate filtering options in a single API path.
- [ ] Implement `query(...)`
  - [ ] Provide structured query contract (pattern, depth, size/type filters, etc.).
  - [ ] Delegate traversal behavior consistently to view integration.
- [ ] Implement `tree(...)`
  - [ ] Recursive traversal with depth controls.
  - [ ] Stable output schema suitable for display and further filtering.

### Path normalization and canonical rules

- [ ] Define and document canonical path behavior (absolute vs relative input handling).
- [ ] Normalize separators, dot-segments (`.`/`..`), and duplicate slashes.
- [ ] Standardize root handling and trailing slash interpretation.
- [ ] Ensure `files.py` and `view.py` apply the same normalization rules.

### Serialization and content rules

- [ ] Define JSON auto-serialization behavior for `write` when payload is `dict` or `list`.
  - [ ] Default encoding and indentation policy.
  - [ ] File extension coupling rules (if any) and explicit override behavior.
- [ ] Clarify text/binary read/write compatibility and error behavior for mismatched mode/content.

### Error translation strategy

- [ ] Apply decorators and/or explicit wrappers that translate low-level filesystem exceptions.
- [ ] Map common filesystem failures to predictable high-level exceptions.
- [ ] Ensure errors include normalized path context for diagnostics.

---

## Architecture Acceptance Criteria

- A single coherent file API exists through `FileManager`, with no ambiguous overlap in file operation entry points.
- Read/write semantics are predictable across text and binary modes.
- Search/query/tree functionality is available through the same cohesive abstraction and consistently integrated with `view.py`.
- Path normalization is deterministic and documented.
- Error behavior is consistent regardless of which method path triggers filesystem access.

---

## Detailed Testing Plan

### `tests/test_files.py` structure

- [ ] Add/expand table-driven tests for read/write content types and encodings:
  - [ ] `str` content with explicit encoding.
  - [ ] `bytes` content round-trip.
  - [ ] `dict`/`list` JSON auto-serialization and read-back validation.
  - [ ] Edge cases: empty content, unicode characters, invalid encoding configuration.

### Search and query behavior

- [ ] Add pattern search tests for:
  - [ ] `*.py`
  - [ ] `**/*.json`
- [ ] Validate filter combinations (e.g., pattern + type + depth/size where applicable).
- [ ] Verify behavior parity between direct `FileManager` search/query and `view.py` integration call paths.

### Tree traversal and recursion

- [ ] Add directory recursion/depth tests for `tree(...)`:
  - [ ] Depth = 0 / 1 / N semantics.
  - [ ] Nested directory ordering and representation.
  - [ ] Mixed file + directory fixtures.

### Error-path coverage

- [ ] Missing path cases (`read`, `stat`, `remove`).
- [ ] Wrong type cases (attempting file ops on directory and directory ops on file).
- [ ] Recursive delete behavior:
  - [ ] Non-recursive delete on non-empty directory should fail predictably.
  - [ ] Recursive delete should remove complete subtree.

### Performance-sensitive fixtures (non-benchmark)

- [ ] Add large fixture tests for methods expected to traverse many files (e.g., `search`, `query`, `tree`).
- [ ] Validate completion and correctness on moderately large fixture sets without dedicated benchmark tooling.
- [ ] Keep tests deterministic and bounded for CI reliability.

### Property-based tests

- [ ] Add property-based tests for path normalization edge cases:
  - [ ] Randomized separator patterns.
  - [ ] Dot-segment combinations.
  - [ ] Relative/absolute path input normalization invariants.

---

## Definition of Done

- File operations behave consistently regardless of call path.
- `FileManager` is the authoritative file I/O + search/query/tree surface.
- Path, content, and error semantics are documented and enforced by tests.
- Integration points with `src/fsdantic/view.py` are validated for behavioral consistency.
