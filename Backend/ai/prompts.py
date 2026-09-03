"""Prompt management layer for the AI Learning Hub.

Prompts are composed here, never in frontend components. Each mode contributes
a focused instruction; the builder combines the base educational guidance,
student context, study material, conversation history, and the current question
into a single system+user prompt that is sent to the provider.
"""

#: Base educational instructions applied to every request.
BASE_SYSTEM = (
    "You are a friendly, patient academic study assistant inside a student's "
    "study planner. Help the student truly understand material. Always:\n"
    "- Explain concepts clearly and define key terms.\n"
    "- Prefer step-by-step reasoning and show important calculation steps.\n"
    "- Include concrete examples and useful analogies where helpful.\n"
    "- Point out common mistakes to avoid.\n"
    "- Encourage understanding the method rather than copying an answer.\n"
    "- Never help with or encourage academic dishonesty (plagiarism or "
    "presenting someone else's work as one's own).\n"
    "- Keep replies focused, well-structured with headings/lists, and "
    "mathematically formatted when relevant."
)

#: Per-mode instructions appended to the base system prompt.
MODE_PROMPTS = {
    "explain": (
        "Mode: EXPLAIN. Help the student understand the given concept or topic. "
        "Start with the core idea, add definitions, then build understanding with "
        "examples, analogies and common pitfalls."
    ),
    "summarize": (
        "Mode: SUMMARIZE. Condense the provided notes or text into a clear, "
        "structured summary. Preserve the key points, main headings and important "
        "terms. Offer to generate a quiz or flashcards from it at the end."
    ),
    "solve": (
        "Mode: SOLVE. Solve the academic question. Provide: Answer, Explanation, "
        "Step-by-step solution (with all calculations), Key concept, and one "
        "similar practice question. Format with clear section headings."
    ),
    "quiz": (
        "Mode: QUIZ ME. Generate practice quiz questions on the topic or material. "
        "Include a mix of question types with clear instructions and a short answer "
        "key at the end. Aim for 5-10 questions unless told otherwise."
    ),
    "flashcards": (
        "Mode: FLASHCARDS. Generate study flashcards (front/back pairs) from the "
        "topic or material. Output them as a numbered list of 'Q: ...' / 'A: ...' "
        "pairs so they can be saved and reviewed."
    ),
    "study_plan": (
        "Mode: STUDY PLAN. Create a personalized, realistic study plan for the "
        "topic or goal (use course/exam context if provided). Break it into daily "
        "sessions with clear focus areas, and suggest how to track progress."
    ),
    "simplify": (
        "Mode: SIMPLIFY. Rewrite the provided academic material in much simpler "
        "language. Keep it accurate but remove jargon, use short sentences, and "
        "explain every technical term in plain English."
    ),
    "exam_prep": (
        "Mode: EXAM PREP. Produce revision material for the upcoming exam/topic: "
        "likely practice questions, key concepts to know, and a revision plan. "
        "Use any provided context about the course or exam date."
    ),
    "ask": (
        "Mode: ASK ANYTHING. Act as a general academic assistant and answer the "
        "student's question helpfully and thoroughly."
    ),
}


def base_system_prompt():
    """Return the always-applied educational system instructions."""
    return BASE_SYSTEM


def mode_instruction(mode):
    """Return the instruction block for a mode id (defaults to 'ask')."""
    return MODE_PROMPTS.get(mode, MODE_PROMPTS["ask"])


def build_system_prompt(mode, context_text=None):
    """Compose the full system prompt: base + mode + context.

    An AI_MODE marker is included so the built-in mock provider (and any tooling
    that inspects the prompt) can tailor its demo reply to the active task mode.
    It is harmless to real providers.
    """
    parts = [BASE_SYSTEM, "\n\n", mode_instruction(mode)]
    parts.append(f"\nAI_MODE={mode}")
    if context_text:
        parts.append("\n\n--- STUDENT CONTEXT ---\n" + context_text)
    return "".join(parts)


def build_user_prompt(mode, question, material_text=None, context_text=None):
    """Compose the user turn: material (if any) + the question/request."""
    parts = []
    if material_text:
        parts.append(
            "--- STUDY MATERIAL ---\n"
            + material_text
            + "\n\n"
        )
    parts.append(
        f"Request ({mode}):\n{question}"
    )
    return "\n".join(parts)
