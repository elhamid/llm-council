from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, Any, Dict, List
import uuid
import re
import json
import asyncio

from .storage import (
    create_conversation,
    get_conversation,
    list_conversations,
    add_user_message,
    add_assistant_message,
    save_conversation,
    delete_conversation,
)

from .council import (
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
    calculate_aggregate_rankings,
    CHAIRMAN_MODEL,
)

from .roles import get_role_spec

app = FastAPI(title="LLM Council API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConversationCreateRequest(BaseModel):
    title: Optional[str] = None


class SendMessageRequest(BaseModel):
    content: str
    contract_stack: Optional[str] = None


def _derive_title_from_first_message(content: str, max_chars: int = 60) -> str:
    if not content:
        return "New conversation"

    line = next((ln.strip() for ln in content.splitlines() if ln.strip()), "").strip()
    if not line:
        return "New conversation"

    line = re.sub(r"[`*_>#]+", "", line)
    line = re.sub(r"\s+", " ", line).strip()

    words = line.split()
    if len(words) > 8:
        line = " ".join(words[:8]) + "…"

    return line[:max_chars].rstrip()


def _derive_title_from_chairman(stage3_text: str, max_chars: int = 60) -> Optional[str]:
    if not stage3_text:
        return None

    lines = [ln.strip() for ln in stage3_text.splitlines() if ln.strip()]
    if not lines:
        return None

    first = lines[0]
    if first.startswith("#"):
        first = re.sub(r"^#+\s*", "", first).strip()

    first = re.sub(r"[`*_>#]+", "", first)
    first = re.sub(r"\s+", " ", first).strip()

    if not first:
        return None

    words = first.split()
    if len(words) > 10:
        first = " ".join(words[:10]) + "…"

    if len(first) > max_chars:
        first = first[:max_chars].rstrip() + "…"

    return first


def _auto_title_if_needed(conversation_id: str, first_user_message: str) -> None:
    try:
        convo = get_conversation(conversation_id)
        if not convo:
            return
        current_title = (convo.get("title") or "").strip()
        if current_title.lower() != "new conversation":
            return
        convo["title"] = _derive_title_from_first_message(first_user_message)
        save_conversation(conversation_id, convo)
    except Exception:
        return


def _auto_title_from_chairman_if_needed(conversation_id: str, first_user_message: str, stage3_text: str) -> None:
    try:
        convo = get_conversation(conversation_id)
        if not convo:
            return

        current_title = (convo.get("title") or "").strip()
        derived_first = _derive_title_from_first_message(first_user_message)

        should_replace = (
            current_title.lower() == "new conversation"
            or current_title == derived_first
        )
        if not should_replace:
            return

        new_title = _derive_title_from_chairman(stage3_text)
        if not new_title:
            return

        convo["title"] = new_title
        convo["title_source"] = "chairman"
        save_conversation(conversation_id, convo)
    except Exception:
        return


@app.get("/")
async def root():
    return {"status": "ok", "service": "LLM Council API"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/api/conversations")
async def get_conversations():
    return list_conversations()


@app.post("/api/conversations")
async def create_new_conversation(request: ConversationCreateRequest):
    conversation_id = str(uuid.uuid4())
    title = request.title or "New conversation"
    return create_conversation(conversation_id, title)


@app.get("/api/conversations/{conversation_id}")
async def get_one_conversation(conversation_id: str):
    conversation = get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.delete("/api/conversations/{conversation_id}")
async def delete_one_conversation(conversation_id: str):
    ok = delete_conversation(conversation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted", "id": conversation_id}


async def _run_council_pipeline(conversation_id: str, request: SendMessageRequest) -> Dict[str, Any]:
    add_user_message(conversation_id, request.content)
    _auto_title_if_needed(conversation_id, request.content)

    stage1_results: List[Dict[str, Any]] = []
    stage2_results: List[Dict[str, Any]] = []
    stage3_result: Dict[str, Any] = {"model": CHAIRMAN_MODEL, "response": ""}

    label_to_model: Dict[str, str] = {}
    aggregate_rankings: List[Dict[str, Any]] = []

    errors: Dict[str, str] = {}
    contract_stack = request.contract_stack

    try:
        stage1_results = await stage1_collect_responses(request.content, contract_stack=contract_stack)
        if not stage1_results:
            errors["stage1"] = "All models failed to respond in Stage 1"
    except Exception as e:
        errors["stage1"] = f"{type(e).__name__}: {e}"

    if stage1_results:
        try:
            stage2_results, label_to_model = await stage2_collect_rankings(
                request.content, stage1_results, contract_stack=contract_stack
            )
            aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
        except Exception as e:
            errors["stage2"] = f"{type(e).__name__}: {e}"

    if stage1_results:
        try:
            stage3_result = await stage3_synthesize_final(
                request.content,
                stage1_results,
                stage2_results,
                label_to_model,
                aggregate_rankings,
                contract_stack=contract_stack,
            )
        except Exception as e:
            errors["stage3"] = f"{type(e).__name__}: {e}"
            stage3_result = {"model": CHAIRMAN_MODEL, "response": ""}

    model_roles = {}
    try:
        for r in stage1_results:
            mid = r.get("model")
            if mid:
                model_roles[mid] = get_role_spec(mid).role
        model_roles[CHAIRMAN_MODEL] = get_role_spec(CHAIRMAN_MODEL).role
    except Exception:
        pass

    meta: Dict[str, Any] = {
        "contract_stack": contract_stack,
        "aggregate_rankings": aggregate_rankings,
        "label_to_model": label_to_model,
        "model_roles": model_roles,
    }
    if errors:
        meta["errors"] = errors

    add_assistant_message(conversation_id, stage1_results, stage2_results, stage3_result, meta=meta)

    stage3_text = (stage3_result or {}).get("response") or ""
    _auto_title_from_chairman_if_needed(conversation_id, request.content, stage3_text)

    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "meta": meta,
        "metadata": meta,
    }


def _sse(data: Dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, request: SendMessageRequest):
    if not get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await _run_council_pipeline(conversation_id, request)


@app.post("/api/conversations/{conversation_id}/messages/stream")
async def send_message_stream(conversation_id: str, request: SendMessageRequest):
    if not get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    async def gen():
        try:
            add_user_message(conversation_id, request.content)
            _auto_title_if_needed(conversation_id, request.content)

            contract_stack = request.contract_stack

            yield _sse({"type": "stage1_start"})
            await asyncio.sleep(0)

            stage1_results = await stage1_collect_responses(request.content, contract_stack=contract_stack)
            if not stage1_results:
                yield _sse({"type": "error", "message": "All models failed to respond in Stage 1"})
                return

            yield _sse({"type": "stage1_complete", "data": stage1_results})
            await asyncio.sleep(0)

            yield _sse({"type": "stage2_start"})
            await asyncio.sleep(0)

            stage2_results, label_to_model = await stage2_collect_rankings(
                request.content, stage1_results, contract_stack=contract_stack
            )
            aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)

            model_roles = {}
            try:
                for r in stage1_results:
                    mid = r.get("model")
                    if mid:
                        model_roles[mid] = get_role_spec(mid).role
                model_roles[CHAIRMAN_MODEL] = get_role_spec(CHAIRMAN_MODEL).role
            except Exception:
                pass

            meta: Dict[str, Any] = {
                "contract_stack": contract_stack,
                "aggregate_rankings": aggregate_rankings,
                "label_to_model": label_to_model,
                "model_roles": model_roles,
            }

            yield _sse({"type": "stage2_complete", "data": stage2_results, "metadata": meta})
            await asyncio.sleep(0)

            yield _sse({"type": "stage3_start"})
            await asyncio.sleep(0)

            stage3_result = await stage3_synthesize_final(
                request.content,
                stage1_results,
                stage2_results,
                label_to_model,
                aggregate_rankings,
                contract_stack=contract_stack,
            )

            yield _sse({"type": "stage3_complete", "data": stage3_result})
            await asyncio.sleep(0)

            add_assistant_message(conversation_id, stage1_results, stage2_results, stage3_result, meta=meta)

            stage3_text = (stage3_result or {}).get("response") or ""
            _auto_title_from_chairman_if_needed(conversation_id, request.content, stage3_text)

            yield _sse({"type": "title_complete"})
            yield _sse({"type": "complete"})
        except Exception as e:
            yield _sse({"type": "error", "message": f"{type(e).__name__}: {e}"})

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream_alias(conversation_id: str, request: SendMessageRequest):
    return await send_message_stream(conversation_id, request)


@app.post("/api/conversations/{conversation_id}/messages/stages")
async def send_message_stages(conversation_id: str, request: SendMessageRequest):
    if not get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await _run_council_pipeline(conversation_id, request)
