# Dependency stack: the agentfs-sdk fork

fsdantic depends on the AgentFS SDK and the pyturso (Turso/libSQL) driver:

- `agentfs-sdk @ git+https://github.com/Bullish-Design/agentfs@v0.6.4-pyturso-0.7.2#subdirectory=sdk/python`
- `pyturso>=0.7.2,<0.8`

## Why the fork exists

Upstream `tursodatabase/agentfs` pins `pyturso==0.4.4` in `sdk/python/pyproject.toml`
(verified through the 0.6.4 release). Fsdantic needs pyturso **0.7.2** for two
driver behaviors that 0.4.4 lacks (both verified by probe):

1. **GIL-releasing busy-wait** — on 0.4.4 a contended async write holds the GIL
   for the full `busy_timeout_ms` (event loop frozen); on 0.7.2 the wait
   releases the GIL and an in-process lock release unblocks the waiter.
2. **MVCC journaling** — `PRAGMA journal_mode = "mvcc"` exists on 0.7.2
   (Limbo engine); 0.4.4 rejects it ("Unknown journal mode").

A dependency graph cannot carry both the SDK's `pyturso==0.4.4` and fsdantic's
`pyturso>=0.7.2,<0.8` — the pins are disjoint, so pip has no valid solution.
The fork breaks the deadlock by bumping the SDK's own pin. The SDK **code** is
upstream v0.6.4 unchanged (byte-identical to the vendored copy at
`.context/agentfs-main/sdk/python`); only `sdk/python/pyproject.toml` differs.

`tests/test_public_api_contract.py::test_dependency_stack_reproducibility`
guards this contract: if a fresh install resolves pyturso < 0.7.2, the suite
fails with a clear message.

## Fork contents (tag `v0.6.4-pyturso-0.7.2`)

- Full upstream `tursodatabase/agentfs` history at v0.6.4 (`main` reset).
- One extra commit: `chore(deps): bump pyturso pin to >=0.7.2,<0.8`.

The vendored `.context/agentfs-main/` copy is **reference-only** — it is used
by the AGENTS.md code-search workflow and stays frozen; do not edit it.

## Refreshing the fork when upstream moves

1. Fetch upstream into a working clone:
   `git fetch upstream && git checkout upstream/main`
2. Diff the SDK code against the previous fork state — the fork must stay
   code-identical to upstream (or carry reviewed changes only):
   `git diff <old-ref> HEAD -- sdk/python/agentfs_sdk/`
3. Keep the pyturso pin bump (`sdk/python/pyproject.toml`): confirm it still
   reads `"pyturso>=0.7.2,<0.8"` after the merge.
4. Bump the SDK version in `sdk/python/pyproject.toml` and
   `sdk/python/agentfs_sdk/__init__.py` only if upstream did (the fork keeps
   upstream's version string; the tag identifies the fork state).
5. Commit, tag (`v<version>-pyturso-<pyturso-version>`), force-push `main`
   and push the tag to `github.com:Bullish-Design/agentfs`.
6. Update the git ref in `pyproject.toml` (`dependencies`) and re-verify:
   clean venv, `pip install -e .`, full suite (`pytest tests/ -q`) + `ruff
   check src/`. The reproducibility contract test must stay green.
