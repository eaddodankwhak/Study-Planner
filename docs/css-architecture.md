# CSS Architecture Guide — Study Planner

This document outlines a clean, maintainable structure for the Study Planner's
front-end styling. It is a **recommendation**, not a rewrite-of-everything order —
you can adopt as much or as little as you like.

## 1. The Problem Today

- Every page currently has its styles in a single `styles.css` file.
- The file is effectively empty (just a placeholder), so pages have no shared look.
- `index.html` links the stylesheet, but `task.html`, `progress.html` and
  `about.html` do **not** link it at all, so they render unstyled and inconsistent.

Goals of a good structure:

- One place to change colours, spacing and type.
- Reusable classes (buttons, cards, forms) instead of repeating CSS.
- Predictable naming, so it is easy to extend as the app grows.

## 2. Recommended File Layout

Flask serves the `Frontend/static` folder at `/static/`. Keep CSS in
`Frontend/static/css/` and split it by responsibility:

```
Frontend/static/css/
├── styles.css          # global entry point (imports the rest)
├── variables.css       # design tokens: colours, spacing, font, radius
├── base.css            # resets + base element styling (body, h1–h6, a, forms)
├── layout.css          # header, nav, main containers
├── components.css      # buttons, cards, task list, badges
└── utilities.css       # small helper classes (.text-center, .mt-2, …)
```

> If you prefer fewer files, `styles.css` + `variables.css` + `base.css` is a
> solid minimal starting point.

## 3. Maintain a Design-Token Layer (variables.css)

Put the "decisions" in one place so the whole app shares a consistent palette.

```css
:root {
  /* colours */
  --color-primary: #2563eb;
  --color-primary-dark: #1d4ed8;
  --color-bg: #f8fafc;
  --color-surface: #ffffff;
  --color-text: #0f172a;
  --color-muted: #64748b;
  --color-border: #e2e8f0;

  /* spacing scale */
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-6: 1.5rem;
  --space-8: 2rem;

  /* typography */
  --font-sans: "Segoe UI", system-ui, Arial, sans-serif;
  --radius: 0.5rem;

  /* effects */
  --shadow-sm: 0 1px 2px rgb(15 23 42 / 0.06);
  --shadow-md: 0 4px 6px -1px rgb(15 23 42 / 0.1);
}
```

## 4. Naming convention

Use **BEM** (Block, Element, Modifier). It is simple, readable and very common.

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

Class naming rules to keep it predictable:

- Use lowercase + hyphens for blocks and utilities: `.task-list`, `.nav-bar`.
- Never style by `#id` for layout (ids are for JS hooks / anchors, not styling).
- Avoid deep nesting and avoid styling bare tags for components — use classes.

## 5. Example Component Styles (components.css)

```css
.nav-bar {
  display: flex;
  gap: var(--space-4);
  padding: var(--space-4);
}

.nav-bar__link {
  color: var(--color-primary);
  text-decoration: none;
  font-weight: 600;
}

.nav-bar__link--active {
  color: var(--color-primary-dark);
  text-decoration: underline;
}

.button {
  padding: var(--space-2) var(--space-4);
  border: none;
  border-radius: var(--radius);
  background: var(--color-primary);
  color: #fff;
  cursor: pointer;
}

.button--danger {
  background: #dc2626;
}
```

## 6. Link the stylesheet on EVERY page

Every template must include the same `<link>` in its `<head>`, or the layout
will not be consistent:

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/styles.css') }}">
```

`about.html`, `task.html` and `progress.html` currently omit this line — add it.

## 7. Responsive approach

- Use a **mobile-first** strategy: write the base styles, then layer `@media`
  queries to enrich larger screens.
- Use the spacing variables above instead of hard-coded pixel values.
- Avoid fixed pixel widths on containers; use `max-width` + `width: 100%`.

```css
.container {
  width: 100%;
  max-width: 72rem;
  margin-inline: auto;
  padding-inline: var(--space-4);
}

@media (min-width: 40rem) {
  .container {
    padding-inline: var(--space-6);
  }
}
```

## 8. What to do next (checklist)

- [ ] Create `variables.css`, `base.css`, `layout.css`, `components.css`,
      `utilities.css` under `Frontend/static/css/`.
- [ ] Turn `styles.css` into the entry point that imports them.
- [ ] Add the `<link>` to every template (`index`, `task`, `progress`, `about`).
- [ ] Replace inline `style="..."` attributes on elements with classes.
- [ ] Move any future component styles into `components.css` by BEM blocks.
- [ ] Before committing, run a quick visual check in the browser on desktop +
      a narrow (mobile) viewport.

This structure keeps the styling simple now, and scales comfortably as you add
tasks, progress charts and authentication later.
