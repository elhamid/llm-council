#!/usr/bin/env python3
"""
LLM Council MCP Server

An MCP server that exposes the LLM Council deliberation system as tools
for Claude and other MCP-compatible clients.

Usage:
    python mcp_server.py

Add to Claude settings (~/.claude.json or claude_desktop_config.json):
    {
      "mcpServers": {
        "llm-council": {
          "command": "python",
          "args": ["/path/to/llm-council/mcp_server/mcp_server.py"],
          "env": {
            "OPENROUTER_API_KEY": "your-key-here"
          }
        }
      }
    }
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

# Load environment from .env if present
from dotenv import load_dotenv

# Try loading from multiple locations
for env_path in [
    os.path.join(os.path.dirname(__file__), ".env"),
    os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
]:
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

# Import MCP SDK
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError as e:
    print(f"Error: MCP SDK not installed. Run: pip install mcp", file=sys.stderr)
    print(f"Details: {e}", file=sys.stderr)
    sys.exit(1)

# Import our tool implementations
from tools import deliberate, configure, get_status

# Configure logging to stderr (stdout is used for MCP protocol)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("llm-council-mcp")

# Create MCP server instance
server = Server("llm-council")


@server.list_tools()
async def list_tools() -> List[Tool]:
    """List available LLM Council tools."""
    return [
        Tool(
            name="llm_council_deliberate",
            description=(
                "⚠️ HIGH COST - Calls multiple LLMs (10+ API calls per deliberation). Use ONLY for strategic decisions.\n\n"
                "COMPLEXITY THRESHOLD: Only use when complexity ≥70% OR for:\n"
                "- Architecture decisions affecting multiple systems\n"
                "- Security review of authentication flows\n"
                "- Major technology stack changes\n"
                "- Strategic direction questions\n\n"
                "❌ NOT FOR:\n"
                "- Bug fixes\n"
                "- Routine code reviews\n"
                "- Quick lookups\n"
                "- Frequent/repeated queries\n\n"
                "PROCESS:\n"
                "Stage 1: Multiple AI models generate independent responses.\n"
                "Stage 2: Models peer-review and rank anonymized responses.\n"
                "Stage 3: A chairman model synthesizes the final answer."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The question or request to deliberate on",
                    },
                    "complexity_score": {
                        "type": "integer",
                        "description": "Complexity score (0-100). Must be ≥70 to proceed with full deliberation. Use 70-79 for complex technical decisions, 80-89 for strategic architecture, 90-100 for critical security/business decisions.",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "strategic_justification": {
                        "type": "string",
                        "description": "Required: Explain WHY this needs full council deliberation (e.g., 'Multi-system architecture decision with security implications')",
                    },
                    "contract_stack": {
                        "type": "string",
                        "description": "Optional: Contract stack to apply (e.g., 'factory_truth_v1')",
                    },
                },
                "required": ["prompt", "strategic_justification"],
            },
        ),
        Tool(
            name="llm_council_configure",
            description=(
                "Configure the LLM Council models and settings. "
                "Set which models participate in Stage 1 (generation), "
                "Stage 2 (evaluation), and which model serves as chairman."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "stage1_models": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of OpenRouter model IDs for Stage 1 response generation",
                    },
                    "stage2_models": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of OpenRouter model IDs for Stage 2 peer evaluation",
                    },
                    "chairman_model": {
                        "type": "string",
                        "description": "OpenRouter model ID for Stage 3 synthesis (chairman)",
                    },
                    "contract_stack": {
                        "type": "string",
                        "description": "Contract stack to apply (comma-separated)",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Timeout for deliberation in seconds (default: 300)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="llm_council_status",
            description=(
                "Get the current LLM Council configuration and status. "
                "Shows configured models, contract stack, and any recent errors."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    """Execute an LLM Council tool."""
    logger.info(f"Tool call: {name} with args: {json.dumps(arguments)[:200]}...")

    try:
        if name == "llm_council_deliberate":
            result = await deliberate(
                prompt=arguments.get("prompt", ""),
                complexity_score=arguments.get("complexity_score"),
                strategic_justification=arguments.get("strategic_justification", ""),
                contract_stack=arguments.get("contract_stack"),
            )
        elif name == "llm_council_configure":
            result = configure(
                stage1_models=arguments.get("stage1_models"),
                stage2_models=arguments.get("stage2_models"),
                chairman_model=arguments.get("chairman_model"),
                contract_stack=arguments.get("contract_stack"),
                timeout_seconds=arguments.get("timeout_seconds"),
            )
        elif name == "llm_council_status":
            result = get_status()
        else:
            result = {"error": f"Unknown tool: {name}"}

        # Format result as JSON text
        return [
            TextContent(
                type="text",
                text=json.dumps(result, indent=2, ensure_ascii=False),
            )
        ]

    except Exception as e:
        logger.exception(f"Error executing tool {name}")
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                    indent=2,
                ),
            )
        ]


async def main():
    """Run the MCP server with stdio transport."""
    logger.info("Starting LLM Council MCP Server...")

    # Check for API key
    if not (os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")):
        logger.warning(
            "No API key found. Set OPENROUTER_API_KEY or OPENAI_API_KEY environment variable."
        )

    # Run server with stdio transport
    async with stdio_server() as (read_stream, write_stream):
        logger.info("MCP Server ready, waiting for connections...")
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
