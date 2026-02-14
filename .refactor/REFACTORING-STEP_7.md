# Phase 7 — Documentation Rewrite (Workspace-First)

## Scope

Rewrite `README.md`, `MIGRATION.md`, and `examples/basic_usage.py` to fully reflect the new workspace-first architecture.

- Prioritize future quality, clarity, and maintainability over backward compatibility messaging.
- Treat documentation as product UX: optimize for first-time success with the modern API surface.
- Remove ambiguous legacy framing that could imply support commitments not aligned with the new direction.

---

## Implementation Checklist

### 1) Quickstart + Common Flows

Update primary docs to show a clear, opinionated onboarding flow centered on the workspace entry point.

- [ ] `README.md` quickstart starts with `Fsdantic.open(...)` and shows lifecycle/cleanup expectations.
- [ ] Include end-to-end examples for:
  - [ ] File operations (read/write/search/stat/list/remove)
  - [ ] KV operations (set/get/list/delete and typed repository usage where applicable)
  - [ ] Overlay operations (merge/list changes/reset with strategy intent)
  - [ ] Materialization flow (preview/diff/to-disk patterns and expected outcomes)
- [ ] Ensure each flow demonstrates preferred patterns, not low-level or legacy-first alternatives.
- [ ] Align `examples/basic_usage.py` to mirror README ordering and terminology so users can follow linearly.

### 2) Error Handling Guidance

Provide practical, copy-paste-safe error handling with the new exception model.

- [ ] Add explicit examples that catch and explain new fsdantic exceptions.
- [ ] Show boundary-level handling patterns (e.g., not found, invalid input, conflict/merge failures, serialization/validation).
- [ ] Include guidance on when to recover vs. when to re-raise.
- [ ] Keep error examples consistent across `README.md`, `MIGRATION.md`, and `examples/basic_usage.py`.

### 3) Migration Notes Positioning

Restructure `MIGRATION.md` as a reference mapping, not a compatibility guarantee.

- [ ] Frame migration sections as **“old vs new”** comparisons.
- [ ] Explicitly state intent: reference for conceptual translation only.
- [ ] Avoid language that promises adapters/shims unless they actually exist in code.
- [ ] Emphasize recommended rewrite patterns into workspace-first APIs.

---

## Architecture Acceptance Criteria

Documentation is acceptable only when it accurately mirrors the implemented architecture and guides users toward intended usage.

- [ ] Docs reflect current code organization and naming (workspace + domain managers).
- [ ] Examples encourage preferred high-level flows before any raw/advanced escape hatches.
- [ ] Terminology is consistent across files (workspace, files, kv, overlay, materialization, raw access).
- [ ] No architectural contradictions between README, migration guide, and runnable example.
- [ ] User journeys in docs match how maintainers expect the library to be used in production.

---

## Detailed Testing Plan

### A) Documentation Validation Checklist

- [ ] All Python snippets in `README.md` and `MIGRATION.md` are extracted and executed via CI doc-test/snippet harness.
- [ ] Snippets pass formatting/linting/type-check gates configured for docs examples.
- [ ] Snippets use imports and APIs that exist in current package exports.

### B) Example Script Execution

- [ ] Validate `examples/basic_usage.py` in a clean environment (no hidden local state).
- [ ] Run against editable install (`pip install -e .`) and confirm script completes successfully.
- [ ] Verify output/messages align with the documented expectations.

### C) Link Integrity Checks

- [ ] Check internal anchors and relative links in `README.md`.
- [ ] Check internal anchors and relative links in `MIGRATION.md`.
- [ ] Validate cross-references between README and migration content.
- [ ] Ensure links to examples and API references resolve.

### D) Manual Copy-Paste Onboarding Validation

Perform a human-style walkthrough using only docs:

- [ ] New environment setup from README.
- [ ] Quickstart execution succeeds without consulting source files.
- [ ] File flow works as documented.
- [ ] KV flow works as documented.
- [ ] Overlay/materialization flow works as documented.
- [ ] Error handling snippets behave as described.
- [ ] Migration reader can map old concepts to new APIs without ambiguity.

---

## Definition of Done

Phase 7 is complete when all of the following are true:

- [ ] `README.md`, `MIGRATION.md`, and `examples/basic_usage.py` are workspace-first and mutually consistent.
- [ ] All documentation and snippet validation checks pass in CI/local verification.
- [ ] Migration content is clearly reference-oriented (“old vs new”), not a compatibility promise.
- [ ] A new user can complete primary workflows from docs alone, without reading source code.
