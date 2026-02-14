#!/usr/bin/env -S uv run --script
from agentfs_session_lib import _run_entry, main_shell


if __name__ == "__main__":
    _run_entry(main_shell)
