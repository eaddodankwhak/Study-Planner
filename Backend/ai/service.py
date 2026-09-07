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
from .providers._http import ProviderHTTPError

#: Map provider HTTP status -> actionable user message. The default "temporarily
#: unavailable" hides the real cause (e.g. a retired model name or a rejected
#: key), which is why a connected key could look like it "isn't working".
_PROVIDER_ERRORS = {
    400: "The AI provider rejected the request (400). Your key or one of the model settings may be out of date.",
    401: "The AI provider rejected your API key (401). Reconnect it in AI Settings.",
    402: "The AI provider needs payment for this key (402). Top up the account and try again.",
    403: "The AI provider denied access with this key on the selected plan (403). It may need different permissions.",
    404: "The AI model you chose is no longer available (404). Try a different model.",
    429: "The AI provider is rate-limiting this key (429). Wait a moment and try again.",
}


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


def generate_reply(request_dict, api_key=None):
    """Run a provider request and return a normalized response.

    request_dict comes from build_provider_request(). Returns the provider's
    normalized generate() result (content + usage). api_key overrides the
    environment key so a user's BYOK connection is used when present.
    """
    provider = get_provider(request_dict["provider_id"], api_key=api_key)
    return provider.generate(request_dict)


def stream_reply(request_dict, api_key=None):
    """Yield chunks from a provider for a request dict."""
    provider = get_provider(request_dict["provider_id"], api_key=api_key)
    for chunk in provider.stream(request_dict):
        if chunk:
            yield chunk


def handle_error(exc):
    """Map internal/provider exceptions to a user-safe, actionable message."""
    if isinstance(exc, limits.RateLimitError):
        return str(exc)
    if isinstance(exc, limits.ValidationError):
        return str(exc)
    if isinstance(exc, ProviderHTTPError):
        return _PROVIDER_ERRORS.get(
            exc.status, f"The AI provider returned HTTP {exc.status}."
        )
    # Anything else -> safe generic message.
    return "The AI service is temporarily unavailable. Please try again."
