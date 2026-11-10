"""Structured research-idea analysis for paper outlines."""

import json

from ..ai import generate

FIELDS = ("research_problem", "core_motivation", "solution", "expected_contribution")
MAX_KEYWORDS = 8


def _json_fields(system: str, payload: str) -> dict:
    """Retry a truncated model response rather than attempting to repair partial JSON."""
    for attempt in range(2):
        raw = generate(
            system
            + " Return one compact JSON object only. Each text field must be at most 180 characters; use at most 6 short keywords. Avoid explanations.",
            payload,
            max_tokens=1200 if attempt == 0 else 1800,
            json_mode=True,
        )
        try:
            return normalize_fields(json.loads(raw))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    raise ValueError("模型连续返回不完整的结构化 JSON，请重试")


def normalize_fields(value: dict) -> dict:
    if not isinstance(value, dict):
        raise TypeError("IDEA analysis must be an object")
    result = {}
    for key in FIELDS:
        text = value.get(key)
        if text is not None and not isinstance(text, str):
            raise ValueError(f"Invalid {key}")
        result[key] = (text or "").strip()[:1200]
    words = value.get("related_work_keywords") or []
    if not isinstance(words, list) or any(not isinstance(word, str) for word in words):
        raise ValueError("Related-work keywords must be a list")
    result["related_work_keywords"] = list(
        dict.fromkeys(word.strip()[:100] for word in words if word.strip())
    )[:MAX_KEYWORDS]
    return result


def analyze_idea(idea: str) -> dict:
    return _json_fields(
        "Extract only facts stated or clearly implied by the user's research idea. Return JSON with research_problem, core_motivation, solution, expected_contribution (strings; empty when absent), and related_work_keywords (short English search phrases; empty when absent). Do not invent contributions, results, or unrelated research directions.",
        idea[:3500],
    )


def complete_fields(idea: str, fields: dict, context: str = "") -> dict:
    current = normalize_fields(fields)
    missing = [key for key in FIELDS if not current[key]]
    if not current["related_work_keywords"]:
        missing.append("related_work_keywords")
    if not missing:
        return current
    suggested = _json_fields(
        "Fill only the listed missing fields from the user's original research idea. The optional review is background, not evidence that the user's method or contribution exists. Return only the missing JSON keys. For unsupported details use an empty string/list, never invent methods, results, or contributions. Related-work keywords must be concise English terms for the same topic.",
        json.dumps(
            {"idea": idea[:1800], "review": context[:900], "missing": missing},
            ensure_ascii=False,
        ),
    )
    return {key: current[key] or suggested[key] for key in FIELDS} | {
        "related_work_keywords": current["related_work_keywords"]
        or suggested["related_work_keywords"]
    }
