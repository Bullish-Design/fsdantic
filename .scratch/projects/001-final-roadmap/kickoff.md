# Fsdantic — remaining roadmap kickoff (clean session)

Complete the two remaining roadmap items for fsdantic: README/docs parity and
coverage hardening. The repo is released at v0.6.0 with a fresh dependency stack;
this prompt is self-contained.

## Repo state (verified 2026-08-02)

- **Repo:** `/home/andrew/Documents/Projects/fsdantic` — `main` @ `ff26155`, tag
  `v0.6.0` (pushed to `git@github.com:Bullish-Design/fsdantic.git`). Working
  tree clean. **Do not rebase.**
- **Dependency stack (do NOT change):**
  - `agentfs-sdk @ git+https://github.com/Bullish-Design/agentfs@v0.6.4-pyturso-0.7.2#subdirectory=sdk/python`
    — fork of upstream v0.6.4; SDK module code is byte-identical to the vendored
    `.context/agentfs-main/sdk/python`. The fork's one patch bumps the pyturso pin.
  - `pyturso>=0.7.2,<0.8` (0.7.2 installed; Limbo engine: GIL-releasing busy-wait,
    `PRAGMA journal_mode="mvcc"` supported).
  - `pydantic>=2.0.0`. **No new third-party dependencies may be added.**
- **Venv:** `/tmp/fsdvenv-fork` (agentfs-sdk 0.6.4 fork + pyturso 0.7.2 + fsdantic
  editable + dev deps). Tests: `/tmp/fsdvenv-fork/bin/python -m pytest tests/ -q`
  → **435 passed, 4 skipped**. Lint: `/tmp/fsdvenv-fork/bin/ruff check src/`
  (must stay clean).
  - If the venv is gone, recreate:
    `python3 -m venv /tmp/fsdvenv-fork && /tmp/fsdvenv-fork/bin/pip install -U pip &&
    /tmp/fsdvenv-fork/bin/pip install -e . && /tmp/fsdvenv-fork/bin/pip install pytest pytest-asyncio pytest-cov hypothesis ruff`
    (`pip install -e .` resolves the fork + pyturso 0.7.2 automatically;
    `allow-direct-references` is already set in pyproject.toml).

## Constraints (non-negotiable)

1. No changes to the vendored SDK (`.context/`) — reference-only; do not edit.
2. No new third-party dependencies.
3. Backwards compatible: public API unchanged; full suite stays green.
4. `src/` must stay ruff-clean; new/modified files ruff-clean.
5. Follow `AGENTS.md` skills: verify SDK API usage against the vendored SDK,
   use typed fsdantic exceptions, type hints.
6. `tests/` has pre-existing lint errors in `conftest.py`/`test_kv_records.py`/
   `test_paths.py`/`test_repository.py` — do NOT "fix" them.

## Item 1 — README/docs parity: Concurrency & Read-only section

The five-phase refactor (F1 readonly, F2 busy_timeout, F3 kv.increment, F4 content
caps, F5 base-union query) is in CHANGELOG and `docs/concurrency.md`, but
**README.md has no user-facing section** covering read-only workspaces,
`busy_timeout_ms`, MVCC, and `Workspace.serialized()`.

Do:
- Add a "Concurrency & read-only" section to README.md (short snippets matching
  the existing quickstart style): WAL mode + `busy_timeout_ms` (default 5000; 0 =
  fail fast; GIL released during busy-wait on pyturso 0.7.2), MVCC mode
  (`enable_mvcc=True`; conflicts are NOT reliably surfaced by the driver — use
  `Workspace.serialized()` or the repository CAS), read-only workspaces
  (`readonly=True`; writes raise `WorkspaceError` WORKSPACE_READONLY; missing DB
  raises WORKSPACE_NOT_FOUND).
- Verify every claim against actual behavior (`client.py` docstrings,
  `docs/concurrency.md`; run a probe if unsure). Do NOT resurrect the stale
  "conflicting writes raise DatabaseError at execute" contract — removed in 0.6.0.
- Cross-link README ↔ `docs/concurrency.md` ↔ `docs/dependencies.md`.

## Item 2 — Coverage hardening (targets verified 2026-08-02)

Current total: 87% (231 missed statements). The three worst modules:

1. **`src/fsdantic/_internal/readonly.py` — 75%** (12 missed). Untested branches:
   - `_first_keyword` comment-skipping: `--` line comments and `/* */` block
     comments (lines ~64-81).
   - `_ReadonlyGuard.executemany` write-rejection branch (lines ~158-163): locked
     guard + `executemany` with INSERT/UPDATE/DELETE must raise `WorkspaceError`
     (WORKSPACE_READONLY).
   - Re-verify while there: the atime-swallow (exact `UPDATE FS_INODE SET ATIME`
     prefix), `executescript` rejection when locked, and pass-through when
     unlocked.
2. **`src/fsdantic/operations.py` — 72%** (4 missed). The deprecated
   `FileOperations` alias methods are untested: `read_file(encoding=None)` binary
   path, `write_file`, `file_exists`, `search_files`. Simple direct tests.
3. **`src/fsdantic/overlay.py` — 79%** (37 missed). Missed lines: 145-152, 164,
   195-202, 218-219, 231->204, 244, 274-275, 310-311, 313, 347-357, 401-402.
   Read those branches and add tests — likely error/partial-failure paths (merge
   with per-item failures, `reset_overlay` partial failure, conflict-resolution
   paths). Confirm with `coverage report -m` after a full `pytest tests/ -q` run.

Target: each of the three modules ≥ 90%, without weakening assertions or testing
implementation trivia — every new test asserts real behavior. (Optional stretch:
`src/fsdantic/materialization.py` at 82%.)

## Definition of done

1. `pytest tests/ -q` green — ≥ 435 passed, 4 skipped (no regressions; new tests
   add to the count).
2. `ruff check src/` clean; new/modified files ruff-clean.
3. Coverage: the three target modules each ≥ 90% (`coverage report -m`).
4. No `.context/` changes (`git diff HEAD -- .context/` empty); no new deps in
   `pyproject.toml`.
5. README section renders and matches actual behavior (spot-check: open a readonly
   workspace, open with `enable_mvcc=True`, read `PRAGMA journal_mode`).
6. One commit per logical unit (e.g. `docs(readme): concurrency & read-only
   section`, `test(readonly): cover comment-skip + executemany rejection`,
   `test(overlay): cover error paths`, `test(operations): cover FileOperations
   alias`) or a single well-documented commit + summary. Commit on `main`.
7. Final report: files touched, coverage before/after per module, test counts
   before/after.

## Useful references

- `AGENTS.md` — agent skills (SDK code search, error handling, testing patterns).
- `docs/concurrency.md`, `docs/dependencies.md` — current documented contracts
  (source of truth for Item 1).
- `CHANGELOG.md` — 0.6.0 entry explains the current MVCC/conflict reality.
- Test patterns: `tests/test_readonly.py`, `tests/test_concurrency.py`,
  `tests/test_overlay.py`, `tests/test_phase2_regressions.py` (merge conflicts).
