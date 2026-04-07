"""
Tests for Azure AI Translator integration.

Tests the translator utility and the admin auto-translate mixin.
"""

from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings


class AzureTranslatorConfigTest(TestCase):
    """Test translator configuration detection."""

    @override_settings(AZURE_TRANSLATOR_KEY="")
    def test_not_configured_when_key_empty(self):
        from azureproject.azure_translator import is_configured
        self.assertFalse(is_configured())

    @override_settings(AZURE_TRANSLATOR_KEY="test-key-123")
    def test_configured_when_key_set(self):
        from azureproject.azure_translator import is_configured
        self.assertTrue(is_configured())


class TranslateTextTest(TestCase):
    """Test single text translation."""

    @override_settings(AZURE_TRANSLATOR_KEY="")
    def test_returns_empty_when_not_configured(self):
        from azureproject.azure_translator import translate_text
        result = translate_text("Hello", "en", ["de", "fr"])
        self.assertEqual(result, {})

    @override_settings(AZURE_TRANSLATOR_KEY="test-key")
    def test_returns_empty_for_empty_text(self):
        from azureproject.azure_translator import translate_text
        result = translate_text("", "en", ["de", "fr"])
        self.assertEqual(result, {"de": "", "fr": ""})

    @override_settings(AZURE_TRANSLATOR_KEY="test-key")
    def test_returns_empty_for_whitespace_text(self):
        from azureproject.azure_translator import translate_text
        result = translate_text("   ", "en", ["de", "fr"])
        self.assertEqual(result, {"de": "", "fr": ""})

    @override_settings(
        AZURE_TRANSLATOR_KEY="test-key",
        AZURE_TRANSLATOR_REGION="westeurope",
    )
    @patch("azureproject.azure_translator.httpx.post")
    def test_successful_translation(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "translations": [
                    {"text": "Hallo Welt", "to": "de"},
                    {"text": "Bonjour le monde", "to": "fr"},
                ]
            }
        ]
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        from azureproject.azure_translator import translate_text
        result = translate_text("Hello world", "en", ["de", "fr"])

        self.assertEqual(result, {"de": "Hallo Welt", "fr": "Bonjour le monde"})

        # Verify API was called correctly
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        self.assertIn("Ocp-Apim-Subscription-Key", call_kwargs.kwargs["headers"])
        self.assertEqual(call_kwargs.kwargs["headers"]["Ocp-Apim-Subscription-Key"], "test-key")
        self.assertEqual(call_kwargs.kwargs["params"]["from"], "en")
        self.assertEqual(call_kwargs.kwargs["params"]["to"], ["de", "fr"])

    @override_settings(AZURE_TRANSLATOR_KEY="test-key")
    @patch("azureproject.azure_translator.httpx.post")
    def test_handles_api_error_gracefully(self, mock_post):
        import httpx
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=mock_response
        )
        mock_post.return_value = mock_response

        from azureproject.azure_translator import translate_text
        result = translate_text("Hello", "en", ["de"])

        self.assertEqual(result, {})

    @override_settings(AZURE_TRANSLATOR_KEY="test-key")
    @patch("azureproject.azure_translator.httpx.post")
    def test_handles_network_error_gracefully(self, mock_post):
        mock_post.side_effect = Exception("Connection timeout")

        from azureproject.azure_translator import translate_text
        result = translate_text("Hello", "en", ["de"])

        self.assertEqual(result, {})


class TranslateBatchTest(TestCase):
    """Test batch text translation."""

    @override_settings(AZURE_TRANSLATOR_KEY="")
    def test_returns_empty_when_not_configured(self):
        from azureproject.azure_translator import translate_batch
        result = translate_batch(["Hello", "World"], "en", ["de"])
        self.assertEqual(result, [{}, {}])

    @override_settings(AZURE_TRANSLATOR_KEY="test-key")
    def test_returns_empty_list_for_empty_input(self):
        from azureproject.azure_translator import translate_batch
        result = translate_batch([], "en", ["de"])
        self.assertEqual(result, [])

    @override_settings(AZURE_TRANSLATOR_KEY="test-key")
    @patch("azureproject.azure_translator.httpx.post")
    def test_successful_batch_translation(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"translations": [{"text": "Hallo", "to": "de"}]},
            {"translations": [{"text": "Welt", "to": "de"}]},
        ]
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        from azureproject.azure_translator import translate_batch
        result = translate_batch(["Hello", "World"], "en", ["de"])

        self.assertEqual(result, [{"de": "Hallo"}, {"de": "Welt"}])


class AutoTranslateMixinTest(TestCase):
    """Test the admin auto-translate mixin helper functions."""

    @override_settings(LANGUAGES=[("en", "English"), ("de", "German"), ("fr", "French")])
    def test_detect_source_lang_prefers_english(self):
        from azureproject.admin_translation_mixin import _detect_source_lang

        obj = MagicMock()
        obj.title_en = "Hello"
        obj.title_de = "Hallo"
        obj.title_fr = ""

        lang, text = _detect_source_lang(obj, "title")
        self.assertEqual(lang, "en")
        self.assertEqual(text, "Hello")

    @override_settings(LANGUAGES=[("en", "English"), ("de", "German"), ("fr", "French")])
    def test_detect_source_lang_falls_back_to_german(self):
        from azureproject.admin_translation_mixin import _detect_source_lang

        obj = MagicMock()
        obj.title_en = ""
        obj.title_de = "Hallo"
        obj.title_fr = ""

        lang, text = _detect_source_lang(obj, "title")
        self.assertEqual(lang, "de")
        self.assertEqual(text, "Hallo")

    @override_settings(LANGUAGES=[("en", "English"), ("de", "German"), ("fr", "French")])
    def test_detect_source_lang_returns_none_when_empty(self):
        from azureproject.admin_translation_mixin import _detect_source_lang

        obj = MagicMock()
        obj.title_en = ""
        obj.title_de = ""
        obj.title_fr = ""

        lang, text = _detect_source_lang(obj, "title")
        self.assertIsNone(lang)
        self.assertIsNone(text)

    @override_settings(LANGUAGES=[("en", "English"), ("de", "German"), ("fr", "French")])
    def test_get_empty_langs(self):
        from azureproject.admin_translation_mixin import _get_empty_langs

        obj = MagicMock()
        obj.title_en = "Hello"
        obj.title_de = ""
        obj.title_fr = "Bonjour"

        empty = _get_empty_langs(obj, "title", "en")
        self.assertEqual(empty, ["de"])

    @override_settings(LANGUAGES=[("en", "English"), ("de", "German"), ("fr", "French")])
    def test_get_empty_langs_all_empty(self):
        from azureproject.admin_translation_mixin import _get_empty_langs

        obj = MagicMock()
        obj.title_en = "Hello"
        obj.title_de = ""
        obj.title_fr = ""

        empty = _get_empty_langs(obj, "title", "en")
        self.assertEqual(empty, ["de", "fr"])
