# Study Planner

Study Planner is a beginner-friendly project that grows from a static HTML page into a Flask web application. Each stage adds one layer of web-development knowledge without replacing the project.

## Project structure

```text
Study-planner/
├── Backend/
│   ├── app.py                 # Flask application entry point
│   ├── requirements.txt       # Python dependencies
│   └── app/
│       └── services/          # Future task and validation logic
├── Database/
│   └── instance/              # Local SQLite database files (not committed)
├── Frontend/
│   ├── static/
│   │   ├── css/styles.css     # Presentation and responsive layout
│   │   └── js/app.js          # Browser-side interactions
│   └── templates/index.html   # Planner page rendered by Flask
├── docs/
│   └── learning-roadmap.md    # Suggested staged learning plan
├── .gitignore
└── README.md
```

## Run the current app

The project is set up for Flask, while the first page remains a simple static planner.

```powershell
cd Backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
flask --app app run --debug
```

Open `http://127.0.0.1:5000` in a browser.

## Learning progression

1. **HTML:** build the planner’s content and form in `Frontend/templates/index.html`.
2. **CSS:** style it responsively in `Frontend/static/css/styles.css`.
3. **Python:** practise task logic in `Backend/app/services/`.
4. **Flask:** connect forms and task views through `Backend/app.py`.
5. **SQLite:** store tasks in `Database/instance/`.
6. **Polish:** add validation, search, filters, accounts, and deployment.

See [the learning roadmap](docs/learning-roadmap.md) for suggested milestones and challenges.
