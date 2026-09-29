"""Seed data for the AI Gateway provider and model registry.

Adding an OpenAI-compatible provider later is one new row here (or a row added
by an admin in the panel) — no adapter code is needed. Model names change often,
so keep the authoritative list in this single file; the idempotent seed in
registry.seed_defaults() applies it.

Tier meanings: free (available to every student within the daily quota),
standard (smaller quota), premium (off by default for students; available via a
connected personal key or an allowed group).

may_train_on_data: True for any free tier whose terms allow training on prompts.
Providers flagged this way are skipped for minor accounts and for Stash uploads
unless an admin explicitly flips allowed_for_minors=1 (the admin panel shows a
warning when doing so).
"""

#: Server environment key aliases accepted in addition to each provider's
#: primary env var (keeps the existing GOOGLE_AI_API_KEY/GEMINI_API_KEY legacy
#: working).
ENV_ALIASES = {
    "google": "GOOGLE_AI_API_KEY",
}

#: Which adapters exist. 'openai_compat' covers OpenAI, Gemini, DeepSeek, Groq,
#: Mistral, OpenRouter, Cerebras and friends; 'anthropic' is the native Claude
#: Messages API.
ADAPTER_OPENAI_COMPAT = "openai_compat"
ADAPTER_ANTHROPIC = "anthropic"

#: Tier identifiers shared with quota rules.
TIER_FREE = "free"
TIER_STANDARD = "standard"
TIER_PREMIUM = "premium"


#: Provider rows. ``env_key_name`` is the primary server env var that enables
#: the provider (see ENV_ALIASES for accepted extras). ``base_url`` comes only
#: from this registry — never from user input (SSRF defence).
PROVIDERS = [
    {
        "slug": "anthropic",
        "display_name": "Claude",
        "adapter": ADAPTER_ANTHROPIC,
        "base_url": "https://api.anthropic.com",
        "env_key_name": "ANTHROPIC_API_KEY",
        "is_enabled": 1,
        "is_free_tier": 0,
        "may_train_on_data": 0,
        "allowed_for_minors": 1,
        "sort_order": 1,
    },
    {
        "slug": "openai",
        "display_name": "ChatGPT",
        "adapter": ADAPTER_OPENAI_COMPAT,
        "base_url": "https://api.openai.com/v1",
        "env_key_name": "OPENAI_API_KEY",
        "is_enabled": 1,
        "is_free_tier": 0,
        "may_train_on_data": 0,
        "allowed_for_minors": 1,
        "sort_order": 2,
    },
    {
        "slug": "google",
        "display_name": "Gemini",
        "adapter": ADAPTER_OPENAI_COMPAT,
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "env_key_name": "GEMINI_API_KEY",
        "is_enabled": 1,
        "is_free_tier": 1,
        "may_train_on_data": 1,
        "allowed_for_minors": 0,
        "sort_order": 3,
    },
    {
        "slug": "deepseek",
        "display_name": "DeepSeek",
        "adapter": ADAPTER_OPENAI_COMPAT,
        "base_url": "https://api.deepseek.com/v1",
        "env_key_name": "DEEPSEEK_API_KEY",
        "is_enabled": 1,
        "is_free_tier": 0,
        "may_train_on_data": 1,
        "allowed_for_minors": 0,
        "sort_order": 4,
    },
    {
        "slug": "groq",
        "display_name": "Groq",
        "adapter": ADAPTER_OPENAI_COMPAT,
        "base_url": "https://api.groq.com/openai/v1",
        "env_key_name": "GROQ_API_KEY",
        "is_enabled": 1,
        "is_free_tier": 1,
        "may_train_on_data": 0,
        "allowed_for_minors": 1,
        "sort_order": 5,
    },
    {
        "slug": "mistral",
        "display_name": "Mistral",
        "adapter": ADAPTER_OPENAI_COMPAT,
        "base_url": "https://api.mistral.ai/v1",
        "env_key_name": "MISTRAL_API_KEY",
        "is_enabled": 1,
        "is_free_tier": 1,
        "may_train_on_data": 1,
        "allowed_for_minors": 0,
        "sort_order": 6,
    },
    {
        "slug": "openrouter",
        "display_name": "OpenRouter",
        "adapter": ADAPTER_OPENAI_COMPAT,
        "base_url": "https://openrouter.ai/api/v1",
        "env_key_name": "OPENROUTER_API_KEY",
        "is_enabled": 1,
        "is_free_tier": 0,
        "may_train_on_data": 1,
        "allowed_for_minors": 0,
        "sort_order": 7,
    },
    {
        "slug": "cerebras",
        "display_name": "Cerebras",
        "adapter": ADAPTER_OPENAI_COMPAT,
        "base_url": "https://api.cerebras.ai/v1",
        "env_key_name": "CEREBRAS_API_KEY",
        "is_enabled": 1,
        "is_free_tier": 1,
        "may_train_on_data": 0,
        "allowed_for_minors": 1,
        "sort_order": 8,
    },
]


#: Model rows. display_name is student-friendly; best_for lists the features a
#: model suits well ('cards,explain,chat'), which the Auto picker uses to rank.
#: context_window/max_output are the provider-published token limits.
MODELS = [
    # Anthropic (native adapter).
    {
        "provider_id": "anthropic",
        "model_id": "claude-sonnet-4-5",
        "display_name": "Claude Sonnet",
        "tier": TIER_PREMIUM,
        "context_window": 200000,
        "max_output": 8192,
        "supports_json_mode": 1,
        "speed": "medium",
        "cost_in_per_million": 3.0,
        "cost_out_per_million": 15.0,
        "best_for": "cards,explain,summarize,solve,chat,simplify",
        "sort_order": 1,
    },
    # OpenAI.
    {
        "provider_id": "openai",
        "model_id": "gpt-5-mini",
        "display_name": "ChatGPT Mini",
        "tier": TIER_PREMIUM,
        "context_window": 128000,
        "max_output": 16384,
        "supports_json_mode": 1,
        "speed": "fast",
        "cost_in_per_million": 0.15,
        "cost_out_per_million": 0.60,
        "best_for": "explain,solve,chat,quiz,flashcards",
        "sort_order": 1,
    },
    # Google Gemini (OpenAI-compatible endpoint).
    {
        "provider_id": "google",
        "model_id": "gemini-3.8-flash",
        "display_name": "Gemini Flash",
        "tier": TIER_FREE,
        "context_window": 1000000,
        "max_output": 8192,
        "supports_json_mode": 1,
        "speed": "fast",
        "cost_in_per_million": 0.0,
        "cost_out_per_million": 0.0,
        "best_for": "cards,explain,summarize,solve,chat,quiz,flashcards,simplify",
        "sort_order": 1,
    },
    # DeepSeek.
    {
        "provider_id": "deepseek",
        "model_id": "deepseek-v4-flash",
        "display_name": "DeepSeek Flash",
        "tier": TIER_STANDARD,
        "context_window": 128000,
        "max_output": 8192,
        "supports_json_mode": 1,
        "speed": "fast",
        "cost_in_per_million": 0.27,
        "cost_out_per_million": 1.10,
        "best_for": "cards,explain,summarize,solve,chat,quiz",
        "sort_order": 1,
    },
    # Groq (free-tier, does not train on prompts).
    {
        "provider_id": "groq",
        "model_id": "llama-3.3-70b-versatile",
        "display_name": "Groq Llama",
        "tier": TIER_FREE,
        "context_window": 131072,
        "max_output": 32768,
        "supports_json_mode": 0,
        "speed": "fast",
        "cost_in_per_million": 0.0,
        "cost_out_per_million": 0.0,
        "best_for": "explain,summarize,solve,chat,quiz",
        "sort_order": 1,
    },
    # Mistral (free tier; terms allow training on prompts -> gated by default).
    {
        "provider_id": "mistral",
        "model_id": "mistral-large-latest",
        "display_name": "Mistral Large",
        "tier": TIER_FREE,
        "context_window": 128000,
        "max_output": 8192,
        "supports_json_mode": 1,
        "speed": "medium",
        "cost_in_per_million": 0.0,
        "cost_out_per_million": 0.0,
        "best_for": "explain,summarize,chat,quiz",
        "sort_order": 1,
    },
    # OpenRouter (many models behind one key).
    {
        "provider_id": "openrouter",
        "model_id": "openrouter/auto",
        "display_name": "OpenRouter Auto",
        "tier": TIER_STANDARD,
        "context_window": 128000,
        "max_output": 8192,
        "supports_json_mode": 1,
        "speed": "medium",
        "cost_in_per_million": 0.0,
        "cost_out_per_million": 0.0,
        "best_for": "chat,explain",
        "sort_order": 1,
    },
    # Cerebras (free-tier, fast).
    {
        "provider_id": "cerebras",
        "model_id": "llama-3.3-70b",
        "display_name": "Cerebras Llama",
        "tier": TIER_FREE,
        "context_window": 131072,
        "max_output": 16384,
        "supports_json_mode": 0,
        "speed": "fast",
        "cost_in_per_million": 0.0,
        "cost_out_per_million": 0.0,
        "best_for": "explain,summarize,solve,chat,quiz",
        "sort_order": 1,
    },
]