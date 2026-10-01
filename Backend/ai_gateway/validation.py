"""JSON parsing and schema validation for the AI Gateway.

The gateway must trust nothing from a model: it parses the raw reply
tolerantly (fenced blocks, stray prose) and then validates the parsed value
against the JSON Schema the caller supplied. Only the JSON-safe subset of JSON
Schema is supported on purpose — the schemas the Study Planner uses (cards,
quiz, flashcard JSON) need ``type``, ``properties``, ``required``, ``items``,
``enum`` and ``additionalProperties``. No external validation library is used,
keeping the backend dependency-light.
"""

import json
import re


class ParseError(ValueError):
    """The reply could not be parsed into a JSON value."""


def extract_json(text):
    """Return the first parseable JSON value embedded in `text`.

    Accepts fenced code blocks, leading/trailing prose, and a bare JSON value.
    Raises ParseError when nothing parseable is found.
    """
    if not isinstance(text, str) or not text.strip():
        raise ParseError("Empty response.")

    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    cleaned = re.sub(r"```", "", cleaned).strip()

    # Fast path: whole text is JSON.
    try:
        return json.loads(cleaned)
    except ValueError:
        pass

    for open_idx in _open_positions(cleaned):
        candidate = _balanced(cleaned, open_idx)
        if candidate is None:
            continue
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue
    raise ParseError("Could not find valid JSON in the response.")


def _open_positions(text):
    positions = [i for i, ch in enumerate(text) if ch in "{["]
    if not positions:
        return []
    objects = [i for i in positions if text[i] == "{"]
    return (objects or positions)[:20]


def _balanced(text, open_idx):
    open_ch = text[open_idx]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    escape = False
    for i in range(open_idx, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[open_idx:i + 1]
    return None


# ------------------------------------------------------------- validation

def validate_schema(value, schema):
    """Return a list of human-readable problems against `schema` ([] == ok).

    The supported subset is deliberately small but sufficient for the study
    app's generator schemas. Unknown keywords are ignored (matching the JS
    Draft-07 behaviour of ignoring unrecognized annotations).
    """
    problems = []

    if "type" in schema and not _type_ok(value, schema["type"]):
        return [f"value is not of type {schema['type']!r}"]

    # enum
    if "enum" in schema and value not in schema["enum"]:
        problems.append(
            f"value {value!r} is not one of the allowed enum values"
        )

    # object structure
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        allowable = set(properties)
        if not schema.get("additionalProperties", True) and set(value) - allowable:
            extra = ", ".join(sorted(set(value) - allowable))
            problems.append(f"unexpected properties: {extra}")
        for name, props in properties.items():
            if name not in value:
                if name in (schema.get("required") or []):
                    problems.append(f"missing required property '{name}'")
                continue
            sub = value.get(name)
            if isinstance(sub, dict) and props.get("type") == "object":
                problems.extend(
                    _prefix(name, validate_schema(sub, props))
                )
            elif isinstance(sub, list):
                problems.extend(_prefix(name, _validate_array(sub, props)))
            elif isinstance(sub, str):
                if "type" in props and not _type_ok(sub, props["type"]):
                    problems.append(f"property '{name}' has the wrong type")
                if isinstance(props.get("enum"), list) and sub not in props["enum"]:
                    problems.append(f"property '{name}' value is not allowed")
            elif isinstance(sub, (int, float)):
                if "type" in props and not _type_ok(sub, props["type"]):
                    problems.append(f"property '{name}' has the wrong type")
            elif props.get("type") in (["null"], "null"):
                problems.append(f"property '{name}' should be null")
            else:
                problems.append(f"property '{name}' has an unexpected value type")

    # array structure
    elif isinstance(value, list):
        problems.extend(_validate_array(value, schema))

    return problems


def _type_ok(value, type_keywords):
    if isinstance(type_keywords, list):
        return any(_one_type_ok(value, t) for t in type_keywords)
    return _one_type_ok(value, type_keywords)


def _one_type_ok(value, t):
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t in ("number", "integer"):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    return True


def _validate_array(items, schema):
    problems = []
    item_schema = schema.get("items")
    if item_schema is None:
        return problems
    for i, item in enumerate(items):
        problems.extend(_prefix(f"[{i}]", validate_schema(item, item_schema)))
    return problems


def _prefix(path, problems):
    return [f"{path}: {p}" for p in problems] if problems else []