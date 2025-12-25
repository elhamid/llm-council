import asyncio
import inspect
import logging
import os
import json
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.config import get_config
from backend.roles import get_role_spec
from backend.storage import (
    add_assistant_message,
    add_user_message,
    create_conversation,
    get_conversation,
    list_conversations,
    delete_conversation,
    save_conversation,
)
import backend.council as council


def _pick(*names):
    """Return the first callable attribute in `backend.council` matching any of the given names."""
    for n in names:
        fn = getattr(council, n, None)
        if callable(fn):
            return fn
    return None


async def _maybe_await(x):
    if asyncio.iscoroutine(x):
        return await x
    return x

def _extract_title_from_stage3_response(resp: str):
    if not resp:
        return None

    def _clean(line: str) -> str:
        t = (line or "").strip()
        if not t:
            return ""
        if t.startswith("#"):
            t = t.lstrip("#").strip()
        if t.startswith("**") and t.endswith("**") and len(t) >= 4:
            t = t[2:-2].strip()
        if t.endswith(":"):
            t = t[:-1].strip()
        return t

    for line in resp.splitlines():
        raw = (line or "").strip()
        if raw.startswith("#"):
            t = _clean(raw)
            if t and t.lower() not in ("synthesis", "chairman's synthesis", "chairman’s synthesis"):
                return t[:120]

    for line in resp.splitlines():
        t = _clean(line)
        if not t:
            continue
        if t.lower() in ("synthesis", "chairman's synthesis", "chairman’s synthesis"):
            continue
        return t[:120]

    return None


def _invoke(func, args=(), kwargs=None):
    """Call `func` safely across signature changes: trims positional args and filters kwargs."""
    if kwargs is None:
        kwargs = {}
    try:
        sig = inspect.signature(func)
        params = list(sig.parameters.values())

        has_var_pos = any(p.kind == p.VAR_POSITIONAL for p in params)
        if has_var_pos:
            pass_args = args
        else:
            max_pos = sum(
                1 for p in params
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            )
            pass_args = args[:max_pos]

        has_var_kw = any(p.kind == p.VAR_KEYWORD for p in params)
        if has_var_kw:
            pass_kwargs = kwargs
        else:
            allowed = set(
                p.name for p in params
                if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
            )
            pass_kwargs = {k: v for k, v in kwargs.items() if k in allowed}

        return func(*pass_args, **pass_kwargs)
    except Exception:
        # If signature introspection fails, fall back to direct call.
        return func(*args, **(kwargs or {}))


# Back-compat constants/diagnostics (may not exist in all forks)
CHAIRMAN_MODEL = getattr(council, "CHAIRMAN_MODEL", "")
STAGE1_LAST_ERRORS = getattr(council, "STAGE1_LAST_ERRORS", {})
STAGE2_LAST_ERRORS = getattr(council, "STAGE2_LAST_ERRORS", {})

log = logging.getLogger("llm-council")


# -------------------------------
# App + config
# -------------------------------

app = FastAPI(title="llm-council", version="0.1.0")

cfg = get_config()
logging.basicConfig(level=getattr(logging, (cfg.log_level or "INFO").upper(), logging.INFO))

# CORS (public beta defaults; tighten for deployment)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.cors_allow_origins,
    allow_credentials=cfg.cors_allow_credentials,
    allow_methods=cfg.cors_allow_methods,
    allow_headers=cfg.cors_allow_headers,
)


# -------------------------------
# Helpers: auth, limits
# -------------------------------

class _AuthMiddleware:
    def __init__(self, app: FastAPI):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        if not cfg.auth_enabled:
            return await self.app(scope, receive, send)

        # Simple bearer token (opt-in)
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers") or []}
        auth = headers.get("authorization", "")
        expected = (cfg.api_token or "").strip()
        if expected:
            if not auth.startswith("Bearer ") or auth.split(" ", 1)[1].strip() != expected:
                from starlette.responses import JSONResponse

                return await JSONResponse({"detail": "Unauthorized"}, status_code=401)(scope, receive, send)

        return await self.app(scope, receive, send)


class _MaxBodyMiddleware:
    def __init__(self, app: FastAPI):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # Enforce max request size (best-effort)
        max_bytes = int(getattr(cfg, "max_request_bytes", 0) or 0)
        if max_bytes <= 0:
            return await self.app(scope, receive, send)

        body = b""
        sent_too_large = False

        async def _receive():
            nonlocal body, sent_too_large
            if sent_too_large:
                # Downstream should stop reading.
                return {"type": "http.disconnect"}


            message = await receive()
            if message.get("type") == "http.request":
                body += message.get("body", b"")
                if len(body) > max_bytes:
                    sent_too_large = True
                    from starlette.responses import JSONResponse

                    # Emit the response immediately.
                    await JSONResponse({"detail": "Request too large"}, status_code=413)(scope, receive, send)

            return message

        return await self.app(scope, _receive, send)


def json_dumps_bytes(obj) -> bytes:
    import json

    return json.dumps(obj).encode("utf-8")


# Apply simple middlewares
app.add_middleware(_AuthMiddleware)
app.add_middleware(_MaxBodyMiddleware)


# -------------------------------
# Models
# -------------------------------

class CreateConversationPayload(BaseModel):
    title: str = ""
    tags: list[str] = []


class SendMessagePayload(BaseModel):
    content: str
    role: Optional[str] = None
    contract_stack: Optional[list[dict]] = None


# -------------------------------
# Routes
# -------------------------------

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "0.1.0"}


@app.post("/api/conversations")
async def api_create_conversation(payload: CreateConversationPayload):
    convo = create_conversation(payload.title or "", tags=payload.tags or [])
    return convo


@app.get("/api/conversations")
async def api_list_conversations(limit: int = 50):
    return list_conversations(limit=limit)




@app.delete("/api/conversations/{conversation_id}")
async def api_delete_conversation(conversation_id: str):
    ok = delete_conversation(conversation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True, "id": conversation_id}
@app.get("/api/conversations/{conversation_id}")
async def api_get_conversation(conversation_id: str):
    convo = get_conversation(conversation_id)
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return convo


@app.post("/api/conversations/{conversation_id}/messages")
async def api_send_message(conversation_id: str, payload: SendMessagePayload, request: Request):
    convo = get_conversation(conversation_id)
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # store user message
    add_user_message(conversation_id, payload.content)

    # role selection
    role_spec = get_role_spec(payload.role or "default")

    contract_stack = payload.contract_stack or []

    # OpenRouter API key is required for any model call
    accept = (request.headers.get("accept") or "").lower()
    wants_stream = "text/event-stream" in accept
    has_key = bool((cfg.openrouter_api_key or "").strip() or (os.getenv("OPENROUTER_API_KEY") or "").strip())

    errors: list[str] = []

    # SSE stream path (only when client explicitly requests it)
    if wants_stream:

        async def _event_stream():
            def _sse(obj) -> str:
                return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

            if not has_key:
                yield _sse({"type": "error", "message": "Missing OPENROUTER_API_KEY"})
                yield _sse({"type": "complete"})
                return

            try:
                yield _sse({"type": "stage1_start"})
                stage1_fn = _pick("stage1_collect_responses","stage1_collect","collect_stage1_responses","stage1")
                if not stage1_fn:
                    raise RuntimeError("Stage1 function not found in backend.council")
                stage1_results = await _maybe_await(_invoke(stage1_fn, args=(payload.content,), kwargs={"system": role_spec.system, "contract_stack": contract_stack, "contracts": contract_stack}))
                yield _sse({"type": "stage1_complete", "data": stage1_results})

                yield _sse({"type": "stage2_start"})
                stage2_fn = _pick("stage2_collect_rankings","stage2_collect","collect_stage2_rankings","stage2")
                if not stage2_fn:
                    raise RuntimeError("Stage2 function not found in backend.council")
                stage2_results = await _maybe_await(_invoke(stage2_fn, args=(payload.content, stage1_results), kwargs={"contract_stack": contract_stack, "contracts": contract_stack}))
                if isinstance(stage2_results, tuple) and len(stage2_results) == 2:
                    stage2_results, _ = stage2_results
                agg_fn = _pick("aggregate_stage2_rankings", "aggregate_rankings")
                aggregate_rankings = _invoke(agg_fn, args=(stage2_results,), kwargs={}) if agg_fn else None

                # Build meta (same keys as JSON path)
                label_to_model = {}
                for idx, r in enumerate(stage1_results or []):
                    label = f"Response {chr(65 + idx)}"
                    mid = (r or {}).get("model")
                    if mid:
                        label_to_model[label] = mid
                model_roles = {}
                try:
                    for r in stage1_results or []:
                        mid = (r or {}).get("model")
                        if mid:
                            model_roles[mid] = get_role_spec(mid).role
                    model_roles[CHAIRMAN_MODEL] = get_role_spec(CHAIRMAN_MODEL).role
                except Exception:
                    pass
                meta = {
                    "contract_stack": contract_stack,
                    "aggregate_rankings": aggregate_rankings,
                    "label_to_model": label_to_model,
                    "model_roles": model_roles,
                    "contract_gate": (_invoke(_pick("build_contract_gate_summary", "contract_gate_summary"), args=(stage1_results, label_to_model), kwargs={}) if _pick("build_contract_gate_summary", "contract_gate_summary") else None),
                    "stage1_last_errors": dict(STAGE1_LAST_ERRORS or {}),
                    "stage2_last_errors": dict(STAGE2_LAST_ERRORS or {}),
                }
                yield _sse({"type": "stage2_complete", "data": stage2_results, "metadata": meta})

                yield _sse({"type": "stage3_start"})
                stage3_fn = _pick("stage3_select_winner","stage3_choose_winner","stage3_select_final","stage3_synthesize","stage3_run","stage3")
                if not stage3_fn:
                    raise RuntimeError("Stage3 function not found in backend.council")
                stage3_result = await _maybe_await(_invoke(stage3_fn, args=(payload.content, stage1_results, stage2_results), kwargs={"contract_stack": contract_stack, "contracts": contract_stack}))
                yield _sse({"type": "stage3_complete", "data": stage3_result})

                # Persist assistant message + stages so sidebar/title updates work in SSE mode
                add_assistant_message(
                    conversation_id,
                    (stage3_result or {}).get("response") or "",
                    stage1=stage1_results,
                    stage2=stage2_results,
                    stage3=stage3_result,
                    meta=meta,
                )

                # Title best-effort (do this before emitting title_complete)
                try:
                    s3_text = (stage3_result or {}).get("response") or ""
                    title = _extract_title_from_stage3_response(s3_text)
                    if not title:
                        up = (payload.content or "").strip().splitlines()[0:1]
                        title = (up[0] if up else "")[:120] or None
                    if title:
                        convo2 = get_conversation(conversation_id)
                        if isinstance(convo2, dict) and (convo2.get("title") in (None, "", "New conversation")):
                            convo2["title"] = title
                            save_conversation(convo2)
                except Exception:
                    pass

            except Exception as e:
                yield _sse({"type": "error", "message": str(e)})
                yield _sse({"type": "complete"})
                return

            yield _sse({"type": "title_complete"})
            yield _sse({"type": "complete"})

        return StreamingResponse(_event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache","X-Accel-Buffering": "no","Connection": "keep-alive"})

    # JSON path: no hard 500 — return stable schema with error visible in meta
    if not has_key:
        errors.append("Missing OPENROUTER_API_KEY")

    try:
        # Stage 1
        stage1_fn = _pick(
            "stage1_collect_responses",
            "stage1_collect",
            "collect_stage1_responses",
            "stage1",
        )
        if not stage1_fn:
            raise RuntimeError("Stage1 function not found in backend.council")
        stage1_results = await _maybe_await(
            _invoke(
                stage1_fn,
                args=(payload.content,),
                kwargs={
                    "system": role_spec.system,
                    "contract_stack": contract_stack,
                    "contracts": contract_stack,
                },
            )
        )

        # Stage 2
        stage2_fn = _pick(
            "stage2_collect_rankings",
            "stage2_collect",
            "collect_stage2_rankings",
            "stage2",
        )
        if not stage2_fn:
            raise RuntimeError("Stage2 function not found in backend.council")
        stage2_results = await _maybe_await(
            _invoke(
                stage2_fn,
                args=(payload.content, stage1_results),
                kwargs={
                    "contract_stack": contract_stack,
                    "contracts": contract_stack,
                },
            )
        )

        # Back-compat: council stage2 may return (results, label_to_model).
        stage2_label_to_model = None
        if isinstance(stage2_results, tuple) and len(stage2_results) == 2:
            stage2_results, stage2_label_to_model = stage2_results

        # Aggregate (optional)
        agg_fn = _pick("aggregate_stage2_rankings", "aggregate_rankings")
        aggregate_rankings = _invoke(agg_fn, args=(stage2_results,), kwargs={}) if agg_fn else None

        # Stage 3
        stage3_fn = _pick(
            "stage3_select_winner",
            "stage3_choose_winner",
            "stage3_select_final",
            "stage3_synthesize",
            "stage3_run",
            "stage3",
        )
        if not stage3_fn:
            raise RuntimeError("Stage3 function not found in backend.council")
        stage3_result = await _maybe_await(
            _invoke(
                stage3_fn,
                args=(payload.content, stage1_results, stage2_results),
                kwargs={
                    "contract_stack": contract_stack,
                    "contracts": contract_stack,
                },
            )
        )

    except Exception as e:
        log.exception("Unhandled error in api_send_message")
        errors.append(str(e))
        # On hard failure, still return a JSON payload with empty stages for UI stability.
        stage1_results = []
        stage2_results = []
        aggregate_rankings = None
        stage3_result = {"model": (CHAIRMAN_MODEL or ""), "response": "", "contract_eval": None}

    # label map for UI/debug
    label_to_model = {}
    for idx, r in enumerate(stage1_results or []):
        label = f"Response {chr(65 + idx)}"
        mid = (r or {}).get("model")
        if mid:
            label_to_model[label] = mid

    # model roles (best-effort)
    model_roles = {}
    try:
        for r in stage1_results or []:
            mid = (r or {}).get("model")
            if mid:
                model_roles[mid] = get_role_spec(mid).role
        model_roles[CHAIRMAN_MODEL] = get_role_spec(CHAIRMAN_MODEL).role
    except Exception:
        pass

    meta = {
        "contract_stack": contract_stack,
        "aggregate_rankings": aggregate_rankings,
        "label_to_model": label_to_model,
        "model_roles": model_roles,
        "contract_gate": (
            _invoke(
                _pick("build_contract_gate_summary", "contract_gate_summary"),
                args=(stage1_results, label_to_model),
                kwargs={},
            )
            if _pick("build_contract_gate_summary", "contract_gate_summary")
            else None
        ),
        "stage1_last_errors": dict(STAGE1_LAST_ERRORS or {}),
        "stage2_last_errors": dict(STAGE2_LAST_ERRORS or {}),
    }
    if errors:
        meta["errors"] = errors




    add_assistant_message(conversation_id, (stage3_result or {}).get("response") or "", stage1=stage1_results, stage2=stage2_results, stage3=stage3_result, meta=meta)

    # Update conversation title (post-save). Prefer Stage 3 title; fallback to user prompt.
    try:
        s3_text = (stage3_result or {}).get("response") or ""
        title = _extract_title_from_stage3_response(s3_text)
        if not title:
            up = (payload.content or "").strip().splitlines()[0:1]
            title = (up[0] if up else "")[:120] or None

        if title:
            convo2 = get_conversation(conversation_id)
            if isinstance(convo2, dict) and (convo2.get("title") in (None, "", "New conversation")):
                convo2["title"] = title
                save_conversation(convo2)
    except Exception as e:
        errors.append(f"title_update_failed: {type(e).__name__}: {e}")


    convo_for_meta = get_conversation(conversation_id) or convo
    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "meta": meta,
        "metadata": {
            "conversation_id": conversation_id,
            "title": (convo_for_meta or {}).get("title") if isinstance(convo_for_meta, dict) else None,
            "role": role_spec.name,
        },
    }