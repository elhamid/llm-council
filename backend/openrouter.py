"""OpenRouter API client for making LLM requests.

Intentionally small and dependency-light to keep the public beta stable.
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional
import httpx

from .config import get_config


async def query_model(model: str, messages: List[Dict[str, str]], timeout: Optional[float] = None) -> Dict[str, Any]:
    """Query a single model via OpenRouter.

    Returns:
      { "model": <model>, "response": <text>, "raw": <payload_optional>, "error": <str_optional> }
    """
    cfg = get_config()
    api_key = cfg.openrouter_api_key
    api_url = cfg.openrouter_api_url
    t = float(timeout if timeout is not None else cfg.openrouter_timeout_s)

    if not api_key:
        return {"model": model, "response": "", "error": "OPENROUTER_API_KEY missing"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": model, "messages": messages}

    try:
        async with httpx.AsyncClient(timeout=t) as client:
            r = await client.post(api_url, headers=headers, json=payload)
            if r.status_code >= 400:
                return {"model": model, "response": "", "error": f"HTTP {r.status_code}: {r.text[:400]}"}
            data = r.json()
            text = ""
            try:
                text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            except Exception:
                text = ""
            return {"model": model, "response": text, "raw": data}
    except Exception as e:
        return {"model": model, "response": "", "error": f"{type(e).__name__}: {e}"}


async def query_models(models: List[str], messages: List[Dict[str, str]], timeout: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """Query multiple models concurrently."""
    import asyncio

    tasks = [query_model(m, messages, timeout=timeout) for m in models]
    results = await asyncio.gather(*tasks)
    return {r.get("model", ""): r for r in results}