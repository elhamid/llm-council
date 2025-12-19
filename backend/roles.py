from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class RoleSpec:
    name: str
    system: str


    @property
    def system_prompt(self) -> str:
        # Back-compat: older council code expects `role_spec.system_prompt`.
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
    "google/": "synthesizer",
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
