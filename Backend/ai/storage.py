"""Persistence for AI conversations, messages, and usage.

Thin wrapper over the shared SQLite layer (db.py). User-scoped access is
enforced by every query, so users can never read or write another user's
conversations. All AI data is keyed by user_id.
"""

import os
import sys

# Make the Backend package importable so `db` resolves.
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


# ------------------------------------------------------------ conversations

def list_conversations(user_id):
    """Return a user's conversations, most recently updated first."""
    return db.ai_list_conversations(user_id)


def get_conversation(user_id, conversation_id):
    """Return a conversation (with messages) belonging to the user, or None."""
    return db.ai_get_conversation(user_id, conversation_id)


def create_conversation(user_id, title="New conversation", model="claude", mode="ask"):
    """Create a conversation and return it."""
    return db.ai_create_conversation(user_id, title=title, model=model, mode=mode)


def add_message(user_id, conversation_id, role, content, model=None, metadata=None):
    """Append a message to a conversation and bump its timestamp."""
    return db.ai_add_message(user_id, conversation_id, role, content, model=model, metadata=metadata)


def rename_conversation(user_id, conversation_id, title):
    """Set a conversation's title; returns True on success."""
    return db.ai_rename_conversation(user_id, conversation_id, title)


def delete_conversation(user_id, conversation_id):
    """Delete a conversation; returns True on success."""
    return db.ai_delete_conversation(user_id, conversation_id)


def set_conversation_model(user_id, conversation_id, model):
    """Update the model used for a conversation."""
    return db.ai_set_conversation_model(user_id, conversation_id, model)


def search_conversations(user_id, query):
    """Return conversations whose title or message content matches the query."""
    return db.ai_search_conversations(user_id, query)


# ---------------------------------------------------------------- usage

def get_usage(user_id):
    """Return usage counters for a user (per-day + totals)."""
    return db.ai_get_usage(user_id)


def record_usage(user_id, model=None, mode=None, input_tokens=0, output_tokens=0):
    """Increment usage counters for a user for the current UTC day."""
    return db.ai_record_usage(user_id, model=model, mode=mode,
                              input_tokens=input_tokens, output_tokens=output_tokens)


def reset_usage_for_tests():
    """Clear all usage counters (used by tests)."""
    db.ai_reset_usage_for_tests()


def _delete_ai_file():
    """Compatibility no-op kept so any legacy calls still resolve."""
    pass