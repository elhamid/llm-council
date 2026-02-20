# LLM Council MCP Server — Release Plan

## rUv Framework Applied

This plan incorporates Reuven Cohen's decision framework across 10 critical dimensions:

1. **Timeline Correction (rUv's Hive-Mind Reality)**: Replaced human "weeks" with agentic "sessions" — parallel agents can ship in hours what humans code in weeks
2. **Smallest Valuable Action (rUv #4)**: v0.1 = ONE tool (council_deliberate), ONE env var — ruthlessly simplified
3. **Verification Over Trust (rUv #3)**: Honest benchmarks showing where deliberation helps AND fails, transparency on DeliberationBench limitations
4. **Pioneer-then-Standardize (rUv #7)**: Define the deliberation standard before competitors do — blog post + community posts
5. **Self-Learning Feedback Loop (rUv #5)**: Every deliberation stores patterns, trains model selection, learns when deliberation adds value
6. **Ship-Measure-Iterate (rUv #1)**: Removed 42-item checklist, replaced with metrics-driven iteration
7. **Community Launch (rUv #10)**: Target 100 early users via r/claudedev, r/aipromptprogramming, Agentics Foundation Discord
8. **Practical Metrics (rUv #9)**: Every council call returns time, models used, agreement score, dissent ratio from v0.1
9. **Tool-Free Expertise-Paid (rUv #10)**: Open source tool, monetize through consulting on deliberation architecture
10. **Problems Solved Not Features Shipped (rUv #6)**: Metrics focus on "complex decisions improved" not "tools implemented"

---

## Decision: GO (with conditions)

The core council logic (3-stage deliberation with anonymized peer review) is genuinely novel — no comparable MCP server exists in the ecosystem. The code already runs standalone without the FastAPI backend. The gap to production-grade is packaging, decoupling, and adding self-learning — not a rewrite.

## Market Analysis

### Competitive Landscape
- **MindBridge MCP** (github.com/pinkpixel-dev/mindbridge-mcp) — multi-LLM routing/comparison but NO structured deliberation or peer review
- **Ultimate MCP Server** (github.com/Dicklesworthstone/ultimate_mcp_server) — model competitions but limited to code/text generation, not general-purpose evaluation
- **MCP-LiteLLM** (github.com/dinhdobathi1992/mcp-server-litellm) — pure routing, no evaluation
- **OpenRouter MCP Server** (github.com/yasanglass/openrouter-mcp-server) — basic single-model calls
- **MCPBench** (github.com/modelscope/MCPBench) — evaluation framework for benchmarking MCP servers themselves, not a user-facing tool

### Gap
No production MCP server provides: multi-LLM parallel querying + structured peer review/deliberation + automated quality scoring + consensus aggregation + self-learning feedback loop. This is the gap.

### Demand Signals
- Karpathy's llm-council went viral (Dec 2025), validating the concept
- Mixture-of-Agents (MoA) research at ICLR 2025: 65.1% on AlpacaEval vs GPT-4's 57.5%
- MCP ecosystem exploding in 2026 (410+ servers across 34 categories on awesome-mcp-servers)
- No dedicated LLM evaluation/comparison category exists yet in MCP registries

### Deliberation Caution (DeliberationBench, Jan 2026)
- arxiv.org/abs/2601.08835 found best-single-model achieves 82.5% win rate vs 13.8% for best deliberation protocol on QA tasks
- However: they excluded frontier models, tested only QA tasks, and did NOT test the anonymized peer review + chairman synthesis protocol that llm-council uses
- The study notes "Teams with moderate diversity exhibit small but consistent gains" — which is exactly what role specialization provides
- **Mitigation**: Position for complex decisions/architecture/tradeoffs, NOT factual retrieval. Document honestly in README with verification metrics.

## Current State Assessment

### What EXISTS and is solid
| Component | File | Status |
|-----------|------|--------|
| 3-stage async pipeline | backend/council.py (1,453 lines) | Production-quality logic, robust error recovery, multi-retry judge parsing |
| OpenRouter client | backend/council.py (lines 77-112) | Clean singleton pattern, handles key detection and base_url switching |
| Role specialization | backend/roles.py (115 lines) | Builder/Reviewer/Integrator/Contrarian + provider-based defaults |
| Anonymized peer review | backend/council.py (lines 427-435, 783-1169) | Robust labeling, multi-pass ranking repair, evidence validation |
| Contract compliance | backend/contracts.py (309 lines) | Lightweight post-hoc checking with hard-fail/soft-warn categories |
| Aggregate ranking | backend/council.py (lines 1172-1249) | Handles disqualifications, partial judges, proper averaging |
| Chairman synthesis | backend/council.py (lines 1252-1387) | Optional briefing from long-context model, contract repair loop |
| MCP server skeleton | mcp_server/mcp_server.py (239 lines) | Working stdio transport, 3 tools |
| MCP tool implementations | mcp_server/tools.py (323 lines) | deliberate/configure/status with validation |

### Architecture Confirmation
The council logic runs INDEPENDENTLY without the web server:
- council.py::_client() creates its own AsyncOpenAI directly from env vars
- stage1_collect_responses(), stage2_collect_rankings(), calculate_aggregate_rankings(), stage3_select_winner() are all standalone async functions
- Only dependencies: contracts.py (system prompts), roles.py (role specs), openai (client)
- The existing mcp_server/tools.py already demonstrates this — imports and calls council functions directly

### Gaps for Production-Grade Standalone
| Gap | Severity | Notes |
|-----|----------|-------|
| No self-learning feedback loop | CRITICAL | Must capture deliberation patterns, model performance, task type correlations |
| Standalone packaging (no uvx/pip install) | CRITICAL | No pyproject.toml with [project.scripts], no PyPI entry point |
| Internal CEO constraints baked in | HIGH | complexity_threshold gating, strategic_justification requirement, JSONL logging to ~/.claude/enclaude/ |
| No observability metrics | HIGH | Must return time, models, agreement score, dissent ratio per call |
| Tool descriptions prescriptive not descriptive | HIGH | "Do NOT use for bug fixes" — external users should decide |
| No progress tokens (30-90 sec silent waits) | HIGH | Nov 2025 MCP spec added Tasks for this |
| README is for web app not MCP | HIGH | No MCP installation docs |
| No verification harness | HIGH | Need honest benchmarks showing where deliberation helps vs fails |
| sys.path.insert hacks | MEDIUM | tools.py line 19: sys.path.insert(0, ...) |
| Import path fragility | MEDIUM | mcp_server.py line 55: bare module import breaks from different dirs |
| Model config via individual env vars | MEDIUM | STAGE1_MODEL_A, STAGE1_MODEL_B — should be comma-separated list |
| No .env.example | MEDIUM | Users need to know what env vars exist |
| No CI/CD | MEDIUM | No GitHub Actions workflows for MCP |

## Tool Design (v0.1)

### v0.1: ONE tool (Smallest Valuable Action)

#### Tool 1: council_deliberate
- **Description:** "Run a multi-LLM deliberation. Sends the prompt to multiple AI models independently, has them peer-review each other's responses anonymously, then a chairman model synthesizes the best final answer. Uses ~10-15 API calls via OpenRouter. Takes 30-90 seconds. Returns the final synthesized answer plus individual model responses, peer rankings, and deliberation metrics (time, agreement score, dissent ratio) for transparency."
- **Parameters:**
  - prompt (string, REQUIRED): The question or problem to deliberate on
  - models (array of strings, optional): OpenRouter model IDs for the council. Defaults to diverse frontier models.
  - chairman (string, optional): OpenRouter model ID for the chairman synthesizer.
  - depth (enum: "quick"/"standard"/"thorough", optional): quick=3 models, standard=4 (default), thorough=5+ with adjudication.
- **Returns (new structure with observability):**
  ```json
  {
    "answer": "synthesized final answer",
    "stage1_responses": [{model, content, reasoning}],
    "stage2_rankings": [{judge, rankings, raw_evaluation}],
    "aggregate_rankings": [{model, avg_rank, vote_count}],
    "metrics": {
      "duration_seconds": 47.3,
      "models_used": ["openai/gpt-4o", "anthropic/claude-sonnet-4", "google/gemini-2.0-flash"],
      "agreement_score": 0.82,
      "dissent_ratio": 0.18,
      "cost_estimate_usd": 0.045
    },
    "metadata": {
      "task_type": "architectural_decision",
      "complexity_detected": 0.73,
      "learning_stored": true
    }
  }
  ```

### v0.2: Configuration + Status (deferred)
- council_configure: Set default models, chairman, timeout
- council_status: Show config and API key status

### Tools deliberately NOT included
| Rejected | Reason |
|----------|--------|
| council_stage1_only | Premature optimization. Half-council is confusing. |
| council_rank_responses | Exposing Stage 2 independently creates confusing API surface |
| council_history | MCP servers should be stateless. History belongs to client. |
| council_estimate_cost | Nice-to-have but not MVP. Returns in metrics instead. |
| council_compare_models | Feature creep. Focus on deliberation, not benchmarking. |
| council_set_contracts | Internal concept. External users don't need contract stacks. |

## Self-Learning Feedback Loop Architecture (rUv #5)

### What Gets Stored Per Deliberation
```python
{
  "deliberation_id": "uuid",
  "timestamp": "2026-02-12T14:32:00Z",
  "task_type": "architectural_decision",  # detected via prompt analysis
  "complexity": 0.73,  # 0-1 scale
  "models_used": ["openai/gpt-4o", "anthropic/claude-sonnet-4", "google/gemini-2.0-flash"],
  "chairman": "anthropic/claude-opus-4",
  "duration_seconds": 47.3,
  "agreement_score": 0.82,  # how aligned were the rankings
  "dissent_ratio": 0.18,   # how much disagreement
  "human_feedback": {
    "quality_rating": 4,  # 1-5, optional user feedback
    "helpful": true,
    "notes": "Caught edge case I missed"
  },
  "stage1_quality": {  # model-specific performance
    "openai/gpt-4o": {"rank": 1, "peer_score": 0.92},
    "anthropic/claude-sonnet-4": {"rank": 2, "peer_score": 0.85},
    "google/gemini-2.0-flash": {"rank": 3, "peer_score": 0.71}
  }
}
```

### Learning Mechanisms

**1. Task Type Detection**
- Analyze prompt to classify: factual_qa, architectural_decision, tradeoff_analysis, code_review, creative_synthesis, debugging
- Store correlation: task_type → deliberation_value_added
- Over time: learn when deliberation helps vs single model is better

**2. Model Selection Intelligence**
- Track per-model performance by task type
- Learn which model combinations produce highest agreement + quality
- Suggest optimal council composition based on task detection

**3. Disagreement Pattern Analysis**
- When models disagree significantly (dissent_ratio > 0.5), store the prompt pattern
- Identify task characteristics that cause disagreement
- Use to refine task type detection and model selection

**4. Human Feedback Integration**
- Optional quality_rating parameter in API (1-5 stars)
- Weight deliberations with human feedback higher in learning
- Identify which task types benefit most from deliberation per human validation

### Storage Implementation
```
~/.llm-council/
  deliberations.db  # SQLite for queryable history
  patterns.json     # Learned patterns and model performance
  models.json       # Model-specific statistics
```

### Learning Query API (v0.3)
```python
# Internal functions for model selection
get_optimal_models(task_type: str, complexity: float) -> List[str]
get_deliberation_value(task_type: str) -> float  # 0-1 confidence
get_task_patterns() -> Dict[str, Pattern]

# User-facing analytics (future)
council_analytics(task_type: Optional[str] = None) -> Stats
```

## Verification Harness (rUv #3)

### Honest Benchmarking Strategy

**Phase 1: Internal Verification (Session 2)**
Build test suite with 20 diverse prompts across categories:
- 5 factual QA (expect deliberation to NOT add value, document this)
- 5 architectural decisions (expect deliberation to add value)
- 5 tradeoff analyses (expect deliberation to add value)
- 5 code reviews (unknown, measure)

For each prompt:
- Run single best model (GPT-4o or Claude Opus 4.6)
- Run council deliberation
- Measure: time cost, quality (via human eval), agreement patterns

**Transparency Commitment:**
- Publish results showing where deliberation FAILS
- Document that factual QA performs worse (per DeliberationBench)
- Show cost vs quality tradeoff honestly
- Include in README with clear "When to Use" guidance

**Phase 2: Community Verification (Post-launch)**
- Invite first 100 users to submit feedback per deliberation
- Aggregate quality ratings by task type
- Publish monthly "Deliberation Report Card"
- Adjust defaults based on real usage patterns

### README Verification Section Template
```markdown
## When Deliberation Helps (and When It Doesn't)

### Works Best For:
- Architectural decisions with tradeoffs
- Complex problems requiring multiple perspectives
- Code review and design critique
- Strategic planning and risk assessment

### Works Poorly For:
- Simple factual questions (use single model)
- Calculations and data lookups (use single model)
- Tasks under time pressure (30-90 sec overhead)

### Evidence:
[Link to benchmark results showing win/loss by category]
[Link to DeliberationBench discussion]
[Link to community feedback aggregates]
```

## Pioneer-then-Standardize (rUv #7)

### Define the Deliberation Standard

**Strategy:**
- Ship first, but define the pattern immediately
- Write authoritative blog post: "How MCP-Based Multi-LLM Deliberation Should Work"
- Submit to Anthropic's MCP blog, post on r/claudedev and r/aipromptprogramming
- Position llm-council as the reference implementation

**Blog Post Outline: "The Deliberation Standard"**
1. Why deliberation matters (MoA research, frontier model plateaus)
2. Core protocol: Stage 1 (parallel), Stage 2 (anonymous peer review), Stage 3 (synthesis)
3. Critical design decisions: anonymization, role specialization, aggregate ranking math
4. When deliberation adds value vs noise (verification harness results)
5. MCP integration patterns (progress tokens, observability, cost transparency)
6. Reference implementation: llm-council-mcp
7. Call for competing implementations and standardization discussion

**Community Posts:**
- r/claudedev: "I built an MCP server for multi-LLM deliberation — here's what I learned about when it helps"
- r/aipromptprogramming: "Mixture-of-Agents via MCP: deliberation protocol and reference implementation"
- Agentics Foundation Discord: Demo + feedback session

**Goal:**
- Be cited when others build deliberation systems
- Establish terminology and evaluation standards
- Attract contributors and competing implementations

## Configuration

### Env vars (one required, rest optional)
```
OPENROUTER_API_KEY=sk-or-v1-...          # REQUIRED
COUNCIL_MODELS=openai/gpt-4o,anthropic/claude-sonnet-4,google/gemini-2.0-flash  # optional
CHAIRMAN_MODEL=anthropic/claude-sonnet-4  # optional
COUNCIL_TIMEOUT=120                       # optional
COUNCIL_MAX_TOKENS=4096                   # optional
LEARNING_ENABLED=true                     # optional, enables feedback loop storage
```

Tool parameters override env vars per-call.

### User config block (what they paste into Claude Desktop / Claude Code)
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

## Architecture Decisions

### Standalone package structure
```
llm-council-mcp/
  pyproject.toml          # standalone package with [project.scripts]
  src/
    llm_council_mcp/
      __init__.py
      server.py           # MCP server (stdio + streamable-http)
      council.py          # extracted core logic (from backend/council.py)
      roles.py            # extracted (from backend/roles.py)
      models.py           # default model configs
      learning.py         # feedback loop and pattern storage (NEW)
      metrics.py          # observability (time, agreement, cost) (NEW)
      verification.py     # benchmark harness (NEW)
      __main__.py         # python -m llm_council_mcp
  tests/
    test_council.py
    test_mcp_tools.py
    test_learning.py
    test_verification.py
    benchmarks/
      factual_qa.json
      architectural.json
      tradeoffs.json
      code_review.json
  README.md
  .env.example
  LICENSE
```

### Transport: stdio primary, streamable-http optional later

### Long-running handling:
- Session 1: MCP progress tokens (Stage 1/2/3 progress notifications)
- v0.3: Full MCP Tasks support (call-now, fetch-later pattern)

## Implementation Sessions (Agentic Timeline)

### Session 1: Core Package + Observability (3-4 hours)
**Goal:** Ship minimal working package with metrics

1. Create standalone package structure with pyproject.toml and [project.scripts]
2. Extract and decouple council core from backend/ into standalone package
3. Remove internal constraints (complexity thresholds, JSONL logging, strategic justification)
4. Simplify model config to comma-separated env var
5. Add metrics.py: duration, models_used, agreement_score, dissent_ratio, cost_estimate
6. Update council_deliberate to return metrics in response
7. Add MCP progress tokens for Stage 1/2/3
8. Write .env.example

**Deliverable:** `uvx llm-council-mcp` runs and returns metrics

### Session 2: Self-Learning + Verification (4-5 hours)
**Goal:** Add feedback loop and honest benchmarks

9. Implement learning.py: SQLite storage, task type detection, pattern learning
10. Hook deliberation completion to store learning data
11. Add optional quality_rating parameter to council_deliberate
12. Build verification harness with 20 test prompts
13. Run benchmarks: single model vs deliberation across categories
14. Document results transparently (including failure modes)
15. Write initial README with "When to Use" section

**Deliverable:** System learns from usage, benchmarks show honest performance

### Session 3: Testing + Quality (3-4 hours)
**Goal:** Production-grade reliability

16. Add unit tests (ranking parsing, config loading, role assignment, metrics calculation)
17. Add integration tests with mocked OpenRouter
18. Add MCP protocol compliance test
19. Harden all error paths to structured JSON
20. Add learning.py tests (pattern storage, model selection)
21. Finalize README with architecture diagram, cost guide, troubleshooting

**Deliverable:** >90% test coverage, all edge cases handled

### Session 4: Ship + Community (2-3 hours)
**Goal:** Live on PyPI and community-visible

22. GitHub Actions CI/CD (lint, type check, test on PR; publish on tag)
23. Publish to PyPI (`uv build && uv publish`)
24. Verify `uvx llm-council-mcp` works end-to-end
25. Submit to awesome-mcp-servers list
26. Write "Deliberation Standard" blog post
27. Post to r/claudedev, r/aipromptprogramming, Agentics Foundation Discord

**Deliverable:** v0.1 live, 100 target users notified

### Post-Launch: Iterate Based on Real Usage (ongoing)
**Goal:** Improve based on community feedback and learning data

28. Monitor learning.db for patterns: which task types benefit most
29. Adjust default models based on performance data
30. Add council_configure and council_status tools (v0.2)
31. Implement optimal model selection suggestions
32. Add council_analytics tool showing learned patterns (v0.3)
33. MCP Tasks support (when client adoption is wider)
34. Streamable-HTTP transport (for remote hosting)
35. Monthly "Deliberation Report Card" posts

**Metrics for Success:**
- 100 active users in first month
- 500+ deliberations logged to learning.db
- Quality rating average >3.5/5
- 10+ community feedback posts
- 2+ competing implementations cite our standard

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Deliberation underperforms single model on factual QA | HIGH | Position for complex decisions only. Verification harness shows honest performance. Task type detection learns when to recommend single model. |
| 30-90 sec tool calls cause client timeouts | HIGH | Progress tokens from day 1. depth: "quick" option. Document expected times. Learning shows which tasks justify the time cost. |
| OpenRouter costs surprise users | MEDIUM | Return cost estimate in every response. Document costs in README. Default to balanced models. Track spending in learning.db. |
| Users don't provide feedback, learning stalls | MEDIUM | Make quality_rating optional but easy (1-5 stars). Auto-learn from agreement patterns even without human feedback. |
| Model ID rot (hardcoded IDs become invalid) | MEDIUM | Configurable via env vars. Model validation on startup. Learning.db tracks model performance over time. |
| MCP SDK version incompatibility | LOW | Pin to mcp>=1.0.0,<2.0.0. Track v2 development. |
| Community rejects our standard | LOW | Ship first advantage. Back with verification data. Invite competing implementations. |

## Practical Metrics (rUv #9)

### Returned in Every Deliberation
```json
{
  "metrics": {
    "duration_seconds": 47.3,
    "models_used": ["openai/gpt-4o", "anthropic/claude-sonnet-4", "google/gemini-2.0-flash"],
    "agreement_score": 0.82,  // 0-1, higher = more consensus
    "dissent_ratio": 0.18,    // 0-1, higher = more disagreement
    "cost_estimate_usd": 0.045
  }
}
```

### Aggregate Analytics (v0.3)
- Deliberations by task type
- Average quality rating by task type
- Cost per task type
- Model performance rankings
- When deliberation adds value vs single model

### Community Transparency
- Monthly published report cards
- Aggregate stats (anonymized)
- Win/loss by category
- Cost analysis

## Community Launch Strategy (rUv #10)

### Target: 100 Early Users

**Launch Channels:**
1. **r/claudedev** — Post: "I built an MCP server for multi-LLM deliberation (inspired by Karpathy's llm-council)" + demo video
2. **r/aipromptprogramming** — Post: "Implementing Mixture-of-Agents via MCP" + verification results
3. **Agentics Foundation Discord** — Live demo session + feedback gathering
4. **Anthropic MCP showcase** — Submit for official blog feature
5. **awesome-mcp-servers** — Add to "LLM Evaluation" category (create if needed)

**Content Strategy:**
- Honest about limitations (DeliberationBench results)
- Show verification harness results
- Demo with real architectural decision use case
- Emphasize self-learning and observability
- Request feedback explicitly

**Engagement Plan:**
- Respond to all feedback within 24 hours
- Fix bugs reported by early users immediately
- Weekly updates on learning patterns discovered
- Monthly report card showing aggregate performance

**Monetization (Tool-Free, Expertise-Paid):**
- Open source MCP server (free)
- Consulting on deliberation architecture for enterprises
- Custom model selection for specific domains
- Training workshops on effective deliberation design

## Key Files Reference

| File | Path | Action |
|------|------|--------|
| Core council logic | backend/council.py | Extract and decouple |
| Current MCP server | mcp_server/mcp_server.py | Redesign |
| Current MCP tools | mcp_server/tools.py | Simplify to 1 tool |
| Role definitions | backend/roles.py | Extract as-is |
| Contract system | backend/contracts.py | Keep optional |
| Config system | backend/config.py | Simplify for standalone |
| Learning system | NEW: learning.py | Build from scratch |
| Metrics system | NEW: metrics.py | Build from scratch |
| Verification harness | NEW: verification.py | Build from scratch |
| Project config | pyproject.toml | Rewrite for standalone package |

## Removed Perfectionism (rUv #1 + #4)

### Deleted from Original Plan
- 42-item quality checklist replaced with ship-measure-iterate
- "4.9/5 quality standard" replaced with real user feedback
- Multi-week timelines replaced with agentic sessions
- Feature creep (council_configure, council_status deferred to v0.2)
- CI/CD moved to Session 3 (not blocking v0.1)

### Replaced With
- 4 agentic sessions to v0.1 (12-16 hours total)
- ONE tool, ONE env var for v0.1
- Quality through iteration and learning, not pre-launch checklists
- Metrics that matter: problems solved, quality ratings, usage patterns
- Honest verification showing failures, not marketing claims

## Sources
- DeliberationBench: arxiv.org/abs/2601.08835
- Mixture-of-Agents (ICLR 2025): arxiv.org/html/2406.04692v1
- MCP Spec (2025-11-25): modelcontextprotocol.io/specification/2025-11-25
- MCP Design Patterns: klavis.ai/blog/less-is-more-mcp-design-patterns-for-ai-agents
- MCP Best Practices: mcpcat.io/blog/mcp-server-best-practices/
- Long-Running MCP Tasks: agnost.ai/blog/long-running-tasks-mcp/
- awesome-mcp-servers: github.com/punkpeye/awesome-mcp-servers
- Reuven Cohen's Decision Framework (rUv): Applied across 10 dimensions

---

**Research conducted:** 2026-02-12
**Status:** Approved for implementation with rUv framework applied
**Next action:** Execute Session 1 (3-4 hours to working package with metrics)
