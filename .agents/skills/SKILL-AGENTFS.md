# SKILL: AgentFS workflow

Use this skill when changing storage behavior or AgentFS integration code.

Architecture context lives in [CONCEPT.md](../../CONCEPT.md) and [SPEC.md](../../SPEC.md).

## Workflow

1. Identify whether the change touches stable state, overlay state, or both.
2. Verify overlay contract is preserved: read fallthrough, overlay-only write, explicit accept/reject.
3. Update integration points in orchestrator/executor code.
4. Run relevant tests (storage + orchestrator contracts).
5. If behavior changed, update `SPEC.md` contract section.

## Minimal snippets

```python
stable = await AgentFS.open(AgentFSOptions(id="stable"))
agent = await AgentFS.open(AgentFSOptions(id=f"agent-{agent_id}"))
```
