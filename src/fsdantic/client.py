"""High-level fsdantic client entrypoint."""

from agentfs_sdk import AgentFS, AgentFSOptions as SDKAgentFSOptions

from .models import AgentFSOptions
from .workspace import Workspace


class Fsdantic:
    """Factory/entrypoint for opening fsdantic workspaces."""

    @classmethod
    async def open(cls, *, id: str | None = None, path: str | None = None) -> Workspace:
        """Open a workspace by ID or path.

        Exactly one of ``id`` or ``path`` must be provided.
        """
        options = AgentFSOptions(id=id, path=path)
        return await cls.open_with_options(options)

    @classmethod
    async def open_with_options(cls, options: AgentFSOptions) -> Workspace:
        """Open a workspace from validated options."""
        sdk_options = SDKAgentFSOptions(id=options.id, path=options.path)
        agentfs = await AgentFS.open(sdk_options)
        return Workspace(agentfs)
