"""Contract prompts for the LLM Council."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ContractSpec:
    contract_id: str
    name: str
    system_prompt: str
    chairman_addendum: str = ""


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

CONTRACTS: Dict[str, ContractSpec] = {
    FACTORY_TRUTH_V1.contract_id: FACTORY_TRUTH_V1,
}


def get_contract(contract_id: str) -> ContractSpec:
    if contract_id not in CONTRACTS:
        raise KeyError(f"Unknown contract_id: {contract_id}")
    return CONTRACTS[contract_id]


def parse_contract_ids(contract_stack: Optional[Any]) -> List[str]:
    """Parse contract IDs. Contracts are opt-in and never auto-injected."""
    if not contract_stack:
        return []

    ids: List[str] = []
    if isinstance(contract_stack, str):
        ids = [c.strip() for c in contract_stack.split(",") if c and c.strip()]
    elif isinstance(contract_stack, list):
        for item in contract_stack:
            if isinstance(item, str) and item.strip():
                ids.append(item.strip())
            elif isinstance(item, dict):
                cid = item.get("contract_id") or item.get("id")
                if isinstance(cid, str) and cid.strip():
                    ids.append(cid.strip())

    out: List[str] = []
    seen = set()
    for cid in ids:
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)

    return out


def build_contract_system_messages(contract_stack: Optional[str]) -> List[dict]:
    messages: List[dict] = []
    for cid in parse_contract_ids(contract_stack):
        spec = get_contract(cid)
        messages.append({"role": "system", "content": spec.system_prompt})
    return messages


def build_chairman_contract_system_messages(contract_stack: Optional[str]) -> List[dict]:
    messages: List[dict] = []
    for cid in parse_contract_ids(contract_stack):
        spec = get_contract(cid)
        content = spec.system_prompt
        if spec.chairman_addendum:
            content = content + "\n" + spec.chairman_addendum
        messages.append({"role": "system", "content": content})
    return messages


def _contains_markdown_table_early(text: str, max_lines: int = 30) -> bool:
    lines = (text or "").splitlines()
    window = [ln.strip() for ln in lines[:max_lines] if ln.strip()]
    if len(window) < 2:
        return False
    has_pipe = any("|" in ln for ln in window[:10])
    has_sep = any(re.search(r"\|\s*:?-{3,}:?\s*\|", ln) or re.search(r"-{3,}\s*\|", ln) for ln in window[:15])
    return bool(has_pipe and has_sep)


def _needs_rubric_table_first(user_prompt: str) -> bool:
    up = (user_prompt or "").lower()
    return "start with the rubric table" in up or "rubric table" in up


def _detect_prohibited_claims(text: str) -> Dict[str, List[str]]:
    t = (text or "").lower()
    reasons: Dict[str, List[str]] = {}

    if re.search(r"\b(guarantee|100%|always works|cannot fail|will prevent|prevents all)\b", t):
        reasons.setdefault("guarantee", []).append("Contains guarantee / absolute prevention language.")

    if re.search(r"\b(accessibility (service|api)|android accessibility)\b", t):
        reasons.setdefault("accessibility_automation", []).append("Mentions Accessibility Service/API automation (disallowed).")

    if re.search(r"\b(background monitoring|always listening|listen 24/7|constant monitoring|monitor in the background)\b", t):
        reasons.setdefault("background_monitoring", []).append("Mentions background/always-on monitoring (disallowed).")

    dosing = re.search(r"\b(take|dose|dosing|administer)\b[^\n\.]{0,80}\b(\d+(?:\.\d+)?\s*(mg|mcg|g|ml))\b", t)
    if dosing:
        reasons.setdefault("medical_dosing", []).append("Contains dosing-like instruction with a specific quantity (disallowed).")

    return reasons


def _detect_soft_warnings(user_prompt: str, text: str, contract_stack: Optional[str]) -> List[str]:
    warnings: List[str] = []
    t = text or ""

    if ("[observed]" not in t.lower()) and ("[assumed]" not in t.lower()) and ("[inferred]" not in t.lower()):
        warnings.append("No [Observed]/[Assumed]/[Inferred] tags detected; contract prefers explicit uncertainty tagging.")

    if _needs_rubric_table_first(user_prompt):
        if not _contains_markdown_table_early(t):
            warnings.append("Requested rubric table format but markdown table not found near top.")

    return warnings


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

    active_contracts = parse_contract_ids(contract_stack)
    if not active_contracts:
        return {
            "stage": stage,
            "status": "PASS",
            "eligible": True,
            "hard_fail_reasons": [],
            "warnings": [],
            "checks": {},
            "evaluated_at": datetime.utcnow().isoformat(),
        }

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
