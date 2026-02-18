from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import Context, FastMCP

from .council import run_deliberation
from .learning import LearningStore, detect_task_type
from .metrics import calculate_metrics
from .models import CouncilConfig, api_configured, get_config, update_config

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("llm-council-mcp")

mcp = FastMCP("llm-deliberate", instructions="Multi-LLM deliberation with anonymized peer review.")

try:
    _learning = LearningStore()
except Exception:
    fallback_db = Path("/tmp/llm-council-deliberations.db")
    _learning = LearningStore(db_path=fallback_db)


@mcp.tool(description="Run multi-LLM deliberation with anonymized peer review and chairman synthesis.")
async def council_deliberate(
    prompt: str,
    models: Optional[list[str]] = None,
    chairman: Optional[str] = None,
    depth: Optional[str] = "standard",
    contract_stack: Optional[str] = None,
    ctx: Context | None = None,
) -> str:
    try:
        if not prompt or not prompt.strip():
            return json.dumps({"error": "prompt is required", "error_type": "ValidationError"}, ensure_ascii=False, indent=2)

        depth = (depth or "standard").strip().lower()
        if depth not in {"quick", "standard", "thorough"}:
            return json.dumps(
                {"error": "depth must be one of: quick, standard, thorough", "error_type": "ValidationError"},
                ensure_ascii=False,
                indent=2,
            )

        cfg = get_config()
        stage1_models = list(models or cfg.stage1_models)
        stage2_models = list(models or cfg.stage2_models)
        chairman_model = (chairman or cfg.chairman_model).strip()

        if ctx:
            await ctx.info(f"Starting deliberation with depth='{depth}'...")

        started = time.time()
        result = await run_deliberation(
            prompt=prompt,
            stage1_models=stage1_models,
            stage2_models=stage2_models,
            chairman_model=chairman_model,
            contract_stack=contract_stack,
            depth=depth,
            timeout=cfg.timeout_seconds,
            progress_cb=(ctx.report_progress if ctx else None),
            info_cb=(ctx.info if ctx else None),
        )

        if result.get("error"):
            return json.dumps(result, ensure_ascii=False, indent=2)

        metrics = calculate_metrics(
            duration_seconds=(time.time() - started),
            stage1_responses=result.get("stage1_responses", []),
            stage2_rankings=result.get("stage2_rankings", []),
            aggregate_rankings=result.get("aggregate_rankings", []),
        )

        task_type = detect_task_type(prompt)
        deliberation_id = _learning.store_deliberation(
            prompt=prompt,
            task_type=task_type,
            models=metrics["models_used"],
            chairman=chairman_model,
            duration_seconds=metrics["duration_seconds"],
            agreement_score=metrics["agreement_score"],
            dissent_ratio=metrics["dissent_ratio"],
            cost_estimate_usd=metrics["cost_estimate_usd"],
            aggregate_rankings=result.get("aggregate_rankings", []),
            depth=depth,
        )

        payload = {
            "answer": result.get("answer", ""),
            "stage1_responses": [
                {
                    "model": r.get("model"),
                    "content": r.get("response", ""),
                    "role": r.get("role") or "",
                }
                for r in result.get("stage1_responses", [])
            ],
            "stage2_rankings": [
                {
                    "judge": r.get("model"),
                    "rankings": r.get("parsed_ranking", []),
                    "raw_evaluation": r.get("ranking", ""),
                }
                for r in result.get("stage2_rankings", [])
            ],
            "aggregate_rankings": [
                {
                    "model": r.get("model"),
                    "average_rank": r.get("average_rank"),
                    "vote_count": r.get("rankings_count", 0),
                }
                for r in result.get("aggregate_rankings", [])
            ],
            "metrics": metrics,
            "metadata": {
                "task_type": task_type,
                "depth": depth,
                "chairman_model": chairman_model,
                "label_to_model": result.get("label_to_model", {}),
                "learning_stored": bool(deliberation_id),
                "deliberation_id": deliberation_id,
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        if ctx:
            await ctx.warning(f"council_deliberate failed: {type(e).__name__}: {e}")
        return json.dumps({"error": str(e), "error_type": type(e).__name__}, ensure_ascii=False, indent=2)


@mcp.tool(description="Show server readiness, active model configuration, and learning store health.")
async def council_status() -> str:
    try:
        cfg = get_config()
        data = {
            "status": "ready",
            "api_configured": api_configured(),
            "config": {
                "stage1_models": cfg.stage1_models,
                "stage2_models": cfg.stage2_models,
                "chairman_model": cfg.chairman_model,
                "timeout_seconds": cfg.timeout_seconds,
            },
            "learning": {
                "db_path": str(Path(_learning.db_path)),
                "total_deliberations": _learning.get_total_deliberations(),
                "task_types_seen": _learning.get_task_types_seen(),
            },
        }
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "error_type": type(e).__name__}, ensure_ascii=False, indent=2)


@mcp.tool(description="Update runtime model/timeouts used by council_deliberate.")
async def council_configure(
    stage1_models: Optional[list[str]] = None,
    stage2_models: Optional[list[str]] = None,
    chairman_model: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> str:
    try:
        cfg = update_config(
            stage1_models=stage1_models,
            stage2_models=stage2_models,
            chairman_model=chairman_model,
            timeout_seconds=timeout_seconds,
        )
        return json.dumps(
            {
                "status": "updated",
                "config": {
                    "stage1_models": cfg.stage1_models,
                    "stage2_models": cfg.stage2_models,
                    "chairman_model": cfg.chairman_model,
                    "timeout_seconds": cfg.timeout_seconds,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return json.dumps({"error": str(e), "error_type": type(e).__name__}, ensure_ascii=False, indent=2)


@mcp.tool(description="Attach a 1-5 quality rating to a prior deliberation for learning.")
async def council_feedback(deliberation_id: str, quality_rating: int) -> str:
    try:
        if not deliberation_id or not deliberation_id.strip():
            return json.dumps({"error": "deliberation_id is required", "error_type": "ValidationError"}, ensure_ascii=False, indent=2)
        if quality_rating < 1 or quality_rating > 5:
            return json.dumps({"error": "quality_rating must be 1-5", "error_type": "ValidationError"}, ensure_ascii=False, indent=2)

        ok = _learning.store_feedback(deliberation_id.strip(), quality_rating)
        if not ok:
            return json.dumps(
                {"error": "deliberation_id not found", "error_type": "NotFoundError", "updated": False},
                ensure_ascii=False,
                indent=2,
            )
        return json.dumps({"status": "updated", "updated": True}, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "error_type": type(e).__name__}, ensure_ascii=False, indent=2)


def main() -> None:
    """Entry point for the console script."""
    mcp.run(transport="stdio")
