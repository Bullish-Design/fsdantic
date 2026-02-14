"""Filesystem example for AgentFS Python SDK"""

import asyncio
from datetime import datetime

from agentfs_sdk import AgentFS, AgentFSOptions


async def main():
    # Initialize AgentFS with persistent storage
    agentfs = await AgentFS.open(AgentFSOptions(id="filesystem-demo"))

    # Write a file
    print("Writing file...")
    await agentfs.fs.write_file("/documents/readme.txt", "Hello, world!")

    # Read the file
    print("\nReading file...")
    content = await agentfs.fs.read_file("/documents/readme.txt")
    print(f"Content: {content}")

    # Get file stats
    print("\nFile stats:")
    stats = await agentfs.fs.stat("/documents/readme.txt")
    print(f"  Inode: {stats.ino}")
    print(f"  Size: {stats.size} bytes")
    print(f"  Mode: {oct(stats.mode)}")
    print(f"  Links: {stats.nlink}")
    print(f"  Is file: {stats.is_file()}")
    print(f"  Is directory: {stats.is_directory()}")
    print(f"  Created: {datetime.fromtimestamp(stats.ctime).isoformat()}")
    print(f"  Modified: {datetime.fromtimestamp(stats.mtime).isoformat()}")

    # List directory
    print("\nListing /documents:")
    files = await agentfs.fs.readdir("/documents")
    print(f"  Files: {files}")

    # Write more files
    await agentfs.fs.write_file("/documents/notes.txt", "Some notes")
    await agentfs.fs.write_file("/images/photo.jpg", b"binary data here")

    # List root
    print("\nListing /:")
    root_files = await agentfs.fs.readdir("/")
    print(f"  Directories: {root_files}")

    # Check directory stats
    print("\nDirectory stats for /documents:")
    dir_stats = await agentfs.fs.stat("/documents")
    print(f"  Is directory: {dir_stats.is_directory()}")
    print(f"  Mode: {oct(dir_stats.mode)}")

    # Close the database
    await agentfs.close()


if __name__ == "__main__":
    asyncio.run(main())
