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
from typing import Dict, List, Optional


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
        "1) Truth-first: prioritize what is most likely true about the user’s real problem; state uncertainty explicitly.\n"
        "2) Separate facts from guesses: tag non-trivial claims as [Observed] / [Assumed] / [Inferred]; do not blur them.\n"
        "3) Ask at most 1 killer question only if it would materially change the recommendation; otherwise proceed with best-guess + assumptions.\n"
        "4) Smallest valuable action: propose something testable this week with minimal build; avoid dependencies and platform thinking.\n"
        "5) One primary risk: name the single highest-risk failure mode and add one simple guardrail.\n"
        "6) One metric that matters: pick one leading indicator; define a clear pass/fail threshold.\n"
        "7) Design for the edge user: handle the most constrained path (low attention, low literacy, high stress) by default.\n"
        "8) Make it legible: include a short rationale and a clear next step; no jargon; no sprawling option lists.\n"
        "9) Creativity inside constraints: propose at most 2 variants (Conservative baseline + Bold alternative), both testable.\n"
        "10) Synthesis discipline: do not introduce new mechanisms unless you label them [New Proposal] and explain why.\n"
        "Keep outputs concise and practical.\n"
        "11) No emojis: do not use emojis unless the user explicitly uses emojis first.\n"

    ),
    chairman_addendum=(
        "Chairman: ensure the final answer is traceable to council inputs. "
        "If you introduce anything not present in Stage 1/2, label it [New Proposal] and justify it briefly.\n"
    ),
)

# --- Example product contract (optional layer) ---
ELDERCARE_SAFETY_V1 = ContractSpec(
    contract_id="eldercare_safety_v1",
    name="Eldercare Safety v1",
    system_prompt=(
        "Product Addendum (elder-care safety):\n"
        "- Do not provide medical diagnosis or dosing advice. Default to safe-hold instructions and escalation.\n"
        "- For scam-risk: prioritize immediate 'stop/hold' guidance; avoid asking for sensitive info.\n"
        "- For caregiver escalation: prioritize burnout controls (rate limits, batching, quiet hours) while preserving safety overrides.\n"
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
    if FACTORY_TRUTH_V1.contract_id in ids:
        ids = [c for c in ids if c != FACTORY_TRUTH_V1.contract_id]
    ids = [FACTORY_TRUTH_V1.contract_id] + ids
    return ids


def build_contract_system_messages(contract_stack: Optional[str]) -> List[dict]:
    """Return system messages for the contract stack (council members)."""
    messages: List[dict] = []
    for cid in parse_contract_ids(contract_stack):
        spec = get_contract(cid)
        messages.append({"role": "system", "content": spec.system_prompt})
    return messages


def build_chairman_contract_system_messages(contract_stack: Optional[str]) -> List[dict]:
    """Return system messages for the contract stack (chairman), including addenda."""
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