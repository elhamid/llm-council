# CODEX_TASK.md -- Build the LLM Council MCP Server Package

> **Self-contained specification for OpenAI Codex.** Everything needed to implement
> the `llm-council-mcp` package is in this document. You should NOT need to read
> any other files. All critical code from the existing codebase is included inline.

---

## Table of Contents

1. [Project Context](#1-project-context)
2. [Goal](#2-goal)
3. [File Structure to Create](#3-file-structure-to-create)
4. [Detailed Implementation Spec](#4-detailed-implementation-spec)
5. [What to Strip Out](#5-what-to-strip-out)
6. [pyproject.toml Exact Content](#6-pyprojecttoml-exact-content)
7. [README.md Content Guide](#7-readmemd-content-guide)
8. [Testing Requirements](#8-testing-requirements)
9. [CI/CD](#9-cicd)
10. [Acceptance Criteria](#10-acceptance-criteria)
11. [Key Code Snippets from Existing Codebase](#11-key-code-snippets-from-existing-codebase)
12. [Environment Setup](#12-environment-setup)
13. [MCP Client Configuration Examples](#13-mcp-client-configuration-examples)

---

## 1. Project Context

### What LLM Council Is

LLM Council is a 3-stage deliberation system where multiple LLMs collaboratively answer user questions:

1. **Stage 1 -- Parallel Response Generation**: A configurable set of frontier LLMs (default: 4 models from different providers) each independently answer the user's prompt. Models are assigned specialization roles (Builder, Reviewer, Integrator, Contrarian) based on their provider prefix.

2. **Stage 2 -- Anonymized Peer Review**: The Stage 1 responses are anonymized as "Response A", "Response B", etc. A panel of judge models evaluates and ranks all responses using a strict 5-line format (4 critique lines + 1 ranking line). The anonymization prevents models from playing favorites. Multiple repair passes handle format-noncompliant outputs. An optional adjudicator breaks ties when judges disagree.

3. **Stage 3 -- Chairman Synthesis**: A chairman model receives all Stage 1 responses, Stage 2 evaluations, and aggregate rankings, then synthesizes the best final answer. An optional long-context helper can pre-digest large payloads.

The key innovation is the anonymized peer review in Stage 2, which prevents bias and produces genuine quality rankings.

### Existing Codebase Structure

```
llm-council/
  backend/
    __init__.py          # Re-exports config vars (OPENROUTER_API_KEY, COUNCIL_MODELS, etc.)
    config.py            # AppConfig dataclass, env var parsing, server config
    council.py           # Core 3-stage deliberation logic (1,453 lines)
    contracts.py         # Contract compliance system (309 lines)
    roles.py             # Role specialization (115 lines)
    main.py              # FastAPI web app (not needed for MCP)
    storage.py           # JSON conversation storage (not needed for MCP)
  mcp_server/
    __init__.py          # Re-exports tools
    mcp_server.py        # Existing MCP server skeleton (239 lines)
    tools.py             # Existing MCP tool implementations (323 lines)
  frontend/             # React frontend (not relevant)
  pyproject.toml        # Current project config (for the web app)
  CLAUDE.md             # Architecture notes
  MCP_RELEASE_PLAN.md   # Release roadmap
```

### What Already Exists in `mcp_server/` and What Needs to Change

The existing `mcp_server/` has a working skeleton but is NOT production-ready:

**Problems to fix:**
- Uses `sys.path.insert(0, ...)` to import from `backend/` -- fragile
- Imports directly from `backend.council` -- creates dependency on the web app package
- Contains internal CEO constraints (`complexity_score`, `strategic_justification`, JSONL logging to `~/.claude/enclaude/`)
- Model config uses individual env vars (`STAGE1_MODEL_A`, `STAGE1_MODEL_B`, etc.)
- No observability metrics (duration, agreement, cost)
- No self-learning feedback loop
- Not packaged for PyPI distribution
- Tool descriptions are prescriptive ("Do NOT use for bug fixes") rather than descriptive

**What to keep (conceptually):**
- The 3-tool structure: deliberate, configure, status
- The stdio transport pattern
- The core deliberation flow calling stage1 -> stage2 -> stage3

**What to build:**
- A completely standalone package (`llm-council-mcp`) that extracts and decouples the core logic
- No dependency on the `backend/` package
- Proper PyPI packaging with `uvx` support
- Metrics, learning, and proper error handling

---

## 2. Goal

Build a **standalone, PyPI-publishable MCP server package** called `llm-council-mcp` that:

- Is installable via `uvx llm-council-mcp` (or `pip install llm-council-mcp`)
- Works with Claude Desktop, Claude Code, Codex, Cursor, and any MCP client
- Contains ALL deliberation logic self-contained (no dependency on `backend/`)
- Is production-grade: structured error handling, observability metrics, self-learning feedback loop
- Has comprehensive tests with >80% coverage
- Has CI/CD via GitHub Actions
- Has a complete README with honest positioning

---

## 3. File Structure to Create

Create this as a NEW directory at the repository root: `llm-council-mcp/`

```
llm-council-mcp/
  pyproject.toml
  README.md
  LICENSE
  .env.example
  src/
    llm_council_mcp/
      __init__.py           # Package entry, exports main()
      __main__.py           # python -m llm_council_mcp
      server.py             # FastMCP server, stdio transport
      council.py            # Extracted core deliberation logic
      contracts.py          # Simplified contract compliance
      roles.py              # Role specialization
      models.py             # Model configuration and defaults
      metrics.py            # Observability (duration, agreement, dissent, cost)
      learning.py           # Self-learning feedback loop (SQLite)
      prompts.py            # Stage 1/2/3 prompt templates
  tests/
    __init__.py
    conftest.py
    test_council.py
    test_server.py
    test_metrics.py
    test_learning.py
    test_prompts.py
    test_roles.py
  .github/
    workflows/
      ci.yml
      publish.yml
```

---

## 4. Detailed Implementation Spec

### 4.1 `src/llm_council_mcp/__init__.py`

**Purpose:** Package entry point. Exports `main()` for the console script.

```python
"""LLM Council MCP Server -- Multi-LLM deliberation with anonymized peer review."""

__version__ = "0.1.0"

from .server import main

__all__ = ["main"]
```

### 4.2 `src/llm_council_mcp/__main__.py`

**Purpose:** Allows `python -m llm_council_mcp`.

```python
"""Allow running as: python -m llm_council_mcp"""
from llm_council_mcp import main

main()
```

### 4.3 `src/llm_council_mcp/server.py` -- The MCP Server

**Purpose:** FastMCP server with stdio transport. Defines all MCP tools.

**Key details:**
- Use `FastMCP` from the `mcp` package (it was merged into the official SDK as `mcp.server.fastmcp.FastMCP`)
- Single primary tool: `council_deliberate`
- Two utility tools: `council_status`, `council_configure`
- Progress reporting via MCP context for each stage
- All errors return structured JSON, never crash the server

**Imports and initialization:**
```python
import asyncio
import json
import logging
import os
import sys
import time
from typing import Any, Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP, Context

from .council import run_deliberation
from .models import get_config, update_config, CouncilConfig
from .metrics import calculate_metrics
from .learning import LearningStore

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", stream=sys.stderr)
logger = logging.getLogger("llm-council-mcp")

mcp = FastMCP(
    "llm-council",
    description="Multi-LLM deliberation with anonymized peer review",
)
```

**Tool: `council_deliberate`**

Parameters:
- `prompt` (str, required): The question or problem to deliberate on
- `models` (list[str] | None, optional): OpenRouter model IDs for the council. If omitted, uses configured defaults.
- `chairman` (str | None, optional): OpenRouter model ID for the chairman synthesizer.
- `depth` (str | None, optional): One of `"quick"`, `"standard"`, `"thorough"`. `quick`=3 models, `standard`=4 (default), `thorough`=5+ with adjudication.
- `contract_stack` (str | None, optional): Comma-separated contract IDs (e.g., `"factory_truth_v1"`). If omitted, no contracts applied.

Returns JSON with:
```json
{
  "answer": "synthesized final answer from chairman",
  "stage1_responses": [
    {"model": "openai/gpt-5.2", "content": "...", "role": "Builder"}
  ],
  "stage2_rankings": [
    {"judge": "anthropic/claude-sonnet-4.5", "rankings": ["Response A", "Response B", ...], "raw_evaluation": "..."}
  ],
  "aggregate_rankings": [
    {"model": "openai/gpt-5.2", "average_rank": 1.5, "vote_count": 3}
  ],
  "metrics": {
    "duration_seconds": 47.3,
    "models_used": ["openai/gpt-5.2", "anthropic/claude-sonnet-4.5", ...],
    "agreement_score": 0.82,
    "dissent_ratio": 0.18,
    "cost_estimate_usd": 0.045
  },
  "metadata": {
    "task_type": "architectural_decision",
    "depth": "standard",
    "chairman_model": "anthropic/claude-opus-4.5",
    "label_to_model": {"Response A": "openai/gpt-5.2", ...},
    "learning_stored": true
  }
}
```

Use `ctx.report_progress(current, total)` for progress:
- Stage 1 start: `report_progress(0, 3)`
- Stage 1 complete: `report_progress(1, 3)`
- Stage 2 complete: `report_progress(2, 3)`
- Stage 3 complete: `report_progress(3, 3)`

Also use `ctx.info()` / `ctx.warning()` for log messages:
- `ctx.info("Stage 1: Querying 4 models in parallel...")`
- `ctx.info("Stage 2: Anonymized peer review in progress...")`
- `ctx.info("Stage 3: Chairman synthesizing final answer...")`

**Tool: `council_status`**

No parameters. Returns:
```json
{
  "status": "ready",
  "api_configured": true,
  "config": {
    "stage1_models": [...],
    "stage2_models": [...],
    "chairman_model": "...",
    "timeout_seconds": 300
  },
  "learning": {
    "total_deliberations": 42,
    "task_types_seen": ["architectural_decision", "code_review"]
  }
}
```

**Tool: `council_configure`**

Parameters:
- `stage1_models` (list[str] | None, optional)
- `stage2_models` (list[str] | None, optional)
- `chairman_model` (str | None, optional)
- `timeout_seconds` (int | None, optional)

Returns the updated config (same shape as `council_status`).

**`main()` function:**
```python
def main():
    """Entry point for the console script."""
    mcp.run(transport="stdio")
```

### 4.4 `src/llm_council_mcp/council.py` -- Core Deliberation Logic

**Purpose:** Extract and simplify the core 3-stage deliberation from `backend/council.py`. This is the heart of the package.

**What to extract from existing code:**
- `_get_openai_client()` / `_client()` -- OpenAI client singleton with OpenRouter support
- `_content_to_text()` -- Convert various response shapes to plain text
- `_deep_extract_text()` -- Fallback text extraction
- `_looks_like_provider_id()` -- Filter out provider IDs returned as content
- `_chat()` -- Core async chat completion call
- `_member_messages()` -- Build message list for Stage 1/2 models (handles contract injection and role assignment per stage)
- `_chairman_messages()` -- Build message list for Stage 3 chairman
- `_label_responses()` -- Anonymize Stage 1 responses
- `_parse_ranking_from_text()` -- Extract rankings from judge output
- `_parse_ranking_order()` -- Parse FINAL_RANKING line
- `_extract_final_ranking_line()` -- Find the ranking line in output
- `_extract_fuzzy_ranking_chain()` -- Fuzzy ranking extraction
- `_coerce_stage2_5line()` -- Coerce judge output to strict 5-line format
- `_contains_process_narration()` -- Detect and filter self-narration in model output
- `_normalize_ws()` -- Whitespace normalization
- `_strip_wrappers()` -- Remove code fences and quotes
- `stage1_collect_responses()` -- Stage 1 parallel queries
- `stage2_collect_rankings()` -- Stage 2 anonymized evaluation
- `stage3_synthesize_final()` -- Stage 3 chairman synthesis
- `calculate_aggregate_rankings()` -- Aggregate rank calculation

**What to write fresh:**
- `run_deliberation()` -- Top-level orchestrator that calls stage1 -> stage2 -> stage3 and returns the unified result dict
- Depth handling: `quick` (3 models), `standard` (4 models), `thorough` (5+ models with adjudication)
- Model list override support (from tool parameters)

**Key differences from existing code:**
- Models come from `models.py` config (not individual env vars)
- No `contract_stack` forced -- contracts are optional
- `stage3_select_winner` aliases removed -- just use `stage3_synthesize_final`
- Error dicts in `STAGE1_LAST_ERRORS` / `STAGE2_LAST_ERRORS` become return values, not module globals
- OpenAI client uses configurable `base_url` (defaults to OpenRouter)

**Top-level function signature:**
```python
async def run_deliberation(
    prompt: str,
    stage1_models: list[str] | None = None,
    stage2_models: list[str] | None = None,
    chairman_model: str | None = None,
    contract_stack: str | None = None,
    depth: str = "standard",
    timeout: int = 300,
) -> dict[str, Any]:
    """
    Run a full 3-stage LLM Council deliberation.

    Returns a dict with keys: answer, stage1_responses, stage2_rankings,
    aggregate_rankings, label_to_model, errors, timing.
    """
```

**Implementation notes:**
- Use `asyncio.wait_for()` with `timeout / 3` per stage
- The `depth` parameter controls how many models to use:
  - `"quick"`: Use first 3 of stage1_models, first 3 of stage2_models
  - `"standard"`: Use all configured models (default 4 each)
  - `"thorough"`: Use all configured models + enable adjudication
- If `stage1_models` or `stage2_models` are passed as tool parameters, use those instead of config defaults
- Always return structured dict, never raise exceptions to the caller

### 4.5 `src/llm_council_mcp/contracts.py` -- Simplified Contract System

**Purpose:** Optional contract compliance checking. Simplified from the existing `backend/contracts.py`.

**What to extract:**
- `ContractSpec` dataclass (fields: `contract_id: str`, `name: str`, `system_prompt: str`, `chairman_addendum: str = ""`)
- `FACTORY_TRUTH_V1` contract
- `parse_contract_ids()` -- but make it NOT auto-inject factory contract (external users decide)
- `build_contract_system_messages()` -- for Stage 1 member prompts
- `build_chairman_contract_system_messages()` -- for Stage 3 chairman prompts
- `evaluate_contract_compliance()` -- lightweight post-check

**What to change:**
- When `contract_stack` is `None` or empty, return empty lists (no contracts applied). Do NOT auto-inject `factory_truth_v1`.
- Remove `ELDERCARE_SAFETY_V1` -- this is domain-specific. Keep only the factory base contract as an available option.
- Keep `evaluate_contract_compliance()` but make it a no-op when no contracts are active.

### 4.6 `src/llm_council_mcp/roles.py` -- Role Specialization

**Purpose:** Assign specialization roles to models based on provider prefix.

**Extract verbatim from `backend/roles.py`:**

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional

@dataclass(frozen=True)
class RoleSpec:
    name: str
    system: str

    @property
    def system_prompt(self) -> str:
        return self.system

DEFAULT_ROLE = RoleSpec(
    name="Generalist",
    system=(
        "You are a strong, truth-first assistant.\n"
        "Be concise, precise, and practical.\n"
        "If information is missing, say what is missing and ask for it.\n"
        "Do not invent facts.\n"
    ),
)

ROLE_SPECS: Dict[str, RoleSpec] = {
    "builder": RoleSpec(
        name="Builder",
        system=(
            "You are a pragmatic senior engineer.\n"
            "Prefer minimal, runnable fixes.\n"
            "When uncertain, state assumptions explicitly.\n"
            "Do not invent facts.\n"
        ),
    ),
    "reviewer": RoleSpec(
        name="Reviewer",
        system=(
            "You are a careful reviewer.\n"
            "Look for edge cases, missing steps, and correctness issues.\n"
            "Do not invent facts.\n"
        ),
    ),
    "synthesizer": RoleSpec(
        name="Synthesizer",
        system=(
            "You are an analytical synthesizer.\n"
            "Combine the best parts of different answers into one.\n"
            "Do not invent facts.\n"
        ),
    ),
    "integrator": RoleSpec(
        name="Integrator",
        system=(
            "You are an integration-focused, adoption-minded advisor.\n"
            "Optimize for real-world constraints (existing systems, stakeholders, budgets, compliance, timelines).\n"
            "Call out integration risks, dependencies, and rollout steps.\n"
            "Prefer pragmatic migration paths and backwards compatibility.\n"
            "Do not invent facts.\n"
        ),
    ),
    "contrarian": RoleSpec(
        name="Contrarian",
        system=(
            "You are a sharp contrarian reviewer.\n"
            "Stress-test assumptions and look for hidden failure modes.\n"
            "Do not invent facts.\n"
        ),
    ),
}

PROVIDER_DEFAULT_ROLE: Dict[str, str] = {
    "openai/": "builder",
    "anthropic/": "reviewer",
    "google/": "integrator",
    "x-ai/": "contrarian",
}

def get_role_spec(model: str) -> RoleSpec:
    m = (model or "").strip()
    for prefix, role_key in PROVIDER_DEFAULT_ROLE.items():
        if m.startswith(prefix):
            return ROLE_SPECS.get(role_key, DEFAULT_ROLE)
    return DEFAULT_ROLE


def build_messages_for_model(
    model: str,
    user_prompt: str,
    contract_system_messages: Optional[List[dict]] = None,
    extra_system: Optional[str] = None,
) -> List[dict]:
    """Build message list for a model with role system prompt and optional contract messages."""
    msgs: List[dict] = []
    if contract_system_messages:
        msgs.extend(contract_system_messages)
    role = get_role_spec(model)
    sys = role.system
    if extra_system:
        sys = sys.rstrip() + "\n\n" + extra_system.strip() + "\n"
    msgs.append({"role": "system", "content": sys})
    msgs.append({"role": "user", "content": user_prompt})
    return msgs


def chairman_system_prompt() -> str:
    return (
        "CHAIRMAN MODE.\n"
        "Synthesize the best final answer for the user.\n"
        "Truth-first: do not invent facts.\n"
        "Prefer actionable, verifiable steps.\n"
        "If information is missing, say what is missing.\n"
    )
```

**Note:** The existing `backend/roles.py` includes both `build_messages_for_model()` and `chairman_system_prompt()`. These are useful helpers for the new package and should be extracted.

### 4.7 `src/llm_council_mcp/models.py` -- Model Configuration

**Purpose:** Central configuration for default models, loaded from env vars.

**Key details:**
- `OPENROUTER_API_KEY` (required) -- from env
- `COUNCIL_MODELS` (optional) -- comma-separated list, defaults to 4 diverse frontier models
- `CHAIRMAN_MODEL` (optional) -- defaults to a strong model
- `COUNCIL_TIMEOUT` (optional) -- defaults to 300 seconds
- `COUNCIL_MAX_TOKENS` (optional) -- defaults to 2048
- Runtime overrides via `update_config()` (used by `council_configure` tool)

```python
import os
from dataclasses import dataclass, field
from typing import List, Optional

DEFAULT_MODELS = [
    "openai/gpt-5.2",
    "anthropic/claude-sonnet-4.5",
    "google/gemini-3-pro-preview",
    "x-ai/grok-4.1-fast",
]

DEFAULT_CHAIRMAN = "anthropic/claude-opus-4.5"

@dataclass
class CouncilConfig:
    stage1_models: List[str] = field(default_factory=list)
    stage2_models: List[str] = field(default_factory=list)
    chairman_model: str = ""
    timeout_seconds: int = 300
    max_tokens: int = 2048

    def __post_init__(self):
        if not self.stage1_models:
            env = os.getenv("COUNCIL_MODELS", "").strip()
            self.stage1_models = [m.strip() for m in env.split(",") if m.strip()] if env else list(DEFAULT_MODELS)
        if not self.stage2_models:
            self.stage2_models = list(self.stage1_models)
        if not self.chairman_model:
            self.chairman_model = os.getenv("CHAIRMAN_MODEL", "").strip() or DEFAULT_CHAIRMAN
        timeout_env = os.getenv("COUNCIL_TIMEOUT", "").strip()
        if timeout_env:
            try:
                self.timeout_seconds = int(timeout_env)
            except ValueError:
                pass
        max_tok_env = os.getenv("COUNCIL_MAX_TOKENS", "").strip()
        if max_tok_env:
            try:
                self.max_tokens = int(max_tok_env)
            except ValueError:
                pass

_config: Optional[CouncilConfig] = None

def get_config() -> CouncilConfig:
    global _config
    if _config is None:
        _config = CouncilConfig()
    return _config

def update_config(
    stage1_models: Optional[List[str]] = None,
    stage2_models: Optional[List[str]] = None,
    chairman_model: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> CouncilConfig:
    cfg = get_config()
    if stage1_models is not None:
        cfg.stage1_models = stage1_models
    if stage2_models is not None:
        cfg.stage2_models = stage2_models
    if chairman_model is not None:
        cfg.chairman_model = chairman_model
    if timeout_seconds is not None:
        cfg.timeout_seconds = timeout_seconds
    return cfg
```

**Important:** The default model IDs above match the actual `backend/council.py` defaults. Use these exact IDs. The key constraint is diversity: one model from each of OpenAI, Anthropic, Google, and xAI. Users can override via the `COUNCIL_MODELS` env var.

### 4.8 `src/llm_council_mcp/metrics.py` -- Observability

**Purpose:** Calculate deliberation metrics from stage results.

**Functions:**

```python
def calculate_metrics(
    stage1_results: list[dict],
    stage2_results: list[dict],
    label_to_model: dict[str, str],
    aggregate_rankings: list[dict],
    duration_seconds: float,
) -> dict:
    """
    Calculate deliberation metrics.

    Returns:
        {
            "duration_seconds": float,
            "models_used": list[str],
            "agreement_score": float,  # 0-1, higher = more consensus
            "dissent_ratio": float,    # 1 - agreement_score
            "cost_estimate_usd": float,
        }
    """
```

**Agreement score calculation:**
- For each pair of non-partial judges, compare their parsed rankings
- Use Kendall's tau (rank correlation) or a simpler approach:
  - For each pair of judges, count how many pairwise model comparisons they agree on
  - Agreement = (agreed pairs) / (total pairs)
  - Average across all judge pairs
- If only 1 non-partial judge, agreement_score = 0.0 (no comparison possible)

**Simple agreement algorithm (recommended):**
```python
def _agreement_score(stage2_results: list[dict], labels: list[str]) -> float:
    """Calculate agreement between judges based on top-1 consensus."""
    valid_rankings = []
    for r in stage2_results:
        if r.get("partial") or r.get("synthetic"):
            continue
        parsed = r.get("parsed_ranking", [])
        if parsed:
            valid_rankings.append(parsed)

    if len(valid_rankings) < 2:
        return 0.0

    # Count pairwise agreement on relative ordering
    n_judges = len(valid_rankings)
    n_labels = len(labels)
    total_pairs = 0
    agreed_pairs = 0

    for i in range(n_judges):
        for j in range(i + 1, n_judges):
            for a_idx in range(n_labels):
                for b_idx in range(a_idx + 1, n_labels):
                    label_a = labels[a_idx]
                    label_b = labels[b_idx]
                    # Get position in each ranking
                    try:
                        pos_i_a = valid_rankings[i].index(label_a)
                        pos_i_b = valid_rankings[i].index(label_b)
                        pos_j_a = valid_rankings[j].index(label_a)
                        pos_j_b = valid_rankings[j].index(label_b)
                    except ValueError:
                        continue
                    total_pairs += 1
                    # Do they agree on relative order?
                    if (pos_i_a < pos_i_b) == (pos_j_a < pos_j_b):
                        agreed_pairs += 1

    return agreed_pairs / total_pairs if total_pairs > 0 else 0.0
```

**Cost estimation:**
```python
# Approximate cost per 1K tokens by provider (input + output averaged)
COST_PER_1K_TOKENS = {
    "openai/": 0.005,
    "anthropic/": 0.008,
    "google/": 0.003,
    "x-ai/": 0.005,
}

def _estimate_cost(models_used: list[str], total_estimated_tokens: int = 15000) -> float:
    """Rough cost estimate. A full deliberation with 4 models uses ~15K tokens total."""
    if not models_used:
        return 0.0
    per_model_tokens = total_estimated_tokens / len(models_used)
    total = 0.0
    for model in models_used:
        rate = 0.005  # default
        for prefix, cost in COST_PER_1K_TOKENS.items():
            if model.startswith(prefix):
                rate = cost
                break
        total += (per_model_tokens / 1000) * rate
    return round(total, 4)
```

### 4.9 `src/llm_council_mcp/learning.py` -- Self-Learning Feedback Loop

**Purpose:** Store deliberation results in SQLite for pattern learning.

**Storage location:** `~/.llm-council/deliberations.db`

**Schema:**
```sql
CREATE TABLE IF NOT EXISTS deliberations (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    task_type TEXT DEFAULT 'unknown',
    models TEXT NOT NULL,           -- JSON array
    chairman TEXT NOT NULL,
    duration_seconds REAL,
    agreement_score REAL,
    dissent_ratio REAL,
    cost_estimate_usd REAL,
    quality_rating INTEGER,         -- 1-5, NULL until feedback
    depth TEXT DEFAULT 'standard'
);

CREATE TABLE IF NOT EXISTS model_rankings (
    deliberation_id TEXT NOT NULL,
    model TEXT NOT NULL,
    average_rank REAL,
    FOREIGN KEY (deliberation_id) REFERENCES deliberations(id)
);
```

**Functions:**

```python
import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path.home() / ".llm-council" / "deliberations.db"

class LearningStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        ...

    def store_deliberation(
        self,
        prompt: str,
        task_type: str,
        models: list[str],
        chairman: str,
        duration_seconds: float,
        agreement_score: float,
        dissent_ratio: float,
        cost_estimate_usd: float,
        aggregate_rankings: list[dict],
        depth: str = "standard",
    ) -> str:
        """Store a deliberation result. Returns the deliberation ID."""
        ...

    def store_feedback(self, deliberation_id: str, quality_rating: int) -> bool:
        """Store user feedback (1-5 quality rating) for a deliberation."""
        ...

    def get_model_stats(self, limit: int = 20) -> list[dict]:
        """Get model performance statistics across all deliberations."""
        ...

    def get_best_models_for_task_type(self, task_type: str, limit: int = 5) -> list[dict]:
        """Get best-performing models for a specific task type."""
        ...

    def get_total_deliberations(self) -> int:
        """Count total stored deliberations."""
        ...

    def get_task_types_seen(self) -> list[str]:
        """List all unique task types seen."""
        ...
```

**Task type auto-detection:**
```python
TASK_TYPE_PATTERNS = {
    "architectural_decision": r"\b(architect|system design|infrastructure|scaling|microservices|monolith)\b",
    "code_review": r"\b(review|refactor|code quality|best practice|clean code|lint)\b",
    "tradeoff_analysis": r"\b(tradeoff|trade-off|pros and cons|compare|versus|vs\.?|alternatives)\b",
    "debugging": r"\b(bug|error|fix|crash|exception|traceback|stack trace|debug)\b",
    "security_review": r"\b(security|auth|authentication|authorization|vulnerability|injection|XSS|CSRF)\b",
    "strategic_planning": r"\b(strategy|roadmap|planning|milestone|OKR|KPI|goal)\b",
    "creative_synthesis": r"\b(brainstorm|creative|novel|innovative|design thinking)\b",
}

def detect_task_type(prompt: str) -> str:
    prompt_lower = prompt.lower()
    scores = {}
    for task_type, pattern in TASK_TYPE_PATTERNS.items():
        matches = re.findall(pattern, prompt_lower, re.IGNORECASE)
        if matches:
            scores[task_type] = len(matches)
    if not scores:
        return "general"
    return max(scores, key=scores.get)
```

### 4.10 `src/llm_council_mcp/prompts.py` -- Prompt Templates

**Purpose:** Centralize all prompt templates used across stages.

**Stage 2 system prompt (extract from existing code -- this is critical and battle-tested):**

```python
STAGE2_SYSTEM_PROMPT = (
    "STAGE 2 EVALUATION MODE.\n"
    "You are grading anonymous answers for a product team: correctness first, then usefulness.\n"
    "Goal: choose the answer a product team would actually ship.\n"
    "Truth-first: do not invent facts; if inputs are missing, call that out as a flaw "
    "and reward answers that request the missing inputs.\n"
    "Output rules (must follow exactly):\n"
    "- No process narration, no internal thoughts, no planning text, no first-person.\n"
    "- EXACTLY 5 lines total.\n"
    "- Lines 1-4: ONE sentence each, and must include BOTH a specific strength AND a specific flaw.\n"
    "  Use this format exactly:\n"
    "  Response A: Strength: <...>; Flaw: <...>\n"
    "  Response B: Strength: <...>; Flaw: <...>\n"
    "  Response C: Strength: <...>; Flaw: <...>\n"
    "  Response D: Strength: <...>; Flaw: <...>\n"
    "- Line 5 must be the VERY LAST LINE and exactly:\n"
    "  FINAL_RANKING: <labels joined by ' > '>\n"
    "- Use ONLY the provided labels (Response A, Response B, ...). Each label must appear exactly once.\n"
    "- Do NOT copy the example ordering unless it is truly correct for the content.\n"
    "- Do NOT write 'Insufficient signal in text.' unless the response is empty/refuses "
    "or the responses are truly indistinguishable.\n"
    "- If answers are similar, break ties by correctness, then actionability, then clarity; "
    "cite ONE concrete detail from each response in its Strength/Flaw.\n"
    "- If an answer is empty or refuses, say that as the flaw.\n"
    "- Output NOTHING else."
)
```

**Stage 2 rubric template:**
```python
def build_stage2_rubric(labels: list[str], example_ranking: str) -> str:
    labels_line = ", ".join(labels)
    return (
        "You are reviewing multiple anonymous answers from different models.\n"
        "Goal: choose the answer a product team would actually ship.\n"
        "Primary criteria:\n"
        "1) Correctness / no hallucinations / respects missing info.\n"
        "2) Directly answers the user's request (or asks for required missing inputs).\n"
        "3) Actionability (specific steps, runnable commands, precise fixes).\n"
        "4) Truth-first discipline (no invented facts; explicitly notes uncertainty / missing inputs).\n"
        "\n"
        "Output format is STRICT (5 lines total; see system rules).\n"
        "Machine-readable last line must be exactly:\n"
        f"FINAL_RANKING: {example_ranking}\n"
        f"Valid labels: {labels_line}\n"
    )
```

**Stage 3 chairman prompt:**
```python
CHAIRMAN_SYSTEM_PROMPT = (
    "CHAIRMAN MODE.\n"
    "Synthesize the best final answer for the user.\n"
    "Truth-first: do not invent facts.\n"
    "Prefer actionable, verifiable steps.\n"
    "If information is missing, say what is missing.\n"
)

def build_chairman_prompt(
    user_prompt: str,
    stage1_data: list[dict],
    stage2_data: list[dict],
    aggregate_rankings: list[dict],
) -> str:
    return (
        "You are the Chairman. Synthesize the best final answer for the user.\n"
        "Use Stage 2 critiques and the aggregate rankings to guide you.\n"
        "Do not claim facts that are not present.\n\n"
        f"USER PROMPT:\n{user_prompt}\n\n"
        f"STAGE 1 OUTPUTS:\n{json.dumps(stage1_data, ensure_ascii=False)}\n\n"
        f"STAGE 2 OUTPUTS:\n{json.dumps(stage2_data, ensure_ascii=False)}\n\n"
        f"AGGREGATE RANKINGS:\n{json.dumps(aggregate_rankings, ensure_ascii=False)}\n"
    )
```

**Stage 2 repair prompt (for one-line ranking recovery):**
```python
STAGE2_REPAIR_SYSTEM_PROMPT = (
    "STAGE 2 REPAIR MODE.\n"
    "Output rules (must follow exactly):\n"
    "- Output ONLY what the user prompt requests (often a single line).\n"
    "- No narration, no headings, no extra lines.\n"
    "- Do not add critiques unless explicitly asked."
)
```

**Example ranking generator (anti-anchoring):**
```python
def example_ranking(labels: list[str]) -> str:
    """Generate a non-trivial example ordering to reduce anchoring bias."""
    if not labels:
        return "Response B > Response C > Response A > Response D"
    if len(labels) == 4:
        return f"{labels[1]} > {labels[2]} > {labels[0]} > {labels[3]}"
    rot = labels[1:] + labels[:1]
    return " > ".join(rot)
```

---

## 5. What to Strip Out

When extracting code from `backend/council.py` into the new package, **remove all of the following:**

1. **`complexity_score` parameter** -- No complexity gating. External users decide when to use the tool.

2. **`strategic_justification` parameter** -- No justification required. This was an internal CEO constraint.

3. **JSONL logging to `~/.claude/enclaude/`** -- The `_log_council_usage()` function in `mcp_server/tools.py` writes to `~/.claude/enclaude/llm-council-usage.jsonl`. Remove this entirely. Replace with the SQLite-based `learning.py`.

4. **`sys.path.insert(0, ...)` hacks** -- The existing `mcp_server/tools.py` line 19 has `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))`. The new package uses proper Python packaging with no path manipulation.

5. **Individual model env vars** -- Remove `STAGE1_MODEL_A`, `STAGE1_MODEL_B`, `STAGE1_MODEL_C`, `STAGE1_MODEL_D` and their Stage 2 equivalents. Replace with single `COUNCIL_MODELS` comma-separated env var.

6. **Contract stack forcing** -- The existing `parse_contract_ids()` auto-injects `factory_truth_v1` when any contract is specified. In the new package, contracts are fully optional and only applied when explicitly requested.

7. **CEO-specific tool descriptions** -- Remove prescriptive language like "Do NOT use for bug fixes", "Must be >= 70 complexity", etc. Replace with descriptive language explaining what the tool does and when it is most useful.

8. **References to "CEO" and "YC-level"** -- Remove any references to "CEO constraints", "CEO thresholds", or "YC-level product team" from prompts. Replace "YC-level product team" with "product team" in the Stage 2 system prompt, the Stage 2 rubric, and the adjudicator prompt. The corrected versions are already in Section 4.10 (prompts.py).

9. **`_contains_process_narration()` function** -- Keep this but simplify. It filters out model outputs that contain self-narration ("I am currently...", etc.). It's still useful for quality.

10. **`STAGE3_HELPER_MODEL` / `STAGE3_HELPER_ENABLED`** -- Remove the long-context helper complexity. The chairman handles synthesis directly. This can be added back in v0.2 if needed.

11. **`STAGE2_ADJUDICATOR_MODEL` / adjudication logic** -- Keep this but only activate when `depth="thorough"`. For `quick` and `standard` depths, skip adjudication.

---

## 6. pyproject.toml Exact Content

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "llm-council-mcp"
version = "0.1.0"
description = "MCP server for multi-LLM deliberation with anonymized peer review"
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.10"
authors = [
    {name = "LLM Council Contributors"},
]
keywords = ["mcp", "llm", "deliberation", "council", "peer-review", "openrouter"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Software Development :: Libraries",
]
dependencies = [
    "mcp>=1.2.0",
    "openai>=1.0.0",
    "pydantic>=2.0.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=5.0.0",
    "ruff>=0.4.0",
    "pyright>=1.1.0",
]

[project.scripts]
llm-council-mcp = "llm_council_mcp:main"

[project.urls]
Homepage = "https://github.com/haza/llm-council"
Repository = "https://github.com/haza/llm-council"
Issues = "https://github.com/haza/llm-council/issues"

[tool.hatch.build.targets.sdist]
include = ["src/llm_council_mcp"]

[tool.hatch.build.targets.wheel]
packages = ["src/llm_council_mcp"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
target-version = "py310"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "I", "W"]

[tool.pyright]
include = ["src"]
pythonVersion = "3.10"
typeCheckingMode = "basic"
```

---

## 7. README.md Content Guide

The README should contain the following sections in this order:

### Title and Badges
```markdown
# LLM Council MCP Server

Multi-LLM deliberation with anonymized peer review -- as an MCP tool.

[![PyPI](https://img.shields.io/pypi/v/llm-council-mcp)](https://pypi.org/project/llm-council-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
```

### Quick Start
```markdown
## Quick Start

### 1. Install
```bash
uvx llm-council-mcp
# or
pip install llm-council-mcp
```

### 2. Get an API Key
Get an [OpenRouter API key](https://openrouter.ai/keys) (provides access to all major LLM providers).

### 3. Add to Your MCP Client
```json
{
  "mcpServers": {
    "llm-council": {
      "command": "uvx",
      "args": ["llm-council-mcp"],
      "env": {
        "OPENROUTER_API_KEY": "sk-or-v1-..."
      }
    }
  }
}
```
```

### How It Works
Explain the 3-stage process with a simple diagram:
```
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

### When Deliberation Helps (and When It Doesn't)

**Works best for:**
- Architecture decisions with genuine tradeoffs
- Complex problems requiring multiple perspectives
- Code review and design critique
- Strategic planning and risk assessment
- Security review of authentication flows

**Works poorly for:**
- Simple factual questions (use a single model)
- Calculations and data lookups (use a single model)
- Tasks under time pressure (30-90 sec overhead)
- Repetitive/routine tasks

**Honesty note:** Research (DeliberationBench, Jan 2026) shows that for simple QA tasks, a single strong model outperforms multi-model deliberation. LLM Council is designed for complex decisions where multiple perspectives add genuine value.

### Configuration

Document all env vars:
- `OPENROUTER_API_KEY` (required)
- `COUNCIL_MODELS` (optional, comma-separated)
- `CHAIRMAN_MODEL` (optional)
- `COUNCIL_TIMEOUT` (optional, seconds)
- `COUNCIL_MAX_TOKENS` (optional)

### Client Configuration Examples

Provide exact JSON for:
- Claude Desktop
- Claude Code
- Cursor
- Generic MCP client

### API Reference

Document all three tools with their parameters and return shapes.

### Cost Transparency

Explain that each deliberation uses 10-15 API calls and costs approximately $0.03-0.15 depending on models and prompt length. Every response includes a `cost_estimate_usd` field.

### Self-Learning

Explain that deliberation results are stored locally in `~/.llm-council/deliberations.db` for pattern learning. No data is sent externally.

---

## 8. Testing Requirements

### Test Structure

```
tests/
  __init__.py
  conftest.py          # Shared fixtures (mock OpenAI client, sample data)
  test_council.py      # Core deliberation logic
  test_server.py       # MCP protocol compliance
  test_metrics.py      # Metrics calculation
  test_learning.py     # SQLite operations
  test_prompts.py      # Prompt template generation
  test_roles.py        # Role assignment
```

### `conftest.py` -- Shared Fixtures

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.fixture
def mock_openai_response():
    """Create a mock OpenAI chat completion response."""
    def _make(content: str):
        msg = MagicMock()
        msg.content = content
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp
    return _make

@pytest.fixture
def sample_stage1_results():
    return [
        {"model": "openai/gpt-5.2", "response": "Answer from GPT-5.2 about architecture..."},
        {"model": "anthropic/claude-sonnet-4.5", "response": "Answer from Claude about architecture..."},
        {"model": "google/gemini-3-pro-preview", "response": "Answer from Gemini about architecture..."},
        {"model": "x-ai/grok-4.1-fast", "response": "Answer from Grok about architecture..."},
    ]

@pytest.fixture
def sample_stage2_results():
    return [
        {
            "model": "openai/gpt-5.2",
            "ranking": "Response A: Strength: ...; Flaw: ...\nResponse B: ...\nResponse C: ...\nResponse D: ...\nFINAL_RANKING: Response B > Response A > Response C > Response D",
            "parsed_ranking": ["Response B", "Response A", "Response C", "Response D"],
            "partial": False,
        },
        # ... more judges
    ]

@pytest.fixture
def tmp_db(tmp_path):
    """Temporary SQLite database for learning tests."""
    return tmp_path / "test_deliberations.db"
```

### Test Requirements by File

**test_council.py:**
- Test `_parse_ranking_from_text()` with:
  - Standard format: `"FINAL_RANKING: Response B > Response A > Response C > Response D"`
  - Arrow variants: `"FINAL_RANKING: Response B -> Response A -> Response C -> Response D"`
  - Letter-only: `"FINAL_RANKING: B > A > C > D"`
  - Missing labels (should return empty list)
  - Extra text around ranking line
  - Fuzzy ranking chain (no FINAL_RANKING header)
- Test `_coerce_stage2_5line()` with partial outputs
- Test `_label_responses()` creates correct anonymization
- Test `_content_to_text()` with string, list, dict, and nested object content
- Test `_looks_like_provider_id()` filtering
- Test `calculate_aggregate_rankings()` with:
  - Normal case (all judges agree)
  - Partial judges (should be excluded)
  - Disqualified models
  - Empty results
- Test `run_deliberation()` end-to-end with mocked API calls

**test_server.py:**
- Test that the MCP server can be instantiated
- Test tool listing returns all 3 tools
- Test `council_deliberate` parameter validation
- Test `council_status` returns expected shape
- Test `council_configure` updates config
- Test error handling returns structured JSON

**test_metrics.py:**
- Test agreement score calculation with unanimous rankings
- Test agreement score with split rankings
- Test agreement score with no valid judges (returns 0.0)
- Test cost estimation with different model mixes
- Test `calculate_metrics()` returns all required fields

**test_learning.py:**
- Test database creation
- Test `store_deliberation()` creates record
- Test `store_feedback()` updates quality rating
- Test `get_model_stats()` returns aggregated stats
- Test `get_best_models_for_task_type()`
- Test `detect_task_type()` classifies correctly
- Test database handles concurrent access

**test_prompts.py:**
- Test `build_stage2_rubric()` includes all labels
- Test `example_ranking()` produces anti-anchored order
- Test `build_chairman_prompt()` includes all stage data

**test_roles.py:**
- Test `get_role_spec()` returns correct role for each provider prefix
- Test unknown provider returns DEFAULT_ROLE

### Coverage Target
- Target: >80% line coverage
- Run with: `pytest --cov=llm_council_mcp --cov-report=term-missing`

---

## 9. CI/CD

### `.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: [main, master]
  pull_request:
    branches: [main, master]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install ruff
      - run: ruff check src/ tests/
      - run: ruff format --check src/ tests/

  typecheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: pyright src/

  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -e ".[dev]"
      - run: pytest --cov=llm_council_mcp --cov-report=xml -v
      - uses: codecov/codecov-action@v4
        if: matrix.python-version == '3.12'
        with:
          file: coverage.xml

  build:
    runs-on: ubuntu-latest
    needs: [lint, typecheck, test]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install build
      - run: python -m build
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/
```

### `.github/workflows/publish.yml`

```yaml
name: Publish to PyPI

on:
  push:
    tags:
      - "v*"

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write  # Required for trusted publishing
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install build
      - run: python -m build
      - uses: pypa/gh-action-pypi-publish@release/v1
```

---

## 10. Acceptance Criteria

The implementation is complete when ALL of the following are true:

1. **`uvx llm-council-mcp` starts the server without errors** -- The package installs cleanly and the stdio MCP server starts and waits for connections.

2. **Claude Desktop can connect and call `council_deliberate`** -- The tool is listed, accepts a prompt, and returns structured JSON.

3. **A deliberation with 4 models completes and returns structured JSON with metrics** -- The response contains `answer`, `stage1_responses`, `stage2_rankings`, `aggregate_rankings`, `metrics` (with all 5 fields), and `metadata`.

4. **Self-learning stores deliberation results in SQLite** -- After a deliberation, `~/.llm-council/deliberations.db` contains a record with correct data.

5. **All tests pass** -- `pytest` runs clean with >80% coverage.

6. **README is complete with client config examples** -- Working configuration JSON for Claude Desktop, Claude Code, Cursor, and generic MCP clients.

7. **No internal CEO constraints remain** -- No `complexity_score` gating, no `strategic_justification` requirement, no JSONL logging to `~/.claude/enclaude/`, no prescriptive tool descriptions.

8. **Error handling returns structured JSON (never crashes the MCP server)** -- All exceptions are caught and returned as `{"error": "...", "error_type": "..."}`.

9. **Progress reporting works for long-running deliberations** -- `ctx.report_progress()` is called at each stage transition.

10. **`council_status` returns current config and health** -- Shows configured models, API key status, and learning stats.

---

## 11. Key Code Snippets from Existing Codebase

These are verbatim copies of critical functions from the existing codebase. Extract, adapt, and use these as the foundation for the new package.

### 11.1 OpenAI Client Setup (`backend/council.py` lines 77-112)

```python
def _get_openai_client() -> AsyncOpenAI:
    openrouter_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()

    api_key = openrouter_key or openai_key
    base_url = (os.getenv("OPENAI_BASE_URL") or "").strip() or (os.getenv("OPENROUTER_BASE_URL") or "").strip()

    if openrouter_key and not base_url:
        base_url = "https://openrouter.ai/api/v1"

    if not api_key:
        raise RuntimeError("Missing API key: set OPENROUTER_API_KEY (preferred) or OPENAI_API_KEY")

    kwargs: Dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url

    return AsyncOpenAI(**kwargs)


_CLIENT: Optional[AsyncOpenAI] = None
_CLIENT_SIG: Optional[str] = None


def _client() -> AsyncOpenAI:
    global _CLIENT, _CLIENT_SIG

    openrouter_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    base_url = (os.getenv("OPENAI_BASE_URL") or "").strip() or (os.getenv("OPENROUTER_BASE_URL") or "").strip()

    sig = f"{openrouter_key or openai_key}|{base_url}"
    if _CLIENT is None or _CLIENT_SIG != sig:
        _CLIENT = _get_openai_client()
        _CLIENT_SIG = sig
    return _CLIENT
```

### 11.2 Message Builders (`backend/council.py` lines 115-207)

These two functions build the system+user message lists for each stage. Key behavior:
- Stage 2 judges do NOT get contract system messages or role prompts (to avoid interference with the strict 5-line format)
- Stage 2 gets its own evaluator system prompt (from `prompts.py` in the new package)
- Stage 1 and Stage 3 get contract messages + role-based system prompts

```python
def _member_messages(
    model: str,
    user_prompt: str,
    contract_stack: Optional[str],
    stage: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Build messages for council members. Stage controls which system prompts are included."""
    # Stage 2 must be a clean evaluator persona -- no contracts, no role prompts.
    if stage in ("stage2", "stage2_repair"):
        system_msgs: List[Dict[str, str]] = []
    else:
        system_msgs = build_contract_system_messages(contract_stack)

    # Keep per-model role prompts for generation (Stage 1), NOT for judging (Stage 2).
    if stage not in ("stage2", "stage2_repair"):
        role_spec = get_role_spec(model)
        system_msgs.append({"role": "system", "content": role_spec.system_prompt})

    # Stage 2 gets the evaluator system prompt (injected here or from prompts.py).
    if stage == "stage2":
        system_msgs.append({"role": "system", "content": STAGE2_SYSTEM_PROMPT})
    elif stage == "stage2_repair":
        system_msgs.append({
            "role": "system",
            "content": (
                "STAGE 2 REPAIR MODE.\n"
                "Output rules (must follow exactly):\n"
                "- Output ONLY what the user prompt requests (often a single line).\n"
                "- No narration, no headings, no extra lines.\n"
                "- Do not add critiques unless explicitly asked."
            ),
        })

    return system_msgs + [{"role": "user", "content": user_prompt}]


def _chairman_messages(model: str, chairman_prompt: str, contract_stack: Optional[str]) -> List[Dict[str, str]]:
    """Build messages for the chairman (Stage 3)."""
    role_spec = get_role_spec(model)
    system_msgs = build_chairman_contract_system_messages(contract_stack)
    system_msgs.append({"role": "system", "content": role_spec.system_prompt})
    return system_msgs + [{"role": "user", "content": chairman_prompt}]
```

**Important:** In the new package, `STAGE2_SYSTEM_PROMPT` comes from `prompts.py` (Section 4.10). The `_member_messages` function should import it from there.

### 11.3 Core Chat Function (`backend/council.py` lines 367-424)

```python
async def _chat(model: str, messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
    client = _client()
    max_gen_tokens = 2048
    _env = (os.getenv("COUNCIL_MAX_TOKENS") or "").strip()
    if _env:
        try:
            max_gen_tokens = int(_env)
        except ValueError:
            max_gen_tokens = 2048

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_gen_tokens,
        )
    except TypeError:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_output_tokens=max_gen_tokens,
        )

    msg = resp.choices[0].message
    content = getattr(msg, "content", None)
    text = _content_to_text(content)

    if _looks_like_provider_id(text):
        text = ""

    if not text:
        raw_msg = None
        try:
            raw_msg = msg.model_dump()
        except Exception:
            try:
                raw_msg = dict(msg)
            except Exception:
                try:
                    raw_msg = vars(msg)
                except Exception:
                    raw_msg = None
        text = _deep_extract_text(raw_msg)
        if _looks_like_provider_id(text):
            text = ""

    return (text or "").strip()
```

### 11.4 Content-to-Text Converter (`backend/council.py` lines 209-269)

```python
def _content_to_text(content: Any) -> str:
    """Convert OpenAI/OpenRouter message.content shapes into plain text."""

    def part_to_text(p: Any) -> str:
        if p is None:
            return ""
        if isinstance(p, str):
            return p
        if isinstance(p, dict):
            if isinstance(p.get("text"), str):
                return p["text"]
            t = p.get("text")
            if isinstance(t, dict) and isinstance(t.get("value"), str):
                return t["value"]
            if isinstance(p.get("content"), str):
                return p["content"]
            c = p.get("content")
            if isinstance(c, list):
                return "".join(part_to_text(x) for x in c)
            return ""

        t = getattr(p, "text", None)
        if isinstance(t, str):
            return t
        if t is not None:
            v = getattr(t, "value", None)
            if isinstance(v, str):
                return v

        c = getattr(p, "content", None)
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "".join(part_to_text(x) for x in c)
        if c is not None:
            return part_to_text(c)

        if hasattr(p, "model_dump"):
            try:
                return part_to_text(p.model_dump())
            except Exception:
                return ""

        if hasattr(p, "__dict__"):
            try:
                return part_to_text(vars(p))
            except Exception:
                return ""

        return ""

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part_to_text(x) for x in content)
    if isinstance(content, dict):
        return part_to_text(content)

    return part_to_text(content) or str(content)
```

### 11.5 Provider ID Filtering (`backend/council.py` lines 272-282)

```python
def _looks_like_provider_id(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if re.fullmatch(r"gen-\d{6,}-[A-Za-z0-9_\-]{8,}", t):
        return True
    if re.fullmatch(r"(chatcmpl|cmpl|req|request|run|msg)-[A-Za-z0-9\-]{12,}", t, flags=re.I):
        return True
    if re.fullmatch(r"[A-Za-z0-9\-]{24,}", t) and (" " not in t) and ("\n" not in t):
        return True
    return False
```

### 11.6 Deep Text Extraction (`backend/council.py` lines 285-364)

```python
def _deep_extract_text(obj: Any) -> str:
    SKIP_KEYS = {
        "id", "request_id", "generation_id", "gen_id", "model", "provider",
        "usage", "created", "created_at", "timestamp", "object",
        "finish_reason", "system_fingerprint",
    }
    TEXT_KEYS = {"content", "text", "value", "output_text"}
    candidates: List[str] = []

    def add_candidate(s: Any) -> None:
        if not isinstance(s, str):
            return
        t = s.strip()
        if not t:
            return
        if _looks_like_provider_id(t):
            return
        candidates.append(t)

    def walk(o: Any, key: Optional[str] = None) -> None:
        if o is None:
            return
        if isinstance(o, str):
            if key and (key.lower() in TEXT_KEYS or key.lower().endswith("content")):
                add_candidate(o)
            return
        if isinstance(o, list):
            for x in o:
                walk(x, key=key)
            return
        if isinstance(o, dict):
            for k, v in o.items():
                lk = (k or "").lower()
                if lk in SKIP_KEYS or lk.endswith("_id"):
                    continue
                if lk in TEXT_KEYS or lk.endswith("content"):
                    if isinstance(v, str):
                        add_candidate(v)
                        continue
                    if isinstance(v, list):
                        for x in v:
                            walk(x, key=lk)
                        continue
                    if isinstance(v, dict):
                        inner_val = v.get("value") if isinstance(v.get("value"), str) else None
                        if inner_val:
                            add_candidate(inner_val)
                        walk(v, key=lk)
                        continue
                walk(v, key=lk)
            return

        if hasattr(o, "model_dump"):
            try:
                walk(o.model_dump(), key=key)
                return
            except Exception:
                pass
        if hasattr(o, "__dict__"):
            try:
                walk(vars(o), key=key)
                return
            except Exception:
                pass

    walk(obj, key=None)
    if not candidates:
        return ""
    best = max(candidates, key=lambda s: len(s))
    return best.strip()
```

### 11.7 Process Narration Filter (`backend/council.py` lines 466-486)

```python
def _contains_process_narration(text: str) -> bool:
    """Detect self-narration in model output (e.g., 'I am currently analyzing...')."""
    raw = _normalize_ws(text or "").lower()
    return bool(
        re.search(
            r"\b("
            r"i am currently|i'm currently|i am now|i'm now|"
            r"i will now|i am going to|i'm going to|"
            r"i have just|i've just|i just|"
            r"i have finished|i've finished|just finished|"
            r"initiating the analysis|my focus is|the plan is|"
            r"finalizing the strategy|processing the parameters|"
            r"assessing the conundrum|interpreting the context"
            r")\b",
            raw,
        )
    )
```

This is used in Stage 2 `_acceptable()` to reject judge outputs that contain process narration instead of actual evaluations.

### 11.8 Response Labeling / Anonymization (`backend/council.py` lines 427-435)

```python
def _label_responses(stage1_results: List[Dict[str, Any]]) -> Tuple[List[str], Dict[str, str]]:
    label_to_model: Dict[str, str] = {}
    labeled_blocks: List[str] = []
    for idx, r in enumerate(stage1_results):
        label = f"Response {chr(ord('A') + idx)}"
        model = r.get("model") or f"model_{idx}"
        label_to_model[label] = model
        labeled_blocks.append(f"{label}:\n{r.get('response','')}".strip())
    return labeled_blocks, label_to_model
```

### 11.9 Ranking Parsing (`backend/council.py` lines 489-698)

```python
def _normalize_ws(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    s = s.replace("\u00a0", " ")
    s = s.replace("\u202f", " ")
    s = s.replace("\u2007", " ")
    return re.sub(r"\s+", " ", s).strip()


def _strip_wrappers(text: str) -> str:
    """Remove common wrappers (code fences, stray backticks/quotes) from model output."""
    t = (text or "").strip()
    if not t:
        return ""
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    t = t.strip().strip("`").strip().strip('"').strip().strip("'").strip()
    return t


def _extract_final_ranking_line(text: str) -> str:
    raw = _strip_wrappers((text or "").strip())
    if not raw:
        return ""
    lines = [_normalize_ws(ln) for ln in raw.splitlines() if ln and ln.strip()]
    for ln in reversed(lines):
        m = re.search(r"\bFINAL_RANKING\s*:\s*", ln, flags=re.I)
        if m:
            return ln[m.start():].strip()
    return ""


def _extract_fuzzy_ranking_chain(text: str) -> str:
    raw = _strip_wrappers((text or "").strip())
    if not raw:
        return ""
    raw = _normalize_ws(raw)
    raw = (
        raw.replace("\u2192", ">")
        .replace("\u21d2", ">")
        .replace("->", ">")
        .replace("\uff1e", ">")
        .replace("\u203a", ">")
        .replace("\u00bb", ">")
    )
    pat_full = re.compile(r"(Response\s*[A-Z](?:\s*>\s*Response\s*[A-Z])+)", flags=re.I)
    matches = pat_full.findall(raw)
    if matches:
        return matches[-1].strip()
    pat_letters = re.compile(r"\b([A-D](?:\s*>\s*[A-D]){2,})\b", flags=re.I)
    m = pat_letters.search(raw)
    if m:
        chain = m.group(1)
        parts = [p.strip() for p in chain.split(">") if p.strip()]
        if parts:
            return " > ".join([f"Response {p.upper()}" for p in parts])
    return ""


def _parse_ranking_from_text(text: str, allowed_labels: Optional[List[str]] = None) -> List[str]:
    strict_line = _extract_final_ranking_line(text)
    if strict_line:
        return _parse_ranking_order(strict_line, allowed_labels=allowed_labels)
    chain = _extract_fuzzy_ranking_chain(text)
    if not chain:
        return []
    return _parse_ranking_order(f"FINAL_RANKING: {chain}", allowed_labels=allowed_labels)


def _parse_ranking_order(text: str, allowed_labels: Optional[List[str]] = None) -> List[str]:
    raw = _normalize_ws(text or "")
    if not raw:
        return []
    allowed = set(allowed_labels) if allowed_labels else None
    m = re.search(r"\bFINAL_RANKING\s*:\s*(.+)$", raw, flags=re.I)
    if not m:
        return []
    tail = _normalize_ws(m.group(1) or "")
    if not tail:
        return []
    tail = tail.replace("\u2192", ">").replace("\u21d2", ">").replace("->", ">")
    chunks = [c.strip() for c in tail.split(">") if c.strip()]

    def norm_label(s: str) -> Optional[str]:
        s = (s or "").strip()
        if not s:
            return None
        m1 = re.search(r"response\s*([A-Z])\b", s, flags=re.I)
        if m1:
            lab = f"Response {m1.group(1).upper()}"
            if allowed is None or lab in allowed:
                return lab
            return None
        m2 = re.fullmatch(r"[A-Z]", s, flags=re.I)
        if m2:
            lab = f"Response {s.upper()}"
            if allowed is None or lab in allowed:
                return lab
            return None
        return None

    out: List[str] = []
    seen = set()
    for ch in chunks:
        lab = norm_label(ch)
        if lab and lab not in seen:
            seen.add(lab)
            out.append(lab)

    if not out:
        return []
    if allowed is not None:
        if len(out) != len(allowed) or set(out) != allowed:
            return []
    return out
```

### 11.10 Stage 2 5-Line Coercion (`backend/council.py` lines 573-640)

```python
def _coerce_stage2_5line(text: str, labels: List[str]) -> str:
    """Coerce judge output into strict 5-line format (A-D + FINAL_RANKING)."""
    if not text:
        return ""

    raw_lines = [ln for ln in (text or "").splitlines() if ln and ln.strip()]
    crit: Dict[str, str] = {}

    for ln in raw_lines:
        nln = _normalize_ws(ln)
        m = re.match(
            r"^\s*(?:[-*]\s*)?(?:Response\s*)?([A-D])\s*(?:[:\-\u2013\u2014\.]|\))\s*(.+)$",
            nln,
            flags=re.I,
        )
        if not m:
            continue
        letter = (m.group(1) or "").upper()
        body = (m.group(2) or "").strip()
        if not letter:
            continue
        label = f"Response {letter}"
        if label not in labels:
            continue
        if body:
            crit[label] = f"{label}: {body}"

    parsed_any = _parse_ranking_from_text(text, allowed_labels=None)
    if not parsed_any:
        return ""

    keep: List[str] = []
    seen = set()
    for lab in parsed_any:
        if lab in labels and lab not in seen:
            seen.add(lab)
            keep.append(lab)

    full = keep + [lab for lab in labels if lab not in seen]
    if len(full) != len(labels) or set(full) != set(labels):
        return ""

    final_line = "FINAL_RANKING: " + " > ".join(full)

    def line_for(letter: str) -> str:
        label = f"Response {letter}"
        if label in crit:
            return crit[label]
        return f"{label}: Strength: None; Flaw: Insufficient signal in text."

    return "\n".join(
        [
            line_for("A"),
            line_for("B"),
            line_for("C"),
            line_for("D"),
            final_line,
        ]
    )
```

### 11.11 Stage 1: Collect Responses (`backend/council.py` lines 711-780)

```python
async def stage1_collect_responses(user_prompt: str, contract_stack: Optional[str] = None) -> List[Dict[str, Any]]:
    models = [m for m in DEFAULT_STAGE1_MODELS if m]
    results: List[Dict[str, Any]] = []
    errors: Dict[str, str] = {}

    async def _try_once(m: str) -> Optional[Dict[str, Any]]:
        out = await _chat(m, _member_messages(m, user_prompt, contract_stack, stage="stage1"), temperature=0.3)
        out = (out or "").strip()
        if _looks_like_provider_id(out):
            out = ""
        if not out:
            return None
        ev = evaluate_contract_compliance(user_prompt, out, contract_stack, stage="stage1")
        return {"model": m, "response": out, "contract_eval": ev}

    async def run_one(m: str) -> Optional[Dict[str, Any]]:
        try:
            r = await _try_once(m)
            if r:
                return r
            if m.startswith("google/"):
                await asyncio.sleep(0.15)
                r2 = await _try_once(m)
                if r2:
                    return r2
            errors[m] = "Empty response"
            return None
        except Exception as e:
            errors[m] = f"{type(e).__name__}: {e}"
            if m.startswith("google/"):
                try:
                    await asyncio.sleep(0.15)
                    r2 = await _try_once(m)
                    if r2:
                        errors.pop(m, None)
                        return r2
                except Exception as e2:
                    errors[m] = f"{type(e2).__name__}: {e2}"
            return None

    done = await asyncio.gather(*[run_one(m) for m in models])

    for m, item in zip(models, done):
        if item:
            results.append(item)
        else:
            results.append(
                {
                    "model": m,
                    "response": "(No response from model.)",
                    "contract_eval": {
                        "status": "FAIL",
                        "eligible": False,
                        "hard_fail_reasons": ["Empty response"],
                    },
                    "synthetic": True,
                    "synthetic_reason": "stage1_empty_fallback",
                }
            )

    real_count = sum(1 for r in results if not r.get("synthetic"))
    if real_count == 0 and errors:
        raise RuntimeError(f"Stage1 all failed: {errors}")

    return results
```

### 11.12 Stage 2: Collect Rankings (simplified view -- `backend/council.py` lines 783-1169)

The full Stage 2 is complex (390 lines). The key structure:

1. Build anonymized prompt with rubric
2. For each judge model, run a multi-attempt flow:
   - Attempt 0: Normal judge prompt at low temperature
   - Attempt 1: Strict re-judge with explicit format requirements
   - Attempt 2: Rewrite previous output into 5-line format
   - Last resort: One-line repair (just the ranking line)
3. Quality classification: check for placeholders, missing Strength/Flaw, evidence overlap
4. Optional adjudication when judges disagree (only for `depth="thorough"`)
5. Return `(results_list, label_to_model_dict)`

**You should extract the full logic from Section 11.9 and 11.10 above plus the stage2 flow. Simplify by:**
- Removing `_contains_process_narration()` check (or keeping it as a simple filter)
- Removing the evidence proxy (`_evidence_tokens`, `_evidence_ok`) -- these are optional quality checks
- Keeping the multi-attempt repair flow but simplifying to 2 attempts max for `quick` depth

### 11.13 Aggregate Rankings (`backend/council.py` lines 1172-1249)

```python
def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str],
    contract_evals_by_model: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    rank_sums: Dict[str, float] = {}
    rank_counts: Dict[str, int] = {}

    contract_evals_by_model = contract_evals_by_model or {}

    disqualified_models: Dict[str, List[str]] = {}
    for m, ev in contract_evals_by_model.items():
        if ev and ev.get("eligible") is False:
            disqualified_models[m] = ev.get("hard_fail_reasons", []) or ["Hard FAIL"]

    for voter in stage2_results or []:
        if voter.get("synthetic") or voter.get("partial"):
            continue
        parsed = voter.get("parsed_ranking") or []
        if not parsed:
            continue

        ordered_models: List[str] = []
        for label in parsed:
            mid = label_to_model.get(label)
            if mid:
                ordered_models.append(mid)

        for i, mid in enumerate(ordered_models):
            if mid in disqualified_models:
                continue
            rank_sums[mid] = rank_sums.get(mid, 0.0) + float(i + 1)
            rank_counts[mid] = rank_counts.get(mid, 0) + 1

    aggregates: List[Dict[str, Any]] = []

    for mid, s in rank_sums.items():
        c = rank_counts.get(mid, 0)
        if c > 0:
            aggregates.append(
                {
                    "model": mid,
                    "average_rank": float(s) / float(c),
                    "rankings_count": int(c),
                    "disqualified": False,
                    "disqualify_reasons": [],
                }
            )

    for mid, reasons in disqualified_models.items():
        aggregates.append(
            {
                "model": mid,
                "average_rank": 9998.0,
                "rankings_count": int(rank_counts.get(mid, 0)),
                "disqualified": True,
                "disqualify_reasons": reasons,
            }
        )

    for label, model in (label_to_model or {}).items():
        if any(a["model"] == model for a in aggregates):
            continue
        disq = model in disqualified_models
        aggregates.append(
            {
                "model": model,
                "average_rank": 9999.0,
                "rankings_count": 0,
                "disqualified": disq,
                "disqualify_reasons": (contract_evals_by_model.get(model, {}) or {}).get("hard_fail_reasons", [])
                if disq
                else [],
            }
        )

    aggregates.sort(key=lambda x: (bool(x.get("disqualified")), float(x.get("average_rank", 9999.0))))
    return aggregates
```

### 11.14 Stage 3: Chairman Synthesis (`backend/council.py` lines 1252-1387)

```python
async def stage3_synthesize_final(
    user_prompt: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str],
    aggregate_rankings: List[Dict[str, Any]],
    contract_stack: Optional[str] = None,
    contract_evals_by_model: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    chairman_model = CHAIRMAN_MODEL

    s1 = [
        {"model": r.get("model"), "response": r.get("response"), "contract_eval": r.get("contract_eval")}
        for r in stage1_results
    ]
    s2 = [
        {
            "model": r.get("model"),
            "ranking": r.get("ranking"),
            "parsed_ranking": r.get("parsed_ranking"),
            "synthetic": bool(r.get("synthetic")),
            "partial": bool(r.get("partial")),
            "partial_reason": r.get("partial_reason"),
        }
        for r in stage2_results
    ]

    chairman_prompt = (
        "You are the Chairman. Synthesize the best final answer for the user.\n"
        "Use Stage 2 critiques and the aggregate rankings to guide you.\n"
        "Do not claim traction or facts that are not present.\n\n"
        f"USER PROMPT:\n{user_prompt}\n\n"
        f"STAGE 1 OUTPUTS:\n{json.dumps(s1, ensure_ascii=False)}\n\n"
        f"STAGE 2 OUTPUTS:\n{json.dumps(s2, ensure_ascii=False)}\n\n"
        f"AGGREGATE RANKINGS:\n{json.dumps(aggregate_rankings, ensure_ascii=False)}\n"
    )

    try:
        out = await _chat(
            chairman_model,
            _chairman_messages(chairman_model, chairman_prompt, contract_stack),
            temperature=0.2,
        )
        out = (out or "").strip()
        ev = evaluate_contract_compliance(user_prompt, out, contract_stack, stage="stage3")

        return {"model": chairman_model, "response": out, "contract_eval": ev}
    except Exception:
        return {"model": chairman_model, "response": "", "contract_eval": {"status": "FAIL", "eligible": False}}
```

### 11.15 Contract Evaluation (`backend/contracts.py` lines 233-280)

```python
def evaluate_contract_compliance(
    user_prompt: str,
    response_text: str,
    contract_stack: Optional[str] = None,
    *,
    stage: str = "stage1",
) -> Dict[str, Any]:
    hard_fail_reasons: List[str] = []
    warnings: List[str] = []
    checks: Dict[str, Any] = {}

    if _needs_rubric_table_first(user_prompt):
        ok_table = _contains_markdown_table_early(response_text)
        checks["rubric_table_first"] = ok_table
        if not ok_table:
            hard_fail_reasons.append("Requested 'Start with the rubric table' but no markdown table detected near the top.")

    prohibited = _detect_prohibited_claims(response_text)
    if prohibited:
        checks["prohibited"] = prohibited
        for _, rs in prohibited.items():
            hard_fail_reasons.extend(rs)

    warnings.extend(_detect_soft_warnings(user_prompt, response_text, contract_stack))

    status = "PASS"
    if hard_fail_reasons:
        status = "FAIL"
    elif warnings:
        status = "WARN"

    return {
        "stage": stage,
        "status": status,
        "eligible": status != "FAIL",
        "hard_fail_reasons": hard_fail_reasons,
        "warnings": warnings,
        "checks": checks,
        "evaluated_at": datetime.utcnow().isoformat(),
    }
```

### 11.16 Factory Truth Contract (`backend/contracts.py` lines 30-47)

```python
FACTORY_TRUTH_V1 = ContractSpec(
    contract_id="factory_truth_v1",
    name="Factory Truth-First v1",
    system_prompt=(
        "You are running inside a product-agnostic LLM Council factory.\n"
        "Factory Contract (must follow):\n"
        "1) Truth-first: prioritize what is most likely true about the user's real problem; state uncertainty explicitly.\n"
        "2) Separate facts from guesses: tag non-trivial claims as [Observed] / [Assumed] / [Inferred]; do not blur them.\n"
        "3) Ask at most 1 killer question only if it would change your recommendation; otherwise proceed with best-guess + assumptions.\n"
        "4) Smallest valuable action: propose something testable with minimal build; avoid dependencies and platform thinking.\n"
        "5) One primary risk: name the single highest-risk failure mode and add one simple guardrail.\n"
        "6) One metric that matters: pick one leading indicator; define a clear pass/fail threshold.\n"
        "7) Design for the edge user: handle the most constrained path (low attention, low literacy, high stress) by default.\n"
        "8) Make it legible: include a short rationale and a clear next step; no jargon; no sprawling option lists.\n"
        "9) Creativity inside constraints: propose at most 2 variants (Conservative baseline + Bold alternative), both testable.\n"
        "10) Synthesis discipline: do not introduce new mechanisms unless you label them [New Proposal] and explain why.\n"
    ),
)
```

---

## 12. Environment Setup

### Required
- Python 3.10+
- `uv` for dependency management (recommended) or `pip`

### Required Environment Variable
- `OPENROUTER_API_KEY` -- Get from [openrouter.ai/keys](https://openrouter.ai/keys)

### Optional Environment Variables
- `COUNCIL_MODELS` -- Comma-separated list of OpenRouter model IDs (default: `openai/gpt-5.2,anthropic/claude-sonnet-4.5,google/gemini-3-pro-preview,x-ai/grok-4.1-fast`)
- `CHAIRMAN_MODEL` -- OpenRouter model ID for chairman (default: `anthropic/claude-opus-4.5`)
- `COUNCIL_TIMEOUT` -- Timeout in seconds for full deliberation (default: `300`)
- `COUNCIL_MAX_TOKENS` -- Max generation tokens per model call (default: `2048`)
- `OPENAI_API_KEY` -- Alternative to OPENROUTER_API_KEY (for direct OpenAI use)
- `OPENAI_BASE_URL` -- Custom base URL (overrides OpenRouter default)

### `.env.example`

```env
# Required: OpenRouter API key (provides access to all major LLM providers)
# Get yours at: https://openrouter.ai/keys
OPENROUTER_API_KEY=sk-or-v1-your-key-here

# Optional: Override default council models (comma-separated OpenRouter model IDs)
# COUNCIL_MODELS=openai/gpt-5.2,anthropic/claude-sonnet-4.5,google/gemini-3-pro-preview,x-ai/grok-4.1-fast

# Optional: Override chairman model (synthesizes the final answer)
# CHAIRMAN_MODEL=anthropic/claude-opus-4.5

# Optional: Timeout for full deliberation in seconds (default: 300)
# COUNCIL_TIMEOUT=300

# Optional: Max generation tokens per model call (default: 2048)
# COUNCIL_MAX_TOKENS=2048
```

---

## 13. MCP Client Configuration Examples

### Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS)

```json
{
  "mcpServers": {
    "llm-council": {
      "command": "uvx",
      "args": ["llm-council-mcp"],
      "env": {
        "OPENROUTER_API_KEY": "sk-or-v1-your-key-here"
      }
    }
  }
}
```

### Claude Code (`.mcp.json` in project root)

```json
{
  "mcpServers": {
    "llm-council": {
      "command": "uvx",
      "args": ["llm-council-mcp"],
      "env": {
        "OPENROUTER_API_KEY": "sk-or-v1-your-key-here"
      }
    }
  }
}
```

### Cursor (`~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "llm-council": {
      "command": "uvx",
      "args": ["llm-council-mcp"],
      "env": {
        "OPENROUTER_API_KEY": "sk-or-v1-your-key-here"
      }
    }
  }
}
```

### With Custom Models

```json
{
  "mcpServers": {
    "llm-council": {
      "command": "uvx",
      "args": ["llm-council-mcp"],
      "env": {
        "OPENROUTER_API_KEY": "sk-or-v1-your-key-here",
        "COUNCIL_MODELS": "openai/gpt-5.2,anthropic/claude-sonnet-4.5,google/gemini-3-pro-preview",
        "CHAIRMAN_MODEL": "anthropic/claude-opus-4.5"
      }
    }
  }
}
```

### Generic MCP Client (Python)

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    server_params = StdioServerParameters(
        command="uvx",
        args=["llm-council-mcp"],
        env={"OPENROUTER_API_KEY": "sk-or-v1-your-key-here"},
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # List available tools
            tools = await session.list_tools()
            print(f"Available tools: {[t.name for t in tools.tools]}")

            # Run a deliberation
            result = await session.call_tool(
                "council_deliberate",
                arguments={"prompt": "Should we use microservices or a monolith for our new SaaS product?"},
            )
            print(result)

asyncio.run(main())
```

---

## LICENSE

Use MIT License:

```
MIT License

Copyright (c) 2026 LLM Council Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## Implementation Notes for Codex

1. **Start with the package structure** -- Create all directories and files first.

2. **Extract council.py first** -- This is the largest and most critical file. Copy the helper functions verbatim from Section 11, then build `run_deliberation()` as the orchestrator.

3. **Use `mcp.server.fastmcp.FastMCP`** -- This is the correct import path. The `fastmcp` package was merged into the official `mcp` SDK. Do NOT install a separate `fastmcp` package.

4. **Progress reporting** -- Use `ctx.report_progress(current, total)` inside the tool function. The `ctx` parameter is automatically injected by FastMCP when you include `ctx: Context` as a parameter in the tool function.

5. **Error handling pattern** -- Every tool function should wrap its entire body in try/except and return structured JSON errors:
   ```python
   @mcp.tool()
   async def council_deliberate(prompt: str, ..., ctx: Context) -> str:
       try:
           # ... do work ...
           return json.dumps(result, indent=2, ensure_ascii=False)
       except Exception as e:
           return json.dumps({"error": str(e), "error_type": type(e).__name__}, indent=2)
   ```

6. **Testing without API keys** -- All tests should mock the OpenAI client. Use `unittest.mock.AsyncMock` for async functions.

7. **SQLite thread safety** -- Use `check_same_thread=False` when creating the SQLite connection in `learning.py` since MCP servers run async.

8. **The ranking parser is battle-tested** -- Do NOT simplify the ranking parsing logic. It handles many edge cases from real model outputs (arrow variants, letter-only rankings, missing labels, code fences, etc.). Copy it verbatim.

9. **Google retry pattern** -- The existing code has a special retry for Google models (`if m.startswith("google/"): await asyncio.sleep(0.15)`). Keep this -- Google models sometimes return empty on first attempt.

10. **The `max_tokens` vs `max_output_tokens` fallback** -- Some providers use `max_tokens`, others use `max_output_tokens`. The `_chat()` function tries `max_tokens` first and falls back to `max_output_tokens` on TypeError. Keep this pattern.

---

**End of specification. This document is self-contained. Build it.**
