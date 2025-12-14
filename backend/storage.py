"""Simple file-based storage for conversations."""

import os
import json
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path

from .config import DATA_DIR

# Ensure data directory exists
Path(DATA_DIR).mkdir(parents=True, exist_ok=True)


def get_conversation_file(conversation_id: str) -> str:
    """Get the file path for a conversation."""
    return os.path.join(DATA_DIR, f"{conversation_id}.json")


def create_conversation(conversation_id: str, title: str) -> Dict[str, Any]:
    """Create a new conversation."""
    conversation = {
        "id": conversation_id,
        "title": title,
        "created_at": datetime.now().isoformat(),
        "messages": [],
    }

    save_conversation(conversation_id, conversation)
    return conversation


def save_conversation(conversation_id: str, conversation: Dict[str, Any]) -> None:
    """Save a conversation to file."""
    file_path = get_conversation_file(conversation_id)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(conversation, f, indent=2, ensure_ascii=False)


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """Get a conversation by ID."""
    file_path = get_conversation_file(conversation_id)
    if not os.path.exists(file_path):
        return None

    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def delete_conversation(conversation_id: str) -> bool:
    """Delete a conversation. Returns True if deleted, False if not found."""
    file_path = get_conversation_file(conversation_id)
    if not os.path.exists(file_path):
        return False
    os.remove(file_path)
    return True


def list_conversations() -> List[Dict[str, Any]]:
    """List all conversations."""
    conversations: List[Dict[str, Any]] = []

    for file_name in os.listdir(DATA_DIR):
        if not file_name.endswith(".json"):
            continue

        file_path = os.path.join(DATA_DIR, file_name)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                conversation = json.load(f)

            messages = conversation.get("messages") or []
            conversations.append(
                {
                    "id": conversation.get("id"),
                    "title": conversation.get("title") or "New conversation",
                    "created_at": conversation.get("created_at"),
                    "message_count": len(messages),
                }
            )
        except Exception:
            # If a file is corrupted, ignore it rather than crashing the UI
            continue

    # Sort by creation date, newest first
    conversations.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return conversations


def add_user_message(conversation_id: str, content: str) -> None:
    """Add a user message to a conversation."""
    conversation = get_conversation(conversation_id)
    if not conversation:
        return

    conversation["messages"].append(
        {
            "role": "user",
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }
    )

    save_conversation(conversation_id, conversation)


def add_assistant_message(
    conversation_id: str,
    stage1: List[Dict[str, Any]],
    stage2: List[Dict[str, Any]],
    stage3: Dict[str, Any],
    *,
    meta: Dict[str, Any] | None = None,
):
    """Add an assistant response (all stages) to a conversation."""
    conversation = get_conversation(conversation_id)
    if not conversation:
        return

    message: Dict[str, Any] = {
        "role": "assistant",
        "stage1": stage1,
        "stage2": stage2,
        "stage3": stage3,
        "timestamp": datetime.now().isoformat(),
    }

    # Store both keys for frontend/backward compat (some UI reads `metadata`)
    if meta is not None:
        message["meta"] = meta
        message["metadata"] = meta

    conversation["messages"].append(message)
    save_conversation(conversation_id, conversation)
