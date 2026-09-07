# Study Planner HCI Design Rules

Use this document whenever designing or reviewing a Study Planner screen, flow, or feature. It translates the DCIT 302 Design Rules framework (Principles, Standards, Guidelines, Golden Rules, and Design Patterns) into concrete requirements for a student success platform.

## Master instruction

Design and build every screen and flow in Study Planner so that it satisfies the three usability principles - **Learnability, Flexibility, and Robustness** - the ISO 9241 usability standard (effectiveness, efficiency, satisfaction), Shneiderman's 8 Golden Rules, Norman's 7 Principles, and reusable HCI design patterns. Before shipping a feature, check it against every relevant item below. If a requirement cannot be met, state the trade-off explicitly rather than silently dropping it.

## 1. Learnability

A new student should be productive immediately.

- **Predictability:** every icon, button, and action signals its effect before it is taken. Delete-looking icons delete; `Start Quiz` starts a quiz.
- **Synthesizability:** every action gets immediate, visible confirmation. A saved deadline shows `Saved`, a submitted quiz shows a score, and a synced calendar shows a sync timestamp.
- **Familiarity:** reuse conventions students already know: calendars look like calendars, progress bars fill left to right, magnifying glasses mean search, and bells mean notifications.
- **Generalizability:** interaction patterns learned in one module transfer to others. For example, if swiping right archives a notification, it should archive similarly in notes and deadlines.
- **Consistency:** use one visual language across dashboard, courses, calendar, quizzes, notes, and analytics. Keep color meaning consistent: red means overdue or destructive everywhere, never save on one screen.

## 2. Flexibility

Support different students and different workflows.

- **Dialogue initiative:** let the student drive. Do not force a rigid onboarding wizard with no skip or back option, and do not block the dashboard until every course is entered.
- **Multithreading:** students should be able to run the focus timer while browsing notes or keep the AI tutor open while working through a quiz. Avoid single-task lockout unless it is required, such as for a timed exam simulation.
- **Task migratability:** offer to do the heavy lifting, such as suggesting a plan from syllabus deadlines or generating spaced-repetition schedules, while allowing manual override at any point.
- **Substitutivity:** provide equivalent ways to complete a task: type a deadline, import it from a synced calendar, or upload a parsed syllabus PDF.
- **Customizability:** support both adaptability (widget order, notification frequency, theme, study-hour preferences) and adaptivity (surfacing weak topics or reordering mastery content). Make system adaptation visible and overridable; never silently reschedule student work.

## 3. Robustness

Students must always know what is happening and be able to recover.

- **Observability:** show real-time state for anything that takes time, including document uploads, AI thinking, and spaced-repetition sync. Never leave a blank screen after an action.
- **Recoverability:** destructive actions need confirmation and, where feasible, an **Undo**. Support backward recovery and forward recovery with clear error messages that explain what went wrong and how to fix it.
- **Responsiveness:** acknowledge input within approximately 100 ms even when the operation takes longer. Show progress immediately for plan generation, uploads, and similar work.
- **Task conformance:** support the complete student journey: register a course, get an adaptive plan, study, practice, measure mastery, and receive reminders. Do not stop at tracking deadlines without helping students act on them.

## 4. ISO 9241 and accessibility baseline

For every core flow, explicitly verify:

- **Effectiveness:** can the student complete the task?
- **Efficiency:** how many steps and how much time does it take relative to the value delivered?
- **Satisfaction:** does it feel acceptable, not merely functional, in tone, friction, and visual polish?

Also maintain WCAG-level color contrast, keyboard navigation, visible focus states, semantic structure, and screen-reader labels.

## 5. Guidelines and trade-offs

- Use the internal style guide for spacing, typography, color tokens, and component states so detailed decisions remain consistent across the app.
- When guidelines conflict, such as reducing clicks versus confirming a destructive action, favor the rule that better protects usability and safety in that context and record the reason.

## 6. Shneiderman's 8 Golden Rules

1. Strive for consistency in styling, terminology, and layout.
2. Enable shortcuts for frequent users, such as keyboard shortcuts, quick-add deadline, and one-tap focus sessions.
3. Offer informative feedback for every action.
4. Design flows to yield closure: study sessions, quizzes, and uploads have clear done states.
5. Prevent errors and handle them simply: validate deadline dates and warn before overwriting plans.
6. Permit easy reversal with undo for destructive or plan-altering actions.
7. Support internal locus of control: the student remains in charge, especially of AI-suggested plans.
8. Reduce short-term memory load by showing relevant course and task context where it is needed.

## 7. Norman's 7 Principles

1. Put knowledge in the world with visible labels instead of relying on hidden gestures.
2. Simplify task structure, turning complex planning into a short guided flow.
3. Make actions and outcomes visible: close both the Gulf of Execution and the Gulf of Evaluation.
4. Get mappings right: visual mastery maps should reflect topic relationships.
5. Exploit constraints: prevent impossible dates and incomplete submissions where required.
6. Design for error: catch typos, duplicate deadlines, and invalid uploads with useful explanations.
7. Standardize where there is no natural solution: use conventional icons and patterns for save, delete, and search.

## 8. Reusable design patterns

Treat recurring UI problems as named patterns and define them once for reuse:

- Deadline card
- Progress ring
- Undo toast
- Empty state with next action
- AI suggestion chip with accept and reject
- Upload progress and failure state
- Saved or synced confirmation

Patterns must compose into complete flows. For example, a Deadline card, Undo toast, and Progress ring should behave coherently on the dashboard, calendar, and course pages rather than being redesigned independently.

## Review checklist

Before calling a screen or flow complete, review it against:

- [ ] Learnability: predictable, familiar, consistent, and visibly confirmed.
- [ ] Flexibility: supports skip, back, alternate inputs, shortcuts, and student control.
- [ ] Robustness: observable, responsive, recoverable, and complete end to end.
- [ ] ISO 9241: effective, efficient, and satisfactory.
- [ ] Accessibility: contrast, keyboard access, focus states, semantics, and labels.
- [ ] Shneiderman: all eight Golden Rules.
- [ ] Norman: all seven Principles.
- [ ] Patterns: named, reusable, and composable with existing components.
- [ ] Trade-offs: any unmet requirement is documented with its reason.

## How to use this prompt

- **Build prompt:** provide the Master instruction plus the relevant sections when generating a Study Planner screen.
- **Design review:** run a finished screen through Learnability, Flexibility, Robustness, the Golden Rules, and Norman's Principles before shipping.
- **Exam-to-practice bridge:** use the Study Planner examples to connect DCIT 302 theory to applied revision.
