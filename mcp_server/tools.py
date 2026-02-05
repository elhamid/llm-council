"""
MCP Tool definitions for LLM Council.

Tools:
- llm_council_deliberate: Run full 3-stage deliberation
- llm_council_configure: Set council/chairman models
- llm_council_status: Get current configuration
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent directory to path for backend imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.council import (
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_select_winner,
    calculate_aggregate_rankings,
    DEFAULT_STAGE1_MODELS,
    DEFAULT_STAGE2_MODELS,
    CHAIRMAN_MODEL,
    STAGE1_LAST_ERRORS,
    STAGE2_LAST_ERRORS,
)

# Runtime configuration (can be overridden via configure tool)
_config: Dict[str, Any] = {
    "stage1_models": list(DEFAULT_STAGE1_MODELS),
    "stage2_models": list(DEFAULT_STAGE2_MODELS),
    "chairman_model": CHAIRMAN_MODEL,
    "contract_stack": os.getenv("COUNCIL_CONTRACTS", "factory_truth_v1"),
    "timeout_seconds": 300,
    "complexity_threshold": 70,  # Minimum complexity score to run full deliberation
}


def _log_council_usage(
    prompt: str,
    complexity_score: Optional[int],
    strategic_justification: str,
    models_used: List[str],
    success: bool,
    error: Optional[str] = None,
) -> None:
    """Log council invocations to ~/.claude/enclaude/llm-council-usage.jsonl"""
    log_dir = Path.home() / ".claude" / "enclaude"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "llm-council-usage.jsonl"

    # Estimate cost: ~$0.10-0.50 per full deliberation depending on models
    cost_estimate = f"${len(models_used) * 0.05:.2f}-${len(models_used) * 0.15:.2f}"

    log_entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "prompt_summary": prompt[:100] + "..." if len(prompt) > 100 else prompt,
        "complexity_score": complexity_score,
        "strategic_justification": strategic_justification,
        "models_used": models_used,
        "num_api_calls": len(models_used),
        "cost_estimate": cost_estimate,
        "success": success,
        "error": error,
    }

    with open(log_file, "a") as f:
        f.write(json.dumps(log_entry) + "\n")


def get_status() -> Dict[str, Any]:
    """Get current configuration and status."""
    return {
        "stage1_models": _config["stage1_models"],
        "stage2_models": _config["stage2_models"],
        "chairman_model": _config["chairman_model"],
        "contract_stack": _config["contract_stack"],
        "timeout_seconds": _config["timeout_seconds"],
        "api_configured": bool(
            os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        ),
        "last_stage1_errors": dict(STAGE1_LAST_ERRORS),
        "last_stage2_errors": dict(STAGE2_LAST_ERRORS),
    }


def configure(
    stage1_models: Optional[List[str]] = None,
    stage2_models: Optional[List[str]] = None,
    chairman_model: Optional[str] = None,
    contract_stack: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Configure the LLM Council parameters.

    Args:
        stage1_models: List of model identifiers for Stage 1 (response generation)
        stage2_models: List of model identifiers for Stage 2 (peer evaluation)
        chairman_model: Model identifier for Stage 3 (final synthesis)
        contract_stack: Comma-separated contract names (e.g., "factory_truth_v1")
        timeout_seconds: Timeout for deliberation (default: 300)

    Returns:
        Updated configuration dict
    """
    if stage1_models is not None:
        _config["stage1_models"] = stage1_models
        # Also update the module-level defaults for the council
        os.environ["STAGE1_MODEL_A"] = stage1_models[0] if len(stage1_models) > 0 else ""
        os.environ["STAGE1_MODEL_B"] = stage1_models[1] if len(stage1_models) > 1 else ""
        os.environ["STAGE1_MODEL_C"] = stage1_models[2] if len(stage1_models) > 2 else ""
        os.environ["STAGE1_MODEL_D"] = stage1_models[3] if len(stage1_models) > 3 else ""

    if stage2_models is not None:
        _config["stage2_models"] = stage2_models
        os.environ["STAGE2_MODEL_A"] = stage2_models[0] if len(stage2_models) > 0 else ""
        os.environ["STAGE2_MODEL_B"] = stage2_models[1] if len(stage2_models) > 1 else ""
        os.environ["STAGE2_MODEL_C"] = stage2_models[2] if len(stage2_models) > 2 else ""
        os.environ["STAGE2_MODEL_D"] = stage2_models[3] if len(stage2_models) > 3 else ""

    if chairman_model is not None:
        _config["chairman_model"] = chairman_model
        os.environ["CHAIRMAN_MODEL"] = chairman_model

    if contract_stack is not None:
        _config["contract_stack"] = contract_stack
        os.environ["COUNCIL_CONTRACTS"] = contract_stack

    if timeout_seconds is not None:
        _config["timeout_seconds"] = timeout_seconds

    return get_status()


async def deliberate(
    prompt: str,
    complexity_score: Optional[int] = None,
    strategic_justification: str = "",
    contract_stack: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run a full 3-stage LLM Council deliberation.

    ⚠️ HIGH COST: Uses 10+ LLM API calls. Only use for strategic decisions.

    Stage 1: Multiple models generate independent responses
    Stage 2: Models peer-review and rank anonymized responses
    Stage 3: Chairman synthesizes the final answer

    Args:
        prompt: The user's question or request
        complexity_score: Complexity rating 0-100 (must be ≥70)
        strategic_justification: Explanation of why full council is needed
        contract_stack: Optional override for contract stack

    Returns:
        Dict with:
        - stage1_results: Individual model responses
        - stage2_results: Peer evaluations and rankings
        - stage3_result: Final synthesized answer
        - aggregate_rankings: Model performance summary
        - label_to_model: Mapping of anonymous labels to models
    """
    # Validate complexity threshold
    threshold = _config.get("complexity_threshold", 70)
    if complexity_score is not None and complexity_score < threshold:
        warning_result = {
            "success": False,
            "blocked": True,
            "reason": "complexity_below_threshold",
            "message": (
                f"⚠️ Council deliberation blocked: complexity score {complexity_score} is below "
                f"threshold of {threshold}.\n\n"
                "LLM Council is HIGH COST (10+ API calls) and should ONLY be used for:\n"
                "- Architecture decisions affecting multiple systems (70-79)\n"
                "- Security reviews of authentication flows (80-89)\n"
                "- Major technology stack changes (80-89)\n"
                "- Critical strategic direction questions (90-100)\n\n"
                "For routine tasks, use a single high-quality model instead."
            ),
            "complexity_score": complexity_score,
            "threshold": threshold,
        }
        _log_council_usage(
            prompt=prompt,
            complexity_score=complexity_score,
            strategic_justification=strategic_justification,
            models_used=[],
            success=False,
            error="blocked_below_complexity_threshold",
        )
        return warning_result

    if not strategic_justification:
        warning_result = {
            "success": False,
            "blocked": True,
            "reason": "missing_justification",
            "message": (
                "⚠️ Council deliberation requires strategic_justification.\n\n"
                "Please explain why this question needs full council review. Examples:\n"
                "- 'Multi-system architecture decision with security implications'\n"
                "- 'Critical authentication flow redesign requiring consensus'\n"
                "- 'Technology stack evaluation for 5-year roadmap'"
            ),
        }
        return warning_result

    contracts = contract_stack or _config.get("contract_stack")
    timeout = _config.get("timeout_seconds", 300)

    # Collect all models that will be used for logging
    all_models = list(set(
        _config["stage1_models"] +
        _config["stage2_models"] +
        [_config["chairman_model"]]
    ))

    try:
        # Stage 1: Collect responses from council models
        stage1_results = await asyncio.wait_for(
            stage1_collect_responses(prompt, contract_stack=contracts),
            timeout=timeout / 3,  # Allocate ~1/3 timeout per stage
        )

        # Stage 2: Peer evaluation and ranking
        stage2_results, label_to_model = await asyncio.wait_for(
            stage2_collect_rankings(prompt, stage1_results, contract_stack=contracts),
            timeout=timeout / 3,
        )

        # Calculate aggregate rankings
        contract_evals = {r.get("model"): r.get("contract_eval") for r in stage1_results}
        aggregate_rankings = calculate_aggregate_rankings(
            stage2_results, label_to_model, contract_evals
        )

        # Stage 3: Chairman synthesizes final answer
        stage3_result = await asyncio.wait_for(
            stage3_select_winner(
                user_prompt=prompt,
                stage1_results=stage1_results,
                stage2_results=(stage2_results, label_to_model),
                contract_stack=contracts,
            ),
            timeout=timeout / 3,
        )

        result = {
            "success": True,
            "prompt": prompt,
            "complexity_score": complexity_score,
            "strategic_justification": strategic_justification,
            "final_answer": stage3_result.get("response", ""),
            "chairman_model": stage3_result.get("model", ""),
            "aggregate_rankings": aggregate_rankings,
            "label_to_model": label_to_model,
            "stage1_results": [
                {
                    "model": r.get("model"),
                    "response": r.get("response"),
                    "contract_status": (r.get("contract_eval") or {}).get("status"),
                }
                for r in stage1_results
            ],
            "stage2_summary": [
                {
                    "model": r.get("model"),
                    "parsed_ranking": r.get("parsed_ranking"),
                    "partial": r.get("partial", False),
                }
                for r in stage2_results
            ],
        }

        # Log successful usage
        _log_council_usage(
            prompt=prompt,
            complexity_score=complexity_score,
            strategic_justification=strategic_justification,
            models_used=all_models,
            success=True,
        )

        return result

    except asyncio.TimeoutError:
        error_result = {
            "success": False,
            "error": "Deliberation timed out",
            "timeout_seconds": timeout,
        }
        _log_council_usage(
            prompt=prompt,
            complexity_score=complexity_score,
            strategic_justification=strategic_justification,
            models_used=all_models,
            success=False,
            error="timeout",
        )
        return error_result

    except Exception as e:
        error_result = {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
        }
        _log_council_usage(
            prompt=prompt,
            complexity_score=complexity_score,
            strategic_justification=strategic_justification,
            models_used=all_models,
            success=False,
            error=f"{type(e).__name__}: {str(e)}",
        )
        return error_result
