# LLM Council MCP Server

Multi-LLM deliberation with anonymized peer review -- as an MCP tool.

[![PyPI](https://img.shields.io/pypi/v/llm-deliberate-mcp)](https://pypi.org/project/llm-deliberate-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Quick Start

### 1. Install
```bash
uvx llm-deliberate-mcp
# or
pip install llm-deliberate-mcp
```

### 2. Get an API Key
Get an [OpenRouter API key](https://openrouter.ai/keys) (provides access to all major LLM providers).

### 3. Add to Your MCP Client
```json
{
  "mcpServers": {
    "llm-deliberate": {
      "command": "uvx",
      "args": ["llm-deliberate-mcp"],
      "env": {
        "OPENROUTER_API_KEY": "sk-or-v1-..."
      }
    }
  }
}
```

## How It Works

```text
User Question
    |
    v
Stage 1: 4 LLMs answer independently (parallel)
    |
    v
Stage 2: Models peer-review anonymized responses
    |       (Response A, B, C, D -- no model names)
    v
Stage 3: Chairman synthesizes the best final answer
    |
    v
Final Answer + Metrics + Rankings
```

## When Deliberation Helps (and When It Doesn't)

Works best for:
- Architecture decisions with genuine tradeoffs
- Complex problems requiring multiple perspectives
- Code review and design critique
- Strategic planning and risk assessment
- Security review of authentication flows

Works poorly for:
- Simple factual questions (use a single model)
- Calculations and data lookups (use a single model)
- Tasks under time pressure (30-90 sec overhead)
- Repetitive/routine tasks

Honesty note: Research (DeliberationBench, Jan 2026) shows that for simple QA tasks, a single strong model outperforms multi-model deliberation. LLM Council is designed for complex decisions where multiple perspectives add genuine value.

## Configuration

- `OPENROUTER_API_KEY` (required)
- `COUNCIL_MODELS` (optional, comma-separated)
- `CHAIRMAN_MODEL` (optional)
- `COUNCIL_TIMEOUT` (optional, seconds)
- `COUNCIL_MAX_TOKENS` (optional)

## Client Configuration Examples

### Claude Desktop
```json
{
  "mcpServers": {
    "llm-deliberate": {
      "command": "uvx",
      "args": ["llm-deliberate-mcp"],
      "env": {"OPENROUTER_API_KEY": "sk-or-v1-your-key-here"}
    }
  }
}
```

### Claude Code
```json
{
  "mcpServers": {
    "llm-deliberate": {
      "command": "uvx",
      "args": ["llm-deliberate-mcp"],
      "env": {"OPENROUTER_API_KEY": "sk-or-v1-your-key-here"}
    }
  }
}
```

### Cursor
```json
{
  "mcpServers": {
    "llm-deliberate": {
      "command": "uvx",
      "args": ["llm-deliberate-mcp"],
      "env": {"OPENROUTER_API_KEY": "sk-or-v1-your-key-here"}
    }
  }
}
```

### Generic MCP client (Python)
```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    server_params = StdioServerParameters(
        command="uvx",
        args=["llm-deliberate-mcp"],
        env={"OPENROUTER_API_KEY": "sk-or-v1-your-key-here"},
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "council_deliberate",
                arguments={"prompt": "Should we use microservices or a monolith?"},
            )
            print(result)

asyncio.run(main())
```

## API Reference

- `council_deliberate(prompt, models=None, chairman=None, depth="standard", contract_stack=None)`
- `council_status()`
- `council_configure(stage1_models=None, stage2_models=None, chairman_model=None, timeout_seconds=None)`

## Cost Transparency

Each deliberation uses roughly 10-15 API calls and typically costs about `$0.03-$0.15` depending on model mix and prompt length. Responses include `cost_estimate_usd`.

## Self-Learning

Deliberation metadata is stored locally in `~/.llm-council/deliberations.db` for pattern learning. No learning data is sent externally.
