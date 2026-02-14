"""Helpers for local AgentFS session lifecycle commands.

These wrappers intentionally delegate mount/unmount behavior to AgentFS CLI
built-ins (`init --base` and `exec`).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


class SessionError(RuntimeError):
    """User-facing session management error."""


def _session_db_path(root: Path, project_name: str) -> Path:
    return root / ".agentfs" / f"{project_name}.db"


def require_project_name(project_name: str | None) -> str:
    value = (project_name or "").strip()
    if not value:
        raise SessionError("PROJECT_NAME must be set")
    return value


def create_session(*, root: Path, project_name: str, agentfs_bin: str) -> int:
    db_path = _session_db_path(root, project_name)
    if db_path.exists():
        print(f"AgentFS session '{project_name}' already exists")
        return 0

    cmd = [agentfs_bin, "init", project_name, "--base", str(root)]
    result = subprocess.run(cmd, cwd=root, check=False)
    return result.returncode


def shell_session(*, root: Path, project_name: str, agentfs_bin: str, shell: str) -> int:
    db_path = _session_db_path(root, project_name)
    if not db_path.exists():
        rc = create_session(root=root, project_name=project_name, agentfs_bin=agentfs_bin)
        if rc != 0:
            return rc

    # agentfs exec mounts for command execution and automatically unmounts on exit.
    cmd = [agentfs_bin, "exec", project_name, shell, "-l"]
    result = subprocess.run(cmd, cwd=root, check=False)
    return result.returncode


def boot_session(*, root: Path, project_name: str, agentfs_bin: str, shell: str) -> int:
    rc = create_session(root=root, project_name=project_name, agentfs_bin=agentfs_bin)
    if rc != 0:
        return rc
    return shell_session(root=root, project_name=project_name, agentfs_bin=agentfs_bin, shell=shell)


def main_create(argv: list[str]) -> int:
    root = Path(os.environ.get("NIXBOX_ROOT", os.getcwd())).resolve()
    project_name = require_project_name(os.environ.get("PROJECT_NAME"))
    agentfs_bin = os.environ.get("AGENTFS_BIN", "agentfs")
    return create_session(root=root, project_name=project_name, agentfs_bin=agentfs_bin)


def main_shell(argv: list[str]) -> int:
    root = Path(os.environ.get("NIXBOX_ROOT", os.getcwd())).resolve()
    project_name = require_project_name(os.environ.get("PROJECT_NAME"))
    agentfs_bin = os.environ.get("AGENTFS_BIN", "agentfs")
    shell = os.environ.get("SHELL", "bash")
    return shell_session(root=root, project_name=project_name, agentfs_bin=agentfs_bin, shell=shell)


def main_boot(argv: list[str]) -> int:
    root = Path(os.environ.get("NIXBOX_ROOT", os.getcwd())).resolve()
    project_name = require_project_name(os.environ.get("PROJECT_NAME"))
    agentfs_bin = os.environ.get("AGENTFS_BIN", "agentfs")
    shell = os.environ.get("SHELL", "bash")
    return boot_session(root=root, project_name=project_name, agentfs_bin=agentfs_bin, shell=shell)


def _run_entry(fn) -> None:
    try:
        raise SystemExit(fn(sys.argv[1:]))
    except SessionError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
