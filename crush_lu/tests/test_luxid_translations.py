"""
Playwright tests for LuxID mockup page translations.
Tests German and French translations for both auth and profile mockup pages.
"""
import pytest
from playwright.sync_api import expect, Page


@pytest.mark.playwright
class TestLuxIDTranslations:
    """Test LuxID mockup pages have correct translations."""

    def test_german_auth_mockup(self, page: Page, live_server):
        """Test German translation on auth mockup page."""
        page.goto(f"{live_server.url}/de/mockup/auth-luxid/")

        # Take screenshot
        page.screenshot(path='crush_lu/tests/screenshots/luxid_auth_de.png')

        # Extract heading text
        heading = page.locator('h2:has-text("Warum"), h2:has-text("Why"), h3:has-text("Warum"), h3:has-text("Why")').first
        heading_text = heading.text_content() if heading.count() > 0 else "NOT FOUND"

        # Extract page content for debugging
        page_content = page.content()

        # Look for translation markers
        has_warum = "Warum" in page_content or "warum" in page_content
        has_why = "Why choose LuxID" in page_content

        print(f"\n=== GERMAN AUTH MOCKUP ===")
        print(f"URL: {live_server.url}/de/mockup/auth-luxid/")
        print(f"Heading found: {heading_text}")
        print(f"Contains 'Warum': {has_warum}")
        print(f"Contains 'Why choose LuxID': {has_why}")

        # Extract benefits list
        benefits = page.locator('ul li').all()
        print(f"Benefits found: {len(benefits)} items")
        if benefits:
            for i, benefit in enumerate(benefits[:5], 1):
                text = benefit.text_content()[:100]
                print(f"  {i}. {text}")

        # Report translation status
        if has_why and not has_warum:
            print("WARNING: TRANSLATION NOT WORKING - Page is in English")
        elif has_warum:
            print("OK: German translation appears to be working")
        else:
            print("WARNING: Could not find translation markers")

    def test_french_auth_mockup(self, page: Page, live_server):
        """Test French translation on auth mockup page."""
        page.goto(f"{live_server.url}/fr/mockup/auth-luxid/")

        # Take screenshot
        page.screenshot(path='crush_lu/tests/screenshots/luxid_auth_fr.png')

        # Extract heading text
        heading = page.locator('h2:has-text("Pourquoi"), h2:has-text("Why"), h3:has-text("Pourquoi"), h3:has-text("Why")').first
        heading_text = heading.text_content() if heading.count() > 0 else "NOT FOUND"

        # Extract page content for debugging
        page_content = page.content()

        # Look for translation markers
        has_pourquoi = "Pourquoi" in page_content or "pourquoi" in page_content
        has_why = "Why choose LuxID" in page_content

        print(f"\n=== FRENCH AUTH MOCKUP ===")
        print(f"URL: {live_server.url}/fr/mockup/auth-luxid/")
        print(f"Heading found: {heading_text}")
        print(f"Contains 'Pourquoi': {has_pourquoi}")
        print(f"Contains 'Why choose LuxID': {has_why}")

        # Extract benefits list
        benefits = page.locator('ul li').all()
        print(f"Benefits found: {len(benefits)} items")
        if benefits:
            for i, benefit in enumerate(benefits[:5], 1):
                text = benefit.text_content()[:100]
                print(f"  {i}. {text}")

        # Report translation status
        if has_why and not has_pourquoi:
            print("WARNING: TRANSLATION NOT WORKING - Page is in English")
        elif has_pourquoi:
            print("OK: French translation appears to be working")
        else:
            print("WARNING: Could not find translation markers")

    def test_german_profile_mockup(self, page: Page, live_server):
        """Test German translation on profile mockup page."""
        page.goto(f"{live_server.url}/de/mockup/profile-luxid/")

        # Take screenshot
        page.screenshot(path='crush_lu/tests/screenshots/luxid_profile_de.png')

        page_content = page.content()

        # Look for common German words
        has_german = any(word in page_content for word in [
            "Profil", "Einstellungen", "Konto", "Sicherheit", "Datenschutz"
        ])
        has_english = "Profile" in page_content or "Settings" in page_content

        print(f"\n=== GERMAN PROFILE MOCKUP ===")
        print(f"URL: {live_server.url}/de/mockup/profile-luxid/")
        print(f"Has German words: {has_german}")
        print(f"Has English words: {has_english}")

        # Extract visible text content (safely handle Unicode)
        try:
            body_text = page.locator('body').text_content()
            if body_text:
                words = body_text.split()[:50]  # First 50 words
                text_sample = ' '.join(words)[:200]
                # Remove emojis for Windows console
                text_sample = text_sample.encode('ascii', 'ignore').decode('ascii')
                print(f"First visible text: {text_sample}...")
        except Exception as e:
            print(f"Could not extract text: {e}")

        if has_english and not has_german:
            print("WARNING: TRANSLATION NOT WORKING - Page is in English")
        elif has_german:
            print("OK: German translation appears to be working")

    def test_french_profile_mockup(self, page: Page, live_server):
        """Test French translation on profile mockup page."""
        page.goto(f"{live_server.url}/fr/mockup/profile-luxid/")

        # Take screenshot
        page.screenshot(path='crush_lu/tests/screenshots/luxid_profile_fr.png')

        page_content = page.content()

        # Look for common French words
        has_french = any(word in page_content for word in [
            "Profil", "Paramètres", "Compte", "Sécurité", "Confidentialité"
        ])
        has_english = "Profile" in page_content or "Settings" in page_content

        print(f"\n=== FRENCH PROFILE MOCKUP ===")
        print(f"URL: {live_server.url}/fr/mockup/profile-luxid/")
        print(f"Has French words: {has_french}")
        print(f"Has English words: {has_english}")

        # Extract visible text content (safely handle Unicode)
        try:
            body_text = page.locator('body').text_content()
            if body_text:
                words = body_text.split()[:50]  # First 50 words
                text_sample = ' '.join(words)[:200]
                # Remove emojis for Windows console
                text_sample = text_sample.encode('ascii', 'ignore').decode('ascii')
                print(f"First visible text: {text_sample}...")
        except Exception as e:
            print(f"Could not extract text: {e}")

        if has_english and not has_french:
            print("WARNING: TRANSLATION NOT WORKING - Page is in English")
        elif has_french:
            print("OK: French translation appears to be working")

    def test_all_pages_summary(self, page: Page, live_server):
        """Summary test that visits all pages and reports translation status."""
        results = {}

        pages_to_test = [
            ('de', 'auth-luxid', 'Warum'),
            ('fr', 'auth-luxid', 'Pourquoi'),
            ('de', 'profile-luxid', 'Profil'),
            ('fr', 'profile-luxid', 'Profil'),
        ]

        for lang, page_type, expected_word in pages_to_test:
            url = f"{live_server.url}/{lang}/mockup/{page_type}/"
            page.goto(url)

            content = page.content()
            has_translation = expected_word in content
            has_english_fallback = "Why choose LuxID" in content or "Profile" in content

            key = f"{lang}_{page_type}"
            results[key] = {
                'url': url,
                'has_translation': has_translation,
                'has_english': has_english_fallback,
            }

        print("\n" + "="*60)
        print("LUXID TRANSLATION TEST SUMMARY")
        print("="*60)

        for key, result in results.items():
            lang, page_type = key.split('_', 1)
            status = "WORKING" if result['has_translation'] else "NOT WORKING"
            print(f"\n{lang.upper()} - {page_type}:")
            print(f"  URL: {result['url']}")
            print(f"  Status: {status}")
            if result['has_english'] and not result['has_translation']:
                print(f"  Issue: Showing English instead of {lang.upper()}")

        print("\n" + "="*60)
