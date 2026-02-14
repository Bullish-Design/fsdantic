"""Example usage of agentfs-pydantic library."""

import asyncio
from datetime import datetime

from agentfs_sdk import AgentFS
from fsdantic import AgentFSOptions, View, ViewQuery


async def main():
    """Demonstrate basic usage of agentfs-pydantic."""

    # Example 1: Create AgentFS with validated options
    print("=" * 60)
    print("Example 1: Creating AgentFS with Pydantic models")
    print("=" * 60)

    options = AgentFSOptions(id="demo-agent")
    print(f"Created options: {options.model_dump_json(indent=2)}")

    async with await AgentFS.open(options.model_dump()) as agent:
        print("✓ Successfully opened AgentFS")

        # Example 2: Create some sample files
        print("\n" + "=" * 60)
        print("Example 2: Creating sample files")
        print("=" * 60)

        await agent.fs.write_file("/notes/todo.txt", "1. Write documentation\n2. Add tests")
        await agent.fs.write_file("/notes/ideas.md", "# Ideas\n\n- Feature A\n- Feature B")
        await agent.fs.write_file("/config/settings.json", '{"theme": "dark", "lang": "en"}')
        await agent.fs.write_file("/data/results.csv", "id,value\n1,100\n2,200")

        print("✓ Created 4 sample files")

        # Example 3: Query all files
        print("\n" + "=" * 60)
        print("Example 3: Query all files")
        print("=" * 60)

        view = View(agent=agent, query=ViewQuery(path_pattern="*", recursive=True))
        all_files = await view.load()

        print(f"Found {len(all_files)} files:")
        for file in all_files:
            if file.stats:
                print(f"  {file.path} ({file.stats.size} bytes)")

        # Example 4: Query only markdown files
        print("\n" + "=" * 60)
        print("Example 4: Query only markdown files")
        print("=" * 60)

        md_view = View(
            agent=agent,
            query=ViewQuery(
                path_pattern="*.md",
                recursive=True,
                include_content=True
            )
        )
        md_files = await md_view.load()

        print(f"Found {len(md_files)} markdown files:")
        for file in md_files:
            print(f"\n  Path: {file.path}")
            if file.content:
                print(f"  Content preview: {file.content[:50]}...")

        # Example 5: Query with size filter
        print("\n" + "=" * 60)
        print("Example 5: Query files larger than 30 bytes")
        print("=" * 60)

        large_files_view = View(
            agent=agent,
            query=ViewQuery(
                path_pattern="*",
                recursive=True,
                min_size=30
            )
        )
        large_files = await large_files_view.load()

        print(f"Found {len(large_files)} files larger than 30 bytes:")
        for file in large_files:
            if file.stats:
                print(f"  {file.path} ({file.stats.size} bytes)")

        # Example 6: Query with regex pattern
        print("\n" + "=" * 60)
        print("Example 6: Query files in /notes directory (regex)")
        print("=" * 60)

        notes_view = View(
            agent=agent,
            query=ViewQuery(
                path_pattern="*",
                recursive=True,
                regex_pattern=r"^/notes/"
            )
        )
        notes_files = await notes_view.load()

        print(f"Found {len(notes_files)} files in /notes:")
        for file in notes_files:
            print(f"  {file.path}")

        # Example 7: Count files without loading content
        print("\n" + "=" * 60)
        print("Example 7: Count files efficiently")
        print("=" * 60)

        count_view = View(agent=agent, query=ViewQuery(path_pattern="*", recursive=True))
        total_count = await count_view.count()
        print(f"Total files: {total_count}")

        # Example 8: Use fluent API to chain view modifications
        print("\n" + "=" * 60)
        print("Example 8: Fluent API - Query JSON files with content")
        print("=" * 60)

        json_files = await (
            View(agent=agent)
            .with_pattern("*.json")
            .with_content(True)
            .load()
        )

        print(f"Found {len(json_files)} JSON files:")
        for file in json_files:
            print(f"  {file.path}")
            if file.content:
                print(f"    Content: {file.content}")

        # Example 9: Custom filter with predicate
        print("\n" + "=" * 60)
        print("Example 9: Custom filter - Files modified today")
        print("=" * 60)

        today = datetime.now().date()
        recent_view = View(agent=agent, query=ViewQuery(path_pattern="*", recursive=True))
        recent_files = await recent_view.filter(
            lambda f: f.stats and f.stats.mtime.date() == today
        )

        print(f"Found {len(recent_files)} files modified today:")
        for file in recent_files:
            if file.stats:
                print(f"  {file.path} (modified: {file.stats.mtime})")

        print("\n" + "=" * 60)
        print("All examples completed successfully!")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
