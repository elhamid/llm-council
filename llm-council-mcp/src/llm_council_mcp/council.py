from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from .contracts import (
    build_chairman_contract_system_messages,
    build_contract_system_messages,
    evaluate_contract_compliance,
)
from .prompts import (
    STAGE2_SYSTEM_PROMPT,
    STAGE2_REPAIR_SYSTEM_PROMPT,
    build_chairman_prompt,
    build_stage2_rubric,
    example_ranking,
    build_stage2_one_line_repair_prompt,
    build_stage2_rewrite_prompt,
    build_stage2_strict_rejudge_prompt,
)
from .roles import get_role_spec


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
    """Build messages for council members. Stage controls which system prompts are included."""
    if stage in ("stage2", "stage2_repair"):
        system_msgs: List[Dict[str, str]] = []
    else:
        system_msgs = build_contract_system_messages(contract_stack)

    if stage not in ("stage2", "stage2_repair"):
        role_spec = get_role_spec(model)
        system_msgs.append({"role": "system", "content": role_spec.system_prompt})

    if stage == "stage2":
        system_msgs.append({"role": "system", "content": STAGE2_SYSTEM_PROMPT})
    elif stage == "stage2_repair":
        system_msgs.append({"role": "system", "content": STAGE2_REPAIR_SYSTEM_PROMPT})

    return system_msgs + [{"role": "user", "content": user_prompt}]


def _chairman_messages(model: str, chairman_prompt: str, contract_stack: Optional[str]) -> List[Dict[str, str]]:
    """Build messages for the chairman (Stage 3)."""
    role_spec = get_role_spec(model)
    system_msgs = build_chairman_contract_system_messages(contract_stack)
    system_msgs.append({"role": "system", "content": role_spec.system_prompt})
    return system_msgs + [{"role": "user", "content": chairman_prompt}]


async def _chat(model: str, messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
    client = _client()
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

    if _looks_like_provider_id(text):
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
            text = ""

    return (text or "").strip()


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
        "id", "request_id", "generation_id", "gen_id", "model", "provider",
        "usage", "created", "created_at", "timestamp", "object",
        "finish_reason", "system_fingerprint",
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


def _contains_process_narration(text: str) -> bool:
    """Detect self-narration in model output (e.g., 'I am currently analyzing...')."""
    raw = _normalize_ws(text or "").lower()
    return bool(
        re.search(
            r"\b("
            r"i am currently|i'm currently|i am now|i'm now|"
            r"i will now|i am going to|i'm going to|"
            r"i have just|i've just|i just|"
            r"i have finished|i've finished|just finished|"
            r"initiating the analysis|my focus is|the plan is|"
            r"finalizing the strategy|processing the parameters|"
            r"assessing the conundrum|interpreting the context"
            r")\b",
            raw,
        )
    )


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


def _strip_wrappers(text: str) -> str:
    """Remove common wrappers (code fences, stray backticks/quotes) from model output."""
    t = (text or "").strip()
    if not t:
        return ""
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    t = t.strip().strip("`").strip().strip('"').strip().strip("'").strip()
    return t


def _extract_final_ranking_line(text: str) -> str:
    raw = _strip_wrappers((text or "").strip())
    if not raw:
        return ""
    lines = [_normalize_ws(ln) for ln in raw.splitlines() if ln and ln.strip()]
    for ln in reversed(lines):
        m = re.search(r"\bFINAL_RANKING\s*:\s*", ln, flags=re.I)
        if m:
            return ln[m.start():].strip()
    return ""


def _extract_fuzzy_ranking_chain(text: str) -> str:
    raw = _strip_wrappers((text or "").strip())
    if not raw:
        return ""
    raw = _normalize_ws(raw)
    raw = (
        raw.replace("\u2192", ">")
        .replace("\u21d2", ">")
        .replace("->", ">")
        .replace("\uff1e", ">")
        .replace("\u203a", ">")
        .replace("\u00bb", ">")
    )
    pat_full = re.compile(r"(Response\s*[A-Z](?:\s*>\s*Response\s*[A-Z])+)", flags=re.I)
    matches = pat_full.findall(raw)
    if matches:
        return matches[-1].strip()
    pat_letters = re.compile(r"\b([A-Z](?:\s*>\s*[A-Z]){2,})\b", flags=re.I)
    m = pat_letters.search(raw)
    if m:
        chain = m.group(1)
        parts = [p.strip() for p in chain.split(">") if p.strip()]
        if parts:
            return " > ".join([f"Response {p.upper()}" for p in parts])
    return ""


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
    tail = tail.replace("\u2192", ">").replace("\u21d2", ">").replace("->", ">")
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


def _coerce_stage2_5line(text: str, labels: List[str]) -> str:
    """Coerce judge output into strict N+1-line format (N critiques + FINAL_RANKING)."""
    if not text:
        return ""

    raw_lines = [ln for ln in (text or "").splitlines() if ln and ln.strip()]
    crit: Dict[str, str] = {}

    for ln in raw_lines:
        nln = _normalize_ws(ln)
        m = re.match(
            r"^\s*(?:[-*]\s*)?(?:Response\s*)?([A-Z])\s*(?:[:\-\u2013\u2014\.]|\))\s*(.+)$",
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

    def line_for(label: str) -> str:
        if label in crit:
            return crit[label]
        return f"{label}: Strength: None; Flaw: Insufficient signal in text."

    return "\n".join([*(line_for(label) for label in labels), final_line])


def _dedupe_preserve_order(items: list[str]) -> list[str]:
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


async def stage1_collect_responses(
    user_prompt: str,
    stage1_models: List[str],
    contract_stack: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], Dict[str, str]]:
    models = [m for m in stage1_models if m]
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

    real_count = sum(1 for r in results if not r.get("synthetic"))
    if real_count == 0 and errors:
        raise RuntimeError(f"Stage1 all failed: {errors}")

    return results, errors


async def stage2_collect_rankings(
    user_prompt: str,
    stage1_results: List[Dict[str, Any]],
    stage2_models: List[str],
    contract_stack: Optional[str] = None,
    depth: str = "standard",
) -> Tuple[List[Dict[str, Any]], Dict[str, str], Dict[str, str]]:
    models = _dedupe_preserve_order([m for m in stage2_models if m])
    labeled_blocks, label_to_model = _label_responses(stage1_results)
    errors: Dict[str, str] = {}

    labels = list(label_to_model.keys())
    example_line = example_ranking(labels)

    rubric = build_stage2_rubric(labels, example_line)
    stage2_prompt = (
        f"USER PROMPT:\n{user_prompt}\n\n"
        f"{rubric}\n\n"
        f"ANONYMIZED RESPONSES:\n\n" + "\n\n".join(labeled_blocks)
    )

    max_attempts = 2 if depth == "quick" else 4

    async def _try_once(m: str, prompt: str, temp: float, stage: str = "stage2") -> str:
        out = await _chat(m, _member_messages(m, prompt, contract_stack, stage=stage), temperature=temp)
        return (out or "").strip()

    async def _call_with_google_retry(m: str, prompt: str, temp: float, stage: str = "stage2") -> str:
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

    def _acceptable(txt: str) -> Tuple[Optional[List[str]], str, bool, str]:
        if not txt:
            return None, "", True, "empty"
        if _looks_like_provider_id(txt):
            return None, "", True, "provider_id"
        if _contains_process_narration(txt):
            return None, "", True, "process_narration"

        parsed_any = _parse_ranking_from_text(txt, allowed_labels=None)
        if not parsed_any:
            return None, "", True, "no_ranking_signal"

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

        lines = [ln.strip() for ln in canonical.splitlines() if ln.strip()]
        if len(lines) != (len(labels) + 1):
            return parsed_full, canonical, True, "bad_line_count"

        partial = False
        reason = ""
        for ln in lines[: len(labels)]:
            lnl = ln.lower()
            if ("strength:" not in lnl) or ("flaw:" not in lnl):
                partial = True
                reason = "missing_strength_flaw"
                break

        return parsed_full, canonical, partial, reason

    async def run_one(m: str) -> Dict[str, Any]:
        out = ""
        format_fix_used = False
        format_fix_output = ""
        try:
            prompts: List[Tuple[str, float, str]] = [
                (stage2_prompt, 0.1, "stage2"),
                (build_stage2_strict_rejudge_prompt(labels, example_line, stage2_prompt), 0.0, "stage2"),
                (build_stage2_rewrite_prompt(labels, example_line, ""), 0.0, "stage2"),
                (build_stage2_one_line_repair_prompt(labels), 0.0, "stage2_repair"),
            ]

            attempted = 0
            for idx, (prompt, temp, stage) in enumerate(prompts):
                if attempted >= max_attempts:
                    break
                attempted += 1

                if idx == 2:
                    prompt = prompt + (format_fix_output or out)

                out = await _call_with_google_retry(m, prompt, temp, stage=stage)
                if idx > 0:
                    format_fix_used = True
                    format_fix_output = out

                parsed_ok, canonical_ok, partial_ok, partial_reason_ok = _acceptable(out)
                if parsed_ok:
                    return {
                        "model": m,
                        "ranking": canonical_ok,
                        "parsed_ranking": parsed_ok,
                        "raw_ranking": out,
                        "format_fix_used": format_fix_used,
                        "format_fix_output": format_fix_output,
                        "coerced": canonical_ok != (out or "").strip(),
                        "partial": bool(partial_ok),
                        "partial_reason": partial_reason_ok if partial_ok else "",
                    }

            errors[m] = errors.get(m) or "stage2_failed_all_attempts"
            return {
                "model": m,
                "ranking": "\n".join(
                    [*[f"{label}: Strength: None; Flaw: Insufficient signal in text." for label in labels], "FINAL_RANKING: " + " > ".join(labels)]
                ),
                "parsed_ranking": labels[:],
                "raw_ranking": out,
                "format_fix_used": True,
                "format_fix_output": format_fix_output,
                "coerced": True,
                "partial": True,
                "partial_reason": "stage2_failed_all_attempts",
            }
        except Exception as e:
            errors[m] = f"{type(e).__name__}: {e}"
            return {
                "model": m,
                "ranking": "",
                "parsed_ranking": labels[:],
                "raw_ranking": str(e),
                "format_fix_used": True,
                "format_fix_output": "",
                "coerced": True,
                "partial": True,
                "partial_reason": "stage2_exception_fallback",
            }

    results = await asyncio.gather(*[run_one(m) for m in models])

    if depth == "thorough":
        vote_counts: Dict[str, int] = {}
        vote_total = 0
        for x in results:
            if x.get("synthetic") or x.get("partial"):
                continue
            pr = x.get("parsed_ranking") or []
            if not pr:
                continue
            top = pr[0]
            if top not in labels:
                continue
            vote_counts[top] = vote_counts.get(top, 0) + 1
            vote_total += 1

        if vote_total >= 3 and len(vote_counts) >= 2:
            top_label = max(vote_counts.items(), key=lambda kv: kv[1])[0]
            top_votes = vote_counts[top_label]
            required = 3 if vote_total >= 4 else 2
            if top_votes < required:
                summary = ", ".join([f"{k}:{v}" for k, v in sorted(vote_counts.items(), key=lambda kv: (-kv[1], kv[0]))])
                adjudicator_prompt = (
                    "JUDGES DISAGREE. Act as the adjudicator to break the tie.\n"
                    "Use the same strict 5-line output format.\n"
                    "Pick the answer a product team would actually ship.\n"
                    "Truth-first: do not invent facts; reward answers that request missing inputs when needed.\n"
                    f"Current top-1 vote counts: {summary}\n\n"
                    + stage2_prompt
                )
                adjudicator_model = "anthropic/claude-opus-4.5"
                adj = await run_one(adjudicator_model)
                adj["adjudicator"] = True
                adj["adjudicator_prompt"] = adjudicator_prompt
                if adj.get("model") in set(models):
                    adj["model"] = f"{adj.get('model')} (adjudicator)"
                results.append(adj)

    return results, label_to_model, errors


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
    chairman_model: str,
    contract_stack: Optional[str] = None,
    contract_evals_by_model: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
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

    chairman_prompt = build_chairman_prompt(user_prompt, s1, s2, aggregate_rankings)

    try:
        out = await _chat(
            chairman_model,
            _chairman_messages(chairman_model, chairman_prompt, contract_stack),
            temperature=0.2,
        )
        out = (out or "").strip()
        ev = evaluate_contract_compliance(user_prompt, out, contract_stack, stage="stage3")
        return {"model": chairman_model, "response": out, "contract_eval": ev}
    except Exception:
        return {"model": chairman_model, "response": "", "contract_eval": {"status": "FAIL", "eligible": False}}


async def run_deliberation(
    prompt: str,
    stage1_models: list[str] | None = None,
    stage2_models: list[str] | None = None,
    chairman_model: str | None = None,
    contract_stack: str | None = None,
    depth: str = "standard",
    timeout: int = 300,
    progress_cb: Any = None,
    info_cb: Any = None,
) -> dict[str, Any]:
    """
    Run a full 3-stage LLM Council deliberation.

    Returns a dict with keys: answer, stage1_responses, stage2_rankings,
    aggregate_rankings, label_to_model, errors, timing.
    """
    try:
        started = datetime.utcnow()
        s1_models = list(stage1_models or [])
        s2_models = list(stage2_models or [])
        if not s1_models:
            raise RuntimeError("No stage1 models configured")
        if not s2_models:
            raise RuntimeError("No stage2 models configured")
        if not chairman_model:
            raise RuntimeError("No chairman model configured")

        if depth == "quick":
            s1_models = s1_models[:3]
            s2_models = s2_models[:3]
        elif depth == "standard":
            s1_models = s1_models[:4]
            s2_models = s2_models[:4]

        stage_timeout = max(30, int(timeout / 3))
        if progress_cb:
            await progress_cb(0, 3)
        if info_cb:
            await info_cb(f"Stage 1: Querying {len(s1_models)} models in parallel...")

        stage1_results, stage1_errors = await asyncio.wait_for(
            stage1_collect_responses(prompt, s1_models, contract_stack=contract_stack),
            timeout=stage_timeout,
        )
        if progress_cb:
            await progress_cb(1, 3)
        if info_cb:
            await info_cb(f"Stage 2: Anonymized peer review with {len(s2_models)} judges...")

        stage2_results, label_to_model, stage2_errors = await asyncio.wait_for(
            stage2_collect_rankings(
                prompt,
                stage1_results,
                s2_models,
                contract_stack=contract_stack,
                depth=depth,
            ),
            timeout=stage_timeout,
        )

        contract_evals_by_model = {
            r.get("model", ""): (r.get("contract_eval") or {})
            for r in stage1_results
            if r.get("model")
        }
        aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model, contract_evals_by_model)
        if progress_cb:
            await progress_cb(2, 3)
        if info_cb:
            await info_cb("Stage 3: Chairman synthesizing final answer...")

        stage3_result = await asyncio.wait_for(
            stage3_synthesize_final(
                user_prompt=prompt,
                stage1_results=stage1_results,
                stage2_results=stage2_results,
                label_to_model=label_to_model,
                aggregate_rankings=aggregate_rankings,
                chairman_model=chairman_model,
                contract_stack=contract_stack,
                contract_evals_by_model=contract_evals_by_model,
            ),
            timeout=stage_timeout,
        )
        if progress_cb:
            await progress_cb(3, 3)

        duration_seconds = (datetime.utcnow() - started).total_seconds()
        return {
            "answer": stage3_result.get("response", ""),
            "stage1_responses": stage1_results,
            "stage2_rankings": stage2_results,
            "aggregate_rankings": aggregate_rankings,
            "label_to_model": label_to_model,
            "errors": {
                "stage1": stage1_errors,
                "stage2": stage2_errors,
            },
            "timing": {
                "started_at": started.isoformat(),
                "duration_seconds": duration_seconds,
            },
        }
    except Exception as e:
        return {
            "error": str(e),
            "error_type": type(e).__name__,
            "answer": "",
            "stage1_responses": [],
            "stage2_rankings": [],
            "aggregate_rankings": [],
            "label_to_model": {},
            "errors": {},
            "timing": {},
        }
