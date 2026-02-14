# Refactoring Step 6: Public API Curation and Internal Boundary Stabilization

## Phase Focus
**Target areas:** `src/fsdantic/__init__.py` and `src/fsdantic/_internal/`

This phase finalizes the package surface by publishing a clean, explicit public API while keeping internals private, organized, and intentionally bounded.

---

## 1) Scope

- Publish a clean public API that is easy to discover and hard to misuse.
- Keep internal helpers under `src/fsdantic/_internal/` private and structurally coherent.
- Ensure exported symbols match the long-term architecture rather than short-term compatibility shims.

---

## 2) Implementation Checklist

- [ ] **Export new entrypoint and managers** from `src/fsdantic/__init__.py`.
  - Include the canonical top-level entrypoint.
  - Include manager/facade types required for the primary workflow.

- [ ] **Export models, advanced interfaces, and exception hierarchy intentionally**.
  - Curate `__all__` to include supported models and advanced APIs.
  - Export the official exception hierarchy for stable error handling.
  - Remove accidental or redundant re-exports.

- [ ] **Add `src/fsdantic/_internal/__init__.py` and stabilize internal module boundaries**.
  - Make internal package structure explicit.
  - Define what internal modules may import from each other.
  - Prevent internal-only modules from leaking into public imports.

- [ ] **Set version bump reflecting breaking refactor**.
  - Update version metadata for the breaking API reorganization.
  - Ensure changelog/release notes call out import-surface changes.

---

## 3) Architecture Acceptance Criteria

The architecture is accepted for this phase only if:

- Public API is **explicit** (clear `__all__`, no accidental exports).
- Public API is **minimal** (only strategic, supported symbols are exposed).
- Public API is **future-oriented** (no compatibility cruft, no legacy leakage).
- Internal implementation modules remain private by default and are not part of support guarantees.

---

## 4) Detailed Testing

### A. Import-contract tests (`__all__` assertions)
- Validate expected symbol set in `src/fsdantic/__init__.py::__all__`.
- Assert all required top-level symbols are present.
- Assert removed/deprecated symbols are absent.

### B. Top-level import smoke tests (README pathways)
- Execute smoke imports for usage patterns shown in README examples.
- Confirm the “happy path” import style works without extra indirection.

### C. Guard tests for internal re-export leakage
- Assert modules under `src/fsdantic/_internal/` are not exposed via top-level package imports.
- Add negative tests ensuring internal names are unavailable from `import fsdantic` unless explicitly public.

### D. Version assertion test
- Add a test asserting package version equals the expected post-refactor breaking version.
- Ensure version metadata remains synchronized with package configuration.

---

## 5) Definition of Done

Phase 6 is complete when:

- API consumers have **one obvious import surface**.
- Top-level symbols have **stable, intentional semantics**.
- Internal modules are structurally clear and stay private.
- Import-contract, smoke, guard, and version tests all pass.
