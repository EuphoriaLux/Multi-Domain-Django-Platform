"""Claude-backed copy generation for the Crush Hub marketing planner."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class ClaudeServiceError(RuntimeError):
    """Raised when Claude is unavailable, misconfigured, or returns bad data."""


@dataclass(frozen=True)
class GeneratedArticle:
    title: str
    content: str


def _messages_request(*, prompt: str, schema: dict, max_tokens: int) -> dict:
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise ClaudeServiceError("ANTHROPIC_API_KEY is not configured")

    payload = {
        "model": settings.ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "temperature": 0.6,
        "system": (
            "You are the senior social-media editor for Crush.lu, a Luxembourg "
            "dating and in-person events brand. Write warm, specific, credible "
            "copy. Never invent statistics, event details, testimonials, or user "
            "facts. Keep supplied personal details anonymized and do not add any."
        ),
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": schema,
            }
        },
    }

    try:
        response = requests.post(
            ANTHROPIC_MESSAGES_URL,
            headers={
                "content-type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
            json=payload,
            timeout=settings.ANTHROPIC_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.exception("Claude Messages API request failed")
        raise ClaudeServiceError(
            "Claude generation is temporarily unavailable"
        ) from exc

    try:
        text_blocks = [
            block["text"]
            for block in data["content"]
            if block.get("type") == "text" and block.get("text")
        ]
        return json.loads("".join(text_blocks))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        logger.error("Claude returned an invalid structured response")
        raise ClaudeServiceError("Claude returned an invalid response") from exc


def generate_social_copy(
    *,
    category: str,
    pillar: str,
    hook: str,
    platforms: list[str],
    languages: list[str],
    context: dict,
) -> dict[str, str]:
    """Return one platform-ready post per requested language."""

    language_names = {"fr": "French", "en": "English", "de": "German"}
    requested = [lang for lang in languages if lang in language_names]
    if not requested:
        raise ClaudeServiceError("At least one supported language is required")

    schema = {
        "type": "object",
        "properties": {
            "posts": {
                "type": "array",
                "minItems": len(requested),
                "maxItems": len(requested),
                "items": {
                    "type": "object",
                    "properties": {
                        "language": {"type": "string", "enum": requested},
                        "content": {"type": "string", "minLength": 40},
                    },
                    "required": ["language", "content"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["posts"],
        "additionalProperties": False,
    }
    prompt = (
        "Create one distinct social post for each requested language.\n"
        f"Category: {category}\nPillar: {pillar}\nHook: {hook}\n"
        f"Target platforms: {', '.join(platforms)}\n"
        f"Languages: {', '.join(language_names[lang] for lang in requested)}\n"
        f"Verified source context (the only factual source): {json.dumps(context, ensure_ascii=False)}\n\n"
        "Requirements: write naturally in each language; keep each post under "
        "1,500 characters; include a clear but non-pushy call to action; use no "
        "more than four relevant hashtags; do not claim a result or quote a "
        "person unless it is present in the verified source context."
    )
    result = _messages_request(prompt=prompt, schema=schema, max_tokens=1800)
    posts = {item["language"]: item["content"].strip() for item in result["posts"]}
    if set(posts) != set(requested):
        raise ClaudeServiceError("Claude did not return every requested language")
    return posts


def expand_social_post(
    *, hook: str, pillar: str, language: str, content: str
) -> GeneratedArticle:
    """Expand a reviewed social post into a long-form Markdown article draft."""

    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 8, "maxLength": 160},
            "content": {"type": "string", "minLength": 300},
        },
        "required": ["title", "content"],
        "additionalProperties": False,
    }
    prompt = (
        "Expand the following approved social post into a useful 600-900 word "
        "Markdown article draft. Preserve its language, facts, and tone. Add a "
        "short introduction, descriptive H2 sections, and a final call to action. "
        "Do not invent facts or testimonials.\n\n"
        f"Language: {language}\nPillar: {pillar}\nHook: {hook}\nPost: {content}"
    )
    result = _messages_request(prompt=prompt, schema=schema, max_tokens=2600)
    return GeneratedArticle(
        title=result["title"].strip(), content=result["content"].strip()
    )
