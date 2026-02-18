# AGENTS.md -- LLM Council

## Project Overview

LLM Council is a 3-stage deliberation system where multiple LLMs collaboratively answer user questions. Stage 1 queries models in parallel, Stage 2 anonymizes responses and has models peer-review each other, Stage 3 has a chairman model synthesize the best final answer. The key innovation is anonymized peer review preventing models from playing favorites.

## Current Task

Your task is specified in `CODEX_TASK.md`. Read it completely before starting.

Build the standalone `llm-council-mcp` package: a PyPI-publishable MCP server that extracts and decouples the core deliberation logic from the existing `backend/` into an independent package installable via `uvx llm-council-mcp`.

## Codebase Structure

```
backend/
  council.py      # Core 3-stage deliberation (1,453 lines) -- extract from here
  contracts.py    # Contract compliance system (309 lines) -- simplify and extract
  roles.py        # Role specialization (115 lines) -- extract verbatim
  config.py       # FastAPI app config (NOT needed for MCP package)
  main.py         # FastAPI web app (NOT needed for MCP package)
  storage.py      # JSON conversation storage (NOT needed for MCP package)
mcp_server/       # Existing MCP skeleton (reference only, do NOT modify)
  mcp_server.py   # Uses low-level mcp.server.Server API
  tools.py        # Has CEO constraints to strip out
frontend/         # React frontend (irrelevant)
pyproject.toml    # Web app config (NOT the new package config)
```

## Conventions

- **Imports:** Use relative imports within packages (e.g., `from .config import ...`). No `sys.path` manipulation.
- **Async:** All API calls are async using `asyncio.gather()` for parallelism. Use `asyncio.wait_for()` with timeouts.
- **Error handling:** Functions return structured dicts on failure, never raise to the MCP server. Every tool wraps its body in try/except returning `{"error": ..., "error_type": ...}`.
- **Type hints:** Use `typing` module. Target Python 3.10+ (`list[str]` syntax is fine in annotations with `from __future__ import annotations`).
- **OpenAI client:** Uses `openai.AsyncOpenAI` with OpenRouter `base_url`. Falls back `max_tokens` -> `max_output_tokens` on TypeError.
- **Logging:** Always to stderr (stdout is MCP protocol). Use `logging.getLogger("llm-council-mcp")`.

## Critical Rules

- All new code goes in `llm-council-mcp/` at the repo root. Do NOT modify `backend/` or existing `mcp_server/`.
- Extract and adapt code from `backend/council.py` -- do not rewrite from scratch.
- The ranking parser (`_parse_ranking_from_text`, `_extract_final_ranking_line`, `_extract_fuzzy_ranking_chain`, `_parse_ranking_order`) is battle-tested across many edge cases. Copy it verbatim. Do not simplify.
- The `_coerce_stage2_5line` function handles format repair. Copy it verbatim.
- The `_content_to_text` and `_deep_extract_text` functions handle diverse API response shapes. Copy them verbatim.
- All errors must return structured JSON -- never crash the MCP server.
- Tests must mock API calls -- never make real API calls in tests.
- Remove all CEO constraints: no `complexity_score`, no `strategic_justification`, no JSONL logging to `~/.claude/enclaude/`.
- Replace "YC-level product team" with "product team" in all prompts.
- The `contracts.py` `parse_contract_ids()` must NOT auto-inject `factory_truth_v1`. Contracts are fully opt-in.

## Testing

- Framework: pytest with pytest-asyncio
- Async mode: `asyncio_mode = "auto"` in pyproject.toml
- Mock pattern: `unittest.mock.AsyncMock` for async OpenAI client calls
- Coverage target: >80% line coverage
- Run: `pytest --cov=llm_council_mcp --cov-report=term-missing`
- Key test areas: ranking parser edge cases, metrics calculation, learning store SQLite ops, role assignment, prompt template generation, MCP tool parameter validation
- Use `tmp_path` fixture for temporary SQLite databases in learning tests

## Environment

- Python 3.10+
- Required: `OPENROUTER_API_KEY` environment variable
- Build system: hatchling with `src/` layout
- Package manager: `uv` preferred, `pip` supported
- MCP SDK: `mcp>=1.2.0` (provides `mcp.server.fastmcp.FastMCP`)
- Default models: `openai/gpt-5.2`, `anthropic/claude-sonnet-4.5`, `google/gemini-3-pro-preview`, `x-ai/grok-4.1-fast`
- Default chairman: `anthropic/claude-opus-4.5`
