"""Template tags for rendering pre-screening questionnaire inputs."""
from __future__ import annotations

from typing import Any

from django import template
from django.template.loader import render_to_string

from crush_lu.pre_screening_schema import (
    FLAG_DESCRIPTIONS,
    PRE_SCREENING_SCHEMA,
    get_question,
    get_section,
    readiness_score_label,
)

register = template.Library()


_INPUT_TEMPLATES: dict[str, str] = {
    "single_select": "crush_lu/pre_screening/_q_single_select.html",
    "multi_select": "crush_lu/pre_screening/_q_multi_select.html",
    "text": "crush_lu/pre_screening/_q_text.html",
    "checkbox": "crush_lu/pre_screening/_q_checkbox.html",
    "yes_no": "crush_lu/pre_screening/_q_yes_no.html",
}


@register.simple_tag(takes_context=True)
def render_prescreening_question(context, question: dict, responses: dict,
                                 errors: dict | None = None):
    """Render a single pre-screening question with its current response.

    Args:
        question: one of the question dicts from PRE_SCREENING_SCHEMA.
        responses: user's current responses (flat dict of question_id -> value).
        errors: optional dict of question_id -> list of error codes.

    Usage:
        {% render_prescreening_question question responses errors %}
    """
    template_path = _INPUT_TEMPLATES.get(question["type"])
    if template_path is None:
        return ""

    value = responses.get(question["id"])
    qid = question["id"]
    ctx = {
        "q": question,
        "value": value,
        "errors": (errors or {}).get(qid, []),
        "request": context.get("request"),
        "csp_nonce": context.get("csp_nonce", ""),
    }
    # Normalize multi_select defaults so templates can iterate safely.
    if question["type"] == "multi_select" and not isinstance(value, list):
        ctx["value"] = []
    return render_to_string(template_path, ctx)


@register.filter
def prescreening_choice_label(question: dict, value: Any):
    """Resolve a stored choice value back to its human label."""
    if question is None or value in (None, ""):
        return ""
    if question["type"] == "multi_select" and isinstance(value, list):
        labels = []
        by_value = {c["value"]: c["label"] for c in question.get("choices", [])}
        for v in value:
            labels.append(str(by_value.get(v, v)))
        return ", ".join(labels)
    for choice in question.get("choices", []):
        if choice["value"] == value:
            return choice["label"]
    return value


@register.filter
def prescreening_question(question_id: str):
    """Look up a question dict by id for use in display templates."""
    return get_question(question_id)


@register.filter
def prescreening_section(section_id: str):
    return get_section(section_id)


@register.filter
def prescreening_flag_description(flag: str):
    return FLAG_DESCRIPTIONS.get(flag, flag)


@register.filter
def prescreening_score_label(score: int | None):
    """Return 'high' / 'medium' / 'low' / 'pending' for a numeric score."""
    return readiness_score_label(score)


@register.simple_tag
def prescreening_schema():
    return PRE_SCREENING_SCHEMA


@register.filter
def get_item(mapping, key):
    """Index a dict by key inside a template. Returns '' if missing."""
    if not mapping:
        return ""
    try:
        return mapping.get(key, "")
    except AttributeError:
        return ""
