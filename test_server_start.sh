#!/bin/bash
# Test that MCP server can start without errors

cd /Users/haza/Projects/llm-council

echo "Testing MCP server startup..."
echo ""

# Start server in background and capture output
uv run python mcp_server/mcp_server.py &
SERVER_PID=$!

# Wait 2 seconds for startup
sleep 2

# Check if process is still running
if kill -0 $SERVER_PID 2>/dev/null; then
    echo "✓ Server started successfully (PID: $SERVER_PID)"

    # Kill the server
    kill $SERVER_PID 2>/dev/null
    wait $SERVER_PID 2>/dev/null

    echo "✓ Server stopped cleanly"
    echo ""
    echo "=========================================="
    echo "✅ SERVER STARTUP TEST PASSED"
    echo "=========================================="
    exit 0
else
    echo "❌ Server failed to start or crashed"
    exit 1
fi
