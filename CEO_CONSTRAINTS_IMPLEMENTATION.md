# CEO Constraints Implementation for LLM Council MCP

**Date:** 2026-02-05
**Status:** ✅ Implemented and Verified

## CEO Guidance (Verbatim)
> "llm council mcp should be used for Opus level decisions and something more strategic, not frequent requests"

## Changes Implemented

### 1. Updated Tool Description (`mcp_server/mcp_server.py`)

**Before:**
- Basic description of 3-stage process
- No cost warnings
- No usage guidelines

**After:**
```
⚠️ HIGH COST - Calls multiple LLMs (10+ API calls per deliberation).
Use ONLY for strategic decisions.

COMPLEXITY THRESHOLD: Only use when complexity ≥70% OR for:
- Architecture decisions affecting multiple systems
- Security review of authentication flows
- Major technology stack changes
- Strategic direction questions

❌ NOT FOR:
- Bug fixes
- Routine code reviews
- Quick lookups
- Frequent/repeated queries
```

### 2. Added Complexity Check (`mcp_server/tools.py`)

**New Parameters:**
- `complexity_score` (optional integer, 0-100): Must be ≥70 to proceed
- `strategic_justification` (required string): Explanation of why council is needed

**Validation Logic:**
```python
threshold = 70  # configurable via _config['complexity_threshold']

if complexity_score < threshold:
    return {
        "success": False,
        "blocked": True,
        "message": "⚠️ Council deliberation blocked: complexity score below threshold"
    }

if not strategic_justification:
    return {
        "success": False,
        "blocked": True,
        "message": "⚠️ Council deliberation requires strategic_justification"
    }
```

### 3. Updated Input Schema

**New Fields:**
- `complexity_score` (integer, 0-100, optional)
  - Guidance: 70-79 for complex technical, 80-89 for strategic, 90-100 for critical
- `strategic_justification` (string, required)
  - Must explain WHY full council is needed

### 4. Usage Logging

**Log Location:** `~/.claude/enclaude/llm-council-usage.jsonl`

**Log Entry Format:**
```json
{
  "timestamp": "2026-02-05T12:34:56Z",
  "prompt_summary": "First 100 chars of prompt...",
  "complexity_score": 85,
  "strategic_justification": "Multi-system architecture decision",
  "models_used": ["anthropic/claude-3.5-sonnet", "..."],
  "num_api_calls": 12,
  "cost_estimate": "$0.60-$1.80",
  "success": true,
  "error": null
}
```

**Logging Triggers:**
- Every deliberation attempt (blocked or successful)
- Timeouts
- Errors

### 5. Cost Estimation

Added cost estimates based on number of models:
- Formula: `$0.05-$0.15` per model call
- Typical full deliberation: 10-12 calls = `$0.50-$1.80`

## Verification

### Syntax Check
```bash
python3 -m py_compile mcp_server/mcp_server.py mcp_server/tools.py
# ✓ No errors
```

### Validation Tests
```bash
python3 test_constraints_simple.py
# ✓ All tests passed
```

**Test Results:**
- ✓ Complexity threshold enforcement (≥70)
- ✓ Strategic justification requirement
- ✓ Boundary cases (score=70 allowed, score=69 blocked)
- ✓ Missing justification blocked

### Tool Description Verification
Confirmed tool description now includes:
- ⚠️ HIGH COST warning (prominent, first line)
- Complexity guidance (≥70%)
- Good use cases (architecture, security, strategic)
- Bad use cases (bugs, reviews, lookups)
- Anti-pattern warning (NOT for frequent requests)

## Usage Examples

### ✅ GOOD - Strategic Architecture Decision
```json
{
  "prompt": "Should we migrate from REST to GraphQL for our API gateway?",
  "complexity_score": 85,
  "strategic_justification": "Multi-system architecture change affecting 15 microservices, client apps, and authentication flows"
}
```

### ✅ GOOD - Security Review
```json
{
  "prompt": "Evaluate our OAuth2 implementation for security vulnerabilities",
  "complexity_score": 90,
  "strategic_justification": "Critical security review of authentication system protecting user data"
}
```

### ❌ BAD - Bug Fix (Blocked)
```json
{
  "prompt": "Why is this function returning undefined?",
  "complexity_score": 40,
  "strategic_justification": "Need to debug a function"
}
// Response: "⚠️ Council deliberation blocked: complexity score 40 is below threshold of 70"
```

### ❌ BAD - Missing Justification (Blocked)
```json
{
  "prompt": "What's the best database for our project?",
  "complexity_score": 80,
  "strategic_justification": ""
}
// Response: "⚠️ Council deliberation requires strategic_justification"
```

## Configuration

The complexity threshold can be adjusted via `_config` in `tools.py`:

```python
_config = {
    "complexity_threshold": 70,  # Minimum score to proceed
    # ... other config
}
```

## Files Modified

1. `/Users/haza/Projects/llm-council/mcp_server/mcp_server.py`
   - Updated `llm_council_deliberate` tool description
   - Updated input schema with new parameters
   - Updated tool call handler to pass new parameters

2. `/Users/haza/Projects/llm-council/mcp_server/tools.py`
   - Added `datetime` and `Path` imports for logging
   - Added `complexity_threshold` to `_config`
   - Added `_log_council_usage()` function
   - Updated `deliberate()` signature with new parameters
   - Added complexity validation logic
   - Added justification validation logic
   - Added logging to all code paths (success, timeout, error, blocked)

## Files Created

1. `/Users/haza/Projects/llm-council/test_constraints_simple.py`
   - Validation tests for constraint logic

2. `/Users/haza/Projects/llm-council/CEO_CONSTRAINTS_IMPLEMENTATION.md`
   - This documentation file

## Rollback Instructions

If these constraints need to be removed:

```bash
cd /Users/haza/Projects/llm-council
git diff mcp_server/mcp_server.py mcp_server/tools.py
git checkout mcp_server/mcp_server.py mcp_server/tools.py
```

## Monitoring

To monitor council usage:

```bash
# View all council invocations
cat ~/.claude/enclaude/llm-council-usage.jsonl | jq .

# Count blocked requests
grep '"blocked": true' ~/.claude/enclaude/llm-council-usage.jsonl | wc -l

# Sum estimated costs
cat ~/.claude/enclaude/llm-council-usage.jsonl | jq '.cost_estimate'

# View recent usage
tail -5 ~/.claude/enclaude/llm-council-usage.jsonl | jq .
```

## Next Steps

1. **Deployment:** Update MCP server configuration in `~/.claude.json` or Claude Code settings
2. **Testing:** Try a real deliberation with valid parameters to confirm end-to-end flow
3. **Monitoring:** Check `~/.claude/enclaude/llm-council-usage.jsonl` after first use
4. **Tuning:** Adjust threshold (70) if needed based on actual usage patterns

## Success Criteria

- [x] Prominent HIGH COST warning in tool description
- [x] Complexity threshold enforcement (≥70)
- [x] Strategic justification required
- [x] Good/bad use case examples in description
- [x] Usage logging to `~/.claude/enclaude/llm-council-usage.jsonl`
- [x] Cost estimation included in logs
- [x] Syntax validation passes
- [x] Constraint tests pass
- [x] Blocking behavior verified

**Status: All requirements met and verified.**
