import asyncio
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from .contracts import (
    build_chairman_contract_system_messages,
    build_contract_gate_summary,
    build_contract_system_messages,
    evaluate_contract_compliance,
)
from .roles import get_role_spec

STAGE1_LAST_ERRORS: Dict[str, str] = {}
STAGE2_LAST_ERRORS: Dict[str, str] = {}


CHAIRMAN_MODEL = os.getenv("CHAIRMAN_MODEL", "anthropic/claude-opus-4.5")

# Optional Stage-3 long-context helper (OFF by default).
# Enable only when Stage 1 + Stage 2 payloads are too large for the Chairman context window.
STAGE3_HELPER_MODEL = (os.getenv("STAGE3_HELPER_MODEL") or "google/gemini-3-pro-preview").strip()
STAGE3_HELPER_ENABLED = (os.getenv("STAGE3_HELPER_ENABLED") or "0").strip() == "1"
try:
    STAGE3_HELPER_TRIGGER_CHARS = int((os.getenv("STAGE3_HELPER_TRIGGER_CHARS") or "120000").strip())
except Exception:
    STAGE3_HELPER_TRIGGER_CHARS = 120000

DEFAULT_STAGE1_MODELS = [
    os.getenv("STAGE1_MODEL_A", "openai/gpt-5.2"),
    os.getenv("STAGE1_MODEL_B", "google/gemini-3-pro-preview"),
    os.getenv("STAGE1_MODEL_C", "anthropic/claude-sonnet-4.5"),
    os.getenv("STAGE1_MODEL_D", "x-ai/grok-4.1-fast"),
]


DEFAULT_STAGE2_MODELS = [
    os.getenv("STAGE2_MODEL_A", "openai/gpt-5.2"),
    os.getenv("STAGE2_MODEL_B", "anthropic/claude-opus-4.5"),
    os.getenv("STAGE2_MODEL_C", "anthropic/claude-sonnet-4.5"),
    os.getenv("STAGE2_MODEL_D", "x-ai/grok-4.1-fast"),
]

# Stage-2 consensus gate + adjudication (Option B3)
STAGE2_ADJUDICATOR_MODEL = os.getenv("STAGE2_ADJUDICATOR_MODEL", "anthropic/claude-opus-4.5")
STAGE2_ADJUDICATE_ENABLED = (os.getenv("STAGE2_ADJUDICATE_ENABLED") or "1").strip() == "1"
try:
    STAGE2_ADJUDICATE_MIN_NONPARTIAL = int((os.getenv("STAGE2_ADJUDICATE_MIN_NONPARTIAL") or "3").strip())
except Exception:
    STAGE2_ADJUDICATE_MIN_NONPARTIAL = 3
try:
    STAGE2_ADJUDICATE_MIN_TOP1_VOTES = int((os.getenv("STAGE2_ADJUDICATE_MIN_TOP1_VOTES") or "0").strip())
except Exception:
    STAGE2_ADJUDICATE_MIN_TOP1_VOTES = 0


STAGE2_ADJUDICATE_MIN_TOP1_VOTES = 0



def _dedupe_preserve_order(items):
    seen = set()
    out = []
    for x in items or []:
        if not x:
            continue
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out

def _get_openai_client() -> AsyncOpenAI:
    openrouter_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()

    api_key = openrouter_key or openai_key
    base_url = (os.getenv("OPENAI_BASE_URL") or "").strip() or (os.getenv("OPENROUTER_BASE_URL") or "").strip()

    if openrouter_key and not base_url:
        base_url = "https://openrouter.ai/api/v1"

    if not api_key:
        raise RuntimeError("Missing API key: set OPENROUTER_API_KEY (preferred) or OPENAI_API_KEY")

    kwargs: Dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url

    return AsyncOpenAI(**kwargs)


_CLIENT: Optional[AsyncOpenAI] = None
_CLIENT_SIG: Optional[str] = None


def _client() -> AsyncOpenAI:
    global _CLIENT, _CLIENT_SIG

    openrouter_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    base_url = (os.getenv("OPENAI_BASE_URL") or "").strip() or (os.getenv("OPENROUTER_BASE_URL") or "").strip()

    sig = f"{openrouter_key or openai_key}|{base_url}"
    if _CLIENT is None or _CLIENT_SIG != sig:
        _CLIENT = _get_openai_client()
        _CLIENT_SIG = sig
    return _CLIENT


def _member_messages(
    model: str,
    user_prompt: str,
    contract_stack: Optional[str],
    stage: Optional[str] = None,
) -> List[Dict[str, str]]:
    # Stage 2 must be a clean evaluator persona. The Factory Contract includes output
    # formatting rules (e.g., [Observed]/[Assumed]/[Inferred]) that conflict with the
    # strict Stage-2 5-line template, and can cause some providers (notably Gemini)
    # to fail the judge step. So: apply contracts for generation (stage1) and
    # synthesis (stage3), but do NOT apply them to judging/repair.
    if stage in ("stage2", "stage2_repair", "stage3_helper"):
        system_msgs: List[Dict[str, str]] = []
    else:
        system_msgs = build_contract_system_messages(contract_stack)

    # Keep per-model role prompts for generation (Stage 1), but do NOT let them leak into judging (Stage 2).
    if stage not in ("stage2", "stage2_repair", "stage3_helper"):
        role_spec = get_role_spec(model)
        system_msgs.append({"role": "system", "content": role_spec.system_prompt})

    # Stage 2 must be a uniform evaluator persona.
    # NOTE: Stage-2 repair prompts must NOT inherit the 5-line evaluator system prompt.
    if stage == "stage2":
        system_msgs.append(
            {
                "role": "system",
                "content": (
                    "STAGE 2 EVALUATION MODE.\n"
                    "You are grading anonymous answers for a YC-level product team: correctness first, then usefulness.\n"
                    "Goal: choose the answer a YC-level product team would actually ship.\n"
                    "Truth-first: do not invent facts; if inputs are missing, call that out as a flaw and reward answers that request the missing inputs.\n"
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
                    "- Do NOT write 'Insufficient signal in text.' unless the response is empty/refuses or the responses are truly indistinguishable.\n"
                    "- If answers are similar, break ties by correctness, then actionability, then clarity; cite ONE concrete detail from each response in its Strength/Flaw.\n"
                    "- If an answer is empty or refuses, say that as the flaw.\n"
                    "- Output NOTHING else."
                ),
            }
        )
    elif stage == "stage2_repair":
        system_msgs.append(
            {
                "role": "system",
                "content": (
                    "STAGE 2 REPAIR MODE.\n"
                    "Output rules (must follow exactly):\n"
                    "- Output ONLY what the user prompt requests (often a single line).\n"
                    "- No narration, no headings, no extra lines.\n"
                    "- Do not add critiques unless explicitly asked."
                ),
            }
        )
    elif stage == "stage3_helper":
        system_msgs.append(
            {
                "role": "system",
                "content": (
                    "STAGE 3 HELPER MODE.\n"
                    "You are a long-context helper preparing a compact briefing for the Chairman.\n"
                    "Truth-first: use ONLY details present in the provided data; do not invent facts.\n"
                    "No process narration, no first-person, no meta commentary.\n"
                    "Output rules (must follow exactly):\n"
                    "- Output 6 to 12 bullet points total.\n"
                    "- Each bullet MUST reference at least one concrete detail from the inputs (a short quote is OK).\n"
                    "- Include: best candidate + why, biggest flaw/risk, missing info, and a suggested final-answer outline.\n"
                    "- Keep it compact.\n"
                    "- Output ONLY the bullets."
                ),
            }
        )

    return system_msgs + [{"role": "user", "content": user_prompt}]


def _chairman_messages(model: str, chairman_prompt: str, contract_stack: Optional[str]) -> List[Dict[str, str]]:
    role_spec = get_role_spec(model)
    system_msgs = build_chairman_contract_system_messages(contract_stack)
    system_msgs.append({"role": "system", "content": role_spec.system_prompt})
    return system_msgs + [{"role": "user", "content": chairman_prompt}]


def _content_to_text(content: Any) -> str:
    """Convert OpenAI/OpenRouter message.content shapes into plain text."""

    def part_to_text(p: Any) -> str:
        if p is None:
            return ""
        if isinstance(p, str):
            return p
        if isinstance(p, dict):
            if isinstance(p.get("text"), str):
                return p["text"]
            t = p.get("text")
            if isinstance(t, dict) and isinstance(t.get("value"), str):
                return t["value"]
            if isinstance(p.get("content"), str):
                return p["content"]
            c = p.get("content")
            if isinstance(c, list):
                return "".join(part_to_text(x) for x in c)
            return ""

        t = getattr(p, "text", None)
        if isinstance(t, str):
            return t
        if t is not None:
            v = getattr(t, "value", None)
            if isinstance(v, str):
                return v

        c = getattr(p, "content", None)
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "".join(part_to_text(x) for x in c)
        if c is not None:
            return part_to_text(c)

        if hasattr(p, "model_dump"):
            try:
                return part_to_text(p.model_dump())
            except Exception:
                return ""

        if hasattr(p, "__dict__"):
            try:
                return part_to_text(vars(p))
            except Exception:
                return ""

        return ""

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part_to_text(x) for x in content)
    if isinstance(content, dict):
        return part_to_text(content)

    return part_to_text(content) or str(content)


def _looks_like_provider_id(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if re.fullmatch(r"gen-\d{6,}-[A-Za-z0-9_\-]{8,}", t):
        return True
    if re.fullmatch(r"(chatcmpl|cmpl|req|request|run|msg)-[A-Za-z0-9\-]{12,}", t, flags=re.I):
        return True
    if re.fullmatch(r"[A-Za-z0-9\-]{24,}", t) and (" " not in t) and ("\n" not in t):
        return True
    return False


def _deep_extract_text(obj: Any) -> str:
    SKIP_KEYS = {
        "id",
        "request_id",
        "generation_id",
        "gen_id",
        "model",
        "provider",
        "usage",
        "created",
        "created_at",
        "timestamp",
        "object",
        "finish_reason",
        "system_fingerprint",
    }
    TEXT_KEYS = {"content", "text", "value", "output_text"}
    candidates: List[str] = []

    def add_candidate(s: Any) -> None:
        if not isinstance(s, str):
            return
        t = s.strip()
        if not t:
            return
        if _looks_like_provider_id(t):
            return
        candidates.append(t)

    def walk(o: Any, key: Optional[str] = None) -> None:
        if o is None:
            return
        if isinstance(o, str):
            if key and (key.lower() in TEXT_KEYS or key.lower().endswith("content")):
                add_candidate(o)
            return
        if isinstance(o, list):
            for x in o:
                walk(x, key=key)
            return
        if isinstance(o, dict):
            for k, v in o.items():
                lk = (k or "").lower()
                if lk in SKIP_KEYS or lk.endswith("_id"):
                    continue
                if lk in TEXT_KEYS or lk.endswith("content"):
                    if isinstance(v, str):
                        add_candidate(v)
                        continue
                    if isinstance(v, list):
                        for x in v:
                            walk(x, key=lk)
                        continue
                    if isinstance(v, dict):
                        inner_val = v.get("value") if isinstance(v.get("value"), str) else None
                        if inner_val:
                            add_candidate(inner_val)
                        walk(v, key=lk)
                        continue
                walk(v, key=lk)
            return

        if hasattr(o, "model_dump"):
            try:
                walk(o.model_dump(), key=key)
                return
            except Exception:
                pass
        if hasattr(o, "__dict__"):
            try:
                walk(vars(o), key=key)
                return
            except Exception:
                pass

    walk(obj, key=None)
    if not candidates:
        return ""
    best = max(candidates, key=lambda s: len(s))
    return best.strip()


async def _chat(model: str, messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
    client = _client()
    # Prevent truncation: increase output token budget.
    # Safe env override (won't crash on invalid values).
    max_gen_tokens = 2048
    _env = (os.getenv("COUNCIL_MAX_TOKENS") or "").strip()
    if _env:
        try:
            max_gen_tokens = int(_env)
        except ValueError:
            max_gen_tokens = 2048

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_gen_tokens,
        )
    except TypeError:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_output_tokens=max_gen_tokens,
        )

    msg = resp.choices[0].message
    content = getattr(msg, "content", None)
    text = _content_to_text(content)

    debug_ids = (os.getenv("COUNCIL_DEBUG_IDS") or "").strip() == "1"

    if _looks_like_provider_id(text):
        if debug_ids:
            print(f"[council] filtered provider-id as content from {model}: {text!r}")
        text = ""

    if not text:
        raw_msg = None
        try:
            raw_msg = msg.model_dump()
        except Exception:
            try:
                raw_msg = dict(msg)
            except Exception:
                try:
                    raw_msg = vars(msg)
                except Exception:
                    raw_msg = None

        text = _deep_extract_text(raw_msg)
        if _looks_like_provider_id(text):
            if debug_ids:
                print(f"[council] filtered provider-id from message deep extract for {model}: {text!r}")
            text = ""

    return (text or "").strip()


def _label_responses(stage1_results: List[Dict[str, Any]]) -> Tuple[List[str], Dict[str, str]]:
    label_to_model: Dict[str, str] = {}
    labeled_blocks: List[str] = []
    for idx, r in enumerate(stage1_results):
        label = f"Response {chr(ord('A') + idx)}"
        model = r.get("model") or f"model_{idx}"
        label_to_model[label] = model
        labeled_blocks.append(f"{label}:\n{r.get('response','')}".strip())
    return labeled_blocks, label_to_model



def _normalize_ws(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    s = s.replace("\u00a0", " ")
    s = s.replace("\u202f", " ")
    s = s.replace("\u2007", " ")
    return re.sub(r"\s+", " ", s).strip()


# Remove common wrappers (code fences, stray backticks/quotes) from model output.
def _strip_wrappers(text: str) -> str:
    """Remove common wrappers (code fences, stray backticks/quotes) from model output."""
    t = (text or "").strip()
    if not t:
        return ""

    # Trim surrounding triple-backtick fences if the whole output is fenced.
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)

    # Remove stray leading/trailing backticks/quotes/spaces.
    t = t.strip().strip("`").strip().strip('"').strip().strip("'").strip()
    return t


def _contains_process_narration(text: str) -> bool:
    raw = _normalize_ws(text or "").lower()
    return bool(
        re.search(
            r"\b("
            r"i am currently|i'm currently|i am now|i'm now|"
            r"initiating the analysis|my focus is|the plan is|"
            r"i will now|i am going to|i'm going to|"
            r"i have just|i've just|i just|"
            r"i have finished|i've finished|just finished|"
            r"i have hit|i've hit|hit a snag|"
            r"i am grappling|i'm grappling|"
            r"i am considering|i'm considering|"
            r"i am deciding|i'm deciding|"
            r"i have decided|i've decided|"
            r"finalizing the strategy|processing the parameters|"
            r"assessing the conundrum|interpreting the context"
            r")\b",
            raw,
        )
    )


def _extract_final_ranking_line(text: str) -> str:
    raw = _strip_wrappers((text or "").strip())
    if not raw:
        return ""

    lines = [_normalize_ws(ln) for ln in raw.splitlines() if ln and ln.strip()]
    for ln in reversed(lines):
        m = re.search(r"\bFINAL_RANKING\s*:\s*", ln, flags=re.I)
        if m:
            # Return the substring starting at FINAL_RANKING so downstream parsing works.
            return ln[m.start():].strip()
    return ""


def _extract_fuzzy_ranking_chain(text: str) -> str:
    raw = _strip_wrappers((text or "").strip())
    if not raw:
        return ""

    raw = _normalize_ws(raw)
    # Normalize arrow variants.
    raw = (
        raw.replace("→", ">")
        .replace("⇒", ">")
        .replace("->", ">")
        .replace("＞", ">")
        .replace("›", ">")
        .replace("»", ">")
    )

    # First try: full labels "Response A > Response B ..."
    pat_full = re.compile(r"(Response\s*[A-Z](?:\s*>\s*Response\s*[A-Z])+)", flags=re.I)
    matches = pat_full.findall(raw)
    if matches:
        return matches[-1].strip()

    # Second try: letters-only "A > B > C > D" (optionally without spaces)
    pat_letters = re.compile(r"\b([A-D](?:\s*>\s*[A-D]){2,})\b", flags=re.I)
    m = pat_letters.search(raw)
    if m:
        chain = m.group(1)
        parts = [p.strip() for p in chain.split(">") if p.strip()]
        if parts:
            return " > ".join([f"Response {p.upper()}" for p in parts])

    return ""


def _critique_is_placeholder(line: str) -> bool:
    raw = (line or "").strip().lower()
    if not raw:
        return True
    return "insufficient signal in text" in raw


# Evidence proxy for Stage-2 critiques: require at least one concrete token overlap
# between each critique line and the underlying response text.
_EVID_STOPWORDS = {
    "the","a","an","and","or","to","of","in","on","for","with","without","by","as","is","are","was","were",
    "be","been","being","this","that","it","its","i","you","we","they","he","she","them","us","our","your","their",
    "from","into","over","under","then","than","if","else","when","while","do","does","did","done","can","could","should",
    "would","may","might","must","will","just",
}


def _evidence_tokens(s: str) -> set:
    # Keep it intentionally simple + robust across providers.
    if not s:
        return set()
    toks = re.findall(r"[A-Za-z0-9_]{5,}", (s or "").lower())
    return {t for t in toks if t not in _EVID_STOPWORDS}


def _evidence_ok(line: str, response_text: str) -> bool:
    # If the underlying response is extremely short, do not penalize the judge.
    rt = (response_text or "").strip()
    if len(rt) < 20:
        return True
    lt = _evidence_tokens(line)
    if not lt:
        return False
    return len(lt & _evidence_tokens(rt)) >= 1


def _coerce_stage2_5line(text: str, labels: List[str]) -> str:
    """Coerce judge output into strict 5-line format (A-D + FINAL_RANKING).

    We salvage partial ranking signals and incomplete critique lines, but we
    will mark such outputs as `partial` upstream so they do not influence
    aggregation. This keeps API shape stable while protecting quality.
    """
    if not text:
        return ""

    raw_lines = [ln for ln in (text or "").splitlines() if ln and ln.strip()]
    crit: Dict[str, str] = {}

    # Accept either:
    # - "Response A: Strength: ...; Flaw: ..."
    # - "A: Strength: ...; Flaw: ..."
    # - legacy "Response A: ..." (we keep it, but may be marked partial)
    for ln in raw_lines:
        nln = _normalize_ws(ln)
        m = re.match(
            r"^\s*(?:[-*]\s*)?(?:Response\s*)?([A-D])\s*(?:[:\-\u2013\u2014\.]|\))\s*(.+)$",
            nln,
            flags=re.I,
        )
        if not m:
            continue
        letter = (m.group(1) or "").upper()
        body = (m.group(2) or "").strip()
        if not letter:
            continue
        label = f"Response {letter}"
        if label not in labels:
            continue
        if body:
            crit[label] = f"{label}: {body}"

    parsed_any = _parse_ranking_from_text(text, allowed_labels=None)
    if not parsed_any:
        return ""

    keep: List[str] = []
    seen = set()
    for lab in parsed_any:
        if lab in labels and lab not in seen:
            seen.add(lab)
            keep.append(lab)

    full = keep + [lab for lab in labels if lab not in seen]
    if len(full) != len(labels) or set(full) != set(labels):
        return ""

    final_line = "FINAL_RANKING: " + " > ".join(full)

    def line_for(letter: str) -> str:
        label = f"Response {letter}"
        if label in crit:
            return crit[label]
        return f"{label}: Strength: None; Flaw: Insufficient signal in text."

    return "\n".join(
        [
            line_for("A"),
            line_for("B"),
            line_for("C"),
            line_for("D"),
            final_line,
        ]
    )


def _parse_ranking_from_text(text: str, allowed_labels: Optional[List[str]] = None) -> List[str]:
    strict_line = _extract_final_ranking_line(text)
    if strict_line:
        return _parse_ranking_order(strict_line, allowed_labels=allowed_labels)
    chain = _extract_fuzzy_ranking_chain(text)
    if not chain:
        return []
    return _parse_ranking_order(f"FINAL_RANKING: {chain}", allowed_labels=allowed_labels)


def _parse_ranking_order(text: str, allowed_labels: Optional[List[str]] = None) -> List[str]:
    raw = _normalize_ws(text or "")
    if not raw:
        return []
    allowed = set(allowed_labels) if allowed_labels else None
    m = re.search(r"\bFINAL_RANKING\s*:\s*(.+)$", raw, flags=re.I)
    if not m:
        return []
    tail = _normalize_ws(m.group(1) or "")
    if not tail:
        return []
    tail = tail.replace("→", ">").replace("⇒", ">").replace("->", ">")
    chunks = [c.strip() for c in tail.split(">") if c.strip()]

    def norm_label(s: str) -> Optional[str]:
        s = (s or "").strip()
        if not s:
            return None
        m1 = re.search(r"response\s*([A-Z])\b", s, flags=re.I)
        if m1:
            lab = f"Response {m1.group(1).upper()}"
            if allowed is None or lab in allowed:
                return lab
            return None
        m2 = re.fullmatch(r"[A-Z]", s, flags=re.I)
        if m2:
            lab = f"Response {s.upper()}"
            if allowed is None or lab in allowed:
                return lab
            return None
        return None

    out: List[str] = []
    seen = set()
    for ch in chunks:
        lab = norm_label(ch)
        if lab and lab not in seen:
            seen.add(lab)
            out.append(lab)

    if not out:
        return []
    if allowed is not None:
        if len(out) != len(allowed) or set(out) != allowed:
            return []
    return out


def _example_ranking(labels: List[str]) -> str:
    # Reduce anchoring on A > B > C > D by providing a non-trivial example ordering.
    if not labels:
        return "Response B > Response C > Response A > Response D"
    if len(labels) == 4:
        return f"{labels[1]} > {labels[2]} > {labels[0]} > {labels[3]}"
    rot = labels[1:] + labels[:1]
    return " > ".join(rot)


async def stage1_collect_responses(user_prompt: str, contract_stack: Optional[str] = None) -> List[Dict[str, Any]]:
    global STAGE1_LAST_ERRORS

    models = [m for m in DEFAULT_STAGE1_MODELS if m]
    results: List[Dict[str, Any]] = []
    errors: Dict[str, str] = {}

    async def _try_once(m: str) -> Optional[Dict[str, Any]]:
        out = await _chat(m, _member_messages(m, user_prompt, contract_stack, stage="stage1"), temperature=0.3)
        out = (out or "").strip()
        if _looks_like_provider_id(out):
            out = ""
        if not out:
            return None
        ev = evaluate_contract_compliance(user_prompt, out, contract_stack, stage="stage1")
        return {"model": m, "response": out, "contract_eval": ev}

    async def run_one(m: str) -> Optional[Dict[str, Any]]:
        try:
            r = await _try_once(m)
            if r:
                return r
            if m.startswith("google/"):
                await asyncio.sleep(0.15)
                r2 = await _try_once(m)
                if r2:
                    return r2
            errors[m] = "Empty response"
            return None
        except Exception as e:
            errors[m] = f"{type(e).__name__}: {e}"
            if m.startswith("google/"):
                try:
                    await asyncio.sleep(0.15)
                    r2 = await _try_once(m)
                    if r2:
                        errors.pop(m, None)
                        return r2
                except Exception as e2:
                    errors[m] = f"{type(e2).__name__}: {e2}"
            return None

    done = await asyncio.gather(*[run_one(m) for m in models])

    # Always return one Stage-1 entry per configured model (A-D), even if a model fails.
    for m, item in zip(models, done):
        if item:
            results.append(item)
        else:
            results.append(
                {
                    "model": m,
                    "response": "(No response from model.)",
                    "contract_eval": {
                        "status": "FAIL",
                        "eligible": False,
                        "hard_fail_reasons": ["Empty response"],
                    },
                    "synthetic": True,
                    "synthetic_reason": "stage1_empty_fallback",
                }
            )

    STAGE1_LAST_ERRORS = dict(errors)

    real_count = sum(1 for r in results if not r.get("synthetic"))
    if real_count == 0 and errors:
        raise RuntimeError(f"Stage1 all failed: {errors}")

    return results


async def stage2_collect_rankings(
    user_prompt: str,
    stage1_results: List[Dict[str, Any]],
    contract_stack: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    global STAGE2_LAST_ERRORS

    models = _dedupe_preserve_order([m for m in DEFAULT_STAGE2_MODELS if m])
    labeled_blocks, label_to_model = _label_responses(stage1_results)
    errors: Dict[str, str] = {}

    labels = list(label_to_model.keys())
    labels_line = ", ".join(labels)
    example_line = _example_ranking(labels)

    rubric = (
        "You are reviewing multiple anonymous answers from different models.\n"
        "Goal: choose the answer a YC-level product team would actually ship.\n"
        "Primary criteria:\n"
        "1) Correctness / no hallucinations / respects missing info.\n"
        "2) Directly answers the user's request (or asks for required missing inputs).\n"
        "3) Actionability (specific steps, runnable commands, precise fixes).\n"
        "4) Truth-first discipline (no invented facts; explicitly notes uncertainty / missing inputs).\n"
        "\n"
        "Output format is STRICT (5 lines total; see system rules).\n"
        "Machine-readable last line must be exactly:\n"
        f"FINAL_RANKING: {example_line}\n"
        f"Valid labels: {labels_line}\n"
    )

    stage2_prompt = (
        f"USER PROMPT:\n{user_prompt}\n\n"
        f"{rubric}\n\n"
        f"ANONYMIZED RESPONSES:\n\n" + "\n\n".join(labeled_blocks)
    )

    async def _try_once(m: str, prompt: str, temp: float, stage: str = "stage2") -> str:
        out = await _chat(m, _member_messages(m, prompt, contract_stack, stage=stage), temperature=temp)
        return (out or "").strip()

    async def run_one(m: str, prompt_override: Optional[str] = None) -> Dict[str, Any]:
        out = ""
        format_fix_used = False
        format_fix_output = ""
        base_prompt = prompt_override or stage2_prompt

        def _canonical_default(order: List[str]) -> str:
            return "\n".join(
                [
                    "Response A: Strength: None; Flaw: Insufficient signal in text.",
                    "Response B: Strength: None; Flaw: Insufficient signal in text.",
                    "Response C: Strength: None; Flaw: Insufficient signal in text.",
                    "Response D: Strength: None; Flaw: Insufficient signal in text.",
                    "FINAL_RANKING: " + " > ".join(order),
                ]
            )

        def _partial_fallback(reason: str, raw: str = "") -> Dict[str, Any]:
            canonical = _canonical_default(labels[:])
            return {
                "model": m,
                "ranking": canonical,
                "parsed_ranking": labels[:],
                "raw_ranking": (raw or "").strip(),
                "format_fix_used": True,
                "format_fix_output": (format_fix_output or "").strip(),
                "coerced": True,
                "partial": True,
                "partial_reason": reason,
            }

        async def _call_with_google_retry(prompt: str, temp: float, stage: str = "stage2") -> str:
            o = await _try_once(m, prompt, temp, stage=stage)
            o = (o or "").strip()
            if _looks_like_provider_id(o):
                o = ""
            if (not o) and m.startswith("google/"):
                await asyncio.sleep(0.25)
                o = await _try_once(m, prompt, temp, stage=stage)
                o = (o or "").strip()
                if _looks_like_provider_id(o):
                    o = ""
            return o

        def _classify_quality(
            canonical_5: str,
            parsed_any: List[str],
            used_example: bool,
            responses_by_label: Dict[str, str],
        ) -> Tuple[bool, str]:
            # "partial" means: do NOT let it influence aggregation (low-information judge output).
            if not canonical_5:
                return True, "empty_canonical"
            lines = [ln.strip() for ln in canonical_5.splitlines() if ln.strip()]
            if len(lines) != 5:
                return True, "bad_line_count"

            critique_lines = lines[:4]

            # Enforce Strength/Flaw structure; otherwise treat as low-quality/partial.
            for ln in critique_lines:
                lnl = ln.lower()
                if ("strength:" not in lnl) or ("flaw:" not in lnl):
                    return True, "missing_strength_flaw"

            placeholder_n = sum(1 for ln in critique_lines if _critique_is_placeholder(ln))
            if placeholder_n >= 2:
                return True, "placeholder_critiques"
            if used_example and placeholder_n > 0:
                return True, "example_order_and_placeholder"
            # If original ranking signal was very partial (e.g., only 1 label), mark partial.
            if len(parsed_any) <= 1:
                return True, "weak_ranking_signal"

            # Evidence proxy: require >=3/4 critique lines to include at least one concrete token
            # that overlaps with the corresponding response text.
            try:
                min_lines = int((os.getenv("STAGE2_EVIDENCE_MIN_LINES") or "3").strip())
            except Exception:
                min_lines = 3

            ok_n = 0
            for i, letter in enumerate(["A", "B", "C", "D"]):
                label = f"Response {letter}"
                resp_txt = responses_by_label.get(label, "")
                crit_ln = critique_lines[i] if i < len(critique_lines) else ""
                if _evidence_ok(crit_ln, resp_txt):
                    ok_n += 1

            if ok_n < min_lines:
                return True, f"missing_evidence_{ok_n}_of_4"

            return False, ""

        def _acceptable(txt: str, responses_by_label: Dict[str, str]) -> Tuple[Optional[List[str]], str, bool, str]:
            """Return (parsed_full_order, canonical_5line, partial_flag, partial_reason)."""
            if not txt:
                return None, "", True, "empty"
            if _looks_like_provider_id(txt):
                return None, "", True, "provider_id"
            if _contains_process_narration(txt):
                return None, "", True, "process_narration"

            parsed_any = _parse_ranking_from_text(txt, allowed_labels=None)
            if not parsed_any:
                return None, "", True, "no_ranking_signal"

            # Complete ranking deterministically over known labels.
            keep: List[str] = []
            seen = set()
            for lab in parsed_any:
                if lab in labels and lab not in seen:
                    seen.add(lab)
                    keep.append(lab)
            parsed_full = keep + [lab for lab in labels if lab not in seen]
            if len(parsed_full) != len(labels) or set(parsed_full) != set(labels):
                return None, "", True, "bad_ranking_completion"

            canonical = _coerce_stage2_5line(txt, labels)
            if not canonical:
                return None, "", True, "cannot_canonicalize"

            used_example = ("FINAL_RANKING: " + " > ".join(parsed_full)).strip().endswith(example_line)
            partial_flag, partial_reason = _classify_quality(canonical, parsed_any, used_example=used_example, responses_by_label=responses_by_label)
            return parsed_full, canonical, partial_flag, partial_reason

        try:
            # Build responses_by_label for evidence checks
            responses_by_label: Dict[str, str] = {}
            for idx, r in enumerate(stage1_results):
                label = f"Response {chr(ord('A') + idx)}"
                responses_by_label[label] = (r.get("response") or "")

            # ===== Attempt 0: Normal judge prompt (low temp) =====
            out = await _call_with_google_retry(base_prompt, 0.1)
            parsed_ok, canonical_ok, partial_ok, partial_reason_ok = _acceptable(out, responses_by_label)
            if parsed_ok and (not partial_ok):
                return {
                    "model": m,
                    "ranking": canonical_ok,
                    "parsed_ranking": parsed_ok,
                    "raw_ranking": out,
                    "format_fix_used": False,
                    "format_fix_output": "",
                    "coerced": canonical_ok != (out or "").strip(),
                    "partial": False,
                    "partial_reason": "",
                }

            # If we got a syntactically valid 5-line output but it is low-signal (placeholders),
            # force one more try that requires concrete evidence from each response.
            if parsed_ok and partial_ok:
                evidence_wrapper = (
                    "OUTPUT EXACTLY 5 LINES. No headings. No markdown. No bullets. No blank lines.\n"
                    "No first-person. No narration.\n"
                    "Each critique line MUST include BOTH 'Strength:' and 'Flaw:' and MUST reference one concrete detail from that response (a short quoted phrase is OK).\n"
                    "Do NOT use 'Insufficient signal in text.' unless the response is empty/refuses.\n"
                    "Template:\n"
                    "Response A: Strength: <...>; Flaw: <...>\n"
                    "Response B: Strength: <...>; Flaw: <...>\n"
                    "Response C: Strength: <...>; Flaw: <...>\n"
                    "Response D: Strength: <...>; Flaw: <...>\n"
                    f"FINAL_RANKING: {example_line}\n"
                    "Return ONLY those 5 lines.\n\n"
                )
                out_ev = await _call_with_google_retry(evidence_wrapper + base_prompt, 0.2)
                parsed_ev, canonical_ev, partial_ev, partial_reason_ev = _acceptable(out_ev, responses_by_label)
                if parsed_ev:
                    return {
                        "model": m,
                        "ranking": canonical_ev,
                        "parsed_ranking": parsed_ev,
                        "raw_ranking": out_ev,
                        "format_fix_used": True,
                        "format_fix_output": out_ev,
                        "coerced": canonical_ev != (out_ev or "").strip(),
                        "partial": bool(partial_ev),
                        "partial_reason": partial_reason_ev if partial_ev else "",
                    }

                # Fall through to the existing strict re-judge attempts

            # ===== Attempt 1: STRICT RE-JUDGE (forces Strength/Flaw; forbids copying example) =====
            strict_wrapper = (
                "OUTPUT EXACTLY 5 LINES. No headings. No markdown. No bullets. No blank lines.\n"
                "No first-person. No narration.\n"
                "Each critique line must be ONE sentence and include BOTH:\n"
                "  Strength: <...>; Flaw: <...>\n"
                "Do NOT copy the example ordering; choose based on the content.\n"
                "Template:\n"
                "Response A: Strength: <...>; Flaw: <...>\n"
                "Response B: Strength: <...>; Flaw: <...>\n"
                "Response C: Strength: <...>; Flaw: <...>\n"
                "Response D: Strength: <...>; Flaw: <...>\n"
                f"FINAL_RANKING: {example_line}\n"
                "Return ONLY those 5 lines.\n\n"
            )
            format_fix_used = True
            out_fix = await _call_with_google_retry(strict_wrapper + base_prompt, 0.0)
            format_fix_output = out_fix

            parsed_fix, canonical_fix, partial_fix, partial_reason_fix = _acceptable(out_fix, responses_by_label)
            if parsed_fix:
                return {
                    "model": m,
                    "ranking": canonical_fix,
                    "parsed_ranking": parsed_fix,
                    "raw_ranking": out_fix,
                    "format_fix_used": True,
                    "format_fix_output": format_fix_output,
                    "coerced": canonical_fix != (out_fix or "").strip(),
                    "partial": bool(partial_fix),
                    "partial_reason": partial_reason_fix if partial_fix else "",
                }

            # ===== Attempt 2: Rewrite its own output into strict 5-line format =====
            rewrite_prompt = (
                "Rewrite the text below into EXACTLY 5 LINES using the required template.\n"
                "Rules:\n"
                "- No markdown, no headings, no extra lines.\n"
                "- No first-person, no narration.\n"
                "- Each critique line MUST include: 'Strength: ...; Flaw: ...' in one sentence.\n"
                "- Do NOT copy the example ordering unless it is truly correct.\n"
                "- If a critique is missing, write: 'Strength: None; Flaw: Insufficient signal in text.'\n"
                "Template:\n"
                "Response A: Strength: <...>; Flaw: <...>\n"
                "Response B: Strength: <...>; Flaw: <...>\n"
                "Response C: Strength: <...>; Flaw: <...>\n"
                "Response D: Strength: <...>; Flaw: <...>\n"
                f"FINAL_RANKING: {example_line}\n\n"
                "TEXT TO REWRITE:\n"
                + (out_fix or out or "")
            )
            out_rewrite = await _call_with_google_retry(rewrite_prompt, 0.0)

            parsed_rewrite, canonical_rw, partial_rw, partial_reason_rw = _acceptable(out_rewrite, responses_by_label)
            if parsed_rewrite:
                return {
                    "model": m,
                    "ranking": canonical_rw,
                    "parsed_ranking": parsed_rewrite,
                    "raw_ranking": out_rewrite,
                    "format_fix_used": True,
                    "format_fix_output": format_fix_output,
                    "coerced": canonical_rw != (out_rewrite or "").strip(),
                    "partial": bool(partial_rw),
                    "partial_reason": partial_reason_rw if partial_rw else "",
                }

            # ===== Last resort: one-line repair (accepted + canonicalized; ALWAYS partial) =====
            repair_prompt = (
                "Return ONLY one line in this exact format (no other text):\n"
                "FINAL_RANKING: <labels joined by ' > '>\n"
                "Rules:\n"
                f"- Use ONLY these labels: {labels_line}\n"
                "- Each label must appear EXACTLY ONCE.\n"
                "- Use ' > ' between labels.\n"
                "- Do NOT use the default A > B > C > D unless it is truly correct.\n"
            )
            out2 = await _call_with_google_retry(repair_prompt, 0.0, stage="stage2_repair")
            parsed2_any = _parse_ranking_from_text(out2, allowed_labels=None)
            # For one-line repair we only need a ranking signal; canonicalization will fill critiques.
            parsed2, canonical_repair, partial2, partial_reason2 = _acceptable(out2, responses_by_label) if out2 else (None, "", True, "empty")
            if parsed2 and canonical_repair:
                errors[m] = errors.get(m) or "repair_only_ranking (accepted; canonicalized; partial)"
                return {
                    "model": m,
                    "ranking": canonical_repair,
                    "parsed_ranking": parsed2,
                    "raw_ranking": out2,
                    "format_fix_used": True,
                    "format_fix_output": format_fix_output,
                    "coerced": True,
                    "partial": True,
                    "partial_reason": partial_reason2 or ("repair_only_ranking" if parsed2_any else "repair_empty"),
                }

            errors[m] = errors.get(m) or "stage2_failed_all_attempts"
            return _partial_fallback("stage2_failed_all_attempts", raw=(out_rewrite or out_fix or out or ""))

        except Exception as e:
            errors[m] = f"{type(e).__name__}: {e}"
            return _partial_fallback("stage2_exception_fallback", raw=str(e))

    results = await asyncio.gather(*[run_one(m) for m in models])
    # ===== Option B3: Consensus gate + adjudication (only when judges disagree) =====
    def _top1_votes(rs: List[Dict[str, Any]]) -> Tuple[Dict[str, int], int]:
        counts: Dict[str, int] = {}
        total = 0
        for x in rs or []:
            if x.get("synthetic") or x.get("partial"):
                continue
            pr = x.get("parsed_ranking") or []
            if not pr:
                continue
            top = pr[0]
            if top not in labels:
                continue
            counts[top] = counts.get(top, 0) + 1
            total += 1
        return counts, total

    if STAGE2_ADJUDICATE_ENABLED:
        vote_counts, vote_total = _top1_votes(results)
        # Require a minimum number of usable (non-partial) judges before adjudicating.
        if vote_total >= STAGE2_ADJUDICATE_MIN_NONPARTIAL and len(vote_counts) >= 2:
            top_label = max(vote_counts.items(), key=lambda kv: kv[1])[0]
            top_votes = vote_counts[top_label]

            # Default consensus rule: for 4+ judges require 3 votes; for 3 judges require 2.
            required = 3 if vote_total >= 4 else 2
            if STAGE2_ADJUDICATE_MIN_TOP1_VOTES > 0:
                required = STAGE2_ADJUDICATE_MIN_TOP1_VOTES

            if top_votes < required:
                # Build a compact disagreement note for the adjudicator.
                summary = ", ".join([f"{k}:{v}" for k, v in sorted(vote_counts.items(), key=lambda kv: (-kv[1], kv[0]))])
                adjudicator_prompt = (
                    "JUDGES DISAGREE. Act as the adjudicator to break the tie.\n"
                    "Use the same strict 5-line output format.\n"
                    "Pick the answer a YC-level product team would actually ship.\n"
                    "Truth-first: do not invent facts; reward answers that request missing inputs when needed.\n"
                    f"Current top-1 vote counts: {summary}\n\n"
                    + stage2_prompt
                )

                # Run a single adjudication pass (reuse Stage-2 judge logic).
                try:
                    adjudicator_model = STAGE2_ADJUDICATOR_MODEL
                    if adjudicator_model in set(models):
                        fallbacks = [x.strip() for x in (os.getenv("STAGE2_ADJUDICATOR_FALLBACKS") or "google/gemini-3-pro-preview,openai/gpt-4.1,anthropic/claude-haiku-3.5").split(",") if x.strip()]
                        for fm in fallbacks:
                            if fm not in set(models):
                                adjudicator_model = fm
                                break
                    adj = await run_one(adjudicator_model, prompt_override=adjudicator_prompt)
                    if isinstance(adj, dict):
                        adj["adjudicator"] = True
                        adj["model"] = adj.get("model") or adjudicator_model
                        if adj.get("model") in set(models):
                            adj["model"] = f"{adj.get('model')} (adjudicator)"
                        results.append(adj)
                except Exception as _e:
                    # If adjudication fails, keep the original results.
                    pass
    STAGE2_LAST_ERRORS = dict(errors)
    return results, label_to_model


def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str],
    contract_evals_by_model: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    rank_sums: Dict[str, float] = {}
    rank_counts: Dict[str, int] = {}

    contract_evals_by_model = contract_evals_by_model or {}

    disqualified_models: Dict[str, List[str]] = {}
    for m, ev in contract_evals_by_model.items():
        if ev and ev.get("eligible") is False:
            disqualified_models[m] = ev.get("hard_fail_reasons", []) or ["Hard FAIL"]

    for voter in stage2_results or []:
        if voter.get("synthetic") or voter.get("partial"):
            continue
        parsed = voter.get("parsed_ranking") or []
        if not parsed:
            continue

        ordered_models: List[str] = []
        for label in parsed:
            mid = label_to_model.get(label)
            if mid:
                ordered_models.append(mid)

        for i, mid in enumerate(ordered_models):
            if mid in disqualified_models:
                continue
            rank_sums[mid] = rank_sums.get(mid, 0.0) + float(i + 1)
            rank_counts[mid] = rank_counts.get(mid, 0) + 1

    aggregates: List[Dict[str, Any]] = []

    for mid, s in rank_sums.items():
        c = rank_counts.get(mid, 0)
        if c > 0:
            aggregates.append(
                {
                    "model": mid,
                    "average_rank": float(s) / float(c),
                    "rankings_count": int(c),
                    "disqualified": False,
                    "disqualify_reasons": [],
                }
            )

    for mid, reasons in disqualified_models.items():
        aggregates.append(
            {
                "model": mid,
                "average_rank": 9998.0,
                "rankings_count": int(rank_counts.get(mid, 0)),
                "disqualified": True,
                "disqualify_reasons": reasons,
            }
        )

    for label, model in (label_to_model or {}).items():
        if any(a["model"] == model for a in aggregates):
            continue
        disq = model in disqualified_models
        aggregates.append(
            {
                "model": model,
                "average_rank": 9999.0,
                "rankings_count": 0,
                "disqualified": disq,
                "disqualify_reasons": (contract_evals_by_model.get(model, {}) or {}).get("hard_fail_reasons", [])
                if disq
                else [],
            }
        )

    aggregates.sort(key=lambda x: (bool(x.get("disqualified")), float(x.get("average_rank", 9999.0))))
    return aggregates


async def stage3_synthesize_final(
    user_prompt: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str],
    aggregate_rankings: List[Dict[str, Any]],
    contract_stack: Optional[str] = None,
    contract_evals_by_model: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    chairman_model = CHAIRMAN_MODEL

    s1 = [
        {"model": r.get("model"), "response": r.get("response"), "contract_eval": r.get("contract_eval")}
        for r in stage1_results
    ]
    s2 = [
        {
            "model": r.get("model"),
            "ranking": r.get("ranking"),
            "parsed_ranking": r.get("parsed_ranking"),
            "synthetic": bool(r.get("synthetic")),
            "partial": bool(r.get("partial")),
            "partial_reason": r.get("partial_reason"),
        }
        for r in stage2_results
    ]

    base_chairman_prompt = (
        "You are the Chairman. Synthesize the best final answer for the user.\n"
        "Use Stage 2 critiques and the aggregate rankings to guide you.\n"
        "Do not claim traction or facts that are not present.\n\n"
        f"USER PROMPT:\n{user_prompt}\n\n"
        f"STAGE 1 OUTPUTS:\n{json.dumps(s1, ensure_ascii=False)}\n\n"
        f"STAGE 2 OUTPUTS:\n{json.dumps(s2, ensure_ascii=False)}\n\n"
        f"AGGREGATE RANKINGS:\n{json.dumps(aggregate_rankings, ensure_ascii=False)}\n"
    )

    def _truncate(s: str, max_chars: int) -> str:
        s = s or ""
        if len(s) <= max_chars:
            return s
        return s[: max_chars - 1] + "…"

    chairman_prompt = base_chairman_prompt

    # Optional long-context helper: OFF by default. When enabled, only triggers when the payload is large.
    if STAGE3_HELPER_ENABLED and STAGE3_HELPER_MODEL and len(base_chairman_prompt) > STAGE3_HELPER_TRIGGER_CHARS:
        helper_input = (
            "Prepare a compact briefing for the Chairman.\n"
            "Use ONLY the provided data. Do not invent facts.\n"
            "If something is missing or ambiguous, state it.\n\n"
            f"USER PROMPT:\n{user_prompt}\n\n"
            f"STAGE 1 OUTPUTS (JSON):\n{json.dumps(s1, ensure_ascii=False)}\n\n"
            f"STAGE 2 OUTPUTS (JSON):\n{json.dumps(s2, ensure_ascii=False)}\n\n"
            f"AGGREGATE RANKINGS (JSON):\n{json.dumps(aggregate_rankings, ensure_ascii=False)}\n"
        )

        helper_brief = ""
        try:
            helper_brief = await _chat(
                STAGE3_HELPER_MODEL,
                _member_messages(STAGE3_HELPER_MODEL, helper_input, contract_stack, stage="stage3_helper"),
                temperature=0.1,
            )
            helper_brief = (helper_brief or "").strip()
        except Exception:
            helper_brief = ""

        # If we got a briefing, shrink what we send to the Chairman.
        # We keep: user prompt, aggregate rankings, the helper briefing, and truncated Stage-1 answers.
        if helper_brief:
            top_models: List[str] = []
            for a in (aggregate_rankings or []):
                mid = a.get("model")
                if isinstance(mid, str) and mid:
                    top_models.append(mid)
            # Prefer the top-ranked 2 models (if present)
            top_models = top_models[:2]

            # Build a small set of candidate responses (full for top-2, truncated for others)
            responses_by_model: Dict[str, str] = {r.get("model"): (r.get("response") or "") for r in stage1_results}

            parts: List[str] = []
            parts.append("HELPER BRIEFING (from long-context model):\n" + helper_brief)
            parts.append("AGGREGATE RANKINGS:\n" + json.dumps(aggregate_rankings, ensure_ascii=False))
            parts.append("\nCANDIDATE RESPONSES (top-2 full, others truncated):")

            # Top-2 full
            for mid in top_models:
                if mid in responses_by_model:
                    parts.append(f"\nMODEL: {mid}\n" + responses_by_model[mid])

            # Others truncated (to preserve some grounding)
            for r in stage1_results:
                mid = r.get("model")
                if not isinstance(mid, str) or not mid or mid in top_models:
                    continue
                parts.append(f"\nMODEL: {mid}\n" + _truncate(r.get("response") or "", 4000))

            chairman_prompt = (
                "You are the Chairman. Synthesize the best final answer for the user.\n"
                "Use the helper briefing and rankings. Do not invent facts not supported by the provided text.\n\n"
                f"USER PROMPT:\n{user_prompt}\n\n" + "\n\n".join(parts)
            )

    try:
        out = await _chat(
            chairman_model,
            _chairman_messages(chairman_model, chairman_prompt, contract_stack),
            temperature=0.2,
        )

        out = (out or "").strip()
        ev = evaluate_contract_compliance(user_prompt, out, contract_stack, stage="stage3")
        if ev.get("status") == "FAIL":
            repair_prompt = (
                "Your previous draft violated hard contract constraints.\n"
                "Rewrite it to comply. Preserve meaning, but fix the violations.\n\n"
                f"USER PROMPT:\n{user_prompt}\n\n"
                f"BAD DRAFT:\n{out}\n\n"
                f"VIOLATIONS:\n{json.dumps(ev, ensure_ascii=False)}\n"
            )
            out2 = await _chat(
                chairman_model,
                _chairman_messages(chairman_model, repair_prompt, contract_stack),
                temperature=0.2,
            )
            out2 = (out2 or "").strip()
            if out2:
                out = out2
                ev = evaluate_contract_compliance(user_prompt, out, contract_stack, stage="stage3")

        return {"model": chairman_model, "response": out, "contract_eval": ev}
    except Exception:
        return {"model": chairman_model, "response": "", "contract_eval": {"status": "FAIL", "eligible": False}}


def now_iso() -> str:
    return datetime.utcnow().isoformat()

# ---------------------------------------------------------------------------
# Back-compat Stage 3 entrypoints
# main.py looks for one of: stage3_select_winner / stage3_choose_winner / ...
# Keep existing behavior by routing to stage3_synthesize_final.
# ---------------------------------------------------------------------------

async def stage3_select_winner(
    user_prompt: str,
    stage1_results,
    stage2_results,
    contract_stack=None,
    contracts=None,
    **_kwargs,
):
    # main.py passes both `contract_stack` and `contracts`; treat them as aliases.
    contract_stack = contract_stack or contracts

    # stage2_collect_rankings may return (results, label_to_model). Normalize.
    label_to_model = {}
    if isinstance(stage2_results, tuple) and len(stage2_results) == 2:
        stage2_results, label_to_model = stage2_results

    stage1_results = stage1_results or []
    stage2_results = stage2_results or []

    # If label_to_model wasn't provided, derive it from Stage 1 order.
    if not label_to_model:
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for i, r in enumerate(stage1_results):
            m = (r or {}).get("model") or ""
            label_to_model[f"Response {letters[i]}"] = m

    # Build contract evals map (used by aggregate rankings + chairman prompt).
    contract_evals_by_model = {}
    for r in stage1_results:
        m = (r or {}).get("model")
        if m:
            contract_evals_by_model[m] = (r or {}).get("contract_eval")

    aggregate_rankings = calculate_aggregate_rankings(
        stage2_results,
        label_to_model,
        contract_evals_by_model,
    )

    return await stage3_synthesize_final(
        user_prompt=user_prompt,
        stage1_results=stage1_results,
        stage2_results=stage2_results,
        label_to_model=label_to_model,
        aggregate_rankings=aggregate_rankings,
        contract_stack=contract_stack,
        contract_evals_by_model=contract_evals_by_model,
    )

# Aliases expected by main.py
stage3_choose_winner = stage3_select_winner
stage3_select_final  = stage3_select_winner
stage3_synthesize    = stage3_select_winner
stage3_run           = stage3_select_winner
stage3               = stage3_select_winner

