# azureproject/azure_translator.py
"""
Azure AI Translator integration for auto-translating model fields.
Uses the Azure Translator REST API v3.0.

Free tier: 2 million characters/month.
Setup: Create an Azure AI Translator resource and set AZURE_TRANSLATOR_KEY + AZURE_TRANSLATOR_REGION.
"""

import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

TRANSLATOR_ENDPOINT = "https://api.cognitive.microsofttranslator.com"


def is_configured():
    """Check if Azure Translator credentials are configured."""
    return bool(getattr(settings, "AZURE_TRANSLATOR_KEY", ""))


def translate_text(text, from_lang, to_langs):
    """
    Translate text using Azure AI Translator API.

    Args:
        text: The text to translate (str or list of str).
        from_lang: Source language code (e.g., 'en', 'de', 'fr').
        to_langs: Target language codes (e.g., ['de', 'fr']).

    Returns:
        dict: {lang_code: translated_text} for each target language.
              Returns empty dict on failure.

    Example:
        >>> translate_text("Hello world", "en", ["de", "fr"])
        {"de": "Hallo Welt", "fr": "Bonjour le monde"}
    """
    if not is_configured():
        logger.warning("Azure Translator not configured. Set AZURE_TRANSLATOR_KEY.")
        return {}

    if not text or not text.strip():
        return {lang: "" for lang in to_langs}

    key = settings.AZURE_TRANSLATOR_KEY
    region = getattr(settings, "AZURE_TRANSLATOR_REGION", "westeurope")

    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Ocp-Apim-Subscription-Region": region,
        "Content-Type": "application/json",
    }

    params = {
        "api-version": "3.0",
        "from": from_lang,
        "to": to_langs,
    }

    body = [{"text": text}]

    try:
        response = httpx.post(
            f"{TRANSLATOR_ENDPOINT}/translate",
            headers=headers,
            params=params,
            json=body,
            timeout=10.0,
        )
        response.raise_for_status()

        result = response.json()
        translations = {}
        for translation in result[0]["translations"]:
            translations[translation["to"]] = translation["text"]

        return translations

    except httpx.HTTPStatusError as e:
        logger.error("Azure Translator API error: %s - %s", e.response.status_code, e.response.text)
        return {}
    except Exception as e:
        logger.error("Azure Translator request failed: %s", e)
        return {}


def translate_batch(texts, from_lang, to_langs):
    """
    Translate multiple texts in a single API call (up to 100 items, 50K chars).

    Args:
        texts: List of strings to translate.
        from_lang: Source language code.
        to_langs: Target language codes.

    Returns:
        list[dict]: List of {lang_code: translated_text} for each input text.
    """
    if not is_configured():
        logger.warning("Azure Translator not configured. Set AZURE_TRANSLATOR_KEY.")
        return [{} for _ in texts]

    if not texts:
        return []

    key = settings.AZURE_TRANSLATOR_KEY
    region = getattr(settings, "AZURE_TRANSLATOR_REGION", "westeurope")

    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Ocp-Apim-Subscription-Region": region,
        "Content-Type": "application/json",
    }

    params = {
        "api-version": "3.0",
        "from": from_lang,
        "to": to_langs,
    }

    body = [{"text": t if t and t.strip() else ""} for t in texts]

    try:
        response = httpx.post(
            f"{TRANSLATOR_ENDPOINT}/translate",
            headers=headers,
            params=params,
            json=body,
            timeout=30.0,
        )
        response.raise_for_status()

        results = []
        for item in response.json():
            translations = {}
            for translation in item["translations"]:
                translations[translation["to"]] = translation["text"]
            results.append(translations)

        return results

    except httpx.HTTPStatusError as e:
        logger.error("Azure Translator batch API error: %s - %s", e.response.status_code, e.response.text)
        return [{} for _ in texts]
    except Exception as e:
        logger.error("Azure Translator batch request failed: %s", e)
        return [{} for _ in texts]
