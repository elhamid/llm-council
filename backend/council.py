import asyncio
import json
import os
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

from .roles import get_role_spec
from .contracts import (
    build_contract_system_messages,
    build_chairman_contract_system_messages,
)

from openai import AsyncOpenAI

# -----------------------------------------------------------------------------
# LLM Council core:
#   Stage 1: multiple models respond
#   Stage 2: multiple models rank peer responses
#   Stage 3: chairman model synthesizes final answer
# -----------------------------------------------------------------------------

# Default council models (can also be overridden in config.py if you prefer)
COUNCIL_MODELS = [
    "openai/gpt-5.2",
    "google/gemini-3-pro-preview",
    "anthropic/claude-sonnet-4.5",
    "x-ai/grok-4.1-fast",
]

CHAIRMAN_MODEL = "anthropic/claude-opus-4.5"


def _client() -> AsyncOpenAI:
    """
    Creates an OpenAI-compatible async client.

    This repo is intended to run everything through **OpenRouter** (one key for all models).
    To make that robust (and avoid accidentally sending an OpenRouter key to OpenAI),
    we default the base_url to OpenRouter whenever OPENROUTER_API_KEY is present.

    Supported env vars:
      - OPENROUTER_API_KEY  (preferred; used for all requests)
      - OPENAI_BASE_URL     (optional override; defaults to https://openrouter.ai/api/v1 when using OpenRouter)
      - OPENROUTER_BASE_URL (optional; alternative name)
      - OPENAI_API_KEY      (only used if OPENROUTER_API_KEY is not set)
    """
    openrouter_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    api_key = openrouter_key or openai_key

    # Base URL: if user didn't set one explicitly and we're using OpenRouter, force OpenRouter base.
    base_url = (os.getenv("OPENAI_BASE_URL") or os.getenv("OPENROUTER_BASE_URL") or "").strip()
    if not base_url and openrouter_key:
        base_url = "https://openrouter.ai/api/v1"

    if not api_key:
        raise RuntimeError(
            "Missing API key. Set OPENROUTER_API_KEY in your .env (recommended) "
            "or set OPENAI_API_KEY if you intend to use OpenAI directly."
        )

    if base_url:
        return AsyncOpenAI(api_key=api_key, base_url=base_url)
    return AsyncOpenAI(api_key=api_key)


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _label_for_index(i: int) -> str:
    # Response A, B, C...
    return f"Response {chr(ord('A') + i)}"


def _parse_ranking_order(text: str) -> List[str]:
    """
    Attempts to parse a final ranking list from a rater model.
    Returns an ordered list like ["Response A","Response C",...]
    """
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]

    # Look for a line containing "FINAL RANKING" then parse subsequent lines
    start_idx = None
    for i, line in enumerate(lines):
        if "FINAL RANK" in line.upper():
            start_idx = i
            break

    candidates: List[str] = []

    if start_idx is not None:
        tail = lines[start_idx + 1 :]
    else:
        # fallback: just scan all lines
        tail = lines

    for line in tail:
        if line.strip().startswith("---"):
            continue

        if "RESPONSE" in line.upper():
            for lab in ["Response A", "Response B", "Response C", "Response D", "Response E", "Response F"]:
                if lab.upper() in line.upper():
                    candidates.append(lab)
                    break

    # De-dup preserving order
    out: List[str] = []
    for c in candidates:
        if c not in out:
            out.append(c)

    return out


async def _chat(model: str, messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
    client = _client()
    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Aggregates rankings across Stage 2 voters into an average rank per model.
    Lower average_rank is better.
    """
    rank_sums: Dict[str, float] = {}
    rank_counts: Dict[str, int] = {}

    for r in stage2_results:
        parsed = r.get("parsed_ranking") or []
        for idx, label in enumerate(parsed):
            model = label_to_model.get(label)
            if not model:
                continue
            rank = float(idx + 1)  # 1-based
            rank_sums[model] = rank_sums.get(model, 0.0) + rank
            rank_counts[model] = rank_counts.get(model, 0) + 1

    aggregates: List[Dict[str, Any]] = []
    for model, s in rank_sums.items():
        c = rank_counts.get(model, 0) or 1
        aggregates.append(
            {
                "model": model,
                "average_rank": s / c,
                "rankings_count": c,
            }
        )

    aggregates.sort(key=lambda x: x["average_rank"])
    return aggregates


def _member_messages(model_id: str, user_content: str, contract_stack: Optional[str]) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = []
    msgs += build_contract_system_messages(contract_stack)
    msgs.append({"role": "system", "content": get_role_spec(model_id).system_prompt})
    msgs.append({"role": "user", "content": user_content})
    return msgs


def _chairman_messages(chairman_model: str, user_content: str, contract_stack: Optional[str]) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = []
    msgs += build_chairman_contract_system_messages(contract_stack)
    msgs.append({"role": "system", "content": get_role_spec(chairman_model).system_prompt})
    msgs.append({"role": "user", "content": user_content})
    return msgs


async def stage1_collect_responses(
    user_prompt: str,
    models: Optional[List[str]] = None,
    contract_stack: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    models = models or COUNCIL_MODELS

    async def run_one(m: str) -> Optional[Dict[str, Any]]:
        try:
            out = await _chat(m, _member_messages(m, user_prompt, contract_stack), temperature=0.2)
            out = (out or "").strip()
            if not out:
                return None
            return {"model": m, "response": out}
        except Exception:
            return None

    tasks = [run_one(m) for m in models]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r]


async def stage2_collect_rankings(
    user_prompt: str,
    stage1_results: List[Dict[str, Any]],
    models: Optional[List[str]] = None,
    contract_stack: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """
    Ask each model to rank the anonymized Stage 1 responses.
    Returns:
      - stage2_results: list of {model, ranking, parsed_ranking}
      - label_to_model: mapping "Response A" -> actual model id
    """
    models = models or COUNCIL_MODELS

    label_to_model: Dict[str, str] = {}
    labeled_blocks: List[str] = []
    for i, r in enumerate(stage1_results):
        label = _label_for_index(i)
        label_to_model[label] = r["model"]
        labeled_blocks.append(f"{label}:\n{r['response']}")

    rubric = (
        "You are reviewing multiple anonymous answers from different models.\n"
        "Rank them from best to worst on accuracy, insight, and usefulness.\n"
        "Return a short critique of each, then a FINAL RANKING list.\n"
        "Use the labels exactly (Response A, Response B, ...).\n"
    )

    stage2_prompt = (
        f"USER PROMPT:\n{user_prompt}\n\n"
        f"{rubric}\n\n"
        f"ANONYMIZED RESPONSES:\n\n" + "\n\n".join(labeled_blocks)
    )

    async def run_one(m: str) -> Optional[Dict[str, Any]]:
        try:
            out = await _chat(m, _member_messages(m, stage2_prompt, contract_stack), temperature=0.2)
            out = (out or "").strip()
            if not out:
                return None
            parsed = _parse_ranking_order(out)
            return {"model": m, "ranking": out, "parsed_ranking": parsed}
        except Exception:
            return None

    tasks = [run_one(m) for m in models]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r], label_to_model


async def stage3_synthesize_final(
    user_prompt: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str],
    aggregate_rankings: List[Dict[str, Any]],
    chairman_model: Optional[str] = None,
    contract_stack: Optional[Any] = None,
) -> Dict[str, Any]:
    chairman_model = chairman_model or CHAIRMAN_MODEL

    # Prepare compact grounding blocks for the Chairman
    s1 = [{"model": r["model"], "response": r["response"]} for r in stage1_results]
    s2 = [{"model": r["model"], "parsed_ranking": r.get("parsed_ranking", [])} for r in stage2_results]

    chairman_prompt = (
        "You are the Chairman of an LLM Council.\n"
        "You will produce the best final answer to the user.\n\n"
        "Grounding data:\n"
        f"- label_to_model: {json.dumps(label_to_model, ensure_ascii=False)}\n"
        f"- aggregate_rankings: {json.dumps(aggregate_rankings, ensure_ascii=False)}\n\n"
        "You have:\n"
        "- Stage 1: initial answers from each model\n"
        "- Stage 2: peer rankings\n\n"
        "Write a single final response to the user.\n"
        "Do not mention internal stages unless explicitly asked.\n\n"
        f"USER PROMPT:\n{user_prompt}\n\n"
        f"STAGE 1 OUTPUTS:\n{json.dumps(s1, ensure_ascii=False)}\n\n"
        f"STAGE 2 OUTPUTS:\n{json.dumps(s2, ensure_ascii=False)}\n"
    )

    try:
        out = await _chat(
    chairman_model,
    _chairman_messages(chairman_model, chairman_prompt, contract_stack),
    temperature=0.2,
)

        out = (out or "").strip()
        return {"model": chairman_model, "response": out}
    except Exception as e:
        return {"model": chairman_model, "response": "", "error": str(e)}


async def run_council(
    user_prompt: str,
    contract_stack: Optional[Any] = None,
) -> Dict[str, Any]:
    stage1_results = await stage1_collect_responses(user_prompt, contract_stack=contract_stack)

    if not stage1_results:
        return {
            "stage1": [],
            "stage2": [],
            "stage3": {
                "model": CHAIRMAN_MODEL,
                "response": "",
                "error": "All models failed to respond in Stage 1",
            },
            "meta": {
                "contract_stack": contract_stack,
                "label_to_model": {},
                "aggregate_rankings": [],
            },
            "timestamp": _now_iso(),
        }

    stage2_results, label_to_model = await stage2_collect_rankings(
        user_prompt, stage1_results, contract_stack=contract_stack
    )
    aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)

    stage3_result = await stage3_synthesize_final(
        user_prompt,
        stage1_results,
        stage2_results,
        label_to_model,
        aggregate_rankings,
        contract_stack=contract_stack,
    )

    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "meta": {
            "contract_stack": contract_stack,
            "label_to_model": label_to_model,
            "aggregate_rankings": aggregate_rankings,
        },
        "timestamp": _now_iso(),
    }
