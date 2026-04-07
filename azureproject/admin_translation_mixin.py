# azureproject/admin_translation_mixin.py
"""
Admin mixin that adds an "Auto-translate empty fields" action to any TranslationAdmin.

Uses Azure AI Translator to fill in missing language variants for translatable fields.
Works with django-modeltranslation's field naming convention: field_en, field_de, field_fr.

Usage:
    from azureproject.admin_translation_mixin import AutoTranslateMixin

    class MyModelAdmin(AutoTranslateMixin, TranslationAdmin):
        ...
"""

import json
import logging

from django.contrib import admin, messages
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from modeltranslation.translator import translator

from azureproject.azure_translator import is_configured, translate_batch

logger = logging.getLogger(__name__)

# Language codes used in this project
ALL_LANGUAGES = [code for code, _name in settings.LANGUAGES]


def _get_translatable_fields(model):
    """Get the list of base field names registered for translation on a model."""
    try:
        opts = translator.get_options_for_model(model)
        return list(opts.fields.keys())
    except Exception:
        return []


def _detect_source_lang(obj, field_name):
    """
    Detect which language variant has content for a field.
    Returns (lang_code, text) or (None, None) if no content found.
    Prefers English as source, then German, then French.
    """
    for lang in ALL_LANGUAGES:
        value = getattr(obj, f"{field_name}_{lang}", None)
        if value and str(value).strip():
            return lang, str(value)
    return None, None


def _get_empty_langs(obj, field_name, source_lang):
    """Get language codes that are empty/missing for a field."""
    empty = []
    for lang in ALL_LANGUAGES:
        if lang == source_lang:
            continue
        value = getattr(obj, f"{field_name}_{lang}", None)
        if not value or not str(value).strip():
            empty.append(lang)
    return empty


class AutoTranslateMixin:
    """
    Admin mixin that adds Azure AI Translator auto-translation as a list action.

    Automatically detects translatable fields via django-modeltranslation registry
    and fills in empty language variants using the Azure Translator API.
    """

    @admin.action(description=_("🌐 Auto-translate empty fields"))
    def auto_translate_empty_fields(self, request, queryset):
        """
        For each selected object, find translatable fields where some languages
        are filled and others are empty, then auto-translate the missing ones.
        """
        if not is_configured():
            self.message_user(
                request,
                _("Azure Translator is not configured. Set AZURE_TRANSLATOR_KEY in environment variables."),
                messages.ERROR,
            )
            return

        model = queryset.model
        translatable_fields = _get_translatable_fields(model)

        if not translatable_fields:
            self.message_user(
                request,
                _("No translatable fields found for this model."),
                messages.WARNING,
            )
            return

        total_translated = 0
        total_objects = 0
        errors = 0

        for obj in queryset:
            obj_translated = 0

            # Collect all translation tasks for this object to batch them
            # Group by (source_lang, target_langs) for efficient batching
            tasks = []  # (field_name, source_lang, source_text, target_langs)

            for field_name in translatable_fields:
                source_lang, source_text = _detect_source_lang(obj, field_name)
                if not source_lang:
                    continue

                empty_langs = _get_empty_langs(obj, field_name, source_lang)
                if not empty_langs:
                    continue

                # Handle JSONField content (e.g., quiz choices, journey options)
                if isinstance(source_text, str) and source_text.startswith(("[", "{")):
                    try:
                        parsed = json.loads(source_text)
                        if isinstance(parsed, list):
                            # Translate each item in the list
                            for target_lang in empty_langs:
                                translated_items = []
                                texts_to_translate = [
                                    str(item) for item in parsed if isinstance(item, str)
                                ]
                                if texts_to_translate:
                                    results = translate_batch(
                                        texts_to_translate, source_lang, [target_lang]
                                    )
                                    translated_items = [
                                        r.get(target_lang, item)
                                        for r, item in zip(results, texts_to_translate)
                                    ]
                                    setattr(
                                        obj,
                                        f"{field_name}_{target_lang}",
                                        json.dumps(translated_items, ensure_ascii=False),
                                    )
                                    obj_translated += 1
                            continue
                    except (json.JSONDecodeError, TypeError):
                        pass  # Not valid JSON, treat as plain text

                tasks.append((field_name, source_lang, source_text, empty_langs))

            # Execute translation tasks grouped by source language
            for field_name, source_lang, source_text, target_langs in tasks:
                try:
                    results = translate_batch([source_text], source_lang, target_langs)
                    if results and results[0]:
                        for lang, translated in results[0].items():
                            setattr(obj, f"{field_name}_{lang}", translated)
                            obj_translated += 1
                except Exception as e:
                    logger.error(
                        "Translation failed for %s.%s: %s",
                        obj.__class__.__name__,
                        field_name,
                        e,
                    )
                    errors += 1

            if obj_translated > 0:
                obj.save()
                total_objects += 1
                total_translated += obj_translated

        if total_translated > 0:
            self.message_user(
                request,
                _("Translated %(fields)d field(s) across %(objects)d object(s).") % {
                    "fields": total_translated,
                    "objects": total_objects,
                },
                messages.SUCCESS,
            )
        elif errors > 0:
            self.message_user(
                request,
                _("Translation failed. Check server logs for details."),
                messages.ERROR,
            )
        else:
            self.message_user(
                request,
                _("No empty translation fields found. All languages are already filled."),
                messages.INFO,
            )

    def get_actions(self, request):
        """Add the auto-translate action to existing actions."""
        actions = super().get_actions(request)
        if is_configured():
            actions["auto_translate_empty_fields"] = (
                self.auto_translate_empty_fields,
                "auto_translate_empty_fields",
                self.auto_translate_empty_fields.short_description,
            )
        return actions
