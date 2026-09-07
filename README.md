# Study Planner

A Flask study planner with a Sakai-style interface. Visitors land on a **welcome** splash, sign up,
complete a short onboarding wizard (school, program, courses, goals, daily availability), and get a
personalized dashboard of subject cards, courses, deadlines and tasks to help them stay on top of
their academic life.

## Features

- **Welcome splash** — opening the app always shows the `welcome` landing (a two-slide intro), with
  a dynamic CTA: *Start* for new users, *Go to dashboard* for returning ones.
- **Authentication** — sign up, log in, and log out with stored (hashed) passwords.
- **Onboarding wizard** — a short flow (`school`, `program`, `courses`, `goals`, `available_hours`)
  on `/onboarding`; `available_hours` is used for planning estimates.
- **Planning dashboard** — the logged-in dashboard (`/dashboard`) shows *your* onboarded subjects as
  polished course cards, plus a planning panel with upcoming deadlines, open tasks, and your courses.
  - Course titles that match a preloaded subject (e.g. "Genetics") reuse that styled card.
  - Any other title (e.g. "Intro to Biochemistry") is turned into a working custom course page
    with its own URL, uploads, collaboration and quizzes.
- **Planning pillar** — per-user courses, academic calendar events, graded deadlines (with manual
  lead-up steps) and tasks, managed from `/courses`, `/calendar`, `/deadlines` and `/task`.
- **Progress** — a `/progress` overview of your activity.
- **Per-subject pages** — home, resources (upload/download materials), assignments, calendar,
  grades, collaboration, and quizzes, all under `/subject/<slug>`.
- **Collaboration** — join a subject workspace with an invite code, share materials, and see members.
- **Quizzes** — create quizzes with auto-generated invite codes, take them, and view a leaderboard.
- **AI Learning Hub** — a chat assistant on `/ai` that explains, summarizes, solves, quizzes,
  makes flashcards, builds study plans, simplifies material, and preps for exams, with
  per-conversation history, streaming replies, and study-material uploads. Works in a clear
  demo mode with no API key and switches to real models automatically when configured.
- **About page** — a modern landing-style `/about` page describing the mission, philosophy and features.

## How onboarding maps the courses to the dashboard

During onboarding the entered course titles are stored on the user record (SQLite `users` table).
The dashboard route in `Backend/app.py` builds the subject-card grid from those titles:

- `Backend/app.py` `user_subjects(user)` matches each title against the preloaded `SUBJECTS` list.
- Titles that do not match any preloaded subject are converted with
  `custom_subject(title, position)` into a full subject card (slug, code, colour, etc.).
- `get_subject(slug)` resolves both preloaded and custom subjects, so a custom course's
  resources, collaboration and quizzes work exactly like any other subject.

Without any onboarded courses (e.g. a freshly registered user who skips the wizard), the full
preloaded subject set is shown as a fallback.

> **Routing note:** `/` is the public welcome landing and is always shown when the app opens.
> The logged-in dashboard lives at `/dashboard` (links use `url_for('home')`, which resolves there).

## Project structure

```text
Study-planner/
├── Backend/
│   ├── app.py                 # Flask application entry point (routes + subject logic)
│   ├── db.py                  # SQLite persistence layer (schema + data access)
│   ├── collab.py              # Collaboration & quiz data layer (SQLite)
│   ├── planner.py             # Planning data layer (courses, calendar, deadlines, tasks)
│   ├── migrate_to_sqlite.py   # One-time JSON -> SQLite migration script
│   ├── requirements.txt       # Python dependencies
│   ├── .env.example           # Copy to .env to enable real AI providers
│   ├── ai/                    # AI Learning Hub package (blueprint, providers, prompts…)
│   │   ├── api.py             # /api/ai/* JSON + SSE endpoints
│   │   ├── service.py         # Orchestration (generate_reply, stream_reply)
│   │   ├── models.py          # Model registry and capability flags
│   │   ├── prompts.py / context.py / limits.py / files.py / storage.py
│   │   ├── providers/         # openai, anthropic, google + mock adapters
│   │   └── tests/             # AI package unit tests
│   ├── tests/                 # Tests for the SQLite layer (db)
│   └── app/
│       └── services/          # Future task and validation logic
├── Database/                  # Runtime data (not committed)
│   ├── instance/study_planner.db   # SQLite database (users, collab, AI)
│   ├── ai_uploads/            # Extracted study-material text (created at runtime)
│   └── uploads/               # Subject course materials (created at runtime)
├── Frontend/
│   ├── static/
│   │   ├── css/               # styles.css + split CSS modules (variables, base, layout, …)
│   │   ├── images/            # Static imagery
│   │   └── js/app.js, ai_hub.js  # Browser-side interactions
│   └── templates/
│       ├── welcome.html       # Public landing splash
│       ├── index.html         # Dashboard (subject cards + planning panel)
│       ├── onboarding.html    # Onboarding wizard
│       ├── courses.html, calendar.html, deadlines.html, task.html, progress.html
│       ├── course_detail.html, deadline_detail.html
│       ├── subject.html       # Per-subject Sakai-style page
│       ├── about.html         # Modern about / mission page
│       ├── ai_hub.html        # AI Learning Hub chat UI
│       └── login.html, signup.html, quiz*.html
├── docs/
│   ├── learning-roadmap.md    # Suggested staged learning plan
│   ├── css-architecture.md    # Front-end styling structure recommendation
│   └── hci-design-rules.md    # HCI design and review checklist
├── run.bat                    # Windows launcher (run from the project root)
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

1. The app opens on the **welcome** splash.
2. Click **Start** and create an account.
3. Complete the **onboarding** wizard with your school, program, courses, goals and daily availability.
4. Land on your **dashboard** (`/dashboard`) and click any subject card to explore it, or use the
   **Courses / Calendar / Deadlines / Tasks / Progress** links to plan your work.

> User, collaboration and AI data live in the SQLite database at
> `Database/instance/study_planner.db` (created automatically on first run), with uploads on disk
> under `Database/uploads/`. The planning features (courses, calendar, deadlines, tasks) use their
> own `Database/planner.json` file. All database files, `Database/ai_uploads/`, and uploaded
> materials are generated at runtime and are not tracked by Git.

## Database

User, collaboration and AI storage uses a single **SQLite** database via Python's standard library
(`sqlite3`), with `Backend/db.py` as the persistence layer. Tables cover: `users`,
`memberships`, `subject_codes`, `materials`, `quizzes`, `attempts`, `ai_conversations`,
`ai_messages`, and `ai_usage`. The schema is created automatically on first run, and lightweight
column migrations (e.g. `users.available_hours`) are applied automatically too.

The planning pillars (`course`, `calendar`, `deadline`, `task`) are kept in `Database/planner.json`
by `Backend/planner.py`.

Existing JSON data (from before this change) can be imported once with:

```powershell
cd Backend
py -m venv .venv                      # if you don't have one yet
& .\.venv\Scripts\Activate.ps1
python migrate_to_sqlite.py
```

`migrate_to_sqlite.py` reads the legacy `Database/users.json`, `Database/collab.json`, and
`Database/ai.json`, and links into SQLite. It is idempotent, so re-running is safe.

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
- `Backend/ai/storage.py` / `limits.py` — SQLite-backed persistence and per-user daily caps.

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

## Toast notifications

Action feedback uses the shared toast surface in `Frontend/templates/_toast.html`, which
is mounted by the base layout and authentication pages. Flask routes should use
`flash("Message", "success")`, `flash("Message", "error")`, or `flash("Message", "info")`
before redirecting so the next page shows a consistent toast. Browser-side code can use
the same pattern through `window.StudyPlannerToast.success(message)`,
`window.StudyPlannerToast.error(message)`, or `window.StudyPlannerToast.info(message)`.

### Optional material-extraction libraries

Add `PyPDF2`, `python-docx`, and `Pillow` for richer PDF/DOCX/image handling. Without them the
hub still works and degrades gracefully for plain-text files.

### Tests

The project ships with unit tests (no external test framework required). Run both the SQLite
layer tests and the AI package tests:

```powershell
cd Backend
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m unittest discover -s ai\tests
```

## Learning progression

1. **HTML:** build and structure the pages in `Frontend/templates/`.
2. **CSS:** style them responsibly using the design tokens in `Frontend/static/css/`.
3. **Python:** practise task/course logic in `Backend/app/services/` and `Backend/planner.py`.
4. **Flask:** connect forms and views through `Backend/app.py`.
5. **Database:** persist users, materials, quizzes and AI data in SQLite via `Backend/db.py`.
6. **Polish:** add validation, search, filters, analytics, and deployment.

See [the learning roadmap](docs/learning-roadmap.md) for suggested milestones and challenges.

For screen and flow design reviews, use the [HCI design rules](docs/hci-design-rules.md).
