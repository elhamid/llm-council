from __future__ import annotations
from pathlib import Path

import json
import os
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any
import uuid

from .config import get_config

# In-memory store (always present; used when persist_storage is False)
_MEM: Dict[str, dict] = {}
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.utcnow().isoformat()


def _prune_conversation(convo: dict, max_messages: int) -> None:
    msgs = convo.get("messages") or []
    if isinstance(msgs, list) and max_messages > 0 and len(msgs) > max_messages:
        convo["messages"] = msgs[-max_messages:]


def _prune_all(convos: Dict[str, dict], max_conversations: int) -> Dict[str, dict]:
    if max_conversations <= 0 or len(convos) <= max_conversations:
        return convos
    items = list(convos.items())

    def key(kv):
        c = kv[1] or {}
        return c.get("updated_at") or c.get("created_at") or ""

    items.sort(key=key)
    keep = dict(items[-max_conversations:])
    return keep


def _load_all_conversations_from_disk(path: str) -> Dict[str, dict]:
    p = Path(path)
    out: Dict[str, dict] = {}

    data = None
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None

    if isinstance(data, list):
        for obj in data:
            if isinstance(obj, dict) and obj.get("id"):
                out[str(obj["id"])] = obj
    elif isinstance(data, dict):
        if isinstance(data.get("conversations"), list):
            for obj in data["conversations"]:
                if isinstance(obj, dict) and obj.get("id"):
                    out[str(obj["id"])] = obj
        else:
            for k, v in data.items():
                if isinstance(v, dict):
                    out[str(k)] = v

    try:
        d = p.parent / "conversations"
        if d.is_dir():
            for fp in sorted(d.glob("*.json")):
                try:
                    obj = json.loads(fp.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(obj, dict) and obj.get("id"):
                    cid = str(obj["id"])
                    if cid not in out:
                        out[cid] = obj
    except Exception:
        pass

    return out


def _save_all_conversations_to_disk(path: str, conversations: Dict[str, dict]) -> None:
    if not isinstance(conversations, dict):
        raise ValueError("conversations must be a dict keyed by conversation id")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_file = path + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(conversations, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, path)


def _load_all_conversations() -> Dict[str, dict]:
    cfg = get_config()
    if not cfg.persist_storage:
        return dict(_MEM)
    disk = _load_all_conversations_from_disk(cfg.conversations_file)
    if _MEM:
        disk.update(_MEM)
    return disk


def _save_all_conversations(conversations: Dict[str, dict]) -> None:
    cfg = get_config()

    if cfg.prune_on_write:
        conversations = _prune_all(conversations, cfg.max_conversations)
        for c in conversations.values():
            _prune_conversation(c, cfg.max_messages_per_convo)

    if not cfg.persist_storage:
        _MEM.clear()
        _MEM.update(conversations)
        return

    _save_all_conversations_to_disk(cfg.conversations_file, conversations)


def list_conversations(limit: int = 50) -> List[dict]:
    with _LOCK:
        convos = _load_all_conversations()
        items = sorted(
            convos.values(),
            key=lambda c: c.get("updated_at") or c.get("created_at") or "",
            reverse=True,
        )
        try:
            n = int(limit)
        except Exception:
            return items
        if n <= 0:
            return []
        return items[:n]


def get_conversation(conversation_id: str) -> Optional[dict]:
    with _LOCK:
        convos = _load_all_conversations()
        return convos.get(conversation_id)


def create_conversation(title: Optional[str] = None, tags: Optional[List[str]] = None) -> dict:
    with _LOCK:
        convos = _load_all_conversations()
        cid = str(uuid.uuid4())
        convo = {
            "id": cid,
            "title": title or "New conversation",
            "tags": tags or [],
            "created_at": _now(),
            "updated_at": _now(),
            "messages": [],
        }
        convos[cid] = convo
        _save_all_conversations(convos)
        return convo


def delete_conversation(conversation_id: str) -> bool:
    with _LOCK:
        convos = _load_all_conversations()
        if conversation_id not in convos:
            return False
        del convos[conversation_id]
        _save_all_conversations(convos)
        return True


def save_conversation(convo: dict) -> None:
    if not isinstance(convo, dict) or not convo.get("id"):
        raise ValueError("convo must be a dict with an 'id'")
    with _LOCK:
        convos = _load_all_conversations()
        cid = str(convo["id"])
        convo["updated_at"] = _now()
        convos[cid] = convo
        _save_all_conversations(convos)


def add_user_message(conversation_id: str, content: str, meta: Optional[dict] = None) -> None:
    with _LOCK:
        convos = _load_all_conversations()
        convo = convos.get(conversation_id)
        if not convo:
            raise KeyError("conversation not found")

        msg = {
            "id": str(uuid.uuid4()),
            "role": "user",
            "content": content,
            "timestamp": _now(),
        }
        if meta is not None:
            msg["meta"] = meta
            msg["metadata"] = meta  # backward-compat

        convo.setdefault("messages", []).append(msg)
        convo["updated_at"] = _now()
        convos[conversation_id] = convo
        _save_all_conversations(convos)


def add_assistant_message(
    conversation_id: str,
    content: str,
    *,
    stage1: Optional[List[dict]] = None,
    stage2: Optional[List[dict]] = None,
    stage3: Optional[dict] = None,
    meta: Optional[dict] = None,
) -> None:
    with _LOCK:
        convos = _load_all_conversations()
        convo = convos.get(conversation_id)
        if not convo:
            raise KeyError("conversation not found")

        msg: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "role": "assistant",
            "content": content,
            "timestamp": _now(),
        }
        if stage1 is not None:
            msg["stage1"] = stage1
        if stage2 is not None:
            msg["stage2"] = stage2
        if stage3 is not None:
            msg["stage3"] = stage3
        if meta is not None:
            msg["meta"] = meta
            msg["metadata"] = meta  # backward-compat

        convo.setdefault("messages", []).append(msg)
        convo["updated_at"] = _now()
        convos[conversation_id] = convo
        _save_all_conversations(convos)
