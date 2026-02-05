#!/usr/bin/env python3
"""
Verify the tool schema includes all CEO constraint requirements.
"""

import sys
import os

# Add mcp_server to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "mcp_server"))

# Mock MCP types to allow import
class Tool:
    def __init__(self, name, description, inputSchema):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema

class TextContent:
    pass

class Server:
    def __init__(self, name):
        self.name = name
    def list_tools(self):
        def decorator(func):
            return func
        return decorator
    def call_tool(self):
        def decorator(func):
            return func
        return decorator
    def create_initialization_options(self):
        return {}

class StdioServer:
    pass

# Mock the mcp module
sys.modules['mcp'] = type(sys)('mcp')
sys.modules['mcp.server'] = type(sys)('mcp.server')
sys.modules['mcp.server.stdio'] = type(sys)('mcp.server.stdio')
sys.modules['mcp.types'] = type(sys)('mcp.types')
sys.modules['mcp'].server = sys.modules['mcp.server']
sys.modules['mcp.server'].Server = Server
sys.modules['mcp.server'].stdio = sys.modules['mcp.server.stdio']
sys.modules['mcp.server.stdio'].stdio_server = StdioServer
sys.modules['mcp'].types = sys.modules['mcp.types']
sys.modules['mcp.types'].Tool = Tool
sys.modules['mcp.types'].TextContent = TextContent

# Now import the actual server module
import mcp_server.mcp_server as server_module

# Get the list_tools function
import asyncio

async def verify_schema():
    """Verify the tool schema meets all requirements."""
    print("Verifying LLM Council Tool Schema...\n")

    tools = await server_module.list_tools()

    # Find the deliberate tool
    deliberate_tool = None
    for tool in tools:
        if tool.name == "llm_council_deliberate":
            deliberate_tool = tool
            break

    if not deliberate_tool:
        print("❌ ERROR: llm_council_deliberate tool not found")
        return False

    print("Tool Name:", deliberate_tool.name)
    print("\n" + "=" * 70)
    print("DESCRIPTION:")
    print("=" * 70)
    print(deliberate_tool.description)
    print()

    # Check requirements
    requirements = {
        "HIGH COST warning": "⚠️ HIGH COST" in deliberate_tool.description or "HIGH COST" in deliberate_tool.description,
        "Complexity threshold": "complexity ≥70" in deliberate_tool.description or "≥70" in deliberate_tool.description,
        "Good use cases": "Architecture decisions" in deliberate_tool.description,
        "Bad use cases": "Bug fixes" in deliberate_tool.description or "NOT FOR" in deliberate_tool.description,
        "Anti-pattern warning": "frequent" in deliberate_tool.description.lower() or "routine" in deliberate_tool.description.lower(),
    }

    print("=" * 70)
    print("DESCRIPTION REQUIREMENTS:")
    print("=" * 70)
    for req, met in requirements.items():
        status = "✓" if met else "❌"
        print(f"{status} {req}")
    print()

    # Check schema fields
    schema = deliberate_tool.inputSchema
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    print("=" * 70)
    print("INPUT SCHEMA:")
    print("=" * 70)

    schema_requirements = {
        "prompt field exists": "prompt" in properties,
        "complexity_score field exists": "complexity_score" in properties,
        "strategic_justification field exists": "strategic_justification" in properties,
        "strategic_justification is required": "strategic_justification" in required,
    }

    for req, met in schema_requirements.items():
        status = "✓" if met else "❌"
        print(f"{status} {req}")

    if "complexity_score" in properties:
        cs = properties["complexity_score"]
        print(f"\n  complexity_score details:")
        print(f"    Type: {cs.get('type')}")
        print(f"    Min: {cs.get('minimum')}")
        print(f"    Max: {cs.get('maximum')}")
        print(f"    Description: {cs.get('description', '')[:80]}...")

    if "strategic_justification" in properties:
        sj = properties["strategic_justification"]
        print(f"\n  strategic_justification details:")
        print(f"    Type: {sj.get('type')}")
        print(f"    Description: {sj.get('description', '')[:80]}...")

    print()

    # Overall verification
    all_passed = all(requirements.values()) and all(schema_requirements.values())

    if all_passed:
        print("=" * 70)
        print("✅ ALL REQUIREMENTS MET")
        print("=" * 70)
        print("\nThe LLM Council MCP tool is now constrained for:")
        print("  • Strategic decisions only (complexity ≥70)")
        print("  • Requires justification")
        print("  • Prominent cost warning")
        print("  • Clear good/bad use case examples")
        return True
    else:
        print("=" * 70)
        print("❌ SOME REQUIREMENTS NOT MET")
        print("=" * 70)
        return False

if __name__ == "__main__":
    success = asyncio.run(verify_schema())
    sys.exit(0 if success else 1)
