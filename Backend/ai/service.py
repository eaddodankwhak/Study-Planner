"""Orchestration layer for AI requests.

Ties together the context builder, prompt builder, provider router, and storage
into a single generate_reply() entry point used by the API routes. Keeps route
handlers thin and provider logic isolated.
"""

from . import context as ctx
from . import limits
from . import models as model_registry
from . import prompts
from . import storage
from .providers import get_provider


def prepare_messages(mode, question, material_text, user, conversation_messages, subject_title=None):
    """Build the provider message list for a request.

    Returns (system, user) prompt strings already composed and truncated.
    """
    context_text = ctx.build_context(user, subject_title=subject_title)
    system = prompts.build_system_prompt(mode, context_text)
    user_prompt = prompts.build_user_prompt(mode, question, material_text=material_text)
    return system, user_prompt


def build_provider_request(mode, question, material_text, user, conversation_messages, model_id, subject_title=None):
    """Assemble the full provider request dict (messages + model + token cap)."""
    model = model_registry.get_model(model_id) or model_registry.get_model("claude")
    system, user_prompt = prepare_messages(
        mode, question, material_text, user, conversation_messages, subject_title
    )

    messages = [{"role": "system", "content": system}]
    for m in conversation_messages:
        if m["role"] in ("user", "assistant"):
            messages.append({"role": m["role"], "content": m["content"]})
    # Append the current user message last (after history).
    messages.append({"role": "user", "content": user_prompt})

    return {
        "provider_id": model["provider"],
        "model": model["model_id"],  # provider-specific model id
        "messages": messages,
        "max_tokens": limits.MAX_OUTPUT_TOKENS,
    }


def generate_reply(request_dict):
    """Run a provider request and return a normalized response.

    request_dict comes from build_provider_request(). Returns the provider's
    normalized generate() result (content + usage).
    """
    provider = get_provider(request_dict["provider_id"])
    return provider.generate(request_dict)


def stream_reply(request_dict):
    """Yield chunks from a provider for a request dict."""
    provider = get_provider(request_dict["provider_id"])
    for chunk in provider.stream(request_dict):
        if chunk:
            yield chunk


def handle_error(exc):
    """Map internal/provider exceptions to a user-safe message."""
    if isinstance(exc, limits.RateLimitError):
        return str(exc)
    if isinstance(exc, limits.ValidationError):
        return str(exc)
    # ProviderHTTPError and friends -> generic, safe message.
    return "The AI service is temporarily unavailable. Please try again."
