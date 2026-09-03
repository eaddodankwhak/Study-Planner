# Study Planner

A Flask study planner with a Sakai-style interface. New users sign up, complete a short
onboarding wizard (school, program, courses, goals), and get a personalized dashboard of
subject cards they can explore, share course materials for, and build collaborative quizzes around.

## Features

- **Authentication** — sign up, log in, and log out with stored (hashed) passwords in `Database/users.json`.
- **Onboarding wizard** — a 4-step flow (`school`, `program`, `courses`, `goals`) on `/onboarding`.
- **Personalized dashboard** — the home page shows *your* onboarded courses, not a fixed list.
  - Course titles that match a preloaded subject (e.g. "Genetics") reuse that polished card.
  - Any other title (e.g. "Intro to Biochemistry") is turned into a working custom course page
    with its own URL, uploads, collaboration and quizzes.
- **Per-subject pages** — home, resources (upload/download materials), assignments, calendar,
  grades, collaboration, and quizzes, all under `/subject/<slug>`.
- **Collaboration** — join a subject workspace with an invite code, share materials, and see members.
- **Quizzes** — create quizzes with auto-generated invite codes, take them, and view a leaderboard.
- **AI Learning Hub** — a chat assistant on `/ai` that explains, summarizes, solves, quizzes,
  makes flashcards, builds study plans, simplifies material, and preps for exams, with
  per-conversation history, streaming replies, and study-material uploads. Works in a clear
  demo mode with no API key and switches to real models automatically when configured.

## How onboarding maps the courses to the dashboard

During onboarding the entered course titles are stored on the user record
(`Database/users.json`, under the user's `courses` list). The `home()` route in
`Backend/app.py` builds the dashboard from those titles:

- `Backend/app.py` `user_subjects(user)` matches each title against the preloaded `SUBJECTS` list.
- Titles that do not match any preloaded subject are converted with
  `custom_subject(title, position)` into a full subject card (slug, code, colour, etc.).
- `get_subject(slug)` resolves both preloaded and custom subjects, so a custom course's
  resources, collaboration and quizzes work exactly like any other subject.

Without any onboarded courses (e.g. a freshly registered user who skips the wizard), the full
preloaded subject set is shown as a fallback.

## Project structure

```text
Study-planner/
├── Backend/
│   ├── app.py                 # Flask application entry point (routes + subject logic)
│   ├── collab.py              # Collaboration & quiz data layer
│   ├── requirements.txt       # Python dependencies
│   ├── ai/                    # AI Learning Hub package (blueprint, providers, prompts…)
│   └── app/
│       └── services/          # Future task and validation logic
├── Database/
│   ├── users.json             # Registered users (created at runtime, not committed)
│   ├── collab.json            # Memberships, materials, quizzes (created at runtime)
│   ├── ai.json                # AI conversations & usage (created at runtime)
│   ├── ai_uploads/            # Extracted study-material text (created at runtime)
│   └── instance/              # Local SQLite database files (not committed)
├── Frontend/
│   ├── static/
│   │   ├── css/               # styles.css + split CSS modules (variables, base, layout, …)
│   │   ├── images/            # Static imagery
│   │   └── js/app.js          # Browser-side interactions
│   └── templates/
│       ├── index.html         # Dashboard (subject cards)
│       ├── onboarding.html    # 4-step onboarding wizard
│       ├── subject.html       # Per-subject Sakai-style page
│       └── login.html, signup.html, quiz*.html, about/task/progress.html
├── docs/
│   ├── learning-roadmap.md    # Suggested staged learning plan
│   └── css-architecture.md    # Front-end styling structure recommendation
├── run.bat                    # Windows launcher (banket run from the project root)
├── .gitignore
└── README.md
```

## Run the app

```powershell
cd Backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
flask --app app run --debug
```

Or just double-click `run.bat` from the project root — it starts the server and opens
`http://127.0.0.1:5000` in your default browser.

Then:

1. Click **Sign up** and create an account.
2. Complete the **onboarding** wizard with your school, program, courses and goals.
3. Land on your personalized dashboard, and click any subject card to explore it.

> `Database/users.json`, `Database/collab.json` and uploaded materials are generated at runtime
> and are not tracked by Git.

## AI Learning Hub

The AI Hub lives at **`/ai`** (linked from the top navigation). It uses a chat interface with
task *modes* (Explain, Summarize, Solve, Quiz Me, Flashcards, Study Plan, Simplify, Exam Prep,
Ask Anything), a choice of models, per-conversation history, streaming replies, and the ability
to attach study materials (`.txt`, `.md`, `.pdf`, `.docx`, images) so the AI can answer from
your notes.

The back end is fully provider-agnostic:

- `Backend/ai/models.py` — the model registry (claude/gpt/gemini) and capability flags.
- `Backend/ai/prompts.py` + `context.py` — prompt composition and student context.
- `Backend/ai/providers/` — one adapter per provider (OpenAI, Anthropic, Google) plus a clearly
  isolated `mock.py`. `get_provider()` returns a real adapter only when its API key is set,
  otherwise it falls back to the mock so the hub works with no credentials.
- `Backend/ai/service.py` — orchestration (`generate_reply`, `stream_reply`).
- `Backend/ai/api.py` — the `/api/ai/*` JSON + SSE endpoints behind the session login.
- `Backend/ai/storage.py` / `limits.py` — JSON persistence and per-user daily caps.

### Demo vs. real models

Out of the box the hub runs in **demo mode** with the built-in mock provider; replies are
clearly marked as demos. To enable a real model, set one API key in your environment
(see `Backend/.env.example`):

```powershell
$env:OPENAI_API_KEY  = "sk-..."
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:GOOGLE_AI_API_KEY  = "AIza..."
```

Optional `*_MODEL` / `*_BASE_URL` variables override the default model/endpoint for each
provider. Limits are configurable via `AI_MAX_REQUESTS_PER_DAY`, `AI_MAX_QUESTION_CHARS`,
`AI_MAX_MATERIAL_CHARS`, `AI_MAX_OUTPUT_TOKENS`, and `AI_MAX_FILE_BYTES`.

### Optional material-extraction libraries

Add `PyPDF2`, `python-docx`, and `Pillow` for richer PDF/DOCX/image handling. Without them the
hub still works and degrades gracefully for plain-text files.

### Tests

The AI package ships with unit tests (no external test framework required):

```powershell
cd Backend
.\.venv\Scripts\python.exe -m unittest discover -s ai\tests
```

## Learning progression

1. **HTML:** build the planner’s content and form in `Frontend/templates/index.html`.
2. **CSS:** style it responsively in `Frontend/static/css/styles.css`.
3. **Python:** practise task logic in `Backend/app/services/`.
4. **Flask:** connect forms and task views through `Backend/app.py`.
5. **Database:** persist users, materials and quizzes in `Database/`.
6. **Polish:** add validation, search, filters, accounts, and deployment.

See [the learning roadmap](docs/learning-roadmap.md) for suggested milestones and challenges.
