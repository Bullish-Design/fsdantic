#!/usr/bin/env -S uv run --script
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def git_repo_root(cwd: Path) -> Path | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return Path(out)
    except Exception:
        return None


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description="Symlink an absolute source path to a destination path relative to the repo root."
    )
    p.add_argument("src", help="Absolute source path to link to")
    p.add_argument("dest_rel", help="Destination path relative to repo root (e.g. .cache/foo)")
    p.add_argument("--root", help="Override repo root (defaults to git top-level, else cwd)")
    p.add_argument("--dry-run", action="store_true", help="Print what would happen without changing anything")
    args = p.parse_args(argv)

    src = Path(args.src)
    if not src.is_absolute():
        print(f"error: src must be an absolute path: {src}", file=sys.stderr)
        return 2

    dest_rel = Path(args.dest_rel)
    if dest_rel.is_absolute():
        print(f"error: dest_rel must be relative (got absolute): {dest_rel}", file=sys.stderr)
        return 2

    root = Path(args.root) if args.root else (git_repo_root(Path.cwd()) or Path.cwd())
    dest = root / dest_rel

    if args.dry_run:
        print(f"[dry-run] root: {root}")
        print(f"[dry-run] would symlink: {dest} -> {src}")
        return 0

    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() or dest.is_symlink():
        if dest.is_dir() and not dest.is_symlink():
            print(f"error: destination exists and is a directory: {dest}", file=sys.stderr)
            return 3
        dest.unlink()

    os.symlink(str(src), str(dest))
    print(f"{dest} -> {src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
