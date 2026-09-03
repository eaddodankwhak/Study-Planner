# CSS Architecture Guide — Study Planner

This document outlines the maintenance conventions for the Study Planner's front-end
styling. It explains the current file split, the design-token layer, naming rules, and
how to add new styles without breaking the shared look.

## 1. Current structure

CSS lives in `Frontend/static/css/` and is split by responsibility. `styles.css` is the
global entry point and is linked from **every** template's `<head>`:

```
Frontend/static/css/
├── styles.css          # global entry point (imports the rest)
├── variables.css       # design tokens: colours, spacing, font, radius
├── base.css            # resets + base element styling (body, h1–h6, a, forms)
├── layout.css          # header, nav, main containers
├── components.css      # buttons, cards, task list, badges, forms, quizzes
├── utilities.css       # small helper classes (.text-center, .mt-2, …)
└── ai.css              # AI Learning Hub chat, streaming, markdown, uploads
```

Every template includes the same `<link>` in its `<head>` so the layout is consistent
across pages:

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/styles.css') }}">
```

Page-specific styles (such as the About or Welcome pages) are kept as small `<style>`
blocks inside the template itself, scoped under a page-level class (e.g. `.about`,
`.welcome`), so they don't bleed into other pages.

## 2. Design tokens (variables.css)

All colours, spacing, radius and effects are defined once in `variables.css` and
referenced via CSS custom properties so the whole app shares a consistent palette.

```css
:root {
  /* Sakai signature navy brand colour */
  --color-primary: #0f3a52;
  --color-primary-dark: #0a2c3e;
  --color-accent: #f09937;

  /* neutrals */
  --color-bg: #eef1f4;
  --color-surface: #ffffff;
  --color-text: #22282e;
  --color-muted: #5f6b76;
  --color-border: #d7dde3;

  /* spacing scale */
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-6: 1.5rem;
  --space-8: 2rem;

  /* typography / effects */
  --font-sans: "Helvetica Neue", Helvetica, Arial, sans-serif;
  --radius: 0.5rem;
  --shadow-sm: 0 1px 2px rgb(15 23 42 / 0.08);
  --shadow-md: 0 4px 12px -2px rgb(15 23 42 / 0.18);
}
```

> Use the tokens instead of hard-coded hex values or raw pixel spacing so a future
> theme/refactor only touches this one file.

## 3. Naming convention (BEM)

Components use **BEM**: Block, Element, Modifier.

```css
/* Block: a standalone component */
.card { … }

/* Element: part of the block (double underscore) */
.card__title { … }
.card__body { … }

/* Modifier: a variation (double dash) */
.button--primary { … }
.button--danger { … }
```

Naming rules to keep things predictable:

- Use lowercase + hyphens for blocks and utilities: `.task-list`, `.nav-bar`.
- Never style by `#id` for layout (ids are for JS hooks / anchors, not styling).
- Avoid deep nesting and avoid styling bare tags for components — use classes.

## 4. Reusable components

Shared building blocks live in `components.css` and are reused across pages:

- `.button`, `.button--primary`, `.button--accent`, `.button--ghost`, `.button--small`
- `.card`, `.card__title`
- `.form-field`, `.form-label`, `.form-input`
- `.course-grid` / `.course-card` (with colour modifiers `--navy/--teal/--orange/…`)
- `.task`, `.task-list`, `.badge` variants
- `.subject-tools`, `.material-list`, `.member-list`, `.quiz-*`

Layout blocks (header, nav, `app-shell`/sidebar, `.container`, `.page-main`) live in
`layout.css`.

## 5. Adding a new component

1. Pick a short, descriptive BEM block name.
2. Put the block styles in `components.css` (or `layout.css` for layout).
3. Reference design tokens from `variables.css` — no magic values.
4. Add the class to the template and link `styles.css` if the page doesn't already.
5. For one-off page styles, use a scoped `<style>` block under a page-level class.

## 6. Responsive approach

- Use a **mobile-first** strategy: write base styles, then layer `@media` queries to
  enrich larger screens.
- Use the spacing tokens rather than hard-coded pixel values.
- Avoid fixed pixel widths on containers; use `max-width` + `width: 100%`.

```css
.container {
  width: 100%;
  max-width: 72rem;
  margin-inline: auto;
  padding-inline: var(--space-4);
}
```

## 7. Checklist when changing styles

- [ ] Reuse existing tokens and component classes before writing new CSS.
- [ ] Keep BEM naming consistent with the rest of the app.
- [ ] Confirm `styles.css` is linked on any page you add styles to.
- [ ] Scope one-off page styles under a page class.
- [ ] Before committing, do a quick visual check on desktop + a narrow (mobile) viewport.

This structure keeps the styling consistent across the growing set of pages (dashboard,
courses, calendar, deadlines, tasks, progress, AI hub, about) and scales comfortably as
you add more.