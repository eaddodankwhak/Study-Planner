# Study Planner learning roadmap

## Stage 1 — HTML

Use `Frontend/templates/` to practise document structure, semantic sections, forms, lists,
classes, and Jinja templating. Build the welcome landing, dashboard, courses, calendar,
deadlines, tasks, and the subject pages.

**Challenge:** Add a profile section and a table that lists a task, category, deadline, and status.

## Stage 2 — CSS

Use `Frontend/static/css/` (design tokens in `variables.css`, components in `components.css`)
for selectors, spacing, colours, cards, Flexbox/Grid, hover states, and responsive breakpoints.

**Challenge:** Make the task list into responsive cards without changing the HTML structure.

## Stage 3 — Python

Practise reusable domain logic. Task/course/calendar/deadline persistence lives in
`Backend/planner.py`, and shared user/collab/AI persistence in `Backend/db.py`.
Put new helper logic in `Backend/app/services/`.

**Challenge:** Write a program that adds, displays, and removes tasks from a list.

## Stage 4 — Flask

Add routes to `Backend/app.py` to display pages and accept form submissions.

**Challenge:** Render a Python list of tasks on the dashboard.

## Stage 5 — Database

Users, collaboration and AI data persist in the SQLite file `Database/instance/study_planner.db`
via `Backend/db.py` (created automatically on first run and ignored by Git). Planning data is
kept in `Database/planner.json` via `Backend/planner.py`.

**Challenge:** Make tasks persist after restarting the application.

## Next features

- Complete, edit, and delete tasks and deadlines
- Search, filters, and richer progress statistics / charts
- Validation and error messages
- Move planning data (`planner.json`) into SQLite alongside the rest
- Deployment (host the Flask app, move the session secret to environment variables)