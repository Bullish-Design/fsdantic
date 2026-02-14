"""Workspace-first fsdantic usage walkthrough."""

import asyncio
from pathlib import Path

from pydantic import BaseModel

from fsdantic import (
    DirectoryNotEmptyError,
    FileNotFoundError,
    Fsdantic,
    KeyNotFoundError,
    MergeStrategy,
    ViewQuery,
)


class UserProfile(BaseModel):
    name: str
    role: str


async def main() -> None:
    """Run the primary fsdantic workflows in README order."""
    print("Opening workspaces...")
    async with await Fsdantic.open(id="fsdantic-demo-base") as base:
        async with await Fsdantic.open(id="fsdantic-demo-main") as workspace:
            print("\n1) File operations")
            await workspace.files.write("/docs/readme.txt", "hello workspace")
            await workspace.files.write("/config.json", {"debug": True}, mode="json")

            print("read:", await workspace.files.read("/docs/readme.txt"))
            print("exists /config.json:", await workspace.files.exists("/config.json"))
            print("stat /config.json size:", (await workspace.files.stat("/config.json")).size)
            print("list /docs:", await workspace.files.list_dir("/docs", output="name"))
            print("search **/*.txt:", await workspace.files.search("**/*.txt"))

            queried = await workspace.files.query(
                ViewQuery(path_pattern="**/*.txt", include_stats=True, include_content=False)
            )
            print("query matches:", [entry.path for entry in queried])

            print("\n2) KV operations")
            await workspace.kv.set("app:theme", "dark")
            print("kv get app:theme:", await workspace.kv.get("app:theme"))
            print("kv list app:", await workspace.kv.list(prefix="app:"))

            repo = workspace.kv.repository(prefix="users:", model_type=UserProfile)
            await repo.save("alice", UserProfile(name="Alice", role="admin"))
            print("typed load alice:", await repo.load("alice"))
            print("typed list_all:", await repo.list_all())

            print("\n3) Overlay operations")
            await base.files.write("/shared/file.txt", "base value")
            await workspace.files.write("/shared/file.txt", "overlay value")

            merge_result = await workspace.overlay.merge(base, strategy=MergeStrategy.PRESERVE)
            print(
                "merge result:",
                {
                    "files_merged": merge_result.files_merged,
                    "conflicts": len(merge_result.conflicts),
                    "errors": len(merge_result.errors),
                },
            )
            print("overlay changes:", await workspace.overlay.list_changes("/"))

            print("\n4) Materialization")
            preview = await workspace.materialize.preview(base)
            diff = await workspace.materialize.diff(base)
            print("preview paths:", [c.path for c in preview])
            print("diff paths:", [c.path for c in diff])

            out_dir = Path(".tmp/materialized-example")
            result = await workspace.materialize.to_disk(out_dir, base=base, clean=True)
            print(
                "to_disk:",
                {
                    "target": str(result.target_path),
                    "files_written": result.files_written,
                    "bytes_written": result.bytes_written,
                    "errors": len(result.errors),
                },
            )

            print("\n5) Error handling patterns")
            try:
                await workspace.files.read("/does-not-exist.txt")
            except FileNotFoundError:
                print("recovered FileNotFoundError with fallback")

            try:
                await workspace.kv.get("settings:timezone")
            except KeyNotFoundError:
                print("recovered KeyNotFoundError with default timezone")

            await workspace.files.write("/tmp/keep.txt", "x")
            try:
                await workspace.files.remove("/tmp", recursive=False)
            except DirectoryNotEmptyError:
                await workspace.files.remove("/tmp", recursive=True)
                print("handled DirectoryNotEmptyError via recursive remove")

            await workspace.kv.delete("app:theme")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
