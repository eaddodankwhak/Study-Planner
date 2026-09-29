# AI Gateway

The AI Gateway is the single server-side boundary for AI access in the Study
Planner. Feature code (the AI Hub, Stash, and anything added later) asks the
gateway for text or structured JSON; it never talks to a provider directly.
The gateway owns provider/model selection, key resolution, usage accounting,
quotas, caching, fallback, and circuit-breaking, so a student never has to
enter an API key to use the built-in providers.

Everything lives under `Backend/ai_gateway/`:

| Module | Responsibility |
| --- | --- |
| `gateway.py` | Selection, fallback, cache, quota check, `generate_text` / `generate_json` / `stream_text` |
| `adapters.py` | Provider protocols: `openai_compat`, `anthropic`, `copilot` |
| `registry.py` | Provider/model catalog (seed + DB overrides), key checks, admin writes |
| `models_seed.py` | Default providers and models |
| `config.py` | Every runtime knob and the admin email allow-list |
| `quotas.py` | Per-day, per-tier request/token rules |
| `cache.py` | Shared-content response cache for document generation |
| `health.py` | Per-provider circuit breaker |
| `validation.py` | JSON schema validation for structured output |
| `errors.py` | User-safe failure types |
| `api.py` | Student-facing JSON endpoints (model picker, usage) |
| `admin.py` | Admin console JSON endpoints (providers, models, quotas, health) |

## Request flow

1. A caller invokes `gateway.generate_text(...)`, `generate_json(...)`, or
   `stream_text(...)` with a `user_id` and a `feature` label.
2. `require_privacy` fails the request closed if the user has turned AI
   activity off (Settings -> Privacy -> "AI activity").
3. A model is selected: the user's pinned preference when it is still usable,
   otherwise auto-selection ranked by feature fit and cost.
4. The key is resolved **server key first, then the student's own BYOK key**.
   `paid_by` records which one was used.
5. `quotas.check` rejects the request when the server-funded budget for the
   model's tier is spent. BYOK requests (`paid_by == "personal"`) bypass the
   server quota.
6. The provider call runs with retries; on failure the fallback chain tries the
   next healthy model. Each provider is gated by its circuit breaker.
7. `storage.record_usage` (features) and the gateway's own
   `ai_gateway_usage` row account for the tokens and cost.

`stream_text` is a generator: it yields plain text deltas and then, as its
final item, a metadata dict (`model`, `provider`, `paid_by`, `usage`).
Fallback only happens before the first token; once tokens are flowing, a
provider error propagates rather than splicing two models into one reply.

## Structured output

`generate_json(user_id, schema, prompt, ...)` asks for JSON, validates it
against the caller's schema, and retries with the validation error fed back to
the model. Stash's card generation uses this path (passing its cards schema so
providers with native JSON mode can use it). The envelope it returns carries
the parsed `data` and a `usage` block so callers can keep their own
accounting.

## Keys

The server holds built-in keys via environment variables (see
`Backend/.env.example`): `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GOOGLE_AI_API_KEY` / `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`,
`COPILOT_GITHUB_TOKEN` / `GH_TOKEN`, and optionally `GROQ_API_KEY`,
`MISTRAL_API_KEY`, `OPENROUTER_API_KEY`, `CEREBRAS_API_KEY`. A provider with no
server key is simply unavailable to students until one is set. Students may
still connect their own key in Settings; the gateway prefers the server key and
falls back to the student's key.

## Quotas

Daily request and token limits live in the `ai_quota_rules` table keyed by
`(tier, user_group)` and are editable from the admin console. Only
server-funded requests count against them. The seed installs safe defaults and
never overwrites an admin's edits.

## Cache, fallback, circuit breaker

- **Cache**: only shared document-generation responses are cached, keyed by a
  content hash, so re-uploading the same textbook costs nothing. Personal text
  (notes, chat, saved cards) is never cached.
- **Fallback**: up to `AI_GATEWAY_FALLBACK_MAX_TRIES` models are tried in
  ranked order.
- **Circuit breaker**: after `AI_GATEWAY_CIRCUIT_THRESHOLD` consecutive
  failures a provider is skipped for `AI_GATEWAY_CIRCUIT_OPEN_SECONDS`, then
  probed by exactly one half-open trial.

## Admin console

The admin console at `/admin/ai-gateway` manages the provider/model registry,
quota rules, and circuit-breaker state. It is gated by an email allow-list:

```
AI_GATEWAY_ADMIN_EMAILS=you@example.com,teammate@example.com
```

The allow-list is **empty by default**, so the console and its API
(`/api/ai-gateway/admin/*`) fail closed until a deployment names its admins.
Providers carry a `may_train_on_data` flag; the console warns when such a
provider is enabled or allowed for minors. Every change is written to the
audit log.

## Configuration reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `AI_GATEWAY_ADMIN_EMAILS` | _(empty)_ | Admin console allow-list |
| `AI_GATEWAY_FREE_DAILY_REQUESTS` | `40` | Daily requests, free tier |
| `AI_GATEWAY_STANDARD_DAILY_REQUESTS` | `60` | Daily requests, standard tier |
| `AI_GATEWAY_PREMIUM_DAILY_REQUESTS` | `80` | Daily requests, premium tier |
| `AI_GATEWAY_CACHE_ENABLED` | `1` | Toggle the shared-content cache |
| `AI_GATEWAY_CACHE_MAX_ROWS` | `500` | Global cache row cap |
| `AI_GATEWAY_CACHE_TTL_SECONDS` | `86400` | Cache entry lifetime |
| `AI_GATEWAY_FALLBACK_MAX_TRIES` | `3` | Models tried before giving up |
| `AI_GATEWAY_CIRCUIT_THRESHOLD` | `3` | Failures before a breaker opens |
| `AI_GATEWAY_CIRCUIT_OPEN_SECONDS` | `300` | Cooldown before a half-open probe |
| `AI_GATEWAY_TIMEOUT_SECONDS` | `60` | Provider call timeout |

## Tests

```
cd Backend
python -m unittest discover -s ai/tests -p "test_*.py"   # AI Hub
python -m unittest tests.test_ai_gateway tests.test_ai_gateway_stream \
    tests.test_ai_gateway_api tests.test_ai_gateway_reliability \
    tests.test_ai_gateway_admin
```
