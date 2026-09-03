"""Student context builder for the AI Learning Hub.

Builds a short, task-relevant context string from the user's Study Planner data.
Only minimal relevant fields are included (courses, program, goals); no private
credentials or unrelated data are ever sent.
"""


def build_context(user, subject_title=None):
    """Return a compact context string about the student, or None.

    user: the current user dict from load_users() (may be empty).
    subject_title: optional course title to narrow the context.
    """
    if not user:
        return None

    lines = []
    courses = [c for c in (user.get("courses") or []) if c.strip()]
    if courses:
        titles = ", ".join(courses)
        lines.append(f"Current courses: {titles}")
    if subject_title:
        lines.append(f"Active topic/course: {subject_title}")
    if user.get("program"):
        lines.append(f"Program of study: {user['program']}")
    if user.get("goals"):
        lines.append(f"Study goals: {user['goals']}")

    if not lines:
        return None
    return "\n".join(lines)
