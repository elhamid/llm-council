from __future__ import annotations

import json
from typing import Any

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

STAGE2_REPAIR_SYSTEM_PROMPT = (
    "STAGE 2 REPAIR MODE.\n"
    "Output rules (must follow exactly):\n"
    "- Output ONLY what the user prompt requests (often a single line).\n"
    "- No narration, no headings, no extra lines.\n"
    "- Do not add critiques unless explicitly asked."
)


def example_ranking(labels: list[str]) -> str:
    """Generate a non-trivial example ordering to reduce anchoring bias."""
    if not labels:
        return "Response B > Response C > Response A > Response D"
    if len(labels) == 4:
        return f"{labels[1]} > {labels[2]} > {labels[0]} > {labels[3]}"
    rot = labels[1:] + labels[:1]
    return " > ".join(rot)


def _critique_template_lines(labels: list[str]) -> list[str]:
    return [f"{label}: Strength: <...>; Flaw: <...>" for label in labels]


def build_stage2_rubric(labels: list[str], example_rank: str) -> str:
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
        f"FINAL_RANKING: {example_rank}\n"
        f"Valid labels: {labels_line}\n"
    )


def build_stage2_strict_rejudge_prompt(labels: list[str], example_rank: str, stage2_prompt: str) -> str:
    line_count = len(labels) + 1
    template = "\n".join(_critique_template_lines(labels))
    return (
        f"OUTPUT EXACTLY {line_count} LINES. No headings. No markdown. No bullets. No blank lines.\n"
        "No first-person. No narration.\n"
        "Each critique line must be ONE sentence and include BOTH:\n"
        "  Strength: <...>; Flaw: <...>\n"
        "Do NOT copy the example ordering; choose based on the content.\n"
        "Template:\n"
        f"{template}\n"
        f"FINAL_RANKING: {example_rank}\n"
        f"Return ONLY those {line_count} lines.\n\n"
        + stage2_prompt
    )


def build_stage2_rewrite_prompt(labels: list[str], example_rank: str, text_to_rewrite: str) -> str:
    line_count = len(labels) + 1
    template = "\n".join(_critique_template_lines(labels))
    return (
        f"Rewrite the text below into EXACTLY {line_count} LINES using the required template.\n"
        "Rules:\n"
        "- No markdown, no headings, no extra lines.\n"
        "- No first-person, no narration.\n"
        "- Each critique line MUST include: 'Strength: ...; Flaw: ...' in one sentence.\n"
        "Template:\n"
        f"{template}\n"
        f"FINAL_RANKING: {example_rank}\n\n"
        "TEXT TO REWRITE:\n"
        + (text_to_rewrite or "")
    )


def build_stage2_one_line_repair_prompt(labels: list[str]) -> str:
    labels_line = ", ".join(labels)
    return (
        "Return ONLY one line in this exact format (no other text):\n"
        "FINAL_RANKING: <labels joined by ' > '>\n"
        "Rules:\n"
        f"- Use ONLY these labels: {labels_line}\n"
        "- Each label must appear EXACTLY ONCE.\n"
        "- Use ' > ' between labels.\n"
        "- Do NOT use default ordering unless it is truly correct.\n"
    )


def build_chairman_prompt(
    user_prompt: str,
    stage1_results: list[dict[str, Any]],
    stage2_results: list[dict[str, Any]],
    aggregate_rankings: list[dict[str, Any]],
) -> str:
    return (
        "You are the Chairman. Synthesize the best final answer for the user.\n"
        "Use Stage 2 critiques and the aggregate rankings to guide you.\n"
        "Do not claim traction or facts that are not present.\n\n"
        f"USER PROMPT:\n{user_prompt}\n\n"
        f"STAGE 1 OUTPUTS:\n{json.dumps(stage1_results, ensure_ascii=False)}\n\n"
        f"STAGE 2 OUTPUTS:\n{json.dumps(stage2_results, ensure_ascii=False)}\n\n"
        f"AGGREGATE RANKINGS:\n{json.dumps(aggregate_rankings, ensure_ascii=False)}\n"
    )
