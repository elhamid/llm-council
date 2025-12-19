"""Contract prompts for the LLM Council.

A "contract" is a small, explicit system prompt that defines how the council should
behave for a given factory run.

Design goals:
- Product-agnostic by default (factory contract)
- Optionally layer in a product-specific addendum
- Keep contracts enforceable and lightweight
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Any

import re
from datetime import datetime


@dataclass(frozen=True)
class ContractSpec:
    contract_id: str
    name: str
    system_prompt: str
    chairman_addendum: str = ""


# --- Factory-base contract (always include) ---
FACTORY_TRUTH_V1 = ContractSpec(
    contract_id="factory_truth_v1",
    name="Factory Truth-First v1",
    system_prompt=(
        "You are running inside a product-agnostic LLM Council factory.\n"
        "Factory Contract (must follow):\n"
        "1) Truth-first: prioritize what is most likely true... about the user’s real problem; state uncertainty explicitly.\n"
        "2) Separate facts from guesses: tag non-trivial cla...ims as [Observed] / [Assumed] / [Inferred]; do not blur them.\n"
        "3) Ask at most 1 killer question only if it would m...ommendation; otherwise proceed with best-guess + assumptions.\n"
        "4) Smallest valuable action: propose something test...with minimal build; avoid dependencies and platform thinking.\n"
        "5) One primary risk: name the single highest-risk failure mode and add one simple guardrail.\n"
        "6) One metric that matters: pick one leading indicator; define a clear pass/fail threshold.\n"
        "7) Design for the edge user: handle the most constr...d path (low attention, low literacy, high stress) by default.\n"
        "8) Make it legible: include a short rationale and a clear next step; no jargon; no sprawling option lists.\n"
        "9) Creativity inside constraints: propose at most 2...ts (Conservative baseline + Bold alternative), both testable.\n"
        "10) Synthesis discipline: do not introduce new mechanisms unless you label them [New Proposal] and explain why.\n"
    ),
)

# --- Product addendum example (elder-care) ---
ELDERCARE_SAFETY_V1 = ContractSpec(
    contract_id="eldercare_safety_v1",
    name="Eldercare Safety v1",
    system_prompt=(
        "Product Addendum (elder-care safety):\n"
        "- Do not provide medical diagnosis or dosing advice. Default to safe-hold instructions and escalation.\n"
        "- For scam-risk: prioritize immediate 'stop/hold' guidance; avoid asking for sensitive info.\n"
        "- For caregiver escalation: prioritize burnout cont...ts, batching, quiet hours) while preserving safety overrides.\n"
        "- Be explicit about consent/privacy when capturing audio; keep retention minimal.\n"
    ),
    chairman_addendum=(
        "Chairman: keep the result minimal and safe; avoid compliance theater; prefer simple guardrails.\n"
    ),
)

CONTRACTS: Dict[str, ContractSpec] = {
    FACTORY_TRUTH_V1.contract_id: FACTORY_TRUTH_V1,
    ELDERCARE_SAFETY_V1.contract_id: ELDERCARE_SAFETY_V1,
}


def get_contract(contract_id: str) -> ContractSpec:
    if contract_id not in CONTRACTS:
        raise KeyError(f"Unknown contract_id: {contract_id}")
    return CONTRACTS[contract_id]


def parse_contract_ids(contract_stack: Optional[str]) -> List[str]:
    """
    Parse a comma-separated contract stack.
    Always ensures the factory base contract is present (first).
    """
    ids: List[str] = []
    if contract_stack:
        ids = [c.strip() for c in contract_stack.split(",") if c.strip()]

    # Ensure factory base is always included first
    if FACTORY_TRUTH_V1.contract_id not in ids:
        ids.insert(0, FACTORY_TRUTH_V1.contract_id)
    else:
        # move it to the front
        ids = [FACTORY_TRUTH_V1.contract_id] + [c for c in ids if c != FACTORY_TRUTH_V1.contract_id]

    return ids


def build_contract_system_messages(contract_stack: Optional[str]) -> List[dict]:
    """
    Build system messages for Council members (Stage 1 + Stage 2).
    """
    messages: List[dict] = []
    for cid in parse_contract_ids(contract_stack):
        spec = get_contract(cid)
        messages.append({"role": "system", "content": spec.system_prompt})
    return messages


def build_chairman_contract_system_messages(contract_stack: Optional[str]) -> List[dict]:
    """
    Build system messages for the Chairman (Stage 3).
    Includes chairman_addendum when present.
    """
    messages: List[dict] = []
    for cid in parse_contract_ids(contract_stack):
        spec = get_contract(cid)
        content = spec.system_prompt
        if spec.chairman_addendum:
            content = content + "\n" + spec.chairman_addendum
        messages.append({"role": "system", "content": content})
    return messages


def contract_summary(contract_stack: Optional[str]) -> str:
    """Short, human-readable summary for prompts/logs."""
    ids = parse_contract_ids(contract_stack)
    names = [get_contract(cid).name for cid in ids]
    return "Contracts applied: " + " + ".join(f"{cid} ({name})" for cid, name in zip(ids, names))


# -----------------------------------------------------------------------------
# Contract compliance evaluation (lightweight post-check + gating)
# -----------------------------------------------------------------------------


def _contains_markdown_table_early(text: str, max_lines: int = 30) -> bool:
    lines = (text or "").splitlines()
    window = [ln.strip() for ln in lines[:max_lines] if ln.strip()]
    if len(window) < 2:
        return False
    # Heuristic: a markdown table has '|' separators and a header separator line with ---.
    has_pipe = any("|" in ln for ln in window[:10])
    has_sep = any(re.search(r"\|\s*:?-{3,}:?\s*\|", ln) or re.search(r"-{3,}\s*\|", ln) for ln in window[:15])
    return bool(has_pipe and has_sep)


def _needs_rubric_table_first(user_prompt: str) -> bool:
    up = (user_prompt or "").lower()
    return "start with the rubric table" in up or "rubric table" in up


def _has_section_heading(text: str, token: str) -> bool:
    # Accept "## B", "B)", "## B)" etc.
    t = (text or "").lower()
    token = token.lower()
    return (f"## {token}" in t) or (f"{token})" in t) or (f"{token} -" in t) or (f"{token}:" in t)


def _detect_prohibited_claims(text: str) -> Dict[str, List[str]]:
    """Return {category: [reason,...]} for hard-fail categories detected."""
    t = (text or "").lower()
    reasons: Dict[str, List[str]] = {}

    # 1) Guaranteed outcomes / scam prevention guarantees
    if re.search(r"\b(guarantee|100%|always works|cannot fail|will prevent|prevents all)\b", t):
        reasons.setdefault("guarantee", []).append("Contains guarantee / absolute prevention language.")

    # 2) Accessibility automation (explicit)
    if re.search(r"\b(accessibility (service|api)|android accessibility)\b", t):
        reasons.setdefault("accessibility_automation", []).append("Mentions Accessibility Service/API automation (disallowed).")

    # 3) Background surveillance / always-on monitoring
    if re.search(r"\b(background monitoring|always listening|listen 24/7|constant monitoring|monitor in the background)\b", t):
        reasons.setdefault("background_monitoring", []).append("Mentions background/always-on monitoring (disallowed).")

    # 4) Medical diagnosis / dosing instructions (only hard-fail if it looks like dosing)
    dosing = re.search(r"\b(take|dose|dosing|administer)\b[^\n\.]{0,80}\b(\d+(?:\.\d+)?\s*(mg|mcg|g|ml))\b", t)
    if dosing:
        reasons.setdefault("medical_dosing", []).append("Contains dosing-like instruction with a specific quantity (disallowed).")

    return reasons


def _detect_soft_warnings(user_prompt: str, text: str, contract_stack: Optional[str]) -> List[str]:
    warnings: List[str] = []
    t = (text or "")

    # Factory contract asks for [Observed]/[Assumed]/[Inferred] tagging on non-trivial claims.
    if ("[observed]" not in t.lower()) and ("[assumed]" not in t.lower()) and ("[inferred]" not in t.lower()):
        warnings.append("No [Observed]/[Assumed]/[Inferred] tags detected; contract prefers explicit uncertainty tagging.")

    # If the user requested a strict structure, warn if headings are missing (soft by default).
    if _needs_rubric_table_first(user_prompt):
        # Must have sections B–F per protocol.
        missing = []
        for sec in ["b", "c", "d", "e", "f"]:
            if not _has_section_heading(t, sec):
                missing.append(sec.upper())
        if missing:
            warnings.append(f"Missing expected sections: {', '.join(missing)} (protocol B–F).")

    # Eldercare addendum: warn if it drifts into medical diagnosis language.
    if contract_stack and "eldercare_safety_v1" in contract_stack:
        if re.search(r"\b(diagnos(e|is)|you have|this means you have)\b", t.lower()):
            warnings.append("Possible medical-diagnosis phrasing detected; prefer safe-hold + escalation.")

    return warnings


def evaluate_contract_compliance(
    user_prompt: str,
    response_text: str,
    contract_stack: Optional[str] = None,
    *,
    stage: str = "stage1",
) -> Dict[str, Any]:
    """Evaluate a response against lightweight enforceable checks.

    Returns a JSON-serializable dict suitable for storing in conversation meta.
    This is intentionally heuristic (high-signal, low false-positive).
    """
    hard_fail_reasons: List[str] = []
    warnings: List[str] = []
    checks: Dict[str, Any] = {}

    # Output-format enforcement when the user explicitly demands it.
    if _needs_rubric_table_first(user_prompt):
        ok_table = _contains_markdown_table_early(response_text)
        checks["rubric_table_first"] = ok_table
        if not ok_table:
            hard_fail_reasons.append("Requested 'Start with the rubric table' but no markdown table detected near the top.")

    # Hard prohibitions (policy / scope / disallowed mechanisms)
    prohibited = _detect_prohibited_claims(response_text)
    if prohibited:
        checks["prohibited"] = prohibited
        for _, rs in prohibited.items():
            hard_fail_reasons.extend(rs)

    # Soft warnings (contract style preferences)
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


def build_contract_gate_summary(
    stage1_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str],
) -> Dict[str, Any]:
    """Create a compact summary for storage/UI without duplicating full text."""
    model_to_label = {m: lbl for lbl, m in (label_to_model or {}).items()}

    disqualified: List[Dict[str, Any]] = []
    eligible = 0

    for r in stage1_results or []:
        ev = r.get("contract_eval") or {}
        if ev.get("eligible"):
            eligible += 1
            continue
        disqualified.append(
            {
                "label": model_to_label.get(r.get("model"), ""),
                "model": r.get("model"),
                "reasons": ev.get("hard_fail_reasons") or [],
            }
        )

    return {
        "total": len(stage1_results or []),
        "eligible": eligible,
        "disqualified": disqualified,
    }