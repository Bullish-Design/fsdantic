"""Key-Value Store example for AgentFS Python SDK"""

import asyncio
import json
import time

from agentfs_sdk import AgentFS, AgentFSOptions


async def main():
    # Initialize AgentFS with persistent storage
    agentfs = await AgentFS.open(AgentFSOptions(id="kvstore-demo"))

    print("=== KvStore Example ===\n")

    # Example 1: Store and retrieve simple values
    print("1. Storing simple values:")
    await agentfs.kv.set("username", "alice")
    await agentfs.kv.set("age", 30)
    await agentfs.kv.set("active", True)

    username = await agentfs.kv.get("username")
    age = await agentfs.kv.get("age")
    active = await agentfs.kv.get("active")

    print(f"  Username: {username}")
    print(f"  Age: {age}")
    print(f"  Active: {active}\n")

    # Example 2: Store and retrieve objects
    print("2. Storing complex objects:")
    user = {
        "id": 1,
        "name": "Alice Johnson",
        "email": "alice@example.com",
        "preferences": {"theme": "dark", "notifications": True},
    }

    await agentfs.kv.set("user:1", user)
    retrieved_user = await agentfs.kv.get("user:1")
    print(f"  Stored user: {json.dumps(retrieved_user, indent=2)}\n")

    # Example 3: Store and retrieve arrays
    print("3. Storing arrays:")
    tags = ["python", "database", "ai", "agent"]
    await agentfs.kv.set("tags", tags)
    retrieved_tags = await agentfs.kv.get("tags")
    assert isinstance(retrieved_tags, list)
    print(f"  Tags: {', '.join(retrieved_tags)}\n")

    # Example 4: Update existing values
    print("4. Updating existing values:")
    print(f"  Age before update: {await agentfs.kv.get('age')}")
    await agentfs.kv.set("age", 31)
    print(f"  Age after update: {await agentfs.kv.get('age')}\n")

    # Example 5: Delete values
    print("5. Deleting values:")
    print(f"  Username before delete: {await agentfs.kv.get('username')}")
    await agentfs.kv.delete("username")
    print(f"  Username after delete: {await agentfs.kv.get('username')}\n")

    # Example 6: Handle non-existent keys
    print("6. Retrieving non-existent keys:")
    non_existent = await agentfs.kv.get("does-not-exist")
    print(f"  Result: {non_existent}\n")

    # Example 7: Use cases for AI agents
    print("7. AI Agent use cases:")

    # Session state
    await agentfs.kv.set(
        "session:current",
        {"conversationId": "conv-123", "userId": "user-456", "startTime": int(time.time() * 1000)},
    )

    # Agent memory
    await agentfs.kv.set(
        "memory:user-preferences",
        {"language": "en", "responseStyle": "concise", "expertise": "intermediate"},
    )

    # Task queue
    await agentfs.kv.set(
        "tasks:pending",
        [
            {"id": 1, "task": "Process document", "priority": "high"},
            {"id": 2, "task": "Send notification", "priority": "low"},
        ],
    )

    print(f"  Session: {json.dumps(await agentfs.kv.get('session:current'), indent=2)}")
    print(f"  Memory: {json.dumps(await agentfs.kv.get('memory:user-preferences'), indent=2)}")
    print(f"  Tasks: {json.dumps(await agentfs.kv.get('tasks:pending'), indent=2)}")

    print("\n=== Example Complete ===")

    # Close the database
    await agentfs.close()


if __name__ == "__main__":
    asyncio.run(main())
