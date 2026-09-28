# Stash — upload a PDF/PPTX, get idea cards

Stash turns study documents into a Deepstash-style card feed. Upload a PDF
(up to 600 pages, 50 MB) or PowerPoint (up to 200 slides), and a background
worker generates one "idea card" per concept, each with a title, a short body,
optional key term and example, a confidence score, and a source page.

Everything lives under `Backend/stash/`:

| Module | Responsibility |
| --- | --- |
| `service.py` | Document lifecycle: upload validate/store/queue, the processing pipeline, regeneration |
| `worker.py` | In-process daemon thread that claims queued jobs and retries transient failures |
| `parsing/` | PDF + PPTX parse, section/chunk building (`pdf_parser`, `pptx_parser`, `chunker`, `cleaner`) |
| `ai/` | Provider resolution, prompt building, response validation, retry with error feedback |
| `quiz.py` | Deterministic quiz builder (no AI spend — real card quotes) |
| `repository.py` | `stash_*` table access |
| `security.py` | Upload type sniffing (magic bytes), size caps, SHA-256 duplicates, local source storage |
| `routes_pages.py`, `routes_api.py` | `Stash` nav pages + `/api/stash/*` JSON API |

## How generation works

1. `POST /api/stash/documents` validates the file (extension **and** magic
   bytes), rejects anything over the byte/page/slide caps, and rejects exact
   duplicates (same SHA-256, same user) with HTTP 409.
2. A `process_document` job is queued. The worker claims it and runs the
   pipeline: parse -> structure (sections + chunks) -> one Claude call per
   chunk -> a recap card per section.
3. Each chunk tracks its own status, so a crashed/restarted job only
   reprocesses what is still pending. Sections under 250 characters of
   extractable text are skipped, and a document with no readable text stops
   with a user-safe error.
4. Cards are renumbered, the document is marked `ready`, and the reader shows
   the feed: save, highlight, note, "I got it", "Review again", deterministic
   quiz, "Ask about this" (deep-links into the AI Hub prefilled), TOC with
   `data-section-id` jumps, last-position resume, and J/K/S/G/R keyboard
   shortcuts.

### User privacy

Users control AI activity through **Settings -> Privacy -> "AI activity"**
(`settings.privacy.ai_activity` in `db.get_settings`). When it is off, job
processing stops immediately with "AI activity is turned off in your privacy
settings."

### Model and key requirements

Stash explicitly **never uses the mock provider** — silently returning invented
content for a paid feature would be worse than failing loudly. It resolves a
real provider + key in this order during processing:

1. The user's explicit pick for the document (provider/model chosen on the
   upload or regenerate dialogs).
2. The user's AI Hub preference (`STASH_MODEL`, or the default provider in
   **Settings -> AI Settings**).
3. The user's own connected key (`db.get_ai_connection_key`) — set in
   **Settings -> AI Settings**.
4. The server keys, checked in this fixed order: `ANTHROPIC_API_KEY` ->
   `OPENAI_API_KEY` -> `GOOGLE_AI_API_KEY` (alias `GEMINI_API_KEY`) ->
   `DEEPSEEK_API_KEY` -> `COPILOT_GITHUB_TOKEN` (alias `GH_TOKEN`).

`STASH_PROVIDER` sets which provider the server prefers before checking this
order. Every provider goes through the same `generate_json` interface: one
high-quality card per request with a schema-enforced response (OpenAI and Gemini
use their native structured-output modes). Per-model context windows size the
chunks; model choices and unverified activity are recorded on the document and
shown in the reader.

Without a usable key the document fails with a hint to add a key; the upload
page and library show the same hint. Per-user generation respects a daily
token cap (`STASH_DAILY_TOKEN_CAP`); only requests paid for by the **server's**
key count toward the cap — a user's own key is never capped. Once the cap is
reached, server-paid processing stops and the rest queues for the next day.

## Environment variables

Set before the app imports `stash` (all have documented defaults in
`stash/config.py`). `Backend/.env.example` lists them commented out.

| Variable | Default | Meaning |
| --- | --- | --- |
| `STASH_MAX_FILE_BYTES` | 52428800 | Upload size cap (50 MB) |
| `STASH_MAX_PDF_PAGES` | 600 | Max PDF pages |
| `STASH_MAX_PPTX_SLIDES` | 200 | Max PPTX slides |
| `STASH_MODEL` | claude | Default model registry id used for generation |
| `STASH_PROVIDER` | anthropic | Default provider registry id used for generation |
| `STASH_MAX_OUTPUT_TOKENS` | 3000 | Max tokens per provider request |
| `STASH_TEMPERATURE` | 0.2 | Generation temperature |
| `STASH_DAILY_TOKEN_CAP` | 250000 | Per-user/day soft cap |
| `STASH_CHUNK_TARGET_CHARS` | 1700 | Target chunk size |
| `STASH_CHUNK_MAX_CHARS` | 2200 | Hard chunk cap |
| `STASH_WORKER_POLL_SECONDS` | 3 | Worker loop poll interval |
| `STASH_REAP_AFTER_SECONDS` | 300 | Requeue abandoned running jobs after this |
| `STASH_HEARTBEAT_SECONDS` | 30 | Job heartbeat interval |
| `STASH_JOB_MAX_ATTEMPTS` | 3 | Transient retries per job |
| `STASH_UPLOAD_DIR` | `../Database/stash_uploads` | On-disk backup root |
| `STASH_KEEP_SOURCE_FILES` | 1 | `0` disables the file backup |
| `STASH_DISABLE_WORKER` | unset | `1`/`true`/`yes` stops the in-process worker (used by tests) |

On Render, the worker runs inside the gunicorn process (one thread per worker;
jobs are heartbeated and stale claims are reaped, so a crashed worker leaves
no stuck job).

## Errors and edge cases

* **Duplicate upload** -> 409 `{"duplicate": true}` with the existing title.
* **No readable text** -> document `error` and a clear message.
* **Chunk generation fails after retries** -> chunk marked `error`, doc marked
  `error` with "Generation is incomplete — some sections could not be read.";
  regenerate the section later.
* **Daily cap hit** -> stops early, keeps the cards already generated,
  documents the reason.
* **Privacy off / no key** -> document `error` with an actionable message.
* **Cross-user access** -> every page and API call authorizes against the
  document owner and returns 404 otherwise.

## Regeneration

`POST /api/stash/documents/<id>/regenerate` with `{"chapters": [1, 3]}` drops
the cards for the selected 1-based TOC positions and re-queues them (an empty
body regenerates the whole document).

## Tests

Stash tests live in `Backend/tests/test_stash.py` (unittest, same style as the
rest of the suite). They use a fake provider, so no API key is needed:

```
cd Backend
python -m unittest tests.test_stash
```

The full backend suite runs with `python -m unittest discover -s tests`.