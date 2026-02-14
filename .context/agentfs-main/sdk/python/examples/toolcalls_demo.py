"""Tool Calls tracking example for AgentFS Python SDK"""

import asyncio
import json
import time

from agentfs_sdk import AgentFS, AgentFSOptions


async def main():
    # Create an agent with persistent storage
    agentfs = await AgentFS.open(AgentFSOptions(id="toolcalls-demo"))

    print("=== Tool Call Tracking Example ===\n")

    # Example 1: Successful tool call
    print("1. Tracking a successful web search:")
    start_time1 = int(time.time())

    # Simulate some work
    await asyncio.sleep(0.1)

    end_time1 = int(time.time())
    search_id = await agentfs.tools.record(
        "web_search",
        start_time1,
        end_time1,
        parameters={"query": "AI agents and LLMs", "maxResults": 10},
        result={
            "results": [
                {"title": "Understanding AI Agents", "url": "https://example.com/1"},
                {"title": "LLM Best Practices", "url": "https://example.com/2"},
            ],
            "count": 2,
        },
    )
    print(f"   Recorded tool call with ID: {search_id}\n")

    # Example 2: Failed tool call
    print("2. Tracking a failed API call:")
    start_time2 = int(time.time())

    await asyncio.sleep(0.05)

    end_time2 = int(time.time())
    api_id = await agentfs.tools.record(
        "api_call",
        start_time2,
        end_time2,
        parameters={"endpoint": "/users", "method": "GET"},
        error="Connection timeout after 30s",
    )
    print(f"   Recorded failed call with ID: {api_id}\n")

    # Example 3: Multiple tool calls
    print("3. Tracking multiple database queries:")
    for i in range(3):
        start = int(time.time())
        await asyncio.sleep(0.02)
        end = int(time.time())

        await agentfs.tools.record(
            "database_query",
            start,
            end,
            parameters={"sql": f"SELECT * FROM users WHERE id = {i + 1}"},
            result={"rows": 1},
        )
    print("   Created 3 database query records\n")

    # Example 4: Using start/success/error pattern
    print("4. Using start/success pattern:")
    call_id = await agentfs.tools.start("data_processing", {"file": "data.csv"})
    await asyncio.sleep(0.05)
    await agentfs.tools.success(call_id, {"rows_processed": 1000})
    print(f"   Completed tool call {call_id}\n")

    # Query tool calls by name
    print("5. Querying tool calls by name:")
    searches = await agentfs.tools.get_by_name("web_search")
    print(f"   Found {len(searches)} web search calls")
    if searches:
        search = searches[0]
        print(f"   - Duration: {search.duration_ms}ms")
        print(f"   - Parameters: {json.dumps(search.parameters)}")
        print(f"   - Result: {json.dumps(search.result)}")
    print()

    # Get recent tool calls
    print("6. Getting recent tool calls:")
    one_minute_ago = int(time.time()) - 60
    recent = await agentfs.tools.get_recent(one_minute_ago)
    print(f"   Found {len(recent)} calls in the last minute:")
    for tc in recent:
        status = "failed" if tc.error else "success"
        print(f"   - {tc.name} ({status})")
    print()

    # Get performance statistics
    print("7. Performance statistics:")
    stats = await agentfs.tools.get_stats()
    print("   Tool Performance:")
    for stat in stats:
        print(f"   - {stat.name}:")
        print(f"     Total: {stat.total_calls}, Success: {stat.successful}, Failed: {stat.failed}")
        print(f"     Avg Duration: {stat.avg_duration_ms:.2f}ms")

    # Clean up
    await agentfs.close()
    print("\n✓ Example completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
