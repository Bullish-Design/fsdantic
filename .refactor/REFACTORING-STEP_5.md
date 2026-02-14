# Phase 5: Workspace Managers for Overlay and Materialization

## Scope

Wrap the existing advanced operations in `src/fsdantic/overlay.py` and `src/fsdantic/materialization.py` behind manager-style workspace APIs so callers can use these capabilities from `Workspace` ergonomically.

## Implementation Checklist

- [ ] Add `OverlayManager` workspace-facing API:
  - [ ] `merge(...)`
  - [ ] `list_changes(...)`
  - [ ] `reset(...)`
- [ ] Add `MaterializationManager` workspace-facing API:
  - [ ] `to_disk(...)`
  - [ ] `diff(...)`
  - [ ] `preview(...)`
- [ ] Ensure interoperability via `Workspace.raw` for source/base workspaces so manager methods can compose between wrapped and raw AgentFS-backed contexts.
- [ ] Keep existing low-level modules as implementation engines (`overlay.py`, `materialization.py`) while exposing stable, discoverable workspace entry points.

## Architecture Acceptance Criteria

- Advanced overlay and materialization operations are discoverable directly from workspace APIs.
- Workspace users can perform complex operations without interacting with lower-level strategy plumbing unless they explicitly opt in.
- Manager abstractions do not hide error semantics; failures and partial results remain inspectable.
- The design avoids leaking internal complexity while preserving full capability for power users.

## Detailed Testing

### `tests/test_overlay.py`

- Validate strategy behavior for all supported merge strategies.
- Verify conflict handling paths and error reporting shape.
- Test reset granularity (single path, subtree, and full reset semantics as applicable).

### `tests/test_materialization.py`

- Validate clean vs non-clean export behavior.
- Validate diff correctness across added/removed/modified files.
- Validate `preview(...)` alias/contract behavior relative to `diff(...)` (or documented equivalent).

### Cross-workspace integration tests

- Add integration coverage for end-to-end workflows that combine merge + materialize operations across source/base workspaces.
- Confirm manager APIs correctly accept and coordinate other workspace instances through `Workspace.raw`.

### Failure-mode tests

- Permission and path conflict handling.
- Partial-result reporting when operations encounter recoverable per-file failures.
- Error propagation for unrecoverable workflow failures.

## Definition of Done

Overlay and materialization operations are available through idiomatic workspace manager APIs and remain robust under failure, with comprehensive tests validating happy paths, conflict behavior, and partial-failure reporting.
