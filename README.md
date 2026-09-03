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
│   └── app/
│       └── services/          # Future task and validation logic
├── Database/
│   ├── users.json             # Registered users (created at runtime, not committed)
│   ├── collab.json            # Memberships, materials, quizzes (created at runtime)
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

## Learning progression

1. **HTML:** build the planner’s content and form in `Frontend/templates/index.html`.
2. **CSS:** style it responsively in `Frontend/static/css/styles.css`.
3. **Python:** practise task logic in `Backend/app/services/`.
4. **Flask:** connect forms and task views through `Backend/app.py`.
5. **Database:** persist users, materials and quizzes in `Database/`.
6. **Polish:** add validation, search, filters, accounts, and deployment.

See [the learning roadmap](docs/learning-roadmap.md) for suggested milestones and challenges.
