"""Persistence for AI conversations, messages, and usage.

Mirrors the JSON-file approach used elsewhere in Study Planner (see collab.py).
All AI data lives in Database/ai.json, keyed by user_id so users can never see
one another's conversations.
"""

import json
import os
import time
import uuid

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATABASE_DIR = os.path.join(BASE_DIR, "Database")
AI_FILE = os.path.join(DATABASE_DIR, "ai.json")


def _default_data():
    return {"conversations": {}, "usage": {}}


def _load():
    if not os.path.exists(AI_FILE):
        return _default_data()
    try:
        with open(AI_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return _default_data()


def _save(data):
    os.makedirs(DATABASE_DIR, exist_ok=True)
    with open(AI_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _now():
    return int(time.time())


# ------------------------------------------------------------ conversations

def list_conversations(user_id):
    """Return a user's conversations, most recently updated first."""
    data = _load()
    convs = data["conversations"].get(user_id, {})
    items = list(convs.values())
    items.sort(key=lambda c: c.get("updated_at", 0), reverse=True)
    return items


def get_conversation(user_id, conversation_id):
    """Return a conversation (with messages) belonging to the user, or None."""
    data = _load()
    conv = data["conversations"].get(user_id, {}).get(conversation_id)
    return conv


def create_conversation(user_id, title="New conversation", model="claude", mode="ask"):
    """Create a conversation and return it."""
    data = _load()
    conv = {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "title": title,
        "model": model,
        "mode": mode,
        "created_at": _now(),
        "updated_at": _now(),
        "messages": [],
    }
    data["conversations"].setdefault(user_id, {})[conv["id"]] = conv
    _save(data)
    return conv


def _touch(data, user_id, conversation_id):
    conv = data["conversations"].get(user_id, {}).get(conversation_id)
    if conv:
        conv["updated_at"] = _now()


def add_message(user_id, conversation_id, role, content, model=None, metadata=None):
    """Append a message to a conversation and bump its timestamp."""
    data = _load()
    conv = data["conversations"].get(user_id, {}).get(conversation_id)
    if not conv:
        return None
    msg = {
        "id": uuid.uuid4().hex,
        "conversationId": conversation_id,
        "role": role,
        "content": content,
        "model": model or conv.get("model"),
        "createdAt": _now(),
        "metadata": metadata or {},
    }
    conv["messages"].append(msg)
    # Auto-title from the first user message if still the default.
    if role == "user" and (conv.get("title") in (None, "", "New conversation")):
        first_line = content.strip().splitlines()[0]
        conv["title"] = first_line[:50] if first_line else "New conversation"
    _touch(data, user_id, conversation_id)
    _save(data)
    return msg


def rename_conversation(user_id, conversation_id, title):
    """Set a conversation's title; returns True on success."""
    data = _load()
    conv = data["conversations"].get(user_id, {}).get(conversation_id)
    if not conv:
        return False
    conv["title"] = title
    _touch(data, user_id, conversation_id)
    _save(data)
    return True


def delete_conversation(user_id, conversation_id):
    """Delete a conversation; returns True on success."""
    data = _load()
    convs = data["conversations"].get(user_id, {})
    if conversation_id not in convs:
        return False
    del convs[conversation_id]
    _save(data)
    return True


def set_conversation_model(user_id, conversation_id, model):
    """Update the model used for a conversation."""
    data = _load()
    conv = data["conversations"].get(user_id, {}).get(conversation_id)
    if not conv:
        return False
    conv["model"] = model
    _touch(data, user_id, conversation_id)
    _save(data)
    return True


def search_conversations(user_id, query):
    """Return conversations whose title or message content matches the query."""
    query = (query or "").lower()
    convs = list_conversations(user_id)
    if not query:
        return convs
    matches = []
    for c in convs:
        haystack = c["title"].lower()
        for m in c.get("messages", []):
            haystack += " " + m["content"].lower()
        if query in haystack:
            matches.append(c)
    return matches


# ---------------------------------------------------------------- usage

def get_usage(user_id):
    """Return usage counters for a user (per-day + totals)."""
    data = _load()
    return data["usage"].get(user_id, {})


def record_usage(user_id, model=None, mode=None, input_tokens=0, output_tokens=0):
    """Increment usage counters for a user for the current UTC day."""
    data = _load()
    usage = data["usage"].setdefault(user_id, {"total_requests": 0, "days": {}})
    day = time.strftime("%Y-%m-%d", time.gmtime())
    day_entry = usage["days"].setdefault(day, {"requests": 0, "input_tokens": 0, "output_tokens": 0})
    day_entry["requests"] += 1
    day_entry["input_tokens"] += int(input_tokens or 0)
    day_entry["output_tokens"] += int(output_tokens or 0)
    usage["total_requests"] = usage.get("total_requests", 0) + 1
    if model:
        by_model = usage.setdefault("models", {})
        by_model[model] = by_model.get(model, 0) + 1
    if mode:
        by_mode = usage.setdefault("modes", {})
        by_mode[mode] = by_mode.get(mode, 0) + 1
    _save(data)
    return usage


def reset_usage_for_tests():
    """Delete the AI data file (used by tests)."""
    if os.path.exists(AI_FILE):
        os.remove(AI_FILE)


def _delete_ai_file():
    if os.path.exists(AI_FILE):
        os.remove(AI_FILE)
